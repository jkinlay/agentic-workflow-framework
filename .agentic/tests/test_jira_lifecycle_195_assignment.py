"""Controller-only Jira tier label/comment write and read-back tests."""
from pathlib import Path
import copy
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.jira_lifecycle import write_tier_assignment


class FakeJira:
    def __init__(self):
        self.issue = {"labels": ["ready", "tier-1"], "comments": []}
        self.calls = []

    def read_issue(self, key):
        self.calls.append("read")
        return copy.deepcopy(self.issue)

    def set_labels(self, key, labels, *, actor_id):
        self.calls.append("labels")
        self.issue["labels"] = list(labels)

    def add_comment(self, key, body, *, actor_id):
        self.calls.append("comment")
        self.issue["comments"].append({"id": "7", "body": body, "author_id": actor_id})


class JiraAssignmentWrites(unittest.TestCase):
    def assignment(self):
        return {"assignment_id": "assign-1", "ticket_key": "AWF-48", "tier": 3,
                "round_cap": 3, "owner_review_required": True, "reasons": ["governance"]}

    def test_controller_writes_label_and_comment_then_reads_back(self):
        jira = FakeJira()
        result = write_tier_assignment(jira, self.assignment(), controller_actor_id="controller",
                                       acting_actor_id="controller")
        self.assertEqual(["read", "labels", "comment", "read"], jira.calls)
        self.assertEqual(["ready", "tier-3"], jira.issue["labels"])
        self.assertEqual("VERIFIED", result["read_back_status"])
        self.assertEqual("7", result["comment_id"])

    def test_non_controller_identity_is_rejected_before_read_or_write(self):
        jira = FakeJira()
        with self.assertRaisesRegex(ValidationError, "controller"):
            write_tier_assignment(jira, self.assignment(), controller_actor_id="controller",
                                  acting_actor_id="worker")
        self.assertEqual([], jira.calls)


if __name__ == "__main__":
    unittest.main()
