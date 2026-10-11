import unittest

from agentic import ValidationError
from agentic.closeout import require_jira_reconciliation_before_counts


class IntegrationQueueCloseoutTests(unittest.TestCase):
    def record(self, transition="jira-transition-1"):
        return {"pr_number": 17, "reviewed_head_sha": "1" * 40,
                "merge_commit_sha": "2" * 40,
                "jira_transition_record_id": transition}

    def test_ac11_jira_reconciliation_precedes_post_merge_count(self):
        result = require_jira_reconciliation_before_counts(self.record(), jira_enabled=True)
        self.assertEqual("PASS", result["status"])
        self.assertTrue(result["count_allowed"])
        self.assertTrue(result["jira_reconciled"])
        self.assertFalse(result["execution_authority"])

    def test_ac11_enabled_jira_without_reconciliation_fails_closed(self):
        with self.assertRaisesRegex(ValidationError, "JIRA_RECONCILED"):
            require_jira_reconciliation_before_counts(self.record(None), jira_enabled=True)

    def test_ac11_disabled_jira_needs_no_synthetic_transition(self):
        result = require_jira_reconciliation_before_counts(self.record(None), jira_enabled=False)
        self.assertTrue(result["count_allowed"])
        self.assertFalse(result["jira_reconciled"])
        with self.assertRaisesRegex(ValidationError, "cannot claim"):
            require_jira_reconciliation_before_counts(self.record(), jira_enabled=False)


if __name__ == "__main__":
    unittest.main()
