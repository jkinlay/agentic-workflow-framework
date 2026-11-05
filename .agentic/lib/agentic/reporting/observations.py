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
    if isinstance(value['records'], dict) and 'readiness_candidates' in value['records']:
        result['readiness_candidates'] = deepcopy(value['records']['readiness_candidates'])
    return result


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
