"""Owner-readability regressions through reporting, controller and CLI ports."""
from test_reporting_timing import _logical_slow_calls
from copy import deepcopy
from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import runpy
import tempfile
import unittest

import source_only
from agentic.continuous_controller import ContinuousControllerStore
from agentic.reporting.runtime import reporting_cycle
from agentic.reporting.bodies import render_body
from test_reporting_records import request
from test_reporting_wiring import binding
import test_reporting_e3_regressions as e3
import test_reporting_e6_regressions as e6
import test_reporting_e12_regressions as e12


NOW = '2026-11-11T17:00:00Z'
ACTION = dict(id='access', decision='Grant access', owner='owner', due='today',
              reason='Access blocks work', options=['Grant', 'Park'], resume='Access observed',
              recommendation='Grant read access')


def records(now=NOW):
    r = request()
    r.update(created_at=now, prs=[], previous_daily_end='2026-11-10T17:00:00Z')
    r['sources'][0]['observed_at'] = now
    r['tickets'][1].update(first_started_at='2026-11-11T10:00:00Z', reconciled_at='2026-11-11T11:10:00Z')
    r['tickets'][1]['required_prs'][0]['merged_at'] = '2026-11-11T11:00:00Z'
    return r


def pr(n):
    return dict(id=str(n), number=n, url='https://github.example/org/repo/pull/' + str(n),
        merged_at='2026-11-11T11:00:00Z', head='a'*40, merge_commit='b'*40, status='merged')


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ContinuousControllerStore(Path(self.temp.name) / 'state', ['A'])
        self.binding = records()['binding']
        self.operating = dict(reporting=dict(mode='both', bindings=[dict(binding(p), **self.binding)
            for p in ('slack', 'teams')]))

    def run_report(self, r=None, now=NOW, **context):
        r = records(now) if r is None else r
        before = deepcopy(r)
        result = reporting_cycle(self.store, operating=self.operating, binding=self.binding, now=now,
            context=dict(records=r, verify_link=lambda url: True, **context), defer_delivery=True)
        self.assertEqual(r, before)
        self.assertGreater(result['generated'], 0)
        return result

    def report(self, result, kind='eod'):
        return next(r['body'] for r in result['reports'] if r['kind'] == kind)

    def test_bad_forecast_inputs_do_not_suppress_reports_or_owner_alert(self):
        for name in ('capacity', 'remaining', 'dependency'):
            with self.subTest(name=name):
                self.setUp()
                r = records()
                node = dict(key='T-1', resource='workers', dependencies=[],
                            remaining_seconds=dict(optimistic=1, base=2, conservative=3))
                f = dict(nodes=[node], milestones={'E-1': ['T-1']}, capacity={'workers': 1})
                if name == 'capacity': f['capacity']['workers'] = 0
                if name == 'remaining': node['remaining_seconds'] = None
                if name == 'dependency': node['dependencies'] = ['unobserved']
                r['forecast'] = f
                result = self.run_report(r, owner_actions=[ACTION])
                self.assertEqual({r['kind'] for r in result['reports']}, {'hourly', 'eod', 'owner-action'})
                self.assertEqual(self.report(result)['data']['detail_metrics']['forecast']['status'], 'UNAVAILABLE')
                self.assertIn('UNAVAILABLE', render_body(self.report(result), 'slack'))

    def test_bad_actuals_are_unavailable_without_losing_valid_delivery(self):
        for name in ('bad-start', 'bad-reconciliation', 'bad-merge', 'early-reconciliation', 'late-start'):
            with self.subTest(name=name):
                self.setUp()
                r = records()
                t = r['tickets'][1]
                valid = deepcopy(t)
                valid['key'] = 'T-4'
                r['tickets'].append(valid)
                r['included'].append('T-4')
                if name == 'bad-start': t['first_started_at'] = 'bad'
                if name == 'bad-reconciliation': t['reconciled_at'] = 'bad'
                if name == 'bad-merge': t['required_prs'][0]['merged_at'] = 'bad'
                if name == 'early-reconciliation': t['reconciled_at'] = '2026-11-11T10:30:00Z'
                if name == 'late-start': t['first_started_at'] = '2026-11-11T12:00:00Z'
                body = self.report(self.run_report(r, owner_actions=[ACTION]))
                self.assertEqual(body['data']['detail_metrics']['elapsed']['standard']['n'], 1)
                self.assertIn('UNAVAILABLE', render_body(body, 'slack'))

    def test_large_report_truncates_with_counts_retains_detail_and_alert(self):
        r = records()
        r['prs'] = [pr(n) for n in range(1, 101)]
        r['tickets'] = [dict(key='T-'+str(n), kind='implementation', state='WORKING') for n in range(30)]
        r['included'] = [t['key'] for t in r['tickets']]
        r['excluded'] = []
        result = self.run_report(r, owner_actions=[ACTION])
        self.assertEqual(len(result['reports']), 3)
        self.assertEqual(len(result['deliveries']), 0)  # deferred, intents retained
        for report in result['reports']:
            for platform, limit in (('slack', 12000), ('teams', 20000)):
                text = render_body(report['body'], platform)
                self.assertLessEqual(len(text.encode()), limit)
                self.assertIn('omitted', text)
                if report['kind'] == 'owner-action': self.assertIn('Grant access', text)
            self.assertEqual(len(report['body']['data']['prs']), 100)

    def test_resolution_and_recommendation_read_as_observed(self):
        first = self.run_report(owner_actions=[ACTION])
        self.assertIn(ACTION['recommendation'], render_body(self.report(first, 'owner-action'), 'slack'))
        result = self.run_report(records('2026-11-11T17:00:01Z'), now='2026-11-11T17:00:01Z', owner_actions=[])
        text = render_body(self.report(result, 'owner-action'), 'slack')
        self.assertIn('Owner action resolved', text)
        self.assertNotIn('Owner action required', text)
        self.assertNotIn('Options:', text)
        self.assertNotIn('options:', text.lower())
        self.assertIn('No further action', text)

    def test_hourly_preserves_changes_queue_conflicts_and_observed_work(self):
        r = records()
        r['tickets'][1].update(state='READY', first_started_at='2026-11-11T10:00:00Z')
        r.update(changes='Merged PR 9', review_queue='Reviewer queue 2', conflicts='Shared file busy')
        streams = [dict(ticket='T-1', state='WORKING', stream='A', actor='worker', activity='Coding', verification_gate='Review')]
        body = self.report(self.run_report(r, streams=streams, gates=['review'], capacity={'workers': 1}), 'hourly')
        text = render_body(body, 'slack')
        for expected in ('Merged PR 9', 'Reviewer queue 2', 'Shared file busy', 'Elapsed: 7 h'):
            self.assertIn(expected, text)
        self.assertNotIn('Implementation has not started', text)

    def test_observed_working_stream_overrides_ready_without_start_time(self):
        r = records()
        r['tickets'][1]['state'] = 'READY'
        r['tickets'][1].pop('first_started_at')
        streams = [dict(ticket='T-1', state='WORKING', stream='A', actor='worker', activity='Coding', verification_gate='Review')]
        body = self.report(self.run_report(r, streams=streams), 'hourly')
        text = render_body(body, 'slack')
        self.assertIn('Stage: WORKING', text)
        self.assertNotIn('Implementation has not started', text)

    def test_review_scope_displays_accepted_denominator_and_blocked_count(self):
        from agentic.reporting.events import approval_event
        r = records()
        r['tickets'][2]['state'] = 'BLOCKED'
        approval = approval_event(self.binding['scope_revision'], 'baseline-1',
            dict(verified=True, revision='baseline-1', evidence_id='synthetic-approval'))
        text = render_body(self.report(self.run_report(r, baseline_approval=approval), 'jira-review'), 'slack')
        self.assertIn('Delivered: 1/2 | Blocked: 1', text)

    def test_unknown_inventory_does_not_claim_remaining_or_planning_defects(self):
        r = records()
        r['tickets'] = [dict(key='T-'+str(n), kind='implementation', state='UNKNOWN', planning_observed=False) for n in range(3)]
        r['included'] = [t['key'] for t in r['tickets']]
        text = render_body(self.report(self.run_report(r)), 'slack')
        self.assertIn('remaining: Unknown', text)
        self.assertIn('Unclassified: 3', text)
        self.assertNotIn('PLANNED DATES MISSING', text)
        self.assertNotIn('Correct accepted planning defects', text)

    def test_nonterminal_states_do_not_erase_delivered(self):
        for state in ('BLOCKED', 'PAUSED', 'REVIEW_PASSED', 'OWNER_APPROVED'):
            with self.subTest(state=state):
                self.setUp()
                r = records()
                r['tickets'][2]['state'] = state
                text = render_body(self.report(self.run_report(r)), 'slack')
                self.assertIn('Tickets delivered: 1 reconciled today', text)

    def test_ineligible_children_do_not_make_working_parent_disappear(self):
        for state, conditional in (('CANCELLED', False), ('READY', True)):
            with self.subTest(state=state, conditional=conditional):
                self.setUp()
                r = records()
                r['tickets'][1]['state'] = 'WORKING'
                r['tickets'][2].update(parent='T-1', state=state, conditional=conditional)
                body = self.report(self.run_report(r))
                self.assertEqual(body['data']['detail_metrics']['scope']['remaining'], 1)

    def test_dispositions_coverage_pending_and_readable_time(self):
        r = records()
        r['tickets'][2]['state'] = 'WORKING'
        for key, state in (('T-4', 'MERGED'), ('T-5', 'SUPERSEDED'), ('T-6', 'DUPLICATE')):
            r['tickets'].append(dict(key=key, kind='implementation', state=state, cohort='standard'))
            r['included'].append(key)
        body = self.report(self.run_report(r))
        self.assertEqual(body['data']['detail_metrics']['scope']['remaining'], 2)
        self.assertEqual(body['data']['detail_metrics']['elapsed']['standard']['coverage'], dict(numerator=1, denominator=1))
        text = render_body(body, 'slack')
        self.assertIn('mean hours: 1', text)
        self.assertIn('Merged pending reconciliation: T-4', text)

    def test_ticket_delivery_visible_when_prs_unavailable(self):
        r = records()
        r['prs'] = None
        self.assertIn('Tickets delivered: 1 reconciled today', render_body(self.report(self.run_report(r)), 'slack'))

    def test_local_period_and_scenario_labels(self):
        now = '2026-07-06T16:00:00Z'
        r = records(now)
        r['previous_daily_end'] = '2026-07-05T16:00:00Z'
        r['tickets'] = [dict(key='E-1', kind='epic'), dict(key='T-1', kind='implementation', state='READY', epic='E-1')]
        r['included'] = ['E-1', 'T-1']
        r['forecast'] = dict(nodes=[dict(key='T-1', resource='workers', dependencies=[],
            remaining_seconds=dict(optimistic=3600, base=7200, conservative=10800))], milestones={'E-1': ['T-1']}, capacity={'workers': 1})
        body = self.report(self.run_report(r, now=now))
        text = render_body(body, 'slack')
        self.assertIn('2026-07-06 00:00 BST', text)
        self.assertIn('2026-07-06 17:00 BST', text)
        self.assertNotIn('Period: id:', text)
        self.assertIn('Base:', text)
        self.assertIn('Optimistic', text)
        self.assertNotIn('remaining seconds:', text)

    def test_enabled_ledger_accounting_reaches_epics_mean_mix_and_forecast(self):
        from agentic.budgeting import BudgetLedger, fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        from agentic.reporting.observations import attributed_accounting
        class Host:
            def observe_enrollment(self, value):
                return deepcopy(value)
        registry = EnrollmentRegistry(Path(self.temp.name) / 'enrollment', Host())
        path = Path(self.temp.name) / 'ledger'
        ledger = BudgetLedger(path, registry)
        settings = dict(fresh_settings(), accounting_enabled=True)
        b = dict(project_id=self.binding['project'], ticket_id='T-1', run_id='run', dispatch_id='dispatch', session_id='session', role='worker')
        registry.enroll(b)
        ledger.reserve(b, settings, 5000)
        ledger.settle(b, 5000, 'usage')
        before = path.read_bytes()
        summary = attributed_accounting(path, dict(budgeting=settings), self.binding['project'])
        r = records()
        accounting = dict(mode='on', observations=summary.get('observations', []),
            enrolled=summary.get('enrolled', []), complete_tickets=summary.get('complete_tickets', []),
            forecasts=[dict(id='remaining', optimistic=10, base=20, conservative=30, basis_observation_ids=['run'])])
        r['token_components'] = [dict(accounting['forecasts'][0], epic='E-1')]
        body = self.report(self.run_report(r, accounting=accounting, accounting_summary=summary))
        metrics = body['data']['detail_metrics']['tokens']
        self.assertEqual(metrics['consumed'], 5000)
        self.assertEqual(metrics['completed_mean'], dict(numerator=5000, denominator=1))
        self.assertEqual(metrics['expected_total']['base'], 5020)
        self.assertEqual(sum(metrics['model_mix'].values()), 5000)
        self.assertEqual(body['data']['detail_metrics']['token_rollup']['epics']['E-1']['consumed'], 5000)
        self.assertNotIn('accounting OFF or incomplete', render_body(body, 'slack'))
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn('budgeting', self.operating)


