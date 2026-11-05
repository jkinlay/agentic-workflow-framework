"""Jira mutations require observed scope, including at adapter boundaries."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic.jira_lifecycle import write_tier_assignment, TierAssignmentOutcomeError
from agentic.continuous_controller import ContinuousControllerStore, production_jira_lifecycle, production_batch_jira_reconciliation
from agentic.intake_assignment import assign_intake

NOW = "2026-11-03T08:30:00Z"
KEY = "EX-42"


def configuration():
    value = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
    value["jira"]["scope"].update(included_epics=["EX-1"], labels_any=[], components_any=[])
    return value


def snapshot(key=KEY, epic="EX-1"):
    return {"issue_id": key, "project_key": "EX", "epic_ids": [epic], "labels": [],
            "component_ids": [], "summary": "Routine work"}


class Jira:
    def __init__(self, value=None):
        self.issue = {**snapshot(), "labels": ["ready"], "comments": []} if value is None else value
        self.calls = []

    def read_issue(self, key):
        self.calls.append("read")
        return copy.deepcopy(self.issue)

    def set_labels(self, key, labels, *, actor_id):
        self.calls.append("labels")
        self.issue["labels"] = labels

    def add_comment(self, key, body, *, actor_id):
        self.calls.append("comment")
        self.issue["comments"].append({"id": "1", "body": body, "author_id": actor_id})


class JiraMutationScopeTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.temp = tempfile.TemporaryDirectory(prefix="awf-jira-write-scope-")
        self.addCleanup(self.temp.cleanup)
        self.store = ContinuousControllerStore(Path(self.temp.name) / "controller.sqlite3", ["A"])
        self.provider = {"cloud_id": "fixture-cloud", "site": "https://jira.example.invalid",
                         "project_id": "fixture-project", "project_key": "EX",
                         "controller_actor_id": "fixture-controller"}
        self.assignment = {"assignment_id": "assignment-1", "ticket_key": KEY, "tier": 3,
                           "round_cap": 3, "owner_review_required": True, "reasons": ["governance"]}

    def assign(self, jira, config=None):
        return write_tier_assignment(jira, self.assignment, config=self.config if config is None else config,
                                     controller_actor_id="fixture-controller", acting_actor_id="fixture-controller", store=self.store)

    def lifecycle(self, observer, writes):
        return production_jira_lifecycle(self.store, config=self.config, contract={},
            event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
            binding={"issue_id": KEY}, issue_type="LEAF", state="DISPATCHED",
            producer_id="fixture-controller", run_id="00000000-0000-0000-0000-000000000001",
            now=NOW, evidence=[], transition_id="31",
            observe_provider_identity=lambda: copy.deepcopy(self.provider),
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
            observe_issue_policy=observer,
            read_current_status=lambda binding: {"issue_id": KEY, "status_id": "Ready",
                "observed_at": NOW, "jira_provider": self.provider},
            write_transition=lambda record: writes.append(record) or {"operation_id": record["operation_id"],
                "issue_id": KEY, "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": self.provider},
            read_transition=lambda record, operation: {"issue_id": KEY, "status": "In Progress",
                "actor": "fixture-controller", "observed_at": NOW, "jira_provider": self.provider})

    def policy(self, value):
        return {"issue_key": KEY, "issue_type": "LEAF", "snapshot": value, "observed_at": NOW}

    def test_assignment_outside_epic_or_missing_observation_never_mutates(self):
        missing = snapshot(); del missing["epic_ids"]
        cases = [snapshot(epic="EX-2"), missing, {**snapshot(), "issue_id": "EX-99"},
                 {**snapshot(), "project_key": "OTHER"}, {**snapshot(), "epic_ids": "EX-1"}]
        for value in cases:
            with self.subTest(value=value):
                jira = Jira({**value, "comments": []})
                before = copy.deepcopy(jira.issue)
                with self.assertRaises(ValidationError):
                    self.assign(jira)
                self.assertEqual(["read"], jira.calls)
                self.assertEqual(before, jira.issue)

    def test_incomplete_or_whitespace_scope_never_mutates(self):
        cases = []
        for field in configuration()["jira"]["scope"]:
            config = configuration()
            del config["jira"]["scope"][field]
            cases.append(config)
        for field, value in [("ownership_required", False), ("ownership_required", 1),
                             ("additional_jql", None), ("additional_jql", False),
                             ("additional_jql", []), ("additional_jql", " "),
                             ("included_epics", ["EX-1", "EX-1"]),
                             ("included_epics", [" "]), ("labels_any", [" "]),
                             ("components_any", [" "]), ("selector_mode", "invalid")]:
            config = configuration()
            config["jira"]["scope"][field] = value
            cases.append(config)
        for config in cases:
            with self.subTest(scope=config["jira"]["scope"]):
                jira = Jira()
                with self.assertRaises(ValidationError):
                    self.assign(jira, config)
                self.assertEqual(["read"], jira.calls)
        for field in ("epic_ids", "labels", "component_ids"):
            for values in ([" "], ["duplicate", "duplicate"]):
                jira = Jira()
                jira.issue[field] = values
                with self.subTest(observation=field, values=values), self.assertRaises(ValidationError):
                    self.assign(jira)
                self.assertEqual(["read"], jira.calls)

    def test_assignment_scope_change_after_label_prevents_comment(self):
        jira = Jira()
        original = jira.set_labels
        def move(key, labels, *, actor_id):
            original(key, labels, actor_id=actor_id)
            jira.issue["epic_ids"] = ["EX-2"]
        jira.set_labels = move
        with self.assertRaises(TierAssignmentOutcomeError) as raised:
            self.assign(jira)
        self.assertNotIn("comment", jira.calls)
        self.assertTrue(raised.exception.outcome["writes_stopped"])

    def test_in_scope_assignment_retains_exact_readback(self):
        jira = Jira()
        self.assertEqual("VERIFIED", self.assign(jira)["read_back_status"])
        self.assertEqual(["read", "labels", "read", "comment", "read"], jira.calls)

    def test_intake_rejects_outside_epic_without_assignment_or_mutation(self):
        jira, state = Jira({**snapshot(epic="EX-2"), "labels": [], "comments": []}), {}
        ticket = {"key": KEY, "summary": "Routine work", "description": "",
                  "write_paths": ["src/widget.py"], "risk_flags": []}
        with self.assertRaises(ValidationError):
            assign_intake(self.config, ticket, state, jira, now=NOW, controller_actor_id="fixture-controller", store=self.store)
        self.assertFalse(any(call in {"labels", "comment"} for call in jira.calls))
        self.assertNotIn(KEY, state.get("tier_assignments", {}))

    def test_lifecycle_missing_or_unrelated_scope_never_calls_write(self):
        for observer in [None, lambda key: self.policy(snapshot(epic="EX-2")),
                         lambda key: self.policy({}), lambda key: {**self.policy(snapshot()), "issue_key": "EX-99"}]:
            with self.subTest(observer=observer):
                writes = []
                result = self.lifecycle(observer, writes)
                self.assertEqual("SCOPE_REFUSED", result["status"])
                self.assertEqual([], writes)
                self.assertEqual([], self.store.jira_operations(KEY))

    def test_lifecycle_rechecks_scope_immediately_before_mutation(self):
        observations = iter([snapshot(), snapshot(epic="EX-2")])
        writes = []
        result = self.lifecycle(lambda key: self.policy(next(observations)), writes)
        self.assertEqual("SCOPE_REFUSED", result["status"])
        self.assertEqual([], writes)

    def test_in_scope_lifecycle_still_writes_and_reads_back(self):
        writes = []
        result = self.lifecycle(lambda key: self.policy(snapshot()), writes)
        self.assertEqual(1, len(writes))
        self.assertEqual("SUCCEEDED", result["record"]["status"])

    def test_batch_closeout_cannot_write_outside_epic(self):
        writes = []
        batch = {"batch_id": "batch-1", "pr_number": 44, "merge_commit_sha": "d" * 40,
                 "constituents": [{"binding": {"issue_id": KEY}, "pr_number": 2,
                    "reviewed_head_sha": "a" * 40, "paths": ["src/widget.py"],
                    "closeout_kind": "merged-via-batch", "batch_id": "batch-1", "merge_commit_sha": "d" * 40}]}
        result = production_batch_jira_reconciliation(self.store, config=self.config, role="controller",
            batch=batch, observed_merge={"batch_id": "batch-1", "target_branch": "main",
                "merge_commit_sha": "d" * 40, "observed_at": NOW}, now=NOW,
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
            policy_context={KEY: {"snapshot": snapshot(epic="EX-2")}},
            read_current_status=lambda binding: {"issue_id": KEY, "status_id": "Ready",
                "observed_at": NOW, "jira_provider": self.provider},
            write_transition=lambda record: writes.append(record),
            read_transition=lambda record, operation: self.fail("no write means no readback"))
        self.assertEqual([], writes)
        self.assertFalse(result["complete"])


    def test_reference_adapter_checks_epic_scope_before_http_post(self):
        import os
        import runpy
        from unittest.mock import patch
        from test_reference_controller_adapter import _base_config, FakeHttp, FakeClock, FakeRunner
        for parent in ("EX-1", "EX-2"):
            with self.subTest(parent=parent), tempfile.TemporaryDirectory(prefix="awf-scope-http-") as temporary:
                root = Path(temporary)
                config = _base_config(root)
                accepted = config["observations"]["accepted_project_config"]
                project = json.loads(Path(accepted["path"]).read_text())
                project["jira"]["scope"].update(included_epics=["EX-1"], labels_any=[], components_any=[])
                raw = json.dumps(project).encode()
                Path(accepted["path"]).write_bytes(raw)
                from agentic.canonical import sha256
                accepted["sha256"] = sha256(raw)

                class Http(FakeHttp):
                    def request(self, method, url, **kwargs):
                        status, body = super().request(method, url, **kwargs)
                        if "?fields=project,parent,labels,components,issuetype,summary" in url:
                            value = json.loads(body)
                            if value["key"] == KEY:
                                value["fields"]["parent"] = {"key": parent}
                            else:
                                value["fields"]["issuetype"] = {"name": "Epic", "hierarchyLevel": 1}
                            body = json.dumps(value)
                        return status, body

                http = Http()
                adapters = runpy.run_path(str(ROOT / ".agentic/adapters/reference_controller_adapter.py"))["build_adapters"](
                    {**config, "_http_transport": http, "_runner": FakeRunner(), "_clock": FakeClock()})
                provider = {"cloud_id": "cloud-1", "site": "https://jira.example.invalid",
                            "project_id": "project-1", "project_key": "EX", "controller_actor_id": "controller"}
                record = {"operation_id": "scope-operation", "binding": {"issue_id": KEY},
                          "transition_id": "3", "jira_provider": provider, "producer_id": "controller"}
                with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "fixture-token"}):
                    if parent == "EX-1":
                        adapters["write_transition"](record)
                    else:
                        with self.assertRaises(ValidationError):
                            adapters["write_transition"](record)
                posts = [call for call in http.calls if call[0] == "POST"]
                self.assertEqual(1 if parent == "EX-1" else 0, len(posts))


if __name__ == "__main__":
    unittest.main()
