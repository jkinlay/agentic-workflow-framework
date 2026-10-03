"""Durable all-reviewer barrier for final review submission.

The barrier records coordination state only.  It neither dispatches agents nor
grants merge, Jira, or provider authority.  A provider adapter may be called
only after :meth:`ReviewCompletionStore.prepare_submission` has atomically
captured a complete, acceptable result set for one frozen candidate tuple.
"""
from __future__ import annotations

from contextlib import contextmanager
import re
import sqlite3
import uuid

from . import ValidationError
from .canonical import canonical, fingerprint, fresh, loads, now_text, timestamp
from .controller_state import configure_database, protected_state_path, restrict_state_permissions


TUPLE_FIELDS = (
    "repository",
    "base_sha",
    "head_sha",
    "head_tree_sha",
    "contract_sha256",
    "review_input_sha256",
)
TERMINAL_OUTCOMES = {"ACCEPTABLE", "FAILED", "TIMED_OUT", "MALFORMED", "CANCELLED"}
FINAL_STATES = {"SUBMITTED", "SUBMISSION_UNKNOWN"}
SHA40 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
STATE_APPLICATION_ID = 0x41574632


DDL = """
CREATE TABLE IF NOT EXISTS review_cycles(
 cycle_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, tuple_json TEXT NOT NULL,
 tuple_sha256 TEXT NOT NULL, reviewers_json TEXT NOT NULL,
 reviewer_set_sha256 TEXT NOT NULL, state TEXT NOT NULL,
 completion_snapshot_json TEXT, completion_snapshot_sha256 TEXT,
 aggregate_json TEXT, aggregate_sha256 TEXT, submission_id TEXT UNIQUE,
 submission_prepared_at TEXT, provider_receipt_json TEXT, invalidation_reason TEXT);
CREATE TABLE IF NOT EXISTS active_cycle(singleton INTEGER PRIMARY KEY CHECK(singleton=1), cycle_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviewer_results(
 cycle_id TEXT NOT NULL, reviewer_id TEXT NOT NULL, state TEXT NOT NULL,
 dispatched_at TEXT, terminal_at TEXT, result_json TEXT, result_sha256 TEXT,
 duplicate_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(cycle_id,reviewer_id),
 FOREIGN KEY(cycle_id) REFERENCES review_cycles(cycle_id));
CREATE TABLE IF NOT EXISTS rejected_results(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id TEXT NOT NULL,
 reviewer_id TEXT NOT NULL, observed_at TEXT NOT NULL, reason TEXT NOT NULL,
 result_sha256 TEXT);
CREATE TABLE IF NOT EXISTS reviewer_set_dispositions(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, disposition_id TEXT NOT NULL UNIQUE,
 applied_at TEXT NOT NULL, old_cycle_id TEXT NOT NULL, new_cycle_id TEXT NOT NULL,
 tuple_sha256 TEXT NOT NULL, old_reviewer_set_sha256 TEXT NOT NULL,
 new_reviewer_set_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
 record_sha256 TEXT NOT NULL, review_json TEXT NOT NULL, review_sha256 TEXT NOT NULL,
 FOREIGN KEY(old_cycle_id) REFERENCES review_cycles(cycle_id),
 FOREIGN KEY(new_cycle_id) REFERENCES review_cycles(cycle_id));
"""

REVIEWER_REMOVAL_MAX_AGE_SECONDS = 300


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _candidate(value):
    _require(isinstance(value, dict) and set(value) == set(TUPLE_FIELDS),
             "Review tuple requires exactly repository/base/head/tree/contract/review-input fields")
    _require(isinstance(value["repository"], str) and re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]), "Invalid repository identity")
    for field in ("base_sha", "head_sha", "head_tree_sha"):
        _require(isinstance(value[field], str) and SHA40.fullmatch(value[field]) is not None,
                 f"{field} must be a lowercase 40-character Git object ID")
    for field in ("contract_sha256", "review_input_sha256"):
        _require(isinstance(value[field], str) and DIGEST.fullmatch(value[field]) is not None,
                 f"{field} must be a lowercase SHA-256 digest")
    return dict(value)


def _reviewers(value):
    _require(isinstance(value, (list, tuple)) and value, "At least one independent reviewer is required")
    _require(all(isinstance(item, str) and item.strip() == item and item for item in value),
             "Reviewer identities must be nonempty strings")
    _require(len(value) == len(set(value)), "Required reviewer identities must be unique")
    return sorted(value)


def _terminal_result(reviewer_id, outcome, result):
    """Validate the reviewer payload and derive whether it can be acceptable.

    The transport cannot assert ACCEPTABLE independently of the result.  An
    approval has to identify the frozen reviewer and carry no open findings.
    Failed/timeout/malformed payloads remain audit data and are never accepted.
    """
    _require(isinstance(result, dict), "Reviewer result must be an object")
    raw = canonical(result)
    _require(len(raw) <= 1024 * 1024, "Reviewer result exceeds the 1 MiB evidence limit")
    if outcome == "ACCEPTABLE":
        _require(result.get("verdict") == "APPROVE", "ACCEPTABLE requires an APPROVE verdict")
        _require(result.get("reviewer") == reviewer_id,
                 "ACCEPTABLE reviewer identity differs from the frozen reviewer")
        findings = result.get("findings", [])
        _require(isinstance(findings, list), "Reviewer findings must be a list")
        _require(all(isinstance(item, dict) for item in findings), "Reviewer finding must be an object")
        _require(not any(item.get("status") != "RESOLVED" for item in findings),
                 "ACCEPTABLE result carries an unresolved finding")
    return raw.decode()


