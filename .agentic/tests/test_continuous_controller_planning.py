"""AWF-50 part-b controller/planner and overlap-allowance regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load
from agentic.continuous_controller import (
    ContinuousControllerStore,
    create_overlap_allowance,
    dispatch_role_contract,
    recompute_controller_plan,
)
from agentic import operating
from agentic import planning


NOW = "2026-10-13T02:00:00Z"
COUNTS = {"required": 0, "completed": 0, "acceptable": 0,
          "failed": 0, "stale": 0, "outstanding": 0}


def config(max_lanes=6):
    value = load(ROOT / ".agentic/PROJECT_CONFIG.yaml")
    value["execution"]["max_parallel_tickets"] = 12
    value["execution"]["max_lanes"] = max_lanes
    value["execution"]["host_broker"] = {"enabled": False, "max_workers": 12}
    value["merge_gate"]["trusted_owner_ids"] = [101]
    value["github"]["expected_actor_id"] = 101
    return value


def planning_ticket(key, path, pr=None, **extra):
    value = {"key": key, "status": "open"}
    if pr is None:
        value["write_paths"] = [path]
    else:
        value["open_pr"] = {"number": pr, "files": [path]}
    value.update(extra)
    return value


def controller_ticket(key, path, priority=1):
    return {
        "ticket": key, "priority": priority, "disposition": "ELIGIBLE",
        "actor": "worker-" + key, "reason": "eligible scoped action",
        "next_action": "implement " + key,
        "resume_trigger": "named prerequisite becomes current",
        "paths": [path], "dependencies_satisfied": True,
        "budget_available": True, "cap_available": True,
        "review_independent": True,
        "exact_tuple": "base:a/head:b/tree:c/contract:d/review:e",
        "activity": "implement " + key, "verification_gate": "PENDING",
        "reviewer_completion": dict(COUNTS), "open_findings": 0,
        "jira_status": "In Progress",
    }


def verify_owner(source, expected):
    return {
        "status": "VERIFIED", "actor_id": source["actor_id"],
        "source_id": source["source_id"], "observed_at": NOW,
        "request_sha256": expected["request_sha256"],
    }


def observe_open_pr(number):
    return {"number": number, "state": "OPEN", "merged": False,
            "observed_at": NOW}


class ConfiguredCeilingTests(unittest.TestCase):
    def test_schema_declares_governed_dynamic_bound_and_runtime_accepts_above_six(self):
        schema = json.loads((ROOT / ".agentic/schemas/operating-config.schema.json").read_text(encoding="utf-8"))
        count = schema["properties"]["streams"]["properties"]["count"]
        self.assertEqual("$.execution.max_lanes", count["x-awf-governed-maximum"]["path"])
        self.assertEqual(6, count["x-awf-governed-maximum"]["default"])
        self.assertNotEqual(6, count.get("maximum"))

        cfg = config(8)
        value = operating.default_operating()
        value["streams"]["count"] = 8
        for label in operating.stream_labels(8):
            value["streams"].setdefault(label, operating._stream())
        self.assertEqual(8, operating.validate_operating(value, cfg).count)

    def test_missing_ceiling_defaults_to_six_and_invalid_values_fail_closed(self):
        cfg = config()
        del cfg["execution"]["max_lanes"]
        self.assertEqual(6, operating.operating_ceiling(cfg)["effective_ceiling"])
        for value in (0, -1, True, "8", None):
            malformed = config()
            malformed["execution"]["max_lanes"] = value
            with self.subTest(value=value), self.assertRaises(operating.OperatingError):
                operating.operating_ceiling(malformed)


class ControllerPlanningTests(unittest.TestCase):
    def test_controller_honours_ceiling_and_authenticated_override(self):
        tickets = [planning_ticket(f"AWF-{number}", f"src/{number}.py")
                   for number in range(1, 6)]
        cfg = config(3)
        state = {}
        result = recompute_controller_plan(
            tickets, cfg, state, now=NOW, trigger="project_start")
        self.assertEqual(3, result["record"]["lane_count"])

        source = planning.owner_override_command(None, "fixed", 2, "owner capacity")
        planning.change_owner_override(
            cfg, state, mode="fixed", value=2, reason="owner capacity", now=NOW,
            owner_source={
                "channel": "github_pr_comment", "comment_id": 70,
                "actor_id": 101, "actor_login": "owner",
                "created_at": NOW, "updated_at": NOW,
                "binding": {"current_override_id": None}, "raw_body": source,
                "raw_body_sha256": planning.sha256(source.encode("utf-8")),
                "source_id": "override-comment-70",
            },
            verify_owner_source=lambda owner_source, expected: {
                "status": "VERIFIED", "actor_id": 101,
                "binding": expected["binding"],
                "raw_body_sha256": expected["raw_body_sha256"],
                "source_id": "override-comment-70", "observed_at": NOW,
            })
        changed = recompute_controller_plan(
            tickets, cfg, state, now="2026-10-13T02:00:01Z",
            trigger="override_changed", verify_owner_source=lambda owner_source, expected: {
                "status": "VERIFIED", "actor_id": 101,
                "binding": expected["binding"],
                "raw_body_sha256": expected["raw_body_sha256"],
                "source_id": "override-comment-70", "observed_at": NOW,
            })
        self.assertEqual(2, changed["record"]["lane_count"])
        self.assertEqual("owner_override", changed["record"]["owner_decision"]["kind"])

    def test_mid_round_recompute_is_deferred_without_reassignment(self):
        cfg, state = config(4), {}
        first = recompute_controller_plan(
            [planning_ticket("AWF-1", "src/one.py")], cfg, state,
            now=NOW, trigger="project_start")
        changed = recompute_controller_plan(
            [planning_ticket("AWF-1", "src/changed.py"),
             planning_ticket("AWF-2", "src/two.py")], cfg, state,
            now="2026-10-13T02:00:01Z", trigger="files_changed",
            round_active=True)
        self.assertEqual("DEFERRED_UNTIL_ROUND_END", changed["status"])
        self.assertEqual(first["record"], state["current_plan"])
        self.assertEqual(1, len(state["planning_history"]))


class ScopedOverlapAllowanceTests(unittest.TestCase):
    def allowance(self, path="src/shared.py", prs=(11, 12)):
        return create_overlap_allowance(
            config(4), path=path, pull_requests=list(prs),
            owner_source={"actor_id": 101, "actor_name": "Owner",
                          "source_id": "comment-91", "observed_at": NOW},
            approved_at=NOW, reason="bounded PR synchronization",
            verify_owner_source=verify_owner)

    def test_exact_path_and_pr_pair_widens_plan_and_lane_start_only(self):
        tickets = [planning_ticket("AWF-1", "src/shared.py", 11),
                   planning_ticket("AWF-2", "src/shared.py", 12)]
        state = {}
        result = recompute_controller_plan(
            tickets, config(4), state, now=NOW, trigger="project_start",
            overlap_allowances=[self.allowance()], observe_pr=observe_open_pr,
            verify_owner_source=verify_owner)
        record = result["record"]
        self.assertEqual(2, record["estimate"]["independent_lane_count"])
        self.assertEqual(1, len(record["applied_overlap_allowances"]))

        with tempfile.TemporaryDirectory(prefix="awf-controller-planning-") as temporary:
            store = ContinuousControllerStore(Path(temporary) / "state.sqlite3", ["A", "B"])
            stored = store.recompute_lane_plan(
                tickets, config(4), now=NOW, trigger="project_start",
                overlap_allowances=[self.allowance()], observe_pr=observe_open_pr,
                verify_owner_source=verify_owner)["record"]
            rows = store.schedule(
                [controller_ticket("AWF-1", "src/shared.py", 1),
                 controller_ticket("AWF-2", "src/shared.py", 2)],
                NOW, host_capacity=2,
                planning_record=stored)
        self.assertEqual(2, sum(row["state"] == "WORKING" for row in rows))

    def test_unlisted_path_malformed_allowance_and_unreadable_pr_fail_closed(self):
        tickets = [
            planning_ticket("AWF-1", "src/shared.py", 11,
                            open_pr_files=["src/other.py"]),
            planning_ticket("AWF-2", "src/shared.py", 12,
                            open_pr_files=["src/other.py"]),
        ]
        exact_only = recompute_controller_plan(
            tickets, config(4), {}, now=NOW, trigger="project_start",
            overlap_allowances=[self.allowance()], observe_pr=observe_open_pr,
            verify_owner_source=verify_owner)
        self.assertEqual(1, exact_only["record"]["estimate"]["independent_lane_count"])
        self.assertEqual(["src/other.py"], exact_only["record"]["effective_overlaps"][0]["paths"])

        malformed = recompute_controller_plan(
            tickets[:1] + [planning_ticket("AWF-2", "src/shared.py", 12)],
            config(4), {}, now=NOW, trigger="project_start",
            overlap_allowances=[{"path": "src/shared.py", "pull_requests": [11, 12]}],
            observe_pr=observe_open_pr, verify_owner_source=verify_owner)
        self.assertEqual(1, malformed["record"]["estimate"]["independent_lane_count"])
        self.assertEqual("INVALID", malformed["record"]["overlap_allowance_audit"][0]["status"])

        unreadable = recompute_controller_plan(
            [planning_ticket("AWF-1", "src/shared.py", 11),
             planning_ticket("AWF-2", "src/shared.py", 12)],
            config(4), {}, now=NOW, trigger="project_start",
            overlap_allowances=[self.allowance()],
            observe_pr=lambda number: (_ for _ in ()).throw(RuntimeError("unreadable")),
            verify_owner_source=verify_owner)
        self.assertEqual(1, unreadable["record"]["estimate"]["independent_lane_count"])
        self.assertEqual("UNOBSERVED", unreadable["record"]["overlap_allowance_audit"][0]["status"])

        expired = recompute_controller_plan(
            [planning_ticket("AWF-1", "src/shared.py", 11),
             planning_ticket("AWF-2", "src/shared.py", 12)],
            config(4), {}, now=NOW, trigger="project_start",
            overlap_allowances=[self.allowance()],
            observe_pr=lambda number: {
                "number": number, "state": "MERGED" if number == 11 else "OPEN",
                "merged": number == 11, "observed_at": NOW,
            }, verify_owner_source=verify_owner)
        self.assertEqual(1, expired["record"]["estimate"]["independent_lane_count"])
        self.assertEqual("EXPIRED", expired["record"]["overlap_allowance_audit"][0]["status"])


class RoleWiringTests(unittest.TestCase):
    def test_writer_requires_code_and_critic_is_fresh_distinct_read_only(self):
        with self.assertRaisesRegex(ValidationError, "code"):
            dispatch_role_contract("writer", code_needed=False)
        writer = dispatch_role_contract("writer", code_needed=True)
        critic = dispatch_role_contract(
            "critic", code_needed=False, writer_session_id="writer-session-1")
        self.assertTrue(writer["fresh_session_required"])
        self.assertEqual("workspace_write", writer["access"])
        self.assertTrue(critic["fresh_session_required"])
        self.assertEqual("read_only", critic["access"])
        self.assertEqual("writer-session-1", critic["must_differ_from_session"])
        self.assertTrue(critic["independent_review_required"])


if __name__ == "__main__":
    unittest.main()
