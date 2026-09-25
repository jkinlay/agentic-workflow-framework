import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FreshBootstrapIntegrationTests(unittest.TestCase):
    def test_cli_install_into_repository_without_awf_succeeds_and_verifies(self):
        manifest = ROOT / "MANIFEST.json"
        pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="awf-fresh-bootstrap-") as raw:
            destination = Path(raw) / "empty-project"
            destination.mkdir()
            self.assertFalse((destination / ".agentic").exists())
            command = [sys.executable, "-B", str(ROOT / "scripts/bootstrap_project.py"),
                "--mode", "install", "--dest", str(destination),
                "--expected-manifest-sha256", pin,
                "--github-repo", "example-owner/example-project", "--repository-id", "54321",
                "--project-name", "example-project", "--project-short-name", "EX",
                "--test-command", "python -m unittest", "--codeowner", "@example-owner",
                "--default-branch", "main"]
            env = os.environ.copy()
            env.update(TMP=raw, TEMP=raw, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "CONFIGURED")
            self.assertTrue(report["installed"])
            checks = report["post_install_checks"]
            self.assertEqual([item["exit_code"] for item in checks], [0, 0])
            self.assertTrue(checks[0]["output"]["integrity_valid"])
            self.assertEqual(checks[1]["output"]["status"], "ACCEPTED")
            self.assertEqual(checks[0]["output"]["source_manifest_sha256"], pin)


if __name__ == "__main__":
    unittest.main()
