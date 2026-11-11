"""Restorations consume the shared review window's presentation allowance."""
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic.canonical import loads
from agentic.continuous_controller import ContinuousControllerStore
from agentic.reporting import runtime
from agentic.reporting.observations import collect_review_corrections
from agentic.review_batching import ReviewBatchStore
import test_reporting_review_view as windows


class RestorationAllowanceTests(unittest.TestCase):
    setUp = windows.ReportingWindowFenceTests.setUp
    policy_inputs = windows.ReportingWindowFenceTests.policy_inputs
    accepted_calendar = windows.ReportingWindowFenceTests.accepted_calendar
    load_policy = windows.ReportingWindowFenceTests.load_policy
    observe = windows.ReportingWindowFenceTests.observe
    fence = windows.ReportingWindowFenceTests.fence

    def seed(self, *, absent_first=False, partial=False):
        self.root['reporting']['mode'] = 'both' if partial else 'slack'
        self.partial = partial
        self.accepted_calendar({})
        calendar = self.root['operating_calendar']
        if absent_first:
            reviews, payload = self.fence('2026-11-09T10:00:00Z', calendar)
            reviews.reconcile(dict(batch_id=payload['batch_id'], payload_sha256=payload['payload_sha256'],
                status='NOT_DELIVERED', observed_at=self.now, provider_receipt_id='confirmed-absent'), calendar)
        reviews, payload = self.fence('2026-11-09T10:01:00Z', calendar)
        owner = self
        class Sender:
            def send(self, intent, attempt):
                owner.sent.append((intent, attempt))
            def reconcile(self, intent, attempts):
                platform = intent['binding']['platform']
                return dict(status='UNKNOWN' if owner.partial and platform == 'teams' else 'VISIBLE',
                    evidence_kind='CHANNEL_READBACK',
                    delivery_id=intent['delivery_id'], destination=intent['binding']['destination'],
                    platform=platform, payload_sha256=intent['payload_sha256'], attempt_ids=attempts,
                    observation_id='read-' + intent['delivery_id'], observed_at=owner.now,
                    message_id='1355517523.000005' if platform == 'slack' else '1616990032035')
        self.providers = {p: Sender() for p in (('slack', 'teams') if partial else ('slack',))}
        self.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        port = self.port()
        receipt = port.deliver_review_batch(payload)
        if partial:
            self.assertIsNone(receipt)
        else:
            reviews.acknowledge(receipt)
        self.now = '2026-11-09T10:02:00Z'
        self.readiness = 'FINAL_REVIEW'
        self.notices()
        self.now = '2026-11-09T10:03:00Z'
        self.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        self.notices()
        status = runtime.reporting_status(self.store, self.root, self.binding)
        self.restoration = next(r for r in status['reports'] if r.get('review_restoration'))
        self.payload = payload
        self.assertEqual(self.restoration['review_batch']['calendar_sha256'], payload['calendar_sha256'])
        self.assertEqual(len(self.sent), 3 if partial else 2)  # Original destinations and withdrawal correction.

    def observation(self, candidate, now):
        return dict(self.observe(candidate, now), readiness=self.readiness)

    def port(self):
        return runtime.ReviewReportingPort(self.store, self.root, self.binding, self.providers,
            lambda: self.now, load_operating=self.load_policy, observe_candidate=self.observation)

    def notices(self):
        notices = collect_review_corrections(self.store, self.binding, self.now, self.observation)
        self.assertEqual(len(notices), 1)
        runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding, now=self.now,
            providers=self.providers, context=dict(review_corrections=notices))
        corrections = {n['id'] for n in notices if not n.get('restored')}
        if corrections:
            runtime.drain(self.store, self.root, self.binding, self.providers, report_ids=corrections,
                          before_send=lambda: self.port()._readmit())

    def count(self):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM review_presentation_intent_v1').fetchone()
            from agentic.review_batching import _window
            return ReviewBatchStore._window_count(row, _window(self.now, self.root['operating_calendar'])['opening'],
                ReviewBatchStore._attempt_windows(db, self.root['operating_calendar']))

    def test_absent_then_landed_withdrawn_restored_defers_exhausted_window(self):
        self.seed(absent_first=True)
        self.assertEqual(self.count(), 2)
        before = list(self.sent)
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(self.sent, before, 'Exhausted window must never invoke the restoration sender')
        self.store = ContinuousControllerStore(self.path, ['A'])
        with self.store.connection() as db:
            deferred = loads(db.execute('SELECT value FROM controller_meta WHERE key=?',
                ('report-review-deferred:' + self.restoration['report_id'],)).fetchone()[0])
        self.assertEqual(deferred['reason'], 'REPORT_REVIEW_WINDOW_EXHAUSTED')
        self.assertEqual(deferred['retry_at'], '2026-11-10T07:00:00Z')
        status = runtime.reporting_status(self.store, self.root, self.binding)
        pending = [d for d in status['deliveries'] if d['report_id'] == self.restoration['report_id']]
        self.assertEqual([d['attempts'] for d in pending], [[]])
        attention = runtime.delivery_attention(self.store, self.root, self.binding)
        self.assertTrue(any(n.get('deferred') == deferred for n in attention))
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(self.sent, before)
        self.now = deferred['retry_at']
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), len(before) + 1)
        self.assertEqual(self.count(), 1)

    def test_shared_guard_enforces_charge_with_calendar_hash_unchanged(self):
        from agentic.operating_calendar import next_review_presentation
        from agentic.review_batching import _window
        self.seed(absent_first=True)
        calendar = self.root['operating_calendar']
        self.assertEqual(next_review_presentation(self.now, calendar)['calendar_sha256'],
                         self.payload['calendar_sha256'])
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_WINDOW_EXHAUSTED$'):
            ReviewBatchStore(self.store).check_delivery_window(self.payload['batch_id'], self.now, calendar,
                reporting=True, restoration=True, restoration_report_id=self.restoration['report_id'],
                restoration_window=_window(self.now, calendar))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.count(), 2)

    def test_charge_rolls_back_with_failed_reservation_then_commits_before_sender(self):
        from agentic.reporting.delivery import DeliveryOutbox
        self.seed()
        original_save = DeliveryOutbox._save
        def fail_reservation(outbox, db, delivery_id, state):
            if state['status'] == 'IN_FLIGHT':
                self.assertEqual(db.execute('SELECT count(*) FROM review_restoration_charge_v1').fetchone()[0], 1)
                raise OSError('Synthetic reservation failure')
            return original_save(outbox, db, delivery_id, state)
        with patch.object(DeliveryOutbox, '_save', fail_reservation):
            self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.count(), 1)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM review_restoration_charge_v1').fetchone()[0], 0)
        original_send = self.providers['slack'].send
        def send(intent, attempt):
            self.assertEqual(self.count(), 2)
            with self.store.connection() as db:
                _, state = DeliveryOutbox._load(db, intent['delivery_id'])
            self.assertEqual(state['status'], 'IN_FLIGHT')
            self.assertEqual(state['attempts'], [attempt])
            return original_send(intent, attempt)
        self.providers['slack'].send = send
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.count(), 2)
        self.store = ContinuousControllerStore(self.path, ['A'])
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.count(), 2)

    def test_restart_preserves_ambiguous_charge_and_next_generation_defers(self):
        self.seed()
        provider = self.providers['slack']
        reconcile = provider.reconcile
        provider.reconcile = lambda intent, attempts: dict(reconcile(intent, attempts), status='UNKNOWN')
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(self.count(), 2)

        self.assertEqual(len(self.sent), 3)
        self.store = ContinuousControllerStore(self.path, ['A'])
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.count(), 2)
        provider.reconcile = reconcile
        self.port().resume(self.root['operating_calendar'], self.observation)
        # A second withdrawal/restoration is a distinct occurrence in this window.
        self.readiness = 'FINAL_REVIEW'
        self.now = '2026-11-09T10:04:00Z'
        self.notices()
        self.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        self.now = '2026-11-09T10:05:00Z'
        self.notices()
        before = list(self.sent)
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(self.sent, before)
        self.assertEqual(self.count(), 2)

    def test_restoration_charge_preserves_original_partial_batch_attempt_identity(self):
        self.seed(partial=True)
        # The first resume turn may select the original UNKNOWN batch; the
        # restoration and that batch retain independent round-robin identities.
        for _ in range(2):
            self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(self.count(), 2)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT state FROM review_presentation_outbox_v1').fetchone()[0], 'REPORTING')
        # An unresolved original destination must keep its original fenced
        # attempt identity even after another destination's restoration sends.
        ReviewBatchStore(self.store).check_delivery_window(self.payload['batch_id'], self.now,
            self.root['operating_calendar'], reporting=True)


if __name__ == '__main__':
    unittest.main()
