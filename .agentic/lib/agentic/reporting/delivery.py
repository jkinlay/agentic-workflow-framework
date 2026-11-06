"""Controller-owned delivery extension, without a scheduler or database owner.

The host supplies its existing BEGIN IMMEDIATE transaction port. Schema changes
are explicit. Provider callbacks are trusted executable ports, never report data.
ABSENT must prove all reserved attempts terminal (including delayed requests),
not merely that a message is currently missing from a possibly stale listing.
No lease expiration authorizes another send. Crash reservations consume attempts.
"""
from copy import deepcopy
import hashlib
import uuid

from .. import ValidationError
from ..canonical import canonical, fingerprint, loads, timestamp


def require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def validate_binding(binding):
    keys = {'project', 'repository', 'scope_revision', 'platform', 'destination', 'revision', 'secret_ref'}
    require(isinstance(binding, dict) and set(binding) == keys, 'REPORT_BINDING_REQUIRED')
    require(all(isinstance(v, str) and 0 < len(v) <= 256 and v.strip() == v
                and not any(ord(c) < 32 for c in v) for v in binding.values()), 'REPORT_BINDING_INVALID')
    require(binding['platform'] in {'teams', 'slack'}, 'REPORT_PLATFORM_INVALID')
    return deepcopy(binding)


def delivery_intent(report_id, binding, payload):
    binding = validate_binding(binding)
    require(isinstance(report_id, str) and 0 < len(report_id) <= 256, 'REPORT_ID_REQUIRED')
    require(isinstance(payload, str) and 0 < len(payload.encode('utf-8')) <= 65536, 'REPORT_PAYLOAD_SIZE')
    # Destination/configuration changes cannot reset the report/platform ceiling.
    identity = [binding['project'], binding['repository'], report_id, binding['platform']]
    return dict(delivery_id=fingerprint('report-delivery-1', identity), report_id=report_id,
                binding=binding, payload=payload,
                payload_sha256=hashlib.sha256(payload.encode('utf-8')).hexdigest())


def install_delivery_schema(db):
    """Called only by the controller's accepted migration, in its transaction."""
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


class DeliveryOutbox:
    def __init__(self, controller_store):
        self.controller = controller_store

    def enqueue(self, intent):
        expected = delivery_intent(intent['report_id'], intent['binding'], intent['payload'])
        require(intent == expected, 'REPORT_INTENT_MISMATCH')
        with self.controller.transaction() as db:
            self.enqueue_in_transaction(db, intent)
        return intent['delivery_id']

    @staticmethod
    def enqueue_in_transaction(db, intent):
        require(intent == delivery_intent(intent['report_id'], intent['binding'], intent['payload']),
                'REPORT_INTENT_MISMATCH')
        encoded = canonical(intent).decode()
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
        db.execute('UPDATE controller_report_delivery SET state_json=? WHERE delivery_id=?',
                   (canonical(state).decode(), delivery_id))

    def get(self, delivery_id):
        with self.controller.transaction() as db:
            return self._load(db, delivery_id)[1]

    @staticmethod
    def _rejection(raw, intent, attempt_id):
        """A trusted send port's terminal non-acceptance, bound to one attempt."""
        if not isinstance(raw, dict) or any(raw.get(k) != v for k, v in dict(
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
    def _observation(raw, intent, attempts):
        """Retain only closed safe fields; provider error bodies never enter state."""
        unknown = dict(status='UNKNOWN', reason='CHANNEL_EVIDENCE_UNAVAILABLE')
        if not isinstance(raw, dict):
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
            if not isinstance(mid, str) or not 0 < len(mid) <= 256 or any(ord(c) < 32 for c in mid):
                return unknown
            return dict(status='DELIVERED', evidence_kind='CHANNEL_READBACK', message_id=mid,
                        observed_at=raw['observed_at'], attempt_ids=attempts, observation_id=observation_id)
        return unknown

    def advance(self, delivery_id, provider, *, allow_send=True, before_send=None):
        """One bounded step; retries require a prior terminal-absence observation.

        A crashed IN_FLIGHT reservation is reconciled, never repeated. There is
        deliberately no timeout takeover which could race a still-live sender.
        """
        with self.controller.transaction() as db:
            intent, state = self._load(db, delivery_id)
            if state['status'] in {'DELIVERED', 'FAILED'}:
                return state
            if not allow_send and state['status'] in {'PENDING', 'ABSENT'}:
                return state
            send = allow_send and state['status'] in {'PENDING', 'ABSENT'} and len(state['attempts']) < 2
            if not send and not state['attempts']:
                return state
            fence, attempts = state['fence'], list(state['attempts'])
        if send:
            attempt_id = str(uuid.uuid4())
            reserved = False

            def reserve():
                nonlocal reserved, fence, attempts
                require(not reserved, 'REPORT_SEND_ALREADY_STARTED')
                if before_send is not None:
                    before_send()
                with self.controller.transaction() as db:
                    _, current = self._load(db, delivery_id)
                    require(current == state, 'REPORT_SEND_STATE_CHANGED')
                    current['attempts'].append(attempt_id)
                    current['history'].append(dict(kind='ATTEMPT_RESERVED', attempt_id=attempt_id))
                    current.update(status='IN_FLIGHT', fence=str(uuid.uuid4()))
                    self._save(db, delivery_id, current)
                    fence, attempts = current['fence'], list(current['attempts'])
                reserved = True

            rejection = None
            try:
                if callable(getattr(provider, 'send_reserved', None)):
                    raw = provider.send_reserved(deepcopy(intent), attempt_id, reserve)
                else:
                    reserve()
                    raw = provider.send(deepcopy(intent), attempt_id)
                rejection = self._rejection(raw, intent, attempt_id) if reserved else None
            except Exception:
                pass
            if not reserved:
                with self.controller.transaction() as db:
                    return self._load(db, delivery_id)[1]
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
            observation = self._observation(raw, intent, attempts)
        except Exception:
            observation = dict(status='UNKNOWN', reason='CHANNEL_EVIDENCE_UNAVAILABLE')
        with self.controller.transaction() as db:
            _, current = self._load(db, delivery_id)
            if current['fence'] != fence or current['attempts'] != attempts or current['status'] in {'DELIVERED', 'FAILED'}:
                return current
            current['history'].append(dict(kind='READBACK', **observation))
            current['status'] = observation['status']
            if current['status'] == 'ABSENT' and len(attempts) == 2:
                current['status'] = 'FAILED'
            # Changing fence also fences a late return from a previous observer.
            current['fence'] = str(uuid.uuid4())
            self._save(db, delivery_id, current)
            return current
