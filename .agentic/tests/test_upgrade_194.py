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
from upgrade_fixtures import materialize, verify_materialized  # noqa: E402


class Upgrade194Tests(unittest.TestCase):
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
        finally:
            for path in destination.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)
            import shutil
            shutil.rmtree(destination)


if __name__ == "__main__":
    unittest.main()
