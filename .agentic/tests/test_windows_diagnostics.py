"""K8-K11 Windows diagnostics, honest preflight, progress and output tests."""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.host_preflight import preflight
from agentic.runtime_commands import command_catalog, installed_paths, powershell_quote

SELF_TEST_SPEC = importlib.util.spec_from_file_location("awf_self_test_progress", ROOT / ".agentic/scripts/self_test.py")
self_test = importlib.util.module_from_spec(SELF_TEST_SPEC)
SELF_TEST_SPEC.loader.exec_module(self_test)


@contextmanager
def temporary_directory(*, prefix="", require_external=False):
    base = Path(os.environ["AWF_TEST_TMP"]).resolve() if os.environ.get("AWF_TEST_TMP") else None
    if require_external and base is not None and base.is_relative_to(ROOT):
        raise unittest.SkipTest("AC36 SKIP: sandbox temp shim is inside the checkout; an external writable temp root is required")
    if base is None:
        with tempfile.TemporaryDirectory(prefix=prefix) as folder:
            if require_external and Path(folder).resolve().is_relative_to(ROOT):
                raise unittest.SkipTest("AC36 SKIP: sandbox temp shim is inside the checkout; an external writable temp root is required")
            yield folder
        return
    candidate = (base / (prefix + uuid.uuid4().hex)).resolve()
    if not candidate.is_relative_to(base) or candidate == base:
        raise RuntimeError("Invalid bounded test temp target")
    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if powershell is None:
            raise unittest.SkipTest("Sandbox temp ACL shim requires PowerShell")
        env = os.environ.copy()
        env["AWF_CASE_TMP"] = str(candidate)
        created = subprocess.run([powershell, "-NoProfile", "-Command",
                                  "New-Item -ItemType Directory -LiteralPath $env:AWF_CASE_TMP | Out-Null"],
                                 env=env, capture_output=True, text=True)
        if created.returncode:
            raise unittest.SkipTest("Sandbox temp ACL shim could not create a writable test directory")
    else:
        candidate.mkdir(parents=True)
    try:
        yield str(candidate)
    finally:
        if os.name == "nt":
            subprocess.run([powershell, "-NoProfile", "-Command",
                            "Remove-Item -LiteralPath $env:AWF_CASE_TMP -Recurse -Force"],
                           env=env, capture_output=True, text=True)
        else:
            shutil.rmtree(candidate)


def probe(executable, code, output, category=None):
    return {"executable": executable, "exit_code": code, "output": output,
            "diagnostic_category": category or ("OK" if code == 0 else "NONZERO_EXIT")}


def policy_output(value="Bypass"):
    scopes = ("MachinePolicy", "UserPolicy", "Process", "CurrentUser", "LocalMachine")
    return "\n".join(scope + "=" + value for scope in scopes)


