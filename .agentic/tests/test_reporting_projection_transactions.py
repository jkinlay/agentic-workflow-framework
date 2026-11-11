"""Reporting observations must not retain SQLite locks or stale dependencies."""
from copy import deepcopy
import sqlite3
import threading
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, loads
from agentic.reporting import runtime
from agentic.reporting.delivery import DeliveryOutbox, delivery_intent
import test_reporting_policy_review as policy_review


class ProjectionTransactionTests(unittest.TestCase):
    controller = policy_review.PolicyReviewTests.controller

    def probe(self, store, results, mutate=None):
        db = sqlite3.connect(str(store.path), timeout=0, isolation_level=None)
        try:
            try:
                db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:
                results.append(False)
                return
            results.append(True)
            if mutate:
                mutate(db)
            db.commit()
        finally:
            db.close()

    def periodic(self, *, secret=False, mutate=None):
        case = self.controller()
        case.prs = [dict(id='99', number=99, html_url='https://github.com/example/project/pull/99',
            merged_at='2026-11-10T09:00:00Z', merge_commit_sha='b'*40, head={'sha': 'c'*40})]
        results = []
        configure = case.adapters['configure_reporting']
        once = []
        def observe():
            self.probe(case.helper.store, results, mutate if not once else None)
            once.append(True)
        if secret:
            def providers(*args, **kwargs):
                values = configure(*args, **kwargs)
                for sender in values.values():
                    original = sender.resolve_secret
                    def resolve(ref, original=original):
                        # Probe retention on the controller thread; concurrent
                        # destination workers may briefly own each other's locks.
                        if threading.current_thread() is threading.main_thread():
                            observe()
                        return original(ref)
                    sender.resolve_secret = resolve
                return values
            case.adapters['configure_reporting'] = providers
        else:
            original = case.adapters['verify_reporting_link']
            def link(*args, **kwargs):
                observe()
                return original(*args, **kwargs)
            case.adapters['verify_reporting_link'] = link
        result = case.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(results, 'the real callback must run')
        return case, result, results

    def test_periodic_link_verifier_runs_without_controller_write_lock(self):
        case, result, probes = self.periodic()
        self.assertTrue(all(probes), probes)
        self.assertEqual([], result['errors'])
        self.assertTrue(case.network.posts)

    def test_periodic_secret_resolver_runs_without_controller_write_lock(self):
        case, result, probes = self.periodic(secret=True)
        self.assertTrue(all(probes), probes)
        self.assertEqual([], result['errors'])
        self.assertTrue(case.network.posts)

    def test_review_batch_retention_resolver_runs_without_controller_write_lock(self):
        from agentic.review_batching import ReviewBatchStore
        case = self.controller()
        now = '2026-11-10T10:00:00Z'
        batches = ReviewBatchStore(case.helper.store)
        batches.enqueue(case.helper.current, now, {})
        payload = batches.claim(now, {}, lambda c: case.helper.observe_candidate(c, now))
        probes = []
        class Sender:
            binding = case.settings['bindings'][1]
            def resolve_secret(sender, ref):
                self.probe(case.helper.store, probes)
                return 'synthetic-report-secret'
        port = runtime.ReviewReportingPort(case.helper.store, {'reporting': case.settings},
            case.binding, {'slack': Sender()}, lambda: now)
        port._retain_review_batch(payload)
        self.assertTrue(probes)
        self.assertTrue(all(probes), probes)
        self.assertTrue(case.status()['reports'])

    def test_link_observation_cannot_commit_against_changed_reporting_cursor(self):
        changed = []
        def mutate(db):
            # The absent row is a dependency too.
            key = 'reporting:' + fingerprint('reporting-scope', case.binding)
            db.execute('INSERT INTO controller_meta VALUES(?,?)',
                (key, canonical(dict(cursor='2026-11-10T09:59:00Z', owner={})).decode()))
            changed.append(True)
        # Install the mutation only after obtaining the fixture binding.
        case = self.controller()
        case.prs = [dict(id='99', number=99, html_url='https://github.com/example/project/pull/99',
            merged_at='2026-11-10T09:00:00Z', merge_commit_sha='b'*40, head={'sha': 'c'*40})]
        probes = []
        original = case.adapters['verify_reporting_link']
        def link(*args, **kwargs):
            if not changed:
                self.probe(case.helper.store, probes, mutate)
            return original(*args, **kwargs)
        case.adapters['verify_reporting_link'] = link
        result = case.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(changed)
        self.assertEqual('REPORT_PROJECTION_STATE_CHANGED', result['reporting']['reason'])
        self.assertEqual([], case.status()['reports'])
        self.assertEqual([], case.network.posts)

    def test_secret_observation_cannot_overwrite_changed_delivery_state(self):
        case = self.controller()
        case.cycle('2026-11-10T09:00:00Z')
        before = deepcopy(case.status()['reports'])
        configure = case.adapters['configure_reporting']
        changed = []
        def providers(*args, **kwargs):
            values = configure(*args, **kwargs)
            for sender in values.values():
                original = sender.resolve_secret
                def resolve(ref, original=original):
                    # Context filtering precedes the snapshot; mutate during retention.
                    import inspect
                    if not changed and any(f.function in {'_retain', '_prepare_report'} for f in inspect.stack()):
                        def mutate(db):
                            row = db.execute('SELECT delivery_id,state_json FROM controller_report_delivery ORDER BY rowid LIMIT 1').fetchone()
                            state = loads(row[1]); state['external_observation'] = 'retained'
                            db.execute('UPDATE controller_report_delivery SET state_json=? WHERE delivery_id=?',
                                (canonical(state).decode(), row[0]))
                            changed.append(row[0])
                        self.probe(case.helper.store, [], mutate)
                    return original(ref)
                sender.resolve_secret = resolve
            return values
        case.adapters['configure_reporting'] = providers
        result = case.cycle('2026-11-10T10:00:00Z')
        self.assertTrue(changed)
        self.assertEqual('REPORT_PROJECTION_STATE_CHANGED', result['reporting']['reason'])
        self.assertEqual(before, case.status()['reports'])
        self.assertEqual('retained', next(d for d in case.status()['deliveries'] if d['delivery_id'] == changed[0])['external_observation'])

    def test_unrelated_controller_metadata_does_not_invalidate_projection(self):
        def mutate(db):
            db.execute("INSERT INTO controller_meta VALUES('unrelated-probe','unchanged-authority')")
        case, result, probes = self.periodic(mutate=mutate)
        self.assertTrue(all(probes), probes)
        self.assertEqual([], result['errors'])
        self.assertTrue(case.network.posts)

    def test_prepared_enqueue_rejects_changed_intent_bytes(self):
        case = self.controller()
        intent = delivery_intent('prepared', case.settings['bindings'][1], 'Safe report')
        prepared = DeliveryOutbox.prepare_enqueue(intent)
        changed = delivery_intent('prepared', case.settings['bindings'][1], 'Changed safe report')
        with self.assertRaisesRegex(ValidationError, 'REPORT_PERSISTENCE_PREPARATION_MISMATCH'):
            with case.helper.store.transaction() as db:
                DeliveryOutbox.enqueue_in_transaction(db, changed, prepared=prepared)
        self.assertEqual([], case.status()['deliveries'])

    def test_transactional_enqueue_without_preparation_never_resolves_secret(self):
        case = self.controller()
        intent = delivery_intent('unprepared', case.settings['bindings'][1], 'Safe report')
        resolved = []
        with patch('agentic.reporting.platform.environment_secret', side_effect=lambda ref: resolved.append(ref)):
            with self.assertRaisesRegex(ValidationError, 'REPORT_PERSISTENCE_PREPARATION_REQUIRED'):
                with case.helper.store.transaction() as db:
                    DeliveryOutbox.enqueue_in_transaction(db, intent)
        self.assertEqual([], resolved)
        self.assertEqual([], case.status()['deliveries'])

    def test_preparation_failure_preserves_cursor_reports_and_delivery_history(self):
        case = self.controller()
        case.cycle('2026-11-10T09:00:00Z')
        before = case.status()
        key = 'reporting:' + fingerprint('reporting-scope', case.binding)
        with case.helper.store.connection() as db:
            cursor = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()[0]
        with patch.object(runtime, 'render_body', side_effect=ValidationError('REPORT_SYNTHETIC_RENDER_FAILURE')):
            result = case.cycle('2026-11-10T10:00:00Z')
        self.assertEqual('REFUSED', result['reporting']['state'])
        self.assertEqual(before, case.status())
        with case.helper.store.connection() as db:
            self.assertEqual(cursor, db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()[0])
