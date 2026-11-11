"""Pre-E12: declared populations and verified baseline events via real entry points."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import unittest

import test_reporting_e3_regressions as e3
import test_reporting_e6_regressions as e6
from test_reporting_records import request
from agentic.canonical import sha256
from agentic.reporting.events import approval_event


def source_ports(case, *, baseline=False):
    """Independent host source ports; controller/composer/outbox/HTTP stay real."""
    retained = {}
    case.approval_mode = 'valid'
    case.approval_revision = 'approved-plan-1'

    def observe(query):
        records = request()
        records.update(binding=deepcopy(query['binding']), created_at=query['observed_at'], prs=[])
        records['sources'] = [dict(field='tickets', observed_at=query['observed_at'], stale=False,
                                   source='accepted-test-observation')]
        if baseline:
            notice = approval_event(case.binding['scope_revision'], case.approval_revision,
                dict(verified=True, revision=case.approval_revision, evidence_id='accepted-' + case.approval_revision))
            records.update(included=['EX-1'], excluded=[],
                tickets=[dict(key='EX-1', kind='implementation', state='READY')],
                baseline_revision=case.approval_revision, stage='BACKLOG_APPROVED',
                owner_approval=deepcopy(notice['record']['approval']), review=notice)
            mode = case.approval_mode
            if mode == 'unverified-approval':
                records['owner_approval']['verified'] = False
            elif mode == 'foreign-scope':
                records['review'] = approval_event('foreign-scope', case.approval_revision, records['owner_approval'])
            elif mode == 'foreign-project':
                records['binding']['project'] = 'foreign-project'
            elif mode == 'foreign-revision':
                records['baseline_revision'] = 'other-plan'
            elif mode == 'stale':
                records['sources'][0]['stale'] = True
            elif mode == 'missing-source':
                records['sources'] = []
        else:
            records['previous_daily_end'] = '2026-11-10T17:00:00Z'
            parent = records['tickets'][1]
            parent.update(first_started_at='2026-11-11T08:00:00Z', reconciled_at='2026-11-11T11:10:00Z')
            parent['required_prs'][0]['merged_at'] = '2026-11-11T11:00:00Z'
            child = deepcopy(parent)
            child.update(key='T-4', parent='T-1', first_started_at='2026-11-11T10:00:00Z')
            records['tickets'].append(child)
            records['included'].append('T-4')
            # Non-required observations cannot contaminate coverage or samples.
            parent['original_estimate'] = child['original_estimate'] = dict(unit='calendar_seconds', value=3600)
        snapshot = dict(binding=deepcopy(query['binding']), observed_at=query['observed_at'],
                        records=records, owner_actions=[])
        retained[query['observed_at']] = deepcopy(snapshot)
        return dict(snapshot, authentication=dict(evidence_id='source-' + query['observed_at']))

    def verify(authentication, query):
        return dict(status='UNVERIFIED' if case.approval_mode == 'unverified-host' else 'VERIFIED',
                    evidence_id=authentication['evidence_id'], snapshot=deepcopy(retained[query['observed_at']]))

    return dict(observe_reporting_sources=observe, verify_reporting_sources=verify)


class E12Tests(unittest.TestCase):
    def setUp(self):
        e6.ControllerTests.setUp(self)
        self.network.latency = .3

    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status

    def population_probe(self, case, run):
        for now in ('2026-11-11T17:00:00Z', '2026-11-12T17:00:00Z'):
            e6.ControllerTests.restart(case)
            run(now)
        reports = [r for r in case.status()['reports'] if r['kind'] == 'eod']
        first = next(r for r in reports if r['period']['end'] == '2026-11-11T17:00:00Z')
        data = first['body']['data']
        metrics = data['detail_metrics']
        self.assertEqual(metrics['scope']['implementation_tickets'], 1)
        self.assertEqual(metrics['scope']['delivered'], 1)
        self.assertEqual(data['delivered'], '1 reconciled today')
        self.assertEqual(metrics['daily']['today']['reconciled_ticket_count'], 1)
        self.assertEqual(metrics['daily']['today']['reconciled_tickets'], ['T-4'])
        self.assertEqual(metrics['daily']['since_previous']['reconciled_ticket_count'], 1)
        self.assertEqual(metrics['daily']['since_previous']['reconciled_tickets'], ['T-4'])
        self.assertEqual(metrics['daily']['reconciliation_coverage'], dict(status='KNOWN', observed=1, total=1))
        for cohort in (metrics['elapsed'], metrics['daily']['today']['elapsed'],
                       metrics['daily']['since_previous']['elapsed'], metrics['daily']['project_to_date']):
            self.assertEqual(set(cohort), {'standard'})
            self.assertEqual(cohort['standard']['n'], 1)
            self.assertEqual(cohort['standard']['mean_seconds'], dict(numerator=3600, denominator=1))
            self.assertEqual(cohort['standard']['median_seconds'], dict(numerator=3600, denominator=1))
            self.assertEqual(cohort['standard']['coverage'], dict(numerator=1, denominator=1))
        self.assertEqual(metrics['estimated_time']['calendar_seconds']['n'], 1)
        second = next(r for r in reports if r['period']['end'] == '2026-11-12T17:00:00Z')
        for view in ('today', 'since_previous'):
            self.assertEqual(second['body']['data']['detail_metrics']['daily'][view]['reconciled_ticket_count'], 0)
        ids = set(first['delivery_ids']) | set(second['delivery_ids'])
        self.assertEqual(len(ids), 4)
        self.assertEqual({d['status'] for d in case.status()['deliveries'] if d['delivery_id'] in ids}, {'DELIVERED'})
        posts = [(p, b) for p, b in case.network.posts if '1 reconciled today' in str(b)]
        self.assertEqual({p for p, _ in posts}, {'slack', 'teams'})
        self.assertEqual(len(posts), 2)
        self.assertFalse(any('2 reconciled today' in str(b) for _, b in case.network.posts))

    def approval_probe(self, case, run):
        for now in ('2026-11-11T12:15:01Z', '2026-11-11T12:16:02Z', '2026-11-11T13:00:00Z'):
            e6.ControllerTests.restart(case)
            run(now)
            self.assertEqual(len([r for r in case.status()['reports'] if r['kind'] == 'jira-review']), 1)
        selected = [r for r in case.status()['reports'] if r['kind'] == 'jira-review']
        self.assertEqual(len(selected), 1)
        original = deepcopy(selected[0])
        self.assertEqual(original['body']['data']['baseline_revision'], 'approved-plan-1')
        self.assertEqual(original['body']['data']['source_records']['review']['record']['approval']['evidence_id'],
                         'accepted-approved-plan-1')
        case.approval_revision = 'approved-plan-2'
        e6.ControllerTests.restart(case)
        run('2026-11-11T13:01:00Z')
        case.approval_revision = 'approved-plan-1'
        e6.ControllerTests.restart(case)
        run('2026-11-11T13:02:00Z')
        selected = [r for r in case.status()['reports'] if r['kind'] == 'jira-review']
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0], original)
        ids = {d for r in selected for d in r['delivery_ids']}
        deliveries = [d for d in case.status()['deliveries'] if d['delivery_id'] in ids]
        self.assertEqual(len(deliveries), 4)
        self.assertEqual({d['platform'] for d in deliveries}, {'teams', 'slack'})
        self.assertEqual({d['status'] for d in deliveries}, {'DELIVERED'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in deliveries))

    def refusal_probe(self, case, run):
        case.approval_mode = 'unverified-approval'
        run('2026-11-11T12:19:00Z')
        original_posts = deepcopy(case.network.posts)
        for minute, mode in enumerate(('unverified-approval', 'foreign-scope', 'foreign-project',
                'foreign-revision', 'stale', 'missing-source', 'unverified-host'), 20):
            with self.subTest(mode=mode):
                case.approval_mode = mode
                e6.ControllerTests.restart(case)
                run('2026-11-11T12:%02d:00Z' % minute, invalid_host=mode == 'unverified-host')
                self.assertEqual([r for r in case.status()['reports'] if r['kind'] == 'jira-review'], [])
                self.assertEqual(case.network.posts, original_posts)
        case.approval_mode = 'valid'
        e6.ControllerTests.restart(case)
        run('2026-11-11T12:30:00Z')
        self.assertEqual(len([r for r in case.status()['reports'] if r['kind'] == 'jira-review']), 1)

    def run_checked(self, now, *, invalid_host=False):
        result = self.cycle(now)
        self.assertEqual([e['operation'] for e in result['errors']], ['reporting_sources'] if invalid_host else [])

    def test_required_population_controls_daily_and_elapsed_metrics(self):
        self.adapters.update(source_ports(self))
        self.population_probe(self, self.run_checked)

    def test_approved_baselines_report_once_across_restarts(self):
        self.adapters.update(source_ports(self, baseline=True))
        self.approval_probe(self, self.run_checked)

    def test_unverified_and_foreign_baselines_cannot_report(self):
        self.adapters.update(source_ports(self, baseline=True))
        self.refusal_probe(self, self.run_checked)


class E12CliTests(unittest.TestCase):
    def cycle(self, case, now, *, invalid_host=False):
        case.clock.value = now
        e3.CLI_ADAPTERS['controller_clock'] = case.clock.now
        root = Path(case.helper.temp.name)
        project, adapter = root / 'cli-project', root / 'cli_adapter.py'
        cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/continuous_controller.py'))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli['main'](['--state', str(case.helper.path), '--worktree-root', str(project / 'worker-worktrees'),
                '--cadence-seconds', '3600', '--stream', 'A', '--stream', 'B', '--stream', 'C',
                '--project-config', str(project / '.agentic/PROJECT_CONFIG.yaml'), '--adapter-module', str(adapter),
                '--adapter-sha256', sha256(adapter.read_bytes()), 'cycle', '--inventory-binding', str(root / 'binding.json'),
                '--repository-root', str(project), '--repository-head-sha', case.repository['repository_head_sha'],
                '--repository-tree-sha', case.repository['repository_tree_sha'], '--now', now, '--host-capacity', '3'], default_root=project)
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual([e['operation'] for e in json.loads(output.getvalue())['errors']],
                         ['reporting_sources'] if invalid_host else [])

    def probe(self, method, *, baseline=False):
        def inspect(case, result, output):
            case.helper.listed = []
            e3.CLI_ADAPTERS.update(source_ports(case, baseline=baseline))
            def run(now, *, invalid_host=False):
                self.cycle(case, now, invalid_host=invalid_host)
            method(self, case, run)
        e6.CliTests.probe(self, lambda case, net: None, inspect)

    def test_cli_required_population_controls_daily_and_elapsed_metrics(self):
        self.probe(E12Tests.population_probe)

    def test_cli_approved_baselines_report_once_across_restarts(self):
        self.probe(E12Tests.approval_probe, baseline=True)

    def test_cli_unverified_and_foreign_baselines_cannot_report(self):
        self.probe(E12Tests.refusal_probe, baseline=True)


if __name__ == '__main__':
    unittest.main()
