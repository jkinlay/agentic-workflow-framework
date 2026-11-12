"""Pre-E13: approval withdrawal and observation-bounded actuals at real entry points."""
from test_reporting_timing import _logical_slow_calls
from copy import deepcopy
import unittest

import test_reporting_e3_regressions as e3
import test_reporting_e6_regressions as e6
import test_reporting_e12_regressions as e12
from agentic.canonical import loads


def source_ports(case, *, baseline=False):
    ports = e12.source_ports(case, baseline=baseline)
    case.withdrawn = False
    case.future_field = 'both'

    def transform(snapshot):
        records = snapshot['records']
        if baseline and case.withdrawn:
            records['stage'] = 'BACKLOG_APPROVAL_WITHDRAWN'
            records['owner_approval'].update(verified=False, status='REVOKED',
                                            evidence_id='accepted-withdrawal-1')
            records['review'] = dict(kind='baseline-approval-withdrawn',
                revision=case.approval_revision, evidence_id='accepted-withdrawal-1')
        elif not baseline:
            for ticket in records['tickets']:
                if ticket.get('state') != 'DONE':
                    continue
                if case.future_field in {'both', 'merge'}:
                    ticket['required_prs'][0]['merged_at'] = '2026-11-12T11:00:00Z'
                if case.future_field in {'both', 'reconciliation'}:
                    ticket['reconciled_at'] = '2026-11-12T11:10:00Z'
        return snapshot

    def observe(query):
        return transform(ports['observe_reporting_sources'](query))

    def verify(auth, query):
        result = ports['verify_reporting_sources'](auth, query)
        result['snapshot'] = transform(result['snapshot'])
        return result

    return dict(observe_reporting_sources=observe, verify_reporting_sources=verify)


