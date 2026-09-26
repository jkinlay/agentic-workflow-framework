"""Synthetic operating/routing/planning contracts; no models or native dispatch."""
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
import itertools
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
import uuid

from agentic import ValidationError
from agentic import operating as op
from agentic.canonical import canonical, load, sha256
from agentic.model_routing import MODELS, RoutingLedger, default_policy, policy_from_config, select_route
from agentic.operating import default_operating, validate_operating
from agentic.store import Store
from agentic.streams import DEFAULT_NATIVE_EXECUTION, _antichain, plan_inventory, recommendation_groups

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-11T10:00:00Z"


def governance():
    policy = default_policy()
    return {"project": {"id": "synthetic-operating-project"}, "execution": {
        **deepcopy(DEFAULT_NATIVE_EXECUTION), "model_routing": policy,
        "roles": {role: {**pair, "approved_model_ids": list(MODELS)} for role, pair in policy["role_defaults"].items()}}}


def configuration(count=3):
    result = default_operating()
    result["streams"]["count"] = count
    for label in "ABCDEF"[:count]:
        result["streams"].setdefault(label, deepcopy(result["streams"]["A"]))
    return result


def request(**changes):
    value = {"ticket_id": "DEMO-11", "agent_id": "synthetic-A", "context_id": "synthetic-context",
             "stream": "A", "role": "worker", "phase": "implementation", "task_class": "synthetic",
             "complexity": "medium", "risk": "low", "uncertainty": "low", "verification": "strong",
             "risk_flags": [], "reservation_tokens": 1000}
    return {**value, **changes}


def capabilities():
    return {"models": {model: data["reasoning_efforts"] for model, data in default_policy()["models"].items()}}


def outcome(run, **changes):
    return {"actual_model": run["model"], "actual_reasoning_effort": run["reasoning_effort"],
            "actual_context_id": "synthetic-context", "actual_tokens": 100, "actual_cost_microusd": None,
            "success": True, "validation_passed": True, "independent_review_passed": True,
            "reviewer_context_id": "synthetic-independent", "escaped_defect": False, **changes}


def inventory(count=6):
    value = load(ROOT / ".agentic/examples/stream-input.json")
    seed = value["tickets"][0]
    value["tickets"] = [{**deepcopy(seed), "id": f"DEMO-{index + 11}", "epic_id": None,
                         "dependencies": [], "write_paths": [f"src/part-{index}"]} for index in range(count)]
    value["epics"] = []
    return value


