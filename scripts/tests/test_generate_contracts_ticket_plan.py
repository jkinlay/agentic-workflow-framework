"""AWF-115 generated ticket-plan contract regressions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_contracts  # noqa: E402


class TicketPlanGenerationTests(unittest.TestCase):
    def test_awf115_ticket_plan_is_first_generated_contract(self):
        catalog = generate_contracts.render_catalog()
        self.assertEqual("ticket-plan", next(iter(catalog)))

    def test_ticket_plan_has_no_generic_unfilled_template(self):
        self.assertNotIn("ticket-plan", generate_contracts.catalog())

    def test_checked_in_schema_is_generator_output(self):
        expected = generate_contracts.render_catalog()["ticket-plan"]
        self.assertEqual(
            expected,
            (ROOT / ".agentic/schemas/ticket-plan.schema.json").read_bytes(),
        )

    def test_generator_writes_ticket_plan_without_hand_authored_residue(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated_root = Path(temporary)
            with mock.patch.object(generate_contracts, "ROOT", generated_root):
                self.assertEqual(0, generate_contracts.main(()))
            actual = generated_root / ".agentic/schemas/ticket-plan.schema.json"
            self.assertTrue(actual.is_file())
            self.assertEqual(
                generate_contracts.render_catalog()["ticket-plan"],
                actual.read_bytes(),
            )
            self.assertEqual(
                "urn:awf:1.2:ticket-plan",
                json.loads(actual.read_text(encoding="utf-8"))["$id"],
            )


if __name__ == "__main__":
    unittest.main()
