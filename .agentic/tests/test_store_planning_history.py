"""Durable planning namespace: immutable records and fail-closed migration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only
from agentic import ValidationError, planning
from agentic.canonical import canonical, fingerprint
from agentic.store import Store
from test_planning import NOW, config, capacity_kwargs

PROJECT = '00000000-0000-0000-0000-000000000050'


def plan(trigger='project_start'):
    cfg = config()
    return planning.recompute_plan([], cfg, {}, now=NOW, trigger=trigger,
                                   **capacity_kwargs(cfg))['record']


class StorePlanningHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='awf50c-store-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path, PROJECT)
        self.record = plan()

    def test_planning_api_round_trip_restart_and_detached_reads(self):
        self.assertEqual(self.record, planning.append_plan_record(self.store, self.record))
        reopened = Store(self.path, PROJECT)
        history = reopened.planning_history()
        self.assertEqual([self.record], history)
        history[0]['trigger'] = 'changed'
        self.assertEqual([self.record], reopened.planning_history())
        self.assertEqual(self.record, reopened.current_plan())

    def test_duplicate_replacement_and_concurrent_replay_refused(self):
        def append(_):
            try:
                planning.append_plan_record(Store(self.path, PROJECT), self.record)
                return 'written'
            except ValidationError:
                return 'refused'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(['refused', 'written'], sorted(pool.map(append, range(2))))
        replacement = deepcopy(self.record)
        replacement['trigger'] = 'files_changed'
        replacement['plan_sha256'] = fingerprint('planning_audit_record',
            {k: v for k, v in replacement.items() if k != 'plan_sha256'})
        with self.assertRaises(ValidationError):
            self.store.append_planning_record(replacement)
        self.assertEqual([self.record], self.store.planning_history())

    def test_invalid_write_and_tampered_bytes_fail_on_every_read_and_append(self):
        invalid = deepcopy(self.record)
        invalid['trigger'] = 'changed'
        with self.assertRaises(ValidationError):
            self.store.append_planning_record(invalid)
        self.assertEqual([], self.store.planning_history())
        self.store.append_planning_record(self.record)
        # Simulate an attacker bypassing the immutable SQL triggers.
        with self.store.connection() as db:
            db.execute('DROP TRIGGER planning_history_no_update')
            db.execute('UPDATE planning_history SET record_json=?', (canonical(invalid).decode(),))
        for read in (self.store.planning_history, self.store.current_plan,
                     lambda: self.store.append_planning_record(plan('files_changed'))):
            with self.subTest(read=read), self.assertRaises(ValidationError):
                read()

    def test_sql_mutation_and_deletion_refused(self):
        import sqlite3
        self.store.append_planning_record(self.record)
        with self.store.connection() as db:
            for sql in ('UPDATE planning_history SET plan_id=plan_id', 'DELETE FROM planning_history'):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_migration_once_restart_same_input_idempotent_content_or_version_mismatch_refused(self):
        legacy = {'planning_history': [self.record], 'current_plan': self.record}
        self.store.migrate_planning_history(legacy, version=1)
        self.store.append_planning_record(plan('files_changed'))
        reopened = Store(self.path, PROJECT)
        reopened.migrate_planning_history(legacy, version=1)
        self.assertEqual(2, len(reopened.planning_history()))
        for state, version in (({}, 1), (legacy, 2),
                               ({'planning_history': [self.record], 'current_plan': None}, 1)):
            with self.subTest(state=state, version=version), self.assertRaises(ValidationError):
                reopened.migrate_planning_history(state, version=version)
        self.assertEqual(2, len(reopened.planning_history()))

    def test_empty_migration_is_recorded_and_not_replaced(self):
        self.store.migrate_planning_history({}, version=1)
        self.store.migrate_planning_history({'planning_history': []}, version=1)
        with self.assertRaises(ValidationError):
            self.store.migrate_planning_history({'planning_history': [self.record]}, version=1)
        self.assertEqual([], self.store.planning_history())

    def test_migration_validation_is_atomic_and_cannot_overwrite_durable_history(self):
        for state in ({'planning_history': [self.record, self.record]},
                      {'planning_history': [self.record, {'plan_id': 'invalid'}]},
                      {'current_plan': self.record}, {'planning_history': 'bad'}):
            with self.subTest(state=state), self.assertRaises(ValidationError):
                self.store.migrate_planning_history(state, version=1)
            self.assertEqual([], self.store.planning_history())
        self.store.append_planning_record(self.record)
        with self.assertRaises(ValidationError):
            self.store.migrate_planning_history({}, version=1)
        self.assertEqual([self.record], self.store.planning_history())

    def test_namespace_version_tampering_refused(self):
        self.store.append_planning_record(self.record)
        with self.store.connection() as db:
            db.execute("UPDATE planning_history_meta SET value='2' WHERE key='version'")
        with self.assertRaises(ValidationError):
            self.store.planning_history()

    def test_missing_namespace_version_cannot_be_repaired_by_reopening(self):
        self.store.append_planning_record(self.record)
        with self.store.connection() as db:
            db.execute("DELETE FROM planning_history_meta WHERE key='version'")
        with self.assertRaises(ValidationError):
            Store(self.path, PROJECT)

    def test_row_identity_and_resealed_record_tampering_refused(self):
        self.store.append_planning_record(self.record)
        replacement = deepcopy(self.record)
        replacement['trigger'] = 'files_changed'
        replacement['plan_sha256'] = fingerprint('planning_audit_record',
            {k: v for k, v in replacement.items() if k != 'plan_sha256'})
        with self.store.connection() as db:
            db.execute('DROP TRIGGER planning_history_no_update')
            db.execute('UPDATE planning_history SET record_json=?', (canonical(replacement).decode(),))
        with self.assertRaises(ValidationError):
            self.store.planning_history()
        with self.store.connection() as db:
            db.execute('UPDATE planning_history SET record_json=?,plan_id=?',
                       (canonical(self.record).decode(), '00000000-0000-0000-0000-000000000099'))
        with self.assertRaises(ValidationError):
            self.store.current_plan()

    def test_existing_event_chain_unchanged(self):
        self.store.ingest('FIXTURE', {'value': 1}, 'fixture:1')
        before = self.store.verify_chain()
        self.store.migrate_planning_history({}, version=1)
        self.store.append_planning_record(self.record)
        self.assertEqual(before, self.store.verify_chain())


if __name__ == '__main__':
    unittest.main()
