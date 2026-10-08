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
from .canonical import load, now_text
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


def structural_problems(snapshot):
    """List required K15 fields that are missing or not well-formed basis-tagged fields.

    A missing field is not an explicit ``unavailable`` one: only a present
    field with ``basis: unavailable`` and a null value records an unobservable fact.
    """
    problems = []
    if not isinstance(snapshot.get("generated_at"), str) or not snapshot["generated_at"]:
        problems.append({"field": "generated_at", "problem": "missing"})
    for key in FIELDS:
        item = _entry(snapshot, key)
        if item is _MISSING:
            problems.append({"field": key, "problem": "missing"})
        elif not (isinstance(item, dict) and set(item) == {"value", "basis", "observed_at"}
                  and item["basis"] in BASES and isinstance(item["observed_at"], str) and item["observed_at"]
                  and (item["value"] is None) == (item["basis"] == "unavailable")):
            problems.append({"field": key, "problem": "malformed"})
    return problems


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
             for key in COMPARED if _lookup(received, key) != _lookup(current, key)]
    return {"status": "MATCH" if not drift else "DRIFT", "received_at": received.get("generated_at"),
            "observed_at": current["generated_at"], "drift": drift, "execution_authority": False}


def _cell(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def render_markdown(snapshot):
    lines = [f"# AWF handoff snapshot ({snapshot['generated_at']})", "",
             "Observations only; grants no execution, merge or Jira authority.", "",
             "| Field | Value | Basis |", "| --- | --- | --- |"]
    for section in ("repository", "awf", "adoption", "operating", "jira"):
        for key, item in snapshot[section].items():
            lines.append(f"| {section}.{key} | {_cell(item['value'])} | {item['basis']} |")
    lines.append(f"| external_resources | {_cell(snapshot['external_resources']['value'])} | {snapshot['external_resources']['basis']} |")
    lines += ["", "## Blockers", ""]
    lines += [f"- {b['code']} ({b['state']}): {b['evidence']}" for b in snapshot["blockers"]["value"]] or ["- none"]
    return "\n".join(lines) + "\n"
