"""R315r2-F01: retained Done intent survives accepted status-map revisions."""
from contextlib import contextmanager
from copy import deepcopy
import json
import unittest

import source_only  # noqa: F401
import test_awf307_jira_production_counts as counts
from test_awf307_jira_owner_policy import ISSUE, NOW, controller
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.lifecycle import definition
from agentic.policy import policy_hash
from agentic.jira_lifecycle import transition_record


OLD_TIME = '2026-10-09T07:59:55Z'


class HistoricalDoneTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        case = counts.ProductionCountsTests()
        try:
            case.setUp()
            yield case
        finally:
            case.doCleanups()

    def retain(self, case, *, batch=False, done='10121', outcome='SUCCEEDED', held=False,
               event='JIRA_RECONCILED', alias=True):
        current = deepcopy(case.config)
        current_commit = case.commit
        historical = deepcopy(current)
        if not held:
            historical['jira']['shared_owner_controller_actor_id'] = None
        historical['jira']['status_map']['done'] = done
        if event == 'WORKER_STARTED' and alias:
            historical['jira']['status_map']['in_progress'] = done
        target = historical['jira']['status_map']['in_progress'] if event == 'WORKER_STARTED' else done
        binding = deepcopy(case.binding)
        binding['policy_hash'] = policy_hash(historical, definition())
        policy = case.adapters['observe_issue_policy'](ISSUE)
        policy['snapshot']['summary'] = 'Release acceptance' if held else 'Routine work'
        policy['observed_at'] = OLD_TIME
        case.accepted = deepcopy(historical)
        case.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(historical)
        case.commit = 'e' * 40
        case.clock.value = OLD_TIME
        writes = []

        def write(record):
            writes.append(deepcopy(record))
            return {'operation_id': record['operation_id'], 'issue_id': ISSUE,
                'status': 'ATTEMPTED', 'observed_at': OLD_TIME, 'jira_provider': case.provider}

        def read(record, operation):
            return {'issue_id': ISSUE, 'status': target if outcome == 'SUCCEEDED' else None,
                'actor': 'controller', 'observed_at': OLD_TIME, 'jira_provider': case.provider}

        arguments = dict(read_current_status=lambda binding: {'issue_id': ISSUE,
            'status_id': '10117', 'observed_at': OLD_TIME, 'jira_provider': case.provider},
            write_transition=write, read_transition=read,
            observe_issue_identity=case.adapters['observe_issue_identity'])
        with case.policy_reads():
            if batch:
                value = {'batch_id': 'historical-batch', 'pr_number': 44, 'merge_commit_sha': 'd' * 40,
                    'constituents': [{'binding': {'issue_id': ISSUE}, 'pr_number': 2,
                        'reviewed_head_sha': 'a' * 40, 'paths': ['src/fixture.py'],
                        'closeout_kind': 'merged-via-batch', 'batch_id': 'historical-batch',
                        'merge_commit_sha': 'd' * 40}]}
                result = controller.production_batch_jira_reconciliation(case.store,
                    config=historical, role='controller', batch=value,
                    observed_merge={'batch_id': value['batch_id'], 'target_branch': 'main',
                        'merge_commit_sha': 'd' * 40, 'observed_at': OLD_TIME}, now=OLD_TIME,
                    policy_context={ISSUE: {'issue_type': 'LEAF', 'owner_closure_required': held,
                        'snapshot': policy['snapshot']}}, **arguments)
                if held:
                    self.assertEqual([ISSUE], result['held'])
                elif outcome == 'SUCCEEDED':
                    self.assertEqual([ISSUE], result['reconciled'])
            else:
                result = controller.production_jira_lifecycle(case.store, config=historical,
                    contract={'owner_closure_required': False}, event=event,
                    facts={'merge_confirmed': True, 'closeout_valid': True,
                           'run_registered': True, 'worktree_verified': True}, binding=binding,
                    issue_type='LEAF', state='MERGED', producer_id='controller',
                    run_id='00000000-0000-4000-8000-000000000059', now=OLD_TIME,
                    evidence=['urn:awf:fixture:previous-merge'], transition_id='31',
                    merge_result_id='00000000-0000-4000-8000-000000000043',
                    observe_provider_identity=case.adapters['observe_provider_identity'],
                    observe_issue_policy=lambda issue: deepcopy(policy), **arguments)
                self.assertEqual(outcome, result['record']['status'], result)
        self.assertEqual(0 if held else 1, len(writes))
        case.accepted = current
        case.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(current)
        case.commit = current_commit
        case.clock.value = NOW
        original = case.http.respond

        def respond(method, url, *, body):
            code, text = original(method, url, body=body)
            if '/issue/' in url and '?fields=status' in url and not held:
                value = json.loads(text)
                if 'changelog' in value:
                    recent = value['changelog']['histories'][0]
                    recent['id'] = '3'
                    value['changelog']['histories'] = [
                        {'id': '1', 'created': OLD_TIME, 'author': {'accountId': 'controller'},
                         'items': [{'field': 'status', 'from': '10117', 'to': target}]},
                        {'id': '2', 'created': '2026-10-09T08:00:00Z',
                         'author': {'accountId': 'controller'},
                         'items': [{'field': 'status', 'from': target, 'to': '10117'}]}, recent]
                    value['changelog']['total'] = 3
                    text = json.dumps(value)
            return code, text
        case.http.respond = respond

    def assert_held(self, case):
        with case.policy_reads():
            result = controller.production_jira_lifecycle(case.store, config=case.config,
                contract=case.contract, event='JIRA_RECONCILED',
                facts={'merge_confirmed': True, 'closeout_valid': True}, binding=case.binding,
                issue_type='LEAF', state='MERGED', producer_id='controller',
                run_id='00000000-0000-4000-8000-000000000052', now=NOW,
                evidence=['urn:awf:fixture:merge-closeout'], transition_id='31',
                merge_result_id='00000000-0000-4000-8000-000000000043',
                **{key: case.adapters[key] for key in ('read_current_status', 'write_transition',
                    'read_transition', 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
        self.assertNotIn('owner_closure', result, result)
        self.assertTrue(result.get('status') == 'HELD' or result.get('writes_stopped'), result)

    def test_ordinary_successful_old_done_is_held(self):
        with self.fixture() as case:
            self.retain(case)
            self.assert_held(case)

    def test_batch_successful_old_done_is_held(self):
        with self.fixture() as case:
            self.retain(case, batch=True)
            self.assert_held(case)

    def test_non_done_event_targeting_historical_done_is_held(self):
        with self.fixture() as case:
            self.retain(case, event='WORKER_STARTED')
            self.assert_held(case)

    def test_proven_non_done_write_survives_mapping_change(self):
        with self.fixture() as case:
            self.retain(case, event='WORKER_STARTED', alias=False)
            case.prepare()
            case.assert_counted()

    def test_completed_non_done_without_its_target_policy_is_held(self):
        with self.fixture() as case:
            self.retain(case, event='WORKER_STARTED', alias=False)
            with case.store.transaction() as db:
                db.execute("DELETE FROM controller_meta WHERE key LIKE 'jira-target-policy:%'")
            self.assert_held(case)

    def test_corrupt_retained_target_policy_is_held(self):
        with self.fixture() as case:
            self.retain(case, event='WORKER_STARTED', alias=False)
            with case.store.transaction() as db:
                db.execute("UPDATE controller_meta SET value='{}' WHERE key LIKE 'jira-target-policy:%'")
            self.assert_held(case)

    def test_changed_target_policy_content_is_held(self):
        with self.fixture() as case:
            self.retain(case, event='WORKER_STARTED')
            with case.store.transaction() as db:
                row = db.execute("SELECT key,value FROM controller_meta WHERE key LIKE 'jira-target-policy:%'").fetchone()
                if row is not None:
                    value = json.loads(row['value'])
                    value['done_status_id'] = '10122'
                    db.execute('UPDATE controller_meta SET value=? WHERE key=?',
                        (canonical(value).decode(), row['key']))
            self.assert_held(case)

    def assert_pending_retry(self, new_done, *, refused=False):
        with self.fixture() as case:
            case.store = controller.ContinuousControllerStore(case.outer / 'pending.sqlite', ['A'],
                worktree_roots=[case.project])
            historical = deepcopy(case.config)
            historical['jira']['shared_owner_controller_actor_id'] = None
            historical['jira']['status_map']['done'] = '10121'
            policy = case.adapters['observe_issue_policy'](ISSUE)
            policy['snapshot']['summary'] = 'Routine work'
            policy['observed_at'] = OLD_TIME
            case.accepted = deepcopy(historical)
            case.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(historical)
            case.commit = 'e' * 40
            case.clock.value = OLD_TIME
            writes, resuming = [], False

            def write(record):
                if not resuming:
                    raise controller.JiraTransitionNotAttempted('Fixture timeout before POST')
                writes.append(deepcopy(record))
                return {'operation_id': record['operation_id'], 'issue_id': ISSUE,
                    'status': 'ATTEMPTED', 'observed_at': OLD_TIME, 'jira_provider': case.provider}

            def invoke(config):
                with case.policy_reads():
                    return controller.production_jira_lifecycle(case.store, config=config,
                        contract={'owner_closure_required': False}, event='WORKER_STARTED',
                        facts={'run_registered': True, 'worktree_verified': True},
                        binding={'issue_id': ISSUE}, issue_type='LEAF', state='DISPATCHED',
                        producer_id='controller', run_id='00000000-0000-4000-8000-000000000059',
                        now=OLD_TIME, evidence=['urn:awf:fixture:pending-target-policy'], transition_id='21',
                        read_current_status=lambda binding: {'issue_id': ISSUE, 'status_id': '10117',
                            'observed_at': OLD_TIME, 'jira_provider': case.provider},
                        write_transition=write, read_transition=lambda record, operation: {
                            'issue_id': ISSUE, 'status': '10119', 'actor': 'controller',
                            'observed_at': OLD_TIME, 'jira_provider': case.provider},
                        **{key: case.adapters[key] for key in ('observe_provider_identity',
                            'observe_issue_identity')}, observe_issue_policy=lambda issue: deepcopy(policy))

            first = invoke(historical)
            self.assertEqual('NOT_ATTEMPTED', first['status'], first)
            self.assertEqual([], writes)
            key = 'jira-target-policy:' + first['operation_id']
            with case.store.connection() as db:
                before = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
                before = before['value'] if before is not None else None
            revised = deepcopy(historical)
            revised['jira']['status_map']['done'] = new_done
            case.accepted = deepcopy(revised)
            case.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(revised)
            case.commit = 'f' * 40
            resuming = True
            if refused:
                with self.assertRaisesRegex(ValidationError, 'target policy changed'):
                    invoke(revised)
                self.assertEqual([], writes)
                self.assertEqual('PENDING', case.store.jira_operations(ISSUE)[-1]['status'])
            else:
                result = invoke(revised)
                self.assertEqual('SUCCEEDED', result['record']['status'], result)
                self.assertEqual(1, len(writes))
            with case.store.connection() as db:
                after = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
                after = after['value'] if after is not None else None
            self.assertEqual(before, after, 'Retry must preserve the original target-policy bytes')

    def test_pending_retry_cannot_become_done_under_a_changed_mapping(self):
        self.assert_pending_retry('10119', refused=True)

    def test_pending_retry_with_unchanged_done_mapping_can_resume(self):
        self.assert_pending_retry('10121')

    def test_pending_retry_preserves_safe_done_mapping_changes(self):
        self.assert_pending_retry('10122')

    def test_unknown_old_done_is_held_on_both_paths(self):
        for batch in (False, True):
            with self.subTest(batch=batch), self.fixture() as case:
                self.retain(case, batch=batch, outcome='UNKNOWN')
                self.assert_held(case)

    def test_current_done_control_is_held_on_both_paths(self):
        for batch in (False, True):
            with self.subTest(batch=batch), self.fixture() as case:
                self.retain(case, batch=batch, done='10120')
                self.assert_held(case)

    def test_unestablished_recorded_target_keeps_closure_held(self):
        for event, target in (('JIRA_RECONCILED', None), ('WORKER_STARTED', ''),
                              ('UNRECOGNIZED', '10121')):
            with self.subTest(event=event, target=target), self.fixture() as case:
                record = transition_record(case.binding, event, '10117', target, '31',
                    jira_provider=case.provider,
                    merge_result_id='00000000-0000-4000-8000-000000000043',
                    producer_id='controller', run_id='00000000-0000-4000-8000-000000000059',
                    now=OLD_TIME, evidence=['urn:awf:fixture:unestablished-target'])
                case.store.prepare_jira_operation(record, OLD_TIME,
                    identity=controller._jira_operation_identity(case.config, record))
                self.assert_held(case)

    def test_pending_inflight_and_abandoned_old_done_intents_are_held(self):
        for outcome in ('PENDING', 'IN_FLIGHT', 'ABANDONED'):
            with self.subTest(outcome=outcome), self.fixture() as case:
                record = transition_record(case.binding, 'JIRA_RECONCILED', '10117', '10121', '31',
                    jira_provider=case.provider,
                    merge_result_id='00000000-0000-4000-8000-000000000043',
                    producer_id='controller', run_id='00000000-0000-4000-8000-000000000059',
                    now=OLD_TIME, evidence=['urn:awf:fixture:old-done-intent'])
                row = case.store.prepare_jira_operation(record, OLD_TIME,
                    identity=controller._jira_operation_identity(case.config, record))
                if outcome == 'IN_FLIGHT':
                    case.store.begin_jira_operation(record['operation_id'], OLD_TIME)
                elif outcome == 'ABANDONED':
                    controller.resolve_jira_operation(case.store, case.config, issue=ISSUE,
                        operation_id=record['operation_id'], expected_intent_sha256=row['intent_sha256'],
                        expected_project_id=case.config['project']['id'], expected_repository_id=101,
                        operator='fixture-owner', now=NOW, disposition='ABANDON_NO_REPLAY',
                        reason='Historical operation abandoned', provenance='urn:awf:fixture:operator')
                self.assert_held(case)

    def test_proven_unsent_held_old_done_still_counts(self):
        with self.fixture() as case:
            self.retain(case, batch=True, held=True)
            case.prepare()
            case.assert_counted()

    def test_historical_done_never_reaches_production_counts(self):
        for batch in (False, True):
            with self.subTest(batch=batch), self.fixture() as case:
                self.retain(case, batch=batch)
                try:
                    case.prepare()
                except AssertionError as exc:
                    # prepare() asserts a positive closure before constructing
                    # the real Git gate; refusing that closure is the first gate.
                    self.assertIn("'owner_closure' not found", str(exc))
                    self.assertIn("'status': 'HELD'", str(exc))
                else:
                    gate, progress = case.count(), case.progress()
                    self.assertFalse(gate['count_allowed'], (gate, progress))
                    self.assertNotEqual('COUNTED', progress['jira_state'], progress)

    def test_retained_closure_count_gate_refuses_old_done_attempts(self):
        for batch, outcome in ((False, 'SUCCEEDED'), (True, 'SUCCEEDED'),
                               (False, 'UNKNOWN'), (True, 'UNKNOWN')):
            with self.subTest(batch=batch, outcome=outcome), self.fixture() as case:
                case.prepare()
                self.retain(case, batch=batch, outcome=outcome)
                with self.assertRaisesRegex(ValidationError, 'Done intent|attempted, unknown or in-flight'):
                    case.count()
                progress = case.progress()
                self.assertEqual('UNOBSERVED', progress['jira_state'], progress)
                self.assertEqual('UNOBSERVED', progress['closed'])
                self.assertEqual('UNOBSERVED', progress['remaining_open'])


if __name__ == '__main__':
    unittest.main()
