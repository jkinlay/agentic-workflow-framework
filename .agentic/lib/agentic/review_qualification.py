"""Pinned live evidence for the optional GitHub/Codex review host.

The probe writes evidence only.  It never edits host configuration or turns a
qualification flag on; that remains an operator action after inspecting a
passing record.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .canonical import fingerprint, fresh, loads, now_text, sha256, timestamp
from .child_process import HOST_AUTH_ENV_VARS, PROVIDER_API_KEY_ENV_VARS
from .review_loop import ValidationError, require
from .safeio import Tree


FORMAT = "awf-review-host-qualification-1"
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
FLAGS = ("sandbox_verified", "credentials_isolated", "branch_owned",
         "single_host_database")
AUTH_LOCATIONS = ("~/.codex/auth.json", "$CODEX_HOME/auth.json")
AGENT_AUTH_ROOT_ENV_VARS = ("HOME", "USERPROFILE", "CODEX_HOME")
CREDENTIAL_ENVIRONMENT_NAME = re.compile(
    r"(?:TOKEN|API_KEY|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
AGENT_RESULT_KEYS = {
    "role", "checkout_write", "outside_write", "network",
    "credential_environment_names", "agent_auth_files",
}
ARTIFACT_KEYS = {
    "input_sha256", "effective_config_sha256", "codex_log_sha256",
    "result_sha256",
}
PROTECTED_STATE_NAMES = {
    "review-loop.sqlite3", "review-loop.sqlite3-journal",
    "review-loop.sqlite3-shm", "review-loop.sqlite3-wal",
    "writer-lock.sqlite3", "writer-lock.sqlite3-journal",
    "writer-lock.sqlite3-shm", "writer-lock.sqlite3-wal",
    "publication-deny.json",
}


def is_host_auth_environment_name(name):
    """Return whether an environment name carries GitHub host authentication."""
    return isinstance(name, str) and name.upper() in HOST_AUTH_ENV_VARS


def _ambient_environment_binding(environment=None):
    """Return only launch-relevant names and agent-auth root locations."""
    environment = os.environ if environment is None else environment
    credential_names = sorted({
        name for name in environment
        if (isinstance(name, str)
            and name.upper() not in PROVIDER_API_KEY_ENV_VARS
            and not is_host_auth_environment_name(name)
            and CREDENTIAL_ENVIRONMENT_NAME.search(name))
    })
    return {
        "credential_environment_names": credential_names,
        "agent_auth_roots": {
            name: environment.get(name) for name in AGENT_AUTH_ROOT_ENV_VARS
        },
    }


def host_binding_sha256(config, *, environment=None):
    """Bind evidence to the host controls that establish its isolation.

    PR/head/contract/scope fields are deliberately excluded: qualification is
    performed on a disposable PR and then reused for another PR on the same
    repository and host.  A runtime, executable, checkout, database, model or
    sandbox-policy change requires a new live record.
    """
    value = {
        "version": config.get("version"),
        "github_host": config.get("github_host", "github.com"),
        "repository": config.get("repository"),
        "repository_id": config.get("repository_id"),
        "state_dir": config.get("state_dir"),
        "worker_checkout": config.get("worker_checkout"),
        "critic_checkout": config.get("critic_checkout"),
        "runtime_manifest_sha256": config.get("runtime_manifest_sha256"),
        "executables": config.get("executables"),
        "models": config.get("models"),
        "reasoning_effort": config.get("reasoning_effort"),
        "codex_config_overrides": config.get("codex_config_overrides"),
        "agent_timeout_seconds": config.get("agent_timeout_seconds"),
        "ambient_environment": _ambient_environment_binding(environment),
    }
    return fingerprint("review_host_qualification", value)


def _qualification_shape(config, *, require_record):
    value = config.get("qualification")
    legacy = {"operator", "evidence", *FLAGS}
    required = {"operator", "evidence_path", "evidence_sha256",
                "max_age_seconds", *FLAGS}
    if isinstance(value, dict) and set(value) == legacy:
        raise ValidationError(
            "Legacy bare qualification booleans are refused; run review_loop.py "
            "qualify and pin its evidence path and SHA-256")
    require(isinstance(value, dict) and set(value) == required,
            "Invalid qualification record pin")
    operator = value["operator"]
    require(isinstance(operator, str) and operator.strip() == operator
            and operator and "CHANGE_ME" not in operator,
            "Qualification operator identity is missing")
    require(type(value["max_age_seconds"]) is int
            and 0 < value["max_age_seconds"] <= MAX_AGE_SECONDS,
            "Qualification max_age_seconds must be 1..604800")
    require(all(type(value[name]) is bool for name in FLAGS),
            "Qualification flags must be booleans")
    evidence_path = value["evidence_path"]
    require(isinstance(evidence_path, str) and Path(evidence_path).is_absolute()
            and "CHANGE_ME" not in evidence_path,
            "Qualification evidence_path must be an absolute state path")
    digest = value["evidence_sha256"]
    if require_record:
        require(all(value[name] is True for name in FLAGS),
                "Host qualification incomplete")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
                "Qualification evidence_sha256 is not pinned")
    else:
        require(isinstance(digest, str)
                and (digest == "CHANGE_ME" or re.fullmatch(r"[0-9a-f]{64}", digest)),
                "Qualification evidence_sha256 must be CHANGE_ME or a SHA-256")
        require(all(value[name] is False for name in FLAGS),
                "Set all qualification flags false before collecting new evidence")
    return value


def _auth_files(value):
    require(isinstance(value, list) and len(value) == len(AUTH_LOCATIONS),
            "Qualification auth-file observations are incomplete")
    result = {}
    for item in value:
        require(isinstance(item, dict) and set(item) == {"location", "status"}
                and item.get("location") in AUTH_LOCATIONS
                and item.get("status") in {"ABSENT", "DENIED", "READABLE", "ERROR"},
                "Invalid qualification auth-file observation")
        require(item["location"] not in result,
                "Duplicate qualification auth-file observation")
        result[item["location"]] = item["status"]
    require(set(result) == set(AUTH_LOCATIONS),
            "Qualification auth-file observations are incomplete")
    return result


def validate_agent_result(value, role):
    require(isinstance(value, dict) and set(value) == AGENT_RESULT_KEYS,
            "Invalid qualification agent result")
    require(value["role"] == role, "Qualification agent role mismatch")
    for name in ("checkout_write", "outside_write", "network"):
        require(value[name] in {"SUCCEEDED", "DENIED", "ERROR"},
                f"Invalid qualification result: {name}")
    names = value["credential_environment_names"]
    require(isinstance(names, list) and len(names) == len(set(names))
            and all(isinstance(name, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    for name in names),
            "Invalid credential environment-name inventory")
    _auth_files(value["agent_auth_files"])
    return value


def _validate_artifacts(value):
    require(isinstance(value, dict) and set(value) == ARTIFACT_KEYS
            and all(isinstance(digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", digest)
                    for digest in value.values()),
            "Invalid qualification artifact pins")
    return value


def _derived_flags(probes):
    critic = probes.get("critic", {})
    worker = probes.get("worker", {})
    critic_result = critic.get("result") if critic.get("status") == "OBSERVED" else None
    worker_result = worker.get("result") if worker.get("status") == "OBSERVED" else None
    sandbox = bool(
        critic_result and worker_result
        and critic_result["checkout_write"] == "DENIED"
        and critic_result["outside_write"] == "DENIED"
        and critic_result["network"] == "DENIED"
        and worker_result["checkout_write"] == "SUCCEEDED"
        and worker_result["outside_write"] == "DENIED"
        and worker_result["network"] == "DENIED"
        and critic.get("host_observation") == {
            "checkout_marker": "ABSENT", "outside_marker": "ABSENT",
            "checkout_clean_after_cleanup": True,
        }
        and worker.get("host_observation") == {
            "checkout_marker": "EXACT", "outside_marker": "ABSENT",
            "checkout_clean_after_cleanup": True,
        })
    credentials = bool(critic_result and worker_result)
    if credentials:
        for result in (critic_result, worker_result):
            credentials = (credentials
                           and not result["credential_environment_names"]
                           and all(status in {"ABSENT", "DENIED"}
                                   for status in _auth_files(
                                       result["agent_auth_files"]).values()))
    branch = probes.get("branch_lease") == {
        "unowned_before_probe": True,
        "unique_owner_constraint": True,
        "temporary_lease_rolled_back": True,
    }
    database = probes.get("canonical_database")
    single_database = isinstance(database, dict) and set(database) == {
        "relative_path", "resolved_path_sha256", "integrity_check",
        "writer_lock_exclusive",
    } and database["relative_path"] == "review-loop.sqlite3" \
        and isinstance(database["resolved_path_sha256"], str) \
        and re.fullmatch(r"[0-9a-f]{64}", database["resolved_path_sha256"]) \
        and database["integrity_check"] == "ok" \
        and database["writer_lock_exclusive"] is True
    return {
        "sandbox_verified": sandbox,
        "credentials_isolated": credentials,
        "branch_owned": branch,
        "single_host_database": bool(single_database),
    }


def validate_qualification_record(record, config, *, now=None,
                                  environment=None):
    required = {
        "format", "record_id", "observed_at", "operator",
        "host_binding_sha256", "disposable_pr_confirmed", "candidate",
        "probes", "findings", "qualification", "result",
        "execution_authority",
    }
    require(isinstance(record, dict) and set(record) == required,
            "Invalid qualification evidence record")
    require(record["format"] == FORMAT, "Unsupported qualification evidence format")
    try:
        require(str(uuid.UUID(record["record_id"])) == record["record_id"],
                "Invalid qualification record id")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Invalid qualification record id") from exc
    qualification = config["qualification"]
    require(record["operator"] == qualification["operator"],
            "Qualification operator identity mismatch")
    fresh(record["observed_at"], now or now_text(),
          qualification["max_age_seconds"])
    require(record["host_binding_sha256"] == host_binding_sha256(
                config, environment=environment),
            "Qualification evidence is for different host controls, including "
            "credential environment names or agent-auth roots")
    require(record["disposable_pr_confirmed"] is True,
            "Qualification evidence does not identify a disposable PR")
    candidate = record["candidate"]
    require(isinstance(candidate, dict)
            and set(candidate) == {"repository_id", "pr", "head", "base",
                                   "head_ref", "base_ref"}
            and candidate["repository_id"] == config["repository_id"]
            and type(candidate["pr"]) is int and candidate["pr"] > 0
            and all(isinstance(candidate[name], str)
                    for name in ("head", "base", "head_ref", "base_ref"))
            and re.fullmatch(r"[0-9a-f]{40}", candidate["head"])
            and re.fullmatch(r"[0-9a-f]{40}", candidate["base"])
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*",
                             candidate["head_ref"])
            and candidate["base_ref"] == config["base_branch"]
            and candidate["head_ref"] != candidate["base_ref"],
            "Invalid disposable qualification candidate")
    probes = record["probes"]
    require(isinstance(probes, dict)
            and set(probes) == {"critic", "worker", "branch_lease",
                                "canonical_database"},
            "Incomplete qualification probes")
    for role in ("critic", "worker"):
        probe = probes[role]
        require(isinstance(probe, dict)
                and set(probe) == {"status", "result", "artifacts", "error",
                                   "host_observation"}
                and probe["status"] == "OBSERVED" and probe["error"] is None,
                f"{role} qualification probe did not complete")
        validate_agent_result(probe["result"], role)
        _validate_artifacts(probe["artifacts"])
    derived = _derived_flags(probes)
    require(record["qualification"] == derived,
            "Qualification flags do not match probe evidence")
    require(all(derived.values()) and record["result"] == "PASS"
            and record["findings"] == [],
            "Qualification evidence did not pass every probe")
    require(record["execution_authority"] is False,
            "Qualification evidence cannot grant execution authority")
    return record


def _evidence_path(config, state_root, *, config_path=None):
    """Return a direct state evidence path that cannot alias protected state."""
    state_root = Path(state_root).resolve(strict=True)
    evidence = Path(config["qualification"]["evidence_path"])
    require(evidence.parent.resolve(strict=True) == state_root
            and evidence.name not in {"", ".", ".."},
            "Qualification evidence must be a direct file in state_dir")
    protected = [state_root / name for name in PROTECTED_STATE_NAMES]
    configured_path = config_path or config.get("_config_path")
    if configured_path:
        protected.append(Path(configured_path))
    contract_path = config.get("contract_path")
    if contract_path:
        protected.append(Path(contract_path))
    evidence_resolved = evidence.resolve(strict=False)
    for candidate in protected:
        candidate_resolved = candidate.resolve(strict=False)
        aliases = evidence_resolved == candidate_resolved
        if not aliases and evidence.exists() and candidate.exists():
            try:
                aliases = evidence.samefile(candidate)
            except OSError:
                aliases = False
        require(not aliases,
                "Qualification evidence path aliases protected host state")
    return evidence


def validate_config_qualification(config, state_root, *, require_record=True,
                                  now=None, config_path=None,
                                  environment=None):
    qualification = _qualification_shape(config, require_record=require_record)
    state_root = Path(state_root).resolve(strict=True)
    evidence = _evidence_path(config, state_root, config_path=config_path)
    if not require_record:
        return None
    resolved = evidence.resolve(strict=True)
    require(resolved.parent == state_root,
            "Qualification evidence must be a direct file in state_dir")
    with Tree(state_root) as tree:
        raw = tree.read(evidence.name, maximum=1024 * 1024)
    require(sha256(raw) == qualification["evidence_sha256"],
            "Qualification evidence SHA-256 mismatch")
    try:
        record = loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValidationError("Qualification evidence is not UTF-8") from exc
    return validate_qualification_record(record, config, now=now,
                                         environment=environment)


def _database_probes(store, config, candidate, record_id):
    expected = (Path(config["state_dir"]) / "review-loop.sqlite3").resolve(strict=True)
    rows = store.db.execute("PRAGMA database_list").fetchall()
    main = [row for row in rows if row[1] == "main"]
    require(len(main) == 1 and Path(main[0][2]).resolve(strict=True) == expected,
            "LoopStore is not using the configured canonical database")
    integrity = store.db.execute("PRAGMA quick_check").fetchone()
    require(integrity == ("ok",), "Canonical review-loop database failed quick_check")
    owner = config["repository"] + ":" + candidate["head_ref"]
    require(store.db.execute("SELECT 1 FROM prs WHERE owner=?", (owner,)).fetchone()
            is None, "Disposable qualification branch is already leased")
    lock_exclusive = False
    unique_owner = False
    rolled_back = False
    with store.lock():
        second = sqlite3.connect(Path(config["state_dir"]) / "writer-lock.sqlite3",
                                 timeout=0, isolation_level=None)
        try:
            try:
                second.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                lock_exclusive = True
            finally:
                try:
                    second.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
        finally:
            second.close()
        store.db.execute("BEGIN IMMEDIATE")
        try:
            key = "qualification:" + record_id
            store.db.execute("INSERT INTO prs VALUES (?,?,?)", (key, owner, "{}"))
            try:
                store.db.execute("INSERT INTO prs VALUES (?,?,?)",
                                 (key + ":duplicate", owner, "{}"))
            except sqlite3.IntegrityError:
                unique_owner = True
        finally:
            store.db.execute("ROLLBACK")
            rolled_back = (store.db.execute(
                "SELECT 1 FROM prs WHERE owner=?", (owner,)).fetchone() is None)
    require(lock_exclusive, "Canonical writer lock admitted a second writer")
    require(unique_owner and rolled_back,
            "Canonical database did not enforce and roll back the branch lease probe")
    normalized = str(expected).replace("\\", "/")
    if os.name == "nt":
        normalized = normalized.casefold()
    return ({
        "unowned_before_probe": True,
        "unique_owner_constraint": unique_owner,
        "temporary_lease_rolled_back": rolled_back,
    }, {
        "relative_path": "review-loop.sqlite3",
        "resolved_path_sha256": sha256(normalized.encode("utf-8")),
        "integrity_check": "ok",
        "writer_lock_exclusive": lock_exclusive,
    })


def _agent_finding(role, result, observation):
    findings = []
    expected = {
        "critic": {"checkout_write": "DENIED", "outside_write": "DENIED",
                   "network": "DENIED"},
        "worker": {"checkout_write": "SUCCEEDED", "outside_write": "DENIED",
                   "network": "DENIED"},
    }[role]
    for name, wanted in expected.items():
        if result.get(name) != wanted:
            findings.append({
                "id": f"QUAL-{role.upper()}-{name.upper()}",
                "severity": "BLOCKER",
                "message": f"{role} {name} was {result.get(name)!r}; expected {wanted}",
            })
    if result.get("credential_environment_names"):
        findings.append({
            "id": f"QUAL-{role.upper()}-CREDENTIAL-ENV",
            "severity": "BLOCKER",
            "message": "Credential-like environment names reached the child: "
                       + ", ".join(result["credential_environment_names"]),
        })
    readable = [item["location"] for item in result.get("agent_auth_files", [])
                if item.get("status") in {"READABLE", "ERROR"}]
    if readable:
        findings.append({
            "id": f"QUAL-{role.upper()}-AGENT-AUTH",
            "severity": "BLOCKER",
            "message": "Agent CLI authentication file was readable or unevaluable: "
                       + ", ".join(readable),
        })
    expected_observation = {
        "critic": {"checkout_marker": "ABSENT", "outside_marker": "ABSENT",
                   "checkout_clean_after_cleanup": True},
        "worker": {"checkout_marker": "EXACT", "outside_marker": "ABSENT",
                   "checkout_clean_after_cleanup": True},
    }[role]
    if observation != expected_observation:
        findings.append({
            "id": f"QUAL-{role.upper()}-HOST-OBSERVATION",
            "severity": "BLOCKER",
            "message": "Host marker observation did not match the sandbox claim",
        })
    return findings


def _probe_agent(driver, config, role, record_id):
    checkout = Path(config["critic_checkout"] if role == "critic"
                    else config["worker_checkout"])
    marker_name = f".awf-qualification-{record_id}-{role}.tmp"
    checkout_marker = checkout / marker_name
    outside_marker = Path(config["state_dir"]) / marker_name
    require(not checkout_marker.exists() and not checkout_marker.is_symlink()
            and not outside_marker.exists() and not outside_marker.is_symlink(),
            "Qualification marker already exists")
    marker_text = f"AWF qualification {record_id} {role}"
    result = None
    artifacts = None
    error = None
    observation = None
    try:
        envelope = driver.qualification_agent(role, record_id, {
            "checkout_marker": str(checkout_marker),
            "outside_marker": str(outside_marker),
            "marker_text": marker_text,
            "network_url": "https://api.github.com/meta",
            "credential_environment_name_pattern":
                "(?i)(TOKEN|API_KEY|SECRET|PASSWORD|CREDENTIAL)",
            "agent_auth_locations": list(AUTH_LOCATIONS),
        })
        require(isinstance(envelope, dict)
                and set(envelope) == {"result", "artifacts"},
                "Invalid qualification-agent evidence envelope")
        result = validate_agent_result(envelope["result"], role)
        artifacts = _validate_artifacts(envelope["artifacts"])
    except Exception as exc:  # A failed live probe is evidence, not qualification.
        error = f"{type(exc).__name__}: {exc}"[:4000]
    checkout_state = "ABSENT"
    if checkout_marker.is_symlink():
        checkout_state = "UNSAFE"
    elif checkout_marker.is_file():
        try:
            checkout_state = ("EXACT" if checkout_marker.read_bytes()
                              == marker_text.encode("utf-8") else "MISMATCH")
        except OSError:
            checkout_state = "ERROR"
    outside_state = "ABSENT"
    if outside_marker.is_symlink():
        outside_state = "UNSAFE"
    elif outside_marker.exists():
        outside_state = "PRESENT"
    for path in (checkout_marker, outside_marker):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    try:
        clean = not driver.git(checkout, "status", "--porcelain",
                               "--untracked-files=all")
    except Exception:
        clean = False
    observation = {
        "checkout_marker": checkout_state,
        "outside_marker": outside_state,
        "checkout_clean_after_cleanup": clean,
    }
    return {
        "status": "OBSERVED" if error is None else "ERROR",
        "result": result,
        "artifacts": artifacts,
        "error": error,
        "host_observation": observation,
    }


def collect_qualification(config, driver, store, *, confirm_disposable_pr,
                          observed_at=None, record_id=None):
    """Run live probes and atomically write their record inside state_dir."""
    require(confirm_disposable_pr is True,
            "qualify requires explicit confirmation that this is a disposable PR")
    validate_config_qualification(config, config["state_dir"],
                                  require_record=False)
    record_id = record_id or str(uuid.uuid4())
    try:
        require(str(uuid.UUID(record_id)) == record_id,
                "Qualification record id must be a canonical UUID")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Qualification record id must be a canonical UUID") from exc
    candidate = driver.snapshot()
    require(candidate is not None, "Disposable qualification PR is closed")
    driver.preflight(candidate)
    findings = []
    probes = {}
    for role in ("critic", "worker"):
        probe = _probe_agent(driver, config, role, record_id)
        probes[role] = probe
        if probe["status"] == "OBSERVED":
            findings.extend(_agent_finding(role, probe["result"],
                                           probe["host_observation"]))
        else:
            findings.append({
                "id": f"QUAL-{role.upper()}-PROBE-ERROR",
                "severity": "BLOCKER",
                "message": probe["error"],
            })
    try:
        branch, database = _database_probes(store, config, candidate, record_id)
    except Exception as exc:
        branch = {"unowned_before_probe": False,
                  "unique_owner_constraint": False,
                  "temporary_lease_rolled_back": False}
        database = {"relative_path": "review-loop.sqlite3",
                    "resolved_path_sha256": "0" * 64,
                    "integrity_check": "ERROR",
                    "writer_lock_exclusive": False}
        findings.append({
            "id": "QUAL-BRANCH-DATABASE-PROBE",
            "severity": "BLOCKER",
            "message": f"{type(exc).__name__}: {exc}"[:4000],
        })
    probes["branch_lease"] = branch
    probes["canonical_database"] = database
    flags = _derived_flags(probes)
    result = "PASS" if all(flags.values()) and not findings else "FAIL"
    record = {
        "format": FORMAT,
        "record_id": record_id,
        "observed_at": observed_at or now_text(),
        "operator": config["qualification"]["operator"],
        "host_binding_sha256": host_binding_sha256(config),
        "disposable_pr_confirmed": True,
        "candidate": deepcopy(candidate),
        "probes": probes,
        "findings": findings,
        "qualification": flags,
        "result": result,
        "execution_authority": False,
    }
    # Parse the timestamp even for a failing record so malformed operator input
    # cannot become retained evidence.
    timestamp(record["observed_at"])
    encoded = (json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)
               + "\n").encode("utf-8")
    evidence = _evidence_path(config, config["state_dir"])
    state = Path(config["state_dir"]).resolve(strict=True)
    with Tree(state) as tree:
        tree.write(evidence.name, encoded)
    return {
        "status": "QUALIFICATION_EVIDENCE_RECORDED",
        "result": result,
        "record_id": record_id,
        "evidence_path": str(evidence),
        "evidence_sha256": sha256(encoded),
        "qualification_updated": False,
        "findings": findings,
        "qualification": flags,
        "execution_authority": False,
    }
