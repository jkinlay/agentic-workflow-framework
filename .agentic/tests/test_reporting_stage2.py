"""Offline stage-two regressions; all network effects use fake HTTP."""
import copy
from datetime import datetime, timezone
import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters'))
from agentic import ValidationError


class EventTests(unittest.TestCase):
    def test_reopened_action_gets_new_event_and_cross_scope_state_refuses(self):
        from agentic.reporting.events import owner_events
        action = dict(id='a', decision='decide', owner='o', due='now', reason='blocked', options=['park'], resume='decision')
        first, state = owner_events('scope', [action], {})
        _, state = owner_events('scope', [], state)
        reopened, state = owner_events('scope', [action], state)
        self.assertNotEqual(first[0]['id'], reopened[0]['id'])
        with self.assertRaises(ValidationError):
            owner_events('other-scope', [action], state)

    def test_owner_dedup_material_change_resolution_and_no_mutation(self):
        from agentic.reporting.events import owner_events
        action = dict(id='access', decision='Grant read access', owner='owner', due='tomorrow',
                      reason='blocked', options=['grant', 'park'], resume='access observed')
        old = {}
        events, state = owner_events('scope', [action], old)
        self.assertEqual(len(events), 1)
        self.assertEqual(old, {})
        self.assertEqual(owner_events('scope', [action], state)[0], [])
        changed = dict(action, due='today')
        self.assertEqual(owner_events('scope', [changed], state)[0][0]['kind'], 'owner-action-updated')
        resolved, final = owner_events('scope', [], state)
        self.assertEqual(resolved[0]['kind'], 'owner-action-resolved')
        self.assertEqual(owner_events('scope', [], final)[0], [])

    def test_combined_readiness_and_every_tuple_drift_corrects(self):
        from agentic.reporting.events import readiness_events
        c = dict(project='p', repository='r', pr=1, head='a'*40, base='b'*40,
                 scope_revision='s', gate='g', stage='READY_FOR_OWNER_AUTHORIZATION',
                 critic=True, specialists=True, final_gate=True)
        events, state = readiness_events([c], {})
        self.assertEqual([e['kind'] for e in events], ['final-human-review'])
        self.assertTrue(events[0]['includes_jira_review'])
        self.assertEqual(readiness_events([c], state)[0], [])
        for field in ('head', 'base', 'scope_revision', 'gate'):
            drift = dict(c, **{field: 'changed'})
            events, _ = readiness_events([drift], state)
            self.assertEqual(events[0]['kind'], 'correction')
        for field in ('critic', 'specialists', 'final_gate'):
            self.assertEqual(readiness_events([dict(c, **{field: False})], {})[0], [])

    def test_periods_dst_catchup_daily_overlap_and_calendar_injection(self):
        from agentic.reporting.events import due_periods, daily_views
        calls = []
        def calendar(root):
            calls.append(root)
            return dict(timezone='Europe/London', eod=dict(time='17:00', weekdays=list(range(1, 8))))
        periods = due_periods('2026-10-25T00:00:00Z', '2026-10-25T03:00:00Z', {}, effective_calendar=calendar)
        hours = [p for p in periods if p['kind'] == 'hourly']
        self.assertEqual(len(hours), 3)
        self.assertEqual(len({p['id'] for p in hours}), 3)
        self.assertTrue(hours[0]['catch_up'])
        self.assertNotEqual(hours[0]['local_end'], hours[1]['local_end'])
        spring = due_periods('2026-03-29T00:00:00Z', '2026-03-29T03:00:00Z', {}, effective_calendar=calendar)
        self.assertFalse(any('T01:00:00' in p['local_end'] for p in spring))
        evening = due_periods('2026-11-07T16:00:00Z', '2026-11-07T18:00:00Z', {}, effective_calendar=calendar)
        self.assertEqual([p['kind'] for p in evening].count('hourly'), 2)
        self.assertEqual([p['kind'] for p in evening].count('eod'), 1)
        views = daily_views('2026-11-07T17:00:00Z', '2026-11-06T17:00:00Z', 'Europe/London')
        self.assertFalse(views['additive'])
        self.assertEqual(views['today']['start'], '2026-11-07T00:00:00Z')
        self.assertTrue(calls)