class E13Tests(unittest.TestCase):
    def setUp(self):
        e6.ControllerTests.setUp(self)
        self.network.latency = .3

    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status

    def run_checked(self, now, *, invalid_host=False):
        result = self.cycle(now)
        self.assertEqual([e['operation'] for e in result['errors']],
                         ['reporting_sources'] if invalid_host else [])

    def withdrawal_probe(self, case, run, *, queued=False, mixed=False):
        initial_posts = len(case.network.posts)
        if queued or mixed:
            case.network.read_throttled = {'/me?'} if mixed else {'auth.test', '/me?'}
        run('2026-11-11T12:15:01Z')
        original = deepcopy(next(r for r in case.status()['reports'] if r['kind'] == 'jira-review'))
        ids = set(original['delivery_ids'])
        states = [d for d in case.status()['deliveries'] if d['delivery_id'] in ids]
        self.assertEqual(len(states), 2)
        self.assertEqual(len(case.network.posts) - initial_posts, 0 if queued else 1 if mixed else 2)
        if queued:
            self.assertTrue(all(d['attempts'] == [] for d in states))
        roots = {d['platform']: d['history'][-1]['message_id'] for d in states if d['status'] == 'DELIVERED'}
        before = len(case.network.posts)
        case.withdrawn = True
        # Withdrawal itself must survive a crash/restart before destinations recover.
        run('2026-11-11T12:15:02Z')
        case.network.read_throttled.clear()
        for now in ('2026-11-11T12:16:02Z', '2026-11-11T12:17:03Z', '2026-11-11T12:18:04Z'):
            e6.ControllerTests.restart(case)
            run(now)
        self.assertEqual(next(r for r in case.status()['reports'] if r['report_id'] == original['report_id']), original)
        states = [d for d in case.status()['deliveries'] if d['delivery_id'] in ids]
        self.assertEqual({d['platform']: d['status'] for d in states},
            {p: 'DELIVERED' if p in roots else 'WITHDRAWN' for p in ('teams', 'slack')})
        self.assertTrue(all(d['attempts'] == [] for d in states if d['status'] == 'WITHDRAWN'))
        corrections = [r for r in case.status()['reports'] if 'baseline_correction' in r]
        self.assertEqual(len(corrections), len(roots))
        self.assertEqual(len(case.network.posts) - before, len(roots))
        for report in corrections:
            self.assertIn('approval withdrawn', report['body']['data']['summary'])
            self.assertEqual(report['baseline_correction']['prior_report'], original['report_id'])
            delivery = next(d for d in case.status()['deliveries'] if d['delivery_id'] in report['delivery_ids'])
            self.assertEqual(delivery['status'], 'DELIVERED')
            self.assertEqual(len(delivery['attempts']), 1)
            with case.helper.store.connection() as db:
                intent = loads(db.execute('SELECT intent_json FROM controller_report_delivery WHERE delivery_id=?',
                                          (delivery['delivery_id'],)).fetchone()[0])
            self.assertEqual(intent['thread_id'], roots[delivery['platform']])
            payloads = [b for p, b in case.network.posts[before:] if p == delivery['platform']]
            self.assertEqual(len(payloads), 1)
            if delivery['platform'] == 'slack':
                self.assertEqual(payloads[0]['thread_ts'], roots['slack'])
            else:
                self.assertTrue(any('/messages/' + roots['teams'] + '/replies' in url
                                    for method, url in case.network.calls if method == 'POST'))

    def queued_probe(self, case, run):
        self.withdrawal_probe(case, run, queued=True)

    def delivered_probe(self, case, run):
        self.withdrawal_probe(case, run)

    def mixed_probe(self, case, run):
        self.withdrawal_probe(case, run, mixed=True)

    def future_probe(self, case, run):
        for minute, field in enumerate(('both', 'merge', 'reconciliation')):
            case.future_field = field
            e6.ControllerTests.restart(case)
            run('2026-11-11T%02d:00:00Z' % (17 + minute))
            report = next(r for r in reversed(case.status()['reports'])
                          if r['kind'] == ('hourly' if minute else 'eod'))
            data = report['body']['data']
            metrics = data['detail_metrics']
            self.assertEqual(metrics['scope']['delivered'], 0)
            self.assertEqual(metrics['scope']['remaining'], 1)
            self.assertEqual(metrics['elapsed']['standard']['n'], 0)
            self.assertIsNone(metrics['elapsed']['standard']['mean_seconds'])
            self.assertIsNone(metrics['elapsed']['standard']['median_seconds'])
            self.assertIn('FUTURE_TICKET_ACTUAL', str(data))
            if not minute:
                self.assertIsNone(metrics['daily']['today']['reconciled_ticket_count'])
                self.assertEqual(metrics['daily']['project_to_date']['standard']['n'], 0)
            ids = set(report['delivery_ids'])
            self.assertEqual({d['status'] for d in case.status()['deliveries'] if d['delivery_id'] in ids}, {'DELIVERED'})
        # Raw evidence is retained; the same actuals become usable at their time.
        case.future_field = 'both'
        run('2026-11-12T17:00:00Z')
        report = next(r for r in reversed(case.status()['reports']) if r['kind'] == 'eod')
        self.assertEqual(report['body']['data']['detail_metrics']['scope']['delivered'], 1)
        self.assertEqual(report['body']['data']['detail_metrics']['elapsed']['standard']['n'], 1)

    @_logical_slow_calls
    def test_queued_baseline_withdrawal_survives_restart(self):
        self.adapters.update(source_ports(self, baseline=True))
        self.queued_probe(self, self.run_checked)

    @_logical_slow_calls
    def test_delivered_baseline_corrected_once_on_original_threads(self):
        self.adapters.update(source_ports(self, baseline=True))
        self.delivered_probe(self, self.run_checked)

    @_logical_slow_calls
    def test_partial_delivery_withdraws_queue_and_corrects_delivered_platform(self):
        self.adapters.update(source_ports(self, baseline=True))
        self.mixed_probe(self, self.run_checked)

    @_logical_slow_calls
    def test_future_nested_actuals_are_unavailable(self):
        self.adapters.update(source_ports(self))
        self.future_probe(self, self.run_checked)


class E13CliTests(unittest.TestCase):
    cycle = e12.E12CliTests.cycle
    withdrawal_probe = E13Tests.withdrawal_probe

    def probe(self, method, *, baseline=False):
        def inspect(case, result, output):
            case.helper.listed = []
            # Settle the fixture's earlier periodic catch-up before this event.
            self.cycle(case, '2026-11-11T12:14:59Z')
            e3.CLI_ADAPTERS.update(source_ports(case, baseline=baseline))
            method(self, case, lambda now: self.cycle(case, now))
        e6.CliTests.probe(self, lambda case, net: None, inspect)

    @_logical_slow_calls
    def test_cli_queued_baseline_withdrawal_survives_restart(self):
        self.probe(E13Tests.queued_probe, baseline=True)

    @_logical_slow_calls
    def test_cli_delivered_baseline_corrected_once_on_original_threads(self):
        self.probe(E13Tests.delivered_probe, baseline=True)

    @_logical_slow_calls
    def test_cli_partial_delivery_withdraws_queue_and_corrects_delivered_platform(self):
        self.probe(E13Tests.mixed_probe, baseline=True)

    @_logical_slow_calls
    def test_cli_future_nested_actuals_are_unavailable(self):
        self.probe(E13Tests.future_probe)


if __name__ == '__main__':
    unittest.main()
