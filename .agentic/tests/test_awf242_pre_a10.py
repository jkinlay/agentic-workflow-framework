"""Pre-A10 regressions: semantic failures and security controls survive OFF."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic.providers.github_review_host import HostDriver
from agentic.review_loop import resume, tick
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs
import test_awf242_pre_a3 as fixtures
import test_awf242_pre_a6 as pre_a6
import test_awf242_pre_a9 as pre_a9
from test_reference_controller_adapter import _dispatch_payload, _ticket, FakeClock, NOW


class ProductionPreA10Tests(unittest.TestCase):
    setUp = fixtures.ProductionPreA3Tests.setUp
    review = fixtures.ProductionPreA3Tests.review
    agent = fixtures.ProductionPreA3Tests.agent
    adapter = fixtures.ProductionPreA3Tests.adapter
    configured_adapter = pre_a6.ProductionPreA6Tests.configured_adapter
    dispatch = pre_a6.ProductionPreA6Tests.dispatch
    malformed_cli = pre_a9.ProductionPreA9Tests.malformed_cli
    loop = pre_a9.ProductionPreA9Tests.loop

    def failed(self, driver, run):
        receipt = json.loads((driver.state / 'runs' / run / 'execution-outcome.json').read_text())
        self.assertFalse(receipt['success'])
        with driver.budgeting.operations.transaction('') as db:
            self.assertEqual(db.execute('SELECT success FROM execution_outcomes WHERE run_id=?', (run,)).fetchone()[0], 0)

    def test_stale_review_cannot_resume_with_zero_recovery_in_either_mode(self):
        for enabled in (False, True):
            with self.subTest(accounting=enabled):
                self.gov['project']['id'] = str(uuid.uuid4())
                self.gov['execution']['model_routing']['reconciliation']['max_reconciled_incident_retries_per_ticket'] = 0
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(state=str(uuid.uuid4()))
                report = dict(candidate=dict(self.candidate, head='c'*40), verdict='APPROVE',
                    reviewed_files=['fixture.txt'], findings=[], summary='Stale review')
                launches = self.malformed_cli(driver, json.dumps(report))
                store, config = self.loop(driver)
                self.assertEqual(tick(store, config, driver)['phase'], 'PAUSED')
                paused = store.get(config['key'])
                run = paused['inflight']['id']
                self.failed(driver, run)
                with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                    resume(store, config, self.candidate, run)
                self.assertEqual(len(launches), 1)

    def test_semantic_validation_covers_specialist_and_amendment_results(self):
        for enabled in (False, True):
            for role in ('specialist', 'worker'):
                with self.subTest(accounting=enabled, role=role):
                    self.gov['project']['id'] = str(uuid.uuid4())
                    self.value['budgeting']['accounting_enabled'] = enabled
                    driver = self.review(state=str(uuid.uuid4()))
                    report = dict(candidate=dict(self.candidate, head='c'*40), summary='Stale result')
                    report.update(dict(outcome='CHANGED') if role == 'worker' else
                        dict(verdict='APPROVE', reviewed_files=['fixture.txt'], findings=[]))
                    self.malformed_cli(driver, json.dumps(report))
                    run = str(uuid.uuid4())
                    with self.assertRaisesRegex(ValidationError, 'candidate|Candidate'):
                        driver.agent(role, self.candidate, [], run, ['fixture.txt'])
                    self.failed(driver, run)

    def test_first_draft_semantic_failure_is_operational_failure(self):
        for enabled in (False, True):
            with self.subTest(accounting=enabled):
                self.gov['project']['id'] = str(uuid.uuid4())
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(state=str(uuid.uuid4()))
                driver.verified_contract_text = lambda: 'Fixture scope'
                driver.first_draft_git_controls = lambda: {}
                driver.require_first_draft_git_controls = lambda expected: None
                driver.require_clean_first_draft_checkout = lambda: None
                report = dict(outcome='CHANGED', changes=[dict(path='outside.txt', action='modified')],
                    tested_tree='a'*40, ignored_untracked=[], summary='Out of scope')
                self.malformed_cli(driver, json.dumps(report))
                run = str(uuid.uuid4())
                with self.assertRaisesRegex(ValidationError, 'exact scope'):
                    driver.first_draft_worker(run_id=run)
                self.failed(driver, run)

    def test_independence_quarantine_survives_mode_change_and_generic_recovery(self):
        self.value['budgeting']['accounting_enabled'] = False
        worker_driver = self.review()
        context = worker_driver.codex_run_id(self.agent(worker_driver, 'worker'))
        self.value['budgeting']['accounting_enabled'] = True
        driver = self.review(state='enabled')
        driver.thread = context
        run = str(uuid.uuid4())
        with self.assertRaisesRegex(ValidationError, 'independent|quarantined'):
            driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
        self.value['budgeting']['accounting_enabled'] = False
        restarted = self.review(state='off-again')
        with self.assertRaisesRegex(ValidationError, 'quarantin|remediat'):
            self.agent(restarted, 'critic')
        self.failed(driver, run)
        binding = json.loads((driver.state / 'runs' / run / 'budget-intent.json').read_text())
        driver.budgeting.operations.resolve(binding)
        with self.assertRaisesRegex(ValidationError, 'quarantin|remediat'):
            self.agent(restarted, 'critic')
        with self.assertRaisesRegex(ValidationError, 'remediation'):
            driver.budgeting.operations.remediate(binding, {})
        driver.budgeting.operations.remediate(binding, dict(verified=True,
            evidence_ref='fixture-security-remediation', reason='Verified context separation'))
        self.agent(restarted, 'critic')

    def test_off_ignores_optional_hardlink_but_protects_required_state(self):
        self.value['budgeting']['accounting_enabled'] = False
        source = self.root / 'unrelated'
        source.write_bytes(b'Unrelated optional source')
        ledger = self.root / 'linked.sqlite'
        os.link(source, ledger)
        driver = self.review(ledger_path=ledger)
        self.agent(driver, 'critic')
        self.assertEqual(source.read_bytes(), b'Unrelated optional source')
        os.link(driver.budgeting.operations.path, self.root / 'operational-link')
        with self.assertRaisesRegex(ValidationError, 'hardlinks'):
            self.agent(driver, 'critic')

    def test_off_ignores_every_optional_source_exception(self):
        self.value['budgeting']['accounting_enabled'] = False
        for failure in (ValidationError, ValueError, TypeError, RuntimeError, UnicodeError):
            with self.subTest(exception=failure.__name__):
                self.gov['project']['id'] = str(uuid.uuid4())
                driver = self.review(state=str(uuid.uuid4()))
                with patch.object(driver.budgeting, '_recovery_database', side_effect=failure('Optional source failed')):
                    self.agent(driver, 'critic')

    def test_other_security_quarantines_block_off_across_tickets(self):
        for field, value in (('actual_model', 'unexpected-model'),
                             ('actual_context_id', 'unexpected-context'),
                             ('operating_hash', '0'*64)):
            with self.subTest(field=field):
                self.gov['project']['id'] = str(uuid.uuid4())
                self.value['budgeting']['accounting_enabled'] = True
                driver = self.review(state=str(uuid.uuid4()))
                observe = driver.observe_budget_outcome
                def mismatched(admission, observation):
                    outcome = observe(admission, observation)
                    outcome[field] = value
                    return outcome
                # The context check needs an expected launch context; this is
                # an external admission observation, never worker authority.
                if field == 'actual_context_id':
                    admission_observer = driver.observe_budget_admission
                    def expected_context(binding, selected):
                        result = admission_observer(binding, selected)
                        result['request']['context_id'] = 'expected-context'
                        return result
                    driver.observe_budget_admission = expected_context
                driver.observe_budget_outcome = mismatched
                run = str(uuid.uuid4())
                with self.assertRaisesRegex(ValidationError, 'quarantined'):
                    driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
                self.failed(driver, run)
                self.value['budgeting']['accounting_enabled'] = False
                restarted = self.review(ticket='EX-11', state=str(uuid.uuid4()))
                with self.assertRaisesRegex(ValidationError, 'quarantin|remediat'):
                    self.agent(restarted, 'critic')

    def test_amendment_post_worker_validation_precedes_success(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        report = dict(candidate=self.candidate, outcome='CHANGED', summary='Fixture amendment')
        self.malformed_cli(driver, json.dumps(report))
        driver.preflight = lambda candidate: None
        driver.snapshot = lambda: deepcopy(self.candidate)
        driver.repository_git_controls = lambda checkout: {}
        def git(checkout, *args, **kwargs):
            if args == ('rev-parse', 'HEAD'):
                return self.candidate['head']
            if args == ('symbolic-ref', '--short', 'HEAD'):
                return self.candidate['head_ref']
            if args == ('diff', '--cached', '--name-only'):
                return 'fixture.txt'
            self.fail('Unexpected Git operation before staging refusal')
        driver.git = git
        run = str(uuid.uuid4())
        with self.assertRaisesRegex(ValidationError, 'staged changes'):
            driver.amend(self.candidate, [], run)
        self.failed(driver, run)

    def test_ambiguous_host_context_settles_failure_and_requires_remediation(self):
        driver = self.review()
        original = driver.run
        def conflicting(name, argv, **kwargs):
            original(name, argv, **kwargs)
            with Path(kwargs['log']).open('a') as stream:
                stream.write('\n' + json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))))
        driver.run = conflicting
        run = str(uuid.uuid4())
        with self.assertRaises(ValidationError):
            driver.agent('critic', self.candidate, [], run, ['fixture.txt'])
        self.failed(driver, run)
        with driver.budgeting._ledger()._transaction() as db:
            row = db.execute('SELECT * FROM model_runs').fetchone()
        self.assertNotEqual(row['status'], 'reserved')
        self.assertEqual(row['actual_tokens'], 100)
        self.assertFalse(json.loads(row['outcome'])['success'])
        self.value['budgeting']['accounting_enabled'] = False
        restarted = self.review(state='ambiguous-restart')
        binding = json.loads((driver.state / 'runs' / run / 'budget-intent.json').read_text())
        driver.budgeting.operations.resolve(binding)
        with self.assertRaisesRegex(ValidationError, 'quarantin|remediat'):
            self.agent(restarted, 'critic')

    def test_production_controller_cycle_ignores_optional_hardlinked_ledger(self):
        config, runner, _ = self.adapter()
        store = ContinuousControllerStore(self.root / 'controller.sqlite', ['A'],
            worktree_roots=[self.root / 'worktree'])
        binding = dict(project_id='project-1', repository_id='101', scope_sha256='a'*64)
        inputs = planning_fixture_inputs([_ticket('tracked.txt')], store, 1, binding=binding, now=NOW)
        self.gov = inputs['planning_config']
        value = deepcopy(inputs['planning_operating_snapshot'].config)
        value['budgeting'] = dict(self.value['budgeting'], accounting_enabled=False)
        snapshot = fixtures.op.validate_operating(value, self.gov)
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        config['codex']['roles']['writer']['reasoning_effort'] = 'high'
        adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
        source, ledger = self.root / 'unrelated-cycle', self.root / 'hardlinked.sqlite'
        source.write_bytes(b'Unrelated optional cycle source')
        os.link(source, ledger)
        head, tree = [fixtures.subprocess.check_output(['git', '-C', str(fixtures.ROOT),
            'rev-parse', ref], text=True).strip() for ref in ('HEAD', 'HEAD^{tree}')]
        result = production_controller_cycle(store, controller_config=dict(dispatch_enabled=True,
            auto_dispatch=True, auto_request_critic=True, auto_resume_amendments=True), now=NOW,
            host_capacity=1, **{**planning_resource_inputs(1), **inputs}, inventory_binding=binding,
            repository_root=fixtures.ROOT, repository_head_sha=head, repository_tree_sha=tree,
            publication_config=config, budget_ledger_path=ledger,
            **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations',
                'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
        for receipt in result['dispatch_receipts']:
            record = json.loads((runner._budget_record_dir / (receipt['dispatch_id'] + '.json')).read_text())
            runner.observe(record, timeout=10)
        self.assertEqual(len(result['dispatch_receipts']), 1)
        self.assertFalse(result['errors'])
        self.assertEqual(source.read_bytes(), b'Unrelated optional cycle source')

    def test_review_wrapper_validation_precedes_success(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        driver.collect_provenance_observation = lambda: deepcopy(self.candidate)
        driver.validate_local_provenance = lambda *args, **kwargs: None
        driver.publish_critic_evidence = lambda binding: {}
        config_reads = []
        def git(checkout, *args, **kwargs):
            if args == ('config', '--list', '--includes', '--null'):
                config_reads.append(1)
                return 'before' if len(config_reads) == 1 else 'changed'
            self.fail('Unexpected Git operation before config refusal')
        driver.git = git
        run = str(uuid.uuid4())
        with self.assertRaisesRegex(ValidationError, 'changed Git configuration'):
            driver.review(self.candidate, [], run, ['fixture.txt'])
        self.failed(driver, run)

    def test_security_quarantine_migration_does_not_skip_successful_process(self):
        driver = self.review()
        observe = driver.observe_budget_outcome
        driver.observe_budget_outcome = lambda admission, observation: dict(observe(admission, observation),
            actual_model='unexpected-model')
        with self.assertRaisesRegex(ValidationError, 'quarantined'):
            self.agent(driver, 'critic')
        ledger = driver.budgeting.database.path
        with patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'migrated-state')}):
            migrated = self.review(state='migration', ledger_path=ledger)
            migrated.budgeting.migrate_operational_recovery()
            self.value['budgeting']['accounting_enabled'] = False
            off = self.review(ticket='EX-11', state='migrated-off', ledger_path=ledger)
            with self.assertRaisesRegex(ValidationError, 'quarantin|remediat'):
                self.agent(off, 'critic')

    def test_security_state_loss_cannot_recreate_an_empty_quarantine(self):
        self.value['budgeting']['accounting_enabled'] = False
        driver = self.review()
        self.agent(driver, 'critic')
        # Simulate corruption outside the protected API; absence is not proof
        # that no security incident ever existed.
        import sqlite3
        with closing(sqlite3.connect(driver.budgeting.operations.path)) as db:
            db.execute('DROP TABLE execution_quarantines')
            db.execute('DROP TABLE execution_remediations')
        restarted = self.review(state='security-loss')
        with self.assertRaisesRegex(ValidationError, 'evidence incomplete|restore'):
            self.agent(restarted, 'critic')

    def test_stale_reviews_consume_the_two_resolution_allowance(self):
        for enabled in (False, True):
            with self.subTest(accounting=enabled):
                self.gov['project']['id'] = str(uuid.uuid4())
                self.value['budgeting']['accounting_enabled'] = enabled
                driver = self.review(state=str(uuid.uuid4()))
                report = dict(candidate=dict(self.candidate, head='c'*40), verdict='APPROVE',
                    reviewed_files=['fixture.txt'], findings=[], summary='Stale review')
                launches = self.malformed_cli(driver, json.dumps(report))
                store, config = self.loop(driver)
                for count in (1, 2, 3):
                    self.assertEqual(tick(store, config, driver)['phase'], 'PAUSED')
                    paused = store.get(config['key'])
                    run = paused['inflight']['id']
                    self.failed(driver, run)
                    if count < 3:
                        if enabled:
                            driver.budgeting._ledger().resolve_failure(run, dict(verified=True,
                                evidence_ref='fixture-stale-recovery-' + str(count), reason='Verified stale result recovery'))
                        resume(store, config, self.candidate, run)
                    else:
                        with self.assertRaisesRegex(ValidationError, 'recovery retry limit exhausted'):
                            resume(store, config, self.candidate, run)
                    self.assertEqual(len(launches), count)
                with driver.budgeting.operations.transaction('') as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM execution_resolutions').fetchone()[0], 2)

    def test_adapter_independence_stop_survives_off_restart(self):
        self.value['budgeting']['accounting_enabled'] = False
        worker_driver = self.review()
        thread = worker_driver.codex_run_id(self.agent(worker_driver, 'worker'))
        runner, adapters = self.configured_adapter(True, thread=thread)
        payload = dict(_dispatch_payload(str(uuid.uuid4()), role='critic'), ticket='EX-10')
        try:
            with self.assertRaisesRegex(ValidationError, 'independent|quarantined'):
                self.dispatch(adapters, payload)
        finally:
            record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
            try:
                runner.observe(record, timeout=10)
                adapters['observe_dispatch'](payload)
            except ValidationError:
                pass
        _, restarted = self.configured_adapter(False)
        next_payload = dict(_dispatch_payload(str(uuid.uuid4()), role='critic'), ticket='EX-10')
        with self.assertRaisesRegex(ValidationError, 'quarantin|remediat|resolve non-reasoning failure'):
            self.dispatch(restarted, next_payload)
        next_record = json.loads((runner._budget_record_dir / (next_payload['dispatch_id'] + '.json')).read_text())
        self.assertNotIn('pid', next_record)

    def test_conflicting_adapter_contexts_always_settle_and_record_failure(self):
        base_config, _, _ = self.adapter()
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                self.gov['project']['id'] = str(uuid.uuid4())
                config = deepcopy(base_config)
                config['codex']['run_record_directory'] = str(self.root / ('records-' + str(exit_code)))
                config['github']['project_id'] = self.gov['project']['id']
                first, second = str(uuid.uuid4()), str(uuid.uuid4())
                class ConflictingCli(fixtures.ControllerCli):
                    def launch(inner, argv, **kwargs):
                        program = ('import json,time; print(json.dumps(dict(type="thread.started",thread_id=' + repr(first) +
                            ')),flush=True); time.sleep(0.3); print(json.dumps(dict(type="thread.started",thread_id=' + repr(second) +
                            ')),flush=True); print(json.dumps(dict(type="turn.completed",usage=dict(input_tokens=70,output_tokens=30))),flush=True); raise SystemExit(' + str(exit_code) + ')')
                        return fixtures.ADAPTER['_SubprocessRunner'].launch(inner, [sys.executable, '-B', '-c', program], **kwargs)
                runner = ConflictingCli(record_dir=config['codex']['run_record_directory'])
                adapters = fixtures.ADAPTER['build_adapters'](dict(config, _runner=runner, _clock=FakeClock()))
                context = adapters['dispatch_ticket'].configure_budgeting(path=self.root / (str(uuid.uuid4()) + '.sqlite'),
                    governance=self.gov, operating=fixtures.op.validate_operating(self.value, self.gov))
                payload = dict(_dispatch_payload(str(uuid.uuid4())), ticket='EX-10')
                try:
                    with self.assertRaisesRegex(ValidationError, 'context|identity'):
                        self.dispatch(adapters, payload)
                finally:
                    record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
                    try:
                        runner.observe(record, timeout=10)
                    except ValidationError:
                        pass
                record = json.loads((runner._budget_record_dir / (payload['dispatch_id'] + '.json')).read_text())
                self.assertEqual(record['status'], 'FAILED')
                self.assertTrue(Path(record['terminal_proof_path']).is_file())
                with context._ledger()._transaction() as db:
                    row = db.execute('SELECT * FROM model_runs').fetchone()
                self.assertNotEqual(row['status'], 'reserved')
                self.assertEqual(row['actual_tokens'], 100)
                self.assertFalse(json.loads(row['outcome'])['success'])
                with context.operations.transaction('') as db:
                    self.assertEqual(db.execute('SELECT success FROM execution_outcomes').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
