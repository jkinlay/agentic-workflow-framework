"""K8-K11 Windows diagnostics, honest preflight, progress and output tests."""
from __future__ import annotations

import hashlib
import importlib.util
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import venv
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import host_preflight
from agentic.host_preflight import preflight
from agentic import ValidationError
from agentic.contracts import Contracts
from agentic.runtime_commands import command_catalog, installed_paths, powershell_quote

SELF_TEST_SPEC = importlib.util.spec_from_file_location("awf_self_test_progress", ROOT / ".agentic/scripts/self_test.py")
self_test = importlib.util.module_from_spec(SELF_TEST_SPEC)
SELF_TEST_SPEC.loader.exec_module(self_test)

_RUNTIME_DISTRIBUTIONS = (
    "PyYAML", "jsonschema", "attrs", "jsonschema-specifications",
    "referencing", "rpds-py", "typing-extensions",
)


def _ac36_bootstrap_command(source, project, manifest_pin, wheelhouse):
    """Build the one AC36 bootstrap command; the offline wheelhouse is mandatory."""
    source, project, wheelhouse = map(Path, (source, project, wheelhouse))
    if not all(path.is_absolute() for path in (source, project, wheelhouse)):
        raise ValueError("AC36 fixture paths must be absolute")
    return [sys.executable, "-B", str(source / "scripts/bootstrap_project.py"),
            "--dest", str(project), "--expected-manifest-sha256", manifest_pin,
            "--runtime-wheelhouse", str(wheelhouse),
            "--github-repo", "example-owner/example-repo", "--repository-id", "24680",
            "--project-name", "Example Repo", "--project-short-name", "EX",
            "--test-command", "python -m unittest", "--default-branch", "main"]


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
        try:
            candidate.mkdir(parents=True)
        except OSError:
            if powershell is None:
                raise unittest.SkipTest("Sandbox temp ACL shim requires PowerShell")
            env = os.environ.copy()
            env["AWF_CASE_TMP"] = str(candidate)
            created = subprocess.run([powershell, "-NoProfile", "-Command",
                                      "New-Item -ItemType Directory -LiteralPath $env:AWF_CASE_TMP | Out-Null"],
                                     env=env, capture_output=True, text=True)
            if created.returncode:
                raise unittest.SkipTest("AC36 SKIP: restricted-token sandbox denied the external temp fixture")
    else:
        candidate.mkdir(parents=True)
    try:
        yield str(candidate)
    finally:
        if os.name == "nt":
            try:
                shutil.rmtree(candidate)
            except OSError:
                if powershell is not None:
                    env = os.environ.copy()
                    env["AWF_CASE_TMP"] = str(candidate)
                    subprocess.run([powershell, "-NoProfile", "-Command",
                                    "Remove-Item -LiteralPath $env:AWF_CASE_TMP -Recurse -Force"],
                                   env=env, capture_output=True, text=True)
        else:
            shutil.rmtree(candidate)


def probe(executable, code, output, category=None):
    return {"executable": executable, "exit_code": code, "output": output,
            "diagnostic_category": category or ("OK" if code == 0 else "NONZERO_EXIT")}


