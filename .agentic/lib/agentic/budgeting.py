"""AWF-242 modes and enrollment facade over the canonical routing usage ledger.

Fresh setup must explicitly install fresh_settings(). Missing fields in an
existing installation mean legacy behavior, never implicit consent to disable.
Budget accounting never confers dispatch, security, recovery or merge authority.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from .budget_enrollment import ProtectedDatabase, encoded, require, text_id

SETTINGS_KEYS = frozenset({'accounting_enabled', 'enforcement_enabled',
                           'max_tokens_per_ticket', 'max_tokens_per_project'})


def fresh_settings():
    return dict(accounting_enabled=False, enforcement_enabled=False,
                max_tokens_per_ticket=4_000_000, max_tokens_per_project=1_000_000_000)


def legacy_settings():
    """Diagnostic only: retain the installed legacy engine and its actual caps.

    No lifetime cap existed in 1.9.5. This marker cannot enter the new engine or
    be edited; versioned adoption must select an explicit new disposition.
    """
    return dict(accounting_enabled=True, enforcement_enabled=True,
                max_tokens_per_ticket=None, max_tokens_per_project=None,
                legacy_behavior='PRESERVE_INSTALLED_POLICY')


def resolve_settings(operating, *, fresh=False):
    """Resolve a budgeting block without guessing an installation's age.

    Only the trusted setup caller may declare fresh=True. Upgrade callers leave
    it false and continue using the legacy engine until explicit owner adoption.
    An explicit malformed/null block fails instead of falling back to defaults.
    """
    require(isinstance(operating, dict), 'Operating settings must be an object')
    require(type(fresh) is bool, 'Fresh setup classification must be boolean')
    if 'budgeting' in operating:
        return validate_settings(operating['budgeting'])
    return fresh_settings() if fresh else legacy_settings()


def validate_settings(value):
    require(isinstance(value, dict) and set(value) == SETTINGS_KEYS, 'Explicit complete 1.9.6 budget settings required')
    for key in ('accounting_enabled', 'enforcement_enabled'):
        require(type(value[key]) is bool, key + ' must be boolean')
    for key in ('max_tokens_per_ticket', 'max_tokens_per_project'):
        require(type(value[key]) is int and 0 < value[key] <= 2**63 - 1, key + ' must be a positive signed 64-bit integer')
    require(value['accounting_enabled'] or not value['enforcement_enabled'], 'Enforcement requires accounting')
    return deepcopy(value)


def transition_settings(current, changes):
    result = validate_settings(current)
    require(isinstance(changes, dict) and bool(changes) and set(changes) <= SETTINGS_KEYS,
            'Budget changes require supported settings only; identity assertions are not settings')
    result.update(deepcopy(changes))
    if changes.get('accounting_enabled') is False and 'enforcement_enabled' not in changes:
        result['enforcement_enabled'] = False
    return validate_settings(result)


def settings_hash(value):
    return hashlib.sha256(encoded(validate_settings(value)).encode('utf-8')).hexdigest()


def codex_usage(events):
    """Sum completed-turn input/output; cached input is a subset, not extra.

    Missing/malformed usage or no completed turn is UNKNOWN. CLI reports do not
    authenticate provider billing or establish a token enforcement capability.
    """
    total, seen = 0, False
    for event in events:
        if not isinstance(event, dict) or event.get('type') != 'turn.completed':
            continue
        seen = True
        usage = event.get('usage')
        if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                for key in ('input_tokens', 'output_tokens')):
            return None
        total += usage['input_tokens'] + usage['output_tokens']
        if total > 2**63 - 1:
            return None
    return total if seen else None


def subprocess_budget_outcome(binding, selected, *, session_id, actual_tokens, success,
                              failure_kind='unknown'):
    """Normalize verified CLI completion for both trusted subprocess hosts.

    The caller verifies terminal process/log evidence. Missing thread or usage
    stays explicitly unknown; a failed command never becomes a successful run.
    """
    from .canonical import canonical
    context_known = text_id(session_id)
    require(type(success) is bool and (context_known or not success and session_id is None),
            'Successful CLI completion requires an observed context')
    context = session_id if context_known else binding['session_id']
    result = dict(actual_model=selected['model'], actual_reasoning_effort=selected['reasoning_effort'],
        actual_context_id=context, actual_tokens=actual_tokens, actual_cost_microusd=None,
        success=success, validation_passed=False, independent_review_passed=False, escaped_defect=False,
        observation_id=('codex-session-usage:' if context_known else 'codex-launch-usage:') +
            hashlib.sha256(canonical(context)).hexdigest())
    if not success:
        result['failure_kind'] = failure_kind
    if not context_known:
        result['actual_context_observed'] = False
    return result


def protected_state_roots(*roots):
    """Canonical complete exclusions, shared by ledger and execution state."""
    return tuple(dict.fromkeys(Path(root).resolve() for root in roots))


def project_ledger_path(project_id, *, path=None, accounting_enabled, worktree_roots=(), previous_paths=()):
    """One host-owned lifetime ledger per project, independent of checkouts.

    AWF_STATE_HOME selects the host state directory; otherwise use the platform
    state home. A reviewed explicit path supports existing ledger migrations.
    Resolution is read-only, and unavailable state never blocks OFF work.
    """
    from . import ValidationError
    from .path_containment import is_within
    explicit = path is not None
    try:
        require(text_id(project_id), 'Budget project identity is unavailable')
        if path is None:
            home = os.environ.get('AWF_STATE_HOME')
            if home is None:
                home = (os.environ.get('LOCALAPPDATA') if os.name == 'nt' else
                        os.environ.get('XDG_STATE_HOME'))
                if home is None and os.name != 'nt':
                    home = str(Path.home() / '.local/state')
            require(isinstance(home, str) and bool(home.strip()) and Path(home).is_absolute(),
                    'Host state home is unavailable or not absolute')
            path = Path(home) / 'awf/projects' / hashlib.sha256(project_id.encode('utf-8')).hexdigest() / 'routing.sqlite3'
        candidate = Path(path)
        require(candidate.is_absolute(), 'Project ledger path must be absolute')
        for parent in (candidate, *candidate.parents):
            require(not (parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction())),
                    'Project ledger path traverses a link/reparse point')
        candidate = candidate.resolve()
        require(not any(is_within(candidate, root) for root in protected_state_roots(*worktree_roots)),
                'Project ledger must be outside worker and reviewer worktrees')
        require(explicit or not any(Path(prior).exists() and Path(prior).resolve() != candidate
                                    for prior in previous_paths),
                'Preserve/migrate existing per-host budget history before selecting the canonical ledger')
        return candidate
    except (OSError, RuntimeError, ValueError, TypeError, ValidationError) as exc:
        if accounting_enabled:
            detail = str(exc) + '; ' if isinstance(exc, ValidationError) else ''
            raise ValidationError('Enabled budgeting cannot resolve the canonical project ledger; ' + detail +
                'configure an absolute host-owned AWF_STATE_HOME or budget_ledger_path outside worktrees') from exc
        return None


def subprocess_budget_request(binding, *, worker_context_id=None, stream=None):
    """Conservative routing facts for an explicitly dispatched CLI run."""
    value = dict(ticket_id=binding['ticket_id'], role=binding['role'],
        agent_id=binding['session_id'], context_id=None, phase=binding['role'],
        task_class='awf-subprocess', complexity='high', risk='high',
        uncertainty='high', verification='none', risk_flags=[],
        reservation_tokens=None, reservation_cost_microusd=None)
    if binding['role'] in ('critic', 'specialist'):
        require(worker_context_id is None or text_id(worker_context_id), 'Review worker context is invalid')
        # A standalone review has no enrolled worker to compare. UNKNOWN is
        # diagnostic, never an observed execution or an independence attestation.
        value['worker_context_id'] = worker_context_id if worker_context_id is not None else 'UNKNOWN'
        value['worker_context_observed'] = worker_context_id is not None
    if stream is not None:
        value['stream'] = stream
    return value


class ControllerBudgeting:
    """Lazy trusted-host bridge shared by dispatch and review adapters.

    ``session_id`` is the host execution identity allocated before launch, not
    a predicted Codex thread. The host must observe the actual context/usage at
    completion. Neither ticket content nor process ancestry enrolls a run.
    Constructing an OFF context opens no enrollment or accounting database.
    """
    def __init__(self, path, governance, operating, host, *, worktree_roots=()):
        from .budget_enrollment import EnrollmentRegistry
        from .operating import validate_snapshot
        validate_snapshot(operating, governance)
        self.governance = deepcopy(governance)
        self.operating = operating
        self.settings = resolve_settings(operating.config)
        self.host = host
        self.worktree_roots = worktree_roots = protected_state_roots(*worktree_roots)
        require(path is not None or not self.settings['accounting_enabled'],
                'Enabled budgeting requires the canonical project ledger')
        self.database = None if path is None else ProtectedDatabase(path, worktree_roots)
        self.registry = None if path is None else EnrollmentRegistry(self.database.path.with_suffix('.enrollment.sqlite'), host, worktree_roots)

    @property
    def operations(self):
        from .budget_enrollment import OperationalRegistry
        return OperationalRegistry.for_project(self.governance['project']['id'], self.worktree_roots)

    def start_execution(self, binding):
        limit = self.governance['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket']
        self.operations.begin(binding, limit, self._shared_failure_resolved)

    def _shared_failure_resolved(self, binding):
        return self.operational_failure_resolved({'binding': binding})

    def advance(self, store, *args, **kwargs):
        """Advance local lifecycle with the validated, effective mode."""
        require('budgeting' not in kwargs, 'Caller cannot replace the validated budget mode')
        settings = None if 'legacy_behavior' in self.settings else self.settings
        return store.advance(*args, budgeting=settings, **kwargs)

    def transition(self, *args, **kwargs):
        from .lifecycle import transition
        require('budgeting' not in kwargs, 'Caller cannot replace the validated budget mode')
        settings = None if 'legacy_behavior' in self.settings else self.settings
        return transition(*args, budgeting=settings, **kwargs)

    def _ledger(self):
        from .model_routing import RoutingLedger
        require(self.database is not None, 'Retained budget completion requires the canonical project ledger')
        # Apply the same containment/link checks before RoutingLedger opens it.
        with self.database.transaction(''):
            pass
        return RoutingLedger(self.database.path)

    def admit(self, binding, selected, *, stream=None, enrolled=True):
        from .budget_enrollment import validate_binding
        from .model_routing import policy_from_config
        require(type(enrolled) is bool, 'Enrollment classification must be boolean')
        if not enrolled:
            self.operations.admit(binding, self._shared_failure_resolved)
            return None  # Operational recovery applies without enrollment/usage.
        if binding.get('role') not in ('worker', 'fix', 'critic', 'specialist'):
            return None
        if 'legacy_behavior' in self.settings:
            return None  # Existing legacy host controls remain the host's responsibility.
        if binding['role'] in ('critic', 'specialist'):
            self.operations.worker_contexts(binding)
        self.operations.admit(binding, self._shared_failure_resolved)
        if not self.settings['accounting_enabled']:
            self._retained_recovery(binding, selected, stream=stream)
            return None
        binding = validate_binding(binding)
        require(binding['project_id'] == self.governance['project']['id'], 'Budget project identity differs')
        observer = getattr(self.host, 'observe_budget_admission', None)
        require(callable(observer), 'Trusted-host budget admission observer unavailable')
        observed = observer(deepcopy(binding), deepcopy(selected))
        require(isinstance(observed, dict) and set(observed) == {'request', 'capabilities', 'limits_enforced'},
                'Budget admission observation is malformed')
        require(type(observed['limits_enforced']) is bool, 'Host limit capability must be observed')
        require(not self.settings['enforcement_enabled'] or observed['limits_enforced'],
                'Budget enforcement requires observed host token limits')
        request = deepcopy(observed['request'])
        if binding['role'] in ('critic', 'specialist'):
            contexts = self.operations.worker_contexts(binding)
            request['worker_context_ids'] = sorted(set(request.get('worker_context_ids', [])) |
                                                    {row['context_id'] for row in contexts})
        require(request.get('ticket_id') == binding['ticket_id'] and request.get('role') == binding['role'],
                'Budget request differs from admission identity')
        # Verify the launch pair before creating a reservation, including floors.
        from .model_routing import select_route
        policy = policy_from_config(self.governance)
        route = select_route(policy, request, observed['capabilities'],
                             operating=self.operating, governance=self.governance)
        require(route['status'] == 'ready' and all(route[key] == selected.get(key)
                for key in ('model', 'reasoning_effort')), 'Budget route differs from the host launch route')
        self.registry.enroll(binding)
        reservation = self._ledger().reserve(binding['project_id'], policy, request, observed['capabilities'],
            operating=self.operating, governance=self.governance, budget_registry=self.registry, budget_binding=binding)
        require(reservation['status'] == 'reserved', 'Budget routing admission refused: ' + str(reservation))
        require(all(reservation[key] == selected.get(key) for key in ('model', 'reasoning_effort')),
                'Budget recovery route differs from the host launch route')
        return {'binding': binding, 'reservation': reservation}

    def _recovery_database(self):
        """Open existing protected recovery evidence read-only, never create it."""
        import sqlite3
        from .path_containment import is_within
        if self.database is None or not self.database.path.is_file():
            return None
        path = self.database.path
        for parent in (path, *path.parents):
            require(not (parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction())),
                    'Protected state path traverses a link/reparse point')
        require(path.stat().st_nlink == 1, 'Protected state has multiple hardlinks')
        require(not any(is_within(path, root) for root in self.database.worktree_roots),
                'Protected state must be outside worker worktrees')
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        return db

    def operational_failure_resolved(self, admission, binding=None):
        """Only retained authenticated resolution can clear a host failure.

        Operational failure records live with host executions in every mode.
        Budgeted history can use its existing protected resolution authority;
        a missing ledger or admission never supplies evidence of resolution.
        """
        if admission is None:
            return binding is not None and self.operations.resolved(binding)
        db = self._recovery_database()
        if db is None:
            return False
        try:
            db.execute('BEGIN')
            binding = admission['binding']
            row = db.execute('SELECT request FROM model_runs WHERE run_id=?', (binding['run_id'],)).fetchone()
            if row is None:
                return False
            require(json.loads(row['request']).get('_budget_binding') == binding,
                    'Operational resolution differs from retained execution identity')
            return db.execute('SELECT run_id FROM model_failure_resolutions WHERE run_id=?',
                              (binding['run_id'],)).fetchone() is not None
        finally:
            db.close()

    def _retained_recovery(self, binding, selected, *, stream=None):
        """Read existing operational history in OFF mode, without creating state.

        Fresh host failures are independently durable in the host run registry.
        This also retains pre-existing ledger recovery stops without telemetry.
        """
        from .model_routing import policy_from_config, recovery_admission, _current_retry_ceiling
        from .budget_enrollment import validate_binding
        db = self._recovery_database()
        if db is None:
            return
        binding = validate_binding(binding)
        require(binding['project_id'] == self.governance['project']['id'], 'Budget project identity differs')
        policy = policy_from_config(self.governance)
        request = subprocess_budget_request(binding, stream=stream)
        capabilities = {'models': {selected['model']: [selected['reasoning_effort']]}}
        try:
            db.execute('BEGIN')
            ceiling = _current_retry_ceiling(db, binding['project_id'])
            limit = policy['reconciliation']['max_reconciled_incident_retries_per_ticket']
            require(ceiling is None or limit <= ceiling['retry_limit'],
                    'configured retry cap exceeds the protected ledger ceiling; explicit human direction required')
            route, _, _ = recovery_admission(db, binding['project_id'], policy, request, capabilities, limit,
                operating=self.operating, governance=self.governance, enforcing=False)
            require(route['status'] == 'ready', 'Operational recovery admission refused: ' + str(route))
            require(all(route[key] == selected.get(key) for key in ('model', 'reasoning_effort')),
                    'Operational recovery route differs from the host launch route')
        finally:
            db.close()

    def complete(self, admission, observation):
        if admission is None:
            return None
        observer = getattr(self.host, 'observe_budget_outcome', None)
        require(callable(observer), 'Trusted-host budget outcome observer unavailable')
        outcome = observer(deepcopy(admission), deepcopy(observation))
        require(isinstance(outcome, dict), 'Budget outcome observation is malformed')
        outcome = deepcopy(outcome)
        outcome.setdefault('actual_tokens', None)  # RoutingLedger's UNKNOWN normalization.
        require(text_id(outcome.get('observation_id')), 'Budget outcome needs an immutable usage observation')
        binding = admission['binding']
        ledger = self._ledger()
        # A crash between settlement and the adapter's receipt must not charge
        # twice. Only the exact saved observation can recover a closed run.
        with ledger._transaction() as db:
            row = db.execute('SELECT * FROM model_runs WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is not None and json.loads(row['request']).get('_budget_binding') == binding,
                    'Budget completion differs from admission identity')
            if row['status'] != 'reserved':
                saved = json.loads(row['outcome'] or '{}')
                derived = {'operating_hash', 'operating_state', 'violations', 'settled_at'}
                require({k: v for k, v in saved.items() if k not in derived} ==
                        {k: v for k, v in outcome.items() if k not in derived}
                        and ('operating_hash' not in outcome or outcome['operating_hash'] == row['operating_hash']),
                        'Budget completion replay differs from retained observation')
                return {'status': row['status'], 'run_id': binding['run_id'],
                        'accepted': not saved['violations'], 'violations': saved['violations'],
                        'operating_hash': row['operating_hash'], 'policy_sha256': row['policy_hash'],
                        'actual_model': saved['actual_model'], 'actual_reasoning_effort': saved['actual_reasoning_effort']}
        return ledger.settle(binding['run_id'], outcome)


def require_unused_usage_observation(db, observation_id):
    """Consume through the caller's model_runs update under BEGIN IMMEDIATE.

    Both settlement interfaces hold the canonical write lock across this check
    and their outcome write. Retained outcomes (including bridged history) are
    the consumption record; no separate usage ledger or history rewrite is needed.
    """
    require(text_id(observation_id), 'Settlement requires immutable host observation identity')
    for other in db.execute('SELECT outcome FROM model_runs WHERE outcome IS NOT NULL'):
        require(json.loads(other['outcome']).get('observation_id') != observation_id,
                'Usage observation already belongs to another run')


def admit_totals(db, project_id, ticket_id, settings, tokens):
    """Check lifetime totals while holding RoutingLedger's BEGIN IMMEDIATE lock.

    Legacy rows remain charged conservatively. Explicit OFF/unattributed runs
    have no usage charge; unknown enabled usage retains its reservation.
    """
    rows = db.execute('SELECT * FROM model_runs WHERE project_id=?', (project_id,)).fetchall()
    charged = []
    for row in rows:
        request = json.loads(row['request'])
        mode = request.get('_budgeting')
        if mode is not None and not (mode['accounting_enabled'] and mode['attributable']):
            continue
        outcome = json.loads(row['outcome'] or '{}')
        require(row['actual_tokens'] is not None or not request.get('_reservation_unknown'),
                'Reservation estimate is UNKNOWN; enforced admission unavailable')
        require(row['status'] == 'reserved' or row['actual_tokens'] is not None,
                'Historical usage is UNKNOWN; enforced admission unavailable')
        require(row['status'] == 'reconciled' or not (outcome.get('budget_overrun') or
                set(outcome.get('violations', [])) & {'host token ceiling overrun', 'host cost ceiling overrun'}),
                'Unresolved budget overrun; owner recovery required')
        charged.append(row)
    def used(history):
        return sum(row['actual_tokens'] if row['actual_tokens'] is not None else row['reserved_tokens'] for row in history)
    require(used(charged) + tokens <= settings['max_tokens_per_project'], 'Lifetime project token budget exhausted')
    require(used([row for row in charged if row['ticket_id'] == ticket_id]) + tokens <= settings['max_tokens_per_ticket'],
            'Ticket token budget exhausted')


def bridge_stage_one(db):
    """Seal the pre-integration table as an archive; charge each identity once.

    Called within routing schema initialization's write transaction. Source rows
    are retained byte-for-byte. Stage one recorded no times: UNKNOWN placeholders
    are retained instead of inventing historical run/closure timestamps.
    """
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_charges'").fetchone() is None:
        return
    for row in db.execute('SELECT * FROM budget_charges').fetchall():
        source_hash = hashlib.sha256(encoded(dict(row)).encode()).hexdigest()
        prior = db.execute('SELECT request FROM model_runs WHERE run_id=?', (row['run_id'],)).fetchone()
        if prior is not None:
            require(json.loads(prior['request']).get('_legacy_budget_source_sha256') == source_hash,
                    'Stage-one charge conflicts with canonical run identity; preserve both and reconcile')
            continue
        binding = json.loads(row['binding'])
        settings = validate_settings(json.loads(row['settings']))
        from .budget_enrollment import validate_binding
        validate_binding(binding)
        require((row['run_id'], row['project_id'], row['ticket_id']) ==
                (binding['run_id'], binding['project_id'], binding['ticket_id']), 'Stage-one charge binding mismatch')
        require(row['status'] in ('RESERVED', 'UNKNOWN', 'SETTLED'), 'Unrecognized stage-one charge status')
        request = {'role': binding['role'], '_budget_binding': binding,
                   '_budgeting': {**settings, 'attributable': True}, 'task_class': 'enrolled-host-run',
                   '_legacy_budget_source_sha256': source_hash}
        route = {'model': 'unobserved', 'reasoning_effort': 'unobserved', 'simple': False, 'high_risk': True}
        status = 'reserved' if row['status'] == 'RESERVED' else 'quarantined' if row['budget_overrun'] else 'settled'
        outcome = None if status == 'reserved' else encoded({
            'observation_id': row['observation_id'], 'budget_overrun': bool(row['budget_overrun']),
            'success': False, 'actual_usage_known': row['actual_tokens'] is not None,
            'historical_time': 'UNKNOWN',
            'violations': ['host token ceiling overrun'] if row['budget_overrun'] else []})
        db.execute("""INSERT INTO model_runs
            (run_id,project_id,ticket_id,role,agent_id,phase,created_at,day,policy_hash,status,
             reserved_tokens,reserved_cost,actual_tokens,actual_cost,escalated,request,route,outcome,operating_hash)
            VALUES(?,?,?,?,?,?,'UNKNOWN','UNKNOWN','enrolled-budget-1',?,?,NULL,?,NULL,0,?,?,?,NULL)""",
            (binding['run_id'], binding['project_id'], binding['ticket_id'], binding['role'], binding['session_id'],
             'enrolled-host-run', status, row['reserved_tokens'], row['actual_tokens'], encoded(request), encoded(route), outcome))
    for operation in ('INSERT', 'UPDATE', 'DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS stage_one_budget_no_{operation.lower()} BEFORE {operation} ON budget_charges BEGIN SELECT RAISE(ABORT,'archived budget charges; use canonical routing ledger'); END")


class BudgetLedger(ProtectedDatabase):
    """Enrollment facade over RoutingLedger's sole authoritative model_runs.

    No second usage table is created. Hosts may use this facade for an enrolled
    run without routing, or RoutingLedger.reserve with the same registry/binding;
    the run ID is shared and cannot be charged twice. Existing routing history
    participates in lifetime admission under the same SQLite write lock.
    """
    def __init__(self, path, registry, worktree_roots=()):
        super().__init__(path, worktree_roots)
        self.registry = registry

    def _routing(self):
        from .model_routing import RoutingLedger
        # Apply the protected-state path/permission checks before schema access.
        with self.transaction(''):
            pass
        return RoutingLedger(self.path)

    def reserve(self, binding, settings, tokens):
        from datetime import datetime, timezone
        settings = validate_settings(settings)
        if not settings['accounting_enabled']:
            return {'status': 'OFF', 'budget_required': False, 'created': False}
        if not self.registry.enrolled(binding):
            return {'status': 'UNKNOWN', 'budget_required': False, 'attribution': 'UNKNOWN', 'created': False}
        unknown = tokens is None and not settings['enforcement_enabled']
        require(unknown or (type(tokens) is int and 0 < tokens <= 2**63 - 1),
                'Enforcement requires a positive token reservation; accounting estimates may be UNKNOWN')
        stored_tokens = 0 if unknown else tokens
        with self._routing()._transaction() as db:
            old = db.execute('SELECT * FROM model_runs WHERE run_id=?', (binding['run_id'],)).fetchone()
            if old is not None:
                request = json.loads(old['request'])
                require(request.get('_budget_binding') == binding and
                        request.get('_budgeting') == {**settings, 'attributable': True} and
                        old['reserved_tokens'] == stored_tokens and bool(request.get('_reservation_unknown')) == unknown,
                        'Reservation identity collision')
                return {'status': self._status(old), 'run_id': binding['run_id'], 'settings_hash': settings_hash(settings), 'created': False}
            if settings['enforcement_enabled']:
                admit_totals(db, binding['project_id'], binding['ticket_id'], settings, tokens)
            now = datetime.now(timezone.utc).isoformat()
            request = {'role': binding['role'], '_budget_binding': binding,
                       '_budgeting': {**settings, 'attributable': True}, 'task_class': 'enrolled-host-run'}
            if unknown:
                request['_reservation_unknown'] = True
            route = {'model': 'unobserved', 'reasoning_effort': 'unobserved', 'simple': False, 'high_risk': True}
            db.execute("""INSERT INTO model_runs
                (run_id,project_id,ticket_id,role,agent_id,phase,created_at,day,policy_hash,status,
                 reserved_tokens,reserved_cost,actual_tokens,actual_cost,escalated,request,route,outcome,operating_hash)
                VALUES(?,?,?,?,?,?,?,?,?,'reserved',?,NULL,NULL,NULL,0,?,?,NULL,NULL)""",
                (binding['run_id'], binding['project_id'], binding['ticket_id'], binding['role'],
                 binding['session_id'], 'enrolled-host-run', now, now[:10], 'enrolled-budget-1',
                 stored_tokens, encoded(request), encoded(route)))
        return {'status': 'RESERVED', 'run_id': binding['run_id'], 'settings_hash': settings_hash(settings), 'created': True}

    @staticmethod
    def _status(row):
        if row['status'] == 'reserved':
            return 'RESERVED'
        return 'UNKNOWN' if row['actual_tokens'] is None else 'SETTLED'

    def settle(self, binding, tokens, observation_id):
        """Admission-time mode survives later OFF settings; UNKNOWN is never zero."""
        from datetime import datetime, timezone
        require(self.registry.enrolled(binding), 'Settlement requires the exact enrolled identity')
        require(tokens is None or (type(tokens) is int and 0 <= tokens <= 2**63 - 1), 'Observed tokens must be a nonnegative integer or UNKNOWN (null)')
        require(text_id(observation_id), 'Settlement requires immutable host observation identity')
        with self._routing()._transaction() as db:
            row = db.execute('SELECT * FROM model_runs WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is not None and json.loads(row['request']).get('_budget_binding') == binding,
                    'Unknown reservation or mismatched identity')
            require(row['phase'] == 'enrolled-host-run',
                    'Routed runs must complete through RoutingLedger.settle to retain host security checks')
            prior = json.loads(row['outcome'] or '{}')
            if row['status'] != 'reserved':
                require(row['actual_tokens'] == tokens and prior.get('observation_id') == observation_id, 'Conflicting or replayed settlement')
                return {'status': self._status(row), 'budget_overrun': bool(prior.get('budget_overrun'))}
            require_unused_usage_observation(db, observation_id)
            settings = json.loads(row['request'])['_budgeting']
            overrun = bool(settings['enforcement_enabled'] and tokens is not None and tokens > row['reserved_tokens'])
            now = datetime.now(timezone.utc).isoformat()
            outcome = {'observation_id': observation_id, 'budget_overrun': overrun, 'success': False,
                       'actual_usage_known': tokens is not None, 'settled_at': now,
                       'violations': ['host token ceiling overrun'] if overrun else []}
            db.execute('UPDATE model_runs SET status=?,actual_tokens=?,outcome=?,closed_day=? WHERE run_id=?',
                       ('quarantined' if overrun else 'settled', tokens, encoded(outcome), now[:10], binding['run_id']))
        return {'status': 'UNKNOWN' if tokens is None else 'SETTLED', 'budget_overrun': overrun}

    def summary(self, project_id):
        require(text_id(project_id), 'Project identity required')
        with self._routing()._transaction() as db:
            rows = db.execute('SELECT * FROM model_runs WHERE project_id=?', (project_id,)).fetchall()
        rows = [r for r in rows if json.loads(r['request']).get('_budgeting') is None or
                (json.loads(r['request'])['_budgeting']['accounting_enabled'] and
                 json.loads(r['request'])['_budgeting']['attributable'])]
        return {'known_tokens': sum(r['actual_tokens'] for r in rows if r['actual_tokens'] is not None),
                'reserved_tokens': sum(r['reserved_tokens'] for r in rows if r['status'] == 'reserved'),
                'unresolved_reserved_tokens': sum(r['reserved_tokens'] for r in rows if r['status'] != 'reserved' and r['actual_tokens'] is None),
                'unknown_runs': sum(r['actual_tokens'] is None and
                                    (r['status'] != 'reserved' or json.loads(r['request']).get('_reservation_unknown', False)) for r in rows),
                'reservations': len(rows), 'budget_overruns': sum(bool(json.loads(r['outcome'] or '{}').get('budget_overrun')) for r in rows)}
