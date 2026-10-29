import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic.installer import install  # noqa: E402
from source_only import skip_unless_source_assets  # noqa: E402
from upgrade_fixtures import materialize, verify_materialized  # noqa: E402


class Upgrade194Tests(unittest.TestCase):
    @skip_unless_source_assets("needs excluded historical upgrade fixtures", ".agentic/tests/fixtures/upgrades/versions.json")
    def test_193_dry_run_reaches_plan_without_conflicts(self):
        destination = Path(tempfile.gettempdir()) / ("awf-upgrade-194-" + uuid.uuid4().hex)
        destination.mkdir()
        try:
            materialize("1.9.3", destination)
            verify_materialized("1.9.3", destination)
            pin = __import__("hashlib").sha256((ROOT / "MANIFEST.json").read_bytes()).hexdigest()
            result = install(ROOT, destination, pin, mode="upgrade", configure=True,
                             discover=False, dry_run=True)
            self.assertEqual(result["status"], "PLAN")
            self.assertEqual(result["upgrade"]["detected_version"], "1.9.3")
            self.assertEqual(result["conflicts"], [])
            diff = result["upgrade"]["configuration_diff_total"]
            self.assertIn('-    "expected_workflow_version": "1.9.3"', diff)
            self.assertIn('+    "expected_workflow_version": "1.9.6"', diff)
            self.assertEqual(diff.count("\n-"), 2)
            self.assertEqual(diff.count("\n+"), 3)  # header, scalar and additive policy
            self.assertIn('+    "risk_tiers": {tier3_review: {roles: [critic, specialist], '
                          'findings: blocking, max_rounds: 3},', diff)
            steps = result["upgrade"]["steps"]
            self.assertEqual([(step["from"], step["to"]) for step in steps],
                             [("1.9.3", "1.9.4"), ("1.9.4", "1.9.5"), ("1.9.5", "1.9.6")])
            historical_diff = steps[0]["configuration_diff"]
            self.assertIn('-    "expected_workflow_version": "1.9.3"', historical_diff)
            self.assertIn('+    "expected_workflow_version": "1.9.4"', historical_diff)
            self.assertEqual(historical_diff.count("\n-"), 1)
            self.assertEqual(historical_diff.count("\n+"), 2)
            self.assertNotIn("tier3_review", historical_diff)
            self.assertNotIn("migrate_1_9_3_to_1_9_4", json.dumps(result["upgrade"]))
        finally:
            for path in destination.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)
            import shutil
            shutil.rmtree(destination)


if __name__ == "__main__":
    unittest.main()
