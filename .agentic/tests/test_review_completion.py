import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from agentic import ValidationError
from agentic.authorization import make_request
from agentic.canonical import fingerprint, load
from agentic.contracts import Contracts
from agentic.cli import local_semantics
from agentic.gates import evaluate
from agentic.interaction import gate_handoff
from agentic.lifecycle import definition, transition
from agentic.review_completion import ReviewCompletionStore
from agentic.store import Store

ROOT = Path(__file__).resolve().parents[2]

def candidate(head="2" * 40, tree="3" * 40):
    return {
        "repository": "example/project",
        "base_sha": "1" * 40,
        "head_sha": head,
        "head_tree_sha": tree,
        "contract_sha256": "4" * 64,
        "review_input_sha256": fingerprint("review-input", {"verdict": "APPROVE"}),
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
                                        {"verdict": "APPROVE", "reviewer": reviewer,
                                         "findings": []})

    @staticmethod
    def receipt(admission):
        return {"status": "SUBMITTED", **admission["provider_preconditions"],
                "observed_at": admission["provider_preconditions"]["prepared_at"]}

    def test_incomplete_set_cannot_mutate_provider_and_reports_all_counts(self):
        bindings = self.freeze_and_dispatch()
        self.acceptable("reviewer-a", bindings["reviewer-a"])
        calls = []
        with self.assertRaisesRegex(ValidationError, "Every frozen independent reviewer"):
            self.store.submit(self.current, self.reviewers, {"verdict": "APPROVE"}, calls.append,
                              lambda admission, intent: self.receipt(admission))
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
            self.store.record_result("one", forged, "ACCEPTABLE",
                                     {"verdict": "APPROVE", "reviewer": "one", "findings": []})

    def test_failed_timeout_malformed_and_tuple_mismatch_never_complete(self):
        reviewers = ["failed", "timeout", "malformed", "stale"]
        self.store.freeze(self.current, reviewers)
        bindings = {name: self.store.dispatch(name, self.current, reviewers) for name in reviewers}
        self.store.record_result("failed", bindings["failed"], "FAILED", {"error": "review failed"})
        self.store.record_result("timeout", bindings["timeout"], "TIMED_OUT", {"seconds": 30})
        self.store.record_result("malformed", bindings["malformed"], "MALFORMED", {"reason": "invalid JSON envelope"})
        stale = dict(bindings["stale"])
        stale["tuple_sha256"] = "0" * 64
        self.store.record_result("stale", stale, "ACCEPTABLE",
                                 {"verdict": "APPROVE", "reviewer": "stale", "findings": []})
        summary = self.store.status(self.current, reviewers)
        self.assertFalse(summary["ready"])
        self.assertEqual(summary["counts"], {"required": 4, "completed": 4, "acceptable": 0,
                                             "failed": 3, "stale": 1, "outstanding": 0})

    def test_duplicate_terminal_result_invalidates_acceptance_without_replacing_first(self):
        reviewers = ["one"]
        self.store.freeze(self.current, reviewers)
        binding = self.store.dispatch("one", self.current, reviewers)
        self.store.record_result("one", binding, "ACCEPTABLE",
                                 {"verdict": "APPROVE", "reviewer": "one", "findings": [], "sequence": 1})
        duplicate = self.store.record_result("one", binding, "ACCEPTABLE",
                                             {"verdict": "APPROVE", "reviewer": "one", "findings": [], "sequence": 2})
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
                self.current, self.reviewers, {"verdict": "APPROVE"}, provider,
                lambda admission, intent: self.receipt(admission))

        thread = threading.Thread(target=submit)
        thread.start()
        self.assertTrue(entered.wait(5))
        late = self.store.record_result("reviewer-a", bindings["reviewer-a"], "FAILED", {"error": "late"})
        self.assertEqual(late["reason"], "LATE")
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome["summary"]["state"], "SUBMITTED")
        self.assertEqual(self.store.submitted()["aggregate"], {"verdict": "APPROVE"})

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
            self.current, self.reviewers, {"verdict": "APPROVE"}, provider,
            lambda admission, intent: self.receipt(admission)))
        first.start()
        self.assertTrue(entered.wait(5))
        with self.assertRaisesRegex(ValidationError, "already prepared"):
            self.store.submit(self.current, self.reviewers, {"verdict": "APPROVE"}, provider,
                              lambda admission, intent: self.receipt(admission))
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
        with self.assertRaisesRegex(ValidationError, "review-input digest"):
            self.store.prepare_submission(self.current, reviewers, {"verdict": "REQUEST_CHANGES"})
        admission = self.store.prepare_submission(self.current, reviewers, {"verdict": "APPROVE"})
        contracts.validate("review-submission", admission)
        local_semantics("review-submission", admission)
        forged = copy.deepcopy(admission)
        forged["completion_snapshot"]["counts"]["acceptable"] = 0
        with self.assertRaisesRegex(ValidationError, "counts"):
            local_semantics("review-submission", forged)

    def test_transport_cannot_assert_acceptable_against_result(self):
        for result, message in [
            ({"verdict": "REQUEST_CHANGES", "reviewer": "one", "findings": []}, "APPROVE"),
            ({"verdict": "APPROVE", "reviewer": "other", "findings": []}, "identity"),
            ({"verdict": "APPROVE", "reviewer": "one",
              "findings": [{"id": "F1", "status": "OPEN"}]}, "unresolved"),
        ]:
            with self.subTest(result=result):
                path = Path(self.temporary.name) / (str(len(message)) + message + ".sqlite3")
                store = ReviewCompletionStore(path)
                store.freeze(self.current, ["one"])
                binding = store.dispatch("one", self.current, ["one"])
                with self.assertRaisesRegex(ValidationError, message):
                    store.record_result("one", binding, "ACCEPTABLE", result)

    def test_provider_receipt_must_be_fresh_observation_bound_to_exact_tuple(self):
        self.store.freeze(self.current, ["one"])
        dispatch = self.store.dispatch("one", self.current, ["one"])
        self.acceptable("one", dispatch)
        admission = self.store.prepare_submission(self.current, ["one"], {"verdict": "APPROVE"})
        forged = self.receipt(admission)
        forged["head_sha"] = "9" * 40
        with self.assertRaisesRegex(ValidationError, "head_sha"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["operation_id"] = str(uuid.UUID(int=9))
        with self.assertRaisesRegex(ValidationError, "operation_id"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["completion_snapshot_sha256"] = "8" * 64
        with self.assertRaisesRegex(ValidationError, "completion_snapshot_sha256"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["aggregate_sha256"] = "7" * 64
        with self.assertRaisesRegex(ValidationError, "aggregate_sha256"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["observed_at"] = "1970-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValidationError, "predates"):
            self.store.complete_submission(admission, forged)
        self.store.mark_submission_unknown(admission["submission_id"], "provider observation mismatch")
        self.assertEqual(self.store.reconcile_submission(admission, self.receipt(admission))["state"],
                         "SUBMITTED")

    def test_altered_caller_admission_cannot_redirect_complete_or_reconcile(self):
        self.store.freeze(self.current, ["one"])
        dispatch = self.store.dispatch("one", self.current, ["one"])
        self.acceptable("one", dispatch)
        admission = self.store.prepare_submission(self.current, ["one"], {"verdict": "APPROVE"})

        def changed_identity(value):
            value["submission_id"] = str(uuid.UUID(int=44))
            value["provider_preconditions"]["operation_id"] = value["submission_id"]

        def changed_time(value):
            value["provider_preconditions"]["prepared_at"] = "2030-01-01T00:00:00Z"

        def changed_tuple(value):
            snapshot = value["completion_snapshot"]
            snapshot["tuple"]["repository"] = "other/project"
            snapshot["tuple_sha256"] = fingerprint("review-tuple", snapshot["tuple"])
            value["provider_preconditions"]["repository"] = "other/project"
            value["provider_preconditions"]["tuple_sha256"] = snapshot["tuple_sha256"]
            value["completion_snapshot_sha256"] = fingerprint("review-completion", snapshot)
            value["provider_preconditions"]["completion_snapshot_sha256"] = value["completion_snapshot_sha256"]

        def changed_reviewers(value):
            snapshot = value["completion_snapshot"]
            snapshot["required_reviewers"].append("reviewer-z")
            snapshot["results"].append({"reviewer_id": "reviewer-z", "state": "ACCEPTABLE",
                "result_sha256": "9" * 64, "terminal_at": snapshot["results"][0]["terminal_at"]})
            snapshot["counts"].update(required=2, completed=2, acceptable=2)
            snapshot["reviewer_set_sha256"] = fingerprint("reviewer-set", snapshot["required_reviewers"])
            value["provider_preconditions"]["reviewer_set_sha256"] = snapshot["reviewer_set_sha256"]
            value["completion_snapshot_sha256"] = fingerprint("review-completion", snapshot)
            value["provider_preconditions"]["completion_snapshot_sha256"] = value["completion_snapshot_sha256"]

        def changed_aggregate(value):
            value["aggregate"] = {"verdict": "APPROVE", "redirected": True}
            value["aggregate_sha256"] = fingerprint("review-aggregate", value["aggregate"])
            value["provider_preconditions"]["aggregate_sha256"] = value["aggregate_sha256"]

        mutations = (changed_identity, changed_time, changed_tuple, changed_reviewers, changed_aggregate)
        for mutation in mutations:
            with self.subTest(route="complete", mutation=mutation.__name__):
                forged = copy.deepcopy(admission)
                mutation(forged)
                from agentic.review_completion import validate_submission_semantics
                validate_submission_semantics(forged)
                with self.assertRaisesRegex(ValidationError, "immutable durable admission"):
                    self.store.complete_submission(forged, self.receipt(forged))
        self.store.mark_submission_unknown(admission["submission_id"], "provider completion unavailable")
        for mutation in mutations:
            with self.subTest(route="reconcile", mutation=mutation.__name__):
                forged = copy.deepcopy(admission)
                mutation(forged)
                with self.assertRaisesRegex(ValidationError, "immutable durable admission"):
                    self.store.reconcile_submission(forged, self.receipt(forged))
        self.assertEqual(self.store.reconcile_submission(admission, self.receipt(admission))["state"],
                         "SUBMITTED")

    def test_production_workflow_cli_exposes_review_completion_barrier(self):
        root = Path(self.temporary.name)
        worktree = root / "candidate"
        worktree.mkdir()
        state_path = root / "review.sqlite3"
        candidate_path, reviewers_path = root / "candidate.json", root / "reviewers.json"
        candidate_path.write_text(json.dumps(self.current), encoding="utf-8")
        reviewers_path.write_text(json.dumps(["one"]), encoding="utf-8")
        prefix = [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                  "--root", str(ROOT), "review-completion", "--state", str(state_path),
                  "--worktree-root", str(worktree)]
        command = [*prefix, "freeze", "--candidate", str(candidate_path),
                   "--reviewers", str(reviewers_path)]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["counts"]["outstanding"], 1)

        cli_store = ReviewCompletionStore(state_path, worktree_roots=[worktree])
        binding = cli_store.dispatch("one", self.current, ["one"])
        cli_store.record_result("one", binding, "ACCEPTABLE",
                                {"verdict": "APPROVE", "reviewer": "one", "findings": []})
        admission = cli_store.prepare_submission(self.current, ["one"], {"verdict": "APPROVE"})
        forged = copy.deepcopy(admission)
        forged["provider_preconditions"]["repository"] = "other/project"
        admission_path, receipt_path = root / "forged-admission.json", root / "forged-receipt.json"
        admission_path.write_text(json.dumps(forged), encoding="utf-8")
        receipt_path.write_text(json.dumps(self.receipt(forged)), encoding="utf-8")
        completed = subprocess.run([*prefix, "complete", "--admission", str(admission_path),
                                    "--receipt", str(receipt_path)],
                                   cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("repository", completed.stderr)
        self.assertEqual(cli_store.submitted()["state"], "SUBMITTING")
        cli_store.mark_submission_unknown(admission["submission_id"], "CLI completion rejected")
        completed = subprocess.run([*prefix, "reconcile", "--admission", str(admission_path),
                                    "--receipt", str(receipt_path)],
                                   cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("repository", completed.stderr)
        self.assertEqual(cli_store.submitted()["state"], "SUBMISSION_UNKNOWN")

    def test_review_state_is_rejected_inside_worktree_or_through_hardlink(self):
        root = Path(self.temporary.name)
        worktree = root / "reviewer-worktree"
        worktree.mkdir()
        with self.assertRaisesRegex(ValidationError, "outside"):
            ReviewCompletionStore(worktree / "state.sqlite3", worktree_roots=[worktree])
        linked = root / "linked.sqlite3"
        os.link(self.path, linked)
        with self.assertRaisesRegex(ValidationError, "hardlinks"):
            ReviewCompletionStore(linked)

    def test_old_gate_authorization_interaction_and_lifecycle_routes_cannot_bypass_barrier(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        bundle = load(ROOT / ".agentic/examples/evidence-bundle.json")
        gate = evaluate(config, definition(), bundle, contracts, "2026-09-09T12:00:00Z")
        request = make_request(gate, contracts, "2026-09-09T12:00:00Z")
        gate_handoff(gate, request, config, contracts, "2026-09-09T12:00:00Z")
        facts = {"derived_gate_ready": True, "requirements_current": True,
                 "final_gate_current": True, "final_gate": gate}
        self.assertEqual(transition("FINAL_REVIEW", "FINAL_GATE_PASSED", facts),
                         "READY_FOR_OWNER_AUTHORIZATION")

        missing = copy.deepcopy(bundle)
        del missing["review_submission"]
        with self.assertRaises(ValidationError):
            evaluate(config, definition(), missing, contracts, "2026-09-09T12:00:00Z")
        old_gate = copy.deepcopy(gate)
        del old_gate["review_submission"]
        with self.assertRaises(ValidationError):
            make_request(old_gate, contracts, "2026-09-09T12:00:00Z")
        with self.assertRaises(ValidationError):
            gate_handoff(old_gate, request, config, contracts, "2026-09-09T12:00:00Z")
        with self.assertRaises(ValidationError):
            transition("FINAL_REVIEW", "FINAL_GATE_PASSED",
                       {"derived_gate_ready": True, "requirements_current": True,
                        "final_gate_current": True})

        state = Path(self.temporary.name) / "coordinator.sqlite3"
        store = Store(state, config["project"]["id"])
        with store.connection() as db:
            db.execute("INSERT INTO tickets VALUES('EX-1','FINAL_REVIEW',0)")
        with self.assertRaises(ValidationError):
            store.advance("EX-1", "FINAL_GATE_PASSED", 0,
                          {"derived_gate_ready": True, "requirements_current": True,
                           "final_gate_current": True})
        self.assertEqual(store.advance("EX-1", "FINAL_GATE_PASSED", 0, facts)[0],
                         "READY_FOR_OWNER_AUTHORIZATION")


if __name__ == "__main__":
    unittest.main()
