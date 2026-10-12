import copy
from datetime import datetime, timedelta
import hashlib
import inspect
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.integration_queue import (
    IntegrationQueueStore, build_input_id, delta_review_requirement,
    post_merge_action, resolve_mode)
from test_integration_queue import (
    BUILD_REGISTRY, GATE_NOW, GIT_REGISTRY, MODE_REGISTRY, NOW, PROJECT_CONFIG,
    SHA1, SHA2, SHA3, entry, config_observer, observed_build, observed_git,
    observed_mode, owner_observer, register_build_runner,
    register_integration_observer, runner, trusted_readiness,
    integration_status)


def _legacy_or_current_mode_record():
    config = {"mode": "epic", "test_command": "python -B -m unittest",
              "provenance": {"source": "reviewed_config",
                  "config_path": ".agentic/integration-queue-config.yaml",
                  "config_sha256": "a" * 64, "review_record_id": "config-review-1",
                  "reviewed_at": NOW, "producer_id": "fixture-mode-provider"}}
    if "mode_observer_registry" in inspect.signature(resolve_mode).parameters:
        return resolve_mode(
            config, app_binding={"producer_id": "fixture-mode-provider"},
            epic="AWF-EPIC", project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY, now=NOW)
    return resolve_mode(
        config, epic="AWF-EPIC", project_config=PROJECT_CONFIG,
        reviewed_config_observer=config_observer)


def _same_tree_different_commit_runner():
    def callback(state):
        outcome = observed_build(state)
        prior = state["target_sha"]
        for index, step in enumerate(outcome["construction"], 1):
            commit = hashlib.sha1(f"alternate-merge-{index}".encode()).hexdigest()
            step["base_commit_sha"] = prior
            step["parents"] = [prior, step["head_sha"]]
            step["result_commit_sha"] = commit
            outcome["prefix_results"][index - 1]["commit_sha"] = commit
            prior = commit
        outcome["result_commit_sha"] = prior
        deterministic = {"input_id": outcome["input_id"],
            "result_commit_sha": outcome["result_commit_sha"],
            "result_tree_sha": outcome["result_tree_sha"],
            "construction": outcome["construction"],
            "prefix_results": outcome["prefix_results"]}
        observation = outcome["observation"]
        observation["result_id"] = fingerprint("integration_build_result", deterministic)
        observation["observation_id"] = fingerprint(
            "integration_build_observation",
            {key: value for key, value in observation.items()
             if key != "observation_id"})
        return outcome
    return register_build_runner(callback)


