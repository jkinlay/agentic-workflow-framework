"""Durable all-reviewer barrier for final review submission.

The barrier records coordination state only.  It neither dispatches agents nor
grants merge, Jira, or provider authority.  A provider adapter may be called
only after :meth:`ReviewCompletionStore.prepare_submission` has atomically
captured a complete, acceptable result set for one frozen candidate tuple.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import uuid

from . import ValidationError
from .canonical import canonical, fingerprint, loads, now_text


TUPLE_FIELDS = (
    "repository",
    "base_sha",
    "head_sha",
    "head_tree_sha",
    "contract_sha256",
    "review_input_sha256",
)
TERMINAL_OUTCOMES = {"ACCEPTABLE", "FAILED", "TIMED_OUT", "MALFORMED"}
FINAL_STATES = {"SUBMITTED", "SUBMISSION_UNKNOWN"}
SHA40 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")


DDL = """
CREATE TABLE IF NOT EXISTS review_cycles(
 cycle_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, tuple_json TEXT NOT NULL,
 tuple_sha256 TEXT NOT NULL, reviewers_json TEXT NOT NULL,
 reviewer_set_sha256 TEXT NOT NULL, state TEXT NOT NULL,
 completion_snapshot_json TEXT, completion_snapshot_sha256 TEXT,
 aggregate_json TEXT, aggregate_sha256 TEXT, submission_id TEXT UNIQUE,
 provider_receipt_json TEXT, invalidation_reason TEXT);
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
"""


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


def _binding(candidate, reviewers):
    candidate, reviewers = _candidate(candidate), _reviewers(reviewers)
    return {
        "tuple": candidate,
        "tuple_sha256": fingerprint("review-tuple", candidate),
        "required_reviewers": reviewers,
        "reviewer_set_sha256": fingerprint("reviewer-set", reviewers),
    }


class ReviewCompletionStore:
    """SQLite ledger that closes the race between review completion and submit.

    The database belongs in protected controller state outside worker and
    reviewer worktrees.  Each public mutation uses ``BEGIN IMMEDIATE``.  The
    submitted aggregate and its completion snapshot are immutable thereafter.
    """

    def __init__(self, path, worktree_roots=()):
        self.path = Path(path).absolute()
        for parent in (self.path, *self.path.parents):
            if parent.exists() and (parent.is_symlink() or
                    (hasattr(parent, "is_junction") and parent.is_junction())):
                raise ValidationError("Review state path traverses a link/reparse point")
        if self.path.exists() and self.path.stat().st_nlink > 1:
            raise ValidationError("Review state file has multiple hardlinks")
        for worktree in worktree_roots:
            if self.path.is_relative_to(Path(worktree).resolve()):
                raise ValidationError("Review completion state must be outside candidate worktrees")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(DDL)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
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
    def _active(db):
        row = db.execute("SELECT c.* FROM active_cycle a JOIN review_cycles c ON c.cycle_id=a.cycle_id WHERE a.singleton=1").fetchone()
        _require(row is not None, "No frozen reviewer cycle; freeze the complete set before dispatch")
        return row

    @staticmethod
    def _matches(row, binding):
        return row["tuple_sha256"] == binding["tuple_sha256"] and row["reviewer_set_sha256"] == binding["reviewer_set_sha256"]

    def freeze(self, candidate, reviewers):
        """Freeze the complete reviewer set before any reviewer dispatch.

        Repeating the exact freeze is idempotent.  A changed candidate or set
        invalidates a collecting/ready aggregate and creates a fresh cycle.
        An uncertain provider submission must be reconciled first.
        """
        binding = _binding(candidate, reviewers)
        with self.transaction() as db:
            active = db.execute("SELECT c.* FROM active_cycle a JOIN review_cycles c ON c.cycle_id=a.cycle_id WHERE a.singleton=1").fetchone()
            if active is not None and self._matches(active, binding) and active["state"] != "INVALIDATED":
                return self._summary(db, active, current=True)
            if active is not None:
                _require(active["state"] not in {"SUBMITTING", "SUBMISSION_UNKNOWN"},
                         "Reconcile the uncertain provider submission before freezing another cycle")
                if active["state"] != "SUBMITTED":
                    db.execute("UPDATE review_cycles SET state='INVALIDATED',invalidation_reason=? WHERE cycle_id=?",
                               ("candidate or required reviewer set moved", active["cycle_id"]))
            cycle_id = str(uuid.uuid4())
            created = now_text()
            db.execute("INSERT OR IGNORE INTO review_cycles(cycle_id,created_at,tuple_json,tuple_sha256,reviewers_json,reviewer_set_sha256,state) VALUES(?,?,?,?,?,?,'COLLECTING')",
                       (cycle_id, created, canonical(binding["tuple"]).decode(), binding["tuple_sha256"],
                        canonical(binding["required_reviewers"]).decode(), binding["reviewer_set_sha256"]))
            db.execute("INSERT INTO active_cycle VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET cycle_id=excluded.cycle_id", (cycle_id,))
            return self._summary(db, self._active(db), current=True)

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
        raw = canonical(result).decode()
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
            db.execute("UPDATE review_cycles SET state='SUBMITTING',completion_snapshot_json=?,completion_snapshot_sha256=?,aggregate_json=?,aggregate_sha256=?,submission_id=? WHERE cycle_id=?",
                       (snapshot_json, snapshot_hash, aggregate_json, aggregate_hash, submission_id, cycle["cycle_id"]))
            return {"submission_id": submission_id, "cycle_id": cycle["cycle_id"],
                    "completion_snapshot": snapshot, "completion_snapshot_sha256": snapshot_hash,
                    "aggregate": loads(aggregate_json), "aggregate_sha256": aggregate_hash,
                    "provider_preconditions": {"tuple_sha256": cycle["tuple_sha256"],
                                               "reviewer_set_sha256": cycle["reviewer_set_sha256"]},
                    "execution_authority": False}

    def complete_submission(self, admission, provider_receipt):
        """Persist the provider receipt without permitting aggregate replacement."""
        receipt_json = canonical(provider_receipt).decode()
        with self.transaction() as db:
            cycle = self._active(db)
            _require(cycle["state"] == "SUBMITTING" and cycle["submission_id"] == admission.get("submission_id"),
                     "Submission admission is stale, uncertain, or already consumed")
            _require(cycle["completion_snapshot_sha256"] == admission.get("completion_snapshot_sha256") and
                     cycle["aggregate_sha256"] == admission.get("aggregate_sha256"),
                     "Submission payload differs from the atomic completion snapshot")
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

    def submit(self, candidate, reviewers, aggregate, provider_mutation):
        """Call a tuple-preconditioned provider mutation once completion is frozen.

        The adapter must enforce ``provider_preconditions`` at the provider.
        Failure is recorded as uncertain and is never automatically retried.
        """
        _require(callable(provider_mutation), "Provider mutation adapter must be callable")
        admission = self.prepare_submission(candidate, reviewers, aggregate)
        try:
            receipt = provider_mutation(admission)
        except BaseException as exc:
            self.mark_submission_unknown(admission["submission_id"], type(exc).__name__)
            raise
        return self.complete_submission(admission, receipt)

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
