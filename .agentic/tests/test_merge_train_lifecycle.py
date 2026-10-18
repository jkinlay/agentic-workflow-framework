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