def invalid_ports(case):
    ports = e12.source_ports(case)
    def change(snapshot):
        r = snapshot['records']
        r['tickets'][1]['reconciled_at'] = 'bad'
        r['forecast'] = dict(nodes=[], milestones={}, capacity={'workers': 0})
        return snapshot
    return dict(observe_reporting_sources=lambda q: change(ports['observe_reporting_sources'](q)),
        verify_reporting_sources=lambda a, q: dict(ports['verify_reporting_sources'](a, q),
            snapshot=change(ports['verify_reporting_sources'](a, q)['snapshot'])))


def presentation_ports(case):
    case.resolved = False
    case.unknown = False
    def snapshot(query):
        r = records(query['observed_at'])
        r['binding'] = deepcopy(query['binding'])
        r.update(changes='Merged PR 9', review_queue='Reviewer queue 2', conflicts='Shared file busy')
        r['tickets'][2]['state'] = 'BLOCKED'
        for n, state in enumerate(('PAUSED', 'REVIEW_PASSED', 'OWNER_APPROVED', 'MERGED', 'SUPERSEDED', 'DUPLICATE'), 4):
            r['tickets'].append(dict(key='T-'+str(n), kind='implementation', state=state, cohort='standard'))
        r['tickets'] += [dict(key='T-10', kind='implementation', state='WORKING'),
            dict(key='T-11', kind='implementation', parent='T-10', state='CANCELLED')]
        for t in r['tickets']:
            t['planning_observed'] = False
        r['prs'] = [dict(pr(n), url='https://github.com/example/project/pull/' + str(n)) for n in range(1, 101)]
        if case.unknown:
            r['tickets'] = [dict(key='T-'+str(n), kind='implementation', state='UNKNOWN', planning_observed=False) for n in range(3)]
        r['included'], r['excluded'] = [t['key'] for t in r['tickets']], []
        return dict(binding=deepcopy(query['binding']), observed_at=query['observed_at'], records=r,
                    owner_actions=[] if case.resolved else [deepcopy(ACTION)])
    return dict(observe_reporting_sources=lambda q: dict(snapshot(q), authentication={'evidence_id': 'presentation'}),
        verify_reporting_sources=lambda a, q: dict(status='VERIFIED', evidence_id=a['evidence_id'], snapshot=snapshot(q)))


