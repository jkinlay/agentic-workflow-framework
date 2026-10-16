"""AWF-115 deterministic ticket-plan and lane-fill regressions."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

FIXTURE = ROOT / ".agentic/tests/fixtures/planning_graph/hand_computed.json"
LANE_FILL_FIELDS = (
    "critical_path", "critical_path_position", "serial_path_id",
    "serial_predecessor", "lane_class", "independent",
    "generator_order_slot", "owner_lane", "idle_lane_reason",
    "blockers", "prestage_allowed", "phase", "status", "in_lane",
)


def implementation():
    return importlib.import_module("agentic.planning_graph")


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build(value=None, **overrides):
    value = value or fixture()
    options = {
        "lane_cap": value["lane_cap"],
        "start_gap_minutes": value["start_gap_minutes"],
        "tier3_round_cap": value["tier3_round_cap"],
        "generator_order": value["generator_order"],
        **overrides,
    }
    return implementation().build_ticket_plan(
        value["tickets"],
        **options,
    )


class TicketPlanSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(
            (ROOT / ".agentic/schemas/ticket-plan.schema.json").read_text(encoding="utf-8")
        )
        self.validator = Draft202012Validator(self.schema)
        self.plan = build()

    def test_schema_round_trip_includes_every_lane_fill_field(self):
        module = implementation()
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual([], list(self.validator.iter_errors(self.plan)))
        round_tripped = json.loads(module.serialize_ticket_plan(self.plan))
        self.assertEqual(self.plan, module.validate_ticket_plan(round_tripped))
        for ticket in round_tripped["tickets"]:
            self.assertTrue(set(LANE_FILL_FIELDS) <= set(ticket))

    def test_missing_unknown_and_malformed_fields_fail_closed(self):
        module = implementation()
        cases = []
        missing = deepcopy(self.plan)
        del missing["tickets"][0]["owner_lane"]
        cases.append((missing, "MISSING_REQUIRED_FIELD"))
        malformed = deepcopy(self.plan)
        malformed["tickets"] = "not-a-list"
        cases.append((malformed, "INVALID_FIELD"))
        unknown = deepcopy(self.plan)
        unknown["tickets"][0]["invented"] = True
        cases.append((unknown, "UNKNOWN_FIELD"))
        for value, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(module.PlanningGraphError) as caught:
                    module.validate_ticket_plan(value)
                self.assertEqual(expected, caught.exception.reason)

    def test_each_lane_fill_field_missing_or_invalid_fails_closed(self):
        module = implementation()
        invalid_values = {
            "critical_path": "yes",
            "critical_path_position": 0,
            "serial_path_id": "",
            "serial_predecessor": 7,
            "lane_class": "background",
            "independent": "yes",
            "generator_order_slot": 0,
            "owner_lane": "",
            "blockers": ["NOT_A_GATE"],
            "prestage_allowed": "yes",
            "idle_lane_reason": "MAYBE_LATER",
            "phase": "",
            "status": "",
            "in_lane": 1,
        }
        for field in LANE_FILL_FIELDS:
            with self.subTest(field=field, case="missing"):
                value = deepcopy(self.plan)
                del value["tickets"][0][field]
                with self.assertRaises(module.PlanningGraphError) as caught:
                    module.validate_ticket_plan(value)
                self.assertEqual("MISSING_REQUIRED_FIELD", caught.exception.reason)
            with self.subTest(field=field, case="invalid"):
                value = deepcopy(self.plan)
                value["tickets"][0][field] = invalid_values[field]
                with self.assertRaises(module.PlanningGraphError) as caught:
                    module.validate_ticket_plan(value)
                expected = (
                    "UNKNOWN_IDLE_LANE_REASON"
                    if field in {"idle_lane_reason", "blockers"}
                    else "INVALID_FIELD"
                )
                self.assertEqual(expected, caught.exception.reason)


class PlanningGraphValidationTests(unittest.TestCase):
    def test_nested_objects_and_arrays_fail_with_named_reasons(self):
        module = implementation()
        for field, malformed, expected in (
            ("dependencies", [{}], "INVALID_FIELD"),
            ("write_paths", [[]], "INVALID_FIELD"),
            ("lane_class", [], "INVALID_FIELD"),
            ("idle_lane_reason", {}, "UNKNOWN_IDLE_LANE_REASON"),
            ("blockers", [{}], "UNKNOWN_IDLE_LANE_REASON"),
        ):
            with self.subTest(field=field):
                value = fixture()
                value["tickets"][0][field] = malformed
                with self.assertRaises(module.PlanningGraphError) as caught:
                    build(value)
                self.assertEqual(expected, caught.exception.reason)

    def test_tier3_cap_is_frozen_at_three_in_runtime_and_schema(self):
        module = implementation()
        value = fixture()
        value["tier3_round_cap"] = 4
        value["tickets"][0]["round_cap"] = 4
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("TIER_ROUND_CAP_VIOLATION", caught.exception.reason)

        schema = json.loads(
            (ROOT / ".agentic/schemas/ticket-plan.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(3, schema["properties"]["tier3_round_cap"]["maximum"])
        malformed_plan = build()
        malformed_plan["tickets"][0]["round_cap"] = 4
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(malformed_plan)))

    def test_tier_round_cap_cycle_and_dangling_dependency_have_named_reasons(self):
        module = implementation()
        value = fixture()
        value["tickets"][0]["risk_tier"] = 4
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("INVALID_RISK_TIER", caught.exception.reason)

        value = fixture()
        value["tickets"][0]["round_cap"] = 4
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("TIER_ROUND_CAP_VIOLATION", caught.exception.reason)

        value = fixture()
        value["tickets"][0]["dependencies"] = ["AWF-300"]
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("DEPENDENCY_CYCLE", caught.exception.reason)

        value = fixture()
        value["tickets"][0]["dependencies"] = ["AWF-999"]
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("DANGLING_DEPENDENCY", caught.exception.reason)

    def test_unknown_input_and_idle_reason_fail_closed(self):
        module = implementation()
        value = fixture()
        value["tickets"][0]["surprise"] = "unreviewed"
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("UNKNOWN_FIELD", caught.exception.reason)

        value = fixture()
        value["tickets"][1]["idle_lane_reason"] = "UNKNOWN"
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("UNKNOWN_IDLE_LANE_REASON", caught.exception.reason)


class PlanningGraphSchedulingTests(unittest.TestCase):
    def test_overlap_orders_writers_while_disjoint_footprints_run_in_parallel(self):
        plan = build()
        edges = {(edge["from"], edge["to"], edge["kind"]) for edge in plan["edges"]}
        self.assertIn(("AWF-115", "AWF-36", "dependency"), edges)
        self.assertIn(("AWF-115", "AWF-36", "file_overlap"), edges)
        self.assertFalse(any("AWF-200" in (edge["from"], edge["to"]) for edge in plan["edges"]))
        starts = {item["key"]: item["earliest_start_minute"] for item in plan["tickets"]}
        self.assertGreaterEqual(starts["AWF-200"] - starts["AWF-115"], 2)

    def test_portable_case_aliases_create_a_file_overlap_edge(self):
        value = fixture()
        value["tickets"] = [value["tickets"][0], value["tickets"][2]]
        value["tickets"][0]["write_paths"] = ["src/Foo.py"]
        value["tickets"][1]["write_paths"] = ["src/foo.py"]
        plan = build(value)
        overlap = [edge for edge in plan["edges"] if edge["kind"] == "file_overlap"]
        self.assertEqual(
            [{"from": "AWF-115", "to": "AWF-200", "kind": "file_overlap", "paths": ["src/foo.py"]}],
            overlap,
        )

        generator_value = fixture()
        generator_value["tickets"] = [generator_value["tickets"][0]]
        generator_value["tickets"][0]["write_paths"] = ["Scripts/Generate_Contracts.py"]
        generator_plan = build(generator_value)
        self.assertEqual(1, generator_plan["tickets"][0]["generator_order_slot"])

    def test_owner_window_deadline_takes_priority_when_schedule_is_feasible(self):
        value = fixture()
        long_ticket = value["tickets"][2]
        long_ticket["duration_minutes"] = 10
        windowed = value["tickets"][3]
        windowed["duration_minutes"] = 2
        windowed["owner_review_window"] = {"opens_at_minute": 0, "closes_at_minute": 5}
        value["tickets"] = [long_ticket, windowed]
        value["lane_cap"] = 1
        value["start_gap_minutes"] = 0
        plan = build(value)
        starts = {ticket["key"]: ticket["earliest_start_minute"] for ticket in plan["tickets"]}
        self.assertEqual({"AWF-200": 2, "AWF-210": 0}, starts)

    def test_hand_computed_critical_path_cap_gap_and_owner_windows(self):
        value = fixture()
        plan = build(value)
        self.assertEqual(value["expected"]["critical_path"], plan["critical_path"])
        self.assertEqual(value["expected"]["duration_minutes"], plan["duration_minutes"])
        self.assertEqual(
            value["expected"]["earliest_starts"],
            {item["key"]: item["earliest_start_minute"] for item in plan["tickets"]},
        )
        starts = sorted(item["earliest_start_minute"] for item in plan["tickets"])
        self.assertTrue(all(
            later - earlier >= value["start_gap_minutes"]
            for earlier, later in zip(starts, starts[1:])
        ))
        tickets = {item["key"]: item for item in plan["tickets"]}
        self.assertEqual(3, tickets["AWF-115"]["round_cap"])
        self.assertEqual(1, tickets["AWF-115"]["critical_path_position"])
        self.assertEqual(2, tickets["AWF-36"]["generator_order_slot"])
        self.assertIsNone(tickets["AWF-200"]["generator_order_slot"])
        self.assertEqual(10, tickets["AWF-210"]["earliest_start_minute"])


class LaneFillAcceptanceTests(unittest.TestCase):
    def test_freeze_only_blockers_prestage_and_other_blockers_do_not(self):
        module = implementation()
        for blockers, allowed in (
            (["MERGE_FREEZE"], True),
            (["MERGE_FREEZE", "RESYNC_PENDING"], True),
            (["MERGE_FREEZE", "HOLD"], False),
            ([], False),
        ):
            with self.subTest(blockers=blockers):
                value = fixture()
                value["tickets"][0].update(
                    blockers=blockers,
                    in_lane=False,
                    status="waiting",
                    idle_lane_reason=blockers[0] if blockers else None,
                )
                plan = build(value)
                ticket = next(item for item in plan["tickets"] if item["key"] == "AWF-115")
                self.assertEqual(allowed, ticket["prestage_allowed"])
                if allowed:
                    self.assertTrue(ticket["in_lane"])
                    self.assertEqual("prestaged-hold", ticket["status"])

        value = fixture()
        value["tickets"][0].update(
            blockers=["MERGE_FREEZE"],
            idle_lane_reason="MERGE_FREEZE",
            in_lane=False,
            status="waiting",
        )
        prestaged = build(value)
        schema = json.loads(
            (ROOT / ".agentic/schemas/ticket-plan.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(prestaged)))
        prestaged["tickets"][0]["prestage_allowed"] = False
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(prestaged)))

        value = fixture()
        value["tickets"][0]["blockers"] = ["UNREVIEWED_GATE"]
        with self.assertRaises(module.PlanningGraphError) as caught:
            build(value)
        self.assertEqual("UNKNOWN_IDLE_LANE_REASON", caught.exception.reason)

    def test_refill_rank_is_deterministic_and_reacts_to_gate_changes(self):
        module = implementation()
        value = fixture()
        for ticket in value["tickets"]:
            ticket["in_lane"] = False
        value["tickets"][0]["blockers"] = ["HOLD"]
        value["tickets"][0]["idle_lane_reason"] = "HOLD"
        plan = build(value)
        options = {
            "owner_lanes": [ticket["owner_lane"] for ticket in value["tickets"]],
            "merged_tickets": [],
            "live_write_paths": [],
            "generator_busy": False,
            "hold": False,
            "start_lock_free": True,
        }
        first = module.refill_rank(plan, **options)
        second = module.refill_rank(plan, **options)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual("AWF-200", first["selected_ticket"])

        value["tickets"][0]["blockers"] = []
        value["tickets"][0]["idle_lane_reason"] = None
        cleared = module.refill_rank(build(value), **options)
        self.assertEqual("AWF-115", cleared["selected_ticket"])

        locked = module.refill_rank(build(value), **{**options, "start_lock_free": False})
        self.assertIsNone(locked["selected_ticket"])
        self.assertEqual("START_GAP_PENDING", locked["idle_lane_reason"])

        held = module.refill_rank(build(value), **{**options, "hold": True})
        self.assertIsNone(held["selected_ticket"])
        self.assertEqual("HOLD", held["idle_lane_reason"])

        generator_busy = module.refill_rank(build(value), **{**options, "generator_busy": True})
        self.assertEqual("AWF-200", generator_busy["selected_ticket"])

        wrong_owner = module.refill_rank(
            build(value),
            **{**options, "owner_lanes": ["docs", "docs-two", "controller"]},
        )
        self.assertEqual("AWF-200", wrong_owner["selected_ticket"])

        footprint_busy = module.refill_rank(
            build(value),
            **{**options, "live_write_paths": ["DOCS/INDEPENDENT.MD"]},
        )
        awf200 = next(item for item in footprint_busy["blocked_tickets"] if item["key"] == "AWF-200")
        self.assertIn("FILE_CLASH", awf200["reasons"])

        value["tickets"][1]["blockers"] = []
        value["tickets"][1]["idle_lane_reason"] = None
        successor = module.refill_rank(
            build(value),
            **{**options, "merged_tickets": ["AWF-115"]},
        )
        self.assertEqual("AWF-36", successor["selected_ticket"])

    def test_idle_lane_status_uses_injected_clock_and_lists_gates(self):
        value = fixture()
        for ticket in value["tickets"]:
            ticket["in_lane"] = False
        value["tickets"][3].update(
            owner_lane="framework-contracts",
            blockers=["HOLD", "CAP_REACHED"],
            idle_lane_reason="HOLD",
        )
        plan = build(
            value,
            observed_minute=11,
            lane_observations=[
                {"owner_lane": "docs", "idle_since_minute": 0},
                {"owner_lane": "framework-contracts", "idle_since_minute": 0},
            ],
        )
        statuses = {item["owner_lane"]: item for item in plan["lane_status"]}
        schema = json.loads(
            (ROOT / ".agentic/schemas/ticket-plan.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(plan)))
        self.assertEqual(plan, implementation().validate_ticket_plan(
            json.loads(implementation().serialize_ticket_plan(plan))
        ))
        self.assertEqual("FAIL", statuses["docs"]["status_verdict"])
        self.assertEqual(["AWF-200"], statuses["docs"]["ready_tickets"])
        self.assertEqual("OK", statuses["framework-contracts"]["status_verdict"])
        self.assertEqual(
            ["CAP_REACHED", "HOLD", "PREDECESSOR_UNMERGED"],
            statuses["framework-contracts"]["idle_lane_reasons"],
        )

        module = implementation()
        for field, invalid in (
            ("status_verdict", "UNKNOWN"),
            ("idle_lane_reasons", ["UNKNOWN"]),
            ("ready_tickets", [{}]),
        ):
            with self.subTest(field=field):
                malformed = deepcopy(plan)
                malformed["lane_status"][0][field] = invalid
                with self.assertRaises(module.PlanningGraphError):
                    module.validate_ticket_plan(malformed)

        missing = deepcopy(plan)
        del missing["lane_status"][0]["status_verdict"]
        with self.assertRaises(module.PlanningGraphError) as caught:
            module.validate_ticket_plan(missing)
        self.assertEqual("MISSING_REQUIRED_FIELD", caught.exception.reason)

    def test_output_is_byte_deterministic_and_mermaid_is_stable(self):
        module = implementation()
        first = build()
        value = fixture()
        value["tickets"].reverse()
        second = build(value)
        self.assertEqual(module.serialize_ticket_plan(first), module.serialize_ticket_plan(second))
        diagram = module.render_mermaid(first)
        self.assertEqual(diagram, first["mermaid"])
        self.assertTrue(diagram.startswith("flowchart LR\n"))
        self.assertIn('AWF_115["AWF-115', diagram)
        self.assertIn("AWF_115 -->|dependency| AWF_36", diagram)


if __name__ == "__main__":
    unittest.main()
