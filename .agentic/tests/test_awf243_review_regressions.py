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

    def counted_done(self, calls, **overrides):
        current = overrides.pop("current", "In Review")
        observer = overrides.pop("observer", self.transition_evidence)
        def before(binding):
            calls.append("status")
            return {"issue_id": KEY, "status_id": current, "observed_at": NOW,
                    "jira_provider": copy.deepcopy(self.provider)}
        def after(record, operation):
            calls.append("transition")
            return observer(record, operation)
        return self.done(read_current_status=before, read_transition=after, **overrides)

    def test_pre3_progress_malformed_stale_and_foreign_reads_preserve_success_then_retry(self):
        self.writes = []
        calls = []
        self.counted_done(calls)
        before = self.store.jira_operations(KEY)[0]
        valid = {"issue_id": KEY, "status_id": "Done", "observed_at": NOW,
                 "jira_provider": self.provider}
        variants = [{"status_id": "Done"}, {**valid, "observed_at": "2026-11-03T08:29:59Z"},
                    {**valid, "issue_id": "EX-99"}, {**valid, "observed_at": "bad"}]
        for value in variants:
            def read(binding):
                calls.append("progress")
                return value
            with self.subTest(value=value):
                self.assertEqual("UNOBSERVED", self.progress(read_current_status=read)["jira_state"])
                self.assertEqual(before, self.store.jira_operations(KEY)[0])
                self.assertEqual(1, len(self.writes))
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        def read(binding):
            calls.append("progress")
            return valid
        self.assertEqual("COUNTED", self.progress(read_current_status=read)["jira_state"])
        self.assertEqual(["status", "transition"] + ["progress"] * 5, calls)
        self.assertEqual(1, len(self.writes))

    def test_pre3_every_event_requires_transition_evidence_before_next_event(self):
        from pathlib import Path
        events = [("WORKER_STARTED", {"run_registered": True, "worktree_verified": True}, "DISPATCHED"),
                  ("PR_READY", {"draft_cleared": True}, "PR_DRAFT"),
                  ("OWNER_CHANGES_REQUESTED", {"external_event_recorded": True, "prior_evidence_invalidated": True}, "READY_FOR_CRITIC"),
                  ("HEAD_CHANGED", {"external_event_recorded": True, "prior_evidence_invalidated": True}, "READY_FOR_CRITIC"),
                  ("JIRA_RECONCILED", {"merge_confirmed": True, "closeout_valid": True}, "MERGED")]
        for index, (event, facts, state) in enumerate(events):
            with self.subTest(event=event):
                self.store = ContinuousControllerStore(Path(self.temp.name) / f"event-{index}.sqlite", ["A"])
                self.writes, calls = [], []
                def timeout(record, operation):
                    raise TimeoutError()
                self.assertEqual("UNKNOWN", self.counted_done(calls, event=event, facts=facts,
                    state=state, current="Ready", observer=timeout)["record"]["status"])
                original = self.store.jira_operations(KEY)[0]
                for value in (None, {}, {"status": original["record"]["to_status_id"]},
                              {"issue_id": KEY, "status": original["record"]["to_status_id"],
                               "actor": self.provider["controller_actor_id"], "jira_provider": self.provider,
                               "observed_at": "2026-11-03T08:29:59Z"}):
                    self.store = ContinuousControllerStore(self.store.path, ["A"])
                    result = self.counted_done(calls, event="PR_READY", facts={"draft_cleared": True},
                        state="PR_DRAFT", current=original["record"]["to_status_id"], observer=lambda r, o: value)
                    self.assertTrue(result["writes_stopped"])
                    self.assertEqual(original, self.store.jira_operations(KEY)[0])
                    self.assertEqual(1, len(self.writes))
                result = self.counted_done(calls, event="PR_READY", facts={"draft_cleared": True},
                    state="PR_DRAFT", current=original["record"]["to_status_id"])
                self.assertEqual("SUCCEEDED", self.store.jira_operations(KEY)[0]["status"])
                self.assertEqual(1 if event == "PR_READY" else 2, len(self.writes))
                self.assertEqual(6, calls.count("status"))
                self.assertEqual(6 if event == "PR_READY" else 7, calls.count("transition"))

    def test_pre3_issue_only_identity_is_historical_and_legacy_recovery_is_additive(self):
        from agentic.canonical import loads
        self.writes, calls = [], []
        self.counted_done(calls, event="WORKER_STARTED", facts={"run_registered": True,
            "worktree_verified": True}, state="DISPATCHED", current="Ready")
        row = self.store.jira_operations(KEY)[0]
        for section, field, value in (("project", "id", "other-project"),
                                      ("github", "repository_id", 999999)):
            with self.subTest(field=field), patch.dict(self.config[section], {field: value}):
                self.store = ContinuousControllerStore(self.store.path, ["A"])
                with self.assertRaisesRegex(ValidationError, "different project/repository/issue"):
                    self.counted_done(calls)
                self.assertEqual(1, len(self.writes))
        with self.store.transaction() as db:
            pin = db.execute("SELECT value FROM controller_meta WHERE key=?", ("jira-identity:" + row["operation_id"],)).fetchone()
            self.assertIsNotNone(pin)
            historical = loads(pin["value"])
            db.execute("DELETE FROM controller_meta WHERE key=?", ("jira-identity:" + row["operation_id"],))
        self.store = ContinuousControllerStore(self.store.path, ["A"])
        with self.assertRaisesRegex(ValidationError, "Historical Jira identity.*observe_jira_operation_identity"):
            self.counted_done(calls)
        self.assertEqual(row, self.store.jira_operations(KEY)[0])
        history_reads = []
        def history(record):
            history_reads.append(record["operation_id"])
            return historical
        self.counted_done(calls, observe_jira_operation_identity=history)
        self.assertEqual([row["operation_id"]], history_reads)
        self.assertEqual(2, len(self.writes))
        self.assertEqual(5, calls.count("status"))
        self.assertEqual(2, calls.count("transition"))

    def test_pre3_batch_closeout_defers_original_unknown_then_completes_after_evidence(self):
        from test_merge_train_controller import OperationalControllerTests, JiraSpy, NOW as BATCH_NOW
        fixture = OperationalControllerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        spy = JiraSpy()
        args = fixture.operational_inputs(spy)
        calls = []
        def before(binding):
            calls.append("status")
            return spy.read_current_status(binding)
        def after(record, operation):
            calls.append("transition")
            raise TimeoutError()
        lifecycle = dict(config=args["config"], contract={}, event="JIRA_RECONCILED",
            facts={"merge_confirmed": True, "closeout_valid": True}, binding={"issue_id": "AWF-1"},
            issue_type="LEAF", state="MERGED", producer_id="controller-1", run_id="fixture",
            now=BATCH_NOW, evidence=[], transition_id="31", merge_result_id="merge-1",
            **{name: args[name] for name in ("observe_provider_identity", "observe_issue_identity", "observe_issue_policy")},
            read_current_status=before, write_transition=spy.write_transition, read_transition=after)
        self.assertEqual("UNKNOWN", production_jira_lifecycle(fixture.store, **lifecycle)["record"]["status"])
        result = fixture.controller.production_batch_closeout(fixture.store, **args)
        with fixture.store.connection() as db:
            self.assertEqual("PENDING", db.execute("SELECT status FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()[0])
        self.assertFalse(result["jira"]["complete"])
        self.assertEqual(2, len(spy.writes))
        self.assertEqual(2, sum(op == "read" for op, key in spy.calls))
        self.assertEqual(1, sum(op == "read_back" for op, key in spy.calls))
        fixture.store = ContinuousControllerStore(fixture.store.path, ["B"])
        def evidence(record, operation):
            calls.append("transition")
            return spy.read_transition(record, operation)
        lifecycle["read_transition"] = evidence
        self.assertEqual("SUCCEEDED", production_jira_lifecycle(fixture.store, **lifecycle)["record"]["status"])
        args["read_current_status"] = lambda binding: {**spy.read_current_status(binding), "status_id": "Done"}
        self.assertTrue(fixture.controller.production_batch_closeout(fixture.store, **args)["jira"]["complete"])
        self.assertEqual(["status", "transition", "status", "transition"], calls)
        self.assertEqual(2, len(spy.writes))
        self.assertEqual(4, sum(op == "read" for op, key in spy.calls))
        self.assertEqual(2, sum(op == "read_back" for op, key in spy.calls))

    def test_pre3_conflict_retains_success_and_recovers_only_with_transition_evidence(self):
        self.writes, calls = [], []
        self.counted_done(calls)
        success = self.store.jira_operations(KEY)[0]
        def changed(binding):
            calls.append("progress")
            return {"issue_id": KEY, "status_id": "In Review", "observed_at": NOW,
                    "jira_provider": self.provider}
        self.assertEqual("UNOBSERVED", self.progress(read_current_status=changed)["jira_state"])
        conflict = self.store.jira_operations(KEY)[0]
        self.assertEqual("FAILED", conflict["status"])
        self.assertEqual(success["readback_json"], conflict["readback_json"])
        for value in (None, {}, {"status": "Done"}):
            self.assertTrue(self.counted_done(calls, observer=lambda r, o: value)["writes_stopped"])
            self.assertEqual(conflict, self.store.jira_operations(KEY)[0])
        self.assertEqual("SUCCEEDED", self.counted_done(calls)["record"]["status"])
        self.assertEqual("COUNTED", self.progress()["jira_state"])
        self.assertEqual(1, len(self.writes))
        self.assertEqual(5, calls.count("status"))
        self.assertEqual(5, calls.count("transition"))
        self.assertEqual(1, calls.count("progress"))

    def test_pre3_unattempted_claim_and_deferred_batch_resume_without_inventing_transition(self):
        from test_merge_train_controller import BatchReconciliationSafetyTests, JiraSpy, scope_lifecycle, NOW as BATCH_NOW, PROVIDER
        fixture = BatchReconciliationSafetyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.config["github"]["repository_id"] = 101
        calls, writes = [], []
        def before(binding):
            calls.append("status")
            return {"issue_id": "AWF-1", "status_id": "Ready", "observed_at": BATCH_NOW,
                    "jira_provider": PROVIDER}
        def after(record, operation):
            calls.append("transition")
            return {"issue_id": "AWF-1", "status": "In Progress", "observed_at": BATCH_NOW,
                    "actor": "controller-1", "jira_provider": PROVIDER}
        def write(record):
            writes.append(record)
            return {"issue_id": "AWF-1", "operation_id": record["operation_id"], "status": "ATTEMPTED",
                    "observed_at": BATCH_NOW, "jira_provider": PROVIDER}
        args = dict(config=fixture.config, contract={}, event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True}, binding={"issue_id": "AWF-1"},
            issue_type="LEAF", state="DISPATCHED", producer_id="controller-1", run_id="fixture",
            now=BATCH_NOW, evidence=[], transition_id="31", observe_provider_identity=lambda: PROVIDER,
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": BATCH_NOW},
            read_current_status=before, write_transition=write, read_transition=after)
        begin = fixture.store.begin_jira_operation
        spy = JiraSpy()
        def claimed(operation_id, now):
            result = begin(operation_id, now)
            fixture.run_batch(spy)
            return result
        with patch.object(fixture.store, "begin_jira_operation", side_effect=claimed):
            self.assertTrue(scope_lifecycle(fixture.controller, fixture.store, **args)["writes_stopped"])
        self.assertEqual([], writes)
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"), ("read_back", "AWF-2")], spy.calls)
        self.assertTrue(scope_lifecycle(fixture.controller, fixture.store, **args)["writes_stopped"])
        self.assertEqual([], writes)
        self.assertEqual(0, calls.count("transition"))
        result = scope_lifecycle(fixture.controller, fixture.store, **{**args, "event": "BLOCK"})
        self.assertFalse(result["planned"])
        self.assertEqual("PENDING", fixture.store.jira_operations("AWF-1")[0]["status"])
        self.assertEqual(0, calls.count("transition"))
        self.assertEqual("SUCCEEDED", scope_lifecycle(fixture.controller, fixture.store, **args)["record"]["status"])
        self.assertEqual(1, len(writes))
        self.assertEqual(4, calls.count("status"))
        self.assertEqual(1, calls.count("transition"))
        fixture.run_batch(spy, read_current_status=lambda b: {**spy.read_current_status(b), "status_id": "Done"})
        self.assertEqual(1, len(spy.writes))
        self.assertEqual(2, sum(op == "read" for op, key in spy.calls))
        self.assertEqual(1, sum(op == "read_back" for op, key in spy.calls))

    def test_pre3_unknown_appearing_after_precheck_stops_other_ordinary_write(self):
        self.writes, calls = [], []
        identities = []
        def provider():
            identities.append(1)
            if len(identities) == 2:
                def timeout(record, operation):
                    raise TimeoutError()
                self.assertEqual("UNKNOWN", self.counted_done(calls, observer=timeout,
                    run_id="00000000-0000-0000-0000-000000000002")["record"]["status"])
            return copy.deepcopy(self.provider)
        self.assertTrue(self.counted_done(calls, observe_provider_identity=provider)["writes_stopped"])
        self.assertEqual(1, len(self.writes))
        self.assertEqual(["status", "status", "transition"], calls)
        self.assertFalse(self.counted_done(calls, event="BLOCK")["planned"])
        self.assertEqual(1, len(self.writes))
        self.assertEqual(3, calls.count("status"))
        self.assertEqual(2, calls.count("transition"))

    def test_pre3_failed_legacy_identity_observers_preserve_journal_until_valid_history(self):
        from agentic.canonical import loads
        self.writes, calls = [], []
        self.counted_done(calls)
        row = self.store.jira_operations(KEY)[0]
        key = "jira-identity:" + row["operation_id"]
        with self.store.transaction() as db:
            history = loads(db.execute("SELECT value FROM controller_meta WHERE key=?", (key,)).fetchone()["value"])
            db.execute("DELETE FROM controller_meta WHERE key=?", (key,))
        history_reads = []
        for value in (None, {}, TimeoutError(), {**history, "operation_id": "foreign"},
                      {**history, "intent_sha256": "0" * 64},
                      {**history, "identity": {**history["identity"], "repository_id": 999999}}):
            def observe(record):
                history_reads.append(record["operation_id"])
                if isinstance(value, Exception):
                    raise value
                return value
            with self.subTest(value=value):
                self.store = ContinuousControllerStore(self.store.path, ["A"])
                with self.assertRaises((ValidationError, TimeoutError)):
                    self.counted_done(calls, observe_jira_operation_identity=observe)
                self.assertEqual(row, self.store.jira_operations(KEY)[0])
                with self.store.connection() as db:
                    self.assertIsNone(db.execute("SELECT value FROM controller_meta WHERE key=?", (key,)).fetchone())
                self.assertEqual(1, len(self.writes))
        self.counted_done(calls, current="Done", observe_jira_operation_identity=lambda r: history_reads.append(r["operation_id"]) or history)
        self.assertEqual(row, self.store.jira_operations(KEY)[0])
        self.assertEqual(7, len(history_reads))
        self.assertEqual(8, calls.count("status"))
        self.assertEqual(1, calls.count("transition"))
        self.assertEqual(1, len(self.writes))

    def test_pre3_legacy_batch_provider_spelling_and_identity_migrate_without_rewriting_rows(self):
        from agentic.canonical import canonical, loads
        from test_merge_train_controller import OperationalControllerTests, JiraSpy, NOW as BATCH_NOW
        fixture = OperationalControllerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        spy = JiraSpy()
        args = fixture.operational_inputs(spy)
        def timeout(record, operation):
            spy.calls.append(("read_back", record["binding"]["issue_id"]))
            raise TimeoutError()
        fixture.controller.production_batch_closeout(fixture.store, **{**args, "read_transition": timeout})
        with fixture.store.transaction() as db:
            row = dict(db.execute("SELECT * FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone())
            record = loads(row["record_json"])
            record["jira_provider"]["provider_project_id"] = record["jira_provider"].pop("project_id")
            original = canonical(record).decode()
            intent = fingerprint("controller-jira-operation", {k: v for k, v in record.items() if k != "created_at"})
            db.execute("UPDATE controller_batch_jira_operation SET record_json=?,intent_sha256=? WHERE operation_id=?", (original, intent, row["operation_id"]))
            history = loads(db.execute("SELECT value FROM controller_meta WHERE key=?", ("jira-identity:" + row["operation_id"],)).fetchone()["value"])
            history["intent_sha256"] = intent
            db.execute("DELETE FROM controller_meta WHERE key=?", ("jira-identity:" + row["operation_id"],))
        reads = []
        lifecycle = dict(config=args["config"], contract={}, event="JIRA_RECONCILED",
            facts={"merge_confirmed": True, "closeout_valid": True}, binding={"issue_id": "AWF-1"},
            issue_type="LEAF", state="MERGED", producer_id="controller-1", run_id="fixture",
            now=BATCH_NOW, evidence=[], transition_id="31", merge_result_id="merge-1",
            **{name: args[name] for name in ("observe_provider_identity", "observe_issue_identity", "observe_issue_policy")},
            read_current_status=lambda b: reads.append("status") or spy.read_current_status(b),
            write_transition=lambda r: self.fail("legacy recovery must not write"),
            read_transition=lambda r, o: reads.append("transition") or spy.read_transition(r, o),
            observe_jira_operation_identity=lambda r: reads.append("identity") or history)
        self.assertEqual("SUCCEEDED", production_jira_lifecycle(fixture.store, **lifecycle)["record"]["status"])
        self.assertTrue(fixture.controller.production_batch_closeout(fixture.store, **args)["jira"]["complete"])
        with fixture.store.connection() as db:
            retained = db.execute("SELECT * FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()
            self.assertEqual(original, retained["record_json"])
            self.assertEqual(intent, retained["intent_sha256"])
        self.assertEqual(["status", "identity", "transition"], reads)
        self.assertEqual(2, len(spy.writes))
        self.assertEqual(3, sum(op == "read" for op, key in spy.calls))
        self.assertEqual(3, sum(op == "read_back" for op, key in spy.calls))

    def test_pre3_batch_owned_unknown_and_failed_have_lifecycle_evidence_route(self):
        from test_merge_train_controller import OperationalControllerTests, JiraSpy, NOW as BATCH_NOW
        for failure in ("UNKNOWN", "FAILED"):
            with self.subTest(failure=failure):
                fixture = OperationalControllerTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                spy = JiraSpy(mismatch="AWF-1" if failure == "FAILED" else None)
                args = fixture.operational_inputs(spy)
                if failure == "UNKNOWN":
                    def timeout(record, operation):
                        spy.calls.append(("read_back", record["binding"]["issue_id"]))
                        raise TimeoutError()
                    args["read_transition"] = timeout
                fixture.controller.production_batch_closeout(fixture.store, **args)
                with fixture.store.connection() as db:
                    before = dict(db.execute("SELECT * FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone())
                self.assertEqual(failure, before["status"])
                reads = []
                lifecycle = dict(config=args["config"], contract={}, event="JIRA_RECONCILED",
                    facts={"merge_confirmed": True, "closeout_valid": True}, binding={"issue_id": "AWF-1"},
                    issue_type="LEAF", state="MERGED", producer_id="controller-1", run_id="fixture",
                    now=BATCH_NOW, evidence=[], transition_id="31", merge_result_id="merge-1",
                    **{name: args[name] for name in ("observe_provider_identity", "observe_issue_identity", "observe_issue_policy")},
                    read_current_status=lambda b: reads.append("status") or spy.read_current_status(b),
                    write_transition=lambda r: self.fail("recovery must not write"))
                for value in (None, {}, {"status": "Done"}):
                    fixture.store = ContinuousControllerStore(fixture.store.path, ["B"])
                    lifecycle["read_transition"] = lambda r, o: reads.append("transition") or value
                    self.assertTrue(production_jira_lifecycle(fixture.store, **lifecycle)["writes_stopped"])
                    with fixture.store.connection() as db:
                        self.assertEqual(before, dict(db.execute("SELECT * FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()))
                    self.assertEqual(1, len(spy.writes))
                spy.mismatch = None
                lifecycle["read_transition"] = lambda r, o: reads.append("transition") or spy.read_transition(r, o)
                self.assertEqual("SUCCEEDED", production_jira_lifecycle(fixture.store, **lifecycle)["record"]["status"])
                args["read_transition"] = spy.read_transition
                self.assertTrue(fixture.controller.production_batch_closeout(fixture.store, **args)["jira"]["complete"])
                self.assertEqual(["status", "transition"] * 4, reads)
                self.assertEqual(2, len(spy.writes))
                self.assertEqual(6, sum(op == "read" for op, key in spy.calls))
                self.assertEqual(3, sum(op == "read_back" for op, key in spy.calls))

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


class BatchRecoveryRegressions(unittest.TestCase):
    def setUp(self):
        from test_merge_train_controller import OperationalControllerTests, JiraSpy
        self.fixture = OperationalControllerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.batch['constituents'] = self.fixture.batch['constituents'][:1]
        self.spy = JiraSpy()
        self.args = self.fixture.operational_inputs(self.spy)

    def reopen(self):
        self.fixture.store = ContinuousControllerStore(self.fixture.store.path, ['B'])

    def batch(self, **overrides):
        return self.fixture.controller.production_batch_closeout(
            self.fixture.store, **{**self.args, **overrides})['jira']

    def lifecycle(self, **overrides):
        args = dict(config=self.args['config'], contract={}, event='BLOCK', facts={},
            binding={'issue_id': 'AWF-1'}, issue_type='LEAF', state='MERGED',
            producer_id='controller-1', run_id='fixture', now=self.args['now'],
            evidence=[], transition_id='31', merge_result_id='merge-1',
            **{key: self.args[key] for key in ('observe_provider_identity',
                'observe_issue_identity', 'observe_issue_policy', 'read_current_status',
                'write_transition', 'read_transition')})
        return production_jira_lifecycle(self.fixture.store, **{**args, **overrides})

    def row(self):
        with self.fixture.store.connection() as db:
            return dict(db.execute('SELECT * FROM controller_batch_jira_operation').fetchone())

    def counts(self, writes, reads, transitions):
        self.assertEqual(writes, sum(op == 'write' for op, _ in self.spy.calls))
        self.assertEqual(reads, sum(op == 'read' for op, _ in self.spy.calls))
        self.assertEqual(transitions, sum(op == 'read_back' for op, _ in self.spy.calls))

    def test_pre4_lost_batch_response_recovers_once_and_closeout_stays_complete(self):
        def lost(record):
            self.spy.write_transition(record)
            raise TimeoutError('response lost after POST')
        self.assertFalse(self.batch(write_transition=lost)['complete'])
        self.counts(1, 1, 0)
        self.reopen()
        self.assertFalse(self.lifecycle().get('writes_stopped', False))
        self.assertEqual('SUCCEEDED', self.row()['status'])
        self.assertIsNone(self.row()['operation_json'])
        self.counts(1, 2, 1)
        for _ in range(2):
            self.reopen()
            self.assertTrue(self.batch()['complete'])
            self.assertEqual('SUCCEEDED', self.row()['status'])
            self.counts(1, 2, 1)

    def test_pre4_batch_prewrite_read_failure_retries_without_transition_recovery(self):
        def timeout(binding):
            self.spy.read_current_status(binding)
            raise TimeoutError('GET failed before POST')
        self.assertFalse(self.batch(read_current_status=timeout)['complete'])
        self.counts(0, 1, 0)
        with self.fixture.store.connection() as db:
            self.assertIsNotNone(db.execute('SELECT value FROM controller_meta WHERE key=?',
                ('jira-not-attempted:' + self.row()['operation_id'],)).fetchone())
        self.reopen()
        self.assertFalse(self.lifecycle().get('writes_stopped', False))
        self.assertEqual('PENDING', self.row()['status'])
        self.counts(0, 2, 0)
        self.reopen()
        self.assertTrue(self.batch()['complete'])
        self.counts(1, 3, 1)

    def test_pre4_batch_prewrite_failure_retries_directly_after_reopen(self):
        def timeout(binding):
            self.spy.read_current_status(binding)
            raise TimeoutError()
        self.batch(read_current_status=timeout)
        self.counts(0, 1, 0)
        self.reopen()
        self.assertTrue(self.batch()['complete'])
        self.counts(1, 2, 1)

    def test_pre4_lost_batch_response_status_alone_never_resolves_or_repeats(self):
        def lost(record):
            self.spy.write_transition(record)
            raise TimeoutError()
        self.batch(write_transition=lost)
        stopped = self.row()
        def status_only(record, operation):
            self.spy.calls.append(('read_back', 'AWF-1'))
            return {'status': 'Done'}
        for _ in range(2):
            self.reopen()
            self.assertTrue(self.lifecycle(read_transition=status_only)['writes_stopped'])
            self.assertFalse(self.batch()['complete'])
            self.assertEqual(stopped, self.row())
        self.counts(1, 3, 2)

    def legacy_deferred(self, provable):
        from agentic.canonical import canonical, loads
        def lost(record):
            self.spy.write_transition(record)
            raise TimeoutError('original POST response lost')
        self.assertEqual('UNKNOWN', self.lifecycle(event='WORKER_STARTED', state='DISPATCHED',
            facts={'run_registered': True, 'worktree_verified': True},
            write_transition=lost)['record']['status'])
        self.batch()
        # Legacy closeout copied the predecessor readback (including JSON null)
        # into FAILED, without an operation receipt or a deferred metadata key.
        histories = {}
        with self.fixture.store.transaction() as db:
            db.execute("UPDATE controller_batch_jira_operation SET status='FAILED',readback_json=?",
                       ('null' if provable else canonical({'reason': 'unproven legacy stop'}).decode(),))
            for row in db.execute('SELECT * FROM controller_meta WHERE key LIKE \'jira-identity:%\'').fetchall():
                history = loads(row['value'])
                histories[history['operation_id']] = history
            db.execute("DELETE FROM controller_meta WHERE key LIKE 'jira-deferred:%' OR key LIKE 'jira-identity:%'")
        self.counts(1, 1, 0)
        self.reopen()
        def evidence(record, operation):
            value = self.spy.read_transition(record, operation)
            if record['lifecycle_event'] != 'WORKER_STARTED':
                return None
            return {**value, 'status': record['to_status_id']}
        return self.lifecycle(read_transition=evidence,
            observe_jira_operation_identity=lambda record: histories[record['operation_id']])

    def test_pre4_legacy_proven_deferred_row_does_not_invent_transition(self):
        self.assertFalse(self.legacy_deferred(True).get('writes_stopped', False))
        self.assertEqual('SUCCEEDED', self.fixture.store.jira_operations('AWF-1')[0]['status'])
        self.assertEqual('PENDING', self.row()['status'])
        self.counts(1, 2, 1)
        self.reopen()
        self.assertTrue(self.batch()['complete'])
        self.counts(2, 3, 2)

    def test_pre4_ambiguous_legacy_stop_preserves_original_resolution(self):
        result = self.legacy_deferred(False)
        self.assertTrue(result['writes_stopped'])
        self.assertIn('Legacy batch FAILED row has no proof of an unattempted write', result['reason'])
        self.assertEqual('SUCCEEDED', self.fixture.store.jira_operations('AWF-1')[0]['status'])
        stopped = self.row()
        self.assertEqual('FAILED', stopped['status'])
        self.counts(1, 2, 1)
        self.reopen()
        self.assertFalse(self.batch()['complete'])
        self.assertEqual(stopped, self.row())
        self.counts(1, 2, 1)


if __name__ == "__main__":
    unittest.main()
