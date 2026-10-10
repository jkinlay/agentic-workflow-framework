import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agentic.continuous_controller import (
    ContinuousControllerStore,
    canonical_repository_paths,
    production_controller_cycle as real_production_controller_cycle,
    production_jira_lifecycle,
    production_merge_observed,
    production_post_merge_progress,
)
from agentic import continuous_controller
from agentic.contracts import Contracts, schema_inventory
from agentic import ValidationError
from agentic.canonical import fingerprint, sha256
from test_publication_readiness import publication_config, publication_observation
from agentic.cli import local_semantics

ROOT = Path(__file__).resolve().parents[2]

NOW = "2026-10-02T10:00:00Z"
BINDING = {"cloud_id": "cloud-1", "project_id": "project-1", "actor_id": "actor-1"}
COUNTS = {"required": 1, "completed": 0, "acceptable": 0,
          "failed": 0, "stale": 0, "outstanding": 1}
INVENTORY_BINDING = {"project_id": "project-1", "repository_id": "101",
                     "scope_sha256": "a" * 64}
REPOSITORY = {
    "repository_root": ROOT,
    "repository_head_sha": subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
    "repository_tree_sha": subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"], text=True).strip(),
}


def production_controller_cycle(*args, **kwargs):
    """Existing controller cases use explicit synthetic passing publication facts."""
    kwargs.setdefault("publication_config", publication_config())
    kwargs.setdefault("observe_publication", lambda item: publication_observation(item["ticket"], kwargs["now"]))
    return real_production_controller_cycle(*args, **kwargs)


def inventory_observation(now, tickets):
    return {"source": "host_observation", "observed_at": now,
            "binding": dict(INVENTORY_BINDING), "complete": True,
            "inventory_sha256": fingerprint("controller-inventory", {
                "binding": INVENTORY_BINDING, "tickets": tickets}), "tickets": tickets}


def dispatch_receipt(payload, observed_at):
    keys = ["dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce",
            "prepared_at", "begun_at"]
    if "reconcile_nonce" in payload:
        keys += ["reconcile_nonce", "reconcile_at"]
    return {key: payload[key] for key in keys} | {
        "status": "ACCEPTED", "observed_at": observed_at}


def jira_current(binding, status, observed_at):
    return {"issue_id": binding["issue_id"], "status_id": status,
            "observed_at": observed_at, "jira_provider": binding["jira_provider"]}


def configured_jira_provider(config):
    jira = config["jira"]
    return {"cloud_id": jira["cloud_id"], "site": jira["site"],
            "project_id": jira["provider_project_id"], "project_key": jira["project_key"],
            "controller_actor_id": jira["controller_actor_id"]}


def observe_jira_provider(config, **overrides):
    value = configured_jira_provider(config)
    value.update(overrides)
    return value


