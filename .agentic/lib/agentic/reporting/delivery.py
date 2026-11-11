"""Controller-owned delivery extension, without a scheduler or database owner.

The host supplies its existing BEGIN IMMEDIATE transaction port. Schema changes
are explicit. Provider callbacks are trusted executable ports, never report data.
ABSENT must prove all reserved attempts terminal (including delayed requests),
not merely that a message is currently missing from a possibly stale listing.
No lease expiration authorizes another send. Crash reservations consume attempts.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
import uuid

from .. import ValidationError
from ..canonical import canonical, fingerprint, loads, timestamp
from .safety import safe_report
from .bounded import send_guard, delivery_timeout, source_timeout, admit_request


def require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def valid_message_id(platform, value):
    pattern = r'[0-9]{1,16}\.[0-9]{6}' if platform == 'slack' else r'[0-9]{1,20}'
    return (platform in {'slack', 'teams'} and isinstance(value, str)
            and re.fullmatch(pattern, value) is not None and safe_report(value) == value)


def validate_binding(binding):
    keys = {'project', 'repository', 'scope_revision', 'platform', 'destination', 'revision', 'secret_ref'}
    require(isinstance(binding, dict) and set(binding) == keys, 'REPORT_BINDING_REQUIRED')
    require(all(isinstance(v, str) and 0 < len(v) <= 256 and v.strip() == v
                and not any(ord(c) < 32 for c in v) for v in binding.values()), 'REPORT_BINDING_INVALID')
    require(binding['platform'] in {'teams', 'slack'}, 'REPORT_PLATFORM_INVALID')
    return deepcopy(binding)


def delivery_intent(report_id, binding, payload, *, thread_id=None):
    binding = validate_binding(binding)
    require(safe_report(binding, trusted_keys=True) == binding, 'REPORT_CREDENTIAL_WITHHELD')
    require(isinstance(report_id, str) and 0 < len(report_id) <= 256, 'REPORT_ID_REQUIRED')
    require(isinstance(payload, str) and 0 < len(payload.encode('utf-8')) <= 65536, 'REPORT_PAYLOAD_SIZE')
    require(safe_report(report_id) == report_id, 'REPORT_CREDENTIAL_WITHHELD')
    require(safe_report(payload) == payload, 'REPORT_CREDENTIAL_WITHHELD')
    require('AWF delivery:' not in payload, 'REPORT_RESERVED_MARKER')
    require(thread_id is None or valid_message_id(binding['platform'], thread_id), 'REPORT_THREAD_ID_INVALID')
    # Destination/configuration changes cannot reset the report/platform ceiling.
    identity = [binding['project'], binding['repository'], report_id, binding['platform']]
    intent = dict(delivery_id=fingerprint('report-delivery-1', identity), report_id=report_id,
                binding=binding, payload=payload,
                payload_sha256=hashlib.sha256(payload.encode('utf-8')).hexdigest())
    if thread_id is not None:
        intent['thread_id'] = thread_id
    return intent


def same_destination(left, right):
    """An explicitly retained channel binding; only scope may have advanced."""
    return {k: v for k, v in left.items() if k != 'scope_revision'} == {
        k: v for k, v in right.items() if k != 'scope_revision'}


def install_delivery_schema(db):
    """Called only by the controller's accepted migration, in its transaction."""
    db.execute('''CREATE TABLE IF NOT EXISTS controller_report_cooldown(
        scope TEXT PRIMARY KEY, retry_not_before TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS controller_reports(
        report_id TEXT PRIMARY KEY, kind TEXT NOT NULL, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL)''')
    # Additive relocation of pre-release reporting rows. The old status outbox
    # must contain only its own wire format, including after a rollback.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controller_outbox'").fetchone():
        for row in db.execute("SELECT delivery_id,observed_at,payload_json FROM controller_outbox WHERE kind='REPORT'").fetchall():
            record = loads(row[2])
            require(record.get('report_id') == row[0] and record.get('kind') in
                    {'hourly', 'eod', 'owner-action', 'jira-review', 'final-human-review'}, 'REPORT_MIGRATION_INVALID')
            retained = db.execute('SELECT payload_json FROM controller_reports WHERE report_id=?', (row[0],)).fetchone()
            if retained:
                require(retained[0] == row[2], 'REPORT_MIGRATION_CONFLICT')
            else:
                db.execute('INSERT INTO controller_reports VALUES(?,?,?,?)', (row[0], record['kind'], row[1], row[2]))
        db.execute("DELETE FROM controller_outbox WHERE kind='REPORT'")
    for operation in ('UPDATE', 'DELETE'):
        db.execute('CREATE TRIGGER IF NOT EXISTS controller_reports_no_' + operation.lower() +
                   ' BEFORE ' + operation + " ON controller_reports BEGIN SELECT RAISE(ABORT, 'Report is immutable'); END")
    db.execute('''CREATE TRIGGER IF NOT EXISTS controller_reports_no_replace
        BEFORE INSERT ON controller_reports WHEN EXISTS(
        SELECT 1 FROM controller_reports WHERE report_id=NEW.report_id) BEGIN
        SELECT RAISE(ABORT, 'Report already exists'); END''')
    db.execute('''CREATE TABLE IF NOT EXISTS controller_report_delivery(
        delivery_id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, state_json TEXT NOT NULL)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS controller_report_delivery_no_delete
        BEFORE DELETE ON controller_report_delivery BEGIN
        SELECT RAISE(ABORT, 'Reporting attempt history cannot be deleted'); END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS controller_report_delivery_no_intent_update
        BEFORE UPDATE OF intent_json,delivery_id ON controller_report_delivery BEGIN
        SELECT RAISE(ABORT, 'Reporting intent is immutable'); END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS controller_report_delivery_no_replace
        BEFORE INSERT ON controller_report_delivery
        WHEN EXISTS(SELECT 1 FROM controller_report_delivery WHERE delivery_id=NEW.delivery_id) BEGIN
        SELECT RAISE(ABORT, 'Reporting intent already exists'); END''')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='controller_meta'").fetchone():
        from ..review_batching import ReviewBatchStore
        ReviewBatchStore.install_review_chain(db)


