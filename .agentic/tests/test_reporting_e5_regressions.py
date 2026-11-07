from test_reporting_fixtures import reporting_policy_snapshot
"""Pre-E5 regressions: real controller, CLI, outbox and platform transports."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
import subprocess
import time
import unittest

import test_reporting_e2_regressions as e2
import test_reporting_e3_regressions as e3
from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.reporting.delivery import DeliveryOutbox
from test_reporting_fixtures import slack_message, graph_message

SENTINEL = 'gh' + 'p_' + 'SYNTHETIC_CREDENTIAL_SENTINEL'


def legacy_intent(report_id, binding, payload):
    """Untrusted caller builds the public envelope without the safe constructor."""
    return dict(delivery_id=fingerprint('report-delivery-1',
        [binding['project'], binding['repository'], report_id, binding['platform']]),
        report_id=report_id, binding=deepcopy(binding), payload=payload,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest())


class BoundaryTests(unittest.TestCase):
    setUp = e2.ReportingE2Regressions.setUp

    def test_public_enqueue_and_transaction_refuse_credentials_before_sqlite(self):
        outbox = DeliveryOutbox(self.helper.store)
        for destination in self.settings['bindings']:
            intent = legacy_intent('unsafe', destination, SENTINEL)
            before = self.helper.path.read_bytes()
            with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                outbox.enqueue(intent)
            self.assertEqual(self.helper.path.read_bytes(), before)
            with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                with self.helper.store.transaction() as db:
                    outbox.enqueue_in_transaction(db, intent)
            self.assertNotIn(SENTINEL.encode(), self.helper.path.read_bytes())

    def test_real_factory_senders_refuse_legacy_envelope_before_any_http(self):
        self.network.__class__ = e3.Network
        e3.Network.__init__(self.network)
        providers = self.adapters['configure_reporting'](reporting_policy_snapshot(self), self.binding)
        for destination in self.settings['bindings']:
            intent = legacy_intent('unsafe', destination, SENTINEL)
            with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                providers[destination['platform']].send(intent, 'synthetic-attempt')
        self.assertEqual(self.network.posts, [])
        self.assertEqual(self.network.thread_observations, [])

    def test_direct_transport_cannot_post_a_credential(self):
        providers = self.adapters['configure_reporting'](reporting_policy_snapshot(self), self.binding)
        for destination in self.settings['bindings']:
            for credential in (SENTINEL, 'token\nsynthetic-value'):
                intent = legacy_intent('unsafe', destination, credential)
                with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                    providers[destination['platform']].transport.send(intent, 'synthetic-attempt', 'synthetic-report-secret')
        self.assertEqual(self.network.posts, [])


class ThreadNetwork(e3.Network):
    def __init__(self):
        super().__init__()
        self.correction_urls = []

    def __call__(self, method, url, headers, payload):
        value = super().__call__(method, url, headers, payload)
        if method == 'POST' and '/replies' in url:
            self.correction_urls.append(url)
        if 'conversations.replies' in url:
            return dict(ok=True, messages=[slack_message(m, i)
                for i, m in enumerate(self.messages['slack'])])
        if '/replies?' in url:
            return dict(value=[graph_message(m, i)
                for i, m in enumerate(self.messages['teams'])])
        return value


class ControllerTests(unittest.TestCase):
    setUp = e2.ReportingE2Regressions.setUp
    cycle = e2.ReportingE2Regressions.cycle
    status = e2.ReportingE2Regressions.status

    def review(self):
        self.network.__class__ = ThreadNetwork
        ThreadNetwork.__init__(self.network)
        self.network.latency = 0
        self.helper.listed = [deepcopy(self.helper.current)]
        self.assertEqual(self.cycle('2026-11-10T10:00:00Z')['errors'], [])
        self.assertEqual(len(self.network.posts), 4)
        self.helper.listed = []

    def test_head_change_alone_corrects_and_restart_cannot_repeat(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.review()
        self.helper.current['head_sha'] = 'e'*40
        self.assertEqual(self.cycle('2026-11-10T10:01:00Z')['errors'], [])
        self.assertEqual(len(self.network.posts), 6)
        self.helper.store = ContinuousControllerStore(self.helper.path, ['A', 'B', 'C'], cadence_seconds=3600)
        self.assertEqual(self.cycle('2026-11-10T10:02:00Z')['errors'], [])
        self.assertEqual(len(self.network.posts), 6)

    def test_stale_observation_is_unavailable_then_gate_invalidation_corrects(self):
        self.review()
        self.assertEqual(self.cycle('2026-11-10T10:01:00Z')['errors'], [])
        self.assertEqual(len(self.network.posts), 4)
        self.helper.change = dict(final_gate_valid=False, observed_at='2026-11-10T10:01:00Z')
        result = self.cycle('2026-11-10T10:02:00Z')
        self.assertEqual(result['reporting']['coverage']['review_corrections'], 'UNAVAILABLE')
        self.assertEqual(len(self.network.posts), 4)
        self.helper.change = dict(final_gate_valid=False)
        self.assertEqual(self.cycle('2026-11-10T10:03:00Z')['errors'], [])
        self.assertEqual(len(self.network.posts), 6)

    def test_delivered_review_withdrawal_then_head_change_corrects_each_thread_once(self):
        self.network.__class__ = ThreadNetwork
        ThreadNetwork.__init__(self.network)
        self.helper.listed = [deepcopy(self.helper.current)]
        result = self.cycle('2026-11-10T10:00:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(self.network.posts), 4)
        review = next(r for r in self.status()['reports'] if r['kind'] == 'final-human-review')
        roots = {d['platform']: d['history'][-1]['message_id']
                 for d in self.status()['deliveries'] if d['report_id'] == review['report_id']}
        self.helper.readiness = 'FINAL_REVIEW'
        self.helper.listed = []
        result = self.cycle('2026-11-10T10:01:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(self.network.posts), 6)
        self.helper.current['head_sha'] = 'e'*40
        self.cycle('2026-11-10T10:02:00Z')
        self.cycle('2026-11-10T10:03:00Z')
        self.assertEqual(len(self.network.posts), 6)
        corrections = [r for r in self.status()['reports'] if r.get('review_correction')]
        self.assertEqual(len(corrections), 2)
        corrected_ids = {r['report_id'] for r in corrections}
        self.assertEqual({d['status'] for d in self.status()['deliveries']
                          if d['report_id'] in corrected_ids}, {'DELIVERED'})
        for platform, body in self.network.posts[4:]:
            self.assertIn('Correction', str(body))
            if platform == 'slack':
                self.assertEqual(body['thread_ts'], roots[platform])
        teams_urls = getattr(self.network, 'correction_urls', [])
        self.assertTrue(any('/messages/' + roots['teams'] + '/replies' in u for u in teams_urls))


class SlowSourceRunner:
    """Only source network transport changes; honours real command deadlines."""
    def __init__(self, original, pages):
        self.original, self.pages, self.calls = original, pages, []

    def run(self, argv, **kwargs):
        if '/pulls?state=closed' not in argv[-1]:
            return self.original.run(argv, **kwargs)
        self.calls.append(kwargs['timeout'])
        time.sleep(min(2, kwargs['timeout']))
        if kwargs['timeout'] < 2:
            raise subprocess.TimeoutExpired('synthetic-source', kwargs['timeout'])
        page = len(self.calls)
        return dict(items=[], complete=page >= self.pages, next_page=None if page >= self.pages else page+1)


class CLITests(unittest.TestCase):
    def test_actual_cli_slow_sources_do_not_consume_delivery_budget(self):
        observed = []
        started = time.monotonic()
        def prepare(case, net):
            # Factory runner is captured by reference: change network run only.
            original = case.runner.run
            slow = SlowSourceRunner(type('Port', (), {'run': staticmethod(original)})(), 16)
            case.runner.run = slow.run
            # Observe thread routes and provide the provider's reply readbacks.
            observed.append((case, slow))
        def inspect(case, result, output):
            self.assertEqual(len(case.network.posts), 2)
            self.assertEqual({d['status'] for d in case.status()['deliveries']}, {'DELIVERED'})
            self.assertLessEqual(len(observed[0][1].calls), 16)
            self.assertTrue(all(0 < t <= 30 for t in observed[0][1].calls))
            self.assertEqual(result['reporting']['coverage']['sources'], 'PARTIAL')
            self.assertIn('REPORT_SOURCE_INCOMPLETE_OR_DEADLINE', output)
            self.assertLess(time.monotonic() - started, 40)
            self.assertTrue(all(r['body']['data']['source_records']['prs'] is None
                                for r in case.status()['reports']))
        # Existing CLI harness requires zero errors; an incomplete verified
        # source snapshot must therefore expose its coverage marker directly.
        with redirect_stdout(io.StringIO()):
            e3.cli_probe(.3, prepare=prepare, inspect=inspect)


if __name__ == '__main__':
    unittest.main()