def _binding(candidate, reviewers):
    candidate, reviewers = _candidate(candidate), _reviewers(reviewers)
    return {
        "tuple": candidate,
        "tuple_sha256": fingerprint("review-tuple", candidate),
        "required_reviewers": reviewers,
        "reviewer_set_sha256": fingerprint("reviewer-set", reviewers),
    }


def _canonical_uuid(value, label):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(f"{label} must be a UUID") from exc
    _require(str(parsed) == value, f"{label} must be canonical")
    return value


def _reviewer_removal_disposition(value, *, active, binding, owner_actor_id,
                                  provider_identity, now):
    """Validate a fresh provider-observed owner decision to weaken one frozen set."""
    _require(isinstance(value, dict) and set(value) == {"record", "record_sha256", "review"},
             "Reviewer removal disposition must contain exact record, digest, and review fields")
    record, review = value["record"], value["review"]
    record_fields = {
        "schema_version", "disposition_id", "decision", "old_cycle_id", "tuple",
        "tuple_sha256", "old_required_reviewers", "old_reviewer_set_sha256",
        "new_required_reviewers", "new_reviewer_set_sha256", "removed_reviewers",
        "reason", "owner_actor_id", "provider_identity", "issued_at", "expires_at",
    }
    _require(isinstance(record, dict) and set(record) == record_fields,
             "Reviewer removal disposition record must be an exact object")
    _require(record["schema_version"] == 1 and record["decision"] == "REMOVE_REQUIRED_REVIEWERS",
             "Reviewer removal disposition has an unsupported version or decision")
    _canonical_uuid(record["disposition_id"], "Reviewer removal disposition_id")
    _canonical_uuid(record["old_cycle_id"], "Reviewer removal old_cycle_id")
    _require(isinstance(record["reason"], str) and record["reason"].strip() == record["reason"] and
             1 <= len(record["reason"]) <= 1000, "Reviewer removal needs a bounded nonempty reason")
    old_reviewers = _reviewers(record["old_required_reviewers"])
    new_reviewers = _reviewers(record["new_required_reviewers"])
    _require(record["old_required_reviewers"] == old_reviewers and
             record["new_required_reviewers"] == new_reviewers,
             "Reviewer removal sets must use deterministic sorted order")
    removed = sorted(set(old_reviewers) - set(new_reviewers))
    _require(removed and record["removed_reviewers"] == removed,
             "Reviewer removal disposition must name every and only removed reviewer")
    _require(record["tuple"] == binding["tuple"] and
             record["tuple_sha256"] == binding["tuple_sha256"],
             "Reviewer removal disposition differs from the exact candidate tuple")
    _require(record["old_cycle_id"] == active["cycle_id"] and
             old_reviewers == loads(active["reviewers_json"]) and
             record["old_reviewer_set_sha256"] == active["reviewer_set_sha256"],
             "Reviewer removal disposition differs from the active old cycle or reviewer set")
    _require(new_reviewers == binding["required_reviewers"] and
             record["new_reviewer_set_sha256"] == binding["reviewer_set_sha256"],
             "Reviewer removal disposition differs from the requested new reviewer set")
    _require(record["old_reviewer_set_sha256"] == fingerprint("reviewer-set", old_reviewers) and
             record["new_reviewer_set_sha256"] == fingerprint("reviewer-set", new_reviewers),
             "Reviewer removal disposition carries an invalid reviewer-set digest")
    _require(isinstance(owner_actor_id, str) and owner_actor_id and
             isinstance(provider_identity, str) and provider_identity,
             "Reviewer removal requires configured owner actor and provider identities")
    _require(record["owner_actor_id"] == owner_actor_id and
             record["provider_identity"] == provider_identity,
             "Reviewer removal actor or provider identity is not trusted")
    issued, expires, observed_now = (timestamp(record["issued_at"]),
                                     timestamp(record["expires_at"]), timestamp(now))
    _require(expires > issued and (expires - issued).total_seconds() <= REVIEWER_REMOVAL_MAX_AGE_SECONDS,
             "Reviewer removal disposition freshness window is invalid")
    fresh(record["issued_at"], now, REVIEWER_REMOVAL_MAX_AGE_SECONDS)
    _require(observed_now <= expires, "Reviewer removal disposition has expired")
    record_hash = fingerprint("reviewer-removal-disposition", record)
    _require(value["record_sha256"] == record_hash,
             "Reviewer removal disposition digest does not match its record")
    review_fields = {"status", "review_id", "record_sha256", "owner_actor_id",
                     "provider_identity", "observed_at"}
    _require(isinstance(review, dict) and set(review) == review_fields and
             review["status"] == "APPROVED", "Reviewer removal needs an exact approved review")
    _require(isinstance(review["review_id"], str) and review["review_id"].strip() == review["review_id"] and
             review["review_id"], "Reviewer removal review_id must be nonempty")
    _require(review["record_sha256"] == record_hash and
             review["owner_actor_id"] == owner_actor_id and
             review["provider_identity"] == provider_identity,
             "Reviewer removal review is not bound to the record, actor, and provider")
    fresh(review["observed_at"], now, REVIEWER_REMOVAL_MAX_AGE_SECONDS)
    observed = timestamp(review["observed_at"])
    _require(issued <= observed <= expires,
             "Reviewer removal review observation is outside the disposition freshness window")
    return record, record_hash, review


