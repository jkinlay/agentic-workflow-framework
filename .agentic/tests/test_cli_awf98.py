"""AWF-98 CLI regressions for owner disposition requests."""
from __future__ import annotations

import contextlib
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

from agentic.authorization import cap_disposition_record_id  # noqa: E402
from agentic.canonical import sha256  # noqa: E402
from agentic.cli import main as workflow_main  # noqa: E402
from agentic.gates import review_verdict_json  # noqa: E402


class DispositionRequestCliTests(unittest.TestCase):
    def test_cap_request_outputs_exact_owner_line_and_bound_verdict(self):
        head = "b" * 40
        critic_run_id = str(uuid.uuid4())
        artifact = {
            "critic_run_id": critic_run_id,
            "result_sha256": "5" * 64,
            "head_sha": head,
            "round": 3,
        }
        verdict = {
            "record_id": str(uuid.uuid4()),
            "run_id": critic_run_id,
            "head_sha": head,
            "round": 3,
            "owner_review": False,
            "critic_artifact_binding": artifact,
        }
        binding = {
            "project_id": "00000000-0000-0000-0000-000000000001",
            "repository_id": 123,
            "issue_id": "AWF-98",
            "requirements_hash": "1" * 64,
            "contract_hash": "2" * 64,
            "policy_hash": "3" * 64,
            "candidate_id": "4" * 64,
        }
        disposition = {
            "schema_version": 3,
            "record_id": "",
            "created_at": "2026-10-15T18:50:00Z",
            "producer_id": "owner-disposition-verifier",
            "run_id": str(uuid.uuid4()),
            "binding": binding,
            "decision": "EXTEND_ONE_CYCLE",
            "critic_artifact_binding": artifact,
            "review_verdict_record_id": verdict["record_id"],
            "review_verdict_sha256": sha256(
                review_verdict_json(verdict).encode("utf-8")
            ),
            "open_finding_ids": ["AWF98-R3-001"],
            "notes": "One bounded extension.",
            "cycles": 3,
            "cap_extensions": 0,
            "successor_ticket": None,
            "evidence": ["urn:awf:test:critic-artifact"],
        }
        disposition["record_id"] = cap_disposition_record_id(disposition)
        request_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config_path = base / "config.json"
            disposition_path = base / "disposition.json"
            verdict_path = base / "verdict.json"
            config_path.write_text(json.dumps({
                "merge_gate": {"trusted_owner_ids": [1001]},
            }), encoding="utf-8")
            disposition_path.write_text(json.dumps(disposition), encoding="utf-8")
            verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch("agentic.cli.verify_installed", return_value="0" * 64), \
                    mock.patch("agentic.cli.Contracts.validate"), \
                    contextlib.redirect_stdout(stdout):
                code = workflow_main([
                    "--root", str(ROOT), "disposition-request",
                    "review-cap-disposition", str(disposition_path),
                    "--head", head,
                    "--config", str(config_path),
                    "--review-verdict", str(verdict_path),
                    "--request-id", request_id,
                ])

        output = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(request_id, output["authorization_request_id"])
        self.assertIn(f"request={request_id}", output["expected_text"])
        self.assertEqual(
            verdict["record_id"],
            output["disposition_fields"]["review_verdict_record_id"],
        )
        self.assertFalse(output["execution_authority"])


if __name__ == "__main__":
    unittest.main()
