"""Twelve-lane routing and fresh-default regressions; no live execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load
from agentic.lane_labels import STREAM_LABELS
from agentic.model_routing import RoutingLedger, default_policy, policy_from_config, routing_context, select_route
from agentic.operating import initialize_operating, default_operating, plan_initialize_operating, validate_operating
from agentic.streams import LABELS


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

    def test_supported_operating_and_schema_labels_are_canonical(self):
        from agentic.operating import stream_labels
        for count in range(13):
            self.assertEqual(STREAM_LABELS[:count], stream_labels(count))
        self.assertEqual(("Z", "AA", "AB"), stream_labels(28)[25:])
        if not (ROOT / "scripts/generate_contracts.py").is_file():
            self.skipTest("source schema generator is not installed")
        spec = importlib.util.spec_from_file_location("lane_contracts", ROOT / "scripts/generate_contracts.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        streams = generator.catalog()["operating-config"]["properties"]["streams"]["properties"]
        self.assertEqual(set(STREAM_LABELS), set(streams) - {"count"})

    def test_planner_describes_all_supported_labels(self):
        from agentic.streams import render_markdown
        from test_operating_integration import OperatingPlanningTests
        planner = OperatingPlanningTests()
        planner.setUp()
        self.assertIn("streams A\u2013L", render_markdown(planner.plan()).decode("utf-8"))
        self.assertIn("A\u2013L", (ROOT / ".agentic/scripts/plan_streams.py").read_text(encoding="utf-8").splitlines()[1])

    def test_all_twelve_workers_and_critics_keep_safety_floors(self):
        self.assertEqual(tuple("ABCDEFGHIJKL"), STREAM_LABELS)
        self.assertEqual(STREAM_LABELS, LABELS)
        for stream in STREAM_LABELS:
            for role in ("worker", "fix", "critic"):
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
        for stream in ("M", "AB", "", None, 12, True, False, [], {}, ["L"], ("L",)):
            with self.subTest(stream=stream), self.assertRaises(ValidationError):
                self.route(self.request(stream=stream))

    def test_observed_stream_is_required_for_operating_worker_fix_and_critic(self):
        for role in ("worker", "fix", "critic"):
            request = self.request(role=role, worker_context_id="independent")
            del request["stream"]
            with self.subTest(role=role), self.assertRaisesRegex(ValidationError, "observed stream"):
                self.route(request)

    def test_unavailable_fresh_model_or_effort_refuses_without_fallback(self):
        for observation in ([], ["low", "medium"], {"status": "unavailable"}):
            self.capabilities["models"]["gpt-6.1-sol"] = observation
            for role in ("worker", "fix", "critic"):
                with self.subTest(observation=observation, role=role):
                    result = self.route(self.request(role=role, worker_context_id="independent"))
                    self.assertEqual("unavailable", result["status"])
                    self.assertEqual({"model": "gpt-6.1-sol", "reasoning_effort": "high"}, result["requested"])
                    self.assertIn("no fallback", result["reason"])

    def test_explicit_reviewer_pin_below_fresh_floor_refuses(self):
        for stream in STREAM_LABELS:
            for pair in ({"model": "gpt-6-luna", "reasoning_effort": "high"},
                         {"model": "gpt-6.1-sol", "reasoning_effort": "medium"}):
                value = deepcopy(self.value)
                value["streams"][stream]["reviewer"] = {**pair, "pinned": True}
                with self.subTest(stream=stream, pair=pair), self.assertRaises(ValidationError):
                    snapshot = validate_operating(value, self.gov)
                    self.route(self.request(stream=stream, role="critic", worker_context_id="independent"), snapshot)

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

    def test_missing_audited_operating_refuses_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".agentic").mkdir()
            (root / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(self.gov), encoding="utf-8")
            initialize_operating(root, self.gov)
            (root / "OPERATING_CONFIG.yaml").unlink()
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValidationError, "missing with retained audit history"):
                plan_initialize_operating(root, self.gov)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_planned_target_routing_is_scoped_and_explicit_paths_remain_independent(self):
        fresh_path = ROOT / ".agentic/defaults/routing.yaml"
        legacy_path = ROOT / ".agentic/tests/fixtures/routing/valid.yaml"
        legacy = default_policy(legacy_path)
        ambient = default_policy()
        with routing_context(legacy_path.read_bytes(), "preserved target routing"):
            self.assertEqual(legacy, default_policy())
            self.assertEqual(self.policy["role_defaults"], default_policy(fresh_path)["role_defaults"])
            with self.assertRaisesRegex(RuntimeError, "reset"):
                with routing_context(fresh_path.read_bytes(), "fresh target routing"):
                    self.assertEqual("gpt-6.1-sol", default_policy()["role_defaults"]["worker"]["model"])
                    raise RuntimeError("reset")
            self.assertEqual(legacy, default_policy())
        self.assertEqual(ambient, default_policy())

    def test_published_195_runtime_retains_original_upper_label_refusal(self):
        import hashlib
        import io
        import subprocess
        import zipfile
        from agentic.child_process import child_env
        from source_only import AWF_SOURCE_REPOSITORY
        if not AWF_SOURCE_REPOSITORY or not (ROOT / ".git").exists():
            self.skipTest("published historical Git source is absent from closed release archives")
        commit = "8ad399943c8f5e4f17db72f3e321b9365ee27782"
        pin = "1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7"
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", commit,
                                  ".agentic", "MANIFEST.json"], capture_output=True, timeout=30, env=child_env())
        self.assertEqual(0, archive.returncode, archive.stderr.decode("utf-8", errors="replace"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(archive.stdout)) as historical:
                historical.extractall(root)
            self.assertEqual(pin, hashlib.sha256((root / "MANIFEST.json").read_bytes()).hexdigest())
            script = """
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / '.agentic/lib'))
from agentic import ValidationError
from agentic.model_routing import default_policy, select_route
policy = default_policy(root / '.agentic/routing.yaml')
request = json.loads(sys.argv[2])
caps = {'models': {m: v['reasoning_efforts'] for m, v in policy['models'].items()}}
results = {}
for label in 'ABCDEFGHIJKL':
    try:
        results[label] = select_route(policy, {**request, 'stream': label}, caps)['status']
    except ValidationError as error:
        results[label] = str(error)
print(json.dumps(results))
"""
            done = subprocess.run([sys.executable, "-B", "-I", "-c", script, str(root), json.dumps(self.request())],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30, env=child_env())
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            results = json.loads(done.stdout)
            self.assertEqual({label: "ready" for label in STREAM_LABELS[:6]}, {label: results[label] for label in STREAM_LABELS[:6]})
            for label in STREAM_LABELS[6:]:
                self.assertIn("stream must be A through F", results[label])

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
