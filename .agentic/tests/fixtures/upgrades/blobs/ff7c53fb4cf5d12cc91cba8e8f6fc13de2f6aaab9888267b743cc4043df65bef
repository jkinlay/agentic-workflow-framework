"""Synthetic stream allocation, ownership and safe-publication acceptance tests."""
from base64 import b64encode
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, loads, sha256
from agentic.safeio import Tree
from agentic.streams import DEFAULT_NATIVE_EXECUTION, JOURNAL, MARKDOWN, PLAN, plan_inventory, render_markdown, write_project_plan
from test_operating_integration import configuration as operating_configuration, governance as operating_governance

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-11T10:00:00Z"
FIXTURE = ROOT / ".agentic/examples/stream-input.json"


def unresolved(ticket_id):
    return {"ticket_id": ticket_id, "state": "unresolved", "evidence": [], "verified_by": None, "verified_at": None}


def accepted(ticket_id):
    return {"ticket_id": ticket_id, "state": "verified", "evidence": ["synthetic:accepted-test-results"], "verified_by": "synthetic-critic", "verified_at": NOW}


def history(summary="Synthetic work completed"):
    return {"at": NOW, "actor": "synthetic-worker", "summary": summary}


def owner(stream="A", agent="synthetic-agent-A", state="active"):
    return {"stream": stream, "agent_id": agent, "worktree": f"synthetic/worktrees/{agent}", "state": state, "evidence": "synthetic:writer-lease"}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.value = loads(FIXTURE.read_text(encoding="utf-8"))

    def plan(self, value=None, previous=None):
        raw = canonical(value or self.value)
        return plan_inventory(raw, sha256(raw), NOW, allow_synthetic=True, previous=previous)

    def test_three_coherent_streams_with_ready_first_work_and_all_ticket_details(self):
        plan = self.plan()
        self.assertEqual([s["label"] for s in plan["streams"]], ["A", "B", "C"])
        self.assertEqual(plan["tickets"]["DEMO-11"]["stream"], plan["tickets"]["DEMO-12"]["stream"])
        self.assertEqual({p["ticket_id"] for p in plan["dispatch_packets"]}, {"DEMO-11", "DEMO-21", "DEMO-31"})
        self.assertEqual(len({t for s in plan["streams"] for t in s["ticket_ids"]}), 4)
        self.assertEqual(len(plan["dependency_barriers"]), 1)
        self.assertFalse(plan["jira_mutations"])
        self.assertFalse(plan["execution_authority"])
        self.assertTrue(all(not p["live_dispatch_eligible"] for p in plan["dispatch_packets"]))
        markdown = render_markdown(plan).decode()
        for ticket in self.value["tickets"]:
            self.assertIn(ticket["id"], markdown)
            self.assertIn(ticket["scope"], markdown)
        self.assertIn("SYNTHETIC EXAMPLE", markdown)

    def test_allocation_is_deterministic_under_inventory_order_permutation(self):
        first = self.plan()
        self.value["tickets"].reverse()
        self.value["epics"].reverse()
        second = self.plan()
        self.assertEqual(first["streams"], second["streams"])
        self.assertEqual(first["tickets"], second["tickets"])

    def test_serial_graph_does_not_create_idle_agents(self):
        tickets = self.value["tickets"]
        for index, ticket in enumerate(tickets):
            ticket["write_paths"] = [f"src/component-{index}"]
            ticket["dependencies"] = [] if not index else [unresolved(tickets[index - 1]["id"])]
        plan = self.plan()
        self.assertEqual(len(plan["streams"]), 1)
        self.assertEqual([p["ticket_id"] for p in plan["dispatch_packets"]], ["DEMO-11"])
        self.assertIn("At most one independent", plan["rationale"])

    def test_overlapping_case_folded_subtrees_share_one_writer(self):
        for ticket in self.value["tickets"]:
            ticket["dependencies"] = []
            ticket["write_paths"] = ["SRC/shared/module.py"]
        self.value["tickets"][0]["write_paths"] = ["src"]
        plan = self.plan()
        self.assertEqual(len(plan["streams"]), 1)
        self.assertEqual(len(plan["dispatch_packets"]), 1)

    def test_done_without_acceptance_never_unblocks_a_prerequisite(self):
        prerequisite = self.value["tickets"][0]
        prerequisite.update(status="done", history=[history()])
        plan = self.plan()
        self.assertNotIn("DEMO-12", {p["ticket_id"] for p in plan["dispatch_packets"]})
        self.assertTrue(any(b["ticket_id"] == "DEMO-12" for b in plan["dependency_barriers"]))
        self.value["tickets"][1]["dependencies"] = [accepted("DEMO-11")]
        refreshed = self.plan()
        self.assertIn("DEMO-12", {p["ticket_id"] for p in refreshed["dispatch_packets"]})

    def test_false_claim_of_prerequisite_acceptance_is_rejected(self):
        self.value["tickets"][1]["dependencies"] = [accepted("DEMO-11")]
        with self.assertRaisesRegex(ValidationError, "completed tickets"):
            self.plan()
        self.value["tickets"][0].update(status="done", history=[history()])
        self.value["tickets"][1]["dependencies"][0]["evidence"] = []
        with self.assertRaisesRegex(ValidationError, "status alone"):
            self.plan()

    def test_ongoing_work_continues_its_existing_agent(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history("Started existing work")])
        plan = self.plan()
        packet = next(p for p in plan["dispatch_packets"] if p["ticket_id"] == "DEMO-11")
        self.assertEqual(packet["action"], "continue_existing_agent")
        self.assertEqual(packet["existing_agent_id"], "synthetic-agent-A")
        self.assertEqual(packet["stream"], "A")
        self.assertEqual(plan["tickets"]["DEMO-11"]["history"], self.value["tickets"][0]["history"])

    def test_paused_owner_is_retained_and_never_replaced_with_new_agent(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(state="paused"), history=[history("Paused existing work")])
        self.value["tickets"][1]["dependencies"] = []
        plan = self.plan()
        self.assertFalse(any(p["stream"] == "A" for p in plan["dispatch_packets"]))
        lane = next(s for s in plan["streams"] if s["label"] == "A")
        self.assertIn("Reconcile the retained writer", lane["next_step"])

    def test_conflicting_existing_stream_write_surfaces_are_not_silently_reassigned(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner("A"), history=[history()])
        self.value["tickets"][1].update(status="in_progress", ownership=owner("B", "synthetic-agent-B"), history=[history()])
        with self.assertRaisesRegex(ValidationError, "conflicting write surfaces"):
            self.plan()

    def test_multiple_retained_writers_in_one_stream_are_rejected(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history()])
        self.value["tickets"][2].update(status="in_progress", ownership=owner("A", "different-agent"), history=[history()])
        with self.assertRaisesRegex(ValidationError, "More than one retained writer"):
            self.plan()

    def test_in_progress_ticket_without_owner_is_rejected(self):
        self.value["tickets"][0].update(status="in_progress", history=[history()])
        with self.assertRaisesRegex(ValidationError, "requires its existing owner"):
            self.plan()

    def test_blocked_inventory_has_explicit_next_action_and_no_dispatch(self):
        for ticket in self.value["tickets"]:
            ticket["status"] = "blocked"
        plan = self.plan()
        self.assertEqual(plan["dispatch_packets"], [])
        self.assertIn("Resolve the listed", plan["next_step"]["action"])
        self.assertTrue(all(s["next_step"] for s in plan["streams"]))

    def test_missing_duplicate_self_and_cyclic_references_reject(self):
        mutations = [
            lambda v: v["tickets"][0]["dependencies"].append(unresolved("DEMO-999")),
            lambda v: v["tickets"].append(deepcopy(v["tickets"][0])),
            lambda v: v["tickets"][0]["dependencies"].append(unresolved("DEMO-11")),
            lambda v: v["tickets"][0]["dependencies"].append(unresolved("DEMO-12")),
            lambda v: v["epics"].append(deepcopy(v["epics"][0])),
            lambda v: v["tickets"][0].update(epic_id="DEMO-999"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = deepcopy(self.value)
                mutate(changed)
                with self.assertRaises(ValidationError):
                    self.plan(changed)

    def test_partial_stale_future_and_unpinned_inventories_reject(self):
        raw = canonical(self.value)
        with self.assertRaisesRegex(ValidationError, "trusted hash"):
            plan_inventory(raw, "0" * 64, NOW, True)
        for change in [{"complete": False}, {"captured_at": "2026-09-09T10:00:00Z"},
                       {"captured_at": "2026-09-12T10:00:00Z"}, {"source": "unknown"}, {"verification_evidence": ""}]:
            with self.subTest(change=change):
                changed = deepcopy(self.value)
                changed["inventory"].update(change)
                with self.assertRaises(ValidationError):
                    self.plan(changed)
        with self.assertRaisesRegex(ValidationError, "allow-synthetic"):
            plan_inventory(raw, sha256(raw), NOW)

    def test_unknown_fields_and_duplicate_json_keys_reject(self):
        self.value["tickets"][0]["authorize_merge"] = True
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            self.plan()
        raw = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(ValidationError, "Duplicate mapping key"):
            plan_inventory(raw, sha256(raw), NOW, True)

    def test_ambiguous_and_governance_write_boundaries_reject(self):
        for path in ["../outside", "C:/outside", "src/*", "src/CON", ".agentic", "AGENTS.md", "src/AGENTS.md", ".github/workflows", "STREAMS.md"]:
            with self.subTest(path=path):
                changed = deepcopy(self.value)
                changed["tickets"][0]["write_paths"] = [path]
                with self.assertRaises(ValidationError):
                    self.plan(changed)

    def test_retained_assignments_and_history_survive_a_refresh(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history("Started")])
        before = self.plan()
        self.value["tickets"][0].update(status="done", history=[history("Started"), history("Accepted")])
        self.value["tickets"][0]["ownership"]["state"] = "released"
        self.value["tickets"][1]["dependencies"] = [accepted("DEMO-11")]
        after = self.plan(previous=before)
        self.assertEqual({k: t["stream"] for k, t in before["tickets"].items()}, {k: t["stream"] for k, t in after["tickets"].items()})
        self.assertEqual(after["tickets"]["DEMO-11"]["history"], self.value["tickets"][0]["history"])
        self.assertIn("DEMO-12", {p["ticket_id"] for p in after["dispatch_packets"]})

    def test_replanning_cannot_erase_history_owner_or_tickets(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history()])
        previous = self.plan()
        mutations = [lambda v: v["tickets"][0].update(history=[history("Replaced original")]),
                     lambda v: v["tickets"][0]["ownership"].update(agent_id="replacement"),
                     lambda v: v["tickets"].pop()]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = deepcopy(self.value)
                mutate(changed)
                with self.assertRaises(ValidationError):
                    self.plan(changed, previous)

    def test_markdown_treats_imported_text_as_data(self):
        self.value["tickets"][0]["title"] = "[click](https://example.invalid) | <script>alert(1)</script>"
        markdown = render_markdown(self.plan()).decode()
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertIn("\\[click\\]", markdown)
        self.assertIn("\\|", markdown)

    def test_tickets_without_epics_do_not_require_placeholder_epics(self):
        self.value["epics"] = []
        for ticket in self.value["tickets"]:
            ticket["epic_id"] = None
        plan = self.plan()
        self.assertEqual(len(plan["streams"]), 3)
        self.assertEqual(plan["epics"], {})
        self.assertTrue(all(p["epic_id"] is None for p in plan["dispatch_packets"]))
        self.assertIn("No Epic", render_markdown(plan).decode())

    def test_external_dependency_inventory_does_not_expand_writer_scope(self):
        external = deepcopy(self.value["tickets"][3])
        external.update(id="OTHER-1", epic_id=None, dispatch_scope="dependency_only", write_paths=[])
        self.value["tickets"].append(external)
        self.value["tickets"][0]["dependencies"] = [unresolved("OTHER-1")]
        plan = self.plan()
        self.assertIsNone(plan["tickets"]["OTHER-1"]["stream"])
        self.assertFalse(any(p["ticket_id"] in {"OTHER-1", "DEMO-11"} for p in plan["dispatch_packets"]))
        self.assertFalse(any("OTHER-1" in s["ticket_ids"] for s in plan["streams"]))
        self.assertIn("Read-only prerequisite inventory", render_markdown(plan).decode())
        self.value["tickets"][-1].update(status="done", history=[history()])
        self.value["tickets"][0]["dependencies"] = [accepted("OTHER-1")]
        after = self.plan()
        self.assertIn("DEMO-11", {p["ticket_id"] for p in after["dispatch_packets"]})
        self.assertNotIn("OTHER-1", {p["ticket_id"] for p in after["dispatch_packets"]})

    def test_external_owned_and_dependency_only_writer_claims_are_rejected(self):
        changed = deepcopy(self.value)
        changed["tickets"][-1]["id"] = "OTHER-1"
        with self.assertRaisesRegex(ValidationError, "Owned tickets"):
            self.plan(changed)
        changed["tickets"][-1]["dispatch_scope"] = "dependency_only"
        with self.assertRaisesRegex(ValidationError, "cannot reserve write paths"):
            self.plan(changed)

    def test_shared_bootstrap_reserves_three_future_streams_before_branches_are_ready(self):
        for index, ticket in enumerate(self.value["tickets"]):
            ticket["write_paths"] = [f"src/component-{index}"]
            ticket["dependencies"] = [] if index == 0 else [unresolved("DEMO-11")]
        first = self.plan()
        self.assertEqual([s["label"] for s in first["streams"]], ["A", "B", "C"])
        self.assertEqual({p["ticket_id"] for p in first["dispatch_packets"]}, {"DEMO-11"})
        self.assertEqual(len({first["tickets"][t]["stream"] for t in ["DEMO-12", "DEMO-21", "DEMO-31"]}), 3)
        self.assertIn("reserve separate streams from the outset", first["rationale"])
        self.value["tickets"][0].update(status="done", history=[history()])
        for ticket in self.value["tickets"][1:]:
            ticket["dependencies"] = [accepted("DEMO-11")]
        second = self.plan(previous=first)
        self.assertEqual({p["ticket_id"] for p in second["dispatch_packets"]}, {"DEMO-12", "DEMO-21", "DEMO-31"})
        self.assertEqual({k: t["stream"] for k, t in first["tickets"].items()}, {k: t["stream"] for k, t in second["tickets"].items()})

    def test_completed_scope_reports_completion_instead_of_permanent_blocking(self):
        for ticket in self.value["tickets"]:
            ticket.update(status="done", history=[history()])
        plan = self.plan()
        self.assertEqual(plan["status"], "COMPLETE")
        self.assertEqual(plan["dispatch_packets"], [])
        self.assertEqual(plan["dependency_barriers"], [])
        self.assertIn("scope is complete", plan["next_step"]["action"])
        self.assertFalse(plan["next_step"]["continue_independent_work"])

    def test_read_only_inventory_cannot_generate_any_owned_stream(self):
        for ticket in self.value["tickets"]:
            ticket.update(dispatch_scope="dependency_only", write_paths=[])
        plan = self.plan()
        self.assertEqual(plan["streams"], [])
        self.assertEqual(plan["dispatch_packets"], [])
        self.assertEqual(plan["status"], "COMPLETE")

    def test_existing_streams_cannot_share_an_agent_or_worktree(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history()])
        self.value["tickets"][2].update(status="in_progress", ownership=owner("B"), history=[history()])
        with self.assertRaisesRegex(ValidationError, "separate agents and worktrees"):
            self.plan()
        self.value["tickets"][2]["ownership"]["agent_id"] = "different-agent"
        with self.assertRaisesRegex(ValidationError, "separate agents and worktrees"):
            self.plan()


