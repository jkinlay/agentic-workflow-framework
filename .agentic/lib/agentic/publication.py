"""History-aware publication scanning and unpublished-branch rewriting.

Findings deliberately contain only match digests.  Git paths are read from
NUL-delimited plumbing, while every content channel that is treated as text
must be strict UTF-8.  Ambiguous, binary, or over-budget content blocks a pass.
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
MAX_REGEX_ENTRIES = 64
MAX_REGEX_CHARS = 256
MAX_REGEX_REPEAT = 64
ALIAS = re.compile(r"^[a-z][a-z0-9_]*$")
UNC = re.compile(r"(?<![\\])\\\\[A-Za-z0-9][A-Za-z0-9._-]*[\\/][^\s<>:\"|?*]+(?:[\\/][^\s<>:\"|?*]+)*")
WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s<>:\"|?*]+[\\/]?)+")
HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s]+(?:/[^\s]*)?")
IP_CANDIDATE = re.compile(r"(?<![0-9A-Fa-f:.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
IPV6_CANDIDATE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
IPV4_PRIVATE_NETWORKS = tuple(ipaddress.ip_network((value, prefix)) for value, prefix in (
    (0x0A000000, 8), (0xAC100000, 12), (0xC0A80000, 16)))
IPV6_PRIVATE_NETWORK = ipaddress.ip_network((0xFC << 120, 7))

# Closed identities for public detector-definition/test-vector lines that were
# removed when AWF made its own tracked source scan-clean.  History scanning
# must retain deleted-line coverage, so these exact full-line identities avoid
# self-reference without accepting a value, regex, changed line, or other path.
# Project-tracked configuration cannot extend this set or disable a built-in.
_HISTORICAL_SELF_REFERENCES = {
    (".agentic/lib/agentic/publication.py", "builtin.private_ipv4"): frozenset({
        "4f515107bd58420bfded2fdb4eb0c0488264038ecfad82b5c745b6926af63d83",
        "6665bfc2585ba8a5e193709bd26d347b728432934d5102f9fbfc3ecf4a474cbd",
        "23c73dfb099cbf28a560053f2163b2eef79b93ab6ff5e5e9a09041dcc305e7a6",
    }),
    (".agentic/lib/agentic/publication.py", "builtin.private_ipv6"): frozenset({
        "4c668724866b52e2869dcbc51ca6cca0c9333b0ae8b4805b5a374c05a4518084",
    }),
    (".agentic/tests/test_operating.py", "builtin.windows_absolute"): frozenset({
        "c39c65eb607d83ae03418e589ac08a2a6c9240e0f65757516f364b5b89de522f",
    }),
}


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


def _validate_safe_regex(pattern, label):
    """Accept a deliberately small, bounded-backtracking regular-expression subset."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX_CHARS:
        raise ValidationError(f"{label} contains an empty or over-budget regular expression")
    # Character classes and escaped characters are literals for this structural
    # check.  The remaining syntax permits anchors, dots, and explicitly
    # bounded repeats, but no grouping, alternation, optional/open repetition,
    # lookaround, or backreferences.  At most one ranged repeat is allowed;
    # exact repeats do not introduce alternative match lengths.
    structural = re.sub(r"\[(?:\\.|[^]\\])*\]", "", pattern)
    structural = re.sub(r"\\.", "", structural)
    if any(token in structural for token in ("(", ")", "|", "*", "+", "?")):
        raise ValidationError(f"{label} contains an unsafe regular expression")
    spans = []
    ranged_repeats = 0
    for match in re.finditer(r"\{([0-9]+)(?:,([0-9]+))?\}", structural):
        lower = int(match.group(1))
        upper = int(match.group(2)) if match.group(2) is not None else lower
        if lower < 1 or lower > upper or upper > MAX_REGEX_REPEAT:
            raise ValidationError(f"{label} contains an unsafe regular expression")
        ranged_repeats += lower != upper
        spans.append(match.span())
    if ranged_repeats > 1:
        raise ValidationError(f"{label} contains an unsafe regular expression")
    without_repeats = structural
    for start, end in reversed(spans):
        without_repeats = without_repeats[:start] + without_repeats[end:]
    if "{" in without_repeats or "}" in without_repeats:
        raise ValidationError(f"{label} contains an unsafe regular expression")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValidationError(f"{label} contains an invalid regular expression") from exc
    if compiled.search("") is not None:
        raise ValidationError(f"{label} contains an empty-matching regular expression")
    return compiled


