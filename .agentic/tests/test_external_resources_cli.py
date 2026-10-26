"""The external-resource CLI is parseable and fails closed."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import external_resources
from agentic.contracts import Contracts
from agentic.external_resources import admit_resource


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agentic/scripts/external_resources.py"


class ExternalResourceCliTests(unittest.TestCase):
    @staticmethod
    def synthetic_resource():
        registry = {
            "format": "awf-external-resource-registry-1", "resources": {
                "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                               "host_mapping": "operator_local", "sensitivity": "restricted",
                               "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                         "timeout_seconds": 5}}}}
        mappings = {
            "format": "awf-external-resource-mapping-1", "resources": {
                "raw_estate": {"local_path": "/synthetic/estate",
                               "canonical_locator": "urn:awf:synthetic:raw-estate",
                               "mapping_kind": "local"}}}
        return registry, mappings

    @staticmethod
    def successful_probe():
        return {
            "diagnostic_category": "OK", "exit_code": 0,
            "probe": {"observed_locator": "urn:awf:synthetic:raw-estate",
                      "listing_count": 1, "sample_size": 1,
                      "sample_mtime": "2026-10-09T12:00:00Z", "bytes_read": 1},
        }

    def run_cli(self, *args, code=0, env=None, timeout=30):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if env is not None:
            environment.update(env)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                env=environment, timeout=timeout)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def test_awf92_windows_resolver_prefers_verified_system_directory(self):
        class PortablePath:
            def __init__(self, value):
                self.value = value.rstrip("/")

            def __truediv__(self, value):
                return PortablePath(self.value + "/" + value)

            def is_absolute(self):
                return self.value.startswith("/")

            def is_file(self):
                return self.value.endswith("/WindowsPowerShell/v1.0/powershell.exe")

            def __str__(self):
                return self.value

        system_directory = PortablePath("/windows/System32")
        expected = "/windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        with (patch.object(external_resources.os, "name", "nt"),
              patch.dict(external_resources.os.environ,
                         {"PATH": "", "PATHEXT": ""}, clear=True),
              patch.object(external_resources, "_windows_system_directory",
                           return_value=system_directory, create=True),
              patch.object(external_resources.shutil, "which",
                           side_effect=AssertionError("sanitized PATH must not be consulted"))):
            self.assertEqual(external_resources._powershell_executable(), expected)

    def test_awf101_windows_resolver_refuses_unverified_relative_fallback(self):
        with (patch.object(external_resources.os, "name", "nt"),
              patch.dict(external_resources.os.environ,
                         {"PATH": "", "PATHEXT": ""}, clear=True),
              patch.object(external_resources, "_windows_system_directory",
                           return_value=None),
              patch.object(external_resources.shutil, "which",
                           return_value=None) as which):
            with self.assertRaisesRegex(
                    OSError, "no verified absolute PowerShell executable"):
                external_resources._powershell_executable()
        self.assertEqual(
            [item.args[0] for item in which.call_args_list],
            ["pwsh.exe", "powershell.exe"])

    def test_awf101_resolver_preserves_non_windows_behavior(self):
        with (patch.object(external_resources.os, "name", "posix"),
              patch.object(external_resources, "_windows_system_directory",
                           side_effect=AssertionError("Windows discovery must not run")),
              patch.object(external_resources.shutil, "which",
                           side_effect=AssertionError("PATH discovery must not run"))):
            self.assertEqual(external_resources._powershell_executable(), "powershell")

    def test_awf124_failed_start_emits_safe_diagnostics_to_stderr(self):
        registry = {
            "format": "awf-external-resource-registry-1", "resources": {
                "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                               "host_mapping": "operator_local", "sensitivity": "restricted",
                               "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                         "timeout_seconds": 5}}}}
        mappings = {
            "format": "awf-external-resource-mapping-1", "resources": {
                "raw_estate": {"local_path": "/synthetic/estate",
                               "canonical_locator": "urn:awf:synthetic:raw-estate",
                               "mapping_kind": "local"}}}
        failure = {
            "diagnostic_category": "EXECUTION_UNAVAILABLE", "exit_code": None,
            "output": "OSError: synthetic start failure",
            "cleanup_errors": ["job_close:WinError6"],
            "resource_cleanup_complete": False,
        }
        environment = {
            "PATH": "", "SYSTEMROOT": "/windows", "COMSPEC": "/windows/system32/cmd.exe",
            "TEMP": "/tmp/user", "TMP": "/tmp/user", "PATHEXT": ".EXE;.CMD",
            "API_SECRET": "must-not-appear",
        }
        stderr = io.StringIO()
        with (patch.dict(external_resources.os.environ, environment, clear=True),
              redirect_stderr(stderr)):
            receipt = admit_resource(
                registry, mappings, "raw_estate", task_id="AWF-8", principal="worker-A",
                session_id="session-1", now="2026-10-09T12:00:00Z",
                execute=lambda command, cwd=None: failure)

        self.assertEqual(receipt["state"], "PROCESS_START_FAILED")
        self.assertNotIn("start_diagnostics", receipt["attempts"][0])
        diagnostics = json.loads(stderr.getvalue().split("=", 1)[1])
        self.assertEqual(diagnostics["diagnostic_category"], "EXECUTION_UNAVAILABLE")
        self.assertEqual(diagnostics["captured_output"], "OSError: synthetic start failure")
        self.assertEqual(diagnostics["cleanup_errors"], ["job_close:WinError6"])
        self.assertIs(type(diagnostics["elapsed_milliseconds"]), int)
        self.assertGreaterEqual(diagnostics["elapsed_milliseconds"], 0)
        snapshot = diagnostics["child_environment"]
        self.assertEqual(snapshot["variable_names"], sorted(snapshot["variable_names"]))
        self.assertIn("API_SECRET", snapshot["variable_names"])
        self.assertEqual(snapshot["selected_values"], {
            "PATH": "", "SYSTEMROOT": "/windows", "WINDIR": None,
            "COMSPEC": "/windows/system32/cmd.exe", "TEMP": "/tmp/user",
            "TMP": "/tmp/user", "PATHEXT": ".EXE;.CMD",
        })
        self.assertNotIn("must-not-appear", json.dumps(receipt) + stderr.getvalue())
        self.assertEqual(external_resources.public_evidence(receipt)["result"],
                         "PROCESS_START_FAILED")

    def test_awf131_success_and_failure_receipts_satisfy_contract(self):
        registry = {
            "format": "awf-external-resource-registry-1", "resources": {
                "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                               "host_mapping": "operator_local", "sensitivity": "restricted",
                               "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                         "timeout_seconds": 5}}}}
        mappings = {
            "format": "awf-external-resource-mapping-1", "resources": {
                "raw_estate": {"local_path": "/synthetic/estate",
                               "canonical_locator": "urn:awf:synthetic:raw-estate",
                               "mapping_kind": "local"}}}
        success = {
            "diagnostic_category": "OK", "exit_code": 0,
            "probe": {"observed_locator": "urn:awf:synthetic:raw-estate",
                      "listing_count": 1, "sample_size": 1,
                      "sample_mtime": "2026-10-09T12:00:00Z", "bytes_read": 1},
        }
        failure = {
            "diagnostic_category": "EXECUTION_UNAVAILABLE", "exit_code": None,
            "output": "OSError: synthetic start failure", "cleanup_errors": [],
        }

        def receipt(result):
            with redirect_stderr(io.StringIO()):
                return admit_resource(
                    registry, mappings, "raw_estate", task_id="AWF-131",
                    principal="worker-A", session_id="session-1",
                    now="2026-10-09T12:00:00Z",
                    execute=lambda command, cwd=None: result)

        contracts = Contracts(ROOT / ".agentic" / "schemas")
        contracts.validate("external-resource-receipt", receipt(success))
        contracts.validate("external-resource-receipt", receipt(failure))

    def test_awf131_h1_runner_receives_verified_absolute_powershell_with_empty_path(self):
        class PortablePath:
            def __init__(self, value):
                self.value = value.rstrip("/")

            def __truediv__(self, value):
                return PortablePath(self.value + "/" + value)

            def is_absolute(self):
                return self.value.startswith("/")

            def is_file(self):
                return self.value.endswith("/WindowsPowerShell/v1.0/powershell.exe")

            def __str__(self):
                return self.value

        registry, mappings = self.synthetic_resource()
        commands = []

        def execute(command, cwd=None):
            commands.append(command)
            return self.successful_probe()

        with (patch.object(external_resources.os, "name", "nt"),
              patch.dict(external_resources.os.environ,
                         {"PATH": "", "PATHEXT": ""}, clear=True),
              patch.object(external_resources, "_windows_system_directory",
                           return_value=PortablePath("/windows/System32")),
              patch.object(external_resources.shutil, "which",
                           side_effect=AssertionError("PATH fallback must not run"))):
            receipt = admit_resource(
                registry, mappings, "raw_estate", task_id="AWF-131",
                principal="worker-A", session_id="session-1",
                now="2026-10-09T12:00:00Z", execute=execute)

        self.assertEqual(receipt["state"], "READ_VERIFIED")
        self.assertEqual(commands[0][0],
                         "/windows/System32/WindowsPowerShell/v1.0/powershell.exe")

    def test_awf131_h2_timeout_is_not_classified_as_process_start_failure(self):
        registry, mappings = self.synthetic_resource()
        stderr = io.StringIO()
        timeouts = []

        def execute(command, *, cwd=None, timeout_seconds=None):
            timeouts.append(timeout_seconds)
            return {
                "diagnostic_category": "TIMEOUT", "exit_code": None,
                "output": "child exceeded 5-second deadline",
                "cleanup_errors": [], "resource_cleanup_complete": True,
            }

        with (patch.object(external_resources, "_default_execute", side_effect=execute),
              redirect_stderr(stderr)):
            receipt = admit_resource(
                registry, mappings, "raw_estate", task_id="AWF-131",
                principal="worker-A", session_id="session-1",
                now="2026-10-09T12:00:00Z")

        self.assertEqual(timeouts, [5])
        self.assertEqual(receipt["state"], "TIMEOUT")
        self.assertEqual(receipt["attempts"][0]["state"], "TIMEOUT")
        self.assertEqual(receipt["error_layer"], "COMMAND_TIMEOUT")
        self.assertEqual(json.loads(stderr.getvalue().split("=", 1)[1])
                         ["diagnostic_category"], "TIMEOUT")
        Contracts(ROOT / ".agentic" / "schemas").validate(
            "external-resource-receipt", receipt)
        evidence = external_resources.public_evidence(receipt)
        Contracts(ROOT / ".agentic" / "schemas").validate(
            "external-resource-evidence", evidence)
        self.assertEqual(evidence["result"], "COMMAND_NONZERO")

    def test_awf131_h3_missing_child_variables_are_visible_in_diagnostics(self):
        registry, mappings = self.synthetic_resource()
        environment = {"PATH": "", "TEMP": "/tmp/user", "TMP": "/tmp/user"}
        stderr = io.StringIO()
        with (patch.dict(external_resources.os.environ, environment, clear=True),
              redirect_stderr(stderr)):
            receipt = admit_resource(
                registry, mappings, "raw_estate", task_id="AWF-131",
                principal="worker-A", session_id="session-1",
                now="2026-10-09T12:00:00Z",
                execute=lambda command, cwd=None: {
                    "diagnostic_category": "EXECUTION_UNAVAILABLE", "exit_code": None,
                    "output": "synthetic environment-sensitive start failure",
                    "cleanup_errors": [], "resource_cleanup_complete": True,
                })

        self.assertEqual(receipt["state"], "PROCESS_START_FAILED")
        selected = json.loads(stderr.getvalue().split("=", 1)[1])[
            "child_environment"]["selected_values"]
        self.assertEqual(selected["PATH"], "")
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            self.assertIsNone(selected[name])
        self.assertNotIn("child_environment", json.dumps(receipt))
        Contracts(ROOT / ".agentic" / "schemas").validate(
            "external-resource-receipt", receipt)

    def test_validate_registry_and_bounded_scan(self):
        with tempfile.TemporaryDirectory(prefix="awf-resource-cli-") as temporary:
            base = Path(temporary)
            registry = base / "registry.json"
            registry.write_text(json.dumps({
                "format": "awf-external-resource-registry-1", "resources": {
                    "raw_estate": {"access": "read_only", "required_by": [],
                                   "host_mapping": "operator_local", "sensitivity": "restricted",
                                   "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                             "timeout_seconds": 5}}}}), encoding="utf-8")
            accepted = self.run_cli("validate-registry", "--registry", registry)
            self.assertEqual(accepted["status"], "ACCEPTED")
            scan = base / "scan.json"
            scan.write_text(json.dumps({"resource_alias": "raw_estate", "recursive": True,
                                        "max_depth": 0, "file_limit": 5,
                                        "date_range": {"start": "2026-01-01", "end": "2026-10-09"},
                                        "timeout_seconds": 5}), encoding="utf-8")
            rejected = self.run_cli("validate-scan", "--request", scan, code=2)
            self.assertEqual(rejected["status"], "REJECTED")
            self.assertIn("UNBOUNDED_SCAN", rejected["reason"])

    def test_require_needs_host_pin_registry_and_private_mapping(self):
        with tempfile.TemporaryDirectory(prefix="awf-resource-require-") as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            registry = {
                "format": "awf-external-resource-registry-1", "resources": {
                    "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                                   "host_mapping": "operator_local", "sensitivity": "restricted",
                                   "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                             "timeout_seconds": 5}}}}
            mappings = {
                "format": "awf-external-resource-mapping-1", "resources": {
                    "raw_estate": {"local_path": str(base / "estate"),
                                   "canonical_locator": "urn:awf:synthetic:raw-estate",
                                   "mapping_kind": "local"}}}
            receipt = admit_resource(
                registry, mappings, "raw_estate", task_id="AWF-8", principal="worker-A",
                session_id="session-1", now="2026-10-09T12:00:00Z",
                execute=lambda command, cwd=None: {
                    "diagnostic_category": "OK", "exit_code": 0,
                    "probe": {"observed_locator": "urn:awf:synthetic:raw-estate",
                              "listing_count": 1, "sample_size": 1,
                              "sample_mtime": "2026-10-09T12:00:00Z", "bytes_read": 1}})
            registry_path = project / "registry.json"
            mapping_path = base / "mapping.json"
            receipt_path = base / "receipt.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            mapping_path.write_text(json.dumps(mappings), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            common = (
                "require", "--project-root", project, "--registry", registry_path,
                "--mapping", mapping_path, "--receipt", receipt_path,
                "--resource", "raw_estate", "--task", "AWF-8", "--principal", "worker-A",
                "--session", "session-1", "--now", "2026-10-09T12:00:00Z")
            admitted = self.run_cli(
                *common, "--expected-receipt-sha256", receipt["receipt_id"])
            self.assertEqual(admitted["status"], "ADMITTED")
            rejected = self.run_cli(
                *common, "--expected-receipt-sha256", "0" * 64, code=2)
            self.assertIn("trusted receipt digest", rejected["reason"])

    @unittest.skipUnless(os.name == "nt", "real PowerShell admission is Windows-specific")
    def test_real_powershell_admits_temporary_read_only_resource(self):
        with tempfile.TemporaryDirectory(prefix="awf-resource-real-") as temporary:
            base = Path(temporary)
            project = base / "project"
            estate = base / "estate"
            project.mkdir()
            estate.mkdir()
            (estate / "sample.bin").write_bytes(b"x")
            registry = project / "registry.json"
            registry.write_text(json.dumps({
                "format": "awf-external-resource-registry-1", "resources": {
                    "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                                   "host_mapping": "operator_local", "sensitivity": "restricted",
                                   "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                             "timeout_seconds": 5}}}}), encoding="utf-8")
            mapping = base / "private-mapping.json"
            mapping.write_text(json.dumps({
                "format": "awf-external-resource-mapping-1", "resources": {
                    "raw_estate": {"local_path": str(estate),
                                   "canonical_locator": "urn:awf:synthetic:raw-estate",
                                   "mapping_kind": "local"}}}), encoding="utf-8")
            receipt = self.run_cli(
                "admit", "--project-root", project, "--registry", registry,
                "--mapping", mapping, "--resource", "raw_estate", "--task", "AWF-8",
                "--principal", "worker-A", "--session", "session-1", "--now",
                "2026-10-09T12:00:00Z")
            self.assertEqual(receipt["state"], "READ_VERIFIED")
            self.assertEqual(receipt["evidence"]["bytes_read"], 1)
            self.assertNotIn(str(estate), json.dumps(receipt))

    @unittest.skipUnless(os.name == "nt", "real PowerShell admission is Windows-specific")
    def test_awf8_r5_admit_resolves_powershell_with_sanitized_path(self):
        with tempfile.TemporaryDirectory(prefix="awf-resource-sanitized-") as temporary:
            base = Path(temporary)
            project = base / "project"
            estate = base / "estate"
            project.mkdir()
            estate.mkdir()
            (estate / "sample.bin").write_bytes(b"x")
            registry = project / "registry.json"
            registry.write_text(json.dumps({
                "format": "awf-external-resource-registry-1", "resources": {
                    "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                                   "host_mapping": "operator_local", "sensitivity": "restricted",
                                   "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                             "timeout_seconds": 30}}}}), encoding="utf-8")
            mapping = base / "private-mapping.json"
            mapping.write_text(json.dumps({
                "format": "awf-external-resource-mapping-1", "resources": {
                    "raw_estate": {"local_path": str(estate),
                                   "canonical_locator": "urn:awf:synthetic:raw-estate",
                                   "mapping_kind": "local"}}}), encoding="utf-8")

            receipt = self.run_cli(
                "admit", "--project-root", project, "--registry", registry,
                "--mapping", mapping, "--resource", "raw_estate", "--task", "AWF-8",
                "--principal", "worker-A", "--session", "session-1", "--now",
                "2026-10-09T12:00:00Z", env={"PATH": ""}, timeout=120)

            self.assertEqual(receipt["state"], "READ_VERIFIED")
            self.assertEqual(receipt["evidence"]["bytes_read"], 1)


if __name__ == "__main__":
    unittest.main()
