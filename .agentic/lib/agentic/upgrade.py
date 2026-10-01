"""Data-driven historical installation identification and pure migrations."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import difflib
from functools import lru_cache
import json
from pathlib import Path
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


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _normalized_sql(value):
    return None if value is None else re.sub(r"\s+", " ", value).strip()


def _sqlite_schema(connection):
    """Return complete structural metadata, including constraints, indexes and triggers."""
    quick = connection.execute("PRAGMA quick_check").fetchone()
    if quick is None or quick[0] != "ok":
        raise ValidationError("routing ledger PRAGMA quick_check failed")
    tables = tuple(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
    table_metadata = []
    index_metadata = []
    for name in tables:
        quoted = _quoted(name)
        table_metadata.append((
            name,
            tuple(tuple(row) for row in connection.execute(f"PRAGMA table_info({quoted})")),
            tuple(tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")),
        ))
        indexes = []
        for row in connection.execute(f"PRAGMA index_list({quoted})"):
            index_name = row[1]
            indexes.append((index_name, row[2], row[3], row[4],
                            tuple(tuple(detail) for detail in connection.execute(
                                f"PRAGMA index_xinfo({_quoted(index_name)})"))))
        index_metadata.append((name, tuple(sorted(indexes))))
    objects = tuple((kind, name, table, _normalized_sql(sql))
                    for kind, name, table, sql in connection.execute(
                        "SELECT type,name,tbl_name,sql FROM sqlite_master "
                        "WHERE type IN ('table','index','trigger','view') ORDER BY type,name"))
    return {
        "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        "tables": tuple(table_metadata),
        "indexes": tuple(index_metadata),
        "objects": objects,
    }


@lru_cache(maxsize=2)
def _expected_routing_schema(legacy):
    from .model_routing import initialize_routing_ledger
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        initialize_routing_ledger(connection, legacy_only=legacy)
        connection.commit()
        return _sqlite_schema(connection)
    finally:
        connection.close()


def _schema_difference(actual, expected):
    for section in ("user_version", "tables", "indexes", "objects"):
        if actual[section] != expected[section]:
            return f"routing ledger {section} differs from the target initializer"
    return "routing ledger schema differs from the target initializer"


def _routing_validation(raw, *, allow_legacy=False):
    if not raw.startswith(b"SQLite format 3\x00"):
        return None, "routing ledger is not a SQLite 3 database"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.deserialize(raw)
        actual = _sqlite_schema(connection)
    except (sqlite3.DatabaseError, ValidationError) as exc:
        return None, f"routing ledger validation failed: {exc}"
    finally:
        connection.close()
    target = _expected_routing_schema(False)
    if actual == target:
        return "routing-ledger:model_runs-v1", None
    if allow_legacy and actual == _expected_routing_schema(True):
        return "routing-ledger:model_runs-v1-legacy", None
    return None, _schema_difference(actual, target)


def _migrate_routing_ledger(raw):
    schema, _ = _routing_validation(raw, allow_legacy=True)
    if schema == "routing-ledger:model_runs-v1":
        return raw
    if schema != "routing-ledger:model_runs-v1-legacy":
        return raw
    from .model_routing import initialize_routing_ledger
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.deserialize(raw)
        initialize_routing_ledger(connection)
        connection.commit()
        if _sqlite_schema(connection) != _expected_routing_schema(False):
            return raw
        return connection.serialize()
    except (sqlite3.DatabaseError, ValidationError, KeyError, TypeError, ValueError):
        return raw
    finally:
        connection.close()


@lru_cache(maxsize=1)
def _state_contracts():
    from .contracts import Contracts
    return Contracts(Path(__file__).resolve().parents[2] / "schemas")


def _record_value(path, raw):
    try:
        if path.endswith(".json"):
            return loads(raw.decode("utf-8")), None
        if path.endswith((".yaml", ".yml")):
            return load_yaml(raw), None
    except (UnicodeDecodeError, ValueError) as exc:
        return None, str(exc)
    return None, "state record is not supported JSON or YAML"


def _contract_validation(name, label, value):
    try:
        _state_contracts().validate(name, value)
        return label, None
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        return None, str(exc)


def _state_validation(path, raw, *, allow_legacy=False):
    relative = path.removeprefix(".agentic-state/")
    area = relative.split("/", 1)[0]
    if area == "routing" and path.endswith((".sqlite", ".sqlite3", ".db")):
        return _routing_validation(raw, allow_legacy=allow_legacy)
    value, parse_error = _record_value(path, raw)
    if parse_error is not None:
        return None, parse_error
    contracts = {
        "reviews": ("critic-review", "critic-review:3"),
        "lifecycle": ("controller-event", "controller-event:3"),
        "operating": ("operating-change", "operating-change:1"),
    }
    if area not in contracts:
        return None, "state path is not a recognized durable record kind"
    name, label = contracts[area]
    schema, reason = _contract_validation(name, label, value)
    if schema is not None or not allow_legacy or not isinstance(value, dict):
        return schema, reason
    if area == "reviews" and "closure" not in value:
        candidate = dict(value)
        candidate["closure"] = {"result": "UNKNOWN",
                                "evidence": list(value.get("evidence_checked", []))}
        schema, _ = _contract_validation(name, label, candidate)
        if schema is not None:
            return "critic-review:3-pre-closure", None
    if area == "lifecycle" and "digest_sha256" not in value:
        candidate = dict(value)
        candidate["digest_sha256"] = None
        schema, _ = _contract_validation(name, label, candidate)
        if schema is not None:
            return "controller-event:3-pre-digest", None
    return None, reason


def state_schema(path, raw):
    """Return a fully validated current or recognized migratable schema."""
    return _state_validation(path, raw, allow_legacy=True)[0]


def state_migration_plan(state, migrated, previous, current):
    actions = []
    for path, raw in sorted(state.items()):
        if path.startswith(ARCHIVE_ROOT + "/"):
            continue
        schema, source_reason = _state_validation(path, raw, allow_legacy=previous in {"1.8.3", "1.8.9"})
        target_schema, target_reason = _state_validation(
            path, migrated[path], allow_legacy=current == "1.8.9")
        action = ("migrated_schema" if migrated[path] != raw and target_schema else
                  "retained_schema_compatible" if schema and target_schema else
                  "archive_read_only_copy")
        item = {"path": path, "action": action, "schema": schema,
                "target_schema": target_schema, "from": previous, "to": current}
        if action == "archive_read_only_copy":
            item.update(validation_path=path,
                        validation_reason=target_reason or source_reason or
                        "state record does not match its target schema")
        actions.append(item)
    return actions


def _migrate_state(state, previous, current):
    migrated = dict(state)
    for path, raw in sorted(state.items()):
        relative = path.removeprefix(".agentic-state/")
        if (relative.split("/", 1)[0] == "routing"
                and path.endswith((".sqlite", ".sqlite3", ".db"))):
            migrated[path] = _migrate_routing_ledger(raw)
    if previous == "1.8.9" and current == "1.9.1":
        for path, raw in sorted(state.items()):
            if _state_validation(path, raw, allow_legacy=True)[0] != "critic-review:3-pre-closure":
                continue
            value, _ = _record_value(path, raw)
            value["closure"] = {"result": "UNKNOWN", "evidence": list(value["evidence_checked"])}
            candidate = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
            if _state_validation(path, candidate)[0] is not None:
                migrated[path] = candidate
        for path, raw in sorted(state.items()):
            if _state_validation(path, raw, allow_legacy=True)[0] != "controller-event:3-pre-digest":
                continue
            value, _ = _record_value(path, raw)
            value["digest_sha256"] = None
            candidate = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
            if _state_validation(path, candidate)[0] is not None:
                migrated[path] = candidate
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
    unknown = {path: (raw, _state_validation(path, raw)[1]) for path, raw in state.items()
               if _state_validation(path, raw)[0] is None
               and not path.startswith(ARCHIVE_ROOT + "/")}
    if not unknown:
        return {}, []
    files = []
    additions = {}
    for path, (raw, reason) in sorted(unknown.items()):
        relative = path.removeprefix(".agentic-state/")
        archive = f"{ARCHIVE_ROOT}/files/{relative}"
        additions[archive] = raw
        files.append({"source": path, "archive": archive, "sha256": sha256(raw),
                      "source_retained": True, "validation_path": path,
                      "validation_reason": reason})
    manifest = {"format": "awf-state-archive-1", "target_version": "1.9.3", "files": files}
    additions[f"{ARCHIVE_ROOT}/manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    return additions, [{"path": item["source"], "action": "archived_read_only_copy",
                        "archive": item["archive"],
                        "validation_path": item["validation_path"],
                        "validation_reason": item["validation_reason"]} for item in files]
