"""Pre-A4 regressions through production construction; budget observers stay real."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

import source_only
from source_only import skip_unless_source_repo
from settings_pr_fixtures import accepted_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.canonical import sha256
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from agentic.providers.github_review_host import HostDriver
import test_awf242_pre_a3 as fixtures
from test_reference_controller_adapter import _dispatch_payload, _ticket, FakeClock, NOW
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs


class ProductionPreA4Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    summary = fixtures.ProductionPreA3Tests.summary
    adapter = fixtures.ProductionPreA3Tests.adapter

    def test_failed_worker_recovery_stop_survives_off_restart(self):
        config, runner, adapters = self.adapter()
        runner.exit_code = 1
        path = self.root / 'ledger.sqlite'
        context = adapters['dispatch_ticket'].configure_budgeting(path=path,
            governance=self.gov, operating=accepted_fixture_snapshot(self, self.root, self.value, self.gov), repository_root=self.root)
        payload = _dispatch_payload('failure')
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            adapters['dispatch_ticket'](payload)
            adapters['observe_dispatch'](payload)
        with context._ledger()._transaction() as db:
            row = dict(db.execute('SELECT * FROM model_runs').fetchone())
        self.assertEqual(row['status'], 'settled')
        self.assertEqual(row['actual_tokens'], 100)
        self.assertEqual(json.loads(row['outcome'])['failure_kind'], 'unknown')
        before = path.read_bytes()
        # A new host and OFF mode must preserve operational recovery admission.
        restarted = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        self.value['budgeting']['accounting_enabled'] = False
        restarted['dispatch_ticket'].configure_budgeting(path=path, governance=self.gov,
            operating=accepted_fixture_snapshot(self, self.root, self.value, self.gov), repository_root=self.root)
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure before retry: unknown'):
            try:
                restarted['dispatch_ticket'](_dispatch_payload('retry'))
            finally:
                # Reap the CLI if this regression exposes an unauthorized retry.
                retry = runner._budget_record_dir / 'retry.json'
                if retry.exists():
                    record = json.loads(retry.read_text())
                    if 'pid' in record:
                        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
                            runner.observe(record, timeout=10)
        self.assertEqual(path.read_bytes(), before)

    def test_off_without_history_accepts_unusable_ledger_path(self):
        config, runner, adapters = self.adapter()
        self.value['budgeting']['accounting_enabled'] = False
        unusable = self.root / 'not-a-directory'
        unusable.write_text('Fixture obstruction')
        adapters['dispatch_ticket'].configure_budgeting(path=unusable / 'ledger.sqlite',
            governance=self.gov, operating=accepted_fixture_snapshot(self, self.root, self.value, self.gov), repository_root=self.root)
        payload = _dispatch_payload('off')
        adapters['dispatch_ticket'](payload)
        record = json.loads((runner._budget_record_dir / 'off.json').read_text())
        deadline = time.monotonic() + 10
        while not runner.observe(record, timeout=10).get('terminal') and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(adapters['observe_dispatch'](dict(payload, reconcile_nonce='off-reconcile', reconcile_at=NOW))['terminal'])
        self.assertEqual(unusable.read_text(), 'Fixture obstruction')

    @skip_unless_source_repo("Requires source Git HEAD/tree for repository ownership and reservations", ".git")
    def test_production_cycle_refuses_unresolved_worker_retry_with_accounting_off(self):
        config, runner, adapters = self.adapter()
        binding = dict(project_id='project-1', repository_id='101', scope_sha256='a'*64)
        state = ContinuousControllerStore(self.root / 'controller.sqlite', ['A'],
            worktree_roots=[self.root / 'worktree'])
        inputs = planning_fixture_inputs([_ticket('tracked.txt')], state, 1, binding=binding, now=NOW)
        self.gov = inputs['planning_config']
        value = deepcopy(inputs['planning_operating_snapshot'].config)
        value['budgeting'] = dict(self.value['budgeting'], accounting_enabled=True)
        config['codex']['roles']['writer']['reasoning_effort'] = 'high'
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        path = self.root / 'host-state/project.sqlite'
        context = adapters['dispatch_ticket'].configure_budgeting(path=path,
            governance=self.gov, operating=accepted_fixture_snapshot(self, self.root, value, self.gov), repository_root=self.root)
        runner.exit_code = 1
        payload = _dispatch_payload('failed-cycle-worker')
        payload['ticket'] = 'AWF-32'
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            adapters['dispatch_ticket'](payload)
            adapters['observe_dispatch'](payload)
        before = path.read_bytes()
        # The full controller cycle configures its own budgeting bridge. Only
        # external inventory/publication and CLI behavior come from the runner.
        runner.exit_code = 0
        value['budgeting']['accounting_enabled'] = False
        repository = self.root / 'controller-repository'
        snapshot = accepted_fixture_snapshot(self, repository, value, self.gov)
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        head, tree = [subprocess.check_output(['git', '-C', str(repository), 'rev-parse', ref],
                     text=True).strip() for ref in ('HEAD', 'HEAD^{tree}')]
        result = production_controller_cycle(state, controller_config=dict(dispatch_enabled=True,
            auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
            host_capacity=1, **{**planning_resource_inputs(1), **inputs}, inventory_binding=binding,
            repository_root=repository, repository_head_sha=head, repository_tree_sha=tree,
            budget_ledger_path=path,
            publication_config=config, **{name: adapters[name] for name in ('observe_inventory',
                'observe_reservations', 'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
        self.assertEqual(result['dispatch_receipts'], [])
        self.assertEqual(result['errors'][0]['operation'], 'dispatch')
        self.assertEqual(result['streams'][0]['state'], 'BLOCKED')
        # The controller deliberately sanitizes adapter error messages.
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure before retry: unknown'):
            adapters['dispatch_ticket'](dict(_dispatch_payload('check-recovery'), ticket='AWF-32'))
        self.assertEqual(path.read_bytes(), before)
        with context._ledger()._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_runs').fetchone()[0], 1)

    def failed_review_command(self, *, usage=True, thread_known=True):
        driver = self.review()
        driver.run = HostDriver.run.__get__(driver)
        config_path = self.root / 'host-policy.json'
        config_path.write_text('Fixture pinned host policy')
        executable = Path(sys.executable)
        driver.c.update(executables={'codex': dict(path=str(executable), sha256=sha256(executable.read_bytes()))},
            _config_path=str(config_path), config_hash=sha256(config_path.read_bytes()), command_timeout_seconds=10)
        thread = str(uuid.uuid4())
        events = [dict(type='thread.started', thread_id=thread)] if thread_known else []
        if usage:
            events.append(dict(type='turn.completed', usage=dict(input_tokens=70, output_tokens=30)))
        raw = ('\n'.join(map(json.dumps, events)) + ('\n' if events else '')).encode()
        run = str(uuid.uuid4())
        # Only the external command response and unrelated qualification/skill
        # preflight are fixtures. Host run(), agent(), and observers stay real.
        with patch('agentic.providers.github_review_host.review_qualification.validate_config_qualification'), \
             patch('agentic.providers.github_review_host.enforce_pinned_awf_skill'), \
             patch('agentic.providers.github_review_host.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 1, raw, b'')):
            with self.assertRaisesRegex(ValidationError, 'HOST_COMMAND_FAILED'):
                HostDriver.agent(driver, 'critic', self.candidate, [], run, ['fixture.txt'])
        with driver.budgeting._ledger()._transaction() as db:
            row = dict(db.execute('SELECT * FROM model_runs WHERE run_id=?', (run,)).fetchone())
        self.assertEqual(row['status'], 'settled')
        self.assertEqual(row['actual_tokens'], 100 if usage else None)
        outcome = json.loads(row['outcome'])
        self.assertFalse(outcome['success'])
        self.assertEqual(outcome['failure_kind'], 'unknown')
        self.assertEqual(outcome['actual_context_id'], thread if thread_known else run)
        self.assertEqual(outcome.get('actual_context_observed', True), thread_known)
        driver = self.review()
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.agent(driver, 'critic')
        self.assertEqual(self.summary(driver)['known_tokens'], 100 if usage else 0)
        self.assertEqual(self.summary(driver)['unknown_runs'], 0 if usage else 1)
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.agent(driver, 'critic')
        self.assertEqual(self.summary(driver)['known_tokens'], 100 if usage else 0)

    def test_review_host_failed_command_charges_and_closes_before_retry(self):
        self.failed_review_command()

    def test_review_host_failed_command_without_usage_closes_unknown(self):
        self.failed_review_command(usage=False)

    def test_review_host_failed_command_before_thread_closes_unknown(self):
        self.failed_review_command(usage=False, thread_known=False)

    def test_all_candidate_worker_contexts_excluded_across_modes_and_restart(self):
        for enabled, role in ((True, 'critic'), (False, 'critic'), (True, 'specialist'), (False, 'specialist')):
            with self.subTest(accounting=enabled, role=role):
                # Each scenario deliberately ends with an unresolved security
                # quarantine, so it needs a separate project identity.
                self.gov['project']['id'] = str(uuid.uuid4())
                state = str(uuid.uuid4())
                ledger_path = self.root / state / 'project.sqlite'
                self.value['budgeting']['accounting_enabled'] = False
                driver = self.review(state=state, ledger_path=ledger_path)
                w1 = self.agent(driver, 'worker')
                context = driver.codex_run_id(w1)
                self.value['budgeting']['accounting_enabled'] = True
                driver = self.review(state=state, ledger_path=ledger_path)
                self.agent(driver, 'worker')
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(state=state, ledger_path=ledger_path)
                driver.thread = context
                with self.assertRaisesRegex(ValidationError, 'independent|quarantined'):
                    self.agent(driver, role)

    def test_changed_head_preserves_worker_independence(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        worker = self.agent(driver, 'worker')
        context = driver.codex_run_id(worker)
        self.candidate = dict(self.candidate, head='c'*40)
        self.value['budgeting']['accounting_enabled'] = True
        driver = self.review()
        driver.thread = context
        # An amendment publishes a new head on the same ticket/PR. The worker
        # remains a contributor; exact candidate equality lost this lineage.
        with self.assertRaisesRegex(ValidationError, 'independent|quarantined'):
            self.agent(driver, 'critic')
        self.assertEqual(self.summary(driver)['known_tokens'], 100)


if __name__ == '__main__':
    unittest.main()
