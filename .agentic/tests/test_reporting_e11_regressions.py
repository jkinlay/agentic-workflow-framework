"""Pre-E11: real controller/CLI, restarted outbox and strict HTTP threads."""
from test_reporting_timing import _logical_slow_calls
from copy import deepcopy
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlsplit

import test_reporting_e3_regressions as e3
import test_reporting_e6_regressions as e6
import test_reporting_e7_regressions as e7
from test_reporting_fixtures import graph_message, slack_message


class ThreadNetwork(e3.Network):
    """Network-only fake: replies are visible only under their original root.

    Reject Graph reply-as-root URLs and Slack reply-as-parent timestamps. Slow
    Graph reads paginate unrelated replies with opaque continuation tokens.
    """
    def __init__(self):
        super().__init__()
        self.graph_threads = {}
        self.slow_teams = False
        self.reply_pages = []
        self.landed_at = {}
        self.invalid_roots = []
        self.prove_overlap = False
        self.teams_read_entered = threading.Event()
        self.slack_read_complete = threading.Event()
        self.overlap_order = []

    def __call__(self, method, url, headers, payload):
        platform = 'slack' if 'slack.com' in url else 'teams'
        path = unquote(urlsplit(url).path)
        reply = platform == 'teams' and path.endswith('/replies')
        root = path.split('/messages/', 1)[1].split('/')[0] if reply else None
        if platform == 'teams' and method == 'GET' and self.prove_overlap and not self.teams_read_entered.is_set():
            self.overlap_order.append('teams-blocked')
            self.teams_read_entered.set()
            # The documented HTTP maximum is only a deadlock failure guard.
            # Success requires Slack readback while this read remains blocked.
            if not self.slack_read_complete.wait(15):
                raise TimeoutError('Slack readback serialized behind Teams')
            self.overlap_order.append('teams-released')
        if platform == 'teams' and self.slow_teams:
            time.sleep(2 - self.latency)
        if reply:
            roots = {str(1793902283424 + i) for i in range(len(self.messages['teams']))
                     if self.graph_threads.get(i) is None}
            if root not in roots:
                self.invalid_roots.append((platform, root))
                raise HTTPError(url, 404, 'Root message not found', {}, None)
        if method == 'POST' and platform == 'slack' and payload.get('thread_ts'):
            roots = {slack_message(p, i)['ts'] for i, p in enumerate(self.messages['slack'])
                     if not p.get('thread_ts')}
            if payload['thread_ts'] not in roots:
                self.invalid_roots.append((platform, payload['thread_ts']))
                raise HTTPError(url, 400, 'Parent message required', {}, None)
        result = super().__call__(method, url, headers, payload)
        if method == 'POST':
            if platform == 'teams':
                self.graph_threads[len(self.messages['teams']) - 1] = root
            return result
        if platform == 'teams' and '/messages' in path:
            if reply and self.slow_teams:
                token = parse_qs(urlsplit(url).query).get('$skiptoken', [''])[0]
                self.reply_pages.append(token)
                # Deliberately keep the landed restoration beyond this bounded
                # listing, as a busy channel can. Never fabricate its receipt.
                unrelated = graph_message(dict(body=dict(contentType='html', content='Other reply')))
                return dict(value=[unrelated], **{'@odata.nextLink':
                    'https://graph.microsoft.com' + path + '?$skiptoken=opaque%2Breply%2F' + str(len(self.reply_pages))})
            messages = [graph_message(p, i) for i, p in enumerate(self.messages['teams'])
                        if self.graph_threads.get(i) == root]
            result = dict(value=self.message_filter('teams', messages))
        if 'conversations.replies' in url or reply:
            if platform == 'slack' and self.prove_overlap and not self.slack_read_complete.is_set():
                if not self.teams_read_entered.wait(15):
                    raise TimeoutError('Teams read did not enter overlap probe')
                self.overlap_order.append('slack-readback')
                self.slack_read_complete.set()
            self.landed_at[platform] = time.monotonic()
        return result


def configure_network(case, net):
    net.__class__ = ThreadNetwork
    ThreadNetwork.__init__(net)


def reports(case, key):
    return [r for r in case.status()['reports'] if r.get(key)]


def states(case, selected):
    ids = {r['report_id'] for r in selected}
    return [d for d in case.status()['deliveries'] if d['report_id'] in ids]


