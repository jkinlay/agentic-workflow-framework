"""Candidate-bound routine human-review intents in protected controller state.

This module never sends, dispatches, enables reporting, authenticates an owner,
evaluates merge authorization or changes lifecycle state. The caller supplies a
trusted fresh observer and verified delivery readback through reviewed adapters.
Uncertain sends remain claimed across restart until explicit reconciliation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import re

from . import ValidationError
from .canonical import canonical, fingerprint, loads
from .operating_calendar import (next_review_presentation, utc_instant, utc_text,
                                 validate_calendar, _zone, _local_boundary)


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
    """CREATE TABLE IF NOT EXISTS review_presentation_phase_v1(
        batch_id TEXT PRIMARY KEY, phase TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS review_presentation_detail_v1(
        intent_id TEXT PRIMARY KEY, reason TEXT NOT NULL, changed_at TEXT NOT NULL,
        retry_at TEXT, window_attempts INTEGER NOT NULL)""",
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


def _observation_current(value, now):
    """Only a complete, fresh trusted observation can establish invalidation."""
    if not isinstance(value, dict) or set(value) not in (_OBSERVATION_KEYS, _OBSERVATION_KEYS | {"ready_at"}):
        return False
    try:
        _candidate(value["candidate"])
        observed = utc_instant(value["observed_at"])
        expiry = utc_instant(value["expires_at"])
        if "ready_at" in value and utc_instant(value["ready_at"]) > now:
            return False
    except (ValidationError, TypeError, ValueError):
        return False
    return (observed == now and now < expiry and
            type(value["open"]) is bool and type(value["merged"]) is bool and
            isinstance(value["readiness"], str) and type(value["final_gate_valid"]) is bool)


def _window(now, calendar):
    """Identify the current local window and the next bounded retry opening."""
    effective = validate_calendar(calendar)
    current = utc_instant(now)
    zone = _zone(effective["timezone"])
    day = current.astimezone(zone).date()
    review = effective["review_batching"]
    if review["enabled"]:
        opening = _local_boundary(day, review["presentation_time"], zone)
        closing = _local_boundary(day, review["cutoff_time"], zone)
        try:
            following = next_review_presentation(closing, effective)["presentation_at"]
        except ValidationError:
            following = None  # No representable future window, never retry.
    else:
        # Immediate presentation still has a finite daily retry allowance.
        opening = _local_boundary(day, "00:00", zone)
        try:
            closing = _local_boundary(day + timedelta(days=1), "00:00", zone)
            following = utc_text(closing)
        except OverflowError:
            following = None
    return {"opening": utc_text(opening), "next_opening": following}


def _observation_valid(value, candidate, now):
    """A trusted collector must observe at the tick instant, with a live expiry.

    No caller-selected age tolerance: stale observations fail closed. The adapter
    must independently validate current final-gate bytes, not echo a stored bool.
    """
    return (_observation_current(value, now) and value["candidate"] == candidate and
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

    @staticmethod
    def _changed(db):
        db.execute("UPDATE controller_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    @staticmethod
    def _detail(db, identity, reason, now, *, retry_at=None, attempts=None):
        prior = db.execute("SELECT * FROM review_presentation_detail_v1 WHERE intent_id=?", (identity,)).fetchone()
        count = attempts if attempts is not None else (prior['window_attempts'] if prior else 0)
        retry = retry_at if retry_at is not None else (prior['retry_at'] if prior else None)
        db.execute("INSERT OR REPLACE INTO review_presentation_detail_v1 VALUES(?,?,?,?,?)",
                   (identity, reason, utc_text(now), retry, count))

    @staticmethod
    def _attempt_windows(db, calendar):
        """Retained send evidence, keyed by the immutable presentation opening.

        Detail/retry times are diagnostic history, never calendar authority.
        PREPARED/UNSENT batches have no charge. Legacy terminal receipts prove
        non-ambiguity; preparation identifies their window under today's calendar,
        never a delayed receipt time. Unexplained charges stay charged.
        """
        counts, totals = {}, {}
        has_phase = db.execute("SELECT 1 FROM sqlite_master WHERE name='review_presentation_phase_v1'").fetchone()
        join = "LEFT JOIN review_presentation_phase_v1 p USING(batch_id) " if has_phase else ""
        started = "(p.phase='SENDING' OR p.phase IS NULL)" if has_phase else "1"
        for row in db.execute("SELECT o.* FROM review_presentation_outbox_v1 o " + join +
                              "WHERE o.state IN ('NOT_DELIVERED','DELIVERED') "
                              "OR (o.state='CLAIMED' AND " + started + ")"):
            payload = ReviewBatchStore._payload(row)
            opening = payload.get('presentation_window', {}).get('opening')
            if opening is None:
                if row['receipt_json'] is None:
                    continue  # Legacy UNKNOWN has no release/window evidence.
                opening = _window(payload['prepared_at'], calendar)['opening']
            opening = utc_text(opening)
            for member in payload['members']:
                identity = member['intent_id']
                counts[identity, opening] = counts.get((identity, opening), 0) + 1
                totals[identity] = totals.get(identity, 0) + 1
        return counts, totals

    @staticmethod
    def _window_count(row, opening, evidence):
        counts, totals = evidence
        identity = row['intent_id']
        return counts.get((identity, opening), 0) + max(0, row['attempts'] - totals.get(identity, 0))

    @staticmethod
    def view_from_db(db, now, calendar):
        """Read-only state for status/AWF-241; no provider calls or recovery."""
        result = {'intents': [], 'unresolved_batches': [], 'owner_alerts': []}
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='review_presentation_intent_v1'").fetchone():
            return result
        has_detail = db.execute("SELECT 1 FROM sqlite_master WHERE name='review_presentation_detail_v1'").fetchone()
        evidence = ReviewBatchStore._attempt_windows(db, calendar)
        terminal = ReviewBatchStore._reporting_failure_intents(db)
        for row in db.execute("SELECT * FROM review_presentation_intent_v1 ORDER BY intent_id"):
            detail = db.execute("SELECT * FROM review_presentation_detail_v1 WHERE intent_id=?", (row['intent_id'],)).fetchone() if has_detail else None
            try:
                schedule = next_review_presentation(row['ready_at'], calendar)
                held = (utc_instant(schedule['presentation_at']) > utc_instant(now) or
                        utc_instant(next_review_presentation(now, calendar)['presentation_at']) > utc_instant(now))
            except ValidationError:
                schedule, held = None, True
            retry = detail['retry_at'] if detail else None
            if row['intent_id'] in terminal:
                retry = None
            elif row['state'] == 'EXHAUSTED':
                try:
                    window = _window(now, calendar)
                    if schedule is None:
                        retry = None
                    elif ReviewBatchStore._window_count(row, window['opening'], evidence) >= 2:
                        retry = window['next_opening']
                    else:
                        retry = utc_text(max(utc_instant(schedule['presentation_at']),
                                             utc_instant(next_review_presentation(now, calendar)['presentation_at'])))
                except ValidationError:
                    retry = None
            item = {'intent_id': row['intent_id'], 'candidate': loads(row['candidate_json']),
                    'state': row['state'], 'ready_at': row['ready_at'], 'attempts': row['attempts'],
                    'held': row['state'] == 'PENDING' and held,
                    'scheduled_at': schedule['presentation_at'] if schedule else None,
                    'reason': detail['reason'] if detail else 'Retained review intent',
                    'changed_at': detail['changed_at'] if detail else row['ready_at'],
                    'retry_at': retry}
            result['intents'].append(item)
            if row['state'] == 'EXHAUSTED':
                result['owner_alerts'].append({'intent_id': row['intent_id'], 'state': 'EXHAUSTED',
                    'reason': item['reason'], 'retry_at': item['retry_at']})
        for row in db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE state='CLAIMED' ORDER BY batch_id"):
            payload = ReviewBatchStore._payload(row)
            item = {'batch_id': row['batch_id'], 'prepared_at': payload['prepared_at'],
                    'state': 'UNKNOWN', 'reason': 'Delivery unresolved; receipt-only reconciliation required',
                    'intent_ids': [member['intent_id'] for member in payload['members']]}
            result['unresolved_batches'].append(item)
            result['owner_alerts'].append(deepcopy(item))
        return result

    def view(self, now, calendar):
        with self.controller.connection() as db:
            return self.view_from_db(db, now, calendar)

    def reporting_failure_intents(self):
        """Reporting owns terminal failures and its durable owner-action notices.

        Keep them in the shared view and immediate status channel without
        treating an already handled outcome as another controller error.
        Only the exact retained reporting handoff can establish that ownership.
        """
        identities = set()
        with self.controller.connection() as db:
            identities = self._reporting_failure_intents(db)
        return identities

    @staticmethod
    def _reporting_failure_intents(db):
        """Use the durable handoff/receipt, never calendar or diagnostic text.

        Ordinary review exhaustion has a per-window allowance. Reporting owns
        a lifetime per-report/platform ceiling; recovery cannot replace it.
        """
        identities = set()
        for row in db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE state='FAILED'"):
            payload = ReviewBatchStore._payload(row)
            receipt = loads(row['receipt_json'])
            _require(receipt['batch_id'] == payload['batch_id'] and
                     receipt['payload_sha256'] == payload['payload_sha256'] and
                     receipt['status'] == 'FAILED' and receipt['reason'] == 'REPORT_DELIVERY_EXHAUSTED',
                     'Reporting failure receipt differs from its retained batch')
            report = db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?',
                                ('review-' + payload['batch_id'],)).fetchone()
            _require(report is not None and loads(report[0]).get('review_batch') == payload,
                     'Reporting failure has no matching retained report')
            identities.update(m['intent_id'] for m in payload['members'])
        return identities

    def enqueue(self, candidate, ready_at, calendar):
        """Retain first readiness and original schedule; identical events dedup.

        A new tuple withdraws pending older tuples for the same repository/PR.
        Replay cannot revive withdrawn identities. None means no pending intent
        was retained; claimed/delivered/exhausted identities never enqueue again.
        """
        return self._enqueue(candidate, ready_at, calendar, restore=False)

    def _enqueue(self, candidate, ready_at, calendar, *, restore, observed_at=None):
        candidate = _candidate(candidate)
        identity = _identity(candidate)
        schedule = next_review_presentation(ready_at, calendar)
        changed_at = ready_at if observed_at is None else observed_at
        with self.controller.transaction() as db:
            existing = db.execute("SELECT * FROM review_presentation_intent_v1 WHERE intent_id=?",
                                  (identity,)).fetchone()
            if existing is not None:
                if identity in self._reporting_failure_intents(db):
                    return None
                if existing["state"] == "PENDING":
                    return identity
                allowance_used = self._window_count(existing, _window(changed_at, calendar)['opening'],
                                                    self._attempt_windows(db, calendar))
                if not (restore and existing["state"] == "WITHDRAWN" and allowance_used < 2):
                    return None
            for row in db.execute("SELECT intent_id,candidate_json FROM review_presentation_intent_v1 WHERE state='PENDING'").fetchall():
                prior = loads(row["candidate_json"])
                if (prior["repository_id"], prior["pull_request"]) == (candidate["repository_id"], candidate["pull_request"]):
                    db.execute("UPDATE review_presentation_intent_v1 SET state='WITHDRAWN' WHERE intent_id=?",
                               (row["intent_id"],))
                    self._detail(db, row['intent_id'], 'Superseded by a newly qualified candidate', changed_at)
            if existing is None:
                db.execute("INSERT INTO review_presentation_intent_v1 VALUES(?,?,?,?,?,0)",
                           (identity, canonical(candidate).decode(), schedule["ready_at"],
                            canonical(schedule).decode(), "PENDING"))
            else:
                # Fresh qualification restores only a resolved, unsent intent.
                # Preserve first readiness, schedule and the delivery retry cap.
                db.execute("UPDATE review_presentation_intent_v1 SET state='PENDING' WHERE intent_id=?",
                           (identity,))
            self._detail(db, identity, 'Awaiting review presentation', changed_at)
            self._changed(db)
        return identity

    def enqueue_observed(self, candidate, observation, now, calendar, *, repository_id):
        """Controller admission from a fresh trusted lifecycle/gate observation.

        Discovery supplies locators, never readiness. The reviewed collector
        independently verifies lifecycle and current final-gate evidence. Invalid
        readiness cannot create an intent or revive a withdrawn identity. Fresh
        qualification may restore an unsent withdrawn intent, never a delivery.
        """
        candidate = _candidate(candidate)
        _require(str(candidate['repository_id']) == repository_id,
                 'Review candidate repository differs from the controller binding')
        if not _observation_valid(observation, candidate, utc_instant(now)):
            return None
        return self._enqueue(candidate, observation.get('ready_at', now), calendar, restore=True, observed_at=now)

    def discovery_candidates(self, candidates, limit):
        """Bound fresh admissions, rotating deferred locators across restarts.

        Delivered/claimed/exhausted identities need no discovery observation;
        reporting owns delivered-notice withdrawal/restoration occurrences.
        """
        with self.controller.transaction() as db:
            eligible = []
            for candidate in candidates:
                identity = _identity(candidate)
                row = db.execute('SELECT state FROM review_presentation_intent_v1 WHERE intent_id=?',
                                 (identity,)).fetchone()
                if row is None or row[0] == 'PENDING':
                    eligible.append((identity, candidate))
            eligible.sort(key=lambda item: item[0])
            key = 'report-review-discovery-cursor'
            row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
            cursor = row[0] if row else ''
            ordered = [v for v in eligible if v[0] > cursor] + [v for v in eligible if v[0] <= cursor]
            selected = ordered[:limit]
            if selected:
                db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (key, selected[-1][0]))
            return [deepcopy(v[1]) for v in selected], len(eligible) - len(selected)

    @staticmethod
    def _state_binding(db):
        """Bind every review row used by selection, retry accounting and recheck.

        Read under a SQLite snapshot, compare under BEGIN IMMEDIATE. Neither
        provider latency nor a concurrent claim/release can reuse stale evidence.
        """
        tables = ('intent', 'outbox', 'receipt', 'check', 'phase', 'detail')
        return fingerprint('review-observation-state-v1', {
            name: [list(row) for row in db.execute(
                'SELECT * FROM review_presentation_' + name + '_v1 ORDER BY rowid')]
            for name in tables})

    def claim(self, now, calendar, observer, *, max_members=None, exclude=()):
        """Recompute pending eligibility and atomically claim one due digest.

        Collect provider observations without a transaction, then atomically
        recheck all dependent state and persist their bindings. Exceptions leave
        the tick unchanged; fresh invalid/changed/closed members withdraw.
        Stale, malformed or unavailable evidence retains the pending intent.
        A missed run catches up during a current presentation window, never
        after cutoff.
        A pending intent whose schedule cannot resolve stays pending without
        blocking other intents; later settings may make it resolvable again.
        No lease timeout can retry an ambiguous provider send.
        """
        _require(callable(observer), "Fresh review candidate observer is required")
        current = utc_instant(now)
        window = next_review_presentation(now, calendar)
        if utc_instant(window["presentation_at"]) != current:
            return None
        selected_ids = None
        if max_members is not None:
            # Commit the scheduling cursor before a remote observation can
            # fail/roll back claim. A broken observer or unresolved schedule
            # must not monopolize the bounded first-candidate slot on restart.
            with self.controller.transaction() as db:
                terminal = self._reporting_failure_intents(db)
                ids = [r[0] for r in db.execute("SELECT intent_id FROM review_presentation_intent_v1 WHERE state IN ('PENDING','EXHAUSTED') ORDER BY intent_id")
                       if r[0] not in exclude and r[0] not in terminal]
                key = 'report-review-claim-cursor'
                row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
                cursor = row[0] if row else ''
                selected_ids = ([i for i in ids if i > cursor] + [i for i in ids if i <= cursor])[:max_members]
                if selected_ids:
                    db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, selected_ids[-1]))
        with self.controller.connection() as db:
            db.execute('BEGIN')
            state_binding = self._state_binding(db)
            presentation_window = _window(now, calendar)
            evidence = self._attempt_windows(db, calendar)
            terminal = self._reporting_failure_intents(db)
            fenced = set()
            # Raw unresolved sends fence their PR until terminal readback.
            # REPORTING has transferred responsibility to the exact retained
            # per-destination outbox: it keeps these members claimed without
            # preventing a newer candidate from being presented independently.
            for claimed in db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE state='CLAIMED'"):
                for member in self._payload(claimed)['members']:
                    value = member['candidate']
                    fenced.add((value['repository_id'], value['pull_request']))
            selected = []
            rows = db.execute("SELECT * FROM review_presentation_intent_v1 WHERE state IN ('PENDING','EXHAUSTED') ORDER BY intent_id").fetchall()
            if selected_ids is not None:
                rows = [row for row in rows if row['intent_id'] in selected_ids]
            for index, row in enumerate(rows):
                if max_members is not None and index >= max_members:
                    break
                candidate = _candidate(loads(row["candidate_json"]))
                if (row['intent_id'] in terminal or row['intent_id'] in exclude or
                        (candidate['repository_id'], candidate['pull_request']) in fenced):
                    continue
                count = self._window_count(row, presentation_window['opening'], evidence)
                if count >= 2:
                    continue
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
                selected.append((dict(row), candidate, schedule, count))
            db.rollback()
        observations = [(row, candidate, schedule, count, deepcopy(observer(deepcopy(candidate))))
                        for row, candidate, schedule, count in selected]
        with self.controller.transaction() as db:
            if self._state_binding(db) != state_binding:
                return None  # Changed while reading; a later tick must re-observe.
            members = []
            for row, candidate, schedule, count, observation in observations:
                if not _observation_current(observation, current):
                    continue
                if not _observation_valid(observation, candidate, current):
                    db.execute("UPDATE review_presentation_intent_v1 SET state='WITHDRAWN' WHERE intent_id=?",
                               (row["intent_id"],))
                    self._detail(db, row['intent_id'], 'Candidate changed, closed or no longer owner-ready', now)
                    self._changed(db)
                    continue
                self._detail(db, row['intent_id'], 'Qualified in the current presentation window', now, attempts=count)
                members.append({"intent_id": row["intent_id"], "candidate": candidate,
                                "ready_at": row["ready_at"], "scheduled_at": schedule["presentation_at"],
                                "initial_schedule": loads(row["initial_schedule_json"]),
                                "held_schedule": schedule['held'],
                                "observation": deepcopy(observation), "attempt": row["attempts"] + 1})
            if not members:
                return None
            body = {"kind": "routine-final-human-review", "version": 1,
                    'preparation_sequence': db.execute('SELECT count(*) FROM review_presentation_outbox_v1').fetchone()[0] + 1,
                    "prepared_at": utc_text(current), "calendar_sha256": window["calendar_sha256"],
                    "members": members, 'presentation_window': presentation_window,
                    "catch_up": any(utc_instant(item['scheduled_at']) < current and
                        (item['held_schedule'] or utc_instant(item['scheduled_at']) < utc_instant(presentation_window['opening']))
                        for item in members),
                    "merge_authorized": False}
            batch_id = fingerprint("review-presentation-batch-v1", body)
            body["batch_id"] = batch_id
            body["payload_sha256"] = fingerprint("review-presentation-payload-v1", body)
            db.execute("INSERT INTO review_presentation_outbox_v1 VALUES(?,?,'CLAIMED',NULL)",
                       (batch_id, canonical(body).decode()))
            db.execute("INSERT INTO review_presentation_phase_v1 VALUES(?,'PREPARED')", (batch_id,))
            for member in members:
                db.execute("UPDATE review_presentation_intent_v1 SET state='CLAIMED' WHERE intent_id=?",
                           (member["intent_id"],))
            self._changed(db)
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
        """Immediate pre-send recheck; immutable payload, no reusable permit.

        The serialized host releases provably unsent PREPARED claims on refusal.
        A send-started or legacy claim requires verified terminal readback.
        """
        _require(callable(observer), "Fresh review candidate observer is required")
        current = utc_instant(now)
        window = next_review_presentation(now, calendar)
        with self.controller.connection() as db:
            db.execute('BEGIN')
            state_binding = self._state_binding(db)
            row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            _require(row is not None and row["state"] in {"CLAIMED", "REPORTING"}, "Review batch is not claimed")
            payload = self._payload(row)
            terminal = self._reporting_failure_intents(db)
            _require(not any(m['intent_id'] in terminal for m in payload['members']),
                     'REPORT_REVIEW_INTENT_TERMINAL')
            _require(current >= utc_instant(payload["prepared_at"]) and
                     utc_instant(window["presentation_at"]) == current and
                     window["calendar_sha256"] == payload["calendar_sha256"],
                     "Review batch calendar changed or presentation window is closed")
            db.rollback()
        observations = [{"intent_id": member["intent_id"],
                         "observation": deepcopy(observer(deepcopy(member["candidate"])))}
                        for member in payload["members"]]
        with self.controller.transaction() as db:
            _require(self._state_binding(db) == state_binding,
                     "Review batch dependencies changed during provider observation")
            row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            _require(row is not None and row["state"] in {"CLAIMED", "REPORTING"}, "Review batch is not claimed")
            payload = self._payload(row)
            _require(current >= utc_instant(payload["prepared_at"]) and
                     utc_instant(window["presentation_at"]) == current and
                     window["calendar_sha256"] == payload["calendar_sha256"],
                     "Review batch calendar changed or presentation window is closed")
            for member, evidence in zip(payload["members"], observations):
                _require(_observation_valid(evidence["observation"], member["candidate"], current),
                         "Review candidate is no longer current and ready")
            encoded = canonical(observations).decode()
            prior = db.execute("SELECT observation_json FROM review_presentation_check_v1 WHERE batch_id=? AND checked_at=?",
                               (batch_id, utc_text(current))).fetchone()
            _require(prior is None or prior[0] == encoded, "Conflicting review observations at the same instant")
            db.execute("INSERT OR IGNORE INTO review_presentation_check_v1 VALUES(?,?,?)",
                       (batch_id, utc_text(current), encoded))
            return payload

    def pending_batches(self, *, limit=None, batch_ids=None):
        """Read unresolved identities for provider readback, never for resending."""
        with self.controller.transaction() as db:
            rows = list(db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE state IN ('CLAIMED','REPORTING') ORDER BY batch_id"))
            if batch_ids is not None:
                rows = [row for row in rows if row['batch_id'] in batch_ids]
            if limit is not None:
                key = 'report-review-readback-cursor'
                prior = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
                cursor = prior[0] if prior else ''
                rows = ([r for r in rows if r['batch_id'] > cursor] +
                        [r for r in rows if r['batch_id'] <= cursor])[:limit]
                if rows:
                    db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, rows[-1]['batch_id']))
            return [self._payload(row) for row in rows]

    def track_reporting(self, batch_id):
        """Transfer a claim to the durable per-destination reporting outbox.

        Only an exact retained report can take responsibility. Unknown outcomes
        remain tracked without blocking unrelated presentations or allowing a
        second batch/attempt identity for these members.
        """
        with self.controller.transaction() as db:
            row = db.execute('SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?', (batch_id,)).fetchone()
            _require(row is not None, 'Unknown reporting batch')
            payload = self._payload(row)
            report = db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?', ('review-' + batch_id,)).fetchone()
            _require(report is not None and loads(report[0]).get('review_batch') == payload,
                     'Reporting handoff requires the exact retained batch')
            db.execute("UPDATE review_presentation_outbox_v1 SET state='REPORTING' WHERE batch_id=? AND state='CLAIMED'", (batch_id,))

    def finish_reporting_failure(self, batch_id, now):
        """Close exhausted reporting identities, never manufacture absence.

        All selected destinations must be terminal. A successful destination's
        receipt and every attempt remain untouched. Members cannot be retried
        under a new presentation identity.
        """
        with self.controller.transaction() as db:
            row = db.execute('SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?', (batch_id,)).fetchone()
            _require(row is not None, 'Unknown reporting batch')
            if row['state'] in {'FAILED', 'DELIVERED', 'NOT_DELIVERED'}:
                return
            _require(row['state'] == 'REPORTING', 'Reporting batch has no tracked claim')
            payload = self._payload(row)
            report = loads(db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?',
                                     ('review-' + batch_id,)).fetchone()[0])
            _require(report.get('review_batch') == payload and utc_instant(now) >= utc_instant(payload['prepared_at']),
                     'Reporting failure binding/time mismatch')
            states = []
            for delivery_id in report['delivery_ids']:
                intent_json, state_json = db.execute('SELECT intent_json,state_json FROM controller_report_delivery WHERE delivery_id=?',
                                                     (delivery_id,)).fetchone()
                intent, state = loads(intent_json), loads(state_json)
                _require(intent['report_id'] == report['report_id'], 'Reporting delivery binding mismatch')
                _require(state['status'] != 'FAILED' or len(state['attempts']) == 2,
                         'Reporting failure requires exhausted send attempts')
                states.append(dict(delivery_id=delivery_id, platform=intent['binding']['platform'],
                                   status=state['status'], attempts=deepcopy(state['attempts'])))
            if not states or not any(s['status'] == 'FAILED' for s in states) or not all(
                    s['status'] in {'FAILED', 'DELIVERED'} for s in states):
                return
            receipt = dict(batch_id=batch_id, payload_sha256=payload['payload_sha256'], status='FAILED',
                           observed_at=utc_text(utc_instant(now)), destinations=states,
                           reason='REPORT_DELIVERY_EXHAUSTED', merge_authorized=False)
            db.execute("UPDATE review_presentation_outbox_v1 SET state='FAILED',receipt_json=? WHERE batch_id=?",
                       (canonical(receipt).decode(), batch_id))
            for member in payload['members']:
                db.execute("UPDATE review_presentation_intent_v1 SET state='EXHAUSTED' WHERE intent_id=? AND state='CLAIMED'",
                           (member['intent_id'],))
                self._detail(db, member['intent_id'],
                    'Reporting delivery exhausted; per-platform attempts retained; owner attention required', now)
                # Reporting owns the two-send ceiling after handoff. Calendar
                # recovery must not issue another identity or resend a platform
                # whose receipt already proves delivery.
                db.execute('UPDATE review_presentation_detail_v1 SET retry_at=NULL WHERE intent_id=?',
                           (member['intent_id'],))
            self._changed(db)

    def release_unsent(self, batch_id, now, calendar, observations=None, reason='Pre-send check refused'):
        """Release only a durable PREPARED claim, never legacy/started sends.

        The serialized host calls this before invoking any delivery port. Missing
        phase evidence is ambiguous, including all claims from the old runtime.
        Fresh invalid observations withdraw only their own candidate identity.
        """
        current = utc_instant(now)
        with self.controller.transaction() as db:
            row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            phase = db.execute("SELECT phase FROM review_presentation_phase_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None or row['state'] != 'CLAIMED' or phase is None or phase[0] != 'PREPARED':
                return False
            payload = self._payload(row)
            for member in payload['members']:
                observation = (observations or {}).get(member['intent_id'])
                withdrawn = (_observation_current(observation, current) and
                             not _observation_valid(observation, member['candidate'], current))
                state = 'WITHDRAWN' if withdrawn else 'PENDING'
                why = 'Candidate changed, closed or no longer owner-ready' if withdrawn else reason
                db.execute("UPDATE review_presentation_intent_v1 SET state=? WHERE intent_id=? AND state='CLAIMED'",
                           (state, member['intent_id']))
                self._detail(db, member['intent_id'], why, now)
            db.execute("UPDATE review_presentation_outbox_v1 SET state='UNSENT' WHERE batch_id=?", (batch_id,))
            db.execute("UPDATE review_presentation_phase_v1 SET phase='RELEASED' WHERE batch_id=?", (batch_id,))
            self._changed(db)
            return True

    def _start(self, db, payload, now):
        for member in payload['members']:
            detail = db.execute("SELECT * FROM review_presentation_detail_v1 WHERE intent_id=?", (member['intent_id'],)).fetchone()
            count = detail['window_attempts'] if detail else member['attempt'] - 1
            _require(count < 2, 'Review delivery attempt ceiling reached')
            db.execute("UPDATE review_presentation_intent_v1 SET attempts=attempts+1 WHERE intent_id=?", (member['intent_id'],))
            self._detail(db, member['intent_id'], 'Delivery started; unresolved until terminal readback', now,
                         attempts=count + 1, retry_at=payload.get('presentation_window', {}).get('next_opening'))
        db.execute("UPDATE review_presentation_phase_v1 SET phase='SENDING' WHERE batch_id=?", (payload['batch_id'],))
        self._changed(db)

    def begin_delivery(self, batch_id, now):
        """Durably fence the side effect and charge attempts immediately before it.

        A crash after this transaction is UNKNOWN, even if the host never reached
        the provider. Only the live serialized caller's returned continuation may
        cancel before invoking transport; restart cannot reconstruct that proof.
        """
        with self.controller.transaction() as db:
            row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            phase = db.execute("SELECT phase FROM review_presentation_phase_v1 WHERE batch_id=?", (batch_id,)).fetchone()
            _require(row is not None and row['state'] == 'CLAIMED' and phase is not None and phase[0] == 'PREPARED',
                     'Review delivery already started or has no unsent proof')
            _require(db.execute("SELECT 1 FROM review_presentation_check_v1 WHERE batch_id=?", (batch_id,)).fetchone(),
                     'Review delivery requires a pre-send recheck')
            payload = self._payload(row)
            terminal = self._reporting_failure_intents(db)
            _require(not any(m['intent_id'] in terminal for m in payload['members']),
                     'REPORT_REVIEW_INTENT_TERMINAL')
            self._start(db, payload, now)

        def cancel_uninvoked(cancelled_at):
            # This continuation is exclusively for a post-fence check refusal,
            # before calling any transport. Never retain it or use after a send.
            with self.controller.transaction() as db:
                row = db.execute("SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?", (batch_id,)).fetchone()
                phase = db.execute("SELECT phase FROM review_presentation_phase_v1 WHERE batch_id=?", (batch_id,)).fetchone()
                _require(row['state'] == 'CLAIMED' and phase[0] == 'SENDING' and row['receipt_json'] is None,
                         'Uninvoked delivery cancellation is no longer current')
                for member in self._payload(row)['members']:
                    intent = db.execute("SELECT * FROM review_presentation_intent_v1 WHERE intent_id=?", (member['intent_id'],)).fetchone()
                    detail = db.execute("SELECT * FROM review_presentation_detail_v1 WHERE intent_id=?", (member['intent_id'],)).fetchone()
                    _require(intent['state'] == 'CLAIMED' and intent['attempts'] == member['attempt']
                             and detail['window_attempts'] > 0, 'Uninvoked delivery charge differs')
                    db.execute("UPDATE review_presentation_intent_v1 SET state='PENDING',attempts=attempts-1 WHERE intent_id=?",
                               (member['intent_id'],))
                    self._detail(db, member['intent_id'], 'Post-fence window or expiry check refused; transport not invoked',
                                 cancelled_at, attempts=detail['window_attempts'] - 1)
                db.execute("UPDATE review_presentation_outbox_v1 SET state='UNSENT' WHERE batch_id=?", (batch_id,))
                db.execute("UPDATE review_presentation_phase_v1 SET phase='RELEASED' WHERE batch_id=?", (batch_id,))
                self._changed(db)
        return cancel_uninvoked

    def acknowledge(self, receipt):
        _require(isinstance(receipt, dict) and receipt.get("status") == "DELIVERED",
                 "Review acknowledgement requires verified DELIVERED readback")
        return self.reconcile(receipt)

    def reconcile(self, receipt, calendar=None):
        """Persist verified readback; at most two attempts before next window.

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
            _require(row["state"] in {"CLAIMED", "REPORTING"}, "Review batch has no unresolved claim")
            phase = db.execute("SELECT phase FROM review_presentation_phase_v1 WHERE batch_id=?", (row['batch_id'],)).fetchone()
            if phase is not None and phase[0] == 'PREPARED':
                # Verified terminal provider evidence also proves an attempt for
                # store clients that supply receipts without the production host.
                self._start(db, payload, receipt['observed_at'])
            _require(db.execute("SELECT 1 FROM review_presentation_receipt_v1 WHERE provider_receipt_id=?",
                                (receipt["provider_receipt_id"],)).fetchone() is None,
                     "Provider review receipt identity was reused; stop and reconcile")
            db.execute("INSERT INTO review_presentation_receipt_v1 VALUES(?,?)",
                       (receipt["provider_receipt_id"], receipt["batch_id"]))
            db.execute("UPDATE review_presentation_outbox_v1 SET state=?,receipt_json=? WHERE batch_id=?",
                       (receipt["status"], encoded, receipt["batch_id"]))
            for member in payload["members"]:
                detail = db.execute("SELECT * FROM review_presentation_detail_v1 WHERE intent_id=?", (member['intent_id'],)).fetchone()
                count = detail['window_attempts'] if detail else member['attempt']
                state = "DELIVERED" if receipt["status"] == "DELIVERED" else (
                    "EXHAUSTED" if count >= 2 else "PENDING")
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
                retry = payload.get('presentation_window', {}).get('next_opening')
                if state == 'EXHAUSTED' and retry is None:
                    retry = _window(observed, calendar or {})['next_opening']
                self._detail(db, member['intent_id'],
                    'Delivery allowance exhausted; owner attention and bounded next-window recovery required' if state == 'EXHAUSTED'
                    else ('Newer candidate takes precedence' if state == 'WITHDRAWN' else receipt['status']),
                    observed, retry_at=retry, attempts=count)
            self._changed(db)
