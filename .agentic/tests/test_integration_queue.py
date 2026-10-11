from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.integration_queue import (DEFAULTS, WORKER_GUIDANCE, IntegrationQueueStore,
    build_input_id, build_record, delta_review_requirement, integration_status, post_merge_action,
    resolve_mode, steward_policy, verify_own_commits)


SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40
SHA4 = "4" * 40
SHA5 = "5" * 40
NOW = "2026-10-10T10:00:00Z"


def entry(pr, head, *, depends=(), base="main", validated=NOW, paths=None, patch=None):
    return {
        "pr_number": pr,
        "ticket": f"AWF-{pr}",
        "head_sha": head,
        "branch": f"awf/task-{pr}",
        "base_branch": base,
        "depends_on": list(depends),
        "validated_at": validated,
        "reviewed_paths": paths or [f"src/task-{pr}.py"],
        "own_commits": [head],
        "patch_id": patch or head,
    }


def observed_build(state, *, fail_at=None, variant="stable"):
    target = state["target_sha"]
    entries = state["entries"]
    command = "python -B -m unittest"
    prior = target
    construction = []
    for index, item in enumerate(entries, 1):
        commit = hashlib.sha1(f"{prior}:{item['head_sha']}:{variant}".encode()).hexdigest()
        tree = hashlib.sha1(f"{commit}:tree:{variant}".encode()).hexdigest()
        construction.append({"length": index, "base_commit_sha": prior,
            "head_sha": item["head_sha"], "parents": [prior, item["head_sha"]],
            "result_commit_sha": commit,
            "result_tree_sha": tree})
        prior = commit
    tree = (construction[-1]["result_tree_sha"] if construction else
            hashlib.sha1(f"{target}:tree:{variant}".encode()).hexdigest())
    input_id = build_input_id("AWF-EPIC", target, entries, command)
    prefixes = [
        {"length": index, "commit_sha": step["result_commit_sha"],
         "tree_sha": step["result_tree_sha"],
         "passed": fail_at is None or index < fail_at,
         "result_digest": f"test-result-{index}"}
        for index, step in enumerate(construction, 1)
    ]
    deterministic_result = {"input_id": input_id, "result_commit_sha": prior,
                            "result_tree_sha": tree, "construction": construction,
                            "prefix_results": prefixes}
    result_id = fingerprint("integration_build_result", deterministic_result)
    observation = {"producer_id": "fixture-build-adapter", "observed_at": NOW,
                   "input_id": input_id, "result_id": result_id}
    observation["observation_id"] = fingerprint("integration_build_observation", observation)
    return {
        "input_id": input_id,
        "result_commit_sha": prior,
        "result_tree_sha": tree,
        "test_command": "python -B -m unittest",
        "construction": construction,
        "prefix_results": prefixes,
        "observation": observation,
    }


def runner(*, fail_at=None, variant="stable"):
    return lambda state: observed_build(state, fail_at=fail_at, variant=variant)


def instruction(action, payload, identifier="owner-1"):
    return {"instruction_id": identifier, "owner_actor_id": "owner-actor",
            "producer_id": "fixture-owner-collector", "source_event_id": f"event-{identifier}",
            "action": action, "epic": "AWF-EPIC", "payload": payload, "issued_at": NOW}


def epic_mode_record():
    return resolve_mode({"mode": "epic", "test_command": "python -B -m unittest", "provenance": {
        "source": "reviewed_config", "config_path": ".agentic/integration-queue-config.yaml",
        "config_sha256": "a" * 64, "review_record_id": "config-review-1",
        "reviewed_at": NOW, "producer_id": "fixture-config-validator"}}, epic="AWF-EPIC")


