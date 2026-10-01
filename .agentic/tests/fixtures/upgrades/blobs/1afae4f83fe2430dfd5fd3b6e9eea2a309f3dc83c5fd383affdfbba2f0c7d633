"""Project-wide progress must preserve ongoing work and unsent user decisions."""
from copy import deepcopy
from pathlib import Path
import unittest

from agentic import ValidationError
from agentic.canonical import load
from agentic.continuation import project_cycle, deliver_input_draft, render_cycle
from agentic.contracts import Contracts

ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-09-11T10:00:00Z'


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load(ROOT / '.agentic/examples/project-cycle.json')
        self.contracts = Contracts(ROOT / '.agentic/schemas')

    def cycle(self):
        return project_cycle(self.snapshot, self.contracts, NOW)

    def test_one_authorization_does_not_stop_other_streams_or_reviews(self):
        value = self.cycle()
        self.assertEqual({a['reference'] for a in value['continue_actions']},
                         {'A', 'C', self.snapshot['pull_requests'][0]['url']})
        self.assertEqual(value['status'], 'RUNNING')
        self.assertFalse(value['finish_allowed'])
        self.assertFalse(value['status_update_ends_work'])
        self.assertEqual(len(value['prs_requiring_review']), 1)
        self.assertFalse(value['input_drafts'][0]['submit'])
        self.assertIn('Required decision:', render_cycle(value))

    def test_stale_authorization_is_refreshed_without_stopping_ready_streams(self):
        self.snapshot['authorizations'][0]['expires_at'] = '2026-09-11T09:59:00Z'
        value = self.cycle()
        self.assertEqual(value['input_drafts'], [])
        self.assertTrue(value['pending_authorizations'][0]['refresh_required'])
        self.assertIn('A', {a['reference'] for a in value['continue_actions']})
        self.assertIn('authorization_reconciliation', {a['kind'] for a in value['continue_actions']})
        self.assertNotIn('Authorize synthetic build', render_cycle(value))

    def test_missing_or_invalid_merge_records_do_not_emit_an_approval(self):
        self.snapshot['authorizations'][0] = {'id': 'staging-fixture-42', 'kind': 'candidate_merge', 'gate': {}, 'request': {}, 'config': {}}
        value = self.cycle()
        self.assertEqual(value['input_drafts'], [])
        self.assertTrue(value['continue_actions'])

    def test_routine_work_never_acquires_a_new_approval_phrase(self):
        self.snapshot['authorizations'][0]['decision'] = {'kind': 'delegate', 'target': 'stream B', 'scope_authorized': True}
        value = self.cycle()
        self.assertEqual(value['input_drafts'], [])
        self.assertFalse(value['pending_authorizations'][0]['report']['next_step']['authorization']['required'])

    def test_generic_phrase_cannot_impersonate_the_bound_merge_protocol(self):
        self.snapshot['authorizations'][0]['decision']['authorization']['phrase'] = 'AUTHORIZE AWF1.2 fake'
        self.assertEqual(self.cycle()['input_drafts'], [])

    def test_waiting_and_owner_actions_are_not_completion(self):
        for item in self.snapshot['streams']:
            if item['label'] != 'B':
                item.update(state='WAITING', resume_trigger='observed CI completion')
        self.snapshot['pull_requests'][0]['state'] = 'CI_RUNNING'
        value = self.cycle()
        self.assertFalse(value['finish_allowed'])
        self.assertEqual(value['status'], 'WAITING')
        self.assertEqual(len(value['watch_actions']), 3)
        self.snapshot['scope_complete'] = True
        with self.assertRaises(ValidationError):
            self.cycle()

    def test_completion_requires_finished_scope_and_no_remaining_pr_or_decision(self):
        self.snapshot['authorizations'] = []
        for item in self.snapshot['streams'] + self.snapshot['pull_requests']:
            item.update(state='COMPLETE', authorization_id=None)
        self.assertFalse(self.cycle()['finish_allowed'])
        self.snapshot['scope_complete'] = True
        self.assertTrue(self.cycle()['finish_allowed'])
        self.assertEqual(self.cycle()['status'], 'COMPLETE')
        self.assertIn('No further in-scope execution', self.cycle()['continuity_requirement'])

    def test_cancellation_stops_new_actions_and_preserves_handoff(self):
        self.snapshot['cancelled'] = True
        value = self.cycle()
        self.assertEqual(value['status'], 'CANCELLED')
        self.assertEqual(value['continue_actions'], [])
        self.assertIn('surviving workers', value['next_step']['action'])
        self.assertEqual(value['input_drafts'], [])
        self.assertEqual(value['pending_authorizations'], [])
        self.assertNotIn('Required decision:', render_cycle(value))
        self.assertNotIn('Authorize synthetic build', render_cycle(value))
        self.assertIn('Do not start further', value['continuity_requirement'])

    def test_missing_wakeup_cannot_be_claimed_as_background_execution(self):
        self.snapshot['wakeup'] = {'kind': 'unavailable', 'reference': None, 'evidence': 'Host has no wakeup integration.'}
        value = self.cycle()
        self.assertFalse(value['background_execution_verified_by_this_tool'])
        self.assertIn('Keep the foreground', value['continuity_requirement'])
        self.snapshot['wakeup'] = {'kind': 'heartbeat', 'reference': 'reported-id', 'evidence': 'Reported by coordinator.'}
        self.assertTrue(self.cycle()['reported_heartbeat_registered'])
        self.assertFalse(self.cycle()['background_execution_verified_by_this_tool'])

    def test_running_writers_need_real_distinct_agent_ids(self):
        self.snapshot['streams'][0]['state'] = 'RUNNING'
        with self.assertRaises(ValidationError):
            self.cycle()
        self.snapshot['streams'][0]['agent_id'] = 'agent-a'
        self.snapshot['streams'][2].update(state='RUNNING', agent_id='agent-a')
        with self.assertRaises(ValidationError):
            self.cycle()

    def test_stale_observation_requires_reconciliation(self):
        self.snapshot['observed_at'] = '2026-09-11T09:00:00Z'
        with self.assertRaises(ValidationError):
            self.cycle()