def _require_operator_local_mapping(root, path):
    """Reject a repository-controlled mapping before it can suppress built-ins."""
    root = Path(root).resolve()
    lexical = Path(path).absolute()
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        # The same checkout can have distinct lexical spellings (notably
        # /var and /private/var on macOS).  An in-repository mapping must not
        # become trusted merely because its caller used the other spelling.
        try:
            relative = lexical.resolve(strict=False).relative_to(root)
        except ValueError:
            return
    cursor = root
    for component in relative.parts:
        cursor /= component
        if cursor.is_symlink():
            raise ValidationError("Operator-local publication mapping must not traverse repository symlinks")
    tracked = _git(root, "ls-files", "--error-unmatch", "--", relative.as_posix(), check=False)
    ignored = _git(root, "check-ignore", "--quiet", "--no-index", "--", relative.as_posix(), check=False)
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise ValidationError("Operator-local publication mapping must be untracked and ignored")


def load_mapping(root, mapping_path=None):
    """Load the ignored operator-local mapping without returning private values in errors."""
    path = Path(mapping_path) if mapping_path else Path(root) / DEFAULT_MAPPING
    if not path.is_absolute():
        path = Path(root) / path
    if not path.exists():
        return {"version": 1, "aliases": {}, "deny_literals": [], "deny_regexes": [],
                "internal_hostnames": [], "builtin_allow": []}, None, path
    _require_operator_local_mapping(root, path)
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
        if not isinstance(values, list) or len(values) > MAX_REGEX_ENTRIES:
            raise ValidationError(f"Mapping {key} must be a bounded list")
        identifiers = set()
        for item in values:
            if not isinstance(item, dict) or set(item) != {"id", "pattern"} or not ALIAS.fullmatch(item["id"]):
                raise ValidationError(f"Mapping {key} entries need lower-case id and pattern")
            if item["id"] in identifiers:
                raise ValidationError(f"Mapping {key} entries need unique ids")
            identifiers.add(item["id"])
            _validate_safe_regex(item["pattern"], f"Mapping {key}")
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
    regexes = publication.get("deny_regexes", [])
    if not isinstance(regexes, list) or len(regexes) > MAX_REGEX_ENTRIES:
        raise ValidationError("Project publication deny_regexes must be a bounded list")
    identifiers = set()
    for item in regexes:
        if not isinstance(item, dict) or set(item) != {"id", "pattern"} or not ALIAS.fullmatch(item["id"]):
            raise ValidationError("Project publication regex entries need lower-case id and pattern")
        if item["id"] in identifiers:
            raise ValidationError("Project publication regex entries need unique ids")
        identifiers.add(item["id"])
        _validate_safe_regex(item["pattern"], "Project publication declarations")
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
        result.extend(Detector(f"{source}.regex.{item['id']}", _validate_safe_regex(
            item["pattern"], f"{source} publication regex")) for item in values)
    allows = [(item["id"], _validate_safe_regex(item["pattern"], "Local built-in allowance"))
              for item in mapping.get("builtin_allow", [])]
    return result, allows


def _redacted(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _private_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in IPV4_PRIVATE_NETWORKS)
    return address in IPV6_PRIVATE_NETWORK


def _historical_self_reference(path, detector_id, line):
    identities = _HISTORICAL_SELF_REFERENCES.get((path, detector_id), ())
    return hashlib.sha256(line.encode("utf-8", "surrogatepass")).hexdigest() in identities


def _scan_text(text, *, commit, path, source, detectors, allows, line_offset=0, change=None):
    findings = []
    for number, line in enumerate(text.splitlines() or [text], 1):
        for detector in detectors:
            for match in detector.regex.finditer(line):
                value = match.group(0)
                if detector.private_ip and not _private_ip(value):
                    continue
                if _historical_self_reference(path, detector.detector_id, line):
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
    try:
        return value.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid-utf8"