@dataclass(frozen=True)
class PreparedDelivery:
    """Admitted immutable bytes only; never a credential or send permit."""
    encoded: bytes


class DeliveryOutbox:
    def __init__(self, controller_store):
        self.controller = controller_store

    @staticmethod
    def _admit_persistence(intent, resolve_secret):
        binding = validate_binding(intent['binding'])
        require(safe_report(intent, trusted_keys=True) == intent, 'REPORT_CREDENTIAL_WITHHELD')
        # Resolve only the structurally admitted binding. Inspect every member
        # before constructing any content hashes or handing bytes to SQLite.
        # safe_report owns the transient value; neither caller retains it.
        require(safe_report(intent, trusted_keys=True, secret_refs=(binding['secret_ref'],),
                            resolve_secret=resolve_secret) == intent, 'REPORT_CREDENTIAL_WITHHELD')
        expected = delivery_intent(intent['report_id'], binding, intent['payload'], thread_id=intent.get('thread_id'))
        require(intent == expected, 'REPORT_INTENT_MISMATCH')

    @staticmethod
    def prepare_enqueue(intent, *, resolve_secret=None):
        source_timeout()
        DeliveryOutbox._admit_persistence(intent, resolve_secret)
        source_timeout()
        return PreparedDelivery(canonical(intent))

    def enqueue(self, intent, *, resolve_secret=None):
        prepared = self.prepare_enqueue(intent, resolve_secret=resolve_secret)
        with self.controller.transaction() as db:
            self.enqueue_in_transaction(db, intent, prepared=prepared)
        return intent['delivery_id']

    @staticmethod
    def enqueue_in_transaction(db, intent, *, prepared=None):
        require(isinstance(prepared, PreparedDelivery), 'REPORT_PERSISTENCE_PREPARATION_REQUIRED')
        require(canonical(intent) == prepared.encoded, 'REPORT_PERSISTENCE_PREPARATION_MISMATCH')
        binding = validate_binding(intent['binding'])
        require(intent == delivery_intent(intent['report_id'], binding, intent['payload'],
                thread_id=intent.get('thread_id')), 'REPORT_INTENT_MISMATCH')
        encoded = prepared.encoded.decode()
        row = db.execute('SELECT intent_json FROM controller_report_delivery WHERE delivery_id=?',
                         (intent['delivery_id'],)).fetchone()
        if row:
            require(row[0] == encoded, 'REPORT_IDENTITY_REUSED_WITH_DIFFERENT_BYTES')
            return
        state = dict(status='PENDING', attempts=[], history=[], fence=None)
        db.execute('INSERT INTO controller_report_delivery VALUES(?,?,?)',
                   (intent['delivery_id'], encoded, canonical(state).decode()))

    @staticmethod
    def _load(db, delivery_id):
        row = db.execute('SELECT intent_json,state_json FROM controller_report_delivery WHERE delivery_id=?',
                         (delivery_id,)).fetchone()
        require(row is not None, 'REPORT_DELIVERY_UNKNOWN')
        return loads(row[0]), loads(row[1])

    @staticmethod
    def _save(db, delivery_id, state):
        require(safe_report(state, trusted_keys=True) == state, 'REPORT_CREDENTIAL_WITHHELD')
        db.execute('UPDATE controller_report_delivery SET state_json=? WHERE delivery_id=?',
                   (canonical(state).decode(), delivery_id))

    def get(self, delivery_id):
        with self.controller.transaction() as db:
            return self._load(db, delivery_id)[1]

    @staticmethod
    def withdraw_in_transaction(db, delivery_id, notice, *, basis=False):
        """Fence unsent approval presentations, retaining uncertain attempts.

        An uncertain send still needs readback; withdrawal never proves absence.
        """
        _, state = DeliveryOutbox._load(db, delivery_id)
        DeliveryOutbox._save(db, delivery_id, DeliveryOutbox.withdraw_state(state, notice, basis=basis))

    @staticmethod
    def withdraw_state(state, notice, *, basis=False):
        """Pure withdrawal projection; history and attempt ceilings survive."""
        state = deepcopy(state)
        field = 'basis_withdrawal' if basis else 'approval_withdrawal'
        reason = 'REPORT_BASIS_CHANGED' if basis else 'BASELINE_APPROVAL_WITHDRAWN'
        if state.get(field):
            return state
        # SEND cooldown follows definite non-acceptance (or a deferred
        # pre-send check). RECONCILE cooldown can still hide a landed message.
        nonacceptance = (state['status'] == 'THROTTLED' and state.get('retry_action') != 'RECONCILE'
            and bool(state['history']) and state['history'][-1]['kind'] in {'SEND_THROTTLED', 'REQUEST_DEFERRED'})
        state[field] = deepcopy(notice)
        state['history'].append(dict(kind='BASIS_WITHDRAWN' if basis else 'APPROVAL_WITHDRAWN', notice=deepcopy(notice)))
        if not state['attempts'] or state['status'] == 'ABSENT' or nonacceptance:
            state.update(status='WITHDRAWN', fence=str(uuid.uuid4()), reason=reason)
        elif state['status'] == 'THROTTLED':
            state['retry_action'] = 'RECONCILE'
        return state

    @staticmethod
    def _rejection(raw, intent, attempt_id):
        """A trusted send port's terminal non-acceptance, bound to one attempt."""
        if not isinstance(raw, dict) or safe_report(raw, trusted_keys=True) != raw or any(raw.get(k) != v for k, v in dict(
                status='REJECTED', terminal=True, evidence_kind='PROVIDER_REJECTION',
                delivery_id=intent['delivery_id'], destination=intent['binding']['destination'],
                platform=intent['binding']['platform'], payload_sha256=intent['payload_sha256'],
                attempt_id=attempt_id).items()):
            return None
        observation_id = raw.get('observation_id')
        if not isinstance(observation_id, str) or not 0 < len(observation_id) <= 256 or any(ord(c) < 32 for c in observation_id):
            return None
        try:
            timestamp(raw['observed_at'])
        except (ValidationError, KeyError, TypeError, ValueError):
            return None
        return dict(status='REJECTED', terminal=True, evidence_kind='PROVIDER_REJECTION', attempt_id=attempt_id,
                    observation_id=observation_id, observed_at=raw['observed_at'])

    @staticmethod
    def _throttle(raw, intent, attempt_id):
        """Retain a bound non-acceptance/deadline, never raw headers or errors."""
        if not isinstance(raw, dict) or safe_report(raw, trusted_keys=True) != raw or any(raw.get(k) != v for k, v in dict(
                status='THROTTLED', evidence_kind='PROVIDER_THROTTLE',
                delivery_id=intent['delivery_id'], destination=intent['binding']['destination'],
                platform=intent['binding']['platform'], payload_sha256=intent['payload_sha256'],
                attempt_id=attempt_id).items()):
            return None
        observation_id = raw.get('observation_id')
        if not isinstance(observation_id, str) or not 0 < len(observation_id) <= 256 or any(ord(c) < 32 for c in observation_id):
            return None
        try:
            require(timestamp(raw['retry_not_before']) > timestamp(raw['observed_at']), 'REPORT_RETRY_DEADLINE')
        except (ValidationError, KeyError, TypeError, ValueError):
            return None
        return dict(status='THROTTLED', evidence_kind='PROVIDER_THROTTLE', attempt_id=attempt_id,
                    observation_id=observation_id, observed_at=raw['observed_at'], retry_not_before=raw['retry_not_before'])

    @staticmethod
    def _observation(raw, intent, attempts):
        """Retain only closed safe fields; provider error bodies never enter state."""
        unknown = dict(status='UNKNOWN', reason='CHANNEL_EVIDENCE_UNAVAILABLE')
        if not isinstance(raw, dict) or safe_report(raw, trusted_keys=True) != raw:
            return unknown
        if any(raw.get(k) != v for k, v in dict(delivery_id=intent['delivery_id'],
                destination=intent['binding']['destination'], platform=intent['binding']['platform'],
                payload_sha256=intent['payload_sha256'], attempt_ids=attempts).items()):
            return unknown
        if raw.get('evidence_kind') != 'CHANNEL_READBACK':
            return unknown
        observation_id = raw.get('observation_id')
        if not isinstance(observation_id, str) or not 0 < len(observation_id) <= 256 or any(ord(c) < 32 for c in observation_id):
            return unknown
        try:
            timestamp(raw['observed_at'])
        except (ValidationError, KeyError, TypeError, ValueError):
            return unknown
        if raw.get('status') == 'ABSENT' and raw.get('terminal') is True:
            return dict(status='ABSENT', evidence_kind='CHANNEL_READBACK', terminal=True,
                        attempt_ids=attempts, observation_id=observation_id, observed_at=raw['observed_at'])
        if raw.get('status') == 'VISIBLE':
            mid = raw.get('message_id')
            if not valid_message_id(intent['binding']['platform'], mid):
                return unknown
            return dict(status='DELIVERED', evidence_kind='CHANNEL_READBACK', message_id=mid,
                        observed_at=raw['observed_at'], attempt_ids=attempts, observation_id=observation_id)
        return unknown

    def advance(self, delivery_id, provider, *, allow_send=True, before_send=None, clock=None):
        """One bounded step; retries require a prior terminal-absence observation.

        A crashed IN_FLIGHT reservation is reconciled, never repeated. There is
        deliberately no timeout takeover which could race a still-live sender.
        """
        with self.controller.transaction() as db:
            intent, state = self._load(db, delivery_id)
            if state['status'] in {'DELIVERED', 'FAILED', 'WITHDRAWN'}:
                return state
            if state.get('approval_withdrawal') or state.get('basis_withdrawal'):
                allow_send = False
            if state['status'] == 'THROTTLED':
                now = clock() if clock is not None else datetime.now(timezone.utc).isoformat()
                if timestamp(now) < timestamp(state['retry_not_before']):
                    return state
            read_retry = state.get('retry_action') == 'RECONCILE'
            if not allow_send and state['status'] in {'PENDING', 'ABSENT', 'THROTTLED'} and not read_retry:
                return state
            send = allow_send and not read_retry and state['status'] in {'PENDING', 'ABSENT', 'THROTTLED'} and len(state['attempts']) < 2
            if not send and not state['attempts']:
                return state
            fence, attempts = state['fence'], list(state['attempts'])
        if callable(getattr(provider, 'bind_controller', None)):
            provider.bind_controller(self.controller)
        if send:
            attempt_id = str(uuid.uuid4())
            reserved = False

            def reserve():
                nonlocal reserved, fence, attempts
                require(not reserved, 'REPORT_SEND_ALREADY_STARTED')
                reservation = before_send() if before_send is not None else None
                with send_guard(), self.controller.transaction() as db:
                    delivery_timeout()
                    _, current = self._load(db, delivery_id)
                    require(current == state, 'REPORT_SEND_STATE_CHANGED')
                    # A review guard may return a local persistence continuation.
                    # Its window charge and delivery fence commit or roll back together.
                    if callable(reservation):
                        reservation(db, intent)
                    admit_request()
                    current['attempts'].append(attempt_id)
                    current['history'].append(dict(kind='ATTEMPT_RESERVED', attempt_id=attempt_id))
                    current.pop('retry_not_before', None)
                    current.pop('reason', None)
                    current.pop('retry_action', None)
                    current.update(status='IN_FLIGHT', fence=str(uuid.uuid4()))
                    self._save(db, delivery_id, current)
                    fence, attempts = current['fence'], list(current['attempts'])
                reserved = True

            rejection = throttle = None
            try:
                if callable(getattr(provider, 'send_reserved', None)):
                    raw = provider.send_reserved(deepcopy(intent), attempt_id, reserve)
                else:
                    reserve()
                    with send_guard():
                        raw = provider.send(deepcopy(intent), attempt_id)
                rejection = self._rejection(raw, intent, attempt_id) if reserved else None
                throttle = self._throttle(raw, intent, attempt_id)
            except Exception:
                pass
            if not reserved:
                with self.controller.transaction() as db:
                    _, current = self._load(db, delivery_id)
                    if throttle and current == state:
                        current.update(status='THROTTLED', retry_action='SEND',
                            retry_not_before=throttle['retry_not_before'], reason='PROVIDER_THROTTLED', fence=str(uuid.uuid4()))
                        current['history'].append(dict(kind='REQUEST_DEFERRED', **throttle))
                        self._save(db, delivery_id, current)
                    return current
            if throttle:
                with self.controller.transaction() as db:
                    _, current = self._load(db, delivery_id)
                    current['history'].append(dict(kind='SEND_THROTTLED', **throttle))
                    if current['attempts'] == attempts and current['status'] not in {'DELIVERED', 'FAILED'}:
                        current.update(status='FAILED' if len(attempts) == 2 else 'THROTTLED',
                            retry_not_before=throttle['retry_not_before'],
                            reason='TWO_SEND_ATTEMPTS_EXHAUSTED' if len(attempts) == 2 else 'PROVIDER_THROTTLED',
                            fence=str(uuid.uuid4()))
                    self._save(db, delivery_id, current)
                    return current
            if rejection:
                with self.controller.transaction() as db:
                    _, current = self._load(db, delivery_id)
                    # A competing readback may change the fence, but cannot
                    # erase an actual send outcome for the immutable attempt.
                    current['history'].append(dict(kind='SEND_REJECTED', **rejection))
                    if current['attempts'] == attempts and current['status'] not in {'DELIVERED', 'FAILED'}:
                        current['status'] = 'FAILED' if len(attempts) == 2 else 'ABSENT'
                        current['fence'] = str(uuid.uuid4())
                    self._save(db, delivery_id, current)
                    return current
        try:
            raw = provider.reconcile(deepcopy(intent), list(attempts))
            throttle = self._throttle(raw, intent, None) if isinstance(raw, dict) and raw.get('attempt_ids') == attempts else None
            observation = throttle or self._observation(raw, intent, attempts)
        except Exception:
            observation = dict(status='UNKNOWN', reason='CHANNEL_EVIDENCE_UNAVAILABLE')
        with self.controller.transaction() as db:
            _, current = self._load(db, delivery_id)
            if current['fence'] != fence or current['attempts'] != attempts or current['status'] in {'DELIVERED', 'FAILED'}:
                return current
            current['history'].append(dict(kind='READBACK', **observation))
            current['status'] = observation['status']
            if current['status'] == 'THROTTLED':
                current.update(retry_action='RECONCILE', retry_not_before=observation['retry_not_before'],
                               reason='PROVIDER_THROTTLED')
            else:
                for key in ('retry_action', 'retry_not_before', 'reason'):
                    current.pop(key, None)
            if current['status'] == 'ABSENT' and len(attempts) == 2:
                current['status'] = 'FAILED'
            if current['status'] == 'ABSENT' and (current.get('approval_withdrawal') or current.get('basis_withdrawal')):
                current.update(status='WITHDRAWN', reason='REPORT_BASIS_CHANGED' if current.get('basis_withdrawal')
                               else 'BASELINE_APPROVAL_WITHDRAWN')
            # Changing fence also fences a late return from a previous observer.
            current['fence'] = str(uuid.uuid4())
            self._save(db, delivery_id, current)
            return current
