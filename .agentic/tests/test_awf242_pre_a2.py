"""Production-boundary regressions for pre-A2; no budget observers replaced."""
from copy import deepcopy
from pathlib import Path
import json
import os
import runpy
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import source_only
from settings_pr_fixtures import accepted_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.budgeting import BudgetLedger, ControllerBudgeting, fresh_settings
from agentic.providers.github_review_host import HostDriver
from agentic.review_loop import LoopStore, enroll, tick
from test_awf242_prereview import PreReviewTests
from test_operating import governance
from test_review_loop import FakeDriver, record_fixture_codex_identity
from test_model_routing import outcome
from test_awf242_budgeting import FixtureHost
from agentic.budget_enrollment import EnrollmentRegistry
from test_reference_controller_adapter import _base_config, _dispatch_payload, FakeClock
from settings_pr_fixtures import publish_fixture_settings

ROOT = Path(__file__).resolve().parents[2]
RUNNER = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))['_SubprocessRunner']


class SettlementIdentityTests(PreReviewTests):
    def test_explicit_accounting_refuses_missing_observation_identity(self):
        for enforcement in (False, True):
            self.value['budgeting']['enforcement_enabled'] = enforcement
            for index in range(2):
                binding = self.enroll()
                reserved = self.reserve(binding, phase=f'{enforcement}-{index}')
                before = self.row(binding['run_id'])
                with self.assertRaisesRegex(ValidationError, 'observation identity'):
                    self.ledger.settle(binding['run_id'], outcome(reserved))
                self.assertEqual(self.row(binding['run_id']), before)
        self.assertEqual(self.facade.summary(self.project)['known_tokens'], 0)

    def test_equal_usage_with_distinct_observations_counts_both_runs(self):
        for index in range(2):
            reserved = self.reserve(self.enroll(), phase=str(index))
            self.assertTrue(self.ledger.settle(reserved['run_id'],
                outcome(reserved, observation_id='distinct-' + str(index)))['accepted'])
        self.assertEqual(self.facade.summary(self.project)['known_tokens'], 200)


class ProductionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state_home = tempfile.TemporaryDirectory()
        self.addCleanup(state_home.cleanup)
        environment = patch.dict(os.environ, {'AWF_STATE_HOME': state_home.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])

    def test_off_review_continues_past_legacy_total_run_quota(self):
        driver = FakeDriver()
        driver.budgeting = ControllerBudgeting(self.root / 'unused.sqlite', self.gov,
            accepted_fixture_snapshot(self, self.root, self.value, self.gov), None)
        config = dict(key='12:7', repository='fixture/project', config_hash='f'*64,
                      max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=1)
        store = LoopStore(self.root / 'loop')
        self.addCleanup(store.close)
        enroll(store, config, driver.snapshot())
        state = store.get(config['key'])
        state['agent_runs'] = 1
        store.save(state)
        result = tick(store, config, driver)
        self.assertEqual(result['phase'], 'AMEND')
        self.assertEqual(len(driver.calls), 1)
        self.assertFalse((self.root / 'unused.sqlite').exists())

    def test_off_keeps_review_cap_and_legacy_keeps_total_run_quota(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                value = deepcopy(self.value)
                if legacy:
                    value.pop('budgeting')
                driver = FakeDriver()
                driver.budgeting = ControllerBudgeting(self.root / 'unused.sqlite', self.gov,
                    accepted_fixture_snapshot(self, self.root, value, self.gov), None)
                config = dict(key='12:7', repository='fixture/project', config_hash='f'*64,
                    max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=1)
                store = LoopStore(self.root / str(legacy))
                self.addCleanup(store.close)
                enroll(store, config, driver.snapshot())
                state = store.get(config['key'])
                state.update(agent_runs=1, phase='AMEND', cycles=3)
                store.save(state)
                result = tick(store, config, driver)
                self.assertEqual(result['phase'], 'PAUSED')
                self.assertIn('Total agent-run budget exhausted' if legacy else 'REVIEW_CAP_REACHED', result['reason'])
                self.assertEqual(driver.calls, [])

    def test_off_runner_retains_no_usage_telemetry(self):
        runner = RUNNER()
        nonce = str(uuid.uuid4())
        program = ('import json; print(json.dumps({"type":"thread.started","thread_id":"off-thread"})); '
            'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":70,"output_tokens":30}}))')
        launched = runner.launch([sys.executable, '-B', '-c', program], cwd=self.root,
            env=dict(__import__('os').environ), timeout=10, stdin='',
            handoff_path=str(self.root / 'off.handoff.json'),
            terminal_proof_path=str(self.root / 'off.terminal.json'), launch_nonce=nonce)
        result = runner.observe(launched, timeout=10)
        self.assertEqual(result['session_id'], 'off-thread')
        self.assertNotIn('actual_tokens', result)

    def test_real_review_construction_loads_persisted_enforcement(self):
        # Constructor used by the entry point, with real persisted settings.
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
            enforcement_enabled=True, max_tokens_per_ticket=100, max_tokens_per_project=100)
        (self.root / '.agentic').mkdir()
        (self.root / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps(self.gov))
        (self.root / 'OPERATING_CONFIG.yaml').write_text(json.dumps(self.value))
        publish_fixture_settings(self, self.root, self.gov, self.value)
        config = dict(worker_checkout=str(self.root / 'worker'), critic_checkout=str(self.root / 'critic'),
            state_dir=str(self.root / 'state'), repository='fixture/project',
            head_branch='codex/EX-10-fixture')
        driver = HostDriver.from_project(config, self.root)
        self.assertTrue(driver.budgeting.settings['enforcement_enabled'])
        self.assertEqual(driver.budget_ticket_id, 'EX-10')
        record_fixture_codex_identity(driver, str(uuid.uuid4()))
        # Exhaust the very database selected by production construction.
        host = FixtureHost()
        registry = EnrollmentRegistry(self.root / 'seed.sqlite', host)
        binding = dict(project_id=self.gov['project']['id'], ticket_id='EX-10',
            run_id=str(uuid.uuid4()), dispatch_id='seed', session_id='seed', role='worker')
        host.enrollment = binding
        registry.enroll(binding)
        ledger = BudgetLedger(driver.budgeting.database.path, registry)
        ledger.reserve(binding, self.value['budgeting'], 100)
        ledger.settle(binding, 100, 'seed-usage')
        prompts = self.root / '.agentic/review-loop'
        shutil.copytree(ROOT / '.agentic/review-loop', prompts)
        contract = self.root / 'contract.txt'
        contract.write_text('Fixture scope')
        from agentic.canonical import sha256
        driver.c.update(allowed_paths=['fixture.txt'], contract_path=str(contract),
            contract_sha256=sha256(contract.read_bytes()), models={'critic': 'gpt-6.1-sol'},
            reasoning_effort={'critic': 'medium'}, agent_timeout_seconds=10)
        # A real launch would fail because executables are deliberately absent.
        for role in ('critic', 'specialist'):
            with self.subTest(role=role), self.assertRaisesRegex(ValidationError, 'observed host token limits'):
                driver.agent(role, {}, [], str(uuid.uuid4()), ['fixture.txt'])
        self.assertEqual(ledger.summary(self.gov['project']['id'])['known_tokens'], 100)

    def test_legacy_host_without_operating_file_keeps_legacy_controls(self):
        (self.root / '.agentic').mkdir()
        (self.root / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps(self.gov))
        driver = HostDriver.from_project(dict(worker_checkout=str(self.root / 'worker'),
            critic_checkout=str(self.root / 'critic'), state_dir=str(self.root / 'state'),
            repository='fixture/project', head_branch='codex/EX-10-fixture'), self.root)
        self.assertIsNone(driver.budgeting)
        self.assertFalse((self.root / 'state').exists())

    def test_production_review_accounting_enrolls_both_review_roles(self):
        class CliFixture(HostDriver):
            def run(self, name, argv, **kwargs):
                run = Path(kwargs['log']).parent
                payload = json.loads((run / 'input.json').read_text())
                result = dict(candidate=payload['candidate'], verdict='APPROVE',
                    reviewed_files=payload['changed_files'], findings=[], summary='Fixture review')
                (run / 'result.json').write_text(json.dumps(result))
                events = [dict(type='thread.started', thread_id=str(uuid.uuid4())),
                    dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
                (run / 'codex.jsonl').write_text('\n'.join(json.dumps(event) for event in events))
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
            max_tokens_per_ticket=1, max_tokens_per_project=1)
        (self.root / '.agentic').mkdir()
        (self.root / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps(self.gov))
        (self.root / 'OPERATING_CONFIG.yaml').write_text(json.dumps(self.value))
        publish_fixture_settings(self, self.root, self.gov, self.value)
        shutil.copytree(ROOT / '.agentic/review-loop', self.root / '.agentic/review-loop')
        contract = self.root / 'contract.txt'
        contract.write_text('Fixture scope')
        from agentic.canonical import sha256
        config = dict(worker_checkout=str(self.root / 'worker'), critic_checkout=str(self.root / 'critic'),
            state_dir=str(self.root / 'state'), repository='fixture/project', head_branch='codex/EX-10-fixture',
            allowed_paths=['fixture.txt'], contract_path=str(contract), contract_sha256=sha256(contract.read_bytes()),
            models={'critic': 'gpt-6.1-sol'}, reasoning_effort={'critic': 'medium'}, agent_timeout_seconds=10,
            budget_stream='A')
        driver = CliFixture.from_project(config, self.root)
        record_fixture_codex_identity(driver, str(uuid.uuid4()))
        for role in ('critic', 'specialist'):
            run_id = str(uuid.uuid4())
            candidate = dict(repository_id=12, pr=7, head='a'*40, base='b'*40,
                head_ref='codex/EX-10-fixture', base_ref='main')
            self.assertEqual(driver.agent(role, candidate, [], run_id, ['fixture.txt'])['verdict'], 'APPROVE')
            admission = json.loads((driver.state / 'runs' / run_id / 'budget-admission.json').read_text())
            self.assertEqual(admission['binding']['role'], role)
            self.assertTrue(driver.budgeting.registry.enrolled(admission['binding']))
        self.assertEqual(BudgetLedger(driver.budgeting.database.path, driver.budgeting.registry)
            .summary(self.gov['project']['id'])['known_tokens'], 200)

    def test_real_runner_observes_bound_process_and_usage(self):
        runner = RUNNER(record_dir=self.root)
        nonce = str(uuid.uuid4())
        binding = dict(project_id='project-1', ticket_id='EX-10', run_id=nonce,
            dispatch_id='dispatch', session_id=nonce, role='worker')
        record = dict(format='awf-reference-dispatch-2', dispatch_id='dispatch',
            ticket='EX-10', launch_nonce=nonce, role='writer', stream='A', status='PREPARED',
            argv=['exec', '--model', 'gpt-6.1-sol', '-c', 'model_reasoning_effort=high'],
            budget_admission=dict(binding=binding))
        (self.root / 'dispatch.json').write_text(json.dumps(record))
        observed = runner.observe_budget_admission(binding, dict(model='gpt-6.1-sol', reasoning_effort='high'))
        self.assertFalse(observed['limits_enforced'])
        self.assertIsNone(observed['request']['reservation_tokens'])
        self.assertEqual(runner.observe_enrollment(binding), binding)
        handoff, terminal = self.root / 'dispatch.handoff.json', self.root / 'dispatch.terminal.json'
        program = ('import json; print(json.dumps({"type":"thread.started","thread_id":"fixture-thread"})); '
            'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":70,"cached_input_tokens":20,"output_tokens":30}}))')
        launched = runner.launch([sys.executable, '-B', '-c', program], cwd=self.root,
            env=dict(__import__('os').environ), timeout=10, stdin='', handoff_path=str(handoff),
            terminal_proof_path=str(terminal), launch_nonce=nonce)
        record.update(launched)
        result = runner.observe(record, timeout=10)
        (self.root / 'dispatch.json').write_text(json.dumps(record))
        admission = dict(binding=binding, reservation=dict(model='gpt-6.1-sol', reasoning_effort='high'))
        usage = runner.observe_budget_outcome(admission, result)
        self.assertEqual(usage['actual_tokens'], 100)
        self.assertEqual(usage['actual_context_id'], 'fixture-thread')
        self.assertTrue(usage['observation_id'])
        with self.assertRaises(ValidationError):
            runner.observe_budget_outcome(admission, dict(result, session_id='foreign'))

    def test_production_runner_exposes_observers_without_injection(self):
        runner = RUNNER()
        for method in ('observe_enrollment', 'observe_budget_admission', 'observe_budget_outcome'):
            self.assertTrue(callable(getattr(runner, method, None)), method)

    def test_enabled_adapter_uses_real_runner_observers_and_durable_receipts(self):
        namespace = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        # Only the external CLI is a fixture. Budgeting, watcher, enrollment,
        # startup/terminal observation and settlement run production code.
        class CliFixture(namespace['_SubprocessRunner']):
            def launch(self, argv, **kwargs):
                self.launches += 1
                thread = 'thread-' + kwargs['launch_nonce']
                events = [dict(type='thread.started', thread_id=thread)]
                if self.usage:
                    events.append(dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30)))
                program = 'import json; events=' + repr(events) + '; [print(json.dumps(e)) for e in events]'
                return super().launch([sys.executable, '-B', '-c', program], **kwargs)

        config = _base_config(self.root)
        config['codex']['roles']['writer'].update(model='gpt-6.1-sol', reasoning_effort='medium')
        runner = CliFixture(record_dir=Path(config['codex']['run_record_directory']))
        runner.launches, runner.usage = 0, True
        adapters = namespace['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
            max_tokens_per_ticket=1, max_tokens_per_project=1)
        path = self.root / 'canonical.sqlite'
        def configure():
            return adapters['dispatch_ticket'].configure_budgeting(path=path, governance=self.gov,
                operating=accepted_fixture_snapshot(self, self.root, self.value, self.gov))
        context = configure()
        for name, usage in (('known', True), ('unknown', False)):
            runner.usage = usage
            payload = _dispatch_payload(name)
            receipt = adapters['dispatch_ticket'](payload)
            self.assertEqual(receipt['dispatch_state'], 'LAUNCHED')
            record = json.loads((runner._budget_record_dir / (name + '.json')).read_text())
            runner.observe(record, timeout=10)
            settled = adapters['observe_dispatch'](payload)
            self.assertTrue(settled['terminal'])
        summary = BudgetLedger(path, context.registry).summary(self.gov['project']['id'])
        self.assertEqual(summary['known_tokens'], 100)
        self.assertEqual(summary['unknown_runs'], 1)
        self.assertEqual(summary['reservations'], 2)
        self.value['budgeting']['enforcement_enabled'] = True
        configure()
        with self.assertRaisesRegex(ValidationError, 'observed host token limits'):
            adapters['dispatch_ticket'](_dispatch_payload('enforced'))
        self.assertEqual(runner.launches, 2)


if __name__ == '__main__':
    unittest.main()
