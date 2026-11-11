"""Pre-E3: real controller, factory, adapters and outbox; network-only faults."""
from copy import deepcopy
import json
import io
import os
from pathlib import Path
from contextlib import redirect_stdout
import runpy
import subprocess
import sys
import threading
import time
import unittest

import test_reporting_e2_regressions as e2


class Network(e2.ReportingNetwork):
    def __init__(self):
        super().__init__()
        self.latency = .3
        self.outage = False
        self.reject = False
        self.before_call = None
        self.thread_observations = []

    def __call__(self, method, url, headers, payload):
        self.thread_observations.append((threading.current_thread().name, threading.current_thread().daemon))
        time.sleep(self.latency)
        if self.before_call:
            self.before_call(method, url)
        if self.outage and (method == 'POST' or 'history' in url or '/messages?' in url):
            raise TimeoutError('synthetic outage')
        if method == 'POST' and self.reject:
            self.posts.append(('slack' if 'slack.com' in url else 'teams', deepcopy(payload)))
            return dict(ok=False, error='invalid_arguments')
        return super().__call__(method, url, headers, payload)


class ReportingE3Regressions(unittest.TestCase):
    setUp = e2.ReportingE2Regressions.setUp
    cycle = e2.ReportingE2Regressions.cycle
    status = e2.ReportingE2Regressions.status

    def slow_network(self):
        # The real factory closes over this network object, so replace its
        # callable implementation without replacing provider/controller ports.
        self.network.__class__ = Network
        Network.__init__(self.network)
        return self.network

    def test_joined_delivery_finishes_ordinary_latency_without_inflight_work(self):
        net = self.slow_network()
        dispatch_seen = []
        net.before_call = lambda method, url: dispatch_seen.append('implementation' in self.helper.events)
        result = self.cycle('2026-11-10T10:00:00Z', [e2.ticket('EX-1', 1, paths=['README.md'])])
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['dispatch_receipts']), 1)
        self.assertTrue(dispatch_seen and all(dispatch_seen))
        self.assertEqual(len(net.posts), 2)
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in self.status()['deliveries']))
        self.assertTrue(all(not daemon for _, daemon in net.thread_observations))
        self.assertFalse(any(t.name.startswith('awf-report-') for t in threading.enumerate()))

    def test_late_terminal_rejection_is_durable_and_allows_exactly_one_retry(self):
        net = self.slow_network()
        self.settings['mode'] = 'slack'
        net.reject = True
        self.cycle('2026-11-10T10:00:00Z')
        first = self.status()['deliveries'][0]
        self.assertEqual(first['status'], 'ABSENT')
        self.assertEqual(len(first['attempts']), 1)
        self.assertEqual(first['history'][-1]['kind'], 'SEND_REJECTED')
        net.reject = False
        self.cycle('2026-11-10T10:00:01Z')
        self.assertEqual(self.status()['deliveries'][0]['status'], 'DELIVERED')
        self.assertEqual(len(self.status()['deliveries'][0]['attempts']), 2)
        self.cycle('2026-11-10T10:00:02Z')
        self.assertEqual(len(net.posts), 2)

    def test_outage_history_cannot_starve_new_hourly_or_owner_alert(self):
        net = self.slow_network()
        net.outage = True
        for hour in range(10, 14):
            self.cycle('2026-11-10T%02d:00:00Z' % hour)
        net.outage = False
        self.runner.inventory[0]['body'] = json.dumps(dict(json.loads(self.runner.inventory[0]['body']),
            disposition='BLOCKED', actor='owner', reason='Owner access required', next_action='Grant access'))
        for hour in range(14, 17):
            self.cycle('2026-11-10T%02d:00:00Z' % hour)
            reports = self.status()['reports']
            newest = [r['report_id'] for r in reports if r['kind'] == 'hourly'][-1]
            self.assertEqual({d['status'] for d in self.status()['deliveries'] if d['report_id'] == newest}, {'DELIVERED'})
        self.assertTrue(any(d['status'] == 'UNKNOWN' for d in self.status()['deliveries']))
        alerts = {r['report_id'] for r in self.status()['reports'] if r['kind'] == 'owner-action'}
        self.assertTrue(alerts)
        self.assertEqual({d['status'] for d in self.status()['deliveries'] if d['report_id'] in alerts}, {'DELIVERED'})
        for hour in range(10, 14):
            old = next(r for r in self.status()['reports'] if r['kind'] == 'hourly'
                       and r['period']['end'] == '2026-11-10T%02d:00:00Z' % hour)
            states = [d for d in self.status()['deliveries'] if d['report_id'] == old['report_id']]
            self.assertTrue(all(len(d['attempts']) == 1 for d in states))
        self.assertFalse(any(t.name.startswith('awf-report-') for t in threading.enumerate()))

    def test_unavailable_reconciliation_is_not_observed_zero(self):
        self.cycle('2026-11-10T17:00:00Z')
        daily = next(r for r in self.status()['reports'] if r['kind'] == 'eod')['body']['data']
        self.assertIsNone(daily['detail_metrics']['daily']['today']['reconciled_ticket_count'])
        self.assertIn('UNAVAILABLE', daily['delivered'])

    def test_observed_empty_reconciliation_remains_a_known_zero(self):
        self.runner.inventory = []
        self.cycle('2026-11-10T17:00:00Z')
        daily = next(r for r in self.status()['reports'] if r['kind'] == 'eod')['body']['data']
        self.assertEqual(daily['detail_metrics']['daily']['today']['reconciled_ticket_count'], 0)
        self.assertEqual(daily['detail_metrics']['daily']['reconciliation_coverage']['status'], 'KNOWN')
        self.assertEqual(daily['delivered'], '0 reconciled today')

    def test_elapsed_budget_leaves_unsent_work_pending_without_attempts(self):
        net = self.slow_network()
        net.latency = 2
        started = time.monotonic()
        self.cycle('2026-11-10T17:00:00Z')
        self.assertLess(time.monotonic() - started, 45)
        reports = self.status()['reports']
        daily = next(r['report_id'] for r in reports if r['kind'] == 'eod')
        states = self.status()['deliveries']
        teams = next(d for d in states if d['report_id'] == daily and d['platform'] == 'teams')
        self.assertEqual(teams['status'], 'PENDING')
        self.assertEqual(teams['attempts'], [])
        self.assertEqual({d['status'] for d in states if d['platform'] == 'slack'}, {'DELIVERED'})
        self.assertEqual(len(net.posts), 3)
        self.assertFalse(any(t.name.startswith('awf-report-') for t in threading.enumerate()))

    def test_credentials_are_withheld_before_retention_and_readonly_output(self):
        sentinel = 'gh' + 'p_' + 'SYNTHETIC_CREDENTIAL_SENTINEL'
        self.runner.inventory[0]['body'] = json.dumps(dict(json.loads(self.runner.inventory[0]['body']),
            disposition='BLOCKED', actor='owner', reason=sentinel, next_action='Grant access'))
        result = self.cycle('2026-11-10T10:00:00Z')
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertNotIn(sentinel, json.dumps(self.status()))
        self.assertNotIn(sentinel.encode(), self.helper.path.read_bytes())
        self.assertIn('CREDENTIAL_WITHHELD', json.dumps(result))
        from test_continuous_controller_planning import planning_fixture_inputs
        project = Path(self.helper.temp.name) / 'status-project'
        (project / '.agentic').mkdir(parents=True)
        inputs = planning_fixture_inputs([], self.helper.store, 3, binding=e2.INVENTORY_BINDING)
        (project / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps(inputs['planning_config']), encoding='utf-8')
        config = deepcopy(inputs['planning_operating_snapshot'].config)
        config['reporting'] = deepcopy(self.settings)
        (project / 'OPERATING_CONFIG.yaml').write_text(json.dumps(config), encoding='utf-8')
        accepted = project / 'binding.json'
        accepted.write_text(json.dumps(self.binding), encoding='utf-8')
        before = self.helper.path.read_bytes()
        from settings_pr_fixtures import publish_fixture_settings
        publish_fixture_settings(self, project, inputs['planning_config'], config)
        # Match the parent fixture's synthetic prior admission in this fresh
        # process; this redaction test must never open the real owner store.
        entry = str(Path(__file__).resolve().parents[1] / 'scripts/workflow.py')
        args = [entry, '--root', str(project), 'reporting-status', '--state', str(self.helper.path), '--binding', str(accepted)]
        wrapper = ('import sys,runpy\n'
            f'sys.path.insert(0,{str(Path(__file__).resolve().parents[1] / "lib")!r})\n'
            f'sys.argv={args!r}\nrunpy.run_path({entry!r},run_name="__main__")\n')
        process = subprocess.run([sys.executable, '-B', '-c', wrapper],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn(sentinel, process.stdout + process.stderr)
        self.assertIn('CREDENTIAL_WITHHELD', process.stdout)
        self.assertEqual(self.helper.path.read_bytes(), before)

    def test_retry_checks_after_verification_and_before_post(self):
        net = self.slow_network()
        net.latency = 0
        self.settings['mode'] = 'slack'
        self.helper.listed = [deepcopy(self.helper.current)]
        net.reject = True
        self.cycle('2026-11-10T16:59:58Z')
        review = next(r for r in self.status()['reports'] if r['kind'] == 'final-human-review')
        state = next(d for d in self.status()['deliveries'] if d['report_id'] == review['report_id'])
        self.assertEqual(state['status'], 'ABSENT')
        self.assertEqual(len(state['attempts']), 1)
        posts = len(net.posts)

        def advance_at_verification(method, url):
            if 'conversations.info' in url:
                self.helper.now = '2026-11-10T17:00:01Z'
                self.clock.value = self.helper.now
                self.helper.current['head_sha'] = 'e' * 40
        net.before_call = advance_at_verification
        net.latency = .3
        net.reject = False
        self.cycle('2026-11-10T16:59:59Z')
        time.sleep(1.2)  # Also detect a legacy worker posting after CLI return.
        self.assertEqual(len(net.posts), posts)
        state = next(d for d in self.status()['deliveries'] if d['report_id'] == review['report_id'])
        self.assertEqual(state['status'], 'ABSENT')
        self.assertEqual(len(state['attempts']), 1)

    def test_single_cycle_cli_waits_for_300_1000_2000_ms_http(self):
        for latency in (.3, 1, 2):
            with self.subTest(latency=latency):
                process = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    '--cli-probe', str(latency)], capture_output=True, text=True, timeout=90)
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result['posts'], 2)
                self.assertEqual(result['states'], ['DELIVERED', 'DELIVERED'])
                self.assertEqual(result['attempts'], [1, 1])
                self.assertEqual(result['workers'], [])
                self.assertTrue(result['non_daemon'])
        process = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
            '--cli-probe', '0'], capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)['unavailable'])


