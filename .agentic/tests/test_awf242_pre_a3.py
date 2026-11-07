"""Pre-A3 production construction: only external CLI/provider responses are fixtures."""
from copy import deepcopy
from pathlib import Path
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import source_only
from owner_admission_fixtures import confirmed_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.budgeting import BudgetLedger, fresh_settings, project_ledger_path
from agentic.canonical import sha256
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from agentic.providers.github_review_host import HostDriver
from agentic.model_routing import policy_from_config
from test_operating import governance
from test_reference_controller_adapter import (
    _base_config, _dispatch_payload, _ticket, FakeClock, FakeRunner, NOW,
)
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs
from test_review_loop import record_fixture_codex_identity
from owner_admission_fixtures import record_fixture_owner_confirmation

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))


class ReviewCli(HostDriver):
    """Replace the CLI boundary; admission, identity and settlement stay real."""
    thread = None

    def run(self, name, argv, **kwargs):
        run = Path(kwargs['log']).parent
        payload = json.loads((run / 'input.json').read_text())
        worker = 'worker-result.schema.json' in ' '.join(argv)
        result = dict(candidate=payload['candidate'], summary='Fixture execution')
        result.update(dict(outcome='NO_CHANGE') if worker else
                      dict(verdict='APPROVE', reviewed_files=payload['changed_files'], findings=[]))
        (run / 'result.json').write_text(json.dumps(result))
        events = [dict(type='thread.started', thread_id=self.thread or str(uuid.uuid4())),
                  dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
        (run / 'codex.jsonl').write_text('\n'.join(map(json.dumps, events)))


class ControllerCli(ADAPTER['_SubprocessRunner']):
    """Real detached watcher and process, with a tiny local CLI stand-in."""
    exit_code = 0
    usage = True
    delay = False
    emit_thread = True

    def run(self, argv, **kwargs):
        # Retain the existing fixture's bounded provider responses and real Git.
        return FakeRunner().run(argv, **kwargs)

    def launch(self, argv, **kwargs):
        events = []
        if self.emit_thread:
            events.append(dict(type='thread.started', thread_id=str(uuid.uuid4())))
        if self.usage:
            events.append(dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30)))
        program = ('import json,time; events=' + repr(events) +
                   '; [print(json.dumps(e),flush=True) for e in events]; '
                   'time.sleep(' + ('0.2' if self.delay else '0') + '); raise SystemExit(' + str(self.exit_code) + ')')
        return super().launch([sys.executable, '-B', '-c', program], **kwargs)