class ReviewCompletionStore:
    """SQLite ledger that closes the race between review completion and submit.

    The database belongs in protected controller state outside worker and
    reviewer worktrees.  Each public mutation uses ``BEGIN IMMEDIATE``.  The
    submitted aggregate and its completion snapshot are immutable thereafter.
    """

    def __init__(self, path, worktree_roots=(), *, owner_actor_id=None, provider_identity=None):
        self.path = protected_state_path(path, worktree_roots)
        self.owner_actor_id = owner_actor_id
        self.provider_identity = provider_identity
        with self.connection() as db:
            db.executescript(DDL)
            columns = {row[1] for row in db.execute("PRAGMA table_info(review_cycles)")}
            if "submission_prepared_at" not in columns:
                db.execute("ALTER TABLE review_cycles ADD COLUMN submission_prepared_at TEXT")
        restrict_state_permissions(self.path)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            configure_database(db, self.path, STATE_APPLICATION_ID, foreign_keys=True)
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
    def _active(db):
        row = db.execute("SELECT c.* FROM active_cycle a JOIN review_cycles c ON c.cycle_id=a.cycle_id WHERE a.singleton=1").fetchone()
        _require(row is not None, "No frozen reviewer cycle; freeze the complete set before dispatch")
        return row

    @staticmethod
    def _matches(row, binding):
        return row["tuple_sha256"] == binding["tuple_sha256"] and row["reviewer_set_sha256"] == binding["reviewer_set_sha256"]

    def freeze(self, candidate, reviewers, reviewer_removal_disposition=None):
        """Freeze the complete reviewer set before any reviewer dispatch.

        Repeating the exact freeze is idempotent.  A changed candidate or set
        invalidates a collecting/ready aggregate and creates a fresh cycle.
        Once any reviewer was dispatched, removing or replacing a required
        reviewer needs a fresh reviewed owner disposition bound to both sets.
        An uncertain provider submission must be reconciled first.
        """
        binding = _binding(candidate, reviewers)
        with self.transaction() as db:
            active = db.execute("SELECT c.* FROM active_cycle a JOIN review_cycles c ON c.cycle_id=a.cycle_id WHERE a.singleton=1").fetchone()
            if active is not None and self._matches(active, binding) and active["state"] != "INVALIDATED":
                _require(reviewer_removal_disposition is None,
                         "Reviewer removal disposition supplied for an unchanged reviewer set")
                return self._summary(db, active, current=True)
            disposition = None
            if active is not None:
                _require(active["state"] not in {"SUBMITTING", "SUBMISSION_UNKNOWN"},
                         "Reconcile the uncertain provider submission before freezing another cycle")
                old_reviewers = loads(active["reviewers_json"])
                removed = sorted(set(old_reviewers) - set(binding["required_reviewers"]))
                dispatched = db.execute(
                    "SELECT 1 FROM reviewer_results WHERE cycle_id=? LIMIT 1",
                    (active["cycle_id"],)).fetchone() is not None
                if removed and dispatched:
                    _require(reviewer_removal_disposition is not None,
                             "A reviewed owner disposition is required to remove a dispatched-cycle reviewer")
                    _require(loads(active["tuple_json"]) == binding["tuple"],
                             "Reviewer removal disposition requires the unchanged exact candidate tuple")
                    disposition = _reviewer_removal_disposition(
                        reviewer_removal_disposition, active=active, binding=binding,
                        owner_actor_id=self.owner_actor_id,
                        provider_identity=self.provider_identity, now=now_text())
                else:
                    _require(reviewer_removal_disposition is None,
                             "Reviewer removal disposition supplied when no dispatched-cycle reviewer is removed")
                if active["state"] != "SUBMITTED":
                    db.execute("UPDATE review_cycles SET state='INVALIDATED',invalidation_reason=? WHERE cycle_id=?",
                               (("reviewer set reduced by reviewed owner disposition " + disposition[0]["disposition_id"])
                                if disposition else "candidate or required reviewer set moved", active["cycle_id"]))
            cycle_id = str(uuid.uuid4())
            created = now_text()
            db.execute("INSERT OR IGNORE INTO review_cycles(cycle_id,created_at,tuple_json,tuple_sha256,reviewers_json,reviewer_set_sha256,state) VALUES(?,?,?,?,?,?,'COLLECTING')",
                       (cycle_id, created, canonical(binding["tuple"]).decode(), binding["tuple_sha256"],
                        canonical(binding["required_reviewers"]).decode(), binding["reviewer_set_sha256"]))
            db.execute("INSERT INTO active_cycle VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET cycle_id=excluded.cycle_id", (cycle_id,))
            if active is not None and disposition is not None:
                record, record_hash, review = disposition
                db.execute(
                    "INSERT INTO reviewer_set_dispositions(disposition_id,applied_at,old_cycle_id,new_cycle_id,tuple_sha256,old_reviewer_set_sha256,new_reviewer_set_sha256,record_json,record_sha256,review_json,review_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (record["disposition_id"], now_text(), active["cycle_id"], cycle_id,
                     binding["tuple_sha256"], active["reviewer_set_sha256"], binding["reviewer_set_sha256"],
                     canonical(record).decode(), record_hash, canonical(review).decode(),
                     fingerprint("reviewer-removal-review", review)))
            return self._summary(db, self._active(db), current=True)

    def reviewer_set_audit(self):
        """Return append-only reviewer-set weakening evidence from protected state."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT sequence,applied_at,old_cycle_id,new_cycle_id,tuple_sha256,old_reviewer_set_sha256,"
                "new_reviewer_set_sha256,record_json,record_sha256,review_json,review_sha256 "
                "FROM reviewer_set_dispositions ORDER BY sequence").fetchall()
            result = []
            for row in rows:
                record, review = loads(row["record_json"]), loads(row["review_json"])
                _require(row["record_sha256"] == fingerprint("reviewer-removal-disposition", record) and
                         row["review_sha256"] == fingerprint("reviewer-removal-review", review),
                         "Persisted reviewer removal audit digest is invalid")
                _require(record["old_cycle_id"] == row["old_cycle_id"] and
                         record["tuple_sha256"] == row["tuple_sha256"] and
                         record["old_reviewer_set_sha256"] == row["old_reviewer_set_sha256"] and
                         record["new_reviewer_set_sha256"] == row["new_reviewer_set_sha256"] and
                         review["record_sha256"] == row["record_sha256"],
                         "Persisted reviewer removal audit binding is invalid")
                result.append({**{key: row[key] for key in (
                    "sequence", "applied_at", "old_cycle_id", "new_cycle_id",
                    "record_sha256", "review_sha256")}, "record": record, "review": review})
            return result

    def dispatch(self, reviewer_id, candidate, reviewers):
        """Persist RUNNING before dispatch and return the exact result binding."""
        binding = _binding(candidate, reviewers)
        with self.transaction() as db:
            cycle = self._active(db)
            _require(self._matches(cycle, binding), "Candidate or reviewer set moved; freeze a fresh cycle")
            _require(cycle["state"] == "COLLECTING", "Review dispatch is closed for this cycle")
            required = loads(cycle["reviewers_json"])
            _require(reviewer_id in required, "Reviewer is not in the frozen required set")
            old = db.execute("SELECT * FROM reviewer_results WHERE cycle_id=? AND reviewer_id=?",
                             (cycle["cycle_id"], reviewer_id)).fetchone()
            _require(old is None or old["state"] == "RUNNING", "Reviewer already has a terminal result")
            db.execute("INSERT INTO reviewer_results(cycle_id,reviewer_id,state,dispatched_at) VALUES(?,?,'RUNNING',?) ON CONFLICT(cycle_id,reviewer_id) DO NOTHING",
                       (cycle["cycle_id"], reviewer_id, now_text()))
            return {"cycle_id": cycle["cycle_id"], "reviewer_id": reviewer_id,
                    "tuple_sha256": cycle["tuple_sha256"], "reviewer_set_sha256": cycle["reviewer_set_sha256"]}

    def record_result(self, reviewer_id, result_binding, outcome, result):
        """Persist one terminal result; duplicates and stale bindings fail closed."""
        _require(outcome in TERMINAL_OUTCOMES, "Unknown reviewer outcome")
        _require(isinstance(result_binding, dict) and set(result_binding) == {
            "cycle_id", "reviewer_id", "tuple_sha256", "reviewer_set_sha256"}, "Malformed reviewer result binding")
        _require(result_binding["reviewer_id"] == reviewer_id, "Reviewer identity differs from dispatch binding")
        raw = _terminal_result(reviewer_id, outcome, result)
        result_hash = fingerprint("reviewer-result", result)
        with self.transaction() as db:
            cycle = self._active(db)
            required = loads(cycle["reviewers_json"])
            _require(reviewer_id in required, "Reviewer is not in the frozen required set")
            row = db.execute("SELECT * FROM reviewer_results WHERE cycle_id=? AND reviewer_id=?",
                             (cycle["cycle_id"], reviewer_id)).fetchone()
            if cycle["state"] != "COLLECTING":
                self._reject(db, cycle["cycle_id"], reviewer_id, "LATE", result_hash)
                return {"accepted": False, "reason": "LATE", "summary": self._summary(db, cycle, current=True)}
            _require(row is not None, "Reviewer result arrived without a persisted dispatch")
            if row is not None and row["terminal_at"] is not None:
                db.execute("UPDATE reviewer_results SET duplicate_count=duplicate_count+1 WHERE cycle_id=? AND reviewer_id=?",
                           (cycle["cycle_id"], reviewer_id))
                self._reject(db, cycle["cycle_id"], reviewer_id, "DUPLICATE", result_hash)
                return {"accepted": False, "reason": "DUPLICATE", "summary": self._summary(db, cycle, current=True)}
            bound = (result_binding["cycle_id"] == cycle["cycle_id"] and
                     result_binding["tuple_sha256"] == cycle["tuple_sha256"] and
                     result_binding["reviewer_set_sha256"] == cycle["reviewer_set_sha256"])
            state = outcome if bound else "STALE"
            observed = now_text()
            db.execute("INSERT INTO reviewer_results(cycle_id,reviewer_id,state,dispatched_at,terminal_at,result_json,result_sha256) VALUES(?,?,?,?,?,?,?) "
                       "ON CONFLICT(cycle_id,reviewer_id) DO UPDATE SET state=excluded.state,terminal_at=excluded.terminal_at,result_json=excluded.result_json,result_sha256=excluded.result_sha256",
                       (cycle["cycle_id"], reviewer_id, state, observed, observed, raw, result_hash))
            if not bound:
                self._reject(db, cycle["cycle_id"], reviewer_id, "TUPLE_MISMATCH", result_hash)
            return {"accepted": bound, "reason": None if bound else "TUPLE_MISMATCH",
                    "summary": self._summary(db, cycle, current=True)}

    @staticmethod
    def _reject(db, cycle_id, reviewer_id, reason, result_hash):
        db.execute("INSERT INTO rejected_results(cycle_id,reviewer_id,observed_at,reason,result_sha256) VALUES(?,?,?,?,?)",
                   (cycle_id, reviewer_id, now_text(), reason, result_hash))

    def _summary(self, db, cycle, *, current):
        reviewers = loads(cycle["reviewers_json"])
        rows = {row["reviewer_id"]: row for row in db.execute(
            "SELECT * FROM reviewer_results WHERE cycle_id=?", (cycle["cycle_id"],)).fetchall()}
        states = []
        completed = acceptable = failed = stale = 0
        for reviewer in reviewers:
            row = rows.get(reviewer)
            if row is None or row["terminal_at"] is None:
                state = "RUNNING" if row is not None else "MISSING"
            else:
                completed += 1
                if row["duplicate_count"]:
                    state = "DUPLICATE"
                    failed += 1
                else:
                    state = row["state"]
                    if state == "ACCEPTABLE":
                        acceptable += 1
                    elif state == "STALE":
                        stale += 1
                    else:
                        failed += 1
            states.append({"reviewer_id": reviewer, "state": state})
        counts = {"required": len(reviewers), "completed": completed, "acceptable": acceptable,
                  "failed": failed, "stale": stale, "outstanding": len(reviewers) - completed}
        ready = current and cycle["state"] == "COLLECTING" and acceptable == len(reviewers)
        return {"schema_version": 3, "cycle_id": cycle["cycle_id"], "state": cycle["state"],
                "current": current, "ready": ready, "tuple": loads(cycle["tuple_json"]),
                "tuple_sha256": cycle["tuple_sha256"], "required_reviewers": reviewers,
                "reviewer_set_sha256": cycle["reviewer_set_sha256"], "counts": counts,
                "reviewers": states, "completion_snapshot_sha256": cycle["completion_snapshot_sha256"]}

    def status(self, candidate=None, reviewers=None):
        """Return counts and invalidate a collecting aggregate on observed movement."""
        binding = _binding(candidate, reviewers) if candidate is not None or reviewers is not None else None
        _require(binding is not None or (candidate is None and reviewers is None),
                 "Candidate and reviewer set must be supplied together")
        with self.transaction() as db:
            cycle = self._active(db)
            current = binding is None or self._matches(cycle, binding)
            if not current and cycle["state"] == "COLLECTING":
                db.execute("UPDATE review_cycles SET state='INVALIDATED',invalidation_reason=? WHERE cycle_id=?",
                           ("candidate or required reviewer set moved", cycle["cycle_id"]))
                cycle = self._active(db)
            elif not current and cycle["state"] == "SUBMITTING":
                db.execute("UPDATE review_cycles SET state='SUBMISSION_UNKNOWN',invalidation_reason=? WHERE cycle_id=?",
                           ("candidate or reviewer set moved during provider submission", cycle["cycle_id"]))
                cycle = self._active(db)
            return self._summary(db, cycle, current=current)

    def prepare_submission(self, candidate, reviewers, aggregate):
        """Atomically capture the complete set and immutable final aggregate."""
        binding = _binding(candidate, reviewers)
        aggregate_json = canonical(aggregate).decode()
        _require(binding["tuple"]["review_input_sha256"] == fingerprint("review-input", aggregate),
                 "Final review aggregate differs from the frozen review-input digest")
        with self.transaction() as db:
            cycle = self._active(db)
            _require(self._matches(cycle, binding), "Candidate or reviewer set moved; aggregate is invalid")
            _require(cycle["state"] == "COLLECTING", "Final submission is already prepared or closed")
            summary = self._summary(db, cycle, current=True)
            _require(summary["counts"]["acceptable"] == summary["counts"]["required"] and
                     summary["counts"]["failed"] == 0 and summary["counts"]["stale"] == 0 and
                     summary["counts"]["outstanding"] == 0,
                     "Every frozen independent reviewer must complete acceptably before final submission")
            result_rows = db.execute("SELECT reviewer_id,state,result_sha256,terminal_at FROM reviewer_results WHERE cycle_id=? ORDER BY reviewer_id",
                                     (cycle["cycle_id"],)).fetchall()
            snapshot = {"cycle_id": cycle["cycle_id"], "tuple": summary["tuple"],
                        "tuple_sha256": summary["tuple_sha256"], "required_reviewers": summary["required_reviewers"],
                        "reviewer_set_sha256": summary["reviewer_set_sha256"], "counts": summary["counts"],
                        "results": [dict(row) for row in result_rows]}
            snapshot_json = canonical(snapshot).decode()
            snapshot_hash = fingerprint("review-completion", snapshot)
            aggregate_hash = fingerprint("review-aggregate", aggregate)
            submission_id = str(uuid.uuid4())
            prepared_at = now_text()
            db.execute("UPDATE review_cycles SET state='SUBMITTING',completion_snapshot_json=?,completion_snapshot_sha256=?,aggregate_json=?,aggregate_sha256=?,submission_id=?,submission_prepared_at=? WHERE cycle_id=?",
                       (snapshot_json, snapshot_hash, aggregate_json, aggregate_hash,
                        submission_id, prepared_at, cycle["cycle_id"]))
            return self._durable_admission(db, self._active(db))

    @staticmethod
    def _durable_admission(db, cycle):
        """Reconstruct and reprove the only admission owned by durable state."""
        _require(all(cycle[field] is not None for field in (
            "submission_id", "submission_prepared_at", "completion_snapshot_json",
            "completion_snapshot_sha256", "aggregate_json", "aggregate_sha256")),
            "Durable review admission is incomplete")
        tuple_value = loads(cycle["tuple_json"])
        reviewers = loads(cycle["reviewers_json"])
        snapshot = loads(cycle["completion_snapshot_json"])
        admission = {
            "submission_id": cycle["submission_id"], "cycle_id": cycle["cycle_id"],
            "completion_snapshot": snapshot,
            "completion_snapshot_sha256": cycle["completion_snapshot_sha256"],
            "aggregate": loads(cycle["aggregate_json"]),
            "aggregate_sha256": cycle["aggregate_sha256"],
            "provider_preconditions": {
                "operation_id": cycle["submission_id"],
                "prepared_at": cycle["submission_prepared_at"],
                "repository": tuple_value["repository"], "base_sha": tuple_value["base_sha"],
                "head_sha": tuple_value["head_sha"], "head_tree_sha": tuple_value["head_tree_sha"],
                "tuple_sha256": cycle["tuple_sha256"],
                "reviewer_set_sha256": cycle["reviewer_set_sha256"],
                "completion_snapshot_sha256": cycle["completion_snapshot_sha256"],
                "aggregate_sha256": cycle["aggregate_sha256"]},
            "execution_authority": False}
        validate_submission_semantics(admission)
        _require(snapshot["tuple"] == tuple_value and
                 snapshot["tuple_sha256"] == cycle["tuple_sha256"],
                 "Durable completion snapshot differs from its frozen tuple")
        _require(snapshot["required_reviewers"] == reviewers and
                 snapshot["reviewer_set_sha256"] == cycle["reviewer_set_sha256"],
                 "Durable completion snapshot differs from its frozen reviewer set")
        expected_results = [dict(row) for row in db.execute(
            "SELECT reviewer_id,state,result_sha256,terminal_at FROM reviewer_results "
            "WHERE cycle_id=? ORDER BY reviewer_id", (cycle["cycle_id"],)).fetchall()]
        _require(snapshot["results"] == expected_results,
                 "Durable completion snapshot differs from persisted reviewer results")
        return admission

    @staticmethod
    def _provider_receipt(admission, provider_receipt):
        _require(isinstance(provider_receipt, dict) and set(provider_receipt) == {
            "status", "operation_id", "repository", "base_sha", "head_sha", "head_tree_sha",
            "tuple_sha256", "reviewer_set_sha256", "completion_snapshot_sha256",
            "aggregate_sha256", "prepared_at", "observed_at"},
            "Provider receipt must be the exact bound submission receipt")
        _require(provider_receipt["status"] == "SUBMITTED", "Provider did not confirm submission")
        try:
            operation_id = uuid.UUID(provider_receipt["operation_id"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError("Provider receipt operation_id must be a UUID") from exc
        _require(str(operation_id) == provider_receipt["operation_id"],
                 "Provider receipt operation_id must be canonical")
        expected = admission["provider_preconditions"]
        for field in ("operation_id", "prepared_at", "repository", "base_sha", "head_sha",
                      "head_tree_sha", "tuple_sha256", "reviewer_set_sha256",
                      "completion_snapshot_sha256", "aggregate_sha256"):
            _require(provider_receipt[field] == expected[field],
                     f"Provider receipt {field} differs from the admitted submission")
        _require(timestamp(provider_receipt["observed_at"]) >= timestamp(expected["prepared_at"]),
                 "Provider receipt observation predates the durable submission admission")
        return canonical(provider_receipt).decode()

    def complete_submission(self, admission, provider_receipt):
        """Persist the provider receipt without permitting aggregate replacement."""
        validate_submission_semantics(admission)
        with self.transaction() as db:
            cycle = self._active(db)
            _require(cycle["state"] == "SUBMITTING",
                     "Submission admission is stale, uncertain, or already consumed")
            durable = self._durable_admission(db, cycle)
            _require(canonical(admission) == canonical(durable),
                     "Caller admission differs from the immutable durable admission")
            receipt_json = self._provider_receipt(durable, provider_receipt)
            db.execute("UPDATE review_cycles SET state='SUBMITTED',provider_receipt_json=? WHERE cycle_id=?",
                       (receipt_json, cycle["cycle_id"]))
            return self._summary(db, self._active(db), current=True)

    def mark_submission_unknown(self, submission_id, reason):
        _require(isinstance(reason, str) and reason.strip(), "Unknown submission needs a reason")
        with self.transaction() as db:
            cycle = self._active(db)
            _require(cycle["state"] == "SUBMITTING" and cycle["submission_id"] == submission_id,
                     "Submission is not in flight")
            db.execute("UPDATE review_cycles SET state='SUBMISSION_UNKNOWN',invalidation_reason=? WHERE cycle_id=?",
                       (reason[:1000], cycle["cycle_id"]))

    def submit(self, candidate, reviewers, aggregate, provider_mutation, provider_observation):
        """Call a tuple-preconditioned provider mutation once completion is frozen.

        The adapter must enforce ``provider_preconditions`` at the provider.
        Failure is recorded as uncertain and is never automatically retried.
        """
        _require(callable(provider_mutation) and callable(provider_observation),
                 "Provider mutation and independent observation adapters must be callable")
        admission = self.prepare_submission(candidate, reviewers, aggregate)
        try:
            mutation_intent = provider_mutation(admission)
            receipt = provider_observation(admission, mutation_intent)
        except BaseException as exc:
            self.mark_submission_unknown(admission["submission_id"], type(exc).__name__)
            raise
        try:
            return self.complete_submission(admission, receipt)
        except BaseException as exc:
            self.mark_submission_unknown(admission["submission_id"], type(exc).__name__)
            raise

    def reconcile_submission(self, admission, provider_receipt):
        """Close an uncertain operation only from a fresh bound provider observation."""
        validate_submission_semantics(admission)
        with self.transaction() as db:
            cycle = self._active(db)
            _require(cycle["state"] == "SUBMISSION_UNKNOWN",
                     "Only the matching uncertain submission can be reconciled")
            durable = self._durable_admission(db, cycle)
            _require(canonical(admission) == canonical(durable),
                     "Caller admission differs from the immutable durable admission")
            receipt_json = self._provider_receipt(durable, provider_receipt)
            db.execute("UPDATE review_cycles SET state='SUBMITTED',provider_receipt_json=? WHERE cycle_id=?",
                       (receipt_json, cycle["cycle_id"]))
            return self._summary(db, self._active(db), current=True)

    def recover(self):
        """On restart, preserve all results and make an in-flight provider call uncertain."""
        with self.transaction() as db:
            cycle = self._active(db)
            if cycle["state"] == "SUBMITTING":
                db.execute("UPDATE review_cycles SET state='SUBMISSION_UNKNOWN',invalidation_reason=? WHERE cycle_id=?",
                           ("controller restarted before provider receipt was recorded", cycle["cycle_id"]))
                cycle = self._active(db)
            return self._summary(db, cycle, current=True)

    def submitted(self):
        """Return the immutable submitted aggregate and receipt for reconciliation."""
        with self.connection() as db:
            cycle = self._active(db)
            return {"state": cycle["state"], "submission_id": cycle["submission_id"],
                    "completion_snapshot_sha256": cycle["completion_snapshot_sha256"],
                    "aggregate": loads(cycle["aggregate_json"]) if cycle["aggregate_json"] else None,
                    "aggregate_sha256": cycle["aggregate_sha256"],
                    "provider_receipt": loads(cycle["provider_receipt_json"]) if cycle["provider_receipt_json"] else None}


def validate_completion_semantics(value):
    """Validate cross-field invariants not expressible as simple JSON types."""
    _require(isinstance(value, dict), "Review completion must be an object")
    reviewers = value.get("reviewers")
    required = value.get("required_reviewers")
    counts = value.get("counts")
    _require(isinstance(reviewers, list) and isinstance(required, list) and isinstance(counts, dict),
             "Review completion is missing reviewer semantics")
    _require([row.get("reviewer_id") for row in reviewers] == required,
             "Reviewer rows must exactly match the sorted frozen reviewer set")
    _require(required == sorted(required), "Frozen reviewer set must use deterministic sorted order")
    _require(value.get("tuple_sha256") == fingerprint("review-tuple", value.get("tuple")),
             "Review tuple digest does not match the tuple")
    _require(value.get("reviewer_set_sha256") == fingerprint("reviewer-set", required),
             "Reviewer-set digest does not match the frozen reviewer set")
    states = [row.get("state") for row in reviewers]
    terminal = [state for state in states if state not in {"MISSING", "RUNNING"}]
    expected = {"required": len(states), "completed": len(terminal),
                "acceptable": states.count("ACCEPTABLE"),
                "failed": sum(state in {"FAILED", "TIMED_OUT", "MALFORMED", "CANCELLED", "DUPLICATE"} for state in states),
                "stale": states.count("STALE"), "outstanding": len(states) - len(terminal)}
    _require(counts == expected, "Reviewer counts contradict reviewer states")
    ready = (value.get("current") is True and value.get("state") == "COLLECTING" and
             expected["acceptable"] == expected["required"])
    _require(value.get("ready") is ready, "Review ready flag contradicts frozen reviewer completion")
    return value


def validate_submission_semantics(value):
    """Fully validate one immutable, provider-bound review submission."""
    top_fields = {"submission_id", "cycle_id", "completion_snapshot",
                  "completion_snapshot_sha256", "aggregate", "aggregate_sha256",
                  "provider_preconditions", "execution_authority"}
    _require(isinstance(value, dict) and set(value) == top_fields,
             "Review submission must be an exact object")
    for field in ("submission_id", "cycle_id"):
        try:
            parsed = uuid.UUID(value[field])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError(f"Review submission {field} must be a UUID") from exc
        _require(str(parsed) == value[field], f"Review submission {field} must be canonical")
    snapshot = value["completion_snapshot"]
    snapshot_fields = {"cycle_id", "tuple", "tuple_sha256", "required_reviewers",
                       "reviewer_set_sha256", "counts", "results"}
    _require(isinstance(snapshot, dict) and set(snapshot) == snapshot_fields,
             "Completion snapshot must be an exact object")
    _candidate(snapshot["tuple"])
    required = _reviewers(snapshot["required_reviewers"])
    _require(snapshot["required_reviewers"] == required,
             "Submission reviewers must use deterministic sorted order")
    results = snapshot["results"]
    _require(isinstance(results, list) and len(results) == len(required),
             "Submission needs one result for every frozen reviewer")
    _require(all(isinstance(row, dict) and set(row) == {
        "reviewer_id", "state", "result_sha256", "terminal_at"} for row in results),
        "Submission reviewer results must be exact objects")
    _require([row["reviewer_id"] for row in results] == required,
             "Submission results must exactly match the sorted frozen reviewer set")
    _require(value["cycle_id"] == snapshot["cycle_id"],
             "Submission cycle differs from the completion snapshot")
    _require(all(row["state"] == "ACCEPTABLE" and isinstance(row["result_sha256"], str) and
                 DIGEST.fullmatch(row["result_sha256"]) is not None for row in results),
             "Submission contains a non-acceptable reviewer result")
    result_times = [timestamp(row["terminal_at"]) for row in results]
    counts = snapshot["counts"]
    _require(counts == {"required": len(required), "completed": len(required),
                        "acceptable": len(required), "failed": 0, "stale": 0, "outstanding": 0},
             "Submission counts do not prove complete acceptable review")
    preconditions = value["provider_preconditions"]
    precondition_fields = {"operation_id", "prepared_at", "repository", "base_sha", "head_sha",
                           "head_tree_sha", "tuple_sha256", "reviewer_set_sha256",
                           "completion_snapshot_sha256", "aggregate_sha256"}
    _require(isinstance(preconditions, dict) and set(preconditions) == precondition_fields,
             "Provider preconditions must be an exact durable binding")
    _require(preconditions["operation_id"] == value["submission_id"],
             "Provider operation identity differs from the durable submission identity")
    prepared_at = timestamp(preconditions["prepared_at"])
    _require(all(observed <= prepared_at for observed in result_times),
             "Submission preparation predates a frozen reviewer result")
    for field in ("repository", "base_sha", "head_sha", "head_tree_sha"):
        _require(preconditions[field] == snapshot["tuple"][field],
                 f"Provider precondition {field} differs from the completion tuple")
    _require(preconditions["tuple_sha256"] == snapshot["tuple_sha256"] and
             preconditions["reviewer_set_sha256"] == snapshot["reviewer_set_sha256"] and
             preconditions["completion_snapshot_sha256"] == value["completion_snapshot_sha256"] and
             preconditions["aggregate_sha256"] == value["aggregate_sha256"],
             "Provider precondition digests differ from the admitted review evidence")
    _require(snapshot["tuple_sha256"] == fingerprint("review-tuple", snapshot["tuple"]) and
             snapshot["reviewer_set_sha256"] == fingerprint("reviewer-set", required),
             "Submission tuple or reviewer-set digest is invalid")
    _require(value["completion_snapshot_sha256"] == fingerprint("review-completion", snapshot),
             "Completion snapshot digest is invalid")
    _require(value["aggregate_sha256"] == fingerprint("review-aggregate", value["aggregate"]),
             "Review aggregate digest is invalid")
    _require(value["execution_authority"] is False,
             "Review submission record cannot grant execution authority")
    return value


def gate_review_aggregate(candidate, critic, specialists):
    """Return the only aggregate shape accepted by the final-gate route.

    The completion ledger proves that the frozen reviewers all terminated
    acceptably.  This aggregate additionally binds that proof to the concrete
    critic/specialist records consumed by the gate, so a completion admission
    from another evidence bundle cannot be replayed.
    """
    reviewers = sorted([critic["producer_id"], *[row["producer_id"] for row in specialists]])
    _require(len(reviewers) == len(set(reviewers)),
             "Final-gate reviewers must have unique producer identities")
    return {
        "candidate_id": fingerprint("candidate", candidate),
        "critic_record_id": critic["record_id"],
        "critic_sha256": fingerprint("critic-review", critic),
        "specialist_record_ids": sorted(row["record_id"] for row in specialists),
        "specialist_sha256": sorted(fingerprint("specialist-review", row) for row in specialists),
        "required_reviewers": reviewers,
    }


def gate_review_tuple(candidate, contract, aggregate):
    """Build the frozen tuple expected by final-gate evaluation."""
    return {
        "repository": candidate["repository"],
        "base_sha": candidate["target_base_sha"],
        "head_sha": candidate["head_sha"],
        "head_tree_sha": candidate["head_tree_sha"],
        "contract_sha256": fingerprint("contract", contract),
        "review_input_sha256": fingerprint("review-input", aggregate),
    }


def validate_gate_submission(value, candidate, contract, critic, specialists):
    """Validate an atomic completion admission against final-gate inputs."""
    validate_submission_semantics(value)
    aggregate = gate_review_aggregate(candidate, critic, specialists)
    _require(value.get("aggregate") == aggregate,
             "Review-completion aggregate differs from the final-gate review records")
    snapshot = value["completion_snapshot"]
    _require(snapshot.get("required_reviewers") == aggregate["required_reviewers"],
             "Frozen reviewer set differs from the final-gate reviewers")
    expected_tuple = gate_review_tuple(candidate, contract, aggregate)
    _require(snapshot.get("tuple") == expected_tuple,
             "Review-completion tuple differs from the final-gate candidate or contract")
    return value


def validate_ready_gate_completion(gate):
    """Fail closed when a readiness/authorization route lacks atomic review proof.

    This portable guard is deliberately usable by lifecycle, interaction and
    authorization code that does not own a schema registry.  Full gate
    construction performs the stronger cross-record check above.
    """
    _require(isinstance(gate, dict) and gate.get("conclusion") == "READY_FOR_OWNER_AUTHORIZATION",
             "Final gate is not ready for owner authorization")
    submission = gate.get("review_submission")
    validate_submission_semantics(submission)
    candidate = gate.get("candidate", {})
    tuple_value = submission["completion_snapshot"]["tuple"]
    for gate_field, tuple_field in (("repository", "repository"),
                                    ("target_base_sha", "base_sha"),
                                    ("head_sha", "head_sha"),
                                    ("head_tree_sha", "head_tree_sha")):
        _require(candidate.get(gate_field) == tuple_value.get(tuple_field),
                 f"Final-gate candidate {gate_field} differs from completion admission")
    aggregate = submission["aggregate"]
    binding = gate.get("binding", {})
    _require(binding.get("candidate_id") == fingerprint("candidate", candidate) == aggregate.get("candidate_id"),
             "Final-gate candidate identity differs from completion aggregate")
    _require(binding.get("contract_hash") == tuple_value.get("contract_sha256"),
             "Final-gate contract identity differs from completion tuple")
    _require(tuple_value.get("review_input_sha256") == fingerprint("review-input", aggregate),
             "Completion review-input digest differs from its aggregate")
    record_ids = set(gate.get("record_ids", []))
    _require(aggregate.get("critic_record_id") in record_ids and
             set(aggregate.get("specialist_record_ids", [])) <= record_ids,
             "Completion aggregate names review records outside the final gate")
    _require(aggregate.get("required_reviewers") ==
             submission["completion_snapshot"].get("required_reviewers"),
             "Completion aggregate reviewer set differs from its frozen snapshot")
    return submission
