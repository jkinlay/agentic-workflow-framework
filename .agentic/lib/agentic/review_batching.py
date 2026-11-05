"""Candidate-bound routine human-review intents in protected controller state.

This module never sends, dispatches, enables reporting, authenticates an owner,
evaluates merge authorization or changes lifecycle state. The caller supplies a
trusted fresh observer and verified delivery readback through reviewed adapters.
Uncertain sends remain claimed across restart until explicit reconciliation.
"""
from __future__ import annotations

from copy import deepcopy
import re

from . import ValidationError
from .canonical import canonical, fingerprint, loads
from .operating_calendar import next_review_presentation, utc_instant, utc_text


_CANDIDATE_KEYS = {"repository_id", "pull_request", "head_sha", "base_sha",
                   "head_tree_sha", "final_gate_sha256"}
_OBSERVATION_KEYS = {"candidate", "observed_at", "expires_at", "open", "merged",
                     "readiness", "final_gate_valid"}
_RECEIPT_KEYS = {"batch_id", "payload_sha256", "status", "observed_at", "provider_receipt_id"}
_DDL = (
    """CREATE TABLE IF NOT EXISTS review_presentation_intent_v1(
        intent_id TEXT PRIMARY KEY, candidate_json TEXT NOT NULL,
        ready_at TEXT NOT NULL, initial_schedule_json TEXT NOT NULL,
        state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS review_presentation_outbox_v1(
        batch_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
        state TEXT NOT NULL, receipt_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS review_presentation_receipt_v1(
        provider_receipt_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS review_presentation_check_v1(
        batch_id TEXT NOT NULL, checked_at TEXT NOT NULL,
        observation_json TEXT NOT NULL, PRIMARY KEY(batch_id,checked_at))""",
)


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _candidate(value):
    _require(isinstance(value, dict) and set(value) == _CANDIDATE_KEYS,
             "Review candidate must carry the exact repository/PR/head/base/tree/final-gate identity")
    for key in ("repository_id", "pull_request"):
        _require(type(value[key]) is int and value[key] > 0, "Review candidate IDs must be positive integers")
    for key in ("head_sha", "base_sha", "head_tree_sha", "final_gate_sha256"):
        length = 64 if key == "final_gate_sha256" else 40
        _require(isinstance(value[key], str) and re.fullmatch("[0-9a-f]{%d}" % length, value[key]),
                 "Review candidate has an invalid " + key)
    return deepcopy(value)


def _identity(value):
    return fingerprint("review-presentation-intent-v1", _candidate(value))


def _observation_valid(value, candidate, now):
    """A trusted collector must observe at the tick instant, with a live expiry.

    No caller-selected age tolerance: stale observations fail closed. The adapter
    must independently validate current final-gate bytes, not echo a stored bool.
    """
    if not isinstance(value, dict) or set(value) != _OBSERVATION_KEYS:
        return False
    try:
        same = _candidate(value["candidate"]) == candidate
        observed = utc_instant(value["observed_at"])
        expiry = utc_instant(value["expires_at"])
    except (ValidationError, TypeError, ValueError):
        return False
    return (same and observed == now and now < expiry and
            value["open"] is True and value["merged"] is False and
            value["readiness"] == "READY_FOR_OWNER_AUTHORIZATION" and
            value["final_gate_valid"] is True)


