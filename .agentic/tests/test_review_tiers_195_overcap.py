"""AWF-81 canonical owner approval for Tier 3 rounds beyond the cap."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.review_policy import authorize_tier3_round
from agentic.review_tiers import review_decision


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

    def test_record_allows_only_its_named_round(self):
        with self.assertRaisesRegex(ValidationError, "canonical owner approval"):
            authorize_tier3_round(
                5,
                CAP,
                pr_comments=[self.comment()],
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


if __name__ == "__main__":
    unittest.main()
