"""AWF-81 canonical owner approval for Tier 3 rounds beyond the cap."""
import base64
import copy
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, sha256
from agentic.gates import (evaluate,
                           overcap_approval_collector_receipt_sha256)
from agentic.lifecycle import definition, review_round_transition
from agentic.review_policy import authorize_tier3_round
from agentic.review_tiers import review_decision
from test_review_policy import (Fixture, bind_production_posting_fixture,
                                bind_review_admission)
import test_review_tiers_195_gates as gate_fixtures


CAP = 3
ROUND = 4
OWNER = 1001
RULING_AT = "2026-10-13T17:31:00Z"
POSTED_AT = "2026-10-13T17:32:00Z"
ROUND_STARTED_AT = "2026-10-13T17:33:00Z"
CANONICAL = (
    "AWF-OVERCAP-APPROVAL: round 4 of cap 3, approver 1001, "
    "ruling 2026-10-13T17:31:00Z"
)
GATE_CANONICAL = (
    "AWF-OVERCAP-APPROVAL: round 4 of cap 3, approver 1001, "
    "ruling 2026-09-09T11:57:00Z"
)


class Tier3OvercapApprovalTests(unittest.TestCase):
    def comment(self, *, body=CANONICAL, actor_id=OWNER, created_at=POSTED_AT,
                updated_at=None):
        return {
            "channel": "github_pr_comment",
            "comment_id": 41,
            "actor_id": actor_id,
            "created_at": created_at,
            "updated_at": created_at if updated_at is None else updated_at,
            "raw_body": body,
        }

    def authorize(self, comments):
        return authorize_tier3_round(
            ROUND,
            CAP,
            pr_comments=comments,
            trusted_owner_ids=[OWNER],
            round_started_at=ROUND_STARTED_AT,
        )

    def test_no_record_refuses_round_four(self):
        with self.assertRaisesRegex(ValidationError, "canonical owner approval"):
            self.authorize([])

    def test_canonical_owner_record_allows_exact_round(self):
        self.assertTrue(self.authorize([self.comment()]))

    def test_review_decision_admits_the_approved_round(self):
        result = review_decision(
            3,
            ROUND,
            overcap_approvals=[self.comment()],
            trusted_owner_ids=[OWNER],
            round_started_at=ROUND_STARTED_AT,
        )
        self.assertEqual("OWNER_REVIEW_REQUIRED", result["status"])

    def test_lifecycle_refuses_caller_constructed_approval_context(self):
        with self.assertRaisesRegex(ValidationError, "authenticated.*approval context"):
            review_round_transition(
                3,
                ROUND,
                overcap_approvals=[self.comment()],
                trusted_owner_ids=[OWNER],
                round_started_at=ROUND_STARTED_AT,
            )

    def test_record_allows_only_its_named_round(self):
        with self.assertRaisesRegex(ValidationError, "only round 4"):
            authorize_tier3_round(
                5,
                CAP,
                pr_comments=[self.comment()],
                trusted_owner_ids=[OWNER],
                round_started_at=ROUND_STARTED_AT,
            )

    def test_even_a_separately_canonical_round_five_record_is_refused(self):
        round_five = CANONICAL.replace("round 4", "round 5")
        with self.assertRaisesRegex(ValidationError, "only round 4"):
            authorize_tier3_round(
                5,
                CAP,
                pr_comments=[self.comment(body=round_five)],
                trusted_owner_ids=[OWNER],
                round_started_at=ROUND_STARTED_AT,
            )

    def test_wrong_round_or_cap_is_refused(self):
        wrong_round = CANONICAL.replace("round 4", "round 5")
        wrong_cap = CANONICAL.replace("cap 3", "cap 2")
        for body in (wrong_round, wrong_cap):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                self.authorize([self.comment(body=body)])

    def test_malformed_or_surrounded_line_is_refused(self):
        malformed = CANONICAL.replace("AWF-OVERCAP-APPROVAL:", "AWF-OVERCAP-APPROVAL")
        for body in (malformed, CANONICAL + "\nplease approve", "prefix " + CANONICAL):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                self.authorize([self.comment(body=body)])

    def test_untrusted_or_impersonated_approver_is_refused(self):
        with self.assertRaises(ValidationError):
            self.authorize([self.comment(actor_id=9999)])
        impersonated = CANONICAL.replace("approver 1001", "approver 9999")
        with self.assertRaises(ValidationError):
            self.authorize([self.comment(body=impersonated)])

    def test_record_posted_at_or_after_round_start_is_refused(self):
        for created_at in (ROUND_STARTED_AT, "2026-10-13T17:34:00Z"):
            with self.subTest(created_at=created_at), self.assertRaises(ValidationError):
                self.authorize([self.comment(created_at=created_at)])

    def test_edited_or_non_pr_record_is_refused(self):
        edited = self.comment(updated_at="2026-10-13T17:32:30Z")
        non_pr = self.comment()
        non_pr["channel"] = "ticket_comment"
        for record in (edited, non_pr):
            with self.subTest(record=record), self.assertRaises(ValidationError):
                self.authorize([record])

    def test_rounds_at_or_below_cap_need_no_overcap_record(self):
        for round_number in (1, 2, 3):
            with self.subTest(round_number=round_number):
                self.assertTrue(authorize_tier3_round(
                    round_number,
                    CAP,
                    pr_comments=[],
                    trusted_owner_ids=[],
                    round_started_at=ROUND_STARTED_AT,
                ))

    def test_tier_one_and_two_decisions_are_unchanged(self):
        self.assertEqual("QUALIFIED", review_decision(1, 1, latest_pass=True)["status"])
        self.assertEqual("CONTINUE", review_decision(2, 1)["status"])
        with self.assertRaises(ValidationError):
            review_decision(2, 4)


