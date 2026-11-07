"""Pre-A11: an exit-zero worker without a thread is a retained failure."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import uuid

import source_only
from source_only import skip_unless_source_repo
from settings_pr_fixtures import accepted_fixture_snapshot
from agentic import ValidationError, operating as op
from agentic.budgeting import BudgetLedger
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
import test_awf242_pre_a3 as fixtures
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs
from test_planning import config as planning_config, operating_snapshot
from test_reference_controller_adapter import _dispatch_payload, _ticket, FakeClock, NOW


class ProductionPreA11Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    adapter = fixtures.ProductionPreA3Tests.adapter

    def configured(self, enabled, *, config=None):
        if config is None:
            config, _, _ = self.adapter()
        config['github']['project_id'] = self.gov['project']['id']
        runner = fixtures.ControllerCli(credential_envs=(config['jira']['token_env'], config['jira'].get('email_env')), record_dir=config['codex']['run_record_directory'])
        runner.emit_thread = False
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        value = deepcopy(self.value)
        value['budgeting']['accounting_enabled'] = enabled
        context = adapters['dispatch_ticket'].configure_budgeting(path=self.root / 'ledger.sqlite',
            governance=self.gov, operating=accepted_fixture_snapshot(self, self.root, value, self.gov), repository_root=self.root)
        return config, runner, adapters, context

    def record(self, runner, payload):
        return json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())

    def assert_failed(self, context, record):
        self.assertEqual(record['status'], 'FAILED')
        self.assertNotIn('session_id', record)
        self.assertEqual(record['terminal_failure']['returncode'], 0)
        self.assertNotIn('session_id', record['terminal_failure'])
        run = record['launch_nonce']
        with context.operations.transaction('') as db:
            self.assertEqual(db.execute('SELECT success FROM execution_outcomes WHERE run_id=?',
                (run,)).fetchone()[0], 0)
            self.assertIsNone(db.execute('SELECT context_id FROM execution_contexts WHERE run_id=?',
                (run,)).fetchone())

    def test_missing_thread_startup_is_failure_without_budget_telemetry(self):
        config, runner, adapters, context = self.configured(False)
        runner.usage = False
        payload = _dispatch_payload('missing-thread-off')
        with self.assertRaisesRegex(ValidationError, 'observed session identity'):
            adapters['dispatch_ticket'](payload)
        self.assert_failed(context, self.record(runner, payload))
        self.assertFalse(context.database.path.exists())
        self.assertFalse(context.registry.path.exists())
        proof = json.loads(Path(self.record(runner, payload)['terminal_proof_path']).read_text())
        self.assertNotIn('actual_tokens', proof)
        # Reconciliation consumes the same independent allowance as other
        # terminal failures; repeated observation cannot change it to success.
        _, _, restarted, context = self.configured(False, config=config)
        reconcile = dict(payload, reconcile_nonce='fixture-recovery', reconcile_at=NOW)
        self.assertEqual(restarted['observe_dispatch'](reconcile)['dispatch_state'], 'FAILED')
        self.assertEqual(restarted['observe_dispatch'](reconcile)['dispatch_state'], 'FAILED')
        with context.operations.transaction('') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_resolutions').fetchone()[0], 1)
        self.assert_failed(context, self.record(runner, payload))
        self.assertFalse(context.database.path.exists())

    def test_missing_thread_retains_usage_and_can_resolve_after_mode_change(self):
        config, runner, adapters, context = self.configured(True)
        payload = _dispatch_payload('missing-thread-usage')
        with self.assertRaises(ValidationError):
            adapters['dispatch_ticket'](payload)
        record = self.record(runner, payload)
        run = record['launch_nonce']
        with context._ledger()._transaction() as db:
            row = dict(db.execute('SELECT * FROM model_runs WHERE run_id=?', (run,)).fetchone())
        self.assertEqual(row['status'], 'settled')
        self.assertEqual(row['actual_tokens'], 100)
        outcome = json.loads(row['outcome'])
        self.assertFalse(outcome['success'])
        self.assertEqual(outcome['failure_kind'], 'unknown')
        self.assertFalse(outcome['actual_context_observed'])
        self.assertEqual(json.loads(row['request'])['_budget_binding'], record['budget_admission']['binding'])
        self.assertEqual(outcome['actual_context_id'], record['launch_nonce'])
        self.assertTrue(outcome['observation_id'].startswith('codex-launch-usage:'))
        self.assert_failed(context, record)
        ledger = BudgetLedger(context.database.path, context.registry)
        summary = ledger.summary(self.gov['project']['id'])
        self.assertEqual(summary['known_tokens'], 100)
        for enabled in (True, False):
            _, _, restarted, restarted_context = self.configured(enabled, config=config)
            with self.assertRaisesRegex(ValidationError, 'observed session identity'):
                restarted['observe_dispatch'](payload)
            with restarted_context._ledger()._transaction() as db:
                self.assertEqual(dict(db.execute('SELECT * FROM model_runs WHERE run_id=?',
                    (run,)).fetchone()), row)
            self.assertEqual(ledger.summary(self.gov['project']['id']), summary)
            self.assert_failed(restarted_context, self.record(runner, payload))
        resolution = restarted_context._ledger().resolve_failure(run, dict(verified=True,
            evidence_ref='fixture-nonce-bound-terminal-proof', reason='Fixture verified recovery'))
        self.assertEqual(resolution['status'], 'resolved')
        self.assertFalse(resolution['budgets_refunded'])
        self.assertTrue(restarted_context.operational_failure_resolved(record['budget_admission'],
            record['operational_binding']))
        self.assertEqual(ledger.summary(self.gov['project']['id']), summary)

    def cycle(self, config, adapters, enabled, name):
        store = ContinuousControllerStore(self.root / (name + '.sqlite'), ['A'],
            worktree_roots=[self.root / 'worktree'])
        binding = dict(project_id=self.gov['project']['id'], repository_id='101', scope_sha256='a'*64)
        inputs = planning_fixture_inputs([_ticket('tracked.txt')], store, 1, binding=binding, now=NOW,
            config=self.gov, cycle_id=name)
        value = deepcopy(inputs['planning_operating_snapshot'].config)
        value['budgeting']['accounting_enabled'] = enabled
        repository = self.root / 'controller-repository'
        snapshot = accepted_fixture_snapshot(self, repository, value, inputs['planning_config'])
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        head, tree = [fixtures.subprocess.check_output(['git', '-C', str(repository),
            'rev-parse', ref], text=True).strip() for ref in ('HEAD', 'HEAD^{tree}')]
        return production_controller_cycle(store, controller_config=dict(dispatch_enabled=True,
            auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
            host_capacity=1, **{**planning_resource_inputs(1), **inputs}, inventory_binding=binding,
            repository_root=repository, repository_head_sha=head, repository_tree_sha=tree,
            publication_config=config, budget_ledger_path=self.root / 'ledger.sqlite',
            **{key: adapters[key] for key in ('observe_inventory', 'observe_reservations',
                'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})

    def zero_recovery_cycle(self, enabled):
        self.gov = planning_config(1)
        self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
        self.value = operating_snapshot(self.gov, 1).config
        config, _, _ = self.adapter()
        config['codex']['roles']['writer']['reasoning_effort'] = 'high'
        config, runner, adapters, context = self.configured(enabled, config=config)
        first = self.cycle(config, adapters, enabled, 'first')
        self.assertEqual(first['dispatch_receipts'], [])
        self.assertTrue(first['errors'])
        records = [json.loads(path.read_text()) for path in runner._budget_record_dir.glob('*.json')]
        launched = [row for row in records if row.get('format') == 'awf-reference-dispatch-2' and 'pid' in row]
        self.assertEqual(len(launched), 1, first['errors'])
        # A fresh controller store and adapter must not erase the failed host
        # execution or permit retry without resolution, even with budgeting OFF.
        _, restarted_runner, restarted, restarted_context = self.configured(enabled, config=config)
        restarted_runner.emit_thread = True
        second = self.cycle(config, restarted, enabled, 'restarted')
        for receipt in second['dispatch_receipts']:
            restarted_runner.observe(self.record(restarted_runner, receipt), timeout=10)
        self.assertEqual(second['dispatch_receipts'], [], second['errors'])
        self.assertEqual([error['reason'] for error in second['errors']], ['ValidationError'])
        with self.assertRaisesRegex(ValidationError, 'resolve non-reasoning failure'):
            restarted['dispatch_ticket'](dict(_dispatch_payload('unresolved-retry'), ticket=launched[0]['ticket']))
        self.assert_failed(context, self.record(runner, launched[0]))
        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
            restarted_context.operations.resolve(launched[0]['operational_binding'])
        with restarted_context.operations.transaction('') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_outcomes').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM execution_resolutions').fetchone()[0], 0)
        records = [json.loads(path.read_text()) for path in runner._budget_record_dir.glob('*.json')]
        self.assertEqual(sum(row.get('format') == 'awf-reference-dispatch-2' and 'pid' in row
            for row in records), 1)
        if enabled:
            with restarted_context._ledger()._transaction() as db:
                rows = list(db.execute('SELECT * FROM model_runs'))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['status'], 'settled')
            self.assertEqual(rows[0]['actual_tokens'], 100)
            self.assertFalse(json.loads(rows[0]['outcome'])['success'])
        else:
            self.assertFalse(context.database.path.exists())
            self.assertFalse(context.registry.path.exists())

    @skip_unless_source_repo("Requires source Git HEAD/tree for repository ownership and reservations", ".git")
    def test_off_production_cycle_cannot_retry_rejected_startup_after_restart(self):
        self.zero_recovery_cycle(False)

    @skip_unless_source_repo("Requires source Git HEAD/tree for repository ownership and reservations", ".git")
    def test_accounting_production_cycle_cannot_retry_rejected_startup_after_restart(self):
        self.zero_recovery_cycle(True)


if __name__ == '__main__':
    unittest.main()
