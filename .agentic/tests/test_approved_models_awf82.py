"""AWF-82 regressions for the shipped gpt-6.1-sol allowlist entry."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.model_routing import policy_from_config, select_route

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-6.1-sol"
ROLLBACK_MODEL = "gpt-5.6-sol"
ROLES = ("controller", "worker", "critic", "specialist")


def request():
    return {
        "ticket_id": "AWF-82",
        "role": "worker",
        "agent_id": "awf82-writer",
        "context_id": "awf82-context",
        "phase": "implementation",
        "task_class": "governance-change",
        "complexity": "medium",
        "risk": "low",
        "uncertainty": "low",
        "verification": "strong",
        "risk_flags": [],
        "reservation_tokens": 1000,
        "reservation_cost_microusd": None,
    }


class ApprovedModelsAwf82Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load(ROOT / ".agentic" / "PROJECT_CONFIG.yaml")
        Contracts(ROOT / ".agentic" / "schemas").validate("project-config", cls.config)

    def policy(self):
        return policy_from_config(deepcopy(self.config))

    def test_shipped_config_accepts_gpt_6_1_sol_at_medium_and_high(self):
        execution = self.config["execution"]
        routing = execution["model_routing"]
        self.assertIn(MODEL, routing["model_order"])
        self.assertEqual(["medium", "high"], routing["models"][MODEL]["reasoning_efforts"])
        for role in ROLES:
            with self.subTest(role=role):
                self.assertIn(MODEL, execution["roles"][role]["approved_model_ids"])
                self.assertIn(MODEL, routing["role_allowed_models"][role])

        for effort in ("medium", "high"):
            with self.subTest(effort=effort):
                policy = self.policy()
                policy["ticket_overrides"] = {
                    "AWF-82": {"worker": {"model": MODEL, "reasoning_effort": effort}}
                }
                route = select_route(
                    policy,
                    request(),
                    {"models": {MODEL: ["medium", "high"]}},
                )
                self.assertEqual("ready", route["status"])
                self.assertEqual((MODEL, effort), (route["model"], route["reasoning_effort"]))

    def test_unlisted_model_is_refused_by_routing_validation(self):
        policy = self.policy()
        policy["ticket_overrides"] = {
            "AWF-82": {"worker": {"model": "gpt-9-unlisted", "reasoning_effort": "high"}}
        }
        with self.assertRaisesRegex(ValidationError, "unknown model"):
            select_route(policy, request(), {"models": {"gpt-9-unlisted": ["high"]}})

    def test_gpt_6_1_sol_ultra_effort_is_refused(self):
        policy = self.policy()
        self.assertIn(MODEL, policy["models"])
        policy["ticket_overrides"] = {
            "AWF-82": {"worker": {"model": MODEL, "reasoning_effort": "ultra"}}
        }
        with self.assertRaisesRegex(ValidationError, "unsupported policy model/effort combination"):
            select_route(policy, request(), {"models": {MODEL: ["ultra"]}})

    def test_gpt_5_6_sol_remains_allowed_and_defaults_are_unchanged(self):
        execution = self.config["execution"]
        routing = execution["model_routing"]
        expected_defaults = {
            "controller": (ROLLBACK_MODEL, "high"),
            "worker": ("gpt-5.6-luna", "medium"),
            "critic": (ROLLBACK_MODEL, "high"),
            "specialist": (ROLLBACK_MODEL, "high"),
        }
        for role, expected in expected_defaults.items():
            with self.subTest(role=role):
                self.assertIn(ROLLBACK_MODEL, execution["roles"][role]["approved_model_ids"])
                self.assertIn(ROLLBACK_MODEL, routing["role_allowed_models"][role])
                self.assertEqual(expected, (
                    execution["roles"][role]["model"],
                    execution["roles"][role]["reasoning_effort"],
                ))
                self.assertEqual(expected, (
                    routing["role_defaults"][role]["model"],
                    routing["role_defaults"][role]["reasoning_effort"],
                ))


if __name__ == "__main__":
    unittest.main()