class MetricTests(unittest.TestCase):
    def test_scope_refuses_foreign_epic_parent_cycle_and_usage_preserves_components(self):
        from agentic.reporting.analysis import scope_metrics, token_metrics
        rows = [dict(key='A', kind='implementation', parent='B', state='READY'),
                dict(key='B', kind='implementation', parent='A', state='READY')]
        with self.assertRaises(ValidationError):
            scope_metrics(rows)
        rows[0]['parent'] = None
        rows[1]['parent'] = None
        rows[0]['epic'] = 'foreign'
        with self.assertRaises(ValidationError):
            scope_metrics(rows)
        observation = dict(id='usage', agent='a', role='review', ticket='T', input=10, output=5, model='m',
                           cached_input=4, reasoning=2, outcome='FAILED')
        result = token_metrics('on', [observation], ['a'])
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)
        self.assertEqual(result['consumed'], 15)
        self.assertEqual(result['by_role']['review'], 15)
        self.assertEqual(result['by_outcome']['FAILED'], 15)

    def test_daily_views_merge_counts_deduplicate_and_never_add_views(self):
        from agentic.reporting.analysis import daily_metrics
        prs = [dict(id='one', merged_at='2026-11-06T18:00:00Z'),
               dict(id='two', merged_at='2026-11-07T10:00:00Z')]
        result = daily_metrics(prs + [prs[1]], [], '2026-11-07T17:00:00Z', '2026-11-06T17:00:00Z', 'Europe/London')
        self.assertEqual(result['today']['merged_pr_count'], 1)
        self.assertEqual(result['since_previous']['merged_pr_count'], 2)
        self.assertFalse(result['additive'])

    def test_epic_token_rollups_reconcile_once_with_overhead_and_keep_unknown(self):
        from agentic.reporting.analysis import token_rollup
        usage = dict(consumed=30, tickets={'A': 10, 'B': 15}, overhead=5)
        result = token_rollup(usage, {'A': 'E', 'B': 'E'},
                              [dict(id='work', epic='E', optimistic=1, base=2, conservative=3),
                               dict(id='overhead', epic=None, optimistic=2, base=3, conservative=4)])
        self.assertEqual(result['epics']['E']['consumed'], 25)
        self.assertEqual(result['scope']['expected_total']['base'], 35)
        self.assertEqual(result['shared']['consumed'], 5)
        self.assertIsNone(token_rollup(dict(usage, consumed=None), {}, [])['scope']['consumed'])
        unavailable = token_rollup(usage, {'A': 'E', 'B': 'E'}, None)
        self.assertIsNone(unavailable['scope']['remaining'])
        self.assertIsNone(unavailable['epics']['E']['expected_total'])

    def test_resource_reservation_conflict_cycle_and_milestone_distinctions(self):
        from agentic.reporting.analysis import milestone_forecast
        def node(key, resource='worker', deps=()):
            return dict(key=key, dependencies=list(deps), resource=resource, conflicts=['shared-file'],
                        remaining_seconds=dict(optimistic=10, base=10, conservative=10))
        graph = [node('A'), node('B'), node('review', 'reviewer', ['A', 'B'])]
        result = milestone_forecast(graph, {'code': ['A', 'B'], 'uat': ['review']},
                                    dict(worker=2, reviewer=1), '2026-11-05T00:00:00Z', reservations=dict(worker=[5, 5]))
        self.assertEqual(result['milestones']['code']['base']['remaining_seconds'], 25)
        self.assertEqual(result['milestones']['uat']['base']['remaining_seconds'], 35)
        self.assertEqual(len(result['schedule']['base']), 3)
        graph[0]['dependencies'] = ['review']
        with self.assertRaises(ValidationError):
            milestone_forecast(graph, {'uat': ['review']}, dict(worker=2, reviewer=1), '2026-11-05T00:00:00Z')

    def test_elapsed_compatible_original_estimate_and_components(self):
        from agentic.reporting.analysis import elapsed_comparison
        from test_reporting_records import request
        ticket = request()['tickets'][1]
        ticket['original_estimate'] = dict(unit='calendar_seconds', value=1800)
        ticket['components_seconds'] = dict(review=600)
        result = elapsed_comparison([ticket])
        self.assertEqual(result['comparisons'][0]['variance']['numerator'], 1800)
        self.assertEqual(result['components']['review']['n'], 1)
        self.assertIn('iqr_seconds', result)
        ticket['original_estimate']['unit'] = 'engineer_days'
        self.assertEqual(elapsed_comparison([ticket])['comparison_unavailable'], 1)
        from agentic.reporting.analysis import estimated_time
        estimates = estimated_time([ticket])
        self.assertEqual(estimates['engineer_days']['estimated_median']['numerator'], 1800)
        self.assertEqual(estimates['engineer_days']['n'], 1)
        self.assertEqual(estimates['engineer_days']['basis'], 'Estimate, not observed elapsed time')

    def test_scope_projection_preserves_dispositions_epic_categories_and_unknowns(self):
        from agentic.reporting.analysis import scope_metrics
        rows = [dict(key='E', kind='epic', priority='High', size='L'),
                dict(key='A', kind='implementation', epic='E', state='READY', priority='High', size='S'),
                dict(key='B', kind='implementation', state='CANCELLED'),
                dict(key='C', kind='implementation', state='DEFERRED'),
                dict(key='D', kind='implementation', state='BLOCKED', conditional=True)]
        result = scope_metrics(rows)
        self.assertEqual(result['remaining'], 1)
        self.assertEqual(result['dispositions'], dict(cancelled=1, deferred=1, conditional=1))
        self.assertEqual(result['epic_priority_size'], {'High': {'L': 1}})
        self.assertEqual(result['planned_dates'][0]['key'], 'E')

    def test_observed_token_forecast_uses_remaining_fraction_and_shared_work_once(self):
        from agentic.reporting.analysis import observed_token_forecast
        history = [dict(id='h1', cohort='small', tokens=100), dict(id='h2', cohort='small', tokens=300)]
        work = [dict(id='A', cohort='small', remaining_numerator=1, remaining_denominator=2),
                dict(id='review', cohort='small', remaining_numerator=1, remaining_denominator=1)]
        result = observed_token_forecast(work, history)
        self.assertEqual(result['remaining'], dict(optimistic=150, base=300, conservative=450))
        self.assertEqual(result['components'][0]['sample_n'], 2)
        self.assertEqual(observed_token_forecast([dict(work[0], cohort='missing')], history)['status'], 'UNAVAILABLE')
        with self.assertRaises(ValidationError):
            observed_token_forecast(work + [work[0]], history)

    def test_dates_epic_due_baseline_predecessors_and_conditional(self):
        from agentic.reporting.analysis import planning_defects
        records = [dict(key='E', kind='epic', planned_start='2026-11-02', planned_end='2026-11-04'),
                   dict(key='A', epic='E', planned_start='2026-11-01', planned_end='2026-11-05', due='2026-11-06'),
                   dict(key='B', epic='E', planned_start='2026-11-03', planned_end='2026-11-04', dependencies=['A']),
                   dict(key='C', epic='E', conditional=True)]
        defects = planning_defects(records)
        self.assertTrue({'EPIC_WINDOW', 'DUE_MISMATCH', 'PREDECESSOR_ORDER', 'PLANNED_DATES_MISSING'} <= {d['reason'] for d in defects})

    def test_dependency_scenarios_capacity_shared_once_external_conditional(self):
        from agentic.reporting.analysis import milestone_forecast
        graph = [dict(key=k, dependencies=deps, remaining_seconds=dict(optimistic=10, base=20, conservative=30), resource='worker')
                 for k, deps in [('A', []), ('B', ['A']), ('C', ['A'])]]
        result = milestone_forecast(graph, {'release': ['B', 'C']},
                                    dict(worker=1), '2026-11-05T00:00:00Z')
        self.assertEqual(result['milestones']['release']['base']['remaining_seconds'], 60)
        self.assertEqual(result['milestones']['release']['optimistic']['remaining_seconds'], 30)
        graph[0]['external_wait'] = 'access decision'
        result = milestone_forecast(graph, {'release': ['B']}, dict(worker=2), '2026-11-05T00:00:00Z')
        self.assertEqual(result['milestones']['release']['base']['status'], 'CONDITIONAL')
        self.assertIsNone(result['milestones']['release']['base']['eta'])
        with self.assertRaises(ValidationError):
            milestone_forecast(graph, {'release': ['B']}, dict(worker=0), '2026-11-05T00:00:00Z')

    def test_tokens_off_unknown_excluded_dedup_coverage_forecast(self):
        from agentic.reporting.analysis import token_metrics
        self.assertEqual(token_metrics('off', [], [])['status'], 'UNAVAILABLE')
        rows = [dict(id='1', agent='awf', role='worker', ticket='A', input=10, output=5, model='m'),
                dict(id='2', agent='main', role='controller', ticket=None, input=999, output=999, model='m'),
                dict(id='3', agent='awf', role='review', ticket=None, input=2, output=3, model='m')]
        result = token_metrics('on', rows + [rows[0]], ['awf'], completed=['A'], complete_tickets=['A'])
        self.assertEqual(result['consumed'], 20)
        self.assertEqual(result['completed_mean'], dict(numerator=15, denominator=1))
        self.assertEqual(result['overhead'], 5)
        rows[0]['input'] = None
        self.assertIsNone(token_metrics('on', rows, ['awf'])['consumed'])


