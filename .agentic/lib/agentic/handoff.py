"""Read-only project handoff snapshot (K15) and its receiving-host comparison.

The snapshot records observations only. It carries no credentials and grants
no execution, merge or Jira authority; a receiving host compares it with
``workflow.py doctor --handoff`` instead of trusting it.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urlsplit, urlunsplit

from . import VERSION
from .canonical import fingerprint, load, loads, now_text, timestamp
from .child_process import child_env
from .operating import read_operating

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
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_RESOURCE_NAME = re.compile(r"[a-z][a-z0-9_]*")
_TICKET = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]*-[1-9][0-9]*)(?![A-Z0-9_])")
_EPIC = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*")
_ROUTE_KEYS = ("model", "reasoning_effort", "pinned")
_URL_SCHEMES = {"git", "git+ssh", "http", "https", "ssh"}
_SCP_REMOTE = re.compile(
    r"(?:(?P<userinfo>[^@/\\\s]+)@)?"
    r"(?P<host>(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])):"
    r"(?P<path>[^\s\\]+)"
)


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
    """Return a parsed network remote without credentials, or ``None``.

    URL remotes lose user-info, query and fragment components. SCP-style
    remotes retain only a conventional bare ``git@`` user; all other user-info
    is dropped. Local paths and values that cannot be classified fail closed.
    """
    if (not isinstance(url, str) or not url or url != url.strip() or
            any(ord(character) < 32 for character in url)):
        return None
    stripped = re.split(r"[?#]", url, maxsplit=1)[0]
    if "://" in stripped:
        try:
            parsed = urlsplit(stripped)
            if (parsed.scheme.lower() not in _URL_SCHEMES or not parsed.hostname or not parsed.path or
                    "\\" in parsed.path or any(character.isspace() for character in parsed.path)):
                return None
            port = parsed.port
        except ValueError:
            return None
        hostname = parsed.hostname
        authority = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None:
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))

    # Drive-relative and drive-absolute Windows paths otherwise resemble an
    # SCP remote (``C:path``). Other local path forms do not match the grammar.
    if re.match(r"^[A-Za-z]:[/\\]?", stripped):
        return None
    match = _SCP_REMOTE.fullmatch(stripped)
    if not match or match["path"].startswith(("/", "./", "../")):
        return None
    user = "git@" if match["userinfo"] == "git" else ""
    return f"{user}{match['host']}:{match['path']}"


def _route(value, *, simple_worker=False):
    """Project a validated route to the only fields a handoff can disclose."""
    if not isinstance(value, dict):
        return None
    keys = _ROUTE_KEYS + (("enabled",) if simple_worker else ())
    selected = {key: value[key] for key in keys if key in value}
    return selected or None


def _operating_routes(root, governance):
    """Return a credential-free route projection and its validated hash.

    ``read_operating`` observes the same immutable operating snapshot that
    computes its hash.  The caller binds that hash to the earlier status
    observation before marking either fact verified.
    """
    try:
        operating = read_operating(root, governance)
    except (OSError, ValueError):
        return None, None
    value = operating.config
    routes = {}
    for key in ("controller", "critic", "specialist"):
        route = _route(value.get(key))
        if route:
            routes[key] = route
    simple_worker = _route(value.get("simple_worker"), simple_worker=True)
    if simple_worker:
        routes["simple_worker"] = simple_worker
    streams = value.get("streams")
    if isinstance(streams, dict):
        selected = {}
        for key, stream in streams.items():
            if not isinstance(key, str) or key not in "ABCDEF" or not isinstance(stream, dict):
                continue
            entry = {name: route for name in ("worker", "reviewer")
                     if (route := _route(stream.get(name)))}
            if entry:
                selected[key] = entry
        if selected:
            routes["streams"] = selected
    overrides = value.get("epic_overrides")
    if isinstance(overrides, dict):
        selected = {}
        for epic, value in overrides.items():
            if not isinstance(epic, str) or not _EPIC.fullmatch(epic) or not isinstance(value, dict):
                continue
            entry = {key: route for key in ("controller", "critic", "specialist")
                     if (route := _route(value.get(key)))}
            streams = value.get("streams")
            if isinstance(streams, dict):
                scoped = {}
                for key, stream in streams.items():
                    if not isinstance(key, str) or key not in "ABCDEF" or not isinstance(stream, dict):
                        continue
                    routes_for_stream = {name: route for name in ("worker", "reviewer")
                                         if (route := _route(stream.get(name)))}
                    if routes_for_stream:
                        scoped[key] = routes_for_stream
                if scoped:
                    entry["streams"] = scoped
            if entry:
                selected[epic] = entry
        if selected:
            routes["epic_overrides"] = selected
    return routes, operating.operating_hash


def _completed_tickets(state_path, project_id, root):
    """Read DONE tickets from a project-bound, hash-chained coordinator store.

    The AWF coordinator SQLite database is the authoritative local ticket
    lifecycle source. The caller must explicitly supply its protected path;
    absent, unreadable, unbound or internally inconsistent state is
    unavailable. Git history and commit subjects are never closure evidence.
    """
    if state_path is None or not isinstance(project_id, str):
        return None
    path = Path(state_path).absolute()
    try:
        if (not path.is_file() or path.resolve().is_relative_to(Path(root).resolve()) or
                path.stat().st_nlink > 1 or any(
                    parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction())
                    for parent in (path, *path.parents) if parent.exists())):
            return None
        database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        database.row_factory = sqlite3.Row
        try:
            database.execute("PRAGMA query_only=ON")
            database.execute("BEGIN")
            integrity = database.execute("PRAGMA quick_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                return None
            metadata = {row["key"]: row["value"]
                        for row in database.execute("SELECT key,value FROM meta")}
            if metadata.get("schema") != "3" or metadata.get("project") != project_id:
                return None

            previous, sequence = "0" * 64, 0
            observed_tickets = {}
            for row in database.execute("SELECT * FROM events ORDER BY sequence"):
                sequence += 1
                payload = loads(row["payload"])
                envelope = {
                    "sequence": row["sequence"], "event_id": row["event_id"],
                    "project_id": project_id, "event_type": row["event_type"],
                    "timestamp": row["timestamp"], "external_id": row["external_id"],
                    "payload": payload, "previous_hash": row["previous_hash"],
                }
                if (row["sequence"] != sequence or row["previous_hash"] != previous or
                        fingerprint("event", envelope) != row["event_hash"]):
                    return None
                previous = row["event_hash"]
                if isinstance(payload, dict) and {"issue_id", "state", "revision"} <= set(payload):
                    observed_tickets[payload["issue_id"]] = (
                        payload["state"], payload["revision"], row["event_type"])

            completed = []
            for row in database.execute("SELECT issue_id,state,revision FROM tickets ORDER BY issue_id"):
                observed = observed_tickets.get(row["issue_id"])
                if observed is None or observed[:2] != (row["state"], row["revision"]):
                    return None
                if row["state"] == "DONE":
                    if (observed[2] != "JIRA_RECONCILED" or not isinstance(row["issue_id"], str) or
                            not _TICKET.fullmatch(row["issue_id"])):
                        return None
                    completed.append(row["issue_id"])
            return completed
        finally:
            database.close()
    except (OSError, sqlite3.Error, TypeError, ValueError, UnicodeError):
        return None


def _field_at(snapshot, dotted):
    item = snapshot
    for part in dotted.split("."):
        if not isinstance(item, dict) or part not in item:
            return None
        item = item[part]
    return item


def build_snapshot(root, *, status=None, now=None, state_path=None):
    """Build K15 observations, optionally reading protected coordinator state.

    ``completed_tickets`` is verified only from ``state_path`` after its AWF
    project binding and event hash chain validate. Without that authoritative
    source the field is unavailable, regardless of commit messages.
    """
    root = Path(root).absolute()
    now = now or now_text()
    if status is None:
        from .providers.github_status import project_status
        status = project_status(root)
    config_path = root / ".agentic/PROJECT_CONFIG.yaml"
    try:
        config = load(config_path)
    except (OSError, ValueError):
        config = None
    if not isinstance(config, dict):
        config = None
    jira = config.get("jira") if config else {}
    github = config.get("github") if config else {}
    execution = config.get("execution") if config else None
    project = config.get("project") if config else None
    jira = jira if isinstance(jira, dict) else {}
    github = github if isinstance(github, dict) else {}
    broker = execution.get("host_broker") if isinstance(execution, dict) else None
    project_id = project.get("id") if isinstance(project, dict) else None
    declared_resources = broker.get("resources") if isinstance(broker, dict) else None
    resources = (sorted(name for name, slots in declared_resources.items()
                        if isinstance(name, str) and _RESOURCE_NAME.fullmatch(name)
                        and type(slots) is int and slots > 0)
                 if isinstance(declared_resources, dict) else None)
    operating = status.get("operating") or {}
    repository_id = status.get("repository_id")
    repository_basis = "verified"
    if repository_id is None:
        repository_id = github.get("repository_id")
        repository_basis = "configured"
    trust_basis = status.get("release_trust_basis")
    if trust_basis is None and isinstance(status.get("release_trust"), dict):
        trust_basis = status["release_trust"].get("basis")
    operating_hash = operating.get("hash")
    routes = None
    if config is not None and operating.get("status") == "ACCEPTED":
        routes, observed_operating_hash = _operating_routes(root, config)
        # ``project_status`` and this read are separate observations.  Do not
        # label a new route set as verified against an older operating hash.
        if observed_operating_hash != operating_hash:
            operating_hash, routes = None, None
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
            "hash": _field(operating_hash, "verified", now),
            "routes": _field(routes, "verified", now),
        },
        "jira": {key: _field(jira.get(key), "configured", now)
                 for key in ("cloud_id", "provider_project_id", "project_key", "controller_actor_id")},
        "external_resources": _field(resources, "configured", now),
        "completed_tickets": _field(_completed_tickets(state_path, project_id, root), "verified", now),
        "blockers": _field(blockers, "verified", now),
    }


def _comparison_fact(snapshot, dotted):
    item = _field_at(snapshot, dotted)
    return (item["value"], item["basis"])


def _text(value):
    return isinstance(value, str) and bool(value)


def _route_value(value, *, simple_worker=False):
    allowed = set(_ROUTE_KEYS) | ({"enabled"} if simple_worker else set())
    if not isinstance(value, dict) or not value or not set(value) <= allowed:
        return False
    if not {"model", "reasoning_effort"} <= set(value):
        return False
    if not _text(value["model"]):
        return False
    if not _text(value["reasoning_effort"]):
        return False
    if "pinned" in value and type(value["pinned"]) is not bool:
        return False
    return not simple_worker or ("enabled" in value and type(value["enabled"]) is bool)


def _route_set(value):
    if not isinstance(value, dict) or not value:
        return False
    allowed = {"controller", "critic", "specialist", "simple_worker", "streams", "epic_overrides"}
    if not set(value) <= allowed:
        return False
    if not {"controller", "specialist", "simple_worker", "streams"} <= set(value):
        return False
    for name in ("controller", "critic", "specialist"):
        if name in value and not _route_value(value[name]):
            return False
    if "simple_worker" in value and not _route_value(value["simple_worker"], simple_worker=True):
        return False

    def streams_valid(streams):
        if not isinstance(streams, dict) or not streams:
            return False
        for name, routes in streams.items():
            if name not in "ABCDEF" or not isinstance(routes, dict) or not routes or not set(routes) <= {"worker", "reviewer"}:
                return False
            if any(not _route_value(route) for route in routes.values()):
                return False
        return True

    if "streams" in value and not streams_valid(value["streams"]):
        return False
    overrides = value.get("epic_overrides")
    if overrides is not None:
        if not isinstance(overrides, dict) or not overrides:
            return False
        for epic, routes in overrides.items():
            if not isinstance(epic, str) or not _EPIC.fullmatch(epic) or not isinstance(routes, dict) or not routes:
                return False
            if not set(routes) <= {"controller", "critic", "specialist", "streams"}:
                return False
            if any(not _route_value(routes[name]) for name in ("controller", "critic", "specialist") if name in routes):
                return False
            if "streams" in routes and not streams_valid(routes["streams"]):
                return False
    return True


def _value_valid(dotted, value):
    validators = {
        "repository.path": _text,
        "repository.origin": _text,
        "repository.id": lambda item: type(item) is int and item > 0,
        "repository.branch": _text,
        "repository.head": lambda item: isinstance(item, str) and _SHA40.fullmatch(item) is not None,
        "awf.version": _text,
        "awf.project_state": _text,
        "awf.trust_basis": _text,
        "adoption.pr": lambda item: type(item) is int and item > 0,
        "adoption.merge": lambda item: isinstance(item, str) and _SHA40.fullmatch(item) is not None,
        "operating.hash": lambda item: isinstance(item, str) and _SHA64.fullmatch(item) is not None,
        "operating.routes": _route_set,
        "jira.cloud_id": _text,
        "jira.provider_project_id": _text,
        "jira.project_key": _text,
        "jira.controller_actor_id": _text,
        "external_resources": lambda items: isinstance(items, list) and items == sorted(set(items)) and
        all(isinstance(item, str) and _RESOURCE_NAME.fullmatch(item) is not None for item in items),
        "completed_tickets": lambda items: isinstance(items, list) and items == sorted(set(items)) and
        all(isinstance(item, str) and _TICKET.fullmatch(item) is not None for item in items),
        "blockers": lambda items: isinstance(items, list) and all(
            isinstance(item, dict) and set(item) == {"code", "state", "evidence"} and
            all(_text(item[key]) for key in ("code", "state", "evidence")) for item in items),
    }
    return validators[dotted](value)


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
            continue
        if item["value"] is not None and not _value_valid(dotted, item["value"]):
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
    drift = [{"field": key, "received": received_fact[0], "received_basis": received_fact[1],
              "current": current_fact[0], "current_basis": current_fact[1]}
             for key in COMPARED
             if (received_fact := _comparison_fact(received, key)) !=
             (current_fact := _comparison_fact(current, key))]
    return {"status": "MATCH" if not drift else "DRIFT", "received_at": received.get("generated_at"),
            "observed_at": current["generated_at"], "drift": drift, "execution_authority": False}


def render_markdown(snapshot):
    lines = [f"# AWF handoff snapshot ({snapshot['generated_at']})", "",
             "Observations only; grants no execution, merge or Jira authority.", "",
             "| Field | Value | Basis | Observed at |", "| --- | --- | --- | --- |"]
    for dotted in REQUIRED_FIELDS:
        item = _field_at(snapshot, dotted)
        lines.append(f"| {dotted} | {item['value']} | {item['basis']} | {item['observed_at']} |")
    lines += ["", "Completed tickets are verified only from project-bound, hash-chained AWF coordinator state; "
              "without that source they are unavailable.", "", "## Blockers", ""]
    lines += [f"- {b['code']} ({b['state']}): {b['evidence']}" for b in snapshot["blockers"]["value"]] or ["- none"]
    return "\n".join(lines) + "\n"
