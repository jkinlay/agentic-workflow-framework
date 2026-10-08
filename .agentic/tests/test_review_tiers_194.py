import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from agentic import ValidationError
from agentic.lifecycle import review_round_transition
from agentic.review_tiers import (classify, diff_effect, review_decision,
                                   round_cap, specialists, verdict_record)


class ReviewTiers194Tests(unittest.TestCase):
    def setUp(self):
        self.config = {"execution": {"risk_tiers": {
            "tier1_eligible_paths": ["docs/**", "evidence/**"],
            "tier1_excluded_paths": [],
            "tier1_review": {"specialist_when_touching": ["security"]},
            "tier3_review": {"max_rounds": 3}}}}

    def test_tier1_single_pass_qualifies(self):
        self.assertEqual(1, round_cap(1))
        self.assertEqual("QUALIFIED", review_decision(1, 1, latest_pass=True)["status"])

    def test_tier2_has_three_rounds_and_refuses_fourth(self):
        self.assertEqual(3, round_cap(2))
        with self.assertRaises(ValidationError):
            review_decision(2, 4)

    def test_tier2_cap_ticket_p2_and_qualifies_after_ticket(self):
        finding = {"id": "F2", "severity": "MINOR"}
        result = review_decision(2, 3, latest_pass=True, open_findings=[finding])
        self.assertEqual("TICKET_P2", result["status"])
        result = review_decision(2, 3, latest_pass=True, open_findings=[finding],
                                 ticketed_p2_records=[{"finding_id": "F2", "ticket_key": "AWF-100"}])
        self.assertEqual("QUALIFIED", result["status"])

    def test_tier1_pass_qualifies_with_advisory_minor(self):
        self.assertEqual("QUALIFIED", review_decision(
            1, 1, latest_pass=True, open_findings=[{"id": "M", "severity": "MINOR"}])["status"])

    def test_cap_requires_authenticated_bounded_record(self):
        with self.assertRaises(ValidationError):
            review_decision(2, 4, owner_cap_disposition=True)
        self.assertEqual("CONTINUE", review_decision(2, 4, owner_cap_disposition={
            "decision": "EXTEND_ONE_CYCLE", "record_id": "owner-record",
            "authenticated": True, "bounded": True})["status"])

    def test_tier2_cap_p1_routes_owner(self):
        self.assertEqual("ROUTE_TO_OWNER", review_decision(
            2, 3, open_findings=[{"id": "F1", "severity": "MAJOR"}])["status"])

    def test_tier3_requires_owner_review(self):
        self.assertEqual("OWNER_REVIEW_REQUIRED", review_decision(3, 3, latest_pass=True)["status"])
        self.assertEqual("QUALIFIED", review_decision(3, 3, latest_pass=True, owner_review=True)["status"])

    def test_highest_precedence_and_escalation_evidence(self):
        result = classify(self.config, [".agentic/PROJECT_CONFIG.yaml"], risk_flags=["release"])
        self.assertEqual(3, result["tier"])
        self.assertEqual([2, 3], result["matched_tiers"])
        self.assertTrue(result["evidence"]["3"])
        classified = classify(self.config, ["src/app.py"], risk_flags=["security"])
        self.assertEqual(["security"], classified["risk_flags"])

    def test_base_only_keeps_history_but_diff_change_invalidates(self):
        self.assertFalse(diff_effect("a", "a", base_only=True)["invalidate"])
        self.assertTrue(diff_effect("a", "b", base_only=False)["invalidate"])
        self.assertTrue(review_round_transition(2, 2, previous_diff_sha="a", current_diff_sha="b")["history_preserved"])
        self.assertTrue(review_round_transition(2, 2, base_only=True, previous_diff_sha="a", current_diff_sha="a")["history_preserved"])

    def test_specialists_additive_and_verdict_links_are_required(self):
        self.assertEqual(["security"], specialists(self.config, 1, ["security", "operations"]))
        with self.assertRaises(ValidationError):
            verdict_record(pr_comment_url="", pr_body_link="https://example.test/pr#review",
                           verdict="PASS", tier=1, round_number=1, head_sha="a" * 40, reviewer_id="critic")
        record = verdict_record(pr_comment_url="https://example.test/comment/1",
                                pr_body_link="https://example.test/pr#verdict-1", verdict="PASS",
                                tier=1, round_number=1, head_sha="a" * 40, reviewer_id="critic")
        self.assertEqual("https://example.test/comment/1", record["pr_comment_url"])

    def test_boundary_findings_block(self):
        result = review_decision(1, 1, latest_pass=True,
                                 open_findings=[{"id": "B", "severity": "MINOR", "boundary_code": "SCOPE_ESCAPE"}])
        self.assertEqual("BLOCKED", result["status"])

    def test_run_cap_is_independent_from_review_cap(self):
        self.assertEqual(3, round_cap(2))
        self.assertEqual(1, round_cap(1))


if __name__ == "__main__":
    unittest.main()
