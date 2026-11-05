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
        self._assert_done_stop_survives_lifecycle_restart()

    def test_r1_status_only_lifecycle_cannot_clear_unknown_done_write(self):
        self.writes = []
        def unknown(record):
            self.writes.append(record)
            raise TimeoutError("unknown Done outcome")
        self.assertEqual("UNKNOWN", self.done(write_transition=unknown)["record"]["status"])
        self._assert_done_stop_survives_lifecycle_restart()

    def test_r1_progress_timeout_preserves_success_across_reopen(self):
        self.writes = []
        self.assertEqual("SUCCEEDED", self.done()["record"]["status"])
        before = self.store.jira_operations(KEY)[0]
        for value in (TimeoutError(), OSError(), None, {}):
            def unknown(binding):
                if isinstance(value, Exception):
                    raise value
                return value
            with self.subTest(value=value):
                self.store = ContinuousControllerStore(self.store.path, ["A"])
                self.assertEqual("UNOBSERVED", self.progress(read_current_status=unknown)["jira_state"])
                self.assertEqual(before, self.store.jira_operations(KEY)[0])
                self.assertEqual(1, len(self.writes))
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        self.assertEqual("SUCCEEDED", self.store.jira_operations(KEY)[0]["status"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def _assert_done_stop_survives_lifecycle_restart(self):
        for event, status in (("JIRA_RECONCILED", "Done"), ("WORKER_STARTED", "In Review"),
                              ("JIRA_RECONCILED", "In Review"), ("JIRA_RECONCILED", "Done")):
            self.store = ContinuousControllerStore(self.store.path, ["A"])
            result = self.done(event=event,
                facts={"merge_confirmed": True, "closeout_valid": True,
                       "run_registered": True, "worktree_verified": True},
                read_current_status=lambda binding, status=status: {
                "issue_id": KEY, "status_id": status, "observed_at": NOW,
                "jira_provider": self.provider},
                read_transition=lambda record, operation, status=status: {
                    "issue_id": KEY, "status_id": status, "observed_at": NOW,
                    "jira_provider": self.provider})
            self.assertTrue(result.get("writes_stopped"), result)
            self.assertFalse(result["planned"])
            self.assertEqual("UNKNOWN", self.store.jira_operations(KEY)[0]["status"])
            self.assertEqual(1, len(self.writes))

    def test_revised_candidate_reconciles_after_worker_started(self):
        self.writes = []
        candidate_a = self.candidate_binding()
        candidate_b = {**candidate_a, "candidate_id": "b" * 64}
        started = self.done(binding=candidate_a, event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True}, state="DISPATCHED",
            read_transition=lambda record, operation: {"issue_id": KEY, "status": "In Progress",
                "actor": "fixture-controller", "observed_at": NOW, "jira_provider": self.provider})
        self.assertEqual("SUCCEEDED", started["record"]["status"])
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        reconciled = self.done(binding=candidate_b)
        self.assertEqual("SUCCEEDED", reconciled["record"]["status"])
        self.assertEqual([candidate_a, candidate_b], [row["binding"] for row in self.writes])
        for provider_field, config_field in (("cloud_id", "cloud_id"), ("site", "site"),
                ("project_id", "provider_project_id"), ("controller_actor_id", "controller_actor_id")):
            value = "https://other.example.invalid" if provider_field == "site" else "other-identity"
            with self.subTest(identity=provider_field), patch.dict(self.provider, {provider_field: value}), \
                 patch.dict(self.config["jira"], {config_field: value}):
                with self.assertRaisesRegex(ValidationError, "different connector/site/project/actor"):
                    self.done(binding=candidate_b, producer_id=self.provider["controller_actor_id"])
        self.assertEqual(2, len(self.writes))

    def candidate_binding(self):
        return {"issue_id": KEY, "project_id": self.config["project"]["id"],
                "repository_id": self.config["github"]["repository_id"],
                "requirements_hash": "c" * 64, "contract_hash": "d" * 64,
                "policy_hash": "e" * 64, "candidate_id": "a" * 64}

    def unknown_readback(self):
        self.writes = []
        def timeout(record, operation):
            raise TimeoutError("write succeeded; readback timed out")
        self.assertEqual("UNKNOWN", self.done(read_transition=timeout)["record"]["status"])
        self.assertEqual(1, len(self.writes))
        self.store = ContinuousControllerStore(self.store.path, ["A"])

    def transition_evidence(self, record, operation):
        self.assertEqual(record["operation_id"], operation["operation_id"])
        self.assertEqual(KEY, operation["issue_id"])
        return {"issue_id": KEY, "status": record["to_status_id"],
                "actor": self.provider["controller_actor_id"], "observed_at": NOW,
                "jira_provider": copy.deepcopy(self.provider)}

    def test_r2_unknown_recovers_through_original_transition_observer_without_write(self):
        self.unknown_readback()
        reads = []
        def observe(record, operation):
            reads.append((copy.deepcopy(record), copy.deepcopy(operation)))
            return self.transition_evidence(record, operation)
        recovered = self.done(read_transition=observe)
        self.assertFalse(recovered["planned"])
        self.assertEqual("SUCCEEDED", recovered["record"]["status"])
        self.assertEqual(1, len(reads))
        self.assertEqual(self.writes[0], reads[0][0])
        self.assertEqual("SUCCEEDED", self.store.jira_operations(KEY)[0]["status"])
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        self.assertFalse(self.done()["planned"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r2_absent_incomplete_failed_and_status_only_evidence_can_be_read_again(self):
        self.unknown_readback()
        reads = []
        for value in (None, {}, {"status": "Done"}, TimeoutError(), OSError()):
            def observe(record, operation):
                reads.append(record["operation_id"])
                if isinstance(value, Exception):
                    raise value
                return value
            for event in ("JIRA_RECONCILED", "WORKER_STARTED"):
                with self.subTest(value=value, event=event):
                    self.store = ContinuousControllerStore(self.store.path, ["A"])
                    result = self.done(event=event, read_transition=observe)
                    self.assertTrue(result["writes_stopped"])
                    self.assertFalse(result["planned"])
                    self.assertEqual("UNKNOWN", self.store.jira_operations(KEY)[0]["status"])
                    self.assertEqual("UNOBSERVED", self.progress()["jira_state"])
                    self.assertEqual(1, len(self.writes))
        self.assertEqual(10, len(reads))
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        self.done(read_transition=self.transition_evidence)
        self.assertEqual("SUCCEEDED", self.store.jira_operations(KEY)[0]["status"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r2_legacy_unknown_without_receipt_reads_original_operation_after_reopen(self):
        self.unknown_readback()
        row = self.store.jira_operations(KEY)[0]
        with self.store.transaction() as db:
            db.execute("UPDATE controller_jira_operation SET operation_json=NULL WHERE operation_id=?",
                       (row["operation_id"],))
        reads = []
        def observe(record, operation):
            reads.append((record, operation))
            self.assertEqual(row["record"], record)
            self.assertEqual(record["created_at"], operation["observed_at"])
            return self.transition_evidence(record, operation)
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        self.assertEqual("SUCCEEDED", self.done(read_transition=observe)["record"]["status"])
        self.assertEqual(1, len(reads))
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r2_foreign_actor_target_issue_provider_or_time_stays_unknown(self):
        self.unknown_readback()
        cases = [("actor", "other-actor"), ("status", "In Review"), ("issue_id", "EX-99"),
                 ("jira_provider", {**self.provider, "project_id": "foreign"}),
                 ("observed_at", "2026-11-03T08:29:59Z")]
        for field, value in cases:
            reads = []
            def observe(record, operation):
                reads.append(record["operation_id"])
                return {**self.transition_evidence(record, operation), field: value}
            with self.subTest(field=field):
                self.store = ContinuousControllerStore(self.store.path, ["A"])
                self.assertTrue(self.done(read_transition=observe)["writes_stopped"])
                self.assertEqual(1, len(reads))
                self.assertEqual("UNKNOWN", self.store.jira_operations(KEY)[0]["status"])
                self.assertEqual("UNOBSERVED", self.progress()["jira_state"])
                self.assertEqual(1, len(self.writes))

    def test_r2_mismatched_operation_receipt_never_recovers(self):
        import json
        self.unknown_readback()
        row = self.store.jira_operations(KEY)[0]
        operation = {"operation_id": "other-operation", "issue_id": KEY,
                     "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": self.provider}
        with self.store.transaction() as db:
            db.execute("UPDATE controller_jira_operation SET operation_json=? WHERE operation_id=?",
                       (json.dumps(operation), row["operation_id"]))
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        reads = []
        def observe(record, operation):
            reads.append(operation)
            return self.transition_evidence(record, operation)
        self.assertTrue(self.done(read_transition=observe)["writes_stopped"])
        self.assertEqual([], reads)
        self.assertEqual("UNKNOWN", self.store.jira_operations(KEY)[0]["status"])
        self.assertEqual("UNOBSERVED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))

    def test_r2_reference_observer_refuses_foreign_target_actor_and_outside_window(self):
        import json
        import os
        import runpy
        import tempfile
        from pathlib import Path
        from test_reference_controller_adapter import _base_config, ProgressHttp, FakeClock, FakeRunner, NOW as ADAPTER_NOW
        with tempfile.TemporaryDirectory(prefix="awf243-recovery-") as temporary:
            http = ProgressHttp()
            adapters = runpy.run_path(str(scope_fixture.ROOT / ".agentic/adapters/reference_controller_adapter.py"))["build_adapters"](
                {**_base_config(Path(temporary)), "_http_transport": http,
                 "_runner": FakeRunner(), "_clock": FakeClock()})
            self.config["jira"].update(cloud_id="cloud-1", provider_project_id="project-1",
                                       controller_actor_id="controller")
            self.config["jira"]["scope"].update(included_epics=[], labels_any=["awf-fixture"])
            args = {name: adapters[name] for name in ("observe_provider_identity", "observe_issue_identity",
                    "observe_issue_policy", "read_current_status", "write_transition", "read_transition")}
            def timeout(record, operation):
                raise TimeoutError("readback timed out after Done write")
            def invoke(**overrides):
                return self.done(binding={"issue_id": "EX-100"}, now=ADAPTER_NOW,
                                 producer_id="controller", **{**args, **overrides})
            original_request = http.request
            cases = [("absent", None), ("incomplete", None), ("actor", "other-actor"),
                     ("target", "In Review"), ("time", "2020-01-01T00:00:00Z"),
                     ("time", "2099-01-01T00:00:00Z")]
            with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "fixture-token"}):
                self.assertEqual("UNKNOWN", invoke(read_transition=timeout)["record"]["status"])
                self.assertEqual(1, sum(call[0] == "POST" for call in http.calls))
                for field, value in cases:
                    def changed(method, url, **kwargs):
                        status, body = original_request(method, url, **kwargs)
                        if "expand=changelog" in url:
                            payload = json.loads(body)
                            history = payload["changelog"]["histories"][0]
                            if field == "absent":
                                payload["changelog"]["histories"] = []
                            elif field == "incomplete":
                                history.pop("author")
                            elif field == "actor":
                                history["author"]["accountId"] = value
                            elif field == "target":
                                history["items"][0]["to"] = value
                            else:
                                history["created"] = value
                            body = json.dumps(payload)
                        return status, body
                    with self.subTest(field=field, value=value), patch.object(http, "request", side_effect=changed):
                        self.store = ContinuousControllerStore(self.store.path, ["A"])
                        self.assertTrue(invoke()["writes_stopped"])
                        self.assertEqual("UNKNOWN", self.store.jira_operations("EX-100")[0]["status"])
                        self.assertEqual(1, sum(call[0] == "POST" for call in http.calls))
                self.store = ContinuousControllerStore(self.store.path, ["A"])
                self.assertEqual("SUCCEEDED", invoke()["record"]["status"])
                self.assertEqual(1, sum(call[0] == "POST" for call in http.calls))

    def test_r2_project_and_repository_identity_changes_or_missing_fields_refuse_zero_writes(self):
        self.writes = []
        binding = self.candidate_binding()
        self.assertEqual("SUCCEEDED", self.done(binding=binding, event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True}, state="DISPATCHED",
            read_transition=lambda record, operation: {**self.transition_evidence(record, operation),
                                                       "status": "In Progress"})["record"]["status"])
        self.assertEqual(1, len(self.writes))
        self.writes.clear()
        cases = [{**binding, "project_id": "00000000-0000-0000-0000-000000000002"},
                 {**binding, "repository_id": binding["repository_id"] + 1},
                 {**binding, "unrecognized_identity": "foreign"}]
        cases.extend({**binding, field: "f" * 64}
                     for field in ("requirements_hash", "contract_hash", "policy_hash"))
        cases.extend({key: value for key, value in binding.items() if key != field}
                     for field in binding)
        for revised in cases:
            with self.subTest(binding=revised):
                self.store = ContinuousControllerStore(self.store.path, ["A"])
                with self.assertRaises(ValidationError):
                    self.done(binding=revised)
                self.assertEqual(0, len(self.writes))

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
