"""Keep the permanent adversarial inventory registered in the short pre-controller gate."""
import importlib.util
import json
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
