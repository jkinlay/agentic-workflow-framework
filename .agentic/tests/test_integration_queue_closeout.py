from pathlib import Path
import unittest

from agentic import ValidationError
from agentic.closeout import require_jira_reconciliation_before_counts
from agentic.contracts import Contracts


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-10-10T10:01:00Z"
TRANSITION_ID = "10000000-0000-0000-0000-000000000001"
MERGE_RESULT_ID = "10000000-0000-0000-0000-000000000002"


class IntegrationQueueCloseoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")

    def binding(self, issue="AWF-17"):
        return {"project_id": "10000000-0000-0000-0000-000000000003",
                "repository_id": 17, "issue_id": issue,
                "requirements_hash": "a" * 64, "contract_hash": "b" * 64,
                "policy_hash": "c" * 64, "candidate_id": "d" * 64}

    def record(self, transition="VALID"):
        if transition == "VALID":
            transition = TRANSITION_ID
        return {"schema_version": 3,
                "record_id": "10000000-0000-0000-0000-000000000004",
                "created_at": NOW, "producer_id": "controller-1",
                "run_id": "10000000-0000-0000-0000-000000000005",
                "binding": self.binding(), "pr_number": 17,
                "reviewed_head_sha": "1" * 40, "base_sha": "3" * 40,
                "merge_commit_sha": "2" * 40, "merge_tree_sha": "4" * 40,
                "bound_files": [{"path": "src/task.py", "blob_sha": "5" * 40,
                                 "sha256": "e" * 64}],
                "gate_record_id": "10000000-0000-0000-0000-000000000006",
                "review_record_ids": ["10000000-0000-0000-0000-000000000007"],
                "jira_transition_record_id": transition,
                "evidence": ["urn:awf:fixture:closeout"]}

    def reconciliation(self, **changes):
        value = {"schema_version": 3, "record_id": TRANSITION_ID,
                 "created_at": NOW, "producer_id": "controller-1",
                 "run_id": "10000000-0000-0000-0000-000000000008",
                 "binding": self.binding(),
                 "operation_id": "10000000-0000-0000-0000-000000000009",
                 "merge_result_id": MERGE_RESULT_ID,
                 "jira_provider": {"cloud_id": "cloud-1", "site": "https://jira.example.test",
                                   "project_id": "17", "project_key": "AWF",
                                   "controller_actor_id": "controller-actor-1"},
                 "lifecycle_event": "JIRA_RECONCILED", "from_status_id": "3",
                 "to_status_id": "Done", "transition_id": "31", "status": "SUCCEEDED",
                 "evidence": ["urn:awf:fixture:jira-transition"],
                 "read_back": {"observed_status": "Done",
                               "observed_actor": "controller-actor-1",
                               "observed_at": NOW}}
        value.update(changes)
        return value

    def gate(self, closeout, reconciliation=None, *, enabled=True):
        return require_jira_reconciliation_before_counts(
            closeout, jira_enabled=enabled, contracts=self.contracts,
            jira_reconciliation_record=reconciliation)

    def test_ac11_schema_valid_jira_reconciliation_precedes_post_merge_count(self):
        closeout, transition = self.record(), self.reconciliation()
        self.contracts.validate("closeout-record", closeout)
        self.contracts.validate("jira-transition", transition)
        result = self.gate(closeout, transition)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("AWF-17", result["ticket"])
        self.assertTrue(result["count_allowed"])
        self.assertTrue(result["jira_reconciled"])
        self.assertFalse(result["execution_authority"])

    def test_ac11_enabled_jira_without_reconciliation_fails_closed(self):
        with self.assertRaisesRegex(ValidationError, "jira-transition"):
            self.gate(self.record(None))

    def test_ac11_unrelated_transition_id_and_binding_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "resolve"):
            self.gate(self.record("10000000-0000-0000-0000-000000000099"),
                      self.reconciliation())
        wrong = self.reconciliation(binding=self.binding("AWF-18"))
        with self.assertRaisesRegex(ValidationError, "closeout candidate"):
            self.gate(self.record(), wrong)

    def test_ac11_transition_and_readback_must_succeed(self):
        with self.assertRaisesRegex(ValidationError, "did not succeed"):
            self.gate(self.record(), self.reconciliation(status="FAILED"))
        bad = self.reconciliation(read_back={"observed_status": "In Review",
            "observed_actor": "controller-actor-1", "observed_at": NOW})
        with self.assertRaisesRegex(ValidationError, "target-status readback"):
            self.gate(self.record(), bad)
        missing_observation_time = self.reconciliation(read_back={"observed_status": "Done",
            "observed_actor": "controller-actor-1", "observed_at": None})
        with self.assertRaisesRegex(ValidationError, "target-status readback"):
            self.gate(self.record(), missing_observation_time)

    def test_ac11_disabled_jira_needs_no_synthetic_transition(self):
        result = self.gate(self.record(None), enabled=False)
        self.assertTrue(result["count_allowed"])
        self.assertFalse(result["jira_reconciled"])
        with self.assertRaisesRegex(ValidationError, "cannot claim"):
            self.gate(self.record(), self.reconciliation(), enabled=False)

    def test_ac11_rejects_old_synthetic_record_shapes(self):
        synthetic = {"ticket": "AWF-17", "pr_number": 17,
                     "reviewed_head_sha": "1" * 40,
                     "merge_commit_sha": "2" * 40,
                     "jira_transition_record_id": TRANSITION_ID}
        with self.assertRaisesRegex(ValidationError, "closeout-record"):
            self.gate(synthetic, self.reconciliation())


if __name__ == "__main__":
    unittest.main()