def _changed_pairs(root, old, new, extra_env=None):
    arguments = ["diff-tree", "-r", "-z", "--no-commit-id", "--name-status", "-M"]
    if old is None:
        arguments.extend(("--root", new))
    else:
        arguments.extend((old, new))
    raw = _git(root, *arguments, extra_env=extra_env).stdout.split(b"\0")
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
    base_tree = tree_cache.setdefault(base_sha, _tree(root, base_sha, extra_env))
    # Keep provenance per commit.  A traversal-global pathname map makes merge
    # results depend on parent order when disconnected roots reuse a path.
    # Each parent transition is derived independently and merge results retain
    # a base origin if any parent carries one.
    origin_maps = {base_sha: {path: path for path in base_tree}}
    touched_head_paths = set()
    for commit in commits:
        raw_commit = _git(root, "cat-file", "commit", commit, extra_env=extra_env).stdout
        message = raw_commit.split(b"\n\n", 1)[1] if b"\n\n" in raw_commit else b""
        try:
            message_text = message.decode("utf-8")
        except UnicodeDecodeError:
            unscanned.append({"commit": commit, "path": "message", "source": "message",
                              "reason": "invalid-utf8", "parent": None})
        else:
            findings += _scan_text(message_text, commit=commit, path="message", source="message",
                                   detectors=detectors, allows=allows)
        parents = _git(root, "rev-list", "--parents", "-n", "1", commit, extra_env=extra_env).stdout.decode("ascii").split()[1:]
        commit_tree = tree_cache.setdefault(commit, _tree(root, commit, extra_env))
        transition_maps = []
        for parent in parents or [None]:
            if parent is None:
                parent_origins = {}
            elif parent in origin_maps:
                parent_origins = origin_maps[parent]
            elif _git(root, "merge-base", "--is-ancestor", parent, base_sha,
                      extra_env=extra_env, check=False).returncode == 0:
                parent_tree = tree_cache.setdefault(parent, _tree(root, parent, extra_env))
                parent_origins = {path: path for path in parent_tree}
            else:
                raise ValidationError("Publication history traversal omitted a merge parent")
            transitioned = dict(parent_origins)
            transition_maps.append(transitioned)
            for status, before_path, after_path in _changed_pairs(root, parent, commit, extra_env):
                old_tree = {} if parent is None else tree_cache.setdefault(parent, _tree(root, parent, extra_env))
                origin = parent_origins.get(before_path)
                if after_path is not None:
                    touched_head_paths.add(after_path)
                    if status.startswith("A"):
                        transitioned[after_path] = None
                    elif status.startswith(("R", "C")):
                        transitioned[after_path] = origin
                    else:
                        transitioned[after_path] = origin
                if status.startswith("R") and before_path != after_path:
                    transitioned.pop(before_path, None)
                elif status.startswith("D"):
                    transitioned.pop(before_path, None)
                output_path = _path_for_output(after_path or before_path, detectors, allows)
                path_text = (before_path or "") + "\n" + (after_path or "")
                findings += _scan_text(path_text, commit=commit, path=output_path, source="patch-path",
                                       detectors=detectors, allows=allows)
                old_text, old_reason = ("", None)
                new_text, new_reason = ("", None)
                if before_path and before_path in old_tree and old_tree[before_path][1] == "blob":
                    old_text, old_reason = _blob(root, old_tree[before_path][2], extra_env)
                if after_path and after_path in commit_tree and commit_tree[after_path][1] == "blob":
                    new_text, new_reason = _blob(root, commit_tree[after_path][2], extra_env)
                if old_reason or new_reason:
                    unscanned.append({"commit": commit, "path": output_path, "source": "patch",
                                      "reason": old_reason or new_reason, "parent": parent})
                    continue
                old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
                matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
                for opcode, a1, a2, b1, b2 in matcher.get_opcodes():
                    if opcode in {"delete", "replace"} and origin is None:
                        findings += _scan_text("\n".join(old_lines[a1:a2]), commit=commit, path=output_path,
                                               source="patch", change="deleted", line_offset=a1,
                                               detectors=detectors, allows=allows)
                    if opcode in {"insert", "replace"}:
                        findings += _scan_text("\n".join(new_lines[b1:b2]), commit=commit, path=output_path,
                                               source="patch", change="added", line_offset=b1,
                                               detectors=detectors, allows=allows)
        for transitioned in transition_maps:
            for path in list(transitioned):
                if path not in commit_tree:
                    transitioned.pop(path)
            for path in commit_tree:
                transitioned.setdefault(path, None)
        origin_maps[commit] = {
            path: next((origin for origin in (values.get(path) for values in transition_maps)
                        if origin is not None), None)
            for path in commit_tree
        }
    head_tree = tree_cache.setdefault(head_sha, _tree(root, head_sha, extra_env))
    for path in sorted(touched_head_paths):
        if path is None or path not in head_tree or head_tree[path][1] != "blob":
            continue
        output_path = _path_for_output(path, detectors, allows)
        new_text, reason = _blob(root, head_tree[path][2], extra_env)
        if reason:
            unscanned.append({"commit": head_sha, "path": output_path, "source": "current-file", "reason": reason,
                              "parent": None})
        else:
            findings += _scan_text(new_text, commit=head_sha, path=output_path, source="current-file",
                                   detectors=detectors, allows=allows)
    body_entries, comment_entries = [], []
    for values, entries, channel in ((pr_body_texts, body_entries, "pr-body"),
                                     (comment_texts, comment_entries, "comment")):
        for value in values:
            if not isinstance(value, str):
                raise ValidationError(f"Publication {channel} text must be a string")
            try:
                raw = value.encode("utf-8")
                reason = "oversize" if len(raw) > MAX_TEXT_BYTES else ("binary" if "\0" in value else None)
            except UnicodeEncodeError:
                raw, reason = value.encode("utf-8", "surrogatepass"), "invalid-utf8"
            entries.append((value if reason is None else None, raw, reason))
    for paths, entries, channel in ((pr_body_paths, body_entries, "pr-body"),
                                    (comment_paths, comment_entries, "comment")):
        for source_path in paths:
            try:
                raw = Path(source_path).read_bytes()
            except OSError as exc:
                raise ValidationError(f"Publication {channel} input could not be read: {type(exc).__name__}") from exc
            reason = "oversize" if len(raw) > MAX_TEXT_BYTES else ("binary" if b"\0" in raw else None)
            text = None
            if reason is None:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    reason = "invalid-utf8"
            entries.append((text, raw, reason))
    body_hashes = [hashlib.sha256(raw).hexdigest() for _text, raw, _reason in body_entries]
    comment_hashes = [hashlib.sha256(raw).hexdigest() for _text, raw, _reason in comment_entries]
    for entries, channel in ((body_entries, "pr-body"), (comment_entries, "comment")):
        for text, _raw, reason in entries:
            if reason:
                unscanned.append({"commit": head_sha, "path": channel, "source": channel,
                                  "reason": reason, "parent": None})
            else:
                findings += _scan_text(text, commit=head_sha, path=channel, source=channel,
                                       detectors=detectors, allows=allows)
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
                         "strict_utf8": True, "pr_bodies": bool(body_entries),
                         "pr_comments": bool(comment_entries)},
            "execution_authority": False}


