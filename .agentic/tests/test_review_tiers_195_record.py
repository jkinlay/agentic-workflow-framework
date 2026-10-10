"""AWF-48 binding of AWF-16 review caps to intake assignments."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.review_policy import cap_disposition_plan, cap_status
from agentic.review_tiers import review_decision, round_cap, validate_round


class AssignmentRecordCaps(unittest.TestCase):
    def record(self, tier, cap, assignment_id="assignment-1"):
        record = {"assignment_id": assignment_id, "ticket_key": "AWF-48", "tier": tier,
                  "round_cap": cap, "owner_review_required": tier == 3}
        record["assignment_sha256"] = fingerprint("intake_assignment_record", record)
        return record

    def test_round_cap_and_escalation_read_assignment_record(self):
        assigned = self.record(2, 2)
        self.assertEqual(2, round_cap(2, assignment_record=assigned))
        self.assertEqual("ROUTE_TO_OWNER", review_decision(
            2, 2, open_findings=[{"id": "F1", "severity": "MAJOR"}],
            assignment_record=assigned)["status"])
        with self.assertRaisesRegex(ValidationError, "cap is 2"):
            validate_round(2, 3, assignment_record=assigned)

    def test_owner_change_applies_to_next_round(self):
        old = self.record(2, 2, "assignment-old")
        changed = self.record(3, 3, "assignment-new")
        with self.assertRaises(ValidationError):
            validate_round(2, 3, assignment_record=old)
        self.assertTrue(validate_round(3, 3, assignment_record=changed))
        self.assertEqual(3, review_decision(3, 3, assignment_record=changed)["cap"])

    def test_mutated_assignment_record_fails_closed(self):
        record = self.record(2, 2)
        record["round_cap"] = 1
        with self.assertRaisesRegex(ValidationError, "digest"):
            round_cap(2, assignment_record=record)

    def test_review_policy_cap_status_reads_assignment_record(self):
        cfg = {"execution": {"max_amendment_cycles": 3, "max_cap_extensions": 2}}
        status = cap_status(cfg, 2, 0, assignment_record=self.record(2, 2))
        self.assertTrue(status["cap_reached"])
        self.assertEqual(2, status["max_amendment_cycles"])

    def test_cap_disposition_cannot_mislabel_a_tier_three_assignment(self):
        cfg = {"execution": {"max_amendment_cycles": 3, "max_cap_extensions": 2}}
        disposition = {"decision": "EXTEND_ONE_CYCLE", "open_finding_ids": []}
        with self.assertRaisesRegex(ValidationError, "disagrees"):
            cap_disposition_plan(cfg, disposition, 3, 0, [], risk_tier=2,
                                 assignment_record=self.record(3, 3))

    def test_authenticated_extension_uses_the_recorded_tier_two_cap(self):
        disposition = {
            "decision": "EXTEND_ONE_CYCLE", "record_id": "owner-record",
            "created_at": "2026-10-10T08:00:00Z", "producer_id": "verifier",
            "run_id": "run", "binding": {}, "open_finding_ids": [],
            "notes": "extend", "cycles": 2, "cap_extensions": 1,
            "successor_ticket": None, "authorization_request_id": "request",
            "owner_source": {"channel": "github_pr_comment", "comment_id": 1},
            "evidence": ["urn:awf:test:evidence"],
        }
        self.assertTrue(validate_round(2, 3, owner_cap_disposition=disposition,
                                       assignment_record=self.record(2, 2)))


if __name__ == "__main__":
    unittest.main()
