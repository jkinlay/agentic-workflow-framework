"""Read-only reporting observations over existing controller and routing state."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3

from .. import ValidationError
from ..budgeting import resolve_settings
from ..budget_enrollment import validate_binding
from ..canonical import loads
from ..controller_state import protected_state_path
from .delivery import require


@contextmanager
def read_database(path, worktree_roots=()):
    require(Path(path).is_file(), 'REPORT_STATE_UNAVAILABLE')
    path = protected_state_path(path, worktree_roots)
    require(path.is_file(), 'REPORT_STATE_UNAVAILABLE')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'REPORT_STATE_INTEGRITY')
        db.execute('BEGIN')
        yield db
        db.execute('ROLLBACK')
    finally:
        db.close()


def attributed_accounting(path, operating, project_id, *, worktree_roots=()):
    """No ledger constructor/migration, enrollment, reservation or settlement.

    Canonical model_runs' authenticated enrollment binding is required. Legacy,
    controller and independently spawned runs cannot enter these report totals.
    Aggregate actual tokens are retained without inventing input/output splits.
    """
    unavailable = dict(status='UNAVAILABLE', consumed=None, known_tokens=None,
                       reason='ACCOUNTING_OFF_OR_UNKNOWN', coverage=None)
    settings = resolve_settings(operating)
    if settings['accounting_enabled'] is not True or 'legacy_behavior' in settings or path is None:
        return unavailable
    try:
        with read_database(path, worktree_roots) as db:
            rows = db.execute('SELECT * FROM model_runs WHERE project_id=? LIMIT 100001', (project_id,)).fetchall()
        require(len(rows) <= 100000, 'REPORT_ACCOUNTING_WINDOW_TOO_LARGE')
        admitted = []
        for row in rows:
            request = loads(row['request'])
            mode, binding = request.get('_budgeting'), request.get('_budget_binding')
            if not isinstance(mode, dict) or mode.get('accounting_enabled') is not True or mode.get('attributable') is not True:
                continue
            try:
                validate_binding(binding)
            except ValidationError:
                continue
            require(binding['project_id'] == project_id and binding['run_id'] == row['run_id'] and
                    binding['ticket_id'] == row['ticket_id'] and binding['role'] == row['role'] and
                    binding['session_id'] == row['agent_id'], 'REPORT_ACCOUNTING_ATTRIBUTION_MISMATCH')
            admitted.append(row)
        known = [r for r in admitted if r['actual_tokens'] is not None]
        require(all(type(r['actual_tokens']) is int and r['actual_tokens'] >= 0 for r in known), 'REPORT_ACCOUNTING_USAGE_INVALID')
        total = sum(r['actual_tokens'] for r in known)
        complete = bool(admitted) and len(known) == len(admitted)
        return dict(status='AVAILABLE' if complete else 'PARTIAL' if admitted else 'UNAVAILABLE',
                    consumed=total if complete else None, known_tokens=total if known else None,
                    coverage=dict(observed=len(known), enrolled=len(admitted)),
                    unknown_runs=[r['run_id'] for r in admitted if r['actual_tokens'] is None],
                    reason=None if complete else 'ATTRIBUTED_USAGE_INCOMPLETE',
                    components='UNAVAILABLE', source='canonical routing ledger; read only')
    except (OSError, sqlite3.Error, ValidationError, ValueError, KeyError):
        return unavailable


def collect_sources(binding, now, observe, verify):
    """Closed independent source evidence; missing input stays unavailable."""
    if observe is None and verify is None:
        return {}
    require(callable(observe) and callable(verify), 'REPORT_SOURCE_PORTS_REQUIRED')
    from .bounded import source_timeout
    source_timeout()
    request = dict(binding=deepcopy(binding), observed_at=now)
    value = observe(deepcopy(request))
    require(isinstance(value, dict) and set(value) == {
        'binding', 'observed_at', 'records', 'owner_actions', 'authentication'}, 'REPORT_SOURCE_ENVELOPE_INVALID')
    require(value['binding'] == binding and value['observed_at'] == now, 'REPORT_SOURCE_BINDING_MISMATCH')
    auth = value['authentication']
    require(isinstance(auth, dict) and set(auth) == {'evidence_id'} and isinstance(auth['evidence_id'], str)
            and bool(auth['evidence_id']), 'REPORT_SOURCE_AUTHENTICATION_REQUIRED')
    snapshot = {k: deepcopy(v) for k, v in value.items() if k != 'authentication'}
    require(verify(deepcopy(auth), deepcopy(request)) == dict(status='VERIFIED', evidence_id=auth['evidence_id'],
                                                            snapshot=snapshot), 'REPORT_SOURCE_VERIFIER_MISMATCH')
    result = dict(records=deepcopy(value['records']), owner_actions=deepcopy(value['owner_actions']))
    if isinstance(value['records'], dict) and 'source_coverage' in value['records']:
        result['source_coverage'] = deepcopy(value['records']['source_coverage'])
    if isinstance(value['records'], dict) and 'readiness_candidates' in value['records']:
        result['readiness_candidates'] = deepcopy(value['records']['readiness_candidates'])
    return result


def collect_review_corrections(store, binding, now, observe):
    """Re-observe notices that actually landed, independently per destination.

    Durable review records and channel evidence seed this set, including partial
    batch delivery. Unavailable observations never mean readiness withdrawal.
    A correction identity closes each prior delivery once, across restarts.
    """
    from ..canonical import loads, fingerprint, timestamp
    from ..review_batching import _candidate, _OBSERVATION_KEYS, _observation_valid
    from .bounded import source_timeout
    from .delivery import DeliveryOutbox
    notices = []
    with store.connection() as db:
        rows = db.execute("SELECT payload_json FROM controller_reports WHERE kind='final-human-review'").fetchall()
        for row in rows:
            report = loads(row[0])
            if report['binding'] != binding or 'review_batch' not in report:
                continue
            for delivery_id in report['delivery_ids']:
                intent, state = DeliveryOutbox._load(db, delivery_id)
                correction_id = fingerprint('report-review-correction-1', delivery_id)
                if state['status'] != 'DELIVERED' or db.execute(
                        'SELECT 1 FROM controller_reports WHERE report_id=?', (correction_id,)).fetchone():
                    continue
                require(callable(observe), 'REPORT_REVIEW_OBSERVER_UNAVAILABLE')
                invalid = []
                for member in report['review_batch']['members']:
                    source_timeout()
                    prior = _candidate(member['candidate'])
                    current = observe(deepcopy(prior), now)
                    source_timeout()
                    require(isinstance(current, dict) and set(current) == _OBSERVATION_KEYS,
                            'REPORT_REVIEW_OBSERVATION_UNAVAILABLE')
                    candidate = _candidate(current['candidate'])
                    require((candidate['repository_id'], candidate['pull_request']) ==
                            (prior['repository_id'], prior['pull_request']) and
                            timestamp(current['observed_at']) == timestamp(now) < timestamp(current['expires_at']),
                            'REPORT_REVIEW_OBSERVATION_UNAVAILABLE')
                    if not _observation_valid(current, prior, timestamp(now)):
                        invalid.append(dict(withdrawn=prior, current=current))
                if invalid:
                    readback = next(h for h in reversed(state['history']) if h.get('status') == 'DELIVERED')
                    notices.append(dict(id=correction_id, prior_report=report['report_id'],
                        delivery_id=delivery_id, binding=intent['binding'],
                        thread_id=readback['message_id'], invalidated=invalid))
    return notices


def inspect_reporting(path, operating, binding, *, worktree_roots=()):
    """Read-only CLI view: opening a report never initializes controller state."""
    from ..continuous_controller import STATE_APPLICATION_ID
    from ..controller_state import STATE_SCHEMA_VERSION
    from .runtime import reporting_status
    class Reader:
        @contextmanager
        def connection(self):
            with read_database(path, worktree_roots) as db:
                require(db.execute('PRAGMA application_id').fetchone()[0] == STATE_APPLICATION_ID and
                        db.execute('PRAGMA user_version').fetchone()[0] == STATE_SCHEMA_VERSION,
                        'REPORT_CONTROLLER_IDENTITY_MISMATCH')
                yield db
    return reporting_status(Reader(), operating, binding)