class OperatingRoutingTests(unittest.TestCase):
    def setUp(self):
        self.governance = governance()
        self.value = configuration()

    def route(self, **changes):
        return select_route(policy_from_config(self.governance), request(**changes), capabilities(),
                            operating=validate_operating(self.value, self.governance), governance=self.governance)

    def test_operating_role_stream_and_simple_routes(self):
        self.assertEqual(self.route()["model"], MODELS[1])
        simple = self.route(complexity="low")
        self.assertEqual(simple["model"], MODELS[0])
        self.assertIn("unpinned ordinary stream default", " ".join(simple["reasons"]))
        self.value["streams"]["A"]["worker"] = {"model": MODELS[2], "reasoning_effort": "high", "pinned": True}
        self.assertEqual(self.route(complexity="low")["model"], MODELS[2])
        self.value["simple_worker"]["enabled"] = False
        self.assertEqual(self.route(complexity="low")["model"], MODELS[2])
        self.value["controller"] = {"model": MODELS[3], "reasoning_effort": "high"}
        self.assertEqual(self.route(role="controller")["model"], MODELS[3])

    def test_unpinned_default_metadata_and_explicit_epic_inheritance(self):
        self.value["streams"]["A"]["worker"]["pinned"] = False
        self.assertEqual(self.route(complexity="low")["model"], MODELS[0])
        # A recorded Epic route remains an explicit scoped override even when
        # its model/effort equals the global default; recommendation cannot
        # invent or remove that scope-specific direction.
        self.value["epic_overrides"] = {"DEMO-1": {"streams": {"A": {"worker": {
            **self.value["streams"]["A"]["worker"]}}}}}
        scoped = self.route(complexity="low", epic_id="DEMO-1", epic_risk_flags=[])
        self.assertEqual(scoped["model"], MODELS[1])
        self.assertFalse(scoped["pinned"])
        self.assertEqual(self.route(complexity="low", epic_id="DEMO-2", epic_risk_flags=[])["model"], MODELS[0])

    def test_unpinning_custom_worker_preserves_its_route_for_simple_work(self):
        self.value["streams"]["A"]["worker"] = {"model": MODELS[2], "reasoning_effort": "high", "pinned": False}
        route = self.route(complexity="low")
        self.assertEqual((route["model"], route["reasoning_effort"]), (MODELS[2], "high"))
        self.assertFalse(route["pinned"])

    def test_epic_route_changes_only_matching_epic_and_preserves_safety_floor(self):
        self.value["epic_overrides"] = {"DEMO-1": {"streams": {"A": {"worker": {
            "model": MODELS[2], "reasoning_effort": "xhigh", "pinned": True}}}}}
        scoped = self.route(epic_id="DEMO-1", epic_risk_flags=[], complexity="low")
        self.assertEqual((scoped["model"], scoped["reasoning_effort"]), (MODELS[2], "xhigh"))
        self.assertTrue(scoped["pinned"])
        other = self.route(epic_id="DEMO-2", epic_risk_flags=[], complexity="low")
        self.assertEqual(other["model"], MODELS[0])
        self.assertEqual(self.route(complexity="low")["model"], MODELS[0])
        risky = self.route(epic_id="DEMO-1", epic_risk_flags=["concurrency"])
        self.assertEqual((risky["model"], risky["reasoning_effort"]), (MODELS[3], "xhigh"))

    def test_mandatory_epic_risk_floor_wins_over_pin_without_lowering_effort(self):
        self.value["streams"]["A"]["worker"] = {"model": MODELS[0], "reasoning_effort": "max", "pinned": True}
        route = self.route(epic_id="DEMO-1", epic_risk_flags=["security"])
        self.assertEqual((route["model"], route["reasoning_effort"]), (MODELS[3], "max"))
        self.assertTrue(route["pinned"])
        self.assertTrue(route["high_risk"])
        self.assertIn("mandatory risk/review floor", " ".join(route["reasons"]))

    def test_missing_inherited_risk_context_is_not_assumed_empty(self):
        with self.assertRaisesRegex(ValidationError, "epic_risk_flags"):
            self.route(epic_id="DEMO-1")
        self.assertFalse(self.route(epic_id="DEMO-1", epic_risk_flags=[])["high_risk"])

    def test_agent_ticket_precedence_and_final_review_floor(self):
        policy = self.governance["execution"]["model_routing"]
        policy["agent_overrides"] = {"synthetic-A": {"worker": {"model": MODELS[2], "reasoning_effort": "high"}}}
        policy["ticket_overrides"] = {"DEMO-11": {"worker": {"model": MODELS[0], "reasoning_effort": "low", "pinned": True}}}
        self.assertEqual(self.route()["model"], MODELS[0])
        self.assertEqual(self.route(risk="high")["model"], MODELS[3])
        review = self.route(role="critic", worker_context_id="other")
        self.assertEqual((review["model"], review["reasoning_effort"]), (MODELS[2], "high"))

    def test_pin_blocks_optional_escalation_but_unavailable_floor_never_falls_back(self):
        self.value["streams"]["A"]["worker"]["pinned"] = True
        route = self.route(last_failure_kind="reasoning", previous_route={"model": MODELS[1], "reasoning_effort": "medium"})
        self.assertEqual(route["status"], "blocked")
        self.assertIn("pin", route["reason"])
        host = {"models": {MODELS[1]: ["medium"]}}
        route = select_route(policy_from_config(self.governance), request(risk="high"), host,
                             operating=validate_operating(self.value, self.governance), governance=self.governance)
        self.assertEqual(route["status"], "unavailable")
        self.assertEqual(route["requested"]["model"], MODELS[3])

    def test_new_mandatory_risk_floor_also_wins_over_pin_on_reasoning_retry(self):
        self.value["streams"]["A"]["worker"]["pinned"] = True
        route = self.route(risk="high", last_failure_kind="reasoning",
                           previous_route={"model": MODELS[1], "reasoning_effort": "medium"})
        self.assertEqual(route["status"], "ready")
        self.assertEqual(route["model"], MODELS[3])

    def test_forged_or_governance_mismatched_snapshot_rejected(self):
        snapshot = validate_operating(self.value, self.governance)
        for changed in [replace(snapshot, operating_hash="f" * 64), replace(snapshot, governance_hash="e" * 64)]:
            with self.assertRaises(ValidationError):
                select_route(policy_from_config(self.governance), request(), capabilities(), operating=changed, governance=self.governance)
        with self.assertRaises(ValidationError):
            select_route(policy_from_config(self.governance), request(), capabilities(), operating=snapshot)

    def test_operating_edit_does_not_change_policy_hash(self):
        first = self.route()
        self.value["streams"]["A"]["worker"]["model"] = MODELS[2]
        second = self.route()
        self.assertNotEqual(first["operating_hash"], second["operating_hash"])
        self.assertEqual(first["policy_sha256"], second["policy_sha256"])

    def test_reservation_settlement_and_cohorts_retain_original_operating_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "ledger.sqlite")
            policy = policy_from_config(self.governance)
            first_snapshot = validate_operating(self.value, self.governance)
            first = ledger.reserve("synthetic", policy, request(), capabilities(), operating=first_snapshot, governance=self.governance)
            self.value["source"] = "user"
            second_snapshot = validate_operating(self.value, self.governance)
            settled = ledger.settle(first["run_id"], outcome(first))
            second = ledger.reserve("synthetic", policy, request(phase="amendment"), capabilities(), operating=second_snapshot, governance=self.governance)
            ledger.settle(second["run_id"], outcome(second))
            self.assertEqual(settled["operating_hash"], first_snapshot.operating_hash)
            self.assertEqual(second["operating_hash"], second_snapshot.operating_hash)
            self.assertEqual(first["policy_sha256"], second["policy_sha256"])
            groups = ledger.evidence("synthetic", policy)["groups"]
            self.assertEqual({g["operating_hash"] for g in groups}, {first_snapshot.operating_hash, second_snapshot.operating_hash})
            self.assertTrue(all(g["samples"] == 1 for g in groups))

    def test_wrong_settlement_hash_quarantines_and_retains_reserved_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "ledger.sqlite")
            run = ledger.reserve("synthetic", policy_from_config(self.governance), request(), capabilities(),
                                 operating=validate_operating(self.value, self.governance), governance=self.governance)
            result = ledger.settle(run["run_id"], outcome(run, operating_hash="a" * 64))
            self.assertEqual(result["status"], "quarantined")
            self.assertEqual(result["operating_hash"], run["operating_hash"])
            with closing(sqlite3.connect(ledger.path)) as connection:
                recorded = json.loads(connection.execute("SELECT outcome FROM model_runs").fetchone()[0])
            self.assertEqual(recorded["operating_hash"], run["operating_hash"])

    def test_legacy_rows_stay_unattributed_after_additive_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.sqlite"
            ledger = RoutingLedger(path)
            run = ledger.reserve("synthetic", default_policy(), request(), capabilities())
            ledger.settle(run["run_id"], outcome(run))
            with closing(sqlite3.connect(path)) as connection:
                before = connection.execute("SELECT policy_hash,request,route,outcome FROM model_runs").fetchone()
                connection.execute("ALTER TABLE model_runs DROP COLUMN operating_hash")
                connection.commit()
            again = RoutingLedger(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT policy_hash,request,route,outcome FROM model_runs").fetchone(), before)
                self.assertIsNone(connection.execute("SELECT operating_hash FROM model_runs").fetchone()[0])
            self.assertIsNone(again.evidence("synthetic", default_policy())["groups"][0]["operating_hash"])

    def test_reference_budget_events_inherit_reserved_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "state.sqlite", str(uuid.uuid4()))
            store.control("RUNNING", "synthetic reference fixture", reconciled=True)
            run = str(uuid.uuid4())
            limits = {"runs": 2, "tokens": 200, "cost": 200, "daily_cost": 200}
            store.reserve_budget(run, "synthetic", NOW, 100, 100, limits, operating_hash="a" * 64)
            with self.assertRaisesRegex(ValidationError, "collision"):
                store.reserve_budget(run, "synthetic", NOW, 100, 100, limits, operating_hash="b" * 64)
            store.settle_budget(run, 20, 20)
            with store.connection() as connection:
                events = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM events WHERE event_type LIKE 'BUDGET_%'")]
            self.assertEqual([event["operating_hash"] for event in events], ["a" * 64, "a" * 64])
            store.verify_chain()

    def test_draining_reservation_requires_retained_ticket_and_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = RoutingLedger(Path(temporary) / "ledger.sqlite")
            policy = policy_from_config(self.governance)
            first = ledger.reserve("synthetic", policy, request(stream="C"), capabilities(),
                                   operating=validate_operating(self.value, self.governance), governance=self.governance)
            ledger.settle(first["run_id"], outcome(first))
            self.value["streams"]["count"] = 2
            reduced = validate_operating(self.value, self.governance)
            for changed in [{"ticket_id": "NEW-1"}, {"agent_id": "replacement"}]:
                with self.assertRaisesRegex(ValidationError, "[Dd]raining"):
                    ledger.reserve("synthetic", policy, request(stream="C", phase="amendment", **changed), capabilities(),
                                   operating=reduced, governance=self.governance)
            continued = ledger.reserve("synthetic", policy, request(stream="C", phase="amendment"), capabilities(),
                                       operating=reduced, governance=self.governance)
            self.assertEqual(continued["status"], "reserved")
            self.assertTrue(continued["draining"])


class OperatingRecommendationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-adopt-route-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.governance = governance()
        (self.root / ".agentic").mkdir()
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.governance))
        op.initialize_operating(self.root, self.governance)
        self.epics = self.root / "epics.json"
        self.epics.write_bytes(canonical({"schema_version": 1, "epics": [
            {"id": f"DEMO-{i}", "title": f"Bounded component {i}", "scope": [f"component{i}/**"],
             "risk_flags": ["concurrency"] if i == 2 else [],
             "complexity": "low", "uncertainty": "low", "verification": "strong"} for i in range(1, 5)]}))

    def adopt(self):
        record = op.save_recommendation(self.root, self.governance, self.epics, now=NOW)
        return record, op.set_operating(self.root, self.governance, "adopt all", recommendation_id=record["id"], accept="all")

    def route(self, snapshot, **changes):
        return select_route(policy_from_config(self.governance), request(**changes), capabilities(),
                            operating=snapshot, governance=self.governance)

    def test_adopt_all_keeps_default_workers_simple_and_escalatable(self):
        record, snapshot = self.adopt()
        self.assertEqual(4, snapshot.count)
        rows = {row["path"]: row for row in record["rows"]}
        self.assertNotIn("streams.A.worker", rows)
        self.assertFalse(rows["streams.D.worker"]["recommended"]["pinned"])
        for label in "ACD":
            with self.subTest(stream=label):
                simple = self.route(snapshot, stream=label, complexity="low")
                self.assertEqual((simple["model"], simple["reasoning_effort"]), (MODELS[0], "low"))
                self.assertFalse(simple["pinned"])
                retry = self.route(snapshot, stream=label, last_failure_kind="reasoning",
                                   previous_route={"model": MODELS[1], "reasoning_effort": "medium"})
                self.assertEqual(retry["status"], "ready")
                self.assertTrue(retry["escalated"])
        self.assertTrue(snapshot.config["streams"]["B"]["worker"]["pinned"])
        self.assertEqual(MODELS[3], self.route(snapshot, stream="B")["model"])

    def test_adoption_preserves_pins_until_explicit_custom_reversal(self):
        op.set_operating(self.root, self.governance, "Pin A to Sol/high", ["A.worker=gpt-5.6-sol/high"])
        scoped = op.set_operating(self.root, self.governance, "DEMO-1 A worker Astra/xhigh",
                                  ["A.worker=gpt-6-astra/xhigh"], epic_id="DEMO-1")
        record, snapshot = self.adopt()
        self.assertEqual(scoped.config["epic_overrides"], snapshot.config["epic_overrides"])
        kept = next(row for row in record["rows"] if row["path"] == "streams.A.worker")
        self.assertEqual(kept["current"], kept["recommended"])
        self.assertIn("Keeps your pin", kept["reason"])
        self.assertEqual(MODELS[2], self.route(snapshot, complexity="low")["model"])
        self.assertEqual(MODELS[3], self.route(snapshot, complexity="low", epic_id="DEMO-1", epic_risk_flags=[])["model"])
        self.assertEqual("blocked", self.route(snapshot, last_failure_kind="reasoning",
                         previous_route={"model": MODELS[2], "reasoning_effort": "high"})["status"])
        risky = self.route(snapshot, risk="high")
        self.assertEqual(MODELS[3], risky["model"])
        self.assertTrue(risky["pinned"])
        changed = op.set_operating(self.root, self.governance, "Set A worker back to Terra/medium",
                                   ["A.worker=gpt-5.6-terra/medium"])
        self.assertTrue(changed.config["streams"]["A"]["worker"]["pinned"])
        changed = op.set_operating(self.root, self.governance, "Unpin A's default worker", ["A.worker.pinned=false"])
        self.assertEqual(MODELS[0], self.route(changed, complexity="low")["model"])
        self.assertEqual(MODELS[3], self.route(changed, complexity="low", epic_id="DEMO-1", epic_risk_flags=[])["model"])
        with self.assertRaisesRegex(op.OperatingError, "stale"):
            op.set_operating(self.root, self.governance, "adopt previous", recommendation_id=record["id"], accept="all")

    def test_adopted_governance_default_change_is_unpinned(self):
        self.governance["execution"]["model_routing"]["role_defaults"]["worker"] = {
            "model": MODELS[2], "reasoning_effort": "medium"}
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.governance))
        record, snapshot = self.adopt()
        row = next(row for row in record["rows"] if row["path"] == "streams.A.worker")
        self.assertEqual({"model": MODELS[2], "reasoning_effort": "medium", "pinned": False}, row["recommended"])
        self.assertEqual(row["recommended"], snapshot.config["streams"]["A"]["worker"])
        self.assertEqual(MODELS[0], self.route(snapshot, complexity="low")["model"])
        self.assertEqual(MODELS[2], self.route(snapshot)["model"])


