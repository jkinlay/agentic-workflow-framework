"""Retained review ancestry owns occurrence identity and terminal detail."""
from copy import deepcopy
import unittest

import source_only
from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.reporting import runtime
from agentic.review_batching import ReviewBatchStore
import test_reporting_restoration_allowance as fixtures


class RestorationChainTests(unittest.TestCase):
    setUp = fixtures.RestorationAllowanceTests.setUp
    policy_inputs = fixtures.RestorationAllowanceTests.policy_inputs
    accepted_calendar = fixtures.RestorationAllowanceTests.accepted_calendar
    load_policy = fixtures.RestorationAllowanceTests.load_policy
    observe = fixtures.RestorationAllowanceTests.observe
    fence = fixtures.RestorationAllowanceTests.fence
    seed = fixtures.RestorationAllowanceTests.seed
    observation = fixtures.RestorationAllowanceTests.observation
    port = fixtures.RestorationAllowanceTests.port
    notices = fixtures.RestorationAllowanceTests.notices
    count = fixtures.RestorationAllowanceTests.count

    def retain(self, notice):
        return runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding,
            now=self.now, providers=self.providers, context=dict(review_corrections=[notice]))

    def next_notice(self, prior):
        notice = deepcopy(self.restoration['review_restoration'])
        notice.update(id='second-restoration', prior_report=prior)
        return notice

    def test_original_pointer_cannot_alias_first_restoration_charge_public_cycle(self):
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual((len(self.sent), self.count()), (3, 2))
        notice = self.next_notice('review-' + self.payload['batch_id'])
        refused = False
        try:
            self.retain(notice)
        except ValidationError as exc:
            self.assertEqual(str(exc), 'REPORT_REVIEW_CHAIN_TIP_CHANGED')
            refused = True
        self.port().resume(self.root['operating_calendar'], self.observation)
        with self.store.connection() as db:
            charges = db.execute('SELECT count(*) FROM review_restoration_charge_v1').fetchone()[0]
            retained = db.execute('SELECT 1 FROM controller_reports WHERE report_id=?', (notice['id'],)).fetchone()
        self.assertEqual((len(self.sent), self.count(), charges), (3, 2, 1))
        self.assertTrue(refused)
        self.assertIsNone(retained)

    def test_legitimate_second_restoration_uses_tip_and_generation_two(self):
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        notice = self.next_notice(self.restoration['report_id'])
        self.retain(notice)
        with self.store.connection() as db:
            occurrence = ReviewBatchStore.restoration_identity(db, notice['id'])
        self.assertEqual(occurrence, fingerprint('review-restoration-occurrence-v1',
            [self.payload['batch_id'], self.binding, 2]))
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual((len(self.sent), self.count()), (3, 2))
        self.now = '2026-11-10T07:00:00Z'
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual((len(self.sent), self.count()), (4, 1))

    def test_correction_is_retained_tip_and_generation_walks_through_it(self):
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.readiness = 'FINAL_REVIEW'
        self.notices()
        status = runtime.reporting_status(self.store, self.root, self.binding)
        correction = next(r for r in status['reports'] if r.get('review_correction', {}).get(
            'prior_report') == self.restoration['report_id'])
        notice = self.next_notice(correction['report_id'])
        self.retain(notice)
        with self.store.connection() as db:
            occurrence = ReviewBatchStore.restoration_identity(db, notice['id'])
        self.assertEqual(occurrence, fingerprint('review-restoration-occurrence-v1',
            [self.payload['batch_id'], self.binding, 2]))

    def test_stale_and_forked_prior_refused_at_retention(self):
        self.seed()
        for prior in ('missing-report', 'review-' + self.payload['batch_id']):
            with self.subTest(prior=prior):
                with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_TIP_CHANGED$'):
                    self.retain(self.next_notice(prior))
        with self.store.connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM controller_reports WHERE report_id='second-restoration'").fetchone())

    def test_scope_change_can_retain_correction_but_cannot_restore_old_readiness(self):
        from agentic.reporting.observations import collect_review_corrections
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        original_binding = deepcopy(self.binding)
        self.binding['scope_revision'] = 'new-scope'
        for destination in self.root['reporting']['bindings']:
            destination['scope_revision'] = self.binding['scope_revision']
        notices = collect_review_corrections(self.store, self.binding, self.now, self.observation)
        self.assertEqual(len(notices), 1)
        self.retain(notices[0])
        status = runtime.reporting_status(self.store, self.root, self.binding)
        correction = next(r for r in status['reports'] if r['report_id'] == notices[0]['id'])
        self.assertEqual(correction['binding'], self.binding)
        self.assertEqual(correction['review_correction']['prior_report'], self.restoration['report_id'])
        notice = self.next_notice(correction['report_id'])
        notice['binding']['scope_revision'] = self.binding['scope_revision']
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_TIP_CHANGED$'):
            self.retain(notice)
        self.binding = original_binding
        for destination in self.root['reporting']['bindings']:
            destination['scope_revision'] = self.binding['scope_revision']
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_TIP_CHANGED$'):
            self.retain(self.next_notice(self.restoration['report_id']))
        # Reverting to the original accepted scope still needs the actual tip.
        # A correction's diagnostic scope is not restoration policy authority.
        self.retain(self.next_notice(correction['report_id']))

    def terminal_detail(self, failed):
        self.seed()
        if failed:
            provider = self.providers['slack']
            reconcile = provider.reconcile
            provider.reconcile = lambda intent, attempts: dict(reconcile(intent, attempts), status='ABSENT', terminal=True)
        for _ in range(2):
            self.port().resume(self.root['operating_calendar'], self.observation)
        status = runtime.reporting_status(self.store, self.root, self.binding)
        delivery = next(d for d in status['deliveries'] if d['report_id'] == self.restoration['report_id'])
        expected = 'FAILED' if failed else 'DELIVERED'
        self.assertEqual(delivery['status'], expected)
        with self.store.connection() as db:
            detail = db.execute('SELECT * FROM review_presentation_detail_v1').fetchone()
        self.assertEqual(detail['reason'], 'Restoration ' + expected)
        self.assertIsNone(detail['retry_at'])

    def test_delivered_restoration_clears_unresolved_detail_and_retry(self):
        self.terminal_detail(False)

    def test_failed_restoration_clears_unresolved_detail_and_retry(self):
        self.terminal_detail(True)


if __name__ == '__main__':
    unittest.main()
