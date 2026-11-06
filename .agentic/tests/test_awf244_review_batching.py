"""Protected review intent/outbox behavior; no provider sends in these tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import ValidationError
from agentic.continuous_controller import ContinuousControllerStore
from agentic.review_batching import ReviewBatchStore


def candidate(number=1):
    return {"repository_id": 123, "pull_request": number, "head_sha": "a" * 40,
            "base_sha": "b" * 40, "head_tree_sha": "c" * 40,
            "final_gate_sha256": "d" * 64}


class BatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.controller = ContinuousControllerStore(Path(self.tmp.name) / "state.db", ["C"])
        self.store = ReviewBatchStore(self.controller)
        self.ready = "2026-11-06T18:00:00Z"
        self.now = "2026-11-09T08:00:00Z"

    def observe(self, value):
        return {"candidate": deepcopy(value), "observed_at": self.now,
                "expires_at": "2026-11-09T08:01:00Z", "open": True, "merged": False,
                "readiness": "READY_FOR_OWNER_AUTHORIZATION", "final_gate_valid": True}

    def enqueue(self, value=None):
        return self.store.enqueue(value or candidate(), self.ready, {})

    def test_repeat_restart_and_receipt_dedup(self):
        first = self.enqueue()
        self.assertEqual(first, self.enqueue())
        self.assertIsNone(self.store.claim("2026-11-07T10:00:00Z", {}, self.observe))
        batch = ReviewBatchStore(self.controller).claim(self.now, {}, self.observe)
        self.assertTrue(batch["catch_up"])
        self.assertEqual(batch["members"][0]["ready_at"], self.ready)
        self.assertEqual(batch["members"][0]["scheduled_at"], "2026-11-09T07:00:00Z")
        self.assertFalse(batch["merge_authorized"])
        self.assertIsNone(ReviewBatchStore(self.controller).claim(self.now, {}, self.observe))
        receipt = {"batch_id": batch["batch_id"], "payload_sha256": batch["payload_sha256"],
                   "status": "DELIVERED", "observed_at": self.now, "provider_receipt_id": "receipt-1"}
        self.store.acknowledge(receipt)
        self.store.acknowledge(receipt)
        self.enqueue()
        self.assertIsNone(self.store.claim(self.now, {}, self.observe))

    def test_concurrent_claim_single_winner(self):
        self.enqueue()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.claim(self.now, {}, self.observe), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_stale_closed_merged_unready_expired_and_changed(self):
        for number, change in enumerate([{"open": False}, {"merged": True}, {"readiness": "FINAL_REVIEW"},
                       {"final_gate_valid": False}, {"expires_at": self.now},
                       {"candidate": {**candidate(), "head_sha": "e" * 40}},
                       {"observed_at": "2026-11-09T07:59:59Z"}], 10):
            with self.subTest(change=change):
                # A fresh identity permits each failure to be tested independently.
                value = candidate(number)
                self.enqueue(value)
                def observer(item):
                    return {**self.observe(item), **change}
                self.assertIsNone(self.store.claim(self.now, {}, observer))
        # Stale evidence still withholds delivery, but only fresh invalidation
        # may withdraw the remaining pending intents.
        with self.controller.connection() as db:
            pending = [row[0] for row in db.execute(
                "SELECT candidate_json FROM review_presentation_intent_v1 WHERE state='PENDING'")]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(self.store.claim(self.now, {}, lambda item:
                          {**self.observe(item), "open": False}))
        self.assertIsNone(self.store.claim(self.now, {}, self.observe))

    def test_missing_observer_and_exception_do_not_claim(self):
        self.enqueue()
        with self.assertRaises(ValidationError):
            self.store.claim(self.now, {}, None)
        def broken(value):
            raise RuntimeError("provider unavailable")
        with self.assertRaises(RuntimeError):
            self.store.claim(self.now, {}, broken)
        self.assertIsNotNone(self.store.claim(self.now, {}, self.observe))

    def test_config_change_recomputes_pending(self):
        self.enqueue()
        now = "2026-11-07T10:00:00Z"
        self.now = now
        original = self.observe
        def observe(value):
            return {**original(value), "expires_at": "2026-11-07T10:01:00Z"}
        self.assertIsNotNone(self.store.claim(now, {"review_batching": {"enabled": False}}, observe))

    def test_changed_identity_withdraws_prior_head(self):
        self.enqueue()
        self.enqueue({**candidate(), "head_sha": "e" * 40})
        batch = self.store.claim(self.now, {}, self.observe)
        self.assertEqual(len(batch["members"]), 1)
        self.assertEqual(batch["members"][0]["candidate"]["head_sha"], "e" * 40)

    def test_fresh_restoration_supersedes_pending_replacement_without_resetting_schedule(self):
        original = self.enqueue()
        replacement = self.enqueue({**candidate(), "head_sha": "e" * 40})
        self.assertIsNone(self.enqueue())
        invalid = {**self.observe(candidate()), "expires_at": self.now}
        self.assertIsNone(self.store.enqueue_observed(candidate(), invalid, self.now, {}, repository_id='123'))
        restored = self.store.enqueue_observed(candidate(), self.observe(candidate()), self.now, {}, repository_id='123')
        self.assertEqual(restored, original)
        with self.controller.connection() as db:
            states = dict(db.execute('SELECT intent_id,state FROM review_presentation_intent_v1'))
        self.assertEqual(states, {original: 'PENDING', replacement: 'WITHDRAWN'})
        batch = self.store.claim(self.now, {}, self.observe)
        self.assertEqual(len(batch['members']), 1)
        self.assertEqual(batch['members'][0]['intent_id'], original)
        self.assertEqual(batch['members'][0]['ready_at'], self.ready)
        self.assertEqual(batch['members'][0]['attempt'], 1)
        self.assertTrue(batch['catch_up'])
        self.assertIsNone(self.store.enqueue_observed(candidate(), self.observe(candidate()), self.now, {}, repository_id='123'))

    def test_restored_confirmed_unsent_intent_keeps_delivery_retry_limit(self):
        self.enqueue()
        first = self.store.claim(self.now, {}, self.observe)
        self.store.reconcile(self.receipt(first, 'NOT_DELIVERED', 'negative-1'))
        self.enqueue({**candidate(), 'base_sha': 'e' * 40})
        self.store.enqueue_observed(candidate(), self.observe(candidate()), self.now, {}, repository_id='123')
        second = self.store.claim(self.now, {}, self.observe)
        self.assertEqual(second['members'][0]['attempt'], 2)
        self.store.reconcile(self.receipt(second, 'NOT_DELIVERED', 'negative-2'))
        self.assertIsNone(self.store.enqueue_observed(candidate(), self.observe(candidate()), self.now, {}, repository_id='123'))
        self.assertIsNone(self.store.claim(self.now, {}, self.observe))

    def test_unknown_send_needs_reconciliation_and_no_blind_retry(self):
        self.enqueue()
        batch = self.store.claim(self.now, {}, self.observe)
        self.assertIsNone(self.store.claim(self.now, {}, self.observe))
        with self.assertRaises(ValidationError):
            self.store.acknowledge({"batch_id": batch["batch_id"], "status": "DELIVERED"})
        # No retry/release method: readback can acknowledge the existing identity.
        self.assertEqual(self.store.pending_batches()[0]["batch_id"], batch["batch_id"])

    def test_does_not_touch_dispatch_or_status_outbox(self):
        self.enqueue()
        self.store.claim(self.now, {}, self.observe)
        with self.controller.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM controller_outbox").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM controller_dispatch").fetchone()[0], 0)

    def receipt(self, batch, status="DELIVERED", provider_id="receipt-1"):
        return {"batch_id": batch["batch_id"], "payload_sha256": batch["payload_sha256"],
                "status": status, "observed_at": self.now, "provider_receipt_id": provider_id}

    def test_provider_receipt_cannot_acknowledge_two_batches(self):
        self.enqueue()
        first = self.store.claim(self.now, {}, self.observe)
        self.store.acknowledge(self.receipt(first))
        self.enqueue(candidate(2))
        second = self.store.claim(self.now, {}, self.observe)
        with self.assertRaisesRegex(ValidationError, "receipt.*reused"):
            self.store.acknowledge(self.receipt(second))
        self.assertEqual(len(self.store.pending_batches()), 1)

    def test_recheck_retains_fresh_observation_and_refuses_drift(self):
        self.enqueue()
        batch = self.store.claim(self.now, {}, self.observe)
        self.assertEqual(self.store.recheck(batch["batch_id"], self.now, {}, self.observe), batch)
        with self.controller.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM review_presentation_check_v1").fetchone()[0], 1)
        for observer in (None, lambda value: {**self.observe(value), "open": False}):
            with self.assertRaises(ValidationError):
                self.store.recheck(batch["batch_id"], self.now, {}, observer)
        with self.assertRaises(ValidationError):
            self.store.recheck(batch["batch_id"], self.now, {"eod": {"time": "18:00"}}, self.observe)

    def test_late_tick_after_cutoff_waits_until_window(self):
        self.enqueue()
        self.assertIsNone(self.store.claim("2026-11-09T17:00:00Z", {}, self.observe))
        self.assertIsNotNone(self.store.claim(self.now, {}, self.observe))

    def test_confirmed_non_delivery_allows_only_one_retry(self):
        self.enqueue()
        first = self.store.claim(self.now, {}, self.observe)
        self.store.reconcile(self.receipt(first, "NOT_DELIVERED", "negative-1"))
        second = self.store.claim(self.now, {}, self.observe)
        self.assertNotEqual(first["batch_id"], second["batch_id"])
        self.assertEqual(second["members"][0]["attempt"], 2)
        self.store.reconcile(self.receipt(second, "NOT_DELIVERED", "negative-2"))
        self.assertIsNone(self.store.claim(self.now, {}, self.observe))
        with self.assertRaises(ValidationError):
            self.store.acknowledge(self.receipt(second, provider_id="late-contradiction"))

    def test_newer_pending_head_survives_old_negative_readback(self):
        self.enqueue()
        old = self.store.claim(self.now, {}, self.observe)
        self.enqueue({**candidate(), "head_sha": "e" * 40})
        self.store.reconcile(self.receipt(old, "NOT_DELIVERED", "negative-1"))
        batch = self.store.claim(self.now, {}, self.observe)
        self.assertEqual(len(batch["members"]), 1)
        self.assertEqual(batch["members"][0]["candidate"]["head_sha"], "e" * 40)

    def test_multi_member_batch_and_atomic_observer_failure(self):
        self.enqueue(candidate(1))
        self.enqueue(candidate(2))
        calls = []
        def observer(value):
            calls.append(value)
            if len(calls) == 2:
                raise RuntimeError("readback unavailable")
            return {**self.observe(value), "open": False}
        with self.assertRaises(RuntimeError):
            self.store.claim(self.now, {}, observer)
        batch = self.store.claim(self.now, {}, self.observe)
        self.assertEqual(len(batch["members"]), 2)


if __name__ == "__main__":
    unittest.main()
