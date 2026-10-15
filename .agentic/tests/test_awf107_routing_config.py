"""AWF-107 operator-owned routing configuration regressions."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.model_routing import (RoutingConfigError, default_policy,
                                   load_routing, policy_from_config,
                                   select_route, validate_policy)
from agentic.installer import OPERATOR_CONFIG_PATHS, managed
from agentic.operating import validate_operating

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / ".agentic/tests/fixtures/routing"


def load_script(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class RoutingConfigTests(unittest.TestCase):
    def test_valid_fixture_exposes_catalog_tiers_and_defaults(self):
        routing = load_routing(FIXTURES / "valid.yaml")
        self.assertEqual(1, routing["schema_version"])
        self.assertEqual(["gpt-5.6-luna", "gpt-5.6-sol", "gpt-6.1-sol"],
                         list(routing["catalog"]))
        self.assertEqual("gpt-6.1-sol", routing["tiers"]["3"]["critic"]["model"])
        self.assertEqual("gpt-5.6-luna", routing["role_defaults"]["worker"]["model"])

    def test_named_invalid_fixtures_fail_closed_at_the_key(self):
        cases = {
            "unknown-model.yaml": "tiers.1.writer.model",
            "unsupported-effort.yaml": "tiers.1.critic.effort",
            "missing-tier.yaml": "tiers.3",
            "missing-critic.yaml": "tiers.2.critic",
            "extra-key.yaml": "unexpected",
            "bad-schema-version.yaml": "schema_version",
            "floor-violation.yaml": "risk_route",
            "malformed.yaml": "$",
        }
        for name, key in cases.items():
            with self.subTest(name=name), self.assertRaises(RoutingConfigError) as caught:
                load_routing(FIXTURES / name)
            self.assertIn(f": {key}: ", str(caught.exception))

    def test_missing_file_fails_closed_with_path_and_root_key(self):
        missing = FIXTURES / "does-not-exist.yaml"
        with self.assertRaises(RoutingConfigError) as caught:
            load_routing(missing)
        self.assertTrue(str(caught.exception).startswith(str(missing) + ": $: "))

    def test_closed_shapes_types_and_catalog_efforts(self):
        valid = json.loads((FIXTURES / "valid.yaml").read_text(encoding="utf-8"))
        mutations = [
            ("missing top key", lambda v: v.pop("tiers"), "tiers"),
            ("extra tier", lambda v: v["tiers"].update({"4": deepcopy(v["tiers"]["3"])}), "tiers.4"),
            ("extra tier role", lambda v: v["tiers"]["1"].update({"specialist": deepcopy(v["tiers"]["1"]["critic"])}), "tiers.1.specialist"),
            ("missing tier writer", lambda v: v["tiers"]["1"].pop("writer"), "tiers.1.writer"),
            ("extra default role", lambda v: v["role_defaults"].update({"fix": deepcopy(v["role_defaults"]["worker"])}), "role_defaults.fix"),
            ("extra route key", lambda v: v["simple_worker"].update({"pinned": True}), "simple_worker.pinned"),
            ("wrong route type", lambda v: v.update({"risk_route": []}), "risk_route"),
            ("empty efforts", lambda v: v["catalog"]["gpt-5.6-luna"].update({"efforts": []}), "catalog.gpt-5.6-luna.efforts"),
            ("duplicate effort", lambda v: v["catalog"]["gpt-5.6-luna"].update({"efforts": ["low", "low"]}), "catalog.gpt-5.6-luna.efforts"),
            ("unknown effort", lambda v: v["catalog"]["gpt-5.6-luna"].update({"efforts": ["tiny"]}), "catalog.gpt-5.6-luna.efforts"),
        ]
        with tempfile.TemporaryDirectory(prefix="awf-routing-config-") as temporary:
            for label, mutate, key in mutations:
                value = deepcopy(valid)
                mutate(value)
                path = Path(temporary) / (label.replace(" ", "-") + ".yaml")
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(label=label), self.assertRaises(RoutingConfigError) as caught:
                    load_routing(path)
                self.assertIn(f": {key}: ", str(caught.exception))

    def test_one_line_switch_takes_effect_on_reload(self):
        original = (FIXTURES / "valid.yaml").read_text(encoding="utf-8")
        switched = original.replace(
            '"worker": {"model": "gpt-5.6-luna", "effort": "medium"}',
            '"worker": {"model": "gpt-5.6-sol", "effort": "medium"}',
            1,
        )
        with tempfile.TemporaryDirectory(prefix="awf-routing-switch-") as temporary:
            path = Path(temporary) / "routing.yaml"
            path.write_text(original, encoding="utf-8")
            before = load_routing(path)
            before_policy = default_policy(path)
            path.write_text(switched, encoding="utf-8")
            after = load_routing(path)
            after_policy = default_policy(path)
        self.assertEqual("gpt-5.6-luna", before["role_defaults"]["worker"]["model"])
        self.assertEqual("gpt-5.6-sol", after["role_defaults"]["worker"]["model"])
        self.assertEqual("gpt-5.6-luna", before_policy["role_defaults"]["worker"]["model"])
        self.assertEqual("gpt-5.6-sol", after_policy["role_defaults"]["worker"]["model"])

    def test_one_line_switch_reaches_unpinned_default_operating_worker(self):
        original = (FIXTURES / "valid.yaml").read_text(encoding="utf-8")
        switched = original.replace(
            '"worker": {"model": "gpt-5.6-luna", "effort": "medium"}',
            '"worker": {"model": "gpt-5.6-sol", "effort": "medium"}',
            1,
        )
        governance = json.loads(
            (ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
        operating = json.loads(
            (ROOT / "OPERATING_CONFIG.yaml").read_text(encoding="utf-8"))
        request = {
            "ticket_id": "AWF-107",
            "agent_id": "writer-a",
            "phase": "implementation",
            "task_class": "ordinary",
            "role": "worker",
            "complexity": "medium",
            "risk": "medium",
            "uncertainty": "medium",
            "verification": "strong",
            "risk_flags": [],
            "stream": "A",
        }
        with tempfile.TemporaryDirectory(prefix="awf-routing-switch-") as temporary:
            path = Path(temporary) / "routing.yaml"
            path.write_text(switched, encoding="utf-8")
            with patch("agentic.model_routing._default_routing_path", return_value=path):
                policy = policy_from_config(governance)
                snapshot = validate_operating(operating, governance)
                capabilities = {
                    "models": {
                        model: metadata["reasoning_efforts"]
                        for model, metadata in policy["models"].items()
                    }
                }
                route = select_route(
                    policy, request, capabilities,
                    operating=snapshot, governance=governance)
        self.assertEqual(
            ("gpt-5.6-sol", "medium"),
            (route["model"], route["reasoning_effort"]),
        )

    def test_one_line_switch_does_not_change_rules_policy_or_manifest_check(self):
        generator = load_script("awf107_generate_rules", "scripts/generate_rules_activation.py")
        builder = load_script("awf107_build_release", "scripts/build_release.py")
        original = (FIXTURES / "valid.yaml").read_text(encoding="utf-8")
        switched = original.replace(
            '"worker": {"model": "gpt-5.6-luna", "effort": "medium"}',
            '"worker": {"model": "gpt-5.6-sol", "effort": "medium"}',
            1,
        )
        fixed = b"immutable release member\n"
        files = {".agentic/routing.yaml": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                 "fixed.txt": hashlib.sha256(fixed).hexdigest()}
        manifest, _ = builder.render_manifests(files)

        class FakeTree:
            def __init__(self, routing):
                self.routing = routing.encode("utf-8")

            def read(self, name):
                if name == ".agentic/routing.yaml":
                    return self.routing
                if name == ".agentic/tests/fixtures/routing/valid.yaml":
                    return original.encode("utf-8")
                return fixed

        with tempfile.TemporaryDirectory(prefix="awf-routing-switch-") as temporary:
            path = Path(temporary) / "routing.yaml"
            path.write_text(original, encoding="utf-8")
            with patch("agentic.model_routing._default_routing_path", return_value=path):
                before = json.loads(generator.render())["record"]["configuration"]["policy_sha256"]
            path.write_text(switched, encoding="utf-8")
            with patch("agentic.model_routing._default_routing_path", return_value=path):
                after = json.loads(generator.render())["record"]["configuration"]["policy_sha256"]
        self.assertEqual(before, after)
        with patch.object(builder, "release_paths",
                          return_value=[".agentic/routing.yaml", "fixed.txt"]):
            builder.verify_virtual_release(FakeTree(original), manifest)
            builder.verify_virtual_release(FakeTree(switched), manifest)
        self.assertIn(".agentic/routing.yaml", OPERATOR_CONFIG_PATHS)
        self.assertFalse(managed(".agentic/routing.yaml"))

    def test_default_policy_translates_operator_schema_to_existing_shape(self):
        policy = default_policy(FIXTURES / "valid.yaml")
        validate_policy(policy)
        self.assertEqual(list(load_routing(FIXTURES / "valid.yaml")["catalog"]),
                         policy["model_order"])
        self.assertEqual({"model": "gpt-6.1-sol", "reasoning_effort": "medium"},
                         policy["review_floor"])
        self.assertNotIn("tiers", policy)

    def test_existing_inline_policy_validation_does_not_reload_operator_file(self):
        policy = default_policy(FIXTURES / "valid.yaml")
        with patch("agentic.model_routing._default_routing_path",
                   return_value=FIXTURES / "absent.yaml"):
            self.assertIs(validate_policy(policy), policy)

    def test_routing_reference_preserves_project_owned_non_route_policy(self):
        policy = default_policy(FIXTURES / "valid.yaml")
        configured = {
            "routing": ".agentic/routing.yaml",
            **{key: deepcopy(policy[key]) for key in (
                "high_risk_flags", "agent_overrides", "ticket_overrides",
                "escalation", "budgets", "adaptive", "reconciliation",
            )},
        }
        configured["budgets"]["max_runs_per_ticket"] = 7
        config = {"execution": {"model_routing": configured}}
        with patch("agentic.model_routing._default_routing_path",
                   return_value=FIXTURES / "valid.yaml"):
            derived = policy_from_config(config)
        self.assertEqual(7, derived["budgets"]["max_runs_per_ticket"])
        self.assertEqual("gpt-6.1-sol", derived["role_defaults"]["critic"]["model"])

    def test_routing_reference_rejects_unknown_project_policy_key(self):
        config = {"execution": {"model_routing": {
            "routing": ".agentic/routing.yaml", "unexpected": True}}}
        with patch("agentic.model_routing._default_routing_path",
                   return_value=FIXTURES / "valid.yaml"):
            with self.assertRaisesRegex(ValidationError, "unexpected model_routing reference field"):
                policy_from_config(config)


if __name__ == "__main__":
    unittest.main()