def render_scan(result):
    lines = [f"Publication scan: {result['status']}", f"Base: {result['base_sha']}", f"Head: {result['head_sha']}",
             f"Commits scanned: {len(result['commits_scanned'])}", f"Findings: {len(result['findings'])}",
             f"Unscanned binary/oversize/invalid-UTF-8 entries: {len(result['unscanned'])}"]
    for item in result["findings"]:
        line = "" if item["line"] is None else f":{item['line']}"
        lines.append(f"- {item['commit']} {item['path']}{line} {item['detector_id']} {item['redacted_excerpt']}")
    for item in result["unscanned"]:
        lines.append(f"- NOT SCANNED {item['commit']} {item['path']} ({item['reason']})")
    return "\n".join(lines) + "\n"


def _ref_tips(root, prefixes=("refs/heads", "refs/tags", "refs/remotes")):
    raw = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)%00", *prefixes).stdout.split(b"\0")
    values = [item.decode("utf-8", "surrogateescape").lstrip("\n") for item in raw if item.strip(b"\n")]
    return list(zip(values[0::2], values[1::2]))


def _local_remote_has_ref(root, url, ref):
    """Sandbox-safe fallback after ls-remote was attempted for a local bare remote."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url) and not url.lower().startswith("file://"):
        return None
    value = url[7:] if url.lower().startswith("file://") else url
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


def _remote_urls(root, remote):
    """Return every distinct fetch and push endpoint configured for a remote."""
    values = set()
    for arguments in (("--all", remote), ("--push", "--all", remote)):
        result = _git(root, "remote", "get-url", *arguments, check=False)
        if result.returncode:
            raise ValidationError("Remote URL inventory could not be verified; rewrite refused without changes")
        try:
            values.update(line.decode("utf-8") for line in result.stdout.splitlines() if line)
        except UnicodeDecodeError as exc:
            raise ValidationError("Remote URL inventory is not UTF-8; rewrite refused without changes") from exc
    if not values:
        raise ValidationError("Remote URL inventory is empty; rewrite refused without changes")
    return sorted(values)


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
    remotes = [line.decode("utf-8") for line in _git(root, "remote").stdout.splitlines()]
    for remote in remotes:
        for url in _remote_urls(root, remote):
            # Read-only local bare remotes are used by the refusal proof; no
            # object transfer or sub-protocol is invoked by ls-remote.
            probe = _git(root, "-c", "protocol.file.allow=always", "ls-remote", "--exit-code",
                         "--heads", url, ref, check=False, timeout=60)
            if probe.returncode == 0 and probe.stdout:
                raise ValidationError("Published-history rewrite refused: branch exists on a configured remote URL; owner decision is out of scope")
            if probe.returncode == 2:
                continue
            local_present = _local_remote_has_ref(root, url, ref)
            if local_present is True:
                raise ValidationError("Published-history rewrite refused: branch exists on a configured remote URL; owner decision is out of scope")
            if local_present is not False:
                raise ValidationError("Remote publication state could not be verified; rewrite refused without changes")
    for other_ref, tip in _ref_tips(root):
        if other_ref == ref:
            continue
        if any(_git(root, "merge-base", "--is-ancestor", commit, tip, check=False).returncode == 0 for commit in old_commits):
            raise ValidationError("Old branch commits are reachable from another local or remote-tracking ref; rewrite refused")
    message = Path(message_file).read_bytes()
    if not message or b"\0" in message:
        raise ValidationError("Rewrite message file must contain a non-NUL commit message")
    try:
        message.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Rewrite message file must be strict UTF-8") from exc
    old_tree = _git(root, "rev-parse", old_head + "^{tree}").stdout.decode("ascii").strip()
    verify_publisher_tree(root, old_tree)
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
        if len(_git(root, "rev-list", base_sha + ".." + created, extra_env=extra).stdout.splitlines()) != 1:
            raise ValidationError("Replacement history does not contain exactly one commit")
        object_format = _git(root, "rev-parse", "--show-object-format").stdout.decode("ascii").strip()
        if object_format not in {"sha1", "sha256"}:
            raise ValidationError("Unsupported Git object format")
        raw_object = _git(root, "cat-file", "commit", created, extra_env=extra).stdout
    # The quarantine has been fully validated and removed before the only two
    # persistent operations. Git writes the object atomically and update-ref is
    # the final operation. If that CAS fails, retain the validated object:
    # no ref census can exclude a validated lock being renamed immediately
    # after the census, and ordinary Git grace/GC safely handles unreachable
    # objects without risking a concurrent ref, pseudoref or reflog.
    installed = _git(root, "hash-object", "-t", "commit", "-w", "--stdin",
                     input_bytes=raw_object).stdout.decode("ascii").strip()
    installed_present = _git(root, "cat-file", "-e", created + "^{commit}", check=False).returncode == 0
    if installed != created or not installed_present:
        raise ValidationError("Replacement object installation did not preserve its identity")
    update = _git(root, "update-ref", ref, created, old_head, check=False)
    if update.returncode:
        raise ValidationError("Atomic branch update failed; branch was not changed and the validated replacement object was retained for Git recovery/GC")
    return {"status": "PASS", "branch": branch, "base_sha": base_sha, "old_head": old_head,
            "head_sha": created, "head_tree": old_tree, "commits": 1, "publication_scan": scan,
            "old_commits_reachable_from_branches_or_tags": False,
            "old_commits_reachable_from_local_or_remote_tracking_refs": False,
            "remote_branch_absent": True,
            "remote_push_branch_absent": True,
            "reflog_notice": "The old commit can remain in local reflogs and the object store. If it must be removed locally, expire relevant reflogs and prune unreachable objects under an operator-approved retention policy after preserving required recovery evidence.",
            "execution_authority": False}
