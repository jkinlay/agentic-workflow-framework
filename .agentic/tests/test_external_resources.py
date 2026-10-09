"""External-resource admission stays bounded, per-task, and proportionate."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.contracts import Contracts
from agentic.external_resources import (
    admit_resource, admission_decision, classify_probe_result, public_evidence,
    require_admissions, split_root_policy, validate_claim, validate_registry,
    validate_scan_request,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-10-09T12:00:00Z"


class ExternalResourceTests(unittest.TestCase):
    def setUp(self):
        temporary_mapping = tempfile.TemporaryDirectory(prefix="awf-external-resource-")
        self.addCleanup(temporary_mapping.cleanup)
        self.raw_local_path = str(Path(temporary_mapping.name).resolve())
        self.registry = {
            "format": "awf-external-resource-registry-1",
            "resources": {
                "raw_estate": {
                    "access": "read_only",
                    "required_by": ["EX-6"],
                    "host_mapping": "operator_local",
                    "sensitivity": "restricted",
                    "probe": {"sample_relative_path": "sample/data.bin",
                              "listing_limit": 8, "timeout_seconds": 10},
                }
            },
        }
        self.mappings = {
            "format": "awf-external-resource-mapping-1",
            "resources": {
                "raw_estate": {"local_path": self.raw_local_path, "canonical_locator": "\\\\host-a\\estate",
                               "mapping_kind": "mapped_drive"}
            },
        }

    def success(self, locator="\\\\host-a\\estate", bytes_read=1):
        return {"diagnostic_category": "OK", "exit_code": 0, "output": "",
                "probe": {"observed_locator": locator, "listing_count": 3,
                          "sample_size": 20, "sample_mtime": NOW,
                          "bytes_read": bytes_read}}

    def admit(self, **kwargs):
        values = {"task_id": "AWF-8", "principal": "worker-A", "session_id": "session-1",
                  "execute": lambda command, cwd=None: self.success(), "now": NOW}
        values.update(kwargs)
        return admit_resource(self.registry, self.mappings, "raw_estate", **values)

    def test_registry_is_logical_only_and_schemas_accept_records(self):
        normalized = validate_registry(self.registry)
        serialized = json.dumps(normalized)
        self.assertNotIn(self.raw_local_path, serialized)
        self.assertNotIn(json.dumps(self.raw_local_path)[1:-1], serialized)
        self.assertNotIn("host-a", serialized)
        leaked = deepcopy(self.registry)
        leaked["resources"]["raw_estate"]["local_path"] = "private"
        with self.assertRaisesRegex(ValidationError, "exactly"):
            validate_registry(leaked)
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("external-resource-registry", self.registry)
        contracts.validate("external-resource-mapping", self.mappings)
        receipt = self.admit()
        contracts.validate("external-resource-receipt", receipt)
        contracts.validate("external-resource-evidence", public_evidence(receipt))
        contracts.validate("external-resource-scan", validate_scan_request({
            "resource_alias": "raw_estate", "recursive": True, "max_depth": 2,
            "file_limit": 50, "date_range": {"start": "2026-01-01", "end": "2026-10-09"},
            "timeout_seconds": 30}))
        contracts.validate("external-resource-claim", validate_claim(
            receipt, {"kind": "accessibility", "value": "CONFIRMED",
                      "supporting_evidence": []}))

    def test_ac48_classifies_every_failure_at_its_actual_layer(self):
        cases = [
            ({"diagnostic_category": "SANDBOX_BLOCKED", "exit_code": None}, "SANDBOX_BLOCKED", "SANDBOX"),
            ({"diagnostic_category": "EXECUTION_UNAVAILABLE", "exit_code": None}, "PROCESS_START_FAILED", "PROCESS_START"),
            ({"diagnostic_category": "NONZERO_EXIT", "exit_code": 7}, "COMMAND_NONZERO", "COMMAND_EXIT"),
            ({"diagnostic_category": "NONZERO_EXIT", "exit_code": 42}, "ACCESS_DENIED", "WINDOWS_ACCESS"),
            ({"diagnostic_category": "NONZERO_EXIT", "exit_code": 41}, "PATH_NOT_FOUND", "FILESYSTEM_PATH"),
            ({"diagnostic_category": "NONZERO_EXIT", "exit_code": 40}, "MAPPING_MISSING", "POWERSHELL_MAPPING"),
        ]
        for observation, state, layer in cases:
            with self.subTest(state=state):
                self.assertEqual(classify_probe_result(observation)[:2], (state, layer))
        self.assertEqual(classify_probe_result(self.success())[:2], ("READ_VERIFIED", "READ_PROBE"))

    def test_ac49_narrow_approval_retries_the_exact_command(self):
        commands = []
        results = [
            {"diagnostic_category": "SANDBOX_BLOCKED", "exit_code": None, "output": ""},
            self.success(),
        ]

        def execute(command, cwd=None):
            commands.append(command)
            return results.pop(0)

        def approve(command, request):
            self.assertEqual(request["access"], "read_only")
            return {"approved": True, "access": "read_only",
                    "command_sha256": request["command_sha256"], "scope": "one_command",
                    "approved_at": NOW, "expires_at": "2026-10-09T12:01:00Z"}

        receipt = self.admit(execute=execute, approval=approve)
        self.assertEqual(receipt["state"], "READ_VERIFIED")
        self.assertEqual(commands[0], commands[1])
        self.assertEqual(receipt["attempts"][0]["command_sha256"],
                         receipt["attempts"][1]["command_sha256"])
        self.assertEqual(receipt["permission"]["scope"], "one_command")
        self.assertEqual(receipt["permission"]["source"], "narrow_approval")

    def test_declared_probe_timeout_is_enforced_by_host_runner(self):
        with patch("agentic.host_preflight.run", return_value=self.success()) as host_run:
            receipt = admit_resource(
                self.registry, self.mappings, "raw_estate", task_id="AWF-8",
                principal="worker-A", session_id="session-1", now=NOW)
        self.assertEqual(receipt["state"], "READ_VERIFIED")
        self.assertEqual(host_run.call_args.kwargs["timeout_seconds"], 10)

    def test_ac50_access_is_not_inherited_and_refuses_before_work(self):
        controller = self.admit(principal="controller")
        dispatch = require_admissions(
            ["raw_estate"], [controller], task_id="AWF-8", principal="worker-A",
            session_id="session-1", now=NOW)
        decision = dispatch["resources"][0]
        self.assertEqual(dispatch["status"], "REFUSED")
        self.assertEqual(decision["status"], "REFUSED")
        self.assertEqual(decision["state"], "STALE")
        self.assertTrue(decision["before_work_started"])
        admitted = admission_decision(
            self.admit(), resource_alias="raw_estate", task_id="AWF-8",
            principal="worker-A", session_id="session-1", now=NOW)
        self.assertEqual(admitted["status"], "ADMITTED")

    def test_ac50_malformed_receipt_never_admits_work(self):
        malformed = {
            "format": "awf-external-resource-receipt-1", "resource_alias": "raw_estate",
            "task_id": "AWF-8", "principal": "worker-A", "session_id": "session-1",
            "state": "READ_VERIFIED", "permission": {"scope": "task"},
        }
        with self.assertRaisesRegex(ValidationError, "incomplete"):
            admission_decision(
                malformed, resource_alias="raw_estate", task_id="AWF-8",
                principal="worker-A", session_id="session-1", now=NOW)

    def test_ac50_tampered_receipt_identity_never_admits_work(self):
        for mutation in (
                lambda value: value.update(principal="worker-B"),
                lambda value: value["evidence"].update(
                    bytes_read=0, accessibility="UNCONFIRMED"),
                lambda value: value["attempts"][-1].update(error_layer="FORGED")):
            with self.subTest(mutation=mutation):
                receipt = self.admit()
                mutation(receipt)
                with self.assertRaisesRegex(ValidationError, "identity|final attempt"):
                    admission_decision(
                        receipt, resource_alias="raw_estate", task_id="AWF-8",
                        principal="worker-A", session_id="session-1", now=NOW)

    def test_ac51_new_task_and_expired_lifetime_are_stale(self):
        receipt = self.admit(permission_scope="task", permission_expires_at="2026-10-09T12:05:00Z")
        self.assertEqual(receipt["permission"]["scope"], "task")
        self.assertEqual(receipt["permission"]["expires_at"], "2026-10-09T12:05:00Z")
        new_task = admission_decision(
            receipt, resource_alias="raw_estate", task_id="AWF-9",
            principal="worker-A", session_id="session-1", now=NOW)
        expired = admission_decision(
            receipt, resource_alias="raw_estate", task_id="AWF-8",
            principal="worker-A", session_id="session-1", now="2026-10-09T12:06:00Z")
        self.assertEqual(new_task["state"], "STALE")
        self.assertEqual(expired["state"], "STALE")

    def test_ac52_remapped_drive_is_detected_without_locator_leak(self):
        receipt = self.admit(execute=lambda command, cwd=None: self.success("\\\\host-b\\other"))
        self.assertEqual(receipt["state"], "MAPPING_CHANGED")
        tracked = public_evidence(receipt)
        serialized = json.dumps(tracked)
        self.assertNotIn("host-a", serialized)
        self.assertNotIn("host-b", serialized)
        self.assertNotIn("private_mapping_fingerprint", tracked)

    def test_ac52_remap_precedes_a_subsequent_path_failure(self):
        receipt = self.admit(execute=lambda command, cwd=None: {
            "diagnostic_category": "NONZERO_EXIT", "exit_code": 41,
            "probe": {"observed_locator": "\\\\host-b\\other"},
        })
        self.assertEqual(receipt["state"], "MAPPING_CHANGED")
        self.assertEqual(receipt["error_layer"], "MAPPING_IDENTITY")
        self.assertEqual(receipt["attempts"][-1]["state"], "MAPPING_CHANGED")
        self.assertEqual(receipt["evidence"]["accessibility"], "UNCONFIRMED")

    def test_ac54_read_only_root_keeps_repository_as_only_writable_root(self):
        policy = split_root_policy(self.registry)
        self.assertEqual(policy["status"], "SUPPORTED")
        self.assertEqual(policy["repository_role"], "writable")
        writable = deepcopy(self.registry)
        writable["resources"]["raw_estate"]["access"] = "write"
        refused = split_root_policy(writable)
        self.assertEqual(refused["status"], "UNSUPPORTED_SPLIT_WRITABLE_ROOTS")
        self.assertIn("single writable workspace root", refused["remedy"])

    def test_ac55_scan_requires_and_records_every_bound(self):
        request = {"resource_alias": "raw_estate", "recursive": True, "max_depth": 0,
                   "file_limit": 100, "date_range": {"start": "2026-01-01", "end": "2026-10-09"},
                   "timeout_seconds": 30}
        with self.assertRaisesRegex(ValidationError, "UNBOUNDED_SCAN"):
            validate_scan_request(request)
        request["max_depth"] = 3
        accepted = validate_scan_request(request)
        self.assertEqual(accepted["bounds"], {key: request[key] for key in (
            "recursive", "max_depth", "file_limit", "date_range", "timeout_seconds")})

    def test_ac56_single_byte_proves_access_not_validity_or_completeness(self):
        receipt = self.admit()
        self.assertEqual(receipt["evidence"]["accessibility"], "CONFIRMED")
        self.assertEqual(receipt["evidence"]["data_validity"], "UNCONFIRMED")
        access = validate_claim(receipt, {"kind": "accessibility", "value": "CONFIRMED",
                                          "supporting_evidence": []})
        validity = validate_claim(receipt, {"kind": "data_validity", "value": "CONFIRMED",
                                            "supporting_evidence": []})
        absence = validate_claim(receipt, {"kind": "absence", "value": "CONFIRMED",
                                           "supporting_evidence": ["bounded_listing"]})
        self.assertEqual(access["status"], "CLAIM_ACCEPTED")
        self.assertEqual(validity["reason"], "SEPARATE_VALIDATION_REQUIRED")
        self.assertEqual(absence["reason"], "CLAIM_EXCEEDS_OBSERVATION")


if __name__ == "__main__":
    unittest.main()
