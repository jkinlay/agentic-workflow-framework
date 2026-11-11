"""Pre-E14: admitted controller/CLI observations, fake HTTP and disk restart."""
from copy import deepcopy
from test_reporting_timing import _logical_slow_calls
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import test_reporting_e3_regressions as e3
import test_reporting_e6_regressions as e6
import test_reporting_e12_regressions as e12
from test_reporting_e15_regressions import ACTION, records
from agentic.canonical import loads


def factory(case):
    import test_reporting_e2_regressions as e2
    from agentic.reporting.http_transport import http_json
    root = Path(case.helper.temp.name) / 'e16-host'
    root.mkdir(exist_ok=True)
    if not hasattr(case, 'e16_config'):
        case.e16_config = e2._base_config(root)
    config = deepcopy(case.e16_config)
    config['github'].update(project_id=case.binding['project'], scope_sha256=case.binding['scope_revision'])
    config.update(_runner=case.runner, _clock=case.clock, _reporting_http=http_json,
        reporting_sources=dict(binding=deepcopy(case.binding), pull_requests=[99]),
        reporting={p: dict(context='workspace' if p == 'slack' else 'tenant', sender='sender')
                   for p in ('teams', 'slack')})
    return case.namespace['build_adapters'](config)


def ports(case):
    case.scenario = 'capacity'
    case.actions = []
    def snapshot(q):
        r = records(q['observed_at'])
        r['binding'] = deepcopy(q['binding'])
        if case.scenario in {'capacity', 'empty', 'omitted', 'dependency'}:
            for t in r['tickets'][1:]:
                t.update(state='READY', dependencies=[])
            r['forecast'] = dict(nodes=[dict(key=k, resource='workers', dependencies=[],
                remaining_seconds=dict(optimistic=3600, base=3600, conservative=3600)) for k in ('T-1', 'T-2')],
                milestones={'E-1': ['T-1', 'T-2']}, capacity={'workers': 2})
            if case.scenario == 'empty':
                r['forecast'].update(nodes=[], milestones={'E-1': []})
            if case.scenario == 'omitted':
                r['forecast']['milestones']['E-1'] = ['T-1']
            if case.scenario == 'dependency':
                r['tickets'][2]['dependencies'] = ['T-1']
        elif case.scenario == 'tokens':
            r['tickets'][1]['required_prs'][0]['merged_at'] = None
        elif case.scenario == 'baseline':
            from agentic.reporting.events import approval_event
            notice = approval_event(q['binding']['scope_revision'], 'baseline-1',
                dict(verified=True, revision='baseline-1', evidence_id='accepted-baseline'))
            r.update(baseline_revision='baseline-1', owner_approval=notice['record']['approval'], review=notice)
        return dict(binding=deepcopy(q['binding']), observed_at=q['observed_at'], records=r,
                    owner_actions=deepcopy(case.actions))
    return dict(observe_reporting_sources=lambda q: dict(snapshot(q), authentication={'evidence_id': 'e16'}),
        verify_reporting_sources=lambda a, q: dict(status='VERIFIED', evidence_id=a['evidence_id'], snapshot=snapshot(q)))


def reports(case):
    # Inspect every retained scope without changing the CLI's current binding.
    with case.helper.store.connection() as db:
        return [loads(r[0]) for r in db.execute('SELECT payload_json FROM controller_reports ORDER BY rowid')]


def states(case, selected):
    from agentic.reporting.delivery import DeliveryOutbox
    return [DeliveryOutbox(case.helper.store).get(d) for r in selected for d in r['delivery_ids']]


