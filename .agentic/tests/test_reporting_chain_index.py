"""Review recovery parses only its indexed chain, with honest shared detail."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic.canonical import canonical, loads
from agentic.reporting import runtime
from agentic.review_batching import ReviewBatchStore
import agentic.review_batching as batching
import test_reporting_restoration_chain as fixtures


class ChainIndexTests(unittest.TestCase):
    for _name in ('setUp', 'policy_inputs', 'accepted_calendar', 'load_policy', 'observe',
                  'fence', 'seed', 'observation', 'port', 'notices', 'count', 'retain', 'next_notice'):
        locals()[_name] = getattr(fixtures.RestorationChainTests, _name)

    def parses(self, callback):
        count = 0
        def counted(raw):
            nonlocal count
            value = loads(raw)
            if isinstance(value, dict) and 'report_id' in value and 'kind' in value:
                count += 1
            return value
        with patch.object(batching, 'loads', counted):
            callback()
        return count

    def checked_sweep(self, pending):
        counts = [0, 0]
        def counted(method, position):
            def call(*args):
                rows = method(*args)
                counts[position] += len(rows)
                return rows
            return call
        with patch.object(ReviewBatchStore, '_occurrence_reports', counted(ReviewBatchStore._occurrence_reports, 0)), \
                patch.object(ReviewBatchStore, '_occurrence_delivery_rows', counted(ReviewBatchStore._occurrence_delivery_rows, 1)):
            parses = self.parses(lambda: pending.finish_restoration_readback(self.now))
        return parses, counts

    def archive(self, hours=3000, completed=64):
        # Synthetic immutable history: unrelated hourly reports and distinct,
        # fully delivered review chains, each with its own original charge.
        with self.store.transaction() as db:
            from agentic.reporting.delivery import delivery_intent, DeliveryOutbox
            self.remove_index(db)
            reports = [loads(r[0]) for r in db.execute('SELECT payload_json FROM controller_reports')]
            original = next(r for r in reports if not r.get('review_correction') and not r.get('review_restoration'))
            db.executemany('INSERT INTO controller_reports VALUES(?,?,?,?)',
                [(f'archive-hour-{i}', 'hourly', self.now, canonical(dict(report_id=f'archive-hour-{i}',
                    kind='hourly', binding=self.binding, delivery_ids=[])).decode()) for i in range(hours)])
            for i in range(completed):
                batch = 'archive-batch-' + str(i)
                ids = {r['report_id']: ('review-' + batch if r is original else f'archive-{i}-' + r['report_id'])
                       for r in reports}
                for prior in reports:
                    report = deepcopy(prior)
                    report['report_id'] = ids[prior['report_id']]
                    # Each archive report has its own durable outbox identities;
                    # aliasing the live report's rows is not valid history.
                    report['delivery_ids'] = []
                    for delivery in prior['delivery_ids']:
                        intent, state = DeliveryOutbox._load(db, delivery)
                        cloned = delivery_intent(report['report_id'], intent['binding'], intent['payload'],
                                                 thread_id=intent.get('thread_id'))
                        db.execute('INSERT INTO controller_report_delivery VALUES(?,?,?)',
                            (cloned['delivery_id'], canonical(cloned).decode(), canonical(state).decode()))
                        report['delivery_ids'].append(cloned['delivery_id'])
                    if report.get('review_batch'):
                        report['review_batch']['batch_id'] = batch
                    notice = report.get('review_correction') or report.get('review_restoration')
                    if notice:
                        notice['prior_report'] = ids[notice['prior_report']]
                    db.execute('INSERT INTO controller_reports VALUES(?,?,?,?)',
                        (report['report_id'], report['kind'], self.now, canonical(report).decode()))
                occurrence = batching.fingerprint('review-restoration-occurrence-v1', [batch, self.binding, 1])
                db.execute('INSERT INTO review_restoration_charge_v1(occurrence_id,opening,batch_id,reserved_at) VALUES(?,?,?,?)',
                    (occurrence, '2026-11-09T00:00:00Z', batch, self.now))

    @staticmethod
    def remove_index(db):
        # Model an older database, whose retained reports predate the index.
        for name in ('review_chain_pending_insert_v1', 'review_chain_no_update_v1',
                     'review_chain_no_delete_v1', 'review_chain_no_replace_v1'):
            db.execute('DROP TRIGGER IF EXISTS ' + name)
        db.execute('DROP TABLE IF EXISTS review_report_chain_v1')
        db.execute('DROP TABLE IF EXISTS review_chain_pending_v1')
        db.execute("DELETE FROM controller_meta WHERE key='review-chain-index-v1' OR key LIKE 'review-chain-tip:%'")

    def measured(self):
        with self.store.connection() as db:
            notice = deepcopy(self.restoration)
            notice['review_restoration']['prior_report'] = self.restoration['report_id']
            validation = self.parses(lambda: ReviewBatchStore.validate_review_notice(db, notice))
        sweep = self.parses(lambda: ReviewBatchStore(self.store).finish_restoration_readback(self.now))
        return validation, sweep

    def test_archive_does_not_expand_validation_or_recovery_parse_count(self):
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        small = self.measured()
        self.archive()
        # Re-open as an existing database: the one-time upgrade is outside
        # admission. Subsequent validations/sweeps must never parse the archive.
        from agentic.continuous_controller import ContinuousControllerStore
        self.store = ContinuousControllerStore(self.path, ['A'])
        ReviewBatchStore(self.store).finish_restoration_readback(self.now)
        large = self.measured()
        print('CHAIN_PARSE_COUNTS small=%r large=%r (validation,sweep)' % (small, large), flush=True)
        self.assertLessEqual(large[0], 12)
        self.assertLessEqual(large[1], 3)
        self.assertEqual(large, small)

    def test_existing_reports_backfilled_once_and_restart_reuses_index(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        with self.store.transaction() as db:
            # A legacy crash can leave terminal delivery with stale detail.
            db.execute("UPDATE review_presentation_detail_v1 SET reason='Restoration reserved; unresolved until terminal readback',"
                       "retry_at='2026-11-10T07:00:00Z'")
            self.remove_index(db)
            db.execute('DROP INDEX review_restoration_unresolved_v1')
            db.execute('ALTER TABLE review_restoration_charge_v1 DROP COLUMN resolved')
        first = self.parses(lambda: ContinuousControllerStore(self.path, ['A']))
        second = self.parses(lambda: ContinuousControllerStore(self.path, ['A']))
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT resolved FROM review_restoration_charge_v1').fetchone()[0], 1)
            detail = db.execute('SELECT * FROM review_presentation_detail_v1').fetchone()
            self.assertEqual(detail['reason'], 'Restoration DELIVERED')
            self.assertIsNone(detail['retry_at'])
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(len(self.sent), 3)

    def test_unresolved_sweep_parses_only_active_chain_after_large_archive(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        self.archive()
        self.store = ContinuousControllerStore(self.path, ['A'])
        pending = ReviewBatchStore(self.store)
        pending.finish_restoration_readback(self.now)
        self.now = '2026-11-10T07:00:00Z'
        self.retain(self.next_notice(self.restoration['report_id']))
        provider = self.providers['slack']
        reconcile = provider.reconcile
        provider.reconcile = lambda intent, attempts: dict(reconcile(intent, attempts), status='UNKNOWN')
        self.port().resume(self.root['operating_calendar'], self.observation)
        small, small_checks = self.checked_sweep(pending)
        with self.store.transaction() as db:
            db.executemany('INSERT INTO controller_reports VALUES(?,?,?,?)',
                [(f'extra-{i}', 'hourly', self.now, canonical(dict(report_id=f'extra-{i}', kind='hourly')).decode())
                 for i in range(3000)])
        large, large_checks = self.checked_sweep(pending)
        print('UNRESOLVED_SWEEP_PARSE_COUNTS small=%d large=%d' % (small, large), flush=True)
        print('OCCURRENCE_CROSSCHECK_ROWS small=%r large=%r (reports,outbox)' %
              (small_checks, large_checks), flush=True)
        self.assertEqual(small_checks, [1, 1])
        self.assertEqual(large_checks, small_checks)
        self.assertEqual(small, large)
        self.assertGreater(small, 0)
        self.assertLessEqual(large, 4)

    def test_upgrade_with_forked_rows_rolls_back_instead_of_accepting_index(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.seed()
        report = deepcopy(self.restoration)
        report['report_id'] = 'legacy-fork'
        with self.store.transaction() as db:
            self.remove_index(db)
            db.execute('INSERT INTO controller_reports VALUES(?,?,?,?)',
                (report['report_id'], report['kind'], self.now, canonical(report).decode()))
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_TIP_CHANGED$'):
            ContinuousControllerStore(self.path, ['A'])
        with self.store.connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='review_report_chain_v1'").fetchone())

    def test_index_disagreement_refuses_public_resume_without_send(self):
        self.seed()
        before = list(self.sent)
        with self.store.transaction() as db:
            db.execute('DROP TRIGGER review_chain_no_delete_v1')
            db.execute('DELETE FROM review_report_chain_v1 WHERE report_id=?', (self.restoration['report_id'],))
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(self.sent, before)

    def test_unindexed_insert_is_refused_instead_of_silently_rebuilt(self):
        self.seed()
        before = list(self.sent)
        report = deepcopy(self.restoration)
        report['report_id'] = 'unindexed-report'
        with self.store.transaction() as db:
            db.execute('INSERT INTO controller_reports VALUES(?,?,?,?)',
                (report['report_id'], report['kind'], self.now, canonical(report).decode()))
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            self.port().resume(self.root['operating_calendar'], self.observation)
        self.assertEqual(self.sent, before)

    def test_chain_update_delete_and_replace_are_refused(self):
        self.seed()
        import sqlite3
        for statement in ('UPDATE review_report_chain_v1 SET report_id=report_id',
                          'DELETE FROM review_report_chain_v1',
                          'INSERT OR REPLACE INTO review_report_chain_v1 SELECT * FROM review_report_chain_v1'):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db:
                    db.execute(statement)

    def test_terminal_withdrawal_resolves_charge_and_stops_future_sweeps(self):
        from agentic.reporting.delivery import DeliveryOutbox
        self.seed()
        provider = self.providers['slack']
        reconcile = provider.reconcile
        provider.reconcile = lambda intent, attempts: dict(reconcile(intent, attempts), status='ABSENT', terminal=True)
        self.port().resume(self.root['operating_calendar'], self.observation)
        with self.store.transaction() as db:
            DeliveryOutbox.withdraw_in_transaction(db, self.restoration['delivery_ids'][0],
                dict(reason='SYNTHETIC_READINESS_WITHDRAWN'))
        pending = ReviewBatchStore(self.store)
        pending.finish_restoration_readback(self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT resolved FROM review_restoration_charge_v1').fetchone()[0], 1)
        detail = pending.view(self.now, self.root['operating_calendar'])['intents'][0]
        self.assertEqual(detail['reason'], 'Restoration WITHDRAWN')
        self.assertIsNone(detail['retry_at'])
        self.assertEqual(self.parses(lambda: pending.finish_restoration_readback(self.now)), 0)

    def test_staggered_destination_unknown_has_unresolved_public_detail(self):
        self.staggered_retention()
        retained_detail = ReviewBatchStore(self.store).view(self.now, self.root['operating_calendar'])['intents'][0]
        self.assertIn('unresolved', retained_detail['reason'])
        provider = self.providers['teams']
        reconcile = provider.reconcile
        provider.reconcile = lambda intent, attempts: dict(reconcile(intent, attempts), status='UNKNOWN')
        for _ in range(2):
            self.port().resume(self.root['operating_calendar'], self.observation)
        status = runtime.reporting_status(self.store, self.root, self.binding)
        restorations = {r['report_id'] for r in status['reports'] if r.get('review_restoration')}
        states = [d['status'] for d in status['deliveries'] if d['report_id'] in restorations]
        detail = ReviewBatchStore(self.store).view(self.now, self.root['operating_calendar'])['intents'][0]
        print('STAGGERED_PUBLIC_DETAIL states=%r reason=%r retry_at=%r' %
              (states, detail['reason'], detail['retry_at']), flush=True)
        self.assertIn('DELIVERED', states)
        self.assertIn('UNKNOWN', states)
        self.assertIn('unresolved', detail['reason'])
        self.assertIsNotNone(detail['retry_at'])

    def staggered_retention(self):
        self.seed(partial=True)
        for _ in range(2):
            self.port().resume(self.root['operating_calendar'], self.observation)
        # First restoration has delivered. The second original destination
        # becomes visible only later, so its correction/restoration is retained
        # later at the same generation and reuses the existing charge.
        self.partial = False
        self.port().observe_review_delivery(self.payload)
        self.readiness = 'FINAL_REVIEW'
        self.now = '2026-11-09T10:04:00Z'
        def teams_notice():
            from agentic.reporting.observations import collect_review_corrections
            for _ in range(4):
                notices = collect_review_corrections(self.store, self.binding, self.now, self.observation)
                selected = [n for n in notices if n['binding']['platform'] == 'teams']
                if selected:
                    runtime.reporting_cycle(self.store, operating=self.root, binding=self.binding, now=self.now,
                        providers=self.providers, context=dict(review_corrections=selected))
                    runtime.drain(self.store, self.root, self.binding, self.providers,
                        report_ids={n['id'] for n in selected if not n.get('restored')},
                        before_send=lambda: self.port()._readmit())
                    return
            self.fail('Expected a later Teams notice through the public collector')
        teams_notice()
        self.readiness = 'READY_FOR_OWNER_AUTHORIZATION'
        self.now = '2026-11-09T10:05:00Z'
        teams_notice()

    def test_partial_occurrence_index_refuses_resolution_and_status(self):
        self.staggered_retention()
        with self.store.transaction() as db:
            db.execute('DROP TRIGGER review_chain_no_delete_v1')
            db.execute('DELETE FROM review_report_chain_v1 WHERE occurrence_id IS NOT NULL AND report_id<>?',
                       (self.restoration['report_id'],))
        pending = ReviewBatchStore(self.store)
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            pending.finish_restoration_readback(self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT resolved FROM review_restoration_charge_v1').fetchone()[0], 0)
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            pending.view(self.now, self.root['operating_calendar'])

    def test_dangling_index_row_refuses_reopening(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.seed()
        with self.store.transaction() as db:
            db.execute('DROP TRIGGER controller_reports_no_delete')
            db.execute('DELETE FROM controller_reports WHERE report_id=?', (self.restoration['report_id'],))
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            ContinuousControllerStore(self.path, ['A'])

    def test_missing_outbox_member_refuses_resolution(self):
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        with self.store.transaction() as db:
            db.execute('UPDATE review_restoration_charge_v1 SET resolved=0')
            db.execute('DROP TRIGGER controller_report_delivery_no_delete')
            db.execute('DELETE FROM controller_report_delivery WHERE delivery_id=?', (self.restoration['delivery_ids'][0],))
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            ReviewBatchStore(self.store).finish_restoration_readback(self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT resolved FROM review_restoration_charge_v1').fetchone()[0], 0)

    def test_extra_outbox_member_refuses_resolution(self):
        from agentic.reporting.delivery import DeliveryOutbox, delivery_intent
        self.seed()
        self.port().resume(self.root['operating_calendar'], self.observation)
        with self.store.transaction() as db:
            db.execute('UPDATE review_restoration_charge_v1 SET resolved=0')
            intent, state = DeliveryOutbox._load(db, self.restoration['delivery_ids'][0])
            destination = dict(intent['binding'], platform='teams', destination='synthetic-extra')
            extra = delivery_intent(intent['report_id'], destination, intent['payload'])
            db.execute('INSERT INTO controller_report_delivery VALUES(?,?,?)',
                       (extra['delivery_id'], canonical(extra).decode(), canonical(state).decode()))
        with self.assertRaisesRegex(ValidationError, '^REPORT_REVIEW_CHAIN_INDEX_MISMATCH$'):
            ReviewBatchStore(self.store).finish_restoration_readback(self.now)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT resolved FROM review_restoration_charge_v1').fetchone()[0], 0)

    def test_reused_charge_has_retry_at_retention_before_reservation(self):
        self.staggered_retention()
        with self.store.connection() as db:
            detail = db.execute('SELECT * FROM review_presentation_detail_v1').fetchone()
        self.assertIn('unresolved', detail['reason'])
        self.assertEqual(detail['retry_at'], '2026-11-10T07:00:00Z')

    def test_newer_completed_generation_cannot_hide_older_unknown(self):
        self.staggered_retention()
        provider = self.providers['teams']
        reconcile = provider.reconcile
        provider.reconcile = lambda intent, attempts: dict(reconcile(intent, attempts), status='UNKNOWN')
        for _ in range(2):
            self.port().resume(self.root['operating_calendar'], self.observation)
        self.now = '2026-11-10T07:00:00Z'
        self.retain(self.next_notice(self.restoration['report_id']))
        for _ in range(4):
            self.port().resume(self.root['operating_calendar'], self.observation)
        status = runtime.reporting_status(self.store, self.root, self.binding)
        newer = next(d for d in status['deliveries'] if d['report_id'] == 'second-restoration')
        self.assertEqual(newer['status'], 'DELIVERED')
        older = [d for d in status['deliveries'] if d['platform'] == 'teams' and d['status'] == 'UNKNOWN']
        self.assertTrue(older)
        detail = ReviewBatchStore(self.store).view(self.now, self.root['operating_calendar'])['intents'][0]
        self.assertIn('unresolved', detail['reason'])
        self.assertEqual(detail['retry_at'], '2026-11-11T07:00:00Z')
        provider.reconcile = reconcile
        for _ in range(4):
            self.port().resume(self.root['operating_calendar'], self.observation)
        detail = ReviewBatchStore(self.store).view(self.now, self.root['operating_calendar'])['intents'][0]
        self.assertEqual(detail['reason'], 'Restoration DELIVERED')
        self.assertIsNone(detail['retry_at'])


if __name__ == '__main__':
    unittest.main()