class RuntimeCommandTests(unittest.TestCase):
    def test_paths_are_built_from_canonical_components_and_quote_spaces(self):
        folder = ROOT.parent / "example repo"
        root, interpreter, entry = installed_paths(folder, platform="nt")
        self.assertEqual(interpreter, root / ".agentic" / ".venv" / "Scripts" / "python.exe")
        self.assertEqual(entry, root / ".agentic" / "scripts" / "workflow.py")
        report = command_catalog(root, platform="nt")
        self.assertEqual({"adoption", "verification", "validation", "status", "operating"},
                         {item["purpose"] for item in report["commands"]})
        for item in report["commands"]:
            self.assertIn(powershell_quote(interpreter), item["command"])
            self.assertIn(powershell_quote(entry), item["command"])
            self.assertNotIn("\n", item["command"])
            self.assertNotIn("[", item["command"])
        self.assertEqual("'example ''repo'", powershell_quote("example 'repo"))

    def test_doctor_cli_emits_the_same_structured_commands_and_paths(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
             "--root", str(ROOT), "doctor", "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(2, completed.returncode, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(command_catalog(ROOT)["commands"], report["commands"])
        self.assertEqual(str(installed_paths(ROOT)[1]), report["runtime"]["interpreter"])
        self.assertEqual(str(installed_paths(ROOT)[2]), report["runtime"]["entry_point"])

    @unittest.skipUnless(os.name == "nt", "AC36 real PowerShell execution requires a Windows host")
    def test_ac36_generated_commands_execute_unchanged_in_real_powershell(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("AC36 SKIP: powershell.exe is unavailable on this Windows host")
        manifest = ROOT / "MANIFEST.json"
        import hashlib
        pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
        with temporary_directory(prefix="example repo ", require_external=True) as folder:
            project = Path(folder) / "fixture project"
            project.mkdir()
            bootstrap = [sys.executable, "-B", str(ROOT / "scripts/bootstrap_project.py"),
                         "--dest", str(project), "--expected-manifest-sha256", pin,
                         "--github-repo", "example-owner/example-repo", "--repository-id", "24680",
                         "--project-name", "Example Repo", "--project-short-name", "EX",
                         "--test-command", "python -m unittest", "--default-branch", "main"]
            installed = subprocess.run(bootstrap, cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(0, installed.returncode, installed.stderr + installed.stdout)
            report = command_catalog(project, platform="nt")
            for item in report["commands"]:
                with self.subTest(purpose=item["purpose"]):
                    completed = subprocess.run([powershell, "-NoProfile", "-Command", item["command"]],
                                               cwd=project, capture_output=True, text=True, timeout=90)
                    self.assertIn(completed.returncode, item["expected_exit_codes"],
                                  completed.stderr + completed.stdout)

    def test_documentation_lint_rejects_each_damaged_command_form(self):
        damaged = [".agentic" + ".venv", "workflow" + "\\" + ".py",
                   "[python workflow.py](command-target)"]
        problems = [problem for line in damaged
                    for problem in self_test.documentation_command_line_problems(line)]
        self.assertEqual(3, len(problems))


class HonestPreflightTests(unittest.TestCase):
    def run_scenario(self, policy_probe):
        def fake_run(arguments, cwd=None):
            if arguments[0] == "powershell":
                return policy_probe
            key = arguments[-1]
            return probe("git.exe", 0, "true" if key == "core.longpaths" else "false")
        with patch("agentic.host_preflight.run", side_effect=fake_run):
            return {item["check"]: item for item in preflight(ROOT, platform="nt")["rows"]}

    def test_ac37_module_load_failure_nonzero_valid_output_missing_timeout_and_valid(self):
        cases = [
            (probe("powershell.exe", 1, "security module could not be loaded", "MODULE_LOAD_FAILURE"), "WARN", "MODULE_LOAD_FAILURE"),
            (probe("powershell", None, "unavailable", "MISSING_EXECUTABLE"), "SKIP", "MISSING_EXECUTABLE"),
            (probe("powershell.exe", None, "deadline", "TIMEOUT"), "SKIP", "TIMEOUT"),
            (probe("powershell.exe", 9, policy_output(), "NONZERO_EXIT"), "WARN", "NONZERO_EXIT"),
            (probe("powershell.exe", 0, policy_output()), "PASS", "OK"),
        ]
        for observed, status, category in cases:
            with self.subTest(status=status, category=category):
                row = self.run_scenario(observed)["powershell_execution_policy"]
                self.assertEqual(status, row["status"])
                self.assertEqual(category, row["diagnostic_category"])
                self.assertEqual(observed["exit_code"], row["exit_code"])
                self.assertEqual(observed["executable"], row["executable"])

    def test_zero_exit_with_wrong_policy_row_shape_warns(self):
        row = self.run_scenario(probe("powershell.exe", 0, "CurrentUser=Bypass"))["powershell_execution_policy"]
        self.assertEqual("WARN", row["status"])
        self.assertEqual("INVALID_OUTPUT", row["diagnostic_category"])

    def test_every_nonzero_child_observation_is_never_pass(self):
        def fake_run(arguments, cwd=None):
            return probe(str(arguments[0]), 7, policy_output() if arguments[0] == "powershell" else "true")
        original_read_text = Path.read_text
        def read_text(path, *args, **kwargs):
            if Path(path).resolve() == (ROOT / ".gitattributes").resolve():
                return "* text=auto filter=lfs\n"
            return original_read_text(path, *args, **kwargs)
        with patch("agentic.host_preflight.run", side_effect=fake_run), patch.object(Path, "read_text", read_text):
            rows = {item["check"]: item for item in preflight(ROOT, platform="nt")["rows"]}
        for name in ("core.longpaths", "powershell_execution_policy", "line_endings", "git_lfs"):
            self.assertNotEqual("PASS", rows[name]["status"], rows[name])
            self.assertEqual(7, rows[name]["exit_code"])


def synthetic_pass():
    return None


def synthetic_interrupt():
    raise KeyboardInterrupt()


class SelfTestProgressTests(unittest.TestCase):
    def test_ac38_start_is_immediate_and_heartbeat_is_bounded(self):
        stream = io.StringIO()
        progress = self_test.SelfTestProgress(stream=stream, interval=0.01)
        progress.start()
        time.sleep(0.035)
        progress.stop()
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual("self_test_start", events[0]["event"])
        self.assertTrue(any(item["event"] == "self_test_heartbeat" for item in events[1:]))

    def test_ac38_synthetic_final_result_is_deterministic_apart_from_timings(self):
        outputs = []
        for _ in range(2):
            progress = self_test.SelfTestProgress(stream=io.StringIO(), interval=1)
            progress.start()
            progress.begin_phase("test_suite")
            result, log = self_test.execute_test_suite(
                unittest.TestSuite([unittest.FunctionTestCase(synthetic_pass)]), progress)
            progress.complete_phase("test_suite")
            progress.stop()
            outputs.append(json.dumps({"run": result.testsRun, "successful": result.wasSuccessful(),
                                       "failures": len(result.failures), "errors": len(result.errors),
                                       "log": log}, sort_keys=True).encode("ascii"))
        self.assertEqual(outputs[0], outputs[1])

    def test_ac38_interruption_names_last_phase_and_current_test_without_pass(self):
        progress = self_test.SelfTestProgress(stream=io.StringIO(), interval=1)
        progress.start()
        progress.begin_phase("documentation")
        progress.complete_phase("documentation")
        progress.begin_phase("test_suite")
        with self.assertRaises(KeyboardInterrupt):
            self_test.execute_test_suite(
                unittest.TestSuite([unittest.FunctionTestCase(synthetic_interrupt)]), progress)
        evidence = {"status": "INTERRUPTED", "last_completed_phase": progress.last_completed_phase,
                    "current_phase": progress.current_phase,
                    "current_test": progress.current_test or progress.last_test}
        progress.stop()
        self.assertEqual("documentation", evidence["last_completed_phase"])
        self.assertEqual("test_suite", evidence["current_phase"])
        self.assertIn("synthetic_interrupt", evidence["current_test"])
        self.assertNotEqual("PASS", evidence["status"])


class EncodingOutputTests(unittest.TestCase):
    def run_redirected(self, *arguments):
        scratch = ROOT / ".tmp-tests"
        scratch.mkdir(exist_ok=True)
        identity = uuid.uuid4().hex
        output = scratch / (identity + "-output.bin")
        errors = scratch / (identity + "-errors.bin")
        try:
            env = os.environ.copy()
            env.pop("PYTHONIOENCODING", None)
            if os.name == "nt":
                env["PYTHONLEGACYWINDOWSSTDIO"] = "1"
            else:
                env["PYTHONIOENCODING"] = "cp1252"
            with output.open("wb") as stdout, errors.open("wb") as stderr:
                completed = subprocess.run([sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                                            "--root", str(ROOT), *arguments], cwd=ROOT, stdout=stdout,
                                           stderr=stderr, env=env, timeout=60)
            return completed.returncode, output.read_bytes(), errors.read_bytes()
        finally:
            for path in (output, errors):
                if path.is_file():
                    path.unlink()

    def test_ac37_ascii_safe_json_and_ascii_status_survive_redirected_legacy_encoding(self):
        code, raw, errors = self.run_redirected("status", "--json")
        self.assertIn(code, (0, 2), errors.decode("ascii", errors="replace"))
        parsed = json.loads(raw.decode("ascii"))
        self.assertIn("project_state", parsed)
        code, raw, errors = self.run_redirected("status")
        self.assertIn(code, (0, 2), errors.decode("ascii", errors="replace"))
        first = raw.splitlines()[0]
        first.decode("ascii")
        self.assertNotIn(b"?", first)


if __name__ == "__main__":
    unittest.main()
