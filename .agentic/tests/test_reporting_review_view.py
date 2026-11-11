"""Periodic reports retain the shared batching view without claiming reviews."""
import unittest
from copy import deepcopy

import source_only
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.review_batching import ReviewBatchStore
from agentic.continuous_controller import production_review_batching_cycle
from agentic.reporting.runtime import reporting_cycle, render_body
import test_reporting_wiring as reporting
from test_awf244_controller_wiring import candidate


class ReportingReviewViewTests(unittest.TestCase):
    setUp = reporting.ReportingRuntimeTests.setUp

    def policy_inputs(self):
        from pathlib import Path
        from test_operating import governance
        from agentic import operating as op
        from settings_pr_fixtures import accepted_fixture_snapshot
        config = governance()
        config['project']['id'] = self.binding['project']
        config['github']['repository_id'] = self.binding['repository']
        value = op.default_operating(config['execution']['model_routing'])
        value['reporting'] = self.root['reporting']
        root = Path(self.temp.name) / 'policy'
        snapshot = accepted_fixture_snapshot(self, root, value, config)
        return dict(repository_root=root, config=config, operating_snapshot=snapshot)

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
            **self.policy_inputs(),
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
        port = ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: now,
                                   load_operating=lambda: self.root)
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
            **self.policy_inputs(),
            observe_review_candidate=observe, candidate_limit=1,
            deliver_review_batch=lambda p: self.fail('Exhausted reporting cannot be presented again'),
            observe_review_delivery=lambda p: self.fail('Terminal reporting needs no readback'))
        self.assertEqual(view['owner_alerts'], result['owner_alerts'])
        self.assertEqual([], result['errors'])


if __name__ == '__main__':
    unittest.main()


