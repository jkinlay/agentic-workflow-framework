"""Data-driven historical installation identification and pure migrations."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import json
import re
from types import MappingProxyType

from . import ValidationError
from .canonical import load_yaml, loads, sha256

CONFIG = ".agentic/PROJECT_CONFIG.yaml"
INSTALLED = ".agentic/installed-manifest.json"
PROVENANCE = ".agentic/workflow-version.yaml"
OPERATING = "OPERATING_CONFIG.yaml"
ARCHIVE_ROOT = ".agentic-state/archive/upgrade-to-1.9.3"
CURRENT_RECEIPT_SCHEMA = "awf-installed-receipt-embedded-manifest-1"


def _canonical_manifest(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def managed_manifest_sha256(value):
    return sha256(_canonical_manifest(value))


def _version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        return None
    return tuple(int(part) for part in value.split("."))


def load_known_versions(raw):
    try:
        value = loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("Invalid known-versions table") from exc
    if set(value) != {"format", "floor", "target", "versions"} or value["format"] != "awf-known-versions-1":
        raise ValidationError("Unsupported known-versions table")
    if _version_tuple(value["floor"]) is None or _version_tuple(value["target"]) is None or not isinstance(value["versions"], list):
        raise ValidationError("Invalid known-versions version bounds")
    versions = {}
    previous = None
    for entry in value["versions"]:
        required = {"version", "provenance", "source_manifest_sha256", "managed_manifest_sha256",
                    "managed_file_count", "receipt_schema", "configuration_schema",
                    "operating_configuration_schema", "migration"}
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValidationError("Invalid known-versions entry")
        version = entry["version"]
        parsed = _version_tuple(version)
        if parsed is None or parsed < _version_tuple(value["floor"]) or parsed >= _version_tuple(value["target"]):
            raise ValidationError("Known version is outside the upgrade range: " + str(version))
        if version in versions or (previous is not None and parsed <= previous):
            raise ValidationError("Known versions must be unique and ordered")
        for field in ("source_manifest_sha256", "managed_manifest_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(entry[field])):
                raise ValidationError(f"Invalid {field} for known version {version}")
        migration = entry["migration"]
        if (not isinstance(migration, dict) or set(migration) != {"kind", "to", "new_required_settings"}
                or migration["kind"] != "version-only" or _version_tuple(migration["to"]) is None
                or not isinstance(migration["new_required_settings"], list)):
            raise ValidationError("Invalid migration step for known version " + version)
        versions[version] = MappingProxyType(entry)
        previous = parsed
    for version, entry in versions.items():
        target = entry["migration"]["to"]
        if target != value["target"] and target not in versions:
            raise ValidationError(f"Migration from {version} has unknown target {target}")
    return MappingProxyType({"floor": value["floor"], "target": value["target"],
                             "versions": MappingProxyType(versions)})


def immutable_from_source_manifest(source_manifest):
    from .installer import managed
    return {path: digest for path, digest in source_manifest["files"].items()
            if managed(path) and path not in {CONFIG, PROVENANCE, ".github/CODEOWNERS"}}


def _claimed_version(receipt, config_raw):
    claimed = receipt.get("template_version") if isinstance(receipt, dict) else None
    try:
        config = load_yaml(config_raw) if config_raw is not None else None
        configured = config.get("template", {}).get("expected_workflow_version") if isinstance(config, dict) else None
    except Exception:
        configured = None
    return claimed if _version_tuple(claimed) is not None else configured


def identify_installation(tree, table, config_raw, receipt_raw):
    """Return a pinned historical identity after checking every receipt-managed byte."""
    if receipt_raw is None or config_raw is None:
        raise ValidationError("Upgrade requires a verified AWF installation receipt and project configuration")
    try:
        receipt = loads(receipt_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("Unrecognised AWF installation: invalid receipt; provide its distribution") from exc
    claimed = _claimed_version(receipt, config_raw)
    parsed = _version_tuple(claimed)
    if parsed is not None and parsed < _version_tuple(table["floor"]):
        raise ValidationError(f"AWF {claimed} is below the supported upgrade floor {table['floor']}; no files changed")
    entry = table["versions"].get(claimed)
    if entry is None:
        label = claimed if claimed is not None else "unknown"
        raise ValidationError(f"Unrecognised AWF installation version {label}; provide that version's distribution; no files changed")
    if receipt.get("source_manifest_sha256") != entry["source_manifest_sha256"]:
        raise ValidationError(f"Unrecognised AWF {claimed} receipt: source manifest digest is not registered; provide that version's distribution; no files changed")
    schema = entry["receipt_schema"]
    if schema == CURRENT_RECEIPT_SCHEMA:
        source_raw = receipt.get("source_manifest_json")
        if not isinstance(source_raw, str) or sha256(source_raw.encode("utf-8")) != entry["source_manifest_sha256"]:
            raise ValidationError(f"Unrecognised AWF {claimed} receipt: embedded source manifest is not registered; no files changed")
        source_manifest = loads(source_raw)
        if source_manifest.get("format") != "awf-manifest-1" or source_manifest.get("template_version") != claimed:
            raise ValidationError(f"Unrecognised AWF {claimed} receipt: embedded source manifest is invalid; no files changed")
        expected = immutable_from_source_manifest(source_manifest)
        if receipt.get("immutable_files") != expected:
            raise ValidationError(f"Unrecognised AWF {claimed} receipt: managed membership differs from its registered manifest; no files changed")
    elif schema == "awf-installed-receipt-legacy-1":
        expected = receipt.get("immutable_files")
        if not isinstance(expected, dict):
            raise ValidationError(f"Unrecognised AWF {claimed} legacy receipt: managed manifest is missing; no files changed")
    else:
        raise ValidationError("Unsupported registered receipt schema: " + str(schema))
    if len(expected) != entry["managed_file_count"] or managed_manifest_sha256(expected) != entry["managed_manifest_sha256"]:
        raise ValidationError(f"Unrecognised AWF {claimed} receipt: managed manifest digest is not registered; no files changed")
    problems = []
    for path, expected_hash in sorted(expected.items()):
        info = tree.inspect(path)
        actual_hash = None if info is None else sha256(tree.read(path))
        if actual_hash != expected_hash:
            problems.append({"path": path, "expected_sha256": expected_hash, "actual_sha256": actual_hash})
    if problems:
        details = "; ".join(f"{p['path']} expected={p['expected_sha256']} actual={p['actual_sha256'] or 'MISSING'}" for p in problems)
        raise ValidationError("Locally modified managed files; no files changed: " + details)
    try:
        config = load_yaml(config_raw)
    except Exception as exc:
        raise ValidationError("Project configuration is not deterministically migratable. Owner question: which valid registered configuration should be retained?") from exc
    configured = config.get("template", {}).get("expected_workflow_version") if isinstance(config, dict) else None
    if configured != claimed:
        raise ValidationError(f"Project configuration is not deterministically migratable. Owner question: should template.expected_workflow_version be {claimed}?")
    provenance_raw = tree.read(PROVENANCE) if tree.inspect(PROVENANCE) is not None else None
    if provenance_raw is None:
        raise ValidationError("Unrecognised AWF installation: workflow provenance is missing; no files changed")
    provenance = loads(provenance_raw.decode("utf-8"))
    if (provenance.get("template", {}).get("version") != claimed
            or provenance.get("installation", {}).get("source_manifest_sha256") != entry["source_manifest_sha256"]):
        raise ValidationError("Unrecognised AWF installation: workflow provenance does not match the registered receipt; no files changed")
    return {"version": claimed, "entry": entry, "receipt": receipt, "immutable_files": expected,
            "install_id": provenance.get("installation", {}).get("install_id") or receipt.get("install_id")}


def _replace_scalar(raw, key, previous, current, *, question):
    parsed = load_yaml(raw)
    if key == "expected_workflow_version":
        value = parsed.get("template", {}).get(key) if isinstance(parsed, dict) else None
    else:
        value = parsed.get(key) if isinstance(parsed, dict) else None
    if value != previous:
        raise ValidationError(f"Migration is not deterministic. Owner question: {question}")
    token = rb'(?P<quote>["\']?)' + re.escape(previous.encode("ascii")) + rb'(?P=quote)'
    pattern = re.compile(rb'(?m)^(?P<prefix>[ \t]*(?:["\']?' + re.escape(key.encode("ascii")) + rb'["\']?)[ \t]*:[ \t]*)' + token
                         + rb'(?P<suffix>[ \t]*(?:,[ \t]*)?(?:#[^\r\n]*)?(?:\r\n|\n|\r|$))')
    matches = list(pattern.finditer(raw))
    if len(matches) != 1:
        raise ValidationError(f"Migration is not deterministic. Owner question: {question}")
    match = matches[0]
    start = match.start() + len(match.group("prefix")) + len(match.group("quote"))
    migrated = raw[:start] + current.encode("ascii") + raw[start + len(previous):]
    expected = json.loads(json.dumps(parsed))
    if key == "expected_workflow_version":
        expected["template"][key] = current
    else:
        expected[key] = current
    if load_yaml(migrated) != expected:
        raise ValidationError(f"Migration is not deterministic. Owner question: {question}")
    return migrated


@dataclass(frozen=True)
class MigrationBundle:
    project_config: bytes
    operating_config: bytes | None
    receipt: bytes
    state: MappingProxyType


def migrate_step(bundle, previous, current):
    """Pure version-only step; owner bytes outside the two version scalars are retained."""
    config = _replace_scalar(bundle.project_config, "expected_workflow_version", previous, current,
        question=f"should the unique template.expected_workflow_version scalar be changed from {previous} to {current}?")
    receipt = _replace_scalar(bundle.receipt, "template_version", previous, current,
        question=f"which registered {previous} installation receipt should be migrated to {current}?")
    if bundle.operating_config is not None:
        try:
            load_yaml(bundle.operating_config)
        except Exception as exc:
            raise ValidationError("Migration is not deterministic. Owner question: which valid OPERATING_CONFIG.yaml should be retained?") from exc
    return MigrationBundle(config, bundle.operating_config, receipt, MappingProxyType(dict(bundle.state)))


def migrate_1_8_3_to_1_8_9(bundle):
    return migrate_step(bundle, "1.8.3", "1.8.9")


def migrate_1_8_9_to_1_9_1(bundle):
    return migrate_step(bundle, "1.8.9", "1.9.1")


def migrate_1_9_1_to_1_9_2(bundle):
    return migrate_step(bundle, "1.9.1", "1.9.2")


def migrate_1_9_2_to_1_9_3(bundle):
    return migrate_step(bundle, "1.9.2", "1.9.3")


def config_diff(before, after, previous, current):
    return "".join(difflib.unified_diff(before.decode("utf-8").splitlines(keepends=True),
        after.decode("utf-8").splitlines(keepends=True), fromfile=previous, tofile=current))


def migration_chain(table, version):
    steps = []
    seen = set()
    while version != table["target"]:
        if version in seen or version not in table["versions"]:
            raise ValidationError("Known-versions migration chain is incomplete at " + version)
        seen.add(version)
        step = table["versions"][version]["migration"]
        steps.append((version, step["to"], tuple(step["new_required_settings"])))
        version = step["to"]
    return tuple(steps)


def apply_chain(table, version, project_config, operating_config, receipt, state):
    bundle = MigrationBundle(project_config, operating_config, receipt, MappingProxyType(dict(state)))
    reports = []
    for previous, current, new_settings in migration_chain(table, version):
        migrated = migrate_step(bundle, previous, current)
        reports.append({"from": previous, "to": current, "configuration_diff":
                        config_diff(bundle.project_config, migrated.project_config, previous, current),
                        "new_required_settings": list(new_settings), "state_migrations": []})
        bundle = migrated
    return bundle, reports


def state_archive_plan(state):
    """Recognized state is byte-compatible; unknown leaves get immutable archive copies."""
    def recognized(path, raw):
        relative = path.removeprefix(".agentic-state/")
        area = relative.split("/", 1)[0]
        if area not in {"routing", "operating", "reviews", "lifecycle", "audit"}:
            return False
        if path.endswith((".sqlite", ".sqlite3", ".db")):
            return area == "routing" and raw.startswith(b"SQLite format 3\x00")
        try:
            if path.endswith(".json"):
                loads(raw.decode("utf-8"))
                return True
            if path.endswith((".yaml", ".yml")):
                load_yaml(raw)
                return True
        except Exception:
            return False
        return False
    unknown = {path: raw for path, raw in state.items()
               if not recognized(path, raw) and not path.startswith(ARCHIVE_ROOT + "/")}
    if not unknown:
        return {}, []
    files = []
    additions = {}
    for path, raw in sorted(unknown.items()):
        relative = path.removeprefix(".agentic-state/")
        archive = f"{ARCHIVE_ROOT}/files/{relative}"
        additions[archive] = raw
        files.append({"source": path, "archive": archive, "sha256": sha256(raw), "source_retained": True})
    manifest = {"format": "awf-state-archive-1", "target_version": "1.9.3", "files": files}
    additions[f"{ARCHIVE_ROOT}/manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    return additions, [{"path": item["source"], "action": "archived_read_only_copy", "archive": item["archive"]} for item in files]