class Composer:
    def __init__(self, text='', race=False, fail=False):
        self.text, self.revision, self.race, self.fail = text, 1, race, fail
        self.writes = 0

    def read_draft(self):
        return {'text': self.text, 'revision': self.revision}

    def compare_and_set_draft(self, revision, text):
        self.writes += 1
        if self.fail:
            raise OSError('Host became unavailable')
        if self.race:
            self.text = 'User started typing'
            self.revision += 1
        if revision != self.revision:
            return False
        self.text = text
        self.revision += 1
        return True


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.draft = {'text': 'Authorize prepared action 42 only.', 'expires_at': '2026-09-11T11:00:00Z',
                      'submit': False, 'execution_authority': False}

    def test_no_adapter_returns_copyable_text_without_prefill_claim(self):
        value = deliver_input_draft(self.draft, NOW)
        self.assertEqual(value['status'], 'COPYABLE_TEXT')
        self.assertFalse(value['prefilled'])
        self.assertFalse(value['submitted'])

    def test_real_adapter_prefills_empty_input_and_checks_readback_without_send(self):
        host = Composer()
        value = deliver_input_draft(self.draft, NOW, host)
        self.assertTrue(value['prefilled'])
        self.assertEqual(host.text, self.draft['text'])
        self.assertEqual(host.writes, 1)
        self.assertFalse(value['submitted'])

    def test_user_draft_is_never_overwritten(self):
        host = Composer('My existing message')
        value = deliver_input_draft(self.draft, NOW, host)
        self.assertFalse(value['prefilled'])
        self.assertEqual(host.text, 'My existing message')
        self.assertEqual(host.writes, 0)

    def test_concurrent_user_edit_and_host_failure_keep_fallback(self):
        for host in [Composer(race=True), Composer(fail=True)]:
            self.assertFalse(deliver_input_draft(self.draft, NOW, host)['prefilled'])
        self.assertEqual(deliver_input_draft(self.draft, NOW, Composer(self.draft['text']))['status'], 'PREFILLED')

    def test_expired_or_auto_submit_draft_is_rejected(self):
        for field, value in [('expires_at', NOW), ('submit', True), ('execution_authority', True)]:
            draft = deepcopy(self.draft)
            draft[field] = value
            with self.assertRaises(ValidationError):
                deliver_input_draft(draft, NOW, Composer())
