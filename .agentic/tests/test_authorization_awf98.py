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
from agentic.canonical import candidate_id, sha256  # noqa: E402
from agentic.gates import (  # noqa: E402
    cap_disposition_record_id,
    review_verdict_json,
    verify_disposition_review_binding,
)
from agentic.lifecycle import definition, review_round_transition  # noqa: E402
from agentic.policy import policy_hash  # noqa: E402


NOW = "2026-10-15T18:50:00Z"
HEAD = "b" * 40
SCHEMA = "review-cap-disposition"


class NoopContracts:
    def validate(self, _schema, _record):
        return None


CONTRACTS = NoopContracts()


class OwnerDispositionTests(unittest.TestCase):
    def setUp(self):
        self.config = {"project": {"id": "00000000-0000-0000-0000-000000000001"},
                       "github": {"repository_id": 123, "expected_actor_id": 2002},
                       "merge_gate": {"trusted_owner_ids": [1001]},
                       "validation": {"max_evidence_age_seconds": 1800}}
        self.candidate = {
            "repository_id": 123, "pr_number": 157,
            "target_base_sha": "a" * 40, "head_sha": HEAD,
        }
        self.binding = {
            "project_id": "00000000-0000-0000-0000-000000000001",
            "repository_id": 123, "issue_id": "AWF-98",
            "requirements_hash": "1" * 64, "contract_hash": "2" * 64,
            "policy_hash": policy_hash(self.config, definition()),
            "candidate_id": candidate_id(self.candidate),
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
        critic_record_id = str(uuid.uuid4())
        verdict = {
            "record_id": str(uuid.uuid4()),
            "run_id": self.record["critic_artifact_binding"]["critic_run_id"],
            "binding": copy.deepcopy(self.binding), "head_sha": HEAD,
            "round": 3, "owner_review": False, "verdict": "REQUEST_CHANGES",
            "critic_artifact_binding": copy.deepcopy(self.record["critic_artifact_binding"]),
            "candidate_binding": {
                "repository_id": self.binding["repository_id"],
                "pr_number": 157,
                "base_sha": "a" * 40,
                "head_sha": HEAD,
            },
            "critic_review": {
                "record_id": critic_record_id,
                "run_id": self.record["critic_artifact_binding"]["critic_run_id"],
                "round": 3,
                "head_sha": HEAD,
                "verdict": "REQUEST_CHANGES",
                "findings_sha256": "9" * 64,
            },
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

    def _verify_with_registered_verifier(self, record):
        runs = {
            record["run_id"]: {
                "role": "verifier",
                "producer_id": record["producer_id"],
                "context_id": "owner-disposition-verifier-context",
                "parent_run_id": None,
            }
        }
        return verify_owner_record(
            record, SCHEMA, self.config, CONTRACTS, NOW,
            head_sha=HEAD, binding=self.binding, runs=runs,
            excluded_contexts={"worker-context", "critic-context"},
            excluded_producers={"worker-producer", "critic-producer"})

    def _verify_verdict(self, verdict):
        return verify_disposition_review_binding(
            self.record, SCHEMA, verdict, CONTRACTS, HEAD,
            expected_binding=self.binding,
            config=self.config,
            assignment_record={"ticket_key": "AWF-98"},
            expected_policy_hash=self.binding["policy_hash"],
            expected_ticket_key="AWF-98",
        )

    def _assert_verify_rejected(self, record, pattern):
        with self.assertRaisesRegex(ValidationError, pattern):
            self._verify(record)

    def _signed_rescope(self, successor_ticket="AWF-200"):
        self.record["decision"] = "RESCOPE"
        self.record["successor_ticket"] = successor_ticket
        self.record["record_id"] = cap_disposition_record_id(self.record)
        return self._signed_record()

    def _owner_cap_context(self, verdict):
        return {
            "config": self.config,
            "contracts": CONTRACTS,
            "now": NOW,
            "head_sha": HEAD,
            "binding": self.binding,
            "candidate": self.candidate,
            "review_verdict": verdict,
            "assignment_record": {"ticket_key": "AWF-98"},
        }

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

    def test_disposition_rejects_automation_actor_as_trusted_owner(self):
        self.config["github"]["expected_actor_id"] = 1001

        with self.subTest(operation="request"), \
                self.assertRaisesRegex(ValidationError, "automation actor|owner"):
            make_owner_disposition_request(
                self.record, SCHEMA, self.config, head_sha=HEAD)

        with self.subTest(operation="verify"), \
                self.assertRaisesRegex(ValidationError, "automation actor|owner"):
            self._verify(self._signed_record())

    def test_registered_verifier_cannot_make_automation_owner_overlap_actionable(self):
        self.config["github"]["expected_actor_id"] = 1001

        with self.assertRaisesRegex(ValidationError, "automation actor|owner"):
            self._verify_with_registered_verifier(self._signed_record())

    def test_cap_disposition_documentation_matches_signed_subject_grammar(self):
        documentation = (
            ROOT / ".agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`cycles/extensions:ids/successor`", documentation)

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

    def test_rescope_rejects_forged_successor_ticket(self):
        record = self._signed_rescope()
        record["successor_ticket"] = "AWF-999"

        self._assert_verify_rejected(record, "record ID|recorded fields")

    def test_rescope_rejects_replayed_disposition_with_different_successor(self):
        record = self._signed_rescope()
        record["successor_ticket"] = "AWF-999"
        record["record_id"] = cap_disposition_record_id(record)

        self._assert_verify_rejected(record, "recorded fields")

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

    def test_cap_disposition_rejects_cross_ticket_verdict_and_record(self):
        verdict = self._review_verdict()
        verdict["binding"]["issue_id"] = "AWF-99"
        self.record["binding"]["issue_id"] = "AWF-99"

        with self.assertRaisesRegex(ValidationError, "ticket|candidate context"):
            self._verify_verdict(verdict)

    def test_cap_disposition_rejects_tampered_candidate_binding_head(self):
        verdict = self._review_verdict()
        verdict["candidate_binding"]["head_sha"] = "c" * 40

        with self.assertRaisesRegex(ValidationError, "candidate_binding|candidate head"):
            self._verify_verdict(verdict)

    def test_cap_disposition_rejects_tampered_critic_review_head(self):
        verdict = self._review_verdict()
        verdict["critic_review"]["head_sha"] = "c" * 40

        with self.assertRaisesRegex(ValidationError, "critic_review|critic review"):
            self._verify_verdict(verdict)

    def test_lifecycle_extension_rejects_different_verdict_artifact(self):
        self.record["cap_extensions"] = 1
        verdict = self._review_verdict()
        record = self._signed_record()
        different = copy.deepcopy(verdict)
        different["record_id"] = str(uuid.uuid4())
        different["run_id"] = str(uuid.uuid4())
        different["critic_artifact_binding"] = {
            **different["critic_artifact_binding"],
            "critic_run_id": different["run_id"],
            "result_sha256": "7" * 64,
        }

        with self.assertRaisesRegex(ValidationError, "exact AWF review-verdict"):
            review_round_transition(
                2, 4, owner_cap_disposition=record,
                owner_cap_context=self._owner_cap_context(different))

    def test_lifecycle_extension_rejects_wrong_verdict_head(self):
        self.record["cap_extensions"] = 1
        verdict = self._review_verdict()
        record = self._signed_record()
        verdict["head_sha"] = "c" * 40
        verdict["critic_artifact_binding"]["head_sha"] = verdict["head_sha"]

        with self.assertRaisesRegex(ValidationError, "exact candidate head"):
            review_round_transition(
                2, 4, owner_cap_disposition=record,
                owner_cap_context=self._owner_cap_context(verdict))

    def test_lifecycle_extension_rejects_tampered_verdict(self):
        self.record["cap_extensions"] = 1
        verdict = self._review_verdict()
        record = self._signed_record()
        verdict["extra_retained_field"] = "tampered"

        with self.assertRaisesRegex(ValidationError, "exact AWF review-verdict"):
            review_round_transition(
                2, 4, owner_cap_disposition=record,
                owner_cap_context=self._owner_cap_context(verdict))

    def test_lifecycle_extension_rejects_disposition_signed_under_stale_policy(self):
        self.record["cap_extensions"] = 1
        stale_policy_hash = "3" * 64
        self.binding["policy_hash"] = stale_policy_hash
        self.record["binding"]["policy_hash"] = stale_policy_hash
        verdict = self._review_verdict()
        record = self._signed_record()

        with self.assertRaisesRegex(ValidationError, "policy"):
            review_round_transition(
                2, 4, owner_cap_disposition=record,
                owner_cap_context=self._owner_cap_context(verdict))

    def test_lifecycle_extension_rejects_stale_candidate_identity(self):
        self.record["cap_extensions"] = 1
        verdict = self._review_verdict()
        record = self._signed_record()
        self.candidate["diff_sha256"] = "7" * 64

        with self.assertRaisesRegex(ValidationError, "candidate binding"):
            review_round_transition(
                2, 4, owner_cap_disposition=record,
                owner_cap_context=self._owner_cap_context(verdict))


def cap_request_fixture():
    case = OwnerDispositionTests(methodName="runTest")
    case.setUp()
    return HEAD, case.config, case.record, case._review_verdict()


if __name__ == "__main__":
    unittest.main()
