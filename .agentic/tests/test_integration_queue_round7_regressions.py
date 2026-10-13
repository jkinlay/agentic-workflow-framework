import copy
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.integration_queue import (
    IntegrationQueueStore, _registered_provider_observation, build_record)
from test_integration_queue import (
    BUILD_REGISTRY, GIT_REGISTRY, MODE_REGISTRY, NOW, PROJECT_CONFIG,
    GATE_NOW, SHA1, SHA2, SHA3, SHA4, SHA5, entry, epic_mode_record, observed_build,
    observed_git, observed_mode, register_build_runner,
    register_integration_observer, runner, trusted_readiness,
    integration_status)


def observed_git_with_merge(request):
    if request["operation"] == "PULL_REQUEST_MERGE":
        return {
            "status": "MERGED",
            "pr_number": request["pr_number"],
            "reviewed_head_sha": request["reviewed_head_sha"],
            "target_branch": request["target_branch"],
            "old_target_sha": request["old_target_sha"],
            "new_target_sha": request["expected_new_target_sha"],
        }
    if request["operation"] == "RETARGET_RETENTION":
        return {
            "pr_number": request["pr_number"],
            "head_sha": request["head_sha"],
            "target_sha": request["target_sha"],
            "patch_id": request["reviewed_patch_id"],
        }
    return observed_git(request)


