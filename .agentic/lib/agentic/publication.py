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
import stat
import subprocess
import tempfile
import uuid
import zlib

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

# PR #34 initially used short Jira-shaped labels for synthetic controller test
# tickets. The published commits cannot be rewritten without owner authority.
# These identities cover only those exact historical full lines, in that exact
# test path, for the operator-local restricted-identifier detector. Current
# fixtures use EX-* labels. A changed line, path, detector, or Jira value is
# still a finding.
_HISTORICAL_SYNTHETIC_TEST_REFERENCES = {
    ".agentic/tests/test_continuous_controller.py": frozenset({
        "0a0472d6c185682c5886ba6fe5dc6af41c5afa439a434bb90dd45afb0fca89a3",
        "0db2d0bfb0d2d2ea6af3b5e6d4e1b544b753fe14de176580072795747b7ed1c6",
        "0e2416a558804e03b323b89435ffb6b6a1a5d4b53a1a4f86f8ed0f833fdf6c57",
        "168ef0f6eb2e14bfc3dd281f4457152c3204313b42474724ec8374c26e555fc8",
        "18e161041415c5c2b5568f462a42f3f5c01d76f9774b4f4f56448287bcfcb85b",
        "195f617308ef7689e34a78db6277135d9e53644d752467778c6a096c60213e49",
        "1a39bd27bbc855b7a746124ebfe465a885ff91aaa30a4991a6cb641002b17af1",
        "2fa632f95da7665069ee0dd85f92c9240fd8529c03f07325022e9b9f2bc185f3",
        "457bf8a93440d088996e833d5fdfc336e9dad1d0f66fed91603b8379fab05d5d",
        "47fc1047b6bc1ab8c3a859cdbc0174d2a54ba65a2aadd9c289bab5553cbb5b62",
        "4e20399bb57229eaa1afdb5d97be8b15cf275379a958ad6ad42812066a152619",
        "53f340f5b7419bad5f1293ec03a53253c7546837fc72728b66e8e7044fbf75bd",
        "5498acbf26ec57a3f77e81f3ce65964e80ce61329f8eafcde2581c0f7490f70c",
        "5883dfaeb2709a2d6ac130314dad4d75a6f67ae14ca310dc83d8df1072eb6fa1",
        "5f42210bc3da0981a6160409e6a5be91a0805183d98341569e4d5d483010a593",
        "648c5a3185bd32df97cb0871e7ddfb13a771ceda8311797fac0aecb9a7338be2",
        "6cfc7156b4d100242ca7f84c5ff9f47c166b497a5b7807df4c28d8d0ce7d087f",
        "6fe138c3864e43a93a2a8e3570cdc27e85e26c1a6ae004fb04a3d171150b03fb",
        "7b01bf7a27182c7c1005d73fb666fa9d1ac32139c0c4a5e181168eb349fbe0e5",
        "7ce66651fd1556eaf265ccda36c6cba9ae50f3e02c38bb8d5fa4e521ec2a53b4",
        "7d1978c4d447193cd6181b614a839df63c5e38485167a002fffb5dc9ff610718",
        "7edc6197541b815e71cc2bb6bd71f8ab7105d609d0c57860639a3f14c51e940c",
        "89a0198387ccef34a546e6d3c70fa59a75d3d9571923b6efd917b991fbdd4da9",
        "8d8fcb6aff357d6e6a0d0db8feaf236d3f9961fd140bb38f4582009344c4e71d",
        "982d3d873252621721e6b423e25f16b811e2dd085f85331b5e63b30e745720b8",
        "9a33402f86d5e602e4be01e7a230f33e7b317a7e57366f4f0d7f28677e2edf74",
        "9cf0ce46d56eaece7ab9af8cd18ddc286877c53b5a031cee272157a17445e9e7",
        "a826adddc50cafda162a9e707fe6017c0ccd8b4680243b15eee8ce4c0ece35b3",
        "a884bbd9fdc64dea0738cd8ecd8953fc76942eb3658baa158f792b30dc50f0fa",
        "b1af1cddea32f05eb6f4b939cf844df875c9e174f956dae70660042bafd71066",
        "beb3ffac8b924cf4fae6303be78d142a914616a1a2a6aec7cd925f92caaee328",
        "c778b3303c665ea0f89259037f75046ea46253e4f28ecb05aeab0bd151bda386",
        "ce49c5023cdde26dc105bd516c4828ad859360cf4dc060fa6276be71eaa70534",
        "d8e71f1a9287d97ecdddc4d6b1d2084b2e3e309651cc5915ad81fd20515e7dd4",
        "ebd0ddb6e79206d6b3a7773001b93adc4f565568695d8a33cd0062f613ebe127",
        "ed928b55071730ca2880f0eae5202a63af10a98763a62acdca5b34fb81fbf56b",
        "edca96ab210b7ff2916ca74bc11b5d2dbe8e702241d91924fbbc1f85347f598b",
        "f684483718de3a6cb8f8bf546c22f91f3bffbf3db891f8da706de6d6f8bf2fba",
        "fb650fa803749af726932b21de36cd97c54d5ee33f3478d4d0beb72d0235a9bd",
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
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationError("Operator-local publication mapping identity could not be resolved") from exc

    def reparse_point(candidate):
        try:
            if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
                return True
            attributes = getattr(candidate.lstat(), "st_file_attributes", 0)
            return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        except OSError as exc:
            raise ValidationError("Operator-local publication mapping path could not be inspected") from exc

    # Inspect every existing lexical component.  resolve() alone is not a
    # provenance proof on Windows because junction/reparse traversal can make
    # an apparently external path name repository-controlled content.
    cursor = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        cursor /= component
        if cursor.exists() and reparse_point(cursor):
            raise ValidationError("Operator-local publication mapping must not traverse symlinks, junctions, or reparse points")

    def relative_to_root(candidate):
        try:
            return candidate.relative_to(root)
        except ValueError:
            return None

    lexical_relative = relative_to_root(lexical)
    resolved_relative = relative_to_root(resolved)
    raw_index = _git(root, "ls-files", "-z").stdout
    raw_head = _git(root, "ls-tree", "-rz", "--name-only", "HEAD").stdout
    tracked_paths = {item.decode("utf-8", "surrogateescape") for raw in (raw_index, raw_head)
                     for item in raw.split(b"\0") if item}
    key = (lambda value: value.casefold()) if os.name == "nt" else (lambda value: value)
    tracked_keys = {key(value) for value in tracked_paths}
    for relative in (lexical_relative, resolved_relative):
        if relative is not None and key(relative.as_posix()) in tracked_keys:
            raise ValidationError("Operator-local publication mapping must not resolve to tracked content")
    # File identity closes hard-link and alternate-spelling aliases that path
    # normalization cannot prove, including Windows case-insensitive names.
    for tracked_path in tracked_paths:
        candidate = root / Path(tracked_path)
        try:
            if candidate.exists() and os.path.samefile(resolved, candidate):
                raise ValidationError("Operator-local publication mapping must not share identity with tracked content")
        except OSError as exc:
            raise ValidationError("Tracked mapping identity could not be verified") from exc
    for relative in {item for item in (lexical_relative, resolved_relative) if item is not None}:
        ignored = _git(root, "check-ignore", "--quiet", "--no-index", "--", relative.as_posix(), check=False)
        if ignored.returncode != 0:
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


def _historical_synthetic_test_reference(path, detector_id, line, value):
    if detector_id != "local.regex.restricted_identifier":
        return False
    if re.fullmatch("QA-" + r"[1-6]", value, re.IGNORECASE) is None:
        return False
    identities = _HISTORICAL_SYNTHETIC_TEST_REFERENCES.get(path, ())
    return hashlib.sha256(line.encode("utf-8", "surrogatepass")).hexdigest() in identities
def _matching_values(text, *, path, detectors, allows, line_offset=0):
    """Yield detector matches without normalizing the raw matched value."""
    for number, line in enumerate(text.splitlines() or [text], 1):
        for detector in detectors:
            for match in detector.regex.finditer(line):
                value = match.group(0)
                if detector.private_ip and not _private_ip(value):
                    continue
                if _historical_self_reference(path, detector.detector_id, line):
                    continue
                if _historical_synthetic_test_reference(path, detector.detector_id, line, value):
                    continue
                if detector.detector_id.startswith("builtin.") and any(
                        allow_id in {"all", detector.detector_id.removeprefix("builtin.")} and pattern.search(value)
                        for allow_id, pattern in allows):
                    continue
                yield number + line_offset, detector.detector_id, value


def _scan_text(text, *, commit, path, source, detectors, allows, line_offset=0, change=None,
               base_membership=(), force_blocking=False):
    findings = []
    for number, detector_id, value in _matching_values(
            text, path=path, detectors=detectors, allows=allows, line_offset=line_offset):
        classification = "BLOCKING" if force_blocking or change == "added" else (
            "PRE_EXISTING" if (detector_id, value) in base_membership else "BLOCKING")
        findings.append({"commit": commit, "path": path, "line": number,
                         "source": source, "change": change, "detector_id": detector_id,
                         "classification": classification, "redacted_excerpt": _redacted(value)})
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


def _blob(root, oid, extra_env=None, *, classification=False):
    size_result = _git(root, "cat-file", "-s", oid, extra_env=extra_env, check=False)
    if size_result.returncode:
        raise ValidationError("Git blob size could not be verified before reading")
    try:
        size = int(size_result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError("Git blob size is invalid") from exc
    if size > MAX_TEXT_BYTES:
        return None, "oversize"
    value = _git(root, "cat-file", "blob", oid, extra_env=extra_env).stdout
    if len(value) > MAX_TEXT_BYTES:
        return None, "oversize"
    if classification:
        # Base membership is not publication output. Search every bounded byte
        # losslessly so binary/invalid bytes cannot hide a proven exact value.
        return value.decode("utf-8", "surrogateescape"), None
    if b"\0" in value:
        return None, "binary"
    try:
        return value.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "invalid-utf8"


def _provider_file(path):
    """Read provider text with bounded memory while hashing all supplied bytes."""
    digest = hashlib.sha256()
    chunks, total = [], 0
    try:
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, MAX_TEXT_BYTES + 1))
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if total <= MAX_TEXT_BYTES:
                    chunks.append(chunk)
                else:
                    chunks.clear()
    except OSError as exc:
        raise ValidationError(f"Publication provider input could not be read: {type(exc).__name__}") from exc
    if total > MAX_TEXT_BYTES:
        return None, digest.hexdigest(), "oversize"
    raw = b"".join(chunks)
    if b"\0" in raw:
        return None, digest.hexdigest(), "binary"
    try:
        return raw.decode("utf-8"), digest.hexdigest(), None
    except UnicodeDecodeError:
        return None, digest.hexdigest(), "invalid-utf8"


def _provider_text(value):
    """Hash already-supplied provider text without a second unbounded allocation."""
    if not isinstance(value, str):
        raise ValidationError("Publication provider text must be a string")
    digest = hashlib.sha256()
    total, binary, invalid = 0, False, False
    for offset in range(0, len(value), 256 * 1024):
        chunk = value[offset:offset + 256 * 1024]
        try:
            raw = chunk.encode("utf-8")
        except UnicodeEncodeError:
            raw = chunk.encode("utf-8", "surrogatepass")
            invalid = True
        digest.update(raw)
        total += len(raw)
        binary = binary or b"\0" in raw
    reason = "oversize" if total > MAX_TEXT_BYTES else (
        "binary" if binary else ("invalid-utf8" if invalid else None))
    return (value if reason is None else None), digest.hexdigest(), reason


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
    # Classification is exact and case-sensitive.  Known positives survive an
    # incomplete base scan, while every unknown remains blocking and the
    # unscanned base entry independently prevents PASS.
    base_membership = set()
    for base_path, (_mode, kind, oid) in sorted(base_tree.items()):
        if kind != "blob":
            continue
        output_path = _path_for_output(base_path, detectors, allows)
        base_text, base_reason = _blob(root, oid, extra_env, classification=True)
        if base_reason:
            unscanned.append({"commit": base_sha, "path": output_path, "source": "base-classification",
                              "reason": base_reason, "parent": None})
            continue
        for _line, detector_id, value in _matching_values(
                base_text, path=base_path, detectors=detectors, allows=allows):
            base_membership.add((detector_id, value))
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
                    if opcode in {"delete", "replace"}:
                        findings += _scan_text("\n".join(old_lines[a1:a2]), commit=commit, path=output_path,
                                               source="patch", change="deleted", line_offset=a1,
                                               detectors=detectors, allows=allows,
                                               base_membership=base_membership)
                    if opcode in {"insert", "replace"}:
                        findings += _scan_text("\n".join(new_lines[b1:b2]), commit=commit, path=output_path,
                                               source="patch", change="added", line_offset=b1,
                                               detectors=detectors, allows=allows,
                                               base_membership=base_membership, force_blocking=True)
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
                                   detectors=detectors, allows=allows, base_membership=base_membership)
    body_entries, comment_entries = [], []
    for values, entries, channel in ((pr_body_texts, body_entries, "pr-body"),
                                     (comment_texts, comment_entries, "comment")):
        for value in values:
            try:
                entries.append(_provider_text(value))
            except ValidationError as exc:
                raise ValidationError(f"Publication {channel} text must be a string") from exc
    for paths, entries, channel in ((pr_body_paths, body_entries, "pr-body"),
                                    (comment_paths, comment_entries, "comment")):
        for source_path in paths:
            text, digest, reason = _provider_file(source_path)
            entries.append((text, digest, reason))
    body_hashes = [digest for _text, digest, _reason in body_entries]
    comment_hashes = [digest for _text, digest, _reason in comment_entries]
    for entries, channel in ((body_entries, "pr-body"), (comment_entries, "comment")):
        for text, _digest, reason in entries:
            if reason:
                unscanned.append({"commit": head_sha, "path": channel, "source": channel,
                                  "reason": reason, "parent": None})
            else:
                findings += _scan_text(text, commit=head_sha, path=channel, source=channel,
                                       detectors=detectors, allows=allows)
    findings.sort(key=lambda item: (item["commit"] or "", item["path"] or "", item["line"] or 0, item["detector_id"]))
    blocking_count = sum(item["classification"] == "BLOCKING" for item in findings)
    pre_existing_count = sum(item["classification"] == "PRE_EXISTING" for item in findings)
    status = "BLOCKED" if blocking_count or unscanned else "PASS"
    return {"schema_version": 3, "status": status, "base_sha": base_sha, "head_sha": head_sha,
            "pr_body_sha256": body_hashes[0] if len(body_hashes) == 1 else None,
            "additional_pr_body_sha256": body_hashes[1:], "comment_sha256": comment_hashes,
            "mapping_sha256": mapping_sha, "project_config_sha256": config_sha,
            "mapping_loaded": mapping_sha is not None, "mapping_location": DEFAULT_MAPPING.as_posix(),
            "commits_scanned": commits, "findings": findings,
            "total_findings": len(findings), "blocking_findings": blocking_count,
            "pre_existing_findings": pre_existing_count,
            "unscanned": unscanned, "unscanned_count": len(unscanned),
            "coverage": {"current_files": True, "commit_messages": True, "every_patch": True,
                         "generated_reports": "when committed or passed as provider text",
                         "captured_command_output": "when committed or passed as provider text",
                         "strict_utf8": True, "pr_bodies": bool(body_entries),
                         "pr_comments": bool(comment_entries)},
            "execution_authority": False}


def render_scan(result):
    lines = [f"Publication scan: {result['status']}", f"Base: {result['base_sha']}", f"Head: {result['head_sha']}",
             f"Commits scanned: {len(result['commits_scanned'])}",
             f"Findings: {result['total_findings']} ({result['blocking_findings']} blocking, "
             f"{result['pre_existing_findings']} pre-existing)",
             f"Unscanned binary/oversize/invalid-UTF-8 entries: {len(result['unscanned'])}"]
    for item in result["findings"]:
        line = "" if item["line"] is None else f":{item['line']}"
        lines.append(f"- {item['classification']} {item['commit']} {item['path']}{line} "
                     f"{item['detector_id']} {item['redacted_excerpt']}")
    for item in result["unscanned"]:
        lines.append(f"- NOT SCANNED {item['commit']} {item['path']} ({item['reason']})")
    return "\n".join(lines) + "\n"


def _ref_tips(root):
    """Enumerate every ref namespace."""
    raw = _git(root, "for-each-ref", "--format=%(refname)%00%(objectname)%00").stdout.split(b"\0")
    values = [item.decode("utf-8", "surrogateescape").lstrip("\n") for item in raw if item.strip(b"\n")]
    return list(zip(values[0::2], values[1::2]))


def _is_ancestor(root, older, newer):
    """Distinguish a proven non-ancestor from a failed reachability lookup."""
    result = _git(root, "merge-base", "--is-ancestor", older, newer, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise ValidationError("Git reachability lookup failed; rewrite refused without changes")


_PSEUDOREFS = ("ORIG_HEAD", "FETCH_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD",
               "REVERT_HEAD", "REBASE_HEAD", "AUTO_MERGE", "BISECT_HEAD")


def _git_common_dir(root):
    value = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.decode("utf-8").strip()
    return Path(value).resolve(strict=True)


def _file_bytes(path):
    try:
        path = Path(path)
        if not path.exists():
            return None
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if (not path.is_file() or path.is_symlink() or
                (hasattr(path, "is_junction") and path.is_junction()) or
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValidationError("Rewrite state file is not a canonical regular file")
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError("Rewrite state file could not be read") from exc


def _tree_files(path):
    root = Path(path)
    if not root.exists():
        return {}
    try:
        result = {}
        for current, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                item = Path(current) / name
                attributes = getattr(item.lstat(), "st_file_attributes", 0)
                if (item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()) or
                        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                    raise ValidationError("Rewrite state inventory contains an alias")
            for name in files:
                item = Path(current) / name
                if not item.is_file():
                    raise ValidationError("Rewrite state inventory contains a non-file")
                result[item.relative_to(root).as_posix()] = item.read_bytes()
        return result
    except OSError as exc:
        raise ValidationError("Rewrite state inventory could not be read") from exc


def _worktree_state(root):
    records, current = [], {}
    for raw in _git(root, "worktree", "list", "--porcelain").stdout.decode("utf-8", "surrogateescape").splitlines() + [""]:
        if not raw:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = raw.partition(" ")
        current[key] = value
    result = {}
    for record in records:
        path = str(Path(record["worktree"]).resolve())
        git_dir = Path(_git(path, "rev-parse", "--path-format=absolute", "--absolute-git-dir")
                       .stdout.decode("utf-8").strip()).resolve(strict=True)
        head = _git(path, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode("ascii").strip()
        symbolic = _git(path, "symbolic-ref", "-q", "HEAD", check=False)
        symbol = symbolic.stdout.decode("utf-8").strip() if symbolic.returncode == 0 else None
        status = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        pseudos = {}
        for name in _PSEUDOREFS:
            probe = _git(path, "rev-parse", "--verify", "--quiet", name + "^{commit}", check=False)
            if probe.returncode == 0:
                pseudos[name] = probe.stdout.decode("ascii").strip()
            elif probe.returncode not in {1, 128}:
                raise ValidationError("Pseudoref inventory failed; rewrite refused")
        pseudo_files = {name: _file_bytes(git_dir / name) for name in _PSEUDOREFS}
        result[path] = {"head": head, "symbolic": symbol, "status": status,
                        "pseudos": pseudos, "pseudo_files": pseudo_files}
    return result


def _object_ids(root):
    raw = _git(root, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)").stdout
    return tuple(sorted(line.decode("ascii") for line in raw.splitlines() if line))


def _rewrite_snapshot(root):
    common = _git_common_dir(root)
    object_dir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.decode("utf-8").strip())
    try:
        object_resolved = object_dir.resolve(strict=True)
        object_identity = (object_resolved.stat().st_dev, object_resolved.stat().st_ino)
    except OSError as exc:
        raise ValidationError("Primary object directory identity could not be proved") from exc
    indexes = {"index": _file_bytes(common / "index")}
    logs = _tree_files(common / "logs")
    worktrees = common / "worktrees"
    if worktrees.exists():
        for item in worktrees.iterdir():
            indexes["worktrees/" + item.name + "/index"] = _file_bytes(item / "index")
            for key, value in _tree_files(item / "logs").items():
                logs["worktrees/" + item.name + "/" + key] = value
    alternate = _file_bytes(object_resolved / "info" / "alternates")
    object_format = _git(root, "rev-parse", "--show-object-format").stdout.decode("ascii").strip()
    if object_format not in {"sha1", "sha256"}:
        raise ValidationError("Unsupported Git object format")
    return {"refs": dict(_ref_tips(root)), "worktrees": _worktree_state(root),
            "logs": logs, "indexes": indexes,
            "alternate": alternate, "object_format": object_format,
            "object_dir": str(object_resolved), "object_identity": object_identity,
            "objects": _object_ids(root)}


class _RewriteLock:
    def __init__(self, root):
        self.path = _git_common_dir(root) / "awf-publication-rewrite.lock"
        self.token = (str(os.getpid()) + ":" + uuid.uuid4().hex).encode("ascii")

    def __enter__(self):
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(self.token)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ValidationError("Repository-wide publication rewrite lock is unavailable") from exc
        return self

    def __exit__(self, *_args):
        try:
            if self.path.read_bytes() == self.token:
                self.path.unlink()
        except OSError:
            pass


def _canonical_object_dir(root, snapshot):
    common = _git_common_dir(root)
    expected = common / "objects"
    try:
        resolved = expected.resolve(strict=True)
        if str(resolved) != snapshot["object_dir"] or (resolved.stat().st_dev, resolved.stat().st_ino) != snapshot["object_identity"]:
            raise ValidationError("Primary object directory identity changed")
        cursor = common
        for component in expected.relative_to(common).parts:
            cursor /= component
            attributes = getattr(cursor.lstat(), "st_file_attributes", 0)
            if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValidationError("Primary object directory traverses an alias")
    except (OSError, ValueError) as exc:
        raise ValidationError("Primary object directory identity could not be proved") from exc
    return resolved


def _quarantine_objects(root, object_dir, extra_env, object_format):
    length = 40 if object_format == "sha1" else 64
    records = []
    for path in sorted(Path(object_dir).glob("[0-9a-f][0-9a-f]/*")):
        oid = path.parent.name + path.name
        if not re.fullmatch(rf"[0-9a-f]{{{length}}}", oid):
            raise ValidationError("Quarantine contains an invalid object path")
        kind = _git(root, "cat-file", "-t", oid, extra_env=extra_env).stdout.decode("ascii").strip()
        raw = _git(root, "cat-file", kind, oid, extra_env=extra_env).stdout
        preexisting = _git(root, "cat-file", "-e", oid, check=False).returncode == 0
        records.append({"oid": oid, "kind": kind, "raw": raw, "preexisting": preexisting})
    if not records:
        raise ValidationError("Quarantine object inventory is empty")
    return records


def _loose_path(object_dir, oid, object_format):
    length = 40 if object_format == "sha1" else 64
    if not re.fullmatch(rf"[0-9a-f]{{{length}}}", oid):
        raise ValidationError("Object identity is invalid")
    path = Path(object_dir) / oid[:2] / oid[2:]
    try:
        fanout = Path(object_dir) / oid[:2]
        if path.resolve(strict=False).parent != fanout.resolve(strict=False):
            raise ValidationError("Loose object path escaped the primary object directory")
        if fanout.exists():
            attributes = getattr(fanout.lstat(), "st_file_attributes", 0)
            if (not fanout.is_dir() or fanout.is_symlink() or
                    (hasattr(fanout, "is_junction") and fanout.is_junction()) or
                    attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or
                    fanout.resolve(strict=True).parent != Path(object_dir)):
                raise ValidationError("Loose object fanout directory is aliased")
    except OSError as exc:
        raise ValidationError("Loose object path could not be proved") from exc
    return path


def _install_quarantine_objects(root, records, snapshot):
    object_dir = _canonical_object_dir(root, snapshot)
    # Prove every destination before creating the first object so a later
    # validation failure cannot strand a partially installed set.
    for record in records:
        path = _loose_path(object_dir, record["oid"], snapshot["object_format"])
        record["path"] = path
        record["fanout_preexisting"] = path.parent.exists()
        if not record["preexisting"] and path.exists():
            raise ValidationError("New object has an ambiguous pre-install loose path")
    for record in records:
        path = record["path"]
        installed = _git(root, "hash-object", "-t", record["kind"], "-w", "--stdin",
                         input_bytes=record["raw"]).stdout.decode("ascii").strip()
        if installed != record["oid"] or _git(root, "cat-file", "-e", installed, check=False).returncode:
            raise ValidationError("Replacement object installation did not preserve its identity")
        if not record["preexisting"]:
            attributes = getattr(path.lstat(), "st_file_attributes", 0) if path.exists() else 0
            if not path.is_file() or path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValidationError("New object is not a canonical primary loose object")


def _claimant_tips(root):
    tips = set(dict(_ref_tips(root)).values())
    for state in _worktree_state(root).values():
        tips.add(state["head"])
        tips.update(state["pseudos"].values())
    length = 40 if _git(root, "rev-parse", "--show-object-format").stdout.strip() == b"sha1" else 64
    expression = re.compile(rb"(?<![0-9a-f])[0-9a-f]{" + str(length).encode("ascii") + rb"}(?![0-9a-f])")
    for content in _tree_files(_git_common_dir(root) / "logs").values():
        tips.update(value.decode("ascii") for value in expression.findall(content))
    common = _git_common_dir(root) / "worktrees"
    if common.exists():
        for item in common.iterdir():
            for content in _tree_files(item / "logs").values():
                tips.update(value.decode("ascii") for value in expression.findall(content))
    return tips


def _has_claimant(root, oid):
    for tip in _claimant_tips(root):
        if tip == oid:
            return True
        peeled = _git(root, "rev-parse", "--verify", "--quiet", tip + "^{commit}", check=False)
        if peeled.returncode in {1, 128}:
            continue
        if peeled.returncode != 0:
            raise ValidationError("Claimant type lookup failed; cleanup refused")
        if _is_ancestor(root, oid, peeled.stdout.decode("ascii").strip()):
            return True
    return False


def _unlink_loose_object(path):
    path = Path(path)
    try:
        # Git deliberately makes loose objects read-only on Windows.  This path
        # is reached only after the canonical-path, pre-existence, and claimant
        # guards have proved that AWF created this exact loose object.
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        path.unlink()
    except OSError as exc:
        raise ValidationError("Replacement loose object cleanup failed") from exc


def _loose_object_matches(record, object_format):
    path = record["path"]
    expected = (record["kind"] + " " + str(len(record["raw"]))).encode("ascii") + b"\0" + record["raw"]
    try:
        size = path.stat().st_size
        if size > len(expected) * 2 + 1024:
            return False
        compressed = path.read_bytes()
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(compressed, len(expected) + 1)
        if len(decoded) > len(expected) or inflater.unconsumed_tail:
            return False
        decoded += inflater.flush(len(expected) + 1 - len(decoded))
    except (OSError, zlib.error):
        return False
    algorithm = hashlib.sha1 if object_format == "sha1" else hashlib.sha256
    return (inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail and
            decoded == expected and algorithm(expected).hexdigest() == record["oid"])


def _cleanup_new_objects(root, records, snapshot):
    _canonical_object_dir(root, snapshot)
    removable = [record for record in records
                 if not record["preexisting"] and record.get("path") is not None
                 and record["path"].exists()]
    if any(_has_claimant(root, record["oid"]) for record in removable):
        raise ValidationError("A replacement object has a concurrent claimant")
    for record in removable:
        path = record["path"]
        attributes = getattr(path.lstat(), "st_file_attributes", 0) if path.exists() else 0
        if (not path.is_file() or path.is_symlink() or
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValidationError("Replacement object is not removable as a primary loose object")
        if not _loose_object_matches(record, snapshot["object_format"]):
            raise ValidationError("Replacement loose object identity could not be proved")
        _unlink_loose_object(path)
        if path.exists():
            raise ValidationError("Replacement loose object cleanup did not complete")
        if not record["fanout_preexisting"]:
            try:
                path.parent.rmdir()
            except OSError:
                pass


def _restore_rewrite_reflogs(root, snapshot, ref):
    common = _git_common_dir(root)
    for relative in ("HEAD", ref):
        key = relative if relative == "HEAD" else relative
        path = common / "logs" / key
        original = snapshot["logs"].get(key)
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + ".awf-restore")
                temporary.write_bytes(original)
                os.replace(temporary, path)
        except OSError as exc:
            raise ValidationError("Rewrite reflog restoration failed") from exc


def _recovery_message(code, records, old_head, new_head, detail):
    retained = ",".join(record["oid"] for record in records if not record["preexisting"])
    command = "git cat-file -t " + (retained.split(",")[0] if retained else new_head) + " && git fsck --full --no-reflogs"
    return (f"{code}: old_head={old_head}; new_head={new_head}; retained_objects={retained or 'none'}; "
            f"detail={detail}; non_destructive_recovery={command}")


def _failure_state_matches(root, snapshot):
    return _rewrite_snapshot(root) == snapshot


def _post_cas_proof(root, snapshot, ref, created, old_commits, records):
    current = _rewrite_snapshot(root)
    expected_refs = dict(snapshot["refs"])
    expected_refs[ref] = created
    if current["refs"] != expected_refs:
        return False, "ref census changed"
    for path, before in snapshot["worktrees"].items():
        after = current["worktrees"].get(path)
        if (after is None or after["symbolic"] != before["symbolic"] or
                after["status"] != before["status"] or
                after["pseudo_files"] != before["pseudo_files"]):
            return False, "worktree census changed"
        expected_head = created if before["symbolic"] == ref else before["head"]
        if after["head"] != expected_head or after["pseudos"] != before["pseudos"]:
            return False, "worktree or pseudoref changed"
    if set(current["worktrees"]) != set(snapshot["worktrees"]):
        return False, "worktree set changed"
    allowed_logs = {"HEAD", ref}
    for key in set(current["logs"]) | set(snapshot["logs"]):
        if key not in allowed_logs and current["logs"].get(key) != snapshot["logs"].get(key):
            return False, "unexpected reflog changed"
    for key in ("indexes", "alternate", "object_format", "object_dir", "object_identity"):
        if current[key] != snapshot[key]:
            return False, key + " changed"
    expected_objects = set(snapshot["objects"]) | {record["oid"] for record in records}
    if set(current["objects"]) != expected_objects:
        return False, "object inventory changed"
    other_tips = set(current["refs"].values()) - {created}
    for path, state in current["worktrees"].items():
        if state["symbolic"] != ref:
            other_tips.add(state["head"])
        other_tips.update(state["pseudos"].values())
    for old in old_commits:
        if any(_is_ancestor(root, old, tip) for tip in other_tips):
            return False, "old commit became reachable"
    return True, "proved"


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
    if not _is_ancestor(root, base_sha, old_head):
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
        if any(_is_ancestor(root, commit, tip) for commit in old_commits):
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
    with tempfile.TemporaryDirectory(prefix="awf-publication-rewrite-") as temporary:
        temp_objects = Path(temporary) / "objects"
        temp_objects.mkdir()
        object_dir = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.decode("utf-8").strip())
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
        records = _quarantine_objects(root, temp_objects, extra, object_format)
        if created not in {record["oid"] for record in records}:
            raise ValidationError("Replacement commit is absent from the quarantine inventory")
        with _RewriteLock(root):
            snapshot = _rewrite_snapshot(root)
            if snapshot["refs"].get(ref) != old_head:
                raise ValidationError("Target ref changed before installation; rewrite refused without changes")
            for other_ref, tip in snapshot["refs"].items():
                if other_ref != ref and any(_is_ancestor(root, commit, tip) for commit in old_commits):
                    raise ValidationError("Old commits became reachable before installation; rewrite refused without changes")
            try:
                _install_quarantine_objects(root, records, snapshot)
            except ValidationError as install_error:
                try:
                    current = _rewrite_snapshot(root)
                    if any(current[key] != snapshot[key] for key in snapshot if key != "objects"):
                        raise ValidationError("repository state changed during installation failure")
                    _cleanup_new_objects(root, records, snapshot)
                    if not _failure_state_matches(root, snapshot):
                        raise ValidationError("repository snapshot was not restored")
                except ValidationError as recovery_error:
                    raise ValidationError(_recovery_message(
                        "PRE_CAS_RECOVERY_REQUIRED", records, old_head, created,
                        str(install_error) + "; recovery: " + str(recovery_error))) from recovery_error
                raise ValidationError(_recovery_message(
                    "PRE_CAS_FAILED_RECOVERED", records, old_head, created,
                    str(install_error) + "; exact pre-operation snapshot restored")) from install_error
            update = _git(root, "update-ref", ref, created, old_head, check=False)
            if update.returncode:
                try:
                    current = _rewrite_snapshot(root)
                    if any(current[key] != snapshot[key] for key in snapshot if key != "objects"):
                        raise ValidationError("repository state changed during failed CAS")
                    _cleanup_new_objects(root, records, snapshot)
                    if not _failure_state_matches(root, snapshot):
                        raise ValidationError("repository snapshot was not restored")
                except ValidationError as exc:
                    raise ValidationError(_recovery_message(
                        "CAS_FAILED_RECOVERY_REQUIRED", records, old_head, created, str(exc))) from exc
                raise ValidationError(_recovery_message(
                    "CAS_FAILED_RECOVERED", records, old_head, created, "exact pre-operation snapshot restored"))
            proved, detail = _post_cas_proof(root, snapshot, ref, created, old_commits, records)
            if not proved:
                rollback = _git(root, "update-ref", ref, old_head, created, check=False)
                if rollback.returncode:
                    raise ValidationError(_recovery_message(
                        "POST_CAS_RECOVERY_REQUIRED", records, old_head, created,
                        "exact-CAS rollback failed after " + detail))
                try:
                    _restore_rewrite_reflogs(root, snapshot, ref)
                    current = _rewrite_snapshot(root)
                    if any(current[key] != snapshot[key] for key in snapshot if key != "objects"):
                        raise ValidationError("repository state changed during post-CAS recovery")
                    _cleanup_new_objects(root, records, snapshot)
                    if not _failure_state_matches(root, snapshot):
                        raise ValidationError("repository snapshot was not restored")
                except ValidationError as exc:
                    raise ValidationError(_recovery_message(
                        "POST_CAS_RECOVERY_REQUIRED", records, old_head, created, str(exc))) from exc
                raise ValidationError(_recovery_message(
                    "POST_CAS_PROOF_FAILED_RECOVERED", records, old_head, created,
                    "exact-CAS rollback restored the pre-operation snapshot after " + detail))
    return {"status": "PASS", "branch": branch, "base_sha": base_sha, "old_head": old_head,
            "head_sha": created, "head_tree": old_tree, "commits": 1, "publication_scan": scan,
            "old_commits_reachable_from_branches_or_tags": False,
            "old_commits_reachable_from_local_or_remote_tracking_refs": False,
            "remote_branch_absent": True,
            "remote_push_branch_absent": True,
            "reflog_notice": "The old commit can remain in local reflogs. Failed-CAS cleanup removes only newly created, unclaimed, canonical primary loose objects after exact snapshot proof.",
            "execution_authority": False}