def policy_output(value="Bypass", **overrides):
    scopes = ("MachinePolicy", "UserPolicy", "Process", "CurrentUser", "LocalMachine")
    return "\n".join(scope + "=" + overrides.get(scope, value) for scope in scopes)


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
        for unsafe in ("two\nlines", "two\rlines", "nul\x00value"):
            with self.subTest(unsafe=repr(unsafe)), self.assertRaisesRegex(ValueError, "one line"):
                powershell_quote(unsafe)

    def test_doctor_claims_only_the_literal_transport_it_generates(self):
        transport = command_catalog(ROOT, platform="nt")["long_argument_transport"]
        self.assertEqual("literal_argv", transport["method"])
        self.assertIn("PowerShell single-quote", transport["detail"])
        self.assertIn("No generic JSON carrier", transport["detail"])
        self.assertIn("no generic json carrier", transport["detail"].lower())
        self.assertIn("response-file", transport["detail"])

    def test_ac36_bootstrap_command_cannot_drop_the_offline_wheelhouse(self):
        base = (ROOT.parent / "AC36 fixture with spaces").resolve()
        command = _ac36_bootstrap_command(
            base / "release source", base / "project", "a" * 64,
            base / "offline wheelhouse")
        self.assertEqual(1, command.count("--runtime-wheelhouse"))
        index = command.index("--runtime-wheelhouse")
        self.assertEqual(str(base / "offline wheelhouse"), command[index + 1])
        self.assertLess(index, command.index("--github-repo"))
        with self.assertRaisesRegex(ValueError, "must be absolute"):
            _ac36_bootstrap_command(Path("relative-source"), base / "project", "a" * 64,
                                    base / "offline wheelhouse")

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

    def test_doctor_contract_requires_exactly_one_command_for_each_purpose(self):
        contracts = Contracts(ROOT / ".agentic" / "schemas")
        report = command_catalog(ROOT)
        contracts.validate("doctor-output", report)
        duplicated = copy.deepcopy(report)
        duplicated["commands"][-1]["purpose"] = "adoption"
        with self.assertRaises(ValidationError):
            contracts.validate("doctor-output", duplicated)

    @unittest.skipUnless(os.name == "nt", "AC36 real PowerShell execution requires a Windows host")
    def test_ac36_generated_commands_execute_unchanged_in_noninstalling_powershell_fixture(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("AC36 SKIP: powershell.exe is unavailable on this Windows host")
        with temporary_directory(prefix="example repo ", require_external=True) as folder:
            project = Path(folder) / "fixture project"
            scripts = project / ".agentic" / "scripts"
            scripts.mkdir(parents=True)
            venv.EnvBuilder(with_pip=False).create(project / ".agentic" / ".venv")
            entry = scripts / "workflow.py"
            entry.write_text(
                "import argparse,json\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--root',required=True)\n"
                "p.add_argument('command')\n"
                "p.add_argument('remainder',nargs='*')\n"
                "a=p.parse_args()\n"
                "print(json.dumps({'command':a.command,'remainder':a.remainder,'root':a.root},sort_keys=True))\n",
                encoding="utf-8", newline="\n")
            report = command_catalog(project, platform="nt")
            self.assertTrue(report["runtime"]["interpreter_exists"])
            self.assertTrue(report["runtime"]["entry_point_exists"])
            reached = set()
            for item in report["commands"]:
                with self.subTest(purpose=item["purpose"]):
                    completed = subprocess.run(
                        [powershell, "-NoProfile", "-Command", item["command"]],
                        cwd=project, capture_output=True, text=True, timeout=30)
                    self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
                    observed = json.loads(completed.stdout)
                    self.assertEqual(str(project.resolve()), observed["root"])
                    reached.add(item["purpose"])
            self.assertEqual({"adoption", "verification", "validation", "status", "operating"}, reached)

    @unittest.skipUnless(os.name == "nt", "AC36 real PowerShell execution requires a Windows host")
    def test_ac36_generated_commands_execute_unchanged_in_real_powershell(self):
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("AC36 SKIP: powershell.exe is unavailable on this Windows host")
        from test_bootstrap_configuration import installed_wheel, wheel_lock
        with temporary_directory(prefix="example repo ", require_external=True) as folder:
            fixture_source = Path(folder) / "release source"
            shutil.copytree(ROOT, fixture_source, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".tmp", "tmp", ".tmp-tests", ".venv"))
            wheelhouse = Path(folder) / "offline wheelhouse"
            wheelhouse.mkdir()
            entries, expected_artifacts = [], {}
            for name in _RUNTIME_DISTRIBUTIONS:
                version, wheel = installed_wheel(name)
                filename = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
                (wheelhouse / filename).write_bytes(wheel)
                entries.append((name, version, wheel))
                expected_artifacts[name] = hashlib.sha256(wheel).hexdigest()
            lock = wheel_lock(entries)
            self.assertNotEqual((ROOT / ".agentic/requirements.lock").read_bytes(), lock)
            (fixture_source / ".agentic/requirements.lock").write_bytes(lock)
            built = subprocess.run(
                [sys.executable, "-B", "scripts/build_release.py", "--manifest-only"],
                cwd=fixture_source, capture_output=True, text=True, timeout=120)
            self.assertEqual(0, built.returncode, built.stderr + built.stdout)
            manifest = fixture_source / "MANIFEST.json"
            pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
            project = Path(folder) / "fixture project"
            project.mkdir()
            bootstrap = _ac36_bootstrap_command(fixture_source, project, pin, wheelhouse)
            installed = subprocess.run(bootstrap, cwd=fixture_source, capture_output=True,
                                       text=True, timeout=180)
            self.assertEqual(0, installed.returncode, installed.stderr + installed.stdout)
            installation = json.loads(installed.stdout)
            self.assertEqual("CONFIGURED", installation["status"])
            self.assertEqual(expected_artifacts, installation["runtime"]["artifact_sha256"])
            self.assertEqual(hashlib.sha256(lock).hexdigest(),
                             installation["runtime"]["requirements_lock_sha256"])
            self.assertEqual(lock, (project / ".agentic/requirements.lock").read_bytes())
            report = command_catalog(project, platform="nt")
            self.assertTrue(report["runtime"]["interpreter_exists"])
            self.assertTrue(report["runtime"]["entry_point_exists"])
            reached = set()
            for item in report["commands"]:
                with self.subTest(purpose=item["purpose"]):
                    completed = subprocess.run([powershell, "-NoProfile", "-Command", item["command"]],
                                               cwd=project, capture_output=True, text=True, timeout=90)
                    self.assertIn(completed.returncode, item["expected_exit_codes"],
                                  completed.stderr + completed.stdout)
                    self.assertTrue(completed.stdout.strip(),
                                    "Generated command returned no evidence: " + item["command"])
                    if item["purpose"] == "adoption":
                        self.assertEqual("awf-host-preflight-1", json.loads(completed.stdout)["format"])
                    elif item["purpose"] == "verification":
                        self.assertIs(True, json.loads(completed.stdout)["integrity_valid"])
                    elif item["purpose"] == "validation":
                        self.assertEqual("ACCEPTED", json.loads(completed.stdout)["status"])
                    elif item["purpose"] == "status":
                        self.assertTrue(completed.stdout.splitlines()[0].startswith("AWF 1.9.3: "))
                    elif item["purpose"] == "operating":
                        self.assertIn("operating configuration", completed.stdout)
                        self.assertIn("Options: keep", completed.stdout)
                    else:
                        self.fail("Unexpected generated command purpose: " + item["purpose"])
                    reached.add(item["purpose"])
            self.assertEqual({"adoption", "verification", "validation", "status", "operating"}, reached)

    def test_documentation_lint_rejects_each_damaged_command_form(self):
        damaged = [".agentic" + ".venv", "workflow" + "\\" + ".py",
                   "[python workflow.py](command-target)"]
        problems = [problem for line in damaged
                    for problem in self_test.documentation_command_line_problems(line)]
        self.assertEqual(3, len(problems))

    def test_documentation_lint_accepts_the_current_docs_prompts_and_templates(self):
        checked, problems = self_test.documentation_command_problems(ROOT)
        self.assertGreater(checked, 0)
        self.assertEqual([], problems)


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

    def test_zero_exit_capture_diagnostics_are_never_pass(self):
        for category in ("INVALID_ENCODING", "OUTPUT_TRUNCATED", "OUTPUT_CAPTURE_FAILURE"):
            with self.subTest(category=category):
                observed = probe("powershell.exe", 0, policy_output(), category)
                row = self.run_scenario(observed)["powershell_execution_policy"]
                self.assertEqual("WARN", row["status"])
                self.assertEqual(category, row["diagnostic_category"])

    def test_effective_policy_uses_scope_precedence_not_any_restrictive_lower_scope(self):
        output = policy_output("Undefined", CurrentUser="RemoteSigned", LocalMachine="Restricted")
        row = self.run_scenario(probe("powershell.exe", 0, output))["powershell_execution_policy"]
        self.assertEqual("PASS", row["status"], row)
        self.assertIn("effective=CurrentUser=RemoteSigned", row["detail"])
        self.assertEqual("", row["remedy"])

    def test_process_bypass_overrides_restricted_user_and_machine_scopes(self):
        output = policy_output("Undefined", Process="Bypass", CurrentUser="Restricted", LocalMachine="AllSigned")
        row = self.run_scenario(probe("powershell.exe", 0, output))["powershell_execution_policy"]
        self.assertEqual("PASS", row["status"], row)
        self.assertIn("effective=Process=Bypass", row["detail"])

    def test_group_policy_restriction_overrides_permissive_lower_scopes(self):
        output = policy_output("Bypass", MachinePolicy="AllSigned")
        row = self.run_scenario(probe("powershell.exe", 0, output))["powershell_execution_policy"]
        self.assertEqual("WARN", row["status"], row)
        self.assertIn("effective=MachinePolicy=AllSigned", row["detail"])
        self.assertIn("Group Policy", row["remedy"])

    def test_all_undefined_warns_about_platform_default(self):
        row = self.run_scenario(probe("powershell.exe", 0, policy_output("Undefined")))["powershell_execution_policy"]
        self.assertEqual("WARN", row["status"], row)
        self.assertIn("effective=platform-default=Undefined", row["detail"])

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

    def test_nonzero_category_observes_stderr_even_when_stdout_is_present(self):
        code = ("import os,sys; os.write(1,b'partial rows'); "
                "os.write(2,b'security module could not be loaded'); sys.exit(1)")
        with patch("agentic.providers.github_status.host_executable", return_value=sys.executable):
            observed = host_preflight.run(["python", "-c", code])
        self.assertEqual("MODULE_LOAD_FAILURE", observed["diagnostic_category"])
        self.assertIn("partial rows", observed["output"])
        self.assertIn("security module", observed["output"])

    def test_child_output_is_byte_bounded_and_truncation_fails_closed(self):
        size = host_preflight.MAX_CAPTURE_BYTES * 4
        code = f"import os; os.write(1,b'x'*{size})"
        with patch("agentic.providers.github_status.host_executable", return_value=sys.executable):
            observed = host_preflight.run(["python", "-c", code])
        self.assertEqual(0, observed["exit_code"])
        self.assertEqual("OUTPUT_TRUNCATED", observed["diagnostic_category"])
        self.assertIn(f"truncated at {host_preflight.MAX_CAPTURE_BYTES} bytes", observed["output"])
        self.assertLessEqual(len(observed["output"]), host_preflight.MAX_DIAGNOSTIC_CHARS)
        captured, state = bytearray(), {"truncated": False, "error": None}
        host_preflight._drain_bounded(io.BytesIO(b"x" * size), captured, state)
        self.assertEqual(host_preflight.MAX_CAPTURE_BYTES, len(captured))
        self.assertTrue(state["truncated"])

    def test_invalid_child_encoding_is_an_explicit_diagnostic(self):
        code = "import os; os.write(1,b'valid\\xffinvalid')"
        with patch("agentic.providers.github_status.host_executable", return_value=sys.executable):
            observed = host_preflight.run(["python", "-c", code])
        self.assertEqual(0, observed["exit_code"])
        self.assertEqual("INVALID_ENCODING", observed["diagnostic_category"])
        self.assertIn("invalid UTF-8 from child stdout", observed["output"])

    def test_child_timeout_retains_the_deadline_diagnostic_and_output(self):
        code = "import os,time; os.write(1,b'before timeout'); time.sleep(5)"
        with patch("agentic.providers.github_status.host_executable", return_value=sys.executable), \
                patch.object(host_preflight, "HOST_COMMAND_TIMEOUT_SECONDS", 0.05):
            observed = host_preflight.run(["python", "-c", code])
        self.assertIsNone(observed["exit_code"])
        self.assertEqual("TIMEOUT", observed["diagnostic_category"])
        self.assertIn("deadline", observed["output"])

    def test_repeated_timeouts_close_descendants_pipes_and_reader_threads(self):
        baseline = {thread.ident for thread in threading.enumerate()}
        sentinels = [ROOT / ".tmp-tests" / f"timeout-descendant-{uuid.uuid4().hex}.txt"
                     for _ in range(4)]
        try:
            for sentinel in sentinels:
                child = ("import pathlib,time; time.sleep(.6); "
                         f"pathlib.Path({str(sentinel)!r}).write_text('leaked',encoding='utf-8')")
                parent = ("import subprocess,sys,time; "
                          "p=subprocess.Popen([sys.executable,'-B','-c'," + repr(child) +
                          "],stdout=sys.stdout,stderr=sys.stderr); "
                          "print(p.pid,flush=True); time.sleep(5)")
                with patch("agentic.providers.github_status.host_executable", return_value=sys.executable), \
                        patch.object(host_preflight, "HOST_COMMAND_TIMEOUT_SECONDS", 0.08):
                    observed = host_preflight.run(["python", "-c", parent])
                self.assertEqual("TIMEOUT", observed["diagnostic_category"], observed)
                self.assertTrue(observed["resource_cleanup_complete"], observed)
                self.assertEqual([], observed["cleanup_errors"])
            time.sleep(.8)
            leaked_threads = [thread for thread in threading.enumerate()
                              if thread.ident not in baseline and thread.is_alive()]
            self.assertEqual([], leaked_threads)
            self.assertFalse(any(path.exists() for path in sentinels))
        finally:
            for path in sentinels:
                path.unlink(missing_ok=True)

    def test_timeout_cleanup_failures_are_structured_and_fail_closed(self):
        class Process:
            def __init__(self):
                self.stdout, self.stderr = io.BytesIO(b"partial"), io.BytesIO()
                self.returncode = None
                self.waits = 0

            def wait(self, timeout):
                self.waits += 1
                if self.waits < 3:
                    raise subprocess.TimeoutExpired(["synthetic"], timeout)
                raise OSError("synthetic wait failure")

            def terminate(self):
                raise OSError("synthetic terminate failure")

            def kill(self):
                raise OSError("synthetic kill failure")

            def poll(self):
                return None

        class Tree:
            @staticmethod
            def terminate_and_wait(_timeout):
                return ["tree_kill:SYNTHETIC_FAILURE"]

        with patch("agentic.providers.github_status.host_executable", return_value=sys.executable), \
                patch.object(host_preflight, "_spawn_tree", return_value=(Process(), Tree())):
            observed = host_preflight.run(["python", "-c", "pass"])
        self.assertEqual("TIMEOUT", observed["diagnostic_category"])
        self.assertFalse(observed["resource_cleanup_complete"])
        self.assertTrue(any(item.startswith("terminate:") for item in observed["cleanup_errors"]))
        self.assertTrue(any(item.startswith("kill:") for item in observed["cleanup_errors"]))
        self.assertIn("tree_kill:SYNTHETIC_FAILURE", observed["cleanup_errors"])
        self.assertIn("child cleanup failed", observed["output"])

    def test_markdown_diagnostics_escape_table_metacharacters_and_normalize_lines(self):
        rendered = host_preflight.render_markdown({
            "platform": "windows",
            "rows": [{"check": "repository_config", "status": "WARN",
                      "detail": "owner|value\r\nsecond\rthird",
                      "remedy": "set a|b\nthen <review>"}],
        })
        self.assertIn("owner&#124;value<br>second<br>third", rendered)
        self.assertIn("set a&#124;b<br>then &lt;review&gt;", rendered)
        self.assertNotIn("\r", rendered)
        self.assertEqual(7, len(rendered.splitlines()))