class BodyTests(unittest.TestCase):
    def test_composition_reports_not_started_and_keeps_cohorts_separate(self):
        from agentic.reporting.compose import compose_report
        from agentic.reporting.bodies import render_body
        from test_reporting_records import request
        supplied = request()
        for ticket in supplied['tickets']:
            if ticket['kind'] == 'implementation':
                ticket['state'] = 'READY'
                ticket.pop('first_started_at', None)
                ticket.pop('required_prs', None)
                ticket.pop('reconciled_at', None)
        supplied['tickets'][2]['cohort'] = 'other'
        body = compose_report('hourly', supplied, supplied['binding'], verify_sources=lambda v, b: v)
        self.assertIn('Implementation has not started', render_body(body, 'slack'))
        self.assertEqual(set(body['data']['detail_metrics']['elapsed']), {'standard', 'other'})

    def test_composition_uses_admitted_metrics_and_keeps_off_accounting_unknown(self):
        from agentic.reporting.compose import compose_report
        from agentic.reporting.bodies import render_body
        from test_reporting_records import request
        supplied = request()
        supplied['project_name'] = 'Example'
        supplied['timezone'] = 'Europe/London'
        for kind in ('hourly', 'eod', 'owner-action', 'final-human-review', 'jira-review'):
            body = compose_report(kind, supplied, supplied['binding'], verify_sources=lambda v, b: v)
            self.assertEqual(body['data']['detail_metrics']['scope']['epics'], 1)
            self.assertEqual(body['data']['detail_metrics']['tokens']['status'], 'UNAVAILABLE')
            for platform in ('slack', 'teams'):
                rendered = render_body(body, platform)
                self.assertIn('baseline-1', rendered)
                self.assertIn('Unknown', rendered)
        unverified = compose_report('jira-review', supplied, supplied['binding'])
        self.assertEqual(unverified['data']['freshness'], 'UNVERIFIED')
        self.assertNotIn('scope', unverified['data']['detail_metrics'])
        with self.assertRaises(ValidationError):
            compose_report('hourly', supplied, dict(supplied['binding'], scope_revision='foreign'))

    def test_immediate_alert_template_and_combined_review_content(self):
        from agentic.reporting.bodies import prepare_body, render_body
        data = dict(project='Example', observed_at='2026-11-05T12:00:00Z', timezone='Europe/London',
                    scope='s', candidate='c', summary='Outcome', sections={}, decision='Approve access',
                    decision_owner='Owner', requested_response='today', options=['approve', 'park'],
                    recommendation='approve', delay_consequence='Blocked access', independent_work='Tests', resume='Observed access')
        for kind in ('owner-action', 'final-human-review'):
            for platform in ('slack', 'teams'):
                text = render_body(prepare_body(kind, data), platform)
                labels = ('Decision / readiness', 'Owner action', 'Impact and independent work', 'Evidence')
                bold = '*' if platform == 'slack' else '**'
                positions = [text.index(bold + label + bold + '\n') for label in labels]
                self.assertEqual(positions, sorted(positions))
                for value in ('Approve access', 'Owner', 'today', 'approve', 'park', 'Blocked access', 'Tests', 'Observed access'):
                    self.assertIn(value, text)
                if kind == 'final-human-review':
                    self.assertIn('Epic outlook', text)
                    self.assertIn('Scope and progress', text)

    def test_explicit_fields_and_unicode_limits_detail_retains_full_data(self):
        from agentic.reporting.bodies import prepare_body, render_body, render_detail
        data = dict(project='Example', observed_at='2026-11-05T12:00:00Z', timezone='Europe/London',
                    scope='s', candidate='c', summary='Idle; implementation has not started.', sections={},
                    current=[dict(key='T-1', title='Unicode ✓', stage='PAUSED')], next=[],
                    detail_url='https://example.invalid/report', catch_up=True)
        body = prepare_body('hourly', data, verify_link=lambda u: True)
        for platform in ('slack', 'teams'):
            rendered = render_body(body, platform)
            self.assertIn('Planned start: Unknown: planning defect', rendered)
            self.assertIn('CATCH-UP', rendered)
            self.assertIn('PAUSED', rendered)
            self.assertIn('Unicode', render_detail(body))
            exact = len(rendered.encode('utf-8'))
            self.assertEqual(render_body(body, platform, max_bytes=exact), rendered)
            with self.assertRaises(ValidationError):
                render_body(body, platform, max_bytes=exact-1)

    def test_every_layout_order_platform_links_unknowns_and_limit(self):
        from agentic.reporting.bodies import prepare_body, render_body, LAYOUTS
        data = dict(project='Example', observed_at='2026-11-05T12:00:00Z', timezone='Europe/London',
                    scope='scope-1', candidate='head/base/gate', summary='Implementation has not started.',
                    sections={}, prs=[dict(number=2, url='https://example.invalid/pr/2',
                                          tickets=[dict(key='T-1', url='https://example.invalid/T-1'),
                                                   dict(key='T-2', url='https://example.invalid/T-2')])])
        for kind, titles in LAYOUTS.items():
            body = prepare_body(kind, data, verify_link=lambda u: True)
            for platform in ('slack', 'teams'):
                text = render_body(body, platform)
                positions = [text.index(('*' if platform == 'slack' else '**') + title) for title in titles]
                self.assertEqual(positions, sorted(positions))
                self.assertIn('Unknown', text)
                self.assertIn('scope-1', text)
                if 'PRs and Jira tickets' in titles:
                    self.assertIn('T-1', text)
                    self.assertIn('T-2', text)
                with self.assertRaisesRegex(ValidationError, 'TOO_LARGE'):
                    render_body(body, platform, max_bytes=10)
        with self.assertRaises(ValidationError):
            prepare_body('hourly', data, verify_link=lambda u: False)


