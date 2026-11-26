"""R315r1-F01: authenticated Jira closure through the complete production gate."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
import runpy
import subprocess
import threading
import unittest
from unittest.mock import patch

import source_only
from test_awf307_jira_owner_policy import JiraFixture, ROOT, NOW, ISSUE, controller
from agentic import ValidationError
from agentic.canonical import canonical, sha256
from agentic.closeout import git, require_jira_reconciliation_before_counts
from agentic.jira_owner_policy import context_available
from agentic.policy import policy_hash
from agentic.lifecycle import definition


@unittest.skipIf(source_only.committed_git_tuple(ROOT) is None, source_only.COMMITTED_GIT_REASON)
class ProductionCountsTests(JiraFixture):
    def setUp(self):
        super().setUp()
        # Own the repository metadata read by isolated closeout Git plumbing.
        # Borrow immutable objects only; never commit or change source refs.
        self.project = self.outer / 'repository'
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', '--quiet',
                        str(ROOT), str(self.project)], check=True, capture_output=True,
                       timeout=120)
        self.store = controller.ContinuousControllerStore(self.outer / 'counts.sqlite', ['A'],
            worktree_roots=[self.project])
        self.binding = {'project_id': self.config['project']['id'], 'repository_id': 101,
            'issue_id': ISSUE, 'requirements_hash': 'a' * 64, 'contract_hash': 'b' * 64,
            'policy_hash': policy_hash(self.config, definition()), 'candidate_id': 'd' * 64}
        # Reconstruct the intent, whose operation identity includes its binding.
        from agentic.jira_lifecycle import transition_record
        self.started = transition_record(self.binding, 'WORKER_STARTED', '10118', '10119', '21',
            jira_provider=self.provider, merge_result_id=None, producer_id='controller',
            run_id=self.started['run_id'], now=NOW, evidence=self.started['evidence'])
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))
        # Cache only immutable schema construction, preserving actual validation,
        # provider registrations/receipts and the unchanged observer deadline.
        self.enterContext(patch('agentic.contracts.Contracts', return_value=self.contracts))
        self.stack = self.enterContext(ExitStack())
        self.script = runpy.run_path(str(ROOT / '.agentic/scripts/continuous_controller.py'))

    def prepare(self, *, actor='controller'):
        self.http.actor = actor
        with self.policy_reads():
            result = controller.production_jira_lifecycle(self.store, config=self.config,
                contract=self.contract, event='JIRA_RECONCILED',
                facts={'merge_confirmed': True, 'closeout_valid': True}, binding=self.binding,
                issue_type='LEAF', state='MERGED', producer_id='controller',
                run_id='00000000-0000-4000-8000-000000000052', now=NOW,
                evidence=['urn:awf:fixture:merge-closeout'], transition_id='31',
                merge_result_id='00000000-0000-4000-8000-000000000043',
                **{key: self.adapters[key] for key in ('read_current_status', 'write_transition',
                    'read_transition', 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
        self.assertIn('owner_closure', result, result)
        head = git(self.project, 'rev-parse', 'HEAD')
        path = 'AGENTS.md'
        closeout = {'schema_version': 3,
            'record_id': '00000000-0000-4000-8000-000000000053', 'created_at': NOW,
            'producer_id': 'fixture-controller', 'run_id': '00000000-0000-4000-8000-000000000054',
            'binding': deepcopy(self.binding), 'pr_number': 32, 'reviewed_head_sha': head,
            'base_sha': git(self.project, 'rev-parse', 'HEAD^'), 'merge_commit_sha': head,
            'merge_tree_sha': git(self.project, 'rev-parse', 'HEAD^{tree}'),
            'bound_files': [{'path': path, 'blob_sha': git(self.project, 'rev-parse', head + ':' + path),
                             'sha256': sha256(git(self.project, 'show', head + ':' + path, binary=True))}],
            'gate_record_id': '00000000-0000-4000-8000-000000000055',
            'review_record_ids': ['00000000-0000-4000-8000-000000000056'],
            'jira_transition_record_id': result['record']['record_id'],
            'evidence': ['urn:awf:fixture:closeout']}
        target = Path(self.adapter_config['observations']['closeout_directory']) / (closeout['record_id'] + '.json')
        target.write_bytes(canonical({'record': closeout,
            'merge_result_id': result['record']['merge_result_id'],
            'merge_observed_at': '2026-10-09T08:00:00Z'}))
        self.gate = self.script['_count_gate_inputs']({'closeout_record': closeout},
            config=self.config, repository=self.project, contracts=self.contracts, adapters=self.adapters,
            adapter_sha256=sha256((ROOT / '.agentic/adapters/reference_controller_adapter.py').read_bytes()),
            observed_at=NOW, store=self.store)
        return result

    def count(self):
        with self.policy_reads():
            return require_jira_reconciliation_before_counts(**self.gate)

    def progress(self):
        with self.policy_reads():
            return controller.post_merge_jira_progress(jira_enabled=True, merged_ticket=ISSUE,
                scope='project = EX', observed_at=NOW,
                jira_binding={'cloud_id': 'cloud-1', 'project_id': 'project-1', 'actor_id': 'controller'},
                fetch_scope_page=self.adapters['fetch_scope_page'], count_gate=self.gate,
                store=self.store, config=self.config,
                **{key: self.adapters[key] for key in ('observe_provider_identity',
                    'observe_issue_identity', 'observe_issue_policy', 'read_current_status')})

    @contextmanager
    def after_observation(self, mutate):
        from agentic.integration_queue import _registered_provider_observation
        def observed(*args, **kwargs):
            value = _registered_provider_observation(*args, **kwargs)
            if args[2]['operation'] == 'JIRA_RECONCILIATION':
                self.assertFalse(context_available())
                mutate()
            return value
        with patch('agentic.integration_queue._registered_provider_observation', side_effect=observed):
            yield

    def assert_counted(self):
        result = self.count()
        self.assertEqual('PASS', result['status'])
        self.assertTrue(result['count_allowed'])
        self.assertEqual('fast_forward', result['closeout_validation']['merge_relationship'])
        progress = self.progress()
        self.assertEqual('COUNTED', progress['jira_state'], progress)
        self.assertEqual((1, 1), (progress['closed'], progress['remaining_open']))
        self.assertFalse(context_available())
        self.assertTrue(all(call[0] == 'GET' for call in self.http.calls))

    def test_shared_closure_counts_through_real_adapter_and_closeout(self):
        self.prepare()
        self.assert_counted()

    def test_distinct_owner_control_counts_through_same_gate(self):
        self.prepare(actor='fixture-owner')
        self.assert_counted()

    def test_shared_closure_reports_counted_production_progress(self):
        self.prepare()
        progress = self.progress()
        self.assertEqual('COUNTED', progress['jira_state'], progress)
        self.assertEqual((1, 1), (progress['closed'], progress['remaining_open']))

    def test_count_gate_without_runtime_scope_refuses_shared_proof(self):
        self.prepare()
        self.gate.pop('jira_owner_closure_scope', None)
        with self.assertRaisesRegex(ValidationError, 'protected issue-owned controller context'):
            self.count()

    def test_observed_proof_must_still_match_protected_retained_bytes(self):
        result = self.prepare()
        def remove():
            with self.store.transaction() as db:
                db.execute('DELETE FROM controller_meta WHERE key=?',
                    ('jira-owner-closure:' + result['record']['record_id'],))
        with self.after_observation(remove):
            with self.assertRaisesRegex(ValidationError, 'retained closure is missing'):
                self.count()

    def test_shared_proof_without_protected_context_is_refused(self):
        result = self.prepare()
        with self.assertRaisesRegex(ValidationError, 'protected issue-owned controller context'):
            controller.validate_jira_owner_closure(
                {key: result[key] for key in ('record', 'owner_closure')}, self.request(result) | {'binding': self.binding}, now=NOW)

    def test_policy_change_between_observation_and_validation_is_refused(self):
        self.prepare()
        with self.after_observation(lambda: setattr(self, 'commit', 'b' * 40)):
            with self.assertRaisesRegex(ValidationError, 'policy|Policy'):
                self.count()

    def test_keyword_change_between_observation_and_validation_is_refused(self):
        self.prepare()
        with self.after_observation(lambda: setattr(self.http, 'keyword', False)):
            with self.assertRaisesRegex(ValidationError, 'keyword'):
                self.count()

    def test_scope_change_between_observation_and_validation_is_refused(self):
        self.prepare()
        original = self.adapters['observe_issue_policy']
        def outside(issue):
            value = original(issue)
            value['snapshot']['labels'] = []
            return value
        with self.after_observation(lambda: self.adapters.update(observe_issue_policy=outside)):
            with self.assertRaisesRegex(ValidationError, 'scope'):
                self.count()

    def add_done_intent(self, status):
        from agentic.jira_lifecycle import transition_record
        record = transition_record(self.binding, 'JIRA_RECONCILED', '10117', '10120', '31',
            jira_provider=self.provider, merge_result_id='00000000-0000-4000-8000-000000000043',
            producer_id='controller', run_id='00000000-0000-4000-8000-000000000057',
            now=NOW, evidence=['urn:awf:fixture:done-intent'])
        self.store.prepare_jira_operation(record, NOW,
            identity=controller._jira_operation_identity(self.config, record))
        self.store.begin_jira_operation(record['operation_id'], NOW)
        self.store.finish_jira_operation(record['operation_id'], status,
            {'issue_id': ISSUE, 'jira_provider': self.provider, 'status_id': '10120',
             'actor': 'controller', 'observed_at': NOW}, NOW,
            operation={'operation_id': record['operation_id'], 'issue_id': ISSUE,
                       'jira_provider': self.provider, 'status': 'ATTEMPTED', 'observed_at': NOW})

    def test_attempted_done_between_observation_and_validation_is_refused(self):
        self.prepare()
        with self.after_observation(lambda: self.add_done_intent('SUCCEEDED')):
            with self.assertRaisesRegex(ValidationError, 'Done intent'):
                self.count()

    def test_unknown_attempt_between_observation_and_validation_is_refused(self):
        self.prepare()
        with self.after_observation(lambda: self.add_done_intent('UNKNOWN')):
            with self.assertRaisesRegex(ValidationError, 'attempted, unknown or in-flight'):
                self.count()

    def test_controller_write_in_flight_at_validation_is_refused(self):
        self.prepare()
        def claim():
            self.store.begin_jira_operation(self.started['operation_id'], NOW)
            owned = self.stack.enter_context(controller._jira_claim_ownership(self.store, self.started['operation_id']))
            self.assertTrue(owned)
        with self.after_observation(claim):
            with self.assertRaisesRegex(ValidationError, 'attempted, unknown or in-flight'):
                self.count()

    def test_exclusive_issue_ownership_is_reacquired_at_validation(self):
        self.prepare()
        def own_issue():
            self.assertTrue(self.stack.enter_context(controller._batch_jira_ownership(self.store, issue_id=ISSUE)))
        with self.after_observation(own_issue):
            with self.assertRaisesRegex(ValidationError, 'live controller writer'):
                self.count()

    def test_validation_rechecks_history_before_scope_exit(self):
        self.prepare()
        original = controller.validate_jira_owner_closure
        caller = threading.get_ident()
        def validate(*args, **kwargs):
            value = original(*args, **kwargs)
            if threading.get_ident() == caller:
                self.add_done_intent('UNKNOWN')
            return value
        with patch.object(controller, 'validate_jira_owner_closure', side_effect=validate):
            with self.assertRaisesRegex(ValidationError, 'attempted, unknown or in-flight'):
                self.count()