def presentation_probe(test, case, run):
    run(NOW)
    reports = case.status()['reports']
    body = next(r['body'] for r in reversed(reports) if r['kind'] == 'eod')
    metrics = body['data']['detail_metrics']
    test.assertEqual(metrics['daily']['today']['reconciled_ticket_count'], 1)
    test.assertEqual(metrics['scope']['remaining'], 6)
    test.assertEqual(metrics['elapsed']['standard']['coverage'], dict(numerator=1, denominator=1))
    test.assertEqual(len(body['data']['prs']), 100)
    test.assertIn('Merged pending reconciliation: T-7', render_body(body, 'slack'))
    hourly = next(r['body'] for r in reversed(reports) if r['kind'] == 'hourly')
    for value in ('Merged PR 9', 'Reviewer queue 2', 'Shared file busy'):
        test.assertIn(value, render_body(hourly, 'slack'))
    alert = next(r['body'] for r in reversed(reports) if r['kind'] == 'owner-action')
    test.assertEqual(alert['data']['recommendation'], ACTION['recommendation'])
    test.assertIn('Grant access', render_body(alert, 'slack'))
    alert_ids = {d for r in reports if r['kind'] == 'owner-action' for d in r['delivery_ids']}
    test.assertEqual({d['status'] for d in case.status()['deliveries'] if d['delivery_id'] in alert_ids}, {'DELIVERED'})
    # Three report kinds share the unchanged 32-call admission. Reconcile any
    # landed third message on the next cycle rather than relaxing that ceiling.
    run('2026-11-11T17:00:01Z')
    ids = {d for r in reports if r.get('period', {}).get('end') == NOW or r['kind'] == 'owner-action' for d in r['delivery_ids']}
    test.assertEqual({d['status'] for d in case.status()['deliveries'] if d['delivery_id'] in ids}, {'DELIVERED'})
    case.resolved = True
    run('2026-11-11T17:00:02Z')
    alert = next(r['body'] for r in reversed(case.status()['reports']) if r['kind'] == 'owner-action')
    test.assertIn('Owner action resolved', render_body(alert, 'slack'))
    test.assertNotIn('Options:', render_body(alert, 'slack'))
    test.assertNotIn('options:', render_body(alert, 'slack').lower())
    case.unknown = True
    run('2026-11-11T18:00:00Z')
    latest = next(r['body'] for r in reversed(case.status()['reports']) if r['kind'] == 'hourly')
    test.assertIsNone(latest['data']['detail_metrics']['scope']['remaining'])
    test.assertEqual(latest['data']['detail_metrics']['scope']['unclassified'], 3)
    test.assertEqual(latest['data']['detail_metrics']['scope']['planning_defects'], [])


