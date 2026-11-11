"""Pre-E9 regressions through production cycles and the pinned CLI loader."""
from copy import deepcopy
from datetime import timedelta
import time
import unittest

import test_reporting_e6_regressions as e6
import test_reporting_e7_regressions as e7
import test_reporting_e3_regressions as e3
from agentic.canonical import timestamp
from agentic.reporting.bounded import source_timeout


class E9Tests(unittest.TestCase):
    setUp = e6.ControllerTests.setUp
    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status
    restart = e6.ControllerTests.restart

    def budget_probe(self, case, run):
        case.network.latency = .3
        case.helper.listed = [dict(case.helper.current, pull_request=n) for n in range(41, 46)]
        timeouts = []
        def observe(candidate, now):
            for _ in range(3):
                timeout = source_timeout()
                timeouts.append(timeout)
                time.sleep(min(1.6, timeout))
                source_timeout()
            return dict(candidate=deepcopy(candidate), observed_at=now,
                expires_at=(timestamp(now) + timedelta(minutes=5)).isoformat(),
                open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)

        case.helper.observe_candidate = observe
        if case is not self:
            e3.CLI_ADAPTERS['observe_review_candidate'] = observe
        started = time.monotonic()
        run('2026-11-11T10:00:00Z')
        self.assertLess(time.monotonic() - started, 45)
        reviews = [r for r in case.status()['reports'] if r['kind'] == 'final-human-review']
        delivered = {m['candidate']['pull_request'] for r in reviews for m in r['review_batch']['members']
                     if all(d['status'] == 'DELIVERED' for d in case.status()['deliveries']
                            if d['report_id'] == r['report_id'])}
        self.assertGreater(len(delivered), 0)
        self.assertLess(len(delivered), 5)
        self.assertTrue(timeouts and all(0 < t <= 15 for t in timeouts))
        # Remaining locators survive repeated discovery and process after restart.
        for second in range(1, 6):
            e6.ControllerTests.restart(case)
            started = time.monotonic()
            run('2026-11-11T10:00:0%dZ' % second)
            self.assertLess(time.monotonic() - started, 45)
        reviews = [r for r in case.status()['reports'] if r['kind'] == 'final-human-review']
        self.assertEqual({m['candidate']['pull_request'] for r in reviews for m in r['review_batch']['members']},
                         set(range(41, 46)))
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1
                            for r in reviews for d in case.status()['deliveries'] if d['report_id'] == r['report_id']))
        self.assertTrue(all(0 < t <= 15 for t in timeouts))

    def restoration_probe(self, case, run):
        case.network.latency = .3
        case.helper.listed = [deepcopy(case.helper.current)]
        run('2026-11-11T10:00:00Z')
        before = deepcopy(case.status()['deliveries'])
        e6.ControllerTests.restart(case)
        case.helper.readiness = 'FINAL_REVIEW'
        run('2026-11-11T10:00:01Z')
        self.assertEqual(len(case.network.posts), len(before) + 2)
        e6.ControllerTests.restart(case)
        case.helper.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        posts = len(case.network.posts)
        run('2026-11-11T10:00:02Z')
        self.assertEqual(len(case.network.posts), posts + 2)
        for platform in ('slack', 'teams'):
            latest = [p for name, p in case.network.posts if name == platform][-1]
            self.assertIn('readiness restored', str(latest).lower())
        states = {d['delivery_id']: d for d in case.status()['deliveries']}
        for prior in before:
            self.assertEqual(states[prior['delivery_id']], prior)
        e6.ControllerTests.restart(case)
        run('2026-11-11T10:00:03Z')
        self.assertEqual(len(case.network.posts), posts + 2)
        self.assertEqual(case.status()['deliveries'], list(states.values()))

    def test_new_batch_observations_share_deadline_and_make_progress(self):
        self.budget_probe(self, self.cycle)

    def test_ready_withdrawn_restored_is_presented_once_per_platform(self):
        self.restoration_probe(self, self.cycle)


class E9CliTests(unittest.TestCase):
    def probe(self, probe):
        def inspect(case, result, output):
            probe(self, case, lambda now: e7.E7CliTests.rerun(self, case, now))
        e6.CliTests.probe(self, lambda case, net: None, inspect)

    def test_cli_new_batch_observations_share_deadline_and_make_progress(self):
        self.probe(E9Tests.budget_probe)

    def test_cli_ready_withdrawn_restored_is_presented_once_per_platform(self):
        self.probe(E9Tests.restoration_probe)


if __name__ == '__main__':
    unittest.main()
