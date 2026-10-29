import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/tests"))
from test_bootstrap_configuration import installed_wheel, wheel_lock


MIN_INSTALL_TIMEOUT_SECONDS = 180
OBSERVED_CONTROLLER_LOAD_SECONDS = 96.3


def install_timeout_seconds(observed_seconds):
    """Add 50% measured headroom, with twice the former 90s limit."""
    return max(MIN_INSTALL_TIMEOUT_SECONDS, math.ceil(observed_seconds * 1.5))


INSTALL_TIMEOUT_SECONDS = install_timeout_seconds(OBSERVED_CONTROLLER_LOAD_SECONDS)


def run_with_timeout(command, *, timeout, **kwargs):
    started = time.monotonic()
    try:
        return subprocess.run(command, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as failure:
        elapsed = time.monotonic() - started
        raise AssertionError(
            f"subprocess timeout: elapsed={elapsed:.3f}s; limit={float(timeout):.3f}s; "
            f"command={command!r}"
        ) from failure


class InstallSubprocessTimeoutTests(unittest.TestCase):
    def test_timeout_limit_has_documented_margin_over_observed_controller_load(self):
        self.assertEqual(180, install_timeout_seconds(96.3))
        self.assertEqual(300, install_timeout_seconds(200.0))

    def test_timeout_failure_reports_elapsed_time_and_limit(self):
        failure = subprocess.TimeoutExpired(["synthetic-install"], 180)
        with mock.patch("subprocess.run", side_effect=failure), \
                mock.patch("time.monotonic", side_effect=[10.0, 190.25]):
            with self.assertRaisesRegex(
                    AssertionError, r"elapsed=180\.250s; limit=180\.000s"):
                run_with_timeout(["synthetic-install"], timeout=180)


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
            built = run_with_timeout(
                [sys.executable, "-B", "scripts/build_release.py", "--manifest-only"],
                cwd=fixture_source, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, built.returncode, built.stderr)
            manifest = fixture_source / "MANIFEST.json"
            pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
            # Mutable source operating choices must not seed a new installation.
            (fixture_source / "OPERATING_CONFIG.yaml").write_text(
                '{"streams":{"count":1}}', encoding="utf-8")
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
            result = run_with_timeout(
                command, cwd=fixture_source, capture_output=True, text=True, env=env,
                timeout=INSTALL_TIMEOUT_SECONDS)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "CONFIGURED")
            self.assertTrue(report["installed"])
            checks = report["post_install_checks"]
            self.assertEqual([item["exit_code"] for item in checks], [0, 0])
            self.assertTrue(checks[0]["output"]["integrity_valid"])
            self.assertEqual(checks[1]["output"]["status"], "ACCEPTED")
            self.assertEqual(checks[0]["output"]["source_manifest_sha256"], pin)
            operating = json.loads((destination / "OPERATING_CONFIG.yaml").read_text())
            self.assertEqual(12, operating["streams"]["count"])
            routing = json.loads((destination / ".agentic/routing.yaml").read_text())
            for role in ("worker", "controller", "critic", "specialist"):
                self.assertEqual({"model":"gpt-6.1-sol", "effort":"high"}, routing["role_defaults"][role])
            self.assertEqual({"model":"gpt-6-luna", "effort":"high"}, routing["simple_worker"])



if __name__ == "__main__":
    unittest.main()
