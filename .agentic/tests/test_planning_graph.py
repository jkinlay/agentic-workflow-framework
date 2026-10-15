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
    "phase", "status", "in_lane",
)


def implementation():
    return importlib.import_module("agentic.planning_graph")


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build(value=None):
    value = value or fixture()
    return implementation().build_ticket_plan(
        value["tickets"],
        lane_cap=value["lane_cap"],
        start_gap_minutes=value["start_gap_minutes"],
        tier3_round_cap=value["tier3_round_cap"],
        generator_order=value["generator_order"],
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
                    if field == "idle_lane_reason"
                    else "INVALID_FIELD"
                )
                self.assertEqual(expected, caught.exception.reason)


class PlanningGraphValidationTests(unittest.TestCase):
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
        self.assertEqual(starts["AWF-115"], starts["AWF-200"])

    def test_hand_computed_critical_path_cap_gap_and_owner_windows(self):
        value = fixture()
        plan = build(value)
        self.assertEqual(value["expected"]["critical_path"], plan["critical_path"])
        self.assertEqual(value["expected"]["duration_minutes"], plan["duration_minutes"])
        self.assertEqual(
            value["expected"]["earliest_starts"],
            {item["key"]: item["earliest_start_minute"] for item in plan["tickets"]},
        )
        tickets = {item["key"]: item for item in plan["tickets"]}
        self.assertEqual(3, tickets["AWF-115"]["round_cap"])
        self.assertEqual(1, tickets["AWF-115"]["critical_path_position"])
        self.assertEqual(2, tickets["AWF-36"]["generator_order_slot"])
        self.assertIsNone(tickets["AWF-200"]["generator_order_slot"])
        self.assertEqual(8, tickets["AWF-210"]["earliest_start_minute"])

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
