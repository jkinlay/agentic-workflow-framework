import tempfile
import unittest
from pathlib import Path

from agentic.continuous_controller import (
    ContinuousControllerStore,
    post_merge_jira_progress,
)
from agentic.contracts import Contracts

ROOT = Path(__file__).resolve().parents[2]

NOW = "2026-10-02T10:00:00Z"
COUNTS = {"required": 1, "completed": 0, "acceptable": 0,
          "failed": 0, "stale": 0, "outstanding": 1}


def ticket(name, priority, disposition="ELIGIBLE", paths=None, **overrides):
    value = {
        "ticket": name,
        "priority": priority,
        "disposition": disposition,
        "actor": "worker-" + name,
        "reason": "eligible scoped action" if disposition == "ELIGIBLE" else "specific wait condition",
        "next_action": "continue " + name,
        "resume_trigger": "named prerequisite becomes current",
        "paths": paths or ["src/" + name.lower() + ".py"],
        "dependencies_satisfied": True,
        "budget_available": True,
        "cap_available": True,
        "review_independent": True,
        "exact_tuple": "base:a/head:b/tree:c/contract:d/review:e",
        "activity": "implement " + name,
        "verification_gate": "PENDING",
        "reviewer_completion": dict(COUNTS),
        "open_findings": 0,
        "jira_status": "In Progress",
    }
    value.update(overrides)
    return value


class ContinuousControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-controller-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "controller.sqlite3"
        self.store = ContinuousControllerStore(self.path, ["A", "B", "C"])

    def test_blocked_or_input_dependent_stream_does_not_pause_eligible_work(self):
        inventory = [
            ticket("QA-1", 1, "BLOCKED", reason="dependency unavailable"),
            ticket("QA-2", 2, "PAUSED_INPUT", reason="owner input required"),
            ticket("QA-3", 3),
        ]
        snapshot = self.store.schedule(inventory, NOW, host_capacity=3)
        self.assertEqual({item["state"] for item in snapshot}, {"WORKING", "BLOCKED", "PAUSED_INPUT"})
        working = next(item for item in snapshot if item["state"] == "WORKING")
        self.assertEqual(working["ticket"], "QA-3")
        self.assertTrue(all(item["reason"] and item["next_action"] and item["resume_trigger"] for item in snapshot))

    def test_completed_run_refills_same_transaction_with_next_eligible_action(self):
        first = self.store.schedule([ticket("QA-1", 1), ticket("QA-2", 2)], NOW, host_capacity=1)
        stream = next(item for item in first if item["state"] == "WORKING")
        self.assertEqual(stream["ticket"], "QA-1")
        after = self.store.finish_and_refill(stream["stream"], "QA-1", [ticket("QA-2", 2)],
                                             "2026-10-02T10:00:01Z", host_capacity=1)
        refilled = next(item for item in after if item["stream"] == stream["stream"])
        self.assertEqual((refilled["state"], refilled["ticket"]), ("WORKING", "QA-2"))
        self.assertNotIn(None, [item["state"] for item in after])

    def test_path_capacity_budget_cap_dependency_and_independence_are_fail_closed(self):
        inventory = [
            ticket("QA-1", 1, paths=["src/shared"]),
            ticket("QA-2", 2, paths=["SRC/shared/nested.py"]),
            ticket("QA-3", 3, budget_available=False),
            ticket("QA-4", 4, cap_available=False),
            ticket("QA-5", 5, dependencies_satisfied=False),
            ticket("QA-6", 6, review_independent=False),
        ]
        snapshot = self.store.schedule(inventory, NOW, host_capacity=2)
        self.assertEqual(sum(item["state"] == "WORKING" for item in snapshot), 1)
        self.assertTrue(all(item["state"] in {"WORKING", "BLOCKED", "PAUSED_INPUT", "COMPLETE"}
                            for item in snapshot))

    def test_restart_preserves_visible_stream_states_and_has_no_idle_value(self):
        before = self.store.schedule([ticket("QA-1", 1), ticket("QA-2", 2, "PAUSED_INPUT")],
                                     NOW, host_capacity=1)
        restarted = ContinuousControllerStore(self.path, ["A", "B", "C"])
        self.assertEqual(restarted.snapshot(), before)
        self.assertTrue(all(item["state"] in {"WORKING", "PAUSED_INPUT", "BLOCKED", "COMPLETE"}
                            for item in restarted.snapshot()))

    def test_default_and_configured_cadence_with_immediate_change_digest(self):
        first = self.store.digest(NOW)
        self.assertEqual((first["kind"], first["cadence_seconds"]), ("REGULAR", 900))
        self.assertIsNone(self.store.digest("2026-10-02T10:01:00Z"))
        self.store.schedule([ticket("QA-1", 1)], "2026-10-02T10:02:00Z", host_capacity=1)
        changed = self.store.digest("2026-10-02T10:02:01Z")
        self.assertEqual(changed["kind"], "CHANGE")
        self.assertEqual(len(changed["streams"]), 3)
        self.assertTrue(all(item["exact_tuple"] for item in changed["streams"]))
        # The change digest does not move the regular 15-minute deadline.
        self.assertEqual(self.store.digest("2026-10-02T10:15:00Z")["kind"], "REGULAR")
        self.store.set_cadence(300)
        self.assertEqual(self.store.digest("2026-10-02T10:20:00Z")["cadence_seconds"], 300)

    def test_generated_digest_contract_accepts_runtime_record(self):
        self.store.schedule([ticket("QA-1", 1)], NOW, host_capacity=1)
        Contracts(ROOT / ".agentic/schemas").validate("controller-status-digest", self.store.digest(NOW))


