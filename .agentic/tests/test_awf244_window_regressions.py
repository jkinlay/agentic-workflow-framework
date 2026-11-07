"""Real production cycles and installed owner CLI; clocks and gh stay offline."""
import json
import unittest
from unittest.mock import patch

from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path
import source_only
from settings_pr_fixtures import publish_fixture_settings, fixture_repository, fixture_reservations
from test_operating import governance
import test_awf244_controller_wiring as controller_fixture
from agentic import operating as op
from agentic.continuous_controller import ContinuousControllerStore
from agentic.review_batching import ReviewBatchStore
from test_continuous_controller_planning import planning_fixture_inputs


class WindowRegressions(unittest.TestCase):
    observe_ready = controller_fixture.ControllerBatchingTests.observe_ready
    observe_candidate = controller_fixture.ControllerBatchingTests.observe_candidate
    deliver = controller_fixture.ControllerBatchingTests.deliver
    readback = controller_fixture.ControllerBatchingTests.readback
    intent_states = controller_fixture.ControllerBatchingTests.intent_states

    def setUp(self):
        controller_fixture.ControllerBatchingTests.setUp(self)
        self.owner = SimpleNamespace(root=Path(self.temp.name) / 'project', gov=governance())
        self.owner.gov['github']['repository_id'] = 101
        self.owner.gov['merge_gate']['trusted_owner_ids'] = [1001]
        self.binding = dict(controller_fixture.INVENTORY_BINDING,
                            project_id=self.owner.gov['project']['id'])
        publish_fixture_settings(self, self.owner.root, self.owner.gov,
            op.default_operating(self.owner.gov['execution']['model_routing']))

    def cycle(self, now):
        self.now = now
        snapshot = op.read_operating(self.owner.root, self.owner.gov)
        with patch('test_planning.operating_snapshot', return_value=snapshot):
            inputs = planning_fixture_inputs([], self.store, 3, config=self.owner.gov,
                binding=self.binding, now=now, repository=controller_fixture.REPOSITORY)
        inputs.update(planning_operating_snapshot=snapshot, operating_config_sha256=snapshot.operating_hash,
                      planning_config=self.owner.gov,
                      project_config_sha256=op.fingerprint('accepted-project-config', self.owner.gov))
        return controller_fixture.production_controller_cycle(self.store, **inputs, now=now, host_capacity=3,
            controller_config=self.owner.gov['controller'],
            inventory_binding=self.binding, **fixture_repository(self.owner.root),
            observe_reservations=lambda request: fixture_reservations(self.owner.root, request),
            controller_clock=lambda: self.now,
            observe_inventory=lambda: dict(source='host_observation', observed_at=now,
                binding=self.binding, complete=True, tickets=[],
                inventory_sha256=op.fingerprint('controller-inventory', {'binding': self.binding, 'tickets': []})),
            dispatch_ticket=lambda _: self.fail('unexpected dispatch'),
            observe_dispatch=lambda _: self.fail('unexpected dispatch readback'),
            deliver_status=lambda digest: dict(delivery_id=digest['delivery_id'], status='DELIVERED', observed_at=now),
            observe_owner_ready=self.observe_ready, observe_review_candidate=self.observe_candidate,
            deliver_review_batch=self.deliver, observe_review_delivery=self.readback)

    def restart(self):
        self.store = ContinuousControllerStore(self.path, ['A', 'B', 'C'], cadence_seconds=3600)

    def merged_change(self, assignment):
        value = op.read_operating(self.owner.root, self.owner.gov).config
        path, text = assignment.split('=', 1)
        op._put(value, op._path(path), op._selection(path, text))
        op.validate_operating(value, self.owner.gov)
        publish_fixture_settings(self, self.owner.root, self.owner.gov, value)
        self.assertEqual(value, op.read_operating(self.owner.root, self.owner.gov).config)

    def exhaust_friday(self):
        self.negative_delivery = True
        self.cycle('2026-11-06T16:00:00Z')
        self.cycle('2026-11-06T16:01:00Z')
        self.assertEqual(['EXHAUSTED'], self.intent_states())
        self.assertEqual(2, len(self.delivered))
        self.negative_delivery = False

    def assert_attempts(self, lifetime, window):
        with self.store.connection() as db:
            self.assertEqual(lifetime, db.execute('SELECT attempts FROM review_presentation_intent_v1').fetchone()[0])
            self.assertEqual(window, db.execute('SELECT window_attempts FROM review_presentation_detail_v1').fetchone()[0])

    def test_calendar_merge_overtakes_send(self):
        original = ReviewBatchStore.begin_delivery
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)
            self.merged_change('operating_calendar.review_batching.cutoff_time=16:30')
            snapshot = op.read_operating(self.owner.root, self.owner.gov)
            self.assertEqual('16:30', op.effective_calendar(snapshot.config)['review_batching']['cutoff_time'])
            return cancel
        with patch.object(ReviewBatchStore, 'begin_delivery', fence):
            result = self.cycle('2026-11-06T16:59:00Z')
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(0, 0)
        self.assertEqual('2026-11-09T07:00:00Z',
                         result['review_batching']['status']['intents'][0]['scheduled_at'])
        self.restart()
        self.cycle('2026-11-09T07:00:00Z')
        self.restart()
        self.cycle('2026-11-09T07:01:00Z')
        self.assertEqual(1, len(self.delivered))
        self.assert_attempts(1, 1)

    def test_backward_clock_after_fence_recovers_without_send(self):
        original = ReviewBatchStore.begin_delivery
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)
            self.now = '2026-11-06T15:59:59Z'
            return cancel
        with patch.object(ReviewBatchStore, 'begin_delivery', fence):
            result = self.cycle('2026-11-06T16:00:00Z')
        self.assertTrue(result['errors'])
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(0, 0)
        self.restart()
        self.cycle('2026-11-06T16:01:00Z')
        self.restart()
        self.cycle('2026-11-09T07:00:00Z')
        self.assertEqual(1, len(self.delivered))
        self.assertEqual(['DELIVERED'], self.intent_states())
        self.assert_attempts(1, 1)

    def test_post_fence_malformed_default_calendar_cancels_without_attempt(self):
        original = ReviewBatchStore.begin_delivery
        accepted = op.read_operating(self.owner.root, self.owner.gov).config
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)
            value = deepcopy(accepted)
            value['operating_calendar'] = None
            publish_fixture_settings(self, self.owner.root, self.owner.gov, value)
            # Working defaults cannot mask malformed accepted calendar policy.
            (self.owner.root / op.CONFIG).write_bytes(op._json(accepted))
            return cancel
        with patch.object(ReviewBatchStore, 'begin_delivery', fence):
            result = self.cycle('2026-11-06T16:00:00Z')
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(0, 0)
        self.assertTrue(any(error['operation'] == 'review_recheck'
                            and 'Protected default-branch settings unavailable:' in error['reason']
                            and 'operating_calendar' in error['reason']
                            for error in result['errors']))
        publish_fixture_settings(self, self.owner.root, self.owner.gov, accepted)
        self.restart()
        self.cycle('2026-11-06T16:01:00Z')
        self.assertEqual(1, len(self.delivered))
        self.assert_attempts(1, 1)

    def test_post_fence_settings_io_exception_cancels_and_reports_original(self):
        original = ReviewBatchStore.begin_delivery
        failure = patch.object(op, 'read_operating', side_effect=PermissionError('fixture settings unavailable'))
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)
            failure.start()
            return cancel
        try:
            with patch.object(ReviewBatchStore, 'begin_delivery', fence):
                result = self.cycle('2026-11-06T16:00:00Z')
        finally:
            failure.stop()
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(0, 0)
        self.assertTrue(any(error['reason'] == 'PermissionError' for error in result['errors']))
        self.restart()
        self.cycle('2026-11-06T16:01:00Z')
        self.assertEqual(1, len(self.delivered))
        self.assert_attempts(1, 1)

    def test_cutoff_crossed_by_fence_commit_cancels_without_attempt_and_restart_deduplicates(self):
        original = ReviewBatchStore.begin_delivery
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)  # The real durable commit completes first.
            self.now = '2026-11-06T17:00:00Z'
            return cancel
        with patch.object(ReviewBatchStore, 'begin_delivery', fence):
            self.cycle('2026-11-06T16:59:59Z')
        self.assertEqual([], self.delivered)
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(0, 0)
        self.restart()
        self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual([], self.delivered)
        self.cycle('2026-11-09T07:00:00Z')
        self.restart()
        self.cycle('2026-11-09T07:01:00Z')
        self.assertEqual(1, len(self.delivered))
        self.assert_attempts(1, 1)

    def test_evidence_expired_during_fence_commit_does_not_send(self):
        original = ReviewBatchStore.begin_delivery
        def fence(batch_store, *args):
            cancel = original(batch_store, *args)
            self.now = '2026-11-06T16:01:00Z'
            return cancel
        with patch.object(ReviewBatchStore, 'begin_delivery', fence):
            self.cycle('2026-11-06T16:00:00Z')
        self.assertEqual([], self.delivered)
        self.assert_attempts(0, 0)

    def test_pending_failure_does_not_consume_next_window_allowance_across_restart(self):
        self.negative_delivery = True
        self.cycle('2026-11-06T16:00:00Z')
        self.restart()
        self.cycle('2026-11-09T07:00:00Z')
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(2, 1)
        self.restart()
        self.negative_delivery = False
        self.cycle('2026-11-09T07:01:00Z')
        self.assertEqual(3, len(self.delivered))
        self.assertEqual(['DELIVERED'], self.intent_states())
        self.assert_attempts(3, 2)

    def test_exhausted_merged_disabled_batching_presents_saturday(self):
        self.exhaust_friday()
        with self.store.connection() as db:
            receipts = [tuple(row) for row in db.execute('SELECT * FROM review_presentation_outbox_v1 ORDER BY batch_id')]
        self.merged_change('operating_calendar.review_batching.enabled=false')
        view = ReviewBatchStore(self.store).view('2026-11-07T10:00:00Z', op.effective_calendar(
            op.read_operating(self.owner.root, self.owner.gov).config))
        self.assertEqual('2026-11-07T10:00:00Z', view['intents'][0]['retry_at'])
        self.restart()
        self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual(3, len(self.delivered))
        self.assert_attempts(3, 1)
        with self.store.connection() as db:
            for receipt in receipts:
                self.assertEqual(receipt, tuple(db.execute(
                    'SELECT * FROM review_presentation_outbox_v1 WHERE batch_id=?', (receipt[0],)).fetchone()))

    def test_exhausted_merged_earlier_opening_presents_at_six(self):
        self.exhaust_friday()
        self.merged_change('operating_calendar.review_batching.presentation_time=06:00')
        self.restart()
        self.cycle('2026-11-09T05:59:59Z')
        self.assertEqual(2, len(self.delivered))
        self.cycle('2026-11-09T06:00:00Z')
        self.assertEqual(3, len(self.delivered))
        self.assert_attempts(3, 1)

    def test_exhausted_merged_timezone_holds_until_current_local_opening(self):
        self.exhaust_friday()
        self.merged_change('operating_calendar.timezone=America/New_York')
        self.restart()
        self.cycle('2026-11-09T07:00:00Z')
        self.assertEqual(2, len(self.delivered))
        self.cycle('2026-11-09T12:00:00Z')
        self.assertEqual(3, len(self.delivered))
        self.assert_attempts(3, 1)

    def test_disabled_batching_pending_failure_gets_two_attempts_each_local_day(self):
        self.merged_change('operating_calendar.review_batching.enabled=false')
        self.negative_delivery = True
        self.cycle('2026-11-07T23:59:00Z')
        self.restart()
        self.cycle('2026-11-08T00:00:00Z')
        self.assertEqual(['PENDING'], self.intent_states())
        self.assert_attempts(2, 1)
        self.restart()
        self.cycle('2026-11-08T00:01:00Z')
        self.cycle('2026-11-08T00:02:00Z')
        self.assertEqual(3, len(self.delivered))
        self.assertEqual(['EXHAUSTED'], self.intent_states())
        self.assert_attempts(3, 2)

    def test_eod_only_change_does_not_grant_more_attempts_in_same_review_window(self):
        self.exhaust_friday()
        self.merged_change('operating_calendar.eod.time=18:00')
        self.restart()
        self.cycle('2026-11-06T16:02:00Z')
        self.assertEqual(2, len(self.delivered))
        self.assert_attempts(2, 2)

    def test_crash_after_fence_before_postcheck_keeps_unknown_across_restart(self):
        original = ReviewBatchStore.begin_delivery
        def crash(batch_store, *args):
            original(batch_store, *args)
            raise RuntimeError('fixture lost live cancellation continuation')
        with patch.object(ReviewBatchStore, 'begin_delivery', crash):
            self.cycle('2026-11-06T16:59:59Z')
        self.assertEqual([], self.delivered)
        self.assertEqual(['CLAIMED'], self.intent_states())
        self.restart()
        self.cycle('2026-11-09T07:00:00Z')
        self.assertEqual([], self.delivered)
        self.assertEqual(['CLAIMED'], self.intent_states())
        self.assert_attempts(1, 1)

    def test_branch_and_hand_edited_calendar_cannot_release_exhausted(self):
        self.exhaust_friday()
        self.restart()
        self.cycle('2026-11-07T10:00:00Z')
        self.assertEqual(2, len(self.delivered))
        value = json.loads((self.owner.root / op.CONFIG).read_bytes())
        value['operating_calendar'] = op.effective_calendar(value)
        value['operating_calendar']['review_batching']['enabled'] = False
        (self.owner.root / op.CONFIG).write_bytes(op._json(value))
        self.cycle('2026-11-07T10:01:00Z')
        self.assertEqual(2, len(self.delivered))
        self.assert_attempts(2, 2)


if __name__ == '__main__':
    unittest.main()
