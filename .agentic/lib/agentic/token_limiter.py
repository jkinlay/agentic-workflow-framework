"""Offline token-limiter contract and durable supervisory attempt identities.

Host-owned state lives outside worktrees. This journal is not a usage ledger:
the enrolled host must settle each outcome in the existing routing ledger.
No method grants dispatch, recovery, cap-increase or settlement authority.
"""
from copy import deepcopy
import hashlib
import json
import sqlite3
from typing import Protocol
import uuid

from .budget_enrollment import ProtectedDatabase, encoded, require

MAX_TOKENS = 2**63 - 1


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def token_count(value, *, positive=False):
    return type(value) is int and (1 if positive else 0) <= value <= MAX_TOKENS


def sha256_text(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class TokenLimiter(Protocol):
    """Trusted hosts supply reserved identities; observations never raise caps."""
    def prepare(self, binding, *, reservation_id, attempt_id, model, effort): ...
    def observe(self, attempt_id, observation): ...
    def stop(self, attempt_id, *, reason): ...
    def finish(self, attempt_id, *, success): ...


DDL = """
CREATE TABLE IF NOT EXISTS limiter_attempts(
 attempt_id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL,
 reservation_id TEXT UNIQUE NOT NULL, plan TEXT NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS limiter_events(
 attempt_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL,
 PRIMARY KEY(attempt_id, sequence));
CREATE TRIGGER IF NOT EXISTS limiter_events_no_update BEFORE UPDATE ON limiter_events
 BEGIN SELECT RAISE(ABORT,'immutable limiter event'); END;
CREATE TRIGGER IF NOT EXISTS limiter_events_no_delete BEFORE DELETE ON limiter_events
 BEGIN SELECT RAISE(ABORT,'immutable limiter event'); END;
"""


class AttemptJournal(ProtectedDatabase):
    """One reservation/run per attempt, atomic observations and durable stop.

Trusted infrastructure must protect this directory and serialize stream input.
After supervisor loss a claimed attempt cannot launch again. Reconciliation,
including any settlement retry, belongs to the existing host/ledger boundary.
"""
    def create(self, plan):
        try:
            require(str(uuid.UUID(plan['attempt_id'])) == plan['attempt_id'], 'Invalid attempt identity')
        except (ValueError, TypeError, AttributeError):
            require(False, 'Invalid attempt identity')
        snapshot = dict(state='PREPARED', sequence=0, lower_bound_tokens=0,
                        complete=False, unknown=False, stopped=False, outcome=None)
        try:
            with self.transaction(DDL) as db:
                db.execute('INSERT INTO limiter_attempts VALUES (?,?,?,?,?)',
                    (plan['attempt_id'], plan['binding']['run_id'], plan['reservation_id'],
                     encoded(plan), encoded(snapshot)))
        except sqlite3.IntegrityError:
            require(False, 'Attempt/run/reservation identity is already consumed')
        return deepcopy(plan)

    def _row(self, db, attempt_id):
        row = db.execute('SELECT plan,snapshot FROM limiter_attempts WHERE attempt_id=?',
                         (attempt_id,)).fetchone()
        require(row is not None, 'Unknown limiter attempt identity')
        return json.loads(row['plan']), json.loads(row['snapshot'])

    def read(self, attempt_id):
        with self.transaction(DDL) as db:
            plan, snapshot = self._row(db, attempt_id)
        return dict(plan=plan, snapshot=snapshot)

    def change(self, attempt_id, operation):
        with self.transaction(DDL) as db:
            plan, snapshot = self._row(db, attempt_id)
            result = operation(db, plan, snapshot)
            db.execute('UPDATE limiter_attempts SET snapshot=? WHERE attempt_id=?',
                       (encoded(snapshot), attempt_id))
        return deepcopy(result)

    def claim(self, attempt_id):
        def claim(db, plan, snapshot):
            require(snapshot['state'] == 'PREPARED' and not snapshot['stopped'],
                    'Attempt identity cannot be relaunched or resumed')
            snapshot['state'] = 'RUNNING'
            return snapshot
        return self.change(attempt_id, claim)

    def observe(self, attempt_id, observation):
        require(isinstance(observation, dict) and set(observation) == {
            'sequence', 'event_id', 'counter_tokens', 'complete', 'unknown'},
            'Limiter observation fields are invalid')
        require(token_count(observation['sequence'], positive=True)
                and sha256_text(observation['event_id'])
                and token_count(observation['counter_tokens'])
                and type(observation['complete']) is bool and type(observation['unknown']) is bool,
                'Limiter observation types are invalid')
        def observe(db, plan, snapshot):
            require(snapshot['state'] == 'RUNNING', 'Observation requires an owned running attempt')
            sequence = observation['sequence']
            old = db.execute('SELECT event FROM limiter_events WHERE attempt_id=? AND sequence=?',
                             (attempt_id, sequence)).fetchone()
            if old is not None:
                if old['event'] != encoded(observation):
                    snapshot['unknown'] = True
                return snapshot
            snapshot['unknown'] |= (observation['unknown'] or sequence != snapshot['sequence'] + 1
                or observation['counter_tokens'] < snapshot['lower_bound_tokens'])
            snapshot['sequence'] = max(sequence, snapshot['sequence'])
            snapshot['lower_bound_tokens'] = max(snapshot['lower_bound_tokens'], observation['counter_tokens'])
            snapshot['complete'] = observation['complete']
            db.execute('INSERT INTO limiter_events VALUES (?,?,?)',
                       (attempt_id, sequence, encoded(observation)))
            return snapshot
        return self.change(attempt_id, observe)

    def stop(self, attempt_id, *, reason):
        require(reason in {'OWNER_STOP', 'USAGE_UNKNOWN', 'TOKEN_LIMIT', 'PROCESS_FAILURE'},
                'Unsupported limiter stop reason')
        def stop(db, plan, snapshot):
            require(snapshot['state'] != 'FINISHED', 'Finished attempt cannot be stopped')
            snapshot['stopped'] = True
            snapshot['unknown'] |= reason == 'USAGE_UNKNOWN'
            snapshot.setdefault('stop_reason', reason)
            return dict(attempt_id=attempt_id, stop_requested=True,
                        remote_termination_verified=False)
        return self.change(attempt_id, stop)

    def finish(self, attempt_id, *, success):
        require(type(success) is bool, 'Process success must be observed boolean')
        def finish(db, plan, snapshot):
            if snapshot['outcome'] is not None:
                return snapshot['outcome']
            known = (snapshot['state'] == 'RUNNING' and snapshot['complete']
                     and not snapshot['unknown'])
            actual = snapshot['lower_bound_tokens'] if known else None
            snapshot['outcome'] = dict(attempt_id=attempt_id,
                reservation_id=plan['reservation_id'], observation_id='limiter-attempt:' + attempt_id,
                plan_sha256=digest(plan), qualification_sha256=(
                    digest(plan['qualification_receipt']) if plan['qualification_receipt'] else None),
                actual_tokens=actual, lower_bound_tokens=snapshot['lower_bound_tokens'],
                retained_reservation_tokens=plan['reservation_tokens'] if actual is None else 0,
                limits_enforced=plan['limits_enforced'],
                ceiling_violation=snapshot['lower_bound_tokens'] > plan['reservation_tokens'],
                success=success and known, remote_termination_verified=False)
            snapshot['state'] = 'FINISHED'
            return snapshot['outcome']
        return self.change(attempt_id, finish)
