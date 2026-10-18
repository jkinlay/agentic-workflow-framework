"""AWF-54B T8: the batch ledger contract, red until implemented."""
import copy
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))


class MergeTrainLifecycleTests(unittest.TestCase):
    def test_each_batch_audit_record_is_immutable(self):
        from agentic import ValidationError
        from agentic.canonical import canonical
        from agentic.continuous_controller import ContinuousControllerStore

        # Q4 is resolved by the owner: candidate-bound SHA256, AWF-91 store,
        # release-lifetime retention, and no purge absent a new owner ruling.
        # Doc 47 still leaves lifecycle/final-gate fields and branch/PR naming
        # open. These are neutral candidate data, not assertions about those
        # policies. append_batch_audit / batch_audit_records are proposed public
        # store entry points; their absence must fail INSIDE this test.
        candidate = {
            "repository_id": 42,
            "batch_id": "fixture-batch-1",
            "base_sha": "1" * 40,
            "head_sha": "2" * 40,
            "tree_sha": "3" * 40,
            "sources": [{"pr_number": 11, "head_sha": "4" * 40}],
        }
        record = {
            "candidate": candidate,
            "record_sha256": hashlib.sha256(canonical(candidate)).hexdigest(),
        }
        collision = copy.deepcopy(record)
        collision["candidate"]["head_sha"] = "5" * 40
        collision["record_sha256"] = hashlib.sha256(
            canonical(collision["candidate"])
        ).hexdigest()

        with tempfile.TemporaryDirectory(prefix="awf-batch-audit-") as temporary:
            state_path = Path(temporary) / "controller/state.sqlite"

            def reopen():
                return ContinuousControllerStore(
                    state_path, ["A"], worktree_roots=[ROOT]
                )

            store = reopen()
            first = store.append_batch_audit(copy.deepcopy(record))
            self.assertEqual(store.batch_audit_records(), [record])
            # Restart before retry: idempotency and retention must be durable.
            store = reopen()
            self.assertEqual(store.append_batch_audit(copy.deepcopy(record)), first)
            self.assertEqual(store.batch_audit_records(), [record])
            with self.assertRaises(ValidationError):
                store.append_batch_audit(collision)
            self.assertEqual(reopen().batch_audit_records(), [record])

            # Discover the physical ledger row through the public connection,
            # without prescribing a table/column name for the resolved Q4.
            # A no-op UPDATE still attempts a rewrite and must be refused;
            # DELETE must also refuse, rather than relying on a read-only API.
            with store.connection() as db:
                ledger_rows = []
                tables = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                for table in tables:
                    quoted = '"' + table[0].replace('"', '""') + '"'
                    for row in db.execute(f"SELECT * FROM {quoted}"):
                        if any(candidate["batch_id"] in value
                               for value in row if isinstance(value, str)):
                            ledger_rows.append((quoted, dict(row)))
            self.assertEqual(len(ledger_rows), 1, "one durable record per batch")
            table, stored = ledger_rows[0]
            columns = ['"' + name.replace('"', '""') + '"' for name in stored]
            predicate = " AND ".join(f"{column} IS ?" for column in columns)
            parameters = tuple(stored.values())
            for statement in (
                f"UPDATE {table} SET {columns[0]}={columns[0]} WHERE {predicate}",
                f"DELETE FROM {table} WHERE {predicate}",
            ):
                with self.subTest(mutation=statement.split()[0]):
                    with self.assertRaises((sqlite3.IntegrityError, ValidationError)):
                        with store.transaction() as db:
                            db.execute(statement, parameters)
                    self.assertEqual(reopen().batch_audit_records(), [record])


if __name__ == "__main__":
    unittest.main()


