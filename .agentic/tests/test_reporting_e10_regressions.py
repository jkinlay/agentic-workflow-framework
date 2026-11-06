"""Pre-E10 regressions: real controller/CLI, restarts and production HTTP."""
from copy import deepcopy
from datetime import timedelta
import time
import unittest
from urllib.error import HTTPError

import test_reporting_e3_regressions as e3
import test_reporting_e6_regressions as e6
import test_reporting_e7_regressions as e7
from agentic.canonical import timestamp, loads, fingerprint
from agentic.reporting.bounded import source_timeout
from agentic.reporting.runtime import ReviewReportingPort
from agentic.review_batching import ReviewBatchStore


class E10Tests(unittest.TestCase):
    setUp = e6.ControllerTests.setUp
    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status
    restart = e6.ControllerTests.restart

    def alert_probe(self, case, run):
        case.network.latency = .3
        now = '2026-11-11T10:00:01Z'
        case.clock.value = now
        def observe(candidate, current):
            for _ in range(3):
                time.sleep(min(1.6, source_timeout()))
                source_timeout()
            return dict(candidate=deepcopy(candidate), observed_at=current,
                expires_at=(timestamp(current) + timedelta(minutes=5)).isoformat(),
                open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        # Historical five-member payload, made through the real batch store.
        batches = ReviewBatchStore(case.helper.store)
        fast = lambda c: dict(candidate=c, observed_at=now, expires_at='2026-11-11T10:05:00Z',
            open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        for n in range(41, 46):
            batches.enqueue(dict(case.helper.current, pull_request=n), now, {})
        payload = batches.claim(now, {}, fast)
        def reject(method, url):
            if method == 'POST':
                raise HTTPError(url, 400, 'Synthetic rejection', {}, None)
        case.network.before_call = reject
        providers = case.adapters['configure_reporting']({'reporting': case.settings}, case.binding)
        port = ReviewReportingPort(case.helper.store, {'reporting': case.settings}, case.binding,
                                   providers, lambda: now)
        self.assertIsNone(port.deliver_review_batch(payload))
        case.network.before_call = None
        case.helper.listed = []
        case.helper.observe_candidate = observe
        if case is not self:
            e3.CLI_ADAPTERS['observe_review_candidate'] = observe
        e7.E7ControllerTests.owner_alert(case, 'Urgent owner decision')
        for second in (2, 3):
            e6.ControllerTests.restart(case)
            started = time.monotonic()
            run('2026-11-11T10:00:0%dZ' % second)
            self.assertLess(time.monotonic() - started, 45)
            alerts = {r['report_id'] for r in case.status()['reports'] if r['kind'] == 'owner-action'
                      and 'Urgent owner decision' in str(r)}
            self.assertTrue(alerts)
            states = [d for d in case.status()['deliveries'] if d['report_id'] in alerts]
            self.assertEqual({d['platform'] for d in states}, {'teams', 'slack'})
            self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1 for d in states))

    def failure_probe(self, case, run, failing):
        case.network.latency = .3
        case.helper.listed = [deepcopy(case.helper.current)]
        def reject(method, url):
            platform = 'slack' if 'slack.com' in url else 'teams'
            if method == 'POST' and platform in failing:
                raise HTTPError(url, 400, 'Synthetic rejection', {}, None)
        case.network.before_call = reject
        run('2026-11-11T10:00:00Z')
        old = next(r for r in case.status()['reports'] if r['kind'] == 'final-human-review')
        e6.ControllerTests.restart(case)
        run('2026-11-11T10:00:01Z')
        old_states = [d for d in case.status()['deliveries'] if d['report_id'] == old['report_id']]
        self.assertEqual({d['status'] for d in old_states if d['platform'] in failing}, {'FAILED'})
        self.assertTrue(all(len(d['attempts']) == (2 if d['platform'] in failing else 1) for d in old_states))
        with case.helper.store.connection() as db:
            row = db.execute('SELECT state,receipt_json FROM review_presentation_outbox_v1 WHERE batch_id=?',
                             (old['review_batch']['batch_id'],)).fetchone()
        self.assertEqual(row['state'], 'FAILED')
        self.assertEqual(loads(row['receipt_json'])['status'], 'FAILED')
        case.network.before_call = None
        case.helper.current['head_sha'] = 'e' * 40
        case.helper.listed = [deepcopy(case.helper.current)]
        for second in (2, 3):
            e6.ControllerTests.restart(case)
            run('2026-11-11T10:00:0%dZ' % second)
        reviews = [r for r in case.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual(len(reviews), 2)
        newest = reviews[-1]
        self.assertTrue(all(d['status'] == 'DELIVERED' for d in case.status()['deliveries']
                            if d['report_id'] == newest['report_id']))
        self.assertEqual([d for d in case.status()['deliveries'] if d['report_id'] == old['report_id']], old_states)
        alerts = {r['report_id'] for r in case.status()['reports'] if r['kind'] == 'owner-action'
                  and old['report_id'] in str(r)}
        self.assertTrue(alerts)
        self.assertTrue(all(d['status'] == 'DELIVERED' for d in case.status()['deliveries'] if d['report_id'] in alerts))
        e6.ControllerTests.restart(case)
        run('2026-11-11T11:00:00Z')
        hourly = [r for r in case.status()['reports'] if r['kind'] == 'hourly'][-1]
        self.assertIn(old['report_id'], str(hourly['body']))
        self.assertIn('FAILED', str(hourly['body']))

    def test_alert_first_cycle_with_slow_historical_review(self):
        self.alert_probe(self, self.cycle)

    def test_exhausted_both_destinations_release_claim(self):
        self.failure_probe(self, self.cycle, {'teams', 'slack'})

    def test_exhausted_teams_preserves_slack_and_new_head(self):
        self.failure_probe(self, self.cycle, {'teams'})

    def test_uncertain_old_destination_does_not_block_new_candidate(self):
        self.network.latency = .3
        self.helper.listed = [deepcopy(self.helper.current)]
        self.network.timeout_post = lambda platform, payload: platform == 'teams' and 'Ready for final human review' in str(payload)
        self.cycle('2026-11-11T10:00:00Z')
        old = next(r for r in self.status()['reports'] if r['kind'] == 'final-human-review')
        prior = [d for d in self.status()['deliveries'] if d['report_id'] == old['report_id']]
        self.assertEqual({d['platform']: d['status'] for d in prior}, {'teams': 'UNKNOWN', 'slack': 'DELIVERED'})
        self.network.timeout_post = lambda platform, payload: False
        self.helper.current['head_sha'] = 'e' * 40
        self.helper.listed = [deepcopy(self.helper.current)]
        for second in (1, 2, 3):
            self.restart()
            self.cycle('2026-11-11T10:00:0%dZ' % second)
        reviews = [r for r in self.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual(len(reviews), 2)
        self.assertTrue(all(d['status'] == 'DELIVERED' for d in self.status()['deliveries']
                            if d['report_id'] == reviews[-1]['report_id']))
        old_teams = next(d for d in self.status()['deliveries'] if d['report_id'] == old['report_id'] and d['platform'] == 'teams')
        self.assertEqual(old_teams['status'], 'UNKNOWN')
        self.assertEqual(old_teams['attempts'], next(d['attempts'] for d in prior if d['platform'] == 'teams'))

    def test_claim_without_report_recovers_same_identity_after_restart(self):
        self.network.latency = .3
        now = '2026-11-11T10:00:00Z'
        batches = ReviewBatchStore(self.helper.store)
        batches.enqueue(self.helper.current, now, {})
        payload = batches.claim(now, {}, lambda c: self.helper.observe_candidate(c, now))
        # Crash after the durable send boundary, before report retention.
        # A PREPARED-only claim now has proof of no send and is released by
        # AWF-244; this unresolved claim must retain its original identity.
        batches.recheck(payload['batch_id'], now, {}, lambda c: self.helper.observe_candidate(c, now))
        batches.begin_delivery(payload['batch_id'], now)
        self.assertEqual(self.status()['reports'], [])
        self.helper.listed = []
        for second in (1, 2):
            self.restart()
            self.assertEqual(self.cycle('2026-11-11T10:00:0%dZ' % second)['errors'], [])
        reviews = [r for r in self.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual([r['review_batch'] for r in reviews], [payload])
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1
                            for d in self.status()['deliveries'] if d['report_id'] == reviews[0]['report_id']))

    def test_review_readback_progresses_during_continuous_periodic_uncertainty(self):
        self.network.latency = .3
        self.helper.listed = [deepcopy(self.helper.current)]
        self.network.message_filter = lambda platform, messages: []
        self.cycle('2026-11-11T10:00:00Z')
        review = next(r for r in self.status()['reports'] if r['kind'] == 'final-human-review')
        self.assertEqual({d['status'] for d in self.status()['deliveries']
                          if d['report_id'] == review['report_id']}, {'UNKNOWN'})
        self.network.message_filter = lambda platform, messages: [m for m in messages
            if 'Ready for final human review' in str(m)]
        self.helper.listed = []
        for hour in (11, 12, 13):
            self.restart()
            self.assertEqual(self.cycle('2026-11-11T%d:00:00Z' % hour)['errors'], [])
        states = [d for d in self.status()['deliveries'] if d['report_id'] == review['report_id']]
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1 for d in states))

    def test_pending_observer_failure_rotates_without_blocking_other_candidates(self):
        self.network.latency = .3
        now = '2026-11-11T10:00:00Z'
        candidates = [dict(self.helper.current, pull_request=n) for n in (41, 42)]
        candidates.sort(key=lambda c: fingerprint('review-presentation-intent-v1', c))
        bad, healthy = candidates
        batches = ReviewBatchStore(self.helper.store)
        for candidate in candidates:
            batches.enqueue(candidate, now, {})
        self.helper.listed = []
        original = self.helper.observe_candidate
        def observe(candidate, current):
            if candidate == bad:
                raise RuntimeError('Synthetic observation unavailable')
            return dict(original(candidate, current), candidate=deepcopy(candidate))
        self.helper.observe_candidate = observe
        for second in (1, 2):
            self.restart()
            self.cycle('2026-11-11T10:00:0%dZ' % second)
        reviews = [r for r in self.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual([m['candidate'] for r in reviews for m in r['review_batch']['members']], [healthy])
        self.assertTrue(all(d['status'] == 'DELIVERED' for d in self.status()['deliveries']
                            if d['report_id'] == reviews[0]['report_id']))

    def test_two_second_alert_transport_gets_full_turn_despite_unresolved_review(self):
        self.network.latency = .3
        self.helper.listed = [deepcopy(self.helper.current)]
        self.network.message_filter = lambda platform, messages: [m for m in messages
            if 'Ready for final human review' not in str(m)]
        self.cycle('2026-11-11T10:00:00Z')
        self.helper.listed = []
        self.network.latency = 2
        e7.E7ControllerTests.owner_alert(self, 'Urgent decision with slow healthy transport')
        for second in (1, 2):
            self.restart()
            started = time.monotonic()
            self.cycle('2026-11-11T10:00:0%dZ' % second)
            self.assertLess(time.monotonic() - started, 45)
        alerts = {r['report_id'] for r in self.status()['reports'] if r['kind'] == 'owner-action'}
        states = [d for d in self.status()['deliveries'] if d['report_id'] in alerts]
        self.assertEqual({d['platform'] for d in states}, {'teams', 'slack'})
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1 for d in states))

    def test_alert_hourly_eod_and_review_continue_under_sustained_arrivals(self):
        self.network.latency = .3
        self.helper.listed = [deepcopy(self.helper.current)]
        # Distinct candidates, simultaneous hourly/EoD catch-up, new urgent
        # actions and restarts exercise all admissions without fake send ports.
        original = self.helper.observe_candidate
        self.helper.observe_candidate = lambda c, n: dict(original(c, n), candidate=deepcopy(c))
        for day in range(11, 14):
            self.restart()
            self.helper.current['pull_request'] = day
            self.helper.listed = [deepcopy(self.helper.current)]
            e7.E7ControllerTests.owner_alert(self, 'Urgent owner action %d' % day)
            self.assertEqual(self.cycle('2026-11-%dT16:59:59Z' % day)['errors'], [])
            self.assertEqual(self.cycle('2026-11-%dT17:00:00Z' % day)['errors'], [])
        status = self.status()
        for kind in ('owner-action', 'hourly', 'eod', 'final-human-review'):
            reports = [r for r in status['reports'] if r['kind'] == kind and r['delivery_ids']]
            self.assertTrue(reports, kind)
            self.assertTrue(all(d['status'] == 'DELIVERED' for r in reports for d in status['deliveries']
                                if d['report_id'] == r['report_id']), kind)


class E10CliTests(unittest.TestCase):
    def probe(self, probe):
        def inspect(case, result, output):
            probe(self, case, lambda now: e7.E7CliTests.rerun(self, case, now))
        e6.CliTests.probe(self, lambda case, net: None, inspect)

    def test_cli_alert_first_cycle_with_slow_historical_review(self):
        self.probe(E10Tests.alert_probe)

    def test_cli_terminal_failure_releases_claim(self):
        self.probe(lambda owner, case, run: E10Tests.failure_probe(owner, case, run, {'teams', 'slack'}))

    def test_cli_terminal_teams_failure_preserves_slack(self):
        self.probe(lambda owner, case, run: E10Tests.failure_probe(owner, case, run, {'teams'}))


if __name__ == '__main__':
    unittest.main()
