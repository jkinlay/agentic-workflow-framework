"""Controller/review-host budget boundary regressions; observations are fixtures."""
from copy import deepcopy
from pathlib import Path
import json
import os
import runpy
import tempfile
import unittest
import uuid
from unittest.mock import patch

import source_only
from agentic import ValidationError, budgeting
from agentic import operating as op
from agentic.budgeting import BudgetLedger, fresh_settings, project_ledger_path
from agentic.model_routing import RoutingLedger, policy_from_config, select_route
from test_operating import governance
from test_model_routing import request, capabilities, outcome
from settings_pr_fixtures import publish_fixture_settings, accepted_fixture_snapshot
import test_reference_controller_adapter as reference_tests
from test_reference_controller_adapter import _base_config, _dispatch_payload, FakeRunner, FakeClock

ROOT = Path(__file__).resolve().parents[2]


class ObservedBudgetHost(FakeRunner):
    def observe_enrollment(self, binding):
        return deepcopy(binding)

    def observe_budget_admission(self, binding, selected):
        req = request(ticket_id=binding['ticket_id'], role=binding['role'],
                      context_id=None, worker_context_id='independent-writer', stream='A',
                      reservation_tokens=100)
        return {'request': req, 'capabilities': capabilities(), 'limits_enforced': True}

    def observe_budget_outcome(self, admission, observation):
        return outcome(admission['reservation'], actual_tokens=100,
                       actual_context_id=observation['session_id'],
                       independent_review_passed=False,
                       observation_id='usage-' + admission['binding']['run_id'])


class BudgetWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'host-state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.path = self.root / 'routing.sqlite'
        self.host = ObservedBudgetHost()

    def context(self, *, enabled=True, enforcement=False, limit=50):
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=enabled,
            enforcement_enabled=enforcement, max_tokens_per_ticket=limit, max_tokens_per_project=limit)
        publish_fixture_settings(self, self.root / 'worker', self.gov, self.value)
        return budgeting.ControllerBudgeting(self.path, self.gov,
            op.validate_operating(self.value, self.gov), self.host, worktree_roots=[self.root / 'worker'], repository_root=self.root / 'worker')

    def binding(self, role='worker', ticket='EX-10'):
        run = str(uuid.uuid4())
        return dict(project_id=self.gov['project']['id'], ticket_id=ticket,
                    run_id=run, dispatch_id='dispatch-' + run, session_id='host-session-' + run, role=role)

    def test_foreign_snapshot_provenance_cannot_disable_budget_admission(self):
        from dataclasses import replace
        self.context(enabled=True, enforcement=True, limit=50)
        foreign = self.root / 'foreign'
        off = op.default_operating(self.gov['execution']['model_routing'])
        publish_fixture_settings(self, foreign, self.gov, off)
        snapshot = replace(op.validate_operating(self.value, self.gov),
            _provenance_json=op.canonical({'path': str(foreign / op.CONFIG)}))
        op.validate_snapshot(snapshot, self.gov)
        with self.assertRaisesRegex(ValidationError, 'provenance.*repository root'):
            context = budgeting.ControllerBudgeting(self.path, self.gov, snapshot,
                self.host, worktree_roots=[self.root / 'worker'], repository_root=self.root / 'worker')
            self.assertIsNotNone(self.admit(context),
                'Foreign accepted OFF policy bypassed enforced budget admission')

    def test_explicit_root_selects_policy_without_snapshot_path_or_worktree_order(self):
        self.context(enabled=True, enforcement=True, limit=50)
        foreign = self.root / 'foreign'
        off = op.default_operating(self.gov['execution']['model_routing'])
        publish_fixture_settings(self, foreign, self.gov, off)
        context = budgeting.ControllerBudgeting(self.path, self.gov,
            op.validate_operating(off, self.gov), self.host,
            repository_root=self.root / 'worker', worktree_roots=[foreign, self.root / 'worker'])
        self.assertTrue(context.settings['enforcement_enabled'])
        with self.assertRaisesRegex(ValidationError, 'budget exhausted'):
            self.admit(context)

    def test_adapter_factory_refuses_foreign_snapshot_provenance(self):
        self.context(enabled=True, enforcement=True)
        foreign = self.root / 'foreign'
        snapshot = accepted_fixture_snapshot(self, foreign,
            op.default_operating(self.gov['execution']['model_routing']), self.gov)
        adapters = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))['build_adapters'](
            dict(_base_config(self.root), _runner=self.host, _clock=FakeClock()))
        with self.assertRaisesRegex(ValidationError, 'provenance.*repository root'):
            adapters['dispatch_ticket'].configure_budgeting(path=self.path,
                governance=self.gov, operating=snapshot, repository_root=self.root / 'worker')

    def test_production_cycle_refuses_foreign_provenance_before_budget_adapter(self):
        from dataclasses import replace
        fixture = reference_tests.ReferenceControllerAdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        real_cycle = reference_tests.production_controller_cycle
        def cycle(store, **kwargs):
            snapshot = kwargs['planning_operating_snapshot']
            kwargs['planning_operating_snapshot'] = replace(snapshot,
                _provenance_json=op.canonical({'path': str(self.root / 'foreign' / op.CONFIG)}))
            def unexpected_adapter(**ignored):
                self.fail('Foreign provenance reached the budget adapter')
            kwargs['configure_budgeting'] = unexpected_adapter
            return real_cycle(store, **kwargs)
        with patch.object(reference_tests, 'production_controller_cycle', cycle):
            with self.assertRaisesRegex(ValidationError, 'provenance.*repository root'):
                fixture.test_cycle_runs_end_to_end_through_real_adapter_and_strips_credentials()

    def admit(self, context, binding=None):
        binding = binding or self.binding()
        selected = select_route(policy_from_config(self.gov),
            self.host.observe_budget_admission(binding, None)['request'], capabilities(),
            operating=op.validate_operating(self.value, self.gov), governance=self.gov)
        return context.admit(binding, selected)

    def test_off_needs_no_ledger_enrollment_usage_or_reservation(self):
        context = self.context(enabled=False)
        with patch.object(self.host, 'observe_enrollment', side_effect=AssertionError('enrollment')), \
             patch.object(self.host, 'observe_budget_admission', side_effect=AssertionError('admission')), \
             patch('agentic.model_routing.RoutingLedger', side_effect=AssertionError('ledger')):
            self.assertIsNone(context.admit(self.binding(), {}))
            self.assertIsNone(context.complete(None, {}))
        self.assertFalse(self.path.exists())

    def test_accounting_only_records_over_limit_without_refusal(self):
        context = self.context(limit=1)
        admitted = self.admit(context)
        self.assertEqual(context.complete(admitted, {'session_id': 'observed-thread'})['status'], 'settled')
        summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
        self.assertEqual(summary['known_tokens'], 100)
        self.assertEqual(summary['reservations'], 1)

    def test_enforcement_refuses_at_lifetime_limit(self):
        context = self.context(enforcement=True, limit=100)
        admitted = self.admit(context)
        context.complete(admitted, {'session_id': 'observed-thread'})
        with self.assertRaisesRegex(ValidationError, 'limit|budget|Lifetime|Ticket'):
            self.admit(context, self.binding(ticket='EX-20'))
        self.assertEqual(BudgetLedger(self.path, None).summary(self.gov['project']['id'])['known_tokens'], 100)

    def test_controller_and_independent_runs_never_enroll(self):
        context = self.context(enforcement=True, limit=100)
        for role in ('controller', 'independent'):
            with self.subTest(role=role), patch.object(self.host, 'observe_enrollment', side_effect=AssertionError('enrollment')):
                self.assertIsNone(context.admit(self.binding(role), {}))
        admitted = self.admit(context)
        context.complete(admitted, {'session_id': 'observed-thread'})
        self.assertEqual(BudgetLedger(self.path, None).summary(self.gov['project']['id'])['reservations'], 1)

    def test_existing_admission_settles_after_switch_off_and_restart(self):
        admitted = self.admit(self.context())
        context = self.context(enabled=False)
        first = context.complete(admitted, {'session_id': 'observed-thread'})
        self.assertEqual(first['status'], 'settled')
        self.assertEqual(context.complete(admitted, {'session_id': 'observed-thread'}), first)
        self.assertEqual(BudgetLedger(self.path, None).summary(self.gov['project']['id'])['known_tokens'], 100)

    def test_enforcement_requires_host_limits_before_reserving(self):
        context = self.context(enforcement=True, limit=100)
        original = self.host.observe_budget_admission
        self.host.observe_budget_admission = lambda *args: dict(original(*args), limits_enforced=False)
        with self.assertRaisesRegex(ValidationError, 'limit'):
            self.admit(context)
        self.assertFalse(self.path.exists())

    def test_unknown_usage_replay_remains_unknown_after_restart(self):
        context = self.context()
        admitted = self.admit(context)
        original = self.host.observe_budget_outcome
        def unknown(*args):
            observed = original(*args)
            observed.pop('actual_tokens')
            return observed
        self.host.observe_budget_outcome = unknown
        self.assertEqual(context.complete(admitted, {'session_id': 'observed-thread'})['status'], 'settled')
        self.assertEqual(self.context(enabled=False).complete(admitted, {'session_id': 'observed-thread'})['status'], 'settled')
        summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
        self.assertEqual(summary['unknown_runs'], 1)
        self.assertEqual(summary['unresolved_reserved_tokens'], 100)

    def test_changed_observation_cannot_recover_a_settlement(self):
        context = self.context()
        admitted = self.admit(context)
        context.complete(admitted, {'session_id': 'observed-thread'})
        original = self.host.observe_budget_outcome
        self.host.observe_budget_outcome = lambda *args: dict(original(*args), actual_tokens=99)
        with self.assertRaisesRegex(ValidationError, 'replay differs'):
            context.complete(admitted, {'session_id': 'observed-thread'})
        self.assertEqual(BudgetLedger(self.path, None).summary(self.gov['project']['id'])['known_tokens'], 100)

    def test_review_roles_use_same_canonical_ledger(self):
        context = self.context(limit=1)
        for role in ('critic', 'specialist'):
            admitted = self.admit(context, self.binding(role))
            self.assertEqual(admitted['binding']['role'], role)
            self.assertEqual(context.complete(admitted, {'session_id': role + '-thread'})['status'], 'settled')
        self.assertEqual(BudgetLedger(self.path, None).summary(self.gov['project']['id'])['known_tokens'], 200)

    def test_reference_adapter_enrolls_before_launch_and_settles(self):
        config = _base_config(self.root)
        adapters = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))['build_adapters'](
            dict(config, _runner=self.host, _clock=FakeClock()))
        context = self.context(limit=1)
        adapters['dispatch_ticket'].configure_budgeting(context)
        selected = self.admit_route()
        # Fixture adapter role must match the independently reviewed route.
        config['codex']['roles']['writer'].update(model=selected['model'], reasoning_effort=selected['reasoning_effort'])
        adapters = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))['build_adapters'](
            dict(config, _runner=self.host, _clock=FakeClock()))
        adapters['dispatch_ticket'].configure_budgeting(context)
        receipt = adapters['dispatch_ticket'](_dispatch_payload())
        self.assertTrue(receipt['terminal'])
        self.assertEqual(BudgetLedger(self.path, None).summary(self.gov['project']['id'])['known_tokens'], 100)
        record = json.loads((Path(config['codex']['run_record_directory']) / 'isolation.json').read_text())
        self.assertEqual(record['budget_admission']['binding']['dispatch_id'], 'isolation')
        self.assertNotEqual(record['budget_admission']['binding']['session_id'], receipt['session_id'])

    def admit_route(self):
        binding = self.binding()
        return select_route(policy_from_config(self.gov), self.host.observe_budget_admission(binding, None)['request'],
            capabilities(), operating=op.validate_operating(self.value, self.gov), governance=self.gov)

    def test_review_host_exposes_shared_budget_context(self):
        from agentic.providers.github_review_host import HostDriver
        driver = HostDriver(dict(worker_checkout=str(self.root / 'worker'),
            critic_checkout=str(self.root / 'critic'), state_dir=str(self.root / 'review'),
            repository='fixture/project'), self.root)
        context = self.context()
        driver.configure_budgeting(context)
        self.assertIs(driver.budgeting, context)

    def test_validated_context_passes_budget_mode_to_store_and_lifecycle(self):
        from agentic.store import Store
        context = self.context(enabled=False)
        store = Store(self.root / 'state.sqlite', self.gov['project']['id'])
        with patch.object(store, 'advance', return_value=('DISPATCHED', 3)) as advance:
            self.assertEqual(context.advance(store, 'EX-10', 'DISPATCH_ACCEPTED', 2, {}), ('DISPATCHED', 3))
            self.assertEqual(advance.call_args.kwargs['budgeting'], fresh_settings() | {
                'max_tokens_per_ticket': 50, 'max_tokens_per_project': 50})
        facts = dict(dispatch_permitted=True, lease_current=True, ownership_current=True)
        self.assertEqual(context.transition('READY', 'DISPATCH_ACCEPTED', facts), 'DISPATCHED')
        with self.assertRaisesRegex(ValidationError, 'lease_current'):
            context.transition('READY', 'DISPATCH_ACCEPTED', dict(facts, lease_current=False))

    def test_off_production_cycle_never_calls_budget_databases_or_observers(self):
        fixture = reference_tests.ReferenceControllerAdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        with patch('agentic.model_routing.RoutingLedger', side_effect=AssertionError('ledger')), \
             patch('agentic.budget_enrollment.EnrollmentRegistry.enroll', side_effect=AssertionError('enrollment')), \
             patch.object(budgeting.ControllerBudgeting, 'complete', side_effect=AssertionError('settlement')):
            fixture.test_cycle_runs_end_to_end_through_real_adapter_and_strips_credentials()

    def test_review_host_real_agent_enrolls_critic_and_specialist_before_launch(self):
        from agentic.providers.github_review_host import HostDriver
        from agentic.canonical import sha256
        for role in ('critic', 'specialist'):
            with self.subTest(role=role):
                context = self.context(limit=1)
                run_id = str(uuid.uuid4())
                selected = self.admit_route_for(role)
                contract = self.root / 'contract.txt'
                contract.write_text('Fixture scope only', encoding='utf-8')
                driver = HostDriver.__new__(HostDriver)
                driver.root = ROOT
                driver.state = self.root / role
                driver.worker = self.root / 'worker'
                driver.critic = self.root / 'critic'
                driver.c = {'allowed_paths': ['fixture.txt'], 'contract_path': str(contract),
                    'contract_sha256': sha256(contract.read_bytes()), 'models': {'critic': selected['model']},
                    'reasoning_effort': {'critic': selected['reasoning_effort']}, 'agent_timeout_seconds': 10}
                driver.configure_budgeting(context, ticket_id='EX-10')
                candidate = {'repository_id': 101, 'pr': 7, 'head': 'a'*40, 'base': 'b'*40,
                             'head_ref': 'codex/fixture', 'base_ref': 'main'}
                def execute(name, args, **kwargs):
                    run = driver.state / 'runs' / run_id
                    admission = json.loads((run / 'budget-admission.json').read_text())
                    self.assertEqual(admission['binding']['role'], role)
                    self.assertEqual(admission['binding']['session_id'], run_id)
                    (run / 'codex.jsonl').write_text(json.dumps({'type': 'thread.started',
                        'thread_id': '01a1317f-dadb-7071-80de-12832161734c'}) + '\n', encoding='utf-8')
                    (run / 'result.json').write_text(json.dumps({'verdict': 'APPROVE', 'summary': 'Fixture',
                        'findings': [], 'candidate': candidate, 'reviewed_files': ['fixture.txt']}), encoding='utf-8')
                driver.run = execute
                self.assertEqual(driver.agent(role, candidate, [], run_id, ['fixture.txt'])['verdict'], 'APPROVE')
        summary = BudgetLedger(self.path, None).summary(self.gov['project']['id'])
        self.assertEqual(summary['known_tokens'], 200)
        self.assertEqual(summary['reservations'], 2)

    def test_accounting_production_cycle_uses_validated_snapshot_and_canonical_ledger(self):
        fixture = reference_tests.ReferenceControllerAdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.config['codex']['roles']['writer'].update(model='gpt-6.1-sol', reasoning_effort='high')
        real_cycle = reference_tests.production_controller_cycle
        captured = {}
        def cycle(store, **kwargs):
            value = kwargs['planning_operating_snapshot'].config
            value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
                                     max_tokens_per_ticket=1, max_tokens_per_project=1)
            worker = Path(kwargs['repository_root'])
            snapshot = accepted_fixture_snapshot(self, worker, value, kwargs['planning_config'])
            # A worker disables its local file; the real controller load still
            # uses the accepted remote-tracking settings and charges its run.
            (worker / op.GOVERNANCE).parent.mkdir(exist_ok=True)
            (worker / op.GOVERNANCE).write_bytes(op._json(kwargs['planning_config']))
            edited = deepcopy(value)
            edited['budgeting'] = fresh_settings()
            (worker / op.CONFIG).write_bytes(op._json(edited))
            snapshot = op.read_operating(worker, kwargs['planning_config'])
            self.assertTrue(snapshot.config['budgeting']['accounting_enabled'])
            self.assertIsNotNone(snapshot.provenance['protected_settings']['commit'])
            kwargs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
            import subprocess
            kwargs.update(repository_head_sha=subprocess.check_output(['git', '-C', str(worker), 'rev-parse', 'HEAD'], text=True).strip(),
                repository_tree_sha=subprocess.check_output(['git', '-C', str(worker), 'rev-parse', 'HEAD^{tree}'], text=True).strip())
            captured['path'] = project_ledger_path(kwargs['planning_config']['project']['id'], accounting_enabled=True)
            return real_cycle(store, **kwargs)
        with patch.object(reference_tests, 'production_controller_cycle', cycle), \
             patch.object(FakeRunner, 'observe_enrollment', lambda runner, *args: self.host.observe_enrollment(*args), create=True), \
             patch.object(FakeRunner, 'observe_budget_admission', lambda runner, *args: self.host.observe_budget_admission(*args), create=True), \
             patch.object(FakeRunner, 'observe_budget_outcome', lambda runner, *args: self.host.observe_budget_outcome(*args), create=True):
            fixture.test_cycle_runs_end_to_end_through_real_adapter_and_strips_credentials()
        summary = BudgetLedger(captured['path'], None).summary('project-1')
        self.assertEqual(summary['known_tokens'], 100)
        self.assertEqual(summary['reservations'], 1)

    def admit_route_for(self, role):
        binding = self.binding(role)
        return select_route(policy_from_config(self.gov), self.host.observe_budget_admission(binding, None)['request'],
            capabilities(), operating=op.validate_operating(self.value, self.gov), governance=self.gov)


if __name__ == '__main__':
    unittest.main()
