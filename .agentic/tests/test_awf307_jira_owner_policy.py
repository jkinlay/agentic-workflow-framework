"""AWF-307 Jira: real reference reads under protected controller admission."""
from copy import deepcopy
from pathlib import Path
import json
import os
import runpy
import tempfile
import unittest
from unittest.mock import patch

from test_awf307_provider_owner_policy import ProviderPolicyFixture
from test_reference_controller_adapter import ROOT, FakeHttp, FakeClock, FakeRunner, _base_config
from agentic import ValidationError
from agentic.canonical import canonical, load, sha256
from agentic.configuration import inspect_config
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from agentic import continuous_controller as controller

KEY = 'shared_owner_controller_actor_id'
FIELD = 'shared_owner_controller_evidence'
NOW = '2026-10-09T08:00:03Z'
ISSUE = 'EX-42'


class ClosureHttp(FakeHttp):
    def __init__(self):
        super().__init__()
        self.problem = None
        self.actor = 'controller'
        self.keyword = True
        self.owner_done = True

    def respond(self, method, url, *, body):
        if '?fields=project,parent,labels,components,issuetype,summary' in url:
            return 200, json.dumps({'key': ISSUE, 'fields': {
                'project': {'id': 'project-1', 'key': 'EX'}, 'labels': ['awf-fixture'],
                'components': [], 'issuetype': {'name': 'Task'},
                'summary': 'Release acceptance' if self.keyword else 'Routine work'}})
        if '/issue/' in url and '?fields=status' in url:
            if not self.owner_done:
                return super().respond(method, url, body=body)
            histories = [{'id': '2', 'created': '2026-10-09T08:00:01.195+0000',
                'author': {'accountId': self.actor}, 'items': [
                    {'field': 'status', 'from': '10117', 'to': '10120'}]}]
            if getattr(self, 'transition_bodies', []):
                # The instrumented In Progress write precedes the owner's
                # external In Review and Done actions in this synthetic history.
                histories = [
                    {'id': '1', 'created': '2026-10-09T07:59:59Z', 'author': {'accountId': self.actor},
                     'items': [{'field': 'status', 'from': '10116', 'to': '10118'}]},
                    {'id': '2', 'created': '2026-10-09T08:00:00Z', 'author': {'accountId': self.actor},
                     'items': [{'field': 'status', 'from': '10118', 'to': '10119'}]},
                    {'id': '3', 'created': '2026-10-09T08:00:00.500Z', 'author': {'accountId': self.actor},
                     'items': [{'field': 'status', 'from': '10119', 'to': '10117'}]},
                    {**histories[0], 'id': '4'}]
            page = {'startAt': 0, 'total': len(histories), 'histories': histories}
            value = {'key': ISSUE, 'fields': {'status': {'id': '10120'},
                'updated': '2026-10-09T08:00:02.195+0000'}, 'changelog': page}
            if self.problem == 'gap':
                page['startAt'] = 1
            elif self.problem == 'incomplete':
                page['total'] = 2
            elif self.problem == 'corrupt':
                histories.append(deepcopy(histories[0])); page['total'] = 2
            elif self.problem == 'actor':
                histories[0].pop('author')
            elif self.problem == 'provider-time':
                value['fields'].pop('updated')
            elif self.problem == 'later':
                histories.append({'id': '3', 'created': '2026-10-09T08:00:02Z',
                    'author': {'accountId': 'controller'},
                    'items': [{'field': 'status', 'from': '10120', 'to': '10117'}]})
                page['total'] = 2
            return 200, json.dumps(value)
        return super().respond(method, url, body=body)


