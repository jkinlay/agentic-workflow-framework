import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.continuous_controller import ContinuousControllerStore, production_controller_cycle
from test_publication_readiness import publication_config, publication_observation


NOW = "2026-10-15T08:00:00Z"
BINDING = {"project_id": "project-1", "repository_id": "101", "scope_sha256": "a" * 64}
REPOSITORY = {
    "repository_root": Path(__file__).resolve().parents[2],
    "repository_head_sha": "a" * 40,
    "repository_tree_sha": "b" * 40,
}
COUNTS = {"required": 1, "completed": 0, "acceptable": 0,
          "failed": 0, "stale": 0, "outstanding": 1}


def ticket(name, priority, *, role="writer", stage="implementation"):
    return {
        "ticket": name, "priority": priority, "disposition": "ELIGIBLE",
        "actor": "controller", "reason": "eligible", "next_action": "continue " + name,
        "resume_trigger": "completion", "paths": ["fixture-" + name.lower() + ".txt"],
        "dependencies_satisfied": True, "budget_available": True, "cap_available": True,
        "review_independent": True, "exact_tuple": "base:a/head:b/tree:c/contract:d/review:e",
        "activity": "work " + name, "verification_gate": "PENDING",
        "reviewer_completion": dict(COUNTS), "open_findings": 0,
        "jira_status": "In Progress", "dispatch_role": role, "lifecycle_stage": stage,
    }


def observation(items):
    return {"source": "host_observation", "observed_at": NOW, "binding": dict(BINDING),
            "complete": True, "inventory_sha256": fingerprint(
                "controller-inventory", {"binding": BINDING, "tickets": items}),
            "tickets": items}


def receipt(payload):
    keys = ["dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce",
            "prepared_at", "begun_at", "role", "lifecycle_stage"]
    return {key: payload[key] for key in keys} | {
        "status": "ACCEPTED", "observed_at": NOW,
        "session_id": "session-" + payload["ticket"],
    }


class ContinuousControllerPlanningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf89-planning-")
        self.addCleanup(temporary.cleanup)
        self.store = ContinuousControllerStore(
            Path(temporary.name) / "controller.sqlite3", ["A", "B"])
        self.repository_patch = patch(
            "agentic.continuous_controller.canonical_repository_paths",
            side_effect=lambda items, *_: (items, {"repository_root": "/fixture",
                                                   "head_sha": "a" * 40,
                                                   "tree_sha": "b" * 40,
                                                   "git_path_count": 0}))
        self.repository_patch.start()
        self.addCleanup(self.repository_patch.stop)
        self.skill_patch = patch(
            "agentic.codex_skill.observe_codex_awf_skills",
            return_value={"format": "awf-codex-skill-selection-1", "expected_version": "1.9.4",
                          "project_root": "/fixture", "pinned_skill_path": "/fixture/skill",
                          "launch_directory": "/fixture", "visible_skills": ["awf"],
                          "status": "PASS", "problems": []})
        self.skill_patch.start()
        self.addCleanup(self.skill_patch.stop)

    def cycle(self, items, *, capacity=2, dispatch=None):
        calls = []
        dispatch = dispatch or (lambda payload: calls.append(dict(payload)) or receipt(payload))
        result = production_controller_cycle(
            self.store, now=NOW, host_capacity=capacity, inventory_binding=BINDING,
            **REPOSITORY, observe_inventory=lambda: observation(items),
            dispatch_ticket=dispatch,
            observe_dispatch=lambda payload: self.fail("unexpected reconciliation"),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                                           "status": "DELIVERED", "observed_at": NOW},
            publication_config=publication_config(),
            observe_publication=lambda item: publication_observation(item["ticket"], NOW))
        return calls, result

    def test_production_cycle_dispatches_mixed_writer_and_critic_per_ticket(self):
        calls, result = self.cycle([
            ticket("AWF-101", 1, role="writer", stage="implementation"),
            ticket("AWF-102", 2, role="critic", stage="independent_review"),
        ])
        self.assertEqual([(item["ticket"], item["role"], item["lifecycle_stage"])
                          for item in calls], [
            ("AWF-101", "writer", "implementation"),
            ("AWF-102", "critic", "independent_review"),
        ])
        self.assertEqual(len(result["dispatch_receipts"]), 2)

    def test_role_authority_mismatch_fails_closed_independent_of_capacity(self):
        with self.assertRaisesRegex(ValidationError, "role|lifecycle"):
            self.cycle([ticket("AWF-103", 1, role="critic", stage="implementation")],
                       capacity=2,
                       dispatch=lambda payload: self.fail("mismatched role dispatched"))

    def test_host_capacity_limits_homogeneous_roles_independent_of_role_authority(self):
        calls, result = self.cycle([
            ticket("AWF-104", 1), ticket("AWF-105", 2),
        ], capacity=1)
        self.assertEqual([(item["ticket"], item["role"]) for item in calls],
                         [("AWF-104", "writer")])
        self.assertEqual(len(result["dispatch_receipts"]), 1)


if __name__ == "__main__":
    unittest.main()
