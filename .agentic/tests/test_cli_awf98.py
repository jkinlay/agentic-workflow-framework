import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.cli import main as workflow_main  # noqa: E402
import test_authorization_awf98 as auth_fixture  # noqa: E402


class DispositionRequestCliTests(unittest.TestCase):
    def _invoke_request(self, base, config, disposition, verdict, bundle):
        request_id = str(uuid.uuid4())
        config_path = base / "config.json"
        disposition_path = base / "disposition.json"
        verdict_path = base / "verdict.json"
        bundle_path = base / "bundle.json"
        assignment_path = base / "assignments.json"
        for path, value in (
                (config_path, config), (disposition_path, disposition),
                (verdict_path, verdict), (bundle_path, bundle)):
            path.write_text(json.dumps(value), encoding="utf-8")
        assignment_path.write_text("{}\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("agentic.cli.verify_installed", return_value="0" * 64), \
                mock.patch("agentic.cli.Contracts.validate"), \
                mock.patch("agentic.cli._load_cli_intake_assignment",
                           return_value=(config, {"ticket_key": "AWF-98"})), \
                mock.patch("agentic.policy.policy_hash",
                           return_value=bundle["contract"]["policy_hash"]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = workflow_main([
                "--root", str(ROOT), "disposition-request",
                "review-cap-disposition", str(disposition_path),
                "--head", bundle["candidate"]["head_sha"],
                "--config", str(config_path),
                "--review-verdict", str(verdict_path),
                "--bundle", str(bundle_path),
                "--assignment-state", str(assignment_path),
                "--ticket", "AWF-98", "--request-id", request_id,
            ])
        stream = stdout if code == 0 else stderr
        return code, json.loads(stream.getvalue()), request_id

    def _invoke_cap_plan(self, base, config, disposition, verdict, bundle):
        paths = {
            "config": base / "config.json",
            "disposition": base / "disposition.json",
            "verdict": base / "verdict.json",
            "bundle": base / "bundle.json",
            "findings": base / "findings.json",
            "assignments": base / "assignments.json",
        }
        for name, value in (
                ("config", config), ("disposition", disposition),
                ("verdict", verdict), ("bundle", bundle)):
            paths[name].write_text(json.dumps(value), encoding="utf-8")
        paths["findings"].write_text("[]\n", encoding="utf-8")
        paths["assignments"].write_text("{}\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        assignment = {"ticket_key": "AWF-98", "tier": 2}
        with mock.patch("agentic.cli.verify_installed", return_value="0" * 64), \
                mock.patch("agentic.cli.Contracts.validate"), \
                mock.patch("agentic.cli._load_cli_intake_assignment",
                           return_value=(config, assignment)), \
                mock.patch("agentic.authorization.verify_owner_record"), \
                mock.patch("agentic.review_policy.cap_disposition_plan",
                           return_value={"event": "CAP_PARK"}), \
                mock.patch("agentic.policy.policy_hash",
                           return_value=bundle["contract"]["policy_hash"]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = workflow_main([
                "--root", str(ROOT), "cap-plan",
                "--disposition", str(paths["disposition"]),
                "--findings", str(paths["findings"]),
                "--cycles", "3", "--extensions", "0",
                "--head", bundle["candidate"]["head_sha"],
                "--review-verdict", str(paths["verdict"]),
                "--bundle", str(paths["bundle"]),
                "--config", str(paths["config"]),
                "--assignment-state", str(paths["assignments"]),
                "--ticket", "AWF-98",
            ])
        stream = stdout if code == 0 else stderr
        return code, json.loads(stream.getvalue())

    def test_cap_request_outputs_exact_owner_line_and_bound_verdict(self):
        case = auth_fixture.OwnerDispositionTests(methodName="runTest")
        case.setUp()
        verdict = case._review_verdict()

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            code, output, request_id = self._invoke_request(
                base, case.config, case.record, verdict, case.trusted_bundle())

        self.assertEqual(0, code)
        self.assertEqual(request_id, output["authorization_request_id"])
        self.assertIn(f"request={request_id}", output["expected_text"])
        self.assertEqual(
            verdict["record_id"],
            output["disposition_fields"]["review_verdict_record_id"])
        self.assertFalse(output["execution_authority"])

    def test_request_rejects_mutually_consistent_record_for_another_ticket(self):
        case = auth_fixture.OwnerDispositionTests(methodName="runTest")
        case.setUp()
        verdict = case._review_verdict()
        case.record["binding"]["issue_id"] = "FOREIGN-ISSUE"
        verdict["binding"]["issue_id"] = "FOREIGN-ISSUE"

        with tempfile.TemporaryDirectory() as folder:
            code, output, _ = self._invoke_request(
                Path(folder), case.config, case.record, verdict,
                case.trusted_bundle())

        self.assertEqual(2, code)
        self.assertIn("candidate context", output["reason"])

    def test_request_rejects_mutually_consistent_record_for_another_candidate(self):
        case = auth_fixture.OwnerDispositionTests(methodName="runTest")
        case.setUp()
        verdict = case._review_verdict()
        case.record["binding"]["candidate_id"] = "8" * 64
        verdict["binding"]["candidate_id"] = "8" * 64
        verdict["candidate_binding"]["pr_number"] = 999

        with tempfile.TemporaryDirectory() as folder:
            code, output, _ = self._invoke_request(
                Path(folder), case.config, case.record, verdict,
                case.trusted_bundle())

        self.assertEqual(2, code)
        self.assertIn("candidate context", output["reason"])

    def test_cap_plan_rejects_mutually_consistent_foreign_ticket_and_candidate(self):
        case = auth_fixture.OwnerDispositionTests(methodName="runTest")
        case.setUp()
        verdict = case._review_verdict()
        case.record["binding"]["issue_id"] = "FOREIGN-ISSUE"
        case.record["binding"]["candidate_id"] = "8" * 64
        verdict["binding"] = copy.deepcopy(case.record["binding"])
        verdict["candidate_binding"]["pr_number"] = 999
        case.record["review_verdict_sha256"] = auth_fixture.sha256(
            auth_fixture.review_verdict_json(verdict).encode("utf-8"))
        case.record["record_id"] = auth_fixture.cap_disposition_record_id(case.record)

        with tempfile.TemporaryDirectory() as folder:
            code, output = self._invoke_cap_plan(
                Path(folder), case.config, case.record, verdict,
                case.trusted_bundle())

        self.assertEqual(2, code)
        self.assertIn("candidate context", output["reason"])


if __name__ == "__main__":
    unittest.main()