class Probes:
    def forecast_probe(self, case, run, scenario):
        case.scenario = scenario
        run('2026-11-11T17:00:00Z')
        e6.ControllerTests.restart(case)
        run('2026-11-11T17:00:01Z')
        report = next(r for r in reversed(reports(case)) if r['kind'] == 'eod')
        m = report['body']['data']['detail_metrics']
        self.assertEqual(m['scope']['remaining'], 2)
        f = m['forecast']
        if scenario == 'capacity':
            self.assertEqual(f['capacity']['workers'], 1)
            self.assertEqual(f['milestones']['E-1']['base']['remaining_seconds'], 7200)
            # The observed resource bottleneck limits the host's three slots.
            self.assertEqual(f['status'], 'SCENARIOS')
        else:
            self.assertEqual(f['status'], 'UNAVAILABLE')
            self.assertIn('REPORT_FORECAST_', f['reason'])
        self.assertEqual({s['status'] for s in states(case, [report])}, {'DELIVERED'})

    def action_probe(self, case, run, mode):
        case.scenario = 'actions'
        case.actions = [deepcopy(ACTION)]
        if mode != 'delivered':
            case.network.read_throttled = {'auth.test', '/me?'} if mode != 'mixed' else {'/me?'}
        if mode == 'legacy':
            import agentic.reporting.runtime as runtime
            retain = runtime._retain
            def old_record(*args, **kwargs):
                kwargs = dict(kwargs)
                if 'extra' in kwargs:
                    kwargs['extra'] = deepcopy(kwargs['extra'])
                    kwargs['extra'].pop('owner_action_event', None)
                    kwargs['extra'].pop('after_deliveries', None)
                return retain(*args, **kwargs)
            with patch.object(runtime, '_retain', old_record):
                run('2026-11-11T17:00:00Z')
        else:
            run('2026-11-11T17:00:00Z')
        original = next(r for r in reversed(reports(case)) if 'Grant access' in r['body']['data']['summary']
                        or r['body']['data'].get('decision') == 'Grant access')
        prior = states(case, [original])
        delivered = sum(s['status'] == 'DELIVERED' for s in prior)
        self.assertEqual(delivered, 2 if mode == 'delivered' else 1 if mode == 'mixed' else 0)
        if mode == 'updated':
            case.actions[0]['decision'] = 'Approve replacement'
        else:
            case.actions = []
        run('2026-11-11T17:00:01Z')
        case.network.read_throttled.clear()
        for second in (2, 3, 4):
            e6.ControllerTests.restart(case)
            run('2026-11-11T17:01:0%dZ' % second)
        final = states(case, [original])
        self.assertEqual(sum(s['status'] == 'DELIVERED' for s in final), delivered)
        self.assertEqual(sum(s['status'] == 'WITHDRAWN' for s in final), 2 - delivered)
        self.assertTrue(all(s['attempts'] == [] for s in final if s['status'] == 'WITHDRAWN'))
        new = [r for r in reports(case) if r['kind'] == 'owner-action' and r['report_id'] != original['report_id']]
        self.assertEqual(len(new), 1)
        self.assertEqual({s['status'] for s in states(case, new)}, {'DELIVERED'})
        posted = [str(b) for p, b in case.network.posts]
        self.assertEqual(sum('Owner action required' in b and 'Grant access' in b for b in posted), delivered)

    def reopen_probe(self, case, run, legacy=False):
        case.scenario = 'actions'
        case.actions = [deepcopy(ACTION)]
        run('2026-11-11T12:00:00Z')
        case.network.read_throttled = {'auth.test', '/me?'}
        case.actions = []
        if legacy:
            import agentic.reporting.runtime as runtime
            retain = runtime._retain
            def old_record(*args, **kwargs):
                kwargs = dict(kwargs)
                if 'extra' in kwargs:
                    kwargs['extra'] = deepcopy(kwargs['extra'])
                    kwargs['extra'].pop('owner_action_event', None)
                    kwargs['extra'].pop('after_deliveries', None)
                return retain(*args, **kwargs)
            with patch.object(runtime, '_retain', old_record):
                run('2026-11-11T12:00:01Z')
        else:
            run('2026-11-11T12:00:01Z')
        resolved = next(r for r in reversed(reports(case)) if r['body']['data'].get('action_resolved'))
        self.assertTrue(all(not s['attempts'] for s in states(case, [resolved])))
        case.actions = [deepcopy(ACTION)]
        run('2026-11-11T12:00:02Z')
        case.network.read_throttled.clear()
        for minute in (1, 2):
            e6.ControllerTests.restart(case)
            run('2026-11-11T12:0%d:03Z' % minute)
        self.assertEqual({s['status'] for s in states(case, [resolved])}, {'WITHDRAWN'})
        self.assertFalse(any('Owner action resolved' in str(b) for _, b in case.network.posts))

    def uncertain_probe(self, case, run):
        case.scenario = 'actions'
        case.actions = [deepcopy(ACTION)]
        case.network.message_filter = lambda p, messages: []
        run('2026-11-11T12:00:00Z')
        original = next(r for r in reversed(reports(case)) if r['body']['data'].get('decision') == 'Grant access')
        self.assertEqual({s['status'] for s in states(case, [original])}, {'UNKNOWN'})
        case.actions = []
        run('2026-11-11T12:00:01Z')
        self.assertFalse(any('Owner action resolved' in str(b) for _, b in case.network.posts))
        case.network.message_filter = lambda p, messages: messages
        for minute in (1, 2, 3):
            e6.ControllerTests.restart(case)
            run('2026-11-11T12:0%d:02Z' % minute)
        resolution = [r for r in reports(case) if r['body']['data'].get('action_resolved')]
        self.assertEqual(len(resolution), 1)
        self.assertEqual({s['status'] for s in states(case, [original] + resolution)}, {'DELIVERED'})
        self.assertTrue(all(len(s['attempts']) == 1 for s in states(case, [original] + resolution)))

    def send_throttle_probe(self, case, run):
        case.scenario = 'actions'
        case.actions = [deepcopy(ACTION)]
        case.network.throttled = {'slack', 'teams'}
        run('2026-11-11T17:00:00Z')
        original = next(r for r in reversed(reports(case)) if r['body']['data'].get('decision') == 'Grant access')
        self.assertEqual({s['status'] for s in states(case, [original])}, {'THROTTLED'})
        self.assertTrue(all(len(s['attempts']) == 1 for s in states(case, [original])))
        case.actions = []
        run('2026-11-11T17:00:01Z')
        self.assertEqual({s['status'] for s in states(case, [original])}, {'WITHDRAWN'})
        case.network.throttled.clear()
        for second in (2, 3, 4):
            e6.ControllerTests.restart(case)
            run('2026-11-11T17:01:0%dZ' % second)
        resolution = [r for r in reports(case) if r['body']['data'].get('action_resolved')]
        self.assertEqual(len(resolution), 1)
        self.assertEqual({s['status'] for s in states(case, resolution)}, {'DELIVERED'})
        self.assertTrue(all(len(s['attempts']) == 1 for s in states(case, [original] + resolution)))

    def token_probe(self, case, run):
        from agentic.budgeting import BudgetLedger, fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        root = Path(case.helper.temp.name)
        class Host:
            def observe_enrollment(self, value): return deepcopy(value)
        registry = EnrollmentRegistry(root / 'enrollment', Host())
        ledger = BudgetLedger(root / 'ledger', registry)
        settings = dict(fresh_settings(), accounting_enabled=True)
        b = dict(project_id=case.binding['project'], ticket_id='T-1', run_id='run', dispatch_id='dispatch', session_id='session', role='worker')
        registry.enroll(b)
        ledger.reserve(b, settings, 1000)
        ledger.settle(b, 1000, 'usage')
        case.ledger_path = root / 'ledger'
        case.budgeting = settings
        case.scenario = 'tokens'
        run('2026-11-11T17:00:00Z')
        e6.ControllerTests.restart(case)
        run('2026-11-11T17:00:01Z')
        m = next(r for r in reversed(reports(case)) if r['kind'] == 'eod')['body']['data']['detail_metrics']
        self.assertEqual(m['scope']['delivered'], 0)
        self.assertEqual(m['tokens']['consumed'], 1000)
        self.assertEqual(m['tokens']['completed_n'], 0)
        self.assertIsNone(m['tokens']['completed_mean'])
        self.assertEqual(m['elapsed']['standard']['n'], 0)

    def scope_probe(self, case, run, baseline=False, uncertain=False, offline=False):
        case.scenario = 'baseline' if baseline else 'actions'
        case.helper.listed = [] if baseline else [deepcopy(case.helper.current)]
        if uncertain:
            case.network.message_filter = lambda p, messages: []
        run('2026-11-11T10:00:00Z')
        kind = 'jira-review' if baseline else 'final-human-review'
        field = 'baseline_correction' if baseline else 'review_correction'
        original = next(r for r in reversed(reports(case)) if r['kind'] == kind)
        self.assertEqual({s['status'] for s in states(case, [original])}, {'UNKNOWN' if uncertain else 'DELIVERED'})
        case.helper.listed = []
        case.scenario = 'actions'
        if offline:
            def unavailable(candidate, now):
                raise RuntimeError('Synthetic candidate source unavailable')
            case.helper.observe_candidate = unavailable
            if case is not self:
                e3.CLI_ADAPTERS['observe_review_candidate'] = unavailable
        case.binding['scope_revision'] = 'b' * 64
        for d in case.settings['bindings']:
            d['scope_revision'] = case.binding['scope_revision']
        # Even a still-ready candidate cannot restore readiness in the old scope.
        case.network.read_throttled = {'auth.test', '/me?'}
        run('2026-11-11T10:00:01Z')
        corrections = [r for r in reports(case) if field in r]
        self.assertEqual(len(corrections), 0 if uncertain else 2)
        # A second scope change must not abandon the already queued correction.
        case.binding['scope_revision'] = 'c' * 64
        for d in case.settings['bindings']:
            d['scope_revision'] = case.binding['scope_revision']
        case.network.read_throttled.clear()
        case.network.message_filter = lambda p, messages: messages
        for minute in (1, 2, 3):
            e6.ControllerTests.restart(case)
            run('2026-11-11T10:0%d:02Z' % minute)
        corrections = [r for r in reports(case) if field in r]
        self.assertEqual(len(corrections), 2)
        self.assertEqual({s['status'] for s in states(case, corrections)}, {'DELIVERED'})
        self.assertTrue(all(len(s['attempts']) == 1 for s in states(case, corrections)))
        self.assertFalse(any('review_restoration' in r for r in reports(case)))
        text = 'prior backlog approval withdrawn' if baseline else 'prior readiness withdrawn'
        self.assertEqual(sum(text in str(b) for _, b in case.network.posts), 2)


