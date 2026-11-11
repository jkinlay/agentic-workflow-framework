"""Deadline and progress regressions through offline ports and controller seams."""
import threading
from time import sleep as _slow_sleep
import unittest
from unittest.mock import patch

import source_only
from agentic.reporting.bounded import DeliveryBudget


class _FixtureNamespace:
    """Override one module consumer without mutating the shared module."""
    def __init__(self, module, **overrides):
        self.module = module
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self.module, name)


class _LogicalSlowCalls:
    """Model independent joined call timelines; exclude CPU/storage scheduling.

    Each child inherits its parent's elapsed time. A join merges the later
    completion, so concurrent destination calls overlap rather than add up.
    Budget code sees the original 30/45-second deadlines and exact call delays.
    Host subprocess polling, pipe readers and other infrastructure stay native.
    """
    def __init__(self, *fixture_modules):
        self.local = threading.local()
        self.fixture_modules = fixture_modules

    def now(self):
        return getattr(self.local, 'elapsed', 0.0)

    def sleep(self, seconds):
        self.local.elapsed = self.now() + seconds

    def __enter__(self):
        from contextlib import ExitStack
        from contextvars import Context
        from agentic.reporting import deadline
        from agentic.reporting.http_transport import BaseTransport
        import test_reporting_e3_regressions as e3
        clock = self
        original = threading.Thread
        workers = {
            ('agentic.reporting.runtime', 'drain.<locals>.run_destination'),
            ('agentic.continuous_controller',
             'production_controller_cycle.<locals>.periodic_observations'),
        }
        class JoinedThread(original):
            def start(self):
                self.started_at = clock.now()
                self.finished_at = self.started_at
                # Execute finite fixture calls on independent logical timelines.
                # Native scheduling must not decide which simulated call wins.
                try:
                    Context().run(self.run)
                finally:
                    clock.local.elapsed = self.started_at

            def run(self):
                clock.local.elapsed = self.started_at
                try:
                    return super().run()
                finally:
                    self.finished_at = clock.now()

            def join(self, timeout=None):
                clock.local.elapsed = max(clock.now(), self.finished_at)

        class ReportingThread(JoinedThread):
            def __new__(cls, *args, **kwargs):
                target = kwargs.get('target', args[1] if len(args) > 1 else None)
                identity = (getattr(target, '__module__', None),
                            getattr(target, '__qualname__', None))
                if identity not in workers:
                    return original(*args, **kwargs)
                return super().__new__(cls)

        class LogicalTimer:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            cancel = join = start
        original_transport = BaseTransport.__init__
        # E7 profiles native C-call sleep identities to reject blocking
        # Retry-After handling. Keep that detector and transport pause native;
        # its HTTP fixture latency still uses e3's separate logical sleeper.
        native_sleep_modules = {module for module in self.fixture_modules
            if getattr(module, '__file__', '').replace('\\', '/').rsplit('/', 1)[-1]
            == 'test_reporting_e7_regressions.py'}
        def transport(instance, *args, **kwargs):
            # BaseTransport captured sleep in its default argument at import.
            if not native_sleep_modules:
                kwargs['pause'] = clock.sleep
            return original_transport(instance, *args, **kwargs)
        self.stack = ExitStack()
        for name, value in [('agentic.reporting.bounded.monotonic', self.now),
                            ('agentic.reporting.runtime.monotonic', self.now),
                            ('agentic.reporting.deadline.monotonic', self.now),
                            (__name__ + '._slow_sleep', self.sleep),
                            ('agentic.reporting.deadline.threading',
                             _FixtureNamespace(deadline.threading, Timer=LogicalTimer)),
                            ('threading.Thread', ReportingThread),
                            ('agentic.reporting.http_transport.BaseTransport.__init__', transport)]:
            self.stack.enter_context(patch(name, value))
        # Patch each fixture's reference, never attributes on stdlib time:
        # subprocess polling and host infrastructure must keep native clocks.
        for module in dict.fromkeys((e3, *self.fixture_modules)):
            if module in native_sleep_modules:
                continue
            if hasattr(module, 'time'):
                self.stack.enter_context(patch.object(module, 'time',
                    _FixtureNamespace(module.time, sleep=self.sleep, monotonic=self.now)))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def _logical_slow_calls(test):
    from functools import wraps
    import sys
    @wraps(test)
    def run(self):
        with _LogicalSlowCalls(sys.modules[test.__module__]):
            return test(self)
    return run


