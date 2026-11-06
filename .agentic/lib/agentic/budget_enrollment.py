"""Explicit AWF enrollment at a protected, trusted-host adapter boundary.

The host object is installed by trusted infrastructure, never deserialized from
worker/configuration/CLI input. These interfaces do not authenticate a Codex UI.
Protect this state outside *all* worker worktrees; repository/model/ancestry is
never an enrollment signal. Enrollment is operational identity, not telemetry.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from . import ValidationError
from .controller_state import restrict_state_permissions
from .path_containment import is_within


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def text_id(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


BINDING_KEYS = frozenset({'project_id', 'ticket_id', 'run_id', 'dispatch_id', 'session_id', 'role'})


def validate_binding(binding):
    require(isinstance(binding, dict) and set(binding) == BINDING_KEYS,
            'Enrollment requires an exact project/ticket/run/dispatch/session/role binding')
    require(all(text_id(value) for value in binding.values()), 'Enrollment identities must be observed nonempty bounded strings')
    require(binding['role'] in ('worker', 'fix', 'critic', 'specialist', 'design', 'spec_review'),
            'Only explicit AWF sub-agents may enroll; controller/independent agents are excluded')
    return deepcopy(binding)


class ProtectedDatabase:
    """Lazy database: construction alone creates no budget files or telemetry.

    The trusted host supplies a complete worktree inventory and protects the
    directory from worker writes. Path checks are defense in depth, not sandboxing.
    """
    def __init__(self, path, worktree_roots=()):
        self.path = Path(path).absolute()
        self.worktree_roots = tuple(Path(root).resolve() for root in worktree_roots)

    @contextmanager
    def transaction(self, ddl):
        for parent in (self.path, *self.path.parents):
            require(not (parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction())),
                    'Protected state path traverses a link/reparse point')
        require(not self.path.exists() or self.path.stat().st_nlink == 1, 'Protected state has multiple hardlinks')
        require(not any(is_within(self.path, root) for root in self.worktree_roots),
                'Protected state must be outside worker worktrees')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            restrict_state_permissions(self.path)
            db.executescript(ddl)
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
        finally:
            db.close()


DDL = """
CREATE TABLE IF NOT EXISTS budget_enrollments(
 run_id TEXT PRIMARY KEY, binding TEXT UNIQUE NOT NULL);
CREATE TRIGGER IF NOT EXISTS budget_enrollments_no_update BEFORE UPDATE ON budget_enrollments
 BEGIN SELECT RAISE(ABORT,'immutable enrollment'); END;
CREATE TRIGGER IF NOT EXISTS budget_enrollments_no_delete BEFORE DELETE ON budget_enrollments
 BEGIN SELECT RAISE(ABORT,'immutable enrollment'); END;