def ticket(name, priority, disposition="ELIGIBLE", paths=None, **overrides):
    value = {
        "ticket": name,
        "priority": priority,
        "disposition": disposition,
        "actor": "worker-" + name,
        "reason": "eligible scoped action" if disposition == "ELIGIBLE" else "specific wait condition",
        "next_action": "continue " + name,
        "resume_trigger": "named prerequisite becomes current",
        "paths": paths or [".agentic/tests/fixtures/" + name.lower() + ".txt"],
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
    def test_publication_failure_blocks_before_dispatch_while_other_streams_continue(self):
        calls = []
        def observe(item):
            result = publication_observation(item["ticket"], NOW)
            if item["ticket"] == "EX-1":
                result["push_permitted"] = False
            return result
        result = production_controller_cycle(self.store, now=NOW, host_capacity=3,
            inventory_binding=INVENTORY_BINDING, **REPOSITORY,
            observe_inventory=lambda: inventory_observation(NOW, [ticket("EX-1", 1), ticket("EX-2", 2)]),
            observe_publication=observe,
            dispatch_ticket=lambda payload: calls.append(payload["ticket"]) or dispatch_receipt(payload, NOW),
            observe_dispatch=lambda payload: self.fail("no prior launch"),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"], "status": "DELIVERED", "observed_at": NOW})
        self.assertEqual(calls, ["EX-2"])
        blocked = next(row for row in result["streams"] if row["ticket"] == "EX-1")
        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertIn("PUSH_PERMISSION", blocked["reason"])
        self.assertEqual(result["errors"][0]["state"], "BLOCKED")

    def test_missing_publication_observer_refuses_worker_launch(self):
        result = production_controller_cycle(self.store, now=NOW, host_capacity=3,
            inventory_binding=INVENTORY_BINDING, **REPOSITORY,
            observe_inventory=lambda: inventory_observation(NOW, [ticket("EX-1", 1)]),
            observe_publication=None,
            dispatch_ticket=lambda payload: self.fail("missing publication facts dispatched work"),
            observe_dispatch=lambda payload: self.fail("no prior launch"),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"], "status": "DELIVERED", "observed_at": NOW})
        self.assertEqual(result["dispatch_receipts"], [])
        self.assertIn("PUBLICATION_UNOBSERVED", result["errors"][0]["reason"])

    def test_git_child_environment_uses_provider_key_scrubbing_helper(self):
        completed = subprocess.CompletedProcess(args=['git'], returncode=0, stdout=b'ok')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'must-not-reach-child'}), \
                patch.object(continuous_controller.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(continuous_controller._git(ROOT, 'status'), b'ok')
        child_environment = run.call_args.kwargs['env']
        self.assertNotIn('OPENAI_API_KEY', child_environment)
        self.assertEqual(child_environment['GIT_TERMINAL_PROMPT'], '0')

    def test_schema_inventory_matches_loaded_generated_catalog(self):
        schema_dir = ROOT / '.agentic/schemas'
        contracts = Contracts(schema_dir)
        inventory = schema_inventory(schema_dir)
        self.assertEqual(inventory, set(contracts.schemas))
        self.assertIn('operating-config', inventory)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-controller-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "controller.sqlite3"
        self.store = ContinuousControllerStore(self.path, ["A", "B", "C"])

    def test_blocked_or_input_dependent_stream_does_not_pause_eligible_work(self):
        inventory = [
            ticket("EX-1", 1, "BLOCKED", reason="dependency unavailable"),
            ticket("EX-2", 2, "PAUSED_INPUT", reason="owner input required"),
            ticket("EX-3", 3),
        ]
        snapshot = self.store.schedule(inventory, NOW, host_capacity=3)
        self.assertEqual({item["state"] for item in snapshot}, {"WORKING", "BLOCKED", "PAUSED_INPUT"})
        working = next(item for item in snapshot if item["state"] == "WORKING")
        self.assertEqual(working["ticket"], "EX-3")
        self.assertTrue(all(item["reason"] and item["next_action"] and item["resume_trigger"] for item in snapshot))

    def test_completed_run_refills_same_transaction_with_next_eligible_action(self):
        first = self.store.schedule([ticket("EX-1", 1), ticket("EX-2", 2)], NOW, host_capacity=1)
        stream = next(item for item in first if item["state"] == "WORKING")
        self.assertEqual(stream["ticket"], "EX-1")
        after = self.store.finish_and_refill(stream["stream"], "EX-1", [ticket("EX-2", 2)],
                                             "2026-10-02T10:00:01Z", host_capacity=1)
        refilled = next(item for item in after if item["stream"] == stream["stream"])
        self.assertEqual((refilled["state"], refilled["ticket"]), ("WORKING", "EX-2"))
        self.assertNotIn(None, [item["state"] for item in after])

    def test_path_capacity_budget_cap_dependency_and_independence_are_fail_closed(self):
        inventory = [
            ticket("EX-1", 1, paths=["src/shared"]),
            ticket("EX-2", 2, paths=["SRC/shared/nested.py"]),
            ticket("EX-3", 3, budget_available=False),
            ticket("EX-4", 4, cap_available=False),
            ticket("EX-5", 5, dependencies_satisfied=False),
            ticket("EX-6", 6, review_independent=False),
        ]
        snapshot = self.store.schedule(inventory, NOW, host_capacity=2)
        self.assertEqual(sum(item["state"] == "WORKING" for item in snapshot), 1)
        self.assertTrue(all(item["state"] in {"WORKING", "BLOCKED", "PAUSED_INPUT", "COMPLETE"}
                            for item in snapshot))

    def test_contradictory_reviewer_counts_fail_before_dispatch_or_delivery(self):
        malformed = ticket("QA-BAD", 1, reviewer_completion={
            "required": 2, "completed": 1, "acceptable": 1,
            "failed": 0, "stale": 0, "outstanding": 0})
        calls = []
        with self.assertRaisesRegex(ValidationError, "counts are contradictory"):
            production_controller_cycle(
                self.store, now=NOW, host_capacity=1,
                inventory_binding=INVENTORY_BINDING,
                **REPOSITORY,
                observe_inventory=lambda: inventory_observation(NOW, [malformed]),
                dispatch_ticket=lambda payload: calls.append("dispatch"),
                observe_dispatch=lambda payload: calls.append("observe"),
                deliver_status=lambda payload: calls.append("deliver"))
        self.assertEqual(calls, [])

    def test_restart_preserves_visible_stream_states_and_has_no_idle_value(self):
        before = self.store.schedule([ticket("EX-1", 1), ticket("EX-2", 2, "PAUSED_INPUT")],
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
        self.store.schedule([ticket("EX-1", 1)], "2026-10-02T10:02:00Z", host_capacity=1)
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
        self.store.schedule([ticket("EX-1", 1)], NOW, host_capacity=1)
        digest = self.store.digest(NOW)
        Contracts(ROOT / ".agentic/schemas").validate("controller-status-digest", digest)
        local_semantics("controller-status-digest", digest)
        digest["streams"][0]["reviewer_completion"]["outstanding"] = 99
        with self.assertRaisesRegex(ValidationError, "counts"):
            local_semantics("controller-status-digest", digest)

    def test_running_work_is_revalidated_and_completed_ticket_is_not_redispatched(self):
        self.store.schedule([ticket("EX-1", 1)], NOW, host_capacity=1)
        blocked = self.store.schedule([ticket("EX-1", 1, dependencies_satisfied=False)],
                                      "2026-10-02T10:00:01Z", host_capacity=1)
        self.assertEqual(next(item for item in blocked if item["ticket"] == "EX-1")["state"], "BLOCKED")
        fresh = ContinuousControllerStore(Path(self.temporary.name) / "finished.sqlite3", ["A"])
        fresh.schedule([ticket("EX-2", 1)], NOW, host_capacity=1)
        with self.assertRaisesRegex(ValidationError, "Completed ticket remains dispatch-eligible"):
            fresh.finish_and_refill("A", "EX-2", [ticket("EX-2", 1)],
                                    "2026-10-02T10:00:01Z", host_capacity=1)

    def test_path_aliases_overlap_and_external_or_traversal_paths_are_rejected(self):
        snapshot = self.store.schedule([ticket("EX-1", 1, paths=["src\\shared"]),
                                        ticket("EX-2", 2, paths=["SRC/shared/nested.py"])],
                                       NOW, host_capacity=2)
        self.assertEqual(sum(item["state"] == "WORKING" for item in snapshot), 1)
        drive_absolute = "".join(("C", ":/outside"))
        for path in ("../outside", "src/../outside", drive_absolute, "//server/share"):
            with self.subTest(path=path), self.assertRaisesRegex(ValidationError, "path"):
                self.store.schedule([ticket("BAD", 1, paths=[path])], NOW, host_capacity=1)

    def test_git_inventory_serializes_ntfs_hardlink_directory_claims_and_rejects_junctions(self):
        if os.name != "nt":
            self.skipTest("NTFS alias regression is Windows-specific")
        repository = Path(self.temporary.name) / "alias-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Fixture"], check=True)
        left = repository / "left"
        right = repository / "right"
        left.mkdir()
        right.mkdir()
        source = left / "file.py"
        source.write_text("value = 1\n", encoding="utf-8", newline="\n")
        hardlink = right / "hard.py"
        os.link(source, hardlink)
        subprocess.run(["git", "-C", str(repository), "add", "left/file.py", "right/hard.py"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "synthetic alias inventory"], check=True)
        head = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"], text=True).strip()
        admitted, _binding = canonical_repository_paths(
            [ticket("LEFT", 1, paths=["left/**"]),
             ticket("RIGHT", 2, paths=["right/*"])], repository, head, tree)
        self.assertIn("left/file.py", admitted[0]["paths"])
        self.assertIn("left/file.py", admitted[1]["paths"])
        snapshot = self.store.schedule(admitted, NOW, host_capacity=2)
        self.assertEqual(sum(item["state"] == "WORKING" for item in snapshot), 1)
        self.assertEqual(sum(item["state"] == "BLOCKED" for item in snapshot), 1)

        hardlink.unlink()
        subprocess.run(["git", "-C", str(repository), "checkout", "--", "right/hard.py"], check=True)
        alias = repository / "alias"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(left)],
                              capture_output=True, text=True)
        if made.returncode:
            self.skipTest("NTFS junction creation unavailable on this host")
        with self.assertRaisesRegex(ValidationError, "junction|reparse"):
            canonical_repository_paths([ticket("JUNCTION", 1, paths=["alias/file.py"])],
                                       repository, head, tree)

    def test_git_inventory_collapses_case_aliases_and_rejects_wrong_tuple(self):
        exact = ".agentic/lib/agentic/continuous_controller.py"
        admitted, binding = canonical_repository_paths(
            [ticket("LOWER", 1, paths=[exact]), ticket("UPPER", 2, paths=[exact.upper()])],
            ROOT, REPOSITORY["repository_head_sha"], REPOSITORY["repository_tree_sha"])
        self.assertEqual(admitted[0]["paths"], admitted[1]["paths"])
        self.assertEqual(binding["head_sha"], REPOSITORY["repository_head_sha"])
        snapshot = self.store.schedule(admitted, NOW, host_capacity=2)
        self.assertEqual(sum(item["state"] == "WORKING" for item in snapshot), 1)
        with self.assertRaisesRegex(ValidationError, "pinned head/tree"):
            canonical_repository_paths(
                [ticket("WRONG", 1, paths=[exact])], ROOT,
                "0" * 40, REPOSITORY["repository_tree_sha"])

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
        inventory = [ticket("EX-1", 1), ticket("EX-2", 2), ticket("EX-3", 3)]
        calls = []
        def dispatch(payload):
            calls.append(("dispatch", payload["ticket"]))
            return dispatch_receipt(payload, NOW)
        def deliver(digest):
            calls.append(("deliver", digest["delivery_id"]))
            return {"delivery_id": digest["delivery_id"], "status": "DELIVERED", "observed_at": NOW}
        result = production_controller_cycle(self.store, now=NOW, host_capacity=3,
            inventory_binding=INVENTORY_BINDING,
            **REPOSITORY,
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
        first_tickets = [ticket("EX-4", 1)]
        first = production_controller_cycle(store, now=NOW, host_capacity=1,
            inventory_binding=INVENTORY_BINDING,
            **REPOSITORY,
            observe_inventory=lambda: inventory_observation(NOW, first_tickets),
            dispatch_ticket=fail_dispatch, observe_dispatch=lambda payload: self.fail("not yet"),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": NOW})
        self.assertEqual(first["errors"][0]["state"], "UNKNOWN")
        later = "2026-10-02T10:00:01Z"
        def observe(payload):
            return dispatch_receipt(payload, later)
        second_tickets = [ticket("EX-4", 1)]
        second = production_controller_cycle(store, now=later, host_capacity=1,
            inventory_binding=INVENTORY_BINDING,
            **REPOSITORY,
            observe_inventory=lambda: inventory_observation(later, second_tickets),
            dispatch_ticket=lambda payload: self.fail("uncertain dispatch was reissued"),
            observe_dispatch=observe,
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": later})
        self.assertEqual(len(dispatch_calls), 1)
        self.assertEqual(second["dispatch_receipts"][0]["status"], "ACCEPTED")

    def test_dispatch_rejects_stale_direct_and_cached_restart_receipts(self):
        store = ContinuousControllerStore(Path(self.temporary.name) / "dispatch-stale.sqlite3", ["A"])
        payloads = []
        first = production_controller_cycle(store, now=NOW, host_capacity=1,
            inventory_binding=INVENTORY_BINDING, **REPOSITORY,
            observe_inventory=lambda: inventory_observation(NOW, [ticket("EX-6", 1)]),
            dispatch_ticket=lambda payload: payloads.append(payload) or
                dispatch_receipt(payload, "2026-10-02T09:59:59Z"),
            observe_dispatch=lambda payload: self.fail("first cycle cannot reconcile"),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": NOW})
        self.assertEqual(first["errors"][0]["state"], "UNKNOWN")
        cached_receipt = dispatch_receipt(payloads[0], NOW)
        later = "2026-10-02T10:00:01Z"
        cached = production_controller_cycle(store, now=later, host_capacity=1,
            inventory_binding=INVENTORY_BINDING, **REPOSITORY,
            observe_inventory=lambda: inventory_observation(later, [ticket("EX-6", 1)]),
            dispatch_ticket=lambda payload: self.fail("unknown dispatch was reissued"),
            observe_dispatch=lambda payload: cached_receipt,
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": later})
        self.assertEqual(cached["dispatch_receipts"], [])
        self.assertEqual(cached["errors"][0]["state"], "UNKNOWN")
        fresh = "2026-10-02T10:00:02Z"
        accepted = production_controller_cycle(store, now=fresh, host_capacity=1,
            inventory_binding=INVENTORY_BINDING, **REPOSITORY,
            observe_inventory=lambda: inventory_observation(fresh, [ticket("EX-6", 1)]),
            dispatch_ticket=lambda payload: self.fail("unknown dispatch was reissued"),
            observe_dispatch=lambda payload: dispatch_receipt(payload, fresh),
            deliver_status=lambda digest: {"delivery_id": digest["delivery_id"],
                "status": "DELIVERED", "observed_at": fresh})
        self.assertEqual(accepted["dispatch_receipts"][0]["dispatch_nonce"],
                         payloads[0]["dispatch_nonce"])

    def test_production_cycle_rejects_partial_or_wrong_scope_inventory(self):
        observed = inventory_observation(NOW, [ticket("EX-5", 1)])
        for mutate in (lambda value: value.update(complete=False),
                       lambda value: value["binding"].update(project_id="other"),
                       lambda value: value.update(inventory_sha256="0" * 64)):
            value = json.loads(json.dumps(observed))
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValidationError):
                production_controller_cycle(self.store, now=NOW, host_capacity=1,
                    inventory_binding=INVENTORY_BINDING,
                    **REPOSITORY,
                    observe_inventory=lambda item=value: item,
                    dispatch_ticket=lambda payload: self.fail("invalid inventory dispatched"),
                    observe_dispatch=lambda payload: self.fail("invalid inventory observed"),
                    deliver_status=lambda payload: self.fail("invalid inventory delivered"))


class JiraProgressTests(unittest.TestCase):
    scope = "project=QA AND fixVersion=1.9.3"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-controller-jira-")
        self.addCleanup(self.temporary.cleanup)
        self.store = ContinuousControllerStore(
            Path(self.temporary.name) / "controller.sqlite3", ["A", "B", "C"])

    def receipt(self, ticket="EX-1", **overrides):
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
        args = dict(jira_enabled=True, merged_ticket="EX-1", scope=self.scope, observed_at=NOW,
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
        self.assertEqual(calls[0], ("reconcile", "EX-1"))
        self.assertEqual((result["jira_state"], result["closed"], result["remaining_open"]),
                         ("COUNTED", 1, 1))

    def test_routine_merge_observation_uses_reconcile_first_progress_route(self):
        calls = []
        args = dict(jira_enabled=True, merged_ticket="EX-1", scope=self.scope, observed_at=NOW,
                    jira_binding=BINDING,
                    reconcile_merged_ticket=lambda ticket: calls.append(("reconcile", ticket)) or
                        self.receipt(ticket),
                    fetch_scope_page=lambda scope, cursor: calls.append(("count", cursor)) or
                        self.page([]))
        result = production_merge_observed(
            lifecycle_state="MERGING",
            lifecycle_facts={"merge_confirmed": True, "candidate_matched": True},
            jira_progress=args)
        self.assertEqual(calls, [("reconcile", "EX-1"), ("count", None)])
        self.assertEqual((result["state"], result["jira_progress"]["jira_state"],
                          result["execution_authority"]), ("MERGED", "COUNTED", False))
        calls.clear()
        with self.assertRaises(ValidationError):
            production_merge_observed(lifecycle_state="MERGING", lifecycle_facts={},
                                      jira_progress=args)
        self.assertEqual(calls, [])

    def test_disabled_jira_performs_no_reads_or_writes(self):
        calls = []
        result = self.call(jira_enabled=False,
            reconcile=lambda value: calls.append("write"), fetch=lambda scope, cursor: calls.append("read"))
        self.assertEqual(calls, [])
        self.assertEqual((result["jira_state"], result["closed"], result["remaining_open"]),
                         ("JIRA_DISABLED", "UNOBSERVED", "UNOBSERVED"))

    def test_wrong_reconciliation_binding_is_not_retried_or_counted(self):
        for field, value in (("cloud_id", "other"), ("actor_id", "other"),
                             ("status", "UNKNOWN"), ("ticket", "EX-2")):
            calls = []
            result = self.call(reconcile=lambda ticket, f=field, v=value:
                               calls.append(ticket) or self.receipt(ticket, **{f: v}),
                               fetch=lambda scope, cursor: self.fail("count requires proven reconciliation"))
            self.assertEqual(calls, ["EX-1"])
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

    def test_count_snapshot_must_not_predate_reconciliation(self):
        receipt = self.receipt(observed_at="2026-10-02T10:00:01Z")
        stale = self.page([], observed=NOW)
        result = self.call(reconcile=lambda ticket: receipt,
                           fetch=lambda scope, cursor: stale)
        self.assertEqual((result["jira_state"], result["closed"], result["remaining_open"]),
                         ("RECONCILED", "UNOBSERVED", "UNOBSERVED"))
        self.assertIn("incomplete", result["reason"])

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
        binding = bundle["critic"]["binding"]
        provider = configured_jira_provider(config)
        result = production_jira_lifecycle(self.store, config=config, contract=bundle["contract"],
            event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
            binding=binding, issue_type="LEAF", state="DISPATCHED",
            producer_id="fixture-controller", run_id=str(uuid.UUID(int=7)), now=NOW,
            evidence=["urn:awf:fixture:jira"], transition_id="31",
            observe_provider_identity=lambda: observe_jira_provider(config),
            read_current_status=lambda value: calls.append("read-before") or jira_current(value, "Ready", NOW),
            write_transition=lambda record: calls.append("write") or {
                "operation_id": record["operation_id"], "issue_id": binding["issue_id"],
                "status": "ATTEMPTED", "observed_at": NOW,
                "jira_provider": provider},
            read_transition=lambda record, operation: calls.append("read-after") or {
                "issue_id": binding["issue_id"], "status": "In Progress",
                "actor": "fixture-controller", "observed_at": NOW,
                "jira_provider": provider})
        self.assertEqual(calls, ["read-before", "write", "read-after"])
        self.assertEqual((result["record"]["status"], result["writes_stopped"]), ("SUCCEEDED", False))


    def test_disabled_jira_lifecycle_uses_no_adapter(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        config["jira"]["enabled"] = False
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        calls = []
        result = production_jira_lifecycle(self.store,
            config=config, contract=bundle["contract"], event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True},
            binding=bundle["critic"]["binding"], issue_type="LEAF",
            state="DISPATCHED", producer_id="fixture-controller",
            run_id=str(uuid.UUID(int=11)), now=NOW, evidence=[], transition_id="31",
            read_current_status=lambda value: calls.append("read-before"),
            write_transition=lambda value: calls.append("write"),
            read_transition=lambda record, operation: calls.append("read-after"))
        self.assertEqual(calls, [])
        self.assertFalse(result["planned"])

    def test_jira_lifecycle_refuses_unbound_or_unobserved_identity_before_issue_lookup(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        calls = []
        common = dict(config=config, contract=bundle["contract"], event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True},
            binding=bundle["critic"]["binding"], issue_type="LEAF", state="DISPATCHED",
            producer_id="fixture-controller", run_id=str(uuid.UUID(int=17)), now=NOW,
            evidence=[], transition_id="31", read_current_status=lambda value: calls.append("lookup"),
            write_transition=lambda value: calls.append("write"),
            read_transition=lambda record, operation: calls.append("readback"))
        unbound = json.loads(json.dumps(config))
        unbound["jira"].update(cloud_id=None, provider_project_id=None, controller_actor_id=None)
        result = production_jira_lifecycle(self.store, **(common | {"config": unbound}),
            observe_provider_identity=lambda: calls.append("identity"))
        self.assertEqual(result["status"], "IDENTITY_UNBOUND")
        self.assertEqual(calls, [])
        result = production_jira_lifecycle(self.store, **common)
        self.assertEqual(result["status"], "IDENTITY_UNOBSERVED")
        self.assertEqual(calls, [])
        result = production_jira_lifecycle(self.store, **common,
            observe_provider_identity=lambda: (_ for _ in ()).throw(ConnectionError("offline")))
        self.assertEqual(result["status"], "IDENTITY_UNOBSERVED")
        self.assertEqual(result["issue_lookups"], 0)
        self.assertEqual(calls, [])

    def test_production_jira_lifecycle_rejects_unbound_or_stale_adapter_evidence(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        binding = bundle["critic"]["binding"]
        writes = []
        common = dict(config=config, contract=bundle["contract"], event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True}, binding=binding,
            issue_type="LEAF", state="DISPATCHED",
            producer_id="fixture-controller", run_id=str(uuid.UUID(int=8)), now=NOW,
            evidence=["urn:awf:fixture:jira"], transition_id="31",
            observe_provider_identity=lambda: observe_jira_provider(config),
            write_transition=lambda record: writes.append(record),
            read_transition=lambda record, operation: self.fail("invalid write cannot be read back"))
        result = production_jira_lifecycle(self.store, **common,
            read_current_status=lambda value: jira_current(value, "Ready", NOW) | {"issue_id": "other"})
        self.assertTrue(result["writes_stopped"])
        self.assertEqual(writes, [])
        result = production_jira_lifecycle(self.store, **common,
            read_current_status=lambda value: jira_current(value, "Ready", "2026-10-02T09:59:59Z"))
        self.assertTrue(result["writes_stopped"])
        self.assertEqual(writes, [])

    def test_jira_lifecycle_rejects_wrong_connector_site_project_or_actor_before_mutation(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        original = bundle["critic"]["binding"]
        provider = configured_jira_provider(config)
        for field, replacement in (("cloud_id", "other-cloud"),
                                   ("site", "https://other.example.invalid"),
                                   ("project_id", "other-project"),
                                   ("project_key", "OTHER"),
                                   ("controller_actor_id", "other-controller")):
            wrong = dict(provider)
            wrong[field] = replacement
            calls = []
            with self.subTest(field=field):
                result = production_jira_lifecycle(self.store, config=config, contract=bundle["contract"],
                    event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
                    binding=original, issue_type="LEAF", state="DISPATCHED",
                    producer_id="fixture-controller", run_id=str(uuid.UUID(int=18)), now=NOW,
                    evidence=[], transition_id="31",
                    observe_provider_identity=lambda wrong=wrong: wrong,
                    read_current_status=lambda value: calls.append("lookup"),
                    write_transition=lambda record: calls.append("write"),
                    read_transition=lambda record, operation: calls.append("readback"))
                self.assertTrue(result["writes_stopped"])
                self.assertEqual(result["status"], "IDENTITY_MISMATCH")
            self.assertEqual(calls, [])
        calls = []
        result = production_jira_lifecycle(self.store, config=config, contract=bundle["contract"],
                event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
                binding=original, issue_type="LEAF", state="DISPATCHED",
                producer_id="other-controller", run_id=str(uuid.UUID(int=19)), now=NOW,
                evidence=[], transition_id="31",
                observe_provider_identity=lambda: observe_jira_provider(config),
                read_current_status=lambda value: calls.append("read"),
                write_transition=lambda record: calls.append("write"),
                read_transition=lambda record, operation: calls.append("readback"))
        self.assertEqual(result["status"], "IDENTITY_MISMATCH")
        self.assertEqual(calls, [])

    def test_jira_lifecycle_binds_write_and_readback_receipts_to_provider_identity(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        binding = bundle["critic"]["binding"]
        provider = configured_jira_provider(config)
        wrong_site = dict(provider) | {"site": "https://second-connector.example.invalid"}
        for stage in ("write", "readback"):
            store = ContinuousControllerStore(
                Path(self.temporary.name) / f"jira-wrong-{stage}.sqlite3", ["A"])
            result = production_jira_lifecycle(store, config=config, contract=bundle["contract"],
                event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
                binding=binding, issue_type="LEAF", state="DISPATCHED",
                producer_id="fixture-controller", run_id=str(uuid.UUID(int=20)), now=NOW,
                evidence=[], transition_id="31",
                observe_provider_identity=lambda: observe_jira_provider(config),
                read_current_status=lambda value: jira_current(value, "Ready", NOW),
                write_transition=lambda record, stage=stage: {
                    "operation_id": record["operation_id"], "issue_id": binding["issue_id"],
                    "status": "ATTEMPTED", "observed_at": NOW,
                    "jira_provider": wrong_site if stage == "write" else provider},
                read_transition=lambda record, operation, stage=stage: {
                    "issue_id": binding["issue_id"], "status": "In Progress",
                    "actor": "other-controller" if stage == "readback" else "fixture-controller",
                    "observed_at": NOW, "jira_provider": provider})
            self.assertTrue(result["writes_stopped"])
            self.assertEqual(store.jira_operations(binding["issue_id"])[0]["status"], "UNKNOWN")

    def test_jira_identity_is_rechecked_after_issue_read_and_before_write(self):
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        binding = bundle["critic"]["binding"]
        observations = iter([observe_jira_provider(config),
                             observe_jira_provider(config, controller_actor_id="changed-actor")])
        calls = []
        result = production_jira_lifecycle(self.store, config=config, contract=bundle["contract"],
            event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
            binding=binding, issue_type="LEAF", state="DISPATCHED",
            producer_id="fixture-controller", run_id=str(uuid.UUID(int=21)), now=NOW,
            evidence=[], transition_id="31", observe_provider_identity=lambda: next(observations),
            read_current_status=lambda value: calls.append("lookup") or jira_current(value, "Ready", NOW),
            write_transition=lambda record: calls.append("write"),
            read_transition=lambda record, operation: calls.append("readback"))
        self.assertEqual(result["status"], "IDENTITY_MISMATCH")
        self.assertEqual(calls, ["lookup"])

    def test_jira_intent_survives_crash_after_side_effect_without_reissue(self):
        path = Path(self.temporary.name) / "jira-crash.sqlite3"
        store = ContinuousControllerStore(path, ["A"])
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        binding = bundle["critic"]["binding"]
        external = {"status": "Ready", "writes": 0}

        def observe(_binding, observed_at=NOW):
            return jira_current(_binding, external["status"], observed_at)

        def crash_after_mutation(record):
            external["writes"] += 1
            external["status"] = "In Progress"
            raise SystemExit("synthetic process loss after Jira side effect")

        common = dict(config=config, contract=bundle["contract"], event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True}, binding=binding,
            issue_type="LEAF", state="DISPATCHED", producer_id="fixture-controller",
            run_id=str(uuid.UUID(int=12)), evidence=["urn:awf:fixture:jira"], transition_id="31",
            observe_provider_identity=lambda: observe_jira_provider(config),
            read_transition=lambda record, operation: self.fail("crashed call has no receipt"))
        with self.assertRaisesRegex(SystemExit, "synthetic process loss"):
            production_jira_lifecycle(store, now=NOW, read_current_status=observe,
                                      write_transition=crash_after_mutation, **common)
        in_flight = store.jira_operations(binding["issue_id"])
        self.assertEqual((len(in_flight), in_flight[0]["status"], external["writes"]),
                         (1, "IN_FLIGHT", 1))
        operation_id = in_flight[0]["operation_id"]

        restarted = ContinuousControllerStore(path, ["A"])
        recovered_at = "2026-10-02T10:00:01Z"
        restarted.recover_jira_operations(recovered_at)
        self.assertEqual(restarted.jira_operations(binding["issue_id"])[0]["status"], "UNKNOWN")
        result = production_jira_lifecycle(
            restarted, now=recovered_at,
            read_current_status=lambda value: observe(value, recovered_at),
            write_transition=lambda record: self.fail("unknown side effect was reissued"), **common)
        durable = restarted.jira_operations(binding["issue_id"])
        self.assertEqual((external["writes"], durable[0]["operation_id"], durable[0]["status"]),
                         (1, operation_id, "SUCCEEDED"))
        self.assertFalse(result["planned"])

    def test_unknown_jira_intent_without_success_readback_stops_all_retries(self):
        path = Path(self.temporary.name) / "jira-unknown.sqlite3"
        store = ContinuousControllerStore(path, ["A"])
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        binding = bundle["critic"]["binding"]
        common = dict(config=config, contract=bundle["contract"], event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True}, binding=binding,
            issue_type="LEAF", state="DISPATCHED", producer_id="fixture-controller",
            run_id=str(uuid.UUID(int=13)), now=NOW, evidence=[], transition_id="31",
            observe_provider_identity=lambda: observe_jira_provider(config),
            read_current_status=lambda value: jira_current(value, "Ready", NOW),
            read_transition=lambda record, operation: self.fail("no receipt exists"))
        first = production_jira_lifecycle(
            store, write_transition=lambda record: (_ for _ in ()).throw(RuntimeError("lost")), **common)
        self.assertTrue(first["writes_stopped"])
        calls = []
        second = production_jira_lifecycle(
            ContinuousControllerStore(path, ["A"]),
            write_transition=lambda record: calls.append(record), **common)
        self.assertEqual(calls, [])
        self.assertTrue(second["writes_stopped"])
        self.assertIn("not proven successful", second["reason"])

    def test_workflow_cli_runs_all_production_routes_through_digest_pinned_adapter(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf-controller-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        state_path = root / "controller.sqlite3"
        worktree = root / "worker"
        worktree.mkdir()
        inventory = [ticket("EX-42", 1)]
        scope = "project=QA AND fixVersion=1.9.3"
        scope_sha256 = fingerprint("jira-progress-scope", {
            "scope": scope, "binding": BINDING, "include_epics": False})
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())
        adapter_config = {
            "inventory": inventory_observation(NOW, inventory),
            "publication": publication_observation("EX-42", NOW),
            "jira_now": NOW,
            "jira_before": "Ready",
            "jira_after": "In Progress",
            "reconcile_receipt": {"ticket": "QA-CLI", "issue_id": "10001", **BINDING,
                "status": "RECONCILED", "operation_id": str(uuid.UUID(int=9)),
                "before_status_id": "3", "after_status_id": "10002", "observed_at": NOW},
            "jira_page": {"items": [{"id": "10001", "issue_type": "TASK",
                                      "status_category": "TERMINAL"}],
                "next_cursor": None, "complete": True, "snapshot_id": "snapshot-cli",
                "scope_sha256": scope_sha256, "observed_at": NOW},
        }
        adapter_source = '''def build_adapters(config):
    now = config["jira_now"]
    def observe_inventory():
        return config["inventory"]
    def observe_publication(item):
        return config["publication"]
    def dispatch_ticket(payload):
        keys = ["dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce", "prepared_at", "begun_at"]
        if "reconcile_nonce" in payload:
            keys += ["reconcile_nonce", "reconcile_at"]
        return {key: payload[key] for key in keys} | {"status": "ACCEPTED", "observed_at": now}
    def observe_dispatch(payload):
        return dispatch_ticket(payload)
    def deliver_status(digest):
        return {"delivery_id": digest["delivery_id"], "status": "DELIVERED", "observed_at": now}
    def observe_provider_identity():
        return {"cloud_id": "fixture-cloud", "site": "https://jira.example.invalid",
                "project_id": "fixture-project", "project_key": "EX",
                "controller_actor_id": "fixture-controller"}
    def read_current_status(binding):
        return {"issue_id": binding["issue_id"], "status_id": config["jira_before"], "observed_at": now, "jira_provider": binding["jira_provider"]}
    def write_transition(record):
        return {"operation_id": record["operation_id"], "issue_id": record["binding"]["issue_id"], "status": "ATTEMPTED", "observed_at": now, "jira_provider": record["jira_provider"]}
    def read_transition(record, operation):
        return {"issue_id": record["binding"]["issue_id"], "status": config["jira_after"], "actor": record["producer_id"], "observed_at": now, "jira_provider": record["jira_provider"]}
    def reconcile_merged_ticket(ticket):
        value = dict(config["reconcile_receipt"])
        value["ticket"] = ticket
        return value
    def fetch_scope_page(scope, cursor):
        return config["jira_page"]
    return {name: value for name, value in locals().items() if callable(value) and name != "build_adapters"}
'''
        adapter_path = root / "reviewed_adapter.py"
        adapter_path.write_text(adapter_source, encoding="utf-8", newline="\n")
        adapter_config_path = root / "adapter.json"
        adapter_config_path.write_text(json.dumps(adapter_config), encoding="utf-8")
        binding_path = root / "inventory-binding.json"
        binding_path.write_text(json.dumps(INVENTORY_BINDING), encoding="utf-8")
        pin = sha256(adapter_path.read_bytes())
        common = [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                  "--root", str(ROOT), "controller", "--state", str(state_path),
                  "--stream", "A", "--stream", "B", "--stream", "C",
                  "--worktree-root", str(worktree), "--project-config",
                  str(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"),
                  "--adapter-module", str(adapter_path),
                  "--adapter-sha256", pin, "--adapter-config", str(adapter_config_path)]
        cycle = subprocess.run([*common, "cycle", "--inventory-binding", str(binding_path),
                                "--repository-root", str(ROOT),
                                "--repository-head-sha", REPOSITORY["repository_head_sha"],
                                "--repository-tree-sha", REPOSITORY["repository_tree_sha"],
                                "--now", NOW, "--host-capacity", "1"],
                               cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(cycle.returncode, 0, cycle.stderr)
        cycle_output = json.loads(cycle.stdout)
        self.assertEqual((len(cycle_output["dispatch_receipts"]),
                          cycle_output["status_delivery"]["status"]), (1, "DELIVERED"))

        def dump(name, value):
            path = root / name
            path.write_text(json.dumps(value), encoding="utf-8")
            return path
        lifecycle = subprocess.run([*common, "jira-lifecycle",
            "--contract", str(dump("contract.json", bundle["contract"])),
            "--event", "WORKER_STARTED",
            "--facts", str(dump("facts.json", {"run_registered": True, "worktree_verified": True})),
            "--binding", str(dump("binding.json", bundle["critic"]["binding"])),
            "--lifecycle-state", "DISPATCHED",
            "--producer-id", "fixture-controller", "--run-id", str(uuid.UUID(int=10)),
            "--now", NOW, "--evidence", "urn:awf:fixture:jira", "--transition-id", "31"],
            cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(lifecycle.returncode, 0, lifecycle.stderr)
        lifecycle_output = json.loads(lifecycle.stdout)
        self.assertEqual(lifecycle_output["record"]["status"], "SUCCEEDED")
        self.assertEqual(lifecycle_output["current_observation"]["status_id"], "Ready")

        progress = {"jira_enabled": True, "merged_ticket": "QA-CLI", "scope": scope,
                    "observed_at": NOW, "jira_binding": BINDING}
        merge = subprocess.run([*common, "merge-observed", "--lifecycle-state", "MERGING",
            "--lifecycle-facts", str(dump("merge-facts.json", {
                "merge_confirmed": True, "candidate_matched": True})),
            "--jira-progress", str(dump("progress.json", progress))],
            cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(merge.returncode, 0, merge.stderr)
        merge_output = json.loads(merge.stdout)
        self.assertEqual((merge_output["state"], merge_output["jira_progress"]["jira_state"],
                          merge_output["jira_progress"]["closed"]), ("MERGED", "COUNTED", 1))

        wrong_pin = subprocess.run([*common[:common.index("--adapter-sha256") + 1], "0" * 64,
                                    "--adapter-config", str(adapter_config_path), "cycle",
                                    "--inventory-binding", str(binding_path),
                                    "--repository-root", str(ROOT),
                                    "--repository-head-sha", REPOSITORY["repository_head_sha"],
                                    "--repository-tree-sha", REPOSITORY["repository_tree_sha"], "--now", NOW,
                                    "--host-capacity", "1"],
                                   cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(wrong_pin.returncode, 0)
        self.assertIn("SHA-256 pin", wrong_pin.stderr)


if __name__ == "__main__":
    unittest.main()
