"""Pre-A8: operational recovery is independent of budget storage and mode."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

import source_only
from source_only import skip_unless_source_repo
from settings_pr_fixtures import accepted_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.budgeting import protected_state_roots
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from agentic.model_routing import RoutingLedger, policy_from_config, _fingerprint
from agentic.providers.github_review_host import HostDriver
from agentic.review_first_draft import run_first_draft
from agentic.review_loop import LoopStore, resume_first_draft
import test_awf242_pre_a3 as fixtures
import test_awf242_pre_a6 as pre_a6
import test_awf242_pre_a7 as pre_a7
from test_reference_controller_adapter import _dispatch_payload, _ticket, FakeClock, NOW
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs
from test_model_routing import capabilities, request, outcome


class ProductionPreA8Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    adapter = fixtures.ProductionPreA3Tests.adapter
    configured_adapter = pre_a6.ProductionPreA6Tests.configured_adapter
    dispatch = pre_a6.ProductionPreA6Tests.dispatch
    failing_cli = pre_a7.ProductionPreA7Tests.failing_cli

    def test_off_review_never_opens_empty_or_corrupt_budget_history(self):
        self.value['budgeting']['accounting_enabled'] = False
        for content in (b'', b'not a SQLite budget database'):
            with self.subTest(content=content):
                ledger = self.root / (str(uuid.uuid4()) + '.sqlite')
                ledger.write_bytes(content)
                driver = self.review(ledger_path=ledger, state=str(uuid.uuid4()))
                self.agent(driver, 'critic')
                self.assertEqual(ledger.read_bytes(), content)
                self.assertFalse(driver.budgeting.registry.path.exists())

    @skip_unless_source_repo("Requires source Git HEAD/tree for repository ownership and reservations", ".git")
    def test_off_controller_dispatch_never_opens_empty_or_corrupt_budget_history(self):
        config, runner, adapters = self.adapter()
        for content in (b'', b'not a SQLite budget database'):
            with self.subTest(content=content):
                store = ContinuousControllerStore(self.root / (str(uuid.uuid4()) + '.sqlite'), ['A'],
                    worktree_roots=[self.root / 'worktree'])
                binding = dict(project_id='project-1', repository_id='101', scope_sha256='a'*64)
                inputs = planning_fixture_inputs([_ticket('tracked.txt')], store, 1, binding=binding, now=NOW)
                self.gov = inputs['planning_config']
                value = deepcopy(inputs['planning_operating_snapshot'].config)
                value['budgeting']['accounting_enabled'] = False
                repository = self.root / 'controller-repository'
                snapshot = accepted_fixture_snapshot(self, repository, value, self.gov)
                inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
                config['codex']['roles']['writer']['reasoning_effort'] = 'high'
                adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
                ledger = self.root / (str(uuid.uuid4()) + '.sqlite')
                ledger.write_bytes(content)
                head, tree = [fixtures.subprocess.check_output(['git', '-C', str(repository),
                    'rev-parse', ref], text=True).strip() for ref in ('HEAD', 'HEAD^{tree}')]
                result = production_controller_cycle(store, controller_config=dict(dispatch_enabled=True,
                    auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
                    host_capacity=1, **{**planning_resource_inputs(1), **inputs}, inventory_binding=binding,
                    repository_root=repository, repository_head_sha=head, repository_tree_sha=tree,
                    budget_ledger_path=ledger, publication_config=config,
                    **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations',
                        'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
                for receipt in result['dispatch_receipts']:
                    record = json.loads((runner._budget_record_dir / (receipt['dispatch_id'] + '.json')).read_text())
                    runner.observe(record, timeout=10)
                self.assertEqual(len(result['dispatch_receipts']), 1, result['errors'])
                self.assertEqual(result['errors'], [])
                self.assertEqual(ledger.read_bytes(), content)

    def test_review_runtime_checkout_excluded_in_every_mode(self):
        project = self.root / 'runtime'
        for enabled in (False, True):
            with self.subTest(accounting=enabled):
                self.value['budgeting']['accounting_enabled'] = enabled
                with patch.dict(os.environ, {'AWF_STATE_HOME': str(project / 'state')}):
                    with self.assertRaisesRegex(ValidationError, 'outside.*worktree'):
                        driver = self.review(project=project, state=str(uuid.uuid4()))
                        self.agent(driver, 'critic')
                self.assertFalse(list(project.rglob('executions.sqlite3')))
                self.assertFalse(list(project.rglob('routing.sqlite3')))

    def test_canonical_exclusions_always_include_loaded_runtime(self):
        self.assertIn(fixtures.ROOT.resolve(), protected_state_roots(self.root / 'worker'))

    def test_off_first_draft_retries_ignore_legacy_run_quota(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        driver.verified_contract_text = lambda: 'Fixture scope'
        driver.first_draft_git_controls = lambda: {}
        driver.require_first_draft_git_controls = lambda expected: None
        driver.require_clean_first_draft_checkout = lambda: None
        launches = self.failing_cli(driver)
        config = dict(driver.c, key='101:0', pr=0, repository_id=101, config_hash='f'*64,
            max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=1)
        store = LoopStore(driver.state)
        self.addCleanup(store.close)
        for count in (1, 2, 3):
            with self.assertRaisesRegex(ValidationError, 'failed'):
                run_first_draft(store, config, worker=lambda run: driver.first_draft_worker(run_id=run),
                    publisher=lambda receipt: self.fail('Failed worker cannot publish'),
                    observe_pr=lambda publication: self.fail('Failed worker cannot observe PR'))
            self.assertEqual(len(launches), count)
            paused = store.get(config['key'])
            self.assertEqual(paused['agent_runs'], count)
            if count < 3:
                self.assertTrue(resume_first_draft(store, config, paused['inflight']['id'])['resume_ready'])
            else:
                with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                    resume_first_draft(store, config, paused['inflight']['id'])
        self.assertFalse(driver.budgeting.database.path.exists())

    def failed_adapter(self, enabled):
        runner, adapters = self.configured_adapter(enabled)
        runner.exit_code = 1
        payload = dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10')
        with self.assertRaisesRegex(ValidationError, 'non-zero exit'):
            self.dispatch(adapters, payload)
        record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
        return runner, adapters, payload, record

    def test_zero_cap_first_draft_resume_refused_in_both_modes(self):
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        for enabled in (False, True):
            with self.subTest(accounting=enabled):
                self.gov['project']['id'] = 'first-draft-zero-' + str(enabled)
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(state=str(uuid.uuid4()))
                driver.verified_contract_text = lambda: 'Fixture scope'
                driver.first_draft_git_controls = lambda: {}
                driver.require_first_draft_git_controls = lambda expected: None
                driver.require_clean_first_draft_checkout = lambda: None
                launches = self.failing_cli(driver)
                config = dict(driver.c, key='101:0', pr=0, repository_id=101, config_hash='f'*64,
                    max_amendment_cycles=3, max_ci_wait_ticks=2, max_agent_runs=10)
                store = LoopStore(driver.state)
                self.addCleanup(store.close)
                with self.assertRaisesRegex(ValidationError, 'failed'):
                    run_first_draft(store, config, worker=lambda run: driver.first_draft_worker(run_id=run),
                        publisher=lambda receipt: self.fail('Failed worker cannot publish'),
                        observe_pr=lambda publication: self.fail('Failed worker cannot observe PR'))
                paused = store.get(config['key'])
                with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                    resume_first_draft(store, config, paused['inflight']['id'])
                self.assertEqual(len(launches), 1)

    def test_zero_cap_refuses_ledger_resolution_and_keeps_cross_host_stop(self):
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        runner, adapters, payload, record = self.failed_adapter(True)
        driver = self.review(ledger_path=self.root / (self.gov['project']['id'] + '.sqlite'))
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            driver.budgeting.operations.resolve(record['operational_binding'])
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            driver.budgeting._ledger().resolve_failure(record['operational_binding']['run_id'],
                dict(verified=True, evidence_ref='fixture-terminal-proof', reason='Fixture recovery'))
        runner.exit_code = 0
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.dispatch(adapters, dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10'))
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.agent(driver, 'critic')

    def test_mixed_mode_resolutions_share_one_operational_allowance(self):
        for count, enabled in enumerate((False, True, True)):
            runner, adapters, payload, record = self.failed_adapter(enabled)
            if not enabled:
                reconciliation = dict(payload, reconcile_nonce='fixture-reconcile', reconcile_at=NOW)
                self.assertEqual(adapters['observe_dispatch'](reconciliation)['status'], 'RECONCILED')
                self.assertEqual(adapters['observe_dispatch'](reconciliation)['status'], 'RECONCILED')
            else:
                ledger = RoutingLedger(self.root / (self.gov['project']['id'] + '.sqlite'))
                resolve = lambda: ledger.resolve_failure(record['operational_binding']['run_id'],
                    dict(verified=True, evidence_ref='fixture-terminal-proof', reason='Fixture recovery'))
                if count < 2:
                    self.assertEqual(resolve()['status'], 'resolved')
                else:
                    with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                        resolve()
                    with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
                        self.agent(self.review(), 'critic')

    def test_refused_reduction_cannot_be_undone_through_the_ledger(self):
        _, _, _, record = self.failed_adapter(True)
        ledger_path = self.root / (self.gov['project']['id'] + '.sqlite')
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        driver = self.review(ledger_path=ledger_path)
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            driver.budgeting.operations.resolve(record['operational_binding'])
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            RoutingLedger(ledger_path).resolve_failure(record['operational_binding']['run_id'],
                dict(verified=True, evidence_ref='fixture-terminal-proof', reason='Fixture recovery'))

    def test_trusted_increase_spends_one_allowance_and_replay_cannot_restore_a_reduction(self):
        settings = self.gov['execution']['model_routing']['reconciliation']
        settings.update(enabled=True, authorized_operator_ids=['fixture-owner'],
            max_reconciled_incident_retries_per_ticket=0)
        _, _, _, record = self.failed_adapter(True)
        ledger = RoutingLedger(self.root / (self.gov['project']['id'] + '.sqlite'))
        project = self.gov['project']['id']
        settings['max_reconciled_incident_retries_per_ticket'] = 1
        policy = policy_from_config(self.gov)
        now = datetime.now(timezone.utc)
        approval = dict(operation_id='fixture-cap-increase', expected_limit=0, new_limit=1,
            expected_revision=ledger.retry_limit_history(project)['retry_ceiling_revision'],
            approved_at=(now - timedelta(seconds=1)).isoformat(), expires_at=(now + timedelta(hours=1)).isoformat(),
            policy_sha256=_fingerprint(policy), operator_id='fixture-owner', authorization_ref='fixture-approval',
            evidence_ref='fixture-evidence', reason='Fixture trusted-host direction')
        saved = ledger.authorize_retry_limit(project, policy, approval)
        driver = self.review()
        self.assertFalse(driver.budgeting.operations.resolved(record['operational_binding']))
        ledger.resolve_failure(record['operational_binding']['run_id'],
            dict(verified=True, evidence_ref='fixture-terminal-proof', reason='Fixture recovery'))
        self.assertTrue(driver.budgeting.operations.resolved(record['operational_binding']))
        _, _, _, second = self.failed_adapter(True)
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            ledger.resolve_failure(second['operational_binding']['run_id'],
                dict(verified=True, evidence_ref='fixture-terminal-proof-2', reason='Fixture recovery'))
        settings['max_reconciled_incident_retries_per_ticket'] = 0
        reduced = self.review()
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            reduced.budgeting.operations.resolve(second['operational_binding'])
        self.assertEqual(ledger.authorize_retry_limit(project, policy, approval), saved)
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            ledger.resolve_failure(second['operational_binding']['run_id'],
                dict(verified=True, evidence_ref='fixture-terminal-proof-3', reason='Fixture recovery'))

    def test_zero_cap_orphan_closure_does_not_grant_a_resolution(self):
        self.gov['execution']['model_routing']['reconciliation'].update(enabled=True,
            authorized_operator_ids=['fixture-owner'], max_reconciled_incident_retries_per_ticket=0)
        runner, adapters = self.configured_adapter(True)
        payload = dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10')
        adapters['dispatch_ticket'](payload)
        record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
        runner.observe(record, timeout=10)  # Real termination; simulate loss before coordinator settlement.
        ledger = RoutingLedger(self.root / (self.gov['project']['id'] + '.sqlite'))
        binding = record['operational_binding']
        result = ledger.reconcile(self.gov['project']['id'], policy_from_config(self.gov), binding['run_id'], dict(
            reconciliation_id='fixture-close', operator_id='fixture-owner', authorization_ref='fixture-authority',
            reason='Host stopped orphan', evidence_ref='fixture-stop', remediation_ref='fixture-remediation',
            host_stopped=True, remediation_verified=True))
        self.assertEqual(result['status'], 'reconciled')  # Safe closure remains available at the cap.
        with ledger._transaction() as db:
            self.assertIsNone(db.execute('SELECT run_id FROM model_failure_resolutions WHERE run_id=?',
                (binding['run_id'],)).fetchone())
        driver = self.review()
        self.assertFalse(driver.budgeting.operations.resolved(binding))
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.agent(driver, 'critic')

    def test_explicit_legacy_migration_carries_failures_resolutions_and_ceiling(self):
        ledger = RoutingLedger(self.root / 'legacy.sqlite')
        policy = policy_from_config(self.gov)
        first = ledger.reserve(self.gov['project']['id'], policy, request(), capabilities())
        ledger.settle(first['run_id'], outcome(first, success=False, failure_kind='credentials'))
        ledger.resolve_failure(first['run_id'], dict(verified=True, evidence_ref='fixture-check', reason='Fixture recovery'))
        second = ledger.reserve(self.gov['project']['id'], policy, request(), capabilities())
        ledger.settle(second['run_id'], outcome(second, success=False, failure_kind='credentials'))
        before = ledger.path.read_bytes()
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review(ledger_path=ledger.path)
        environment = patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'migrated-host-state')})
        environment.start()
        self.addCleanup(environment.stop)
        ledger.migrate_operational_recovery(self.gov['project']['id'])
        self.assertEqual(ledger.path.read_bytes(), before)
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            self.agent(driver, 'critic')
        ledger.resolve_failure(second['run_id'], dict(verified=True, evidence_ref='fixture-check-2', reason='Fixture recovery'))
        third = ledger.reserve(self.gov['project']['id'], policy, request(), capabilities())
        ledger.settle(third['run_id'], outcome(third, success=False, failure_kind='credentials'))
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            ledger.resolve_failure(third['run_id'], dict(verified=True, evidence_ref='fixture-check-3', reason='Fixture recovery'))


if __name__ == '__main__':
    unittest.main()
