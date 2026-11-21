"""AWF-307 source configuration forms are checked outside installed tests."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.canonical import load

KEY = "shared_owner_automation_actor_id"


class SharedOwnerSourceFormsTests(unittest.TestCase):
    def test_shipped_and_generated_forms_default_to_null(self):
        for relative in ("PROJECT_CONFIG.yaml", "templates/source-config/PROJECT_CONFIG.yaml"):
            with self.subTest(path=relative):
                gate = load(ROOT / ".agentic" / relative)["merge_gate"]
                self.assertIn(KEY, gate)
                self.assertIsNone(gate[KEY])


if __name__ == "__main__":
    unittest.main()
