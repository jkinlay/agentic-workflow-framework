"""Twelve-lane routing and fresh-default regressions; no live execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from agentic import ValidationError
from agentic.canonical import load
from agentic.lane_labels import STREAM_LABELS
from agentic.model_routing import RoutingLedger, default_policy, policy_from_config, select_route
from agentic.operating import default_operating, plan_initialize_operating, validate_operating
from agentic.streams import LABELS

ROOT = Path(__file__).resolve().parents[2]


class TwelveStreamTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy(ROOT / ".agentic/defaults/routing.yaml")
        self.gov = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.gov["execution"].update(max_lanes=12, max_parallel_tickets=12)
        self.gov["execution"]["model_routing"] = deepcopy(self.policy)
        for role in self.policy["role_defaults"]:
            self.gov["execution"]["roles"][role]["approved_model_ids"] = list(self.policy["models"])
        self.policy = policy_from_config(self.gov)
        self.value = default_operating(self.policy, count=12)
        self.operating = validate_operating(self.value, self.gov)
        self.capabilities = {"models": {m: v["reasoning_efforts"] for m, v in self.policy["models"].items()}}

    def request(self, **updates):
        value = dict(ticket_id="FIX-1", role="worker", agent_id="writer", context_id="writer-context",
                     phase="implementation", task_class="unit-fix", complexity="medium", risk="low",
                     uncertainty="low", verification="strong", risk_flags=[], reservation_tokens=100,
                     reservation_cost_microusd=None, stream="L")
        value.update(updates)
        return value

    def route(self, request, operating=None):
        return select_route(self.policy, request, self.capabilities,
                            operating=operating or self.operating, governance=self.gov)

    def test_all_twelve_workers_and_critics_keep_safety_floors(self):
        self.assertEqual(tuple("ABCDEFGHIJKL"), STREAM_LABELS)
        self.assertEqual(STREAM_LABELS, LABELS)
        for stream in STREAM_LABELS:
            for role in ("worker", "critic"):
                with self.subTest(stream=stream, role=role):
                    request = self.request(stream=stream, role=role, worker_context_id="other-context")
                    route = self.route(request)
                    self.assertEqual("ready", route["status"])
                    self.assertEqual(("gpt-6.1-sol", "high"), (route["model"], route["reasoning_effort"]))

    def test_simple_route_requires_each_eligibility_condition(self):
        simple = self.request(complexity="low")
        selected = self.route(simple)
        self.assertEqual(("gpt-6-luna", "high"), (selected["model"], selected["reasoning_effort"]))
        for change in ({"risk":"medium"}, {"complexity":"medium"}, {"uncertainty":"medium"},
                       {"verification":"limited"}, {"risk_flags":["permissions"]}):
            with self.subTest(change=change):
                self.assertEqual("gpt-6.1-sol", self.route({**simple, **change})["model"])

    def test_invalid_labels_remain_closed(self):
        for stream in ("M", "AB", "", None, 12):
            with self.subTest(stream=stream), self.assertRaises(ValidationError):
                self.route(self.request(stream=stream))

    def test_upper_lanes_are_draining_after_capacity_reduction(self):
        reduced = deepcopy(self.value)
        reduced["streams"]["count"] = 3
        snapshot = validate_operating(reduced, self.gov)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "routing.sqlite")
            for stream in "GHIJKL":
                with self.subTest(stream=stream):
                    request = self.request(stream=stream)
                    self.assertTrue(self.route(request, snapshot)["draining"])
                    with self.assertRaisesRegex(ValidationError, "draining"):
                        ledger.reserve("project", self.policy, request, self.capabilities,
                                       operating=snapshot, governance=self.gov)

    def test_fresh_operating_template_and_legacy_missing_file_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            fresh = plan_initialize_operating(temporary, self.gov, initial_operating=self.value)
            self.assertEqual(12, fresh["inspection"]["streams"])
        legacy = deepcopy(self.gov)
        legacy["execution"].pop("max_lanes")
        legacy["execution"]["max_parallel_tickets"] = 6
        with tempfile.TemporaryDirectory() as temporary:
            old = plan_initialize_operating(temporary, legacy)
            self.assertEqual(3, old["inspection"]["streams"])
            self.assertEqual(6, old["inspection"]["effective_ceiling"])

    def test_release_routing_and_generated_new_project_defaults_agree(self):
        if not (ROOT / "scripts/generate_examples.py").is_file():
            self.skipTest("source generator is not installed")
        spec = importlib.util.spec_from_file_location("fresh_examples", ROOT / "scripts/generate_examples.py")
        if spec is None or spec.loader is None:
            self.skipTest("source generator is not installed")
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            generator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(generator)
            config = generator.config(False, self.policy, fresh_defaults=True)
        finally:
            sys.path.remove(str(ROOT / "scripts"))
        self.assertEqual(12, config["execution"]["max_lanes"])
        self.assertEqual(12, config["execution"]["max_parallel_tickets"])
        for role in self.policy["role_defaults"]:
            self.assertEqual("gpt-6.1-sol", config["execution"]["roles"][role]["model"])
            self.assertEqual("high", config["execution"]["roles"][role]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
