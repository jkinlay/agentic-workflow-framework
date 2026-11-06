"""Concrete AWF-241 auxiliary-review regressions; no real provider calls."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters'))
from agentic import ValidationError
from agentic.reporting.compose import compose_report
from agentic.reporting.bodies import render_body
from agentic.reporting.delivery import DeliveryOutbox, delivery_intent, install_delivery_schema
from test_reporting_delivery import Database, Provider, binding
from test_reporting_records import request


class CompositionRegressions(unittest.TestCase):
    def compose(self, supplied, kind='eod'):
        return compose_report(kind, supplied, supplied['binding'], verify_sources=lambda v, b: v)['data']

    def test_cancelled_denominator_matches_snapshot_and_keeps_dispositions(self):
        from agentic.reporting.records import prepare_snapshot
        supplied = request()
        snapshot = prepare_snapshot(supplied, supplied['binding'], verify_sources=lambda v, b: v)
        scope = self.compose(supplied)['detail_metrics']['scope']
        self.assertEqual(snapshot['values']['metrics']['implementation_tickets'], 1)
        for key in ('epics', 'implementation_tickets', 'delivered', 'priority_size', 'states'):
            self.assertEqual(snapshot['values']['metrics'][key], scope[key])
        self.assertEqual(scope['dispositions']['cancelled'], 1)

    def test_missing_prs_unavailable_while_observed_empty_is_zero(self):
        supplied = request()
        supplied.update(timezone='Europe/London', previous_daily_end='2026-11-04T17:00:00Z')
        body = compose_report('eod', supplied, supplied['binding'], verify_sources=lambda v, b: v)
        self.assertNotEqual(body['data']['freshness'], 'FRESH')
        self.assertNotIn('Distinct merged PRs: 0', render_body(body, 'slack'))
        daily = body['data']['detail_metrics']['daily']
        self.assertIsNone(daily['today']['merged_pr_count'])
        self.assertEqual(daily['today']['reconciled_ticket_count'], 1)
        supplied['prs'] = []
        empty = compose_report('eod', supplied, supplied['binding'], verify_sources=lambda v, b: v)
        self.assertEqual(empty['data']['freshness'], 'FRESH')
        self.assertEqual(empty['data']['detail_metrics']['daily']['today']['merged_pr_count'], 0)
        self.assertIn('Distinct merged PRs: 0', render_body(empty, 'slack'))

    def accounting(self):
        supplied = request()
        forecast = dict(id='work', optimistic=10, base=20, conservative=30, basis_observation_ids=['usage'])
        supplied['accounting'] = dict(mode='on', enrolled=['worker'], forecasts=[forecast],
            observations=[dict(id='usage', agent='worker', role='worker', ticket='T-1', input=10, output=5)])
        return supplied, forecast

    def test_rollup_derives_from_retained_forecast(self):
        supplied, _ = self.accounting()
        metrics = self.compose(supplied)['detail_metrics']
        self.assertEqual(metrics['tokens']['expected_total']['base'], 35)
        self.assertEqual(metrics['token_rollup']['scope']['expected_total'], metrics['tokens']['expected_total'])
        self.assertEqual(metrics['token_rollup']['shared']['remaining']['base'], 20)

    def test_rollup_rejects_missing_foreign_or_changed_components(self):
        supplied, forecast = self.accounting()
        for components in ([], [dict(forecast, id='other')], [dict(forecast, base=21)],
                           [dict(forecast, basis_observation_ids=['foreign'])]):
            with self.subTest(components=components), self.assertRaises(ValidationError):
                self.compose(dict(supplied, token_components=components))
        supplied['token_components'] = [dict(forecast, epic='E-1')]
        metrics = self.compose(supplied)['detail_metrics']
        self.assertEqual(metrics['token_rollup']['epics']['E-1']['remaining']['base'], 20)
        self.assertEqual(metrics['token_rollup']['scope']['expected_total'], metrics['tokens']['expected_total'])

    def test_epic_remaining_excludes_delivered_and_keeps_total_population(self):
        supplied = request()
        metrics = self.compose(supplied)['detail_metrics']['scope']
        epic = metrics['per_epic']['E-1']
        self.assertEqual(epic['count'], 1)
        self.assertEqual(epic['remaining'], dict(count=0, priority_size={}, states={}))
        self.assertEqual(self.compose(supplied)['epics'][0]['remaining'], 'count: 0; priority size: None; states: None')
        supplied['tickets'][2].update(state='READY', epic='E-1', size='M', priority='Low')
        epic = self.compose(supplied)['detail_metrics']['scope']['per_epic']['E-1']
        self.assertEqual(epic['remaining'], dict(count=1, priority_size={'Low': {'M': 1}}, states={'READY': 1}))

    def test_composed_delay_retained_separately_and_missing_is_explicit(self):
        supplied = request()
        elapsed = self.compose(supplied)['detail_metrics']['elapsed']['standard']
        self.assertEqual(elapsed['mean_seconds'], dict(numerator=3600, denominator=1))
        self.assertEqual(elapsed['reconciliation_delay']['mean_seconds'], dict(numerator=600, denominator=1))
        self.assertEqual(elapsed['reconciliation_delay']['n'], 1)
        supplied['tickets'][1].pop('reconciled_at')
        delay = self.compose(supplied)['detail_metrics']['elapsed']['standard']['reconciliation_delay']
        self.assertEqual(delay['status'], 'UNAVAILABLE')
        self.assertIsNone(delay['mean_seconds'])


class DeliveryRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(str(Path(self.temp.name) / 'controller.sqlite'))
        with self.db.transaction() as db:
            install_delivery_schema(db)
        self.store = DeliveryOutbox(self.db)

    def transport(self, platform, outcomes):
        from agentic.reporting.http_transport import SlackTransport, TeamsTransport
        from urllib.error import HTTPError
        cls = SlackTransport if platform == 'slack' else TeamsTransport
        b = binding(platform)
        b['destination'] = 'C1' if platform == 'slack' else 'team/channel'
        intent = delivery_intent('report', b, 'Safe report')
        self.store.enqueue(intent)
        posts, messages = [], []
        transport = cls(context='context', sender='sender', pause=lambda seconds: None)
        def http(method, url, headers, payload):
            if method == 'POST':
                posts.append(payload)
                outcome = outcomes[min(len(posts)-1, len(outcomes)-1)]
                if outcome == 'reject':
                    if platform == 'slack':
                        return dict(ok=False, error='channel_not_found', diagnostic='SECRET_DIAGNOSTIC')
                    raise HTTPError(url, 403, 'SECRET_DIAGNOSTIC', {}, None)
                if outcome == 'timeout':
                    raise TimeoutError('SECRET_DIAGNOSTIC')
                messages.append(dict(ts='1355517523.000005', user='sender', text=transport.wire_text(intent)) if platform == 'slack'
                    else dict(id='1616990032035', body=dict(content=transport.wire_text(intent)), **{'from': dict(user=dict(id='sender'))}))
                return dict(ok=True, id='1616990032035')
            if 'auth.test' in url:
                return dict(ok=True, team_id='context', user_id='sender')
            if 'conversations.info' in url:
                return dict(ok=True, channel=dict(id='C1', is_member=True))
            if 'conversations.history' in url:
                return dict(ok=True, messages=messages)
            if '/members' in url:
                return dict(value=[dict(userId='sender')])
            if '/me?' in url:
                return dict(id='sender')
            if '/organization?' in url:
                return dict(value=[dict(id='context')])
            if url.endswith('/teams/team'):
                return dict(id='team')
            if url.endswith('/channels/channel'):
                return dict(id='channel')
            return dict(value=messages)
        transport.http = http
        adapter = importlib.import_module(platform + '_reporting_adapter').ReportingAdapter(
            transport, b, resolve_secret=lambda ref: 'fixture-secret', mode=platform)
        return intent, adapter, posts

    def test_definite_rejection_retry_then_success_or_failure_across_restart(self):
        for platform in ('slack', 'teams'):
            for outcome, final in (('success', 'DELIVERED'), ('reject', 'FAILED')):
                with self.subTest(platform=platform, outcome=outcome):
                    # A separate report identity for each scenario.
                    self.setUp()
                    intent, provider, posts = self.transport(platform, ['reject', outcome])
                    first = self.store.advance(intent['delivery_id'], provider)
                    self.assertEqual(first['status'], 'ABSENT')
                    rejection = next(h for h in first['history'] if h['kind'] == 'SEND_REJECTED')
                    self.assertEqual(rejection['attempt_id'], first['attempts'][0])
                    store = DeliveryOutbox(self.db)
                    result = store.advance(intent['delivery_id'], provider)
                    self.assertEqual(result['status'], final)
                    for _ in range(3):
                        store.advance(intent['delivery_id'], provider)
                    self.assertEqual(len(posts), 2)
                    self.assertNotIn('SECRET_DIAGNOSTIC', str(result))
                    self.assertNotIn('fixture-secret', str(result))

    def test_timeout_without_terminal_evidence_never_retries(self):
        for platform in ('slack', 'teams'):
            intent, provider, posts = self.transport(platform, ['timeout'])
            for _ in range(4):
                result = DeliveryOutbox(self.db).advance(intent['delivery_id'], provider)
                self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(len(posts), 1)

    def test_rejection_survives_competing_observer_and_retry_reservation_is_exclusive(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        intent, adapter, posts = self.transport('slack', ['reject'])
        entered, release = Event(), Event()
        original = adapter.transport.http
        def blocked_http(method, *args):
            if method == 'POST':
                entered.set()
                self.assertTrue(release.wait(5))
            return original(method, *args)
        adapter.transport.http = blocked_http
        other = DeliveryOutbox(self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.store.advance, intent['delivery_id'], adapter)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(other.advance(intent['delivery_id'], adapter)['status'], 'UNKNOWN')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)['status'], 'ABSENT')
            entered.clear()
            release.clear()
            retry = pool.submit(other.advance, intent['delivery_id'], adapter)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.store.advance(intent['delivery_id'], adapter)['status'], 'UNKNOWN')
            finally:
                release.set()
            result = retry.result(timeout=5)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(result['attempts']), 2)
        self.assertEqual(len([h for h in result['history'] if h['kind'] == 'SEND_REJECTED']), 2)

    def test_wrong_attempt_rejection_cannot_authorize_retry(self):
        intent, adapter, posts = self.transport('slack', ['reject'])
        original = adapter.transport.rejection
        adapter.transport.rejection = lambda intent, attempt: dict(original(intent, attempt), attempt_id='stale')
        for _ in range(3):
            self.assertEqual(self.store.advance(intent['delivery_id'], adapter)['status'], 'UNKNOWN')
        self.assertEqual(len(posts), 1)

    def test_settle_polls_for_delayed_visibility_but_empty_window_stays_unknown(self):
        from agentic.reporting.http_transport import SlackTransport, TeamsTransport
        for platform, cls in (('slack', SlackTransport), ('teams', TeamsTransport)):
            intent, adapter, posts = self.transport(platform, ['timeout'])
            original, reads, pauses = adapter.transport.http, [], []
            adapter.transport.pause = pauses.append
            def delayed(method, url, headers, payload):
                if 'conversations.history' in url or method == 'GET' and '/messages' in url:
                    reads.append(url)
                    if len(reads) == 3:
                        text = adapter.transport.wire_text(intent)
                        return (dict(ok=True, messages=[dict(ts='1355517523.000006', user='sender', text=text)])
                                if platform == 'slack' else
                                dict(value=[dict(id='1616990032036', body=dict(content=text), **{'from': dict(user=dict(id='sender'))})]))
                return original(method, url, headers, payload)
            adapter.transport.http = delayed
            result = self.store.advance(intent['delivery_id'], adapter)
            self.assertEqual(result['status'], 'DELIVERED')
            self.assertEqual(len(posts), 1)
            self.assertEqual(len(reads), 3)
            self.assertEqual(pauses, [0.1, 0.1])

    def test_ambiguous_provider_errors_are_not_terminal_rejections(self):
        from urllib.error import HTTPError
        for platform in ('slack', 'teams'):
            for response in ('server', 'timeout', 'accepted'):
                with self.subTest(platform=platform, response=response):
                    self.setUp()
                    intent, adapter, _ = self.transport(platform, ['timeout'])
                    original, posts = adapter.transport.http, []
                    def http(method, url, headers, payload):
                        if method == 'POST':
                            posts.append(payload)
                            if response == 'accepted':
                                return dict(ok=True, id='accepted-only')
                            if platform == 'slack':
                                return dict(ok=False, error='internal_error' if response == 'server' else 'request_timeout')
                            raise HTTPError(url, 500 if response == 'server' else 408, 'SECRET_DIAGNOSTIC', {}, None)
                        return original(method, url, headers, payload)
                    adapter.transport.http = http
                    for _ in range(3):
                        self.assertEqual(self.store.advance(intent['delivery_id'], adapter)['status'], 'UNKNOWN')
                    self.assertEqual(len(posts), 1)

    def test_http_boundary_retains_only_definite_non_acceptance(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        from agentic.reporting.http_transport import http_json, TerminalRejection
        import traceback
        for code, exception in ((403, TerminalRejection), (408, ValidationError), (500, ValidationError)):
            with self.subTest(code=code), patch('agentic.reporting.http_transport.build_opener') as opener:
                opener.return_value.open.side_effect = HTTPError('https://slack.com/api/chat.postMessage', code,
                                                                'SECRET_DIAGNOSTIC', {}, None)
                try:
                    http_json('POST', 'https://slack.com/api/chat.postMessage', {}, {})
                except exception as error:
                    self.assertEqual(isinstance(error, TerminalRejection), code == 403)
                    self.assertNotIn('SECRET_DIAGNOSTIC', traceback.format_exc())
                else:
                    self.fail('HTTP failure must produce a sanitized outcome')

    def test_restored_readiness_not_suppressed_by_outbox(self):
        from agentic.reporting.events import readiness_events
        from agentic.canonical import canonical, loads
        candidate = dict(project='p', repository='r', pr=1, head='a'*40, base='b'*40, scope_revision='s',
                         gate='g', stage='READY_FOR_OWNER_AUTHORIZATION', critic=True, specialists=True, final_gate=True)
        state, identities, provider = {}, [], Provider()
        provider.observation = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', message_id='1355517523.000005')
        for current in ([candidate], [dict(candidate, final_gate=False)], [candidate], [candidate], [], [], [candidate]):
            notices, state = readiness_events(current, loads(canonical(state).decode()))
            for notice in notices:
                identities.append(notice['id'])
                intent = delivery_intent(notice['id'], binding(), str(notice))
                self.store.enqueue(intent)
                self.store.advance(intent['delivery_id'], provider)
        self.assertEqual(len(identities), 5)
        self.assertEqual(len(set(identities)), 5)
        self.assertEqual(provider.calls, 5)


if __name__ == '__main__':
    unittest.main()
