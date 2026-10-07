"""Smoke-run the AC42 harness off Windows; the gate itself runs on Windows."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402

FIXTURES = ROOT / ".agentic/tests/fixtures/upgrades/versions.json"


@unittest.skipUnless(FIXTURES.is_file(), "historical upgrade fixtures are source-repository only")
class AC42HarnessSmokeTests(unittest.TestCase):
    def test_smoke_run_passes_every_covered_clause_except_the_skipped_self_test(self):
        with tempfile.TemporaryDirectory(prefix="awf-ac42-test-") as raw:
            evidence = Path(raw) / "evidence.json"
            done = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/ac42_e2e.py"), "--work",
                                   str(Path(raw) / "work"), "--evidence", str(evidence), "--skip-self-test"],
                                  capture_output=True, text=True, timeout=600,
                                  env=child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1")))
            report = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertNotIn("error", report, done.stderr)
            results = {name: row["result"] for name, row in report["rows"].items()}
            self.assertEqual(results.pop("self_test"), "NOT_COVERED")
            self.assertEqual(results.pop("read_only_external_resource_admission"), "NOT_COVERED")
            self.assertEqual(set(results.values()), {"PASS"}, report["rows"])
            self.assertEqual(report["rows"]["active_single_status_run"]["evidence"]["project_state"], "ACTIVE")
            self.assertEqual(report["result"], "FAIL")  # never a gate pass without the self-test
            self.assertFalse(report["gate_eligible"])
            self.assertEqual(done.returncode, 1)


if __name__ == "__main__":
    unittest.main()