class Tier3OvercapGateTests(Fixture):
    """Exercise the production final-gate round-admission call path."""

    def setUp(self):
        super().setUp()
        gate_fixtures.ReviewTiers195GateTests._tier_three_candidate(self)
        gate_fixtures.ReviewTiers195GateTests._review_history(
            self, 4, historical_tier=3, terminal_tier=3,
        )

    def approval(self, *, body=GATE_CANONICAL, actor_id=OWNER,
                 created_at="2026-09-09T11:58:00Z"):
        return {
            "channel": "github_pr_comment",
            "comment_id": 41,
            "repository_id": self.bundle["candidate"]["repository_id"],
            "pr_number": self.bundle["candidate"]["pr_number"],
            "actor_id": actor_id,
            "created_at": created_at,
            "updated_at": created_at,
            "raw_body": body,
        }

    def authenticated_approval(self, registry, *, body=GATE_CANONICAL,
                               actor_id=OWNER,
                               created_at="2026-09-09T11:58:00Z",
                               round_started_at="2026-09-09T11:59:00Z"):
        approval = self.approval(
            body=body, actor_id=actor_id, created_at=created_at,
        )
        producer_id = next(iter(registry))
        registration = registry[producer_id]
        collector = copy.deepcopy(next(
            run for run in self.bundle["runs"] if run["role"] == "collector"
        ))
        collector.update(
            record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
            context_id=str(uuid.uuid4()), producer_id=producer_id,
        )
        self.bundle["runs"].append(collector)
        comment_url = (
            f"{self.bundle['candidate']['host']}/"
            f"{self.bundle['candidate']['repository']}/pull/"
            f"{self.bundle['candidate']['pr_number']}"
            f"#issuecomment-{approval['comment_id']}"
        )
        provider_response = canonical({
            "id": approval["comment_id"],
            "html_url": comment_url,
            "user": {"id": approval["actor_id"]},
            "created_at": approval["created_at"],
            "updated_at": approval["updated_at"],
            "body": approval["raw_body"],
            "head_sha": self.bundle["candidate"]["head_sha"],
        })
        approval.update(
            source="host_observation",
            observed_at=gate_fixtures.NOW,
            producer_id=producer_id,
            run_id=collector["run_id"],
            provider_kind="github",
            comment_url=comment_url,
            round_started_at=round_started_at,
            provider_response_bytes={
                "encoding": "base64",
                "data": base64.b64encode(provider_response).decode("ascii"),
            },
            provider_response_sha256=sha256(provider_response),
            collector_receipt_sha256="0" * 64,
        )
        basis = copy.deepcopy(approval)
        basis["collector_receipt_sha256"] = "0" * 64
        receipt = overcap_approval_collector_receipt_sha256(
            approval, sha256(canonical(basis)), registration,
        )
        approval["collector_receipt_sha256"] = receipt
        registration["receipts"][collector["run_id"]] = receipt
        return approval

    def gate(self, approval_factory=None):
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        self._owner_review()
        self._refresh_owner_posting_receipt(registry)
        if approval_factory is not None:
            self.bundle["overcap_approvals"] = [approval_factory(registry)]
        return evaluate(
            self.config, definition(), self.bundle, self.contracts,
            gate_fixtures.NOW, posting_collector_registry=registry,
        )

    def _owner_review(self, *, provider_evidence=True):
        return gate_fixtures.ReviewTiers195GateTests._owner_review(
            self, provider_evidence=provider_evidence,
        )

    def _refresh_owner_posting_receipt(self, registry):
        return gate_fixtures.ReviewTiers195GateTests._refresh_owner_posting_receipt(
            self, registry,
        )

    def test_lifecycle_admits_provider_authenticated_approval_context(self):
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        approval = self.authenticated_approval(registry)
        result = review_round_transition(
            3,
            ROUND,
            overcap_approval_context={
                "config": self.config,
                "approvals": [approval],
                "candidate": self.bundle["candidate"],
                "runs": {run["run_id"]: run for run in self.bundle["runs"]},
                "trusted_posting_collectors": registry,
            },
        )
        self.assertEqual("OWNER_REVIEW_REQUIRED", result["status"])

    def test_final_gate_admits_canonical_owner_approved_round_four(self):
        gate = self.gate(self.authenticated_approval)
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertIn(
            "urn:awf:input:overcap_approvals:"
            + fingerprint("gate-input", self.bundle["overcap_approvals"]),
            gate["gates"]["critic_current_tuple"]["evidence"],
        )

    def test_final_gate_refuses_missing_or_wrong_candidate_approval(self):
        with self.assertRaisesRegex(ValidationError, "canonical owner approval"):
            self.gate()

        def wrong_pr(registry):
            approval = self.authenticated_approval(registry)
            approval["pr_number"] += 1
            return approval

        with self.assertRaisesRegex(ValidationError, "provider observation"):
            self.gate(wrong_pr)

    def test_final_gate_rejects_bundle_only_approval_assertion(self):
        self.bundle["overcap_approvals"] = [self.approval()]
        with self.assertRaises(ValidationError):
            self.gate()

    def test_final_gate_verifies_every_approval_field_against_provider_bytes(self):
        mutations = {
            "comment id": lambda value: value.__setitem__("comment_id", 42),
            "actor": lambda value: value.__setitem__("actor_id", 9999),
            "created timestamp": lambda value: value.__setitem__(
                "created_at", "2026-09-09T11:57:30Z"),
            "updated timestamp": lambda value: value.__setitem__(
                "updated_at", "2026-09-09T11:57:30Z"),
            "body": lambda value: value.__setitem__("raw_body", "forged"),
            "round start": lambda value: value.__setitem__(
                "round_started_at", "2026-09-09T11:58:30Z"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                def tampered(registry, mutate=mutate):
                    approval = self.authenticated_approval(registry)
                    mutate(approval)
                    return approval

                with self.assertRaisesRegex(ValidationError, "provider observation"):
                    self.gate(tampered)

    def test_final_gate_uses_authenticated_round_start_not_verdict_time(self):
        def posted_after_start(registry):
            return self.authenticated_approval(
                registry,
                created_at="2026-09-09T11:59:30Z",
                round_started_at="2026-09-09T11:59:00Z",
            )

        with self.assertRaisesRegex(ValidationError, "before the round starts"):
            self.gate(posted_after_start)


if __name__ == "__main__":
    unittest.main()
