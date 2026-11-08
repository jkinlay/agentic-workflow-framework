"""Collapsed weekly windows and isolated pending schedule refusals."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import ValidationError
from agentic.continuous_controller import ContinuousControllerStore
from agentic.operating_calendar import next_review_presentation
from agentic.review_batching import ReviewBatchStore


SUNDAY_GAP = {"review_batching": {
    "weekdays": [7], "presentation_time": "01:10", "cutoff_time": "01:20"}}


def candidate(number):
    return {"repository_id": 123, "pull_request": number, "head_sha": "a" * 40,
            "base_sha": "b" * 40, "head_tree_sha": "c" * 40,
            "final_gate_sha256": "d" * 64}


class CollapsedWindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.controller = ContinuousControllerStore(Path(tmp.name) / "state.db", ["C"])
        self.store = ReviewBatchStore(self.controller)

    @staticmethod
    def observer(now):
        def observe(value):
            return {"candidate": deepcopy(value), "observed_at": now,
                    "expires_at": (datetime.fromisoformat(now.replace("Z", "+00:00")) +
                                   timedelta(minutes=1)).isoformat(),
                    "open": True, "merged": False,
                    "readiness": "READY_FOR_OWNER_AUTHORIZATION", "final_gate_valid": True}
        return observe

    def test_collapsed_sunday_uses_following_weekly_recurrence(self):
        for ready in ("2026-03-23T08:00:00Z", "2026-03-29T00:00:00Z",
                      "2026-03-29T01:00:00Z"):
            with self.subTest(ready=ready):
                schedule = next_review_presentation(ready, SUNDAY_GAP)
                self.assertEqual(schedule["presentation_at"], "2026-04-05T00:10:00Z")
                self.assertTrue(schedule["held"])
                self.assertFalse(schedule["merge_authorized"])

    def test_changed_settings_claim_recovers_at_valid_opening_after_restart(self):
        identity = self.store.enqueue(candidate(1), "2026-03-23T08:00:00Z", {})
        early = "2026-03-29T01:00:00Z"
        self.assertIsNone(self.store.claim(early, SUNDAY_GAP, self.observer(early)))
        now = "2026-04-05T00:10:00Z"
        batch = ReviewBatchStore(self.controller).claim(now, SUNDAY_GAP, self.observer(now))
        self.assertEqual([item["intent_id"] for item in batch["members"]], [identity])
        member = batch["members"][0]
        self.assertEqual(member["ready_at"], "2026-03-23T08:00:00Z")
        self.assertEqual(member["scheduled_at"], now)
        self.assertEqual(member["initial_schedule"]["presentation_at"], member["ready_at"])
        self.assertFalse(batch["merge_authorized"])
        self.assertEqual(self.store.recheck(batch["batch_id"], now, SUNDAY_GAP,
                                           self.observer(now)), batch)
        self.assertIsNone(self.store.claim(now, SUNDAY_GAP, self.observer(now)))

    def test_truly_unresolvable_intent_does_not_block_due_intent_and_can_recover(self):
        disabled = {"timezone": "UTC", "review_batching": {"enabled": False}}
        stranded = self.store.enqueue(candidate(1), "9999-12-31T08:00:00Z", disabled)
        due = self.store.enqueue(candidate(2), "2026-03-23T08:00:00Z", {})
        now = "2026-04-05T00:10:00Z"
        batch = self.store.claim(now, SUNDAY_GAP, self.observer(now))
        self.assertEqual([item["intent_id"] for item in batch["members"]], [due])
        with self.controller.transaction() as db:
            row = db.execute("SELECT state,attempts FROM review_presentation_intent_v1 "
                             "WHERE intent_id=?", (stranded,)).fetchone()
            self.assertEqual(tuple(row), ("PENDING", 0))
        self.store.acknowledge({"batch_id": batch["batch_id"],
                                "payload_sha256": batch["payload_sha256"],
                                "status": "DELIVERED", "observed_at": now,
                                "provider_receipt_id": "delivered-due"})
        later = "9999-12-31T08:00:00Z"
        recovered = self.store.claim(later, disabled, self.observer(later))
        self.assertEqual([item["intent_id"] for item in recovered["members"]], [stranded])
        self.assertEqual(recovered["members"][0]["attempt"], 1)

    def test_refusal_when_no_weekly_recurrence_remains_representable(self):
        with self.assertRaisesRegex(ValidationError, "No valid review presentation window.*supported datetime range"):
            next_review_presentation("9999-12-31T08:00:00Z", SUNDAY_GAP)


if __name__ == "__main__":
    unittest.main()
