"""Persistent no-idle native-stream scheduling and status reporting.

This reference component records decisions only.  Host dispatch and Jira
mutations remain controller-owned adapter operations with their existing
authority, budget, independence, capacity, and readback checks.
"""
from __future__ import annotations

import inspect
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import uuid

from . import ValidationError
from .child_process import child_env
from .canonical import canonical, loads, now_text, timestamp
from .child_process import child_env
from .controller_state import configure_database, protected_state_path, restrict_state_permissions


STREAM_STATES = {"WORKING", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
TICKET_STATES = {"ELIGIBLE", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
DEFAULT_STATUS_CADENCE_SECONDS = 10 * 60
STATE_APPLICATION_ID = 0x41574631

DDL = """
CREATE TABLE IF NOT EXISTS controller_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stream_status(
 stream_id TEXT PRIMARY KEY,state TEXT NOT NULL,ticket TEXT,actor TEXT NOT NULL,
 reason TEXT NOT NULL,next_action TEXT NOT NULL,resume_trigger TEXT NOT NULL,
 exact_tuple TEXT NOT NULL,activity TEXT NOT NULL,verification_gate TEXT NOT NULL,
 reviewer_completion TEXT NOT NULL,open_findings INTEGER NOT NULL,
 jira_status TEXT NOT NULL,paths TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controller_outbox(
 delivery_id TEXT PRIMARY KEY,kind TEXT NOT NULL,revision INTEGER NOT NULL,
 observed_at TEXT NOT NULL,payload_json TEXT NOT NULL,acknowledged_at TEXT);
CREATE TABLE IF NOT EXISTS controller_dispatch(
 dispatch_id TEXT PRIMARY KEY,stream_id TEXT NOT NULL,ticket TEXT NOT NULL,
 exact_tuple TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,
 receipt_json TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controller_jira_operation(
 operation_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL,event TEXT NOT NULL,
 intent_sha256 TEXT NOT NULL,status TEXT NOT NULL,record_json TEXT NOT NULL,
 operation_json TEXT,readback_json TEXT,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS controller_jira_issue ON controller_jira_operation(issue_id,status);
"""


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _reviewer_counts(counts):
    required = {"required", "completed", "acceptable", "failed", "stale", "outstanding"}
    _require(isinstance(counts, dict) and set(counts) == required and
             all(type(number) is int and number >= 0 for number in counts.values()),
             "Reviewer completion requires all six nonnegative counts")
    _require(counts["completed"] + counts["outstanding"] == counts["required"] and
             counts["acceptable"] + counts["failed"] + counts["stale"] == counts["completed"],
             "Reviewer completion counts are contradictory")
    return dict(counts)


def _ticket(value):
    required = {"ticket", "priority", "disposition", "actor", "reason", "next_action", "resume_trigger",
                "paths", "dependencies_satisfied", "budget_available", "cap_available", "review_independent",
                "exact_tuple", "activity", "verification_gate", "reviewer_completion", "open_findings", "jira_status"}
    _require(isinstance(value, dict) and set(value) == required, "Malformed continuous-controller ticket")
    _require(isinstance(value["ticket"], str) and value["ticket"].strip(), "Ticket identity is required")
    _require(type(value["priority"]) is int and value["priority"] >= 0, "Ticket priority must be nonnegative")
    _require(value["disposition"] in TICKET_STATES, "Unknown ticket scheduling disposition")
    _require(isinstance(value["paths"], list), "Ticket paths must be a list")
    value = dict(value)
    value["paths"] = [_owned_path(path) for path in value["paths"]]
    _require(len(value["paths"]) == len(set(value["paths"])), "Ticket paths must be unique canonical paths")
    for key in ("dependencies_satisfied", "budget_available", "cap_available", "review_independent"):
        _require(type(value[key]) is bool, f"{key} must be observed boolean")
    for key in ("actor", "reason", "next_action", "resume_trigger", "exact_tuple", "activity",
                "verification_gate", "jira_status"):
        _require(isinstance(value[key], str) and value[key].strip(), f"{key} is required")
    value["reviewer_completion"] = _reviewer_counts(value["reviewer_completion"])
    _require(type(value["open_findings"]) is int and value["open_findings"] >= 0,
             "Open finding count must be nonnegative")
    return value


def _owned_path(path):
    _require(isinstance(path, str) and path and not any(ord(char) < 32 for char in path),
             "Ticket path must be a nonempty printable repository-relative path")
    value = path.replace("\\", "/").rstrip("/")
    wildcard = ""
    for suffix in ("/**", "/*"):
        if value.endswith(suffix):
            value, wildcard = value[:-len(suffix)].rstrip("/"), suffix
            break
    _require(value and not value.startswith("/") and not value.startswith("//") and
             re.match(r"^[A-Za-z]:", value) is None,
             "Ticket path must stay repository-relative; external/absolute paths are not ownership claims")
    parts = value.split("/")
    _require(all(part not in {"", ".", ".."} for part in parts),
             "Ticket path cannot contain empty, dot, or parent traversal segments")
    return "/".join(parts) + wildcard


def _overlap(first, second):
    def surface(path):
        value = _owned_path(path).casefold()
        for suffix in ("/**", "/*"):
            if value.endswith(suffix):
                value = value[:-len(suffix)].rstrip("/")
        return value
    left, right = [surface(path) for path in first], [surface(path) for path in second]
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in left for b in right)


def _reparse(path):
    """Recognise every link-like filesystem surface available to this host."""
    try:
        metadata = Path(path).lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag) or
            bool(getattr(Path(path), "is_junction", lambda: False)()))