class ReportingWindowFenceTests(unittest.TestCase):
    """The final reporting reservation retains lane C's window and charge."""
    setUp = reporting.ReportingRuntimeTests.setUp
    policy_inputs = ReportingReviewViewTests.policy_inputs

    def accepted_calendar(self, calendar):
        from settings_pr_fixtures import publish_fixture_settings
        inputs = self.policy_inputs()
        self.policy_root, self.gov = inputs['repository_root'], inputs['config']
        self.root = inputs['operating_snapshot'].config
        self.root['operating_calendar'] = calendar
        publish_fixture_settings(self, self.policy_root, self.gov, self.root)
        self.root = self.load_policy()
        return self.root

    def load_policy(self):
        from agentic.operating import read_operating
        return read_operating(self.policy_root, self.gov).config

    def observe(self, candidate, now):
        from datetime import timedelta
        from agentic.canonical import timestamp
        return dict(candidate=candidate, observed_at=now,
            expires_at=(timestamp(now) + timedelta(minutes=5)).isoformat(),
            open=True, merged=False, final_gate_valid=True, readiness='READY_FOR_OWNER_AUTHORIZATION')

    def fence(self, now, calendar):
        self.now = now
        reviews = ReviewBatchStore(self.store)
        reviews.enqueue(candidate(), now, calendar)
        payload = reviews.claim(now, calendar, lambda c: self.observe(c, now))
        reviews.recheck(payload['batch_id'], now, calendar, lambda c: self.observe(c, now))
        reviews.begin_delivery(payload['batch_id'], now)
        return reviews, payload

    def deliver(self, payload, during_verification, *, observer=True, resume=False, restoration=False):
        from agentic.reporting import runtime
        from agentic.reporting.runtime import ReviewReportingPort
        owner = self
        self.verifications, self.reservations, self.posts = [], [], []
        class Sender:
            def send_reserved(self, intent, attempt, reserve):
                owner.verifications.append(attempt)
                during_verification()
                reserve()
                owner.reservations.append(attempt)
                owner.posts.append(attempt)
            def reconcile(self, intent, attempts):
                return dict(status='UNKNOWN', evidence_kind='CHANNEL_READBACK',
                    delivery_id=intent['delivery_id'], destination=intent['binding']['destination'],
                    platform=intent['binding']['platform'], payload_sha256=intent['payload_sha256'],
                    attempt_ids=attempts, observation_id='unobserved', observed_at=owner.now)
        port = ReviewReportingPort(self.store, self.root, self.binding,
            {'slack': Sender()}, lambda: self.now, load_operating=self.load_policy,
            observe_candidate=self.observe if observer else None)
        if resume:
            port._retain_review_batch(payload)
            if restoration:
                # Trusted synthetic readback closes the original occurrence.
                ReviewBatchStore(self.store).acknowledge(dict(batch_id=payload['batch_id'],
                    payload_sha256=payload['payload_sha256'], status='DELIVERED',
                    observed_at=self.now, provider_receipt_id='original-visible'))
                original = runtime.reporting_status(self.store, self.root, self.binding)['reports'][0]
                _, destinations = runtime._bindings(self.root, self.binding)
                prepared = runtime._prepare_report('restore-' + payload['batch_id'], 'final-human-review',
                    original['body'], self.binding, destinations, self.now,
                    extra=dict(review_restoration=dict(id='restoration', prior_report=original['report_id'],
                        binding=destinations[0]), review_batch=payload))
                with self.store.transaction() as db:
                    runtime._retain_prepared(db, prepared)
            port.resume(self.root['operating_calendar'], self.observe)
        else:
            port.deliver_review_batch(payload)

    def assert_unsent(self):
        from agentic.reporting.runtime import reporting_status
        self.assertTrue(self.verifications)
        self.assertEqual(self.reservations, [])
        self.assertEqual(self.posts, [])
        self.assertTrue(all(not d['attempts'] for d in
            reporting_status(self.store, self.root, self.binding)['deliveries']))

    def test_midnight_during_verification_refuses_before_reservation_and_post(self):
        self.root['reporting']['mode'] = 'slack'
        self.accepted_calendar(dict(review_batching=dict(enabled=False)))
        calendar = self.root['operating_calendar']
        for observer, resume, restoration in ((True, False, False), (False, False, False),
                                              (True, True, False), (True, True, True)):
            with self.subTest(observer=observer, resume=resume, restoration=restoration):
                # Each route has an independent fenced intent and reporting outbox.
                from agentic.continuous_controller import ContinuousControllerStore
                self.store = ContinuousControllerStore(self.path.with_name('route-%s-%s-%s.sqlite3' % (observer, resume, restoration)), ['A'])
                _, payload = self.fence('2026-11-10T23:59:59Z', calendar)
                def midnight():
                    self.now = '2026-11-11T00:00:00Z'
                try:
                    self.deliver(payload, midnight, observer=observer, resume=resume, restoration=restoration)
                except ValidationError:
                    pass
                self.assert_unsent()

    def test_reverted_accepted_opening_cannot_borrow_an_exhausted_charge(self):
        from settings_pr_fixtures import publish_fixture_settings
        self.root['reporting']['mode'] = 'slack'
        self.accepted_calendar(dict(review_batching=dict(presentation_time='07:00')))
        calendar = self.root['operating_calendar']
        for now in ('2026-11-06T16:00:00Z', '2026-11-06T16:01:00Z'):
            reviews, payload = self.fence(now, calendar)
            reviews.reconcile(dict(batch_id=payload['batch_id'], payload_sha256=payload['payload_sha256'],
                status='NOT_DELIVERED', observed_at=now, provider_receipt_id='negative-' + now), calendar)
        earlier = self.load_policy()
        later = deepcopy(earlier)
        later['operating_calendar']['review_batching']['presentation_time'] = '08:00'
        publish_fixture_settings(self, self.policy_root, self.gov, later)
        self.root = self.load_policy()
        _, payload = self.fence('2026-11-06T16:02:00Z', self.root['operating_calendar'])
        def revert():
            publish_fixture_settings(self, self.policy_root, self.gov, earlier)
        try:
            self.deliver(payload, revert)
        except ValidationError:
            pass
        self.assert_unsent()

    def test_dst_transition_preserves_the_same_daily_window_charge(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.root['reporting']['mode'] = 'slack'
        self.accepted_calendar(dict(review_batching=dict(enabled=False)))
        for day, opening in (('2026-03-29', '2026-03-29T00:00:00Z'),
                             ('2026-10-25', '2026-10-24T23:00:00Z')):
            with self.subTest(day=day):
                self.store = ContinuousControllerStore(self.path.with_name(day + '.sqlite3'), ['A'])
                _, payload = self.fence(day + 'T00:59:59Z', self.root['operating_calendar'])
                self.assertEqual(payload['presentation_window']['opening'], opening)
                def transition():
                    self.now = day + 'T01:00:00Z'
                self.deliver(payload, transition)
                self.assertEqual(len(self.posts), 1)
                self.assertEqual(len(self.reservations), 1)
                with self.store.connection() as db:
                    self.assertEqual(1, db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0])
                    self.assertEqual(1, db.execute('SELECT window_attempts FROM review_presentation_detail_v1').fetchone()[0])