class ControllerTests(Probes, unittest.TestCase):
    def setUp(self):
        e6.ControllerTests.setUp(self)
        self.adapters.update(ports(self))
        self.ledger_path, self.budgeting = None, None

    def run_cycle(self, now):
        import test_awf244_controller_wiring as fixture
        original = fixture.validate_operating
        def operating(value, governance):
            value = deepcopy(value)
            value['reporting'] = deepcopy(self.settings)
            if self.budgeting:
                value['budgeting'] = self.budgeting
            return original(value, governance)
        self.clock.value = now
        actual = factory(self)
        from test_continuous_controller import INVENTORY_BINDING
        b = dict(INVENTORY_BINDING, scope_sha256=self.binding['scope_revision'])
        from test_continuous_controller_planning import planning_resource_inputs
        resources = planning_resource_inputs(3, resource_capacity=1)
        resource_ports = {k: v for k, v in resources.items() if k.startswith(('observe_', 'verify_'))}
        with patch.object(fixture, 'INVENTORY_BINDING', b), patch('test_continuous_controller.INVENTORY_BINDING', b), \
             patch.object(fixture, 'REPOSITORY', self.repository), \
             patch.object(fixture, 'validate_operating', operating):
            result = self.helper.cycle(now, configure_reporting=actual['configure_reporting'],
                observe_reporting_sources=self.adapters['observe_reporting_sources'],
                verify_reporting_sources=self.adapters['verify_reporting_sources'], budget_ledger_path=self.ledger_path,
                configure_budgeting=actual['dispatch_ticket'].configure_budgeting,
                **resource_ports)
        self.assertEqual(result['errors'], [])


