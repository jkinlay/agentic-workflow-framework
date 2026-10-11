import unittest

from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.closeout import require_jira_reconciliation_before_counts


class IntegrationQueueCloseoutTests(unittest.TestCase):
    def record(self, transition="VALID"):
        if transition == "VALID":
            transition = self.reconciliation()["record_id"]
        return {"ticket": "AWF-17", "pr_number": 17, "reviewed_head_sha": "1" * 40,
                "merge_commit_sha": "2" * 40,
                "jira_transition_record_id": transition}

    def reconciliation(self, **changes):
        value = {"event": "JIRA_RECONCILED", "ticket": "AWF-17", "pr_number": 17,
            "reviewed_head_sha": "1" * 40,
            "merge_commit_sha": "2" * 40, "transition_status": "SUCCEEDED",
            "read_back": {"status": "Done", "actor_id": "controller-1",
                          "observed_at": "2026-10-10T10:01:00Z"},
            "producer_id": "controller-1", "observed_at": "2026-10-10T10:01:00Z"}
        value.update(changes)
        value["record_id"] = fingerprint("jira_reconciled", value)
        return value

    def test_ac11_jira_reconciliation_precedes_post_merge_count(self):
        result = require_jira_reconciliation_before_counts(
            self.record(), jira_enabled=True,
            jira_reconciliation_record=self.reconciliation())
        self.assertEqual("PASS", result["status"])
        self.assertTrue(result["count_allowed"])
        self.assertTrue(result["jira_reconciled"])
        self.assertFalse(result["execution_authority"])

    def test_ac11_enabled_jira_without_reconciliation_fails_closed(self):
        with self.assertRaisesRegex(ValidationError, "JIRA_RECONCILED"):
            require_jira_reconciliation_before_counts(self.record(None), jira_enabled=True)

    def test_ac11_unrelated_transition_id_and_tuple_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "resolve"):
            require_jira_reconciliation_before_counts(
                self.record("00000000-0000-0000-0000-000000000000"), jira_enabled=True,
                jira_reconciliation_record=self.reconciliation())
        with self.assertRaisesRegex(ValidationError, "closeout tuple"):
            wrong_tuple = self.reconciliation(pr_number=18)
            require_jira_reconciliation_before_counts(
                self.record(wrong_tuple["record_id"]), jira_enabled=True,
                jira_reconciliation_record=wrong_tuple)

    def test_ac11_transition_and_readback_must_succeed(self):
        with self.assertRaisesRegex(ValidationError, "did not succeed"):
            failed = self.reconciliation(transition_status="FAILED")
            require_jira_reconciliation_before_counts(
                self.record(failed["record_id"]), jira_enabled=True,
                jira_reconciliation_record=failed)
        bad = self.reconciliation(read_back={"status": "In Review", "actor_id": "controller-1",
                                              "observed_at": "2026-10-10T10:01:00Z"})
        with self.assertRaisesRegex(ValidationError, "Done readback"):
            require_jira_reconciliation_before_counts(
                self.record(bad["record_id"]), jira_enabled=True, jira_reconciliation_record=bad)

    def test_ac11_disabled_jira_needs_no_synthetic_transition(self):
        result = require_jira_reconciliation_before_counts(self.record(None), jira_enabled=False)
        self.assertTrue(result["count_allowed"])
        self.assertFalse(result["jira_reconciled"])
        with self.assertRaisesRegex(ValidationError, "cannot claim"):
            require_jira_reconciliation_before_counts(self.record(), jira_enabled=False)


if __name__ == "__main__":
    unittest.main()