class ProductionPreA3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # A host-owned project state home, independent of any checkout/state DB.
        environment = patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'host-state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        self.value['budgeting'] = dict(fresh_settings(), accounting_enabled=True)
        self.candidate = dict(repository_id=101, pr=7, head='a'*40, base='b'*40,
                              head_ref='codex/EX-10-fixture', base_ref='main')

    def review(self, *, ticket='EX-10', state='review', project=None, ledger_path=None):
        project = project or self.root / 'installed'
        (project / '.agentic').mkdir(parents=True, exist_ok=True)
        (project / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps(self.gov))
        (project / 'OPERATING_CONFIG.yaml').write_text(json.dumps(self.value))
        record_fixture_owner_confirmation(self, project, self.gov, self.value)
        if not (project / '.agentic/review-loop').exists():
            shutil.copytree(ROOT / '.agentic/review-loop', project / '.agentic/review-loop')
        contract = project / 'contract.txt'
        contract.write_text('Fixture scope')
        config = dict(worker_checkout=str(self.root / 'worker'), critic_checkout=str(self.root / 'critic'),
            state_dir=str(self.root / state), repository='example/project', head_branch=f'codex/{ticket}-fixture',
            allowed_paths=['fixture.txt'], contract_path=str(contract), contract_sha256=sha256(contract.read_bytes()),
            models={'worker': 'gpt-6.1-sol', 'critic': 'gpt-6.1-sol'},
            reasoning_effort={'worker': policy_from_config(self.gov)['risk_route']['reasoning_effort'],
                              'critic': self.value['critic']['reasoning_effort']},
            budget_stream='A', agent_timeout_seconds=10)
        if ledger_path is not None:
            config['budget_ledger_path'] = str(ledger_path)
        return ReviewCli.from_project(config, project)

    def agent(self, driver, role):
        run = str(uuid.uuid4())
        driver.agent(role, self.candidate, [], run, ['fixture.txt'])
        return run

    def summary(self, driver):
        return BudgetLedger(driver.budgeting.database.path, driver.budgeting.registry).summary(self.gov['project']['id'])

    def test_worker_critic_worker_critic_binds_latest_worker_after_restart(self):
        driver = self.review()
        self.agent(driver, 'worker')
        self.agent(driver, 'critic')
        worker = self.agent(driver, 'worker')
        driver = self.review()
        critic = self.agent(driver, 'critic')
        admission = json.loads((driver.state / 'runs' / critic / 'budget-admission.json').read_text())
        with driver.budgeting._ledger()._transaction() as db:
            request = json.loads(db.execute('SELECT request FROM model_runs WHERE run_id=?', (critic,)).fetchone()[0])
        self.assertEqual(request['worker_context_id'], driver.codex_run_id(worker))
        self.assertEqual(self.summary(driver)['known_tokens'], 400)

    def test_fresh_host_critic_and_specialist_need_no_budgeted_worker(self):
        driver = self.review()
        critic = self.agent(driver, 'critic')
        self.agent(driver, 'specialist')
        with driver.budgeting._ledger()._transaction() as db:
            request = json.loads(db.execute('SELECT request FROM model_runs WHERE run_id=?', (critic,)).fetchone()[0])
        self.assertEqual(request['worker_context_id'], 'UNKNOWN')
        self.assertFalse(request['worker_context_observed'])
        self.assertEqual(self.summary(driver)['known_tokens'], 200)

    def test_off_worker_context_reuse_still_quarantines_review(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        worker = self.agent(driver, 'worker')
        self.assertFalse(driver.budgeting.database.path.exists())
        self.value['budgeting']['accounting_enabled'] = True
        driver = self.review()
        driver.thread = driver.codex_run_id(worker)
        with self.assertRaisesRegex(ValidationError, 'quarantined'):
            self.agent(driver, 'critic')
        with driver.budgeting._ledger()._transaction() as db:
            row = db.execute('SELECT status,outcome FROM model_runs').fetchone()
        self.assertEqual(row['status'], 'quarantined')
        self.assertIn('host review context is not independent', json.loads(row['outcome'])['violations'])
        self.assertEqual(self.summary(driver)['known_tokens'], 100)

    def test_latest_worker_context_reuse_still_refused_without_double_charge(self):
        driver = self.review()
        self.agent(driver, 'worker')
        self.agent(driver, 'critic')
        worker = self.agent(driver, 'worker')
        driver.thread = driver.codex_run_id(worker)
        # The immutable observation belongs to the worker already. Reusing it
        # must fail even before the ledger's context-quarantine check.
        with self.assertRaisesRegex(ValidationError, 'Usage observation already belongs'):
            self.agent(driver, 'critic')
        self.assertEqual(self.summary(driver)['known_tokens'], 300)

    def adapter(self):
        config = _base_config(self.root)
        config['codex']['roles']['writer'].update(model='gpt-6.1-sol', reasoning_effort='medium')
        runner = ControllerCli(record_dir=config['codex']['run_record_directory'])
        adapters = ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        return config, runner, adapters

    def failed_completion(self, *, usage, delay, emit_thread=True):
        config, runner, adapters = self.adapter()
        runner.exit_code, runner.usage, runner.delay, runner.emit_thread = 1, usage, delay, emit_thread
        context = adapters['dispatch_ticket'].configure_budgeting(path=self.root / 'ledger.sqlite',
            governance=self.gov, operating=confirmed_fixture_snapshot(self, self.root, self.value, self.gov))
        payload = _dispatch_payload('failure')
        try:
            adapters['dispatch_ticket'](payload)
            adapters['observe_dispatch'](payload)
        except ValidationError as exc:
            self.assertRegex(str(exc), 'non-zero exit')
        else:
            self.fail('Failed CLI must remain a failed dispatch')
        ledger = BudgetLedger(context.database.path, context.registry)
        summary = ledger.summary(self.gov['project']['id'])
        self.assertEqual(summary['known_tokens'], 100 if usage else 0)
        self.assertEqual(summary['unknown_runs'], 0 if usage else 1)
        with context._ledger()._transaction() as db:
            row = dict(db.execute('SELECT * FROM model_runs').fetchone())
        self.assertEqual(row['status'], 'settled')
        outcome = json.loads(row['outcome'])
        self.assertFalse(outcome['success'])
        self.assertEqual(outcome['failure_kind'], 'unknown')
        record = json.loads((Path(config['codex']['run_record_directory']) / 'failure.json').read_text())
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['terminal_failure']['returncode'], 1)
        # A restarted OFF host must retain and replay the failed settlement.
        restarted = ADAPTER['build_adapters'](dict(config, _clock=FakeClock()))
        off = deepcopy(self.value)
        off['budgeting']['accounting_enabled'] = False
        restarted['dispatch_ticket'].configure_budgeting(path=context.database.path,
            governance=self.gov, operating=confirmed_fixture_snapshot(self, self.root, off, self.gov))
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            restarted['observe_dispatch'](payload)
        self.assertEqual(ledger.summary(self.gov['project']['id']), summary)
        # Independent recovery controls remain: UNKNOWN failure needs resolution,
        # but it must never masquerade as an outstanding reservation.
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            adapters['dispatch_ticket'](_dispatch_payload('retry'))

    def test_failed_exit_charges_observed_usage_and_closes(self):
        self.failed_completion(usage=True, delay=True)

    def test_failed_exit_without_usage_closes_unknown_at_startup(self):
        self.failed_completion(usage=False, delay=False)

    def test_failed_exit_before_thread_closes_unknown(self):
        self.failed_completion(usage=False, delay=False, emit_thread=False)

    def test_enabled_missing_canonical_location_refuses_and_off_needs_none(self):
        with patch.dict(os.environ, {'AWF_STATE_HOME': 'relative-state'}):
            with self.assertRaisesRegex(ValidationError, 'canonical project ledger.*AWF_STATE_HOME'):
                self.review()
            self.value['budgeting']['accounting_enabled'] = False
            driver = self.review()
            with self.assertRaisesRegex(ValidationError, 'canonical project ledger.*AWF_STATE_HOME'):
                self.agent(driver, 'critic')
            # OFF needs no budget location; independent review still needs
            # readable protected operational identity evidence (pre-A6).
            with patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'identity-state')}):
                self.agent(driver, 'critic')
            self.assertIsNone(driver.budgeting.database)
        self.assertTrue(list((self.root / 'identity-state').rglob('executions.sqlite3')))
        self.assertFalse(list((self.root / 'identity-state').rglob('routing.sqlite3')))
        self.assertFalse((self.root / 'host-state').exists())

    def test_canonical_location_rejects_worktrees_and_distinguishes_projects(self):
        path = project_ledger_path(self.gov['project']['id'], accounting_enabled=True)
        self.assertNotEqual(path, project_ledger_path('other-project', accounting_enabled=True))
        with self.assertRaisesRegex(ValidationError, 'outside worktrees'):
            project_ledger_path(self.gov['project']['id'], accounting_enabled=True,
                worktree_roots=[self.root / 'host-state'])
        self.assertIsNone(project_ledger_path(self.gov['project']['id'], accounting_enabled=False,
            worktree_roots=[self.root / 'host-state']))
        self.assertFalse(path.parent.exists())

    def test_existing_per_host_history_requires_explicit_migration(self):
        prior = self.root / 'review/routing.sqlite3'
        driver = self.review(ledger_path=prior)
        # Keep independent execution state separate from the budget migration
        # location whose non-creation this regression has always asserted.
        with patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'identity-state')}):
            self.agent(driver, 'worker')
        self.assertEqual(self.summary(driver)['known_tokens'], 100)
        with self.assertRaisesRegex(ValidationError, 'existing per-host budget history'):
            self.review()
        self.assertEqual(self.summary(driver)['known_tokens'], 100)
        self.assertFalse((self.root / 'host-state').exists())
        self.assertTrue(list((self.root / 'identity-state').rglob('executions.sqlite3')))
        self.assertFalse(list((self.root / 'identity-state').rglob('routing.sqlite3')))

    def test_controller_and_review_default_construction_sum_to_200(self):
        config, runner, adapters = self.adapter()
        repository = self.root / 'repository'
        repository.mkdir()
        (repository / 'tracked.txt').write_bytes(b'fixture\n')
        subprocess.run(['git', 'init', '-q', str(repository)], check=True)
        subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'add', 'tracked.txt'], check=True)
        subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        head, tree = [subprocess.check_output(['git', '-C', str(repository), 'rev-parse', ref], text=True).strip()
                      for ref in ('HEAD', 'HEAD^{tree}')]
        state = ContinuousControllerStore(self.root / 'controller.sqlite3', ['A'],
            worktree_roots=[self.root / 'worktree'])
        binding = dict(project_id='project-1', repository_id='101', scope_sha256='a'*64)
        planning = planning_fixture_inputs([_ticket('tracked.txt')], state, 1, binding=binding, now=NOW)
        self.gov = planning['planning_config']
        config['codex']['roles']['writer']['reasoning_effort'] = 'high'
        adapters = ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        value = deepcopy(planning['planning_operating_snapshot'].config)
        value['budgeting'] = dict(fresh_settings(), accounting_enabled=True)
        self.value = value
        snapshot = confirmed_fixture_snapshot(self, self.root, value, self.gov)
        planning.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        resources = planning_resource_inputs(1)
        resources.update(planning)
        result = production_controller_cycle(state, controller_config=dict(dispatch_enabled=True,
            auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
            host_capacity=1, **resources, inventory_binding=binding, repository_root=repository,
            repository_head_sha=head, repository_tree_sha=tree, publication_config=config,
            **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations',
                'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
        self.assertEqual(len(result['dispatch_receipts']), 1, result)
        receipt = result['dispatch_receipts'][0]
        record = json.loads((runner._budget_record_dir / (receipt['dispatch_id'] + '.json')).read_text())
        runner.observe(record, timeout=10)
        payload = {key: record[key] for key in ('dispatch_schema_version', 'dispatch_id', 'stream',
            'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at', 'begun_at', 'role',
            'lifecycle_stage', 'lane_start_waivers')}
        adapters['observe_dispatch'](dict(payload, paths=['tracked.txt'], actor='writer', next_action='continue'))
        driver = self.review(ticket='AWF-32')
        record_fixture_codex_identity(driver, str(uuid.uuid4()))
        self.agent(driver, 'critic')
        self.assertEqual(self.summary(driver)['known_tokens'], 200)
        self.assertFalse(state.path.with_suffix('.routing.sqlite3').exists())
        self.assertFalse((driver.state / 'routing.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
