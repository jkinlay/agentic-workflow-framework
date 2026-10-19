"""AWF-54B T11: controller-only batch Jira reconciliation (red contract)."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
NOW = "2026-10-17T16:00:00Z"


class JiraSpy:
    """In-memory provider callbacks; never import or contact a Jira client."""

    def __init__(self, mismatch=None):
        self.calls = []
        self.mismatch = mismatch
        self.writes = {}

    def read_current_status(self, binding):
        ticket = binding["issue_id"]
        self.calls.append(("read", ticket))
        return {"issue_id": ticket, "status_id": "In Review", "observed_at": NOW}

    def write_transition(self, record):
        ticket = record["binding"]["issue_id"]
        self.calls.append(("write", ticket))
        self.writes[ticket] = deepcopy(record)
        return {"issue_id": ticket, "status": "ATTEMPTED", "observed_at": NOW}

    def read_transition(self, record, operation):
        ticket = record["binding"]["issue_id"]
        self.calls.append(("read_back", ticket))
        return {
            "issue_id": ticket,
            "status": "In Progress" if ticket == self.mismatch else "Done",
            "actor": "external-automation" if ticket == self.mismatch else "controller-1",
            "observed_at": NOW,
        }


class MergeTrainControllerTests(unittest.TestCase):
    def test_only_controller_reconciles_constituent_jira(self):
        # Planned production entry point: resolve inside the test so standalone
        # discovery works on main and absence is a behavior-specific red error.
        from agentic import continuous_controller

        reconcile = continuous_controller.production_batch_jira_reconciliation

        # Q5 is answered by the owner: exact commit observed on main, merge
        # order, stop at mismatch, retain progress and resume without replay.
        # Doc 47's audit schema/location/retention and batch lifecycle fields
        # remain open owner questions. The dictionaries below describe only
        # T11's proposed Python seam, not a new canonical record schema.
        config = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text("utf-8"))
        config["jira"].update({
            "enabled": True,
            "cloud_id": "cloud-1",
            "site": "https://jira.example.invalid",
            "provider_project_id": "project-1",
            "project_key": "AWF",
            "controller_actor_id": "controller-1",
        })
        # Deliberately neither ticket nor PR sorted: preserve batch merge order.
        tickets = ["AWF-303", "AWF-101", "AWF-202"]
        constituents = [
            {
                "binding": {"issue_id": ticket},
                "pr_number": number,
                "reviewed_head_sha": digit * 40,
                "paths": [f"src/member-{number}.txt"],
                "closeout_kind": "merged-via-batch",
                "batch_id": "batch-1",
                "merge_commit_sha": "d" * 40,
            }
            for ticket, number, digit in zip(tickets, [33, 11, 22], "abc")
        ]
        batch = {
            "batch_id": "batch-1", "pr_number": 44,
            "merge_commit_sha": "d" * 40,
            "constituents": constituents,
        }
        observed_merge = {
            "batch_id": "batch-1", "target_branch": "main",
            "merge_commit_sha": "d" * 40, "observed_at": NOW,
        }

        def expected_calls(selected):
            return [(operation, ticket) for ticket in selected
                    for operation in ("read", "write", "read_back")]

        with tempfile.TemporaryDirectory(prefix="awf-batch-jira-") as temporary:
            external = Path(temporary)
            project = external / "project"
            project.mkdir()
            for member in constituents:
                path = project / member["paths"][0]
                path.parent.mkdir(exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")

            def store(name):
                # Controller state stays outside the candidate worktree.
                return continuous_controller.ContinuousControllerStore(
                    external / f"{name}.sqlite", ["B"], worktree_roots=(project,))

            def run(state, spy, *, role="controller", enabled=True):
                selected_config = deepcopy(config)
                selected_config["jira"]["enabled"] = enabled
                return reconcile(
                    state, config=selected_config, role=role,
                    batch=deepcopy(batch), observed_merge=deepcopy(observed_merge),
                    now=NOW, read_current_status=spy.read_current_status,
                    write_transition=spy.write_transition,
                    read_transition=spy.read_transition,
                )

            # Disabled Jira forbids all issue access through every role.
            for role in ("worker", "steward", "controller"):
                with self.subTest(enabled=False, role=role):
                    spy = JiraSpy()
                    run(store(f"disabled-{role}"), spy, role=role, enabled=False)
                    self.assertEqual([], spy.calls)
            for role in ("worker", "steward"):
                with self.subTest(enabled=True, role=role):
                    spy = JiraSpy()
                    run(store(role), spy, role=role)
                    self.assertEqual([], spy.calls)

            with self.subTest(enabled=True, role="controller", outcome="success"):
                spy = JiraSpy()
                result = run(store("success"), spy)
                self.assertEqual(expected_calls(tickets), spy.calls)
                self.assertEqual(tickets, result["reconciled"])
                self.assertEqual([], result["remaining"])
                for member in constituents:
                    record = spy.writes[member["binding"]["issue_id"]]
                    self.assertEqual("JIRA_RECONCILED", record["lifecycle_event"])
                    self.assertEqual("Done", record["to_status_id"])
                    self.assertEqual("merged-via-batch", record["closeout_kind"])
                    for key in ("batch_id", "merge_commit_sha", "pr_number", "reviewed_head_sha"):
                        self.assertEqual(member[key], record[key])
                # Reopen durable state: successful constituents cannot be replayed.
                replay = JiraSpy()
                repeated = run(store("success"), replay)
                self.assertEqual([], replay.calls)
                self.assertEqual(tickets, repeated["reconciled"])

            with self.subTest(enabled=True, role="controller", outcome="mismatch"):
                spy = JiraSpy(mismatch=tickets[1])
                result = run(store("partial"), spy)
                self.assertEqual(expected_calls(tickets[:2]), spy.calls)
                self.assertEqual(tickets[:1], result["reconciled"])
                self.assertEqual(tickets[2:], result["remaining"])
                self.assertEqual(tickets[1], result["conflict"]["issue_id"])
                self.assertEqual("In Progress", result["conflict"]["observed_status"])
                self.assertEqual("external-automation", result["conflict"]["observed_actor"])
                self.assertEqual(NOW, result["conflict"]["observed_at"])
                # Resume remaining constituents after restart. The mismatched
                # ticket stays stopped; neither it nor completed work is retried.
                resumed_spy = JiraSpy()
                resumed = run(store("partial"), resumed_spy)
                self.assertEqual(expected_calls(tickets[2:]), resumed_spy.calls)
                self.assertEqual([tickets[0], tickets[2]], resumed["reconciled"])
                self.assertEqual([], resumed["remaining"])
                self.assertEqual(result["conflict"], resumed["conflict"])
                final_spy = JiraSpy()
                run(store("partial"), final_spy)
                self.assertEqual([], final_spy.calls)


class BatchReconciliationSafetyTests(unittest.TestCase):
    def setUp(self):
        from agentic import continuous_controller as controller
        self.controller = controller
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-t11-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.sqlite"
        self.store = controller.ContinuousControllerStore(self.path, ["B"])
        self.config = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text("utf-8"))
        self.config["jira"].update(enabled=True, cloud_id="cloud-1",
            site="https://jira.example.invalid", provider_project_id="project-1",
            project_key="AWF", controller_actor_id="controller-1")
        self.batch = {"batch_id": "batch-1", "pr_number": 44,
            "merge_commit_sha": "d" * 40, "constituents": [
                {"binding": {"issue_id": issue}, "pr_number": number,
                 "reviewed_head_sha": digit * 40, "paths": [f"src/{number}.txt"],
                 "closeout_kind": "merged-via-batch", "batch_id": "batch-1",
                 "merge_commit_sha": "d" * 40}
                for issue, number, digit in [("AWF-1", 1, "a"), ("AWF-2", 2, "b")]]}
        self.merge = {"batch_id": "batch-1", "target_branch": "main",
            "merge_commit_sha": "d" * 40, "observed_at": NOW}

    def run_batch(self, spy=None, **overrides):
        spy = spy or JiraSpy()
        inputs = dict(config=self.config, role="controller", batch=self.batch,
            observed_merge=self.merge, now=NOW,
            read_current_status=spy.read_current_status,
            write_transition=spy.write_transition, read_transition=spy.read_transition)
        inputs.update(overrides)
        return self.controller.production_batch_jira_reconciliation(self.store, **inputs)

    def test_ordinary_lifecycle_after_successful_batch_uses_full_binding(self):
        import uuid
        provider = {"cloud_id": "cloud-1", "site": "https://jira.example.invalid",
                    "project_id": "project-1", "project_key": "AWF",
                    "controller_actor_id": "controller-1"}
        binding = {"issue_id": "AWF-1", "project_id": "local-project",
                   "repository_id": "101", "requirements_hash": "a" * 64}
        writes = []
        def lifecycle(run_id):
            def write(record):
                writes.append(record)
                return {"operation_id": record["operation_id"], "issue_id": "AWF-1",
                        "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": provider}
            return self.controller.production_jira_lifecycle(self.store,
                config=self.config, contract={}, event="WORKER_STARTED",
                facts={"run_registered": True, "worktree_verified": True}, binding=binding,
                issue_type="LEAF", state="DISPATCHED", producer_id="controller-1",
                run_id=str(uuid.UUID(int=run_id)), now=NOW, evidence=["urn:fixture:test"],
                transition_id="31", observe_provider_identity=lambda: provider,
                read_current_status=lambda b: {"issue_id": "AWF-1", "status_id": "Ready",
                    "observed_at": NOW, "jira_provider": provider}, write_transition=write,
                read_transition=lambda r, o: {"issue_id": "AWF-1", "status": "In Progress",
                    "actor": "controller-1", "observed_at": NOW, "jira_provider": provider})
        self.assertEqual("SUCCEEDED", lifecycle(1)["record"]["status"])
        self.assertTrue(self.run_batch()["complete"])
        self.assertEqual("SUCCEEDED", lifecycle(2)["record"]["status"])
        self.assertEqual(2, len(writes))
        replay = JiraSpy()
        self.assertTrue(self.run_batch(replay)["complete"])
        self.assertEqual([], replay.calls)

    def assert_batch_stop_blocks_ordinary_lifecycle(self, outcome):
        import uuid
        provider = {"cloud_id": "cloud-1", "site": "https://jira.example.invalid",
                    "project_id": "project-1", "project_key": "AWF",
                    "controller_actor_id": "controller-1"}
        writes = []

        def lifecycle(issue, run_id, current="Ready"):
            def write(record):
                writes.append(record)
                return {"operation_id": record["operation_id"], "issue_id": issue,
                        "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": provider}
            return self.controller.production_jira_lifecycle(self.store,
                config=self.config, contract={}, event="WORKER_STARTED",
                facts={"run_registered": True, "worktree_verified": True},
                binding={"issue_id": issue, "project_id": "local-project",
                         "repository_id": "101", "requirements_hash": "a" * 64},
                issue_type="LEAF", state="DISPATCHED", producer_id="controller-1",
                run_id=str(uuid.UUID(int=run_id)), now=NOW, evidence=["urn:fixture:test"],
                transition_id="31", observe_provider_identity=lambda: provider,
                read_current_status=lambda b: {"issue_id": issue, "status_id": current,
                    "observed_at": NOW, "jira_provider": provider}, write_transition=write,
                read_transition=lambda r, o: {"issue_id": issue, "status": "In Progress",
                    "actor": "controller-1", "observed_at": NOW, "jira_provider": provider})

        # Retain ordinary intents to exercise recovery and completed-operation replay.
        self.assertEqual("SUCCEEDED", lifecycle("AWF-1", 1)["record"]["status"])
        self.assertEqual("SUCCEEDED", lifecycle("AWF-1", 2)["record"]["status"])
        pending_id = self.store.jira_operations("AWF-1")[1]["operation_id"]
        with self.store.transaction() as db:
            db.execute("UPDATE controller_jira_operation SET status='PENDING' WHERE operation_id=?",
                       (pending_id,))
        spy = JiraSpy(mismatch="AWF-1" if outcome == "FAILED" else None)
        if outcome == "UNKNOWN":
            def unknown(record, operation):
                raise TimeoutError("Provider outcome unknown")
            self.run_batch(spy, read_transition=unknown)
        else:
            self.run_batch(spy)
        with self.store.connection() as db:
            row = db.execute("SELECT status FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()
        self.assertEqual(outcome, row["status"])
        # Reopen durable state, as a restarted controller would.
        self.store = self.controller.ContinuousControllerStore(self.path, ["B"])
        writes.clear()
        for ordinary_status in ("PENDING", "IN_FLIGHT", "UNKNOWN", "SUCCEEDED"):
            with self.store.transaction() as db:
                db.execute("UPDATE controller_jira_operation SET status=? WHERE operation_id=?",
                           (ordinary_status, pending_id))
            before = self.store.jira_operations("AWF-1")
            for run_id in (3, 2, 1):
                for current in ("Ready", "Done"):
                    with self.subTest(outcome=outcome, ordinary_status=ordinary_status,
                                      run_id=run_id, current=current):
                        result = lifecycle("AWF-1", run_id, current)
                        self.assertEqual([], writes)
                        self.assertEqual(before, self.store.jira_operations("AWF-1"))
                        self.assertFalse(result["planned"])
                        self.assertTrue(result["writes_stopped"])
        # Preparation and claiming must also enforce the stop transactionally.
        from agentic import ValidationError
        with self.assertRaisesRegex(ValidationError, "batch Jira operation"):
            self.store.prepare_jira_operation(before[0]["record"], NOW)
        with self.store.transaction() as db:
            db.execute("UPDATE controller_jira_operation SET status='PENDING' WHERE operation_id=?",
                       (pending_id,))
        before = self.store.jira_operations("AWF-1")
        with self.assertRaisesRegex(ValidationError, "batch Jira operation"):
            self.store.begin_jira_operation(pending_id, NOW)
        self.assertEqual(before, self.store.jira_operations("AWF-1"))
        self.assertEqual("SUCCEEDED", lifecycle("AWF-3", 4)["record"]["status"])
        self.assertEqual(["AWF-3"], [record["binding"]["issue_id"] for record in writes])

    def test_unknown_batch_stops_ordinary_lifecycle_for_same_issue(self):
        self.assert_batch_stop_blocks_ordinary_lifecycle("UNKNOWN")

    def test_failed_batch_stops_ordinary_lifecycle_for_same_issue(self):
        self.assert_batch_stop_blocks_ordinary_lifecycle("FAILED")

    def start_batch_process(self, mode):
        import subprocess
        import textwrap
        fixture = Path(self.temporary.name) / "process-input.json"
        fixture.write_text(json.dumps({"config": self.config, "batch": self.batch,
            "observed_merge": self.merge, "now": NOW}), encoding="utf-8")
        source = textwrap.dedent("""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        from agentic.continuous_controller import ContinuousControllerStore, production_batch_jira_reconciliation
        inputs = json.loads(Path(sys.argv[3]).read_text('utf-8'))
        store = ContinuousControllerStore(Path(sys.argv[2]), ['B'])
        def before(binding):
            return {'issue_id': binding['issue_id'], 'status_id': 'In Review', 'observed_at': inputs['now']}
        def write(record):
            if sys.argv[4] == 'crash':
                os._exit(19)
            if record['binding']['issue_id'] == 'AWF-1':
                print('CLAIMED', flush=True)
                sys.stdin.readline()
            return {'issue_id': record['binding']['issue_id'], 'status': 'ATTEMPTED', 'observed_at': inputs['now']}
        def after(record, operation):
            return {'issue_id': record['binding']['issue_id'], 'status': 'Done',
                    'actor': 'controller-1', 'observed_at': inputs['now']}
        result = production_batch_jira_reconciliation(store, role='controller', **inputs,
            read_current_status=before, write_transition=write, read_transition=after)
        print(json.dumps(result), flush=True)
        """)
        process = subprocess.Popen([sys.executable, "-B", "-c", source,
            str(ROOT / ".agentic/lib"), str(self.path), str(fixture), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def finish():
            if process.poll() is None:
                process.communicate("resume\n", timeout=20)
        self.addCleanup(finish)
        return process

    def test_process_death_releases_ownership_and_recovers_remaining_only(self):
        process = self.start_batch_process("crash")
        output, errors = process.communicate(timeout=20)
        self.assertEqual(19, process.returncode, (output, errors))
        self.store = self.controller.ContinuousControllerStore(self.path, ["B"])
        replay = JiraSpy()
        result = self.run_batch(replay)
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"), ("read_back", "AWF-2")], replay.calls)
        self.assertEqual([], result["remaining"])
        self.assertEqual("AWF-1", result["conflict"]["issue_id"])
        self.assertFalse(result["complete"])
        final = JiraSpy()
        self.assertEqual(result["conflict"], self.run_batch(final)["conflict"])
        self.assertEqual([], final.calls)

    def test_live_process_claim_cannot_be_stolen_by_restart_or_global_recovery(self):
        import concurrent.futures
        process = self.start_batch_process("active")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            line = pool.submit(process.stdout.readline)
            try:
                self.assertEqual("CLAIMED", line.result(timeout=20).strip())
                self.store = self.controller.ContinuousControllerStore(self.path, ["B"])
                self.store.recover_jira_operations(NOW)
                spy = JiraSpy()
                result = self.run_batch(spy, now="2026-10-18T16:00:00Z")
                self.assertEqual([], spy.calls)
                self.assertEqual(["AWF-2"], result["remaining"])
                with self.store.connection() as db:
                    self.assertEqual("IN_FLIGHT", db.execute(
                        "SELECT status FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()[0])
            finally:
                output, errors = process.communicate("resume\n", timeout=20)
        self.assertEqual(0, process.returncode, errors)
        self.assertTrue(json.loads(output)["complete"])
        spy = JiraSpy()
        self.assertTrue(self.run_batch(spy)["complete"])
        self.assertEqual([], spy.calls)

    def test_whole_input_rejected_before_callbacks(self):
        from agentic import ValidationError
        original = deepcopy(self.batch)
        cases = []
        for key, value in [("batch_id", "other"), ("merge_commit_sha", "bad"),
                           ("pr_number", True)]:
            bad = deepcopy(original)
            bad["constituents"][-1][key] = value
            cases.append(bad)
        for key, value in [("binding", {"issue_id": ""}), ("paths", ["../escape"]),
                           ("reviewed_head_sha", "bad"), ("closeout_kind", "other")]:
            bad = deepcopy(original)
            bad["constituents"][-1][key] = value
            cases.append(bad)
        bad = deepcopy(original)
        bad["constituents"].append(deepcopy(bad["constituents"][0]))
        cases.append(bad)
        for bad in cases:
            with self.subTest(batch=bad):
                spy = JiraSpy()
                with self.assertRaises(ValidationError):
                    self.run_batch(spy, batch=bad)
                self.assertEqual([], spy.calls)
        for key, value in [("target_branch", "other"), ("batch_id", "other"),
                           ("merge_commit_sha", "e" * 40), ("observed_at", "bad")]:
            spy = JiraSpy()
            with self.assertRaises(ValidationError):
                self.run_batch(spy, observed_merge={**self.merge, key: value})
            self.assertEqual([], spy.calls)

    def test_replay_pins_order_provider_config_and_merge(self):
        from agentic import ValidationError
        self.run_batch()
        bad_config = deepcopy(self.config)
        bad_config["jira"]["cloud_id"] = "other"
        reordered = deepcopy(self.batch)
        reordered["constituents"].reverse()
        changed = deepcopy(self.batch)
        changed["constituents"][0]["paths"] = ["src/other.txt"]
        for override in [dict(config=bad_config), dict(batch=reordered), dict(batch=changed)]:
            spy = JiraSpy()
            with self.assertRaises(ValidationError):
                self.run_batch(spy, **override)
            self.assertEqual([], spy.calls)
        spy = JiraSpy()
        result = self.run_batch(spy, now="2026-10-18T16:00:00Z")
        self.assertEqual([], spy.calls)
        self.assertEqual(["AWF-1", "AWF-2"], result["reconciled"])

    def test_bad_callback_observations_stop_without_replay(self):
        variants = [dict(issue_id="foreign", status_id="In Review", observed_at=NOW),
                    dict(issue_id="AWF-1", observed_at=NOW),
                    dict(issue_id="AWF-1", status_id="In Review", observed_at="bad"),
                    dict(issue_id="AWF-1", status_id="In Review", observed_at="2020-01-01T00:00:00Z")]
        for index, observation in enumerate(variants):
            with self.subTest(observation=observation):
                self.store = self.controller.ContinuousControllerStore(
                    Path(self.temporary.name) / f"bad-{index}.sqlite", ["B"])
                spy = JiraSpy()
                result = self.run_batch(spy, read_current_status=lambda binding: observation)
                self.assertEqual([], spy.calls)
                self.assertEqual("AWF-1", result["conflict"]["issue_id"])
                replay = JiraSpy()
                self.run_batch(replay)
                self.assertFalse(any(ticket == "AWF-1" for _, ticket in replay.calls))

    def test_unknown_write_and_failed_readback_never_repeat(self):
        for stage in ("write_transition", "read_transition"):
            with self.subTest(stage=stage):
                self.store = self.controller.ContinuousControllerStore(
                    Path(self.temporary.name) / f"{stage}.sqlite", ["B"])
                spy = JiraSpy()
                def fail(*args):
                    raise OSError("provider unavailable")
                result = self.run_batch(spy, **{stage: fail})
                self.assertIsNotNone(result["conflict"])
                replay = JiraSpy()
                self.run_batch(replay)
                self.assertFalse(any(ticket == "AWF-1" for _, ticket in replay.calls))

    def test_claim_before_callback_crash_and_two_store_race(self):
        class Crash(BaseException):
            pass
        def crash(binding):
            raise Crash()
        with self.assertRaises(Crash):
            self.run_batch(read_current_status=crash)
        self.store = self.controller.ContinuousControllerStore(self.path, ["B"])
        spy = JiraSpy()
        result = self.run_batch(spy)
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"), ("read_back", "AWF-2")], spy.calls)
        self.assertEqual([], result["remaining"])
        self.assertIsNotNone(result["conflict"])
        self.assertFalse(result["complete"])

    def test_reentrant_competing_store_cannot_call_provider(self):
        second = self.controller.ContinuousControllerStore(self.path, ["B"])
        spy = JiraSpy()
        competing = JiraSpy()
        def read(binding):
            original = self.store
            self.store = second
            try:
                self.run_batch(competing)
            finally:
                self.store = original
            return spy.read_current_status(binding)
        self.run_batch(spy, read_current_status=read)
        self.assertEqual([], competing.calls)
        self.assertEqual(2, len(spy.writes))

    def test_policy_holds_epics_disabled_writes_and_owner_closure(self):
        for kind, policy in [("epic", {"issue_type": "EPIC"}),
                             ("owner", {"owner_closure_required": True}),
                             ("disabled", {})]:
            with self.subTest(kind=kind):
                self.store = self.controller.ContinuousControllerStore(
                    Path(self.temporary.name) / f"{kind}.sqlite", ["B"])
                config = deepcopy(self.config)
                if kind == "disabled":
                    config["jira"]["lifecycle_writes"]["done"] = False
                spy = JiraSpy()
                result = self.run_batch(spy, config=config,
                    policy_context={"AWF-1": policy, "AWF-2": policy})
                self.assertEqual([], spy.calls)
                self.assertFalse(result["complete"])

    def test_custom_done_and_already_target(self):
        config = deepcopy(self.config)
        config["jira"]["status_map"]["done"] = "Closed"
        spy = JiraSpy()
        def current(binding):
            return {"issue_id": binding["issue_id"], "status_id": "Closed", "observed_at": NOW}
        result = self.run_batch(spy, config=config, read_current_status=current)
        self.assertEqual([], spy.calls)
        self.assertTrue(result["complete"])

    def test_conflict_is_not_completion_or_count_authority(self):
        self.run_batch(JiraSpy(mismatch="AWF-1"))
        result = self.run_batch()
        self.assertEqual([], result["remaining"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["count_allowed"])
        self.assertFalse(result["execution_authority"])

    def test_t8_audit_and_controller_state_survive_progress(self):
        import hashlib
        from agentic.canonical import canonical
        candidate = {"repository_id": 1, "batch_id": "batch-1", "base_sha": "a" * 40,
                     "head_sha": "b" * 40, "tree_sha": "c" * 40,
                     "sources": [{"pr_number": 1, "head_sha": "a" * 40}]}
        record = {"candidate": candidate, "record_sha256": hashlib.sha256(canonical(candidate)).hexdigest()}
        self.store.append_batch_audit(record)
        with self.store.connection() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM controller_batch_audit")]
            streams = [tuple(row) for row in db.execute("SELECT * FROM stream_status")]
        self.run_batch()
        reopened = self.controller.ContinuousControllerStore(self.path, ["B"])
        self.assertEqual([record], reopened.batch_audit_records())
        with reopened.connection() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM controller_batch_audit")])
            self.assertEqual(streams, [tuple(row) for row in db.execute("SELECT * FROM stream_status")])

    def test_persistence_failure_after_mutation_leaves_claim(self):
        from unittest.mock import patch
        spy = JiraSpy()
        with patch.object(self.store, "finish_batch_jira_operation", side_effect=OSError("disk unavailable")):
            result = self.run_batch(spy)
        self.assertEqual([("read", "AWF-1"), ("write", "AWF-1"), ("read_back", "AWF-1")], spy.calls)
        self.assertIsNotNone(result["conflict"])
        self.store = self.controller.ContinuousControllerStore(self.path, ["B"])
        replay = JiraSpy()
        result = self.run_batch(replay)
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"), ("read_back", "AWF-2")], replay.calls)
        self.assertEqual([], result["remaining"])
        self.assertFalse(result["complete"])
        final = JiraSpy()
        self.run_batch(final)
        self.assertEqual([], final.calls)

    def test_unknown_stops_one_ticket_and_resumes_remaining_only(self):
        def fail(record):
            raise OSError("mutation receipt lost")
        first = self.run_batch(write_transition=fail)
        replay = JiraSpy()
        result = self.run_batch(replay)
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"), ("read_back", "AWF-2")], replay.calls)
        self.assertEqual(first["conflict"], result["conflict"])
        self.assertFalse(result["complete"])

    def test_malformed_write_and_readback_receipts(self):
        variants = [
            ("write_transition", {"issue_id": "foreign", "status": "ATTEMPTED", "observed_at": NOW}),
            ("write_transition", {"issue_id": "AWF-1", "status": "UNKNOWN", "observed_at": NOW}),
            ("read_transition", {"issue_id": "foreign", "status": "Done", "actor": "controller-1", "observed_at": NOW}),
            ("read_transition", {"issue_id": "AWF-1", "actor": "controller-1", "observed_at": NOW}),
            ("read_transition", {"issue_id": "AWF-1", "status": "Done", "actor": "controller-1", "observed_at": "bad"}),
            ("read_transition", {"issue_id": "AWF-1", "status": "Done", "actor": "controller-1", "observed_at": "2020-01-01T00:00:00Z"}),
            ("read_transition", {"issue_id": "AWF-1", "status": "Done", "actor": "other", "observed_at": NOW}),
        ]
        for index, (stage, value) in enumerate(variants):
            with self.subTest(stage=stage, value=value):
                self.store = self.controller.ContinuousControllerStore(
                    Path(self.temporary.name) / f"receipt-{index}.sqlite", ["B"])
                result = self.run_batch(**{stage: lambda *args: value})
                self.assertNotIn("AWF-1", result["reconciled"])
                self.assertEqual("AWF-1", result["conflict"]["issue_id"])
                replay = JiraSpy()
                self.run_batch(replay)
                self.assertFalse(any(ticket == "AWF-1" for _, ticket in replay.calls))

    def test_prior_ticket_stop_blocks_later_batch(self):
        self.run_batch(JiraSpy(mismatch="AWF-1"))
        batch = deepcopy(self.batch)
        batch["batch_id"] = "batch-2"
        for member in batch["constituents"]:
            member["batch_id"] = "batch-2"
        spy = JiraSpy()
        result = self.run_batch(spy, batch=batch, observed_merge={**self.merge, "batch_id": "batch-2"})
        self.assertFalse(any(ticket == "AWF-1" for _, ticket in spy.calls))
        self.assertEqual("AWF-1", result["conflict"]["issue_id"])

    def test_competing_input_cannot_replace_pin_or_roll_back_existing_state(self):
        from agentic import ValidationError
        changed = deepcopy(self.batch)
        changed["constituents"].reverse()
        competitor = JiraSpy()
        def read(binding):
            with self.assertRaises(ValidationError):
                self.run_batch(competitor, batch=changed)
            return JiraSpy().read_current_status(binding)
        result = self.run_batch(read_current_status=read)
        self.assertTrue(result["complete"])
        self.assertEqual([], competitor.calls)

    def test_custom_done_write_and_owner_keyword_hold(self):
        config = deepcopy(self.config)
        config["jira"]["status_map"]["done"] = "Closed"
        spy = JiraSpy()
        def after(record, operation):
            return {"issue_id": record["binding"]["issue_id"], "status": "Closed",
                    "actor": "controller-1", "observed_at": NOW}
        result = self.run_batch(spy, config=config, read_transition=after,
            policy_context={"AWF-2": {"snapshot": {"summary": "Release acceptance", "labels": []}}})
        self.assertEqual("Closed", spy.writes["AWF-1"]["to_status_id"])
        self.assertEqual(["AWF-2"], result["held"])
        self.assertFalse(result["complete"])

    def test_claim_failure_prevents_all_callbacks(self):
        import sqlite3
        from agentic import ValidationError
        with self.store.transaction() as db:
            db.execute(self.controller.BATCH_JIRA_DDL)
            db.execute("""CREATE TRIGGER test_refuse_claim BEFORE UPDATE ON controller_batch_jira_operation
                BEGIN SELECT RAISE(ABORT, 'claim unavailable'); END""")
        spy = JiraSpy()
        with self.assertRaises((sqlite3.DatabaseError, ValidationError)):
            self.run_batch(spy)
        self.assertEqual([], spy.calls)

    def test_failed_batch_preparation_rolls_back_pin_and_intents(self):
        import sqlite3
        from agentic import ValidationError
        with self.store.transaction() as db:
            db.execute(self.controller.BATCH_JIRA_DDL)
            db.execute("""CREATE TRIGGER test_refuse_second BEFORE INSERT ON controller_batch_jira_operation
                WHEN NEW.issue_id='AWF-2' BEGIN SELECT RAISE(ABORT, 'intent unavailable'); END""")
        spy = JiraSpy()
        with self.assertRaises((sqlite3.DatabaseError, ValidationError)):
            self.run_batch(spy)
        self.assertEqual([], spy.calls)
        with self.store.connection() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM controller_batch_jira_operation").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM controller_meta WHERE key LIKE 't11-batch:%'").fetchone()[0])

    def test_crash_after_mutation_before_receipt_never_reissues(self):
        class Crash(BaseException):
            pass
        spy = JiraSpy()
        def mutate_then_crash(record):
            spy.write_transition(record)
            raise Crash()
        with self.assertRaises(Crash):
            self.run_batch(spy, write_transition=mutate_then_crash)
        self.assertEqual([("read", "AWF-1"), ("write", "AWF-1")], spy.calls)
        replay = JiraSpy()
        result = self.run_batch(replay)
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"), ("read_back", "AWF-2")], replay.calls)
        self.assertEqual([], result["remaining"])
        self.assertFalse(result["complete"])
        final = JiraSpy()
        self.run_batch(final)
        self.assertEqual([], final.calls)

    def test_corrupt_durable_intent_is_rejected_without_callbacks(self):
        from agentic import ValidationError
        self.run_batch()
        with self.store.transaction() as db:
            row = db.execute("SELECT operation_id,record_json FROM controller_batch_jira_operation LIMIT 1").fetchone()
            corrupt = json.loads(row["record_json"])
            corrupt["to_status_id"] = "Other"
            db.execute("UPDATE controller_batch_jira_operation SET record_json=? WHERE operation_id=?",
                       (json.dumps(corrupt), row["operation_id"]))
        spy = JiraSpy()
        with self.assertRaises(ValidationError):
            self.run_batch(spy)
        self.assertEqual([], spy.calls)


if __name__ == "__main__":
    unittest.main()
