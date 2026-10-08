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
          "jira.project_key", "jira.controller_actor_id", "external_resources",
          "continuity.completed_tickets", "blockers")
CONTINUITY = "continuity.completed_tickets"
# Host-local facts (checkout path, current blockers) are recorded but not compared
# by value. Completed tickets are re-verified instead: each recorded merge commit
# must be reachable from the receiving checkout's HEAD.
COMPARED = tuple(key for key in FIELDS if key not in ("repository.path", "blockers", CONTINUITY))
_AUTHORITY = ("execution_authority", "merge_authority", "jira_authority")
# A received snapshot older than this (or carrying older observations) is STALE, never MATCH.
MAX_AGE_SECONDS = 86400
# Tolerated clock difference before a received snapshot counts as future-dated.
SKEW_SECONDS = 30
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)(.*)$", re.DOTALL)


def _field(value, basis, observed_at):
    if value is None:
        basis = "unavailable"
    return {"value": value, "basis": basis, "observed_at": observed_at}


def _run_git(root, *arguments):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=20, env=child_env(env))
    except (OSError, subprocess.SubprocessError):
        return None


def _git(root, *arguments):
    process = _run_git(root, *arguments)
    if process is None or process.returncode != 0:
        return None
    return process.stdout.strip() or None


def _reachable(root, commit):
    """True only when ``commit`` is in the history of the checkout's HEAD."""
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return False
    process = _run_git(root, "merge-base", "--is-ancestor", commit, "HEAD")
    return process is not None and process.returncode == 0


def safe_origin(url):
    """A remote URL without credentials: no user-info, query string or fragment.

    ``scheme://user:token@host/path?access_token=x#y`` becomes
    ``scheme://host/path``. An scp-like ``git@host:path`` keeps its login name,
    which is not a secret, but still loses any query string or fragment.
    """
    if url is None:
        return None
    match = _SCHEME.match(url)
    if match:
        scheme, rest = match.groups()
        authority, slash, path = rest.partition("/")
        # Strip user-info first: a password may itself contain '?' or '#'.
        authority = authority.rpartition("@")[2]
        url = scheme + authority + slash + path
    return re.split(r"[?#]", url, maxsplit=1)[0]


def _completed_tickets(root, records, repository_id):
    """Completed tickets proven by closeout records, else None when none are supplied.

    Each record must validate against its recorded commit, tree and blobs, belong
    to this repository, and name a merge commit reachable from HEAD. A supplied
    record that fails any check is refused rather than silently dropped.
    """
    if not records:
        return None
    from .closeout import validate_closeout
    from .contracts import Contracts
    contracts = Contracts(Path(__file__).resolve().parents[2] / "schemas")
    tickets = []
    for record in records:
        validate_closeout(record, root, contracts)
        binding = record["binding"]
        if repository_id is not None and binding["repository_id"] != repository_id:
            raise ValidationError(f"Closeout record {record['record_id']} is bound to repository "
                                  f"{binding['repository_id']}, not {repository_id}")
        if not _reachable(root, record["merge_commit_sha"]):
            raise ValidationError(f"Closeout record {record['record_id']} merge commit "
                                  f"{record['merge_commit_sha']} is not in the history of HEAD")
        entry = {"issue_id": binding["issue_id"], "pr": record["pr_number"], "merge_commit": record["merge_commit_sha"]}
        if entry not in tickets:
            tickets.append(entry)
    return sorted(tickets, key=lambda item: (item["issue_id"], item["pr"], item["merge_commit"]))


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


def build_snapshot(root, *, status=None, now=None, closeouts=None):
    """Observe the K15 facts. ``closeouts`` are loaded closeout records for completed tickets."""
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
        "continuity": {"completed_tickets": _field(_completed_tickets(root, closeouts, numeric_id["value"]),
                                                   "verified", now)},
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


def _completed(value):
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == {"issue_id", "pr", "merge_commit"} and _text(item["issue_id"])
        and _positive_integer(item["pr"]) and isinstance(item["merge_commit"], str)
        and re.fullmatch(r"[0-9a-f]{40}", item["merge_commit"]) is not None for item in value)


def _blockers(value):
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == {"code", "state", "evidence"}
        and all(entry is None or isinstance(entry, str) for entry in item.values()) for item in value)


# Value type of every K15 field when it is observed (a null value means unavailable).
VALUE_CHECKS = {
    "repository.path": _text,
    # A received origin must already be credential-free, so drift output never echoes a token.
    "repository.origin": lambda value: _text(value) and safe_origin(value) == value, "repository.numeric_id": _positive_integer,
    "repository.branch": _text, "repository.head": _object_id,
    "awf.version": lambda value: isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is not None,
    "awf.project_state": _text, "awf.integrity_valid": lambda value: type(value) is bool, "awf.trust_basis": _text,
    "adoption.state": _text, "adoption.pr": _positive_integer, "adoption.merge_commit": _object_id,
    "operating.hash": lambda value: isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
    "operating.routes": _routes,
    "jira.cloud_id": _text, "jira.provider_project_id": _text, "jira.project_key": _text,
    "jira.controller_actor_id": _text, "external_resources": _resources, CONTINUITY: _completed,
    "blockers": _blockers,
}
_SECTIONS = {section: {key.split(".")[1] for key in FIELDS if key.startswith(section + ".")}
             for section in dict.fromkeys(key.split(".")[0] for key in FIELDS if "." in key)}