class BatchAuditPersistenceTests(unittest.TestCase):
    """Bounded T8 persistence; no operational or AWF-91 placement claim."""

    def setUp(self):
        from agentic.continuous_controller import ContinuousControllerStore
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-t8-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "controller/state.sqlite"
        self.open = lambda: ContinuousControllerStore(
            self.path, ["A"], worktree_roots=[ROOT])
        self.store = self.open()

    @staticmethod
    def record(batch="batch-1", repository=42, head="2" * 40):
        from agentic.canonical import canonical
        candidate = {
            "repository_id": repository, "batch_id": batch,
            "base_sha": "1" * 40, "head_sha": head, "tree_sha": "3" * 40,
            "sources": [{"pr_number": 11, "head_sha": "4" * 40}],
        }
        return {"candidate": candidate,
                "record_sha256": hashlib.sha256(canonical(candidate)).hexdigest()}

    def test_invalid_records_never_insert(self):
        from agentic import ValidationError
        from agentic.canonical import canonical
        bad = [None, [], {}, {**self.record(), "extra": True}]
        for key, values in {
            "repository_id": [True, 0, -1, "42", None, 2 ** 53],
            "batch_id": ["", " ", " batch", "batch ", "a\nb", "x" * 257, None, 1],
            "base_sha": ["A" * 40, "1" * 39, None, 1],
            "head_sha": ["g" * 40, None], "tree_sha": ["", []],
            "sources": [[], None, [{}], [{"pr_number": True, "head_sha": "4" * 40}],
                        [{"pr_number": 0, "head_sha": "4" * 40}],
                        [{"pr_number": 1, "head_sha": None}],
                        [{"pr_number": 1, "head_sha": "4" * 40, "extra": 1}],
                        [{"pr_number": 11, "head_sha": "4" * 40}] * 2],
        }.items():
            for value in values:
                item = self.record()
                item["candidate"][key] = value
                try:
                    item["record_sha256"] = hashlib.sha256(canonical(item["candidate"])).hexdigest()
                except ValidationError:
                    pass
                bad.append(item)
        item = self.record()
        del item["candidate"]["tree_sha"]
        bad.append(item)
        item = self.record()
        item["candidate"]["extra"] = 1
        bad.append(item)
        for digest in [None, True, "A" * 64, "0" * 64, "a" * 63]:
            bad.append({**self.record(), "record_sha256": digest})
        for index, item in enumerate(bad):
            with self.subTest(case=index):
                with self.assertRaises(ValidationError):
                    self.store.append_batch_audit(item)
                self.assertEqual(self.store.batch_audit_records(), [])

    def test_identity_append_order_detachment_and_restart(self):
        from agentic import ValidationError
        records = [self.record("z"), self.record("a"), self.record("z", 43)]
        submitted = copy.deepcopy(records[0])
        result = self.store.append_batch_audit(submitted)
        submitted["candidate"]["sources"][0]["head_sha"] = "f" * 40
        result["candidate"]["head_sha"] = "e" * 40
        for record in records[1:]:
            self.store.append_batch_audit(record)
        reversed_keys = {key: records[0][key] for key in reversed(records[0])}
        self.assertEqual(self.open().append_batch_audit(reversed_keys), records[0])
        with self.assertRaises(ValidationError):
            self.store.append_batch_audit(self.record("z", head="5" * 40))
        returned = self.open().batch_audit_records()
        self.assertEqual(returned, records)
        returned[0]["candidate"]["sources"].clear()
        self.assertEqual(self.open().batch_audit_records(), records)

    def test_concurrent_identical_and_conflicting_append(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from agentic import ValidationError
        for identical in (True, False):
            with self.subTest(identical=identical):
                batch = "same" if identical else "conflict"
                records = [self.record(batch), self.record(batch, head=("2" if identical else "5") * 40)]
                stores = [self.open(), self.open()]
                barrier = Barrier(2)

                def append(index):
                    barrier.wait(timeout=10)
                    try:
                        return stores[index].append_batch_audit(records[index])
                    except ValidationError:
                        return "REFUSED"

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(append, range(2)))
                retained = [r for r in self.open().batch_audit_records()
                            if r["candidate"]["batch_id"] == batch]
                self.assertEqual(len(retained), 1)
                if identical:
                    self.assertEqual(results, records)
                else:
                    self.assertEqual(results.count("REFUSED"), 1)
                    self.assertIn(retained[0], records)
                    self.assertIn(retained[0], results)

    def test_sql_replacement_upsert_and_noop_mutations_refuse(self):
        record = self.record()
        self.store.append_batch_audit(record)
        with self.store.connection() as db:
            row = dict(db.execute("SELECT * FROM controller_batch_audit").fetchone())
        names = list(row)
        columns = ",".join(names)
        placeholders = ",".join("?" for _ in names)
        statements = [
            f"INSERT OR REPLACE INTO controller_batch_audit({columns}) VALUES({placeholders})",
            f"REPLACE INTO controller_batch_audit({columns}) VALUES({placeholders})",
            f"INSERT INTO controller_batch_audit({columns}) VALUES({placeholders}) "
            "ON CONFLICT(repository_id,batch_id) DO UPDATE SET record_json=excluded.record_json",
        ]
        for statement in statements:
            for recursive in (0, 1):
                with self.subTest(sql=statement, recursive=recursive):
                    with self.assertRaises(sqlite3.IntegrityError):
                        with self.store.transaction() as db:
                            db.execute(f"PRAGMA recursive_triggers={recursive}")
                            db.execute(statement, tuple(row.values()))
                    self.assertEqual(self.open().batch_audit_records(), [record])
        # Both the logical key and the append-position primary key resist REPLACE.
        for change in ({"sequence": row["sequence"] + 1},
                       {"repository_id": 99, "batch_id": "other"}):
            altered = {**row, **change}
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db:
                    db.execute(statements[0], tuple(altered.values()))
        for statement in ("UPDATE controller_batch_audit SET record_json=record_json",
                          "DELETE FROM controller_batch_audit"):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db:
                    db.execute(statement)
        self.assertEqual(self.open().batch_audit_records(), [record])

    def test_corrupt_stored_identity_digest_and_bytes_fail_closed(self):
        from agentic import ValidationError
        from agentic.canonical import canonical
        record = self.record()
        for column, corrupt in (
            ("repository_id", 99), ("batch_id", "other"),
            ("record_sha256", "0" * 64), ("record_json", "{}"),
            ("record_json", "not json"),
            ("record_json", canonical(record).decode() + " "),
            ("record_json", canonical(self.record(head="5" * 40)).decode()),
        ):
            with self.subTest(column=column, corrupt=corrupt):
                with self.store.connection() as db:
                    db.execute("DROP TABLE controller_batch_audit")
                self.store = self.open()
                self.store.append_batch_audit(record)
                with self.store.connection() as db:
                    triggers = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                                          "AND tbl_name='controller_batch_audit'").fetchall()
                    for trigger in triggers:
                        db.execute('DROP TRIGGER "' + trigger[0] + '"')
                    db.execute(f"UPDATE controller_batch_audit SET {column}=?", (corrupt,))
                with self.assertRaises(ValidationError):
                    self.store.batch_audit_records()
                with self.assertRaises(ValidationError):
                    self.store.append_batch_audit(record)

    def test_upgrade_and_failed_initialization_are_atomic(self):
        from unittest.mock import patch
        from agentic import continuous_controller as module, ValidationError
        # Model an existing controller database without the extension.
        with self.store.connection() as db:
            db.execute("DROP TABLE controller_batch_audit")
            db.execute("INSERT INTO controller_meta VALUES('fixture','retained')")
            before = list(db.iterdump())
        with patch.object(module, "BATCH_AUDIT_DDL", module.BATCH_AUDIT_DDL + ("INVALID SQL",)):
            with self.assertRaises(sqlite3.DatabaseError):
                self.open()
        with self.store.connection() as db:
            self.assertEqual(list(db.iterdump()), before)
        upgraded = self.open()
        upgraded.append_batch_audit(self.record())
        with upgraded.connection() as db:
            self.assertEqual(db.execute("SELECT value FROM controller_meta WHERE key='fixture'").fetchone()[0],
                             "retained")
            before = list(db.iterdump())
        # A late refusal also rolls back the extension on an older database.
        with upgraded.connection() as db:
            db.execute("DROP TABLE controller_batch_audit")
            before = list(db.iterdump())
        with self.assertRaises(ValidationError):
            module.ContinuousControllerStore(self.path, ["B"], worktree_roots=[ROOT])
        with upgraded.connection() as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertEqual(self.open().batch_audit_records(), [])
