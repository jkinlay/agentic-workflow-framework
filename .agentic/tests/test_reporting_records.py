import copy
import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError


def request():
    return dict(record_id='fixture-report', created_at='2026-11-05T12:00:00Z',
        binding=dict(project='fixture-project', repository='fixture-repository', scope_revision='scope-1'),
        candidate=dict(head='a'*40, base='b'*40, pr=1),
        included=['E-1', 'T-1', 'T-2'], excluded=['T-3'], report_type='hourly',
        period=dict(start='2026-11-05T11:00:00Z', end='2026-11-05T12:00:00Z', catch_up=False),
        baseline_revision='baseline-1', forecast_revision='forecast-1',
        summary='Fixture progress.', owner_action='Unknown: no owner-action source.',
        sources=[dict(field='tickets', observed_at='2026-11-05T11:59:00Z', stale=False, source='fixture-source')],
        tickets=[dict(key='E-1', kind='epic', epic=None, parent=None, priority='High', size='L',
                      state='WORKING', cohort='standard'),
                 dict(key='T-1', kind='implementation', epic='E-1', parent=None, priority='High', size='S',
                      state='DONE', cohort='standard', first_started_at='2026-11-05T10:00:00Z',
                      required_prs=[dict(id='pr-1', merged_at='2026-11-05T11:00:00Z')],
                      reconciled_at='2026-11-05T11:10:00Z'),
                 dict(key='T-2', kind='implementation', epic=None, parent=None, priority=None, size=None,
                      state='CANCELLED', cohort='standard')])


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module('agentic.reporting.records')

    def prepare(self, r=None, verifier=lambda value, binding: value):
        r = request() if r is None else r
        return self.api.prepare_snapshot(r, request()['binding'], verify_sources=verifier)

    def test_versioned_replay_and_exact_scope_candidate_binding(self):
        from agentic.status_report_store import replay_report
        record = self.prepare()
        self.assertEqual(record['format'], 'awf-status-report-record-2')
        self.assertEqual(replay_report(record), record['values'])
        changed = copy.deepcopy(record)
        changed['inputs']['candidate']['head'] = 'c'*40
        with self.assertRaises(ValidationError):
            replay_report(changed)
        self.assertFalse(self.api.readiness_current(record, dict(head='c'*40, base='b'*40, pr=1), request()['binding']))

    def test_foreign_scope_duplicate_keys_and_missing_verifier(self):
        r = request()
        r['binding']['project'] = 'foreign'
        with self.assertRaises(ValidationError):
            self.prepare(r)
        r = request()
        r['tickets'].append(r['tickets'][1])
        with self.assertRaises(ValidationError):
            self.prepare(r)
        record = self.prepare(verifier=None)
        self.assertEqual(record['values']['metrics']['status'], 'UNKNOWN')

    def test_distinct_epics_leaf_denominator_and_unknown_buckets(self):
        metrics = self.prepare()['values']['metrics']
        self.assertEqual(metrics['epics'], 1)
        self.assertEqual(metrics['implementation_tickets'], 2)
        self.assertEqual(metrics['delivered'], 1)
        self.assertEqual(metrics['priority_size']['Unknown']['Unestimated'], 1)
        self.assertEqual(metrics['per_epic']['Unassigned']['implementation_tickets'], 1)
        self.assertEqual(metrics['elapsed']['standard']['mean_seconds'], {'numerator': 3600, 'denominator': 1})
        self.assertEqual(metrics['elapsed']['standard']['coverage'], {'numerator': 1, 'denominator': 1})
        self.assertEqual(metrics['accounting']['status'], 'UNAVAILABLE')
        self.assertEqual(metrics['forecast']['status'], 'UNAVAILABLE')

    def test_parent_is_not_counted_again_and_multi_pr_is_not_delivered_early(self):
        r = request()
        child = copy.deepcopy(r['tickets'][1])
        child.update(key='T-4', parent='T-1')
        child['required_prs'].append(dict(id='pr-2', merged_at=None))
        r['included'].append('T-4')
        r['tickets'].append(child)
        metrics = self.prepare(r)['values']['metrics']
        self.assertEqual(metrics['implementation_tickets'], 2)
        self.assertEqual(metrics['delivered'], 0)
        self.assertEqual(metrics['elapsed']['standard']['n'], 0)

    def test_missing_and_stale_sources_are_explicit(self):
        r = request()
        r['sources'][0]['stale'] = True
        values = self.prepare(r)['values']
        self.assertEqual(values['freshness'], 'STALE')
        self.assertEqual(values['metrics']['status'], 'UNKNOWN')
        r['sources'] = []
        self.assertEqual(self.prepare(r)['values']['freshness'], 'MISSING')

    def test_missing_start_lowers_elapsed_coverage_without_erasing_delivery(self):
        r = request()
        del r['tickets'][1]['first_started_at']
        metrics = self.prepare(r)['values']['metrics']
        self.assertEqual(metrics['delivered'], 1)
        self.assertEqual(metrics['elapsed']['standard']['coverage'], {'numerator': 0, 'denominator': 1})

    def test_unknown_state_and_parent_cycle_rejected(self):
        r = request()
        r['tickets'][1]['state'] = 'GREEN_CI_MEANS_DONE'
        with self.assertRaises(ValidationError):
            self.prepare(r)
        r = request()
        r['tickets'][1]['parent'] = 'T-1'
        with self.assertRaises(ValidationError):
            self.prepare(r)

    def test_snapshot_store_is_append_only_and_identity_conflicts_refuse(self):
        import tempfile
        from agentic.store import Store
        from agentic.status_report_store import ReportStore
        r = request()
        r['binding']['project'] = '3061ccda-dafd-4fab-ab01-fbfcfb2688d4'
        record = self.api.prepare_snapshot(r, r['binding'], verify_sources=lambda v, b: v)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.sqlite'
            original = Store(path, r['binding']['project'])
            store = ReportStore(path, r['binding']['project'])
            self.assertEqual(store.append(record), store.append(record))
            self.assertEqual(original.verify_chain()['sequence'], 1)
            r['summary'] = 'Changed bytes under same identity.'
            changed = self.api.prepare_snapshot(r, r['binding'], verify_sources=lambda v, b: v)
            with self.assertRaisesRegex(ValidationError, 'reused with different content'):
                store.append(changed)

    def test_subsecond_elapsed_is_not_rounded_to_zero(self):
        r = request()
        r['tickets'][1]['first_started_at'] = '2026-11-05T10:59:59.500000Z'
        metrics = self.prepare(r)['values']['metrics']
        self.assertEqual(metrics['elapsed']['standard']['mean_seconds'], {'numerator': 1, 'denominator': 2})


if __name__ == '__main__':
    unittest.main()