class Round7TrustRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-round7-")
        self.addCleanup(self.temporary.cleanup)
        BUILD_REGISTRY.clear()
        GIT_REGISTRY.clear()
        MODE_REGISTRY.clear()
        register_integration_observer(
            "fixture-git-provider", observed_git_with_merge, GIT_REGISTRY)
        register_integration_observer(
            "fixture-mode-provider", observed_mode, MODE_REGISTRY)
        self.store = IntegrationQueueStore(
            Path(self.temporary.name) / "queue.sqlite3",
            project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY,
            build_producer_registry=BUILD_REGISTRY,
            git_observer_registry=GIT_REGISTRY)
        self.store.initialize(
            "AWF-EPIC", "main", SHA1,
            target_observer_id="fixture-git-provider",
            mode_record=epic_mode_record(), observed_at=NOW)

    def admit(self, item, outcome=None, *, observed_at=NOW):
        return self.store.admit(
            "AWF-EPIC", item, "fixture-git-provider", outcome or runner(),
            observed_at=observed_at)

    def test_r6_001_nonfront_pr_cannot_be_removed_by_unrelated_target_move(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        with self.assertRaisesRegex(ValidationError, "front"):
            self.store.retarget_after_merge(
                "AWF-EPIC", 12, SHA4, "fixture-git-provider", runner(),
                observed_at=NOW)
        self.assertEqual(
            [11, 12],
            [item["pr_number"] for item in self.store.snapshot("AWF-EPIC")["entries"]])

    def test_r6_001_merge_observation_binds_front_head_and_result(self):
        self.admit(entry(11, SHA2))

        def unrelated_merge(request):
            if request["operation"] == "PULL_REQUEST_MERGE":
                result = observed_git_with_merge(request)
                result["reviewed_head_sha"] = SHA3
                return result
            return observed_git_with_merge(request)

        register_integration_observer(
            "fixture-unrelated-merge", unrelated_merge, GIT_REGISTRY)
        with self.assertRaisesRegex(ValidationError, "exact front PR/head"):
            self.store.retarget_after_merge(
                "AWF-EPIC", 11, SHA4, "fixture-unrelated-merge", runner(),
                observed_at=NOW)
        self.assertEqual(
            [11],
            [item["pr_number"] for item in self.store.snapshot("AWF-EPIC")["entries"]])

    def test_r6_003_mode_and_app_observation_receipts_are_retained(self):
        trust = self.store.snapshot("AWF-EPIC")["mode_trust"]
        for key in ("activation", "app_binding"):
            self.assertEqual(NOW, trust[key]["observed_at"])
            self.assertRegex(trust[key]["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_r6_003_admission_reobserves_mode_and_app_binding(self):
        later = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) +
                 timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        GIT_REGISTRY.clear()
        register_integration_observer(
            "fixture-git-provider", observed_git_with_merge, GIT_REGISTRY,
            observed_at=later)
        MODE_REGISTRY.clear()
        fresh_build = register_build_runner(
            lambda state: observed_build(state, observed_at=later))
        with self.assertRaisesRegex(
                ValidationError, "observer registration changed|accepted project configuration"):
            self.admit(entry(11, SHA2), fresh_build, observed_at=later)
        self.assertEqual([], self.store.snapshot("AWF-EPIC")["entries"])

    def test_r6_003_changed_app_rule_cannot_reuse_stored_binding(self):
        def changed_binding(request):
            result = observed_mode(request)
            if request["operation"] == "APP_BINDING":
                result["ruleset_revision"] = 2
            return result
        register_integration_observer(
            "fixture-mode-provider", changed_binding, MODE_REGISTRY)
        with self.assertRaisesRegex(ValidationError, "App binding changed"):
            self.admit(entry(11, SHA2))

    def test_r6_003_readiness_reobserves_mode_and_app_binding(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         posting_registry, delta_registry, mode_registry) = trusted_readiness()
        mode_registry.clear()
        pending = integration_status(
            state, state["entries"][0]["pr_number"], bundle, gate, request,
            authorization, delta, config=config, contracts=contracts,
            posting_collector_registry=posting_registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("pending", pending["status"])
        self.assertIn("accepted project configuration", pending["reason"])

    def test_r6_004_unchanged_patch_retention_is_stored_for_readiness(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11",
                         patch="a" * 40))
        result = self.store.retarget_after_merge(
            "AWF-EPIC", 11, SHA4, "fixture-git-provider", runner(),
            observed_at=NOW)
        self.assertTrue(result["decisions"][0]["evidence_retained"])
        retained = self.store.snapshot("AWF-EPIC")["entries"][0]["retained_evidence"]
        self.assertEqual(SHA1, retained["review_target_sha"])
        self.assertEqual(SHA4, retained["target_sha"])
        self.assertEqual(SHA3, retained["head_sha"])
        self.assertEqual("fixture-git-provider", retained["producer_id"])

    def test_r6_004_changed_patch_clears_retained_evidence(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11",
                         patch="b" * 40))
        result = self.store.retarget_after_merge(
            "AWF-EPIC", 11, SHA4, "fixture-git-provider", runner(),
            observed_at=NOW)
        self.assertFalse(result["decisions"][0]["evidence_retained"])
        self.assertNotIn(
            "retained_evidence",
            self.store.snapshot("AWF-EPIC")["entries"][0])

    def test_r6_004_unchanged_patch_retention_is_consumed_by_readiness(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         posting_registry, delta_registry, mode_registry) = trusted_readiness()
        review_target = state["target_sha"]
        state["target_sha"] = SHA5
        current_runner = runner(observed_at=GATE_NOW)
        current_outcome = current_runner(copy.deepcopy(state))
        current_build = build_record(
            state["epic"], state["target_sha"], state["entries"],
            current_outcome, "POST_MERGE", 2)
        state["current_build"] = current_build
        state["build_run_ids"].append(current_build["observation"]["run_id"])
        state["build_results"][current_build["input_id"]] = current_build["result_tree_sha"]
        producer = "fixture-retention-provider"
        state["entries"][0]["retained_evidence"] = {
            "review_target_sha": review_target,
            "prior_target_sha": review_target,
            "target_sha": SHA5,
            "head_sha": state["entries"][0]["head_sha"],
            "patch_id": state["entries"][0]["patch_id"],
            "producer_id": producer,
            "observation": {
                "producer_id": producer, "observation_id": "stored-observation",
                "observed_at": GATE_NOW, "request_sha256": "a" * 64,
                "receipt_sha256": "b" * 64,
                "implementation_sha256": "c" * 64,
                "release_sha256": "d" * 64,
            },
        }
        retention_registry = {}
        register_integration_observer(
            producer, observed_git_with_merge, retention_registry,
            observed_at=GATE_NOW)
        patch_request = {
            "operation": "TASK_COMMIT_RANGE",
            "pr_number": state["entries"][0]["pr_number"],
            "head_sha": state["entries"][0]["head_sha"],
            "claimed_patch_id": state["entries"][0]["patch_id"],
        }
        patch_result, patch_attestation = _registered_provider_observation(
            retention_registry, producer, patch_request,
            "admission patch observation", now=GATE_NOW, max_age=1800,
            return_attestation=True)
        state["entries"][0]["admission_patch"] = {
            "request": patch_request, "result": patch_result,
            "observation": patch_attestation,
        }
        ready = integration_status(
            state, state["entries"][0]["pr_number"], bundle, gate, request,
            authorization, delta, config=config, contracts=contracts,
            posting_collector_registry=posting_registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            retarget_observer_registry=retention_registry, now=GATE_NOW)
        self.assertEqual("success", ready["status"])
        self.assertRegex(
            ready["retarget_retention"]["receipt_sha256"], r"^[0-9a-f]{64}$")

        changed = copy.deepcopy(state)
        changed["entries"][0]["patch_id"] = "e" * 40
        pending = integration_status(
            changed, changed["entries"][0]["pr_number"], bundle, gate, request,
            authorization, delta, config=config, contracts=contracts,
            posting_collector_registry=posting_registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            retarget_observer_registry=retention_registry, now=GATE_NOW)
        self.assertEqual("pending", pending["status"])
        self.assertIn("patch observation", pending["reason"])


if __name__ == "__main__":
    unittest.main()