_TOP_LEVEL = {"schema", "generated_at", *_AUTHORITY, *_SECTIONS, *(key for key in FIELDS if "." not in key)}


def _field_problem(item, check, generated_at=None):
    if not isinstance(item, dict) or set(item) != {"value", "basis", "observed_at"}:
        return "malformed"
    if item["basis"] not in BASES or (item["value"] is None) != (item["basis"] == "unavailable"):
        return "malformed"
    if not _timestamp(item["observed_at"]):
        return "invalid observed_at"
    if generated_at is not None and timestamp(item["observed_at"]) > generated_at:
        return "observed after generated_at"
    if item["value"] is not None and not check(item["value"]):
        return "invalid value"
    return None


def structural_problems(snapshot):
    """List required K15 fields that are missing, malformed or unexpected.

    A missing field is not an explicit ``unavailable`` one: only a present
    field with ``basis: unavailable`` and a null value records an unobservable
    fact.  Values must have the field's type and timestamps must be RFC 3339,
    so a malformed value can never compare equal to a current one.  No fact
    may be observed after the snapshot was generated.
    """
    problems = []
    generated_at = None
    if "generated_at" not in snapshot:
        problems.append({"field": "generated_at", "problem": "missing"})
    elif not _timestamp(snapshot["generated_at"]):
        problems.append({"field": "generated_at", "problem": "invalid timestamp"})
    else:
        generated_at = timestamp(snapshot["generated_at"])
    problems.extend({"field": key, "problem": "unexpected"} for key in sorted(set(snapshot) - _TOP_LEVEL))
    for section, names in sorted(_SECTIONS.items()):
        if isinstance(snapshot.get(section), dict):
            problems.extend({"field": section + "." + key, "problem": "unexpected"}
                            for key in sorted(set(snapshot[section]) - names))
    for key in FIELDS:
        item = _entry(snapshot, key)
        problem = "missing" if item is _MISSING else _field_problem(item, VALUE_CHECKS[key], generated_at)
        if problem:
            problems.append({"field": key, "problem": problem})
    return problems


def _same(left, right):
    # Exact JSON identity: 0 never equals False and 1.0 never equals 1.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _continuity_drift(received, current):
    """Received completed tickets whose merge commit this checkout's HEAD does not contain."""
    tickets = _lookup(received, CONTINUITY)
    if tickets is None:
        return []
    root = _lookup(current, "repository.path")
    missing = [item for item in tickets if root is None or not _reachable(root, item["merge_commit"])]
    if not missing:
        return []
    return [{"field": CONTINUITY, "received": missing, "current": None,
             "reason": "Completed-ticket merge commits are not in the history of this checkout's HEAD"}]


def compare_snapshot(received, current, *, max_age_seconds=MAX_AGE_SECONDS):
    """Compare a received snapshot with a freshly built one; never adopts received state.

    A future-dated snapshot is REJECTED. One generated, or carrying a fact
    observed, more than ``max_age_seconds`` before this host's observation is
    STALE, even when every value matches.
    """
    if not isinstance(received, dict) or received.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "Not an " + SCHEMA + " document", "execution_authority": False}
    if any(received.get(key) is not False for key in _AUTHORITY):
        return {"status": "REJECTED", "reason": "A handoff snapshot cannot carry authority", "execution_authority": False}
    problems = structural_problems(received)
    if problems:
        return {"status": "REJECTED", "reason": "Incomplete or malformed handoff snapshot; export it again with workflow.py handoff",
                "problems": problems, "execution_authority": False}
    now = timestamp(current["generated_at"])
    if (timestamp(received["generated_at"]) - now).total_seconds() > SKEW_SECONDS:
        return {"status": "REJECTED", "reason": "Handoff snapshot is dated after this host's observation",
                "problems": [{"field": "generated_at", "problem": "future-dated"}], "execution_authority": False}
    stale = [key for key in ("generated_at", *FIELDS)
             if (now - timestamp(received["generated_at"] if key == "generated_at"
                                 else _entry(received, key)["observed_at"])).total_seconds() > max_age_seconds]
    drift = [{"field": key, "received": _lookup(received, key), "current": _lookup(current, key)}
             for key in COMPARED if not _same(_lookup(received, key), _lookup(current, key))]
    drift += _continuity_drift(received, current)
    status = "STALE" if stale else "MATCH" if not drift else "DRIFT"
    return {"status": status, "received_at": received.get("generated_at"), "observed_at": current["generated_at"],
            "max_age_seconds": max_age_seconds, "stale": stale, "drift": drift, "execution_authority": False}


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
