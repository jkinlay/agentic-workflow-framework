"""Pre-A5 regressions: production construction and real budget observers."""
import json
from pathlib import Path
import sys
import time
import unittest
import uuid
from unittest.mock import patch

import source_only
from owner_admission_fixtures import confirmed_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.canonical import sha256
from agentic.providers.github_review_host import HostDriver
import test_awf242_pre_a3 as fixtures
from copy import deepcopy
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from test_reference_controller_adapter import _dispatch_payload, _ticket, FakeClock, NOW
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs


class ProductionPreA5Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    summary = fixtures.ProductionPreA3Tests.summary
    adapter = fixtures.ProductionPreA3Tests.adapter

    def draft(self, driver, *, run_id=None):
        driver.verified_contract_text = lambda: 'Fixture scope'
        driver.first_draft_git_controls = lambda: {}
        driver.require_first_draft_git_controls = lambda expected: None
        driver.require_clean_first_draft_checkout = lambda: None
        def cli(name, argv, **kwargs):
            self.launched = True
            run = Path(kwargs['log']).parent
            (run / 'result.json').write_text(json.dumps(dict(outcome='CHANGED',
                changes=[dict(path='fixture.txt', action='modified')],
                tested_tree='c'*40, ignored_untracked=[], summary='Fixture')))
            events = [dict(type='thread.started', thread_id=str(uuid.uuid4())),
                dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30))]
            (run / 'codex.jsonl').write_text('\n'.join(map(json.dumps, events)))
        driver.run = cli
        return driver.first_draft_worker(run_id=run_id)

    def test_first_draft_enrolls_and_settles(self):
        driver = self.review()
        self.draft(driver)
        self.assertEqual(self.summary(driver)['known_tokens'], 100)

    def test_first_draft_refuses_unsupported_enforcement_before_launch(self):
        self.value['budgeting']['enforcement_enabled'] = True
        driver = self.review()
        self.launched = False
        with self.assertRaisesRegex(ValidationError, 'observed host token limits'):
            self.draft(driver)
        self.assertFalse(self.launched)
        self.assertFalse(driver.budgeting.database.path.exists())

    def test_first_draft_retry_retains_all_execution_contexts(self):
        driver = self.review()
        run = str(uuid.uuid4())
        self.draft(driver, run_id=run)
        first = driver.codex_run_id(run)
        self.draft(driver, run_id=run)
        second = driver.codex_run_id(run)
        self.assertNotEqual(first, second)
        driver = self.review()
        critic = self.agent(driver, 'critic')
        with driver.budgeting._ledger()._transaction() as db:
            request = json.loads(db.execute('SELECT request FROM model_runs WHERE run_id=?', (critic,)).fetchone()[0])
        self.assertEqual(set(request['worker_context_ids']), {first, second})
        self.assertEqual(self.summary(driver)['known_tokens'], 300)

    def test_amended_heads_exclude_all_contributors_across_modes(self):
        for index, (enabled, role) in enumerate(((True, 'critic'), (False, 'critic'),
                                               (True, 'specialist'), (False, 'specialist'))):
            with self.subTest(accounting=enabled, role=role):
                # Security quarantine is project-wide and intentionally remains
                # unresolved at the end of each independent scenario.
                self.gov['project']['id'] = str(uuid.uuid4())
                ticket, state = 'EX-' + str(20 + index), str(uuid.uuid4())
                ledger_path = self.root / state / 'project.sqlite'
                self.value['budgeting']['accounting_enabled'] = False
                driver = self.review(ticket=ticket, state=state, ledger_path=ledger_path)
                self.candidate = dict(self.candidate, head='a'*40)
                worker = self.agent(driver, 'worker')
                first = driver.codex_run_id(worker)
                # Two published successor heads and a different worker context.
                self.candidate = dict(self.candidate, head='c'*40)
                self.agent(driver, 'worker')
                self.candidate = dict(self.candidate, head='d'*40)
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(ticket=ticket, state=state, ledger_path=ledger_path)
                driver.thread = first
                with self.assertRaisesRegex(ValidationError, 'independent|quarantined'):
                    self.agent(driver, role)

    def test_missing_worker_execution_identity_fails_closed_after_head_change(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        worker = self.agent(driver, 'worker')
        (driver.state / 'runs' / worker / 'codex.jsonl').unlink()
        self.candidate = dict(self.candidate, head='c'*40)
        self.value['budgeting']['accounting_enabled'] = True
        driver = self.review()
        with self.assertRaisesRegex(ValidationError, 'independence unknown'):
            self.agent(driver, 'critic')
        self.assertFalse(driver.budgeting.database.path.exists())

    def test_legacy_identity_without_task_lineage_is_conservatively_excluded(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        worker = str(uuid.uuid4())
        fixtures.record_fixture_codex_identity(driver, worker)
        context = driver.codex_run_id(worker)
        self.candidate = dict(self.candidate, head='c'*40)
        self.value['budgeting']['accounting_enabled'] = True
        driver = self.review()
        driver.thread = context
        with self.assertRaisesRegex(ValidationError, 'independent|quarantined'):
            self.agent(driver, 'critic')

    def test_off_failure_durable_without_budget_database(self):
        config, runner, adapters = self.adapter()
        runner.exit_code = 1
        self.value['budgeting']['accounting_enabled'] = False
        unusable = self.root / 'not-a-directory'
        unusable.write_text('Fixture obstruction')
        path = unusable / 'ledger.sqlite'
        adapters['dispatch_ticket'].configure_budgeting(path=path, governance=self.gov,
            operating=confirmed_fixture_snapshot(self, self.root, self.value, self.gov))
        payload = _dispatch_payload('off-failure')
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            adapters['dispatch_ticket'](payload)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                adapters['observe_dispatch'](payload)
                time.sleep(0.02)
        record = json.loads((runner._budget_record_dir / 'off-failure.json').read_text())
        self.assertEqual(record['status'], 'FAILED')
        self.assertTrue(record['terminal_failure']['terminal'])
        self.assertNotIn('budget_admission', record)
        self.assertEqual(unusable.read_text(), 'Fixture obstruction')
        runner.exit_code = 0
        restarted = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        restarted['dispatch_ticket'].configure_budgeting(path=None, governance=self.gov,
            operating=confirmed_fixture_snapshot(self, self.root, self.value, self.gov))
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            restarted['dispatch_ticket'](_dispatch_payload('retry-off'))

    def test_timeout_retains_streamed_usage_and_settles(self):
        for usage in (True, False):
            with self.subTest(usage=usage):
                driver = self.review(ticket='EX-10' if usage else 'EX-11', state=str(uuid.uuid4()))
                config = self.root / 'host-policy.json'
                config.write_text('Fixture host policy')
                executable = Path(sys.executable)
                driver.c.update(executables={'codex': dict(path=str(executable),
                    sha256=sha256(executable.read_bytes()))}, _config_path=str(config),
                    config_hash=sha256(config.read_bytes()), command_timeout_seconds=10,
                    agent_timeout_seconds=0.5)
                thread = str(uuid.uuid4())
                events = [dict(type='thread.started', thread_id=thread)]
                if usage:
                    events.append(dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30)))
                program = ('import json,time; events=' + repr(events) +
                    '; [print(json.dumps(e),flush=True) for e in events]; time.sleep(30)')
                driver.run = lambda name, args, **kw: HostDriver.run(driver, name, ['-B', '-c', program], **kw)
                run = str(uuid.uuid4())
                with patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification'), \
                     patch('agentic.providers.github_review_host.enforce_pinned_awf_skill'):
                    with self.assertRaisesRegex(ValidationError, 'SUBPROCESS_CANCELLED'):
                        driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
                with driver.budgeting._ledger()._transaction() as db:
                    row = dict(db.execute('SELECT * FROM model_runs WHERE run_id=?', (run,)).fetchone())
                self.assertEqual(row['status'], 'settled')
                self.assertEqual(row['actual_tokens'], 100 if usage else None)
                outcome = json.loads(row['outcome'])
                self.assertFalse(outcome['success'])
                self.assertEqual(outcome['failure_kind'], 'cancelled')
                self.assertEqual(outcome['actual_context_id'], thread)
                self.assertTrue((driver.state / 'runs' / run / 'codex.jsonl').is_file())

    def test_fresh_off_failure_blocks_full_production_cycle_after_restart(self):
        config, runner, adapters = self.adapter()
        state = ContinuousControllerStore(self.root / 'controller.sqlite', ['A'],
            worktree_roots=[self.root / 'worktree'])
        binding = dict(project_id='project-1', repository_id='101', scope_sha256='a'*64)
        inputs = planning_fixture_inputs([_ticket('tracked.txt')], state, 1, binding=binding, now=NOW)
        self.gov = inputs['planning_config']
        value = deepcopy(inputs['planning_operating_snapshot'].config)
        value['budgeting'] = dict(self.value['budgeting'], accounting_enabled=False)
        snapshot = confirmed_fixture_snapshot(self, self.root, value, self.gov)
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        config['codex']['roles']['writer']['reasoning_effort'] = 'high'
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        path = self.root / 'host-state/project.sqlite'
        adapters['dispatch_ticket'].configure_budgeting(path=path, governance=self.gov, operating=snapshot)
        runner.exit_code = 1
        payload = dict(_dispatch_payload('off-cycle-failure'), ticket='AWF-32')
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            adapters['dispatch_ticket'](payload)
            # A real terminal proof, without a budget admission, must be reaped.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                adapters['observe_dispatch'](payload)
                time.sleep(0.02)
        runner.exit_code = 0
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        head, tree = [fixtures.subprocess.check_output(['git', '-C', str(fixtures.ROOT),
            'rev-parse', ref], text=True).strip() for ref in ('HEAD', 'HEAD^{tree}')]
        result = production_controller_cycle(state, controller_config=dict(dispatch_enabled=True,
            auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
            host_capacity=1, **{**planning_resource_inputs(1), **inputs}, inventory_binding=binding,
            repository_root=fixtures.ROOT, repository_head_sha=head, repository_tree_sha=tree,
            budget_ledger_path=path, publication_config=config,
            **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations',
                'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
        self.assertEqual(result['dispatch_receipts'], [])
        self.assertEqual(result['errors'][0]['operation'], 'dispatch')
        self.assertEqual(result['streams'][0]['state'], 'BLOCKED')
        self.assertFalse(path.exists())

    def test_terminated_command_with_ambiguous_thread_settles_unknown_context(self):
        driver = self.review()
        def terminated(name, args, **kwargs):
            events = [dict(type='thread.started', thread_id=str(uuid.uuid4())) for _ in range(2)]
            events.append(dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30)))
            Path(kwargs['log']).write_text('\n'.join(map(json.dumps, events)))
            raise KeyboardInterrupt('Fixture external cancellation')
        driver.run = terminated
        run = str(uuid.uuid4())
        with self.assertRaises(KeyboardInterrupt):
            driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
        with driver.budgeting._ledger()._transaction() as db:
            row = dict(db.execute('SELECT * FROM model_runs WHERE run_id=?', (run,)).fetchone())
        self.assertEqual(row['status'], 'settled')
        self.assertEqual(row['actual_tokens'], 100)
        outcome = json.loads(row['outcome'])
        self.assertFalse(outcome['actual_context_observed'])
        self.assertEqual(outcome['failure_kind'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
