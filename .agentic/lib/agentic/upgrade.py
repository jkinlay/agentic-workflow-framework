"""Data-driven historical installation identification and pure migrations."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import difflib
import json
import re
import sqlite3
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
        required = {"version", "provenance", "source_manifest_sha256", "source_manifest_base64",
                    "managed_manifest_sha256",
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
        source_encoded = entry["source_manifest_base64"]
        try:
            source_raw = None if source_encoded is None else base64.b64decode(source_encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError("Invalid encoded source manifest for known version " + version) from exc
        if entry["receipt_schema"] == CURRENT_RECEIPT_SCHEMA:
            if not isinstance(source_raw, str) or sha256(source_raw.encode("utf-8")) != entry["source_manifest_sha256"]:
                raise ValidationError("Invalid embedded source manifest for known version " + version)
            source_manifest = loads(source_raw)
            immutable = immutable_from_source_manifest(source_manifest)
            if (len(immutable) != entry["managed_file_count"]
                    or managed_manifest_sha256(immutable) != entry["managed_manifest_sha256"]):
                raise ValidationError("Embedded managed manifest differs for known version " + version)
        elif source_raw is not None:
            raise ValidationError("Legacy known version must not embed a source manifest: " + version)
        else:
            immutable = None
        migration = entry["migration"]
        if (not isinstance(migration, dict) or set(migration) != {"kind", "to", "new_required_settings"}
                or migration["kind"] != "version-only" or _version_tuple(migration["to"]) is None
                or not isinstance(migration["new_required_settings"], list)):
            raise ValidationError("Invalid migration step for known version " + version)
        versions[version] = MappingProxyType({**entry, "_source_manifest_json": source_raw,
                                               "_immutable_files": immutable})
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


def _legacy_routing_policy(config):
    execution = config["execution"]
    roles = execution["roles"]
    role_defaults = {role: {"model": roles[role]["model"],
                            "reasoning_effort": roles[role]["reasoning_effort"]}
                     for role in ("controller", "worker", "critic", "specialist")}
    order = list(dict.fromkeys(pair["model"] for pair in role_defaults.values()))
    efforts = ["low", "medium", "high", "xhigh", "max", "ultra"]
    allowed = {role: list(roles[role]["approved_model_ids"])
               for role in ("controller", "worker", "critic", "specialist")}
    tickets = execution["max_parallel_tickets"]
    return {
        "schema_version": 1, "profile": "balanced", "model_order": order,
        "models": {model: {"reasoning_efforts": efforts} for model in order},
        "role_defaults": role_defaults, "role_allowed_models": allowed,
        "simple_worker": dict(role_defaults["worker"]),
        "review_floor": dict(role_defaults["critic"]),
        "risk_route": dict(role_defaults["critic"]),
        "high_risk_flags": ["security", "permissions", "schema_or_migration", "data_loss",
                            "concurrency", "production", "public_api", "architecture"],
        "agent_overrides": {}, "ticket_overrides": {},
        "escalation": {"enabled": False, "max_escalations_per_ticket": 0,
                       "max_reasoning_failures_per_phase": 3, "effort_ceiling": "high"},
        "budgets": {"max_runs_per_ticket": execution["max_agent_runs_per_ticket"],
                    "max_runs_per_project_day": execution["max_agent_runs_per_ticket"] * tickets,
                    "max_tokens_per_ticket": execution["max_tokens_per_ticket"],
                    "max_tokens_per_project_day": execution["max_tokens_per_ticket"] * tickets,
                    "max_cost_microusd_per_ticket": execution["max_cost_microusd_per_ticket"],
                    "max_cost_microusd_per_project_day": execution["daily_project_cost_microusd"]},
        "adaptive": {"mode": "shadow", "min_reviewed_samples": 20,
                     "min_success_rate_percent": 95},
        "reconciliation": {"enabled": False, "authorized_operator_ids": [],
                           "max_reconciled_incident_retries_per_ticket": 2},
    }


def _insert_legacy_routing(raw):
    config = load_yaml(raw)
    if not isinstance(config, dict) or not isinstance(config.get("execution"), dict):
        raise ValidationError("Migration is not deterministic. Owner question: which valid legacy execution policy should be retained?")
    if "model_routing" in config["execution"]:
        return raw
    try:
        policy = _legacy_routing_policy(config)
    except (KeyError, TypeError) as exc:
        raise ValidationError("Migration is not deterministic. Owner question: how should the legacy roles and budgets map to model_routing?") from exc
    newline = "\r\n" if b"\r\n" in raw else "\n"
    pattern = re.compile(rb'(?m)^(?P<indent>[ \t]*)["\']?host_broker["\']?[ \t]*:')
    matches = list(pattern.finditer(raw))
    if len(matches) != 1:
        raise ValidationError("Migration is not deterministic. Owner question: where should the required model_routing policy be inserted?")
    indent = matches[0].group("indent").decode("ascii")
    rendered = json.dumps({"model_routing": policy}, indent=2, ensure_ascii=False).splitlines()[1:-1]
    block = newline.join(indent + line[2:] for line in rendered) + "," + newline
    migrated = raw[:matches[0].start()] + block.encode("utf-8") + raw[matches[0].start():]
    expected = json.loads(json.dumps(config))
    expected["execution"]["model_routing"] = policy
    if load_yaml(migrated) != expected:
        raise ValidationError("Migration is not deterministic. Owner question: how should the required model_routing policy be serialized?")
    return migrated


@dataclass(frozen=True)
class MigrationBundle:
    project_config: bytes
    operating_config: bytes | None
    receipt: bytes
    provenance: bytes
    state: MappingProxyType


def _target_receipt(raw, current, target):
    value = loads(raw.decode("utf-8"))
    value["template_version"] = current
    value["source_manifest_sha256"] = target["source_manifest_sha256"]
    value["immutable_files"] = dict(target["_immutable_files"])
    source_raw = target.get("_source_manifest_json")
    if source_raw is None:
        value.pop("source_manifest_json", None)
    else:
        value["source_manifest_json"] = source_raw
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _target_provenance(raw, current, target):
    value = loads(raw.decode("utf-8"))
    value.setdefault("template", {})["version"] = current
    value.setdefault("installation", {})["source_manifest_sha256"] = target["source_manifest_sha256"]
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _sqlite_schema(raw):
    if not raw.startswith(b"SQLite format 3\x00"):
        return None
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or quick[0] != "ok":
            return None
        tables = {}
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            tables[name] = tuple(row[1] for row in connection.execute(
                "PRAGMA table_info(" + '"' + name.replace('"', '""') + '"' + ")"))
        return tables
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()


def state_schema(path, raw):
    """Return the recognized durable schema, never merely a syntax result."""
    relative = path.removeprefix(".agentic-state/")
    area = relative.split("/", 1)[0]
    if area == "routing" and path.endswith((".sqlite", ".sqlite3", ".db")):
        tables = _sqlite_schema(raw)
        required = {"run_id", "project_id", "ticket_id", "role", "agent_id", "phase",
                    "created_at", "day", "policy_hash", "status", "reserved_tokens",
                    "reserved_cost", "actual_tokens", "actual_cost", "escalated", "request",
                    "route", "outcome"}
        if tables is not None and required.issubset(set(tables.get("model_runs", ()))):
            return "routing-ledger:model_runs-v1"
        return None
    if not path.endswith(".json"):
        return None
    try:
        value = loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if area == "reviews" and isinstance(value, dict):
        required = {"schema_version", "record_id", "created_at", "producer_id", "run_id",
                    "binding", "verdict", "acceptance_criteria", "findings", "prior_finding_ids",
                    "closure", "coverage", "evidence_checked"}
        if value.get("schema_version") == 3 and set(value) == required:
            return "critic-review:3"
        if value.get("schema_version") == 3 and set(value) == required - {"closure"}:
            return "critic-review:3-pre-closure"
        return None
    if area == "lifecycle" and isinstance(value, dict):
        required = {"schema_version", "event_id", "project_id", "sequence", "timestamp", "actor",
                    "event_type", "digest_sha256", "ticket", "previous_state", "state", "candidate",
                    "external_event_id", "correlation_id", "causation_id", "payload_hash",
                    "previous_hash", "event_hash", "evidence"}
        if value.get("schema_version") == 3 and set(value) == required:
            return "controller-event:3"
        if value.get("schema_version") == 3 and set(value) == required - {"digest_sha256"}:
            return "controller-event:3-pre-digest"
        return None
    if area == "operating" and isinstance(value, dict):
        required = {"id", "created_at", "instruction", "before_hash", "after_hash", "changes",
                    "source", "applied_from", "sequence", "previous_change_id", "governance_hash"}
        return "operating-change:1" if set(value) == required and re.fullmatch(r"C-[0-9a-f]{32}", str(value.get("id"))) else None
    return None


def state_migration_plan(state, migrated, previous, current):
    actions = []
    for path, raw in sorted(state.items()):
        if path.startswith(ARCHIVE_ROOT + "/"):
            continue
        schema, target_schema = state_schema(path, raw), state_schema(path, migrated[path])
        action = ("migrated_schema" if migrated[path] != raw else
                  "retained_schema_compatible" if schema else "archive_read_only_copy")
        actions.append({"path": path, "action": action, "schema": schema,
                        "target_schema": target_schema, "from": previous, "to": current})
    return actions


def _migrate_state(state, previous, current):
    migrated = dict(state)
    if previous == "1.8.9" and current == "1.9.1":
        for path, raw in sorted(state.items()):
            if state_schema(path, raw) != "critic-review:3-pre-closure":
                continue
            value = loads(raw.decode("utf-8"))
            value["closure"] = {"result": "UNKNOWN", "evidence": list(value["evidence_checked"])}
            migrated[path] = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        for path, raw in sorted(state.items()):
            if state_schema(path, raw) != "controller-event:3-pre-digest":
                continue
            value = loads(raw.decode("utf-8"))
            value["digest_sha256"] = None
            migrated[path] = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    return migrated


def migrate_step(bundle, previous, current, target):
    """Pure adjacent step with target-valid receipt/provenance and schema-classified state."""
    config = _replace_scalar(bundle.project_config, "expected_workflow_version", previous, current,
        question=f"should the unique template.expected_workflow_version scalar be changed from {previous} to {current}?")
    if previous == "1.8.3" and current == "1.8.9":
        config = _insert_legacy_routing(config)
    receipt = _target_receipt(bundle.receipt, current, target)
    provenance = _target_provenance(bundle.provenance, current, target)
    if bundle.operating_config is not None:
        try:
            load_yaml(bundle.operating_config)
        except Exception as exc:
            raise ValidationError("Migration is not deterministic. Owner question: which valid OPERATING_CONFIG.yaml should be retained?") from exc
    state = _migrate_state(bundle.state, previous, current)
    return MigrationBundle(config, bundle.operating_config, receipt, provenance,
                           MappingProxyType(state))


def migrate_1_8_3_to_1_8_9(bundle, target):
    return migrate_step(bundle, "1.8.3", "1.8.9", target)


def migrate_1_8_9_to_1_9_1(bundle, target):
    return migrate_step(bundle, "1.8.9", "1.9.1", target)


def migrate_1_9_1_to_1_9_2(bundle, target):
    return migrate_step(bundle, "1.9.1", "1.9.2", target)


def migrate_1_9_2_to_1_9_3(bundle, target):
    return migrate_step(bundle, "1.9.2", "1.9.3", target)


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


def apply_chain(table, version, project_config, operating_config, receipt, provenance, state, target_entry):
    bundle = MigrationBundle(project_config, operating_config, receipt, provenance,
                             MappingProxyType(dict(state)))
    reports = []
    for previous, current, new_settings in migration_chain(table, version):
        target = target_entry if current == table["target"] else table["versions"][current]
        migrated = migrate_step(bundle, previous, current, target)
        reports.append({"from": previous, "to": current, "configuration_diff":
                        config_diff(bundle.project_config, migrated.project_config, previous, current),
                        "new_required_settings": list(new_settings),
                        "state_migrations": state_migration_plan(bundle.state, migrated.state,
                                                                  previous, current)})
        bundle = migrated
    return bundle, reports


def state_archive_plan(state):
    unknown = {path: raw for path, raw in state.items()
               if state_schema(path, raw) is None and not path.startswith(ARCHIVE_ROOT + "/")}
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
