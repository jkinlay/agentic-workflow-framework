"""Real controller cycles with synthetic trusted observers and a fake delivery port.

These fixtures exercise local wiring, never live delivery or qualification.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import tempfile
import unittest

import source_only
from agentic.canonical import timestamp
from agentic.continuous_controller import ContinuousControllerStore
from agentic.operating import validate_operating
from agentic.review_batching import ReviewBatchStore
from test_continuous_controller import (
    INVENTORY_BINDING, REPOSITORY, dispatch_receipt, inventory_observation,
    production_controller_cycle, ticket,
)
from test_continuous_controller_planning import planning_fixture_inputs


def candidate(number=41):
    return dict(repository_id=101, pull_request=number, head_sha='a'*40,
                base_sha='b'*40, head_tree_sha='c'*40, final_gate_sha256='d'*64)


class ControllerBatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'controller.sqlite3'
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)
        self.current = candidate()
        self.listed = [candidate()]
        self.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        self.calendar = {}
        self.events = []
        self.delivered = []
        self.receipts = {}
        self.change = {}
        self.unknown_delivery = False
        self.negative_delivery = False
        self.observations = []
        self.status_digests = []

    def observe_ready(self, request):
        self.events.append('ready')
        return dict(binding=deepcopy(request['binding']), observed_at=request['observed_at'],
                    candidates=deepcopy(self.listed))

    def observe_candidate(self, value, now):
        self.events.append('candidate')
        self.observations.append((deepcopy(value), now))
        return dict(candidate=deepcopy(self.current), observed_at=now,
                    expires_at=(timestamp(now) + timedelta(minutes=1)).isoformat(),
                    open=True, merged=False, readiness=self.readiness,
                    final_gate_valid=True) | deepcopy(self.change)

    def deliver(self, batch):
        self.events.append('review')
        self.delivered.append(deepcopy(batch))
        result = dict(batch_id=batch['batch_id'], payload_sha256=batch['payload_sha256'],
                      status='NOT_DELIVERED' if self.negative_delivery else 'DELIVERED',
                      observed_at=self.now, provider_receipt_id='receipt-' + batch['batch_id'])
        self.receipts[batch['batch_id']] = result
        if self.unknown_delivery:
            raise RuntimeError('fixture lost delivery response')
        return result

    def readback(self, batch):
        self.events.append('readback')
        return deepcopy(self.receipts.get(batch['batch_id']))

    def cycle(self, now, items=None, **overrides):
        self.now = now
        items = [] if items is None else items
        inputs = planning_fixture_inputs(items, self.store, 3, binding=INVENTORY_BINDING,
                                         now=now, repository=REPOSITORY)
        config = deepcopy(inputs['planning_operating_snapshot'].config)
        config['operating_calendar'] = deepcopy(self.calendar)
        from settings_pr_fixtures import accepted_fixture_snapshot
        root = Path(self.temp.name) / 'project'
        root.mkdir(exist_ok=True)
        snapshot = accepted_fixture_snapshot(self, root, config, inputs['planning_config'])
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash)
        def dispatch(payload):
            self.events.append(payload['lifecycle_stage'])
            return dispatch_receipt(payload, now)
        def status(digest):
            self.events.append('status')
            self.status_digests.append(deepcopy(digest))
            return dict(delivery_id=digest['delivery_id'], status='DELIVERED', observed_at=now)
        calls = dict(observe_owner_ready=self.observe_ready,
                     observe_review_candidate=self.observe_candidate,
                     deliver_review_batch=self.deliver, observe_review_delivery=self.readback)
        from settings_pr_fixtures import fixture_repository, fixture_reservations
        repository = fixture_repository(root)
        calls['observe_reservations'] = lambda request: fixture_reservations(root, request)
        calls.update(overrides)
        return production_controller_cycle(self.store, **inputs, now=now, host_capacity=3,
            inventory_binding=INVENTORY_BINDING, **repository,
            controller_clock=lambda: self.now, observe_inventory=lambda: inventory_observation(now, items),
            dispatch_ticket=dispatch, observe_dispatch=lambda payload: dispatch_receipt(payload, now),
            deliver_status=status, **calls)

    def intent_states(self):
        with self.store.connection() as db:
            return [row[0] for row in db.execute('SELECT state FROM review_presentation_intent_v1')]

    def test_thursday_1730_presents_friday_0700(self):
        first = self.cycle('2026-11-05T17:30:00Z')
        self.assertEqual(first['errors'], [])
        self.assertEqual(self.intent_states(), ['PENDING'])
        self.assertEqual(self.delivered, [])
        self.cycle('2026-11-06T06:59:59Z')
        self.assertEqual(self.delivered, [])
        self.cycle('2026-11-06T07:00:00Z')
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.delivered[0]['members'][0]['scheduled_at'], '2026-11-06T07:00:00Z')
        self.assertFalse(self.delivered[0]['merge_authorized'])
        self.assertEqual(self.intent_states(), ['DELIVERED'])

    def test_friday_1800_presents_monday_0700_across_restart(self):
        self.cycle('2026-11-06T18:00:00Z')
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)
        for now in ('2026-11-07T10:00:00Z', '2026-11-08T10:00:00Z', '2026-11-09T06:59:59Z'):
            self.cycle(now)
            self.assertEqual(self.delivered, [])
        self.cycle('2026-11-09T07:00:00Z')
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)
        self.cycle('2026-11-09T08:00:00Z')
        self.assertEqual(len(self.delivered), 1)
        member = self.delivered[0]['members'][0]
        self.assertEqual(member['ready_at'], '2026-11-06T18:00:00Z')
        self.assertEqual(member['scheduled_at'], '2026-11-09T07:00:00Z')

    def test_tuesday_1000_presents_immediately_after_fresh_recheck(self):
        result = self.cycle('2026-11-10T10:00:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.events[-3:], ['candidate', 'candidate', 'review'])
        self.assertEqual(self.delivered[0]['prepared_at'], self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM review_presentation_check_v1').fetchone()[0], 1)

    def test_only_fresh_owner_ready_gate_can_enqueue(self):
        for state in ('PR_DRAFT', 'READY_FOR_CRITIC', 'SPECIALIST_REVIEW', 'FINAL_REVIEW', 'OWNER_AUTHORIZED'):
            self.readiness = state
            self.cycle('2026-11-05T17:30:00Z')
            self.assertEqual(self.intent_states(), [])
        self.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        for change in ({'final_gate_valid': False}, {'open': False}, {'merged': True},
                       {'observed_at': '2026-11-05T17:29:59Z'}, {'expires_at': '2026-11-05T17:30:00Z'}):
            self.change = change
            self.cycle('2026-11-05T17:30:00Z')
            self.assertEqual(self.intent_states(), [])
        self.change = {}
        self.cycle('2026-11-05T17:30:00Z')
        self.assertEqual(self.intent_states(), ['PENDING'])

    def test_foreign_repository_and_wrong_ready_envelope_refuse(self):
        self.current['repository_id'] = 102
        self.listed = [deepcopy(self.current)]
        result = self.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(result['errors'])
        self.assertEqual(self.intent_states(), [])
        self.listed = [candidate()]
        self.current = candidate()
        result = self.cycle('2026-11-10T10:00:00Z', observe_owner_ready=lambda request:
                            dict(self.observe_ready(request), observed_at='2026-11-10T09:59:59Z'))
        self.assertTrue(result['errors'])
        self.assertEqual(self.intent_states(), [])

    def test_changed_head_before_presentation_withdraws(self):
        self.cycle('2026-11-05T17:30:00Z')
        self.listed = []
        self.current['head_sha'] = 'e'*40
        self.cycle('2026-11-06T07:00:00Z')
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.intent_states(), ['WITHDRAWN'])

    def test_expired_presentation_recovers_after_restart_as_timestamped_catchup(self):
        self.cycle('2026-11-06T18:00:00Z')
        self.listed = []
        self.change = {'expires_at': '2026-11-09T07:00:00Z'}
        self.cycle('2026-11-09T07:00:00Z')
        self.assertEqual(self.intent_states(), ['PENDING'])
        self.assertEqual(self.delivered, [])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0], 0)
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)
        self.change = {}
        self.listed = [candidate()]
        result = self.cycle('2026-11-09T08:00:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(self.delivered), 1)
        batch = self.delivered[0]
        self.assertTrue(batch['catch_up'])
        self.assertEqual(batch['prepared_at'], '2026-11-09T08:00:00Z')
        self.assertEqual(batch['members'][0]['ready_at'], '2026-11-06T18:00:00Z')
        self.assertEqual(batch['members'][0]['scheduled_at'], '2026-11-09T07:00:00Z')
        repeated = self.cycle('2026-11-09T09:00:00Z')
        self.assertEqual(repeated['review_batching']['enqueued'], [])
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.intent_states(), ['DELIVERED'])

    def test_unavailable_or_stale_observation_keeps_unsent_candidate_pending(self):
        for number, change in enumerate((None, {}, {'observed_at': '2026-11-09T06:59:59Z'},
                                         {'expires_at': '2026-11-09T07:00:00Z', 'merged': True})):
            with self.subTest(change=change):
                self.store = ContinuousControllerStore(Path(self.temp.name) / ('stale%d.db' % number), ['A', 'B', 'C'])
                self.listed = [candidate()]
                self.cycle('2026-11-06T18:00:00Z')
                self.listed = []
                def unavailable(value, now):
                    if change is None or change == {}:
                        return change
                    return self.observe_candidate(value, now) | change
                self.cycle('2026-11-09T07:00:00Z', observe_review_candidate=unavailable)
                self.assertEqual(self.intent_states(), ['PENDING'])
                self.assertEqual(self.delivered, [])
                self.cycle('2026-11-09T08:00:00Z')
                self.assertEqual(self.intent_states(), ['DELIVERED'])
                self.assertEqual(len(self.delivered), 1)
                self.delivered.clear()

    def test_returned_identity_requires_fresh_qualification_to_restore(self):
        self.cycle('2026-11-06T18:00:00Z')
        self.listed = []
        self.current['head_sha'] = 'e'*40
        self.cycle('2026-11-09T07:00:00Z')
        self.assertEqual(self.intent_states(), ['WITHDRAWN'])
        self.assertIsNone(ReviewBatchStore(self.store).enqueue(candidate(), self.now, {}))
        self.current = candidate()
        self.cycle('2026-11-09T08:00:00Z')
        self.assertEqual(self.intent_states(), ['WITHDRAWN'])
        self.assertEqual(self.delivered, [])
        self.listed = [candidate()]
        self.change = {'final_gate_valid': False}
        self.cycle('2026-11-09T09:00:00Z')
        self.assertEqual(self.intent_states(), ['WITHDRAWN'])
        self.change = {}
        self.cycle('2026-11-09T10:00:00Z')
        self.assertEqual(self.intent_states(), ['DELIVERED'])
        self.assertEqual(len(self.delivered), 1)
        self.assertTrue(self.delivered[0]['catch_up'])
        self.assertEqual(self.delivered[0]['members'][0]['ready_at'], '2026-11-06T18:00:00Z')
        self.assertIsNone(ReviewBatchStore(self.store).enqueue(candidate(), self.now, {}))

    def test_all_candidate_pins_and_readiness_reobserved_when_due(self):
        changes = [dict(candidate={**candidate(), key: value}) for key, value in (
            ('repository_id', 102), ('pull_request', 42), ('base_sha', 'e'*40),
            ('head_tree_sha', 'e'*40), ('final_gate_sha256', 'e'*64))]
        changes += [dict(open=False), dict(merged=True), dict(readiness='FINAL_REVIEW'),
                    dict(final_gate_valid=False)]
        for number, change in enumerate(changes):
            with self.subTest(change=change):
                self.store = ContinuousControllerStore(Path(self.temp.name) / ('case%d.db' % number), ['A', 'B', 'C'])
                self.change = {}
                self.listed = [candidate()]
                self.cycle('2026-11-05T17:30:00Z')
                self.listed = []
                self.change = change
                self.cycle('2026-11-06T07:00:00Z')
                self.assertEqual(self.intent_states(), ['WITHDRAWN'])
                self.assertEqual(self.delivered, [])

    def test_immediate_presend_drift_never_reaches_delivery(self):
        count = 0
        def drift(value, now):
            nonlocal count
            count += 1
            return self.observe_candidate(value, now) | ({'merged': True} if count == 3 else {})
        result = self.cycle('2026-11-10T10:00:00Z', observe_review_candidate=drift)
        self.assertTrue(result['errors'])
        self.assertEqual(count, 3)
        self.assertEqual(self.delivered, [])
        self.assertEqual(len(ReviewBatchStore(self.store).pending_batches()), 0)
        self.assertEqual(self.intent_states(), ['WITHDRAWN'])
        self.assertTrue(ReviewBatchStore(self.store).view(self.now, {})['intents'][0]['reason'])

    def test_ambiguous_send_restart_reads_receipt_without_resend(self):
        self.unknown_delivery = True
        result = self.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(result['errors'])
        self.assertEqual(self.intent_states(), ['CLAIMED'])
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)
        self.cycle('2026-11-10T11:00:00Z')
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.intent_states(), ['DELIVERED'])

    def test_restart_after_provably_unsent_claim_delivers_without_charging_claim(self):
        self.cycle('2026-11-05T17:30:00Z')
        self.now = '2026-11-06T07:00:00Z'
        batch = ReviewBatchStore(self.store).claim(self.now, {}, lambda value: self.observe_candidate(value, self.now))
        self.assertIsNotNone(batch)
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)
        self.cycle('2026-11-06T08:00:00Z')
        self.assertEqual(len(self.delivered), 1)
        self.assertEqual(self.intent_states(), ['DELIVERED'])
        self.assertEqual(self.delivered[0]['members'][0]['attempt'], 1)
        self.assertNotIn('readback', self.events)

    def test_negative_delivery_retry_is_bounded_across_cycles(self):
        self.negative_delivery = True
        self.cycle('2026-11-10T10:00:00Z')
        self.cycle('2026-11-10T11:00:00Z')
        exhausted = self.cycle('2026-11-10T12:00:00Z')
        self.assertEqual(exhausted['review_batching']['enqueued'], [])
        self.assertEqual(len(self.delivered), 2)
        self.assertEqual(self.intent_states(), ['EXHAUSTED'])

    def test_dispatch_independent_review_amendments_and_hourly_status_continue(self):
        items = [ticket('EX-1', 1),
                 ticket('EX-2', 2, dispatch_role='critic', lifecycle_stage='independent_review'),
                 ticket('EX-3', 3, lifecycle_stage='amendment')]
        first = self.cycle('2026-11-05T17:30:00Z', items)
        self.assertEqual(first['errors'], [])
        self.assertEqual(len(first['dispatch_receipts']), 3)
        self.assertEqual(self.events[:4], ['implementation', 'independent_review', 'amendment', 'status'])
        self.cycle('2026-11-05T18:30:00Z', items)
        self.assertEqual(self.events.count('status'), 2)
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.intent_states(), ['PENDING'])

    def test_observer_outage_does_not_delay_dispatch_or_status(self):
        def unavailable(request):
            raise RuntimeError('fixture observer unavailable')
        result = self.cycle('2026-11-10T10:00:00Z', [ticket('EX-1', 1)], observe_owner_ready=unavailable)
        self.assertEqual(len(result['dispatch_receipts']), 1)
        self.assertEqual(result['status_delivery']['status'], 'DELIVERED')
        self.assertTrue(result['errors'])

    def test_effective_calendar_is_resolved_each_cycle_and_shared_with_reporting(self):
        self.cycle('2026-11-06T18:00:00Z')
        self.calendar = dict(review_batching=dict(enabled=False), eod=dict(time='19:00', weekdays=[2, 4]))
        result = self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual(result['operating_calendar']['eod'], dict(time='19:00', weekdays=[2, 4]))
        self.assertEqual(len(self.delivered), 1)
        self.assertFalse(result['execution_authority'])

    def test_collection_can_retain_intents_without_a_delivery_transport(self):
        result = self.cycle('2026-11-10T10:00:00Z', deliver_review_batch=None, observe_review_delivery=None)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.intent_states(), ['PENDING'])
        self.assertEqual(self.delivered, [])

    def test_no_collection_port_does_not_turn_inventory_into_ready_events(self):
        result = self.cycle('2026-11-10T10:00:00Z', [ticket('EX-1', 1)],
                            observe_owner_ready=None, observe_review_candidate=None,
                            deliver_review_batch=None, observe_review_delivery=None)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.intent_states(), [])
        self.assertEqual(self.delivered, [])

    def test_cutoff_crossed_during_claim_is_rechecked_before_send(self):
        count = 0
        def cross_cutoff(value, now):
            nonlocal count
            count += 1
            observation = self.observe_candidate(value, now)
            if count == 2:
                self.now = '2026-11-10T17:00:00Z'
            return observation
        result = self.cycle('2026-11-10T16:59:59Z', observe_review_candidate=cross_cutoff)
        self.assertTrue(result['errors'])
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.intent_states(), ['PENDING'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0], 0)

    def test_final_observation_crossing_cutoff_or_expiry_cannot_send(self):
        for number, end in enumerate(('2026-11-10T17:00:00Z', '2026-11-10T10:02:00Z')):
            with self.subTest(end=end):
                self.store = ContinuousControllerStore(Path(self.temp.name) / ('slow%d.db' % number), ['A', 'B', 'C'])
                count = 0
                def slow(value, now):
                    nonlocal count
                    count += 1
                    observation = self.observe_candidate(value, now)
                    if count == 3:
                        self.now = end
                    return observation
                start = '2026-11-10T16:59:59Z' if number == 0 else '2026-11-10T10:00:00Z'
                result = self.cycle(start, observe_review_candidate=slow)
                self.assertTrue(result['errors'])
                self.assertEqual(self.delivered, [])
                self.assertEqual(self.intent_states(), ['PENDING'])
                with self.store.connection() as db:
                    self.assertEqual(db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0], 0)

    def test_readback_for_another_batch_does_not_resolve_or_resend(self):
        self.unknown_delivery = True
        self.cycle('2026-11-10T10:00:00Z')
        def foreign(batch):
            return dict(self.readback(batch), batch_id='e'*64)
        result = self.cycle('2026-11-10T11:00:00Z', observe_review_delivery=foreign)
        self.assertTrue(result['errors'])
        self.assertEqual(self.intent_states(), ['CLAIMED'])
        self.assertEqual(len(self.delivered), 1)

    def test_missed_window_tick_presents_timestamped_catchup(self):
        self.cycle('2026-11-05T17:30:00Z')
        self.cycle('2026-11-06T09:00:00Z')
        self.assertTrue(self.delivered[0]['catch_up'])
        self.assertEqual(self.delivered[0]['prepared_at'], self.now)
        self.assertEqual(self.delivered[0]['members'][0]['scheduled_at'], '2026-11-06T07:00:00Z')


if __name__ == '__main__':
    unittest.main()
