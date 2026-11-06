"""Periodic reports retain the shared batching view without claiming reviews."""
import unittest

import source_only
from agentic.canonical import canonical
from agentic.review_batching import ReviewBatchStore
from agentic.continuous_controller import production_review_batching_cycle
from agentic.reporting.runtime import reporting_cycle, render_body
import test_reporting_wiring as reporting
from test_awf244_controller_wiring import candidate


class ReportingReviewViewTests(unittest.TestCase):
    setUp = reporting.ReportingRuntimeTests.setUp

    def test_hourly_uses_read_only_shared_held_withdrawn_exhausted_view(self):
        now = '2026-11-07T18:00:00Z'
        reviews = ReviewBatchStore(self.store)
        identities = [reviews.enqueue(dict(candidate(), pull_request=pr), now, {}) for pr in (1, 2, 3)]
        with self.store.transaction() as db:
            db.execute("UPDATE review_presentation_intent_v1 SET state='WITHDRAWN' WHERE intent_id=?", (identities[1],))
            db.execute("UPDATE review_presentation_intent_v1 SET state='EXHAUSTED', attempts=2 WHERE intent_id=?", (identities[2],))
            reviews._detail(db, identities[2], 'Two confirmed negative deliveries', now,
                            retry_at='2026-11-09T07:00:00Z', attempts=2)
        expected = reviews.view(now, {})
        self.assertEqual({'PENDING', 'WITHDRAWN', 'EXHAUSTED'}, {i['state'] for i in expected['intents']})
        self.assertTrue(next(i['held'] for i in expected['intents'] if i['state'] == 'PENDING'))
        result = reporting_cycle(self.store, operating=self.root, binding=self.binding, now=now,
                                 providers={}, context={})
        hourly = next(r for r in result['reports'] if r['kind'] == 'hourly')
        self.assertEqual(expected, hourly['body']['data']['review_batching'])
        self.assertEqual(canonical(expected), canonical(reviews.view(now, {})))
        rendered = render_body(hourly['body'], 'slack')
        for phrase in ('WITHDRAWN', 'EXHAUSTED', 'held', 'Two confirmed negative deliveries'):
            self.assertIn(phrase, rendered)
        self.assertEqual([], self.sent)

    def test_reporting_bound_survives_unsent_recovery_loop(self):
        from datetime import timedelta
        from agentic.canonical import timestamp
        now = '2026-11-09T10:00:00Z'
        reviews = ReviewBatchStore(self.store)
        for pr in (1, 2, 3):
            reviews.enqueue(candidate(pr), now, {})
        calls = []
        def observe(value, observed):
            calls.append(value)
            return dict(candidate=value, observed_at=observed,
                expires_at=(timestamp(observed) + timedelta(minutes=1)).isoformat(),
                open=True, merged=False, final_gate_valid=True,
                readiness='READY_FOR_OWNER_AUTHORIZATION' if len(calls) % 2 else 'FINAL_REVIEW')
        result = production_review_batching_cycle(self.store, calendar={}, binding={}, clock=lambda: now,
            observe_review_candidate=observe, candidate_limit=1,
            deliver_review_batch=lambda payload: self.fail('Invalid recheck reached delivery'),
            observe_review_delivery=lambda payload: self.fail('No old batch exists'))
        self.assertEqual(2, len(calls))
        self.assertEqual(1, len(result['errors']))
        states = [i['state'] for i in reviews.view(now, {})['intents']]
        self.assertEqual(2, states.count('PENDING'))
        self.assertEqual(1, states.count('WITHDRAWN'))

    def test_reporting_exhaustion_cannot_create_a_fresh_batch_next_window(self):
        from datetime import timedelta
        from agentic.canonical import timestamp
        from agentic.reporting.runtime import ReviewReportingPort, reporting_status
        now = '2026-11-09T10:00:00Z'
        reviews = ReviewBatchStore(self.store)
        reviews.enqueue(candidate(), now, {})
        def observe(value, observed):
            return dict(candidate=value, observed_at=observed,
                expires_at=(timestamp(observed) + timedelta(minutes=5)).isoformat(),
                open=True, merged=False, final_gate_valid=True, readiness='READY_FOR_OWNER_AUTHORIZATION')
        payload = reviews.claim(now, {}, lambda c: observe(c, now))
        reviews.recheck(payload['batch_id'], now, {}, lambda c: observe(c, now))
        reviews.begin_delivery(payload['batch_id'], now)
        for platform, provider in self.providers.items():
            original = provider.reconcile
            provider.reconcile = lambda intent, attempts, f=original, p=platform: dict(
                f(intent, attempts), status='VISIBLE' if p == 'slack' else 'ABSENT', terminal=True, observed_at=now)
        port = ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: now)
        port.deliver_review_batch(payload)
        port.resume({}, observe)
        port.observe_review_delivery(payload)
        states = reporting_status(self.store, self.root, self.binding)['deliveries']
        self.assertEqual({'DELIVERED', 'FAILED'}, {d['status'] for d in states})
        self.assertEqual([1, 2], sorted(len(d['attempts']) for d in states))
        self.assertEqual(3, len(self.sent))
        next_day = '2026-11-10T10:00:00Z'
        self.assertIsNone(reviews.claim(next_day, {}, lambda c: observe(c, next_day), max_members=1))
        view = reviews.view(next_day, {})
        self.assertEqual('EXHAUSTED', view['intents'][0]['state'])
        self.assertIsNone(view['intents'][0]['retry_at'])
        self.assertIn('Reporting delivery exhausted', view['intents'][0]['reason'])
        self.assertEqual(3, len(self.sent))
        result = production_review_batching_cycle(self.store, calendar={}, binding={}, clock=lambda: next_day,
            observe_review_candidate=observe, candidate_limit=1,
            deliver_review_batch=lambda p: self.fail('Exhausted reporting cannot be presented again'),
            observe_review_delivery=lambda p: self.fail('Terminal reporting needs no readback'))
        self.assertEqual(view['owner_alerts'], result['owner_alerts'])
        self.assertEqual([], result['errors'])


if __name__ == '__main__':
    unittest.main()