CREATE TABLE IF NOT EXISTS budget_owner_observations(
 observation_id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS budget_owner_observations_no_update BEFORE UPDATE ON budget_owner_observations
 BEGIN SELECT RAISE(ABORT,'immutable owner observation'); END;
CREATE TRIGGER IF NOT EXISTS budget_owner_observations_no_delete BEFORE DELETE ON budget_owner_observations
 BEGIN SELECT RAISE(ABORT,'immutable owner observation'); END;
"""


class EnrollmentRegistry(ProtectedDatabase):
    def __init__(self, path, host, worktree_roots=()):
        super().__init__(path, worktree_roots)
        self.host = host

    def enroll(self, binding):
        """Only a host-observed dispatch may register; every retry enrolls anew."""
        binding = validate_binding(binding)
        require(self.host is not None and callable(getattr(self.host, 'observe_enrollment', None)),
                'Trusted-host enrollment observer unavailable')
        observed = self.host.observe_enrollment(deepcopy(binding))
        require(observed == binding, 'Enrollment differs from trusted-host observation')
        with self.transaction(DDL) as db:
            old = db.execute('SELECT binding FROM budget_enrollments WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(old is None or old['binding'] == encoded(binding), 'Run enrollment identity cannot be rebound')
            if old is None:
                db.execute('INSERT INTO budget_enrollments VALUES (?,?)', (binding['run_id'], encoded(binding)))
        return binding

    def enrolled(self, binding):
        """Unknown attribution remains unknown, including missing session identity."""
        try:
            binding = validate_binding(binding)
        except ValidationError:
            return False
        if not self.path.exists():
            return False
        with self.transaction(DDL) as db:
            old = db.execute('SELECT binding FROM budget_enrollments WHERE run_id=?', (binding['run_id'],)).fetchone()
            return old is not None and old['binding'] == encoded(binding)

    def admit_owner_direction(self, project_id, current, changes):
        """Consume a host observation before operating's atomic mutation.

        Host must independently verify a current, authorized owner direction,
        bound to these exact bytes, and return its immutable provider identity.
        A crash after consumption requires a new observation, never blind replay.
        The caller must hold the operating lock and commit the returned settings
        through operating's journal. This method does not persist operating state.
        """
        from .budgeting import settings_hash, transition_settings
        require(text_id(project_id), 'project_id must be a bounded identity')
        result = transition_settings(current, changes)
        require(self.host is not None and callable(getattr(self.host, 'observe_owner_direction', None)),
                'Trusted-host owner observer unavailable; caller-authored identity is refused')
        prior = settings_hash(current)
        observed = self.host.observe_owner_direction(project_id, prior, deepcopy(changes))
        keys = {'observation_id', 'actor_id', 'project_id', 'prior_settings_hash', 'changes'}
        require(isinstance(observed, dict) and set(observed) == keys,
                'Trusted-host owner observation unavailable or malformed')
        require(text_id(observed['observation_id']) and text_id(observed['actor_id']),
                'Host observation requires immutable source and authenticated actor identities')
        require(observed['project_id'] == project_id and observed['prior_settings_hash'] == prior
                and encoded(observed['changes']) == encoded(changes),
                'Owner observation must bind project, exact changes and prior settings hash')
        with self.transaction(DDL) as db:
            require(db.execute('SELECT 1 FROM budget_owner_observations WHERE observation_id=?',
                               (observed['observation_id'],)).fetchone() is None,
                    'Owner observation already consumed')
            db.execute('INSERT INTO budget_owner_observations VALUES (?,?)',
                       (observed['observation_id'], encoded(observed)))
        return {'settings': result, 'observation': deepcopy(observed)}


OPERATIONAL_DDL = '''
CREATE TABLE IF NOT EXISTS execution_intents(run_id TEXT PRIMARY KEY, binding TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_contexts(run_id TEXT PRIMARY KEY, context_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_outcomes(run_id TEXT PRIMARY KEY, success INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS execution_resolutions(run_id TEXT PRIMARY KEY, binding TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_retry_limits(project_id TEXT PRIMARY KEY, retry_limit INTEGER NOT NULL);
'''
for _table in ('execution_intents', 'execution_contexts', 'execution_outcomes', 'execution_resolutions'):
    for _action in ('UPDATE', 'DELETE'):
        OPERATIONAL_DDL += (f'CREATE TRIGGER IF NOT EXISTS {_table}_no_{_action} BEFORE {_action} ON {_table} '
                            "BEGIN SELECT RAISE(ABORT,'immutable execution evidence'); END;")


class OperationalRegistry(ProtectedDatabase):
    """Shared host execution identity/recovery, with no enrollment or usage.

    Both subprocess hosts use the canonical project state home even when a
    budget ledger has an explicit migration path or accounting is OFF. Hosts
    must register before launch, so lost/unknown worker identity fails closed.
    Only trusted operational resume/reconciliation calls may resolve failures.
    """
    @classmethod
    def for_project(cls, project_id, worktree_roots=()):
        from .budgeting import project_ledger_path
        path = project_ledger_path(project_id, accounting_enabled=True, worktree_roots=worktree_roots)
        return cls(path.with_name('executions.sqlite3'), worktree_roots)

    @contextmanager
    def transaction(self, ddl):
        try:
            with super().transaction(ddl) as db:
                yield db
        except (OSError, sqlite3.Error) as exc:
            raise ValidationError('Worker independence unknown: protected shared execution evidence unavailable') from exc

    def _binding(self, db, binding):
        binding = validate_binding(binding)
        row = db.execute('SELECT binding FROM execution_intents WHERE run_id=?', (binding['run_id'],)).fetchone()
        require(row is not None and row['binding'] == encoded(binding), 'Operational execution identity differs')
        return binding

    def begin(self, binding, retry_limit):
        binding = validate_binding(binding)
        require(type(retry_limit) is int and retry_limit >= 0, 'Operational retry limit is invalid')
        with self.transaction(OPERATIONAL_DDL) as db:
            row = db.execute('SELECT binding FROM execution_intents WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is None or row['binding'] == encoded(binding), 'Operational execution cannot be rebound')
            db.execute('INSERT OR IGNORE INTO execution_intents VALUES (?,?)', (binding['run_id'], encoded(binding)))
            db.execute('INSERT INTO execution_retry_limits VALUES (?,?) ON CONFLICT(project_id) DO UPDATE '
                       'SET retry_limit=min(retry_limit,excluded.retry_limit)', (binding['project_id'], retry_limit))

    def context(self, binding, context_id):
        require(text_id(context_id), 'Worker independence unknown: observed context unavailable')
        with self.transaction(OPERATIONAL_DDL) as db:
            self._binding(db, binding)
            row = db.execute('SELECT context_id FROM execution_contexts WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is None or row['context_id'] == context_id, 'Operational context identity changed')
            db.execute('INSERT OR IGNORE INTO execution_contexts VALUES (?,?)', (binding['run_id'], context_id))

    def finish(self, binding, context_id, success):
        require(type(success) is bool, 'Operational terminal outcome must be observed')
        if context_id is not None:
            self.context(binding, context_id)
        with self.transaction(OPERATIONAL_DDL) as db:
            self._binding(db, binding)
            row = db.execute('SELECT success FROM execution_outcomes WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is None or bool(row['success']) == success, 'Operational terminal outcome changed')
            db.execute('INSERT OR IGNORE INTO execution_outcomes VALUES (?,?)', (binding['run_id'], int(success)))

    def worker_contexts(self, binding):
        validate_binding(binding)
        with self.transaction(OPERATIONAL_DDL) as db:
            contexts = []
            for row in db.execute('SELECT i.run_id,i.binding,c.context_id FROM execution_intents i '
                                  'LEFT JOIN execution_contexts c ON c.run_id=i.run_id'):
                prior = validate_binding(json.loads(row['binding']))
                if (all(prior[key] == binding[key] for key in ('project_id', 'ticket_id'))
                        and prior['role'] in ('worker', 'fix')):
                    require(row['context_id'] is not None, 'Worker independence unknown: shared execution context unavailable')
                    contexts.append(dict(run_id=row['run_id'], context_id=row['context_id']))
            return contexts

    def resolved(self, binding):
        with self.transaction(OPERATIONAL_DDL) as db:
            self._binding(db, binding)
            row = db.execute('SELECT binding FROM execution_resolutions WHERE run_id=?', (binding['run_id'],)).fetchone()
            return row is not None and row['binding'] == encoded(binding)

    def resolve(self, binding):
        """Consume one bounded, exact-run operational reconciliation."""
        with self.transaction(OPERATIONAL_DDL) as db:
            self._binding(db, binding)
            row = db.execute('SELECT success FROM execution_outcomes WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is not None and not row['success'], 'Operational recovery requires observed terminal failure')
            if db.execute('SELECT 1 FROM execution_resolutions WHERE run_id=?', (binding['run_id'],)).fetchone():
                return  # Exact resume replay never resets/consumes another retry.
            limit = db.execute('SELECT retry_limit FROM execution_retry_limits WHERE project_id=?',
                               (binding['project_id'],)).fetchone()
            count = sum(all(json.loads(row['binding'])[key] == binding[key] for key in ('project_id', 'ticket_id'))
                        for row in db.execute('SELECT binding FROM execution_resolutions'))
            require(limit is not None and count < limit['retry_limit'], 'Operational recovery retry limit exhausted')
            db.execute('INSERT INTO execution_resolutions VALUES (?,?)', (binding['run_id'], encoded(binding)))
