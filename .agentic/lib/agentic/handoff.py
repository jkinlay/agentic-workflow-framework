"""Read-only project handoff snapshot (K15) and its receiving-host comparison.

The snapshot records observations only. It carries no credentials and grants
no execution, merge or Jira authority; a receiving host compares it with
``workflow.py doctor --handoff`` instead of trusting it.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess

from . import VERSION
from .canonical import load, now_text, timestamp
from .child_process import child_env

SCHEMA = "awf-handoff-snapshot-1"
BASES = ("verified", "configured", "user-asserted", "unavailable")
REQUIRED_FIELDS = (
    "repository.path", "repository.origin", "repository.id", "repository.branch", "repository.head",
    "awf.version", "awf.project_state", "awf.trust_basis",
    "adoption.pr", "adoption.merge",
    "operating.hash", "operating.routes",
    "jira.cloud_id", "jira.provider_project_id", "jira.project_key", "jira.controller_actor_id",
    "external_resources", "completed_tickets", "blockers",
)
# A checkout can legitimately move to a different local path on the receiving
# host. The remaining K15 facts are compared as the deterministic handoff
# continuity record.
COMPARED = tuple(field for field in REQUIRED_FIELDS if field != "repository.path")
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


def _operating_routes(root):
    """Return only the route-bearing operating choices, never audit prose."""
    try:
        operating = load(Path(root) / "OPERATING_CONFIG.yaml")
    except (OSError, ValueError):
        return None
    if not isinstance(operating, dict):
        return None
    routes = {key: operating[key] for key in ("controller", "critic", "specialist", "simple_worker")
              if key in operating}
    streams = operating.get("streams")
    if isinstance(streams, dict):
        selected = {key: {name: value[name] for name in ("worker", "reviewer") if name in value}
                    for key, value in streams.items() if isinstance(value, dict)
                    and any(name in value for name in ("worker", "reviewer"))}
        if selected:
            routes["streams"] = selected
    overrides = operating.get("epic_overrides")
    if isinstance(overrides, dict):
        selected = {}
        for epic, value in overrides.items():
            if not isinstance(value, dict):
                continue
            entry = {key: value[key] for key in ("controller", "critic", "specialist") if key in value}
            streams = value.get("streams")
            if isinstance(streams, dict):
                scoped = {key: {name: route[name] for name in ("worker", "reviewer") if name in route}
                          for key, route in streams.items() if isinstance(route, dict)
                          and any(name in route for name in ("worker", "reviewer"))}
                if scoped:
                    entry["streams"] = scoped
            if entry:
                selected[epic] = entry
        if selected:
            routes["epic_overrides"] = selected
    return routes


def _field_at(snapshot, dotted):
    item = snapshot
    for part in dotted.split("."):
        if not isinstance(item, dict) or part not in item:
            return None
        item = item[part]
    return item


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
    jira = config.get("jira") or {}
    github = config.get("github") or {}
    execution = config.get("execution") or {}
    resources = sorted(((execution.get("host_broker") or {}).get("resources") or {}).keys())
    operating = status.get("operating") or {}
    repository_id = status.get("repository_id")
    repository_basis = "verified"
    if repository_id is None:
        repository_id = github.get("repository_id")
        repository_basis = "configured"
    trust_basis = status.get("release_trust_basis")
    if trust_basis is None and isinstance(status.get("release_trust"), dict):
        trust_basis = status["release_trust"].get("basis")
    routes = _operating_routes(root)
    routes_basis = "verified" if operating.get("status") == "ACCEPTED" else "configured"
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
            "id": _field(repository_id, repository_basis, now),
            "branch": _field(branch, "verified", now),
            "head": _field(_git(root, "rev-parse", "HEAD"), "verified", now),
        },
        "awf": {
            "version": _field(VERSION, "verified" if status.get("integrity_valid") else "configured", now),
            "project_state": _field(status.get("project_state"), "verified", now),
            # Retain the earlier coarse status observations for consumers of
            # v1 snapshots; the detailed adoption identity is recorded below.
            "integrity_valid": _field(status.get("integrity_valid"), "verified", now),
            "adoption": _field(None if status.get("adoption") == "UNOBSERVED" else status.get("adoption"), "verified", now),
            "trust_basis": _field(trust_basis, "verified", now),
        },
        "adoption": {
            "pr": _field(status.get("adoption_pr"), "verified", now),
            "merge": _field(status.get("adoption_merge_sha"), "verified", now),
        },
        "operating": {
            "hash": _field(operating.get("hash"), "verified", now),
            "routes": _field(routes, routes_basis, now),
        },
        "jira": {key: _field(jira.get(key), "configured", now)
                 for key in ("cloud_id", "provider_project_id", "project_key", "controller_actor_id")},
        "external_resources": _field(resources, "configured", now),
        "completed_tickets": _field(status.get("completed_tickets"), "verified", now),
        "blockers": _field(blockers, "verified", now),
    }


def _lookup(snapshot, dotted):
    item = _field_at(snapshot, dotted)
    return item.get("value") if isinstance(item, dict) and "basis" in item else item


def _snapshot_errors(snapshot):
    """Identify absent and malformed required observations before comparison."""
    errors = []
    if not isinstance(snapshot.get("generated_at"), str):
        errors.append("generated_at")
    else:
        try:
            timestamp(snapshot["generated_at"])
        except (TypeError, ValueError):
            errors.append("generated_at")
    for dotted in REQUIRED_FIELDS:
        item = _field_at(snapshot, dotted)
        if not isinstance(item, dict) or set(item) != {"value", "basis", "observed_at"}:
            errors.append(dotted)
            continue
        if item["basis"] not in BASES or not isinstance(item["observed_at"], str):
            errors.append(dotted)
            continue
        try:
            timestamp(item["observed_at"])
        except (TypeError, ValueError):
            errors.append(dotted)
            continue
        if (item["value"] is None) != (item["basis"] == "unavailable"):
            errors.append(dotted)
    return errors


def compare_snapshot(received, current):
    """Compare a received snapshot with a freshly built one; never adopts received state."""
    if not isinstance(received, dict) or received.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "Not an " + SCHEMA + " document", "execution_authority": False}
    if any(received.get(key) is not False for key in ("execution_authority", "merge_authority", "jira_authority")):
        return {"status": "REJECTED", "reason": "A handoff snapshot cannot carry authority", "execution_authority": False}
    errors = _snapshot_errors(received)
    if errors:
        return {"status": "REJECTED", "reason": "Incomplete or malformed handoff snapshot",
                "invalid_fields": errors, "execution_authority": False}
    drift = [{"field": key, "received": _lookup(received, key), "current": _lookup(current, key)}
             for key in COMPARED if _lookup(received, key) != _lookup(current, key)]
    return {"status": "MATCH" if not drift else "DRIFT", "received_at": received.get("generated_at"),
            "observed_at": current["generated_at"], "drift": drift, "execution_authority": False}


def render_markdown(snapshot):
    lines = [f"# AWF handoff snapshot ({snapshot['generated_at']})", "",
             "Observations only; grants no execution, merge or Jira authority.", "",
             "| Field | Value | Basis | Observed at |", "| --- | --- | --- | --- |"]
    for dotted in REQUIRED_FIELDS:
        item = _field_at(snapshot, dotted)
        lines.append(f"| {dotted} | {item['value']} | {item['basis']} | {item['observed_at']} |")
    lines += ["", "## Blockers", ""]
    lines += [f"- {b['code']} ({b['state']}): {b['evidence']}" for b in snapshot["blockers"]["value"]] or ["- none"]
    return "\n".join(lines) + "\n"
