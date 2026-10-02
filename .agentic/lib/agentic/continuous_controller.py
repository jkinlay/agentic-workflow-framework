"""Persistent no-idle native-stream scheduling and status reporting.

This reference component records decisions only.  Host dispatch and Jira
mutations remain controller-owned adapter operations with their existing
authority, budget, independence, capacity, and readback checks.
"""
from __future__ import annotations

from contextlib import contextmanager
import re
import sqlite3

from . import ValidationError
from .canonical import canonical, loads, now_text, timestamp
from .controller_state import configure_database, protected_state_path, restrict_state_permissions


STREAM_STATES = {"WORKING", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
TICKET_STATES = {"ELIGIBLE", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
DEFAULT_STATUS_CADENCE_SECONDS = 15 * 60
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
    _require(isinstance(value["paths"], list), "Ticket paths must be a list")
    value = dict(value)
    value["paths"] = [_owned_path(path) for path in value["paths"]]
    _require(len(value["paths"]) == len(set(value["paths"])), "Ticket paths must be unique canonical paths")
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


class ContinuousControllerStore:
    """Durable scheduler in which every configured stream has a visible state."""

    def __init__(self, path, stream_ids, cadence_seconds=DEFAULT_STATUS_CADENCE_SECONDS,
                 worktree_roots=()):
        self.path = protected_state_path(path, worktree_roots)
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
        working, held_paths = [], []
        for row in rows:
            item = inventory.get(row["ticket"]) if row["state"] == "WORKING" else None
            prerequisites = item is not None and item["disposition"] == "ELIGIBLE" and all((
                item["dependencies_satisfied"], item["budget_available"], item["cap_available"],
                item["review_independent"]))
            if (prerequisites and len(working) < host_capacity and
                    not any(_overlap(item["paths"], paths) for paths in held_paths)):
                working.append(row)
                held_paths.append(item["paths"])
                self._update_stream(db, row["stream_id"], "WORKING", item, now)
        assigned = {row["ticket"] for row in working}
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
            regular_due = not last_regular or elapsed >= cadence
            change_due = revision > last_revision
            streams = self._snapshot(db)
            all_complete = all(s["state"] == "COMPLETE" for s in streams)
            if all_complete and not change_due:
                return None
            if not regular_due and not change_due:
                return None
            kind = "REGULAR" if regular_due else "CHANGE"
            from .canonical import fingerprint
            body = {"schema_version": 3, "observed_at": now, "kind": kind,
                    "cadence_seconds": cadence, "all_complete": all_complete, "streams": streams}
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
        """Persist deterministic dispatch intents for newly scheduled work."""
        timestamp(now)
        from .canonical import fingerprint
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM stream_status WHERE state='WORKING' ORDER BY stream_id"):
                payload = {"stream": row["stream_id"], "ticket": row["ticket"],
                           "exact_tuple": row["exact_tuple"], "paths": loads(row["paths"]),
                           "actor": row["actor"], "next_action": row["next_action"]}
                dispatch_id = fingerprint("controller-dispatch", payload)
                payload["dispatch_id"] = dispatch_id
                db.execute("INSERT OR IGNORE INTO controller_dispatch VALUES(?,?,?,?,? ,?,NULL,?)",
                           (dispatch_id, row["stream_id"], row["ticket"], row["exact_tuple"],
                            "PENDING", canonical(payload).decode(), now))
            return [dict(row) | {"payload": loads(row["payload_json"])} for row in db.execute(
                "SELECT * FROM controller_dispatch WHERE status IN ('PENDING','UNKNOWN') ORDER BY stream_id,dispatch_id")]

    def begin_dispatch(self, dispatch_id, now):
        """Move one intent to IN_FLIGHT before the external host call."""
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            _require(row is not None and row["status"] == "PENDING", "Dispatch is not pending")
            db.execute("UPDATE controller_dispatch SET status='IN_FLIGHT',updated_at=? WHERE dispatch_id=?",
                       (now, dispatch_id))
            return loads(row["payload_json"])

    def finish_dispatch(self, dispatch_id, receipt, now, *, reconcile=False):
        """Accept only exact host readback; uncertain calls are never reissued."""
        timestamp(now)
        required = {"dispatch_id", "stream", "ticket", "exact_tuple", "status", "observed_at"}
        _require(isinstance(receipt, dict) and set(receipt) == required and
                 receipt["status"] == "ACCEPTED", "Dispatch receipt is not an exact accepted readback")
        timestamp(receipt["observed_at"])
        with self.transaction() as db:
            row = db.execute("SELECT * FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            expected = "UNKNOWN" if reconcile else "IN_FLIGHT"
            _require(row is not None and row["status"] == expected,
                     "Dispatch receipt does not match the durable operation state")
            payload = loads(row["payload_json"])
            _require(all(receipt[key] == payload[key] for key in
                         ("dispatch_id", "stream", "ticket", "exact_tuple")),
                     "Dispatch receipt differs from the durable intent")
            db.execute("UPDATE controller_dispatch SET status='ACCEPTED',receipt_json=?,updated_at=? WHERE dispatch_id=?",
                       (canonical(receipt).decode(), now, dispatch_id))
            return receipt

    def mark_dispatch_unknown(self, dispatch_id, now):
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT status FROM controller_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
            _require(row is not None and row["status"] == "IN_FLIGHT", "Only an in-flight dispatch can become unknown")
            db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE dispatch_id=?",
                       (now, dispatch_id))

    def recover_dispatches(self, now):
        """A crash while calling the host becomes UNKNOWN, never PENDING."""
        timestamp(now)
        with self.transaction() as db:
            db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE status='IN_FLIGHT'", (now,))


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
        timestamp(reconciled["observed_at"])
        _require(all(isinstance(reconciled[key], str) and reconciled[key] for key in
                     ("issue_id", "before_status_id", "after_status_id")),
                 "Jira reconciliation receipt lacks stable issue/status identity")
    except (ValidationError, ValueError, TypeError, AttributeError):
        return {**base, "jira_state": "UNOBSERVED", "reason": "merge reconciliation receipt is malformed"}
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
            timestamp(page["observed_at"])
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


def production_controller_cycle(store, *, now, host_capacity, inventory_binding, observe_inventory,
                                dispatch_ticket, observe_dispatch, deliver_status):
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
    store.recover_dispatches(now)
    _require(isinstance(inventory_binding, dict) and set(inventory_binding) == {
        "project_id", "repository_id", "scope_sha256"} and
        all(isinstance(value, str) and value for value in inventory_binding.values()),
        "Production inventory needs immutable project, repository, and scope bindings")
    observation = observe_inventory()
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
    streams = store.schedule(observation["tickets"], now, host_capacity)
    dispatches, errors = [], []
    for operation in store.prepare_dispatches(now):
        payload, dispatch_id = operation["payload"], operation["dispatch_id"]
        try:
            if operation["status"] == "PENDING":
                payload = store.begin_dispatch(dispatch_id, now)
                receipt = dispatch_ticket(payload)
                dispatches.append(store.finish_dispatch(dispatch_id, receipt, now))
            else:
                receipt = observe_dispatch(payload)
                dispatches.append(store.finish_dispatch(dispatch_id, receipt, now, reconcile=True))
        except Exception as exc:
            if operation["status"] == "PENDING":
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
    return {"schema_version": 3, "observed_at": now, "streams": streams,
            "dispatch_receipts": dispatches, "status_delivery": delivery,
            "errors": errors, "execution_authority": False}


def production_jira_lifecycle(*, config, contract, event, facts, binding,
                              issue_type, current_status, prior_writes,
                              state, producer_id, run_id, now, evidence,
                              transition_id, write_transition, read_transition,
                              merge_result_id=None):
    """Execute one mapped Jira lifecycle event with write/readback adapters."""
    _require(callable(write_transition) and callable(read_transition),
             "Jira lifecycle write and readback adapters must be callable")
    from .jira_lifecycle import planned_write, transition_record, apply_read_back
    key, target = planned_write(config, contract, event, facts, issue_type=issue_type,
                                current_status=current_status, prior_writes=prior_writes,
                                state=state)
    if key is None:
        return {"planned": False, "reason": target, "execution_authority": False}
    record = transition_record(binding, event, current_status, target, transition_id,
                               merge_result_id=merge_result_id, producer_id=producer_id,
                               run_id=run_id, now=now, evidence=evidence)
    try:
        operation = write_transition(record)
        _require(isinstance(operation, dict) and set(operation) == {
            "operation_id", "status", "observed_at"} and
            operation["operation_id"] == record["operation_id"] and
            operation["status"] == "ATTEMPTED", "Jira write receipt is missing or mismatched")
        timestamp(operation["observed_at"])
        observation = read_transition(record, operation)
        _require(isinstance(observation, dict) and set(observation) == {
            "status", "actor", "observed_at"}, "Jira readback has an invalid shape")
        timestamp(observation["observed_at"])
        return {"planned": True, **apply_read_back(record, observation["status"],
                                                     observation["actor"], observation["observed_at"]),
                "execution_authority": False}
    except Exception as exc:
        return {"planned": True, **apply_read_back(record, None),
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