class HTTPTests(unittest.TestCase):
    def test_stdlib_http_enforces_timeout_size_no_redirect_and_safe_errors(self):
        from unittest.mock import patch
        from agentic.reporting import http_transport as module
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                self.limit = limit
                return b'{"ok":true}'
        response = Response()
        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return response
        opener = Opener()
        with patch.object(module, 'build_opener', return_value=opener):
            self.assertEqual(module.http_json('POST', 'https://slack.com/api/chat.postMessage',
                                             {'Authorization': 'Bearer fixture'}, {'text': 'safe'}), {'ok': True})
        self.assertEqual(opener.timeout, 15)
        self.assertEqual(response.limit, module.MAX_RESPONSE + 1)
        self.assertIsNone(module.NoRedirect().redirect_request(None, None, None, None, None, None))
        with self.assertRaises(ValidationError):
            module.http_json('GET', 'https://foreign.invalid', {}, None)

    def test_wire_size_refuses_before_http_and_errors_never_escape(self):
        from agentic.reporting.http_transport import SlackTransport, TeamsTransport
        from agentic.reporting.delivery import delivery_intent
        from test_reporting_delivery import binding
        import traceback
        for cls, platform in ((SlackTransport, 'slack'), (TeamsTransport, 'teams')):
            calls = []
            transport = cls(context='c', sender='s', http=lambda *a: calls.append(a))
            b = binding(platform)
            b['destination'] = 'C1' if platform == 'slack' else 't/c'
            intent = delivery_intent('report', b, 'x' * (12000 if platform == 'slack' else 20000))
            with self.assertRaises(ValidationError):
                transport.send(intent, 'a', 'credential')
            self.assertEqual(calls, [])
            def failure(*args):
                raise RuntimeError('CREDENTIAL_MUST_NOT_ESCAPE')
            transport.http = failure
            try:
                transport.call('GET', 'https://example.invalid', 'credential')
            except ValidationError:
                self.assertNotIn('CREDENTIAL_MUST_NOT_ESCAPE', traceback.format_exc())
            else:
                self.fail('provider error must refuse')

    def test_readback_requires_exact_links_sender_and_all_pages(self):
        from agentic.reporting.http_transport import TeamsTransport, SlackTransport
        from agentic.reporting.delivery import delivery_intent
        from test_reporting_delivery import binding
        b = binding('teams')
        b['destination'] = 't/c'
        intent = delivery_intent('report', b, '[Detail](https://example.invalid/one)')
        transport = TeamsTransport(context='ctx', sender='sender', http=lambda *a: None)
        wire = transport.wire_text(intent)
        transport.http = lambda *a: dict(value=[dict(id='m', body=dict(contentType='html', content=wire.replace('/one', '/two')),
                                                  **{'from': dict(user=dict(id='sender'))})])
        self.assertEqual(transport.reconcile(intent, ['a'], 'secret')['status'], 'UNKNOWN')
        for url in ('https://foreign.invalid/messages', 'https://graph.microsoft.com/v1.0/me'):
            transport.http = lambda *a: {'value': [], '@odata.nextLink': url}
            with self.assertRaises(ValidationError):
                transport.reconcile(intent, ['a'], 'secret')
        b = binding('slack')
        b['destination'] = 'C1'
        intent = delivery_intent('report', b, 'text')
        transport = SlackTransport(context='ctx', sender='sender')
        calls = []
        def pages(method, url, headers, payload):
            calls.append(url)
            if 'cursor=next' not in url:
                return dict(ok=True, messages=[], response_metadata=dict(next_cursor='next'))
            return dict(ok=True, messages=[dict(user='sender', ts='m', text=transport.wire_text(intent))])
        transport.http = pages
        self.assertEqual(transport.reconcile(intent, ['a'], 'secret')['status'], 'VISIBLE')
        self.assertEqual(len(calls), 2)

    def test_real_transport_contract_with_fake_http_only(self):
        from agentic.reporting.http_transport import SlackTransport, TeamsTransport
        from agentic.reporting.delivery import delivery_intent
        from test_reporting_delivery import binding
        for platform, cls, destination in [('slack', SlackTransport, 'C1'), ('teams', TeamsTransport, 'team/channel')]:
            calls = []
            b = binding(platform)
            b['destination'] = destination
            intent = delivery_intent('report', b, 'A safe report')
            transport = cls(context='context', sender='sender', http=lambda *a: None)
            marker = transport.wire_text(intent)
            def http(method, url, headers, payload):
                calls.append((method, url, payload))
                if 'auth.test' in url:
                    return dict(ok=True, team_id='context', user_id='sender')
                if 'conversations.info' in url:
                    return dict(ok=True, channel=dict(id='C1', is_member=True))
                if 'conversations.history' in url:
                    return dict(ok=True, messages=[dict(ts='1.0', text=marker, user='sender')], has_more=False)
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
                if method == 'GET' and '/messages' in url:
                    return dict(value=[dict(id='1', body=dict(content=marker, contentType='text'),
                                           **{'from': dict(user=dict(id='sender'))})])
                return dict(ok=True, id='1')
            transport.http = http
            module = importlib.import_module(platform + '_reporting_adapter')
            adapter = module.ReportingAdapter(transport, b, resolve_secret=lambda ref: 'fixture-secret', mode=platform)
            self.assertEqual(adapter.send(intent, 'a')['status'], 'ACCEPTED_UNVERIFIED')
            self.assertEqual(adapter.reconcile(intent, ['a'])['status'], 'VISIBLE')
            self.assertTrue(any(c[0] == 'POST' for c in calls))
            self.assertNotIn('fixture-secret', str(calls))
            transport.http = lambda *args: dict(ok=True, messages=[], value=[])
            self.assertEqual(transport.reconcile(intent, ['a'], 'fixture-secret')['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
