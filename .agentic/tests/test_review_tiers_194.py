import copy
import json
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

    def test_configured_cap_cannot_raise_mandatory_three_round_ceiling(self):
        with self.assertRaisesRegex(ValidationError, "mandatory"):
            round_cap(3, {"execution": {"risk_tiers": {"tier3_review": {"max_rounds": 4}}}})

    def test_awf16_195_r3_001_tier2_cap_ticket_p2_and_qualifies_after_ticket(self):
        """AWF16-195-R3-001: a Tier 2 cap needs retained ticket keys."""
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
        disposition = {
            "decision": "EXTEND_ONE_CYCLE", "record_id": "owner-record",
            "created_at": "2026-09-09T12:00:00Z", "producer_id": "verifier",
            "run_id": "run", "binding": {}, "open_finding_ids": [],
            "notes": "extend", "cycles": 3, "cap_extensions": 1,
            "successor_ticket": None, "authorization_request_id": "request",
            "owner_source": {"channel": "github_pr_comment", "comment_id": 1},
            "evidence": ["urn:awf:fixture:evidence"]}
        self.assertEqual("CONTINUE", review_decision(2, 4,
                         owner_cap_disposition=disposition)["status"])

    def test_awf16_r1_006_lifecycle_refuses_unverified_or_unbounded_cap_rounds(self):
        """AWF16-R1-006: an extension cannot authorize rounds 5, 6, or 100."""
        from agentic.lifecycle import review_round_transition
        disposition = {"decision": "EXTEND_ONE_CYCLE", "record_id": "owner-record",
                        "created_at": "2026-09-09T12:00:00Z", "producer_id": "verifier",
                        "run_id": "run", "binding": {}, "open_finding_ids": [],
                        "notes": "extend", "cycles": 3, "cap_extensions": 1,
                        "successor_ticket": None, "authorization_request_id": "request",
                        "owner_source": {"channel": "github_pr_comment", "comment_id": 1},
                        "evidence": ["urn:awf:fixture:evidence"]}
        with self.assertRaises(ValidationError):
            review_round_transition(2, 4, owner_cap_disposition=disposition)
        with self.assertRaises(ValidationError):
            review_round_transition(2, 5, owner_cap_disposition=disposition,
                                    owner_cap_verified=True)
        with self.assertRaises(ValidationError):
            review_decision(2, 5, owner_cap_disposition=disposition)
        with self.assertRaises(ValidationError):
            review_decision(3, 4, owner_cap_disposition=disposition)

    def test_tier2_cap_p1_routes_owner(self):
        self.assertEqual("ROUTE_TO_OWNER", review_decision(
            2, 3, open_findings=[{"id": "F1", "severity": "MAJOR"}])["status"])

    def test_tier3_requires_owner_review(self):
        self.assertEqual("OWNER_REVIEW_REQUIRED", review_decision(3, 3, latest_pass=True)["status"])
        owner = {"owner_review": True,
                 "record_id": "owner-record", "run_id": "verify-run",
                 "producer_id": "verifier", "owner_id": 1001,
                 "verdict": "PASS",
                 "candidate_binding": {"repository_id": 1, "pr_number": 1,
                                        "base_sha": "a" * 40, "head_sha": "b" * 40},
                 }
        self.assertEqual("QUALIFIED", review_decision(
            3, 3, latest_pass=True, owner_review=owner,
            configured_owner_ids=[1001],
            expected_candidate_binding=owner["candidate_binding"])["status"])
        self.assertEqual("OWNER_REVIEW_REQUIRED", review_decision(
            3, 3, latest_pass=True, owner_review={**owner, "owner_id": 999},
            configured_owner_ids=[1001],
            expected_candidate_binding=owner["candidate_binding"])["status"])
        self.assertEqual("OWNER_REVIEW_REQUIRED", review_decision(
            3, 3, latest_pass=True, owner_review=True)["status"])

    def test_upgrade_adds_only_tier3_lines_and_is_idempotent(self):
        from agentic.upgrade import review_tier_defaults
        before = (b"# retained full-line comment\n"
                  b"template:\n  expected_workflow_version: '1.9.3'\n"
                  b"execution: # retained execution comment\n"
                  b"  z_key: \"keep\" # retained inline comment\n"
                  b"  risk_tiers: # retained tier comment\n"
                  b"    tier1_review: {max_rounds: 1} # retained tier inline\n"
                  b"    # retained nested comment\n"
                  b"    tier2_review:\n      max_rounds: 3\n"
                  b"  a_key: 'after'\n")
        added = (b"    tier3_review:\n"
                 b"      roles: [critic, specialist]\n"
                 b"      findings: blocking\n"
                 b"      max_rounds: 3\n")
        expected = before.replace(b"  a_key: 'after'\n", added + b"  a_key: 'after'\n")
        after = review_tier_defaults(before)
        self.assertEqual(expected, after)
        self.assertEqual(after, review_tier_defaults(after))
        self.assertEqual(added, after[len(before) - len(b"  a_key: 'after'\n"):len(before) - len(b"  a_key: 'after'\n") + len(added)])

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

    def test_fourth_round_receipt_requires_the_owner_cap_record(self):
        disposition = {
            "decision": "EXTEND_ONE_CYCLE", "record_id": "owner-record",
            "created_at": "2026-09-09T12:00:00Z", "producer_id": "verifier",
            "run_id": "run", "binding": {}, "open_finding_ids": [],
            "notes": "extend", "cycles": 3, "cap_extensions": 1,
            "successor_ticket": None, "authorization_request_id": "request",
            "owner_source": {"channel": "github_pr_comment", "comment_id": 1},
            "evidence": ["urn:awf:fixture:evidence"]}
        with self.assertRaises(ValidationError):
            verdict_record(pr_comment_url="https://example.test/comment/4",
                           pr_body_link="https://example.test/pr#verdict-4",
                           verdict="PASS", tier=2, round_number=4,
                           head_sha="a" * 40, reviewer_id="critic")
        record = verdict_record(pr_comment_url="https://example.test/comment/4",
                                pr_body_link="https://example.test/pr#verdict-4",
                                verdict="PASS", tier=2, round_number=4,
                                head_sha="a" * 40, reviewer_id="critic",
                                owner_cap_disposition=disposition)
        self.assertEqual(4, record["round"])

    def test_boundary_findings_block(self):
        result = review_decision(1, 1, latest_pass=True,
                                 open_findings=[{"id": "B", "severity": "MINOR",
                                                 "basis": {"boundary_code": "SCOPE_ESCAPE"}}])
        self.assertEqual("BLOCKED", result["status"])

    def test_run_cap_is_independent_from_review_cap(self):
        self.assertEqual(3, round_cap(2))
        self.assertEqual(1, round_cap(1))

    def test_real_apply_chain_dispatches_194_to_195_and_preserves_yaml_bytes(self):
        from agentic.upgrade import apply_chain
        table = {"target": "1.9.5", "versions": {
            "1.9.4": {"migration": {"to": "1.9.5", "new_required_settings": []}},
        }}
        target = {"source_manifest_sha256": "a" * 64, "_immutable_files": {}}
        config = (b"# retained comment\ntemplate:\n  expected_workflow_version: 1.9.4\n"
                  b"execution:\n  risk_tiers:\n"
                  b"    tier1_review: {max_rounds: 1} # retained inline\n")
        receipt = json.dumps({"template_version": "1.9.4"}).encode()
        provenance = json.dumps({"template": {"version": "1.9.4"}}).encode()
        migrated, reports = apply_chain(table, "1.9.4", config, None, receipt,
                                        provenance, {}, target)
        self.assertEqual("1.9.4", reports[0]["from"])
        self.assertEqual("1.9.5", reports[0]["to"])
        self.assertIn(b"# retained comment", migrated.project_config)
        self.assertIn(b"# retained inline", migrated.project_config)
        self.assertIn(b"tier3_review", migrated.project_config)
        self.assertEqual(1, migrated.project_config.count(b"tier3_review:"))
        self.assertIn("tier3_review", migrated.project_config.decode("utf-8"))

    def test_real_apply_chain_preserves_json_format_and_is_idempotent(self):
        from agentic.upgrade import apply_chain, review_tier_defaults
        table = {"target": "1.9.5", "versions": {
            "1.9.4": {"migration": {"to": "1.9.5", "new_required_settings": []}},
        }}
        target = {"source_manifest_sha256": "a" * 64, "_immutable_files": {}}
        config = json.dumps({"version": 3, "template": {"expected_workflow_version": "1.9.4"},
                             "execution": {"risk_tiers": {"tier1_review": {"max_rounds": 1}}}}, indent=2).encode()
        receipt = json.dumps({"template_version": "1.9.4"}).encode()
        provenance = json.dumps({"template": {"version": "1.9.4"}}).encode()
        migrated, reports = apply_chain(table, "1.9.4", config, None, receipt, provenance, {}, target)
        parsed = json.loads(migrated.project_config)
        self.assertEqual(3, parsed["execution"]["risk_tiers"]["tier3_review"]["max_rounds"])
        self.assertEqual(migrated.project_config, review_tier_defaults(migrated.project_config))

    def test_awf16_195_r3_002_inline_yaml_execution_mapping_gets_one_tier3_mapping(self):
        """AWF16-195-R3-002: inline YAML gets one valid additive mapping."""
        from agentic.upgrade import review_tier_defaults
        import agentic.canonical as canonical
        before = b"template:\n  expected_workflow_version: 1.9.4\nexecution: {risk_tiers: {tier1_review: {max_rounds: 1}}} # keep\n"
        after = review_tier_defaults(before)
        parsed = canonical.load_yaml(after)
        self.assertEqual(3, parsed["execution"]["risk_tiers"]["tier3_review"]["max_rounds"])
        self.assertEqual(1, after.count(b"tier3_review"))


if __name__ == "__main__":
    unittest.main()
