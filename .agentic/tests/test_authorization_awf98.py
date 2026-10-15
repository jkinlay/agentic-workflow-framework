import copy
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError  # noqa: E402
from agentic.authorization import (  # noqa: E402
    make_owner_disposition_request,
    render_owner_record,
    verify_owner_record,
)
from agentic.canonical import sha256  # noqa: E402
from agentic.gates import (  # noqa: E402
    cap_disposition_record_id,
    review_verdict_json,
    verify_disposition_review_binding,
)


NOW = "2026-10-15T18:50:00Z"
HEAD = "b" * 40
SCHEMA = "review-cap-disposition"


class NoopContracts:
    def validate(self, _schema, _record):
        return None


CONTRACTS = NoopContracts()


class OwnerDispositionTests(unittest.TestCase):
    def setUp(self):
        self.config = {"merge_gate": {"trusted_owner_ids": [1001]},
                       "validation": {"max_evidence_age_seconds": 1800}}
        self.binding = {
            "project_id": "00000000-0000-0000-0000-000000000001",
            "repository_id": 123, "issue_id": "AWF-98",
            "requirements_hash": "1" * 64, "contract_hash": "2" * 64,
            "policy_hash": "3" * 64, "candidate_id": "4" * 64,
        }
        self.record = {
            "schema_version": 3, "record_id": "", "created_at": NOW,
            "producer_id": "owner-disposition-verifier",
            "run_id": str(uuid.uuid4()),
            "binding": copy.deepcopy(self.binding),
            "decision": "EXTEND_ONE_CYCLE",
            "critic_artifact_binding": {
                "critic_run_id": str(uuid.uuid4()),
                "result_sha256": "5" * 64, "head_sha": HEAD, "round": 3,
            },
            "review_verdict_record_id": str(uuid.uuid4()),
            "review_verdict_sha256": "6" * 64,
            "open_finding_ids": ["AWF98-R3-001"],
            "notes": "One bounded owner-authorized extension.",
            "cycles": 3, "cap_extensions": 0, "successor_ticket": None,
            "evidence": ["urn:awf:test:critic-artifact"],
        }
        self.record["record_id"] = cap_disposition_record_id(self.record)

    def _signed_record(self):
        record = copy.deepcopy(self.record)
        record["authorization_request_id"] = str(uuid.uuid4())
        raw = render_owner_record(record, "cap", HEAD)
        record["owner_source"] = {
            "channel": "github_pr_comment", "comment_id": 9001,
            "actor_id": 1001, "actor_login": "owner", "raw_body": raw,
            "raw_body_sha256": sha256(raw.encode("utf-8")),
            "created_at": NOW, "updated_at": NOW,
        }
        return record

    def _review_verdict(self):
        verdict = {
            "record_id": str(uuid.uuid4()),
            "run_id": self.record["critic_artifact_binding"]["critic_run_id"],
            "binding": copy.deepcopy(self.binding), "head_sha": HEAD,
            "round": 3, "owner_review": False,
            "critic_artifact_binding": copy.deepcopy(self.record["critic_artifact_binding"]),
        }
        self.record["review_verdict_record_id"] = verdict["record_id"]
        self.record["review_verdict_sha256"] = sha256(
            review_verdict_json(verdict).encode("utf-8"))
        self.record["record_id"] = cap_disposition_record_id(self.record)
        return verdict

    def _verify(self, record):
        return verify_owner_record(
            record, SCHEMA, self.config, CONTRACTS, NOW,
            head_sha=HEAD, binding=self.binding)

    def _verify_verdict(self, verdict):
        return verify_disposition_review_binding(
            self.record, SCHEMA, verdict, CONTRACTS, HEAD)

    def _assert_verify_rejected(self, record, pattern):
        with self.assertRaisesRegex(ValidationError, pattern):
            self._verify(record)

    def test_request_assigns_id_and_renders_exact_dispose_line(self):
        request_id = str(uuid.uuid4())

        request = make_owner_disposition_request(
            self.record, SCHEMA, self.config, head_sha=HEAD,
            authorization_request_id=request_id)

        self.assertEqual(request_id, request["authorization_request_id"])
        self.assertIn(f"record={self.record['record_id']}", request["expected_text"])
        self.assertIn(f"request={request_id}", request["expected_text"])
        self.assertIn(f"head={HEAD}", request["expected_text"])
        self.assertEqual(
            request["expected_text"],
            render_owner_record(request["disposition_fields"], "cap", HEAD))
        self.assertFalse(request["execution_authority"])

    def test_request_reads_trusted_owner_ids_from_injected_config(self):
        self.config["merge_gate"]["trusted_owner_ids"] = []

        with self.assertRaisesRegex(ValidationError, "trusted_owner_ids"):
            make_owner_disposition_request(
                self.record, SCHEMA, self.config, head_sha=HEAD)

    def test_verification_rejects_cap_artifact_for_another_head(self):
        record = self._signed_record()
        record["critic_artifact_binding"]["head_sha"] = "c" * 40

        self._assert_verify_rejected(record, "artifact head")

    def test_verification_rejects_non_content_addressed_cap_record_id(self):
        record = self._signed_record()
        record["record_id"] = "f" * 64
        raw = render_owner_record(record, "cap", HEAD)
        record["owner_source"]["raw_body"] = raw
        record["owner_source"]["raw_body_sha256"] = sha256(raw.encode("utf-8"))

        self._assert_verify_rejected(record, "record ID")

    def test_verification_rejects_untrusted_comment_author(self):
        record = self._signed_record()
        record["owner_source"]["actor_id"] = 2002

        self._assert_verify_rejected(record, "untrusted numeric owner")

    def test_verification_rejects_edited_comment(self):
        record = self._signed_record()
        record["owner_source"]["updated_at"] = "2026-10-15T18:50:01Z"

        self._assert_verify_rejected(record, "edited owner comments")

    def test_verification_rejects_comment_body_hash_mismatch(self):
        record = self._signed_record()
        record["owner_source"]["raw_body_sha256"] = "0" * 64

        self._assert_verify_rejected(record, "raw-body digest")

    def test_verification_rejects_comment_record_id_mismatch(self):
        record = self._signed_record()
        record["owner_source"]["raw_body"] = record["owner_source"]["raw_body"].replace(
            f"record={record['record_id']}", f"record={'0' * 64}"
        )
        record["owner_source"]["raw_body_sha256"] = sha256(
            record["owner_source"]["raw_body"].encode("utf-8"))

        self._assert_verify_rejected(record, "recorded fields disagree")

    def test_verification_returns_exact_owner_line(self):
        record = self._signed_record()

        result = self._verify(record)

        self.assertEqual(record["owner_source"]["raw_body"], result["expected_text"])

    def test_cap_disposition_binds_exact_awf_review_verdict(self):
        verdict = self._review_verdict()

        result = self._verify_verdict(verdict)

        self.assertEqual(verdict["record_id"], result["review_verdict_record_id"])
        self.assertEqual(
            self.record["critic_artifact_binding"],
            result["critic_artifact_binding"])

    def test_cap_disposition_rejects_different_review_verdict_bytes(self):
        verdict = self._review_verdict()
        verdict["extra_retained_field"] = "changed"

        with self.assertRaisesRegex(ValidationError, "exact AWF review-verdict"):
            self._verify_verdict(verdict)

    def test_cap_disposition_rejects_review_verdict_from_another_context(self):
        for field, other in (
                ("project_id", "00000000-0000-0000-0000-000000000002"),
                ("issue_id", "AWF-99"),
                ("policy_hash", "7" * 64),
                ("candidate_id", "8" * 64)):
            with self.subTest(field=field):
                verdict = self._review_verdict()
                self.record["binding"][field] = other

                with self.assertRaisesRegex(ValidationError, "candidate context"):
                    self._verify_verdict(verdict)


def cap_request_fixture():
    case = OwnerDispositionTests(methodName="runTest")
    case.setUp()
    return HEAD, case.config, case.record, case._review_verdict()


if __name__ == "__main__":
    unittest.main()
