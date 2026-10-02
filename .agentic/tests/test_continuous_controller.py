import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from agentic.continuous_controller import (
    ContinuousControllerStore,
    production_controller_cycle,
    production_jira_lifecycle,
    production_post_merge_progress,
)
from agentic.contracts import Contracts
from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.cli import local_semantics

ROOT = Path(__file__).resolve().parents[2]

NOW = "2026-10-02T10:00:00Z"
BINDING = {"cloud_id": "cloud-1", "project_id": "project-1", "actor_id": "actor-1"}
COUNTS = {"required": 1, "completed": 0, "acceptable": 0,
          "failed": 0, "stale": 0, "outstanding": 1}
INVENTORY_BINDING = {"project_id": "project-1", "repository_id": "repository-1",
                     "scope_sha256": "a" * 64}


def inventory_observation(now, tickets):
    return {"source": "host_observation", "observed_at": now,
            "binding": dict(INVENTORY_BINDING), "complete": True,
            "inventory_sha256": fingerprint("controller-inventory", {
                "binding": INVENTORY_BINDING, "tickets": tickets}), "tickets": tickets}


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
        self.assertEqual(self.store.digest("2026-10-02T10:00:30Z"), first)
        self.store.acknowledge_digest(first["delivery_id"], "2026-10-02T10:00:31Z")
        self.assertIsNone(self.store.digest("2026-10-02T10:01:00Z"))
        self.store.schedule([ticket("QA-1", 1)], "2026-10-02T10:02:00Z", host_capacity=1)
        changed = self.store.digest("2026-10-02T10:02:01Z")
        self.assertEqual(changed["kind"], "CHANGE")
        self.assertEqual(len(changed["streams"]), 3)
        self.assertTrue(all(item["exact_tuple"] for item in changed["streams"]))
        self.store.acknowledge_digest(changed["delivery_id"], "2026-10-02T10:02:02Z")
        # The change digest does not move the regular 15-minute deadline.
        regular = self.store.digest("2026-10-02T10:15:00Z")
        self.assertEqual(regular["kind"], "REGULAR")
        self.store.acknowledge_digest(regular["delivery_id"], "2026-10-02T10:15:01Z")
        self.store.set_cadence(300)
        self.assertEqual(self.store.digest("2026-10-02T10:20:00Z")["cadence_seconds"], 300)

    def test_generated_digest_contract_accepts_runtime_record(self):
        self.store.schedule([ticket("QA-1", 1)], NOW, host_capacity=1)
        digest = self.store.digest(NOW)
        Contracts(ROOT / ".agentic/schemas").validate("controller-status-digest", digest)
        local_semantics("controller-status-digest", digest)
        digest["streams"][0]["reviewer_completion"]["outstanding"] = 99
        with self.assertRaisesRegex(ValidationError, "counts"):
            local_semantics("controller-status-digest", digest)

    def test_running_work_is_revalidated_and_completed_ticket_is_not_redispatched(self):
        self.store.schedule([ticket("QA-1", 1)], NOW, host_capacity=1)
        blocked = self.store.schedule([ticket("QA-1", 1, dependencies_satisfied=False)],
                                      "2026-10-02T10:00:01Z", host_capacity=1)
        self.assertEqual(next(item for item in blocked if item["ticket"] == "QA-1")["state"], "BLOCKED")
        fresh = ContinuousControllerStore(Path(self.temporary.name) / "finished.sqlite3", ["A"])
        fresh.schedule([ticket("QA-2", 1)], NOW, host_capacity=1)
        with self.assertRaisesRegex(ValidationError, "Completed ticket remains dispatch-eligible"):
            fresh.finish_and_refill("A", "QA-2", [ticket("QA-2", 1)],
                                    "2026-10-02T10:00:01Z", host_capacity=1)

    def test_path_aliases_overlap_and_external_or_traversal_paths_are_rejected(self):
        snapshot = self.store.schedule([ticket("QA-1", 1, paths=["src\\shared"]),
                                        ticket("QA-2", 2, paths=["SRC/shared/nested.py"])],
                                       NOW, host_capacity=2)
        self.assertEqual(sum(item["state"] == "WORKING" for item in snapshot), 1)
        drive_absolute = "".join(("C", ":/outside"))
        for path in ("../outside", "src/../outside", drive_absolute, "//server/share"):
            with self.subTest(path=path), self.assertRaisesRegex(ValidationError, "path"):
                self.store.schedule([ticket("BAD", 1, paths=[path])], NOW, host_capacity=1)

    def test_digest_outbox_survives_restart_and_clock_rollback_fails_closed(self):
        first = self.store.digest(NOW)
        restarted = ContinuousControllerStore(self.path, ["A", "B", "C"])
        self.assertEqual(restarted.digest("2026-10-02T10:05:00Z"), first)
        restarted.acknowledge_digest(first["delivery_id"], "2026-10-02T10:05:01Z")
        with self.assertRaisesRegex(ValidationError, "clock moved backwards"):
            restarted.digest("2026-10-02T09:59:59Z")

    def test_controller_state_is_rejected_inside_declared_worktree(self):
        worktree = Path(self.temporary.name) / "worktree"
        worktree.mkdir()
        with self.assertRaisesRegex(ValidationError, "outside"):
            ContinuousControllerStore(worktree / "state.sqlite3", ["A"],
                                      worktree_roots=[worktree])

    def test_controller_state_rejects_wrong_application_or_schema_identity(self):
        for name, application_id, version in (("wrong-app.sqlite3", 7, 1),
                                               ("wrong-version.sqlite3", 0x41574631, 99)):
            path = Path(self.temporary.name) / name
            db = sqlite3.connect(path)
            try:
                db.execute(f"PRAGMA application_id={application_id}")
                db.execute(f"PRAGMA user_version={version}")
                db.commit()
            finally:
                db.close()
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError, "identity|version"):
                ContinuousControllerStore(path, ["A"])

    def test_production_workflow_cli_exposes_configured_controller(self):
        worktree = Path(self.temporary.name) / "worker"
        worktree.mkdir()
        command = [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                   "--root", str(ROOT), "controller", "--state", str(self.path),
                   "--stream", "A", "--stream", "B", "--stream", "C",
                   "--worktree-root", str(worktree), "snapshot"]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(json.loads(completed.stdout)["streams"]), 3)

    def test_production_cycle_observes_inventory_dispatches_and_delivers_digest(self):
        inventory = [ticket("QA-1", 1), ticket("QA-2", 2), ticket("QA-3", 3)]
        calls = []
        def dispatch(payload):
            calls.append(("dispatch", payload["ticket"]))
            return {key: payload[key] for key in ("dispatch_id", "stream", "ticket", "exact_tuple")} | {
                "status": "ACCEPTED", "observed_at": NOW}
        def deliver(digest):
            calls.append(("deliver", digest["delivery_id"]))
            return {"delivery_id": digest["delivery_id"], "status": "DELIVERED", "observed_at": NOW}
        result = production_controller_cycle(self.store, now=NOW, host_capacity=3,
            inventory_binding=INVENTORY_BINDING,
            observe_inventory=lambda: inventory_observation(NOW, inventory),
            dispatch_ticket=dispatch, observe_dispatch=lambda payload: self.fail("no reconciliation"),
            deliver_status=deliver)
        self.assertEqual(len(result["dispatch_receipts"]), 3)
        self.assertEqual(result["errors"], [])
        self.assertTrue(all(row["state"] == "WORKING" for row in result["streams"]))
        self.assertEqual(sum(kind == "dispatch" for kind, _ in calls), 3)
        self.assertEqual(sum(kind == "deliver" for kind, _ in calls), 1)

    def test_uncertain_dispatch_is_observed_and_never_blindly_reissued(self):
        store = ContinuousControllerStore(Path(self.temporary.name) / "unknown.sqlite3", ["A"])
        dispatch_calls = []
        def fail_dispatch(payload):
            dispatch_calls.append(payload)
            raise RuntimeError("synthetic uncertain host result")
        first_tickets = [ticket("QA-4", 1)]
        first = production_controller_cycle(store, now=NOW, host_capacity=1,
            inventory_binding=INVENTORY_BINDING,
            observe_inventory=lambda: inventory_observation(NOW, first_tickets),
            dispatch_ticket=fail_dispatch, observe_dispatch=lambda payload: self.fail("not yet"),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": NOW})
        self.assertEqual(first["errors"][0]["state"], "UNKNOWN")
        later = "2026-10-02T10:00:01Z"
        def observe(payload):
            return {key: payload[key] for key in ("dispatch_id", "stream", "ticket", "exact_tuple")} | {
                "status": "ACCEPTED", "observed_at": later}
        second_tickets = [ticket("QA-4", 1)]
        second = production_controller_cycle(store, now=later, host_capacity=1,
            inventory_binding=INVENTORY_BINDING,
            observe_inventory=lambda: inventory_observation(later, second_tickets),
            dispatch_ticket=lambda payload: self.fail("uncertain dispatch was reissued"),
            observe_dispatch=observe,
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": later})
        self.assertEqual(len(dispatch_calls), 1)
        self.assertEqual(second["dispatch_receipts"][0]["status"], "ACCEPTED")

    def test_production_cycle_rejects_partial_or_wrong_scope_inventory(self):
        observed = inventory_observation(NOW, [ticket("QA-5", 1)])
        for mutate in (lambda value: value.update(complete=False),
                       lambda value: value["binding"].update(project_id="other"),
                       lambda value: value.update(inventory_sha256="0" * 64)):
            value = json.loads(json.dumps(observed))
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValidationError):
                production_controller_cycle(self.store, now=NOW, host_capacity=1,
                    inventory_binding=INVENTORY_BINDING,
                    observe_inventory=lambda item=value: item,
                    dispatch_ticket=lambda payload: self.fail("invalid inventory dispatched"),
                    observe_dispatch=lambda payload: self.fail("invalid inventory observed"),
                    deliver_status=lambda payload: self.fail("invalid inventory delivered"))


class JiraProgressTests(unittest.TestCase):
    scope = "project=QA AND fixVersion=1.9.3"

    def receipt(self, ticket="QA-1", **overrides):
        value = {"ticket": ticket, "issue_id": "10001", **BINDING, "status": "RECONCILED",
                 "operation_id": str(uuid.UUID(int=2)), "before_status_id": "3",
                 "after_status_id": "10002", "observed_at": NOW}
        value.update(overrides)
        return value

    def page(self, items, *, cursor=None, complete=True, snapshot="snapshot-1", observed=NOW,
             include_epics=False):
        scope_hash = fingerprint("jira-progress-scope", {"scope": self.scope, "binding": BINDING,
                                                          "include_epics": include_epics})
        return {"items": items, "next_cursor": cursor, "complete": complete,
                "snapshot_id": snapshot, "scope_sha256": scope_hash, "observed_at": observed}

    def call(self, reconcile=None, fetch=None, **overrides):
        args = dict(jira_enabled=True, merged_ticket="QA-1", scope=self.scope, observed_at=NOW,
                    jira_binding=BINDING,
                    reconcile_merged_ticket=reconcile or (lambda ticket: self.receipt(ticket)),
                    fetch_scope_page=fetch or (lambda scope, cursor: self.page([])))
        args.update(overrides)
        return production_post_merge_progress(**args)

    def test_reconciliation_precedes_complete_paginated_stable_identity_counts(self):
        calls = []
        def reconcile(ticket_id):
            calls.append(("reconcile", ticket_id))
            return self.receipt(ticket_id)
        def page(scope, cursor):
            calls.append(("count", cursor))
            if cursor is None:
                return self.page([{"id": "1", "issue_type": "TASK", "status_category": "TERMINAL"},
                                  {"id": "2", "issue_type": "EPIC", "status_category": "NON_TERMINAL"}],
                                 cursor="p2", complete=False)
            return self.page([{"id": "3", "issue_type": "TASK", "status_category": "NON_TERMINAL"}])
        result = self.call(reconcile=reconcile, fetch=page)
        self.assertEqual(calls[0], ("reconcile", "QA-1"))
        self.assertEqual((result["jira_state"], result["closed"], result["remaining_open"]),
                         ("COUNTED", 1, 1))

    def test_disabled_jira_performs_no_reads_or_writes(self):
        calls = []
        result = self.call(jira_enabled=False,
            reconcile=lambda value: calls.append("write"), fetch=lambda scope, cursor: calls.append("read"))
        self.assertEqual(calls, [])
        self.assertEqual((result["jira_state"], result["closed"], result["remaining_open"]),
                         ("JIRA_DISABLED", "UNOBSERVED", "UNOBSERVED"))

    def test_wrong_reconciliation_binding_is_not_retried_or_counted(self):
        for field, value in (("cloud_id", "other"), ("actor_id", "other"),
                             ("status", "UNKNOWN"), ("ticket", "QA-2")):
            calls = []
            result = self.call(reconcile=lambda ticket, f=field, v=value:
                               calls.append(ticket) or self.receipt(ticket, **{f: v}),
                               fetch=lambda scope, cursor: self.fail("count requires proven reconciliation"))
            self.assertEqual(calls, ["QA-1"])
            self.assertEqual(result["jira_state"], "UNOBSERVED")

    def test_snapshot_scope_time_category_and_completeness_fail_closed(self):
        bad_final = [self.page([], complete=False),
                     self.page([{"id": "1", "issue_type": "TASK", "status_category": "UNKNOWN"}])]
        for page in bad_final:
            with self.subTest(page=page):
                result = self.call(fetch=lambda scope, cursor, value=page: value)
                self.assertEqual((result["jira_state"], result["closed"]), ("RECONCILED", "UNOBSERVED"))
        pages = [self.page([], cursor="p2", complete=False),
                 self.page([], snapshot="snapshot-2", observed="2026-10-02T10:00:01Z")]
        result = self.call(fetch=lambda scope, cursor: pages[0] if cursor is None else pages[1])
        self.assertEqual(result["jira_state"], "RECONCILED")

    def test_pagination_page_and_item_bounds_are_enforced(self):
        calls = []
        def endless(scope, cursor):
            calls.append(cursor)
            number = len(calls)
            return self.page([], cursor=f"p{number}", complete=False)
        result = self.call(fetch=endless, max_pages=2)
        self.assertEqual(result["jira_state"], "RECONCILED")
        self.assertEqual(len(calls), 2)
        result = self.call(fetch=lambda scope, cursor: self.page([
            {"id": "1", "issue_type": "TASK", "status_category": "TERMINAL"},
            {"id": "2", "issue_type": "TASK", "status_category": "TERMINAL"}]), max_items=1)
        self.assertEqual(result["closed"], "UNOBSERVED")
        result = self.call(fetch=lambda scope, cursor: self.page([]), max_bytes=8)
        self.assertEqual(result["closed"], "UNOBSERVED")
        ticks = iter([0.0, 0.0, 2.0])
        result = self.call(fetch=lambda scope, cursor: self.page([]), max_seconds=1,
                           clock=lambda: next(ticks))
        self.assertEqual(result["jira_state"], "RECONCILED")

    def test_epic_inclusion_is_explicit_and_contracts_accept_records(self):
        fetch = lambda scope, cursor: self.page([
            {"id": "1", "issue_type": "EPIC", "status_category": "NON_TERMINAL"}],
            include_epics=True)
        authoritative = self.call(fetch=fetch, include_epics=True)
        self.assertEqual(authoritative["remaining_open"], 1)
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("jira-progress", authoritative)
        local_semantics("jira-progress", authoritative)
        contracts.validate("jira-progress", self.call(jira_enabled=False))

    def test_production_jira_lifecycle_writes_once_and_requires_readback(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        calls = []
        result = production_jira_lifecycle(config=config, contract=bundle["contract"],
            event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
            binding=bundle["critic"]["binding"], issue_type="LEAF", current_status="Ready",
            prior_writes=[], state="DISPATCHED", producer_id="fixture-controller",
            run_id=str(uuid.UUID(int=7)), now=NOW, evidence=["urn:awf:fixture:jira"],
            transition_id="31", write_transition=lambda record: calls.append("write") or {
                "operation_id": record["operation_id"], "status": "ATTEMPTED", "observed_at": NOW},
            read_transition=lambda record, operation: calls.append("read") or {
                "status": "In Progress", "actor": "fixture-controller", "observed_at": NOW})
        self.assertEqual(calls, ["write", "read"])
        self.assertEqual((result["record"]["status"], result["writes_stopped"]), ("SUCCEEDED", False))


if __name__ == "__main__":
    unittest.main()
