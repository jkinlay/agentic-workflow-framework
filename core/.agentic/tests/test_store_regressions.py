"""Real SQLite regression coverage for ticket CAS and lease release semantics."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agentic import ValidationError
from agentic.canonical import loads
from agentic.store import Store

NOW = "2026-09-13T10:00:00Z"
READY_FACTS = dict.fromkeys(["config_valid", "scope_verified", "ownership_claimed", "dependencies_satisfied", "contract_ready"], True)


class StoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.db", str(uuid.uuid4()))

    def snapshot(self):
        with self.store.connection() as db:
            return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
                    for table in ["tickets", "events", "leases"]}

    def lease(self, owner="writer", task=None):
        return self.store.acquire_lease(task or str(uuid.uuid4()), owner, 1, 0, 0, NOW, 60, [1, 0, 0])

    def test_advance_creates_ticket_and_appends_bound_state_revision_event(self):
        self.assertEqual(self.store.advance("EX-1", "TICKET_READY", 0, READY_FACTS), ("READY", 1))
        snapshot = self.snapshot()
        self.assertEqual(snapshot["tickets"], [{"issue_id": "EX-1", "state": "READY", "revision": 1}])
        self.assertEqual(loads(snapshot["events"][0]["payload"]), {
            "issue_id": "EX-1", "previous_state": "BACKLOG", "state": "READY", "revision": 1,
            "facts": READY_FACTS, "resume_state": None})
        self.assertEqual(self.store.verify_chain()["sequence"], 1)

    def test_stale_revision_and_invalid_guards_leave_state_and_audit_unchanged(self):
        self.store.advance("EX-1", "TICKET_READY", 0, READY_FACTS)
        before = self.snapshot()
        for revision, facts in [(0, {}), (1, {})]:
            with self.subTest(revision=revision), self.assertRaises(ValidationError):
                self.store.advance("EX-1", "DISPATCH_ACCEPTED", revision, facts)
            self.assertEqual(self.snapshot(), before)

    def test_competing_revision_updates_commit_exactly_one_winner(self):
        def attempt(_):
            try:
                return self.store.advance("EX-1", "TICKET_READY", 0, READY_FACTS)
            except ValidationError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(results.count(("READY", 1)), 1)
        self.assertEqual(results.count("Concurrent ticket revision changed"), 1)
        self.assertEqual(len(self.snapshot()["events"]), 1)
        self.store.verify_chain()

    def test_audit_failure_rolls_back_advance_state(self):
        before = self.snapshot()
        with patch.object(self.store, "_event", side_effect=RuntimeError("event write failed")):
            with self.assertRaisesRegex(RuntimeError, "event write failed"):
                self.store.advance("EX-1", "TICKET_READY", 0, READY_FACTS)
        self.assertEqual(self.snapshot(), before)

    def test_release_frees_capacity_and_old_fence_stays_invalid(self):
        self.store.control("RUNNING", "fixture reconciliation", reconciled=True)
        first = self.lease()
        with self.assertRaisesRegex(ValidationError, "capacity"):
            self.lease(owner="next-writer")
        self.store.release_lease(first["lease_id"], "writer")
        with self.assertRaisesRegex(ValidationError, "expired, stale"):
            self.store.check_lease(first["lease_id"], first["fencing_token"], "writer", NOW)
        second = self.lease(owner="next-writer")
        self.assertGreater(second["fencing_token"], first["fencing_token"])
        self.assertTrue(self.store.check_lease(second["lease_id"], second["fencing_token"], "next-writer", NOW))
        self.store.verify_chain()

    def test_wrong_owner_unknown_lease_and_repeated_release_are_nonmutating(self):
        self.store.control("RUNNING", "fixture reconciliation", reconciled=True)
        lease = self.lease()
        before = self.snapshot()
        for lease_id, owner in [(lease["lease_id"], "other"), (str(uuid.uuid4()), "writer")]:
            with self.assertRaisesRegex(ValidationError, "Unknown lease or wrong owner"):
                self.store.release_lease(lease_id, owner)
            self.assertEqual(self.snapshot(), before)
        self.store.release_lease(lease["lease_id"], "writer")
        released = self.snapshot()
        self.store.release_lease(lease["lease_id"], "writer")
        self.assertEqual(self.snapshot(), released)
        self.assertEqual(sum(event["event_type"] == "LEASE_RELEASED" for event in released["events"]), 1)

    def test_release_of_revoked_lease_does_not_restore_or_reclassify_it(self):
        self.store.control("RUNNING", "fixture reconciliation", reconciled=True)
        lease = self.lease()
        self.store.control("PAUSED", "operator pause")
        before = self.snapshot()
        self.store.release_lease(lease["lease_id"], "writer")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(before["leases"][0]["status"], "REVOKED")

    def test_audit_failure_rolls_back_release_and_keeps_capacity_reserved(self):
        self.store.control("RUNNING", "fixture reconciliation", reconciled=True)
        lease = self.lease()
        before = self.snapshot()
        with patch.object(self.store, "_event", side_effect=RuntimeError("event write failed")):
            with self.assertRaises(RuntimeError):
                self.store.release_lease(lease["lease_id"], "writer")
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.store.check_lease(lease["lease_id"], lease["fencing_token"], "writer", NOW))


if __name__ == "__main__":
    unittest.main()
