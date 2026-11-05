"""Additive AWF-242 budget contract; host/operating integration is separate.

Fresh setup must explicitly install fresh_settings(). Missing fields in an
existing installation mean legacy behavior, never implicit consent to disable.
Budget accounting never confers dispatch, security, recovery or merge authority.
"""
from copy import deepcopy
import hashlib
import json

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


DDL = """
CREATE TABLE IF NOT EXISTS budget_charges(
 run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
 binding TEXT NOT NULL, settings TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
 status TEXT NOT NULL, actual_tokens INTEGER, observation_id TEXT UNIQUE,
 budget_overrun INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS budget_charges_project ON budget_charges(project_id,ticket_id);
CREATE TRIGGER IF NOT EXISTS budget_charges_no_delete BEFORE DELETE ON budget_charges
 BEGIN SELECT RAISE(ABORT,'charges cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS budget_charges_binding BEFORE UPDATE ON budget_charges
 WHEN NEW.run_id != OLD.run_id OR NEW.project_id != OLD.project_id OR NEW.ticket_id != OLD.ticket_id
 OR NEW.binding != OLD.binding OR NEW.settings != OLD.settings OR NEW.reserved_tokens != OLD.reserved_tokens
 OR OLD.status != 'RESERVED'
 BEGIN SELECT RAISE(ABORT,'immutable charge identity or closed charge'); END;
"""


class BudgetLedger(ProtectedDatabase):
    """Lifetime token ledger for explicitly enrolled AWF runs only.

    Hosts must feed verified usage, including tools/retries attributable to that
    run, as one cumulative final observation. A tool event is not an additional
    settlement. Unknown observations retain their reservation and fail enforced
    admission closed; they never turn into retrospective zero usage.
    """
    def __init__(self, path, registry, worktree_roots=()):
        super().__init__(path, worktree_roots)
        self.registry = registry

    def reserve(self, binding, settings, tokens):
        settings = validate_settings(settings)
        if not settings['accounting_enabled']:
            return {'status': 'OFF', 'budget_required': False, 'created': False}
        if not self.registry.enrolled(binding):
            return {'status': 'UNKNOWN', 'budget_required': False, 'attribution': 'UNKNOWN', 'created': False}
        require(type(tokens) is int and 0 < tokens <= 2**63 - 1, 'Enabled accounting requires a positive token reservation')
        with self.transaction(DDL) as db:
            old = db.execute('SELECT * FROM budget_charges WHERE run_id=?', (binding['run_id'],)).fetchone()
            if old is not None:
                require((old['binding'], old['settings'], old['reserved_tokens']) ==
                        (encoded(binding), encoded(settings), tokens), 'Reservation identity collision')
                return {'status': old['status'], 'run_id': binding['run_id'], 'settings_hash': settings_hash(settings), 'created': False}
            rows = db.execute('SELECT * FROM budget_charges WHERE project_id=?', (binding['project_id'],)).fetchall()
            if settings['enforcement_enabled']:
                require(not any(row['status'] == 'UNKNOWN' for row in rows), 'Historical usage is UNKNOWN; enforced admission unavailable')
                require(not any(row['budget_overrun'] for row in rows), 'Unresolved budget overrun; owner recovery required')
                def used(history):
                    return sum(row['actual_tokens'] if row['actual_tokens'] is not None else row['reserved_tokens'] for row in history)
                require(used(rows) + tokens <= settings['max_tokens_per_project'], 'Lifetime project token budget exhausted')
                require(used([row for row in rows if row['ticket_id'] == binding['ticket_id']]) + tokens
                        <= settings['max_tokens_per_ticket'], 'Ticket token budget exhausted')
            db.execute('INSERT INTO budget_charges(run_id,project_id,ticket_id,binding,settings,reserved_tokens,status) VALUES(?,?,?,?,?,?,?)',
                       (binding['run_id'], binding['project_id'], binding['ticket_id'], encoded(binding), encoded(settings), tokens, 'RESERVED'))
        return {'status': 'RESERVED', 'run_id': binding['run_id'], 'settings_hash': settings_hash(settings), 'created': True}

    def settle(self, binding, tokens, observation_id):
        """Use admission-time mode even if subsequent work has accounting OFF."""
        require(self.registry.enrolled(binding), 'Settlement requires the exact enrolled identity')
        require(tokens is None or (type(tokens) is int and 0 <= tokens <= 2**63 - 1), 'Observed tokens must be a nonnegative integer or UNKNOWN (null)')
        require(text_id(observation_id), 'Settlement requires immutable host observation identity')
        with self.transaction(DDL) as db:
            row = db.execute('SELECT * FROM budget_charges WHERE run_id=?', (binding['run_id'],)).fetchone()
            require(row is not None and row['binding'] == encoded(binding), 'Unknown reservation or mismatched identity')
            if row['status'] != 'RESERVED':
                require(row['actual_tokens'] == tokens and row['observation_id'] == observation_id, 'Conflicting or replayed settlement')
                return {'status': row['status'], 'budget_overrun': bool(row['budget_overrun'])}
            require(db.execute('SELECT 1 FROM budget_charges WHERE observation_id=?', (observation_id,)).fetchone() is None,
                    'Usage observation already belongs to another run')
            settings = json.loads(row['settings'])
            overrun = bool(settings['enforcement_enabled'] and tokens is not None and tokens > row['reserved_tokens'])
            status = 'UNKNOWN' if tokens is None else 'SETTLED'
            db.execute('UPDATE budget_charges SET status=?,actual_tokens=?,observation_id=?,budget_overrun=? WHERE run_id=?',
                       (status, tokens, observation_id, int(overrun), binding['run_id']))
        return {'status': status, 'budget_overrun': overrun}

    def summary(self, project_id):
        require(text_id(project_id), 'Project identity required')
        with self.transaction(DDL) as db:
            rows = db.execute('SELECT * FROM budget_charges WHERE project_id=?', (project_id,)).fetchall()
        return {'known_tokens': sum(row['actual_tokens'] for row in rows if row['actual_tokens'] is not None),
                'reserved_tokens': sum(row['reserved_tokens'] for row in rows if row['status'] == 'RESERVED'),
                'unresolved_reserved_tokens': sum(row['reserved_tokens'] for row in rows if row['status'] == 'UNKNOWN'),
                'unknown_runs': sum(row['status'] == 'UNKNOWN' for row in rows),
                'reservations': len(rows), 'budget_overruns': sum(row['budget_overrun'] for row in rows)}
