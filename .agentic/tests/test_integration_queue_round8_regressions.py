"""Forged-input regressions for the AWF-10 round-8 trust boundaries."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint, sha256
from agentic.integration_queue import (
    IntegrationQueueStore, render_owner_instruction,
    resolve_mode)
from test_integration_queue import (
    BUILD_REGISTRY, GATE_NOW, GIT_REGISTRY, MODE_REGISTRY, NOW, PROJECT_CONFIG,
    PROVIDER_IDENTITY, SHA1, SHA2, SHA3, entry, epic_mode_record,
    instruction, observed_git, observed_mode, register_integration_observer, runner,
    trusted_readiness, integration_status)


class Round8ForgedInputRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-round8-")
        self.addCleanup(self.temporary.cleanup)
        BUILD_REGISTRY.clear()
        GIT_REGISTRY.clear()
        MODE_REGISTRY.clear()
        register_integration_observer(
            "fixture-git-provider", observed_git, GIT_REGISTRY)
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

    def admit(self, item):
        return self.store.admit(
            "AWF-EPIC", item, "fixture-git-provider", runner(),
            observed_at=NOW)

    def test_r7_001_forged_green_build_summary_is_rejected(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         posting_registry, delta_registry, mode_registry) = trusted_readiness()
        candidate = state["entries"][0]
        state["current_build"] = {
            "target_sha": state["target_sha"],
            "entries": [[candidate["pr_number"], candidate["head_sha"]]],
            "green": True,
            "build_id": "caller-green-build",
            "result_commit_sha": SHA2,
            "result_tree_sha": SHA3,
        }
        result = integration_status(
            state, candidate["pr_number"], bundle, gate, request, authorization,
            delta, config=config, contracts=contracts,
            posting_collector_registry=posting_registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("pending", result["status"])
        self.assertIn("authenticated build", result["reason"])

    def test_r7_002_forged_admission_patch_id_is_rejected(self):
        def actual_patch(request):
            if (request["operation"] == "TASK_COMMIT_RANGE" and
                    "claimed_patch_id" in request):
                return {"commits": [request["head_sha"]],
                        "patch_id": "a" * 40}
            return observed_git(request)

        register_integration_observer(
            "fixture-actual-patch", actual_patch, GIT_REGISTRY)
        forged = entry(11, SHA2, patch="f" * 40)
        with self.assertRaisesRegex(ValidationError, "patch"):
            self.store.admit(
                "AWF-EPIC", forged, "fixture-actual-patch", runner(),
                observed_at=NOW)

    def test_r7_004_conflict_at_target_instead_of_integration_tip_is_rejected(self):
        self.admit(entry(11, SHA2))

        def target_only_conflict(request):
            if request["operation"] != "MERGE_CONFLICT":
                return observed_git(request)
            result = {
                "status": "CONFLICT", "target_sha": request["target_sha"],
                "candidate_head_sha": request["head_sha"],
                "conflict_paths": ["src/conflict.py"],
                "commits": [request["head_sha"]],
            }
            if "integration_build_id" in request:
                result.update(
                    integration_build_id=request["integration_build_id"],
                    ordered_prefix=request["ordered_prefix"],
                    integration_result_commit_sha=request["target_sha"],
                    integration_result_tree_sha=request["target_sha"])
            return result

        register_integration_observer(
            "fixture-target-only-conflict", target_only_conflict, GIT_REGISTRY)
        with self.assertRaisesRegex(ValidationError, "integration tip"):
            self.store.conflict(
                "AWF-EPIC", entry(12, SHA3),
                "fixture-target-only-conflict", observed_at=NOW)

    def test_r7_005_caller_selected_removal_reason_is_rejected(self):
        self.admit(entry(11, SHA2))
        with self.assertRaisesRegex(ValidationError, "owner instruction|trigger"):
            self.store.remove(
                "AWF-EPIC", 11, runner(), reason="REMOVAL", observed_at=NOW)

    def test_r7_005_owner_instruction_authorizes_discretionary_removal(self):
        self.admit(entry(11, SHA2))
        result = self.store.remove(
            "AWF-EPIC", 11, runner(), reason="REMOVAL",
            trigger=instruction("REMOVE", {"pr_number": 11}, "remove-11"),
            observed_at=NOW)
        self.assertEqual("REBUILT", result["status"])

    def test_r7_006_altered_configuration_cannot_select_owner(self):
        altered = copy.deepcopy(PROJECT_CONFIG)
        altered["merge_gate"]["trusted_owner_ids"] = [9999]
        settings = {"test_command": "python -B -m unittest",
                    "stall_after_hours": 24, "max_stack_depth": 2}
        raw = render_owner_instruction("ENABLE_EPIC_MODE", "AWF-EPIC", settings, NOW)
        owner = {
            "owner_actor_id": 9999, "producer_id": "fixture-mode-provider",
            "source_event_id": "forged-owner-event",
            "provider_identity": PROVIDER_IDENTITY,
            "action": "ENABLE_EPIC_MODE", "epic": "AWF-EPIC",
            "payload": settings, "issued_at": NOW, "raw_body": raw,
            "raw_body_sha256": sha256(raw.encode("utf-8")),
        }
        owner["instruction_id"] = fingerprint(
            "integration_owner_instruction", owner)
        with self.assertRaisesRegex(ValidationError, "accepted project configuration"):
            resolve_mode(
                {"test_command": settings["test_command"]},
                owner_instruction=owner,
                app_binding={"producer_id": "fixture-mode-provider"},
                epic="AWF-EPIC", project_config=altered,
                mode_observer_registry=MODE_REGISTRY, now=NOW)

    def test_r7_006_altered_configuration_is_rechecked_before_admission(self):
        altered = copy.deepcopy(PROJECT_CONFIG)
        altered["merge_gate"]["trusted_owner_ids"] = [9999]
        self.store.project_config = altered
        with self.assertRaisesRegex(ValidationError, "accepted project configuration"):
            self.admit(entry(11, SHA2))

    def test_r7_007_caller_validation_timestamp_is_rejected(self):
        def actual_validation(request):
            result = observed_git(request)
            if request["operation"] == "CANDIDATE_VALIDATION":
                result["validated_at"] = NOW
            return result

        register_integration_observer(
            "fixture-git-provider", actual_validation, GIT_REGISTRY)
        forged = entry(11, SHA2, validated="2000-01-01T00:00:00Z")
        with self.assertRaisesRegex(ValidationError, "validation observation"):
            self.admit(forged)


if __name__ == "__main__":
    unittest.main()
