"""Synthetic model selection, durable budget and evidence regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.model_routing import (MODELS, RoutingLedger, default_policy,
                                   policy_from_config, select_route, validate_policy)


def request(**updates):
    result = dict(ticket_id="EX-10", role="worker", agent_id="worker-1", context_id="context-1",
                  phase="implementation", task_class="unit-fix", complexity="medium", risk="low",
                  uncertainty="low", verification="strong", risk_flags=[], reservation_tokens=1000,
                  reservation_cost_microusd=None)
    result.update(updates)
    return result


def capabilities():
    return {"models": {m: v["reasoning_efforts"] for m, v in default_policy()["models"].items()}}


def outcome(reservation, **updates):
    result = dict(actual_model=reservation["model"], actual_reasoning_effort=reservation["reasoning_effort"],
                  actual_context_id="context-1", actual_tokens=100, actual_cost_microusd=None,
                  success=True, validation_passed=True, independent_review_passed=True,
                  reviewer_context_id="reviewer-context", escaped_defect=False)
    result.update(updates)
    return result


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.policy = default_policy()
        self.temp = tempfile.TemporaryDirectory(prefix="awf-routing-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "routing.sqlite"
        self.ledger = RoutingLedger(self.path)

    def reserve(self, **updates):
        return self.ledger.reserve("project-1", self.policy, request(**updates), capabilities())

    def test_balanced_roles_and_simple_worker(self):
        expected = {"controller": (MODELS[2], "medium"), "worker": (MODELS[1], "medium"),
                    "critic": (MODELS[2], "high"), "specialist": (MODELS[3], "high")}
        for role, pair in expected.items():
            route = select_route(self.policy, request(role=role, worker_context_id="other"), capabilities())
            self.assertEqual((route["model"], route["reasoning_effort"]), pair)
        cheap = select_route(self.policy, request(complexity="low"), capabilities())
        self.assertEqual((cheap["model"], cheap["reasoning_effort"]), (MODELS[0], "low"))
        limited = select_route(self.policy, request(complexity="low", verification="limited"), capabilities())
        self.assertEqual(limited["model"], MODELS[1])

    def test_complex_risky_and_uncertain_work_uses_astra(self):
        for facts in ({"complexity": "high"}, {"risk": "high"}, {"uncertainty": "high"}, {"risk_flags": ["permissions"]}):
            route = select_route(self.policy, request(**facts), capabilities())
            self.assertEqual(route["model"], MODELS[3])

    def test_review_independence_and_floor(self):
        with self.assertRaisesRegex(ValidationError, "independent"):
            select_route(self.policy, request(role="critic", worker_context_id="context-1"), capabilities())
        self.policy["ticket_overrides"] = {"EX-10": {"critic": {"model": MODELS[0], "reasoning_effort": "low"}}}
        route = select_route(self.policy, request(role="critic", worker_context_id="worker-context"), capabilities())
        self.assertEqual((route["model"], route["reasoning_effort"]), (MODELS[2], "high"))

    def test_override_precedence_and_risk_floor(self):
        self.policy["agent_overrides"] = {"worker-1": {"worker": {"model": MODELS[2], "reasoning_effort": "high"}}}
        self.policy["ticket_overrides"] = {"EX-10": {"worker": {"model": MODELS[1], "reasoning_effort": "high"}}}
        route = select_route(self.policy, request(), capabilities())
        self.assertEqual(route["model"], MODELS[1])
        self.assertEqual(select_route(self.policy, request(risk="high"), capabilities())["model"], MODELS[3])

    def test_unavailable_model_or_effort_has_no_silent_fallback(self):
        for host in ({"models": {MODELS[0]: ["low"]}}, {"models": {MODELS[1]: ["low"]}}):
            route = select_route(self.policy, request(), host)
            self.assertEqual(route["status"], "unavailable")
            self.assertEqual(route["requested"]["model"], MODELS[1])
        self.policy["role_allowed_models"]["worker"] = [MODELS[0]]
        self.assertEqual(select_route(self.policy, request(), capabilities())["status"], "unavailable")

    def test_invalid_policy_and_request_are_rejected(self):
        for alter in (lambda p: p["budgets"].update(max_runs_per_ticket=True),
                      lambda p: p["adaptive"].update(mode="automatic"),
                      lambda p: p["role_defaults"]["worker"].update(reasoning_effort="bogus"),
                      lambda p: p["model_order"].append(MODELS[0])):
            policy = deepcopy(self.policy)
            alter(policy)
            with self.assertRaises(ValidationError):
                validate_policy(policy)
        with self.assertRaises(ValidationError):
            select_route(self.policy, request(risk="maybe"), capabilities())

    def test_config_intersects_existing_caps_and_allowlists(self):
        config = {"execution": {"model_routing": self.policy, "max_agent_runs_per_ticket": 4,
                                "max_tokens_per_ticket": 2000, "max_cost_microusd_per_ticket": 123,
                                "daily_project_cost_microusd": 456,
                                "roles": {"worker": {"approved_model_ids": [MODELS[0]]}}}}
        policy = policy_from_config(config)
        self.assertEqual(policy["budgets"]["max_runs_per_ticket"], 4)
        self.assertEqual(policy["budgets"]["max_tokens_per_ticket"], 2000)
        self.assertEqual(policy["budgets"]["max_cost_microusd_per_ticket"], 123)
        self.assertEqual(policy["role_allowed_models"]["worker"], [MODELS[0]])
        self.assertIsNone(self.policy["budgets"]["max_cost_microusd_per_ticket"])

    def test_null_execution_cost_caps_preserve_token_only_policy(self):
        config = {"execution": {"model_routing": self.policy, "max_cost_microusd_per_ticket": None,
                                "daily_project_cost_microusd": None}}
        effective = policy_from_config(config)
        self.assertIsNone(effective["budgets"]["max_cost_microusd_per_ticket"])
        self.assertIsNone(effective["budgets"]["max_cost_microusd_per_project_day"])

    def test_escalation_uses_persisted_history_and_survives_agent_change(self):
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="implementation", validation_passed=False))
        # Caller cannot erase failures or reset by selecting a new agent identity.
        second = self.reserve(agent_id="new-worker", reasoning_failures=0, previous_route=None)
        self.assertEqual(second["model"], MODELS[2])
        self.assertTrue(second["escalated"])
        self.ledger.settle(second["run_id"], outcome(second, success=False, failure_kind="reasoning", validation_passed=False))
        third = RoutingLedger(self.path).reserve("project-1", self.policy, request(), capabilities())
        self.assertEqual(third["model"], MODELS[3])
        self.ledger.settle(third["run_id"], outcome(third, success=False, failure_kind="reasoning", validation_passed=False))
        self.assertEqual(self.reserve()["status"], "blocked")

    def test_no_escalation_for_credentials_or_infrastructure(self):
        for kind in ("credentials", "infrastructure", "rate_limit", "unknown"):
            ticket = "failure-" + kind
            first = self.reserve(ticket_id=ticket)
            self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind=kind))
            retry = self.reserve(ticket_id=ticket)
            self.assertEqual(retry["status"], "blocked")
            self.assertIn(kind, retry["reason"])

    def test_escalation_caps_and_disabled_escalation(self):
        self.policy["escalation"]["max_escalations_per_ticket"] = 0
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="reasoning"))
        with self.assertRaisesRegex(ValidationError, "escalation limit"):
            self.reserve()
        self.policy["escalation"]["enabled"] = False
        self.assertEqual(self.reserve()["status"], "blocked")

    def test_outstanding_reservations_count_and_duplicate_phase_is_denied(self):
        self.policy["budgets"]["max_tokens_per_ticket"] = 1500
        self.reserve()
        with self.assertRaisesRegex(ValidationError, "outstanding"):
            self.reserve()
        with self.assertRaisesRegex(ValidationError, "token budget"):
            self.reserve(phase="another-phase")

    def test_concurrent_admission_is_atomic_across_connections(self):
        self.policy["budgets"]["max_tokens_per_project_day"] = 1000
        def attempt(index):
            try:
                return RoutingLedger(self.path).reserve("project-1", self.policy, request(ticket_id="T-" + str(index)), capabilities())["status"]
            except ValidationError:
                return "denied"
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(results.count("denied"), 3)

    def test_policy_changes_do_not_reset_ticket_accounting(self):
        self.policy["budgets"]["max_runs_per_ticket"] = 1
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first))
        self.policy["adaptive"]["min_reviewed_samples"] = 99
        with self.assertRaisesRegex(ValidationError, "run budget"):
            self.reserve(phase="test")

    def test_cost_caps_need_known_reservations_and_actuals(self):
        self.policy["budgets"]["max_cost_microusd_per_ticket"] = 100
        with self.assertRaisesRegex(ValidationError, "cost reservation"):
            self.reserve()
        first = self.reserve(reservation_cost_microusd=70)
        with self.assertRaisesRegex(ValidationError, "actual cost required"):
            self.ledger.settle(first["run_id"], outcome(first))
        self.ledger.settle(first["run_id"], outcome(first, actual_cost_microusd=50))
        with self.assertRaisesRegex(ValidationError, "cost budget"):
            self.reserve(reservation_cost_microusd=51)

    def test_token_only_reservation_and_settlement_record_null_cost(self):
        reserved = self.reserve()
        self.assertIsNone(reserved["reservation_cost_microusd"])
        settled = self.ledger.settle(reserved["run_id"], outcome(reserved, actual_tokens=321))
        self.assertEqual("settled", settled["status"])
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT reserved_cost,actual_tokens,actual_cost FROM model_runs WHERE run_id=?",
                                     (reserved["run_id"],)).fetchone()
        self.assertEqual((None, 321, None), row)

    def test_any_effective_monetary_cap_keeps_verified_cost_refusal(self):
        config = {"execution": {"model_routing": self.policy, "max_cost_microusd_per_ticket": None,
                                "daily_project_cost_microusd": 500}}
        effective = policy_from_config(config)
        with self.assertRaisesRegex(ValidationError, "^cost ceiling is configured but a verified hard cost reservation is unavailable$"):
            self.ledger.reserve("project-1", effective, request(), capabilities())

    def test_unpriced_history_blocks_new_cost_cap(self):
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first))
        self.policy["budgets"]["max_cost_microusd_per_ticket"] = 100
        with self.assertRaisesRegex(ValidationError, "historical cost is unknown"):
            self.reserve(reservation_cost_microusd=1)

    def test_mismatch_and_overrun_are_recorded_and_quarantine_project(self):
        for update in ({"actual_model": MODELS[3]}, {"actual_reasoning_effort": "high"},
                       {"actual_context_id": "different"}, {"actual_tokens": 1001}):
            ledger = RoutingLedger(Path(self.temp.name) / (str(len(list(Path(self.temp.name).iterdir()))) + ".sqlite"))
            first = ledger.reserve("p", self.policy, request(), capabilities())
            settled = ledger.settle(first["run_id"], outcome(first, **update))
            self.assertFalse(settled["accepted"])
            self.assertEqual(settled["status"], "quarantined")
            with self.assertRaisesRegex(ValidationError, "quarantined"):
                ledger.reserve("p", self.policy, request(ticket_id="another"), capabilities())
            group = ledger.evidence("p", self.policy)["groups"][0]
            self.assertEqual(group["accepted_samples"], 0)
            self.assertEqual(group["incident_tickets"], 1)

    def test_duplicate_settlement_and_self_review_rejected(self):
        first = self.reserve()
        with self.assertRaisesRegex(ValidationError, "independence"):
            self.ledger.settle(first["run_id"], outcome(first, reviewer_context_id="context-1"))
        self.ledger.settle(first["run_id"], outcome(first))
        with self.assertRaisesRegex(ValidationError, "already settled"):
            self.ledger.settle(first["run_id"], outcome(first))

    def test_shadow_evidence_requires_reviewed_low_risk_samples(self):
        self.policy["adaptive"]["min_reviewed_samples"] = 2
        before = deepcopy(self.policy)
        for index in range(2):
            run = self.reserve(ticket_id="simple-" + str(index), complexity="low")
            self.ledger.settle(run["run_id"], outcome(run))
        evidence = self.ledger.evidence("project-1", self.policy)
        self.assertFalse(evidence["automatic_policy_changes"])
        self.assertIn("eligible for", evidence["groups"][0]["recommendation"])
        self.assertIsNone(evidence["groups"][0]["actual_cost_microusd"])
        self.assertEqual(self.policy, before)
        run = self.reserve(ticket_id="escaped", complexity="low")
        self.ledger.settle(run["run_id"], outcome(run, escaped_defect=True))
        self.assertIn("insufficient", self.ledger.evidence("project-1", self.policy)["groups"][0]["recommendation"])

    def test_policy_change_separates_evidence_cohorts(self):
        first = self.reserve(complexity="low")
        self.ledger.settle(first["run_id"], outcome(first))
        self.policy["adaptive"]["min_reviewed_samples"] = 30
        self.assertEqual(self.ledger.evidence("project-1", self.policy)["groups"], [])

    def test_verified_external_resolution_allows_same_model_retry_without_refund(self):
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="credentials"))
        with self.assertRaises(ValidationError):
            self.ledger.resolve_failure(first["run_id"], {"verified": False})
        self.ledger.resolve_failure(first["run_id"], {"verified": True, "evidence_ref": "host-check-1", "reason": "Credential health check now passes"})
        second = self.reserve(agent_id="replacement-agent")
        self.assertEqual(second["model"], first["model"])
        self.assertFalse(second["escalated"])
        with self.assertRaisesRegex(ValidationError, "already recorded"):
            self.ledger.resolve_failure(first["run_id"], {"verified": True, "evidence_ref": "host-check-2", "reason": "Duplicate"})

    def test_explicit_pin_is_preserved_on_failure(self):
        self.policy["ticket_overrides"] = {"EX-10": {"worker": {"model": MODELS[1], "reasoning_effort": "medium", "pinned": True}}}
        first = self.reserve()
        self.assertTrue(first["pinned"])
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="reasoning"))
        retry = self.reserve()
        self.assertEqual(retry["status"], "blocked")
        self.assertIn("pin", retry["reason"])

    def test_evidence_counts_distinct_tickets_and_late_defects(self):
        self.policy["adaptive"]["min_reviewed_samples"] = 2
        run_ids = []
        for _ in range(3):
            run = self.reserve(complexity="low")
            run_ids.append(run["run_id"])
            self.ledger.settle(run["run_id"], outcome(run))
        group = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(group["samples"], 1)
        self.assertEqual(group["actual_tokens"], 300)
        self.assertIn("insufficient", group["recommendation"])
        run = self.reserve(complexity="low", ticket_id="EX-11")
        self.ledger.settle(run["run_id"], outcome(run))
        self.assertIn("eligible for", self.ledger.evidence("project-1", self.policy)["groups"][0]["recommendation"])
        self.ledger.record_defect(run_ids[0], {"evidence_ref": "bug-100", "reason": "Regression found after acceptance"})
        group = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(group["escaped_defects"], 1)
        self.assertIn("insufficient", group["recommendation"])

    def test_risk_floor_cannot_be_lower_than_review_floor(self):
        self.policy["risk_route"]["model"] = MODELS[1]
        with self.assertRaisesRegex(ValidationError, "review_floor"):
            validate_policy(self.policy)

    def test_escalation_uses_next_allowed_model_in_policy_order(self):
        self.policy["role_allowed_models"]["worker"] = [MODELS[1], MODELS[3]]
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="reasoning"))
        self.assertEqual(self.reserve()["model"], MODELS[3])

    def test_host_assigned_context_is_bound_at_settlement_and_review_stays_independent(self):
        first = self.reserve(context_id=None)
        self.assertTrue(self.ledger.settle(first["run_id"], outcome(first, actual_context_id="host-assigned-23"))["accepted"])
        review = self.reserve(role="critic", phase="review", context_id=None, worker_context_id="host-assigned-23")
        result = self.ledger.settle(review["run_id"], outcome(review, actual_context_id="host-assigned-23"))
        self.assertFalse(result["accepted"])
        self.assertIn("host review context is not independent", result["violations"])

    def test_newly_discovered_risk_is_itself_the_required_escalation(self):
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="implementation"))
        retry = self.reserve(risk="high")
        self.assertEqual(retry["status"], "reserved")
        self.assertEqual(retry["model"], MODELS[3])
        self.assertTrue(retry["escalated"])

    def test_escalation_blocks_instead_of_lowering_previous_effort_above_ceiling(self):
        self.policy["role_defaults"]["worker"]["reasoning_effort"] = "xhigh"
        first = self.reserve()
        self.ledger.settle(first["run_id"], outcome(first, success=False, failure_kind="reasoning"))
        retry = self.reserve()
        self.assertEqual(retry["status"], "blocked")
        self.assertIn("ceiling", retry["reason"])
        self.policy["escalation"]["effort_ceiling"] = "xhigh"
        retry = self.reserve()
        self.assertEqual((retry["model"], retry["reasoning_effort"]), (MODELS[2], "xhigh"))

    def test_new_risk_preserves_previous_effort_and_respects_ceiling(self):
        previous = {"model": MODELS[1], "reasoning_effort": "xhigh"}
        work = request(risk="high", previous_route=previous, last_failure_kind="reasoning")
        self.assertEqual(select_route(self.policy, work, capabilities())["status"], "blocked")
        self.policy["escalation"]["effort_ceiling"] = "xhigh"
        retry = select_route(self.policy, work, capabilities())
        self.assertEqual((retry["model"], retry["reasoning_effort"]), (MODELS[3], "xhigh"))

    def test_accepted_escalations_are_monotone_for_each_role_and_previous_pair(self):
        for role in ("controller", "worker", "critic", "specialist"):
            for model in MODELS:
                for effort in ("low", "medium", "high", "xhigh"):
                    work = request(role=role, worker_context_id="independent", previous_route={"model": model, "reasoning_effort": effort},
                                   last_failure_kind="reasoning")
                    route = select_route(self.policy, work, capabilities())
                    if route["status"] == "ready":
                        self.assertGreaterEqual(MODELS.index(route["model"]), MODELS.index(model))
                        self.assertGreaterEqual(("low", "medium", "high", "xhigh").index(route["reasoning_effort"]),
                                                ("low", "medium", "high", "xhigh").index(effort))
                        self.assertIn(route["reasoning_effort"], ("low", "medium", "high"))

    def test_escalation_ceiling_must_be_supported_by_every_approved_model(self):
        self.policy["models"][MODELS[0]]["reasoning_efforts"] = ["low"]
        with self.assertRaisesRegex(ValidationError, "effort_ceiling"):
            validate_policy(self.policy)
        self.policy["escalation"]["enabled"] = False
        validate_policy(self.policy)

    def test_structural_type_guards_raise_validation_errors(self):
        for roles in (None, "worker", [], 4, {"worker": None}, {"worker": []}, {"worker": "bad"}):
            with self.subTest(roles=roles), self.assertRaises(ValidationError):
                policy_from_config({"execution": {"model_routing": self.policy, "roles": roles}})
        for malformed in (None, [], "config", 2, {"execution": None}):
            with self.subTest(config=malformed), self.assertRaises(ValidationError):
                policy_from_config(malformed)
        for key, bad in (("previous_route", {}), ("previous_route", []), ("last_failure_kind", []),
                         ("reasoning_failures", True), ("resolved_failure_route", {})):
            with self.subTest(key=key, bad=bad), self.assertRaises(ValidationError):
                select_route(self.policy, request(**{key: bad}), capabilities())
        for host in (None, [], {"models": None}, {"models": []}, {"models": {MODELS[1]: None}}):
            with self.subTest(host=host), self.assertRaises(ValidationError):
                select_route(self.policy, request(), host)
        with self.assertRaises(ValidationError):
            self.reserve(reservation_tokens=2**64)

    def enable_reconciliation(self):
        self.policy["reconciliation"] = {"enabled": True, "authorized_operator_ids": ["operator-1"]}

    def reconciliation_observation(self, **updates):
        value = {"reconciliation_id": "reconcile-1", "operator_id": "operator-1",
                 "authorization_ref": "operator-grant-7", "reason": "Host process stopped and limit enforcement repaired",
                 "evidence_ref": "host-process-observation-4", "remediation_ref": "host-limit-check-8",
                 "host_stopped": True, "remediation_verified": True}
        value.update(updates)
        return value

    def test_reconciliation_is_disabled_absent_policy_and_needs_authorized_operator(self):
        run = self.reserve()
        self.policy.pop("reconciliation")
        validate_policy(self.policy)
        with self.assertRaisesRegex(ValidationError, "disabled"):
            self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation())
        self.enable_reconciliation()
        for updates in ({"operator_id": "another"}, {"host_stopped": False}, {"remediation_verified": False},
                        {"authorization_ref": ""}, {"remediation_ref": ""}, {"charged_tokens": 0}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation(**updates))
        with self.assertRaisesRegex(ValidationError, "this project"):
            self.ledger.reconcile("different-project", self.policy, run["run_id"], self.reconciliation_observation())

    def test_orphan_reconciliation_preserves_full_unknown_charge_and_exact_idempotence(self):
        self.enable_reconciliation()
        run = self.reserve(reservation_cost_microusd=50)
        observation = self.reconciliation_observation()
        result = self.ledger.reconcile("project-1", self.policy, run["run_id"], observation)
        self.assertEqual((result["charged_tokens"], result["charged_cost_microusd"]), (1000, 50))
        self.assertFalse(result["budgets_refunded"])
        self.assertEqual(result, RoutingLedger(self.path).reconcile("project-1", self.policy, run["run_id"], observation))
        with self.assertRaisesRegex(ValidationError, "conflicts"):
            self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation(reason="Different claim"))
        with self.assertRaisesRegex(ValidationError, "only outstanding"):
            self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation(reconciliation_id="another-key"))
        self.policy["budgets"]["max_tokens_per_ticket"] = 1999
        with self.assertRaisesRegex(ValidationError, "token budget"):
            self.reserve()
        self.policy["budgets"]["max_tokens_per_ticket"] = 2000
        retry = self.reserve()
        self.assertEqual(retry["model"], run["model"])
        self.assertFalse(retry["escalated"])
        self.assertEqual(self.ledger.evidence("project-1", self.policy)["groups"], [])

    def test_quarantine_reconciliation_preserves_overruns_and_other_incidents(self):
        self.enable_reconciliation()
        first = self.reserve(reservation_cost_microusd=50)
        other = self.reserve(ticket_id="EX-11")
        self.ledger.settle(first["run_id"], outcome(first, actual_tokens=1100, actual_cost_microusd=60))
        self.ledger.settle(other["run_id"], outcome(other, actual_model=MODELS[3]))
        result = self.ledger.reconcile("project-1", self.policy, first["run_id"], self.reconciliation_observation())
        self.assertEqual((result["charged_tokens"], result["charged_cost_microusd"]), (1100, 60))
        with self.assertRaisesRegex(ValidationError, "quarantined"):
            self.reserve()
        self.ledger.reconcile("project-1", self.policy, other["run_id"], self.reconciliation_observation(reconciliation_id="second-incident"))
        self.policy["budgets"]["max_tokens_per_ticket"] = 2000
        with self.assertRaisesRegex(ValidationError, "token budget"):
            self.reserve()
        events = self.ledger.reconciliation_history("project-1")["reconciliations"]
        self.assertEqual(len(events), 2)
        self.assertIn("host token ceiling overrun", json.loads(events[0]["observation"]["before"]["outcome"])["violations"])
        self.assertEqual(self.ledger.reconciliation_history("different-project")["reconciliations"], [])
        self.assertEqual(self.ledger.evidence("project-1", self.policy)["groups"], [])

    def test_reconciled_defect_and_overrun_survive_a_successful_same_ticket_retry(self):
        self.enable_reconciliation()
        self.policy["adaptive"]["min_reviewed_samples"] = 1
        run = self.reserve(complexity="low", reservation_cost_microusd=50)
        self.ledger.settle(run["run_id"], outcome(run, actual_tokens=1100, actual_cost_microusd=60, escaped_defect=True))
        self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation())
        retry = self.reserve(complexity="low", reservation_cost_microusd=20)
        self.ledger.settle(retry["run_id"], outcome(retry, actual_tokens=100, actual_cost_microusd=10))
        group = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(group["samples"], 1)
        self.assertEqual(group["escaped_defects"], 1)
        self.assertEqual(group["incident_tickets"], 1)
        self.assertEqual(group["accepted_samples"], 0)
        self.assertEqual((group["actual_tokens"], group["charged_tokens"]), (1200, 1200))
        self.assertEqual((group["actual_cost_microusd"], group["charged_cost_microusd"]), (70, 70))
        self.assertIn("insufficient", group["recommendation"])

    def test_unknown_reconciled_usage_is_charged_but_never_reported_as_actual_or_positive(self):
        self.enable_reconciliation()
        self.policy["adaptive"]["min_reviewed_samples"] = 1
        run = self.reserve(complexity="low", reservation_cost_microusd=50)
        self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation())
        retry = self.reserve(complexity="low", reservation_cost_microusd=20)
        self.ledger.settle(retry["run_id"], outcome(retry, actual_cost_microusd=10))
        group = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(group["accepted_samples"], 0)
        self.assertEqual(group["incident_tickets"], 1)
        self.assertIsNone(group["actual_tokens"])
        self.assertIsNone(group["actual_cost_microusd"])
        self.assertEqual(group["charged_tokens"], 1100)
        self.assertEqual(group["charged_cost_microusd"], 60)
        self.assertIn("insufficient", group["recommendation"])

    def test_late_defect_on_reconciled_incident_suppresses_otherwise_qualified_cohort(self):
        self.enable_reconciliation()
        incident = self.reserve(complexity="low")
        self.ledger.settle(incident["run_id"], outcome(incident, actual_tokens=1001))
        observation = self.reconciliation_observation()
        reconciled = self.ledger.reconcile("project-1", self.policy, incident["run_id"], observation)
        retry = self.reserve(complexity="low")
        self.ledger.settle(retry["run_id"], outcome(retry))
        for index in range(19):
            run = self.reserve(ticket_id="clean-" + str(index), complexity="low")
            self.ledger.settle(run["run_id"], outcome(run))
        before = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual((before["samples"], before["accepted_samples"], before["reviewed_samples"]), (20, 19, 20))
        self.assertIn("eligible for", before["recommendation"])
        defect = {"evidence_ref": "late-bug-1", "reason": "Escaped defect attributable to reconciled incident"}
        self.ledger.record_defect(incident["run_id"], defect)
        self.ledger.record_defect(incident["run_id"], defect)
        after = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(after["escaped_defects"], 1)
        self.assertEqual(after["charged_tokens"], before["charged_tokens"])
        self.assertIn("insufficient", after["recommendation"])
        self.assertEqual(self.ledger.reconcile("project-1", self.policy, incident["run_id"], observation), reconciled)

    def test_late_defect_on_quarantine_is_append_only_and_outstanding_runs_are_rejected(self):
        run = self.reserve(complexity="low")
        defect = {"evidence_ref": "bug-evidence", "reason": "Attributable escaped defect"}
        with self.assertRaisesRegex(ValidationError, "closed"):
            self.ledger.record_defect(run["run_id"], defect)
        self.ledger.settle(run["run_id"], outcome(run, actual_tokens=1001))
        connection = sqlite3.connect(self.path)
        try:
            before = connection.execute("SELECT status,outcome FROM model_runs WHERE run_id=?", (run["run_id"],)).fetchone()
            self.ledger.record_defect(run["run_id"], defect)
            after = connection.execute("SELECT status,outcome FROM model_runs WHERE run_id=?", (run["run_id"],)).fetchone()
            self.assertEqual(before, after)
        finally:
            connection.close()
        group = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(group["escaped_defects"], 1)
        self.assertEqual(group["accepted_samples"], 0)

    def test_reconciliation_audit_is_append_only_and_late_settlement_cannot_refund(self):
        self.enable_reconciliation()
        run = self.reserve()
        self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation())
        with self.assertRaisesRegex(ValidationError, "already settled"):
            self.ledger.settle(run["run_id"], outcome(run, actual_tokens=0))
        connection = sqlite3.connect(self.path)
        try:
            for sql in ("DELETE FROM model_reconciliations", "UPDATE model_reconciliations SET observation='{}'"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(sql)
            row = connection.execute("SELECT actual_tokens,actual_cost,status FROM model_runs WHERE run_id=?", (run["run_id"],)).fetchone()
            self.assertEqual(row, (None, None, "reconciled"))
        finally:
            connection.close()

    def test_concurrent_reconciliation_has_one_audit_event(self):
        self.enable_reconciliation()
        run = self.reserve()
        def reconcile(_):
            return RoutingLedger(self.path).reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation())
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(reconcile, range(3)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual(len(self.ledger.reconciliation_history("project-1")["reconciliations"]), 1)

    def test_reconciliation_stops_future_daily_carry_but_preserves_ticket_history(self):
        self.enable_reconciliation()
        run = self.reserve()
        self.ledger.reconcile("project-1", self.policy, run["run_id"], self.reconciliation_observation())
        # A stopped reconciled run from a past UTC day no longer consumes every
        # future daily allowance, but its full charge remains on the ticket.
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("UPDATE model_runs SET day='2000-01-01',closed_day='2000-01-02' WHERE run_id=?", (run["run_id"],))
            connection.commit()
        finally:
            connection.close()
        self.policy["budgets"]["max_tokens_per_project_day"] = 1000
        self.reserve(ticket_id="new-day-ticket")
        self.policy["budgets"]["max_tokens_per_ticket"] = 1000
        with self.assertRaisesRegex(ValidationError, "ticket token budget"):
            self.reserve()

    def test_legacy_ledger_migration_preserves_usage_and_budgets(self):
        run = self.reserve()
        self.ledger.settle(run["run_id"], outcome(run))
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute("SELECT * FROM model_runs WHERE run_id=?", (run["run_id"],)).fetchone()
        finally:
            connection.close()
        legacy_path = Path(self.temp.name) / "legacy.sqlite"
        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.execute("""CREATE TABLE model_runs (
              run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
              role TEXT NOT NULL, agent_id TEXT NOT NULL, phase TEXT NOT NULL,
              created_at TEXT NOT NULL, day TEXT NOT NULL, policy_hash TEXT NOT NULL,
              status TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
              reserved_cost INTEGER, actual_tokens INTEGER, actual_cost INTEGER,
              escalated INTEGER NOT NULL, request TEXT NOT NULL, route TEXT NOT NULL, outcome TEXT)""")
            legacy.execute("INSERT INTO model_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row[:18])
            legacy.commit()
        finally:
            legacy.close()
        migrated = RoutingLedger(legacy_path)
        self.policy["budgets"]["max_runs_per_ticket"] = 1
        with self.assertRaisesRegex(ValidationError, "run budget"):
            migrated.reserve("project-1", self.policy, request(), capabilities())
        legacy = sqlite3.connect(legacy_path)
        try:
            restored = legacy.execute("SELECT * FROM model_runs WHERE run_id=?", (run["run_id"],)).fetchone()
            self.assertEqual(restored[:18], row[:18])
            self.assertEqual(restored[18], json.loads(row[17])["settled_at"][:10])
        finally:
            legacy.close()

    def test_repeated_hang_reconciliation_stops_admission_without_reasoning_escalation(self):
        self.enable_reconciliation()
        observations = []
        runs = []
        for index in range(3):
            run = self.reserve()
            self.assertEqual(run["status"], "reserved")
            self.assertEqual(run["model"], MODELS[1])
            self.assertFalse(run["escalated"])
            observation = self.reconciliation_observation(reconciliation_id="hang-" + str(index))
            closed = self.ledger.reconcile("project-1", self.policy, run["run_id"], observation)
            self.assertEqual(closed["charged_tokens"], 1000)
            self.assertFalse(closed["budgets_refunded"])
            runs.append(run)
            observations.append(observation)
        blocked = self.reserve()
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["code"], "RECONCILED_INCIDENT_RETRY_LIMIT")
        self.assertEqual((blocked["reconciled_incidents"], blocked["retry_limit"]), (3, 2))
        self.assertNotIn("reasoning failure", blocked["reason"])
        self.assertEqual(self.ledger.reconcile("project-1", self.policy, runs[-1]["run_id"], observations[-1])["status"], "reconciled")
        self.assertEqual(len(self.ledger.reconciliation_history("project-1")["reconciliations"]), 3)
        group = self.ledger.evidence("project-1", self.policy)["groups"][0]
        self.assertEqual(group["charged_tokens"], 3000)
        self.assertIsNone(group["actual_tokens"])

    def test_reconciled_retry_cap_survives_policy_agent_phase_and_role_changes(self):
        self.enable_reconciliation()
        self.policy["reconciliation"]["max_reconciled_incident_retries_per_ticket"] = 0
        first = self.reserve()
        self.ledger.reconcile("project-1", self.policy, first["run_id"], self.reconciliation_observation())
        self.policy["adaptive"]["min_reviewed_samples"] += 1
        reopened = RoutingLedger(self.path)
        for changes in ({"agent_id": "replacement"}, {"phase": "renamed"},
                        {"role": "critic", "worker_context_id": "independent-worker"}):
            with self.subTest(changes=changes):
                blocked = reopened.reserve("project-1", self.policy, request(**changes), capabilities())
                self.assertEqual(blocked["code"], "RECONCILED_INCIDENT_RETRY_LIMIT")
        self.assertEqual(self.reserve(ticket_id="independent-ticket")["status"], "reserved")

    def test_reconciliation_allowance_never_prevents_closing_already_outstanding_runs(self):
        self.enable_reconciliation()
        self.policy["reconciliation"]["max_reconciled_incident_retries_per_ticket"] = 0
        first = self.reserve()
        second = self.reserve(role="critic", worker_context_id="other-worker", phase="review")
        for index, run in enumerate((first, second)):
            result = self.ledger.reconcile("project-1", self.policy, run["run_id"],
                                           self.reconciliation_observation(reconciliation_id="close-" + str(index)))
            self.assertEqual(result["status"], "reconciled")
        self.assertEqual(self.reserve()["reconciled_incidents"], 2)

    def test_optional_retry_default_does_not_mutate_policy_or_evidence_hash(self):
        self.enable_reconciliation()
        original = deepcopy(self.policy)
        original_bytes = json.dumps(original, sort_keys=True)
        first = self.reserve(complexity="low")
        self.ledger.settle(first["run_id"], outcome(first))
        validate_policy(self.policy)
        derived = policy_from_config({"execution": {"model_routing": self.policy}})
        self.assertEqual(json.dumps(self.policy, sort_keys=True), original_bytes)
        self.assertEqual(derived, original)
        self.assertNotIn("max_reconciled_incident_retries_per_ticket", derived["reconciliation"])
        prior_evidence = self.ledger.evidence("project-1", original)
        self.policy["reconciliation"]["max_reconciled_incident_retries_per_ticket"] = 2
        current = self.ledger.evidence("project-1", self.policy)
        self.assertEqual(current["policy_sha256"], prior_evidence["policy_sha256"])
        self.assertEqual(current["groups"], prior_evidence["groups"])
        self.assertEqual(self.ledger.evidence("project-1", original), prior_evidence)

    def test_reconciled_retry_limit_rejects_invalid_values(self):
        for value in (True, None, -1, "2", [], 2**60):
            policy = deepcopy(self.policy)
            policy["reconciliation"]["max_reconciled_incident_retries_per_ticket"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_policy(policy)

    def test_cli_defaults_suggestion_and_worktree_ledger_rejection(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location("awf_route_model_cli", root / ".agentic/scripts/route_model.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(["defaults"]), 0)
            self.assertEqual(json.loads(output.getvalue())["profile"], "balanced")
        documents = {"config": {"execution": {"model_routing": self.policy}, "project": {"id": "p"}},
                     "request": request(), "capabilities": capabilities()}
        with patch.object(cli, "read_document", side_effect=lambda p: documents[p.name]), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(["suggest", "--legacy-without-operating", "--config", "config", "--request", "request", "--capabilities", "capabilities"]), 0)
            self.assertEqual(json.loads(output.getvalue())["model"], MODELS[1])
        with patch.object(cli, "read_document", side_effect=lambda p: documents[p.name]), patch("sys.stdout", new_callable=io.StringIO) as output:
            args = ["reserve", "--config", "config", "--request", "request", "--capabilities", "capabilities",
                    "--project-root", self.temp.name, "--ledger", str(self.path)]
            self.assertEqual(cli.main(args), 2)
            self.assertIn("outside", output.getvalue())


if __name__ == "__main__":
    unittest.main()