class JiraFixture(ProviderPolicyFixture):
    def setUp(self):
        super().setUp()
        self.config['merge_gate'].update(shared_owner_automation_actor_id=None, trusted_owner_ids=[7777])
        self.config['github'].update(repository='example/project')
        self.config['jira'].update(cloud_id='cloud-1', provider_project_id='project-1',
            controller_actor_id='controller', shared_owner_controller_actor_id='controller')
        self.config['jira']['status_map'].update(done='10120', in_review='10117', ready='10118', in_progress='10119')
        self.accept()
        outer = tempfile.TemporaryDirectory()
        self.addCleanup(outer.cleanup)
        self.outer = Path(outer.name)
        self.adapter_config = _base_config(self.outer)
        project_path = Path(self.adapter_config['observations']['accepted_project_config']['path'])
        project_path.write_bytes(canonical(self.config))
        self.adapter_config['observations']['accepted_project_config']['sha256'] = sha256(project_path.read_bytes())
        self.store = controller.ContinuousControllerStore(self.outer / 'controller.sqlite', ['A'], worktree_roots=[self.project])
        self.http = ClosureHttp()
        self.clock = FakeClock(NOW)
        self.env = patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-token')
        self.env.start(); self.addCleanup(self.env.stop)
        ns = runpy.run_path(str(ROOT / '.agentic/adapters/reference_controller_adapter.py'))
        self.adapters = ns['build_adapters']({**self.adapter_config, '_http_transport': self.http,
            '_clock': self.clock, '_runner': FakeRunner()})
        self.contract = {'owner_closure_required': True}
        self.provider = {'cloud_id': 'cloud-1', 'site': 'https://jira.example.invalid',
            'project_id': 'project-1', 'project_key': 'EX', 'controller_actor_id': 'controller'}
        from agentic.jira_lifecycle import transition_record
        self.started = transition_record({'issue_id': ISSUE}, 'WORKER_STARTED', '10118', '10119', '21',
            jira_provider=self.provider, merge_result_id=None, producer_id='controller',
            run_id='00000000-0000-4000-8000-000000000040', now=NOW, evidence=['urn:awf:fixture:registered-run'])
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))

    def accept(self):
        self.accepted = deepcopy(self.config)
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)

    def close(self, *, now=NOW):
        with self.policy_reads():
            return controller.production_jira_lifecycle(self.store, config=self.config, contract=self.contract,
                event='JIRA_RECONCILED', facts={'merge_confirmed': True, 'closeout_valid': True},
                binding={'issue_id': ISSUE}, issue_type='LEAF', state='MERGED',
                producer_id='controller', run_id='00000000-0000-4000-8000-000000000042', now=now,
                evidence=['urn:awf:fixture:merge-closeout'], transition_id='31',
                merge_result_id='00000000-0000-4000-8000-000000000043',
                **{key: self.adapters[key] for key in ('read_current_status', 'write_transition', 'read_transition',
                    'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})

    def success(self):
        result = self.close()
        self.assertIn('owner_closure', result, result)
        proof = result['owner_closure']
        self.assertEqual('controller', proof['actor'])
        self.assertTrue(proof[FIELD]['shared_owner_controller_account'])
        self.assertEqual(proof[FIELD]['owner_policy_sha256'], proof[FIELD]['accepted_owner_policy']['policy_sha256'])
        self.assertTrue(all(call[0] == 'GET' for call in self.http.calls))
        return result

    def refusal(self):
        try:
            result = self.close()
        except ValidationError as exc:
            result = {'reason': str(exc)}
        self.assertNotIn('owner_closure', result, result)
        self.assertTrue(all(call[0] == 'GET' for call in self.http.calls))
        return result

    def held_batch(self):
        batch = {'batch_id': 'fixture-batch', 'pr_number': 44, 'merge_commit_sha': 'd' * 40,
            'constituents': [{'binding': {'issue_id': ISSUE}, 'pr_number': 2,
                'reviewed_head_sha': 'a' * 40, 'paths': ['src/fixture.py'],
                'closeout_kind': 'merged-via-batch', 'batch_id': 'fixture-batch', 'merge_commit_sha': 'd' * 40}]}
        policy = self.adapters['observe_issue_policy'](ISSUE)
        with self.policy_reads():
            return controller.production_batch_jira_reconciliation(self.store, config=self.config, role='controller',
                batch=batch, observed_merge={'batch_id': 'fixture-batch', 'target_branch': 'main',
                    'merge_commit_sha': 'd' * 40, 'observed_at': NOW}, now=NOW,
                policy_context={ISSUE: {'issue_type': 'LEAF', 'owner_closure_required': True, 'snapshot': policy['snapshot']}},
                **{key: self.adapters[key] for key in ('read_current_status', 'write_transition', 'read_transition',
                                                       'observe_issue_identity')})

    def request(self, value):
        return {'operation': 'JIRA_RECONCILIATION', 'provider_identity': self.provider,
            'source_identity': 'jira_issue_transition_and_readback', 'binding': {'issue_id': ISSUE},
            'issue_id': ISSUE, 'transition_record_id': value['record']['record_id'], 'expected_status_id': '10120'}


class JiraControllerPolicyTests(JiraFixture):
    def test_empty_history_refuses_shared_actor_but_preserves_distinct_owner(self):
        self.success()
        self.store = controller.ContinuousControllerStore(self.outer / 'empty.sqlite', ['A'],
            worktree_roots=[self.project], accepted_policy_root=self.project)
        self.refusal()
        self.http.actor = 'fixture-owner'
        result = self.close()
        self.assertEqual('fixture-owner', result['owner_closure']['actor'], result)
        self.assertNotIn(FIELD, result['owner_closure'])

    def test_both_opt_outs_preserve_instrumented_non_done_history(self):
        self.config['github']['expected_actor_id'] = 1002
        self.config['merge_gate'].update(shared_owner_automation_actor_id=1002, trusted_owner_ids=[1002])
        self.accept()
        path = Path(self.adapter_config['observations']['accepted_project_config']['path'])
        path.write_bytes(canonical(self.config))
        self.adapter_config['observations']['accepted_project_config']['sha256'] = sha256(path.read_bytes())
        self.http.owner_done = False
        with self.policy_reads():
            result = controller.production_jira_lifecycle(self.store, config=self.config, contract=self.contract,
                event='WORKER_STARTED', facts={'run_registered': True, 'worktree_verified': True},
                binding={'issue_id': ISSUE}, issue_type='LEAF', state='DISPATCHED', producer_id='controller',
                run_id=self.started['run_id'], now=NOW, evidence=self.started['evidence'], transition_id='21',
                **{key: self.adapters[key] for key in ('read_current_status', 'write_transition', 'read_transition',
                    'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
        self.assertEqual('SUCCEEDED', result.get('record', {}).get('status'), result)
        self.assertEqual(['21'], [body['transition']['id'] for body in self.http.transition_bodies])
        self.http.calls.clear()
        self.http.owner_done = True
        self.success()

    def test_explicit_host_root_supports_multiple_worktrees(self):
        self.success()
        other = self.outer / 'other-worker'; other.mkdir()
        self.store = controller.ContinuousControllerStore(self.outer / 'multi.sqlite', ['A'],
            worktree_roots=[self.project, other], accepted_policy_root=self.project)
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))
        self.success()

    def test_ambiguous_multiple_worktrees_cannot_choose_policy(self):
        self.success()
        other = self.outer / 'other-worker'; other.mkdir()
        self.store = controller.ContinuousControllerStore(self.outer / 'ambiguous.sqlite', ['A'],
            worktree_roots=[self.project, other])
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))
        self.refusal()

    def test_unenrolled_host_policy_root_refuses(self):
        self.success()
        other = self.outer / 'unenrolled'; other.mkdir()
        with self.assertRaisesRegex(ValidationError, 'Owner policy root differs'):
            controller.ContinuousControllerStore(self.outer / 'unenrolled.sqlite', ['A'],
                worktree_roots=[self.project], accepted_policy_root=other)

    def test_c_readable_complete_identity_bound_journal_required(self):
        self.assertEqual([ISSUE], self.held_batch()['held'])
        self.success()
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM controller_batch_jira_operation').fetchone()
            db.execute('UPDATE controller_batch_jira_operation SET intent_sha256=?', ('0' * 64,))
        self.refusal()
        with self.store.transaction() as db:
            db.execute('UPDATE controller_batch_jira_operation SET intent_sha256=?', (row['intent_sha256'],))
            db.execute('DELETE FROM controller_meta WHERE key=?', ('jira-identity:' + row['operation_id'],))
        self.refusal()

    def test_d_batch_held_before_write_does_not_block_owner_closure(self):
        self.assertEqual([ISSUE], self.held_batch()['held'])
        self.success()
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM controller_batch_jira_operation').fetchone()
            db.execute('DELETE FROM controller_meta WHERE key=?', ('jira-held-before-write:' + row['operation_id'],))
        self.refusal()

    def test_d_attempted_or_unknown_batch_intent_keeps_hold(self):
        self.assertEqual([ISSUE], self.held_batch()['held'])
        self.success()
        for status in ('UNKNOWN', 'IN_FLIGHT', 'FAILED', 'PENDING', 'SUCCEEDED'):
            with self.subTest(status=status):
                with self.store.transaction() as db:
                    db.execute('UPDATE controller_batch_jira_operation SET status=?', (status,))
                self.refusal()

    def test_e_live_issue_writer_cannot_be_reconciled(self):
        self.success()
        with controller._batch_jira_ownership(self.store, issue_id=ISSUE) as owned:
            self.assertTrue(owned)
            self.refusal()

    def test_e_inflight_ordinary_claim_keeps_ticket_held(self):
        self.success()
        from agentic.jira_lifecycle import transition_record
        record = transition_record({'issue_id': ISSUE}, 'WORKER_STARTED', '10118', '10119', '21',
            jira_provider=self.provider, merge_result_id=None, producer_id='controller',
            run_id='00000000-0000-4000-8000-000000000044', now=NOW, evidence=['urn:awf:fixture:claim'])
        self.store.prepare_jira_operation(record, NOW, identity=controller._jira_operation_identity(self.config, record))
        self.store.begin_jira_operation(record['operation_id'], NOW)
        with controller._jira_claim_ownership(self.store, record['operation_id']) as owned:
            self.assertTrue(owned)
            self.refusal()

    def test_a_contract_and_keyword_both_required(self):
        self.success()
        self.contract['owner_closure_required'] = False
        self.refusal()
        self.contract['owner_closure_required'] = True
        self.http.keyword = False
        self.refusal()

    def test_b_complete_latest_actor_and_provider_time_required(self):
        self.success()
        for problem in ('gap', 'incomplete', 'corrupt', 'actor', 'provider-time', 'later'):
            with self.subTest(problem=problem):
                self.http.problem = problem
                self.refusal()

    def test_supplied_working_feature_policy_never_enables(self):
        self.success()
        self.accepted['jira'][KEY] = None
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        self.refusal()

    def test_working_file_cannot_enable_default_policy_exception(self):
        self.success()
        folder = self.project / '.agentic'; folder.mkdir()
        (folder / 'PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        self.accepted['jira'][KEY] = None
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        self.refusal()

    def test_feature_ref_cannot_enable_exception(self):
        self.success()
        self.ref = 'refs/remotes/origin/feature-opt-out'
        self.refusal()

    def test_policy_drift_after_adapter_read_refuses_before_retention(self):
        self.success()
        original = self.adapters['read_current_status']
        def moved(binding):
            value = original(binding)
            if 'owner_closure_status_id' in binding:
                self.commit = 'b' * 40
            return value
        self.adapters['read_current_status'] = moved
        self.refusal()

    def test_returned_annotation_cannot_mutate_the_accepted_policy_pin(self):
        self.success()
        self.store = controller.ContinuousControllerStore(self.outer / 'alias.sqlite', ['A'], worktree_roots=[self.project])
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))
        original = self.adapters['read_current_status']
        def moved(binding):
            value = original(binding)
            if 'owner_closure_status_id' in binding:
                self.commit = 'b' * 40
                value['owner_closure'][FIELD]['accepted_owner_policy']['default_branch_commit'] = self.commit
            return value
        self.adapters['read_current_status'] = moved
        self.refusal()

    def test_callback_cannot_substitute_the_shared_closure_provider(self):
        self.success()
        self.store = controller.ContinuousControllerStore(self.outer / 'provider.sqlite', ['A'], worktree_roots=[self.project])
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))
        original = self.adapters['read_current_status']
        def replaced(binding):
            value = original(binding)
            if 'owner_closure_status_id' in binding:
                binding['jira_provider']['site'] = 'https://fixture-other.invalid'
                value['owner_closure']['jira_provider']['site'] = 'https://fixture-other.invalid'
            return value
        self.adapters['read_current_status'] = replaced
        self.refusal()

    def test_different_controller_account_refuses(self):
        self.success()
        self.config['jira']['controller_actor_id'] = 'fixture-other-controller'
        self.refusal()

    def test_drift_refuses(self):
        self.success()
        with self.policy_reads(drift=True):
            # close() installs its own immutable reader, so exercise the actual
            # shared-policy admission below that wrapper using the live helper.
            from agentic.jira_owner_policy import closure_scope
            with self.assertRaises(ValidationError):
                with closure_scope(self.store, self.config, ISSUE, self.contract,
                    {'issue_type': 'LEAF', 'snapshot': {'summary': 'Release acceptance', 'labels': ['awf-fixture']}}):
                    pass

    def test_null_preserves_same_actor_refusal_message(self):
        self.config['jira'][KEY] = None; self.accept()
        result = self.refusal()
        self.assertIn('Owner closure actor equals the controller actor', result['reason'])

    def test_null_preserves_distinct_owner_closure(self):
        self.config['jira'][KEY] = None; self.accept(); self.http.actor = 'fixture-owner'
        result = self.close()
        self.assertEqual('fixture-owner', result['owner_closure']['actor'])
        self.assertNotIn(FIELD, result['owner_closure'])

    def test_adapter_without_controller_context_keeps_refusal(self):
        self.success()
        self.adapter_config['jira'][KEY] = 'controller'
        with self.assertRaisesRegex(ValidationError, 'Owner closure actor equals the controller actor'):
            self.adapters['read_current_status']({'issue_id': ISSUE, 'jira_provider': self.provider,
                'owner_closure_status_id': '10120'})

    def test_retained_adapter_reobserves_same_complete_history(self):
        value = self.success()
        retained = {key: value[key] for key in ('record', 'owner_closure')}
        from agentic.jira_owner_policy import retained_scope
        with self.policy_reads(), retained_scope(self.store, self.config, retained):
            result = self.adapters['observe_jira_reconciliation'](self.request(value), retained_owner_closure=retained)
        self.assertEqual(retained, result['result'])
        self.http.problem = 'gap'
        with self.policy_reads(), self.assertRaises(ValidationError):
            with retained_scope(self.store, self.config, retained):
                self.adapters['observe_jira_reconciliation'](self.request(value), retained_owner_closure=retained)

    def test_both_opt_outs_coexist_without_rootless_global_lift(self):
        self.config['github']['expected_actor_id'] = 1002
        self.config['merge_gate'].update(shared_owner_automation_actor_id=1002, trusted_owner_ids=[1002])
        self.accept()
        path = Path(self.adapter_config['observations']['accepted_project_config']['path'])
        path.write_bytes(canonical(self.config))
        self.adapter_config['observations']['accepted_project_config']['sha256'] = sha256(path.read_bytes())
        result = self.success()
        self.assertEqual(2, len(result['owner_closure'][FIELD]['accepted_owner_policy']['warnings']))
        from agentic.authorization import trusted_owner_ids
        with self.assertRaisesRegex(ValidationError, 'The configured automation actor must not be a trusted owner'):
            trusted_owner_ids(self.config)

    def test_missing_operation_history_stays_held(self):
        self.success()
        with self.store.transaction() as db:
            db.execute('DELETE FROM controller_jira_operation')
        self.refusal()

    def test_epics_never_transition_under_opt_out(self):
        self.success()
        from agentic.jira_lifecycle import planned_write
        with self.assertRaisesRegex(ValidationError, 'Epics are never transitioned'):
            planned_write(self.config, self.contract, 'JIRA_RECONCILED',
                          {'merge_confirmed': True, 'closeout_valid': True}, issue_type='EPIC')

    def test_recovery_never_counts_an_attempted_or_unknown_done(self):
        self.success()
        self.store = controller.ContinuousControllerStore(self.outer / 'recovery.sqlite', ['A'], worktree_roots=[self.project])
        self.store.prepare_jira_operation(self.started, NOW,
            identity=controller._jira_operation_identity(self.config, self.started))
        from agentic.jira_lifecycle import transition_record
        record = transition_record({'issue_id': ISSUE}, 'JIRA_RECONCILED', '10117', '10120', '31',
            jira_provider=self.provider, merge_result_id='00000000-0000-4000-8000-000000000045',
            producer_id='controller', run_id='00000000-0000-4000-8000-000000000046', now=NOW,
            evidence=['urn:awf:fixture:legacy-done-attempt'])
        self.store.prepare_jira_operation(record, NOW, identity=controller._jira_operation_identity(self.config, record))
        self.store.begin_jira_operation(record['operation_id'], NOW)
        self.store.mark_jira_unknown(record['operation_id'], NOW)
        reads = []
        def recover(record, operation):
            reads.append(record)
            return {'issue_id': ISSUE, 'status': '10120', 'actor': 'controller',
                    'observed_at': NOW, 'jira_provider': self.provider}
        self.adapters['read_transition'] = recover
        self.refusal()
        self.assertEqual([], reads, 'An unknown Done must not be recovered into owner closure')

    def test_recovery_abandoned_intent_never_counts_as_owner_closure(self):
        self.success()
        row = self.store.jira_operations(ISSUE)[0]
        controller.resolve_jira_operation(self.store, self.config, issue=ISSUE,
            operation_id=row['operation_id'], expected_intent_sha256=row['intent_sha256'],
            expected_project_id=self.config['project']['id'], expected_repository_id=101,
            operator='fixture-operator', now=NOW, disposition='ABANDON_NO_REPLAY',
            reason='Fixture bounded abandonment', provenance='urn:awf:fixture:operator-resolution')
        self.refusal()