class Round6TrustRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-round6-")
        self.addCleanup(self.temporary.cleanup)
        BUILD_REGISTRY.clear()
        GIT_REGISTRY.clear()
        MODE_REGISTRY.clear()
        register_integration_observer("fixture-git-provider", observed_git, GIT_REGISTRY)
        register_integration_observer("fixture-mode-provider", observed_mode, MODE_REGISTRY)
        kwargs = {"project_config": PROJECT_CONFIG,
                  "build_producer_registry": BUILD_REGISTRY,
                  "git_observer_registry": GIT_REGISTRY}
        parameters = inspect.signature(IntegrationQueueStore).parameters
        if "mode_observer_registry" in parameters:
            kwargs["mode_observer_registry"] = MODE_REGISTRY
        else:
            kwargs["owner_instruction_observer"] = owner_observer
            kwargs["reviewed_config_observer"] = config_observer
        self.store = IntegrationQueueStore(
            Path(self.temporary.name) / "queue.sqlite3", **kwargs)
        self.store.initialize("AWF-EPIC", "main", SHA1,
                              target_observer_id="fixture-git-provider",
                              mode_record=_legacy_or_current_mode_record(),
                              observed_at=NOW)

    def admit(self, item, outcome=None):
        return self.store.admit("AWF-EPIC", item, "fixture-git-provider",
                                outcome or runner(), observed_at=NOW)

    def test_r5_001_determinism_is_tree_not_commit_identity(self):
        first = self.admit(entry(11, SHA2))["build"]
        rebuilt = self.store.rebuild(
            "AWF-EPIC", SHA1, _same_tree_different_commit_runner(),
            "TARGET_MOVED", target_observer_id="fixture-git-provider",
            observed_at=NOW)["build"]
        self.assertEqual(first["result_tree_sha"], rebuilt["result_tree_sha"])
        self.assertNotEqual(first["result_commit_sha"], rebuilt["result_commit_sha"])

    def test_r5_002_conflict_is_receipted_observation_and_retains_ownership(self):
        result = self.store.conflict(
            "AWF-EPIC", entry(11, SHA2), "fixture-git-provider", observed_at=NOW)
        self.assertEqual(["src/conflict.py"], result["event"]["detail"]["paths"])
        self.assertEqual(11, self.store.snapshot("AWF-EPIC")["observed_commits"][SHA2])

    def test_r5_003_stall_threshold_has_no_caller_parameter(self):
        self.assertNotIn("stall_after_hours",
                         inspect.signature(self.store.move_stalled_front).parameters)

    def test_r5_004_failure_quarantines_dependent_closure(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11"))
        self.store.rebuild(
            "AWF-EPIC", SHA1, runner(fail_at=1), "TARGET_MOVED",
            target_observer_id="fixture-git-provider", observed_at=NOW)
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual([], state["entries"])
        self.assertEqual([11, 12],
                         [item["pr_number"] for item in state["quarantined_entries"]])

    def test_r5_004_readiness_rejects_absent_dependency(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         registry, delta_registry, mode_registry) = trusted_readiness()
        state["entries"][0]["depends_on"] = [99]
        result = integration_status(
            state, 7, bundle, gate, request, authorization, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("pending", result["status"])
        self.assertIn("dependency", result["reason"])

    def test_r5_005_overlap_requires_exact_r_and_r_prime(self):
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/a.py"]}]
        reviewed_diff = [{"path": "src/a.py", "base_blob_sha": SHA1,
                          "head_blob_sha": SHA2}]
        with self.assertRaisesRegex(ValidationError, "effective_diff"):
            delta_review_requirement(
                ["src/a.py"], merges, head_sha=SHA2, review_base_sha=SHA1,
                target_sha=SHA3, build_id="build-1", reviewed_diff=reviewed_diff)

    def test_r5_006_mode_trust_uses_only_runtime_registry(self):
        parameters = inspect.signature(resolve_mode).parameters
        self.assertNotIn("owner_instruction_observer", parameters)
        self.assertNotIn("reviewed_config_observer", parameters)
        self.assertIn("mode_observer_registry", parameters)

    def test_audit_runtime_observations_are_fresh(self):
        register_integration_observer(
            "fixture-stale-conflict", observed_git, GIT_REGISTRY,
            observed_at="2026-10-10T08:00:00Z")
        with self.assertRaisesRegex(ValidationError, "stale"):
            self.store.conflict(
                "AWF-EPIC", entry(11, SHA2), "fixture-stale-conflict",
                observed_at=NOW)
        later = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) +
                 timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        with self.assertRaisesRegex(ValidationError, "stale"):
            self.store.admit(
                "AWF-EPIC", entry(11, SHA2), "fixture-git-provider", runner(),
                observed_at=later)

    def test_audit_target_tip_and_post_merge_ci_are_observed(self):
        parameters = inspect.signature(self.store.initialize).parameters
        self.assertIn("target_observer_id", parameters)
        action = post_merge_action(
            {"producer_id": "fixture-git-provider"},
            provider_observer_registry=GIT_REGISTRY,
            project_config=PROJECT_CONFIG,
            project_config_selector={"producer_id": "fixture-mode-provider"},
            project_config_observer_registry=MODE_REGISTRY,
            target_branch="main", target_sha=SHA1,
            now=NOW)
        self.assertEqual("OPEN_REVERT_PR", action["action"])


if __name__ == "__main__":
    unittest.main()