class CliTests(Probes, unittest.TestCase):
    def probe(self, method, *args):
        def inspect(case, result, output):
            case.helper.listed = []
            case.network.latency = 0
            case.ledger_path, case.budgeting = None, None
            e3.CLI_ADAPTERS.update(ports(case))
            from test_continuous_controller_planning import planning_resource_inputs
            e3.CLI_ADAPTERS.update({k: v for k, v in planning_resource_inputs(3, resource_capacity=1).items()
                                   if k.startswith(('observe_', 'verify_'))})
            def run(now):
                actual = factory(case)
                for key in ('configure_reporting', 'observe_inventory', 'observe_reservations'):
                    e3.CLI_ADAPTERS[key] = actual[key]
                root = Path(case.helper.temp.name)
                p = root / 'cli-project/OPERATING_CONFIG.yaml'
                config = json.loads(p.read_text())
                config['reporting'] = deepcopy(case.settings)
                if case.budgeting: config['budgeting'] = case.budgeting
                p.write_text(json.dumps(config), encoding='utf-8')
                from settings_pr_fixtures import publish_fixture_settings
                publish_fixture_settings(case, p.parent, json.loads((p.parent / '.agentic/PROJECT_CONFIG.yaml').read_text()), config, preserve_checkout=True)
                from test_reporting_fixtures import refresh_reporting_repository
                refresh_reporting_repository(case, p.parent)
                p = root / 'binding.json'
                b = json.loads(p.read_text())
                b['scope_sha256'] = case.binding['scope_revision']
                p.write_text(json.dumps(b), encoding='utf-8')
                # The standard CLI helper is used for ordinary runs; ledger
                # selection is an actual CLI option, inserted at parsing time.
                if case.ledger_path:
                    import runpy
                    original = runpy.run_path
                    def script(path):
                        ns = original(path)
                        main = ns['main']
                        ns['main'] = lambda argv, **kwargs: main(['--budget-ledger', str(case.ledger_path)] + argv, **kwargs)
                        return ns
                    with patch.object(e12.runpy, 'run_path', script):
                        e12.E12CliTests.cycle(self, case, now)
                else:
                    e12.E12CliTests.cycle(self, case, now)
            method(self, case, run, *args)
        e6.CliTests.probe(self, lambda case, net: None, inspect)