class JiraBatchClosurePolicyTests(JiraControllerPolicyTests):
    """Every closure predicate is also exercised with the durable T11 hold."""
    def setUp(self):
        super().setUp()
        self.assertEqual([ISSUE], self.held_batch()['held'])


class RetainedClosureCases:
    def retained(self, value):
        retained = {key: value[key] for key in ('record', 'owner_closure')}
        with self.policy_reads():
            if self.path == 'adapter':
                from agentic.jira_owner_policy import retained_scope
                with retained_scope(self.store, self.config, retained):
                    return self.adapters['observe_jira_reconciliation'](self.request(value), retained_owner_closure=retained)
            return controller._post_merge_jira_receipt(self.store, self.config, ISSUE, NOW,
                {'closeout_record': {'binding': {'issue_id': ISSUE},
                    'jira_transition_record_id': value['record']['record_id']}},
                **{key: self.adapters[key] for key in ('observe_provider_identity', 'observe_issue_identity',
                    'observe_issue_policy', 'read_current_status')})

    def edit_closure(self, value, change):
        key = 'jira-owner-closure:' + value['record']['record_id']
        with self.store.transaction() as db:
            old = json.loads(db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()['value'])
            change(old)
            db.execute('UPDATE controller_meta SET value=? WHERE key=?', (canonical(old).decode(), key))

    def test_policy_drift_rejects_reuse_but_fresh_observation_keeps_past_history(self):
        value = self.success(); self.retained(value)
        self.commit = 'b' * 40
        with self.assertRaises(ValidationError):
            self.retained(value)
        self.clock.value = '2026-10-09T08:00:04Z'
        fresh = self.close(now=self.clock.value)
        self.assertIn('owner_closure', fresh, fresh)
        self.assertNotEqual(value['record']['record_id'], fresh['record']['record_id'])
        self.assertEqual(self.commit, fresh['owner_closure'][FIELD]['accepted_owner_policy']['default_branch_commit'])
        with self.policy_reads():
            # The original record is retained as a fact, with all raw history
            # still validated before admitting the new observation.
            self.assertEqual({key: value[key] for key in ('record', 'owner_closure')},
                controller.retained_jira_owner_closure(self.store, self.config, self.request(value)))
        self.edit_closure(value, lambda old: old['owner_closure'][FIELD]['history'].update(total=99))
        with self.policy_reads(), self.assertRaises(ValidationError):
            controller.require_jira_binding_identity(self.store, self.config, {'issue_id': ISSUE})

    def test_a_retained_contract_flag_and_current_keyword_required(self):
        value = self.success(); self.retained(value)
        self.http.keyword = False
        with self.assertRaises(ValidationError):
            self.retained(value)
        self.http.keyword = True
        self.edit_closure(value, lambda old: old['owner_closure'][FIELD].update(owner_closure_required=False))
        with self.assertRaises(ValidationError):
            self.retained(value)

    def test_b_retained_history_latest_complete_and_actor_bound(self):
        value = self.success(); self.retained(value)
        for problem in ('gap', 'incomplete', 'corrupt', 'actor', 'provider-time', 'later'):
            with self.subTest(problem=problem), self.assertRaises(ValidationError):
                self.http.problem = problem
                self.retained(value)

    def test_c_retained_missing_or_corrupt_controller_history_refuses(self):
        value = self.success(); self.retained(value)
        with self.store.transaction() as db:
            db.execute('UPDATE controller_jira_operation SET record_json=?', ('{',))
        with self.assertRaises(ValidationError):
            self.retained(value)

    def test_d_retained_held_unsent_is_allowed_attempted_is_not(self):
        self.assertEqual([ISSUE], self.held_batch()['held'])
        value = self.success(); self.retained(value)
        with self.store.transaction() as db:
            db.execute('UPDATE controller_batch_jira_operation SET operation_json=?', ('{}',))
        with self.assertRaises(ValidationError):
            self.retained(value)

    def test_e_retained_inflight_writer_refuses(self):
        value = self.success(); self.retained(value)
        with controller._batch_jira_ownership(self.store, issue_id=ISSUE) as owned:
            self.assertTrue(owned)
            with self.assertRaises(ValidationError):
                self.retained(value)


