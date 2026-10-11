from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentic import ValidationError
from agentic.integration_queue import (DEFAULTS, WORKER_GUIDANCE, IntegrationQueueStore,
    build_record, delta_review_requirement, integration_status, post_merge_action,
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


def outcome(count, *, fail_at=None, commit=SHA4, tree=SHA5):
    return {
        "result_commit_sha": commit,
        "result_tree_sha": tree,
        "test_command": "python -B -m unittest",
        "prefix_results": [
            {"length": index, "passed": fail_at is None or index < fail_at,
             "result_digest": f"test-result-{index}"}
            for index in range(1, count + 1)
        ],
    }


def instruction(action, identifier="owner-1"):
    return {"instruction_id": identifier, "owner_actor_id": "owner-actor",
            "action": action, "epic": "AWF-EPIC", "issued_at": NOW}


class IntegrationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-integration-queue-")
        self.addCleanup(self.temporary.cleanup)
        self.store = IntegrationQueueStore(Path(self.temporary.name) / "queue.sqlite3")
        self.store.initialize("AWF-EPIC", "main", SHA1,
                              mode_record={"mode": "epic", "record_id": "mode-1"},
                              observed_at=NOW)

    def admit(self, item, result=None):
        count = len(self.store.snapshot("AWF-EPIC")["entries"]) + 1
        return self.store.admit("AWF-EPIC", item, result or outcome(count), observed_at=NOW)

    def test_ac1_deterministic_build_and_stable_entry_identity(self):
        first = self.admit(entry(11, SHA2))
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual([11], first["queue"])
        self.assertEqual([[11, SHA2]], state["current_build"]["entries"])
        rebuilt = build_record("AWF-EPIC", SHA1, state["entries"], outcome(1),
                               "ADMISSION", 1)
        self.assertEqual(state["current_build"]["build_id"], rebuilt["build_id"])
        self.assertEqual(SHA5, rebuilt["result_tree_sha"])

    def test_ac2_admission_lock_serializes_concurrent_runners(self):
        activity = {"active": 0, "maximum": 0}
        activity_lock = threading.Lock()
        results = []

        def runner(snapshot):
            with activity_lock:
                activity["active"] += 1
                activity["maximum"] = max(activity["maximum"], activity["active"])
            time.sleep(0.05)
            with activity_lock:
                activity["active"] -= 1
            return outcome(len(snapshot["entries"]))

        def admit_item(item):
            results.append(self.store.admit("AWF-EPIC", item, runner, observed_at=NOW))

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
        failed = self.admit(entry(12, SHA3), outcome(2, fail_at=2))
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
        exact = {"valid": True, "pr_number": 11, "head_sha": SHA2, "target_sha": SHA1}
        delta = {"satisfied": True}
        ready = integration_status(state, 11, exact, exact, exact, delta)
        self.assertEqual("success", ready["status"])
        self.assertEqual("awf/task-11", ready["merge_source_branch"])
        self.assertEqual("awf/int/awf-epic", ready["integration_branch_evidence_only"])
        pending = integration_status(state, 12,
            {**exact, "pr_number": 12, "head_sha": SHA3},
            {**exact, "pr_number": 12, "head_sha": SHA3},
            {**exact, "pr_number": 12, "head_sha": SHA3}, delta)
        self.assertEqual("pending", pending["status"])
        self.assertIn("position 2", pending["reason"])
        stale = integration_status(state, 11, {**exact, "head_sha": SHA3}, exact, exact, delta)
        self.assertEqual("pending", stale["status"])
        wrong_delta = {"required": True, "satisfied": True, "head_sha": SHA3}
        self.assertIn("delta review", integration_status(
            state, 11, exact, exact, exact, wrong_delta)["reason"])

    def test_ac5_target_move_invalidates_old_build_and_prefix_ejects_culprit(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        result = self.store.rebuild("AWF-EPIC", SHA4, outcome(2, fail_at=2),
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
            "AWF-EPIC", entry(11, SHA3), outcome(1), observed_at=NOW)
        self.assertEqual([[11, SHA3]], changed["build"]["entries"])
        self.assertEqual("HEAD_CHANGED", changed["build"]["reason"])
        removed = self.store.remove("AWF-EPIC", 11, outcome(0), observed_at=NOW)
        self.assertEqual([], removed["build"]["entries"])
        self.assertEqual("REMOVAL", removed["build"]["reason"])

    def test_ac6_owner_reorder_skip_and_dependency_order(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        reordered = self.store.owner_reorder(
            "AWF-EPIC", [12, 11], instruction("REORDER"), outcome(2), observed_at=NOW)
        self.assertEqual([[12, SHA3], [11, SHA2]], reordered["build"]["entries"])
        skipped = self.store.owner_skip(
            "AWF-EPIC", 11, instruction("SKIP", "owner-2"), outcome(1), observed_at=NOW)
        self.assertEqual([[12, SHA3]], skipped["build"]["entries"])

        other = IntegrationQueueStore(Path(self.temporary.name) / "dependencies.sqlite3")
        other.initialize("AWF-EPIC", "main", SHA1,
                         mode_record={"mode": "epic"}, observed_at=NOW)
        other.admit("AWF-EPIC", entry(11, SHA2), outcome(1), observed_at=NOW)
        other.admit("AWF-EPIC", entry(12, SHA3, depends=(11,), base="awf/task-11"),
                    outcome(2), observed_at=NOW)
        with self.assertRaisesRegex(ValidationError, "dependency behind"):
            other.owner_reorder("AWF-EPIC", [12, 11], instruction("REORDER"),
                                outcome(2), observed_at=NOW)

    def test_ac6_stalled_front_moves_behind_next_independent_and_notifies(self):
        old = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) -
               timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        self.admit(entry(11, SHA2, validated=old))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", validated=old))
        self.admit(entry(13, SHA4, validated=old))
        result = self.store.move_stalled_front(
            "AWF-EPIC", outcome(3), now=NOW, stall_after_hours=24)
        self.assertEqual(13, result["notice"]["new_front"])
        self.assertTrue(result["notice"]["owner_notification_required"])
        self.assertEqual([13, 11, 12], [item[0] for item in result["build"]["entries"]])

    def test_ac7_stacked_retarget_retains_only_unchanged_patch_evidence(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", patch="a" * 40))
        result = self.store.retarget_after_merge(
            "AWF-EPIC", 11, SHA4, {"12": "a" * 40}, outcome(1), observed_at=NOW)
        self.assertEqual([{"pr_number": 12, "retarget_to": "main",
                           "evidence_retained": True, "review_required": False}],
                         result["decisions"])
        self.assertEqual("main", self.store.snapshot("AWF-EPIC")["entries"][0]["base_branch"])

    def test_ac8_overlap_requires_run_and_code_change_uses_round(self):
        none = delta_review_requirement(["src/a.py"], ["src/b.py"])
        self.assertFalse(none["required"])
        required = delta_review_requirement(
            ["src/a.py"], ["src/a.py"], delta_review={"status": "FINDING",
             "run_id": "delta-1", "head_sha": SHA2, "code_change_required": True})
        self.assertTrue(required["consumes_run"])
        self.assertTrue(required["consumes_round"])
        self.assertFalse(required["satisfied"])
        passed = delta_review_requirement(
            ["src/a.py"], ["src/a.py"], delta_review={"status": "PASS",
             "run_id": "delta-2", "head_sha": SHA2, "code_change_required": False})
        self.assertTrue(passed["satisfied"])
        self.assertFalse(passed["consumes_round"])

    def test_ac9_independent_and_stacked_diffs_are_own_commits_only(self):
        independent = entry(11, SHA2)
        stacked = entry(12, SHA3, depends=(11,), base="awf/task-11")
        self.assertEqual("PASS", verify_own_commits(independent, [SHA2], [stacked])["status"])
        self.assertEqual("PASS", verify_own_commits(stacked, [SHA3], [independent])["status"])
        with self.assertRaisesRegex(ValidationError, "exactly its own commits"):
            verify_own_commits(stacked, [SHA2, SHA3], [independent])

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
        owner = {"instruction_id": "enable-1", "owner_actor_id": "owner-actor",
                 "action": "ENABLE_EPIC_MODE", "epic": "AWF-EPIC", "issued_at": NOW}
        self.assertEqual("epic", resolve_mode(owner_instruction=owner)["mode"])
        advisory = resolve_mode({"mode": "epic"}, owner, {"enforced": False})
        self.assertEqual("epic", advisory["mode"])
        with self.assertRaisesRegex(ValidationError, "App integration ID"):
            resolve_mode({"mode": "epic"}, owner, {"enforced": True,
                         "check_name": "awf-integration", "integration_id": 7,
                         "required_from_integration_id": False})
        enforced = resolve_mode({"mode": "epic"}, owner, {"enforced": True,
            "check_name": "awf-integration", "integration_id": 7,
            "required_from_integration_id": True})
        self.assertEqual("epic", enforced["mode"])

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


if __name__ == "__main__":
    unittest.main()
