"""A manifest-only installed tree must self-test with zero failures and errors.

Source-only tests must skip (via .agentic/tests/source_only.py), never error,
when scripts/, tools/, root documents and historical fixtures are absent.
"""
from __future__ import annotations
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402
from agentic.installer import install  # noqa: E402
from agentic.runtime_commands import installed_paths  # noqa: E402


MIN_INSTALL_TIMEOUT_SECONDS = 180


def load_self_test_module():
    path = ROOT / ".agentic/scripts/self_test.py"
    spec = importlib.util.spec_from_file_location("awf_installed_self_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_timeout_seconds(observed_seconds):
    """Add 50% measured headroom, with twice the former 90s limit."""
    return max(MIN_INSTALL_TIMEOUT_SECONDS, math.ceil(observed_seconds * 1.5))


INSTALL_TIMEOUT_SECONDS = install_timeout_seconds(96.3)


def run_with_timeout(command, *, timeout, **kwargs):
    def report_output(stdout, stderr):
        # TimeoutExpired may contain bytes even when text=True was requested.
        # Emit all progress before the temporary installed tree is removed.
        retained = []
        for label, output, stream in (
                ("stdout", stdout, sys.stdout), ("stderr", stderr, sys.stderr)):
            if output is None:
                continue
            if isinstance(output, bytes):
                output = output.decode(kwargs.get("encoding") or "utf-8", errors="replace")
            print(output, file=stream, end="", flush=True)
            retained.append(f"{label}:\n{output}")
        return "\n".join(retained)

    started = time.monotonic()
    try:
        completed = subprocess.run(command, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as failure:
        elapsed = time.monotonic() - started
        progress = report_output(failure.stdout, failure.stderr)
        raise AssertionError(
            f"subprocess timeout: elapsed={elapsed:.3f}s; limit={float(timeout):.3f}s; "
            f"command={command!r}\n{progress}"
        ) from failure
    report_output(completed.stdout, completed.stderr)
    return completed


class InstallSubprocessTimeoutTests(unittest.TestCase):
    def test_timeout_retains_child_progress_after_temporary_directory_cleanup(self):
        for as_bytes in (False, True):
            with self.subTest(as_bytes=as_bytes):
                stdout, stderr = self.child_progress()
                output = stdout.encode("utf-8") if as_bytes else stdout
                errors = stderr.encode("utf-8") if as_bytes else stderr
                failure = subprocess.TimeoutExpired(
                    ["synthetic-installed-self-test"], 3600,
                    output=output, stderr=errors)
                retained_out, retained_err = io.StringIO(), io.StringIO()
                with tempfile.TemporaryDirectory() as raw:
                    with redirect_stdout(retained_out), redirect_stderr(retained_err), \
                            mock.patch("subprocess.run", side_effect=failure) as run, \
                            mock.patch("time.monotonic", side_effect=[25.0, 3625.5]):
                        with self.assertRaises(AssertionError) as raised:
                            run_with_timeout(
                                failure.cmd, timeout=3600, capture_output=True,
                                text=True, cwd=Path(raw))
                self.assertFalse(Path(raw).exists())
                self.assertEqual(stdout, retained_out.getvalue())
                self.assertEqual(stderr, retained_err.getvalue())
                self.assertIn("elapsed=3600.500s; limit=3600.000s", str(raised.exception))
                self.assertIn(repr(failure.cmd), str(raised.exception))
                self.assertIn(stdout, str(raised.exception))
                self.assertIn(stderr, str(raised.exception))
                self.assertIs(failure, raised.exception.__cause__)
                self.assertEqual(3600, run.call_args.kwargs["timeout"])

    def test_success_reports_child_progress_after_temporary_directory_cleanup(self):
        for as_bytes in (False, True):
            with self.subTest(as_bytes=as_bytes):
                stdout, stderr = self.child_progress()
                completed = subprocess.CompletedProcess(
                    ["synthetic-installed-self-test"], 0,
                    stdout=stdout.encode("utf-8") if as_bytes else stdout,
                    stderr=stderr.encode("utf-8") if as_bytes else stderr)
                retained_out, retained_err = io.StringIO(), io.StringIO()
                with tempfile.TemporaryDirectory() as raw:
                    with redirect_stdout(retained_out), redirect_stderr(retained_err), \
                            mock.patch("subprocess.run", return_value=completed) as run:
                        result = run_with_timeout(
                            completed.args, timeout=3600, capture_output=True,
                            text=not as_bytes, cwd=Path(raw))
                self.assertFalse(Path(raw).exists())
                self.assertEqual(stdout, retained_out.getvalue())
                self.assertEqual(stderr, retained_err.getvalue())
                self.assertIs(completed, result)
                self.assertEqual(3600, run.call_args.kwargs["timeout"])

    @staticmethod
    def child_progress():
        stdout = json.dumps({"phase_timings_seconds": {
            "discovery": 8.183, "syntax": 2.666, "documentation": 0.037,
            "release_hygiene": 0.0}}) + "\n"
        stderr = "\n".join(json.dumps(event) for event in (
            {"event": "self_test_phase_complete", "phase": "discovery",
             "phase_elapsed_seconds": 8.183},
            {"event": "self_test_phase_start", "phase": "test_suite",
             "last_completed_phase": "release_hygiene"},
            {"event": "self_test_case_start", "phase": "test_suite",
             "current_test": "test_example.ExampleTests.test_portable_fixture"},
        )) + "\n"
        return stdout, stderr

    def test_ticket_plan_is_classified_as_a_non_form_schema(self):
        module = load_self_test_module()
        schemas = {
            "candidate", "project-config", "evidence-bundle", "operating-config",
            "ticket-plan", "worker-result",
        }
        self.assertEqual(
            {"worker-result"},
            module.unfilled_form_schema_names(schemas, {"operating-config"}),
        )

    def test_timeout_limit_has_documented_margin_over_observed_controller_load(self):
        self.assertEqual(180, install_timeout_seconds(96.3))
        self.assertEqual(300, install_timeout_seconds(200.0))

    def test_timeout_failure_reports_elapsed_time_and_limit(self):
        failure = subprocess.TimeoutExpired(["synthetic-installed-self-test"], 600)
        with mock.patch("subprocess.run", side_effect=failure), \
                mock.patch("time.monotonic", side_effect=[25.0, 625.5]):
            with self.assertRaisesRegex(
                    AssertionError, r"elapsed=600\.500s; limit=600\.000s"):
                run_with_timeout(["synthetic-installed-self-test"], timeout=600)


def git(cwd, *args):
    run_with_timeout(
        ["git", "-c", "user.name=AWF Test", "-c", "user.email=awf@example.invalid",
         "-C", str(cwd), *args], check=True, capture_output=True,
        env=child_env(dict(os.environ)), timeout=INSTALL_TIMEOUT_SECONDS)


@unittest.skipUnless((ROOT / "MANIFEST.json").is_file(), "source repository only")
class InstalledSelfTestTests(unittest.TestCase):
    def assert_unborn_head(self, project):
        head = run_with_timeout(
            ["git", "-C", str(project), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, check=False, env=child_env(dict(os.environ)),
            timeout=INSTALL_TIMEOUT_SECONDS)
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
            run_with_timeout(
                [base, "-m", "venv", *copies, str(project / ".agentic/.venv")], check=True,
                capture_output=True, env=env, timeout=600)
            run_with_timeout(
                [str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
                 "--require-hashes", "--only-binary=:all:", "-r",
                 str(project / ".agentic/requirements.lock")], check=True,
                capture_output=True, env=env, timeout=1200)
            git(project, "init", "-q", "-b", "main")
            report_path = Path(raw) / "self-test.json"
            # Keep this immediately before self-test: the installed-runtime
            # acceptance case is specifically an initialized, unborn repository.
            self.assert_unborn_head(project)
            done = run_with_timeout(
                [str(interpreter), "-B", str(project / ".agentic/scripts/self_test.py"),
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