class NativeCapacityTests(unittest.TestCase):
    """Offline selection proofs; these tests do not start or certify native agents."""
    def setUp(self):
        self.value = loads(FIXTURE.read_text(encoding="utf-8"))
        self.execution = deepcopy(DEFAULT_NATIVE_EXECUTION)

    def plan(self, host=3, *, live_shaped=False, execution=None, depth=0, provenance=None, coordinator=None):
        value = deepcopy(self.value)
        if live_shaped:
            value["inventory"]["source"] = "jira_snapshot"
        raw = canonical(value)
        return plan_inventory(raw, sha256(raw), NOW, allow_synthetic=True,
                              execution=self.execution if execution is None else execution,
                              host_writer_capacity=host, coordinator_spawn_depth=depth,
                              execution_provenance=provenance, coordinator_agent_id=coordinator)

    def test_three_ready_independent_streams_fit_three_total_writers_by_default(self):
        plan = self.plan()
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertEqual({p["stream"] for p in plan["dispatch_packets"]}, {"A", "B", "C"})
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 3)
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 3)
        self.assertEqual(plan["capacity"]["observed_active_writer_count"], 0)
        self.assertFalse(plan["capacity"]["host_admission_confirmed"])
        self.assertTrue(all(p["action"] == "assign_native_writer" for p in plan["dispatch_packets"]))
        self.assertTrue(all("coordinator_if_free" in p["placement_options"] for p in plan["dispatch_packets"]))
        paths = [set(p["write_paths"]) for p in plan["dispatch_packets"]]
        self.assertTrue(all(not left & right for index, left in enumerate(paths) for right in paths[index + 1:]))

    def test_above_three_project_capacity_discloses_operating_and_structural_limits(self):
        self.execution["max_parallel_tickets"] = 5
        before = deepcopy(self.execution)
        plan = self.plan(host=5)
        capacity = plan["capacity"]
        self.assertEqual(capacity["project_writer_limit"], 5)
        self.assertEqual(capacity["planner_writer_limit"], 6)
        self.assertEqual(capacity["effective_writer_capacity"], 3)
        self.assertEqual(capacity["limiting_sources"], ["operating_count"])
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertEqual(self.execution, before)
        self.assertEqual(capacity["operating_state"], "UNOBSERVED_LEGACY")
        self.assertIn("Planner structural writer limit: 6", render_markdown(plan).decode())

    def test_default_review_pool_assigns_one_reviewer_per_stream_not_per_ticket(self):
        plan = self.plan(host=7)
        review = plan["review_plan"]
        self.assertEqual(review["configured_count"], 3)
        self.assertEqual(review["scope"], "project_total_not_per_ticket")
        self.assertEqual({item["stream"]: item["reviewer_slot"] for item in review["assignments"]},
                         {"A": "independent-reviewer-1", "B": "independent-reviewer-2", "C": "independent-reviewer-3"})
        self.assertEqual(review["planned_assignment_count"], 3)
        self.assertEqual(review["concurrent_reviewer_planning_ceiling"], 3)
        self.assertIsNone(review["observed_active_reviewer_count"])
        self.assertFalse(review["host_admission_confirmed"])
        self.assertTrue(all(item["agent_id"] is None and item["independent_context_required"] for item in review["assignments"]))
        self.assertTrue(all(stream["independent_review"]["stream"] == stream["label"] for stream in plan["streams"]))

    def test_four_shared_host_slots_do_not_claim_six_concurrent_agents(self):
        review = self.plan(host=4)["review_plan"]
        self.assertEqual(review["planned_or_retained_writer_slots"], 3)
        self.assertEqual(review["concurrent_reviewer_planning_ceiling"], 0)
        self.assertEqual(review["additional_shared_host_slot_ceiling"], 0)
        self.assertEqual(review["separate_coordinator_slots"], 1)
        self.assertEqual(review["coordinator_slot_basis"], "separate_or_unconfirmed")
        self.assertEqual(review["configured_count"], 3)
        self.assertIsNone(review["observed_active_reviewer_count"])
        self.assertEqual(self.plan(host=3)["review_plan"]["concurrent_reviewer_planning_ceiling"], 0)

    def test_lower_reviewer_pool_keeps_unassigned_streams_waiting_without_waiving_review(self):
        for count in [0, 1, 2]:
            with self.subTest(count=count):
                self.execution["independent_reviewers"]["count"] = count
                plan = self.plan(host=7)
                review = plan["review_plan"]
                self.assertEqual(review["configured_count"], count)
                self.assertEqual(review["planned_assignment_count"], count)
                self.assertEqual(sum(item["state"] == "WAITING_FOR_REVIEWER_POOL" for item in review["assignments"]), 3 - count)
                self.assertEqual(review["concurrent_reviewer_planning_ceiling"], count)
                self.assertEqual(self.execution["independent_reviewers"]["count"], count)

    def test_three_retained_child_writers_and_separate_coordinator_exhaust_four_host_slots(self):
        for index, label in [(0, "A"), (2, "B"), (3, "C")]:
            self.value["tickets"][index].update(status="in_progress", ownership=owner(label, "child-" + label), history=[history()])
        for coordinator in ["distinct-coordinator", None]:
            with self.subTest(coordinator=coordinator):
                review = self.plan(host=4, coordinator=coordinator)["review_plan"]
                self.assertEqual(review["planned_or_retained_writer_slots"], 3)
                self.assertEqual(review["separate_coordinator_slots"], 1)
                self.assertEqual(review["planned_or_retained_host_slots"], 4)
                self.assertEqual(review["concurrent_reviewer_planning_ceiling"], 0)
                self.assertEqual(review["additional_shared_host_slot_ceiling"], 0)

    def test_coordinator_retained_as_writer_is_counted_once_for_reviewer_headroom(self):
        for index, label in [(0, "A"), (2, "B"), (3, "C")]:
            self.value["tickets"][index].update(status="in_progress", ownership=owner(label, "writer-" + label), history=[history()])
        review = self.plan(host=4, coordinator="writer-A")["review_plan"]
        self.assertEqual(review["separate_coordinator_slots"], 0)
        self.assertEqual(review["coordinator_slot_basis"], "retained_writer")
        self.assertEqual(review["planned_or_retained_host_slots"], 3)
        self.assertEqual(review["concurrent_reviewer_planning_ceiling"], 1)

    def test_explicit_direct_coordinator_packet_is_counted_once_for_shared_headroom(self):
        plan = self.plan(host=4, depth=1)
        self.assertEqual(plan["dispatch_packets"][0]["action"], "run_in_coordinator")
        review = plan["review_plan"]
        self.assertEqual(review["coordinator_slot_basis"], "planned_direct_writer")
        self.assertEqual(review["separate_coordinator_slots"], 0)
        self.assertEqual(review["planned_or_retained_host_slots"], 1)
        self.assertEqual(review["additional_shared_host_slot_ceiling"], 3)
        self.assertEqual(review["concurrent_reviewer_planning_ceiling"], 0)

    def test_legacy_execution_defaults_review_pool_without_mutating_input(self):
        del self.execution["independent_reviewers"]
        plan = self.plan(host=4)
        self.assertEqual(plan["review_plan"]["configured_count"], 3)
        self.assertNotIn("independent_reviewers", self.execution)

    def test_unknown_host_and_exhausted_depth_do_not_imply_reviewer_launch_capacity(self):
        self.assertIsNone(self.plan(host=None)["review_plan"]["concurrent_reviewer_planning_ceiling"])
        self.assertEqual(self.plan(host=6, depth=1)["review_plan"]["concurrent_reviewer_planning_ceiling"], 0)

    def test_reviewer_assignments_exclude_known_implementer_identity(self):
        self.value["tickets"][0].update(ownership=owner(), status="in_progress", history=[history()])
        plan = self.plan(host=4)
        assignment = next(item for item in plan["review_plan"]["assignments"] if item["stream"] == "A")
        self.assertEqual(assignment["excluded_implementer_agent_id"], "synthetic-agent-A")
        self.assertIsNone(assignment["agent_id"])

    def test_malformed_review_pools_are_rejected(self):
        for value in [None, {"count": True, "allocation": "one_per_stream"},
                      {"count": -1, "allocation": "one_per_stream"},
                      {"count": 3, "allocation": "three_per_ticket"}, {"count": 3}]:
            with self.subTest(value=value):
                self.execution["independent_reviewers"] = value
                with self.assertRaises(ValidationError):
                    self.plan()

    def test_binding_capacity_sources_distinguish_lower_project_host_and_depth(self):
        for project, host, depth, sources in [(2, 5, 0, ["operating_count", "project_configuration"]),
                                              (5, 1, 0, ["observed_host_capacity"]),
                                              (5, 5, 1, ["spawn_depth"]),
                                              (2, 2, 0, ["observed_host_capacity", "operating_count", "project_configuration"])]:
            with self.subTest(project=project, host=host, depth=depth):
                self.execution["max_parallel_tickets"] = project
                plan = self.plan(host=host, depth=depth)
                self.assertEqual(plan["capacity"]["limiting_sources"], sources)
                self.assertEqual(self.execution["max_parallel_tickets"], project)

    def test_unobserved_host_reports_only_planning_sources_without_claiming_capacity(self):
        self.execution["max_parallel_tickets"] = 5
        capacity = self.plan(host=None)["capacity"]
        self.assertEqual(capacity["limiting_sources"], ["operating_count"])
        self.assertEqual(capacity["planning_writer_capacity"], 3)
        self.assertIsNone(capacity["effective_writer_capacity"])

    def test_lower_project_limit_keeps_fresh_plan_within_ceiling(self):
        self.execution["max_parallel_tickets"] = 2
        plan = self.plan()
        self.assertEqual(len(plan["streams"]), 2)
        self.assertEqual(len(plan["dispatch_packets"]), 2)
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 0)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 2)
        self.assertIn("max_parallel_tickets", " ".join(plan["capacity"]["reasons"]))
        self.assertFalse(plan["next_step"]["authorization"]["required"])

    def test_host_total_one_does_not_mean_one_free_child_plus_parent(self):
        plan = self.plan(host=1)
        self.assertEqual(len(plan["dispatch_packets"]), 1)
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 2)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 1)
        self.assertIn("including_coordinator", plan["capacity"]["host_capacity_source"])

    def test_no_observed_host_capacity_retains_readiness_without_claiming_live_admission(self):
        plan = self.plan(host=None, live_shaped=True)
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertIsNone(plan["capacity"]["effective_writer_capacity"])
        self.assertFalse(plan["capacity"]["host_capacity_observed"])
        self.assertTrue(all(p["selection_state"] == "READY_HOST_CAPACITY_UNVERIFIED" and not p["live_dispatch_eligible"] for p in plan["dispatch_packets"]))
        self.assertIn("Observe the native host", plan["next_step"]["action"])
        self.assertFalse(plan["next_step"]["authorization"]["required"])

    def test_synthetic_packets_stay_non_authoritative_after_capacity_is_supplied(self):
        plan = self.plan(host=3)
        self.assertTrue(all(not p["live_dispatch_eligible"] and not p["execution_authority"] for p in plan["dispatch_packets"]))
        self.assertTrue(all(not p["host_admission_confirmed"] for p in plan["dispatch_packets"]))
        self.assertFalse(plan["execution_authority"])

    def test_known_capacity_makes_live_shaped_packets_eligible_but_never_confirms_running_agents(self):
        plan = self.plan(host=3, live_shaped=True)
        self.assertTrue(all(p["live_dispatch_eligible"] for p in plan["dispatch_packets"]))
        self.assertTrue(all(not p["host_admission_confirmed"] and not p["execution_authority"] for p in plan["dispatch_packets"]))
        self.assertEqual(plan["capacity"]["observed_active_writer_count"], 0)

    def test_active_owner_consumes_capacity_before_a_new_writer(self):
        self.value["tickets"][2].update(status="in_progress", ownership=owner("C", "existing-C"), history=[history()])
        plan = self.plan(host=2)
        self.assertEqual(plan["capacity"]["retained_writer_count"], 1)
        self.assertEqual(plan["capacity"]["observed_active_writer_count"], 1)
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 1)
        self.assertEqual(plan["dispatch_packets"][0]["existing_agent_id"], "existing-C")
        self.assertEqual(len(plan["dispatch_packets"]), 2)
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 1)

    def test_paused_owner_is_not_a_free_slot_or_a_replacement_candidate(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(state="paused"), history=[history()])
        plan = self.plan(host=2)
        self.assertEqual(plan["capacity"]["observed_paused_writer_count"], 1)
        self.assertEqual(len(plan["dispatch_packets"]), 1)
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 1)
        self.assertFalse(any(p["stream"] == "A" for p in plan["dispatch_packets"]))
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 1)

    def test_retained_owners_exceeding_current_capacity_are_preserved_for_reconciliation(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history()])
        self.value["tickets"][2].update(status="in_progress", ownership=owner("B", "existing-B"), history=[history()])
        plan = self.plan(host=1)
        self.assertEqual(plan["capacity"]["retained_writer_count"], 2)
        self.assertTrue(plan["capacity"]["ownership_over_capacity"])
        self.assertEqual(plan["dispatch_packets"], [])
        self.assertTrue(all(p["selection_reason"] == "RETAINED_OWNERSHIP_EXCEEDS_CAPACITY" for p in plan["deferred_dispatch_packets"]))
        self.assertEqual(plan["tickets"]["DEMO-11"]["ownership"], self.value["tickets"][0]["ownership"])
        self.assertIn("Preserve every active or paused owner", plan["next_step"]["action"])

    def test_a_fourth_ready_ticket_or_excess_host_slots_cannot_add_a_fourth_writer(self):
        self.value["tickets"][1]["dependencies"] = []
        self.value["tickets"][1]["write_paths"] = ["src/fourth"]
        self.execution["max_parallel_tickets"] = 20
        self.execution["max_parallel_tickets_per_stream"] = 5
        plan = self.plan(host=20)
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertEqual(len({p["stream"] for p in plan["dispatch_packets"]}), 3)
        self.assertEqual(plan["capacity"]["effective_per_stream_limit"], 1)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 3)

    def test_explicitly_disabled_native_streams_queue_without_inventing_an_activation_token(self):
        self.execution["native_streams"]["enabled"] = False
        plan = self.plan(host=3)
        self.assertEqual(plan["dispatch_packets"], [])
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 3)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 0)
        self.assertIn("policy is disabled", plan["next_step"]["action"])
        self.assertFalse(plan["next_step"]["authorization"]["required"])

    def test_zero_spawn_depth_keeps_one_direct_coordinator_writer(self):
        self.execution["max_spawn_depth"] = 0
        plan = self.plan(host=3)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 1)
        self.assertEqual(len(plan["dispatch_packets"]), 1)
        self.assertEqual(plan["dispatch_packets"][0]["action"], "run_in_coordinator")
        self.assertEqual(plan["dispatch_packets"][0]["placement_options"], ["current_coordinator_if_free"])
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 2)

    def test_non_root_coordinator_honors_its_actual_spawn_depth(self):
        plan = self.plan(host=3, depth=1)
        self.assertEqual(plan["capacity"]["max_spawn_depth"], 1)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 1)
        self.assertEqual(plan["dispatch_packets"][0]["action"], "run_in_coordinator")

    def test_exhausted_spawn_depth_does_not_invalidate_three_retained_writers(self):
        for index, label in [(0, "A"), (2, "B"), (3, "C")]:
            self.value["tickets"][index].update(status="in_progress", ownership=owner(label, "existing-" + label), history=[history()])
        plan = self.plan(depth=1, coordinator="existing-A")
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 3)
        self.assertFalse(plan["capacity"]["ownership_over_capacity"])
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertTrue(all(p["action"] == "continue_existing_agent" for p in plan["dispatch_packets"]))
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 0)

    def test_exhausted_depth_counts_an_existing_coordinator_only_once(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner("A", "coordinator"), history=[history()])
        plan = self.plan(depth=1, coordinator="coordinator")
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 0)
        self.assertEqual(len(plan["dispatch_packets"]), 1)
        self.assertTrue(plan["capacity"]["coordinator_is_retained_writer"])
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 2)

    def test_exhausted_depth_allows_one_proven_free_coordinator_beside_an_existing_writer(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner("A", "existing-child"), history=[history()])
        plan = self.plan(depth=1, coordinator="free-coordinator")
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 2)
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 1)
        self.assertEqual([p["action"] for p in plan["dispatch_packets"]], ["continue_existing_agent", "run_in_coordinator"])

    def test_exhausted_depth_requires_coordinator_identity_before_counting_another_direct_writer(self):
        self.value["tickets"][0].update(status="in_progress", ownership=owner(), history=[history()])
        plan = self.plan(depth=1)
        self.assertEqual(len(plan["dispatch_packets"]), 1)
        self.assertEqual(plan["capacity"]["selected_new_writer_count"], 0)
        self.assertIsNone(plan["capacity"]["coordinator_is_retained_writer"])
        self.assertIn("identity is unknown", " ".join(plan["capacity"]["reasons"]))

    def test_zero_host_capacity_queues_work_with_a_concrete_slot_release_trigger(self):
        plan = self.plan(host=0)
        self.assertEqual(plan["dispatch_packets"], [])
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 3)
        self.assertIn("confirmed writer slot", plan["next_step"]["action"])
        self.assertFalse(plan["next_step"]["authorization"]["required"])

    def test_execution_and_host_facts_are_fingerprinted_and_bound_to_every_packet(self):
        provenance = {"source": "test_configuration", "config_path": "synthetic/config.json", "config_sha256": "a" * 64}
        plan = self.plan(host=3, provenance=provenance)
        capacity = plan["capacity"]
        self.assertEqual(capacity["execution_policy_fingerprint"], fingerprint("native-execution", self.execution))
        self.assertEqual(capacity["capacity_fingerprint"], fingerprint("native-capacity", capacity["capacity_binding"]))
        self.assertTrue(all(p["capacity_fingerprint"] == capacity["capacity_fingerprint"] for p in plan["dispatch_packets"]))
        self.assertNotEqual(capacity["capacity_fingerprint"], self.plan(host=2, provenance=provenance)["capacity"]["capacity_fingerprint"])
        self.execution["max_parallel_tickets"] = 2
        self.assertNotEqual(capacity["execution_policy_fingerprint"], self.plan()["capacity"]["execution_policy_fingerprint"])

    def test_malformed_execution_capacity_and_depth_inputs_are_rejected(self):
        for field, value in [("max_parallel_tickets", True), ("max_parallel_tickets", 0),
                             ("max_parallel_tickets_per_stream", "1"), ("max_spawn_depth", -1),
                             ("max_spawn_depth", 2), ("max_parallel_tickets_per_stream", 7),
                             ("native_streams", {"enabled": "true", "dispatch_policy": "ready_independent"})]:
            with self.subTest(field=field, value=value):
                execution = deepcopy(self.execution)
                execution[field] = value
                with self.assertRaises(ValidationError):
                    self.plan(execution=execution)
        for host, depth in [(True, 0), (-1, 0), ("3", 0), (3, True), (3, -1)]:
            with self.subTest(host=host, depth=depth):
                with self.assertRaises(ValidationError):
                    self.plan(host=host, depth=depth)

    def test_false_reference_and_broker_switches_are_not_native_gates(self):
        self.execution.update(dispatch_enabled=False, auto_dispatch=False, auto_request_critic=False,
                              auto_resume_amendments=False, broker={"enabled": False, "max_workers": 1})
        plan = self.plan(host=3)
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertEqual(plan["capacity"]["effective_writer_capacity"], 3)

    def test_enabled_broker_ceiling_is_shared_but_observed_slots_are_separate(self):
        self.execution["host_broker"] = {"enabled": False, "max_workers": 1, "max_gpu_jobs": 0}
        disabled = self.plan(host=4)
        self.assertEqual(6, disabled["capacity"]["effective_ceiling"])
        self.assertEqual(3, len(disabled["dispatch_packets"]))
        self.execution["host_broker"] = {"enabled": True, "max_workers": 2}
        enabled = self.plan(host=1)
        self.assertEqual(2, enabled["capacity"]["effective_ceiling"])
        self.assertEqual('$.execution.host_broker.max_workers', enabled["capacity"]["effective_ceiling_governance_path"])
        self.assertEqual(1, enabled["capacity"]["effective_writer_capacity"])
        self.assertEqual(1, enabled["capacity"]["host_writer_capacity"])
        self.assertNotEqual(disabled["capacity"]["execution_policy_fingerprint"], enabled["capacity"]["execution_policy_fingerprint"])
        previous_hash = enabled["capacity"]["execution_policy_fingerprint"]
        self.execution["host_broker"]["max_workers"] = 3
        self.assertNotEqual(previous_hash, self.plan(host=1)["capacity"]["execution_policy_fingerprint"])

    def test_disabled_broker_values_are_not_native_limits_and_invalid_enabled_shape_refuses(self):
        self.execution["host_broker"] = {"enabled": False, "max_workers": "unused", "max_heavy_jobs": -1}
        self.assertEqual(3, len(self.plan(host=3)["dispatch_packets"]))
        for broker in (None, {"enabled": 1}, {"enabled": True, "max_workers": False}):
            self.execution["host_broker"] = broker
            with self.subTest(broker=broker), self.assertRaises(ValidationError):
                self.plan(host=3)


class PlanPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-stream-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        (self.runtime / "MANIFEST.json").write_text("{}")
        self.raw = FIXTURE.read_bytes()

    def publish(self, expected=None):
        return write_project_plan(self.project, self.raw, sha256(self.raw), NOW, self.runtime, expected, True)

    def test_project_plan_is_complete_bound_and_rerunnable_with_explicit_cas(self):
        result = self.publish()
        plan_bytes = (self.project / PLAN).read_bytes()
        plan = loads(plan_bytes.decode())
        self.assertEqual(plan["markdown_sha256"], sha256((self.project / MARKDOWN).read_bytes()))
        self.assertEqual(result["plan_sha256"], sha256(plan_bytes))
        self.assertFalse((self.project / JOURNAL).exists())
        again = self.publish(result["plan_sha256"])
        self.assertEqual(result["plan_sha256"], again["plan_sha256"])

    def test_unacknowledged_existing_plan_and_stale_cas_preserve_bytes(self):
        result = self.publish()
        originals = {name: (self.project / name).read_bytes() for name in [PLAN, MARKDOWN]}
        for expected in [None, "0" * 64]:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValidationError, "Existing plan is protected"):
                    self.publish(expected)
                self.assertEqual(originals, {name: (self.project / name).read_bytes() for name in originals})
        self.assertTrue(result["next_step"])

    def test_user_markdown_edits_are_not_clobbered_even_with_correct_plan_pin(self):
        result = self.publish()
        changed = (self.project / MARKDOWN).read_bytes() + b"\nUser-owned additional detail\n"
        (self.project / MARKDOWN).write_bytes(changed)
        with self.assertRaisesRegex(ValidationError, "changed after plan generation"):
            self.publish(result["plan_sha256"])
        self.assertEqual((self.project / MARKDOWN).read_bytes(), changed)

    def test_partial_preexisting_plan_is_preserved(self):
        (self.project / MARKDOWN).write_bytes(b"Existing project plan\n")
        with self.assertRaisesRegex(ValidationError, "Existing plan is incomplete"):
            self.publish()
        self.assertEqual((self.project / MARKDOWN).read_bytes(), b"Existing project plan\n")

    def test_source_release_cannot_be_polluted_with_generated_project_files(self):
        with self.assertRaisesRegex(ValidationError, "immutable release"):
            write_project_plan(self.runtime, self.raw, sha256(self.raw), NOW, self.runtime, allow_synthetic=True)
        self.assertFalse((self.runtime / PLAN).exists())

    def test_installed_project_root_can_receive_a_plan_outside_immutable_runtime(self):
        (self.project / ".agentic").mkdir()
        result = write_project_plan(self.project, self.raw, sha256(self.raw), NOW, self.project, allow_synthetic=True)
        self.assertEqual(result["status"], "PLANNED")
        with self.assertRaisesRegex(ValidationError, "installed runtime files"):
            write_project_plan(self.project / ".agentic", self.raw, sha256(self.raw), NOW, self.project, allow_synthetic=True)

    def test_hardlinked_output_is_rejected_and_target_untouched(self):
        target = self.base / "outside.md"
        target.write_bytes(b"Do not touch\n")
        os.link(target, self.project / MARKDOWN)
        with self.assertRaisesRegex(ValidationError, "linked file"):
            self.publish()
        self.assertEqual(target.read_bytes(), b"Do not touch\n")

    def test_mid_publication_failure_rolls_back_only_own_writes(self):
        original_write = Tree.write
        failed = False

        def fail_once(tree, name, data):
            nonlocal failed
            if name == MARKDOWN and not failed:
                failed = True
                raise OSError("Synthetic full disk")
            return original_write(tree, name, data)

        with patch.object(Tree, "write", fail_once):
            with self.assertRaisesRegex(OSError, "Synthetic full disk"):
                self.publish()
        self.assertFalse((self.project / PLAN).exists())
        self.assertFalse((self.project / MARKDOWN).exists())
        self.assertFalse((self.project / JOURNAL).exists())

    def test_external_edit_during_failure_is_preserved_for_reconciliation(self):
        original_write = Tree.write

        def edit_and_fail(tree, name, data):
            if name == MARKDOWN:
                original_write(tree, name, b"Concurrent human edit\n")
                raise OSError("Synthetic interrupted publication")
            return original_write(tree, name, data)

        with patch.object(Tree, "write", edit_and_fail):
            with self.assertRaisesRegex(ValidationError, "external edits"):
                self.publish()
        self.assertEqual((self.project / MARKDOWN).read_bytes(), b"Concurrent human edit\n")
        self.assertTrue((self.project / JOURNAL).exists())

    def test_forged_journal_is_not_restoration_authority(self):
        self.publish()
        before = {name: (self.project / name).read_bytes() for name in [PLAN, MARKDOWN]}
        journal = {"previous": {name: None for name in before}, "next_sha256": {name: sha256(data) for name, data in before.items()}}
        (self.project / JOURNAL).write_bytes(canonical(journal))
        with self.assertRaisesRegex(ValidationError, "trusted original"):
            self.publish()
        self.assertEqual(before, {name: (self.project / name).read_bytes() for name in before})

    def test_interrupted_update_recovers_only_with_the_trusted_original_plan_pin(self):
        result = self.publish()
        before = {name: (self.project / name).read_bytes() for name in [PLAN, MARKDOWN]}
        next_plan = b'{"synthetic":"interrupted-update"}\n'
        journal = {"previous": {name: b64encode(data).decode() for name, data in before.items()},
                   "next_sha256": {PLAN: sha256(next_plan), MARKDOWN: sha256(before[MARKDOWN])}}
        (self.project / JOURNAL).write_bytes(canonical(journal))
        (self.project / PLAN).write_bytes(next_plan)
        self.publish(result["plan_sha256"])
        self.assertEqual(before, {name: (self.project / name).read_bytes() for name in before})
        self.assertFalse((self.project / JOURNAL).exists())

    def test_tampered_transaction_original_markdown_is_rejected(self):
        result = self.publish()
        before = {name: (self.project / name).read_bytes() for name in [PLAN, MARKDOWN]}
        journal = {"previous": {PLAN: b64encode(before[PLAN]).decode(), MARKDOWN: b64encode(b"forged").decode()},
                   "next_sha256": {name: sha256(data) for name, data in before.items()}}
        (self.project / JOURNAL).write_bytes(canonical(journal))
        with self.assertRaisesRegex(ValidationError, "Markdown binding"):
            self.publish(result["plan_sha256"])
        self.assertEqual(before, {name: (self.project / name).read_bytes() for name in before})


class PlannerCliTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("awf_plan_streams_test", ROOT / ".agentic/scripts/plan_streams.py")
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def test_argument_error_includes_action_owner_and_no_routine_approval_prompt(self):
        output = io.StringIO()
        with patch("sys.stderr", output):
            code = self.cli.main([])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "REJECTED")
        self.assertTrue(report["next_step"]["action"])
        self.assertTrue(report["next_step"]["owner"])
        self.assertFalse(report["prompt_user"])
        self.assertFalse(report["next_step"]["authorization"]["required"])

    def test_integrity_rejection_has_a_concrete_next_step(self):
        output = io.StringIO()
        with patch.object(self.cli, "verify_installed", side_effect=ValidationError("Synthetic manifest mismatch")), patch("sys.stderr", output):
            code = self.cli.main(["--input", str(FIXTURE), "--expected-input-sha256", sha256(FIXTURE.read_bytes()), "--project-root", str(ROOT)])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertIn("Synthetic manifest mismatch", report["next_step"]["action"])

    def test_cli_applies_configured_limit_and_records_observed_host_capacity(self):
        with tempfile.TemporaryDirectory(prefix="awf-cap-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            configuration = operating_governance()
            configuration["execution"]["max_parallel_tickets"] = 2
            configuration["controller"] = {"dispatch_enabled": False, "auto_dispatch": False}
            config_path = root / "config.json"
            config_bytes = canonical(configuration)
            config_path.write_bytes(config_bytes)
            (project / ".agentic").mkdir()
            (project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(config_bytes)
            (project / "OPERATING_CONFIG.yaml").write_bytes(canonical(operating_configuration(2)))
            output = io.StringIO()
            with patch.object(self.cli, "verify_installed", return_value="a" * 64), patch("sys.stdout", output):
                code = self.cli.main(["--input", str(FIXTURE), "--expected-input-sha256", sha256(FIXTURE.read_bytes()),
                    "--project-root", str(project), "--config", str(config_path), "--host-writer-capacity", "3",
                    "--allow-synthetic", "--now", NOW])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["capacity"]["effective_writer_capacity"], 2)
            self.assertEqual(len(report["dispatch_packets"]), 2)
            self.assertEqual(len(report["deferred_dispatch_packets"]), 0)
            self.assertEqual(report["capacity"]["execution_provenance"]["config_sha256"], sha256(config_bytes))
            self.assertFalse(report["prompt_user"])

    def test_external_runtime_defaults_to_actual_target_configuration(self):
        for limit, enabled, expected in [(1, True, 1), (2, True, 2), (3, False, 0)]:
            with self.subTest(limit=limit, enabled=enabled), tempfile.TemporaryDirectory(prefix="awf-target-") as temporary:
                project = Path(temporary)
                (project / ".agentic").mkdir()
                governing = operating_governance()
                policy = governing["execution"]
                policy["max_parallel_tickets"] = limit
                policy["native_streams"]["enabled"] = enabled
                config = project / ".agentic/PROJECT_CONFIG.yaml"
                config.write_bytes(canonical(governing))
                (project / "OPERATING_CONFIG.yaml").write_bytes(canonical(operating_configuration(limit)))
                output = io.StringIO()
                with patch.object(self.cli, "verify_installed", return_value="a" * 64), patch("sys.stdout", output):
                    code = self.cli.main(["--input", str(FIXTURE), "--expected-input-sha256", sha256(FIXTURE.read_bytes()),
                        "--project-root", str(project), "--host-writer-capacity", "3", "--allow-synthetic", "--now", NOW])
                report = json.loads(output.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(len(report["dispatch_packets"]), expected)
                self.assertEqual(report["capacity"]["execution_provenance"]["source"], "installed_target_project_configuration")
                self.assertEqual(Path(report["capacity"]["execution_provenance"]["config_path"]), config)

    def test_live_shaped_inventory_cannot_inherit_missing_or_other_project_configuration(self):
        with tempfile.TemporaryDirectory(prefix="awf-identity-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            value = loads(FIXTURE.read_text())
            value["inventory"]["source"] = "jira_snapshot"
            inventory = root / "inventory.json"
            raw = canonical(value)
            inventory.write_bytes(raw)
            args = ["--input", str(inventory), "--expected-input-sha256", sha256(raw), "--project-root", str(project)]
            for config in [None, {"execution": deepcopy(DEFAULT_NATIVE_EXECUTION), "jira": {"project_key": "OTHER"}, "github": {"repository": "other/repo"}}]:
                with self.subTest(config=config):
                    if config:
                        (project / ".agentic").mkdir(exist_ok=True)
                        (project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(config))
                    output = io.StringIO()
                    with patch.object(self.cli, "verify_installed", return_value="a" * 64), patch("sys.stderr", output):
                        code = self.cli.main(args)
                    report = json.loads(output.getvalue())
                    self.assertEqual(code, 2)
                    self.assertFalse(report["next_step"]["authorization"]["required"])
                    self.assertFalse((project / PLAN).exists())


if __name__ == "__main__":
    unittest.main()
