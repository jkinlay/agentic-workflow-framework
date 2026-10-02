import tempfile
import threading
import unittest
from pathlib import Path

from agentic import ValidationError
from agentic.contracts import Contracts
from agentic.review_completion import ReviewCompletionStore

ROOT = Path(__file__).resolve().parents[2]

def candidate(head="2" * 40, tree="3" * 40):
    return {
        "repository": "example/project",
        "base_sha": "1" * 40,
        "head_sha": head,
        "head_tree_sha": tree,
        "contract_sha256": "4" * 64,
        "review_input_sha256": "5" * 64,
    }


class ReviewCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-review-completion-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "review.sqlite3"
        self.store = ReviewCompletionStore(self.path)
        self.reviewers = ["reviewer-c", "reviewer-a", "reviewer-b"]
        self.current = candidate()

    def freeze_and_dispatch(self):
        frozen = self.store.freeze(self.current, self.reviewers)
        self.assertEqual(frozen["required_reviewers"], sorted(self.reviewers))
        return {reviewer: self.store.dispatch(reviewer, self.current, self.reviewers)
                for reviewer in self.reviewers}

    def acceptable(self, reviewer, binding):
        return self.store.record_result(reviewer, binding, "ACCEPTABLE",
                                        {"verdict": "APPROVE", "reviewer": reviewer})

    def test_incomplete_set_cannot_mutate_provider_and_reports_all_counts(self):
        bindings = self.freeze_and_dispatch()
        self.acceptable("reviewer-a", bindings["reviewer-a"])
        calls = []
        with self.assertRaisesRegex(ValidationError, "Every frozen independent reviewer"):
            self.store.submit(self.current, self.reviewers, {"verdict": "APPROVE"}, calls.append)
        self.assertEqual(calls, [])
        counts = self.store.status(self.current, self.reviewers)["counts"]
        self.assertEqual(counts, {"required": 3, "completed": 1, "acceptable": 1,
                                  "failed": 0, "stale": 0, "outstanding": 2})

    def test_result_cannot_precede_persisted_dispatch(self):
        frozen = self.store.freeze(self.current, ["one"])
        forged = {"cycle_id": frozen["cycle_id"], "reviewer_id": "one",
                  "tuple_sha256": frozen["tuple_sha256"],
                  "reviewer_set_sha256": frozen["reviewer_set_sha256"]}
        with self.assertRaisesRegex(ValidationError, "persisted dispatch"):
            self.store.record_result("one", forged, "ACCEPTABLE", {"verdict": "APPROVE"})

    def test_failed_timeout_malformed_and_tuple_mismatch_never_complete(self):
        reviewers = ["failed", "timeout", "malformed", "stale"]
        self.store.freeze(self.current, reviewers)
        bindings = {name: self.store.dispatch(name, self.current, reviewers) for name in reviewers}
        self.store.record_result("failed", bindings["failed"], "FAILED", {"error": "review failed"})
        self.store.record_result("timeout", bindings["timeout"], "TIMED_OUT", {"seconds": 30})
        self.store.record_result("malformed", bindings["malformed"], "MALFORMED", {"reason": "invalid JSON envelope"})
        stale = dict(bindings["stale"])
        stale["tuple_sha256"] = "0" * 64
        self.store.record_result("stale", stale, "ACCEPTABLE", {"verdict": "APPROVE"})
        summary = self.store.status(self.current, reviewers)
        self.assertFalse(summary["ready"])
        self.assertEqual(summary["counts"], {"required": 4, "completed": 4, "acceptable": 0,
                                             "failed": 3, "stale": 1, "outstanding": 0})

    def test_duplicate_terminal_result_invalidates_acceptance_without_replacing_first(self):
        reviewers = ["one"]
        self.store.freeze(self.current, reviewers)
        binding = self.store.dispatch("one", self.current, reviewers)
        self.store.record_result("one", binding, "ACCEPTABLE", {"verdict": "APPROVE", "sequence": 1})
        duplicate = self.store.record_result("one", binding, "ACCEPTABLE", {"verdict": "APPROVE", "sequence": 2})
        self.assertEqual(duplicate["reason"], "DUPLICATE")
        self.assertEqual(duplicate["summary"]["reviewers"], [{"reviewer_id": "one", "state": "DUPLICATE"}])
        self.assertEqual(duplicate["summary"]["counts"], {"required": 1, "completed": 1,
                                                            "acceptable": 0, "failed": 1,
                                                            "stale": 0, "outstanding": 0})
        with self.assertRaises(ValidationError):
            self.store.prepare_submission(self.current, reviewers, {"verdict": "APPROVE"})

    def test_candidate_or_reviewer_set_movement_invalidates_and_needs_fresh_cycle(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        moved = candidate(head="6" * 40, tree="7" * 40)
        status = self.store.status(moved, self.reviewers)
        self.assertEqual(status["state"], "INVALIDATED")
        self.assertFalse(status["current"])
        with self.assertRaisesRegex(ValidationError, "moved"):
            self.store.prepare_submission(moved, self.reviewers, {"verdict": "APPROVE"})
        fresh = self.store.freeze(moved, [*self.reviewers, "reviewer-d"])
        self.assertNotEqual(fresh["cycle_id"], status["cycle_id"])
        self.assertEqual(fresh["counts"]["outstanding"], 4)

    def test_restart_preserves_frozen_set_and_each_result_state(self):
        bindings = self.freeze_and_dispatch()
        self.acceptable("reviewer-a", bindings["reviewer-a"])
        self.store.record_result("reviewer-b", bindings["reviewer-b"], "FAILED", {"error": "finding"})
        restarted = ReviewCompletionStore(self.path)
        status = restarted.recover()
        self.assertEqual(status["required_reviewers"], sorted(self.reviewers))
        self.assertEqual(status["counts"], {"required": 3, "completed": 2, "acceptable": 1,
                                            "failed": 1, "stale": 0, "outstanding": 1})
        self.assertEqual({item["reviewer_id"]: item["state"] for item in status["reviewers"]},
                         {"reviewer-a": "ACCEPTABLE", "reviewer-b": "FAILED", "reviewer-c": "RUNNING"})

    def test_restart_during_provider_submission_is_uncertain_and_never_replayed(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        admission = self.store.prepare_submission(self.current, self.reviewers, {"verdict": "APPROVE"})
        restarted = ReviewCompletionStore(self.path)
        self.assertEqual(restarted.recover()["state"], "SUBMISSION_UNKNOWN")
        self.assertEqual(restarted.submitted()["aggregate"], {"verdict": "APPROVE"})
        with self.assertRaises(ValidationError):
            restarted.complete_submission(admission, {"provider": "late"})

    def test_late_concurrent_result_cannot_mutate_submitted_verdict(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        entered = threading.Event()
        release = threading.Event()
        outcome = {}

        def provider(admission):
            outcome["admission"] = admission
            entered.set()
            self.assertTrue(release.wait(5))
            return {"provider_status": "submitted", "verdict": admission["aggregate"]["verdict"]}

        def submit():
            outcome["summary"] = self.store.submit(
                self.current, self.reviewers, {"verdict": "APPROVE", "finding_ids": []}, provider)

        thread = threading.Thread(target=submit)
        thread.start()
        self.assertTrue(entered.wait(5))
        late = self.store.record_result("reviewer-a", bindings["reviewer-a"], "FAILED", {"error": "late"})
        self.assertEqual(late["reason"], "LATE")
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome["summary"]["state"], "SUBMITTED")
        self.assertEqual(self.store.submitted()["aggregate"], {"verdict": "APPROVE", "finding_ids": []})

    def test_two_concurrent_submitters_admit_only_one_provider_mutation(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def provider(admission):
            calls.append(admission["submission_id"])
            entered.set()
            self.assertTrue(release.wait(5))
            return {"ok": True}

        first = threading.Thread(target=lambda: self.store.submit(
            self.current, self.reviewers, {"verdict": "APPROVE"}, provider))
        first.start()
        self.assertTrue(entered.wait(5))
        with self.assertRaisesRegex(ValidationError, "already prepared"):
            self.store.submit(self.current, self.reviewers, {"verdict": "APPROVE"}, provider)
        release.set()
        first.join(5)
        self.assertEqual(len(calls), 1)

    def test_generated_status_and_submission_contracts_accept_runtime_records(self):
        reviewers = ["one"]
        self.store.freeze(self.current, reviewers)
        binding = self.store.dispatch("one", self.current, reviewers)
        self.acceptable("one", binding)
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("review-completion", self.store.status(self.current, reviewers))
        admission = self.store.prepare_submission(self.current, reviewers, {"verdict": "APPROVE"})
        contracts.validate("review-submission", admission)


if __name__ == "__main__":
    unittest.main()