def readiness(head, target, *, expires="2026-10-10T11:00:00Z"):
    exact = {"pr_number": 11, "head_sha": head, "target_sha": target}
    review = {"kind": "review", "producer_id": "critic-1", "run_id": "critic-run-1",
              **exact, "created_at": NOW, "conclusion": "APPROVE", "closed": True,
              "result_digest": "a" * 64}
    review["record_id"] = fingerprint("integration_review_record", review)
    gate = {"kind": "final_gate", "producer_id": "gate-evaluator", "run_id": "gate-run-1",
            **exact, "created_at": NOW, "conclusion": "READY_FOR_OWNER_AUTHORIZATION",
            "review_record_id": review["record_id"], "expires_at": expires,
            "execution_authority": False}
    gate["gate_hash"] = fingerprint("integration_gate", gate)
    gate["record_id"] = fingerprint("integration_final_gate_record", gate)
    authorization = {"kind": "owner_authorization", "producer_id": "authorization-verifier",
        "run_id": "authorization-run-1", **exact, "created_at": NOW,
        "decision": "AUTHORIZE", "gate_id": gate["record_id"], "gate_hash": gate["gate_hash"],
        "source": {"channel": "github_pr_comment", "event_id": "owner-comment-1",
                   "actor_id": 7, "raw_body_sha256": "c" * 64, "authenticated": True},
        "expires_at": expires, "execution_authority": False}
    authorization["record_id"] = fingerprint("integration_owner_authorization_record", authorization)
    return review, gate, authorization


class IntegrationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-integration-queue-")
        self.addCleanup(self.temporary.cleanup)
        self.store = IntegrationQueueStore(Path(self.temporary.name) / "queue.sqlite3")
        self.store.initialize("AWF-EPIC", "main", SHA1,
                              mode_record=epic_mode_record(),
                              observed_at=NOW)

    def admit(self, item, result=None):
        return self.store.admit("AWF-EPIC", item, item["own_commits"],
                                result or runner(), observed_at=NOW)

    def test_ac1_deterministic_build_and_stable_entry_identity(self):
        first = self.admit(entry(11, SHA2))
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual([11], first["queue"])
        self.assertEqual([[11, SHA2]], state["current_build"]["entries"])
        rebuilt = build_record("AWF-EPIC", SHA1, state["entries"], observed_build(state),
                               "TARGET_MOVED", 99)
        self.assertEqual(state["current_build"]["build_id"], rebuilt["build_id"])
        self.assertEqual(state["current_build"]["result_tree_sha"], rebuilt["result_tree_sha"])

    def test_ac1_rejects_divergent_results_for_identical_inputs(self):
        self.admit(entry(11, SHA2))
        with self.assertRaisesRegex(ValidationError, "divergent construction"):
            self.store.rebuild("AWF-EPIC", SHA1, runner(variant="different"),
                               "TARGET_MOVED", observed_at=NOW)

    def test_ac1_store_rejects_caller_supplied_build_document(self):
        item = entry(11, SHA2)
        proposed_state = self.store.snapshot("AWF-EPIC")
        proposed_state["entries"].append(item)
        with self.assertRaisesRegex(ValidationError, "provider adapter runner"):
            self.store.admit("AWF-EPIC", item, [SHA2], observed_build(proposed_state),
                             observed_at=NOW)

    def test_ac2_admission_lock_serializes_concurrent_runners(self):
        activity = {"active": 0, "maximum": 0}
        activity_lock = threading.Lock()
        results = []

        def admission_runner(snapshot):
            with activity_lock:
                activity["active"] += 1
                activity["maximum"] = max(activity["maximum"], activity["active"])
            time.sleep(0.05)
            with activity_lock:
                activity["active"] -= 1
            return observed_build(snapshot)

        def admit_item(item):
            results.append(self.store.admit(
                "AWF-EPIC", item, item["own_commits"], admission_runner, observed_at=NOW))

        threads = [threading.Thread(target=admit_item, args=(entry(11, SHA2),)),
                   threading.Thread(target=admit_item, args=(entry(12, SHA3),))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, activity["maximum"])
        self.assertEqual(2, len(results))
        self.assertEqual({11, 12}, {item["pr_number"] for item in
                                   self.store.snapshot("AWF-EPIC")["entries"]})

    def test_ac2_failed_admission_ejects_only_new_head(self):
        self.admit(entry(11, SHA2))
        failed = self.admit(entry(12, SHA3), runner(fail_at=2))
        self.assertEqual("INTEGRATION_FAILED", failed["status"])
        self.assertEqual([11], failed["queue"])
        self.assertEqual(SHA2, self.store.snapshot("AWF-EPIC")["entries"][0]["head_sha"])

    def test_ac3_conflict_records_paths_without_admission(self):
        result = self.store.conflict("AWF-EPIC", entry(11, SHA2),
                                     ["src/conflict.py"], observed_at=NOW)
        self.assertEqual("INTEGRATION_CONFLICT", result["status"])
        self.assertEqual([], result["queue"])
        self.assertEqual(["src/conflict.py"], result["event"]["detail"]["paths"])

    def test_ac4_only_exact_front_tuple_can_succeed(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        state = self.store.snapshot("AWF-EPIC")
        review, gate, authorization = readiness(SHA2, SHA1)
        delta = {"required": False, "satisfied": True, "head_sha": SHA2,
                 "review_base_sha": SHA1, "target_sha": SHA1,
                 "build_id": state["current_build"]["build_id"],
                 "intervening_digest": "a" * 64, "diff_digest": None}
        ready = integration_status(state, 11, review, gate, authorization, delta, now=NOW)
        self.assertEqual("success", ready["status"])
        self.assertEqual("awf/task-11", ready["merge_source_branch"])
        self.assertEqual("awf/int/awf-epic", ready["integration_branch_evidence_only"])
        pending = integration_status(state, 12, review, gate, authorization, delta, now=NOW)
        self.assertEqual("pending", pending["status"])
        self.assertIn("position 2", pending["reason"])
        stale_review = {**review, "head_sha": SHA3}
        stale = integration_status(state, 11, stale_review, gate, authorization, delta, now=NOW)
        self.assertEqual("pending", stale["status"])
        wrong_delta = {**delta, "required": True, "head_sha": SHA3,
                       "diff_digest": "c" * 64}
        self.assertIn("delta review", integration_status(
            state, 11, review, gate, authorization, wrong_delta, now=NOW)["reason"])

    def test_ac4_minimal_self_declared_evidence_and_expiry_are_rejected(self):
        self.admit(entry(11, SHA2))
        state = self.store.snapshot("AWF-EPIC")
        minimal = {"valid": True, "pr_number": 11, "head_sha": SHA2, "target_sha": SHA1}
        delta = {"required": False, "satisfied": True, "head_sha": SHA2,
            "review_base_sha": SHA1, "target_sha": SHA1,
            "build_id": state["current_build"]["build_id"],
            "intervening_digest": "a" * 64, "diff_digest": None}
        self.assertEqual("pending", integration_status(
            state, 11, minimal, minimal, minimal, delta, now=NOW)["status"])
        review, gate, authorization = readiness(
            SHA2, SHA1, expires="2026-10-10T09:59:59Z")
        self.assertIn("expired", integration_status(
            state, 11, review, gate, authorization, delta, now=NOW)["reason"])

    def test_ac5_target_move_invalidates_old_build_and_prefix_ejects_culprit(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        result = self.store.rebuild("AWF-EPIC", SHA4, runner(fail_at=2),
                                    "TARGET_MOVED", observed_at=NOW)
        self.assertEqual("INTEGRATION_FAILED", result["status"])
        self.assertEqual(12, result["ejected_pr"])
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual(SHA4, state["target_sha"])
        self.assertIsNone(state["current_build"])
        self.assertEqual([11], [item["pr_number"] for item in state["entries"]])

    def test_ac5_head_change_and_removal_each_rebuild(self):
        self.admit(entry(11, SHA2))
        changed = self.store.head_changed(
            "AWF-EPIC", entry(11, SHA3), [SHA3], runner(), observed_at=NOW)
        self.assertEqual([[11, SHA3]], changed["build"]["entries"])
        self.assertEqual("HEAD_CHANGED", changed["build"]["reason"])
        removed = self.store.remove("AWF-EPIC", 11, runner(), observed_at=NOW)
        self.assertEqual([], removed["build"]["entries"])
        self.assertEqual("REMOVAL", removed["build"]["reason"])

    def test_ac5_head_change_preserves_stable_entry_identity(self):
        self.admit(entry(11, SHA2))
        changed = entry(11, SHA3)
        changed["ticket"] = "AWF-OTHER"
        with self.assertRaisesRegex(ValidationError, "stable ticket"):
            self.store.head_changed("AWF-EPIC", changed, [SHA3], runner(), observed_at=NOW)

    def test_ac6_owner_reorder_skip_and_dependency_order(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        reordered = self.store.owner_reorder(
            "AWF-EPIC", [12, 11], instruction("REORDER", {"order": [12, 11]}),
            runner(), observed_at=NOW)
        self.assertEqual([[12, SHA3], [11, SHA2]], reordered["build"]["entries"])
        skipped = self.store.owner_skip(
            "AWF-EPIC", 11, instruction("SKIP", {"pr_number": 11}, "owner-2"),
            runner(), observed_at=NOW)
        self.assertEqual([[12, SHA3]], skipped["build"]["entries"])

        other = IntegrationQueueStore(Path(self.temporary.name) / "dependencies.sqlite3")
        other.initialize("AWF-EPIC", "main", SHA1,
                         mode_record=epic_mode_record(), observed_at=NOW)
        other.admit("AWF-EPIC", entry(11, SHA2), [SHA2], runner(), observed_at=NOW)
        other.admit("AWF-EPIC", entry(12, SHA3, depends=(11,), base="awf/task-11"),
                    [SHA3], runner(), observed_at=NOW)
        with self.assertRaisesRegex(ValidationError, "dependency behind"):
            other.owner_reorder("AWF-EPIC", [12, 11],
                instruction("REORDER", {"order": [12, 11]}), runner(), observed_at=NOW)

    def test_ac6_stalled_front_moves_behind_next_independent_and_notifies(self):
        old = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) -
               timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        first = entry(11, SHA2, validated=old)
        self.store.admit("AWF-EPIC", first, [SHA2], runner(), observed_at=old)
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", validated=old))
        self.admit(entry(13, SHA4, validated=old))
        result = self.store.move_stalled_front(
            "AWF-EPIC", runner(), now=NOW, stall_after_hours=24)
        self.assertEqual(13, result["notice"]["new_front"])
        self.assertTrue(result["notice"]["owner_notification_required"])
        self.assertEqual([13, 11, 12], [item[0] for item in result["build"]["entries"]])

    def test_ac6_owner_instruction_payload_is_bound_and_consumed_once(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        recorded = instruction("REORDER", {"order": [12, 11]}, "one-use")
        self.store.owner_reorder("AWF-EPIC", [12, 11], recorded, runner(), observed_at=NOW)
        with self.assertRaisesRegex(ValidationError, "already been consumed"):
            self.store.owner_reorder("AWF-EPIC", [12, 11], recorded, runner(), observed_at=NOW)
        wrong = instruction("SKIP", {"pr_number": 11}, "wrong-payload")
        with self.assertRaisesRegex(ValidationError, "payload"):
            self.store.owner_skip("AWF-EPIC", 12, wrong, runner(), observed_at=NOW)

    def test_ac6_stall_clock_starts_when_entry_becomes_front(self):
        old = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) -
               timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        first = entry(11, SHA2, validated=old)
        second = entry(12, SHA3, validated=old)
        self.store.admit("AWF-EPIC", first, [SHA2], runner(), observed_at=old)
        self.store.admit("AWF-EPIC", second, [SHA3], runner(), observed_at=old)
        self.store.remove("AWF-EPIC", 11, runner(), observed_at=NOW)
        self.admit(entry(13, SHA4))
        with self.assertRaisesRegex(ValidationError, "stall threshold"):
            self.store.move_stalled_front("AWF-EPIC", runner(), now=NOW,
                                          stall_after_hours=24)

    def test_ac7_stacked_retarget_retains_only_unchanged_patch_evidence(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", patch="a" * 40))
        result = self.store.retarget_after_merge(
            "AWF-EPIC", 11, SHA4, {"12": "a" * 40}, runner(), observed_at=NOW)
        self.assertEqual([{"pr_number": 12, "retarget_to": "main",
                           "evidence_retained": True, "review_required": False}],
                         result["decisions"])
        self.assertEqual("main", self.store.snapshot("AWF-EPIC")["entries"][0]["base_branch"])

    def test_ac8_overlap_requires_run_and_code_change_uses_round(self):
        no_merges = [{"merge_commit_sha": SHA3, "paths": ["src/b.py"]}]
        none = delta_review_requirement(["src/a.py"], no_merges, head_sha=SHA2,
            review_base_sha=SHA1, target_sha=SHA3, build_id="build-1")
        self.assertFalse(none["required"])
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/a.py"]}]
        intervening_digest = fingerprint("integration_intervening", merges)
        required = delta_review_requirement(
            ["src/a.py"], merges, head_sha=SHA2, review_base_sha=SHA1,
            target_sha=SHA3, build_id="build-1", delta_review={"status": "FINDING",
             "run_id": "delta-1", "producer_id": "critic-1", "head_sha": SHA2,
             "review_base_sha": SHA1, "target_sha": SHA3, "build_id": "build-1",
             "intervening_digest": intervening_digest, "diff_digest": "d" * 64,
             "reviewed_at": NOW, "code_change_required": True})
        self.assertTrue(required["consumes_run"])
        self.assertTrue(required["consumes_round"])
        self.assertFalse(required["satisfied"])
        passed = delta_review_requirement(
            ["src/a.py"], merges, head_sha=SHA2, review_base_sha=SHA1,
            target_sha=SHA3, build_id="build-1", delta_review={"status": "PASS",
             "run_id": "delta-2", "producer_id": "critic-1", "head_sha": SHA2,
             "review_base_sha": SHA1, "target_sha": SHA3, "build_id": "build-1",
             "intervening_digest": intervening_digest, "diff_digest": "e" * 64,
             "reviewed_at": NOW, "code_change_required": False})
        self.assertTrue(passed["satisfied"])
        self.assertFalse(passed["consumes_round"])

    def test_ac8_delta_review_cannot_replay_after_target_change(self):
        self.admit(entry(11, SHA2))
        old_state = self.store.snapshot("AWF-EPIC")
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/task-11.py"]}]
        digest = fingerprint("integration_intervening", merges)
        delta = delta_review_requirement(["src/task-11.py"], merges, head_sha=SHA2,
            review_base_sha=SHA1, target_sha=SHA1,
            build_id=old_state["current_build"]["build_id"], delta_review={
                "status": "PASS", "run_id": "delta-old", "producer_id": "critic-1",
                "head_sha": SHA2, "review_base_sha": SHA1, "target_sha": SHA1,
                "build_id": old_state["current_build"]["build_id"],
                "intervening_digest": digest, "diff_digest": "d" * 64,
                "code_change_required": False, "reviewed_at": NOW})
        self.store.rebuild("AWF-EPIC", SHA4, runner(), "TARGET_MOVED", observed_at=NOW)
        state = self.store.snapshot("AWF-EPIC")
        review, gate, authorization = readiness(SHA2, SHA4)
        self.assertIn("delta review", integration_status(
            state, 11, review, gate, authorization, delta, now=NOW)["reason"])

    def test_ac9_independent_and_stacked_diffs_are_own_commits_only(self):
        independent = entry(11, SHA2)
        stacked = entry(12, SHA3, depends=(11,), base="awf/task-11")
        self.assertEqual("PASS", verify_own_commits(independent, [SHA2], [stacked])["status"])
        self.assertEqual("PASS", verify_own_commits(stacked, [SHA3], [independent])["status"])
        with self.assertRaisesRegex(ValidationError, "exactly its own commits"):
            verify_own_commits(stacked, [SHA2, SHA3], [independent])

    def test_ac9_admission_requires_observed_diff_and_retains_removed_commits(self):
        with self.assertRaisesRegex(ValidationError, "exactly its own commits"):
            self.store.admit("AWF-EPIC", entry(11, SHA2), [SHA2, SHA3],
                             runner(), observed_at=NOW)
        self.admit(entry(11, SHA2))
        self.store.remove("AWF-EPIC", 11, runner(), observed_at=NOW)
        reused = entry(12, SHA2)
        with self.assertRaisesRegex(ValidationError, "already observed"):
            self.store.admit("AWF-EPIC", reused, [SHA2], runner(), observed_at=NOW)

    def test_ac7_independent_entry_must_target_epic_branch(self):
        with self.assertRaisesRegex(ValidationError, "epic target branch"):
            self.store.admit("AWF-EPIC", entry(11, SHA2, base="release"), [SHA2],
                             runner(), observed_at=NOW)

    def test_ac10_steward_limits_revert_and_epic_close(self):
        policy = steward_policy()
        self.assertIsNone(policy["model_route"])
        self.assertEqual(0, policy["model_tokens"])
        self.assertFalse(policy["may_edit_task_branches"])
        self.assertFalse(policy["may_merge_target"])
        self.assertEqual("OPEN_REVERT_PR", post_merge_action(False)["action"])
        closed = self.store.close("AWF-EPIC", observed_at=NOW)
        self.assertEqual("awf/int/awf-epic", closed["delete_branch"])
        self.assertFalse(closed["execution_authority"])

    def test_ac12_serial_default_and_epic_activation_binding(self):
        self.assertEqual(DEFAULTS, resolve_mode())
        owner = instruction("ENABLE_EPIC_MODE", {
            "test_command": "python -B -m unittest", "stall_after_hours": 24,
            "max_stack_depth": 2}, "enable-1")
        self.assertEqual("epic", resolve_mode(
            {"test_command": "python -B -m unittest"}, owner_instruction=owner,
            epic="AWF-EPIC")["mode"])
        config = {"mode": "epic", "test_command": "python -B -m unittest", "provenance": {
            "source": "reviewed_config", "config_path": ".agentic/integration-queue-config.yaml",
            "config_sha256": "a" * 64, "review_record_id": "review-1",
            "reviewed_at": NOW, "producer_id": "config-validator"}}
        advisory = resolve_mode(config, owner, {"enforced": False}, epic="AWF-EPIC")
        self.assertEqual("epic", advisory["mode"])
        with self.assertRaisesRegex(ValidationError, "App integration ID"):
            resolve_mode(config, owner, {"enforced": True,
                         "check_name": "awf-integration", "integration_id": 7,
                         "required_from_integration_id": False}, epic="AWF-EPIC")
        enforced = resolve_mode(config, owner, {"enforced": True,
            "check_name": "awf-integration", "integration_id": 7,
            "required_from_integration_id": True}, epic="AWF-EPIC")
        self.assertEqual("epic", enforced["mode"])

    def test_ac12_fabricated_mode_records_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "provenance"):
            resolve_mode({"mode": "epic", "test_command": "python -B -m unittest"},
                         epic="AWF-EPIC")
        other = IntegrationQueueStore(Path(self.temporary.name) / "fabricated.sqlite3")
        with self.assertRaisesRegex(ValidationError, "closed accepted"):
            other.initialize("AWF-EPIC", "main", SHA1,
                             mode_record={"mode": "epic"}, observed_at=NOW)

    def test_worker_guidance_covers_untrusted_context_dependencies_and_target(self):
        self.assertIn("untrusted context", WORKER_GUIDANCE)
        self.assertIn("depends_on", WORKER_GUIDANCE)
        self.assertIn("branch from the target", WORKER_GUIDANCE)

    def test_state_must_be_outside_declared_worktree(self):
        root = Path(self.temporary.name) / "worktree"
        root.mkdir()
        with self.assertRaisesRegex(ValidationError, "outside every worktree"):
            IntegrationQueueStore(root / "state.sqlite3", worktree_roots=[root])

    def test_entry_point_exposes_worker_guidance(self):
        script = Path(__file__).resolve().parents[1] / "scripts/integration_queue.py"
        spec = importlib.util.spec_from_file_location("integration_queue_entrypoint", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["worker-guidance"])
        self.assertEqual(0, code)
        self.assertEqual(WORKER_GUIDANCE, json.loads(output.getvalue())["guidance"])

    def test_entry_point_refuses_unauthenticated_build_documents(self):
        script = Path(__file__).resolve().parents[1] / "scripts/integration_queue.py"
        spec = importlib.util.spec_from_file_location("integration_queue_entrypoint", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = Path(self.temporary.name) / "cli.sqlite3"
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["--state", str(state), "admit", "--epic", "AWF-EPIC",
                "--entry", "entry.json", "--outcome", "outcome.json",
                "--observed-diff-commits", "commits.json"])
        self.assertEqual(2, code)
        self.assertIn("standalone CLI cannot authenticate", output.getvalue())


if __name__ == "__main__":
    unittest.main()