class ReviewBatchStore:
    """Use an existing ContinuousControllerStore's protected transaction API.

    Review outbox tables are deliberately separate from controller_outbox: the
    existing status sender must never accidentally pick up a review digest.
    Schema v1 table names avoid implying a controller-wide migration.
    """

    def __init__(self, controller_store):
        self.controller = controller_store
        with self.controller.transaction() as db:
            for statement in _DDL:
                db.execute(statement)

    def enqueue(self, candidate, ready_at, calendar):
        """Retain first readiness and original schedule; identical events dedup.

        A new tuple withdraws pending older tuples for the same repository/PR.
        Withdrawn identities need fresh evidence/new tuple; replay cannot revive
        them. Already claimed presentations require provider reconciliation.
        """
        candidate = _candidate(candidate)
        identity = _identity(candidate)
        schedule = next_review_presentation(ready_at, calendar)
        with self.controller.transaction() as db:
            existing = db.execute("SELECT intent_id FROM review_presentation_intent_v1 WHERE intent_id=?",
                                  (identity,)).fetchone()
            if existing is not None:
                return identity
            for row in db.execute("SELECT intent_id,candidate_json FROM review_presentation_intent_v1 WHERE state='PENDING'").fetchall():
                prior = loads(row["candidate_json"])
                if (prior["repository_id"], prior["pull_request"]) == (candidate["repository_id"], candidate["pull_request"]):
                    db.execute("UPDATE review_presentation_intent_v1 SET state='WITHDRAWN' WHERE intent_id=?",
                               (row["intent_id"],))
            db.execute("INSERT INTO review_presentation_intent_v1 VALUES(?,?,?,?,?,0)",
                       (identity, canonical(candidate).decode(), schedule["ready_at"],
                        canonical(schedule).decode(), "PENDING"))
        return identity

    def claim(self, now, calendar, observer):
        """Recompute pending eligibility and atomically claim one due digest.

        The trusted observer runs inside the serialized transaction. An exception
        rolls back the tick; invalid/stale/closed members withdraw. A missed run
        catches up during a current presentation window, never after cutoff.
        A pending intent whose schedule cannot resolve stays pending without
        blocking other intents; later settings may make it resolvable again.
        No lease timeout can retry an ambiguous provider send.
        """
        _require(callable(observer), "Fresh review candidate observer is required")
        current = utc_instant(now)
        window = next_review_presentation(now, calendar)
        if utc_instant(window["presentation_at"]) != current:
            return None
        with self.controller.transaction() as db:
            if db.execute("SELECT 1 FROM review_presentation_outbox_v1 WHERE state='CLAIMED'").fetchone():
                return None
            members = []
            rows = db.execute("SELECT * FROM review_presentation_intent_v1 WHERE state='PENDING' ORDER BY intent_id").fetchall()
            for row in rows:
                candidate = _candidate(loads(row["candidate_json"]))
                _require(_identity(candidate) == row["intent_id"], "Stored review intent identity mismatch")
                try:
                    schedule = next_review_presentation(row["ready_at"], calendar)
                except ValidationError:
                    # Keep the intent and its attempt count for later settings
                    # recovery. Identity and observer failures still refuse the
                    # transaction; only per-intent scheduling is isolated.
                    continue
                if utc_instant(schedule["presentation_at"]) > current:
                    continue
                observation = observer(deepcopy(candidate))
                if not _observation_valid(observation, candidate, current):
                    db.execute("UPDATE review_presentation_intent_v1 SET state='WITHDRAWN' WHERE intent_id=?",
                               (row["intent_id"],))
                    continue
                members.append({"intent_id": row["intent_id"], "candidate": candidate,
                                "ready_at": row["ready_at"], "scheduled_at": schedule["presentation_at"],
                                "initial_schedule": loads(row["initial_schedule_json"]),
                                "observation": deepcopy(observation), "attempt": row["attempts"] + 1})
            if not members:
                return None
            body = {"kind": "routine-final-human-review", "version": 1,
                    "prepared_at": utc_text(current), "calendar_sha256": window["calendar_sha256"],
                    "members": members, "catch_up": any(utc_instant(item["scheduled_at"]) < current for item in members),
                    "merge_authorized": False}
            batch_id = fingerprint("review-presentation-batch-v1", body)
            body["batch_id"] = batch_id
            body["payload_sha256"] = fingerprint("review-presentation-payload-v1", body)
            db.execute("INSERT INTO review_presentation_outbox_v1 VALUES(?,?,'CLAIMED',NULL)",
                       (batch_id, canonical(body).decode()))
            for member in members:
                db.execute("UPDATE review_presentation_intent_v1 SET state='CLAIMED',attempts=attempts+1 WHERE intent_id=?",
                           (member["intent_id"],))
            return body

    @staticmethod
    def _payload(row):
        value = loads(row["payload_json"])
        pin = value.pop("payload_sha256", None)
        _require(pin == fingerprint("review-presentation-payload-v1", value), "Review payload digest mismatch")
        identity = value.pop("batch_id", None)
        _require(identity == row["batch_id"] == fingerprint("review-presentation-batch-v1", value),
                 "Review batch identity mismatch")
        return {**value, "batch_id": identity, "payload_sha256": pin}

    def recheck(self, batch_id, now, calendar, observer):
        """Mandatory immediate pre-send recheck; never returns a changed payload.

        A refusal leaves an unresolved claim. Only verified provider readback
        (including confirmed NOT_DELIVERED) resolves it, so an earlier ambiguous
        send cannot be accidentally reissued. The sender must not cache a permit.
        """
        _require(callable(observer), "Fresh review candidate observer is required")
        current = utc_instant(now)
        window = next_review_presentation(now, calendar)
        with self.controller.transaction() as db:
            row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            _require(row is not None and row["state"] == "CLAIMED", "Review batch is not claimed")
            payload = self._payload(row)
            _require(current >= utc_instant(payload["prepared_at"]) and
                     utc_instant(window["presentation_at"]) == current and
                     window["calendar_sha256"] == payload["calendar_sha256"],
                     "Review batch calendar changed or presentation window is closed")
            observations = []
            for member in payload["members"]:
                observation = observer(deepcopy(member["candidate"]))
                _require(_observation_valid(observation, member["candidate"], current),
                         "Review candidate is no longer current and ready")
                observations.append({"intent_id": member["intent_id"], "observation": observation})
            encoded = canonical(observations).decode()
            prior = db.execute("SELECT observation_json FROM review_presentation_check_v1 WHERE batch_id=? AND checked_at=?",
                               (batch_id, utc_text(current))).fetchone()
            _require(prior is None or prior[0] == encoded, "Conflicting review observations at the same instant")
            db.execute("INSERT OR IGNORE INTO review_presentation_check_v1 VALUES(?,?,?)",
                       (batch_id, utc_text(current), encoded))
            return payload

    def pending_batches(self):
        """Read unresolved identities for provider readback, never for resending."""
        with self.controller.transaction() as db:
            return [self._payload(row) for row in db.execute(
                "SELECT * FROM review_presentation_outbox_v1 WHERE state='CLAIMED' ORDER BY batch_id")]

    def acknowledge(self, receipt):
        _require(isinstance(receipt, dict) and receipt.get("status") == "DELIVERED",
                 "Review acknowledgement requires verified DELIVERED readback")
        return self.reconcile(receipt)

    def reconcile(self, receipt):
        """Persist verified readback; NOT_DELIVERED allows at most one retry.

        The caller must authenticate provider receipt identity and content. This
        pure persistence boundary cannot turn supplied JSON into trusted evidence.
        Conflicting readback refuses; identical readback is idempotent.
        """
        _require(isinstance(receipt, dict) and set(receipt) == _RECEIPT_KEYS,
                 "Review delivery receipt is incomplete or has unknown fields")
        _require(receipt["status"] in ("DELIVERED", "NOT_DELIVERED") and
                 isinstance(receipt["provider_receipt_id"], str) and bool(receipt["provider_receipt_id"].strip()),
                 "Review receipt requires verified terminal provider readback")
        observed = utc_instant(receipt["observed_at"])
        with self.controller.transaction() as db:
            row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (receipt["batch_id"],)).fetchone()
            _require(row is not None, "Unknown review batch receipt")
            payload = self._payload(row)
            _require(payload["payload_sha256"] == receipt["payload_sha256"] and
                     observed >= utc_instant(payload["prepared_at"]), "Review receipt binding/time mismatch")
            encoded = canonical(receipt).decode()
            if row["receipt_json"] is not None:
                _require(row["receipt_json"] == encoded, "Conflicting review batch readback; stop and reconcile")
                return
            _require(row["state"] == "CLAIMED", "Review batch has no unresolved claim")
            _require(db.execute("SELECT 1 FROM review_presentation_receipt_v1 WHERE provider_receipt_id=?",
                                (receipt["provider_receipt_id"],)).fetchone() is None,
                     "Provider review receipt identity was reused; stop and reconcile")
            db.execute("INSERT INTO review_presentation_receipt_v1 VALUES(?,?)",
                       (receipt["provider_receipt_id"], receipt["batch_id"]))
            db.execute("UPDATE review_presentation_outbox_v1 SET state=?,receipt_json=? WHERE batch_id=?",
                       (receipt["status"], encoded, receipt["batch_id"]))
            for member in payload["members"]:
                state = "DELIVERED" if receipt["status"] == "DELIVERED" else (
                    "EXHAUSTED" if member["attempt"] >= 2 else "PENDING")
                # A newer head must not be displaced by a failed older send.
                if state == "PENDING":
                    for other in db.execute("SELECT candidate_json FROM review_presentation_intent_v1 WHERE state='PENDING'"):
                        candidate = loads(other["candidate_json"])
                        old = member["candidate"]
                        if (candidate["repository_id"], candidate["pull_request"]) == (old["repository_id"], old["pull_request"]):
                            state = "WITHDRAWN"
                            break
                db.execute("UPDATE review_presentation_intent_v1 SET state=? WHERE intent_id=? AND state='CLAIMED'",
                           (state, member["intent_id"]))
