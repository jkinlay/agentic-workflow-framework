"""Dormant production-adapter integration; synthetic receipts only."""
from copy import deepcopy
import os
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock
import uuid

import source_only  # noqa: F401
from agentic import ValidationError
from agentic.budgeting import budget_readiness, fresh_settings
from agentic.canonical import canonical, loads
from agentic.token_limiter import AttemptJournal, digest
from agentic.providers.codex_subprocess_limiter import CodexSubprocessLimiter
from agentic.providers.github_review_host import HostDriver

ROOT = Path(__file__).resolve().parents[2]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='awf315-integration-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binding = dict(project_id='synthetic-project', ticket_id='SYN-315',
            run_id=str(uuid.uuid4()), dispatch_id='dispatch', session_id='synthetic-session', role='worker')
        self.selected = dict(model='gpt-6.1-sol', reasoning_effort='high')
        self.argv = ['exec', '--ephemeral', '--sandbox', 'workspace-write',
            '--model', self.selected['model'], '-c', 'model_reasoning_effort=high', '--json', '-']
        self.host = mock.Mock()
        self.host.observe_estimate.return_value = dict(minutes=1, difficulty='low')
        self.host.observe_runtime.side_effect = lambda launch: dict(cli_version='synthetic',
            platform='synthetic-platform', effective_config_sha256='a'*64,
            applied_launch_sha256=digest(launch))
        self.qualified = False
        self.host.observe_qualification.side_effect = self.receipt
        self.limiter = CodexSubprocessLimiter(AttemptJournal(self.root / 'host/attempts.sqlite'),
            self.host, command=(sys.executable,), project_root=self.root, clock=lambda: 100)
        self.reserved = dict(binding=self.binding, reservation_id='reserved-run',
            reservation_tokens=300000, policy_hash='b'*64, operating_hash='c'*64)
        self.host.observe_reservation.return_value = self.reserved

    def receipt(self, context):
        return (dict(version=1, status='QUALIFIED', qualified_for=deepcopy(context),
            receipt_id='synthetic-receipt', evidence_sha256='d'*64, expires_at=200,
            strict_total_tokens=True) if self.qualified else None)

    def observe(self):
        return self.limiter.admission(self.binding, self.selected,
            dict(reservation_tokens=None), argv=self.argv, cwd=self.root)

    def test_absent_receipt_keeps_legacy_unknown_estimate(self):
        observed = self.observe()
        self.assertFalse(observed['limits_enforced'])
        self.assertIsNone(observed['request']['reservation_tokens'])
        self.assertFalse(self.limiter.journal.path.exists())

    def test_absent_estimate_cannot_claim_enforcement(self):
        self.qualified = True
        self.host.observe_estimate.return_value = None
        self.assertFalse(self.observe()['limits_enforced'])
        self.host.observe_qualification.assert_not_called()

    def test_forged_or_expired_receipt_cannot_claim_enforcement(self):
        self.qualified = True
        for mutation in (dict(expires_at=100), dict(strict_total_tokens=False), dict(status='UNQUALIFIED')):
            with self.subTest(mutation=mutation):
                self.host.observe_qualification.side_effect = lambda context: dict(self.receipt(context), **mutation)
                with self.assertRaises(ValidationError):
                    self.observe()
        self.assertFalse(self.limiter.journal.path.exists())

    def test_qualified_estimate_binds_native_limit_and_actual_launch(self):
        self.qualified = True
        observed = self.observe()
        self.assertTrue(observed['limits_enforced'])
        self.assertEqual(300000, observed['request']['reservation_tokens'])
        plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
        args = self.limiter.launch(plan, argv=self.argv, cwd=self.root)
        self.assertIn('features.rollout_budget.limit_tokens=300000', args)
        self.assertIn('workspace-write', args)
        self.assertEqual('RUNNING', self.limiter.journal.read(plan['attempt_id'])['snapshot']['state'])
        result = self.limiter.complete(plan, actual_tokens=42, success=True)
        self.assertEqual(42, result['actual_tokens'])
        with self.assertRaises(ValidationError):
            self.limiter.launch(plan, argv=self.argv, cwd=self.root)

    def test_other_project_root_cannot_reuse_prepared_qualification(self):
        self.qualified = True
        self.observe()
        plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
        other = self.root / 'other-root'
        other.mkdir()
        self.limiter.project_root = other
        with self.assertRaisesRegex(ValidationError, 'root'):
            self.limiter.launch(plan, argv=self.argv, cwd=other)

    def test_standalone_launcher_cannot_consume_adapter_qualification(self):
        self.qualified = True
        self.observe()
        plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
        with mock.patch('agentic.providers.codex_token_limiter.run_bounded') as process:
            with self.assertRaisesRegex(ValidationError, 'qualification'):
                self.limiter.run(plan['attempt_id'], prompt='synthetic', cwd=self.root)
            process.assert_not_called()
        self.assertEqual('PREPARED', self.limiter.journal.read(plan['attempt_id'])['snapshot']['state'])

    def test_receipt_expiry_route_config_and_reservation_drift_refuse(self):
        for drift in ('receipt', 'argv', 'reservation', 'config'):
            with self.subTest(drift=drift):
                self.setUp()
                self.qualified = True
                self.observe()
                plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
                args = list(self.argv)
                if drift == 'receipt':
                    self.qualified = False
                elif drift == 'argv':
                    args.insert(1, '--resume')
                elif drift == 'reservation':
                    self.host.observe_reservation.return_value = dict(self.reserved, reservation_tokens=300001)
                else:
                    self.host.observe_runtime.side_effect = lambda launch: dict(cli_version='changed',
                        platform='synthetic-platform', effective_config_sha256='a'*64,
                        applied_launch_sha256=digest(launch))
                with self.assertRaises(ValidationError):
                    self.limiter.launch(plan, argv=args, cwd=self.root)

    def test_reference_adapter_uses_trusted_limiter_only_when_enrolled(self):
        module = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        runner = module['_SubprocessRunner'](record_dir=self.root)
        record = dict(format='awf-reference-dispatch-2', dispatch_id='dispatch', ticket='SYN-315',
            launch_nonce=self.binding['run_id'], role='writer', stream='A', argv=self.argv,
            worktree=str(self.root))
        (self.root / 'dispatch.json').write_text(json.dumps(record))
        self.binding['session_id'] = self.binding['run_id']
        old = runner.observe_budget_admission(self.binding, self.selected)
        self.assertFalse(old['limits_enforced'])
        runner.configure_token_limiter(lambda binding, cwd: self.limiter)
        self.qualified = True
        observed = runner.observe_budget_admission(self.binding, self.selected)
        self.assertTrue(observed['limits_enforced'])
        self.assertEqual(300000, observed['request']['reservation_tokens'])

    def test_review_adapter_uses_same_qualification_boundary(self):
        driver = HostDriver.__new__(HostDriver)
        driver.c = {}
        driver.observe_enrollment = mock.Mock()
        driver._budget_execution = mock.Mock(return_value=self.root)
        (self.root / 'input.json').write_text(json.dumps(dict(candidate=None)))
        driver.configure_token_limiter(lambda binding, cwd: self.limiter)
        driver._budget_launch = (self.argv, self.root)
        self.qualified = True
        observed = driver.observe_budget_admission(self.binding, self.selected)
        self.assertTrue(observed['limits_enforced'])
        self.host.observe_runtime.side_effect = lambda launch: None
        self.assertFalse(driver.observe_budget_admission(self.binding, self.selected)['limits_enforced'])

    def test_unavailable_optional_limiter_keeps_reference_admission_unenforced(self):
        module = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        runner = module['_SubprocessRunner'](record_dir=self.root)
        self.binding['session_id'] = self.binding['run_id']
        record = dict(format='awf-reference-dispatch-2', dispatch_id='dispatch', ticket='SYN-315',
            launch_nonce=self.binding['run_id'], role='writer', stream='A', argv=self.argv,
            worktree=str(self.root))
        (self.root / 'dispatch.json').write_text(json.dumps(record))
        runner.configure_token_limiter(lambda binding, cwd: self.limiter)
        self.host.observe_runtime.side_effect = lambda launch: None
        observed = runner.observe_budget_admission(self.binding, self.selected)
        self.assertFalse(observed['limits_enforced'])
        self.assertIsNone(observed['request']['reservation_tokens'])

    def test_review_launch_applies_flags_before_existing_process_runner(self):
        self.qualified = True
        self.argv = self.argv[:-1] + ['--output-last-message', str(self.root / 'result.json'),
            '--output-schema', str(self.root / 'schema.json'), '--cd', str(self.root), '-']
        self.observe()
        plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
        driver = HostDriver.__new__(HostDriver)
        driver.root = self.root
        driver.c = dict(executables=dict(codex=dict(path=sys.executable)), agent_timeout_seconds=10)
        driver.budgeting = mock.Mock()
        driver._pending_agent_outcomes = []
        driver._agent_admission = mock.Mock(return_value=(dict(binding=self.binding, limiter_plan=plan), self.binding))
        driver.run = mock.Mock()
        driver._validated_agent_result = mock.Mock(return_value=(b'{}', {}))
        driver._terminal_context = mock.Mock(return_value='synthetic-thread')
        driver.configure_token_limiter(lambda binding, cwd: self.limiter)
        driver._launch_agent('worker', self.root, self.argv, 'synthetic')
        self.assertIn('features.rollout_budget.limit_tokens=300000', driver.run.call_args.args[1])
        self.assertIn('workspace-write', driver.run.call_args.args[1])

    def test_readiness_names_missing_prerequisites_without_blocking_off(self):
        self.assertEqual('OFF', budget_readiness(fresh_settings())['status'])
        settings = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        report = budget_readiness(settings)
        self.assertEqual('BUDGET_NOT_READY', report['status'])
        self.assertIn('qualification_receipt', report['missing'])
        self.assertIn('reservation_estimate', report['missing'])
        self.assertEqual('UNSUPPORTED_PROVIDER', budget_readiness(settings, provider='claude')['reason'])

    def test_controller_reserves_estimator_tokens_and_binds_observed_reservation(self):
        import test_awf242_controller_wiring as wiring
        fixture = wiring.BudgetWiringTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.context(enforcement=True, limit=1000000)
        binding = fixture.binding()
        self.binding = binding
        self.qualified = True
        route = dict(model='gpt-5.6-luna', reasoning_effort='medium')
        self.argv[self.argv.index('--model') + 1] = route['model']
        self.argv[self.argv.index('model_reasoning_effort=high')] = 'model_reasoning_effort=medium'
        fixture.host.observe_budget_admission = lambda b, s: self.limiter.admission(b, s,
            wiring.request(ticket_id=b['ticket_id'], role=b['role'], context_id=None,
                reservation_tokens=None, stream='A'), argv=self.argv, cwd=self.root)
        def bind(admission):
            self.host.observe_reservation.return_value = dict(self.reserved,
                binding=binding, reservation_id=admission['reservation']['run_id'])
            return self.limiter.reserve(binding, admission['reservation'])
        fixture.host.bind_budget_reservation = bind
        result = context.admit(binding, route)
        self.assertEqual(300000, result['reservation']['reservation_tokens'])
        self.assertTrue(result['limiter_plan']['limits_enforced'])
        self.assertEqual(result['reservation']['run_id'], result['limiter_plan']['reservation_id'])

    def test_accounting_only_never_binds_or_applies_native_enforcement(self):
        import test_awf242_controller_wiring as wiring
        fixture = wiring.BudgetWiringTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.context(enforcement=False, limit=1)
        fixture.host.bind_budget_reservation = mock.Mock(side_effect=AssertionError('Enforcement is OFF'))
        admitted = fixture.admit(context)
        self.assertNotIn('limiter_plan', admitted)
        fixture.host.bind_budget_reservation.assert_not_called()

    def test_claude_enforcement_refuses_before_host_or_ledger(self):
        import test_awf242_controller_wiring as wiring
        fixture = wiring.BudgetWiringTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.context(enforcement=True, limit=1000000)
        with mock.patch.object(fixture.host, 'observe_budget_admission') as observer:
            with self.assertRaisesRegex(ValidationError, 'BUDGET_NOT_READY.*UNSUPPORTED_PROVIDER'):
                context.admit(fixture.binding(), dict(model='claude-synthetic', reasoning_effort='high'))
            observer.assert_not_called()
        self.assertFalse(fixture.path.exists())

    def test_qualified_reference_launch_executes_native_flags_and_settles_usage(self):
        self.qualified_reference_launch()

    def external_journal(self):
        protected = tempfile.TemporaryDirectory(prefix='awf315-protected-')
        self.addCleanup(protected.cleanup)
        self.limiter.journal = AttemptJournal(Path(protected.name) / 'attempts.sqlite',
            worktree_roots=(self.root,))

    def restart_limiter(self):
        self.limiter = CodexSubprocessLimiter(AttemptJournal(self.limiter.journal.path,
            worktree_roots=(self.root,)), self.host, command=self.limiter.command,
            project_root=self.root, clock=lambda: 100)

    def test_reference_persistence_reload_and_launch(self):
        self.qualified_reference_launch()

    def test_review_persistence_reload_and_launch(self):
        from agentic.child_process import CODEX_LOG_RUNNER_POLICY
        from agentic.safeio import run_bounded
        self.external_journal()
        driver = HostDriver.__new__(HostDriver)
        driver.root = self.root
        driver.state = self.limiter.journal.path.parent / 'review-state'
        run = driver.state / 'runs' / self.binding['run_id']
        run.mkdir(parents=True)
        self.binding.update(dispatch_id=run.name, session_id=run.name)
        driver.budget_ticket_id = self.binding['ticket_id']
        driver.c = dict(executables=dict(codex=dict(path=sys.executable)),
            models=dict(worker=self.selected['model']),
            reasoning_effort=dict(worker=self.selected['reasoning_effort']), agent_timeout_seconds=10)
        driver.budgeting = mock.Mock()
        driver.budgeting.settings = {}
        driver.budgeting.governance = dict(project=dict(id=self.binding['project_id']))
        driver._pending_agent_outcomes = []
        self.argv = self.argv[:-1] + ['--output-last-message', str(run / 'result.json'),
            '--output-schema', str(run / 'schema.json'), '--cd', str(self.root), '-']
        self.qualified = True
        self.observe()
        plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
        admission = dict(binding=self.binding, reservation=dict(run_id='reserved-run',
            reservation_tokens=300000), limiter_plan=plan)
        driver.budgeting.admit.return_value = admission
        saved, binding = driver._agent_admission('worker', run)
        restored = loads((run / 'budget-admission.json').read_text())
        self.assertEqual(admission, saved)
        self.assertEqual(admission, restored)
        self.assertEqual(self.binding, binding)
        self.assertEqual(canonical(admission), (run / 'budget-admission.json').read_bytes())
        self.restart_limiter()
        self.assertEqual(restored['limiter_plan'], self.limiter.journal.read(plan['attempt_id'])['plan'])
        driver.budgeting.admit.return_value = restored
        driver.configure_token_limiter(lambda binding, cwd: self.limiter)
        script = self.root / 'fake-review.py'
        script.write_text('import sys,json\n'
            'for setting in ("limit_tokens=300000", "sampling_token_weight=1.0", "prefill_token_weight=1.0"):\n'
            ' assert "features.rollout_budget." + setting in sys.argv\n'
            'assert "workspace-write" in sys.argv\n'
            'sys.stdin.read()\n'
            'print("synthetic-launched")\n')
        def execute(name, args, **kwargs):
            self.assertEqual('codex', name)
            result = run_bounded([sys.executable, '-B', str(script), *args], cwd=self.root,
                stdin=kwargs['stdin'], policy=CODEX_LOG_RUNNER_POLICY)
            self.assertEqual(0, result.returncode)
            self.assertIn(b'synthetic-launched', result.stdout)
        driver.run = mock.Mock(side_effect=execute)
        driver._validated_agent_result = mock.Mock(return_value=(b'{}', {}))
        driver._terminal_context = mock.Mock(return_value='synthetic-thread')
        driver._launch_agent('worker', run, self.argv, 'synthetic')
        driver.run.assert_called_once()
        self.assertEqual(restored, loads((run / 'budget-admission.json').read_text()))
        self.assertEqual('RUNNING', self.limiter.journal.read(plan['attempt_id'])['snapshot']['state'])
        self.assertEqual(42, self.limiter.complete(restored['limiter_plan'],
            actual_tokens=42, success=True)['actual_tokens'])

    def test_qualified_detached_watcher_rejects_interrupted_prefix(self):
        self.qualified_reference_launch(tail='print(json.dumps({"type":"turn.started"}),flush=True)\n',
                                        expected=None)

    def test_qualified_detached_watcher_rejects_malformed_and_duplicate_tail(self):
        for tail in ('print("not-json",flush=True)\n',
                     'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":70,"output_tokens":30}}),flush=True)\n'):
            with self.subTest(tail=tail):
                self.setUp()
                self.qualified_reference_launch(tail=tail, expected=None)

    def test_qualified_detached_watcher_bounds_completed_event_inventory(self):
        self.qualified_reference_launch(tail='for i in range(32768):\n'
            ' print(json.dumps({"type":"turn.started"}))\n'
            ' print(json.dumps({"type":"turn.completed","synthetic_sequence":i,'
            '"usage":{"input_tokens":0,"output_tokens":1}}))\n', expected=None)

    def qualified_reference_launch(self, *, tail='', expected=100):
        self.external_journal()
        module = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        runner = module['_SubprocessRunner'](record_dir=self.root)
        script = self.root / 'fake.py'
        script.write_text('import sys,json\n'
            'assert "features.rollout_budget.limit_tokens=300000" in sys.argv\n'
            'assert "features.rollout_budget.sampling_token_weight=1.0" in sys.argv\n'
            'assert "features.rollout_budget.prefill_token_weight=1.0" in sys.argv\n'
            'sys.stdin.read()\n'
            'for event in [{"type":"thread.started","thread_id":"synthetic-thread"},'
            '{"type":"turn.started"},{"type":"turn.completed",'
            '"usage":{"input_tokens":70,"output_tokens":30}}]: print(json.dumps(event),flush=True)\n' + tail)
        self.limiter.command = (sys.executable, '-B', str(script))
        self.qualified = True
        self.observe()
        plan = self.limiter.reserve(self.binding, dict(run_id='reserved-run', reservation_tokens=300000))
        admission = dict(binding=self.binding, reservation=dict(run_id='reserved-run',
            reservation_tokens=300000), limiter_plan=plan)
        record = dict(budget_admission=admission)
        module['_write_record'](self.root / 'dispatch.json', record)
        restored = module['_read_record'](self.root / 'dispatch.json')
        self.assertEqual(record, restored)
        plan = restored['budget_admission']['limiter_plan']
        self.restart_limiter()
        self.assertEqual(plan, self.limiter.journal.read(plan['attempt_id'])['plan'])
        runner.configure_token_limiter(lambda binding, cwd: self.limiter)
        launched = runner.launch([*self.limiter.command, *self.argv], cwd=self.root,
            env=dict(os.environ), timeout=10, stdin='synthetic',
            handoff_path=str(self.root / 'dispatch.handoff.json'),
            terminal_proof_path=str(self.root / 'dispatch.terminal.json'),
            launch_nonce=self.binding['run_id'])
        proof = runner.observe(dict(launched), timeout=10)
        self.assertEqual(expected, proof['actual_tokens'])
        self.assertEqual(expected, self.limiter.complete(plan, actual_tokens=proof['actual_tokens'], success=True)['actual_tokens'])
        self.assertEqual({}, runner._processes)

    def test_enabled_readiness_is_reported_without_rejecting_configuration(self):
        from agentic.host_preflight import preflight
        from agentic import operating as op
        from test_operating import governance
        from settings_pr_fixtures import publish_fixture_settings
        gov = governance()
        value = op.default_operating(gov['execution']['model_routing'])
        value['budgeting'] = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        publish_fixture_settings(self, self.root, gov, value)
        result = preflight(self.root, platform='posix')
        readiness = next(row for row in result['rows'] if row['check'] == 'budget_readiness')
        self.assertEqual('WARN', readiness['status'])
        self.assertIn('BUDGET_NOT_READY', readiness['detail'])


if __name__ == '__main__':
    unittest.main()