def _host_subprocess_waiter(polls):
    """Model POSIX waitpid polling without changing its host clock or sleeper."""
    import time
    delay = .0005
    for _ in range(polls):
        delay = min(delay * 2, .05)
        time.sleep(delay)


class ReportingTimingTests(unittest.TestCase):
    def test_host_subprocess_polling_never_advances_reporting_clock(self):
        # Exercise zero, one and repeated host sleeps. Their count cannot
        # advance the separate reporting clock; large counts only wait longer.
        for polls in (0, 1, 2, 3):
            with self.subTest(polls=polls), _LogicalSlowCalls() as clock:
                before = clock.now()
                _host_subprocess_waiter(polls)
                self.assertEqual(clock.now() - before, 0)

    @_logical_slow_calls
    def test_budget_expiring_before_retention_releases_only_the_live_uninvoked_claim(self):
        from copy import deepcopy
        from agentic import ValidationError
        from agentic.reporting.runtime import ReviewReportingPort
        from agentic.review_batching import ReviewBatchStore
        import test_reporting_e6_regressions as e6
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.helper.listed = [deepcopy(case.helper.current)]
        with patch.object(ReviewReportingPort, '_retain_review_batch',
                          side_effect=ValidationError('REPORT_DESTINATION_BUDGET')):
            result = case.cycle('2026-11-11T16:00:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['review_batching']['state'], 'DEFERRED')
        self.assertEqual(result['review_batching']['owner_alerts'], [])
        self.assertEqual(case.network.posts, [])
        self.assertEqual(ReviewBatchStore(case.helper.store).pending_batches(), [])
        case.restart()
        self.assertEqual(case.cycle('2026-11-11T16:00:01Z')['errors'], [])
        reviews = {r['report_id'] for r in case.status()['reports'] if r['kind'] == 'final-human-review'}
        self.assertTrue(reviews)
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1
                            for d in case.status()['deliveries'] if d['report_id'] in reviews))

    @_logical_slow_calls
    def test_eight_slow_teams_turns_progress_hourly_eod_and_review(self):
        from copy import deepcopy
        import json
        import time
        import test_reporting_e6_regressions as e6
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.helper.calendar = {'review_batching': {'cutoff_time': '18:00'}}
        case.network.latency = 0
        original_http = type(case.network).__call__
        def http(network, method, url, headers, payload):
            _slow_sleep(1.5 if 'slack.com' in url else 2.2)
            return original_http(network, method, url, headers, payload)
        original_observe = case.helper.observe_candidate
        def observe(candidate, now):
            _slow_sleep(2.5)
            return dict(original_observe(candidate, now), candidate=deepcopy(candidate))
        case.helper.observe_candidate = observe
        turns, errors = [], []
        with patch.object(type(case.network), '__call__', http):
            for second in range(8):
                case.restart()
                case.helper.current['pull_request'] = 41 + second
                case.helper.listed = [deepcopy(case.helper.current)]
                result = case.cycle('2026-11-11T17:00:0%dZ' % second)
                self.assertEqual(result['operating_calendar']['review_batching']['cutoff_time'], '18:00')
                self.assertEqual(result['operating_calendar']['eod']['time'], '17:00')
                errors.extend(result['errors'])
                status = case.status()
                kinds = {r['report_id']: r['kind'] for r in status['reports']}
                turns.append({platform: {
                    kind: [dict(status=d['status'], attempts=len(d['attempts']))
                           for d in status['deliveries'] if d['platform'] == platform
                           and kinds[d['report_id']] == kind]
                    for kind in ('hourly', 'eod', 'final-human-review')}
                    for platform in ('teams', 'slack')})
        print('eight slow platform turns: ' + json.dumps(turns, sort_keys=True))
        self.assertEqual(errors, [])
        for platform in ('teams', 'slack'):
            with self.subTest(platform=platform):
                self.assertTrue(any(d['attempts'] for turn in turns[:2]
                                    for d in turn[platform]['hourly']), turns)
                for kind in ('hourly', 'eod', 'final-human-review'):
                    self.assertTrue(any(d['status'] == 'DELIVERED'
                                        for d in turns[-1][platform][kind]), turns)
                self.assertTrue(all(d['status'] == 'DELIVERED' for kind in ('hourly', 'eod')
                                    for d in turns[-1][platform][kind]), turns)
                self.assertTrue(all(d['attempts'] <= 2 for turn in turns
                                    for states in turn[platform].values() for d in states))

    def sustained_periodic_arrivals(self, turn_count=28):
        from copy import deepcopy
        from datetime import datetime, timedelta, timezone
        import json
        import time
        import test_reporting_e6_regressions as e6
        import test_reporting_e7_regressions as e7
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.helper.calendar = {'review_batching': {'cutoff_time': '23:59'}}
        case.network.latency = 0
        original_http = type(case.network).__call__
        def http(network, method, url, headers, payload):
            _slow_sleep(1.5 if 'slack.com' in url else 2.2)
            return original_http(network, method, url, headers, payload)
        original_observe = case.helper.observe_candidate
        def observe(candidate, now):
            _slow_sleep(2.5)
            return dict(original_observe(candidate, now), candidate=deepcopy(candidate))
        case.helper.observe_candidate = observe
        turns = []
        with _LogicalSlowCalls() as clock, patch.object(type(case.network), '__call__', http):
            for turn in range(turn_count):
                case.restart()
                case.helper.current['pull_request'] = 41 + turn
                case.helper.listed = [deepcopy(case.helper.current)]
                e7.E7ControllerTests.owner_alert(case, 'New owner action %d' % turn)
                now = datetime(2026, 11, 11, 8, tzinfo=timezone.utc) + timedelta(hours=turn)
                started = clock.now()
                result = case.cycle(now.strftime('%Y-%m-%dT%H:00:00Z'))
                self.assertEqual(result['operating_calendar']['review_batching']['cutoff_time'], '23:59')
                self.assertEqual(result['operating_calendar']['eod']['time'], '17:00')
                status = case.status()
                reports = {r['report_id']: r for r in status['reports']}
                turns.append(dict(now=now.isoformat(), elapsed=clock.now() - started, errors=result['errors'],
                    deliveries=[dict(platform=d['platform'], kind=reports[d['report_id']]['kind'],
                        report_id=d['report_id'], status=d['status'], attempts=len(d['attempts']),
                        posts=sum(platform == d['platform'] and
                                  ('AWF delivery: ' + d['delivery_id'] + ' / ') in str(payload)
                                  for platform, payload in case.network.posts))
                        for d in status['deliveries']]))
        print('continuous hourly/alert turns: ' + json.dumps([
            dict(now=t['now'], elapsed=t['elapsed'], errors=t['errors'],
                 eod=[d for d in t['deliveries'] if d['kind'] == 'eod']) for t in turns], sort_keys=True))
        self.assertTrue(all(d['attempts'] <= 2 for t in turns for d in t['deliveries']))
        for platform in ('teams', 'slack'):
            for kind in ('hourly', 'final-human-review'):
                self.assertTrue(any(d['platform'] == platform and d['kind'] == kind
                                    and d['status'] == 'DELIVERED' for d in turns[-1]['deliveries']))
        return turns

    def test_continuous_hourly_alert_arrivals_keep_eod_opportunity(self):
        turns = self.sustained_periodic_arrivals()
        # Six turns to POST applies to this asymmetric scenario only: Teams
        # 2.2 s, Slack 1.5 s, observations 2.5 s, and one new alert, candidate
        # and hourly per restarted turn. EoD first becomes due on turn 9;
        # the six turns include that due turn.
        for platform in ('teams', 'slack'):
            with self.subTest(platform=platform):
                self.assertTrue(any(d['platform'] == platform and d['kind'] == 'eod' and d['attempts']
                                    for t in turns[9:15] for d in t['deliveries']), turns)
        self.assertEqual([error for turn in turns for error in turn['errors']], [])

    def test_boundary_burst_eod_admission_and_readback_keep_one_identity(self):
        """Both platforms take 2.2 s/call with eight new owner actions per turn.

        Start RETRY/PERIODIC, with the first due EoD and no older EoD debt.
        Restart with a new candidate/hourly each turn; observations take 2.5 s.
        Admission takes at most seven turns, then readback at most four more.
        These bounds do not cover other conditions.
        """
        from copy import deepcopy
        from datetime import datetime, timedelta, timezone
        import json
        import time
        import test_reporting_e6_regressions as e6
        from test_reference_controller_adapter import _issue
        case = e6.ControllerTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.helper.calendar = {'review_batching': {'cutoff_time': '23:59'}}
        case.network.latency = 2.2
        template = json.loads(case.runner.inventory[0]['body'])
        case.runner.inventory = []
        original_observe = case.helper.observe_candidate
        def observe(candidate, now):
            _slow_sleep(2.5)
            return dict(original_observe(candidate, now), candidate=deepcopy(candidate))
        case.helper.observe_candidate = observe
        with case.helper.store.transaction() as db:
            for key, value in (('report-review-work-turn', 'RETRY'),
                               ('report-periodic-reconciliation-turn', 'PERIODIC')):
                db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) '
                           'DO UPDATE SET value=excluded.value', (key, value))
        identities, turns = {}, []
        with _LogicalSlowCalls():
            for turn in range(11):
                case.restart()
                case.helper.current['pull_request'] = 41 + turn
                case.helper.listed = [deepcopy(case.helper.current)]
                case.runner.inventory.extend([
                    _issue(dict(template, ticket='AWF-%d' % (32 + 8 * turn + n),
                                disposition='BLOCKED', actor='owner',
                                reason='Storm turn %d action %d' % (turn, n),
                                next_action='Investigate'), number=32 + 8 * turn + n)
                    for n in range(8)])
                now = datetime(2026, 11, 11, 17, tzinfo=timezone.utc) + timedelta(hours=turn)
                self.assertEqual(case.cycle(now.strftime('%Y-%m-%dT%H:00:00Z'))['errors'], [])
                status = case.status()
                self.assertEqual(len(case.runner.inventory), 8 * (turn + 1))
                self.assertEqual(sum(r.get('owner_action_event', {}).get('record', {}).get('action', {})
                                     .get('reason', '').startswith('Storm turn ') for r in status['reports']),
                                 8 * (turn + 1))
                self.assertEqual(sum(r['kind'] == 'eod' for r in status['reports']), 1)
                self.assertTrue(all(len(d['attempts']) <= 2 for d in status['deliveries']))
                first_eod = next(r['report_id'] for r in status['reports'] if r['kind'] == 'eod')
                states = {}
                for delivery in status['deliveries']:
                    if delivery['report_id'] != first_eod:
                        continue
                    platform = delivery['platform']
                    with case.helper.store.connection() as db:
                        intent = db.execute('SELECT intent_json FROM controller_report_delivery '
                                            'WHERE delivery_id=?', (delivery['delivery_id'],)).fetchone()[0]
                    identity = (first_eod, delivery['delivery_id'], intent)
                    self.assertEqual(identity, identities.setdefault(platform, identity))
                    posts = sum(p == platform and ('AWF delivery: ' + delivery['delivery_id'] + ' / ')
                                in str(payload) for p, payload in case.network.posts)
                    states[platform] = dict(status=delivery['status'], attempts=len(delivery['attempts']), posts=posts)
                self.assertEqual(set(states), {'teams', 'slack'})
                turns.append(states)
        print('boundary burst EoD turns: ' + json.dumps(turns, sort_keys=True))
        for platform in ('teams', 'slack'):
            with self.subTest(platform=platform):
                admitted = [i for i, states in enumerate(turns) if states[platform]['posts']]
                self.assertTrue(admitted, turns)
                first = admitted[0]
                self.assertLess(first, 7, turns)
                self.assertTrue(any(states[platform]['status'] == 'DELIVERED'
                                    for states in turns[first:first + 5]), turns)
                self.assertTrue(all(states[platform]['posts'] == 1 and states[platform]['attempts'] == 1
                                    for states in turns[first:]), turns)
                self.assertEqual(turns[-1][platform]['status'], 'DELIVERED')

    def test_elapsed_eod_readback_continues_without_repost_under_catchup(self):
        turns = self.sustained_periodic_arrivals()
        unknown = [(i, d) for i, t in enumerate(turns) for d in t['deliveries']
                   if d['platform'] == 'teams' and d['kind'] == 'eod'
                   and d['status'] == 'UNKNOWN' and d['attempts'] == 1]
        self.assertTrue(unknown, turns)
        first, delivery = unknown[0]
        # Logical HTTP time consumed the shared admission deadline; a later
        # cycle must read the retained send back ahead of older hourly work.
        self.assertGreaterEqual(turns[first]['elapsed'], 30)
        identity = delivery['report_id']
        self.assertTrue(any(d['report_id'] == identity and d['platform'] == 'teams'
                            and d['status'] == 'DELIVERED'
                            for t in turns[first + 1:first + 5] for d in t['deliveries']), turns)
        self.assertTrue(all(d['attempts'] == 1 for t in turns[first:] for d in t['deliveries']
                            if d['report_id'] == identity and d['platform'] == 'teams'))
        self.assertTrue(all(d['posts'] == 1 for t in turns[first:] for d in t['deliveries']
                            if d['report_id'] == identity and d['platform'] == 'teams'))
        self.assertEqual([error for turn in turns for error in turn['errors']], [])

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

    @_logical_slow_calls
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
            _slow_sleep(2.5)
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

    @_logical_slow_calls
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
    def test_unknown_resume_reconciles_before_new_work_even_after_cutoff(self):
        from copy import deepcopy
        from agentic.reporting.runtime import reporting_status
        for provider in self.providers.values():
            original = provider.reconcile
            provider.reconcile = lambda intent, attempts, original=original: dict(
                original(intent, attempts), observed_at=self.now)
        self.port().deliver_review_batch(self.payload)
        before = deepcopy(reporting_status(self.store, self.root, self.binding)['deliveries'])
        self.assertEqual({d['status'] for d in before}, {'UNKNOWN', 'DELIVERED'})
        posts = deepcopy(self.sent)
        self.visible_platforms = {'slack', 'teams'}
        self.observed.clear()
        self.now = '2026-11-11T17:00:00Z'
        budget = DeliveryBudget()
        self.port(budget).resume({}, self.observe)
        after = reporting_status(self.store, self.root, self.binding)['deliveries']
        self.assertEqual({d['status'] for d in after}, {'DELIVERED'})
        self.assertEqual({d['delivery_id']: d['attempts'] for d in before},
                         {d['delivery_id']: d['attempts'] for d in after})
        self.assertTrue(all(len(d['attempts']) == 1 for d in after))
        self.assertEqual(self.sent, posts)
        self.assertEqual(self.observed, [])
        self.assertEqual(budget.reconciliations, {'teams': 1})

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
