"""Keep the permanent adversarial inventory registered in the short pre-controller gate."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_publication_pre_controller.py"
spec = importlib.util.spec_from_file_location("run_publication_pre_controller", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load pre-controller gate script")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class PreControllerRegistryTests(unittest.TestCase):
    def test_every_inventory_evidence_module_is_registered(self):
        inventory = json.loads(
            (ROOT / ".agentic" / "validation" / "five-slot-adversarial-regressions.json")
            .read_text(encoding="utf-8")
        )
        evidence_paths = {row["evidence_path"] for row in inventory["regressions"]}
        self.assertFalse(evidence_paths - gate.INVENTORY_MODULES.keys())

    def test_pr35_activation_findings_resolve_once_through_pre_controller_registry(self):
        inventory = json.loads(
            (ROOT / ".agentic" / "validation" / "five-slot-adversarial-regressions.json")
            .read_text(encoding="utf-8")
        )
        expected = {
            "PR35-C01": "RulesActivationTests.test_ac58_numeric_repository_id_is_bound_before_and_after_owner_action",
            "PR35-C02": "RulesActivationTests.test_ac46_partial_pull_request_ruleset_merge_method_conflict_blocks",
            "PR35-C03": "PreControllerRegistryTests.test_pr35_activation_findings_resolve_once_through_pre_controller_registry",
        }
        loader = unittest.TestLoader()
        for finding_id, test_id in expected.items():
            with self.subTest(finding_id=finding_id):
                rows = [row for row in inventory["regressions"]
                        if row["source_finding_id"] == finding_id]
                self.assertEqual(1, len(rows))
                row = rows[0]
                self.assertEqual(test_id, row["test_id"])
                module = gate.INVENTORY_MODULES.get(row["evidence_path"])
                self.assertIsNotNone(module)
                self.assertEqual(1, loader.loadTestsFromName(test_id, module).countTestCases())
        self.assertIs(
            gate.INVENTORY_MODULES[".agentic/tests/test_rules_activation.py"],
            gate.test_rules_activation,
        )
        self.assertIs(
            gate.INVENTORY_MODULES["scripts/tests/test_pre_controller_registry.py"],
            sys.modules[__name__],
        )


if __name__ == "__main__":
    unittest.main()
