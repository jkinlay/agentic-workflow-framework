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
from .canonical import load, now_text
from .child_process import child_env

SCHEMA = "awf-handoff-snapshot-1"
BASES = ("verified", "configured", "user-asserted", "unavailable")
COMPARED = ("repository.origin", "repository.branch", "repository.head", "awf.version",
            "awf.project_state", "awf.integrity_valid", "operating.hash", "jira.cloud_id",
            "jira.provider_project_id", "jira.project_key", "jira.controller_actor_id",
            "external_resources")
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
    resources = sorted(((config.get("host_broker") or {}).get("resources") or {}).keys())
    operating = status.get("operating") or {}
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
            "branch": _field(branch, "verified", now),
            "head": _field(_git(root, "rev-parse", "HEAD"), "verified", now),
        },
        "awf": {
            "version": _field(VERSION, "verified" if status.get("integrity_valid") else "configured", now),
            "project_state": _field(status.get("project_state"), "verified", now),
            "integrity_valid": _field(bool(status.get("integrity_valid")), "verified", now),
            "adoption": _field(None if status.get("adoption") == "UNOBSERVED" else status.get("adoption"), "verified", now),
        },
        "operating": {"hash": _field(operating.get("hash"), "verified", now)},
        "jira": {key: _field(jira.get(key), "configured", now)
                 for key in ("cloud_id", "provider_project_id", "project_key", "controller_actor_id")},
        "external_resources": _field(resources or None, "configured", now),
        "blockers": _field(blockers, "verified", now),
    }


def _lookup(snapshot, dotted):
    item = snapshot
    for part in dotted.split("."):
        if not isinstance(item, dict) or part not in item:
            return None
        item = item[part]
    return item.get("value") if isinstance(item, dict) and "basis" in item else item


def compare_snapshot(received, current):
    """Compare a received snapshot with a freshly built one; never adopts received state."""
    if not isinstance(received, dict) or received.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "Not an " + SCHEMA + " document", "execution_authority": False}
    if any(received.get(key) is not False for key in ("execution_authority", "merge_authority", "jira_authority")):
        return {"status": "REJECTED", "reason": "A handoff snapshot cannot carry authority", "execution_authority": False}
    drift = [{"field": key, "received": _lookup(received, key), "current": _lookup(current, key)}
             for key in COMPARED if _lookup(received, key) != _lookup(current, key)]
    return {"status": "MATCH" if not drift else "DRIFT", "received_at": received.get("generated_at"),
            "observed_at": current["generated_at"], "drift": drift, "execution_authority": False}


def render_markdown(snapshot):
    lines = [f"# AWF handoff snapshot ({snapshot['generated_at']})", "",
             "Observations only; grants no execution, merge or Jira authority.", "",
             "| Field | Value | Basis |", "| --- | --- | --- |"]
    for section in ("repository", "awf", "operating", "jira"):
        for key, item in snapshot[section].items():
            lines.append(f"| {section}.{key} | {item['value']} | {item['basis']} |")
    lines.append(f"| external_resources | {snapshot['external_resources']['value']} | {snapshot['external_resources']['basis']} |")
    lines += ["", "## Blockers", ""]
    lines += [f"- {b['code']} ({b['state']}): {b['evidence']}" for b in snapshot["blockers"]["value"]] or ["- none"]
    return "\n".join(lines) + "\n"
