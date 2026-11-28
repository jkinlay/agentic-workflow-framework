"""Opt-in admission estimates; no guarantee about an already running process.

Charge UNKNOWN usage at its retained positive reservation. Old accounting rows
with no estimate use generic-v1's one-minute high-difficulty estimate (including
its twofold margin and context allowance). This is a rough conservative planning
charge, never measured usage; actuals remain UNKNOWN. Open reservations count
across restarts. Quota checks and the new reservation share the ledger write lock.
"""
from copy import deepcopy
import json
from pathlib import Path

from .budget_enrollment import encoded, require
from .reservation_estimator import estimate_reservation
from .token_limiter import digest, token_count


def conservative_estimate():
    return estimate_reservation(minutes=1, difficulty='high')['reservation_tokens']


def admit_totals(db, project_id, ticket_id, settings, tokens, *, reserved_run_id=None):
    ticket_used = project_used = unknown = 0
    for row in db.execute('SELECT * FROM model_runs WHERE project_id=?', (project_id,)):
        if row['run_id'] == reserved_run_id:
            continue  # Launch recheck replaces this reservation, never adds it twice.
        request = json.loads(row['request'])
        prior = request.get('_budgeting')
        if prior is not None and not (prior['accounting_enabled'] and prior['attributable']):
            continue
        charge = row['actual_tokens']
        if charge is None:
            unknown += int(row['status'] != 'reserved' or request.get('_reservation_unknown', False))
            charge = (row['reserved_tokens'] if not request.get('_reservation_unknown')
                      and token_count(row['reserved_tokens'], positive=True) else conservative_estimate())
        require(token_count(charge), 'PRE_RUN_USAGE_INVALID: accounted usage is invalid')
        project_used += charge
        if row['ticket_id'] == ticket_id:
            ticket_used += charge
    report = dict(enforcement_mode='pre_run', ticket_accounted_tokens=ticket_used,
        project_accounted_tokens=project_used, reservation_tokens=tokens,
        ticket_headroom_tokens=settings['max_tokens_per_ticket']-ticket_used-tokens,
        project_headroom_tokens=settings['max_tokens_per_project']-project_used-tokens,
        unknown_usage_runs=unknown, running_run_may_overshoot=True)
    for scope, used in (('project', project_used), ('ticket', ticket_used)):
        cap = settings['max_tokens_per_' + scope]
        require(used < cap and used + tokens <= cap,
            'PRE_RUN_' + scope.upper() + '_LIMIT: accounted usage plus reservation exceeds limit; '
            + encoded(report))
    return report


def prepare_launch(context, admission):
    """Use existing adapter ports without a strict host factory/enrollment.

    Generic hosts retain admission enforcement and own their launch. The two
    shipped subprocess adapters also pass the native budget as a backstop.
    Their executable verification, sandbox, isolation and output limits remain
    in the adapter. The reference adapter supplies the executable separately.
    """
    host, binding = context.host, admission['binding']
    if callable(getattr(host, '_budget_record', None)):
        record = host._budget_record(binding)
        argv, cwd = record['argv'], record['worktree']
        command = ()  # Existing reference adapter verifies its pinned executable.
    elif hasattr(host, '_budget_launch'):
        argv, cwd = host._budget_launch
        command = (host.c['executables']['codex']['path'],)
    else:
        return None
    validate_argv(argv, admission['reservation'])
    plan = dict(enforcement_mode='pre_run', binding=deepcopy(binding),
        reservation_id=admission['reservation']['run_id'],
        reservation_tokens=admission['reservation']['reservation_tokens'],
        argv=list(argv), launch_root_sha256=digest(str(Path(cwd).resolve(strict=True))))
    ledger = context._ledger()
    with ledger._transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS pre_run_launches '
            '(run_id TEXT PRIMARY KEY, plan TEXT NOT NULL, claimed INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO pre_run_launches(run_id,plan) VALUES (?,?)',
            (binding['run_id'], encoded(plan)))
    # Per-run binding avoids applying this mode to later strict/OFF launches.
    bridges = getattr(host, '_pre_run_bridges', None)
    if bridges is None:
        bridges = host._pre_run_bridges = {}
    bridges[binding['run_id']] = (context, command)
    return plan


