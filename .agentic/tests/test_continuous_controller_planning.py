"""AWF-50 part-b controller/planner and overlap-allowance regressions."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load
from agentic.continuous_controller import (
    ContinuousControllerStore,
    _controller_planning_tickets,
    create_overlap_allowance,
    dispatch_role_contract,
    production_controller_cycle,
    recompute_controller_plan,
)
from agentic.canonical import fingerprint
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


def controller_ticket(key, path, priority=1, **extra):
    value = {
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
        "jira_status": "In Progress", "code_needed": True,
    }
    value.update(extra)
    return value


def inventory_observation(now, tickets):
    binding = {"project_id": "project-1", "repository_id": "101",
               "scope_sha256": "a" * 64}
    return {"source": "host_observation", "observed_at": now,
            "binding": binding, "complete": True,
            "inventory_sha256": fingerprint("controller-inventory", {
                "binding": binding, "tickets": tickets}), "tickets": tickets}


def dispatch_receipt(payload, observed_at):
    keys = ["dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce",
            "prepared_at", "begun_at"]
    return {key: payload[key] for key in keys} | {
        "status": "ACCEPTED", "observed_at": observed_at}


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
        self.assertEqual(6, count.get("maximum"))

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

    def test_scheduler_admits_only_one_startable_ticket_from_each_selected_lane(self):
        planning_tickets = [
            planning_ticket("AWF-A", "src/left.py"),
            planning_ticket("AWF-B", "src/left.py", write_paths=[
                "src/left.py", "src/right.py",
            ]),
            planning_ticket("AWF-C", "src/right.py"),
            planning_ticket("AWF-D", "src/independent.py"),
        ]
        temporary = tempfile.TemporaryDirectory(prefix="awf-controller-lane-identity-")
        self.addCleanup(temporary.cleanup)
        store = ContinuousControllerStore(Path(temporary.name) / "state.sqlite3", ["A", "B"])
        record = store.recompute_lane_plan(
            planning_tickets, config(2), now=NOW, trigger="project_start",
        )["record"]
        self.assertEqual(
            [["AWF-A", "AWF-B", "AWF-C"], ["AWF-D"]],
            [lane["ticket_keys"] for lane in record["lanes"]],
        )

        rows = store.schedule([
            controller_ticket("AWF-A", "src/left.py", 1),
            controller_ticket("AWF-B", "src/left.py", 2,
                              paths=["src/left.py", "src/right.py"]),
            controller_ticket("AWF-C", "src/right.py", 3),
            controller_ticket("AWF-D", "src/independent.py", 4),
        ], NOW, host_capacity=2, planning_record=record)
        self.assertEqual(
            {"AWF-A", "AWF-D"},
            {row["ticket"] for row in rows if row["state"] == "WORKING"},
        )

        second = ContinuousControllerStore(
            Path(temporary.name) / "next-startable.sqlite3", ["A", "B"])
        second_record = second.recompute_lane_plan(
            planning_tickets, config(2), now=NOW, trigger="project_start",
        )["record"]
        rows = second.schedule([
            controller_ticket("AWF-A", "src/left.py", 1,
                              dependencies_satisfied=False),
            controller_ticket("AWF-B", "src/left.py", 2,
                              paths=["src/left.py", "src/right.py"]),
            controller_ticket("AWF-C", "src/right.py", 3),
            controller_ticket("AWF-D", "src/independent.py", 4),
        ], NOW, host_capacity=2, planning_record=second_record)
        self.assertEqual(
            {"AWF-B", "AWF-D"},
            {row["ticket"] for row in rows if row["state"] == "WORKING"},
        )


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
                [controller_ticket("AWF-1", "src/shared.py", 1,
                                   open_pr={"number": 11, "files": ["src/shared.py"]}),
                 controller_ticket("AWF-2", "src/shared.py", 2,
                                   open_pr={"number": 12, "files": ["src/shared.py"]})],
                NOW, host_capacity=2,
                planning_record=stored, observe_pr=lambda number: {
                    **observe_open_pr(number), "repository_id": 101},
                expected_repository_id="101")
        self.assertEqual(2, sum(row["state"] == "WORKING" for row in rows))

    def test_lane_start_reobserves_exact_open_pr_pair_and_fails_closed(self):
        tickets = [planning_ticket("AWF-1", "src/shared.py", 11),
                   planning_ticket("AWF-2", "src/shared.py", 12)]

        def scheduled(observer, second_pr=12):
            temporary = tempfile.TemporaryDirectory(prefix="awf-controller-pr-start-")
            self.addCleanup(temporary.cleanup)
            store = ContinuousControllerStore(Path(temporary.name) / "state.sqlite3", ["A", "B"])
            record = store.recompute_lane_plan(
                tickets, config(4), now=NOW, trigger="project_start",
                overlap_allowances=[self.allowance()], observe_pr=observe_open_pr,
                verify_owner_source=verify_owner)["record"]
            return store.schedule(
                [controller_ticket("AWF-1", "src/shared.py", 1,
                                   open_pr={"number": 11, "files": ["src/shared.py"]}),
                 controller_ticket("AWF-2", "src/shared.py", 2,
                                   open_pr={"number": second_pr, "files": ["src/shared.py"]})],
                NOW, host_capacity=2, planning_record=record,
                observe_pr=observer, expected_repository_id="101")

        def current(number):
            return {"number": number, "repository_id": 101, "state": "OPEN",
                    "merged": False, "observed_at": NOW}

        self.assertEqual(2, sum(row["state"] == "WORKING"
                                for row in scheduled(current)))
        self.assertEqual(1, sum(row["state"] == "WORKING"
                                for row in scheduled(current, second_pr=13)))
        for name, observer in {
                "merged": lambda number: {
                    **current(number), "state": "MERGED" if number == 11 else "OPEN",
                    "merged": number == 11},
                "unreadable": lambda number: (_ for _ in ()).throw(RuntimeError("unreadable")),
                "wrong_number": lambda number: {**current(number), "number": number + 1},
                "wrong_repository": lambda number: {**current(number), "repository_id": 202},
                "stale": lambda number: {
                    **current(number), "observed_at": "2026-10-13T01:59:59Z"},
        }.items():
            with self.subTest(name=name):
                self.assertEqual(1, sum(row["state"] == "WORKING"
                                        for row in scheduled(observer)))

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

    def test_production_cycle_preserves_authenticated_planner_inputs(self):
        cfg = config(4)
        cfg["github"]["repository_id"] = 101
        tickets = [
            controller_ticket(
                "AWF-1", "src/generator.py", 1,
                write_paths=["src/generator.py"],
                open_pr={"number": 11, "files": ["src/pr.py"]},
                open_pr_files=["src/pr-extra.py"],
                worktree_diff=["src/worktree.py"],
                generated_outputs=["src/generated.py"],
                blocks=["AWF-2"], expected_rounds=2, round_time_minutes=45),
            controller_ticket(
                "AWF-2", "src/consumer.py", 2,
                write_paths=["src/consumer.py"], declared_dependencies=["AWF-1"]),
        ]
        projected = _controller_planning_tickets(tickets, set())
        self.assertEqual({"src/generated.py"}, set(projected[0]["generated_outputs"]))
        self.assertEqual(11, projected[0]["open_pr"]["number"])
        self.assertEqual(["AWF-1"], projected[1]["declared_dependencies"])

        temporary = tempfile.TemporaryDirectory(prefix="awf-controller-production-planning-")
        self.addCleanup(temporary.cleanup)
        store = ContinuousControllerStore(Path(temporary.name) / "state.sqlite3", ["A", "B"])
        binding = {"repository_root": "fixture", "head_sha": "a" * 40,
                   "tree_sha": "b" * 40, "git_path_count": 4}
        with patch("agentic.continuous_controller.canonical_repository_paths",
                   return_value=(tickets, binding)), \
                patch("agentic.publication_readiness.require_publication_readiness",
                      return_value={"status": "READY"}), \
                patch("agentic.codex_skill.observe_codex_awf_skills",
                      return_value={"status": "PASS", "problems": []}):
            result = production_controller_cycle(
                store, now=NOW, host_capacity=2,
                inventory_binding={"project_id": "project-1", "repository_id": "101",
                                   "scope_sha256": "a" * 64},
                repository_root=ROOT, repository_head_sha="a" * 40,
                repository_tree_sha="b" * 40,
                observe_inventory=lambda: inventory_observation(NOW, tickets),
                dispatch_ticket=lambda payload: dispatch_receipt(payload, NOW),
                observe_dispatch=lambda payload: self.fail("no reconciliation expected"),
                deliver_status=lambda digest: {
                    "delivery_id": digest["delivery_id"], "status": "DELIVERED",
                    "observed_at": NOW},
                publication_config=cfg, observe_publication=lambda item: {},
                planning_trigger="files_changed",
                observe_overlap_pr=lambda number: self.fail("no allowance expected"))
        record = result["planning"]["record"]
        self.assertEqual("files_changed", record["trigger"])
        self.assertEqual("AWF-1", record["inputs"]["dependencies"][0]["blocker"])
        self.assertEqual("AWF-1", record["inputs"]["generator_owners"]["src/generated.py"])
        self.assertIn("src/pr.py", record["inputs"]["tickets"]["AWF-1"]["path_sources"])

    def test_production_writer_requires_per_ticket_code_needed_observation(self):
        ticket = controller_ticket("AWF-1", "src/one.py", code_needed=False)
        temporary = tempfile.TemporaryDirectory(prefix="awf-controller-code-needed-")
        self.addCleanup(temporary.cleanup)
        store = ContinuousControllerStore(Path(temporary.name) / "state.sqlite3", ["A"])
        binding = {"repository_root": "fixture", "head_sha": "a" * 40,
                   "tree_sha": "b" * 40, "git_path_count": 1}
        with patch("agentic.continuous_controller.canonical_repository_paths",
                   return_value=([ticket], binding)):
            with self.assertRaisesRegex(ValidationError, "code-needed"):
                production_controller_cycle(
                    store, now=NOW, host_capacity=1,
                    inventory_binding={"project_id": "project-1", "repository_id": "101",
                                       "scope_sha256": "a" * 64},
                    repository_root=ROOT, repository_head_sha="a" * 40,
                    repository_tree_sha="b" * 40,
                    observe_inventory=lambda: inventory_observation(NOW, [ticket]),
                    dispatch_ticket=lambda payload: self.fail("writer must not launch"),
                    observe_dispatch=lambda payload: self.fail("no reconciliation"),
                    deliver_status=lambda digest: self.fail("no delivery"),
                    dispatch_role="writer", planning_config=config(4),
                    planning_trigger="project_start")

        omitted = controller_ticket("AWF-2", "src/two.py")
        del omitted["code_needed"]
        second_store = ContinuousControllerStore(
            Path(temporary.name) / "missing.sqlite3", ["A"])
        with patch("agentic.continuous_controller.canonical_repository_paths",
                   return_value=([omitted], binding)):
            with self.assertRaisesRegex(ValidationError, "code-needed"):
                production_controller_cycle(
                    second_store, now=NOW, host_capacity=1,
                    inventory_binding={"project_id": "project-1", "repository_id": "101",
                                       "scope_sha256": "a" * 64},
                    repository_root=ROOT, repository_head_sha="a" * 40,
                    repository_tree_sha="b" * 40,
                    observe_inventory=lambda: inventory_observation(NOW, [omitted]),
                    dispatch_ticket=lambda payload: self.fail("writer must not launch"),
                    observe_dispatch=lambda payload: self.fail("no reconciliation"),
                    deliver_status=lambda digest: self.fail("no delivery"),
                    dispatch_role="writer", planning_config=config(4),
                    planning_trigger="project_start")

    def test_production_cycle_applies_authenticated_owner_override(self):
        cfg = config(4)
        cfg["github"]["repository_id"] = 101
        ticket = controller_ticket("AWF-1", "src/one.py", write_paths=["src/one.py"])
        reason = "owner capacity decision"
        raw_body = planning.owner_override_command(None, "fixed", 1, reason)
        owner_source = {
            "channel": "github_pr_comment", "comment_id": 77,
            "actor_id": 101, "actor_login": "owner",
            "created_at": NOW, "updated_at": NOW,
            "binding": {"current_override_id": None}, "raw_body": raw_body,
            "raw_body_sha256": planning.sha256(raw_body.encode("utf-8")),
            "source_id": "override-comment-77",
        }

        def verify_override(source, expected):
            return {"status": "VERIFIED", "actor_id": source["actor_id"],
                    "binding": expected["binding"],
                    "raw_body_sha256": expected["raw_body_sha256"],
                    "source_id": source["source_id"], "observed_at": NOW}

        temporary = tempfile.TemporaryDirectory(prefix="awf-controller-production-override-")
        self.addCleanup(temporary.cleanup)
        store = ContinuousControllerStore(Path(temporary.name) / "state.sqlite3", ["A"])
        binding = {"repository_root": "fixture", "head_sha": "a" * 40,
                   "tree_sha": "b" * 40, "git_path_count": 1}
        with patch("agentic.continuous_controller.canonical_repository_paths",
                   return_value=([ticket], binding)), \
                patch("agentic.publication_readiness.require_publication_readiness",
                      return_value={"status": "READY"}):
            result = production_controller_cycle(
                store, now=NOW, host_capacity=0,
                inventory_binding={"project_id": "project-1", "repository_id": "101",
                                   "scope_sha256": "a" * 64},
                repository_root=ROOT, repository_head_sha="a" * 40,
                repository_tree_sha="b" * 40,
                observe_inventory=lambda: inventory_observation(NOW, [ticket]),
                dispatch_ticket=lambda payload: self.fail("zero capacity must not dispatch"),
                observe_dispatch=lambda payload: self.fail("no reconciliation"),
                deliver_status=lambda digest: {
                    "delivery_id": digest["delivery_id"], "status": "DELIVERED",
                    "observed_at": NOW},
                publication_config=cfg, observe_publication=lambda item: {},
                planning_trigger="override_changed",
                planning_override={"mode": "fixed", "value": 1, "reason": reason,
                                   "owner_source": owner_source},
                verify_owner_source=verify_override)
        self.assertEqual("owner_override", result["planning"]["record"]["owner_decision"]["kind"])
        self.assertEqual(1, result["planning"]["record"]["lane_count"])
        self.assertEqual("fixed", result["planning"]["owner_override"]["mode"])

    def test_production_cli_exposes_trigger_override_and_allowance_inputs(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / ".agentic/scripts/continuous_controller.py"),
             "cycle", "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--planning-trigger", completed.stdout)
        self.assertIn("--lane-override", completed.stdout)
        self.assertIn("--overlap-allowances", completed.stdout)


if __name__ == "__main__":
    unittest.main()
