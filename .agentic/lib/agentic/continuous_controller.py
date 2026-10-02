"""Persistent no-idle native-stream scheduling and status reporting.

This reference component records decisions only.  Host dispatch and Jira
mutations remain controller-owned adapter operations with their existing
authority, budget, independence, capacity, and readback checks.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from . import ValidationError
from .canonical import canonical, loads, now_text, timestamp


STREAM_STATES = {"WORKING", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
TICKET_STATES = {"ELIGIBLE", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
DEFAULT_STATUS_CADENCE_SECONDS = 15 * 60

DDL = """
CREATE TABLE IF NOT EXISTS controller_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stream_status(
 stream_id TEXT PRIMARY KEY,state TEXT NOT NULL,ticket TEXT,actor TEXT NOT NULL,
 reason TEXT NOT NULL,next_action TEXT NOT NULL,resume_trigger TEXT NOT NULL,
 exact_tuple TEXT NOT NULL,activity TEXT NOT NULL,verification_gate TEXT NOT NULL,
 reviewer_completion TEXT NOT NULL,open_findings INTEGER NOT NULL,
 jira_status TEXT NOT NULL,paths TEXT NOT NULL,updated_at TEXT NOT NULL);
"""


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _ticket(value):
    required = {"ticket", "priority", "disposition", "actor", "reason", "next_action", "resume_trigger",
                "paths", "dependencies_satisfied", "budget_available", "cap_available", "review_independent",
                "exact_tuple", "activity", "verification_gate", "reviewer_completion", "open_findings", "jira_status"}
    _require(isinstance(value, dict) and set(value) == required, "Malformed continuous-controller ticket")
    _require(isinstance(value["ticket"], str) and value["ticket"].strip(), "Ticket identity is required")
    _require(type(value["priority"]) is int and value["priority"] >= 0, "Ticket priority must be nonnegative")
    _require(value["disposition"] in TICKET_STATES, "Unknown ticket scheduling disposition")
    _require(isinstance(value["paths"], list) and len(value["paths"]) == len(set(value["paths"])) and
             all(isinstance(path, str) and path for path in value["paths"]), "Ticket paths must be unique strings")
    for key in ("dependencies_satisfied", "budget_available", "cap_available", "review_independent"):
        _require(type(value[key]) is bool, f"{key} must be observed boolean")
    for key in ("actor", "reason", "next_action", "resume_trigger", "exact_tuple", "activity",
                "verification_gate", "jira_status"):
        _require(isinstance(value[key], str) and value[key].strip(), f"{key} is required")
    counts = value["reviewer_completion"]
    _require(isinstance(counts, dict) and set(counts) == {
        "required", "completed", "acceptable", "failed", "stale", "outstanding"} and
        all(type(number) is int and number >= 0 for number in counts.values()),
        "Reviewer completion requires all six nonnegative counts")
    _require(type(value["open_findings"]) is int and value["open_findings"] >= 0,
             "Open finding count must be nonnegative")
    return dict(value)


def _overlap(first, second):
    def surface(path):
        value = path.replace("\\", "/").rstrip("/").casefold()
        for suffix in ("/**", "/*"):
            if value.endswith(suffix):
                value = value[:-len(suffix)].rstrip("/")
        return value
    left, right = [surface(path) for path in first], [surface(path) for path in second]
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in left for b in right)


class ContinuousControllerStore:
    """Durable scheduler in which every configured stream has a visible state."""

    def __init__(self, path, stream_ids, cadence_seconds=DEFAULT_STATUS_CADENCE_SECONDS):
        self.path = Path(path).absolute()
        _require(isinstance(stream_ids, (list, tuple)) and stream_ids and
                 len(stream_ids) == len(set(stream_ids)) and
                 all(isinstance(item, str) and item.strip() for item in stream_ids),
                 "Configured stream identities must be unique nonempty strings")
        _require(type(cadence_seconds) is int and cadence_seconds > 0, "Status cadence must be positive seconds")
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
                _require(int(self._meta(db, "cadence_seconds")) == cadence_seconds,
                         "Configured status cadence changed; call set_cadence explicitly")
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

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
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
        working = [row for row in rows if row["state"] == "WORKING" and row["ticket"] in inventory]
        assigned = {row["ticket"] for row in working}
        held_paths = [loads(row["paths"]) for row in working]
        slots = max(0, host_capacity - len(working))
        eligible = sorted((item for item in tickets if item["ticket"] not in assigned and
                           item["disposition"] == "ELIGIBLE"), key=lambda item: (item["priority"], item["ticket"]))
        paused = sorted((item for item in tickets if item["ticket"] not in assigned and
                         item["disposition"] == "PAUSED_INPUT"), key=lambda item: (item["priority"], item["ticket"]))
        blocked = sorted((item for item in tickets if item["ticket"] not in assigned and
                          item["disposition"] == "BLOCKED"), key=lambda item: (item["priority"], item["ticket"]))
        free_rows = [row for row in rows if row not in working]
        for row in free_rows:
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
        """Emit change digests immediately without delaying the regular cadence."""
        current = timestamp(now)
        with self.transaction() as db:
            revision = int(self._meta(db, "revision"))
            last_revision = int(self._meta(db, "last_digest_revision"))
            last_regular = self._meta(db, "last_regular_digest")
            cadence = int(self._meta(db, "cadence_seconds"))
            regular_due = not last_regular or (current - timestamp(last_regular)).total_seconds() >= cadence
            change_due = revision > last_revision
            if not regular_due and not change_due:
                return None
            kind = "REGULAR" if regular_due else "CHANGE"
            if regular_due:
                db.execute("UPDATE controller_meta SET value=? WHERE key='last_regular_digest'", (now,))
            db.execute("UPDATE controller_meta SET value=? WHERE key='last_digest_revision'", (str(revision),))
            streams = self._snapshot(db)
            return {"schema_version": 3, "observed_at": now, "kind": kind,
                    "cadence_seconds": cadence, "all_complete": all(s["state"] == "COMPLETE" for s in streams),
                    "streams": streams}


def _zero_counts():
    return {"required": 0, "completed": 0, "acceptable": 0, "failed": 0, "stale": 0, "outstanding": 0}


def post_merge_jira_progress(*, jira_enabled, merged_ticket, scope, observed_at,
                             reconcile_merged_ticket, fetch_scope_page, include_epics=False):
    """Reconcile the merge first, then report only complete authoritative counts."""
    timestamp(observed_at)
    base = {"schema_version": 3, "merged_ticket": merged_ticket, "scope": scope,
            "observed_at": observed_at, "closed": "UNOBSERVED", "remaining_open": "UNOBSERVED"}
    if not jira_enabled:
        return {**base, "jira_state": "JIRA_DISABLED", "reason": "Jira is disabled; no reads or writes attempted"}
    try:
        reconciled = reconcile_merged_ticket(merged_ticket)
    except BaseException as exc:
        return {**base, "jira_state": "UNOBSERVED", "reason": f"merge reconciliation failed: {type(exc).__name__}"}
    if not isinstance(reconciled, dict) or reconciled.get("ticket") != merged_ticket or reconciled.get("status") != "RECONCILED":
        return {**base, "jira_state": "UNOBSERVED", "reason": "merge reconciliation is incomplete, unknown, or mismatched"}
    items, identities, cursor, seen_cursors = [], set(), None, set()
    try:
        while True:
            page = fetch_scope_page(scope, cursor)
            if not isinstance(page, dict) or set(page) != {"items", "next_cursor", "complete"} or page["complete"] is not True:
                raise ValidationError("Jira pagination/query completeness is unproved")
            for item in page["items"]:
                if not isinstance(item, dict) or set(item) != {"id", "issue_type", "status_category"}:
                    raise ValidationError("Malformed Jira scope item")
                if item["id"] in identities or item["status_category"] not in {"TERMINAL", "NON_TERMINAL"}:
                    raise ValidationError("Duplicate ticket or unknown terminal status category")
                identities.add(item["id"])
                if include_epics or item["issue_type"] != "EPIC":
                    items.append(item)
            following = page["next_cursor"]
            if following is None:
                break
            if following == cursor or following in seen_cursors:
                raise ValidationError("Jira pagination cursor did not advance")
            seen_cursors.add(following)
            cursor = following
    except BaseException as exc:
        return {**base, "jira_state": "RECONCILED", "reason": f"scoped Jira count is incomplete: {type(exc).__name__}"}
    return {**base, "jira_state": "RECONCILED", "closed": sum(i["status_category"] == "TERMINAL" for i in items),
            "remaining_open": sum(i["status_category"] == "NON_TERMINAL" for i in items),
            "reason": "authoritative complete scoped Jira observation"}