class E11Tests(unittest.TestCase):
    def setUp(self):
        e6.ControllerTests.setUp(self)
        configure_network(self, self.network)

    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status
    restart = e6.ControllerTests.restart

    def seed(self, case, run):
        case.helper.listed = [deepcopy(case.helper.current)]
        result = run('2026-11-11T10:00:00Z')
        if result is not None:
            self.assertEqual(result['errors'], [])
        original = deepcopy(case.status()['deliveries'])
        case.helper.listed = []
        e6.ControllerTests.restart(case)
        case.helper.readiness = 'FINAL_REVIEW'
        run('2026-11-11T10:00:01Z')
        corrections = reports(case, 'review_correction')
        self.assertEqual(len(corrections), 2)
        self.assertEqual({d['status'] for d in states(case, corrections)}, {'DELIVERED'})
        case.helper.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        e6.ControllerTests.restart(case)
        return original

    def concurrent_probe(self, case, run):
        E11Tests.seed(self, case, run)
        case.network.slow_teams = True
        case.network.prove_overlap = True
        for second in (2, 3):
            e6.ControllerTests.restart(case)
            started = time.monotonic()
            run('2026-11-11T10:00:0%dZ' % second)
            self.assertLess(time.monotonic() - started, 45)
            restored = states(case, reports(case, 'review_restoration'))
            self.assertEqual(len(restored), 2)
            slack = next(d for d in restored if d['platform'] == 'slack')
            self.assertEqual(slack['status'], 'DELIVERED')
            self.assertEqual(len(slack['attempts']), 1)
            if second == 2:
                self.assertEqual(case.network.overlap_order, ['teams-blocked', 'slack-readback', 'teams-released'])
                self.assertTrue(case.network.slack_read_complete.is_set())
            teams = next(d for d in restored if d['platform'] == 'teams')
            self.assertEqual(teams['status'], 'UNKNOWN')
            self.assertEqual(len(teams['attempts']), 1)
            self.assertFalse(any(t.name.startswith('awf-report-') for t in threading.enumerate()))
        self.assertGreater(len(case.network.reply_pages), 2)
        self.assertTrue(any('opaque+reply/' in token for token in case.network.reply_pages))
        case.network.slow_teams = False
        e6.ControllerTests.restart(case)
        run('2026-11-11T10:00:04Z')
        self.assertEqual({d['status'] for d in states(case, reports(case, 'review_restoration'))}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in states(case, reports(case, 'review_restoration'))))

    def uncertain_probe(self, case, run, *, cutoff=False, withdrawn=True):
        original = E11Tests.seed(self, case, run)
        case.network.message_filter = lambda platform, messages: [m for m in messages
            if 'readiness restored' not in str(m).lower()]
        run('2026-11-11T10:00:02Z')
        restorations = reports(case, 'review_restoration')
        self.assertEqual(len(restorations), 2)
        self.assertEqual({d['status'] for d in states(case, restorations)}, {'UNKNOWN'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in states(case, restorations)))
        case.network.message_filter = lambda platform, messages: messages
        case.helper.readiness = 'FINAL_REVIEW' if withdrawn else 'READY_FOR_OWNER_AUTHORIZATION'
        for second in (3, 4, 5):
            e6.ControllerTests.restart(case)
            result = run('2026-11-11T%s:00:0%dZ' % ('18' if cutoff else '10', second))
            if result is not None:
                self.assertEqual(result['errors'], [])
        self.assertEqual({d['status'] for d in states(case, restorations)}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in states(case, restorations)))
        corrections = reports(case, 'review_correction')
        self.assertEqual(len(corrections), 4 if withdrawn else 2)
        self.assertEqual({d['status'] for d in states(case, corrections)}, {'DELIVERED'})
        restored_ids = {d['delivery_id'] for d in states(case, restorations)}
        self.assertEqual({r['review_correction']['delivery_id'] for r in corrections
                          if r['review_correction']['delivery_id'] in restored_ids}, restored_ids if withdrawn else set())
        self.assertEqual(len([p for _, p in case.network.posts if 'readiness restored' in str(p).lower()]), 2)
        retained = {d['delivery_id']: d for d in case.status()['deliveries']}
        for prior in original:
            self.assertEqual(retained[prior['delivery_id']], prior)
        self.assertEqual(case.network.invalid_roots, [])

    def chain_probe(self, case, run):
        E11Tests.seed(self, case, run)
        run('2026-11-11T10:00:02Z')
        restorations = reports(case, 'review_restoration')
        self.assertEqual(len(restorations), 2)
        self.assertEqual({d['status'] for d in states(case, restorations)}, {'DELIVERED'})
        case.helper.readiness = 'FINAL_REVIEW'
        for second in (3, 4, 5):
            e6.ControllerTests.restart(case)
            run('2026-11-11T10:00:0%dZ' % second)
        corrections = reports(case, 'review_correction')
        self.assertEqual(len(corrections), 4)
        self.assertEqual({d['status'] for d in states(case, corrections)}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in states(case, corrections)))
        for platform in ('slack', 'teams'):
            roots = [d for d in case.status()['deliveries'] if d['platform'] == platform
                     and d['report_id'].startswith('review-')]
            self.assertEqual(len(roots), 1)
            root = roots[0]['history'][-1]['message_id']
            for delivery in states(case, restorations + corrections):
                if delivery['platform'] != platform:
                    continue
                intent = e7.E7ControllerTests.intent(case, delivery)
                self.assertEqual(intent['thread_id'], root)
                self.assertNotEqual(delivery['history'][-1]['message_id'], root)
        self.assertEqual(case.network.invalid_roots, [])
        posts = deepcopy(case.network.posts)
        e6.ControllerTests.restart(case)
        run('2026-11-11T10:00:06Z')
        self.assertEqual(case.network.posts, posts)

    def guard_probe(self, case, run, *, cutoff=False):
        E11Tests.seed(self, case, run)
        def withdraw(method, url):
            if method == 'GET' and ('conversations.info' in url or '/members' in url):
                if cutoff:
                    case.clock.value = '2026-11-11T18:00:00Z'
                    case.helper.now = case.clock.value
                else:
                    case.helper.readiness = 'FINAL_REVIEW'
        case.network.before_call = withdraw
        run('2026-11-11T10:00:02Z')
        restored = states(case, reports(case, 'review_restoration'))
        self.assertEqual(len(restored), 2)
        self.assertEqual({d['status'] for d in restored}, {'PENDING'})
        self.assertTrue(all(d['attempts'] == [] for d in restored))
        self.assertFalse(any('readiness restored' in str(p).lower() for _, p in case.network.posts))

    def test_restorations_share_concurrent_destination_delivery(self):
        self.concurrent_probe(self, self.cycle)

    @_logical_slow_calls
    def test_unknown_restoration_reconciles_after_withdrawal(self):
        self.uncertain_probe(self, self.cycle)

    @_logical_slow_calls
    def test_unknown_restoration_reconciles_after_evening_cutoff_and_withdrawal(self):
        self.uncertain_probe(self, self.cycle, cutoff=True)

    @_logical_slow_calls
    def test_unknown_restoration_reconciles_after_cutoff_while_still_ready(self):
        self.uncertain_probe(self, self.cycle, cutoff=True, withdrawn=False)

    @_logical_slow_calls
    def test_ready_withdrawn_ready_withdrawn_keeps_both_original_roots(self):
        self.chain_probe(self, self.cycle)

    @_logical_slow_calls
    def test_restoration_rechecks_readiness_after_destination_verification(self):
        self.guard_probe(self, self.cycle)

    @_logical_slow_calls
    def test_restoration_rechecks_calendar_after_destination_verification(self):
        self.guard_probe(self, self.cycle, cutoff=True)


