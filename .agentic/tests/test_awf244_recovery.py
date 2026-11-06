"""Red-first production regressions for unsent, unknown and exhausted reviews."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import unittest

import test_awf244_controller_wiring as fixture
from agentic.canonical import timestamp
from agentic.continuous_controller import production_review_batching_cycle
from agentic.cli import local_semantics
from agentic.contracts import Contracts
from agentic.review_batching import ReviewBatchStore


class RecoveryTests(unittest.TestCase):
    setUp = fixture.ControllerBatchingTests.setUp
    observe_ready = fixture.ControllerBatchingTests.observe_ready
    observe_candidate = fixture.ControllerBatchingTests.observe_candidate
    deliver = fixture.ControllerBatchingTests.deliver
    readback = fixture.ControllerBatchingTests.readback
    intent_states = fixture.ControllerBatchingTests.intent_states

    def cycle(self, *args, **kwargs):
        before = len(self.status_digests)
        result = fixture.ControllerBatchingTests.cycle(self, *args, **kwargs)
        contracts = Contracts(Path(__file__).resolve().parents[1] / 'schemas')
        for digest in self.status_digests[before:]:
            contracts.validate('controller-status-digest', digest)
            local_semantics('controller-status-digest', digest)
        self.assertFalse(any(error['operation'] in ('status_delivery', 'review_owner_alert')
                             for error in result['errors']))
        return result

    def assert_status_acknowledged(self, result):
        digest = self.status_digests[-1]
        self.assertEqual(result['status_delivery']['delivery_id'], digest['delivery_id'])
        self.assertEqual(result['status_delivery']['status'], 'DELIVERED')
        with self.store.connection() as db:
            row = db.execute('SELECT acknowledged_at FROM controller_outbox WHERE delivery_id=?',
                             (digest['delivery_id'],)).fetchone()
        self.assertEqual(row[0], self.now)
        return digest['review_batching']

    def batching(self, now, **ports):
        self.now = now
        defaults = dict(observe_owner_ready=self.observe_ready,
                        observe_review_candidate=self.observe_candidate,
                        deliver_review_batch=self.deliver,
                        observe_review_delivery=self.readback)
        defaults.update(ports)
        return production_review_batching_cycle(self.store, calendar={},
            binding=fixture.INVENTORY_BINDING, clock=lambda: self.now, **defaults)

    def observe_each(self, value, now):
        return dict(candidate=deepcopy(value), observed_at=now,
                    expires_at=(timestamp(now) + timedelta(minutes=1)).isoformat(),
                    open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION',
                    final_gate_valid=True)

    def test_recheck_blips_release_without_charging_then_deliver(self):
        for hour in (7, 8):
            count = 0
            def blip(value, now):
                nonlocal count
                count += 1
                if count == 3:
                    raise RuntimeError('fixture pre-send outage')
                return self.observe_candidate(value, now)
            result = self.cycle('2026-11-09T%02d:00:00Z' % hour,
                                observe_review_candidate=blip)
            self.assertTrue(result['errors'])
            self.assertEqual(self.intent_states(), ['PENDING'])
            with self.store.connection() as db:
                self.assertEqual(db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0], 0)
        self.negative_delivery = True
        self.cycle('2026-11-09T09:00:00Z')
        self.negative_delivery = False
        self.cycle('2026-11-09T10:00:00Z')
        self.assertEqual([b['members'][0]['attempt'] for b in self.delivered], [1, 2])
        self.assertEqual(self.intent_states(), ['DELIVERED'])

    def test_changed_member_withdraws_and_survivor_delivers_same_cycle(self):
        self.listed = [fixture.candidate(41), fixture.candidate(42)]
        counts = {}
        def drift(value, now):
            number = value['pull_request']
            counts[number] = counts.get(number, 0) + 1
            observation = self.observe_each(value, now)
            if number == 41 and counts[number] >= 3:
                observation['candidate']['head_sha'] = 'e' * 40
            return observation
        result = self.batching('2026-11-09T07:00:00Z', observe_review_candidate=drift)
        self.assertTrue(result['errors'])
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual([m['candidate']['pull_request'] for m in self.delivered[0]['members']], [42])
        view = ReviewBatchStore(self.store).view(self.now, {})
        withdrawn = next(i for i in view['intents'] if i['state'] == 'WITHDRAWN')
        self.assertEqual(withdrawn['candidate']['pull_request'], 41)
        self.assertEqual(withdrawn['changed_at'], self.now)
        self.assertTrue(withdrawn['reason'])

    def test_unknown_stays_fenced_visible_and_unrelated_pr_delivers(self):
        unknown = []
        def uncertain(batch):
            unknown.append(deepcopy(batch))
            return None
        self.batching('2026-11-09T07:00:00Z', deliver_review_batch=uncertain)
        self.listed = [fixture.candidate(42)]
        for now in ('2026-11-09T08:00:00Z', '2026-11-16T08:00:00Z'):
            result = self.cycle(now, observe_review_candidate=self.observe_each)
            self.assertTrue(result['review_batching']['owner_alerts'])
            self.assertTrue(result['errors'])
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.delivered[0]['members'][0]['candidate']['pull_request'], 42)
        digest = self.store.digest('2026-11-16T09:00:00Z')
        self.assertTrue(digest['review_batching']['owner_alerts'])
        self.assertEqual(len(ReviewBatchStore(self.store).pending_batches()), 1)
        self.assertEqual(len(unknown), 1)
        self.assertFalse(ReviewBatchStore(self.store).release_unsent(unknown[0]['batch_id'], self.now, {}))
        self.assertTrue(any(d['review_batching']['owner_alerts'] for d in self.status_digests))

    def test_prepared_restart_releases_but_legacy_claim_remains_fenced(self):
        self.batching('2026-11-06T18:00:00Z', deliver_review_batch=None, observe_review_delivery=None)
        self.now = '2026-11-09T07:00:00Z'
        batch_store = ReviewBatchStore(self.store)
        batch = batch_store.claim(self.now, {}, lambda c: self.observe_each(c, self.now))
        self.assertEqual(batch['members'][0]['attempt'], 1)
        restarted = ReviewBatchStore(self.store)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0], 0)
        self.batching('2026-11-09T07:00:00Z', observe_review_candidate=self.observe_each)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.delivered[0]['members'][0]['attempt'], 1)
        self.listed = [fixture.candidate(42)]
        self.batching('2026-11-09T08:00:00Z', deliver_review_batch=None, observe_review_delivery=None,
                      observe_review_candidate=self.observe_each)
        old = restarted.claim(self.now, {}, lambda c: self.observe_each(c, self.now))
        # Simulate a persisted pre-upgrade claim: no durable unsent proof.
        with self.store.transaction() as db:
            db.execute('DELETE FROM review_presentation_phase_v1 WHERE batch_id=?', (old['batch_id'],))
        self.assertFalse(restarted.release_unsent(old['batch_id'], self.now, {}))
        result = self.batching('2026-11-16T07:00:00Z', observe_review_candidate=self.observe_each)
        self.assertTrue(result['owner_alerts'])
        self.assertEqual(len(self.delivered), 1)

    def test_exhausted_repeated_outage_is_bounded_in_each_later_window(self):
        self.negative_delivery = True
        for day in (9, 10, 11):
            for hour in (7, 8, 9, 10):
                self.batching('2026-11-%02dT%02d:00:00Z' % (day, hour))
            self.assertEqual(len(self.delivered), (day - 8) * 2)
            self.assertEqual(self.intent_states(), ['EXHAUSTED'])
        self.assertEqual([b['members'][0]['attempt'] for b in self.delivered], [1, 2, 3, 4, 5, 6])

    def test_bad_trusted_ready_time_cannot_admit(self):
        for value in ('bad', None, '2026-11-09T11:00:00Z'):
            result = self.batching('2026-11-09T10:00:00Z',
                observe_review_candidate=lambda c, now: self.observe_each(c, now) | {'ready_at': value})
            self.assertEqual(result['unqualified'], 1)
            self.assertEqual(self.intent_states(), [])
        self.assertEqual(self.delivered, [])

    def test_legacy_exhaustion_recovers_from_retained_negative_receipts(self):
        self.negative_delivery = True
        self.batching('2026-11-09T07:00:00Z')
        self.batching('2026-11-09T08:00:00Z')
        with self.store.transaction() as db:
            db.execute('DELETE FROM review_presentation_detail_v1')
        self.negative_delivery = False
        self.batching('2026-11-10T07:00:00Z')
        self.assertEqual(len(self.delivered), 3)
        self.assertEqual(self.delivered[-1]['members'][0]['attempt'], 3)
        self.assertEqual(self.intent_states(), ['DELIVERED'])

    def test_exhaustion_reaches_immediate_status_delivery(self):
        self.negative_delivery = True
        self.cycle('2026-11-09T07:00:00Z')
        result = self.cycle('2026-11-09T08:00:00Z')
        self.assertTrue(result['review_batching']['owner_alerts'])
        self.assertEqual(self.status_digests[-1]['review_batching']['intents'][0]['state'], 'EXHAUSTED')
        self.assertTrue(self.status_digests[-1]['review_batching']['owner_alerts'])

    def test_full_cycle_unknown_alert_fences_changed_tuple_across_restart_and_windows(self):
        self.store = fixture.ContinuousControllerStore(self.path, ['A', 'B', 'C'],
            cadence_seconds=3600, periodic_status_enabled=False)
        unknown = []
        def uncertain(batch):
            unknown.append(deepcopy(batch))
            return None
        first = self.cycle('2026-11-09T07:00:00Z', deliver_review_batch=uncertain)
        view = self.assert_status_acknowledged(first)
        self.assertEqual(view['intents'][0]['state'], 'CLAIMED')
        alert = view['owner_alerts'][0]
        self.assertEqual(alert['state'], 'UNKNOWN')
        self.assertEqual(alert['prepared_at'], self.now)
        self.assertEqual(alert['intent_ids'], [view['intents'][0]['intent_id']])
        self.assertEqual(alert['batch_id'], unknown[0]['batch_id'])
        self.assertTrue(alert['reason'])
        self.assertTrue(any(e['operation'] == 'review_owner_attention' for e in first['errors']))
        self.store = fixture.ContinuousControllerStore(self.path, ['A', 'B', 'C'],
            cadence_seconds=3600, periodic_status_enabled=False)
        changed = fixture.candidate() | {'head_sha': 'e' * 40}
        self.listed = [changed, fixture.candidate(42)]
        for now in ('2026-11-09T07:00:01Z', '2026-11-10T07:00:00Z', '2026-11-16T07:00:00Z'):
            result = self.cycle(now, observe_review_candidate=self.observe_each)
            self.assertEqual(result['review_batching']['owner_alerts'][0], alert)
        self.assertEqual(len(unknown), 1)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual([m['candidate']['pull_request'] for m in self.delivered[0]['members']], [42])
        self.assertIn('readback', self.events)
        self.assertEqual(ReviewBatchStore(self.store).pending_batches(), unknown)
        self.assertFalse(ReviewBatchStore(self.store).release_unsent(unknown[0]['batch_id'], self.now, {}))

    def test_full_cycle_exhaustion_alert_and_next_window_recovery_with_periodic_off(self):
        self.store = fixture.ContinuousControllerStore(self.path, ['A', 'B', 'C'],
            cadence_seconds=3600, periodic_status_enabled=False)
        self.negative_delivery = True
        self.cycle('2026-11-09T07:00:00Z')
        result = self.cycle('2026-11-09T07:00:01Z')
        view = self.assert_status_acknowledged(result)
        intent = view['intents'][0]
        self.assertEqual(intent['state'], 'EXHAUSTED')
        self.assertEqual(intent['attempts'], 2)
        self.assertEqual(intent['changed_at'], self.now)
        self.assertEqual(intent['retry_at'], '2026-11-10T07:00:00Z')
        self.assertEqual(view['owner_alerts'], [dict(intent_id=intent['intent_id'], state='EXHAUSTED',
            reason=intent['reason'], retry_at=intent['retry_at'])])
        self.assertTrue(intent['reason'])
        self.assertTrue(any(e['operation'] == 'review_owner_attention' for e in result['errors']))
        for now in ('2026-11-09T07:00:02Z', '2026-11-10T06:59:59Z'):
            self.cycle(now)
        self.assertEqual(len(self.delivered), 2)
        self.negative_delivery = False
        self.cycle('2026-11-10T07:00:00Z')
        self.assertEqual([batch['members'][0]['attempt'] for batch in self.delivered], [1, 2, 3])
        result = self.cycle('2026-11-10T07:00:01Z')
        view = self.assert_status_acknowledged(result)
        self.assertEqual(view['intents'][0]['state'], 'DELIVERED')
        self.assertEqual(view['owner_alerts'], [])
        self.assertEqual(len(self.delivered), 3)

    def test_full_cycle_held_and_withdrawn_status_retains_identity_reason_and_time(self):
        self.cycle('2026-11-06T18:00:00Z')
        result = self.cycle('2026-11-06T18:00:01Z')
        held = self.assert_status_acknowledged(result)['intents'][0]
        self.assertEqual(held['candidate'], self.current)
        self.assertEqual(held['state'], 'PENDING')
        self.assertTrue(held['held'])
        self.assertEqual(held['ready_at'], '2026-11-06T18:00:00Z')
        self.assertEqual(held['scheduled_at'], '2026-11-09T07:00:00Z')
        self.assertTrue(held['reason'])
        self.listed = []
        self.change = {'merged': True}
        self.cycle('2026-11-09T07:00:00Z')
        result = self.cycle('2026-11-09T07:00:01Z')
        withdrawn = self.assert_status_acknowledged(result)['intents'][0]
        self.assertEqual(withdrawn['candidate'], held['candidate'])
        self.assertEqual(withdrawn['state'], 'WITHDRAWN')
        self.assertEqual(withdrawn['changed_at'], '2026-11-09T07:00:00Z')
        self.assertEqual(withdrawn['attempts'], 0)
        self.assertTrue(withdrawn['reason'])
        self.assertEqual(self.delivered, [])

    def test_exhausted_is_visible_then_recovers_next_window_with_two_send_ceiling(self):
        self.listed = [fixture.candidate(41), fixture.candidate(42)]
        self.negative_delivery = True
        for hour in (7, 8):
            self.batching('2026-11-09T%02d:00:00Z' % hour, observe_review_candidate=self.observe_each)
        exhausted = self.batching('2026-11-09T09:00:00Z', observe_review_candidate=self.observe_each)
        self.assertEqual(self.intent_states(), ['EXHAUSTED', 'EXHAUSTED'])
        self.assertTrue(exhausted['owner_alerts'])
        self.assertTrue(exhausted['errors'])
        self.assertEqual(len(self.delivered), 2)
        self.negative_delivery = False
        self.batching('2026-11-10T06:59:59Z', observe_review_candidate=self.observe_each)
        self.assertEqual(len(self.delivered), 2)
        self.batching('2026-11-10T07:00:00Z', observe_review_candidate=self.observe_each)
        self.assertEqual(len(self.delivered), 3)
        self.assertEqual([m['attempt'] for m in self.delivered[-1]['members']], [3, 3])
        for day in (11, 12, 13):
            self.batching('2026-11-%02dT07:00:00Z' % day, observe_review_candidate=self.observe_each)
        self.assertEqual(len(self.delivered), 3)
        self.assertEqual(self.intent_states(), ['DELIVERED', 'DELIVERED'])

    def test_normal_subsecond_step_is_not_catchup(self):
        count = 0
        def step(value, now):
            nonlocal count
            count += 1
            observation = self.observe_candidate(value, now)
            if count == 1:
                self.now = '2026-11-09T10:00:01.000001Z'
            return observation
        self.batching('2026-11-09T10:00:00Z', observe_review_candidate=step)
        self.assertEqual(len(self.delivered), 1)
        self.assertFalse(self.delivered[0]['catch_up'])

    def test_trusted_ready_time_before_cutoff_catches_up_next_opening(self):
        def ready(value, now):
            return self.observe_candidate(value, now) | {'ready_at': '2026-11-09T16:40:00Z'}
        self.cycle('2026-11-09T17:10:00Z', observe_review_candidate=ready)
        self.cycle('2026-11-10T07:00:00Z', observe_review_candidate=ready)
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.delivered[0]['members'][0]['ready_at'], '2026-11-09T16:40:00Z')
        self.assertTrue(self.delivered[0]['catch_up'])


if __name__ == '__main__':
    unittest.main()