class ControllerTests(unittest.TestCase):
    def test_controller_owner_readability_and_large_alert_delivery(self):
        e6.ControllerTests.setUp(self)
        self.status = lambda: e6.ControllerTests.status(self)
        self.adapters.update(presentation_ports(self))
        def run(now):
            self.assertEqual(e6.ControllerTests.cycle(self, now)['errors'], [])
        presentation_probe(self, self, run)

    def test_controller_invalid_figures_still_land_both_reports(self):
        e6.ControllerTests.setUp(self)
        self.adapters.update(invalid_ports(self))
        result = e6.ControllerTests.cycle(self, NOW)
        self.assertEqual(result['errors'], [])
        reports = e6.ControllerTests.status(self)['reports']
        self.assertEqual({r['kind'] for r in reports}, {'hourly', 'eod'})
        self.assertEqual({d['status'] for d in e6.ControllerTests.status(self)['deliveries']}, {'DELIVERED'})


class CliTests(unittest.TestCase):
    @_logical_slow_calls
    def test_cli_owner_readability_and_large_alert_delivery(self):
        def inspect(case, result, output):
            case.helper.listed = []
            e3.CLI_ADAPTERS.update(presentation_ports(case))
            presentation_probe(self, case, lambda now: e12.E12CliTests.cycle(self, case, now))
        e6.CliTests.probe(self, lambda case, net: None, inspect)

    @_logical_slow_calls
    def test_cli_invalid_figures_still_land_both_reports(self):
        def inspect(case, result, output):
            case.helper.listed = []
            e3.CLI_ADAPTERS.update(invalid_ports(case))
            e12.E12CliTests.cycle(self, case, NOW)
            reports = case.status()['reports']
            latest = [r for r in reports if r.get('period', {}).get('end') == NOW]
            self.assertEqual({r['kind'] for r in latest}, {'hourly', 'eod'})
            ids = {d for r in latest for d in r['delivery_ids']}
            self.assertEqual({d['status'] for d in case.status()['deliveries'] if d['delivery_id'] in ids}, {'DELIVERED'})
        e6.CliTests.probe(self, lambda case, net: None, inspect)

    @_logical_slow_calls
    def test_cli_reads_enabled_canonical_ledger_without_enabling_or_writing_it(self):
        from agentic.budgeting import BudgetLedger, fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        from agentic.canonical import sha256
        def inspect(case, result, output):
            root = Path(case.helper.temp.name)
            project = root / 'cli-project'
            operating_path = project / 'OPERATING_CONFIG.yaml'
            operating = json.loads(operating_path.read_text())
            settings = dict(fresh_settings(), accounting_enabled=True)
            operating['budgeting'] = settings
            operating_path.write_text(json.dumps(operating), encoding='utf-8')
            from settings_pr_fixtures import publish_fixture_settings
            publish_fixture_settings(case, project, json.loads((project / '.agentic/PROJECT_CONFIG.yaml').read_text()), operating, preserve_checkout=True)
            from test_reporting_fixtures import refresh_reporting_repository
            refresh_reporting_repository(case, project)
            operating_before = operating_path.read_bytes()
            class Host:
                def observe_enrollment(self, value): return deepcopy(value)
            registry = EnrollmentRegistry(root / 'enrollment', Host())
            path = root / 'ledger'
            ledger = BudgetLedger(path, registry)
            b = dict(project_id=case.binding['project'], ticket_id='T-1', run_id='run', dispatch_id='dispatch', session_id='session', role='worker')
            registry.enroll(b)
            ledger.reserve(b, settings, 5000)
            ledger.settle(b, 5000, 'usage')
            ledger_before = path.read_bytes()
            def snapshot(query):
                r = records(query['observed_at'])
                r['binding'] = deepcopy(query['binding'])
                r['accounting'] = dict(mode='off', observations=[dict(id='forged', total=999999)],
                    forecasts=[dict(id='remaining', optimistic=10, base=20, conservative=30, basis_observation_ids=['run'])])
                r['token_components'] = [dict(r['accounting']['forecasts'][0], epic='E-1')]
                return dict(binding=deepcopy(query['binding']), observed_at=query['observed_at'], records=r, owner_actions=[])
            e3.CLI_ADAPTERS.update(observe_reporting_sources=lambda q: dict(snapshot(q), authentication={'evidence_id': 'ledger-report'}),
                verify_reporting_sources=lambda a, q: dict(status='VERIFIED', evidence_id=a['evidence_id'], snapshot=snapshot(q)),
                controller_clock=lambda: NOW)
            adapter = root / 'cli_adapter.py'
            case.clock.value = NOW
            case.helper.now = NOW
            cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/continuous_controller.py'))
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli['main'](['--state', str(case.helper.path), '--budget-ledger', str(path),
                    '--worktree-root', str(project / 'worker-worktrees'), '--cadence-seconds', '3600', '--stream', 'A', '--stream', 'B', '--stream', 'C',
                    '--project-config', str(project / '.agentic/PROJECT_CONFIG.yaml'), '--adapter-module', str(adapter),
                    '--adapter-sha256', sha256(adapter.read_bytes()), 'cycle', '--inventory-binding', str(root / 'binding.json'),
                    '--repository-root', str(project), '--repository-head-sha', case.repository['repository_head_sha'],
                    '--repository-tree-sha', case.repository['repository_tree_sha'], '--now', NOW, '--host-capacity', '3'], default_root=project)
            self.assertEqual(code, 0, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['errors'], [])
            body = next(r['body'] for r in reversed(case.status()['reports']) if r['kind'] == 'eod')
            usage = body['data']['detail_metrics']['tokens']
            self.assertEqual(usage['consumed'], 5000)
            self.assertEqual(usage['completed_mean'], dict(numerator=5000, denominator=1))
            self.assertEqual(usage['expected_total']['base'], 5020)
            self.assertEqual(sum(usage['model_mix'].values()), 5000)
            self.assertEqual(body['data']['detail_metrics']['token_rollup']['epics']['E-1']['consumed'], 5000)
            self.assertNotIn('accounting OFF or incomplete', render_body(body, 'slack'))
            self.assertEqual(operating_path.read_bytes(), operating_before)
            self.assertEqual(path.read_bytes(), ledger_before)
        e6.CliTests.probe(self, lambda case, net: None, inspect)
