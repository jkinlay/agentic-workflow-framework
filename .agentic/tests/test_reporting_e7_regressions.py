"""Pre-E7: real controller/CLI, production HTTP, network-only faults."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import subprocess
import time
import unittest

import test_reporting_e6_regressions as e6
from agentic.canonical import loads, sha256
from agentic.reporting.http_transport import BaseTransport


class E7ControllerTests(unittest.TestCase):
    def setUp(self):
        e6.ControllerTests.setUp(self)
        self.network.latency = .3

    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status
    restart = e6.ControllerTests.restart

    def intent(self, delivery):
        with self.helper.store.connection() as db:
            return loads(db.execute('SELECT intent_json FROM controller_report_delivery WHERE delivery_id=?',
                                    (delivery['delivery_id'],)).fetchone()[0])

    def owner_alert(self, reason):
        item = json.loads(self.runner.inventory[0]['body'])
        item.update(disposition='BLOCKED', actor='owner', reason=reason, next_action='Investigate')
        self.runner.inventory[0]['body'] = json.dumps(item)

    def test_quoted_timed_out_hourly_marker_in_real_owner_alert_never_confirms_hourly(self):
        self.settings['mode'] = 'slack'
        def timeout(method, url):
            if method == 'POST':
                raise TimeoutError('synthetic undelivered hourly')
        self.network.before_call = timeout
        self.cycle('2026-11-10T10:00:00Z')
        hourly = self.status()['deliveries'][0]
        self.assertEqual(hourly['status'], 'UNKNOWN')
        marker = BaseTransport.marker(self.intent(hourly))
        self.network.before_call = None
        self.owner_alert('Investigate timed out report:\n' + marker + '\nNo receipt')
        self.cycle('2026-11-10T10:00:01Z')
        states = {d['delivery_id']: d for d in self.status()['deliveries']}
        self.assertEqual(states[hourly['delivery_id']]['status'], 'UNKNOWN')
        self.assertEqual(len(states[hourly['delivery_id']]['attempts']), 1)
        self.assertEqual(len(self.network.messages['slack']), 1)
        text = self.network.messages['slack'][0]['text']
        self.assertIn('Owner action required', text)
        self.assertNotIn(marker, text)
        self.assertIn('AWF delivery (quoted):', text)
        self.assertEqual([d['status'] for k, d in states.items() if k != hourly['delivery_id']], ['DELIVERED'])

    def test_correct_final_marker_with_wrong_content_is_unknown(self):
        self.settings['mode'] = 'slack'
        def corrupt(platform, messages):
            for message in messages:
                message['text'] = 'Different report\n\n' + message['text'].splitlines()[-1]
            return messages
        self.network.message_filter = corrupt
        self.cycle('2026-11-10T10:00:00Z')
        self.assertEqual(self.status()['deliveries'][0]['status'], 'UNKNOWN')
        self.assertEqual(len(self.status()['deliveries'][0]['attempts']), 1)

    def test_history_newest_first_cursor_and_thread_separation(self):
        self.settings['mode'] = 'slack'
        self.network.slack_page_size = 2
        self.network.message_filter = lambda platform, messages: []
        self.cycle('2026-11-10T10:00:00Z')
        self.assertEqual(self.status()['deliveries'][0]['status'], 'UNKNOWN')
        original = deepcopy(self.network.messages['slack'][0])
        # Later roots hide the old report beyond two cursors. Replies belong to
        # another API and must not appear as channel roots, even with its text.
        self.network.messages['slack'].append(dict(original, thread_ts='1793902283.424109'))
        self.network.messages['slack'].extend(dict(original, text='Later root %d' % i) for i in range(5))
        pages = []
        def inspect(platform, messages):
            pages.append(deepcopy(messages))
            return messages
        self.network.message_filter = inspect
        self.restart()
        self.cycle('2026-11-10T10:00:01Z')
        self.assertEqual(self.status()['deliveries'][0]['status'], 'DELIVERED')
        self.assertEqual(pages[0][0]['text'], 'Later root 4')
        self.assertTrue(all('thread_ts' not in m for page in pages for m in page))
        self.assertTrue(any('cursor=4' in u for m, u in self.network.calls))
        self.assertEqual(len(self.network.posts), 1)

    def test_normalized_slack_entities_and_graph_html_still_match(self):
        self.owner_alert('Check A & B < C \U0001f389')
        def normalize(platform, messages):
            for message in messages:
                if platform == 'slack':
                    message['text'] = message['text'].replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                else:
                    value = message['body']['content'].replace('<br>', '<br />').replace('<strong>', '<b>').replace('</strong>', '</b>')
                    message['body']['content'] = '<div>' + value + '</div>'
            return messages
        self.network.message_filter = normalize
        self.cycle('2026-11-10T10:00:00Z')
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})

    def cooldown(self, platform, operation):
        # Keep the real Git/reservation checks, but isolate timing from the
        # caller's dirty checkout. Its growing diff otherwise triggers a full
        # physical-alias inventory scan per edited file inside the five seconds.
        repository = Path(self.helper.temp.name) / 'cooldown-repository'
        subprocess.run(['git', 'clone', '--shared', '--quiet',
                        str(self.repository['repository_root']), str(repository)],
                       check=True, capture_output=True)
        self.repository['repository_root'] = str(repository)
        self.settings['mode'] = platform
        if operation == 'POST':
            self.network.throttled = {platform}
            matches = lambda m, u: m == 'POST'
        else:
            self.network.read_throttled = {operation}
            matches = lambda m, u: m == 'GET' and operation in u
        self.owner_alert('First alert')
        started = time.monotonic()
        self.assertEqual(self.cycle('2026-11-10T10:00:00Z')['errors'], [])
        initial = [c for c in self.network.calls if matches(*c)]
        self.assertEqual(len(initial), 1)
        states = self.status()['deliveries']
        self.assertTrue(any(d['status'] == 'THROTTLED' for d in states))
        self.assertTrue(all(d.get('retry_not_before') == '2026-11-10T10:01:00Z' for d in states))
        if operation != 'POST':
            self.assertTrue(all(len(d['attempts']) <= 1 for d in states))
        if operation in {'conversations.info', '/members'}:
            self.assertTrue(all(d['attempts'] == [] for d in states))
        self.assertLess(time.monotonic() - started, 5)
        attempts = {d['delivery_id']: list(d['attempts']) for d in states}
        self.restart()
        self.owner_alert('Another report during cooldown')
        self.cycle('2026-11-10T10:00:59Z')
        self.assertEqual([c for c in self.network.calls if matches(*c)], initial)
        for d in self.status()['deliveries']:
            if d['delivery_id'] in attempts:
                self.assertEqual(d['attempts'], attempts[d['delivery_id']])
        self.network.throttled.clear()
        self.network.read_throttled.clear()
        self.cycle('2026-11-10T10:01:00Z')
        self.assertGreater(len([c for c in self.network.calls if matches(*c)]), len(initial))
        # Recovery retains one historical reconciliation/cycle and 32 HTTP calls.
        self.cycle('2026-11-10T10:01:01Z')
        self.cycle('2026-11-10T10:01:02Z')
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})
        if operation != 'POST':
            self.assertTrue(all(len(d['attempts']) == 1 for d in self.status()['deliveries']))

    def test_slack_post_cooldown_shared_by_reports_and_restart(self):
        self.cooldown('slack', 'POST')

    def test_graph_post_cooldown_shared_by_reports_and_restart(self):
        self.cooldown('teams', 'POST')

    def test_slack_history_cooldown_shared_by_reports_and_restart(self):
        self.cooldown('slack', 'conversations.history')

    def test_graph_message_read_cooldown_shared_by_reports_and_restart(self):
        self.cooldown('teams', '/messages?')

    def test_slack_verification_cooldown_never_spends_attempt(self):
        self.cooldown('slack', 'conversations.info')

    def test_graph_verification_cooldown_never_spends_attempt(self):
        self.cooldown('teams', '/members')

    def test_slack_workspace_post_cooldown_covers_new_destination_and_binding(self):
        self.settings['mode'] = 'slack'
        self.network.throttled = {'slack'}
        self.cycle('2026-11-10T10:00:00Z')
        self.restart()
        destination = next(b for b in self.settings['bindings'] if b['platform'] == 'slack')
        destination.update(destination='CSECOND', revision='2')
        self.owner_alert('New destination same workspace')
        self.network.throttled.clear()
        self.cycle('2026-11-10T10:00:59Z')
        self.assertEqual(len(self.network.posts), 1)
        self.cycle('2026-11-10T10:01:00Z')
        self.assertEqual(len(self.network.posts), 2)
        self.assertEqual(self.network.posts[-1][1]['channel'], 'CSECOND')


class E7CliTests(unittest.TestCase):
    delivered = e6.CliTests.delivered

    def probe(self, prepare, inspect):
        def setup(case, net):
            item = json.loads(case.runner.inventory[0]['body'])
            item['actor'] = 'owner'
            case.runner.inventory[0]['body'] = json.dumps(item)
            prepare(case, net)
        e6.CliTests.probe(self, setup, inspect)

    def rerun(self, case, now):
        import test_reporting_e3_regressions as e3
        case.clock.value = now
        e3.CLI_ADAPTERS['controller_clock'] = case.clock.now
        root = Path(case.helper.temp.name)
        project, adapter = root / 'cli-project', root / 'cli_adapter.py'
        cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/continuous_controller.py'))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli['main'](['--state', str(case.helper.path), '--worktree-root', str(project),
                '--cadence-seconds', '3600', '--stream', 'A', '--stream', 'B', '--stream', 'C',
                '--project-config', str(project / '.agentic/PROJECT_CONFIG.yaml'), '--adapter-module', str(adapter),
                '--adapter-sha256', sha256(adapter.read_bytes()), 'cycle', '--inventory-binding', str(root / 'binding.json'),
                '--repository-root', str(project), '--repository-head-sha', case.repository['repository_head_sha'],
                '--repository-tree-sha', case.repository['repository_tree_sha'], '--now', now, '--host-capacity', '3'])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['errors'], [])

    def test_cli_read_throttle_shared_with_other_report_and_recovered_after_restart(self):
        def prepare(case, net):
            net.read_throttled = {'conversations.history', '/messages?'}
        def inspect(case, result, output):
            self.assertEqual(len(case.status()['deliveries']), 4)
            for operation in ('conversations.history', '/messages?'):
                self.assertEqual(len([u for m, u in case.network.calls if operation in u]), 1)
            self.assertEqual({d['status'] for d in case.status()['deliveries']}, {'THROTTLED'})
            e6.ControllerTests.restart(case)
            calls = len(case.network.calls)
            self.rerun(case, '2026-11-10T18:00:59Z')
            self.assertEqual(len(case.network.calls), calls)
            case.network.read_throttled.clear()
            self.rerun(case, '2026-11-10T18:01:00Z')
            self.rerun(case, '2026-11-10T18:01:01Z')
            self.delivered(case)
        self.probe(prepare, inspect)

    def test_cli_post_throttle_shared_with_other_report_and_recovered_after_restart(self):
        def prepare(case, net):
            net.throttled = {'slack', 'teams'}
        def inspect(case, result, output):
            self.assertEqual(len(case.status()['deliveries']), 4)
            self.assertEqual(len(case.network.posts), 2)
            self.assertEqual({d['status'] for d in case.status()['deliveries']}, {'THROTTLED'})
            attempts = {d['delivery_id']: d['attempts'] for d in case.status()['deliveries']}
            e6.ControllerTests.restart(case)
            self.rerun(case, '2026-11-10T18:00:59Z')
            self.assertEqual(len(case.network.posts), 2)
            self.assertEqual({d['delivery_id']: d['attempts'] for d in case.status()['deliveries']}, attempts)
            case.network.throttled.clear()
            self.rerun(case, '2026-11-10T18:01:00Z')
            self.assertEqual({d['status'] for d in case.status()['deliveries']}, {'DELIVERED'})
            self.assertEqual(len(case.network.posts), 6)
            self.assertEqual(sorted(len(d['attempts']) for d in case.status()['deliveries']), [1, 1, 2, 2])
        self.probe(prepare, inspect)

    def test_cli_timed_out_hourly_quoted_by_delivered_alert_remains_unknown(self):
        def prepare(case, net):
            net.timeout_post = lambda platform, payload: 'Hourly progress' in str(payload)
        def inspect(case, result, output):
            hourly = next(d for d in case.status()['deliveries'] if d['platform'] == 'slack'
                          and d['status'] == 'UNKNOWN')
            marker = BaseTransport.marker(E7ControllerTests.intent(case, hourly))
            E7ControllerTests.owner_alert(case, 'Investigate:\n' + marker + '\nNo landing observed')
            e6.ControllerTests.restart(case)
            self.rerun(case, '2026-11-10T18:00:01Z')
            states = case.status()['deliveries']
            old = next(d for d in states if d['delivery_id'] == hourly['delivery_id'])
            self.assertEqual(old['status'], 'UNKNOWN')
            self.assertEqual(len(old['attempts']), 1)
            self.assertEqual(len(case.network.messages['slack']), 2)
            self.assertTrue(all('Hourly progress' not in m['text'] for m in case.network.messages['slack']))
            self.assertTrue(all(marker not in m['text'] for m in case.network.messages['slack']))
        self.probe(prepare, inspect)
