"""History-aware publication scanning and unpublished-branch rewriting.

Findings deliberately contain only redacted match fingerprints.  Git paths are
read from NUL-delimited plumbing and blob content is decoded with replacement,
so unusual path bytes and non-UTF-8 text cannot desynchronise the scan.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from . import ValidationError
from .child_process import child_env
from .gittree import verify_publisher_tree


DEFAULT_MAPPING = Path(".agentic-state/publication-deny.json")
MAX_TEXT_BYTES = 32 * 1024 * 1024
ALIAS = re.compile(r"^[a-z][a-z0-9_]*$")
UNC = re.compile(r"(?<![\\])\\\\[A-Za-z0-9][A-Za-z0-9._-]*[\\/][^\s<>:\"|?*]+(?:[\\/][^\s<>:\"|?*]+)*")
WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s<>:\"|?*]+[\\/]?)+")
HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s]+(?:/[^\s]*)?")
IP_CANDIDATE = re.compile(r"(?<![0-9A-Fa-f:.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
IPV6_CANDIDATE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")


@dataclass(frozen=True)
class Detector:
    detector_id: str
    regex: re.Pattern
    private_ip: bool = False


def _safe_env(extra=None):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_LAZY_FETCH="1")
    if extra:
        env.update(extra)
    return env


def _git(root, *args, input_bytes=None, extra_env=None, check=True, timeout=120):
    executable = shutil.which("git")
    if not executable:
        raise ValidationError("Git is required for publication safety")
    command = [executable, "--no-replace-objects", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=" + os.devnull, "-c", "core.quotePath=false",
               "-c", "protocol.file.allow=never", "-C", str(Path(root).resolve()), *args]
    try:
        result = subprocess.run(command, input=input_bytes, capture_output=True, timeout=timeout,
                                env=child_env(_safe_env(extra_env)), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"git {args[0]} did not complete: {type(exc).__name__}") from exc
    if check and result.returncode:
        detail = re.sub(r"[^\x20-\x7e]", "?", result.stderr.decode("utf-8", "replace"))[:300]
        raise ValidationError(f"git {args[0]} failed with exit {result.returncode}: {detail}")
    return result


def _json_file(path, label):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Invalid {label}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return value


def load_mapping(root, mapping_path=None):
    """Load the ignored operator-local mapping without returning private values in errors."""
    path = Path(mapping_path) if mapping_path else Path(root) / DEFAULT_MAPPING
    if not path.is_absolute():
        path = Path(root) / path
    if not path.exists():
        return {"version": 1, "aliases": {}, "deny_literals": [], "deny_regexes": [],
                "internal_hostnames": [], "builtin_allow": []}, None, path
    value = _json_file(path, "operator-local publication mapping")
    allowed = {"version", "aliases", "deny_literals", "deny_regexes", "internal_hostnames", "builtin_allow"}
    if set(value) - allowed or value.get("version") != 1:
        raise ValidationError("Operator-local publication mapping has unsupported fields or version")
    aliases = value.get("aliases", {})
    if not isinstance(aliases, dict) or not all(ALIAS.fullmatch(key) for key in aliases):
        raise ValidationError("Mapping aliases must use lower-case logical names")
    for values in aliases.values():
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and item for item in values):
            raise ValidationError("Each mapping alias must contain nonempty private strings")
    for key in ("deny_literals", "internal_hostnames"):
        values = value.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            raise ValidationError(f"Mapping {key} must contain nonempty strings")
    for key in ("deny_regexes", "builtin_allow"):
        values = value.get(key, [])
        if not isinstance(values, list):
            raise ValidationError(f"Mapping {key} must be a list")
        for item in values:
            if not isinstance(item, dict) or set(item) != {"id", "pattern"} or not ALIAS.fullmatch(item["id"]):
                raise ValidationError(f"Mapping {key} entries need lower-case id and pattern")
            try:
                re.compile(item["pattern"], re.IGNORECASE)
            except re.error as exc:
                raise ValidationError(f"Mapping {key} contains an invalid regular expression") from exc
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return value, hashlib.sha256(canonical).hexdigest(), path


def render_aliases(text, mapping):
    """Replace private mapping values with their tracked logical aliases."""
    if not isinstance(text, str):
        raise ValidationError("Alias rendering requires text")
    replacements = []
    for alias, values in mapping.get("aliases", {}).items():
        replacements.extend((value, "{" + alias + "}") for value in values)
    for value, alias in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        text = re.sub(re.escape(value), lambda _match, replacement=alias: replacement, text, flags=re.IGNORECASE)
    return text


def _project_declarations(root, config_path=None):
    path = Path(config_path) if config_path else Path(root) / ".agentic/PROJECT_CONFIG.yaml"
    if not path.is_absolute():
        path = Path(root) / path
    if not path.is_file():
        return {}, None
    config = _json_file(path, "project configuration")
    publication = config.get("publication", {})
    if not isinstance(publication, dict) or set(publication) - {"deny_literals", "deny_regexes", "internal_hostnames"}:
        raise ValidationError("Project publication declarations have unsupported fields")
    for key in ("deny_literals", "internal_hostnames"):
        if not isinstance(publication.get(key, []), list) or not all(isinstance(x, str) and x for x in publication.get(key, [])):
            raise ValidationError(f"Project publication {key} must contain nonempty strings")
    for item in publication.get("deny_regexes", []):
        if not isinstance(item, dict) or set(item) != {"id", "pattern"} or not ALIAS.fullmatch(item["id"]):
            raise ValidationError("Project publication regex entries need lower-case id and pattern")
        try:
            re.compile(item["pattern"], re.IGNORECASE)
        except re.error as exc:
            raise ValidationError("Project publication declarations contain an invalid regular expression") from exc
    return publication, hashlib.sha256(path.read_bytes()).hexdigest()


def _detectors(mapping, declarations):
    result = [Detector("builtin.unc_path", UNC), Detector("builtin.windows_absolute", WINDOWS_ABSOLUTE),
              Detector("builtin.home_path", HOME_PATH), Detector("builtin.private_ipv4", IP_CANDIDATE, True),
              Detector("builtin.private_ipv6", IPV6_CANDIDATE, True)]
    for alias, values in mapping.get("aliases", {}).items():
        result.extend(Detector("local.alias." + alias, re.compile(re.escape(value), re.IGNORECASE)) for value in values)
    literals = list(mapping.get("deny_literals", [])) + list(declarations.get("deny_literals", []))
    result.extend(Detector(f"declared.literal.{index}", re.compile(re.escape(value), re.IGNORECASE))
                  for index, value in enumerate(literals, 1))
    hostnames = list(mapping.get("internal_hostnames", [])) + list(declarations.get("internal_hostnames", []))
    result.extend(Detector(f"declared.hostname.{index}", re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(value) + r"(?![A-Za-z0-9_.-])", re.IGNORECASE))
                  for index, value in enumerate(hostnames, 1))
    for source, values in (("local", mapping.get("deny_regexes", [])), ("project", declarations.get("deny_regexes", []))):
        result.extend(Detector(f"{source}.regex.{item['id']}", re.compile(item["pattern"], re.IGNORECASE)) for item in values)
    allows = [(item["id"], re.compile(item["pattern"], re.IGNORECASE)) for item in mapping.get("builtin_allow", [])]
    return result, allows


def _redacted(value):
    prefix = value[:2].encode("unicode_escape").decode("ascii")
    return f"{prefix}… sha256:{hashlib.sha256(value.encode('utf-8', 'surrogatepass')).hexdigest()[:12]}"


def _private_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return (address in ipaddress.ip_network("10.0.0.0/8") or
                address in ipaddress.ip_network("172.16.0.0/12") or
                address in ipaddress.ip_network("192.168.0.0/16"))
    return address in ipaddress.ip_network("fc00::/7")


def _scan_text(text, *, commit, path, source, detectors, allows, line_offset=0, change=None):
    findings = []
    for number, line in enumerate(text.splitlines() or [text], 1):
        for detector in detectors:
            for match in detector.regex.finditer(line):
                value = match.group(0)
                if detector.private_ip and not _private_ip(value):
                    continue
                if detector.detector_id.startswith("builtin.") and any(
                        allow_id in {"all", detector.detector_id.removeprefix("builtin.")} and pattern.search(value)
                        for allow_id, pattern in allows):
                    continue
                findings.append({"commit": commit, "path": path, "line": number + line_offset,
                                 "source": source, "change": change, "detector_id": detector.detector_id,
                                 "redacted_excerpt": _redacted(value)})
    return findings


def _tree(root, commit, extra_env=None):
    raw = _git(root, "ls-tree", "-rz", "--full-tree", commit, extra_env=extra_env).stdout
    result = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split(" ")
        result[raw_path.decode("utf-8", "surrogateescape")] = (mode, kind, oid)
    return result


def _blob(root, oid, extra_env=None):
    value = _git(root, "cat-file", "blob", oid, extra_env=extra_env).stdout
    if len(value) > MAX_TEXT_BYTES:
        return None, "oversize"
    if b"\0" in value:
        return None, "binary"
    return value.decode("utf-8", "replace"), None


def _changed_pairs(root, old, new, extra_env=None):
    raw = _git(root, "diff-tree", "-r", "-z", "--no-commit-id", "--name-status", "-M", old, new,
               extra_env=extra_env).stdout.split(b"\0")
    result, index = [], 0
    while index < len(raw) and raw[index]:
        status = raw[index].decode("ascii", "replace")
        index += 1
        if status.startswith(("R", "C")):
            before = raw[index].decode("utf-8", "surrogateescape")
            after = raw[index + 1].decode("utf-8", "surrogateescape")
            index += 2
        else:
            path = raw[index].decode("utf-8", "surrogateescape")
            index += 1
            before, after = (None, path) if status.startswith("A") else ((path, None) if status.startswith("D") else (path, path))
        result.append((status, before, after))
    return result


def _path_for_output(path, detectors, allows):
    if path is None:
        return None
    if _scan_text(path, commit=None, path="path", source="path", detectors=detectors, allows=allows):
        return "redacted-path sha256:" + hashlib.sha256(path.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return path


def scan_repository(root, base, head, *, pr_body_paths=(), comment_paths=(), pr_body_texts=(), comment_texts=(),
                    mapping_path=None, config_path=None, extra_env=None):
    """Scan branch history, final changed files, and prospective provider text."""
    root = Path(root).resolve()
    base_sha = _git(root, "rev-parse", "--verify", base + "^{commit}", extra_env=extra_env).stdout.decode("ascii").strip()
    head_sha = _git(root, "rev-parse", "--verify", head + "^{commit}", extra_env=extra_env).stdout.decode("ascii").strip()
    ancestor = _git(root, "merge-base", "--is-ancestor", base_sha, head_sha, extra_env=extra_env, check=False)
    if ancestor.returncode != 0:
        raise ValidationError("Publication base must be an ancestor of head")
    mapping, mapping_sha, mapping_file = load_mapping(root, mapping_path)
    declarations, config_sha = _project_declarations(root, config_path)
    detectors, allows = _detectors(mapping, declarations)
    commits = [item.decode("ascii") for item in _git(root, "rev-list", "--reverse", "--topo-order", base_sha + ".." + head_sha,
                                                       extra_env=extra_env).stdout.splitlines()]
    findings, unscanned = [], []
    tree_cache = {}
    for commit in commits:
        raw_commit = _git(root, "cat-file", "commit", commit, extra_env=extra_env).stdout
        message = raw_commit.split(b"\n\n", 1)[1] if b"\n\n" in raw_commit else b""
        findings += _scan_text(message.decode("utf-8", "replace"), commit=commit, path="message", source="message",
                               detectors=detectors, allows=allows)
        parents = _git(root, "rev-list", "--parents", "-n", "1", commit, extra_env=extra_env).stdout.decode("ascii").split()[1:]
        for parent in parents:
            for status, before_path, after_path in _changed_pairs(root, parent, commit, extra_env):
                old_tree = tree_cache.setdefault(parent, _tree(root, parent, extra_env))
                new_tree = tree_cache.setdefault(commit, _tree(root, commit, extra_env))
                output_path = _path_for_output(after_path or before_path, detectors, allows)
                path_text = (before_path or "") + "\n" + (after_path or "")
                findings += _scan_text(path_text, commit=commit, path=output_path, source="patch-path",
                                       detectors=detectors, allows=allows)
                old_text, old_reason = ("", None)
                new_text, new_reason = ("", None)
                if before_path and before_path in old_tree and old_tree[before_path][1] == "blob":
                    old_text, old_reason = _blob(root, old_tree[before_path][2], extra_env)
                if after_path and after_path in new_tree and new_tree[after_path][1] == "blob":
                    new_text, new_reason = _blob(root, new_tree[after_path][2], extra_env)
                if old_reason or new_reason:
                    unscanned.append({"commit": commit, "path": output_path, "source": "patch",
                                      "reason": old_reason or new_reason, "parent": parent})
                    continue
                old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
                matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
                for opcode, a1, a2, b1, b2 in matcher.get_opcodes():
                    if opcode in {"delete", "replace"}:
                        findings += _scan_text("\n".join(old_lines[a1:a2]), commit=commit, path=output_path,
                                               source="patch", change="deleted", line_offset=a1,
                                               detectors=detectors, allows=allows)
                    if opcode in {"insert", "replace"}:
                        findings += _scan_text("\n".join(new_lines[b1:b2]), commit=commit, path=output_path,
                                               source="patch", change="added", line_offset=b1,
                                               detectors=detectors, allows=allows)
    base_tree = tree_cache.setdefault(base_sha, _tree(root, base_sha, extra_env))
    head_tree = tree_cache.setdefault(head_sha, _tree(root, head_sha, extra_env))
    for _status, before_path, path in _changed_pairs(root, base_sha, head_sha, extra_env):
        if path is None or path not in head_tree or head_tree[path][1] != "blob":
            continue
        output_path = _path_for_output(path, detectors, allows)
        new_text, reason = _blob(root, head_tree[path][2], extra_env)
        old_text, old_reason = "", None
        if before_path and before_path in base_tree and base_tree[before_path][1] == "blob":
            old_text, old_reason = _blob(root, base_tree[before_path][2], extra_env)
        if reason or old_reason:
            unscanned.append({"commit": head_sha, "path": output_path, "source": "current-file", "reason": reason or old_reason,
                              "parent": None})
        else:
            old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
            for opcode, _a1, _a2, b1, b2 in difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False).get_opcodes():
                if opcode in {"insert", "replace"}:
                    findings += _scan_text("\n".join(new_lines[b1:b2]), commit=head_sha, path=output_path,
                                           source="current-file", line_offset=b1, detectors=detectors, allows=allows)
    body_hashes, comment_hashes = [], []
    body_values = list(pr_body_texts) + [Path(path).read_text(encoding="utf-8", errors="replace") for path in pr_body_paths]
    comment_values = list(comment_texts) + [Path(path).read_text(encoding="utf-8", errors="replace") for path in comment_paths]
    for body in body_values:
        body_hashes.append(hashlib.sha256(body.encode("utf-8")).hexdigest())
        findings += _scan_text(body, commit=head_sha, path="pr-body", source="pr-body", detectors=detectors, allows=allows)
    for comment in comment_values:
        comment_hashes.append(hashlib.sha256(comment.encode("utf-8")).hexdigest())
        findings += _scan_text(comment, commit=head_sha, path="comment", source="comment", detectors=detectors, allows=allows)
    findings.sort(key=lambda item: (item["commit"] or "", item["path"] or "", item["line"] or 0, item["detector_id"]))
    status = "BLOCKED" if findings or unscanned else "PASS"
    return {"schema_version": 3, "status": status, "base_sha": base_sha, "head_sha": head_sha,
            "pr_body_sha256": body_hashes[0] if len(body_hashes) == 1 else None,
            "additional_pr_body_sha256": body_hashes[1:], "comment_sha256": comment_hashes,
            "mapping_sha256": mapping_sha, "project_config_sha256": config_sha,
            "mapping_loaded": mapping_sha is not None, "mapping_location": DEFAULT_MAPPING.as_posix(),
            "commits_scanned": commits, "findings": findings, "unscanned": unscanned,
            "coverage": {"current_files": True, "commit_messages": True, "every_patch": True,
                         "generated_reports": "when committed or passed as provider text",
                         "captured_command_output": "when committed or passed as provider text",
                         "pr_bodies": bool(body_values), "pr_comments": bool(comment_values)},
            "execution_authority": False}


def render_scan(result):
    lines = [f"Publication scan: {result['status']}", f"Base: {result['base_sha']}", f"Head: {result['head_sha']}",
             f"Commits scanned: {len(result['commits_scanned'])}", f"Findings: {len(result['findings'])}",
             f"Unscanned binary/oversize entries: {len(result['unscanned'])}"]
    for item in result["findings"]:
        line = "" if item["line"] is None else f":{item['line']}"
        lines.append(f"- {item['commit']} {item['path']}{line} {item['detector_id']} {item['redacted_excerpt']}")
    for item in result["unscanned"]:
        lines.append(f"- NOT SCANNED {item['commit']} {item['path']} ({item['reason']})")
    return "\n".join(lines) + "\n"


def _ref_tips(root, prefixes=("refs/heads", "refs/tags")):
    raw = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)%00", *prefixes).stdout.split(b"\0")
    values = [item.decode("utf-8", "surrogateescape").lstrip("\n") for item in raw if item.strip(b"\n")]
    return list(zip(values[0::2], values[1::2]))


def _local_remote_has_ref(root, remote, ref):
    """Sandbox-safe fallback after ls-remote was attempted for a local bare remote."""
    raw = _git(root, "remote", "get-url", remote).stdout.decode("utf-8", "surrogateescape").strip()
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw) and not raw.lower().startswith("file://"):
        return None
    value = raw[7:] if raw.lower().startswith("file://") else raw
    path = Path(value)
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    probe = _git(root, "--git-dir=" + str(path), "rev-parse", "--is-bare-repository", check=False)
    if probe.returncode or probe.stdout.strip() != b"true":
        return None
    present = _git(root, "--git-dir=" + str(path), "show-ref", "--verify", "--quiet", ref, check=False)
    if present.returncode not in {0, 1}:
        return None
    return present.returncode == 0


def rewrite_unpublished(root, base, branch, commits, message_file, *, mapping_path=None, config_path=None):
    """Atomically squash one unpublished branch after scanning the replacement history."""
    root = Path(root).resolve()
    if commits != 1:
        raise ValidationError("Only --commits 1 is supported; another count is refused, never approximated")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", branch or "") or ".." in branch or "@{" in branch:
        raise ValidationError("Unsafe branch name")
    ref = "refs/heads/" + branch
    old_head = _git(root, "rev-parse", "--verify", ref + "^{commit}").stdout.decode("ascii").strip()
    if _git(root, "symbolic-ref", "-q", "HEAD").stdout.decode("utf-8").strip() != ref:
        raise ValidationError("Rewrite requires the named branch to be checked out")
    if _git(root, "rev-parse", "HEAD").stdout.decode("ascii").strip() != old_head:
        raise ValidationError("Checked-out HEAD differs from the named branch")
    if _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout:
        raise ValidationError("Rewrite requires a clean working tree and index")
    base_sha = _git(root, "rev-parse", "--verify", base + "^{commit}").stdout.decode("ascii").strip()
    if _git(root, "merge-base", "--is-ancestor", base_sha, old_head, check=False).returncode:
        raise ValidationError("Rewrite base must be an ancestor of the branch")
    old_commits = [line.decode("ascii") for line in _git(root, "rev-list", base_sha + ".." + old_head).stdout.splitlines()]
    if not old_commits:
        raise ValidationError("Rewrite range contains no commits")
    upstream = _git(root, "for-each-ref", "--format=%(upstream)", ref).stdout.decode("utf-8").strip()
    if upstream:
        raise ValidationError("Published-history rewrite refused: branch has an upstream; owner decision is out of scope")
    for remote_ref, _tip in _ref_tips(root, ("refs/remotes",)):
        if remote_ref.endswith("/" + branch):
            raise ValidationError("Published-history rewrite refused: a remote-tracking ref exists; owner decision is out of scope")
    remotes = [line.decode("utf-8") for line in _git(root, "remote").stdout.splitlines()]
    for remote in remotes:
        # Read-only local bare remotes are used by the refusal proof; no object
        # transfer or sub-protocol is invoked by ls-remote.
        probe = _git(root, "-c", "protocol.file.allow=always", "ls-remote", "--exit-code", "--heads", remote, ref,
                     check=False, timeout=60)
        if probe.returncode == 0 and probe.stdout:
            raise ValidationError("Published-history rewrite refused: branch exists on a remote; owner decision is out of scope")
        if probe.returncode == 2:
            continue
        local_present = _local_remote_has_ref(root, remote, ref)
        if local_present is True:
            raise ValidationError("Published-history rewrite refused: branch exists on a remote; owner decision is out of scope")
        if local_present is not False:
            raise ValidationError("Remote publication state could not be verified; rewrite refused without changes")
    for other_ref, tip in _ref_tips(root):
        if other_ref == ref:
            continue
        if any(_git(root, "merge-base", "--is-ancestor", commit, tip, check=False).returncode == 0 for commit in old_commits):
            raise ValidationError("Old branch commits are reachable from another branch or tag; rewrite refused")
    message = Path(message_file).read_bytes()
    if not message or b"\0" in message:
        raise ValidationError("Rewrite message file must contain a non-NUL commit message")
    old_tree = _git(root, "rev-parse", old_head + "^{tree}").stdout.decode("ascii").strip()
    object_dir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.decode("utf-8").strip())
    with tempfile.TemporaryDirectory(prefix="awf-publication-rewrite-") as temporary:
        temp_objects = Path(temporary) / "objects"
        temp_objects.mkdir()
        alternate = str(object_dir)
        extra = {"GIT_OBJECT_DIRECTORY": str(temp_objects), "GIT_ALTERNATE_OBJECT_DIRECTORIES": alternate}
        created = _git(root, "commit-tree", old_tree, "-p", base_sha, input_bytes=message, extra_env=extra).stdout.decode("ascii").strip()
        replacement_tree = _git(root, "rev-parse", created + "^{tree}", extra_env=extra).stdout.decode("ascii").strip()
        if replacement_tree != old_tree:
            raise ValidationError("Replacement commit tree differs from the original HEAD tree")
        scan = scan_repository(root, base_sha, created, mapping_path=mapping_path, config_path=config_path, extra_env=extra)
        if scan["status"] != "PASS":
            raise ValidationError("Replacement history failed publication scan; branch was not changed")
        object_format = _git(root, "rev-parse", "--show-object-format").stdout.decode("ascii").strip()
        if object_format not in {"sha1", "sha256"}:
            raise ValidationError("Unsupported Git object format")
        source = temp_objects / created[:2] / created[2:]
        destination = object_dir / created[:2] / created[2:]
        destination.parent.mkdir(parents=True, exist_ok=True)
        existed = destination.exists()
        if not existed:
            shutil.copyfile(source, destination)
        update = _git(root, "update-ref", ref, created, old_head, check=False)
        if update.returncode:
            if not existed:
                destination.unlink(missing_ok=True)
            raise ValidationError("Atomic branch update failed; branch was not changed")
        try:
            verify_publisher_tree(root, old_tree)
            if len(_git(root, "rev-list", base_sha + ".." + created).stdout.splitlines()) != 1:
                raise ValidationError("Replacement history does not contain exactly one commit")
            reachable = []
            for check_ref, tip in _ref_tips(root):
                for commit in old_commits:
                    if _git(root, "merge-base", "--is-ancestor", commit, tip, check=False).returncode == 0:
                        reachable.append(check_ref)
            if reachable:
                raise ValidationError("Old commits remain reachable from a branch or tag")
        except Exception:
            rollback = _git(root, "update-ref", ref, old_head, created, check=False)
            if rollback.returncode == 0 and not existed:
                destination.unlink(missing_ok=True)
            raise
    return {"status": "PASS", "branch": branch, "base_sha": base_sha, "old_head": old_head,
            "head_sha": created, "head_tree": old_tree, "commits": 1, "publication_scan": scan,
            "old_commits_reachable_from_branches_or_tags": False,
            "remote_branch_absent": True,
            "reflog_notice": "The old commit can remain in local reflogs and the object store. If it must be removed locally, expire relevant reflogs and prune unreachable objects under an operator-approved retention policy after preserving required recovery evidence.",
            "execution_authority": False}