class OperatingPlanningTests(unittest.TestCase):
    def setUp(self):
        self.governance = governance()
        self.value = inventory()

    def plan(self, count=3, *, previous=None, host=6):
        raw = canonical(self.value)
        snapshot = validate_operating(configuration(count), self.governance)
        return plan_inventory(raw, sha256(raw), NOW, True, previous, execution=self.governance["execution"],
                              host_writer_capacity=host, operating=snapshot, governance=self.governance)

    def test_default_three_and_explicit_six_have_distinct_operating_not_governance_hashes(self):
        three, six = self.plan(), self.plan(6)
        self.assertEqual([s["label"] for s in three["streams"]], list("ABC"))
        self.assertEqual([s["label"] for s in six["streams"]], list("ABCDEF"))
        self.assertEqual(len(six["dispatch_packets"]), 6)
        self.assertEqual(six["review_plan"]["configured_count"], 6)
        self.assertEqual(three["capacity"]["execution_policy_fingerprint"], six["capacity"]["execution_policy_fingerprint"])
        self.assertNotEqual(three["operating_hash"], six["operating_hash"])
        self.assertTrue(all(p["operating_hash"] == six["operating_hash"] for p in six["dispatch_packets"]))

    def test_real_host_capacity_still_limits_six_operating_streams(self):
        plan = self.plan(6, host=3)
        self.assertEqual(len(plan["dispatch_packets"]), 3)
        self.assertEqual(len(plan["deferred_dispatch_packets"]), 3)
        self.assertFalse(plan["capacity"]["host_admission_confirmed"])

    def test_reduction_drains_existing_owners_without_admitting_surplus_tickets(self):
        prior = self.plan(6)
        for ticket in self.value["tickets"]:
            label = prior["tickets"][ticket["id"]]["stream"]
            if label in "CDEF":
                ticket.update(status="in_progress", history=[{"at": NOW, "actor": "synthetic", "summary": "Started current ticket"}],
                              ownership={"stream": label, "agent_id": "synthetic-" + label, "worktree": "synthetic/" + label,
                                         "state": "active", "evidence": "synthetic:accepted-owner"})
        # The trusted refreshed inventory records ownership before the next plan.
        running = self.plan(6, previous=prior)
        reduced = self.plan(2, previous=running)
        self.assertEqual(reduced["capacity"]["draining_streams"], list("CDEF"))
        self.assertFalse(reduced["capacity"]["ownership_over_capacity"])
        self.assertEqual({p["stream"] for p in reduced["dispatch_packets"]}, set("CDEF"))
        self.assertTrue(all(p["action"] == "continue_existing_agent" for p in reduced["dispatch_packets"]))
        self.assertEqual(reduced["capacity"]["selected_new_writer_count"], 0)
        # A finished surplus writer releases its slot, but its next queued ticket
        # stays on the preserved stream and is not admitted after the reduction.
        ticket = next(t for t in self.value["tickets"] if t["ownership"] and t["ownership"]["stream"] == "F")
        ticket.update(status="done")
        ticket["ownership"]["state"] = "released"
        ticket["history"].append({"at": NOW, "actor": "synthetic", "summary": "Finished current ticket"})
        finished = self.plan(2, previous=reduced)
        self.assertFalse(any(p["stream"] == "F" for p in finished["dispatch_packets"]))

    def test_legacy_fixed_reviewer_count_and_governance_ceiling_refuse_not_clamp(self):
        self.governance["execution"]["independent_reviewers"]["count"] = 3
        with self.assertRaisesRegex(ValidationError, "reviewer count"):
            self.plan(2)
        del self.governance["execution"]["independent_reviewers"]["count"]
        self.governance["execution"]["max_parallel_tickets"] = 2
        with self.assertRaisesRegex(ValidationError, "governance ceiling"):
            self.plan(3)

    def test_recommendation_counts_future_branches_despite_single_ready_bootstrap(self):
        bootstrap = self.value["tickets"][0]
        for ticket in self.value["tickets"][1:]:
            ticket["dependencies"] = [{"ticket_id": bootstrap["id"], "state": "unresolved", "evidence": [],
                                       "verified_by": None, "verified_at": None}]
        raw = canonical(self.value)
        recommended = recommendation_groups(raw, sha256(raw), NOW, self.governance["execution"], True)
        self.assertEqual(recommended["independent_group_count"], 5)
        self.assertEqual(len(recommended["groups"]), 5)
        self.assertEqual(len(self.plan(3)["dispatch_packets"]), 1)
        self.assertFalse(recommended["execution_authority"])

    def test_exact_antichain_agrees_with_exhaustive_small_graphs(self):
        rng = random.Random(187)
        for _ in range(40):
            size = 7
            reaches = [sum(1 << j for j in range(i + 1, size) if rng.random() < .3) for i in range(size)]
            for middle in range(size):
                for left in range(size):
                    if reaches[left] & (1 << middle):
                        reaches[left] |= reaches[middle]
            expected = max(len(subset) for count in range(size + 1) for subset in itertools.combinations(range(size), count)
                           if all(not (reaches[a] & (1 << b) or reaches[b] & (1 << a)) for a, b in itertools.combinations(subset, 2)))
            self.assertEqual(len(_antichain(list(range(size)), reaches)), expected)

    def test_risk_metadata_is_optional_strict_and_retained_for_recommendations(self):
        self.value["tickets"][0].update(risk_flags=["security"], complexity="high", uncertainty="medium", verification="weak")
        plan = self.plan()
        self.assertEqual(plan["tickets"]["DEMO-11"]["risk_flags"], ["security"])
        for key, value in [("risk_flags", [1]), ("risk_flags", "security"), ("complexity", True),
                           ("uncertainty", "unknown"), ("verification", "limited")]:
            changed = deepcopy(self.value)
            changed["tickets"][0][key] = value
            raw = canonical(changed)
            with self.assertRaises(ValidationError):
                recommendation_groups(raw, sha256(raw), NOW, self.governance["execution"], True)


if __name__ == "__main__":
    unittest.main()
