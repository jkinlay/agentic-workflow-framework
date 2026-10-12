"""AWF-50 backlog planning and lane-sizing regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load, sha256
from agentic import planning

NOW = "2026-10-12T12:00:00Z"


def config(max_lanes=12):
    value = load(ROOT / ".agentic/PROJECT_CONFIG.yaml")
    value["execution"]["max_parallel_tickets"] = 12
    value["execution"]["max_lanes"] = max_lanes
    value["execution"]["host_broker"] = {"enabled": False, "max_workers": 12}
    value["merge_gate"]["trusted_owner_ids"] = [101, 202]
    value["github"]["expected_actor_id"] = 101
    return value


def ticket(key, path, **extra):
    return {"key": key, "status": "open", "write_paths": [path], **extra}


def owner_source(current, mode, value, reason, actor=101, source_id="comment-1"):
    command = planning.owner_override_command(current, mode, value, reason)
    return {
        "channel": "github_pr_comment", "comment_id": 77,
        "actor_id": actor, "actor_login": "owner",
        "created_at": NOW, "updated_at": NOW,
        "binding": {"current_override_id": current},
        "raw_body": command, "raw_body_sha256": sha256(command.encode("utf-8")),
        "source_id": source_id,
    }


def verify_owner(source, expected):
    return {"status": "VERIFIED", "actor_id": source["actor_id"],
            "binding": source["binding"], "raw_body_sha256": source["raw_body_sha256"],
            "source_id": source["source_id"], "observed_at": NOW}


class GraphTests(unittest.TestCase):
    def test_dependencies_file_sources_generator_ownership_and_manifest_exception(self):
        tickets = [
            ticket("AWF-1", "src/generator.py", generated_outputs=["src/generated.py"],
                   open_pr_files=["src/pr.py", "MANIFEST.json"]),
            ticket("AWF-2", "src/generated.py", declared_dependencies=["AWF-1"],
                   worktree_diff=["src/worktree.py", "MANIFEST.md"]),
            ticket("AWF-3", "src/pr.py", jira_links=[{"type": "Blocks", "direction": "outward", "key": "AWF-4"}]),
            ticket("AWF-4", "src/four.py"),
        ]
        graph = planning.build_backlog_graph(tickets, config())
        edges = {(edge["blocker"], edge["dependent"]): edge["sources"] for edge in graph["dependencies"]}
        self.assertIn(("AWF-1", "AWF-2"), edges)
        self.assertIn(("AWF-3", "AWF-4"), edges)
        overlaps = {(edge["left"], edge["right"]): edge for edge in graph["overlaps"]}
        self.assertEqual(["src/generated.py"], overlaps[("AWF-1", "AWF-2")]["paths"])
        self.assertEqual(["src/pr.py"], overlaps[("AWF-1", "AWF-3")]["paths"])
        self.assertNotIn("MANIFEST.json", str(graph["overlaps"]))
        self.assertNotIn("MANIFEST.md", str(graph["overlaps"]))
        self.assertEqual("AWF-1", graph["generator_owners"]["src/generated.py"])
        self.assertIn("worktree_diff", graph["tickets"]["AWF-2"]["path_sources"]["src/worktree.py"])

    def test_protected_paths_use_awf48_tiering_without_creating_glob_conflicts(self):
        graph = planning.build_backlog_graph([
            ticket("AWF-1", ".agentic/lib/one.py"),
            ticket("AWF-2", ".agentic/lib/two.py"),
        ], config())
        self.assertEqual([], graph["overlaps"])
        self.assertGreaterEqual(graph["tickets"]["AWF-1"]["protected_path_floor"], 2)
        self.assertGreaterEqual(graph["tickets"]["AWF-1"]["risk_tier"], 2)

    def test_ambiguous_paths_and_generator_owners_refuse(self):
        with self.assertRaises(ValidationError):
            planning.build_backlog_graph([ticket("AWF-1", "src/**")], config())
        with self.assertRaises(ValidationError):
            planning.build_backlog_graph([
                ticket("AWF-1", "a.py", generated_outputs=["out.py"]),
                ticket("AWF-2", "b.py", generated_outputs=["out.py"]),
            ], config())

    def test_case_aliased_file_identities_refuse_before_overlap_detection(self):
        with self.assertRaisesRegex(ValidationError, "case-aliased"):
            planning.build_backlog_graph([
                ticket("AWF-1", "src/Foo.py"),
                ticket("AWF-2", "src/two.py", open_pr_files=["src/foo.py"]),
            ], config())


class LaneTests(unittest.TestCase):
    def test_ready_lanes_are_conflict_free_block_dependents_and_rank_critical_path(self):
        tickets = [
            ticket("AWF-1", "shared.py", expected_rounds=1, round_time_minutes=10),
            ticket("AWF-2", "shared.py", expected_rounds=1, round_time_minutes=10),
            ticket("AWF-3", "three.py", expected_rounds=2, round_time_minutes=20,
                   declared_dependencies=["AWF-1"]),
            ticket("AWF-4", "four.py", expected_rounds=1, round_time_minutes=20),
        ]
        result = planning.plan_lanes(planning.build_backlog_graph(tickets, config()), config())
        scheduled = {key for lane in result["lanes"] for key in lane["ticket_keys"]}
        self.assertEqual({"AWF-1", "AWF-2", "AWF-4"}, scheduled)
        self.assertEqual("AWF-1", result["blocked"]["AWF-3"][0])
        self.assertEqual("AWF-1", result["lanes"][0]["ticket_keys"][0])
        lane_paths = [set(lane["write_paths"]) for lane in result["lanes"]]
        self.assertFalse(any(left & right for i, left in enumerate(lane_paths) for right in lane_paths[i + 1:]))

    def test_in_flight_dependent_remains_blocked_until_its_open_blocker_closes(self):
        tickets = [
            ticket("AWF-1", "one.py"),
            ticket("AWF-2", "two.py", status="in_flight", declared_dependencies=["AWF-1"]),
        ]
        result = planning.plan_lanes(planning.build_backlog_graph(tickets, config()), config())
        scheduled = {key for lane in result["lanes"] for key in lane["ticket_keys"]}
        self.assertEqual({"AWF-1"}, scheduled)
        self.assertEqual(["AWF-1"], result["blocked"]["AWF-2"])

    def test_step1_fixture_yields_ten_strict_lanes(self):
        fixture = json.loads((ROOT / ".agentic/tests/fixtures/planning/step1.json").read_text(encoding="utf-8"))
        result = planning.plan_lanes(planning.build_backlog_graph(fixture["tickets"], config()), config())
        self.assertEqual(10, result["estimate"]["independent_lane_count"])
        self.assertEqual(10, len(result["lanes"]))
        self.assertEqual(10, result["lane_count"])

    def test_estimate_names_binding_constraints_and_headroom(self):
        cfg = config(max_lanes=8)
        tickets = [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 11)]
        resources = {"limits": {"cpu": 7, "ram": 9, "gpu": None, "model_rate": 7},
                     "headroom": {"cpu_percent": 46, "ram_gb": 158.4, "gpu_percent": 92,
                                  "rate_limit_events": 0}}
        result = planning.plan_lanes(planning.build_backlog_graph(tickets, cfg), cfg, resources=resources)
        self.assertEqual(7, result["lane_count"])
        self.assertEqual(10, result["estimate"]["independent_lane_count"])
        self.assertEqual(["cpu", "model_rate"], result["estimate"]["binding_constraints"])
        self.assertEqual(resources["headroom"], result["estimate"]["headroom"])

    def test_estimate_reports_each_governance_constraint_at_its_configured_value(self):
        cfg = config(max_lanes=10)
        cfg["execution"]["max_parallel_tickets"] = 12
        cfg["execution"]["host_broker"] = {"enabled": True, "max_workers": 4}
        tickets = [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 11)]
        result = planning.plan_lanes(planning.build_backlog_graph(tickets, cfg), cfg)
        self.assertEqual({
            "dependency_graph/file_overlap": 10,
            "configured_ceiling": 10,
            "max_parallel_tickets": 12,
            "host_broker": 4,
        }, result["estimate"]["constraints"])
        self.assertEqual(["host_broker"], result["estimate"]["binding_constraints"])
        self.assertEqual(["$.execution.host_broker.max_workers"], result["estimate"]["governance_sources"])


class OwnerOverrideTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.state = {}

    def change(self, mode, value, reason="owner choice", actor=101, source_id="comment-1"):
        current = self.state.get("planning_override", {}).get("override_id")
        return planning.change_owner_override(
            self.cfg, self.state, mode=mode, value=value, reason=reason, now=NOW,
            owner_source=owner_source(current, mode, value, reason, actor, source_id),
            verify_owner_source=verify_owner)

    def test_no_response_accepts_recommendation(self):
        result = planning.plan_lanes(planning.build_backlog_graph([ticket("AWF-1", "one.py")], self.cfg), self.cfg)
        self.assertEqual({"kind": "accepted_default", "value": 1}, result["owner_decision"])

    def test_fixed_and_max_overrides_apply_change_and_audit_in_awf48_shape(self):
        fixed = self.change("fixed", 4)
        self.assertEqual(4, fixed["value"])
        self.assertEqual({"mode": None, "value": None}, fixed["change_audit"]["old"])
        self.assertEqual({"mode": "fixed", "value": 4}, fixed["change_audit"]["new"])
        changed = self.change("max", 2, "reduce capacity", source_id="comment-2")
        self.assertEqual(fixed["override_id"], changed["previous_override_id"])
        self.assertEqual(2, len(self.state["planning_override_history"]))
        tickets = [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 5)]
        result = planning.plan_lanes(
            planning.build_backlog_graph(tickets, self.cfg), self.cfg,
            controller_state=self.state, verify_owner_source=verify_owner)
        self.assertEqual(2, result["lane_count"])

    def test_unauthorized_and_over_ceiling_overrides_refuse(self):
        with self.assertRaisesRegex(ValidationError, "trusted owner"):
            self.change("fixed", 2, actor=999)
        with self.assertRaisesRegex(ValidationError, "ceiling"):
            self.change("fixed", 13)

    def test_above_estimate_fixed_value_warns_and_is_not_clamped(self):
        self.change("fixed", 4)
        result = planning.plan_lanes(planning.build_backlog_graph([ticket("AWF-1", "one.py")], self.cfg), self.cfg,
                                     resources={"limits": {"cpu": 2}, "headroom": {}},
                                     controller_state=self.state, verify_owner_source=verify_owner)
        self.assertEqual(4, result["lane_count"])
        self.assertTrue(any("above" in warning for warning in result["warnings"]))

    def test_direct_or_incomplete_self_hashed_override_cannot_authorize_scheduling(self):
        forged = {"schema_version": 1, "override_id": "forged-without-owner",
                  "created_at": NOW, "mode": "fixed", "value": 4,
                  "previous_override_id": None}
        forged["override_sha256"] = planning.fingerprint(
            "planning_override_record", forged)
        graph = planning.build_backlog_graph(
            [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 5)], self.cfg)
        with self.assertRaisesRegex(ValidationError, "authenticated controller context"):
            planning.verify_override_record(forged)
        with self.assertRaisesRegex(ValidationError, "controller-owned"):
            planning.plan_lanes(graph, self.cfg, override=forged)

        forged_state = {"planning_override": deepcopy(forged),
                        "planning_override_history": [deepcopy(forged)]}
        with self.assertRaisesRegex(ValidationError, "owner-source|audit"):
            planning.plan_lanes(graph, self.cfg, controller_state=forged_state,
                                verify_owner_source=verify_owner)

    def test_stored_override_chain_is_reauthenticated_before_each_plan(self):
        self.change("fixed", 2)
        graph = planning.build_backlog_graph(
            [ticket(f"AWF-{i}", f"src/{i}.py") for i in range(1, 3)], self.cfg)

        def reject_replay(source, expected):
            raise RuntimeError("provider observation no longer authenticates")

        with self.assertRaisesRegex(ValidationError, "authentication failed"):
            planning.plan_lanes(graph, self.cfg, controller_state=self.state,
                                verify_owner_source=reject_replay)


class RecomputeAndAuditTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.state = {}
        self.tickets = [ticket("AWF-1", "one.py")]

    def test_merge_file_intake_close_and_override_triggers_recompute(self):
        for trigger in ("pr_merged", "files_changed", "ticket_added", "ticket_closed", "override_changed"):
            with self.subTest(trigger=trigger):
                result = planning.recompute_plan(self.tickets, self.cfg, self.state, now=NOW, trigger=trigger)
                self.assertEqual("PLANNED", result["status"])
                self.assertEqual(trigger, result["record"]["trigger"])
        self.assertEqual(5, len(self.state["planning_history"]))

    def test_recompute_uses_only_reauthenticated_controller_override_state(self):
        planning.change_owner_override(
            self.cfg, self.state, mode="fixed", value=1, reason="owner choice", now=NOW,
            owner_source=owner_source(None, "fixed", 1, "owner choice"),
            verify_owner_source=verify_owner)
        result = planning.recompute_plan(
            self.tickets, self.cfg, self.state, now=NOW, trigger="override_changed",
            verify_owner_source=verify_owner)
        self.assertEqual("owner_override", result["record"]["owner_decision"]["kind"])

        forged = deepcopy(self.state["planning_override"])
        with self.assertRaisesRegex(ValidationError, "controller-owned"):
            planning.recompute_plan(
                self.tickets, self.cfg, self.state, now=NOW, trigger="override_changed",
                override=forged, verify_owner_source=verify_owner)

    def test_mid_round_reassignment_is_deferred(self):
        result = planning.recompute_plan(self.tickets, self.cfg, self.state, now=NOW,
                                         trigger="files_changed", round_active=True)
        self.assertEqual("DEFERRED_UNTIL_ROUND_END", result["status"])
        self.assertEqual([], self.state.get("planning_history", []))
        self.assertEqual(["files_changed"], self.state["pending_planning_triggers"])

    def test_plan_records_are_sealed_and_append_only(self):
        result = planning.recompute_plan(self.tickets, self.cfg, self.state, now=NOW, trigger="project_start")
        record = result["record"]
        self.assertTrue(planning.verify_plan_record(record))
        mutated = deepcopy(record)
        mutated["lane_count"] = 99
        with self.assertRaises(ValidationError):
            planning.verify_plan_record(mutated)
        with self.assertRaises(ValidationError):
            planning.append_plan_record(self.state, record)


if __name__ == "__main__":
    unittest.main()
