import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/tests"))
from test_bootstrap_configuration import installed_wheel, wheel_lock


class FreshBootstrapIntegrationTests(unittest.TestCase):
    def test_cli_install_into_repository_without_awf_succeeds_and_verifies(self):
        with tempfile.TemporaryDirectory(prefix="awf-fresh-bootstrap-") as raw:
            fixture_source = Path(raw) / "source"
            shutil.copytree(ROOT, fixture_source, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".tmp", "tmp", ".tmp-tests"))
            wheelhouse = Path(raw) / "wheelhouse"
            wheelhouse.mkdir()
            entries = []
            for name in ("PyYAML", "jsonschema", "attrs", "jsonschema-specifications",
                         "referencing", "rpds-py", "typing-extensions"):
                version, wheel = installed_wheel(name)
                (wheelhouse / (name.replace("-", "_") + "-" + version + "-py3-none-any.whl")).write_bytes(wheel)
                entries.append((name, version, wheel))
            (fixture_source / ".agentic/requirements.lock").write_bytes(wheel_lock(entries))
            built = subprocess.run([sys.executable, "-B", "scripts/build_release.py", "--manifest-only"],
                                   cwd=fixture_source, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, built.returncode, built.stderr)
            manifest = fixture_source / "MANIFEST.json"
            pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
            destination = Path(raw) / "empty-project"
            destination.mkdir()
            self.assertFalse((destination / ".agentic").exists())
            command = [sys.executable, "-B", str(fixture_source / "scripts/bootstrap_project.py"),
                "--mode", "install", "--dest", str(destination),
                "--expected-manifest-sha256", pin, "--runtime-wheelhouse", str(wheelhouse),
                "--github-repo", "example-owner/example-project", "--repository-id", "54321",
                "--project-name", "example-project", "--project-short-name", "EX",
                "--test-command", "python -m unittest", "--codeowner", "@example-owner",
                "--default-branch", "main"]
            env = os.environ.copy()
            env.update(TMP=raw, TEMP=raw, PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run(command, cwd=fixture_source, capture_output=True, text=True, env=env, timeout=90)
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