class E11CliTests(unittest.TestCase):
    def probe(self, probe, **kwargs):
        def inspect(case, result, output):
            probe(self, case, lambda now: e7.E7CliTests.rerun(self, case, now), **kwargs)
        e6.CliTests.probe(self, configure_network, inspect)

    def test_cli_restorations_share_concurrent_destination_delivery(self):
        self.probe(E11Tests.concurrent_probe)

    @_logical_slow_calls
    def test_cli_unknown_restoration_reconciles_after_withdrawal(self):
        self.probe(E11Tests.uncertain_probe)

    @_logical_slow_calls
    def test_cli_unknown_restoration_reconciles_after_evening_cutoff_and_withdrawal(self):
        self.probe(E11Tests.uncertain_probe, cutoff=True)

    @_logical_slow_calls
    def test_cli_unknown_restoration_reconciles_after_cutoff_while_still_ready(self):
        self.probe(E11Tests.uncertain_probe, cutoff=True, withdrawn=False)

    @_logical_slow_calls
    def test_cli_ready_withdrawn_ready_withdrawn_keeps_both_original_roots(self):
        self.probe(E11Tests.chain_probe)

    @_logical_slow_calls
    def test_cli_restoration_rechecks_readiness_after_destination_verification(self):
        self.probe(E11Tests.guard_probe)

    @_logical_slow_calls
    def test_cli_restoration_rechecks_calendar_after_destination_verification(self):
        self.probe(E11Tests.guard_probe, cutoff=True)


if __name__ == '__main__':
    unittest.main()
