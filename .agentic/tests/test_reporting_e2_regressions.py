"""Pre-E2 reproductions through production cycles and shipped reporting ports.

Only reporting network I/O is substituted. No fake provider or delivery receipt.
The existing controller fixture supplies planning/host observations, never sends.
"""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import os
import runpy
import sqlite3
import subprocess
import threading
import time
import types
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.reporting.runtime import reporting_cycle, reporting_status
import test_awf244_controller_wiring as controller_fixture
from test_continuous_controller import INVENTORY_BINDING, ticket
from test_reference_controller_adapter import _base_config, FakeClock, FakeRunner


from test_reporting_fixtures import ReportingNetwork, TEAMS_DESTINATION


class ReportingE2Regressions(unittest.TestCase):
    def setUp(self):
        self.helper = controller_fixture.ControllerBatchingTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.helper.listed = []
        self.binding = dict(project=INVENTORY_BINDING['project_id'], repository='101', scope_revision='a'*64)
        self.settings = dict(mode='both', bindings=[dict(self.binding, platform=p,
            destination='CAPPROVED' if p == 'slack' else TEAMS_DESTINATION, revision='1', secret_ref='env:AWF_REPORT_TEST')
            for p in ('teams', 'slack')])
        self.network = ReportingNetwork()
        self.addCleanup(self.network.block.set)
        self.clock = FakeClock('2026-11-07T17:00:00Z')
        self.prs = []
        self.repository = deepcopy(controller_fixture.REPOSITORY)
        owner = self
        class NetworkRunner(FakeRunner):
            def run(self, argv, **kwargs):
                if '/pulls?state=closed' in argv[-1]:
                    return [deepcopy(owner.prs)]
                return super().run(argv, **kwargs)
        self.runner = NetworkRunner()
        config = _base_config(Path(self.helper.temp.name))
        config.update(_runner=self.runner, _clock=self.clock, _reporting_http=self.network,
                      reporting_sources=dict(binding=deepcopy(self.binding), pull_requests=[99]),
                      reporting={p: dict(context='workspace' if p == 'slack' else 'tenant', sender='sender')
                                 for p in ('teams', 'slack')})
        root = Path(__file__).resolve().parents[2]
        self.namespace = runpy.run_path(str(root / '.agentic/adapters/reference_controller_adapter.py'))
        self.adapters = self.namespace['build_adapters'](config)
        self.env = patch.dict(os.environ, AWF_REPORT_TEST='synthetic-report-secret')
        self.env.start()
        self.addCleanup(self.env.stop)

    def cycle(self, now, items=None):
        from agentic.operating import validate_operating
        self.clock.value = now
        # Populate the real factory's current, identity-checked inventory cache.
        self.adapters['observe_inventory'](now)
        def operating(value, governance):
            value = deepcopy(value)
            value['reporting'] = deepcopy(self.settings)
            return validate_operating(value, governance)
        calls = {k: self.adapters[k] for k in ('configure_reporting', 'observe_reporting_sources',
                 'verify_reporting_sources', 'verify_reporting_link') if k in self.adapters}
        if 'configure_reporting' not in calls:
            calls['reporting_providers'] = self.adapters.get('reporting_providers', {})
        with patch('test_awf244_controller_wiring.validate_operating', operating), \
             patch.object(controller_fixture, 'REPOSITORY', self.repository), \
             patch('test_continuous_controller.ROOT', self.repository['repository_root']):
            return self.helper.cycle(now, items, **calls)

    def status(self):
        return reporting_status(self.helper.store, {'reporting': self.settings}, self.binding)

    def test_factory_connects_both_transports_and_refuses_missing_bindings(self):
        result = self.cycle('2026-11-07T17:00:00Z')
        self.assertEqual(result['errors'], [])
        states = self.status()['deliveries']
        self.assertEqual({d['platform'] for d in states}, {'teams', 'slack'})
        self.assertTrue(all(d['status'] == 'DELIVERED' and len(d['attempts']) == 1 for d in states))
        self.assertTrue(callable(self.adapters.get('observe_reporting_sources')))
        self.settings['bindings'] = []
        with self.assertRaisesRegex(ValidationError, 'REPORT_BINDING_REQUIRED'):
            self.cycle('2026-11-07T18:00:00Z')
        self.assertNotIn('synthetic-report-secret', str(result) + str(states))

    def test_missing_transport_and_foreign_scope_refuse_before_secret_access(self):
        from agentic.reporting.platform import build_reporting_providers
        operating = dict(reporting=deepcopy(self.settings))
        with patch('agentic.reporting.platform.environment_secret', side_effect=AssertionError('secret read')):
            with self.assertRaisesRegex(ValidationError, 'REPORT_TRANSPORT_BINDING_REQUIRED'):
                build_reporting_providers({}, operating, self.binding)
            operating['reporting']['bindings'][0]['scope_revision'] = 'foreign'
            with self.assertRaisesRegex(ValidationError, 'REPORT_SCOPE_BINDING_MISMATCH'):
                build_reporting_providers({}, operating, self.binding)

    def test_default_off_factory_cycle_has_no_reporting_state_or_secret_reads(self):
        self.settings = dict(mode='off', bindings=[])
        with patch('agentic.reporting.platform.environment_secret', side_effect=AssertionError('secret read')):
            result = self.cycle('2026-11-07T17:00:00Z')
        self.assertEqual(result['reporting']['state'], 'OFF')
        self.assertEqual(self.network.posts, [])
        with self.helper.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM controller_meta WHERE key LIKE 'reporting:%'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM controller_reports').fetchone()[0], 0)

    def test_slow_destination_is_bounded_other_platform_and_next_cycle_proceed(self):
        # Keep real Git admission while excluding unrelated dirty-path scans
        # from the bounded destination timing test. This clone makes no commit.
        clean = Path(self.helper.temp.name) / 'clean-repository'
        subprocess.run(['git', 'clone', '--shared', '--quiet', str(self.repository['repository_root']), str(clean)],
                       check=True, capture_output=True)
        self.repository['repository_root'] = clean
        self.network.slow = True
        from agentic.reporting import runtime
        from agentic.reporting.bounded import DESTINATION_SECONDS, HTTP_TIMEOUT_SECONDS
        timing = {}
        original_drain = runtime.drain
        original_http = ReportingNetwork.__call__
        original_finish = self.helper.store.finish_dispatch

        def finish_dispatch(*args, **kwargs):
            receipt = original_finish(*args, **kwargs)
            timing['dispatch_complete'] = time.monotonic()
            return receipt

        def drain(*args, **kwargs):
            timing['delivery_start'] = time.monotonic()
            # Require actual persisted dispatch completion before network work.
            self.assertIn('dispatch_complete', timing)
            self.assertLessEqual(timing['dispatch_complete'], timing['delivery_start'])
            return original_drain(*args, **kwargs)

        def http(network, method, url, headers, payload):
            response = original_http(network, method, url, headers, payload)
            if 'slack.com' in url:
                if method == 'POST' and 'Hourly progress' in str(payload):
                    timing['slack_post'] = time.monotonic()
                if 'conversations.history' in url and 'slack_post' in timing:
                    timing['slack_readback'] = time.monotonic()
            return response

        started = time.monotonic()
        with patch.object(self.helper.store, 'finish_dispatch', finish_dispatch), \
             patch.object(runtime, 'drain', drain), \
             patch.object(ReportingNetwork, '__call__', http):
            result = self.cycle('2026-11-07T18:00:00Z', [ticket('EX-2', 1, paths=['first.txt'])])
        self.assertEqual(len(result['dispatch_receipts']), 1)
        for event in ('slack_post', 'slack_readback'):
            self.assertLess(timing[event] - timing['delivery_start'], 1.5)
        # Joined delivery may hold the next cycle, within the documented bound.
        self.assertGreaterEqual(time.monotonic() - timing['delivery_start'], 5)
        self.assertLess(time.monotonic() - started, DESTINATION_SECONDS + HTTP_TIMEOUT_SECONDS)
        self.assertFalse(any(t.name.startswith('awf-report-') for t in threading.enumerate()))
        self.assertEqual({d['status'] for d in self.status()['deliveries'] if d['platform'] == 'slack'}, {'DELIVERED'})
        self.assertTrue(self.network.entered.is_set())
        self.assertCountEqual([p for p, _ in self.network.posts], ['teams', 'slack'])
        self.assertEqual(result['reporting']['state'], 'ENABLED')
        self.assertEqual({d['status'] for d in self.status()['deliveries'] if d['platform'] == 'teams'}, {'UNKNOWN'})
        started = time.monotonic()
        continued = self.cycle('2026-11-07T18:00:01Z', [ticket('EX-1', 1, paths=['README.md'])])
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(len(continued['dispatch_receipts']), 1)
        self.assertEqual(len(self.network.posts), 2)
        self.network.block.set()
        deadline = time.monotonic() + 2
        while not self.network.messages['teams'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.cycle('2026-11-07T18:00:02Z')
        self.assertEqual({d['status'] for d in self.status()['deliveries']}, {'DELIVERED'})
        self.assertEqual(len(self.network.posts), 2)

    def test_72_hours_offline_alert_first_all_periods_retained_bounded_posts(self):
        self.cycle('2026-11-07T16:59:59Z')
        # Real factory inventory source maps a blocked owner decision to an alert.
        self.runner.inventory[0]['body'] = __import__('json').dumps(dict(
            __import__('json').loads(self.runner.inventory[0]['body']), disposition='BLOCKED',
            actor='owner', reason='Owner access required', next_action='Grant access'))
        result = self.cycle('2026-11-10T16:59:59Z')
        self.assertEqual(result['errors'], [])
        for platform in ('teams', 'slack'):
            posts = [p for name, p in self.network.posts if name == platform]
            self.assertGreaterEqual(len(posts), 2)
            self.assertLessEqual(len(posts), 4)
            text = posts[0].get('text', posts[0].get('body', {}).get('content', ''))
            self.assertIn('Owner action', text)
            text = posts[1].get('text', posts[1].get('body', {}).get('content', ''))
            self.assertIn('Hourly progress', text)
            catchup = [r for r in self.status()['reports'] if r.get('catch_up_periods')]
            self.assertEqual(len(catchup), 1)
            self.assertEqual(len([p for p in catchup[0]['catch_up_periods'] if p['kind'] == 'hourly']), 72)
            self.assertIn('2026-11-07', str(catchup[0]['body']))
        self.assertEqual(len([r for r in self.status()['reports'] if r['kind'] == 'hourly']), 72)

    def pr(self, merged_at):
        self.prs = [dict(id=99, number=99, html_url='https://github.com/example/project/pull/99',
                         merged_at=merged_at, merge_commit_sha='b'*40, head={'sha': 'c'*40})]

    def test_timed_out_readback_reconciles_after_recovery_without_reposting(self):
        self.settings['mode'] = 'slack'
        self.network.slow_readback = True
        self.cycle('2026-11-07T18:00:00Z')
        self.assertTrue(self.network.entered.is_set())
        self.assertEqual(self.status()['deliveries'][0]['status'], 'UNKNOWN')
        self.network.block.set()
        # A completed timeout leaves no local worker. Restore the remote
        # readback and reconcile the immutable attempt without posting again.
        self.assertFalse(any(t.name.startswith('awf-report-') for t in threading.enumerate()))
        self.cycle('2026-11-07T18:00:01Z')
        self.assertEqual(self.status()['deliveries'][0]['status'], 'DELIVERED')
        self.assertEqual(len(self.network.posts), 1)

    def test_historical_period_excludes_later_valid_merge_and_cursor_advances(self):
        self.cycle('2026-11-07T17:00:00Z')
        self.pr('2026-11-09T10:00:00Z')
        result = self.cycle('2026-11-10T17:00:00Z')
        self.assertEqual(result['errors'], [])
        daily = [r for r in self.status()['reports'] if r['kind'] == 'eod']
        counts = {r['body']['data']['today'][:10]: r['body']['data']['detail_metrics']['daily']['today']['merged_pr_count']
                  for r in daily}
        self.assertIn(0, counts.values())
        self.assertEqual(sum(r['body']['data']['detail_metrics']['daily']['today']['merged_pr_count'] for r in daily), 1)
        self.assertEqual(self.cycle('2026-11-10T17:00:00Z')['reporting']['generated'], 0)

    def test_invalid_merge_fails_daily_view_but_other_periods_and_cursor_progress(self):
        self.cycle('2026-11-07T17:00:00Z')
        self.pr('invalid-time')
        result = self.cycle('2026-11-08T18:00:00Z')
        reports = result['reporting'].get('reports', [])
        failed = [r for r in reports if r.get('generation_error')]
        self.assertTrue(failed)
        self.assertTrue(all(r['generation_error'] == 'REPORT_MERGE_TIMESTAMP_INVALID' for r in failed))
        self.assertTrue(any(r['kind'] == 'hourly' and not r.get('generation_error') for r in reports))
        self.assertEqual(self.cycle('2026-11-08T18:00:00Z')['reporting']['generated'], 0)

    def test_truly_future_merge_still_fails_without_losing_periods(self):
        self.pr('2026-11-11T10:00:00Z')
        result = self.cycle('2026-11-10T17:00:00Z')
        failed = [r for r in result['reporting']['reports'] if r.get('generation_error')]
        self.assertEqual([r['generation_error'] for r in failed], ['REPORT_FUTURE_MERGE'])
        self.assertEqual(self.cycle('2026-11-10T17:00:00Z')['reporting']['generated'], 0)

    def test_first_daily_filters_today_without_prior_report(self):
        self.pr('2026-11-09T10:00:00Z')
        self.cycle('2026-11-10T17:00:00Z')
        daily = next(r for r in self.status()['reports'] if r['kind'] == 'eod')
        self.assertEqual(daily['body']['data']['detail_metrics']['daily']['today']['merged_pr_count'], 0)
        self.assertEqual(daily['body']['data']['prs'], [])
        self.assertIn('2026-11-10', daily['body']['data']['today'])

    def test_default_off_no_transaction_metadata_bytes_or_secrets(self):
        store = self.helper.store
        before = store.path.read_bytes()
        with store.connection() as db:
            metadata = list(db.execute('SELECT * FROM controller_meta'))
        with patch.object(store, 'transaction', side_effect=AssertionError('OFF write transaction')), \
             patch.dict(os.environ, {}, clear=True):
            result = reporting_cycle(store, operating={}, binding=self.binding, now='2026-11-07T17:00:00Z')
        self.assertEqual(result['state'], 'OFF')
        self.assertEqual(store.path.read_bytes(), before)
        with store.connection() as db:
            self.assertEqual(list(db.execute('SELECT * FROM controller_meta')), metadata)

    def merge_base_controller(self):
        root = Path(__file__).resolve().parents[2]
        raw = subprocess.run(['git', 'show', 'a3336b8990a9e59985f67bf69d41846b898db57b:'
            '.agentic/lib/agentic/continuous_controller.py'], cwd=root, check=True, capture_output=True).stdout
        module = types.ModuleType('agentic._reporting_rollback')
        module.__package__ = 'agentic'
        module.__file__ = str(root / '.agentic/lib/agentic/continuous_controller.py')
        exec(compile(raw, '<merge-base-controller>', 'exec'), module.__dict__)
        return module.ContinuousControllerStore

    def test_rollback_merge_base_never_consumes_report_as_legacy_digest(self):
        self.cycle('2026-11-07T17:00:00Z')
        controller = self.merge_base_controller()
        older = controller(self.helper.path, ['A', 'B', 'C'], cadence_seconds=3600)
        digest = older.digest('2026-11-07T18:00:00Z')
        if digest is not None:
            self.assertIn('delivery_id', digest)
            older.acknowledge_digest(digest['delivery_id'], '2026-11-07T18:00:00Z')
        self.assertTrue(self.status()['reports'])

    def test_pre_release_report_rows_relocate_additively_with_attempts_intact(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.cycle('2026-11-07T17:00:00Z')
        report = next(r for r in self.status()['reports'] if r['kind'] == 'eod')
        with self.helper.store.connection() as db:
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='controller_report_delivery'").fetchone()[0]
            rows = [tuple(row) for row in db.execute('SELECT * FROM controller_report_delivery')]
        legacy_path = self.helper.path.with_name('pre-release.sqlite3')
        controller = self.merge_base_controller()
        controller(legacy_path, ['A', 'B', 'C'], cadence_seconds=3600)
        raw = canonical(report).decode()
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute(schema)
            db.executemany('INSERT INTO controller_report_delivery VALUES(?,?,?)', rows)
            db.execute("INSERT INTO controller_outbox VALUES(?,'REPORT',0,?,?,NULL)",
                       (report['report_id'], '2026-11-07T17:00:00Z', raw))
            db.commit()
        for _ in range(2):
            migrated = ContinuousControllerStore(legacy_path, ['A', 'B', 'C'], cadence_seconds=3600)
            with migrated.connection() as db:
                row = db.execute('SELECT kind,payload_json FROM controller_reports').fetchone()
                self.assertEqual(tuple(row), ('eod', raw))
                self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM controller_report_delivery')], rows)
                self.assertEqual(db.execute("SELECT count(*) FROM controller_outbox WHERE kind='REPORT'").fetchone()[0], 0)
        older = controller(legacy_path, ['A', 'B', 'C'], cadence_seconds=3600)
        digest = older.digest('2026-11-07T18:00:00Z')
        self.assertIn('delivery_id', digest)
        older.acknowledge_digest(digest['delivery_id'], '2026-11-07T18:00:00Z')
