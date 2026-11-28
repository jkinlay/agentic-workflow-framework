"""Admission checks are estimates, not a qualified bound on a running process."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import unittest
from unittest import mock
import uuid

import source_only  # noqa: F401
from agentic import ValidationError, budgeting, operating as op
from agentic.budgeting import BudgetLedger, ControllerBudgeting
from agentic.canonical import canonical
from agentic.model_routing import RoutingLedger, policy_from_config, select_route
from agentic.providers.codex_subprocess_limiter import enrolled_limiter
from agentic.pre_run_budgeting import PreRunSubprocessLimiter
from settings_pr_fixtures import publish_fixture_settings
import test_awf242_controller_wiring as wiring
from test_model_routing import request, capabilities, outcome
from test_operating import governance

ROOT = Path(__file__).resolve().parents[2]


class PreRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('awf315-pre-run-' + uuid.uuid4().hex)
        self.root.mkdir()
        patcher = mock.patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'state')})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gov = governance()
        self.host = wiring.ObservedBudgetHost()
        self.path = self.root / 'routing.sqlite'
        self.worker = self.root / 'worker'
        self.tokens = 100
        self.host.observe_budget_admission = self.observe

    def observe(self, binding, selected):
        return dict(request=request(ticket_id=binding['ticket_id'], role=binding['role'],
            context_id=None, worker_context_id='independent-worker', stream='A',
            reservation_tokens=self.tokens), capabilities=capabilities(), limits_enforced=False)

    def context(self, *, ticket=1000, project=1000, mode='pre_run', enabled=True, enforcement=None):
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.value['budgeting'] = dict(budgeting.fresh_settings(), accounting_enabled=enabled,
            enforcement_enabled=enabled if enforcement is None else enforcement,
            max_tokens_per_ticket=ticket, max_tokens_per_project=project)
        if mode is not None:
            self.value['budgeting']['enforcement_mode'] = mode
        publish_fixture_settings(self, self.worker, self.gov, self.value)
        return ControllerBudgeting(self.path, self.gov, op.validate_operating(self.value, self.gov),
            self.host, repository_root=self.worker, worktree_roots=(self.worker,))

    def binding(self, ticket='EX-10', role='worker'):
        run = str(uuid.uuid4())
        return dict(project_id=self.gov['project']['id'], ticket_id=ticket, run_id=run,
            dispatch_id='dispatch-' + run, session_id=run, role=role)

    def admit(self, context, binding=None):
        binding = binding or self.binding()
        selected = select_route(policy_from_config(self.gov), self.observe(binding, None)['request'],
            capabilities(), operating=context.operating, governance=self.gov)
        return context.admit(binding, selected)

    def settle(self, context, admission, tokens):
        self.host.observe_budget_outcome = lambda a, o: outcome(a['reservation'], actual_tokens=tokens,
            actual_context_id='observed-' + a['binding']['run_id'], independent_review_passed=False,
            observation_id='usage-' + a['binding']['run_id'])
        return context.complete(admission, {})

    def test_under_at_over_ticket_limit_and_reservation_push(self):
        for used, allowed in ((899, True), (900, True), (901, False), (1000, False), (1001, False)):
            with self.subTest(used=used):
                self.setUp()
                ctx = self.context(project=10000)
                self.settle(ctx, self.admit(ctx), used)
                if allowed:
                    result = self.admit(ctx)
                    self.assertEqual(1000-used-100,
                        result['reservation']['budget_diagnostics']['ticket_headroom_tokens'])
                else:
                    with self.assertRaisesRegex(ValidationError, 'PRE_RUN_TICKET_LIMIT'):
                        self.admit(ctx)

    def test_under_at_over_project_limit_and_reservation_push(self):
        for used, allowed in ((899, True), (900, True), (901, False), (1000, False), (1001, False)):
            with self.subTest(used=used):
                self.setUp()
                ctx = self.context(ticket=10000)
                self.settle(ctx, self.admit(ctx), used)
                if allowed:
                    result = self.admit(ctx, self.binding('EX-20'))
                    self.assertEqual(1000-used-100,
                        result['reservation']['budget_diagnostics']['project_headroom_tokens'])
                else:
                    with self.assertRaisesRegex(ValidationError, 'PRE_RUN_PROJECT_LIMIT'):
                        self.admit(ctx, self.binding('EX-20'))

    def test_unknown_earlier_usage_charges_reservation_without_changing_actuals(self):
        ctx = self.context(ticket=200, project=200)
        self.settle(ctx, self.admit(ctx), None)
        self.admit(ctx, self.binding('EX-20'))
        with self.assertRaisesRegex(ValidationError, 'PRE_RUN_PROJECT_LIMIT'):
            self.admit(ctx, self.binding('EX-30'))
        summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
        self.assertEqual(1, summary['unknown_runs'])
        self.assertEqual(0, summary['known_tokens'])

    def test_stopped_prefix_stays_unknown_and_retains_ticket_and_project_capacity(self):
        # Completed usage is only a lower bound when another turn is open.
        events = [dict(type='turn.completed', usage=dict(input_tokens=7, output_tokens=3)),
                  dict(type='turn.started')]
        for tail in ('stopped', 'killed'):
            for scope in ('ticket', 'project'):
                with self.subTest(tail=tail, scope=scope):
                    self.setUp()
                    ctx = self.context(ticket=150, project=1000 if scope == 'ticket' else 150)
                    admission = self.admit(ctx)
                    bridge = PreRunSubprocessLimiter(ctx, (), admission['binding'])
                    result = bridge.complete(dict(binding=admission['binding']),
                        actual_tokens=budgeting.codex_usage(events), success=False)
                    self.assertIsNone(result['actual_tokens'])
                    self.assertFalse(result['success'])
                    self.settle(ctx, admission, result['actual_tokens'])
                    summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
                    self.assertEqual(1, summary['unknown_runs'])
                    self.assertEqual(0, summary['known_tokens'])
                    self.assertEqual(100, summary['unresolved_reserved_tokens'])
                    next_binding = self.binding() if scope == 'ticket' else self.binding('EX-20')
                    reason = 'PRE_RUN_' + scope.upper() + '_LIMIT'
                    with self.assertRaisesRegex(ValidationError, reason):
                        self.admit(ctx, next_binding)

    def test_pre_run_idle_failure_and_cancel_keep_completed_usage(self):
        for failure in ('command_failure', 'cancelled'):
            with self.subTest(failure=failure):
                self.setUp()
                ctx = self.context()
                admission = self.admit(ctx)
                bridge = PreRunSubprocessLimiter(ctx, (), admission['binding'])
                result = bridge.complete(dict(binding=admission['binding']),
                    actual_tokens=100, success=False)
                self.assertEqual(100, result['actual_tokens'])
                self.assertFalse(result['success'])
                self.settle(ctx, admission, result['actual_tokens'])

    def test_pre_run_clean_finish_preserves_known_total(self):
        ctx = self.context()
        admission = self.admit(ctx)
        bridge = PreRunSubprocessLimiter(ctx, (), admission['binding'])
        result = bridge.complete(dict(binding=admission['binding']), actual_tokens=10, success=True)
        self.assertEqual(10, result['actual_tokens'])
        self.settle(ctx, admission, result['actual_tokens'])
        summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
        self.assertEqual(10, summary['known_tokens'])
        self.assertEqual(0, summary['unknown_runs'])
        self.assertEqual(0, summary['unresolved_reserved_tokens'])

    def test_unknown_reservation_uses_generic_conservative_fallback(self):
        ctx = self.context(ticket=2_000_000, project=2_000_000, enforcement=False)
        self.tokens = None
        # Existing accounting-only history can have neither estimate nor total.
        self.settle(ctx, self.admit(ctx), None)
        ctx = self.context(ticket=2_000_000, project=2_000_000)
        with self.assertRaisesRegex(ValidationError, 'PRE_RUN_PROJECT_LIMIT'):
            self.admit(ctx, self.binding('EX-20'))

    def test_racing_admissions_share_last_project_headroom(self):
        ctx = self.context(ticket=100, project=100)
        # Initialize the independent operational store before racing quota
        # admissions; its first-use identity setup is a separate host boundary.
        ctx.operations.admit(self.binding())
        barrier = threading.Barrier(2)
        def race(ticket):
            barrier.wait(timeout=10)
            try:
                self.admit(ctx, self.binding(ticket))
                return 'admitted'
            except ValidationError as exc:
                self.assertIn('PRE_RUN_PROJECT_LIMIT', str(exc))
                return 'refused'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(['admitted', 'refused'], list(pool.map(race, ('EX-10', 'EX-20'))))

    def test_restart_retains_reservations_and_project_headroom(self):
        ctx = self.context(ticket=100, project=100)
        self.admit(ctx)
        ctx = self.context(ticket=100, project=100)
        with self.assertRaisesRegex(ValidationError, 'PRE_RUN_PROJECT_LIMIT'):
            self.admit(ctx, self.binding('EX-20'))

    def test_no_enrollment_or_qualification_is_required(self):
        ctx = self.context()
        self.host.observe_enrollment = mock.Mock(side_effect=AssertionError('no enrollment'))
        self.host.bind_budget_reservation = mock.Mock(side_effect=AssertionError('no qualified binder'))
        admitted = self.admit(ctx)
        self.assertFalse(admitted['reservation']['requires_host_limit_enforcement'])
        self.assertFalse(ctx.registry.path.exists())

    def test_reviewers_share_ticket_quota(self):
        ctx = self.context(ticket=100, project=1000)
        self.admit(ctx, self.binding(role='critic'))
        with self.assertRaisesRegex(ValidationError, 'PRE_RUN_TICKET_LIMIT'):
            self.admit(ctx, self.binding(role='specialist'))

    def test_claude_is_refused_before_observation_or_launch(self):
        ctx = self.context()
        with mock.patch.object(self.host, 'observe_budget_admission') as observer:
            with self.assertRaisesRegex(ValidationError, 'UNSUPPORTED_PROVIDER'):
                ctx.admit(self.binding(), dict(model='claude-synthetic', reasoning_effort='high'))
            observer.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_diagnostics_name_mode_and_do_not_claim_running_bound(self):
        ctx = self.context()
        report = budgeting.budget_readiness(ctx.settings)
        self.assertEqual('pre_run', report['enforcement_mode'])
        self.assertNotIn('qualification_receipt', report['missing'])
        admitted = self.admit(ctx)
        self.assertEqual('pre_run', admitted['reservation']['budget_diagnostics']['enforcement_mode'])
        self.assertEqual('settled', self.settle(ctx, admitted, 500)['status'])

    def test_strict_mode_still_requires_observed_limits(self):
        ctx = self.context(mode='strict')
        with self.assertRaisesRegex(ValidationError, 'qualification_receipt'):
            self.admit(ctx)

    def test_off_bytes_remain_identical_to_head(self):
        ctx = self.context(mode=None, enabled=False)
        self.assertEqual(b'{"accounting_enabled":false,"enforcement_enabled":false,'
            b'"max_tokens_per_project":1000000000,"max_tokens_per_ticket":4000000}',
            canonical(budgeting.fresh_settings()))
        self.assertEqual(b'{"admission_required":false,"missing":[],"status":"OFF"}',
            canonical(budgeting.budget_readiness(ctx.settings)))
        with mock.patch.object(self.host, 'observe_budget_admission', side_effect=AssertionError('OFF')):
            self.assertIsNone(ctx.admit(self.binding(), {}))
        self.assertFalse(self.path.exists())
        # Captured read-only from assigned HEAD d4bc158d9f695aedf5199e19086015f952d861eb.
        # Preserve the six byte comparisons in installed/shallow runtimes that
        # cannot read that historical Git object. No baseline bytes are normalized.
        profiles = (
            (False, b'{"accounting_enabled":false,"enforcement_enabled":false,'
                b'"max_tokens_per_project":1000000000,"max_tokens_per_ticket":4000000}',
                b'"e9154cf81343dca8a0cd7ffc76749f72c2d76059534fc2c1bfe98000a5cbe4fe"'),
            (True, b'{"accounting_enabled":true,"enforcement_enabled":false,'
                b'"max_tokens_per_project":1000000000,"max_tokens_per_ticket":4000000}',
                b'"d3306b5db831ef02aa8d5d18b604ca73da1feb7e1d386eadd22cd81c0b1d5a33"'),
        )
        for accounting, expected_settings, expected_hash in profiles:
            settings = dict(budgeting.fresh_settings(), accounting_enabled=accounting)
            for function, args, expected in (
                    ('resolve_settings', ({'budgeting': settings},), expected_settings),
                    ('settings_hash', (settings,), expected_hash),
                    ('budget_readiness', (settings,),
                        b'{"admission_required":false,"missing":[],"status":"OFF"}')):
                self.assertEqual(expected, canonical(getattr(budgeting, function)(*args)))

    def test_missing_estimate_gets_positive_rough_reservation(self):
        self.tokens = None
        ctx = self.context(ticket=4_000_000, project=4_000_000)
        result = self.admit(ctx)
        self.assertEqual(1_100_000, result['reservation']['reservation_tokens'])

    def test_mode_selection_and_schema_keep_existing_defaults(self):
        self.assertEqual('pre_run', op._selection('budgeting.enforcement_mode', 'pre_run'))
        self.assertEqual('pre_run', budgeting.transition_settings(budgeting.fresh_settings(),
            {'enforcement_mode': 'pre_run'})['enforcement_mode'])
        for invalid in (None, True, 'unknown', {}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                budgeting.validate_settings(dict(budgeting.fresh_settings(), enforcement_mode=invalid))

    def test_delayed_launch_rechecks_other_runs_overshoot(self):
        ctx = self.context(ticket=1000, project=1000)
        first = self.admit(ctx)
        binding = self.binding('EX-20')
        selected = select_route(policy_from_config(self.gov), self.observe(binding, None)['request'],
            capabilities(), operating=ctx.operating, governance=self.gov)
        args = ['exec', '--ephemeral', '--json', '--model', selected['model'],
            '-c', 'model_reasoning_effort='+selected['reasoning_effort'], '-']
        self.host._budget_record = lambda b: dict(argv=args, worktree=str(self.worker))
        pending = self.admit(ctx, binding)
        self.settle(ctx, first, 901)
        bridge = enrolled_limiter(self.host, binding, self.worker)
        with self.assertRaisesRegex(ValidationError, 'PRE_RUN_PROJECT_LIMIT'):
            bridge.launch(pending['limiter_plan'], argv=[sys.executable, *args], cwd=self.worker)

    def test_racing_reviewers_share_last_ticket_headroom(self):
        ctx = self.context(ticket=100, project=1000)
        ctx.operations.admit(self.binding())
        barrier = threading.Barrier(2)
        def race(role):
            barrier.wait(timeout=10)
            try:
                self.admit(ctx, self.binding(role=role))
                return 'admitted'
            except ValidationError as exc:
                self.assertIn('PRE_RUN_TICKET_LIMIT', str(exc))
                return 'refused'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(['admitted', 'refused'], list(pool.map(race, ('critic', 'specialist'))))

    def adapter_route(self, ctx, binding):
        req = budgeting.subprocess_budget_request(binding, stream='A')
        return select_route(policy_from_config(self.gov), req, capabilities(),
            operating=ctx.operating, governance=self.gov)

    def test_reference_launch_persists_restores_and_passes_native_backstop(self):
        module = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        runner = module['_SubprocessRunner'](record_dir=self.root)
        self.host = runner
        ctx = self.context(ticket=4_000_000, project=4_000_000)
        binding = self.binding()
        selected = self.adapter_route(ctx, binding)
        args = ['exec', '--ephemeral', '--json', '--sandbox', 'workspace-write',
            '--model', selected['model'], '-c', 'model_reasoning_effort='+selected['reasoning_effort'], '-']
        record = dict(format='awf-reference-dispatch-2', dispatch_id=binding['dispatch_id'],
            ticket=binding['ticket_id'], launch_nonce=binding['run_id'], role='writer',
            argv=args, worktree=str(self.worker), stream='A')
        record_path = self.root / (binding['dispatch_id']+'.json')
        module['_write_record'](record_path, record)
        admission = ctx.admit(binding, selected)
        record['budget_admission'] = admission
        module['_write_record'](record_path, record)
        restored = module['_read_record'](record_path)['budget_admission']
        self.assertEqual(admission, restored)
        # A new runner/context has no enrolled limiter or in-memory bridge.
        self.host = runner = module['_SubprocessRunner'](record_dir=self.root)
        ctx = self.context(ticket=4_000_000, project=4_000_000)
        script = self.root / 'fake-codex.py'
        script.write_text('import sys,json\n'
            'assert "features.rollout_budget.limit_tokens=1100000" in sys.argv\n'
            'assert "features.rollout_budget.prefill_token_weight=1.0" in sys.argv\n'
            'sys.stdin.read()\n'
            'for e in [{"type":"thread.started","thread_id":"synthetic"},'
            '{"type":"turn.started"},{"type":"turn.completed",'
            '"usage":{"input_tokens":70,"output_tokens":30}}]: print(json.dumps(e),flush=True)\n')
        launched = runner.launch([sys.executable, '-B', str(script), *args], cwd=self.worker,
            env=dict(os.environ), timeout=10, stdin='synthetic',
            handoff_path=str(self.root / (binding['dispatch_id']+'.handoff.json')),
            terminal_proof_path=str(self.root / 'terminal.json'), launch_nonce=binding['run_id'])
        proof = runner.observe(dict(launched), timeout=10)
        self.assertEqual(100, proof['actual_tokens'])
        self.assertEqual({}, runner._processes)
        bridge = enrolled_limiter(runner, binding, self.worker)
        with self.assertRaisesRegex(ValidationError, 'already claimed'):
            bridge.launch(restored['limiter_plan'], argv=[sys.executable, *args], cwd=self.worker)

    def test_reference_observer_tail_accounting_and_pre_run(self):
        cases = (('cancel-mid-turn', b'{"type":"turn.started"}\n', True, None),
                 ('failure-mid-turn', b'{"type":"turn.started"}\n', False, None),
                 ('truncated', b'{"type":"turn.st', False, None),
                 ('failure-idle', b'', False, 100),
                 ('cancel-idle', b'', True, 100))
        for enforced in (False, True):
            for name, tail, cancelled, expected in cases:
                with self.subTest(enforced=enforced, case=name):
                    self.setUp()
                    module = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
                    runner = module['_SubprocessRunner'](record_dir=self.root)
                    self.host = runner
                    ctx = self.context(ticket=4_000_000, project=4_000_000, enforcement=enforced)
                    binding = self.binding()
                    selected = self.adapter_route(ctx, binding)
                    args = ['exec', '--ephemeral', '--json', '--model', selected['model'],
                        '-c', 'model_reasoning_effort='+selected['reasoning_effort'], '-']
                    record = dict(format='awf-reference-dispatch-2', dispatch_id=binding['dispatch_id'],
                        ticket=binding['ticket_id'], launch_nonce=binding['run_id'], role='writer',
                        argv=args, worktree=str(self.worker), stream='A')
                    path = self.root / (binding['dispatch_id']+'.json')
                    module['_write_record'](path, record)
                    admission = ctx.admit(binding, selected)
                    record['budget_admission'] = admission
                    module['_write_record'](path, record)
                    events = [dict(type='thread.started', thread_id=str(uuid.uuid4())),
                              dict(type='turn.started'),
                              dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
                    raw = b'\n'.join(json.dumps(e).encode() for e in events) + b'\n' + tail
                    program = ('import sys,time; sys.stdout.buffer.write(' + repr(raw) +
                        '); sys.stdout.buffer.flush(); ' +
                        ('time.sleep(30)' if cancelled else 'raise SystemExit(3)'))
                    launched = runner.launch([sys.executable, '-B', '-c', program, *args],
                        cwd=self.worker, env=dict(os.environ), timeout=1 if cancelled else 10,
                        stdin='synthetic', handoff_path=str(path.with_suffix('.handoff.json')),
                        terminal_proof_path=str(path.with_suffix('.terminal.json')),
                        launch_nonce=binding['run_id'])
                    record.update(launched)
                    module['_write_record'](path, record)
                    with self.assertRaisesRegex(ValidationError, 'timed out|non-zero exit'):
                        runner.observe(record, timeout=10)
                    proof = module['_read_record'](Path(record['terminal_proof_path']))
                    self.assertEqual({}, runner._processes)
                    self.assertEqual(expected, proof['actual_tokens'])
                    self.assertEqual(100, proof['lower_bound_tokens'])
                    ctx.complete(admission, proof)
                    summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
                    self.assertEqual(int(expected is None), summary['unknown_runs'])
                    self.assertEqual(expected or 0, summary['known_tokens'])
                    if enforced:
                        self.assertEqual(1_100_000 if expected is None else 0,
                            summary['unresolved_reserved_tokens'])

    def test_review_launch_persists_and_passes_native_backstop_without_receipt(self):
        from agentic.child_process import CODEX_LOG_RUNNER_POLICY
        from agentic.providers.github_review_host import HostDriver
        from agentic.safeio import run_bounded
        driver = HostDriver.__new__(HostDriver)
        driver.root = self.worker
        driver.state = self.root / 'review-state'
        driver.c = dict(executables=dict(codex=dict(path=sys.executable)), agent_timeout_seconds=10,
            budget_stream='A')
        driver._pending_agent_outcomes = []
        self.host = driver
        ctx = self.context(ticket=4_000_000, project=4_000_000)
        binding = self.binding()
        selected = self.adapter_route(ctx, binding)
        driver.c.update(models=dict(worker=selected['model']),
            reasoning_effort=dict(worker=selected['reasoning_effort']))
        driver.configure_budgeting(ctx, ticket_id=binding['ticket_id'])
        run = driver.state / 'runs' / binding['run_id']
        run.mkdir(parents=True)
        (run / 'input.json').write_text('{"candidate":null}')
        args = ['exec', '--ephemeral', '--json', '--sandbox', 'workspace-write',
            '--model', selected['model'], '-c', 'model_reasoning_effort='+selected['reasoning_effort'],
            '--output-last-message', str(run / 'result.json'), '--output-schema', str(run / 'schema.json'),
            '--cd', str(self.worker), '-']
        driver._budget_launch = (args, self.worker)
        admission, binding = driver._agent_admission('worker', run)
        restored = json.loads((run / 'budget-admission.json').read_text())
        self.assertEqual(admission, restored)
        self.assertEqual(canonical(admission), (run / 'budget-admission.json').read_bytes())
        driver._pre_run_bridges = {}
        ctx = self.context(ticket=4_000_000, project=4_000_000)
        driver.configure_budgeting(ctx, ticket_id=binding['ticket_id'])
        # Admission already persisted above; exercise the existing launch port.
        driver._agent_admission = mock.Mock(return_value=(restored, binding))
        script = self.root / 'fake-review.py'
        script.write_text('import sys\n'
            'assert "features.rollout_budget.limit_tokens=1100000" in sys.argv\n'
            'assert "workspace-write" in sys.argv\n'
            'sys.stdin.read()\n')
        def execute(name, argv, **kwargs):
            self.assertEqual('codex', name)
            result = run_bounded([sys.executable, '-B', str(script), *argv], cwd=self.worker,
                stdin=kwargs['stdin'], policy=CODEX_LOG_RUNNER_POLICY)
            self.assertEqual(0, result.returncode)
        driver.run = mock.Mock(side_effect=execute)
        driver._validated_agent_result = mock.Mock(return_value=(b'{}', {}))
        driver._terminal_context = mock.Mock(return_value='synthetic-thread')
        driver._launch_agent('worker', run, args, 'synthetic')
        driver.run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