def validate_argv(argv, reservation):
    require(isinstance(argv, list) and argv and argv[0] == 'exec' and argv[-1] == '-'
        and '--ephemeral' in argv and '--json' in argv
        and not any(arg in ('resume', '--resume', 'fork') or 'rollout_budget' in arg for arg in argv),
        'PRE_RUN_LAUNCH_INVALID: fresh subprocess arguments required without budget overrides')
    models = [argv[i+1] for i, arg in enumerate(argv[:-1]) if arg in ('--model', '-m')]
    efforts = [arg.split('=', 1)[1].strip('"') for arg in argv if arg.startswith('model_reasoning_effort=')]
    require(models == [reservation['model']] and efforts == [reservation['reasoning_effort']],
        'PRE_RUN_LAUNCH_INVALID: subprocess route differs from reservation')


def restore_launch(context, binding):
    """Recover only retained pre_run launches, including completion after restart."""
    if not context.database.path.exists():
        return None
    with context._ledger()._transaction() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='pre_run_launches'").fetchone() is None:
            return None
        row = db.execute('SELECT plan FROM pre_run_launches WHERE run_id=?', (binding['run_id'],)).fetchone()
        if row is None:
            return None
        require(json.loads(row['plan'])['binding'] == binding, 'PRE_RUN_LAUNCH_INVALID: retained identity differs')
    command = ((context.host.c['executables']['codex']['path'],)
               if hasattr(context.host, '_budget_launch') else ())
    return PreRunSubprocessLimiter(context, command, binding)


class PreRunSubprocessLimiter:
    """Compatibility port for the existing adapters, without strict claims."""
    def __init__(self, context, command, binding):
        self.context, self.command, self.binding = context, command, binding

    def launch(self, plan, *, argv, cwd):
        require(plan['binding'] == self.binding and plan.get('enforcement_mode') == 'pre_run'
            and digest(str(Path(cwd).resolve(strict=True))) == plan['launch_root_sha256'],
            'PRE_RUN_LAUNCH_INVALID: launch identity/root changed')
        prefix = []
        if not self.command:
            # The unchanged reference adapter passes its already-verified binary.
            count = len(argv) - len(plan['argv'])
            require(count > 0, 'PRE_RUN_LAUNCH_INVALID: executable unavailable')
            prefix, argv = argv[:count], argv[count:]
        require(argv == plan['argv'], 'PRE_RUN_LAUNCH_INVALID: launch arguments changed')
        with self.context._ledger()._transaction() as db:
            saved = db.execute('SELECT * FROM pre_run_launches WHERE run_id=?',
                (self.binding['run_id'],)).fetchone()
            row = db.execute('SELECT * FROM model_runs WHERE run_id=?', (self.binding['run_id'],)).fetchone()
            require(saved is not None and saved['plan'] == encoded(plan) and not saved['claimed']
                and row is not None and row['status'] == 'reserved'
                and row['reserved_tokens'] == plan['reservation_tokens']
                and json.loads(row['request']).get('_budget_binding') == self.binding,
                'PRE_RUN_LAUNCH_INVALID: reservation changed or launch already claimed')
            # Another run may have overshot since this admission was prepared.
            # Recheck immediately before claiming launch with the retained limits.
            admit_totals(db, self.binding['project_id'], self.binding['ticket_id'],
                json.loads(row['request'])['_budgeting'], plan['reservation_tokens'],
                reserved_run_id=self.binding['run_id'])
            db.execute('UPDATE pre_run_launches SET claimed=1 WHERE run_id=?', (self.binding['run_id'],))
        settings = {'enabled': 'true', 'enforce_limit': 'true', 'limit_tokens': str(plan['reservation_tokens']),
            'sampling_token_weight': '1.0', 'prefill_token_weight': '1.0'}
        flags = [arg for key, value in settings.items()
                 for arg in ('-c', 'features.rollout_budget.' + key + '=' + value)]
        return prefix + argv[:-1] + flags + ['-']

    def complete(self, plan, *, actual_tokens, success, lower_bound_tokens=None):
        require(plan['binding'] == self.binding, 'PRE_RUN_LAUNCH_INVALID: completion identity changed')
        # The observer supplies a total only for complete telemetry. A failed
        # process can still have completed every turn; success is independent.
        known = token_count(actual_tokens)
        # Overshoot is an actual charge, never a strict-ceiling violation.
        return dict(actual_tokens=actual_tokens if known else None,
            lower_bound_tokens=actual_tokens if known else lower_bound_tokens,
            ceiling_violation=False, success=success)