class JiraRetainedAdapterPolicyTests(RetainedClosureCases, JiraFixture):
    path = 'adapter'


class JiraRetainedControllerPolicyTests(RetainedClosureCases, JiraFixture):
    path = 'controller'

    def test_controller_uses_its_current_keyword_observation(self):
        value = self.success()
        self.retained(value)
        self.http.keyword = False
        reads = []
        def stale_status(binding):
            reads.append(binding)
            return {'issue_id': ISSUE, 'status_id': '10120', 'observed_at': NOW,
                    'jira_provider': self.provider, 'owner_closure': deepcopy(value['owner_closure'])}
        with self.policy_reads(), self.assertRaises(ValidationError):
            controller._post_merge_jira_receipt(self.store, self.config, ISSUE, NOW,
                {'closeout_record': {'binding': {'issue_id': ISSUE},
                    'jira_transition_record_id': value['record']['record_id']}},
                read_current_status=stale_status,
                **{key: self.adapters[key] for key in ('observe_provider_identity', 'observe_issue_identity',
                    'observe_issue_policy')})
        self.assertEqual([], reads)


class JiraConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = load(ROOT / '.agentic/examples/PROJECT_CONFIG.yaml')
        self.config['jira'][KEY] = self.config['jira']['controller_actor_id']
        self.contracts = Contracts(ROOT / '.agentic/schemas')

    def report(self):
        return inspect_config(self.config, definition(), self.contracts)

    def test_exact_account_warns_without_authority(self):
        report = self.report()
        self.assertEqual('ACCEPTED', report['status'], report)
        warning = next(item for item in report['warnings'] if item['code'] == 'SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT')
        self.assertIn(self.config['jira']['controller_actor_id'], warning['message'])
        self.assertIs(report['execution_authority'], False)

    def test_invalid_accounts_refuse(self):
        for value in ('', ' ', ' fixture-controller', 'fixture-controller ', True, False, 1, [], {}, 'fixture-other'):
            with self.subTest(value=value):
                self.config['jira'][KEY] = value
                self.assertEqual('REJECTED', self.report()['status'])

    def test_null_and_absent_keep_reports(self):
        for absent in (False, True):
            self.config['jira'][KEY] = None
            if absent:
                del self.config['jira'][KEY]
            report = self.report()
            self.assertEqual('ACCEPTED', report['status'])
            self.assertNotIn('SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT', [item['code'] for item in report['warnings']])



if __name__ == '__main__':
    unittest.main()
