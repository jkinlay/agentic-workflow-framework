"""AWF-48 intake-tier assignment regressions."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.adoption_config import assign_project_start
from agentic.contracts import Contracts
from agentic.intake_assignment import (
    assign_intake,
    change_assignment,
    classify_ticket,
    inherit_pr_assignment,
)

NOW = "2026-10-10T08:00:00Z"


def config():
    return {
        "execution": {"risk_tiers": {
            "tier1_eligible_paths": ["docs/**", "tests/**", "*.md"],
            "tier1_excluded_paths": [],
            "tier1_review": {"roles": ["critic"], "specialist_when_touching": ["security"], "findings": "advisory"},
            "tier2_review": {"roles": ["critic", "specialist"], "findings": "blocking"},
            "tier3_review": {"roles": ["critic", "specialist"], "findings": "blocking", "max_rounds": 3},
        }},
        "scope": {"protected_paths": [".agentic/**", ".github/workflows/**", "AGENTS.md"]},
        "jira": {"enabled": True, "controller_actor_id": "controller-1"},
        "merge_gate": {"trusted_owner_ids": [1001, 1002]},
    }


def ticket(key="AWF-100", **updates):
    value = {"key": key, "summary": "Moderate application change", "description": "",
             "write_paths": ["src/widget.py"], "risk_flags": []}
    value.update(updates)
    return value


class FakeJira:
    def __init__(self, keys=("AWF-100",)):
        self.issues = {key: {"labels": ["backlog"], "comments": []} for key in keys}
        self.calls = []
        self.mismatch = False

    def read_issue(self, key):
        self.calls.append(("read", key))
        return copy.deepcopy(self.issues[key])

    def set_labels(self, key, labels, *, actor_id):
        self.calls.append(("labels", key, actor_id))
        if not self.mismatch:
            self.issues[key]["labels"] = list(labels)

    def add_comment(self, key, body, *, actor_id):
        self.calls.append(("comment", key, actor_id))
        if not self.mismatch:
            self.issues[key]["comments"].append({"id": str(len(self.issues[key]["comments"]) + 1),
                                                  "body": body, "author_id": actor_id})


class RuleDefaults(unittest.TestCase):
    def test_0950_rule_classifies_samples(self):
        cases = [
            (ticket(summary="Docs", write_paths=["docs/guide.md"]), 1, 1, False),
            (ticket(summary="Tests", write_paths=["tests/test_widget.py"]), 1, 1, False),
            (ticket(summary="Single-file fix", change_kind="single_file_fix"), 1, 1, False),
            (ticket(summary="Moderate code in one area"), 2, 2, False),
            (ticket(summary="Rotate security credential", risk_flags=["security"]), 3, 3, True),
            (ticket(summary="Governance and merge gate"), 3, 3, True),
            (ticket(summary="Database migration"), 3, 3, True),
            (ticket(summary="Installer upgrade packaging"), 3, 3, True),
            (ticket(summary="Controller scheduling"), 3, 3, True),
            (ticket(summary="Release PR", change_kind="release_pr"), 3, 3, True),
        ]
        for item, tier, cap, owner_review in cases:
            with self.subTest(summary=item["summary"]):
                result = classify_ticket(config(), item)
                self.assertEqual((tier, cap, owner_review),
                                 (result["tier"], result["round_cap"], result["owner_review_required"]))


class IntakeRecordsAssignment(unittest.TestCase):
    def test_intake_writes_state_and_jira_then_reads_back(self):
        state, jira = {}, FakeJira()
        record = assign_intake(config(), ticket(), state, jira, now=NOW,
                               controller_actor_id="controller-1")
        self.assertEqual(record, state["tier_assignments"]["AWF-100"])
        self.assertIn("tier-2", jira.issues["AWF-100"]["labels"])
        self.assertIn("Round cap: 2", jira.issues["AWF-100"]["comments"][0]["body"])
        self.assertEqual(["read", "labels", "comment", "read"], [row[0] for row in jira.calls])
        self.assertEqual("VERIFIED", record["jira"]["read_back_status"])

    def test_read_back_mismatch_fails_closed_without_state_record(self):
        state, jira = {}, FakeJira()
        jira.mismatch = True
        with self.assertRaisesRegex(ValidationError, "read-back"):
            assign_intake(config(), ticket(), state, jira, now=NOW,
                          controller_actor_id="controller-1")
        self.assertNotIn("tier_assignments", state)

    def test_repeat_intake_cannot_bypass_owner_change_audit(self):
        state, jira = {}, FakeJira()
        assign_intake(config(), ticket(), state, jira, now=NOW, controller_actor_id="controller-1")
        calls = len(jira.calls)
        with self.assertRaisesRegex(ValidationError, "trusted-owner audit"):
            assign_intake(config(), ticket(), state, jira, now=NOW, controller_actor_id="controller-1")
        self.assertEqual(calls, len(jira.calls))

    def test_disabled_jira_creates_only_a_local_provisional_record(self):
        cfg, state = config(), {}
        cfg["jira"]["enabled"] = False
        record = assign_intake(cfg, ticket(), state, None, now=NOW,
                               controller_actor_id="controller-1")
        self.assertEqual("DISABLED", record["jira"]["read_back_status"])
        self.assertEqual(record, state["tier_assignments"]["AWF-100"])


class ProjectStartAssignsAll(unittest.TestCase):
    def test_adoption_and_bootstrap_hook_assign_every_inventory_ticket(self):
        inventory = [ticket("AWF-100"), ticket("AWF-101", summary="Docs", write_paths=["docs/a.md"])]
        state, jira = {}, FakeJira(("AWF-100", "AWF-101"))
        records = assign_project_start(config(), inventory, state, jira, now=NOW,
                                       controller_actor_id="controller-1")
        self.assertEqual({"AWF-100", "AWF-101"}, set(records))

        spec = importlib.util.spec_from_file_location("bootstrap_project", ROOT / "scripts/bootstrap_project.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        second_state, second_jira = {}, FakeJira(("AWF-100", "AWF-101"))
        hooked = module.assign_startup_inventory(config(), inventory, second_state, second_jira,
                                                 now=NOW, controller_actor_id="controller-1")
        self.assertEqual({"AWF-100", "AWF-101"}, set(hooked))


class PrInheritsAssignment(unittest.TestCase):
    def test_pr_body_and_state_bind_the_ticket_assignment(self):
        state, jira = {}, FakeJira()
        assignment = assign_intake(config(), ticket(), state, jira, now=NOW,
                                   controller_actor_id="controller-1")
        body = inherit_pr_assignment(state, "AWF-100", 48, "## Summary\nChange widget.\n")
        self.assertIn("## AWF review assignment", body)
        self.assertIn("Tier: 2", body)
        self.assertIn("Round cap: 2", body)
        binding = state["pr_assignments"]["48"]
        self.assertEqual(assignment["assignment_id"], binding["assignment_id"])
        self.assertEqual(assignment["assignment_sha256"], binding["assignment_sha256"])


class OwnerChangeAudited(unittest.TestCase):
    def test_owner_change_is_visible_in_ticket_pr_and_state(self):
        state, jira = {}, FakeJira()
        old = assign_intake(config(), ticket(), state, jira, now=NOW,
                            controller_actor_id="controller-1")
        inherit_pr_assignment(state, "AWF-100", 48, "## Summary\nChange widget.\n")
        changed = change_assignment(config(), "AWF-100", state, jira, requested_tier=3,
                                    requested_cap=3, requested_by=1001, reason="Scope now changes governance",
                                    now="2026-10-10T09:00:00Z", controller_actor_id="controller-1")
        audit = changed["change_audit"]
        self.assertEqual({"tier": 2, "round_cap": 2}, audit["old"])
        self.assertEqual({"tier": 3, "round_cap": 3}, audit["new"])
        self.assertEqual((1001, "2026-10-10T09:00:00Z", "Scope now changes governance"),
                         (audit["who"], audit["when"], audit["why"]))
        self.assertEqual(old["assignment_id"], changed["previous_assignment_id"])
        self.assertIn("Changed by trusted owner: 1001", jira.issues["AWF-100"]["comments"][-1]["body"])
        self.assertIn("Tier: 3", state["pr_assignments"]["48"]["body"])
        self.assertIn("Old assignment: T2 cap 2", state["pr_assignments"]["48"]["body"])
        self.assertIn("New assignment: T3 cap 3", state["pr_assignments"]["48"]["body"])
        self.assertEqual(changed["assignment_id"], state["pr_assignments"]["48"]["assignment_id"])


class NonOwnerChangeRejected(unittest.TestCase):
    def test_non_owner_change_is_rejected_without_any_write(self):
        state, jira = {}, FakeJira()
        assign_intake(config(), ticket(), state, jira, now=NOW, controller_actor_id="controller-1")
        before_state, before_issues, calls = copy.deepcopy(state), copy.deepcopy(jira.issues), len(jira.calls)
        with self.assertRaisesRegex(ValidationError, "trusted owner"):
            change_assignment(config(), "AWF-100", state, jira, requested_tier=3,
                              requested_cap=3, requested_by=9999, reason="not authorized",
                              now=NOW, controller_actor_id="controller-1")
        self.assertEqual(before_state, state)
        self.assertEqual(before_issues, jira.issues)
        self.assertEqual(calls, len(jira.calls))


class ProtectedPathFloor(unittest.TestCase):
    def test_protected_path_is_at_least_tier_two_and_cannot_be_overridden_below_floor(self):
        item = ticket(summary="Small config label", write_paths=[".agentic/labels.json"],
                      change_kind="small_config")
        result = classify_ticket(config(), item)
        self.assertEqual((2, 2), (result["tier"], result["protected_path_floor"]))
        state, jira = {}, FakeJira()
        assign_intake(config(), item, state, jira, now=NOW, controller_actor_id="controller-1")
        with self.assertRaisesRegex(ValidationError, "protected-path floor"):
            change_assignment(config(), "AWF-100", state, jira, requested_tier=1,
                              requested_cap=1, requested_by=1001, reason="small",
                              now=NOW, controller_actor_id="controller-1")
        rejection = state["assignment_rejections"][-1]
        self.assertEqual("PROTECTED_PATH_FLOOR", rejection["reason_code"])
        self.assertIn(".agentic/labels.json", rejection["reason"])


class ConfigDefaultsAndOverrides(unittest.TestCase):
    def test_existing_risk_tiers_are_the_default_and_override_path(self):
        default = classify_ticket(config(), ticket(summary="Moderate code"))
        self.assertEqual((2, 2), (default["tier"], default["round_cap"]))
        overridden = config()
        overridden["execution"]["risk_tiers"]["tier1_eligible_paths"].append("src/**")
        self.assertEqual(1, classify_ticket(overridden, ticket(summary="Small source fix",
                                                                change_kind="single_file_fix"))["tier"])
        overridden["execution"]["risk_tiers"]["tier3_review"]["max_rounds"] = 1
        self.assertEqual(1, classify_ticket(overridden, ticket(summary="Governance"))["round_cap"])

    def test_existing_project_schema_accepts_review_tiers_and_rejects_invalid_cap(self):
        schema = json.loads((ROOT / ".agentic/schemas/project-config.schema.json").read_text(encoding="utf-8"))
        actual = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
        jsonschema.validate(actual, schema)
        invalid = copy.deepcopy(actual)
        invalid["execution"]["risk_tiers"]["tier3_review"]["max_rounds"] = 4
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)

    def test_assignment_record_schema(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        state, jira = {}, FakeJira()
        record = assign_intake(config(), ticket(), state, jira, now=NOW,
                               controller_actor_id="controller-1")
        contracts.validate("intake-assignment", record)
        for field, value in (("tier", 4), ("round_cap", 4)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                contracts.validate("intake-assignment", {**record, field: value})
        with self.assertRaises(ValidationError):
            contracts.validate("intake-assignment", {**record, "round_cap": 3})


if __name__ == "__main__":
    unittest.main()