def synthetic_pass():
    return None


def synthetic_interrupt():
    raise KeyboardInterrupt()


def synthetic_timeout():
    raise TimeoutError()


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
        for sequence in range(2):
            progress = self_test.SelfTestProgress(stream=io.StringIO(), interval=1)
            progress.start()
            progress.begin_phase("test_suite")
            result, log = self_test.execute_test_suite(
                unittest.TestSuite([unittest.FunctionTestCase(synthetic_pass)]), progress)
            progress.complete_phase("test_suite")
            progress.stop()
            report = {
                "created_at": "fixture-time-" + str(sequence),
                "elapsed_seconds": sequence + 0.25,
                "phase_timings_seconds": {"test_suite": sequence + 0.125},
                "nondeterministic_fields": list(self_test.NONDETERMINISTIC_REPORT_FIELDS),
                "status": "PASS" if result.wasSuccessful() else "FAILED",
                "tests": {"run": result.testsRun, "successful": result.wasSuccessful(),
                          "failures": len(result.failures), "errors": len(result.errors)},
                "test_log": log,
            }
            outputs.append(self_test.deterministic_report_bytes(report, include_details=True))
        self.assertEqual(outputs[0], outputs[1])

    def test_ac38_interruption_and_timeout_name_last_phase_and_current_test_without_pass(self):
        for function, exception in ((synthetic_interrupt, KeyboardInterrupt),
                                    (synthetic_timeout, TimeoutError)):
            with self.subTest(exception=exception.__name__):
                progress = self_test.SelfTestProgress(stream=io.StringIO(), interval=1)
                progress.start()
                progress.begin_phase("documentation")
                progress.complete_phase("documentation")
                progress.begin_phase("test_suite")
                with self.assertRaises(exception):
                    self_test.execute_test_suite(
                        unittest.TestSuite([unittest.FunctionTestCase(function)]), progress)
                evidence = {"status": "INTERRUPTED", "last_completed_phase": progress.last_completed_phase,
                            "current_phase": progress.current_phase,
                            "current_test": progress.current_test or progress.last_test}
                progress.stop()
                self.assertEqual("documentation", evidence["last_completed_phase"])
                self.assertEqual("test_suite", evidence["current_phase"])
                self.assertIn(function.__name__, evidence["current_test"])
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
        self.assertNotIn(bytes.fromhex("efbfbd"), raw)
        parsed = json.loads(raw.decode("ascii"))
        self.assertIn("project_state", parsed)
        code, raw, errors = self.run_redirected("status")
        self.assertIn(code, (0, 2), errors.decode("ascii", errors="replace"))
        first = raw.splitlines()[0]
        first.decode("ascii")
        self.assertNotIn(b"?", first)
        self.assertNotIn(bytes.fromhex("efbfbd"), raw)


if __name__ == "__main__":
    unittest.main()
