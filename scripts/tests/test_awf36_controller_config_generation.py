"""Red-first schema-generation coverage for AWF-36 controller switches."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_contracts  # noqa: E402


TARGET_SWITCHES = (
    "dispatch_enabled",
    "auto_dispatch",
    "auto_request_critic",
    "auto_resume_amendments",
)
BOOLEAN_SCHEMA = {"type": "boolean"}


class ControllerConfigGenerationTests(unittest.TestCase):
    def test_generator_unlocks_each_controller_switch_as_a_boolean(self):
        properties = generate_contracts.catalog()["project-config"]["properties"][
            "controller"
        ]["properties"]
        for key in TARGET_SWITCHES:
            with self.subTest(key=key):
                self.assertEqual(BOOLEAN_SCHEMA, properties[key])

    def test_checked_in_schema_contains_the_generated_boolean_contract(self):
        schema = json.loads(
            (ROOT / ".agentic/schemas/project-config.schema.json").read_text(
                encoding="utf-8"
            )
        )
        properties = schema["properties"]["controller"]["properties"]
        for key in TARGET_SWITCHES:
            with self.subTest(key=key):
                self.assertEqual(BOOLEAN_SCHEMA, properties[key])


if __name__ == "__main__":
    unittest.main()
