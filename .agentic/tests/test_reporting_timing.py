"""Deadline and progress regressions through offline ports and controller seams."""
import threading
import unittest
from unittest.mock import patch

import source_only
from agentic.reporting.bounded import DeliveryBudget


class ReportingTimingTests(unittest.TestCase):
    def test_unsent_recheck_budget_defers_without_losing_the_intent(self):
        from copy import deepcopy
        from agentic import ValidationError
        from agentic.review_batching import ReviewBatchStore
        import test_reporting_e6_regressions as e6
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.helper.listed = [deepcopy(case.helper.current)]
        with patch.object(ReviewBatchStore, 'recheck', side_effect=ValidationError('REPORT_DESTINATION_BUDGET')):
            result = case.cycle('2026-11-11T16:00:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['review_batching']['state'], 'DEFERRED')
        self.assertEqual(result['review_batching']['owner_alerts'], [])
        self.assertEqual(ReviewBatchStore(case.helper.store).pending_batches(), [])
        with case.helper.store.connection() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM review_presentation_intent_v1')], ['PENDING'])
        self.assertFalse(any(r['kind'] == 'final-human-review' for r in case.status()['reports']))

    def test_slow_sustained_reviews_preserve_recurring_hourly_progress(self):
        from copy import deepcopy
        import json
        import time
        import test_reporting_e6_regressions as e6
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.network.latency = 1.5
        original = case.helper.observe_candidate
        def observe(candidate, now):
            time.sleep(2.5)
            return dict(original(candidate, now), candidate=deepcopy(candidate))
        case.helper.observe_candidate = observe
        turns, errors = [], []
        for second in range(6):
            case.restart()
            case.helper.current['pull_request'] = 41 + second
            case.helper.listed = [deepcopy(case.helper.current)]
            errors.extend(case.cycle('2026-11-11T16:00:0%dZ' % second)['errors'])
            status = case.status()
            kinds = {r['report_id']: r['kind'] for r in status['reports']}
            turns.append({platform: {
                kind: [dict(status=d['status'], attempts=len(d['attempts']))
                       for d in status['deliveries'] if d['platform'] == platform
                       and kinds[d['report_id']] == kind]
                for kind in ('hourly', 'final-human-review')}
                for platform in ('teams', 'slack')})
        print('sustained slow reporting turns: ' + json.dumps(turns, sort_keys=True))
        self.assertEqual(errors, [])
        for platform in ('teams', 'slack'):
            with self.subTest(platform=platform):
                self.assertTrue(any(d['attempts'] for turn in turns[:2]
                                    for d in turn[platform]['hourly']), turns)
                self.assertTrue(any(d['status'] == 'DELIVERED'
                                    for d in turns[-1][platform]['hourly']), turns)
                self.assertTrue(any(d['status'] == 'DELIVERED'
                                    for d in turns[-1][platform]['final-human-review']), turns)
                self.assertTrue(all(d['attempts'] <= 2 for turn in turns
                                    for states in turn[platform].values() for d in states))

    def test_request_grace_is_bounded_scoped_and_cannot_admit_after_cancellation(self):
        from agentic import ValidationError
        from agentic.reporting.bounded import (request_scope, admit_request, transport_timeout,
                                               send_guard, source_timeout)
        with patch('agentic.reporting.bounded.monotonic', return_value=100):
            budget = DeliveryBudget()
        with budget.destination('slack'), request_scope('POST'):
            with patch('agentic.reporting.bounded.monotonic', return_value=129):
                admit_request()
            with patch('agentic.reporting.bounded.monotonic', return_value=129.5):
                with self.assertRaisesRegex(ValidationError, 'REPORT_SEND_ALREADY_STARTED'):
                    admit_request()
                self.assertEqual(transport_timeout(), 14.5)
            with patch('agentic.reporting.bounded.monotonic', return_value=131):
                budget.cancel()
                self.assertEqual(transport_timeout(), 13)
                with send_guard(transport=True):
                    pass
                with self.assertRaises(ValidationError):
                    with send_guard():
                        pass
                with self.assertRaises(ValidationError):
                    source_timeout()
                with self.assertRaises(ValidationError):
                    admit_request()
                with budget.destination('teams'), self.assertRaises(ValidationError):
                    transport_timeout()
                with request_scope('GET'), self.assertRaises(ValidationError):
                    transport_timeout()
            with patch('agentic.reporting.bounded.monotonic', return_value=144):
                with self.assertRaises(ValidationError):
                    transport_timeout()
        with patch('agentic.reporting.bounded.monotonic', return_value=131), budget.destination('slack'):
            with self.assertRaises(ValidationError):
                transport_timeout()

    def test_admitted_post_crossing_commit_deadline_lands_once_and_reconciles_after_restart(self):
        import time
        import test_reporting_e6_regressions as e6
        from agentic.reporting import bounded
        from agentic.reporting.delivery import DeliveryOutbox
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.settings['mode'] = 'slack'
        original = DeliveryOutbox._save
        expired = []
        def cross_deadline(db, delivery_id, state):
            original(db, delivery_id, state)
            if state['status'] == 'IN_FLIGHT' and not expired:
                budget, platform = bounded._current.get()
                expired.append(platform)
                # Exercise the exact reservation/entry boundary, separately
                # from the unchanged real-CPU reproductions in E9/E10.
                budget.deadline = time.monotonic() - .001
        with patch.object(DeliveryOutbox, '_save', side_effect=cross_deadline):
            self.assertEqual(case.cycle('2026-11-11T10:00:00Z')['errors'], [])
        self.assertEqual(expired, ['slack'])
        self.assertEqual(len(case.network.posts), 1)
        first = case.status()['deliveries']
        self.assertEqual(len(first), 1)
        self.assertEqual(len(first[0]['attempts']), 1)
        case.restart()
        self.assertEqual(case.cycle('2026-11-11T10:00:01Z')['errors'], [])
        self.assertEqual(len(case.network.posts), 1)
        latest = case.status()['deliveries']
        self.assertEqual(latest[0]['status'], 'DELIVERED')
        self.assertEqual(latest[0]['attempts'], first[0]['attempts'])

    def test_multiple_owner_alerts_preserve_hourly_and_eod_admission(self):
        from copy import deepcopy
        import json
        import test_reporting_e6_regressions as e6
        from test_reference_controller_adapter import _issue
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.network.latency = .3
        case.helper.listed = [deepcopy(case.helper.current)]
        template = json.loads(case.runner.inventory[0]['body'])
        rows = []
        for number in range(32, 35):
            value = dict(template, ticket='AWF-%d' % number, disposition='BLOCKED', actor='owner',
                         reason='Urgent owner decision %d' % number, next_action='Investigate')
            rows.append(_issue(value, number=number))
        case.runner.inventory = rows
        self.assertEqual(case.cycle('2026-11-11T17:00:00Z')['errors'], [])
        status = case.status()
        periodic = {r['report_id'] for r in status['reports'] if r['kind'] in {'hourly', 'eod'}}
        self.assertEqual(len(periodic), 2)
        self.assertTrue(all(len(d['attempts']) == 1 for d in status['deliveries'] if d['report_id'] in periodic))
        alerts = {r['report_id'] for r in status['reports'] if r['kind'] == 'owner-action'}
        self.assertEqual(len(alerts), 3)
        for platform in ('teams', 'slack'):
            states = [d for d in status['deliveries'] if d['platform'] == platform and d['report_id'] in alerts]
            self.assertEqual(sum(d['status'] == 'DELIVERED' for d in states), 1)
            self.assertTrue(all(d['attempts'] == [] for d in states if d['status'] != 'DELIVERED'))
        # Teams needs 12 HTTP operations per confirmed report. Its fixed
        # 32-call cycle ceiling admits all three protected classes, leaving
        # the last readback for the next turn; it must never cause a repost.
        case.restart()
        self.assertEqual(case.cycle('2026-11-11T17:00:01Z')['errors'], [])
        status = case.status()
        self.assertTrue(all(d['status'] == 'DELIVERED' for d in status['deliveries'] if d['report_id'] in periodic))
        self.assertTrue(all(len(d['attempts']) == 1 for d in status['deliveries'] if d['report_id'] in periodic))

    def test_observer_grace_is_measured_from_entry_within_original_stage_ceiling(self):
        with patch('agentic.reporting.bounded.monotonic', return_value=100):
            budget = DeliveryBudget()
            with budget.checking():
                self.assertEqual(budget.join_deadline(threading.current_thread()), 115)
            # HTTP operations retain their full stage allowance.
            self.assertEqual(budget.join_deadline(threading.current_thread()), 145)
        with patch('agentic.reporting.bounded.monotonic', return_value=129):
            with budget.checking():
                self.assertEqual(budget.join_deadline(threading.current_thread()), 144)

    def test_policy_preparation_is_shared_only_within_cycle_and_post_stays_fresh(self):
        from agentic.reporting import policy
        from test_reporting_policy_review import PolicyReviewTests
        helper = PolicyReviewTests()
        self.addCleanup(helper.doCleanups)
        case = helper.fixture()
        from agentic import operating
        snapshot = operating.read_operating(case.root, case.gov)

        @policy.policy_cycle
        def cycle(change=False):
            current = policy.ReportingPolicy(case.root, case.gov, snapshot)
            with patch.object(policy, '_git', wraps=policy._git) as read:
                shared = policy.ReportingPolicy(case.root, case.gov, current.snapshot)
                self.assertEqual(read.call_count, 0)
                if change:
                    case.publish(case.enabled())
                value = shared()  # The pre-POST admission must still observe Git.
                self.assertGreater(read.call_count, 0)
                return value

        self.assertEqual(cycle()['reporting']['mode'], 'off')
        self.assertEqual(cycle(change=True)['reporting']['mode'], 'both')
        self.assertEqual(cycle()['reporting']['mode'], 'both')

    def test_review_policy_observes_default_calendar_change_after_fence(self):
        from copy import deepcopy
        from agentic.continuous_controller import ContinuousControllerStore, production_review_batching_cycle
        from agentic.operating import read_operating
        from agentic.reporting.policy import ReportingPolicy
        from agentic.review_batching import ReviewBatchStore
        from test_awf244_controller_wiring import candidate
        from test_reporting_policy_review import PolicyReviewTests
        helper = PolicyReviewTests()
        self.addCleanup(helper.doCleanups)
        case = helper.fixture()
        accepted = ReportingPolicy(case.root, case.gov, read_operating(case.root, case.gov)).snapshot
        store = ContinuousControllerStore(case.root / 'review.sqlite', ['A'])
        now = '2026-11-11T10:00:00Z'
        ReviewBatchStore(store).enqueue(candidate(), now, {})
        def observe(value, current):
            return dict(candidate=value, observed_at=current, expires_at='2026-11-11T10:05:00Z',
                        open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        def close_window():
            changed = deepcopy(case.value)
            changed['operating_calendar']['review_batching']['cutoff_time'] = '09:00'
            case.publish(changed)
        sent = []
        result = production_review_batching_cycle(store, calendar={}, repository_root=case.root,
            config=case.gov, operating_snapshot=accepted,
            binding=dict(project_id=case.gov['project']['id'], repository_id='101', scope_sha256='a'*64),
            clock=lambda: now, load_calendar=close_window, observe_review_candidate=observe,
            deliver_review_batch=lambda payload: sent.append(payload), observe_review_delivery=lambda payload: None)
        self.assertEqual(sent, [])
        self.assertTrue(any(e['operation'] == 'review_recheck' for e in result['errors']))

    def test_retained_reporting_budget_deferral_is_not_a_validation_failure(self):
        from agentic import ValidationError
        from agentic.continuous_controller import ContinuousControllerStore, production_review_batching_cycle
        from agentic.operating import read_operating
        from agentic.reporting.policy import ReportingPolicy
        from agentic.reporting.runtime import ReviewReportingPort, reporting_status
        from agentic.review_batching import ReviewBatchStore
        from test_awf244_controller_wiring import candidate
        from test_reporting_policy_review import PolicyReviewTests
        helper = PolicyReviewTests()
        self.addCleanup(helper.doCleanups)
        case = helper.fixture()
        case.publish(case.enabled())
        accepted = ReportingPolicy(case.root, case.gov, read_operating(case.root, case.gov)).snapshot
        store = ContinuousControllerStore(case.root / 'review.sqlite', ['A'])
        now = '2026-11-11T10:00:00Z'
        batches = ReviewBatchStore(store)
        batches.enqueue(candidate(), now, {})
        destination = accepted.config['reporting']['bindings'][0]
        binding = {key: destination[key] for key in ('project', 'repository', 'scope_revision')}
        port = ReviewReportingPort(store, accepted.config, binding, {}, lambda: now)
        def observe(value, current):
            return dict(candidate=value, observed_at=current, expires_at='2026-11-11T10:05:00Z',
                        open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        def defer(payload):
            port._retain_review_batch(payload)
            port._defer(port._id(payload), 'REPORT_DESTINATION_BUDGET')
            raise ValidationError('REPORT_DESTINATION_BUDGET')
        result = production_review_batching_cycle(store, calendar={}, repository_root=case.root,
            config=case.gov, operating_snapshot=accepted,
            binding=dict(project_id=binding['project'], repository_id=binding['repository'],
                         scope_sha256=binding['scope_revision']), clock=lambda: now,
            observe_review_candidate=observe, deliver_review_batch=defer, observe_review_delivery=lambda payload: None)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['state'], 'DEFERRED')
        status = reporting_status(store, accepted.config, binding)
        self.assertEqual(len(status['reports']), 1)
        self.assertTrue(all(d['status'] == 'PENDING' and d['attempts'] == [] for d in status['deliveries']))
        self.assertEqual(batches.pending_batches(), [status['reports'][0]['review_batch']])


class ReportingResumeTimingTests(unittest.TestCase):
    def setUp(self):
        from test_reporting_wiring import ReportingRuntimeTests
        ReportingRuntimeTests.setUp(self)
        self.now = '2026-11-11T16:59:59Z'
        self.observed = []
        from test_awf244_controller_wiring import candidate
        from agentic.review_batching import ReviewBatchStore
        self.batches = ReviewBatchStore(self.store)
        self.batches.enqueue(candidate(), self.now, {})
        self.payload = self.batches.claim(self.now, {}, lambda c: self.observe(c, self.now))
        self.batches.recheck(self.payload['batch_id'], self.now, {}, lambda c: self.observe(c, self.now))
        self.batches.begin_delivery(self.payload['batch_id'], self.now)

    def observe(self, candidate, now):
        from datetime import timedelta
        from agentic.canonical import timestamp
        self.observed.append(candidate)
        return dict(candidate=candidate, observed_at=now,
                    expires_at=(timestamp(now) + timedelta(minutes=5)).isoformat(),
                    open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)

    def port(self, budget=None):
        from agentic.reporting.runtime import ReviewReportingPort
        return ReviewReportingPort(self.store, self.root, self.binding, self.providers, lambda: self.now,
                                   observe_candidate=self.observe, delivery_budget=budget,
                                   load_operating=lambda: self.root)

    def test_closed_window_resume_defers_without_observation_send_or_attempt(self):
        from agentic.canonical import loads
        from agentic.reporting.runtime import reporting_status
        port = self.port()
        port._retain_review_batch(self.payload)
        before = reporting_status(self.store, self.root, self.binding)['deliveries']
        self.observed.clear()
        self.now = '2026-11-11T17:00:00Z'
        self.assertIsNone(port.resume({}, self.observe))
        self.assertEqual(self.observed, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(reporting_status(self.store, self.root, self.binding)['deliveries'], before)
        with self.store.connection() as db:
            record = loads(db.execute('SELECT value FROM controller_meta WHERE key=?',
                ('report-review-deferred:review-' + self.payload['batch_id'],)).fetchone()[0])
        self.assertEqual(record['reason'], 'REPORT_REVIEW_WINDOW_EXHAUSTED')
        self.assertEqual(record['retry_at'], '2026-11-12T07:00:00Z')
        self.assertEqual(self.batches.pending_batches(), [self.payload])

    def test_unretained_claim_resume_preserves_identity_when_budget_is_revoked(self):
        from agentic.canonical import loads
        budget = DeliveryBudget()
        budget.cancel()
        self.assertIsNone(self.port(budget).resume({}, self.observe))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.batches.pending_batches(), [self.payload])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM controller_reports').fetchone()[0], 0)
            record = loads(db.execute('SELECT value FROM controller_meta WHERE key=?',
                ('report-review-deferred:review-' + self.payload['batch_id'],)).fetchone()[0])
        self.assertEqual(record['reason'], 'REPORT_DESTINATION_BUDGET')
