"""Permanent regressions for AWF243-R1 through R4 (Tier 3, FULL)."""
import copy
import unittest
from unittest.mock import patch

import test_jira_mutation_scope as scope_fixture
from test_jira_mutation_scope import Jira, snapshot, KEY, NOW
from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.jira_lifecycle import TierAssignmentOutcomeError
from agentic.continuous_controller import ContinuousControllerStore, production_jira_lifecycle, post_merge_jira_progress


class AssignmentRegressions(unittest.TestCase):
    setUp = scope_fixture.JiraMutationScopeTests.setUp
    assign = scope_fixture.JiraMutationScopeTests.assign
    policy = scope_fixture.JiraMutationScopeTests.policy

    def done(self, **overrides):
        def write(record):
            self.writes.append(record)
            return {"operation_id": record["operation_id"], "issue_id": KEY,
                    "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": self.provider}
        args = dict(config=self.config, contract={"owner_closure_required": False},
            event="JIRA_RECONCILED", facts={"merge_confirmed": True, "closeout_valid": True},
            binding={"issue_id": KEY}, issue_type="LEAF", state="MERGED",
            producer_id="fixture-controller", run_id="00000000-0000-0000-0000-000000000001",
            now=NOW, evidence=[], transition_id="31", merge_result_id="merge-1",
            observe_provider_identity=lambda: copy.deepcopy(self.provider),
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
            observe_issue_policy=lambda key: self.policy(snapshot()),
            read_current_status=lambda binding: {"issue_id": KEY, "status_id": "In Review",
                "observed_at": NOW, "jira_provider": self.provider},
            write_transition=write,
            read_transition=lambda record, operation: {"issue_id": KEY, "status": "Done",
                "actor": "fixture-controller", "observed_at": NOW, "jira_provider": self.provider})
        args.update(overrides)
        return production_jira_lifecycle(self.store, **args)

    def progress(self, **overrides):
        binding = {"cloud_id": self.provider["cloud_id"], "project_id": self.provider["project_id"],
                   "actor_id": self.provider["controller_actor_id"]}
        rows = self.store.jira_operations(KEY)
        closeout = {"binding": {"issue_id": KEY},
                    "jira_transition_record_id": rows[0]["record"]["record_id"] if rows else "missing"}
        scope = "project = EX"
        args = dict(jira_enabled=True, merged_ticket=KEY, scope=scope, observed_at=NOW,
            jira_binding=binding, count_gate={"closeout_record": closeout}, store=self.store,
            config=self.config, observe_provider_identity=lambda: copy.deepcopy(self.provider),
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
            observe_issue_policy=lambda key: self.policy(snapshot()),
            read_current_status=lambda binding: {"issue_id": KEY, "status_id": "Done",
                "observed_at": NOW, "jira_provider": self.provider},
            reconcile_merged_ticket=lambda key: self.fail("legacy mutation callback must never run"),
            fetch_scope_page=lambda scope, cursor: {"items": [], "next_cursor": None,
                "complete": True, "snapshot_id": "snapshot-1", "observed_at": NOW,
                "scope_sha256": fingerprint("jira-progress-scope", {
                    "scope": scope, "binding": binding, "include_epics": False})})
        args.update(overrides)
        with patch("agentic.closeout.require_jira_reconciliation_before_counts",
                   return_value={"status": "PASS", "count_allowed": True, "ticket": KEY}):
            return post_merge_jira_progress(**args)

    def test_r1_completed_lifecycle_is_observed_without_second_write(self):
        self.writes = []
        self.assertEqual("SUCCEEDED", self.done()["record"]["status"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r1_unknown_survives_restart_without_second_write(self):
        self.writes = []
        def unknown(record):
            self.writes.append(record)
            raise TimeoutError("unknown provider outcome")
        self.assertEqual("UNKNOWN", self.done(write_transition=unknown)["record"]["status"])
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        for _ in range(2):
            self.assertEqual("UNOBSERVED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))
        self.assertEqual("UNKNOWN", self.store.jira_operations(KEY)[0]["status"])

    def test_r1_scope_hold_observers_and_journal_are_required(self):
        self.writes = []
        self.done()
        cases = [dict(observe_issue_policy=None), dict(observe_issue_identity=None),
                 dict(observe_provider_identity=None), dict(read_current_status=None), dict(store=None),
                 dict(observe_issue_policy=lambda key: self.policy(snapshot(epic="EX-2"))),
                 dict(observe_issue_policy=lambda key: self.policy({**snapshot(), "summary": "Release publication qualification"})),
                 dict(observe_issue_policy=lambda key: self.policy({**snapshot(), "labels": ["release"]})),
                 dict(count_gate={"closeout_record": {}})]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual("UNOBSERVED", self.progress(**args)["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r1_cli_requires_observers_before_any_progress_callback(self):
        import importlib.util
        from pathlib import Path
        root = scope_fixture.ROOT
        spec = importlib.util.spec_from_file_location("awf243_controller_cli", root / ".agentic/scripts/continuous_controller.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        names = {"observe_provider_identity", "observe_issue_identity", "observe_issue_policy",
                 "read_current_status", "fetch_scope_page"}
        argv = ["--state", str(self.store.path), "--stream", "A", "--worktree-root", str(root),
                "merge-observed", "--lifecycle-state", "MERGING", "--lifecycle-facts", "facts.json",
                "--jira-progress", "progress.json", "--repository-root", str(root)]
        for missing in names:
            adapters = {name: lambda *args: self.fail("missing capability must precede callback")
                        for name in names - {missing}}
            with self.subTest(missing=missing), patch.object(cli, "load", return_value=self.config), \
                 patch.object(cli, "ContinuousControllerStore", return_value=self.store), \
                 patch.object(cli, "_load_reviewed_adapters", return_value=adapters):
                with self.assertRaisesRegex(ValidationError, missing):
                    cli.main(argv)

    def test_r1_post_reconciliation_mismatch_is_durably_stopped(self):
        self.writes = []
        self.done()
        changed = lambda binding: {"issue_id": KEY, "status_id": "In Review",
                                  "observed_at": NOW, "jira_provider": self.provider}
        self.assertEqual("UNOBSERVED", self.progress(read_current_status=changed)["jira_state"])
        self.assertEqual("FAILED", self.store.jira_operations(KEY)[0]["status"])
        self.assertEqual("UNOBSERVED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r2_hold_appearing_at_last_scope_observation_prevents_write(self):
        self.writes = []
        values = iter([snapshot(), {**snapshot(), "labels": ["release"]}])
        result = self.done(observe_issue_policy=lambda key: self.policy(next(values)))
        self.assertFalse(result["planned"])
        self.assertEqual([], self.writes)

    def test_r1_legacy_reconciliation_never_runs_before_admission(self):
        calls = []
        with patch("agentic.closeout.require_jira_reconciliation_before_counts",
                   side_effect=ValueError("invalid closeout")):
            result = post_merge_jira_progress(jira_enabled=True, merged_ticket="OTHER-42",
                scope="project = EX", observed_at=NOW,
                jira_binding={"cloud_id": "fixture-cloud", "project_id": "fixture-project",
                              "actor_id": "fixture-controller"}, count_gate={},
                reconcile_merged_ticket=lambda key: calls.append(key),
                fetch_scope_page=lambda *args: self.fail("unadmitted count"))
        self.assertEqual([], calls)
        self.assertEqual("UNOBSERVED", result["jira_state"])

    def test_r1_reference_legacy_reconciliation_cannot_post(self):
        import os
        import runpy
        import tempfile
        from pathlib import Path
        from test_reference_controller_adapter import _base_config, FakeHttp, FakeClock, FakeRunner, ROOT
        with tempfile.TemporaryDirectory(prefix="awf243-adapter-") as temporary:
            config = _base_config(Path(temporary))
            http = FakeHttp()
            adapters = runpy.run_path(str(ROOT / ".agentic/adapters/reference_controller_adapter.py"))["build_adapters"](
                {**config, "_http_transport": http, "_runner": FakeRunner(), "_clock": FakeClock()})
            with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "fixture-token"}):
                for _ in range(2):
                    with self.assertRaises(ValidationError):
                        adapters["reconcile_merged_ticket"](KEY)
            self.assertFalse([call for call in http.calls if call[0] == "POST"])

    def test_r3_ineffective_label_write_stops_before_comment(self):
        jira = Jira()
        jira.set_labels = lambda *args, **kwargs: jira.calls.append("labels")
        with self.assertRaises(TierAssignmentOutcomeError) as raised:
            self.assign(jira)
        self.assertEqual(["read", "labels", "read"], jira.calls)
        self.assertEqual("FAILED", raised.exception.outcome["status"])

    def test_r4_all_assignment_capabilities_precede_mutation(self):
        for name in ("read_issue", "set_labels", "add_comment"):
            with self.subTest(capability=name):
                jira = Jira()
                setattr(jira, name, None)
                with self.assertRaises(ValidationError):
                    self.assign(jira)
                self.assertFalse(set(jira.calls) & {"labels", "comment"})

    def test_r2_observed_summary_or_labels_hold_done(self):
        for summary, labels in (("Release publication qualification", []),
                                ("Routine work", ["release"])):
            with self.subTest(summary=summary, labels=labels):
                writes = []
                policy = self.policy({**snapshot(), "summary": summary, "labels": labels})
                result = production_jira_lifecycle(self.store, config=self.config,
                    contract={"owner_closure_required": False}, event="JIRA_RECONCILED",
                    facts={"merge_confirmed": True, "closeout_valid": True},
                    binding={"issue_id": KEY}, issue_type="LEAF", state="MERGED",
                    producer_id="fixture-controller", run_id="00000000-0000-0000-0000-000000000001",
                    now=NOW, evidence=[], transition_id="31", merge_result_id="merge-1",
                    observe_provider_identity=lambda: copy.deepcopy(self.provider),
                    observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
                    observe_issue_policy=lambda key: policy,
                    read_current_status=lambda binding: {"issue_id": KEY, "status_id": "In Review",
                        "observed_at": NOW, "jira_provider": self.provider},
                    write_transition=lambda record: writes.append(record) or {
                        "operation_id": record["operation_id"], "issue_id": KEY,
                        "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": self.provider},
                    read_transition=lambda record, operation: {"issue_id": KEY, "status": "Done",
                        "actor": "fixture-controller", "observed_at": NOW, "jira_provider": self.provider})
                self.assertEqual([], writes)
                self.assertFalse(result["planned"])


if __name__ == "__main__":
    unittest.main()
