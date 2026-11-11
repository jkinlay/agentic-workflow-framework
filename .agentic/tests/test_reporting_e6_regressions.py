"""Pre-E6: real controller and CLI, with only urllib network transport faked."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_reporting_e2_regressions as e2
import test_reporting_e3_regressions as e3
from test_reporting_fixtures import CHANNEL_ID, TEAM_ID
from agentic.reporting.http_transport import http_json


def wire(case, net):
    """Run production HTTP/error decoding beneath the already real ports."""
    root = Path(case.helper.temp.name) / 'wire-host'
    root.mkdir()
    config = e2._base_config(root)
    config['github']['project_id'] = case.binding['project']
    config.update(_runner=case.runner, _clock=case.clock, _reporting_http=http_json,
        reporting_sources=dict(binding=deepcopy(case.binding), pull_requests=[99]),
        reporting={p: dict(context='workspace' if p == 'slack' else 'tenant', sender='sender')
                   for p in ('teams', 'slack')})
    case.adapters = case.namespace['build_adapters'](config)
    return patch('agentic.reporting.http_transport.build_opener', return_value=net)


class ControllerTests(unittest.TestCase):
    cycle = e2.ReportingE2Regressions.cycle
    status = e2.ReportingE2Regressions.status

    def setUp(self):
        e2.ReportingE2Regressions.setUp(self)
        self.network.__class__ = e3.Network
        e3.Network.__init__(self.network)
        self.network.latency = 0
        # Keep the patch object alive and stop the same object at cleanup.
        self.network_patch = wire(self, self.network)
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)

    def test_observed_work_survives_unknown_ticket_details(self):
        self.network.latency = .3
        result = self.cycle('2026-11-10T10:00:00Z', [e2.ticket('EX-1', 1, paths=['README.md'])])
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['dispatch_receipts']), 1)
        hourly = next(r for r in self.status()['reports'] if r['kind'] == 'hourly')
        streams = hourly['body']['data']['observations']['streams']
        self.assertTrue(any(s['stream'] == 'A' and s['ticket'] == 'EX-1' and s['state'] == 'WORKING' for s in streams))
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})
        for platform, payload in self.network.posts:
            text = payload['text'] if platform == 'slack' else payload['body']['content']
            current = text.split('Current work', 1)[1].split('PRs and Jira tickets', 1)[0]
            for expected in ('EX-1', 'WORKING', 'A', 'Ticket details unavailable'):
                self.assertIn(expected, current)
            self.assertNotIn('None', current)

    def restart(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.helper.store = ContinuousControllerStore(self.helper.path, ['A', 'B', 'C'], cadence_seconds=3600)

    def test_retry_after_survives_restart_and_defers_without_spending_or_waiting(self):
        from agentic.reporting import http_transport
        from urllib.parse import unquote, urlsplit
        pauses = []
        original_init = http_transport.BaseTransport.__init__

        def transport_init(transport, **kwargs):
            original_init(transport, **kwargs)
            original_pause = transport.pause
            def pause(seconds):
                pauses.append((transport.platform, seconds))
                return original_pause(seconds)
            transport.pause = pause

        self.network.throttled = {'slack', 'teams'}
        with patch.object(http_transport.BaseTransport, '__init__', transport_init):
            self.assertEqual(self.cycle('2026-11-10T10:00:00Z')['errors'], [])
        states = self.status()['deliveries']
        self.assertEqual({d['status'] for d in states}, {'THROTTLED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in states))
        self.assertTrue(all(d['retry_not_before'] == '2026-11-10T10:01:00Z' for d in states))
        self.assertTrue(all(d['history'][-1]['kind'] == 'SEND_THROTTLED' for d in states))
        self.assertEqual([], pauses)
        self.assertEqual(
            {'slack': [('GET', 'auth.test'), ('GET', 'conversations.info'),
                       ('POST', 'chat.postMessage')],
             'teams': [('GET', kind) for kind in
                       ('me', 'organization', TEAM_ID, CHANNEL_ID, 'members')]
                      + [('POST', 'messages')]},
            {platform: [(method, unquote(urlsplit(url).path).rsplit('/', 1)[-1])
                        for method, url in self.network.calls
                        if ('slack.com' in url) == (platform == 'slack')]
             for platform in ('slack', 'teams')})
        calls = len(self.network.calls)
        self.restart()
        self.cycle('2026-11-10T10:00:59Z')
        self.assertEqual(len(self.network.calls), calls)
        self.assertEqual(len(self.network.posts), 2)
        self.network.throttled.clear()
        self.cycle('2026-11-10T10:01:00Z')
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 2 for d in self.status()['deliveries']))

    def test_two_throttled_sends_fail_truthfully_and_never_get_a_third(self):
        self.network.throttled = {'slack', 'teams'}
        self.cycle('2026-11-10T10:00:00Z')
        self.cycle('2026-11-10T10:01:00Z')
        states = self.status()['deliveries']
        self.assertEqual({d['status'] for d in states}, {'FAILED'})
        self.assertTrue(all(len(d['attempts']) == 2 for d in states))
        self.assertTrue(all(d['reason'] == 'TWO_SEND_ATTEMPTS_EXHAUSTED' for d in states))
        self.restart()
        self.network.throttled.clear()
        self.cycle('2026-11-10T10:02:00Z')
        self.assertEqual(len(self.network.posts), 4)

    def test_missing_or_malformed_retry_after_uses_finite_default(self):
        self.network.throttled = {'slack', 'teams'}
        for hour, header in enumerate((None, '-1', 'not-a-delay'), 10):
            with self.subTest(header=header):
                self.network.retry_after = header
                self.cycle('2026-11-10T%02d:00:00Z' % hour)
                reports = self.status()['reports']
                rid = next(r['report_id'] for r in reversed(reports) if r['kind'] == 'hourly')
                states = [d for d in self.status()['deliveries'] if d['report_id'] == rid]
                self.assertEqual({d['status'] for d in states}, {'THROTTLED'})
                self.assertTrue(all(d['retry_not_before'] == '2026-11-10T%02d:01:00Z' % hour for d in states))

    def test_nondefault_retry_after_is_honoured_independently_per_platform(self):
        self.network.throttled = {'teams'}
        self.network.retry_after = '90'
        self.cycle('2026-11-10T10:00:00Z')
        states = {d['platform']: d for d in self.status()['deliveries']}
        self.assertEqual(states['slack']['status'], 'DELIVERED')
        self.assertEqual(states['teams']['retry_not_before'], '2026-11-10T10:01:30Z')
        self.cycle('2026-11-10T10:01:29Z')
        self.assertEqual(len(self.network.posts), 2)
        self.network.throttled.clear()
        self.cycle('2026-11-10T10:01:30Z')
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})
        self.assertEqual(len(self.network.posts), 3)

    def test_normalized_slack_readback_still_requires_exact_marker_sender_and_channel(self):
        self.settings['mode'] = 'slack'
        for hour, field, value in ((10, 'user', 'other-sender'), (11, 'channel', 'CFOREIGN'),
                                    (12, 'text', 'AWF delivery: unrelated / unrelated')):
            with self.subTest(field=field):
                def corrupt(platform, messages):
                    for message in messages:
                        message[field] = value
                    return messages
                self.network.message_filter = corrupt
                self.cycle('2026-11-10T%02d:00:00Z' % hour)
                self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'UNKNOWN'})
                self.assertTrue(all(len(d['attempts']) == 1 for d in self.status()['deliveries']))

    def test_graph_pagination_cannot_change_origin_resource_or_path_segments(self):
        self.settings['mode'] = 'teams'
        self.network.messages_paged = True
        links = (
            lambda path: path.replace('graph.microsoft.com', 'example.invalid') + '?$skiptoken=page2',
            lambda path: path.replace(CHANNEL_ID, '19:other@thread.tacv2') + '?$skiptoken=page2',
            lambda path: path.replace('/channels/', '%2Fchannels%2F') + '?$skiptoken=page2',
        )
        for hour, link in enumerate(links, 10):
            with self.subTest(hour=hour):
                self.network.next_link = link
                self.cycle('2026-11-10T%02d:00:00Z' % hour)
                self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'UNKNOWN'})
        self.assertFalse(any('$skiptoken=' in url for _, url in self.network.calls))

    def test_membership_pagination_is_bounded_and_foreign_links_never_contacted(self):
        self.settings['mode'] = 'teams'
        self.network.membership_paged = True
        self.network.next_link = lambda path: path.replace('graph.microsoft.com', 'example.invalid') + '?$skiptoken=page2'
        self.cycle('2026-11-10T10:00:00Z')
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'PENDING'})
        self.assertTrue(all(not d['attempts'] for d in self.status()['deliveries']))
        self.assertFalse(any('example.invalid' in url for _, url in self.network.calls))
        self.network.next_link = lambda path: path  # Server repeats the first page.
        before = len([url for _, url in self.network.calls if url.endswith('/members')])
        self.cycle('2026-11-10T10:00:01Z')
        after = len([url for _, url in self.network.calls if url.endswith('/members')])
        self.assertEqual(after - before, 10)
        self.assertEqual(self.network.posts, [])


class CliTests(unittest.TestCase):
    def probe(self, prepare, inspect):
        patches = []
        def setup(case, net):
            prepare(case, net)
            network_patch = wire(case, net)
            network_patch.start()
            patches.append(network_patch)
        try:
            with redirect_stdout(io.StringIO()):
                e3.cli_probe(.3, prepare=setup, inspect=inspect)
        finally:
            for network_patch in reversed(patches):
                network_patch.stop()

    def delivered(self, case):
        self.assertEqual({d['status'] for d in case.status()['deliveries']}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in case.status()['deliveries']))

    def test_unescaped_graph_message_next_link_reaches_second_page(self):
        def prepare(case, net):
            net.messages_paged = True
        def inspect(case, result, output):
            self.delivered(case)
            self.assertTrue(any(CHANNEL_ID in url and '/messages?' in url and '$skiptoken=' in url
                                for _, url in case.network.calls))
        self.probe(prepare, inspect)

    def test_approved_member_on_page_two_admits_send(self):
        def prepare(case, net):
            net.membership_paged = True
        def inspect(case, result, output):
            self.delivered(case)
            self.assertTrue(any('/members?' in url and '$skiptoken=' in url for _, url in case.network.calls))
            self.assertTrue(any(TEAM_ID in url for method, url in case.network.calls if method == 'POST'))
        self.probe(prepare, inspect)

    def test_slack_emoji_normalization_preserves_delivery_confirmation(self):
        def prepare(case, net):
            item = json.loads(case.runner.inventory[0]['body'])
            item['actor'] = 'owner'
            item['reason'] = 'Owner access required \U0001f389'
            case.runner.inventory[0]['body'] = json.dumps(item)
        def inspect(case, result, output):
            self.delivered(case)
            self.assertTrue(any('\U0001f389' in p['text'] for platform, p in case.network.posts if platform == 'slack'))
            slack = [d for d in case.status()['deliveries'] if d['platform'] == 'slack']
            self.assertTrue(all(d['history'][-1]['message_id'].endswith('.424109') for d in slack))
        self.probe(prepare, inspect)