def install_tests():
    for name in ('capacity', 'empty', 'omitted', 'dependency'):
        setattr(ControllerTests, 'test_forecast_' + name,
            lambda self, n=name: self.forecast_probe(self, self.run_cycle, n))
        setattr(CliTests, 'test_cli_forecast_' + name,
            lambda self, n=name: self.probe(Probes.forecast_probe, n))
    for name in ('queued', 'delivered', 'mixed', 'updated', 'legacy'):
        setattr(ControllerTests, 'test_action_' + name,
            lambda self, n=name: self.action_probe(self, self.run_cycle, n))
        setattr(CliTests, 'test_cli_action_' + name,
            lambda self, n=name: self.probe(Probes.action_probe, n))
    ControllerTests.test_tokens = lambda self: self.token_probe(self, self.run_cycle)
    CliTests.test_cli_tokens = lambda self: self.probe(Probes.token_probe)
    ControllerTests.test_scope = lambda self: self.scope_probe(self, self.run_cycle)
    CliTests.test_cli_scope = lambda self: self.probe(Probes.scope_probe)
    ControllerTests.test_baseline_scope = lambda self: self.scope_probe(self, self.run_cycle, True)
    CliTests.test_cli_baseline_scope = lambda self: self.probe(Probes.scope_probe, True)
    ControllerTests.test_action_reopened = lambda self: self.reopen_probe(self, self.run_cycle)
    CliTests.test_cli_action_reopened = lambda self: self.probe(Probes.reopen_probe)
    ControllerTests.test_action_uncertain = lambda self: self.uncertain_probe(self, self.run_cycle)
    CliTests.test_cli_action_uncertain = lambda self: self.probe(Probes.uncertain_probe)
    ControllerTests.test_scope_uncertain = lambda self: self.scope_probe(self, self.run_cycle, False, True)
    CliTests.test_cli_scope_uncertain = lambda self: self.probe(Probes.scope_probe, False, True)
    ControllerTests.test_scope_offline = lambda self: self.scope_probe(self, self.run_cycle, False, False, True)
    CliTests.test_cli_scope_offline = lambda self: self.probe(Probes.scope_probe, False, False, True)
    ControllerTests.test_legacy_resolution = lambda self: self.reopen_probe(self, self.run_cycle, True)
    CliTests.test_cli_legacy_resolution = lambda self: self.probe(Probes.reopen_probe, True)
    ControllerTests.test_action_send_throttle = lambda self: self.send_throttle_probe(self, self.run_cycle)
    CliTests.test_cli_action_send_throttle = lambda self: self.probe(Probes.send_throttle_probe)


install_tests()
# The generated CLI tests inherit e3's per-call network latency.
for _name, _test in tuple(vars(CliTests).items()):
    if _name.startswith('test_'):
        setattr(CliTests, _name, _logical_slow_calls(_test))