class JiraProgressTests(unittest.TestCase):
    def test_reconciliation_precedes_complete_paginated_stable_identity_counts(self):
        calls = []

        def reconcile(ticket_id):
            calls.append(("reconcile", ticket_id))
            return {"ticket": ticket_id, "status": "RECONCILED"}

        def page(scope, cursor):
            calls.append(("count", cursor))
            if cursor is None:
                return {"items": [{"id": "1", "issue_type": "TASK", "status_category": "TERMINAL"},
                                   {"id": "2", "issue_type": "EPIC", "status_category": "NON_TERMINAL"}],
                        "next_cursor": "p2", "complete": True}
            return {"items": [{"id": "3", "issue_type": "TASK", "status_category": "NON_TERMINAL"}],
                    "next_cursor": None, "complete": True}

        result = post_merge_jira_progress(jira_enabled=True, merged_ticket="QA-1",
            scope="project=QA AND fixVersion=1.9.3", observed_at=NOW,
            reconcile_merged_ticket=reconcile, fetch_scope_page=page)
        self.assertEqual(calls[0], ("reconcile", "QA-1"))
        self.assertEqual((result["closed"], result["remaining_open"]), (1, 1))
        self.assertEqual(result["scope"], "project=QA AND fixVersion=1.9.3")

    def test_disabled_jira_performs_no_reads_or_writes(self):
        calls = []
        result = post_merge_jira_progress(jira_enabled=False, merged_ticket="QA-1", scope="QA",
            observed_at=NOW, reconcile_merged_ticket=lambda value: calls.append("write"),
            fetch_scope_page=lambda scope, cursor: calls.append("read"))
        self.assertEqual(calls, [])
        self.assertEqual((result["jira_state"], result["closed"], result["remaining_open"]),
                         ("JIRA_DISABLED", "UNOBSERVED", "UNOBSERVED"))

    def test_unknown_or_external_mismatch_is_not_retried_and_not_counted(self):
        for status in ("UNKNOWN", "MISMATCH"):
            calls = []
            result = post_merge_jira_progress(jira_enabled=True, merged_ticket="QA-1", scope="QA", observed_at=NOW,
                reconcile_merged_ticket=lambda value, status=status: calls.append(value) or {"ticket": value, "status": status},
                fetch_scope_page=lambda scope, cursor: self.fail("count must follow proven reconciliation"))
            self.assertEqual(calls, ["QA-1"])
            self.assertEqual(result["closed"], "UNOBSERVED")

    def test_incomplete_pagination_duplicate_identity_or_unknown_category_is_unobserved(self):
        invalid_pages = [
            {"items": [], "next_cursor": None, "complete": False},
            {"items": [{"id": "1", "issue_type": "TASK", "status_category": "UNKNOWN"}],
             "next_cursor": None, "complete": True},
        ]
        for page in invalid_pages:
            result = post_merge_jira_progress(jira_enabled=True, merged_ticket="QA-1", scope="QA", observed_at=NOW,
                reconcile_merged_ticket=lambda value: {"ticket": value, "status": "RECONCILED"},
                fetch_scope_page=lambda scope, cursor, page=page: page)
            self.assertEqual((result["closed"], result["remaining_open"]), ("UNOBSERVED", "UNOBSERVED"))

        calls = []
        def cyclic(scope, cursor):
            calls.append(cursor)
            return {"items": [], "next_cursor": "p2" if cursor is None else "p1" if cursor == "p2" else "p2",
                    "complete": True}
        result = post_merge_jira_progress(jira_enabled=True, merged_ticket="QA-1", scope="QA", observed_at=NOW,
            reconcile_merged_ticket=lambda value: {"ticket": value, "status": "RECONCILED"}, fetch_scope_page=cyclic)
        self.assertEqual((result["closed"], result["remaining_open"]), ("UNOBSERVED", "UNOBSERVED"))
        self.assertLessEqual(len(calls), 3)

    def test_generated_jira_progress_contract_accepts_authoritative_and_unobserved_records(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        authoritative = post_merge_jira_progress(jira_enabled=True, merged_ticket="QA-1", scope="QA", observed_at=NOW,
            reconcile_merged_ticket=lambda value: {"ticket": value, "status": "RECONCILED"},
            fetch_scope_page=lambda scope, cursor: {"items": [], "next_cursor": None, "complete": True})
        contracts.validate("jira-progress", authoritative)
        contracts.validate("jira-progress", post_merge_jira_progress(jira_enabled=False, merged_ticket="QA-1",
            scope="QA", observed_at=NOW, reconcile_merged_ticket=lambda value: None,
            fetch_scope_page=lambda scope, cursor: None))


if __name__ == "__main__":
    unittest.main()