CLI_ADAPTERS = {}


def cli_probe(latency, *, prepare=None, inspect=None):
    """Actual CLI parsing, pinned adapter loading, real cycle and JSON output.

    Reuse the established trusted host/planning fixtures. Only outbound HTTP
    is faulted; no reporting provider, outbox, controller or CLI is mocked.
    """
    from agentic.canonical import sha256
    from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs
    case = ReportingE3Regressions()
    case.setUp()
    try:
        evidence_probe = latency == 0
        now = '2026-11-10T17:00:00Z' if evidence_probe else '2026-11-10T18:00:00Z'
        case.clock.value = now
        net = case.slow_network()
        net.latency = latency
        case.binding['project'] = '00000000-0000-4000-8000-000000000001'
        inventory_binding = dict(e2.INVENTORY_BINDING, project_id=case.binding['project'])
        for destination in case.settings['bindings']:
            destination['project'] = case.binding['project']
        adapter_root = Path(case.helper.temp.name) / 'cli-host'
        adapter_root.mkdir()
        config = e2._base_config(adapter_root)
        config['github']['project_id'] = case.binding['project']
        config.update(_runner=case.runner, _clock=case.clock, _reporting_http=net,
            reporting_sources=dict(binding=deepcopy(case.binding), pull_requests=[99]),
            reporting={p: dict(context='workspace' if p == 'slack' else 'tenant', sender='sender')
                       for p in ('teams', 'slack')})
        case.adapters = case.namespace['build_adapters'](config)
        case.runner.inventory[0]['body'] = json.dumps(dict(json.loads(case.runner.inventory[0]['body']),
            disposition='BLOCKED', actor='controller', reason='Owner access required', next_action='Grant access'))
        if prepare is not None:
            prepare(case, net)
        inventory = case.adapters['observe_inventory'](now)
        inputs = planning_fixture_inputs(inventory['tickets'], case.helper.store, 3,
            binding=inventory_binding, now=now)
        project = Path(case.helper.temp.name) / 'cli-project'
        from test_reporting_fixtures import build_reporting_repository
        build_reporting_repository(project)
        gov = inputs['planning_config']
        (project / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps(gov), encoding='utf-8')
        operating = deepcopy(inputs['planning_operating_snapshot'].config)
        operating['reporting'] = deepcopy(case.settings)
        (project / 'OPERATING_CONFIG.yaml').write_text(json.dumps(operating), encoding='utf-8')
        from settings_pr_fixtures import publish_fixture_settings
        publish_fixture_settings(case, project, gov, operating)
        from test_reporting_fixtures import refresh_reporting_repository
        refresh_reporting_repository(case, project)
        ports = dict(case.adapters)
        ports.update({k: v for k, v in inputs.items() if k.startswith(('observe_', 'verify_'))})
        ports.update({k: v for k, v in planning_resource_inputs(3).items() if k.startswith(('observe_', 'verify_'))})
        ports['controller_clock'] = lambda: now
        ports['observe_owner_ready'] = case.helper.observe_ready
        ports['observe_review_candidate'] = case.helper.observe_candidate
        global CLI_ADAPTERS
        CLI_ADAPTERS = ports
        # The reviewed loader re-reads and hashes this fixture adapter's bytes.
        adapter = Path(case.helper.temp.name) / 'cli_adapter.py'
        adapter.write_text('def build_adapters(config):\n'
            '    from test_reporting_e3_regressions import CLI_ADAPTERS\n'
            '    return CLI_ADAPTERS\n', encoding='utf-8')
        binding = adapter.with_name('binding.json')
        binding.write_text(json.dumps(inventory_binding), encoding='utf-8')
        cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/continuous_controller.py'))
        case.helper.path = Path(case.helper.temp.name) / 'cli-controller.sqlite3'
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli['main'](['--state', str(case.helper.path), '--worktree-root', str(project / 'worker-worktrees'), '--cadence-seconds', '3600', '--stream', 'A', '--stream', 'B', '--stream', 'C',
                '--project-config', str(project / '.agentic/PROJECT_CONFIG.yaml'), '--adapter-module', str(adapter),
                '--adapter-sha256', sha256(adapter.read_bytes()), 'cycle', '--inventory-binding', str(binding),
                '--repository-root', str(project), '--repository-head-sha', case.repository['repository_head_sha'],
                '--repository-tree-sha', case.repository['repository_tree_sha'], '--now', now, '--host-capacity', '3'], default_root=project)
        assert code == 0
        result = json.loads(output.getvalue())
        assert result['errors'] == [], result['errors']
        from agentic.continuous_controller import ContinuousControllerStore
        case.helper.store = ContinuousControllerStore(case.helper.path, ['A', 'B', 'C'], cadence_seconds=3600)
        if inspect is not None:
            inspect(case, result, output.getvalue())
        states = case.status()['deliveries']
        # Hourly and owner alert, independently delivered to each platform.
        periodic = {r['report_id'] for r in case.status()['reports'] if r['kind'] == ('eod' if evidence_probe else 'hourly')}
        daily = [d for d in states if d['report_id'] in periodic]
        print(json.dumps(dict(posts=len([p for p, body in net.posts if ('End of Day' if evidence_probe else 'Hourly progress') in str(body)]),
            states=sorted(d['status'] for d in daily), attempts=[len(d['attempts']) for d in daily],
            workers=[t.name for t in threading.enumerate() if t.name.startswith('awf-report-')],
            non_daemon=all(not daemon for _, daemon in net.thread_observations),
            unavailable='UNAVAILABLE: Jira reconciliation' in output.getvalue())))
    finally:
        case.doCleanups()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--cli-probe':
        # Pin the import used by the fixture adapter to this same process state.
        sys.modules['test_reporting_e3_regressions'] = sys.modules[__name__]
        cli_probe(float(sys.argv[2]))
    else:
        unittest.main()
