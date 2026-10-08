"""Read-only project handoff snapshot (K15) and its receiving-host comparison.

The snapshot records observations only. It carries no credentials and grants
no execution, merge or Jira authority; a receiving host compares it with
``workflow.py doctor --handoff`` instead of trusting it.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess

from . import VERSION, ValidationError
from .canonical import load, now_text, timestamp
from .child_process import child_env

SCHEMA = "awf-handoff-snapshot-1"
BASES = ("verified", "configured", "user-asserted", "unavailable")
# Every K15 fact, each a basis-tagged field. A received snapshot must carry all
# of them; an unobservable fact is recorded as ``unavailable``, never omitted.
FIELDS = ("repository.path", "repository.origin", "repository.numeric_id", "repository.branch",
          "repository.head", "awf.version", "awf.project_state", "awf.integrity_valid",
          "awf.trust_basis", "adoption.state", "adoption.pr", "adoption.merge_commit",
          "operating.hash", "operating.routes", "jira.cloud_id", "jira.provider_project_id",
          "jira.project_key", "jira.controller_actor_id", "external_resources", "blockers")
# Host-local facts (checkout path, current blockers) are recorded but not compared.
COMPARED = tuple(key for key in FIELDS if key not in ("repository.path", "blockers"))
_AUTHORITY = ("execution_authority", "merge_authority", "jira_authority")
_USERINFO = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@]*@")


def _field(value, basis, observed_at):
    if value is None:
        basis = "unavailable"
    return {"value": value, "basis": basis, "observed_at": observed_at}


def _git(root, *arguments):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        process = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=20, env=child_env(env))
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode != 0:
        return None
    return process.stdout.strip() or None


def safe_origin(url):
    """Drop any user-info (tokens, passwords) from a remote URL."""
    return None if url is None else _USERINFO.sub(r"\1", url)


def _operating_routes(root, config, operating):
    """Model routes of the accepted operating snapshot named by status, else None."""
    if not isinstance(operating, dict) or operating.get("status") != "ACCEPTED" or not operating.get("hash"):
        return None
    try:
        from .operating import read_operating
        snapshot = read_operating(root, config)
    except (ValidationError, OSError, ValueError, KeyError, TypeError, UnicodeError):
        return None
    if snapshot.operating_hash != operating["hash"]:
        return None
    return {key: value for key, value in snapshot.config.items() if key not in ("version", "source")}


def _declared_resources(config):
    """Named resources declared under execution.host_broker.resources, else None."""
    execution = config.get("execution")
    broker = execution.get("host_broker") if isinstance(execution, dict) else None
    if not isinstance(broker, dict):
        return None
    resources = broker.get("resources", {})
    if not isinstance(resources, dict):
        return None
    return {name: resources[name] for name in sorted(resources)}


def build_snapshot(root, *, status=None, now=None):
    root = Path(root).absolute()
    now = now or now_text()
    if status is None:
        from .providers.github_status import project_status
        status = project_status(root)
    config_path = root / ".agentic/PROJECT_CONFIG.yaml"
    try:
        config = load(config_path)
    except (OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    jira = config.get("jira") or {}
    github = config.get("github") or {}
    operating = status.get("operating") or {}
    if status.get("repository_id") is not None:
        numeric_id = _field(status["repository_id"], "verified", now)
    else:
        numeric_id = _field(github.get("repository_id"), "configured", now)
    trust_passed = any(item.get("code") == "RELEASE_TRUST" and item.get("state") == "PASS"
                       for item in status.get("checks", []))
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    blockers = [{"code": item.get("code"), "state": item.get("state"), "evidence": item.get("evidence")}
                for item in status.get("checks", []) if item.get("state") not in ("PASS", "N_A", "SKIP")]
    return {
        "schema": SCHEMA,
        "generated_at": now,
        "execution_authority": False,
        "merge_authority": False,
        "jira_authority": False,
        "repository": {
            "path": _field(str(root), "verified", now),
            "origin": _field(safe_origin(_git(root, "remote", "get-url", "origin")), "verified", now),
            "numeric_id": numeric_id,
            "branch": _field(branch, "verified", now),
            "head": _field(_git(root, "rev-parse", "HEAD"), "verified", now),
        },
        "awf": {
            "version": _field(VERSION, "verified" if status.get("integrity_valid") else "configured", now),
            "project_state": _field(status.get("project_state"), "verified", now),
            "integrity_valid": _field(bool(status.get("integrity_valid")), "verified", now),
            "trust_basis": _field(status.get("release_trust_basis") if trust_passed else None, "verified", now),
        },
        "adoption": {
            "state": _field(None if status.get("adoption") == "UNOBSERVED" else status.get("adoption"), "verified", now),
            "pr": _field(status.get("adoption_pr"), "verified", now),
            "merge_commit": _field(status.get("adoption_merge_sha"), "verified", now),
        },
        "operating": {"hash": _field(operating.get("hash"), "verified", now),
                      "routes": _field(_operating_routes(root, config, operating), "verified", now)},
        "jira": {key: _field(jira.get(key), "configured", now)
                 for key in ("cloud_id", "provider_project_id", "project_key", "controller_actor_id")},
        "external_resources": _field(_declared_resources(config), "configured", now),
        "blockers": _field(blockers, "verified", now),
    }


_MISSING = object()


def _entry(snapshot, dotted):
    item = snapshot
    for part in dotted.split("."):
        if not isinstance(item, dict) or part not in item:
            return _MISSING
        item = item[part]
    return item


def _lookup(snapshot, dotted):
    return _entry(snapshot, dotted)["value"]


def _text(value):
    return isinstance(value, str) and bool(value)


def _positive_integer(value):
    return type(value) is int and value > 0


def _object_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None


def _timestamp(value):
    try:
        timestamp(value)
    except ValidationError:
        return False
    return True


def _routes(value):
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _resources(value):
    return isinstance(value, dict) and all(isinstance(key, str) and _positive_integer(slots)
                                           for key, slots in value.items())


def _blockers(value):
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == {"code", "state", "evidence"}
        and all(entry is None or isinstance(entry, str) for entry in item.values()) for item in value)


# Value type of every K15 field when it is observed (a null value means unavailable).
VALUE_CHECKS = {
    "repository.path": _text, "repository.origin": _text, "repository.numeric_id": _positive_integer,
    "repository.branch": _text, "repository.head": _object_id,
    "awf.version": lambda value: isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is not None,
    "awf.project_state": _text, "awf.integrity_valid": lambda value: type(value) is bool, "awf.trust_basis": _text,
    "adoption.state": _text, "adoption.pr": _positive_integer, "adoption.merge_commit": _object_id,
    "operating.hash": lambda value: isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
    "operating.routes": _routes,
    "jira.cloud_id": _text, "jira.provider_project_id": _text, "jira.project_key": _text,
    "jira.controller_actor_id": _text, "external_resources": _resources, "blockers": _blockers,
}
_SECTIONS = {section: {key.split(".")[1] for key in FIELDS if key.startswith(section + ".")}
             for section in dict.fromkeys(key.split(".")[0] for key in FIELDS if "." in key)}
_TOP_LEVEL = {"schema", "generated_at", *_AUTHORITY, *_SECTIONS, *(key for key in FIELDS if "." not in key)}


def _field_problem(item, check):
    if not isinstance(item, dict) or set(item) != {"value", "basis", "observed_at"}:
        return "malformed"
    if item["basis"] not in BASES or (item["value"] is None) != (item["basis"] == "unavailable"):
        return "malformed"
    if not _timestamp(item["observed_at"]):
        return "invalid observed_at"
    if item["value"] is not None and not check(item["value"]):
        return "invalid value"
    return None


def structural_problems(snapshot):
    """List required K15 fields that are missing, malformed or unexpected.

    A missing field is not an explicit ``unavailable`` one: only a present
    field with ``basis: unavailable`` and a null value records an unobservable
    fact.  Values must have the field's type and timestamps must be RFC 3339,
    so a malformed value can never compare equal to a current one.
    """
    problems = []
    if "generated_at" not in snapshot:
        problems.append({"field": "generated_at", "problem": "missing"})
    elif not _timestamp(snapshot["generated_at"]):
        problems.append({"field": "generated_at", "problem": "invalid timestamp"})
    problems.extend({"field": key, "problem": "unexpected"} for key in sorted(set(snapshot) - _TOP_LEVEL))
    for section, names in sorted(_SECTIONS.items()):
        if isinstance(snapshot.get(section), dict):
            problems.extend({"field": section + "." + key, "problem": "unexpected"}
                            for key in sorted(set(snapshot[section]) - names))
    for key in FIELDS:
        item = _entry(snapshot, key)
        problem = "missing" if item is _MISSING else _field_problem(item, VALUE_CHECKS[key])
        if problem:
            problems.append({"field": key, "problem": problem})
    return problems


def _same(left, right):
    # Exact JSON identity: 0 never equals False and 1.0 never equals 1.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def compare_snapshot(received, current):
    """Compare a received snapshot with a freshly built one; never adopts received state."""
    if not isinstance(received, dict) or received.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "Not an " + SCHEMA + " document", "execution_authority": False}
    if any(received.get(key) is not False for key in _AUTHORITY):
        return {"status": "REJECTED", "reason": "A handoff snapshot cannot carry authority", "execution_authority": False}
    problems = structural_problems(received)
    if problems:
        return {"status": "REJECTED", "reason": "Incomplete or malformed handoff snapshot; export it again with workflow.py handoff",
                "problems": problems, "execution_authority": False}
    drift = [{"field": key, "received": _lookup(received, key), "current": _lookup(current, key)}
             for key in COMPARED if not _same(_lookup(received, key), _lookup(current, key))]
    return {"status": "MATCH" if not drift else "DRIFT", "received_at": received.get("generated_at"),
            "observed_at": current["generated_at"], "drift": drift, "execution_authority": False}


def _cell(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def render_markdown(snapshot):
    lines = [f"# AWF handoff snapshot ({snapshot['generated_at']})", "",
             "Observations only; grants no execution, merge or Jira authority.", "",
             "| Field | Value | Basis | Observed at |", "| --- | --- | --- | --- |"]
    for key in FIELDS:
        if key == "blockers":
            continue
        item = _entry(snapshot, key)
        lines.append(f"| {key} | {_cell(item['value'])} | {item['basis']} | {item['observed_at']} |")
    blockers = snapshot["blockers"]
    lines += ["", f"## Blockers ({blockers['basis']}, observed at {blockers['observed_at']})", ""]
    lines += [f"- {b['code']} ({b['state']}): {b['evidence']}" for b in snapshot["blockers"]["value"]] or ["- none"]
    return "\n".join(lines) + "\n"
