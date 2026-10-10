"""A manifest-only installed tree must self-test with zero failures and errors.

Source-only tests must skip (via .agentic/tests/source_only.py), never error,
when scripts/, tools/, root documents and historical fixtures are absent.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402
from agentic.installer import install  # noqa: E402
from agentic.runtime_commands import installed_paths  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=AWF Test", "-c", "user.email=awf@example.invalid",
                    "-C", str(cwd), *args], check=True, capture_output=True,
                   env=child_env(dict(os.environ)))


@unittest.skipUnless((ROOT / "MANIFEST.json").is_file(), "source repository only")
class InstalledSelfTestTests(unittest.TestCase):
    def assert_unborn_head(self, project):
        head = subprocess.run(["git", "-C", str(project), "rev-parse", "--verify", "HEAD"],
                              capture_output=True, text=True, check=False,
                              env=child_env(dict(os.environ)))
        self.assertNotEqual(0, head.returncode, "fixture unexpectedly has a commit")

    def test_manifest_only_install_self_test_has_no_failures_or_errors(self):
        pin = hashlib.sha256((ROOT / "MANIFEST.json").read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="awf-installed-self-test-") as raw:
            project = Path(raw) / "project"
            project.mkdir()
            install(ROOT, project, pin, mode="install", discover=False)
            self.assertFalse((project / "scripts").exists())
            self.assertFalse((project / "MANIFEST.json").exists())
            # An adopter may own a root MANIFEST.json; it must not make the
            # installed project look like the AWF source repository.
            (project / "MANIFEST.json").write_text('{"adopter": "owned"}\n', encoding="utf-8")
            # AWF-39: a fresh installed project may not yet have owner root
            # attributes. Its diagnostics tests must be host-independent.
            self.assertFalse((project / ".gitattributes").exists())
            # Managed runtime: .agentic/.venv with the hash-locked dependencies.
            _, interpreter, _ = installed_paths(project)
            base = getattr(sys, "_base_executable", None) or sys.executable
            env = child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            # POSIX venvs symlink python by default, which the heavy-validation
            # controller refuses as an alias; Windows venvs always copy.
            copies = [] if os.name == "nt" else ["--copies"]
            subprocess.run([base, "-m", "venv", *copies, str(project / ".agentic/.venv")], check=True,
                           capture_output=True, env=env, timeout=600)
            subprocess.run([str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
                            "--require-hashes", "--only-binary=:all:", "-r",
                            str(project / ".agentic/requirements.lock")], check=True,
                           capture_output=True, env=env, timeout=1200)
            git(project, "init", "-q", "-b", "main")
            report_path = Path(raw) / "self-test.json"
            # Keep this immediately before self-test: the installed-runtime
            # acceptance case is specifically an initialized, unborn repository.
            self.assert_unborn_head(project)
            done = subprocess.run([str(interpreter), "-B", str(project / ".agentic/scripts/self_test.py"),
                                   "--checks-only", "--report", str(report_path)], cwd=project,
                                  capture_output=True, text=True, timeout=3600,
                                  env=child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1")))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            failed = [line for line in str(report.get("test_log", "")).splitlines()
                      if line.startswith(("FAIL:", "ERROR:"))]
            tests = report.get("tests", {})
            self.assertEqual((0, 0), (tests.get("failures"), tests.get("errors")), failed or done.stderr[-3000:])
            self.assertEqual("PASS", report["status"], report.get("error"))
            self.assertEqual(0, done.returncode)
            skipped = tests.get("skipped", [])
            for item in skipped:
                self.assertTrue(item.get("test"), item)
                self.assertTrue(item.get("reason"), item)
            for module in ("test_continuous_controller", "test_heavy_validation"):
                matching = [item for item in skipped if module in item["test"]]
                self.assertEqual(1, len(matching), skipped)
                self.assertIn("committed HEAD", matching[0]["reason"])

    def test_unborn_head_guard_rejects_existing_head(self):
        completed = subprocess.CompletedProcess(
            ["git", "rev-parse", "--verify", "HEAD"], 0, stdout="a" * 40 + "\n", stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaisesRegex(AssertionError, "unexpectedly has a commit"):
                self.assert_unborn_head(Path("installed-project"))


if __name__ == "__main__":
    unittest.main()