def _git(root, *args):
    """Run bounded, read-only Git plumbing without shell or lazy network fetch."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("GIT_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                       GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1",
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_LAZY_FETCH="1")
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=" + os.devnull, "-c", "core.quotePath=false",
             "-c", "protocol.file.allow=never", "-C", str(root), *args],
            capture_output=True, timeout=30, check=False, env=child_env(environment))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"Git path inventory failed: {type(exc).__name__}") from exc
    _require(result.returncode == 0, "Git path inventory could not be observed")
    return result.stdout


def canonical_repository_paths(tickets, repository_root, head_sha, tree_sha):
    """Bind ownership paths to one exact worktree, Git tuple and physical surface.

    Git spelling is authoritative. Directory claims expand through the pinned
    tracked inventory. Physical aliases share a canonical ownership key so that
    only one writer can be admitted for the same filesystem surface.
    """
    root_input = Path(repository_root)
    _require(root_input.is_absolute() and root_input.is_dir(),
             "Path ownership needs an absolute existing repository worktree")
    for parent in (root_input, *root_input.parents):
        if parent.exists() and _reparse(parent):
            raise ValidationError("Repository worktree traverses a link, junction, or reparse point")
    root = root_input.resolve(strict=True)
    observed_root = Path(_git(root, "rev-parse", "--show-toplevel").decode("utf-8", "strict").strip()).resolve(strict=True)
    _require(observed_root == root, "Pinned repository root differs from the Git worktree")
    _require(isinstance(head_sha, str) and re.fullmatch(r"[0-9a-f]{40}", head_sha) and
             isinstance(tree_sha, str) and re.fullmatch(r"[0-9a-f]{40}", tree_sha),
             "Repository ownership needs lowercase full head and tree pins")
    observed_head = _git(root, "rev-parse", "HEAD").decode("ascii", "strict").strip()
    observed_tree = _git(root, "rev-parse", "HEAD^{tree}").decode("ascii", "strict").strip()
    _require((observed_head, observed_tree) == (head_sha, tree_sha),
             "Repository ownership tuple differs from the pinned head/tree")

    try:
        records = _git(root, "ls-files", "-s", "-z").decode("utf-8", "strict").split("\0")
    except UnicodeDecodeError as exc:
        raise ValidationError("Git path inventory contains a non-UTF-8 path") from exc
    modes, canonical_names, directory_names = {}, {}, {}
    for record in records:
        if not record:
            continue
        metadata, name = record.split("\t", 1)
        mode, _object_id, stage = metadata.split(" ")
        _require(stage == "0" and _owned_path(name) == name,
                 "Git path inventory contains an unsafe or unmerged path")
        modes[name] = mode
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            bucket = canonical_names.setdefault(prefix.casefold(), set())
            bucket.add(prefix)
            if index < len(parts):
                directory_names.setdefault(prefix.casefold(), set()).add(prefix)
    _require(all(len(names) == 1 for names in canonical_names.values()),
             "Git path inventory has case-folded alias collisions")
    names = {key: next(iter(values)) for key, values in canonical_names.items()}
    directories = {key: next(iter(values)) for key, values in directory_names.items()}

    def tracked_identity(name):
        """Return a stable physical identity for one tracked regular file."""
        mode = modes[name]
        _require(mode not in {"120000", "160000"},
                 "Owned path cannot contain a Git symlink or nested repository")
        candidate = root
        for component in name.split("/"):
            candidate = candidate / component
            if _reparse(candidate):
                raise ValidationError("Owned path traverses a symlink, junction, or reparse point")
        _require(candidate.exists() and candidate.is_file(),
                 "Tracked ownership surface is missing or not a regular file")
        resolved = candidate.resolve(strict=True)
        _require(resolved.is_relative_to(root),
                 "Owned path resolves outside the pinned repository")
        metadata = candidate.stat()
        device, inode = getattr(metadata, "st_dev", None), getattr(metadata, "st_ino", None)
        _require(type(device) is int and type(inode) is int and inode != 0,
                 "Tracked ownership surface has no reliable physical identity")
        return device, inode

    def canonical_path(raw):
        owned = _owned_path(raw)
        wildcard = next((suffix for suffix in ("/**", "/*") if owned.endswith(suffix)), "")
        surface = owned[:-len(wildcard)] if wildcard else owned
        current = root
        for component in surface.split("/"):
            current = current / component
            if current.exists() and _reparse(current):
                raise ValidationError("Owned path traverses a symlink, junction, or reparse point")
        key = surface.casefold()
        if key in names:
            canonical_surface = names[key]
        else:
            parts = surface.split("/")
            canonical_parts, matched_directory = [], False
            for index in range(len(parts), 0, -1):
                prefix_key = "/".join(parts[:index]).casefold()
                if prefix_key in directories:
                    canonical_parts = directories[prefix_key].split("/") + parts[index:]
                    matched_directory = True
                    break
            if not matched_directory:
                _require(len(parts) == 1,
                         "Owned path is absent from the pinned Git inventory and has no canonical parent")
                canonical_parts = parts
            canonical_surface = "/".join(canonical_parts)
        _require(not wildcard or canonical_surface.casefold() in directories,
                 "Wildcard ownership must name a canonical Git directory")
        candidate = root.joinpath(*canonical_surface.split("/"))
        resolved = candidate.resolve(strict=False)
        _require(resolved == root or resolved.is_relative_to(root),
                 "Owned path resolves outside the pinned repository")
        mode = modes.get(canonical_surface)
        _require(mode not in {"120000", "160000"},
                 "Owned path cannot be a Git symlink or nested repository")
        if mode is not None:
            covered = [canonical_surface]
        elif canonical_surface.casefold() in directories:
            prefix = canonical_surface + "/"
            covered = [name for name in modes if name.startswith(prefix)]
        else:
            covered = []
        _require(not wildcard or covered,
                 "Wildcard ownership has no tracked descendants")
        return canonical_surface + wildcard, covered

    prepared, covered_names = [], set()
    for item in tickets:
        admitted = _ticket(item)
        canonical_claims, covered = [], []
        for path in admitted["paths"]:
            claim, descendants = canonical_path(path)
            canonical_claims.append(claim)
            covered.extend(descendants)
        _require(len(canonical_claims) == len(set(canonical_claims)),
                 "Ticket paths collapse to one canonical repository surface")
        covered_names.update(covered)
        prepared.append((admitted, canonical_claims, covered))

    physical_names = {}
    for name in sorted(covered_names, key=lambda value: (value.casefold(), value)):
        identity = tracked_identity(name)
        physical_names.setdefault(identity, []).append(name)
    physical_representative = {
        name: members[0]
        for members in physical_names.values()
        for name in members
    }

    result = []
    for admitted, canonical_claims, covered in prepared:
        # Include every covered descendant's physical representative. Claims
        # that reach hardlinked names therefore share an exact ownership key
        # and cannot both be scheduled as writers.
        aliases = [physical_representative[name] for name in covered]
        admitted["paths"] = list(dict.fromkeys(canonical_claims + aliases))
        result.append(admitted)
    binding = {"repository_root": str(root), "head_sha": head_sha, "tree_sha": tree_sha,
               "git_path_count": len(modes)}
    return result, binding


class ContinuousControllerStore:
    """Durable scheduler in which every configured stream has a visible state."""

    def __init__(self, path, stream_ids, cadence_seconds=DEFAULT_STATUS_CADENCE_SECONDS,
                 worktree_roots=(), periodic_status_enabled=True, migrate_cadence=False):
        self.path = protected_state_path(path, worktree_roots)
        _require(isinstance(stream_ids, (list, tuple)) and stream_ids and
                 len(stream_ids) == len(set(stream_ids)) and
                 all(isinstance(item, str) and item.strip() for item in stream_ids),
                 "Configured stream identities must be unique nonempty strings")
        _require(type(cadence_seconds) is int and cadence_seconds > 0, "Status cadence must be positive seconds")
        _require(type(periodic_status_enabled) is bool, "Periodic status enablement must be boolean")
        _require(type(migrate_cadence) is bool, "Cadence migration choice must be boolean")
        self.periodic_status_enabled = periodic_status_enabled
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(DDL)
            db.execute("BEGIN IMMEDIATE")
            try:
                configured = canonical(sorted(stream_ids)).decode()
                old = db.execute("SELECT value FROM controller_meta WHERE key='streams'").fetchone()
                _require(old is None or old[0] == configured, "Configured stream set changed; perform explicit controller migration")
                for key, value in (("streams", configured), ("cadence_seconds", str(cadence_seconds)),
                                   ("revision", "0"), ("last_regular_digest", ""),
                                   ("last_digest_revision", "-1")):
                    db.execute("INSERT OR IGNORE INTO controller_meta VALUES(?,?)", (key, value))
                stored_cadence = int(self._meta(db, "cadence_seconds"))
                if stored_cadence != cadence_seconds and migrate_cadence:
                    db.execute("UPDATE controller_meta SET value=? WHERE key='cadence_seconds'",
                               (str(cadence_seconds),))
                else:
                    _require(stored_cadence == cadence_seconds,
                             "Configured status cadence changed; explicitly migrate the stored cadence")
                for stream in sorted(stream_ids):
                    db.execute("INSERT OR IGNORE INTO stream_status VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (stream, "COMPLETE", None, "controller", "No scoped work has been scheduled",
                                "Observe the scoped backlog", "an eligible scoped ticket appears", "UNOBSERVED",
                                "No active action", "UNOBSERVED", canonical(_zero_counts()).decode(), 0,
                                "UNOBSERVED", "[]", now_text()))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        restrict_state_permissions(self.path)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            configure_database(db, self.path, STATE_APPLICATION_ID)
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _meta(db, key):
        row = db.execute("SELECT value FROM controller_meta WHERE key=?", (key,)).fetchone()
        _require(row is not None, f"Missing controller metadata {key}")
        return row[0]

    def set_cadence(self, seconds):
        _require(type(seconds) is int and seconds > 0, "Status cadence must be positive seconds")
        with self.transaction() as db:
            db.execute("UPDATE controller_meta SET value=? WHERE key='cadence_seconds'", (str(seconds),))

    def bind_repository(self, binding):
        """Pin one canonical repository/worktree identity for all scheduling."""
        encoded = canonical(binding).decode()
        with self.transaction() as db:
            prior = db.execute("SELECT value FROM controller_meta WHERE key='repository_binding'").fetchone()
            _require(prior is None or prior[0] == encoded,
                     "Controller repository/worktree binding changed; use an explicit state migration")
            db.execute("INSERT OR IGNORE INTO controller_meta VALUES('repository_binding',?)", (encoded,))

    @staticmethod
    def _row_value(stream, state, ticket, now):
        return (ticket.get("ticket") if ticket else None,
                ticket.get("actor", "controller") if ticket else "controller",
                ticket.get("reason", "Scoped backlog complete") if ticket else "Scoped backlog complete",
                ticket.get("next_action", "Observe for newly scoped work") if ticket else "Observe for newly scoped work",
                ticket.get("resume_trigger", "new scoped work") if ticket else "new scoped work",
                ticket.get("exact_tuple", "UNOBSERVED") if ticket else "UNOBSERVED",
                ticket.get("activity", "No remaining scoped action") if ticket else "No remaining scoped action",
                ticket.get("verification_gate", "NOT_APPLICABLE") if ticket else "NOT_APPLICABLE",
                canonical(ticket.get("reviewer_completion", _zero_counts()) if ticket else _zero_counts()).decode(),
                ticket.get("open_findings", 0) if ticket else 0,
                ticket.get("jira_status", "UNOBSERVED") if ticket else "UNOBSERVED",
                canonical(ticket.get("paths", []) if ticket else []).decode(), now)

    def _update_stream(self, db, stream, state, ticket, now):
        _require(state in STREAM_STATES, "A configured stream cannot enter an unlabelled idle state")
        current = db.execute("SELECT * FROM stream_status WHERE stream_id=?", (stream,)).fetchone()
        values = self._row_value(stream, state, ticket, now)
        comparable = (state, *values[:-1])
        before = (current["state"], current["ticket"], current["actor"], current["reason"], current["next_action"],
                  current["resume_trigger"], current["exact_tuple"], current["activity"], current["verification_gate"],
                  current["reviewer_completion"], current["open_findings"], current["jira_status"], current["paths"])
        if comparable != before:
            db.execute("UPDATE stream_status SET state=?,ticket=?,actor=?,reason=?,next_action=?,resume_trigger=?,exact_tuple=?,activity=?,verification_gate=?,reviewer_completion=?,open_findings=?,jira_status=?,paths=?,updated_at=? WHERE stream_id=?",
                       (state, *values, stream))
            revision = int(self._meta(db, "revision")) + 1
            db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))

    @staticmethod
    def _stream_restore_snapshot(state, ticket):
        """Retain the scheduling row hidden by an UNKNOWN-dispatch blocker."""
        return {
            "state": state, "ticket": ticket["ticket"], "actor": ticket["actor"],
            "reason": ticket["reason"], "next_action": ticket["next_action"],
            "resume_trigger": ticket["resume_trigger"], "exact_tuple": ticket["exact_tuple"],
            "activity": ticket["activity"], "verification_gate": ticket["verification_gate"],
            "reviewer_completion": ticket["reviewer_completion"],
            "open_findings": ticket["open_findings"], "jira_status": ticket["jira_status"],
            "paths": ticket["paths"],
        }

    @staticmethod
    def _row_restore_snapshot(row):
        return {
            "state": row["state"], "ticket": row["ticket"], "actor": row["actor"],
            "reason": row["reason"], "next_action": row["next_action"],
            "resume_trigger": row["resume_trigger"], "exact_tuple": row["exact_tuple"],
            "activity": row["activity"], "verification_gate": row["verification_gate"],
            "reviewer_completion": loads(row["reviewer_completion"]),
            "open_findings": row["open_findings"], "jira_status": row["jira_status"],
            "paths": loads(row["paths"]),
        }

    @staticmethod
    def _remember_unknown_stream(db, dispatch_id, snapshot, *, replace):
        operation = db.execute(
            "SELECT payload_json FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
        _require(operation is not None, "Unknown dispatch is missing from the protected state")
        payload = loads(operation["payload_json"])
        if replace or "_stream_before_unknown" not in payload:
            payload["_stream_before_unknown"] = snapshot
            db.execute("UPDATE controller_dispatch SET payload_json=? WHERE dispatch_id=?",
                       (canonical(payload).decode(), dispatch_id))

    @staticmethod
    def _restore_unknown_stream(db, dispatch_id, payload, now):
        stream = db.execute("SELECT * FROM stream_status WHERE stream_id=?",
                            (payload["stream"],)).fetchone()
        unknown_reason = (f"Dispatch outcome unknown for {payload['ticket']} ({dispatch_id}); "
                          "reconcile before retry")
        if (stream is None or stream["ticket"] != payload["ticket"] or
                stream["exact_tuple"] != payload["exact_tuple"] or
                stream["state"] != "BLOCKED" or stream["reason"] != unknown_reason):
            return
        restore = payload.get("_stream_before_unknown")
        _require(isinstance(restore, dict) and set(restore) == {
            "state", "ticket", "actor", "reason", "next_action", "resume_trigger",
            "exact_tuple", "activity", "verification_gate", "reviewer_completion",
            "open_findings", "jira_status", "paths"},
            "UNKNOWN dispatch is missing its matching stream restoration state")
        _require(restore["state"] in STREAM_STATES and restore["state"] != "COMPLETE" and
                 restore["ticket"] == payload["ticket"] and
                 restore["exact_tuple"] == payload["exact_tuple"],
                 "UNKNOWN dispatch stream restoration state is mismatched")
        db.execute(
            "UPDATE stream_status SET state=?,ticket=?,actor=?,reason=?,next_action=?,"
            "resume_trigger=?,exact_tuple=?,activity=?,verification_gate=?,reviewer_completion=?,"
            "open_findings=?,jira_status=?,paths=?,updated_at=? WHERE stream_id=?",
            (restore["state"], restore["ticket"], restore["actor"], restore["reason"],
             restore["next_action"], restore["resume_trigger"], restore["exact_tuple"],
             restore["activity"], restore["verification_gate"],
             canonical(_reviewer_counts(restore["reviewer_completion"])).decode(),
             restore["open_findings"], restore["jira_status"],
             canonical(restore["paths"]).decode(), now, payload["stream"]))

    def schedule(self, inventory, now, host_capacity):
        """Fill every free stream or record its concrete pause/block/complete state."""
        timestamp(now)
        _require(type(host_capacity) is int and host_capacity >= 0, "Observed host capacity must be nonnegative")
        tickets = [_ticket(item) for item in inventory]
        _require(len(tickets) == len({item["ticket"] for item in tickets}), "Scoped inventory has duplicate tickets")
        with self.transaction() as db:
            self._schedule(db, tickets, now, host_capacity)
            return self._snapshot(db)

    def _schedule(self, db, tickets, now, host_capacity):
        rows = db.execute("SELECT * FROM stream_status ORDER BY stream_id").fetchall()
        inventory = {item["ticket"]: item for item in tickets}
        # A durable dispatch intent reserves its ticket globally until the host
        # outcome is observed. Otherwise a priority change can move the same
        # ticket to another stream and create a second dispatch while the first
        # may still be running.
        reserved_dispatches, capacity_intents, unknown_dispatches = {}, [], {}
        for intent in db.execute(
                "SELECT dispatch_id,ticket,stream_id,exact_tuple,status FROM controller_dispatch "
                "WHERE status IN ('PENDING','IN_FLIGHT','UNKNOWN')"):
            reserved_dispatches.setdefault(intent["ticket"], set()).add(intent["stream_id"])
            if intent["status"] in {"IN_FLIGHT", "UNKNOWN"}:
                capacity_intents.append((intent["stream_id"], intent["ticket"]))
            if intent["status"] == "UNKNOWN":
                unknown_dispatches.setdefault(intent["stream_id"], []).append(intent)
        reserved_tickets = set(reserved_dispatches)
        working, held_paths = [], []
        for row in rows:
            item = inventory.get(row["ticket"]) if row["state"] == "WORKING" else None
            prerequisites = item is not None and item["disposition"] == "ELIGIBLE" and all((
                item["dependencies_satisfied"], item["budget_available"], item["cap_available"],
                item["review_independent"]))
            intent_streams = reserved_dispatches.get(item["ticket"], set()) if item else set()
            intent_matches_stream = not intent_streams or intent_streams == {row["stream_id"]}
            if (prerequisites and intent_matches_stream and len(working) < host_capacity and
                    not any(_overlap(item["paths"], paths) for paths in held_paths)):
                working.append(row)
                held_paths.append(item["paths"])
                self._update_stream(db, row["stream_id"], "WORKING", item, now)
        assigned = {row["ticket"] for row in working}
        represented_intents = {(row["stream_id"], row["ticket"]) for row in working}
        represented_capacity = len({intent for intent in capacity_intents
                                    if intent in represented_intents})
        unresolved_capacity = max(0, len(capacity_intents) - represented_capacity)
        slots = max(0, host_capacity - len(working) - unresolved_capacity)
        eligible = sorted((item for item in tickets if item["ticket"] not in assigned and
                           item["ticket"] not in reserved_tickets and
                           item["disposition"] == "ELIGIBLE"), key=lambda item: (item["priority"], item["ticket"]))
        paused = sorted((item for item in tickets if item["ticket"] not in assigned and
                         item["ticket"] not in reserved_tickets and
                         item["disposition"] == "PAUSED_INPUT"), key=lambda item: (item["priority"], item["ticket"]))
        blocked = sorted((item for item in tickets if item["ticket"] not in assigned and
                          item["ticket"] not in reserved_tickets and
                          item["disposition"] == "BLOCKED"), key=lambda item: (item["priority"], item["ticket"]))
        free_rows = [row for row in rows if row not in working]
        for row in free_rows:
            matching_unknown = next((intent for intent in unknown_dispatches.get(row["stream_id"], [])
                                     if row["ticket"] == intent["ticket"] and
                                     row["exact_tuple"] == intent["exact_tuple"] and
                                     intent["ticket"] in inventory and
                                     inventory[intent["ticket"]]["disposition"] != "COMPLETE"), None)
            if matching_unknown is not None:
                item = inventory[matching_unknown["ticket"]]
                underlying_state = "WORKING" if item["disposition"] == "ELIGIBLE" else item["disposition"]
                self._remember_unknown_stream(
                    db, matching_unknown["dispatch_id"],
                    self._stream_restore_snapshot(underlying_state, item), replace=True)
                blocked_item = {
                    **item,
                    "exact_tuple": matching_unknown["exact_tuple"],
                    "reason": (f"Dispatch outcome unknown for {matching_unknown['ticket']} "
                               f"({matching_unknown['dispatch_id']}); reconcile before retry"),
                    "next_action": "Observe the exact durable dispatch intent before dispatching more work on this stream",
                    "resume_trigger": "the host returns a fresh observation for this dispatch and its ticket",
                }
                self._update_stream(db, row["stream_id"], "BLOCKED", blocked_item, now)
                continue
            selected = None
            for item in eligible:
                prerequisites = (item["dependencies_satisfied"] and item["budget_available"] and
                                 item["cap_available"] and item["review_independent"])
                if prerequisites and slots > 0 and not any(_overlap(item["paths"], paths) for paths in held_paths):
                    selected = item
                    break
            if selected is not None:
                eligible.remove(selected)
                assigned.add(selected["ticket"])
                held_paths.append(selected["paths"])
                slots -= 1
                self._update_stream(db, row["stream_id"], "WORKING", selected, now)
                continue
            if eligible:
                item = eligible.pop(0)
                reason = []
                if not item["dependencies_satisfied"]: reason.append("dependencies")
                if not item["budget_available"]: reason.append("budget")
                if not item["cap_available"]: reason.append("run or amendment cap")
                if not item["review_independent"]: reason.append("review independence")
                if slots <= 0: reason.append("host capacity")
                if any(_overlap(item["paths"], paths) for paths in held_paths): reason.append("path ownership")
                blocked_item = {**item, "reason": "Blocked by " + ", ".join(reason),
                                "next_action": "Re-evaluate eligibility without weakening governance",
                                "resume_trigger": "all named prerequisites become current"}
                self._update_stream(db, row["stream_id"], "BLOCKED", blocked_item, now)
            elif paused:
                self._update_stream(db, row["stream_id"], "PAUSED_INPUT", paused.pop(0), now)
            elif blocked:
                self._update_stream(db, row["stream_id"], "BLOCKED", blocked.pop(0), now)
            else:
                self._update_stream(db, row["stream_id"], "COMPLETE", None, now)

    def finish_and_refill(self, stream_id, ticket, inventory, now, host_capacity):
        """Complete one run and refill its stream within the same transaction."""
        timestamp(now)
        tickets = [_ticket(item) for item in inventory]
        repeated = next((item for item in tickets if item["ticket"] == ticket), None)
        _require(repeated is None or repeated["disposition"] == "COMPLETE",
                 "Completed ticket remains dispatch-eligible; record its terminal lifecycle state before refill")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM stream_status WHERE stream_id=?", (stream_id,)).fetchone()
            _require(row is not None and row["state"] == "WORKING" and row["ticket"] == ticket,
                     "Completed run does not match the stream's active ticket")
            self._update_stream(db, stream_id, "COMPLETE", None, now)
            self._schedule(db, tickets, now, host_capacity)
            return self._snapshot(db)

    def _snapshot(self, db):
        result = []
        for row in db.execute("SELECT * FROM stream_status ORDER BY stream_id"):
            result.append({"stream": row["stream_id"], "state": row["state"], "ticket": row["ticket"],
                           "actor": row["actor"], "reason": row["reason"], "next_action": row["next_action"],
                           "resume_trigger": row["resume_trigger"], "exact_tuple": row["exact_tuple"],
                           "activity": row["activity"], "verification_gate": row["verification_gate"],
                           "reviewer_completion": loads(row["reviewer_completion"]),
                           "open_findings": row["open_findings"], "jira_status": row["jira_status"],
                           "updated_at": row["updated_at"]})
        _require(all(item["state"] in STREAM_STATES for item in result), "Unlabelled stream idle state detected")
        return result

    def snapshot(self):
        with self.connection() as db:
            return self._snapshot(db)

    def digest(self, now):
        """Create/replay a durable pending digest; delivery is separate and acknowledged."""
        current = timestamp(now)
        with self.transaction() as db:
            pending = db.execute("SELECT payload_json FROM controller_outbox WHERE acknowledged_at IS NULL ORDER BY rowid LIMIT 1").fetchone()
            if pending is not None:
                return loads(pending["payload_json"])
            revision = int(self._meta(db, "revision"))
            last_revision = int(self._meta(db, "last_digest_revision"))
            last_regular = self._meta(db, "last_regular_digest")
            cadence = int(self._meta(db, "cadence_seconds"))
            elapsed = None if not last_regular else (current - timestamp(last_regular)).total_seconds()
            _require(elapsed is None or elapsed >= 0, "Host clock moved backwards; reconcile cadence before delivery")
            regular_due = self.periodic_status_enabled and (not last_regular or elapsed >= cadence)
            change_due = revision > last_revision and (self.periodic_status_enabled or revision > 0)
            streams = self._snapshot(db)
            unresolved_dispatches = []
            for row in db.execute(
                    "SELECT dispatch_id,stream_id,ticket,exact_tuple,status,updated_at "
                    "FROM controller_dispatch WHERE status='UNKNOWN' ORDER BY stream_id,dispatch_id"):
                owner = db.execute("SELECT ticket,exact_tuple FROM stream_status WHERE stream_id=?",
                                   (row["stream_id"],)).fetchone()
                detached = (owner is None or owner["ticket"] != row["ticket"] or
                            owner["exact_tuple"] != row["exact_tuple"])
                unresolved_dispatches.append({
                    "dispatch_id": row["dispatch_id"], "stream": row["stream_id"],
                    "ticket": row["ticket"], "exact_tuple": row["exact_tuple"],
                    "status": row["status"], "detached": detached,
                    "updated_at": row["updated_at"]})
            all_complete = all(s["state"] == "COMPLETE" for s in streams)
            if all_complete and not change_due:
                return None
            if not regular_due and not change_due:
                return None
            kind = "REGULAR" if regular_due else "CHANGE"
            from .canonical import fingerprint
            body = {"schema_version": 3, "observed_at": now, "kind": kind,
                    "cadence_seconds": cadence, "all_complete": all_complete, "streams": streams,
                    "unresolved_dispatches": unresolved_dispatches}
            delivery_id = fingerprint("controller-status-digest", {"revision": revision, **body})
            body["delivery_id"] = delivery_id
            db.execute("INSERT INTO controller_outbox VALUES(?,?,?,?,?,NULL)",
                       (delivery_id, kind, revision, now, canonical(body).decode()))
            return body

    def acknowledge_digest(self, delivery_id, delivered_at):
        """Acknowledge successful external delivery; unknown delivery is never inferred."""
        timestamp(delivered_at)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_outbox WHERE delivery_id=?", (delivery_id,)).fetchone()
            _require(row is not None, "Unknown controller digest delivery")
            if row["acknowledged_at"] is not None:
                return loads(row["payload_json"])
            _require(timestamp(delivered_at) >= timestamp(row["observed_at"]),
                     "Digest acknowledgement predates its observation")
            db.execute("UPDATE controller_outbox SET acknowledged_at=? WHERE delivery_id=?",
                       (delivered_at, delivery_id))
            prior_revision = int(self._meta(db, "last_digest_revision"))
            db.execute("UPDATE controller_meta SET value=? WHERE key='last_digest_revision'",
                       (str(max(prior_revision, row["revision"])),))
            if row["kind"] == "REGULAR":
                db.execute("UPDATE controller_meta SET value=? WHERE key='last_regular_digest'",
                           (row["observed_at"],))
            return loads(row["payload_json"])

    def prepare_dispatches(self, now):
        """Persist one nonce-bound dispatch intent for newly scheduled work."""
        timestamp(now)
        from .canonical import fingerprint
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM stream_status WHERE state='WORKING' ORDER BY stream_id"):
                prior = db.execute(
                    "SELECT * FROM controller_dispatch WHERE stream_id=? AND ticket=? AND exact_tuple=? "
                    "AND status<>'CANCELLED' ORDER BY rowid DESC LIMIT 1",
                    (row["stream_id"], row["ticket"], row["exact_tuple"])).fetchone()
                if prior is not None:
                    continue
                payload = {"stream": row["stream_id"], "ticket": row["ticket"],
                           "exact_tuple": row["exact_tuple"], "paths": loads(row["paths"]),
                           "actor": row["actor"], "next_action": row["next_action"],
                           "dispatch_nonce": str(uuid.uuid4()), "prepared_at": now}
                dispatch_id = fingerprint("controller-dispatch", payload)
                payload["dispatch_id"] = dispatch_id
                db.execute("INSERT OR IGNORE INTO controller_dispatch VALUES(?,?,?,?,? ,?,NULL,?)",
                           (dispatch_id, row["stream_id"], row["ticket"], row["exact_tuple"],
                            "PENDING", canonical(payload).decode(), now))
            return [dict(row) | {"payload": loads(row["payload_json"])} for row in db.execute(
                "SELECT * FROM controller_dispatch WHERE status IN ('PENDING','UNKNOWN') ORDER BY stream_id,dispatch_id")]

    def pending_dispatches(self):
        """Read never-begun intents so fresh preflight can cancel them safely."""
        with self.connection() as db:
            return [dict(row) | {"payload": loads(row["payload_json"])} for row in db.execute(
                "SELECT * FROM controller_dispatch WHERE status='PENDING' ORDER BY stream_id,dispatch_id")]

    def cancel_pending_dispatch(self, dispatch_id, now, reason):
        """Durably retire an intent proven not to have reached the host."""
        timestamp(now)
        _require(isinstance(reason, str) and reason.strip(),
                 "Pending dispatch cancellation requires a preflight reason")
        with self.transaction() as db:
            row = db.execute("SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?",
                             (dispatch_id,)).fetchone()
            _require(row is not None and row["status"] == "PENDING",
                     "Only a never-begun pending dispatch can be cancelled")
            payload = loads(row["payload_json"])
            _require("begun_at" not in payload,
                     "A dispatch with a host-call begin time cannot be cancelled")
            payload["cancelled_at"] = now
            payload["cancellation_reason"] = reason.strip()
            db.execute("UPDATE controller_dispatch SET status='CANCELLED',payload_json=?,updated_at=? "
                       "WHERE dispatch_id=? AND status='PENDING'",
                       (canonical(payload).decode(), now, dispatch_id))
            revision = int(self._meta(db, "revision")) + 1
            db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
            return dict(row) | {"status": "CANCELLED", "payload": payload}

    def begin_dispatch(self, dispatch_id, now):
        """Move one intent to IN_FLIGHT before the external host call."""
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            _require(row is not None and row["status"] == "PENDING", "Dispatch is not pending")
            payload = loads(row["payload_json"])
            _require("begun_at" not in payload, "Pending dispatch already has a begin time")
            payload["begun_at"] = now
            db.execute("UPDATE controller_dispatch SET status='IN_FLIGHT',payload_json=?,updated_at=? WHERE dispatch_id=?",
                       (canonical(payload).decode(), now, dispatch_id))
            return payload

    def finish_dispatch(self, dispatch_id, receipt, now, *, reconcile=False):
        """Accept only exact host readback; uncertain calls are never reissued."""
        timestamp(now)
        bound = {"dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce",
                 "prepared_at", "begun_at"}
        if reconcile:
            bound |= {"reconcile_nonce", "reconcile_at"}
        required = bound | {"status", "observed_at"}
        _require(isinstance(receipt, dict) and set(receipt) == required and
                 receipt["status"] == "ACCEPTED", "Dispatch receipt is not an exact accepted readback")
        observed_at = timestamp(receipt["observed_at"])
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            expected = "UNKNOWN" if reconcile else "IN_FLIGHT"
            _require(row is not None and row["status"] == expected,
                     "Dispatch receipt does not match the durable operation state")
            payload = loads(row["payload_json"])
            _require(all(receipt[key] == payload[key] for key in bound),
                     "Dispatch receipt differs from the durable intent")
            boundary = timestamp(payload["reconcile_at"] if reconcile else payload["begun_at"])
            _require(observed_at >= boundary,
                     "Dispatch receipt is stale relative to the durable intent or reconciliation")
            db.execute("UPDATE controller_dispatch SET status='ACCEPTED',receipt_json=?,updated_at=? WHERE dispatch_id=?",
                       (canonical(receipt).decode(), now, dispatch_id))
            if reconcile:
                self._restore_unknown_stream(db, dispatch_id, payload, now)
                revision = int(self._meta(db, "revision")) + 1
                db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
            return receipt

    def mark_dispatch_unknown(self, dispatch_id, now):
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?",
                             (dispatch_id,)).fetchone()
            _require(row is not None and row["status"] in {"IN_FLIGHT", "UNKNOWN"},
                     "Only an in-flight or reconciling dispatch can report an unknown outcome")
            if row["status"] == "IN_FLIGHT":
                db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE dispatch_id=?",
                           (now, dispatch_id))
            self._record_dispatch_unknown(db, dispatch_id, loads(row["payload_json"]), now)

    def _record_dispatch_unknown(self, db, dispatch_id, payload, now):
        stream = db.execute("SELECT * FROM stream_status WHERE stream_id=?", (payload["stream"],)).fetchone()
        _require(stream is not None, "Unknown dispatch stream is missing from the protected state")
        if (stream["ticket"] != payload["ticket"] or
                stream["exact_tuple"] != payload["exact_tuple"]):
            # The ticket may have been reallocated since this old operation
            # became uncertain. Preserve the current stream owner and still
            # emit an urgent revision for the retained UNKNOWN intent.
            revision = int(self._meta(db, "revision")) + 1
            db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
            return
        self._remember_unknown_stream(
            db, dispatch_id, self._row_restore_snapshot(stream), replace=False)
        db.execute("UPDATE stream_status SET state='BLOCKED',actor='controller',"
                   "reason=?,next_action=?,resume_trigger=?,updated_at=? WHERE stream_id=?",
                   (f"Dispatch outcome unknown for {payload['ticket']} ({dispatch_id}); reconcile before retry",
                    "Observe the exact durable dispatch intent before dispatching more work on this stream",
                    "the host returns a fresh observation for this dispatch and its ticket",
                    now, payload["stream"]))
        revision = int(self._meta(db, "revision")) + 1
        db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))

    def begin_dispatch_reconciliation(self, dispatch_id, now):
        """Persist a fresh challenge before observing an UNKNOWN dispatch."""
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            _require(row is not None and row["status"] == "UNKNOWN",
                     "Only an unknown dispatch can be reconciled")
            payload = loads(row["payload_json"])
            payload["reconcile_nonce"] = str(uuid.uuid4())
            payload["reconcile_at"] = now
            db.execute("UPDATE controller_dispatch SET payload_json=?,updated_at=? WHERE dispatch_id=?",
                       (canonical(payload).decode(), now, dispatch_id))
            return {key: value for key, value in payload.items()
                    if key != "_stream_before_unknown"}

    def recover_dispatches(self, now):
        """A crash while calling the host becomes UNKNOWN, never PENDING."""
        timestamp(now)
        with self.transaction() as db:
            rows = db.execute("SELECT dispatch_id,payload_json FROM controller_dispatch WHERE status='IN_FLIGHT'").fetchall()
            for row in rows:
                db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE dispatch_id=?",
                           (now, row["dispatch_id"]))
                self._record_dispatch_unknown(db, row["dispatch_id"], loads(row["payload_json"]), now)

    def prepare_jira_operation(self, record, now):
        """Persist an immutable deterministic Jira intent before any provider call."""
        timestamp(now)
        from .canonical import fingerprint
        _require(isinstance(record, dict) and isinstance(record.get("operation_id"), str) and
                 isinstance(record.get("binding"), dict) and
                 isinstance(record["binding"].get("issue_id"), str),
                 "Jira operation intent is malformed")
        immutable_intent = {key: value for key, value in record.items() if key != "created_at"}
        intent_sha256 = fingerprint("controller-jira-operation", immutable_intent)
        encoded = canonical(record).decode()
        with self.transaction() as db:
            prior = db.execute("SELECT * FROM controller_jira_operation WHERE operation_id=?",
                               (record["operation_id"],)).fetchone()
            if prior is None:
                db.execute("INSERT INTO controller_jira_operation VALUES(?,?,?,?,?,?,NULL,NULL,?)",
                           (record["operation_id"], record["binding"]["issue_id"],
                            record["lifecycle_event"], intent_sha256, "PENDING", encoded, now))
            else:
                _require(prior["intent_sha256"] == intent_sha256,
                         "Jira operation identity collided with a different durable intent")
            row = db.execute("SELECT * FROM controller_jira_operation WHERE operation_id=?",
                             (record["operation_id"],)).fetchone()
            return dict(row) | {"record": loads(row["record_json"])}

    def begin_jira_operation(self, operation_id, now):
        """Mark the durable Jira intent in flight before external mutation."""
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_jira_operation WHERE operation_id=?",
                             (operation_id,)).fetchone()
            _require(row is not None and row["status"] == "PENDING",
                     "Jira operation is not pending")
            db.execute("UPDATE controller_jira_operation SET status='IN_FLIGHT',updated_at=? WHERE operation_id=?",
                       (now, operation_id))
            return loads(row["record_json"])

    def mark_jira_unknown(self, operation_id, now):
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT status FROM controller_jira_operation WHERE operation_id=?",
                             (operation_id,)).fetchone()
            _require(row is not None and row["status"] in {"IN_FLIGHT", "UNKNOWN"},
                     "Only an in-flight or unknown Jira operation can remain unknown")
            db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE operation_id=?",
                       (now, operation_id))

    def recover_jira_operations(self, now):
        """A lost process after mutation becomes UNKNOWN and cannot be reissued."""
        timestamp(now)
        with self.transaction() as db:
            db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE status='IN_FLIGHT'",
                       (now,))

    def jira_operations(self, issue_id):
        _require(isinstance(issue_id, str) and issue_id, "Jira operation query needs an issue identity")
        with self.connection() as db:
            return [dict(row) | {"record": loads(row["record_json"])} for row in db.execute(
                "SELECT * FROM controller_jira_operation WHERE issue_id=? ORDER BY rowid", (issue_id,))]

    def finish_jira_operation(self, operation_id, status, observation, now, operation=None):
        """Persist terminal readback or retain UNKNOWN; never infer a provider result."""
        timestamp(now)
        _require(status in {"SUCCEEDED", "FAILED", "UNKNOWN"}, "Invalid Jira operation result")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_jira_operation WHERE operation_id=?",
                             (operation_id,)).fetchone()
            _require(row is not None and row["status"] in {"IN_FLIGHT", "UNKNOWN"},
                     "Jira operation result does not match durable operation state")
            db.execute("UPDATE controller_jira_operation SET status=?,operation_json=?,readback_json=?,updated_at=? WHERE operation_id=?",
                       (status, canonical(operation).decode() if operation is not None else row["operation_json"],
                        canonical(observation).decode() if observation is not None else None,
                        now, operation_id))
            return loads(row["record_json"])


def _zero_counts():
    return {"required": 0, "completed": 0, "acceptable": 0, "failed": 0, "stale": 0, "outstanding": 0}


def post_merge_jira_progress(*, jira_enabled, merged_ticket, scope, observed_at,
                             jira_binding, reconcile_merged_ticket, fetch_scope_page,
                             include_epics=False, max_pages=20, max_items=10000,
                             max_bytes=16 * 1024 * 1024, max_seconds=60, clock=None):
    """Reconcile the merge first, then report only complete authoritative counts."""
    timestamp(observed_at)
    _require(isinstance(merged_ticket, str) and merged_ticket.strip() and
             isinstance(scope, str) and scope.strip(), "Jira ticket and scoped query are required")
    _require(type(include_epics) is bool, "Jira Epic inclusion must be an observed boolean")
    _require(isinstance(jira_binding, dict) and set(jira_binding) == {"cloud_id", "project_id", "actor_id"} and
             all(isinstance(value, str) and value for value in jira_binding.values()),
             "Jira count needs immutable cloud, project, and actor bindings")
    _require(type(max_pages) is int and 0 < max_pages <= 100 and type(max_items) is int and
             0 < max_items <= 100000 and type(max_bytes) is int and 0 < max_bytes <= 64 * 1024 * 1024 and
             isinstance(max_seconds, (int, float)) and 0 < max_seconds <= 300,
             "Jira count bounds are invalid")
    from .canonical import fingerprint
    scope_sha256 = fingerprint("jira-progress-scope", {"scope": scope, "binding": jira_binding,
                                                        "include_epics": include_epics})
    base = {"schema_version": 3, "merged_ticket": merged_ticket, "scope": scope,
            "scope_sha256": scope_sha256, "include_epics": include_epics,
            "snapshot_id": None,
            "observed_at": observed_at, "closed": "UNOBSERVED", "remaining_open": "UNOBSERVED"}
    if not jira_enabled:
        return {**base, "jira_state": "JIRA_DISABLED", "reason": "Jira is disabled; no reads or writes attempted"}
    try:
        reconciled = reconcile_merged_ticket(merged_ticket)
    except Exception as exc:
        return {**base, "jira_state": "UNOBSERVED", "reason": f"merge reconciliation failed: {type(exc).__name__}"}
    required_receipt = {"ticket", "issue_id", "cloud_id", "project_id", "actor_id", "status",
                        "operation_id", "before_status_id", "after_status_id", "observed_at"}
    if (not isinstance(reconciled, dict) or set(reconciled) != required_receipt or
            reconciled.get("ticket") != merged_ticket or reconciled.get("status") != "RECONCILED" or
            any(reconciled.get(key) != jira_binding[key] for key in jira_binding)):
        return {**base, "jira_state": "UNOBSERVED", "reason": "merge reconciliation is incomplete, unknown, or mismatched"}
    try:
        uuid_value = __import__("uuid").UUID(reconciled["operation_id"])
        _require(str(uuid_value) == reconciled["operation_id"], "Jira operation identity is not canonical")
        reconciled_at = timestamp(reconciled["observed_at"])
        _require(all(isinstance(reconciled[key], str) and reconciled[key] for key in
                     ("issue_id", "before_status_id", "after_status_id")),
                 "Jira reconciliation receipt lacks stable issue/status identity")
    except (ValidationError, ValueError, TypeError, AttributeError):
        return {**base, "jira_state": "UNOBSERVED", "reason": "merge reconciliation receipt is malformed"}
    base["observed_at"] = reconciled["observed_at"]
    items, identities, cursor, seen_cursors = [], set(), None, set()
    snapshot_id = page_observed_at = None
    import time
    timer = clock or time.monotonic
    started = timer()
    pages = total_bytes = 0
    try:
        while True:
            pages += 1
            if pages > max_pages or timer() - started > max_seconds:
                raise ValidationError("Jira pagination exceeded its request or time bound")
            page = fetch_scope_page(scope, cursor)
            if timer() - started > max_seconds:
                raise ValidationError("Jira pagination exceeded its request or time bound")
            total_bytes += len(canonical(page))
            if total_bytes > max_bytes:
                raise ValidationError("Jira pagination exceeded its byte bound")
            required_page = {"items", "next_cursor", "complete", "snapshot_id", "scope_sha256", "observed_at"}
            if not isinstance(page, dict) or set(page) != required_page:
                raise ValidationError("Malformed Jira scope page")
            if page["scope_sha256"] != scope_sha256 or not isinstance(page["snapshot_id"], str) or not page["snapshot_id"]:
                raise ValidationError("Jira page has wrong scope or no stable snapshot")
            if snapshot_id is None:
                snapshot_id = page["snapshot_id"]
                base["snapshot_id"] = snapshot_id
            if page["snapshot_id"] != snapshot_id:
                raise ValidationError("Jira snapshot changed during pagination")
            observed_time = timestamp(page["observed_at"])
            if observed_time < reconciled_at:
                raise ValidationError("Jira count snapshot predates merge reconciliation")
            if page_observed_at is None:
                page_observed_at = page["observed_at"]
            if page["observed_at"] != page_observed_at:
                raise ValidationError("Jira page observation time changed during pagination")
            if not isinstance(page["items"], list):
                raise ValidationError("Malformed Jira scope items")
            for item in page["items"]:
                if not isinstance(item, dict) or set(item) != {"id", "issue_type", "status_category"}:
                    raise ValidationError("Malformed Jira scope item")
                if item["id"] in identities or item["status_category"] not in {"TERMINAL", "NON_TERMINAL"}:
                    raise ValidationError("Duplicate ticket or unknown terminal status category")
                identities.add(item["id"])
                if include_epics or item["issue_type"] != "EPIC":
                    items.append(item)
                if len(identities) > max_items:
                    raise ValidationError("Jira scope exceeded its item bound")
            following = page["next_cursor"]
            if following is None:
                if page["complete"] is not True:
                    raise ValidationError("Final Jira page did not prove query completeness")
                base["observed_at"] = page["observed_at"]
                break
            if page["complete"] is not False:
                raise ValidationError("Non-final Jira page made a contradictory completeness claim")
            if not isinstance(following, str) or not following:
                raise ValidationError("Jira pagination cursor is malformed")
            if following == cursor or following in seen_cursors:
                raise ValidationError("Jira pagination cursor did not advance")
            seen_cursors.add(following)
            cursor = following
    except Exception as exc:
        return {**base, "jira_state": "RECONCILED", "reason": f"scoped Jira count is incomplete: {type(exc).__name__}"}
    return {**base, "jira_state": "COUNTED", "closed": sum(i["status_category"] == "TERMINAL" for i in items),
            "remaining_open": sum(i["status_category"] == "NON_TERMINAL" for i in items),
            "reason": "authoritative complete scoped Jira observation"}


def production_controller_cycle(store, *, now, host_capacity, inventory_binding,
                                repository_root, repository_head_sha, repository_tree_sha,
                                observe_inventory, dispatch_ticket, observe_dispatch, deliver_status,
                                publication_config=None, observe_publication=None,
                                dispatch_role="writer"):
    """Run one real host-controller cycle through explicit reviewed adapters.

    Inventory is observed before scheduling.  Dispatch intent is durable before
    the host call, and an interrupted call becomes UNKNOWN and is only
    reconciled by observation.  Status is acknowledged only from exact delivery
    readback.  One adapter failure is returned for that operation while other
    streams continue through the same cycle.
    """
    _require(isinstance(store, ContinuousControllerStore), "Production cycle needs a controller store")
    _require(all(callable(adapter) for adapter in
                 (observe_inventory, dispatch_ticket, observe_dispatch, deliver_status)),
             "Production controller adapters must be callable")
    timestamp(now)
    _require(dispatch_role in {"writer", "critic"},
             "Production dispatch role must be writer or critic")
    store.recover_dispatches(now)
    _require(isinstance(inventory_binding, dict) and set(inventory_binding) == {
        "project_id", "repository_id", "scope_sha256"} and
        all(isinstance(value, str) and value for value in inventory_binding.values()),
        "Production inventory needs immutable project, repository, and scope bindings")
    # The reviewed reference adapter accepts this boundary value and stamps
    # the provider observation with it.  Keeping the timestamp controller-owned
    # prevents a wall-clock race between inventory collection and validation.
    # Retain compatibility with older test/adapter doubles that already
    # returned the controller timestamp, while the shipped reference adapter
    # receives the authoritative value explicitly.
    parameters = inspect.signature(observe_inventory).parameters
    accepts_time = ("now" in parameters or "controller_now" in parameters or
                    any(parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                           inspect.Parameter.POSITIONAL_OR_KEYWORD)
                        and parameter.default is inspect.Parameter.empty
                        for parameter in parameters.values()))
    observation = observe_inventory(now) if accepts_time else observe_inventory()
    _require(isinstance(observation, dict) and set(observation) == {
        "source", "observed_at", "binding", "complete", "inventory_sha256", "tickets"} and
        observation["source"] == "host_observation" and observation["complete"] is True,
        "Production inventory must be an exact authenticated host observation")
    timestamp(observation["observed_at"])
    _require(observation["observed_at"] == now,
             "Production inventory observation time differs from this controller cycle")
    from .canonical import fingerprint
    _require(observation["binding"] == inventory_binding and
             observation["inventory_sha256"] == fingerprint("controller-inventory", {
                 "binding": inventory_binding, "tickets": observation["tickets"]}),
             "Production inventory identity or digest differs from the configured scope")
    tickets, repository_binding = canonical_repository_paths(
        observation["tickets"], repository_root, repository_head_sha, repository_tree_sha)
    store.bind_repository(repository_binding)
    from .publication_readiness import require_publication_readiness
    publication_reports, errors = {}, []
    for index, item in enumerate(tickets):
        if item["disposition"] != "ELIGIBLE":
            continue
        try:
            _require(callable(observe_publication) and isinstance(publication_config, dict),
                     "PUBLICATION_UNOBSERVED: a reviewed publication observer and accepted configuration are required")
            _require(str(publication_config.get("github", {}).get("repository_id")) ==
                     inventory_binding["repository_id"],
                     "Publication repository differs from the controller inventory binding")
            publication_reports[item["ticket"]] = require_publication_readiness(
                publication_config, observe_publication(loads(canonical(item).decode())),
                ticket=item["ticket"], now=now)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValidationError) else "PUBLICATION_UNOBSERVED: " + type(exc).__name__
            tickets[index] = {**item, "disposition": "BLOCKED", "reason": reason,
                              "next_action": "Resolve the named publication blocker and obtain a fresh observation",
                              "resume_trigger": "the exact completion path passes publication readiness"}
            errors.append({"operation": "publication_readiness", "ticket": item["ticket"],
                           "state": "BLOCKED", "reason": reason})
    # A never-begun intent is safe to retire when fresh inventory or publication
    # preflight no longer admits its ticket. Do this before scheduling so the
    # ticket's new PAUSED/BLOCKED/COMPLETE state remains visible immediately.
    ticket_by_id = {item["ticket"]: item for item in tickets}
    for operation in store.pending_dispatches():
        payload = operation["payload"]
        current = ticket_by_id.get(payload["ticket"])
        if (current is not None and current["disposition"] == "ELIGIBLE" and
                payload["ticket"] in publication_reports):
            continue
        if current is None:
            reason = "Fresh complete inventory no longer contains the pending ticket"
        elif current["disposition"] != "ELIGIBLE":
            reason = f"Fresh preflight disposition is {current['disposition']}: {current['reason']}"
        else:
            reason = "Fresh publication preflight did not admit the pending ticket"
        try:
            store.cancel_pending_dispatch(operation["dispatch_id"], now, reason)
        except Exception as exc:
            errors.append({"operation": "dispatch_cancellation", "dispatch_id": operation["dispatch_id"],
                           "state": "PENDING", "reason": type(exc).__name__})
    streams = store.schedule(tickets, now, host_capacity)
    dispatches = []
    for operation in store.prepare_dispatches(now):
        payload, dispatch_id = operation["payload"], operation["dispatch_id"]
        if operation["status"] == "PENDING":
            admitted = (payload["ticket"] in publication_reports and any(
                    stream["stream"] == payload["stream"] and stream["state"] == "WORKING"
                    and stream["ticket"] == payload["ticket"] and stream["exact_tuple"] == payload["exact_tuple"]
                    for stream in streams))
            if not admitted:
                current = next((item for item in tickets if item["ticket"] == payload["ticket"]), None)
                if current is None:
                    reason = "Fresh complete inventory no longer contains the pending ticket"
                elif current["disposition"] != "ELIGIBLE":
                    reason = (f"Fresh preflight disposition is {current['disposition']}: "
                              f"{current['reason']}")
                elif payload["ticket"] not in publication_reports:
                    reason = "Fresh publication preflight did not admit the pending ticket"
                else:
                    reason = "Fresh schedule did not retain this exact stream, ticket, and tuple"
                try:
                    store.cancel_pending_dispatch(dispatch_id, now, reason)
                except Exception as exc:
                    errors.append({"operation": "dispatch_cancellation", "dispatch_id": dispatch_id,
                                   "state": "PENDING", "reason": type(exc).__name__})
                # PENDING proves begin_dispatch did not run, so this durable
                # cancellation is safe and makes the ticket eligible for a
                # newly prepared intent after a later fresh preflight.
                continue
        try:
            if operation["status"] == "PENDING":
                payload = store.begin_dispatch(dispatch_id, now)
                receipt = dispatch_ticket({**payload, "role": dispatch_role})
                dispatches.append(store.finish_dispatch(dispatch_id, receipt, now))
            else:
                payload = store.begin_dispatch_reconciliation(dispatch_id, now)
                receipt = observe_dispatch({**payload, "role": dispatch_role})
                dispatches.append(store.finish_dispatch(dispatch_id, receipt, now, reconcile=True))
        except Exception as exc:
            try:
                store.mark_dispatch_unknown(dispatch_id, now)
            except Exception:
                pass
            errors.append({"operation": "dispatch", "dispatch_id": dispatch_id,
                           "state": "UNKNOWN", "reason": type(exc).__name__})
    digest = store.digest(now)
    delivery = None
    if digest is not None:
        try:
            receipt = deliver_status(digest)
            _require(isinstance(receipt, dict) and set(receipt) == {
                "delivery_id", "status", "observed_at"} and
                receipt["delivery_id"] == digest["delivery_id"] and
                receipt["status"] == "DELIVERED", "Status delivery readback is missing or mismatched")
            timestamp(receipt["observed_at"])
            store.acknowledge_digest(digest["delivery_id"], receipt["observed_at"])
            delivery = receipt
        except Exception as exc:
            errors.append({"operation": "status_delivery", "delivery_id": digest["delivery_id"],
                           "state": "PENDING", "reason": type(exc).__name__})
    streams = store.snapshot()
    return {"schema_version": 3, "observed_at": now, "streams": streams,
            "dispatch_receipts": dispatches, "status_delivery": delivery,
            "publication_readiness": publication_reports,
            "errors": errors, "execution_authority": False}


def production_jira_lifecycle(store, *, config, contract, event, facts, binding,
                              issue_type, state, producer_id, run_id, now, evidence, transition_id,
                              read_current_status, write_transition, read_transition,
                              observe_provider_identity=None,
                              merge_result_id=None):
    """Reconcile durable intent, then perform at most one write and readback."""
    _require(isinstance(store, ContinuousControllerStore),
             "Jira lifecycle production needs the protected controller store")
    _require(all(callable(adapter) for adapter in
                 (read_current_status, write_transition, read_transition)),
             "Jira lifecycle read/write/readback adapters must be callable")
    if config.get("jira", {}).get("enabled", True) is False:
        return {"planned": False, "reason": "Jira is disabled; no reads or writes",
                "execution_authority": False}
    from .provider_identity import (ProviderIdentityError, expected_jira_identity,
                                    require_jira_write_identity)
    expected = {"cloud_id": config.get("jira", {}).get("cloud_id"),
                "site": config.get("jira", {}).get("site"),
                "project_id": config.get("jira", {}).get("provider_project_id"),
                "project_key": config.get("jira", {}).get("project_key"),
                "account_id": config.get("jira", {}).get("controller_actor_id")}
    observed_identity = None
    try:
        expected_jira_identity(config)
        if not callable(observe_provider_identity):
            raise ProviderIdentityError("IDENTITY_UNOBSERVED", "Jira provider identity adapter is unavailable",
                                        expected=expected)
        observed_identity = observe_provider_identity()
        provider = require_jira_write_identity(config, observed_identity)
        if producer_id != provider["controller_actor_id"]:
            raise ProviderIdentityError("IDENTITY_MISMATCH",
                "Jira lifecycle producer differs from the configured controller actor",
                expected=expected, observed=observed_identity)
        _require(isinstance(binding, dict) and isinstance(binding.get("issue_id"), str)
                 and binding["issue_id"], "Jira lifecycle needs an immutable issue binding")
    except ProviderIdentityError as exc:
        return {"planned": False, "status": exc.code, "reason": str(exc),
                "expected_identity": exc.expected or expected,
                "observed_identity": exc.observed if exc.observed is not None else observed_identity,
                "issue_lookups": 0, "writes_stopped": True, "reads_allowed": True,
                "execution_authority": False}
    except Exception as exc:
        return {"planned": False, "status": "IDENTITY_UNOBSERVED",
                "reason": "Jira provider identity observation failed: " + type(exc).__name__,
                "expected_identity": expected, "observed_identity": None,
                "issue_lookups": 0, "writes_stopped": True, "reads_allowed": True,
                "execution_authority": False}
    adapter_binding = {"issue_id": binding["issue_id"], "jira_provider": provider}
    store.recover_jira_operations(now)
    start_time = timestamp(now)
    try:
        current = read_current_status(adapter_binding)
        _require(isinstance(current, dict) and set(current) == {
            "issue_id", "status_id", "observed_at", "jira_provider"} and
            current["issue_id"] == binding["issue_id"] and
            current["jira_provider"] == provider and
            isinstance(current["status_id"], str) and current["status_id"],
            "Jira pre-write observation is missing or mismatched")
        current_time = timestamp(current["observed_at"])
        _require(current_time >= start_time,
                 "Jira pre-write observation predates the lifecycle operation")
    except Exception as exc:
        return {"planned": False, "reason": f"Jira pre-write observation failed: {type(exc).__name__}",
                "writes_stopped": True, "execution_authority": False}
    from .jira_lifecycle import planned_write, transition_record, apply_read_back
    durable = store.jira_operations(binding["issue_id"])
    for operation_row in durable:
        _require(operation_row["record"].get("binding") == binding and
                 operation_row["record"].get("jira_provider") == provider,
                 "Durable Jira intent belongs to a different connector/site/project/actor")
        if operation_row["status"] == "FAILED":
            return {"planned": False, "reason": "A durable Jira operation failed; writes remain stopped",
                    "writes_stopped": True, "operation_id": operation_row["operation_id"],
                    "current_observation": current, "execution_authority": False}
        if operation_row["status"] != "UNKNOWN":
            continue
        record = operation_row["record"]
        if current["status_id"] == record["to_status_id"]:
            store.finish_jira_operation(record["operation_id"], "SUCCEEDED", current,
                                        current["observed_at"])
            continue
        store.finish_jira_operation(record["operation_id"], "UNKNOWN", current,
                                    current["observed_at"])
        return {"planned": False,
                "reason": "Unknown durable Jira operation was not proven successful by readback; writes remain stopped",
                "writes_stopped": True, "operation_id": record["operation_id"],
                "current_observation": current, "execution_authority": False}
    durable = store.jira_operations(binding["issue_id"])
    prior_writes = []
    for operation_row in durable:
        record = dict(operation_row["record"])
        record["status"] = operation_row["status"]
        if operation_row["readback_json"]:
            observed = loads(operation_row["readback_json"])
            record["read_back"] = {"observed_status": observed.get("status_id", observed.get("status")),
                                   "observed_actor": observed.get("actor"),
                                   "observed_at": observed.get("observed_at")}
        prior_writes.append(record)
    key, target = planned_write(config, contract, event, facts, issue_type=issue_type,
                                current_status=current["status_id"], prior_writes=prior_writes,
                                state=state)
    if key is None:
        return {"planned": False, "reason": target, "current_observation": current,
                "execution_authority": False}
    record = transition_record(binding, event, current["status_id"], target, transition_id,
                               jira_provider=provider,
                               merge_result_id=merge_result_id, producer_id=producer_id,
                               run_id=run_id, now=now, evidence=evidence)
    intent = store.prepare_jira_operation(record, now)
    record = intent["record"]
    if intent["status"] == "SUCCEEDED":
        return {"planned": False, "reason": "Durable Jira operation already succeeded",
                "operation_id": record["operation_id"], "current_observation": current,
                "execution_authority": False}
    _require(intent["status"] == "PENDING", "Jira operation is unresolved; writes remain stopped")
    try:
        latest_identity = observe_provider_identity()
        latest_provider = require_jira_write_identity(config, latest_identity)
        if latest_provider != provider:
            raise ProviderIdentityError("IDENTITY_MISMATCH",
                "Jira provider identity moved after the issue pre-read",
                expected=expected, observed=latest_identity)
    except ProviderIdentityError as exc:
        return {"planned": False, "status": exc.code, "reason": str(exc),
                "expected_identity": exc.expected or expected,
                "observed_identity": exc.observed,
                "current_observation": current, "writes_stopped": True,
                "execution_authority": False}
    except Exception as exc:
        return {"planned": False, "status": "IDENTITY_UNOBSERVED",
                "reason": "Jira provider identity recheck failed: " + type(exc).__name__,
                "expected_identity": expected, "observed_identity": None,
                "current_observation": current, "writes_stopped": True,
                "execution_authority": False}
    store.begin_jira_operation(record["operation_id"], now)
    try:
        operation = write_transition(record)
        _require(isinstance(operation, dict) and set(operation) == {
            "operation_id", "issue_id", "status", "observed_at", "jira_provider"} and
            operation["operation_id"] == record["operation_id"] and
            operation["issue_id"] == binding["issue_id"] and
            operation["jira_provider"] == provider and
            operation["status"] == "ATTEMPTED", "Jira write receipt is missing or mismatched")
        operation_time = timestamp(operation["observed_at"])
        _require(operation_time >= current_time,
                 "Jira write receipt predates the pre-write observation")
        observation = read_transition(record, operation)
        _require(isinstance(observation, dict) and set(observation) == {
            "issue_id", "status", "actor", "observed_at", "jira_provider"} and
            observation["issue_id"] == binding["issue_id"] and
            observation["jira_provider"] == provider and
            observation["actor"] == provider["controller_actor_id"],
            "Jira readback has an invalid or mismatched shape")
        readback_time = timestamp(observation["observed_at"])
        _require(readback_time >= operation_time,
                 "Jira readback predates the write receipt")
        result = apply_read_back(record, observation["status"], observation["actor"],
                                 observation["observed_at"])
        store.finish_jira_operation(record["operation_id"], result["record"]["status"],
                                    observation, observation["observed_at"], operation)
        return {"planned": True, "current_observation": current, **result,
                "execution_authority": False}
    except Exception as exc:
        try:
            store.mark_jira_unknown(record["operation_id"], now)
        except Exception:
            pass
        return {"planned": True, "current_observation": current, **apply_read_back(record, None),
                "reason": type(exc).__name__, "execution_authority": False}

def production_post_merge_progress(**adapters):
    """Named production route for reconcile-first scoped Jira reporting."""
    return post_merge_jira_progress(**adapters)


def production_merge_observed(*, lifecycle_state, lifecycle_facts, jira_progress):
    """Record a routine observed merge, then reconcile Jira and report counts."""
    _require(lifecycle_state in {"MERGING", "MERGE_UNKNOWN"},
             "Production merge observation needs a merge-in-progress state")
    _require(isinstance(lifecycle_facts, dict) and isinstance(jira_progress, dict),
             "Production merge observation needs lifecycle facts and Jira adapters")
    from .lifecycle import transition
    state = transition(lifecycle_state, "MERGE_OBSERVED", lifecycle_facts)
    progress = production_post_merge_progress(**jira_progress)
    return {"schema_version": 3, "state": state, "jira_progress": progress,
            "execution_authority": False}
