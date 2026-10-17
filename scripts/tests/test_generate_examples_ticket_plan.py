"""AWF-107 keeps the draft ticket-plan schema out of generated examples."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_examples  # noqa: E402


class GenerateExamplesTicketPlanTests(unittest.TestCase):
    def test_generate_uses_the_non_ticket_plan_catalog(self):
        calls = []
        legacy_catalog = generate_examples.catalog

        def future_catalog(*, include_ticket_plan):
            calls.append(include_ticket_plan)
            return legacy_catalog(include_ticket_plan=False)

        with tempfile.TemporaryDirectory(prefix="awf-generate-examples-") as raw:
            with patch.object(generate_examples, "catalog", future_catalog):
                generate_examples.generate(Path(raw), ROOT)
        self.assertEqual([False], calls)


if __name__ == "__main__":
    unittest.main()
