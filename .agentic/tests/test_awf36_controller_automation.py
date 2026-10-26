"""Red-first acceptance coverage for AWF-36 controller automation switches."""
from __future__ import annotations

import ast
import inspect
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError  # noqa: E402
from agentic.canonical import fingerprint  # noqa: E402
from agentic.continuous_controller import (  # noqa: E402
    ContinuousControllerStore,
    production_controller_cycle,
    validate_ticket_record,
)
from test_publication_readiness import (  # noqa: E402
    publication_config,
    publication_observation,
)


NOW = "2026-10-16T08:00:00Z"
BINDING = {
    "project_id": "project-1",
    "repository_id": "101",
    "scope_sha256": "a" * 64,
}
REPOSITORY = {
    "repository_root": Path("."),
    "repository_head_sha": "a" * 40,
    "repository_tree_sha": "b" * 40,
}
COUNTS = {
    "required": 1,
    "completed": 0,
    "acceptable": 0,
    "failed": 0,
    "stale": 0,
    "outstanding": 1,
}
TARGET_SWITCHES = (
    "dispatch_enabled",
    "auto_dispatch",
    "auto_request_critic",
    "auto_resume_amendments",
)


def controller_config(**overrides):
    value = {key: True for key in TARGET_SWITCHES}
    value.update(overrides)
    return value


def ticket(name, *, role="writer", stage="implementation"):
    return {
        "ticket": name,
        "priority": 1,
        "disposition": "ELIGIBLE",
        "actor": "controller",
        "reason": "eligible",
        "next_action": "continue " + name,
        "resume_trigger": "completion",
        "paths": ["fixtures/" + name.lower() + ".txt"],
        "dependencies_satisfied": True,
        "budget_available": True,
        "cap_available": True,
        "review_independent": True,
        "exact_tuple": "base:a/head:b/tree:c/contract:d/review:e",
        "activity": "work " + name,
        "verification_gate": "PENDING",
        "reviewer_completion": dict(COUNTS),
        "open_findings": 0,
        "jira_status": "In Progress",
        "dispatch_role": role,
        "lifecycle_stage": stage,
    }


def inventory_observation(observed_at, items):
    return {
        "source": "host_observation",
        "observed_at": observed_at,
        "binding": dict(BINDING),
        "complete": True,
        "inventory_sha256": fingerprint(
            "controller-inventory", {"binding": BINDING, "tickets": items}
        ),
        "tickets": items,
    }


def dispatch_receipt(payload, observed_at):
    keys = (
        "dispatch_id",
        "stream",
        "ticket",
        "exact_tuple",
        "dispatch_nonce",
        "prepared_at",
        "begun_at",
        "role",
        "lifecycle_stage",
    )
    return {key: payload[key] for key in keys} | {
        "status": "ACCEPTED",
        "observed_at": observed_at,
        "session_id": "session-" + payload["ticket"],
    }


class ControllerAutomationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf36-controller-")
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.counter = 0
        repository_patch = patch(
            "agentic.continuous_controller.canonical_repository_paths",
            side_effect=lambda items, *_: (
                items,
                {
                    "repository_root": "/fixture",
                    "head_sha": "a" * 40,
                    "tree_sha": "b" * 40,
                    "git_path_count": 0,
                },
            ),
        )
        repository_patch.start()
        self.addCleanup(repository_patch.stop)
        skill_patch = patch(
            "agentic.codex_skill.observe_codex_awf_skills",
            return_value={
                "format": "awf-codex-skill-selection-1",
                "expected_version": "1.9.5",
                "project_root": "/fixture",
                "pinned_skill_path": "/fixture/skill",
                "launch_directory": "/fixture",
                "visible_skills": ["awf"],
                "status": "PASS",
                "problems": [],
            },
        )
        skill_patch.start()
        self.addCleanup(skill_patch.stop)

    def store(self, prefix="cycle"):
        self.counter += 1
        return ContinuousControllerStore(
            self.temporary / f"{prefix}-{self.counter}.sqlite3", ["A"]
        )

    def cycle(
        self,
        store,
        item,
        policy,
        *,
        observed_at=NOW,
        dispatch=None,
        reconcile=None,
    ):
        publication_calls = []
        dispatch_calls = []
        reconciliation_calls = []

        def observe_publication(value):
            publication_calls.append(value["ticket"])
            return publication_observation(value["ticket"], observed_at)

        def dispatch_ticket(payload):
            dispatch_calls.append(payload["ticket"])
            if dispatch is not None:
                return dispatch(payload)
            return dispatch_receipt(payload, observed_at)

        def observe_dispatch(payload):
            reconciliation_calls.append(payload["ticket"])
            if reconcile is not None:
                return reconcile(payload)
            return dispatch_receipt(payload, observed_at)

        arguments = {
            "now": observed_at,
            "host_capacity": 1,
            "inventory_binding": BINDING,
            **REPOSITORY,
            "observe_inventory": lambda: inventory_observation(observed_at, [item]),
            "dispatch_ticket": dispatch_ticket,
            "observe_dispatch": observe_dispatch,
            "deliver_status": lambda digest: {
                "delivery_id": digest["delivery_id"],
                "status": "DELIVERED",
                "observed_at": observed_at,
            },
            "publication_config": publication_config(),
            "observe_publication": observe_publication,
        }
        if "controller_config" in inspect.signature(production_controller_cycle).parameters:
            arguments["controller_config"] = policy
        result = production_controller_cycle(store, **arguments)
        return result, publication_calls, dispatch_calls, reconciliation_calls

    def assert_gate_refusal(self, result, item, switch):
        refusal = next(
            (error for error in result["errors"] if error["operation"] == "dispatch_gate"),
            None,
        )
        self.assertEqual(
            {
                "operation": "dispatch_gate",
                "ticket": item["ticket"],
                "state": "REFUSED",
                "switch": "controller." + switch,
                "reason": (
                    "CONTROLLER_AUTOMATION_DISABLED: controller."
                    + switch
                    + " is false"
                ),
            },
            refusal,
        )

    def test_master_switch_refuses_before_path_gate_and_publication(self):
        item = ticket("AWF-36-MASTER", role="critic", stage="independent_review")
        result, publications, dispatches, _ = self.cycle(
            self.store("master"),
            item,
            controller_config(dispatch_enabled=False, auto_request_critic=False),
        )
        self.assertEqual([], publications, "disabled master reached publication observation")
        self.assertEqual([], dispatches, "disabled master reached dispatch")
        self.assert_gate_refusal(result, item, "dispatch_enabled")
        self.assertFalse(result["execution_authority"])

    def test_implementation_path_switch_refuses_when_false_and_dispatches_when_true(self):
        item = ticket("AWF-36-IMPLEMENTATION")
        disabled, publications, dispatches, _ = self.cycle(
            self.store("implementation-off"),
            item,
            controller_config(auto_dispatch=False),
        )
        self.assertEqual([], publications, "disabled implementation reached publication")
        self.assertEqual([], dispatches, "disabled implementation reached dispatch")
        self.assert_gate_refusal(disabled, item, "auto_dispatch")

        enabled, publications, dispatches, _ = self.cycle(
            self.store("implementation-on"), item, controller_config()
        )
        self.assertEqual([item["ticket"]], publications)
        self.assertEqual([item["ticket"]], dispatches)
        self.assertEqual(1, len(enabled["dispatch_receipts"]))

    def test_independent_review_switch_refuses_when_false_and_dispatches_when_true(self):
        item = ticket("AWF-36-REVIEW", role="critic", stage="independent_review")
        disabled, publications, dispatches, _ = self.cycle(
            self.store("review-off"),
            item,
            controller_config(auto_request_critic=False),
        )
        self.assertEqual([], publications, "disabled review reached publication")
        self.assertEqual([], dispatches, "disabled review reached dispatch")
        self.assert_gate_refusal(disabled, item, "auto_request_critic")

        enabled, publications, dispatches, _ = self.cycle(
            self.store("review-on"), item, controller_config()
        )
        self.assertEqual([item["ticket"]], publications)
        self.assertEqual([item["ticket"]], dispatches)
        self.assertEqual(1, len(enabled["dispatch_receipts"]))

    def test_amendment_is_authenticated_and_its_switch_controls_dispatch(self):
        item = ticket("AWF-36-AMENDMENT", role="writer", stage="amendment")
        try:
            validated = validate_ticket_record(item)
        except ValidationError as exc:
            self.fail(f"amendment must be an authenticated writer stage: {exc}")
        self.assertEqual("writer", validated["dispatch_role"])

        disabled, publications, dispatches, _ = self.cycle(
            self.store("amendment-off"),
            item,
            controller_config(auto_resume_amendments=False),
        )
        self.assertEqual([], publications, "disabled amendment reached publication")
        self.assertEqual([], dispatches, "disabled amendment reached dispatch")
        self.assert_gate_refusal(disabled, item, "auto_resume_amendments")

        enabled, publications, dispatches, _ = self.cycle(
            self.store("amendment-on"), item, controller_config()
        )
        self.assertEqual([item["ticket"]], publications)
        self.assertEqual([item["ticket"]], dispatches)
        self.assertEqual(1, len(enabled["dispatch_receipts"]))

    def test_controller_policy_is_required_exact_and_validated_before_recovery_or_providers(self):
        parameter = inspect.signature(production_controller_cycle).parameters.get(
            "controller_config"
        )
        self.assertIsNotNone(parameter, "production cycle has no controller_config input")
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, parameter.kind)
        self.assertIs(inspect.Parameter.empty, parameter.default)

        invalid = []
        for key in TARGET_SWITCHES:
            missing = controller_config()
            del missing[key]
            invalid.append((missing, key, "missing"))
            invalid.append((controller_config(**{key: 1}), key, "integer"))

        for policy, key, case in invalid:
            with self.subTest(key=key, case=case):
                store = self.store("invalid")
                with patch.object(
                    store,
                    "recover_dispatches",
                    side_effect=AssertionError("invalid policy reached durable recovery"),
                ):
                    with self.assertRaisesRegex(
                        ValidationError, re.escape("controller." + key)
                    ):
                        production_controller_cycle(
                            store,
                            controller_config=policy,
                            now=NOW,
                            host_capacity=1,
                            inventory_binding=BINDING,
                            **REPOSITORY,
                            observe_inventory=lambda: self.fail(
                                "invalid policy reached inventory"
                            ),
                            dispatch_ticket=lambda payload: self.fail(
                                "invalid policy reached dispatch"
                            ),
                            observe_dispatch=lambda payload: self.fail(
                                "invalid policy reached reconciliation"
                            ),
                            deliver_status=lambda digest: self.fail(
                                "invalid policy reached status delivery"
                            ),
                        )

    def test_disabled_gate_cancels_pending_but_reconciles_unknown(self):
        pending_store = self.store("pending")
        pending_item = ticket("AWF-36-PENDING")
        pending_store.schedule([pending_item], NOW, host_capacity=1)
        intent = pending_store.prepare_dispatches(NOW)[0]
        later = "2026-10-16T08:00:01Z"
        result, publications, dispatches, reconciliations = self.cycle(
            pending_store,
            pending_item,
            controller_config(auto_dispatch=False),
            observed_at=later,
        )
        with pending_store.connection() as database:
            status = database.execute(
                "SELECT status FROM controller_dispatch WHERE dispatch_id=?",
                (intent["dispatch_id"],),
            ).fetchone()[0]
        self.assertEqual("CANCELLED", status, repr(result))
        self.assertEqual([], publications)
        self.assertEqual([], dispatches)
        self.assertEqual([], reconciliations)

        unknown_store = self.store("unknown")
        unknown_item = ticket("AWF-36-UNKNOWN")

        def uncertain(_payload):
            raise RuntimeError("synthetic uncertain host result")

        first, _, first_dispatches, _ = self.cycle(
            unknown_store,
            unknown_item,
            controller_config(),
            dispatch=uncertain,
        )
        self.assertEqual([unknown_item["ticket"]], first_dispatches)
        self.assertEqual("UNKNOWN", first["errors"][0]["state"])

        latest = "2026-10-16T08:00:02Z"
        second, publications, dispatches, reconciliations = self.cycle(
            unknown_store,
            unknown_item,
            controller_config(dispatch_enabled=False),
            observed_at=latest,
            reconcile=lambda payload: dispatch_receipt(payload, latest),
        )
        self.assertEqual([], publications)
        self.assertEqual([], dispatches)
        self.assertEqual([unknown_item["ticket"]], reconciliations)
        self.assertEqual("ACCEPTED", second["dispatch_receipts"][0]["status"])

    def test_cli_passes_exact_reviewed_controller_mapping_to_cycle(self):
        source = (ROOT / ".agentic/scripts/continuous_controller.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "production_controller_cycle"
        ]
        self.assertEqual(1, len(calls), "expected one production cycle call in the CLI")
        values = [
            keyword.value
            for keyword in calls[0].keywords
            if keyword.arg == "controller_config"
        ]
        self.assertEqual(
            1,
            len(values),
            "CLI does not pass the reviewed controller mapping",
        )
        value = values[0]
        self.assertIsInstance(value, ast.Subscript)
        self.assertIsInstance(value.value, ast.Name)
        self.assertEqual("config", value.value.id)
        self.assertIsInstance(value.slice, ast.Constant)
        self.assertEqual("controller", value.slice.value)


if __name__ == "__main__":
    unittest.main()
