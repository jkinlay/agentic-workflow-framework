"""AWF-54B T11: controller-only batch Jira reconciliation (red contract)."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.canonical import canonical, fingerprint, sha256
from agentic.provider_identity import require_jira_write_identity

NOW = "2026-10-17T16:00:00Z"
PROVIDER = dict(cloud_id="cloud-1", site="https://jira.example.invalid",
    project_id="project-1", project_key="AWF", controller_actor_id="controller-1")


class JiraSpy:
    """In-memory provider callbacks; never import or contact a Jira client."""

    def __init__(self, mismatch=None):
        self.calls = []
        self.mismatch = mismatch
        self.writes = {}

    def read_current_status(self, binding):
        ticket = binding["issue_id"]
        self.calls.append(("read", ticket))
        return {"issue_id": ticket, "status_id": "In Review", "observed_at": NOW,
                "jira_provider": deepcopy(PROVIDER)}

    def write_transition(self, record):
        ticket = record["binding"]["issue_id"]
        self.calls.append(("write", ticket))
        self.writes[ticket] = deepcopy(record)
        return {"issue_id": ticket, "status": "ATTEMPTED", "observed_at": NOW,
                "operation_id": record["operation_id"], "jira_provider": deepcopy(PROVIDER)}

    def read_transition(self, record, operation):
        ticket = record["binding"]["issue_id"]
        self.calls.append(("read_back", ticket))
        return {
            "issue_id": ticket,
            "status": "In Progress" if ticket == self.mismatch else "Done",
            "actor": "external-automation" if ticket == self.mismatch else "controller-1",
            "observed_at": NOW,
            "jira_provider": deepcopy(PROVIDER),
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
        # This synthetic fixture owns main, independent of the adopting project.
        config["github"]["base_branch"] = "main"
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
                    observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
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
        # This synthetic fixture owns main, independent of the adopting project.
        self.config["github"]["base_branch"] = "main"
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
            write_transition=spy.write_transition, read_transition=spy.read_transition,
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": inputs["now"]})
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
                observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
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
                observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
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

    def interleaving_lifecycle(self, issue="AWF-1", writes=None, during_write=None):
        import uuid
        writes = [] if writes is None else writes
        provider = {"cloud_id": "cloud-1", "site": "https://jira.example.invalid",
                    "project_id": "project-1", "project_key": "AWF",
                    "controller_actor_id": "controller-1"}
        def write(record):
            writes.append(issue)
            if during_write is not None:
                during_write()
            return {"operation_id": record["operation_id"], "issue_id": issue,
                    "status": "ATTEMPTED", "observed_at": NOW, "jira_provider": provider}
        return self.controller.production_jira_lifecycle(self.store,
            config=self.config, contract={}, event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True},
            binding={"issue_id": issue, "project_id": "local-project",
                     "repository_id": "101", "requirements_hash": "a" * 64},
            issue_type="LEAF", state="DISPATCHED", producer_id="controller-1",
            run_id=str(uuid.uuid4()), now=NOW, evidence=["urn:fixture:interleaving"],
            transition_id="31", observe_provider_identity=lambda: provider,
                observe_issue_identity=lambda key: {"issue_key": key, "observed_at": NOW},
            read_current_status=lambda b: {"issue_id": issue, "status_id": "Ready",
                "observed_at": NOW, "jira_provider": provider}, write_transition=write,
            read_transition=lambda r, o: {"issue_id": issue, "status": "In Progress",
                "actor": "controller-1", "observed_at": NOW, "jira_provider": provider})

    def assert_stop_after_ordinary_claim(self, outcome):
        from unittest.mock import patch
        writes = []
        begin = self.store.begin_jira_operation
        def claimed(operation_id, now):
            result = begin(operation_id, now)
            spy = JiraSpy()
            self.run_batch(spy)
            # T11 observes the live ordinary claim and durably fails without
            # calling Jira. Also exercise the unresolved stop representation.
            self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"),
                              ("read_back", "AWF-2")], spy.calls)
            with self.store.transaction() as db:
                row = db.execute("SELECT status FROM controller_batch_jira_operation "
                                 "WHERE issue_id='AWF-1'").fetchone()
                self.assertEqual("FAILED", row["status"])
                if outcome == "UNKNOWN":
                    db.execute("UPDATE controller_batch_jira_operation SET status='UNKNOWN' "
                               "WHERE issue_id='AWF-1'")
            return result
        with patch.object(self.store, "begin_jira_operation", side_effect=claimed):
            result = self.interleaving_lifecycle(writes=writes)
        self.assertEqual([], writes, "ordinary writer ran after a durable T11 stop")
        self.assertTrue(result["writes_stopped"])
        row = self.store.jira_operations("AWF-1")[0]
        self.assertEqual("FAILED", row["status"])
        self.assertIsNone(row["operation_json"])
        self.store = self.controller.ContinuousControllerStore(self.path, ["B"])
        self.store.recover_jira_operations(NOW)
        self.assertEqual("FAILED", self.store.jira_operations("AWF-1")[0]["status"])
        self.assertTrue(self.interleaving_lifecycle(writes=writes)["writes_stopped"])
        self.assertEqual([], writes)
        self.assertEqual("SUCCEEDED", self.interleaving_lifecycle("AWF-3", writes)["record"]["status"])
        self.assertEqual(["AWF-3"], writes)

    def test_failed_t11_stop_between_ordinary_claim_and_writer(self):
        self.assert_stop_after_ordinary_claim("FAILED")

    def test_unknown_t11_stop_between_ordinary_claim_and_writer(self):
        self.assert_stop_after_ordinary_claim("UNKNOWN")

    def test_t11_claim_blocks_ordinary_before_t11_completes(self):
        writes = []
        spy = JiraSpy()
        def before(binding):
            if binding["issue_id"] == "AWF-1":
                self.assertTrue(self.interleaving_lifecycle(writes=writes)["writes_stopped"])
                self.assertEqual([], self.store.jira_operations("AWF-1"))
                self.assertEqual("SUCCEEDED", self.interleaving_lifecycle("AWF-3", writes)["record"]["status"])
            return spy.read_current_status(binding)
        self.assertTrue(self.run_batch(spy, read_current_status=before)["complete"])
        self.assertEqual(["AWF-3"], writes)

    def test_t11_cannot_stop_issue_during_ordinary_writer(self):
        from unittest.mock import patch
        finish = self.store.finish_jira_operation
        # The batch competes while the ordinary callback interval is owned;
        # it must defer this issue, while continuing the unrelated constituent.
        def writing(operation_id, status, observation, now, operation=None):
            spy = JiraSpy()
            result = self.run_batch(spy)
            self.assertIn("AWF-1", result["remaining"])
            self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"),
                              ("read_back", "AWF-2")], spy.calls)
            return finish(operation_id, status, observation, now, operation)
        with patch.object(self.store, "finish_jira_operation", side_effect=writing):
            self.assertEqual("SUCCEEDED", self.interleaving_lifecycle()["record"]["status"])
        self.assertTrue(self.run_batch()["complete"])

    def test_ordinary_writer_owns_issue_across_callback(self):
        observations = []
        def compete():
            # A second connection can commit while the external callback runs.
            # This would fail if the implementation held a DB write transaction.
            with self.store.transaction() as db:
                db.execute("INSERT INTO controller_meta VALUES('callback-probe','committed')")
            spy = JiraSpy()
            observations.append((self.run_batch(spy), spy.calls))
        result = self.interleaving_lifecycle(during_write=compete)
        self.assertEqual("SUCCEEDED", result["record"]["status"])
        batch_result, calls = observations[0]
        self.assertEqual(["AWF-1"], batch_result["remaining"])
        self.assertIsNone(batch_result["conflict"])
        self.assertEqual([("read", "AWF-2"), ("write", "AWF-2"),
                          ("read_back", "AWF-2")], calls)
        self.assertTrue(self.run_batch()["complete"])

    def test_other_process_t11_claim_blocks_ordinary_only_for_same_issue(self):
        import concurrent.futures
        process = self.start_batch_process("active")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            line = pool.submit(process.stdout.readline)
            try:
                self.assertEqual("CLAIMED", line.result(timeout=20).strip())
                writes = []
                self.assertTrue(self.interleaving_lifecycle(writes=writes)["writes_stopped"])
                self.assertEqual([], writes)
                self.assertEqual([], self.store.jira_operations("AWF-1"))
                self.assertEqual("SUCCEEDED", self.interleaving_lifecycle("AWF-3", writes)["record"]["status"])
                self.assertEqual(["AWF-3"], writes)
            finally:
                output, errors = process.communicate("resume\n", timeout=20)
        self.assertEqual(0, process.returncode, errors)
        self.assertTrue(json.loads(output)["complete"])

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
        provider = {key: inputs['config']['jira'][source] for key, source in (
            ('cloud_id', 'cloud_id'), ('site', 'site'), ('project_id', 'provider_project_id'),
            ('project_key', 'project_key'), ('controller_actor_id', 'controller_actor_id'))}
        def before(binding):
            return {'issue_id': binding['issue_id'], 'status_id': 'In Review', 'observed_at': inputs['now'], 'jira_provider': provider}
        def write(record):
            if sys.argv[4] == 'crash':
                os._exit(19)
            if record['binding']['issue_id'] == 'AWF-1':
                print('CLAIMED', flush=True)
                sys.stdin.readline()
            return {'issue_id': record['binding']['issue_id'], 'status': 'ATTEMPTED', 'observed_at': inputs['now'], 'jira_provider': provider, 'operation_id': record['operation_id']}
        def after(record, operation):
            return {'issue_id': record['binding']['issue_id'], 'status': 'Done',
                    'actor': 'controller-1', 'observed_at': inputs['now'], 'jira_provider': provider}
        result = production_batch_jira_reconciliation(store, role='controller', **inputs,
            read_current_status=before, write_transition=write, read_transition=after,
            observe_issue_identity=lambda key: {"issue_key": key, "observed_at": inputs["now"]})
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
            return {"issue_id": binding["issue_id"], "status_id": "Closed", "observed_at": NOW, "jira_provider": deepcopy(PROVIDER)}
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
                    "actor": "controller-1", "observed_at": NOW, "jira_provider": deepcopy(PROVIDER)}
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


class CanonicalIdentitySnapshotTests(unittest.TestCase):
    setUp = BatchReconciliationSafetyTests.setUp
    run_batch = BatchReconciliationSafetyTests.run_batch

    def test_batch_observer_mutation_cannot_replace_validated_inputs(self):
        spy = JiraSpy()
        observed = []

        def observe(key):
            observed.append(key)
            self.batch["constituents"][0]["binding"]["issue_id"] = "2001"
            self.batch["constituents"][1]["binding"]["issue_id"] = "2002"
            self.config["jira"]["status_map"]["done"] = "Wrong status"
            return {"issue_key": key, "observed_at": NOW}

        result = self.run_batch(spy, observe_issue_identity=observe)
        self.assertEqual(["AWF-1", "AWF-2"], observed)
        self.assertEqual([(operation, issue) for issue in ("AWF-1", "AWF-2")
                          for operation in ("read", "write", "read_back")], spy.calls)
        self.assertTrue(result["complete"])
        with self.store.connection() as db:
            rows = db.execute("SELECT issue_id, status, record_json FROM "
                              "controller_batch_jira_operation ORDER BY issue_id").fetchall()
        self.assertEqual([("AWF-1", "SUCCEEDED"), ("AWF-2", "SUCCEEDED")],
                         [(row["issue_id"], row["status"]) for row in rows])
        for row in rows:
            self.assertEqual("Done", json.loads(row["record_json"])["to_status_id"])

    def test_ordinary_callbacks_cannot_replace_observed_binding(self):
        import uuid
        from unittest.mock import patch
        provider = {"cloud_id": "cloud-1", "site": "https://jira.example.invalid",
                    "project_id": "project-1", "project_key": "AWF",
                    "controller_actor_id": "controller-1"}
        for stage in ("issue_observer", "provider_observer", "pre_read", "provider_recheck"):
            with self.subTest(stage=stage):
                store = self.controller.ContinuousControllerStore(
                    self.path.with_name(stage + ".sqlite"), ["B"])
                binding = {"issue_id": "AWF-1", "project_id": "local-project",
                           "repository_id": "101", "requirements_hash": "a" * 64}
                original = deepcopy(binding)
                calls, observed, locks = [], [], []
                provider_calls = []

                def mutate(point):
                    if stage == point:
                        binding["issue_id"] = "2001"
                        binding["requirements_hash"] = "b" * 64

                def observe(key):
                    observed.append(key)
                    mutate("issue_observer")
                    return {"issue_key": key, "observed_at": NOW}

                def identity():
                    provider_calls.append(True)
                    mutate("provider_observer" if len(provider_calls) == 1 else "provider_recheck")
                    return provider

                def read(selected):
                    calls.append(("read", selected["issue_id"]))
                    mutate("pre_read")
                    return {"issue_id": selected["issue_id"], "status_id": "Ready",
                            "observed_at": NOW, "jira_provider": provider}

                def write(record):
                    calls.append(("write", record["binding"]["issue_id"]))
                    return {"operation_id": record["operation_id"],
                            "issue_id": record["binding"]["issue_id"], "status": "ATTEMPTED",
                            "observed_at": NOW, "jira_provider": provider}

                def readback(record, operation):
                    calls.append(("read_back", record["binding"]["issue_id"]))
                    return {"issue_id": record["binding"]["issue_id"], "status": "In Progress",
                            "actor": "controller-1", "observed_at": NOW, "jira_provider": provider}

                ownership = self.controller._batch_jira_ownership
                def lock(selected_store, *, issue_id=None):
                    locks.append(issue_id)
                    return ownership(selected_store, issue_id=issue_id)

                with patch.object(self.controller, "_batch_jira_ownership", side_effect=lock):
                    result = self.controller.production_jira_lifecycle(store,
                        config=self.config, contract={}, event="WORKER_STARTED",
                        facts={"run_registered": True, "worktree_verified": True},
                        binding=binding, issue_type="LEAF", state="DISPATCHED",
                        producer_id="controller-1", run_id=str(uuid.uuid4()), now=NOW,
                        evidence=["urn:fixture:identity-snapshot"], transition_id="31",
                        observe_issue_identity=observe, observe_provider_identity=identity,
                        read_current_status=read, write_transition=write, read_transition=readback)
                self.assertEqual(["AWF-1"], observed)
                self.assertEqual([("read", "AWF-1"), ("write", "AWF-1"),
                                  ("read_back", "AWF-1")], calls)
                self.assertEqual(["AWF-1"], locks)
                self.assertEqual("SUCCEEDED", result["record"]["status"])
                self.assertEqual(original, result["record"]["binding"])
                self.assertEqual([], store.jira_operations("2001"))
                self.assertEqual(original, store.jira_operations("AWF-1")[0]["record"]["binding"])


class MissingCanonicalObserverTests(unittest.TestCase):
    setUp = BatchReconciliationSafetyTests.setUp

    def test_ordinary_missing_issue_observer_records_refusal_without_transition(self):
        spy = JiraSpy()
        result = self.controller.production_jira_lifecycle(self.store,
            config=self.config, contract={}, event="WORKER_STARTED",
            facts={"run_registered": True, "worktree_verified": True},
            binding={"issue_id": "AWF-1"}, issue_type="LEAF", state="DISPATCHED",
            producer_id="controller-1", run_id="fixture", now=NOW, evidence=[],
            transition_id="31", observe_issue_identity=None,
            observe_provider_identity=lambda: self.fail("provider lookup before refusal"),
            read_current_status=spy.read_current_status,
            write_transition=spy.write_transition, read_transition=spy.read_transition)
        self.assertEqual("REFUSED", result["status"])
        self.assertIn("canonical issue observer", result["reason"])
        self.assertTrue(result["writes_stopped"])
        self.assertFalse(result["planned"])
        self.assertFalse(result["execution_authority"])
        self.assertEqual([], spy.calls)
        self.assertEqual([], self.store.jira_operations("AWF-1"))

    def test_batch_missing_issue_observer_records_refusal_without_t11_intent(self):
        spy = JiraSpy()
        with self.store.transaction() as db:
            db.execute(self.controller.BATCH_JIRA_DDL)
        result = self.controller.production_batch_jira_reconciliation(self.store,
            config=self.config, role="controller", batch=self.batch,
            observed_merge=self.merge, now=NOW, observe_issue_identity=None,
            read_current_status=spy.read_current_status,
            write_transition=spy.write_transition, read_transition=spy.read_transition)
        with self.store.connection() as db:
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM controller_batch_jira_operation").fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT count(*) FROM controller_meta WHERE key='t11-batch:batch-1'").fetchone()[0])
        self.assertEqual([], spy.calls)
        self.assertEqual("REFUSED", result["status"])
        self.assertIn("canonical issue observer", result["reason"])
        self.assertTrue(result["writes_stopped"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["count_allowed"])
        self.assertFalse(result["execution_authority"])


class RetainedCoordinatorFixture:
    """Doc 49 items 2-4: reviewed coordinator; retained bytes, no caller evidence.

    Audit reads check namespace/key/identity/bytes/digest. Identical append is
    idempotent; conflicting append is refused; no replace or purge operation.
    W1 replaces this fixture after merge; operational assertions stay.
    """
    def __init__(self, candidate):
        self.candidate = deepcopy(candidate)
        self.events = {}
        self.append_audit(self.candidate)

    def append_audit(self, candidate):
        from agentic.canonical import canonical, sha256
        from agentic import ValidationError
        key = (candidate['repository_id'], candidate['batch_id'])
        raw = canonical({k:v for k,v in candidate.items() if k != 'audit_sha256'})
        candidate['audit_sha256'] = sha256(raw)
        event = ('awf-merge-train-audit-1', key, raw, sha256(raw))
        if key in self.events and self.events[key] != event:
            raise ValidationError('immutable audit conflict')
        self.events[key] = event
        return deepcopy(event)

    def __call__(self, request):
        from agentic import ValidationError
        from agentic.canonical import canonical, sha256
        if set(request) != {'operation', 'repository_id', 'batch_id'} or request['operation'] != 'load':
            raise ValidationError('fixture accepts explicit retained load only')
        key = (request['repository_id'], request['batch_id'])
        event = self.events[key]
        raw = canonical({k:v for k,v in self.candidate.items() if k != 'audit_sha256'})
        if (event != ('awf-merge-train-audit-1', key, raw, sha256(raw))
                or self.candidate['audit_sha256'] != sha256(raw)):
            raise ValidationError('invalid retained audit')
        from agentic.closeout import validate_retained_batch_candidate
        validate_retained_batch_candidate(self.candidate, repository_id=key[0], batch_id=key[1])
        return deepcopy(self.candidate)


def reviewed_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location('awf_controller_cli_fixture', ROOT / '.agentic/scripts/continuous_controller.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OperationalControllerTests(unittest.TestCase):
    setUp = BatchReconciliationSafetyTests.setUp
    run_batch = BatchReconciliationSafetyTests.run_batch

    def test_operational_restart_with_fresh_merge_observation_resumes_remaining_only(self):
        from unittest.mock import patch
        spy = JiraSpy()
        inputs = self.operational_inputs(spy)
        def lost(record):
            spy.write_transition(record)
            raise OSError('lost response')
        inputs['write_transition'] = lost
        first = self.controller.production_batch_closeout(self.store, **inputs)
        with self.store.connection() as db:
            original = [dict(row) for row in db.execute(
                'SELECT operation_id,intent_sha256,record_json,status FROM controller_batch_jira_operation ORDER BY issue_id')]
        self.store = self.controller.ContinuousControllerStore(self.path, ['B'])
        replay = JiraSpy()
        later = '2026-10-17T16:01:00Z'
        # Fresh authenticated merge/policy/identity observations on restart.
        with patch.dict(globals(), NOW=later):
            result = self.controller.production_batch_closeout(self.store, **self.operational_inputs(replay))
        self.assertEqual([('read','AWF-2'),('write','AWF-2'),('read_back','AWF-2')], replay.calls)
        self.assertEqual(first['jira']['conflict'], result['jira']['conflict'])
        self.assertFalse(result['count_allowed'])
        with self.store.connection() as db:
            retained = [dict(row) for row in db.execute(
                'SELECT operation_id,intent_sha256,record_json,status FROM controller_batch_jira_operation ORDER BY issue_id')]
        self.assertEqual(original[0], retained[0])
        self.assertEqual('UNKNOWN', retained[0]['status'])
        self.assertEqual('SUCCEEDED', retained[1].pop('status'))
        original[1].pop('status')
        self.assertEqual(original[1], retained[1])

    def test_jira_lifecycle_provider_admission_precedes_all_issue_callbacks(self):
        for mode in ['unbound', 'unavailable', 'failing', 'mismatch', 'accepted']:
            with self.subTest(mode=mode):
                config = deepcopy(self.config)
                calls = []
                def provider():
                    calls.append('provider')
                    if mode == 'failing':
                        raise OSError('unavailable')
                    return dict(PROVIDER, cloud_id='foreign') if mode == 'mismatch' else deepcopy(PROVIDER)
                def canonical(key):
                    calls.append('canonical')
                    return {'issue_key': key, 'observed_at': NOW}
                def current(binding):
                    calls.append('status')
                    raise OSError('stop after admission')
                if mode == 'unbound':
                    config['jira']['cloud_id'] = None
                result = self.controller.production_jira_lifecycle(self.store,
                    config=config, contract={}, event='WORKER_STARTED', facts={},
                    binding={'issue_id':'AWF-1'}, issue_type='LEAF', state='DISPATCHED',
                    producer_id='controller-1', run_id='fixture', now=NOW,
                    evidence=[], transition_id='31', observe_issue_identity=canonical,
                    observe_provider_identity=None if mode == 'unavailable' else provider,
                    read_current_status=current,
                    write_transition=lambda r: calls.append('write'),
                    read_transition=lambda r,o: calls.append('readback'))
                self.assertTrue(result['writes_stopped'])
                self.assertEqual(['provider','canonical','status'] if mode == 'accepted'
                    else ['provider'] if mode in ['failing','mismatch'] else [], calls)
                if mode != 'accepted':
                    self.assertEqual(0, result['issue_lookups'])

    def test_batch_jira_receipts_reject_foreign_provider_and_operation(self):
        for mode in ['before', 'write', 'write-provider', 'after']:
            with self.subTest(mode=mode):
                self.store = self.controller.ContinuousControllerStore(
                    Path(self.temporary.name) / f'receipt-{mode}.sqlite', ['B'])
                spy = JiraSpy()
                def before(binding):
                    value = spy.read_current_status(binding)
                    if mode == 'before':
                        value.update(status_id='Done', jira_provider=dict(PROVIDER, cloud_id='foreign'))
                    return value
                def write(record):
                    value = spy.write_transition(record)
                    if mode == 'write':
                        value['operation_id'] = 'foreign-operation'
                    if mode == 'write-provider':
                        value['jira_provider'] = dict(PROVIDER, cloud_id='foreign')
                    return value
                def after(record, operation):
                    value = spy.read_transition(record, operation)
                    if mode == 'after':
                        value['jira_provider'] = dict(PROVIDER, project_id='foreign')
                    return value
                result = self.run_batch(spy, read_current_status=before,
                    write_transition=write, read_transition=after)
                self.assertFalse(result['complete'])
                self.assertFalse(result['count_allowed'])
                self.assertEqual([], result['reconciled'])
                self.assertEqual([('read','AWF-1')] +
                    ([('write','AWF-1')] if mode != 'before' else []) +
                    ([('read_back','AWF-1')] if mode == 'after' else []), spy.calls)
                with self.store.connection() as db:
                    self.assertEqual('UNKNOWN', db.execute(
                        "SELECT status FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()[0])

    def test_restart_retains_legacy_timestamp_pin_and_intents(self):
        from unittest.mock import patch
        from agentic.canonical import canonical, fingerprint, sha256
        spy = JiraSpy()
        inputs = self.operational_inputs(spy)
        def lost(record):
            spy.write_transition(record)
            raise OSError('lost response')
        inputs['write_transition'] = lost
        self.controller.production_batch_closeout(self.store, **inputs)
        # Reconstruct the exact pre-fix timestamp-bearing pin and fingerprints.
        with self.store.transaction() as db:
            pin = json.loads(db.execute("SELECT value FROM controller_meta WHERE key='t11-batch:batch-1'").fetchone()[0])
            pin['observed_merge']['observed_at'] = NOW
            encoded = canonical(pin)
            db.execute("UPDATE controller_meta SET value=? WHERE key='t11-batch:batch-1'", (encoded.decode(),))
            for row in db.execute('SELECT * FROM controller_batch_jira_operation').fetchall():
                record = json.loads(row['record_json'])
                record['batch_input_sha256'] = sha256(encoded)
                intent = fingerprint('controller-jira-operation', {k:v for k,v in record.items() if k != 'created_at'})
                db.execute('UPDATE controller_batch_jira_operation SET record_json=?,intent_sha256=? WHERE operation_id=?',
                    (canonical(record).decode(), intent, row['operation_id']))
            original = [tuple(row) for row in db.execute(
                'SELECT operation_id,intent_sha256,record_json FROM controller_batch_jira_operation ORDER BY issue_id')]
        replay = JiraSpy()
        with patch.dict(globals(), NOW='2026-10-17T16:01:00Z'):
            result = self.controller.production_batch_closeout(self.store, **self.operational_inputs(replay))
        self.assertEqual(['AWF-2'], result['jira']['reconciled'])
        self.assertEqual([('read','AWF-2'),('write','AWF-2'),('read_back','AWF-2')], replay.calls)
        self.assertFalse(result['count_allowed'])
        with self.store.connection() as db:
            self.assertEqual(encoded.decode(), db.execute("SELECT value FROM controller_meta WHERE key='t11-batch:batch-1'").fetchone()[0])
            self.assertEqual(original, [tuple(row) for row in db.execute(
                'SELECT operation_id,intent_sha256,record_json FROM controller_batch_jira_operation ORDER BY issue_id')])

    def operational_inputs(self, spy=None):
        spy = spy or JiraSpy()
        self.config['github'].update(repository_id=101, repository='example/project')
        candidate = dict(repository_id=101, batch_id='batch-1', generation=1,
            pr=44, head='d'*40, tree='e'*40, base='f'*40,
            state='MERGED_OBSERVED', audit_sha256='a'*64,
            sources=[dict(pr=m['pr_number'], head=m['reviewed_head_sha'],
                issue_key=m['binding']['issue_id'], paths=m['paths']) for m in self.batch['constituents']])
        self.coordinator = RetainedCoordinatorFixture(candidate)
        script = reviewed_script()
        provider = {'host': self.config['github']['host'], 'repository': 'example/project', 'repository_id': 101}
        repository = {'repository': 'example/project', 'repository_id': 101}
        self.merge_response = dict(repository_id=101, batch_id='batch-1', generation=1,
            pr=44, head='d'*40, tree='e'*40, base='f'*40,
            target_branch='main', merge_commit_sha='d'*40, observed_at=NOW)
        registry = script._adapter_observer_registration('merge-fixture', 'BATCH_MERGE',
            lambda request: {'observed_at': NOW, 'result': deepcopy(self.merge_response)},
            'a'*64, provider_identity=provider, repository_identity=repository)
        return dict(config=self.config, request={'operation':'load','repository_id':101,'batch_id':'batch-1'},
            coordinator=self.coordinator, merge_observer_id='merge-fixture', merge_observer_registry=registry,
            observe_issue_identity=lambda key: {'issue_key': key, 'observed_at': NOW},
            observe_issue_policy=lambda key: {'issue_key':key, 'issue_type':'LEAF',
                'snapshot':{'summary':'Routine work', 'labels':[]}, 'observed_at':NOW},
            observe_provider_identity=lambda: {'cloud_id':'cloud-1','site':'https://jira.example.invalid',
                'project_id':'project-1','project_key':'AWF','controller_actor_id':'controller-1'},
            now=NOW, read_current_status=spy.read_current_status,
            write_transition=spy.write_transition, read_transition=spy.read_transition)

    def test_jira_alias_input_rejected_before_dispatch(self):
        from agentic import ValidationError
        with self.store.transaction() as db:
            db.execute(self.controller.BATCH_JIRA_DDL)
        for alias in ['10001', 'awf-1', 'OLD-1']:
            spy = JiraSpy()
            bad = deepcopy(self.batch)
            bad['constituents'][0]['binding']['issue_id'] = alias
            with self.assertRaisesRegex(ValidationError, 'canonical'):
                self.run_batch(spy, batch=bad,
                    observe_issue_identity=lambda key: {'issue_key':'AWF-1','observed_at':NOW})
            self.assertEqual([], spy.calls)
            with self.store.connection() as db:
                self.assertEqual(0, db.execute('SELECT count(*) FROM controller_batch_jira_operation').fetchone()[0])
            with self.assertRaisesRegex(ValidationError, 'canonical'):
                self.controller.production_jira_lifecycle(self.store, config=self.config,
                    contract={}, event='WORKER_STARTED', facts={}, binding={'issue_id':alias},
                    issue_type='LEAF', state='DISPATCHED', producer_id='controller-1', run_id='fixture',
                    now=NOW, evidence=[], transition_id='31', read_current_status=spy.read_current_status,
                    write_transition=spy.write_transition, read_transition=spy.read_transition,
                    observe_provider_identity=lambda: deepcopy(PROVIDER),
                    observe_issue_identity=lambda key: {'issue_key':'AWF-1','observed_at':NOW})
            self.assertEqual([], spy.calls)

    def test_numeric_2001_rejected_before_any_callback_or_intent(self):
        from agentic import ValidationError
        calls = []
        def forbidden(*args):
            calls.append(args)
            self.fail('numeric issue dispatched a callback')
        with self.store.transaction() as db:
            db.execute(self.controller.BATCH_JIRA_DDL)
        bad = deepcopy(self.batch)
        bad['constituents'][0]['binding']['issue_id'] = '2001'
        with self.assertRaisesRegex(ValidationError, 'canonical'):
            self.run_batch(batch=bad,
                read_current_status=forbidden, write_transition=forbidden,
                read_transition=forbidden)
        with self.assertRaisesRegex(ValidationError, 'canonical'):
            self.controller.production_jira_lifecycle(self.store, config=self.config,
                contract={}, event='WORKER_STARTED', facts={}, binding={'issue_id':'2001'},
                issue_type='LEAF', state='DISPATCHED', producer_id='controller-1', run_id='fixture',
                now=NOW, evidence=[], transition_id='31', observe_provider_identity=forbidden,
                observe_issue_identity=forbidden,
                read_current_status=forbidden,
                write_transition=forbidden, read_transition=forbidden)
        self.assertEqual([], calls)
        self.assertEqual([], self.store.jira_operations('2001'))
        with self.store.connection() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM controller_batch_jira_operation').fetchone()[0])

    def test_provider_issue_alias_cannot_bypass_batch_stop_or_issue_lock(self):
        from agentic import ValidationError
        self.run_batch(JiraSpy(mismatch='AWF-1'))
        spy = JiraSpy()
        inputs = self.operational_inputs(spy)
        self.coordinator.candidate['sources'][0]['issue_key'] = 'OLD-1'
        self.coordinator.events.clear()
        self.coordinator.append_audit(self.coordinator.candidate)
        inputs['observe_issue_identity'] = lambda key: {'issue_key':'AWF-1','observed_at':NOW}
        for locked in [False, True]:
            with self.controller._batch_jira_ownership(self.store, issue_id='AWF-1'):
                with self.assertRaisesRegex(ValidationError, 'canonical'):
                    self.controller.production_batch_closeout(self.store, **inputs)
            self.assertEqual([], spy.calls)

    def test_operational_restart_preserves_unknown_and_resumes_remaining_only(self):
        spy = JiraSpy()
        inputs = self.operational_inputs(spy)
        def lost(record):
            spy.write_transition(record)
            raise OSError('lost response')
        inputs['write_transition'] = lost
        first = self.controller.production_batch_closeout(self.store, **inputs)
        self.store = self.controller.ContinuousControllerStore(self.path, ['B'])
        replay = JiraSpy()
        result = self.controller.production_batch_closeout(self.store, **self.operational_inputs(replay))
        self.assertEqual([('read','AWF-2'),('write','AWF-2'),('read_back','AWF-2')], replay.calls)
        self.assertEqual(first['jira']['conflict'], result['jira']['conflict'])
        self.assertFalse(result['count_allowed'])
        with self.store.connection() as db:
            self.assertEqual('UNKNOWN', db.execute("SELECT status FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()[0])

    def test_operational_provider_policy_holds_and_disabled_jira(self):
        for issue_type, summary in [('EPIC','Routine work'), ('LEAF','Release acceptance')]:
            self.store = self.controller.ContinuousControllerStore(
                Path(self.temporary.name) / f'policy-{issue_type}.sqlite', ['B'])
            spy = JiraSpy()
            inputs = self.operational_inputs(spy)
            inputs['observe_issue_policy'] = lambda key: {'issue_key':key,
                'issue_type':issue_type, 'snapshot':{'summary':summary,'labels':[]},'observed_at':NOW}
            result = self.controller.production_batch_closeout(self.store, **inputs)
            self.assertEqual(['AWF-1','AWF-2'], result['jira']['held'])
            self.assertFalse(result['count_allowed'])
            self.assertEqual([], spy.calls)
        inputs = self.operational_inputs()
        inputs['config']['jira']['enabled'] = False
        for name in ['observe_issue_identity','observe_provider_identity','observe_issue_policy',
                'read_current_status','write_transition','read_transition']:
            inputs[name] = lambda *args: self.fail('disabled Jira callback')
        result = self.controller.production_batch_closeout(self.store, **inputs)
        self.assertFalse(result['count_allowed'])

    def test_batch_command_rejects_unpinned_adapters_and_caller_asserted_evidence(self):
        from agentic import ValidationError
        script = reviewed_script()
        for pin in [None, '0'*64]:
            with self.assertRaises(ValidationError):
                script._load_reviewed_adapters(Path(script.__file__).resolve(), pin, None)
        for extra in ['authenticated', 'observed_merge', 'candidate', 'audit_sha256']:
            spy = JiraSpy()
            inputs = self.operational_inputs(spy)
            inputs['request'][extra] = True
            with self.assertRaises(ValidationError):
                self.controller.production_batch_closeout(self.store, **inputs)
            self.assertEqual([], spy.calls)
        with self.assertRaises(ValidationError):
            script._batch_operation_request({'operation':'merge','repository_id':101,'batch_id':'batch-1'})


# Persisted receipts model unchecked legacy outputs; intent bytes remain valid.
class PersistedCompletionTests(unittest.TestCase):
    setUp = BatchReconciliationSafetyTests.setUp
    operational_inputs = OperationalControllerTests.operational_inputs

    def rows(self):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute(
                'SELECT * FROM controller_batch_jira_operation ORDER BY issue_id')]

    def pin(self):
        with self.store.connection() as db:
            return db.execute("SELECT value FROM controller_meta WHERE key='t11-batch:batch-1'").fetchone()[0]

    def close(self, spy, **overrides):
        inputs = self.operational_inputs(spy)
        inputs.update(overrides)
        return self.controller.production_batch_closeout(self.store, **inputs)['jira']

    def reopen(self):
        self.store = self.controller.ContinuousControllerStore(self.path, ['B'])

    def b1(self, already_done):
        self.config['jira']['site'] += '/'
        admitted = require_jira_write_identity(self.config, deepcopy(PROVIDER))
        self.assertEqual(PROVIDER, admitted)
        spy = JiraSpy()
        def before(binding):
            value = spy.read_current_status(binding)
            if already_done:
                value['status_id'] = 'Done'
            return value
        first = self.close(spy, read_current_status=before)
        initial = self.rows()
        original_pin = self.pin()
        self.reopen()
        replay = JiraSpy()
        # Restart must trust retained valid receipts without any callback replay.
        def raw_before(binding):
            value = replay.read_current_status(binding)
            value['jira_provider']['site'] += '/'
            value['status_id'] = 'Done'
            return value
        with patch.dict(globals(), NOW='2026-10-17T16:01:00Z'):
            second = self.close(replay, read_current_status=raw_before)
        self.assertEqual(original_pin, self.pin())
        self.assertEqual(initial[0], self.rows()[0])
        self.assertNotIn(('read', 'AWF-1'), replay.calls)
        self.assertTrue(first['complete'], 'admitted normalized provider must reconcile')
        self.assertTrue(second['complete'])
        self.assertEqual([], replay.calls)
        self.assertEqual(['SUCCEEDED', 'SUCCEEDED'], [row['status'] for row in initial])
        self.assertEqual([('read', issue) for issue in ('AWF-1', 'AWF-2')] if already_done else [(op, issue) for issue in ('AWF-1', 'AWF-2') for op in ('read', 'write', 'read_back')], spy.calls)

    def test_b1_already_done(self):
        self.b1(True)

    def test_b1_write_readback(self):
        self.b1(False)

    def seed_legacy(self, mode):
        spy = JiraSpy()
        def done(binding):
            return dict(spy.read_current_status(binding), status_id='Done')
        result = self.close(spy, **({'read_current_status': done} if mode == 'before-provider' else {}))
        self.assertTrue(result['complete'])
        with self.store.transaction() as db:
            pin = json.loads(self.pin())
            pin['observed_merge']['observed_at'] = NOW
            encoded = canonical(pin)
            db.execute("UPDATE controller_meta SET value=? WHERE key='t11-batch:batch-1'", (encoded.decode(),))
            for row in db.execute('SELECT * FROM controller_batch_jira_operation').fetchall():
                record = json.loads(row['record_json'])
                record['batch_input_sha256'] = sha256(encoded)
                intent = fingerprint('controller-jira-operation', {k:v for k,v in record.items() if k != 'created_at'})
                db.execute('UPDATE controller_batch_jira_operation SET record_json=?,intent_sha256=? WHERE operation_id=?',
                           (canonical(record).decode(), intent, row['operation_id']))
            row = db.execute("SELECT * FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()
            column = 'operation_json' if (mode.startswith('write-') or mode in ('json-null-operation', 'early-write')) else 'readback_json'
            value = json.loads(row[column])
            if mode in ('before-provider', 'after-provider', 'write-provider'):
                value['jira_provider']['cloud_id'] = 'foreign'
            elif mode == 'write-operation':
                value['operation_id'] = 'foreign-operation'
            elif mode in ('missing-provider', 'write-missing-provider'):
                del value['jira_provider']
            elif mode == 'write-missing-operation':
                del value['operation_id']
            elif mode == 'json-null-operation':
                value = None
            elif mode == 'foreign-issue':
                value['issue_id'] = 'AWF-999'
            elif mode == 'wrong-status':
                value['status'] = 'In Review'
            elif mode == 'wrong-actor':
                value['actor'] = 'foreign'
            elif mode == 'malformed-time':
                value['observed_at'] = 'bad'
            elif mode == 'early-write':
                value['observed_at'] = '2026-10-17T15:59:59Z'
            elif mode == 'early-readback':
                value['observed_at'] = '2026-10-17T15:59:59Z'
            elif mode == 'extra-field':
                value['extra'] = True
            db.execute('UPDATE controller_batch_jira_operation SET ' + column + '=? WHERE issue_id=?',
                       (canonical(value).decode(), 'AWF-1'))

    def test_b2_legacy_success_must_validate_evidence(self):
        for mode in ('before-provider', 'after-provider', 'write-provider', 'write-operation',
                     'missing-provider', 'write-missing-provider', 'write-missing-operation',
                     'json-null-operation', 'foreign-issue', 'wrong-status', 'wrong-actor',
                     'malformed-time', 'early-write', 'early-readback', 'extra-field'):
            with self.subTest(mode=mode):
                self.path = Path(self.temporary.name) / (mode + '.sqlite')
                self.reopen()
                self.seed_legacy(mode)
                original, pin = self.rows(), self.pin()
                self.reopen()
                spy = JiraSpy()
                def forbidden(*args):
                    self.fail('completed historical operation replayed a provider callback')
                for later in ('2026-10-17T16:01:00Z', '2026-10-17T16:02:00Z'):
                    with patch.dict(globals(), NOW= later):
                        result = self.close(spy, read_current_status=forbidden,
                                            write_transition=forbidden, read_transition=forbidden)
                    self.assertEqual(pin, self.pin())
                    current = self.rows()
                    for a,b in zip(original, current):
                        for key in ('operation_id','intent_sha256','record_json','operation_json','readback_json'):
                            self.assertEqual(a[key], b[key])
                self.assertEqual([], spy.calls)
                self.assertFalse(result['count_allowed'])
                self.assertFalse(result['complete'], 'unproven legacy completion must be quarantined')
                self.assertEqual('UNKNOWN', current[0]['status'])
                self.assertEqual(['AWF-2'], result['reconciled'])
                self.assertEqual('AWF-1', result['conflict']['issue_id'])

    def test_control_valid_historical_success(self):
        spy = JiraSpy()
        self.assertTrue(self.close(spy)['complete'])
        before = self.rows()
        self.reopen()
        replay = JiraSpy()
        with patch.dict(globals(), NOW='2026-10-17T16:01:00Z'):
            self.assertTrue(self.close(replay)['complete'])
        self.assertEqual([], replay.calls)
        self.assertEqual(before, self.rows())



    def test_invalid_completion_without_ownership_never_reports_success(self):
        from contextlib import contextmanager
        real_ownership = self.controller._batch_jira_ownership
        for blocked in ('batch', 'issue'):
            with self.subTest(blocked=blocked):
                self.path = Path(self.temporary.name) / (blocked + '.sqlite')
                self.reopen()
                self.seed_legacy('after-provider')
                original = self.rows()
                @contextmanager
                def ownership(store, *, issue_id=None):
                    if (blocked == 'batch' and issue_id is None) or (blocked == 'issue' and issue_id == 'AWF-1'):
                        yield False
                    else:
                        with real_ownership(store, issue_id=issue_id) as owned:
                            yield owned
                spy = JiraSpy()
                with patch.object(self.controller, '_batch_jira_ownership', ownership):
                    result = self.close(spy)
                self.assertFalse(result['complete'])
                self.assertEqual(['AWF-2'], result['reconciled'])
                self.assertEqual('AWF-1', result['conflict']['issue_id'])
                self.assertEqual(original, self.rows())
                self.assertEqual([], spy.calls)
                self.assertFalse(self.close(JiraSpy())['complete'])
                self.assertEqual('UNKNOWN', self.rows()[0]['status'])

    def test_quarantine_persistence_failure_prevents_callbacks_and_success(self):
        import sqlite3
        from contextlib import contextmanager
        self.seed_legacy('after-provider')
        original = self.rows()
        transaction = self.store.transaction
        @contextmanager
        def failing_transaction():
            with transaction() as db:
                def authorizer(action, table, column, *args):
                    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and table == 'controller_batch_jira_operation' and column == 'status' else sqlite3.SQLITE_OK
                db.set_authorizer(authorizer)
                yield db
        spy = JiraSpy()
        with patch.object(self.store, 'transaction', failing_transaction), self.assertRaises(sqlite3.DatabaseError):
            self.close(spy)
        self.assertEqual([], spy.calls)
        self.assertEqual(original, self.rows())

    def test_invalid_later_success_quarantines_before_pending_callback(self):
        self.seed_legacy('after-provider')
        with self.store.transaction() as db:
            # AWF-1 is pending and precedes the invalid retained success on AWF-2.
            bad = json.loads(db.execute("SELECT readback_json FROM controller_batch_jira_operation WHERE issue_id='AWF-1'").fetchone()[0])
            before = dict(issue_id='AWF-2', status_id='Done', observed_at=NOW, jira_provider=bad['jira_provider'])
            db.execute("UPDATE controller_batch_jira_operation SET operation_json=NULL,readback_json=? WHERE issue_id='AWF-2'", (canonical(before).decode(),))
            db.execute("UPDATE controller_batch_jira_operation SET status='PENDING',operation_json=NULL,readback_json=NULL WHERE issue_id='AWF-1'")
        spy = JiraSpy()
        result = self.close(spy)
        self.assertFalse(result['complete'])
        self.assertEqual([], spy.calls)
        self.assertEqual(['PENDING', 'UNKNOWN'], [row['status'] for row in self.rows()])
        self.assertEqual('AWF-2', result['conflict']['issue_id'])
        resumed = JiraSpy()
        self.close(resumed)
        self.assertEqual([('read', 'AWF-1'), ('write', 'AWF-1'), ('read_back', 'AWF-1')], resumed.calls)

    def test_valid_legacy_no_write_completion_preserves_historical_receipts(self):
        spy = JiraSpy()
        self.close(spy, read_current_status=lambda binding: dict(spy.read_current_status(binding), status_id='Done'))
        original, pin = self.rows(), self.pin()
        self.reopen()
        replay = JiraSpy()
        with patch.dict(globals(), NOW='2026-10-17T17:00:00Z'):
            result = self.close(replay)
        self.assertTrue(result['complete'])
        self.assertEqual([], replay.calls)
        self.assertEqual(original, self.rows())
        self.assertEqual(pin, self.pin())


class RetainedReceiptStorageTests(unittest.TestCase):
    """SQLite storage types are distinct from JSON text null and JSON objects."""

    setUp = PersistedCompletionTests.setUp
    operational_inputs = PersistedCompletionTests.operational_inputs
    rows = PersistedCompletionTests.rows
    pin = PersistedCompletionTests.pin
    close = PersistedCompletionTests.close
    reopen = PersistedCompletionTests.reopen

    def fresh(self, label):
        self.path = Path(self.temporary.name) / (label + '.sqlite')
        self.reopen()

    def storage_values(self):
        return (None, b'', b'{}', b'not-json')

    def corrupt(self, column, value, status='SUCCEEDED'):
        with self.store.transaction() as db:
            db.execute('UPDATE controller_batch_jira_operation SET ' + column +
                       '=?,status=? WHERE issue_id=?', (value, status, 'AWF-1'))

    def test_storage_success_quarantines_without_replaying(self):
        for column in ('readback_json', 'operation_json'):
            for index, value in enumerate(self.storage_values()):
                with self.subTest(column=column, value=value):
                    self.fresh(column + str(index))
                    self.assertTrue(self.close(JiraSpy())['complete'])
                    self.corrupt(column, value)
                    original, pin = self.rows(), self.pin()
                    self.reopen()
                    for _ in range(2):
                        spy = JiraSpy()
                        result = self.close(spy)
                        self.assertFalse(result['complete'])
                        self.assertFalse(result['count_allowed'])
                        self.assertEqual(['AWF-2'], result['reconciled'])
                        self.assertEqual('AWF-1', result['conflict']['issue_id'])
                        self.assertEqual([], spy.calls)
                        self.assertEqual('UNKNOWN', self.rows()[0]['status'])
                        self.assertEqual(pin, self.pin())
                        for before, after in zip(original, self.rows()):
                            for key in ('operation_id', 'intent_sha256', 'record_json',
                                        'operation_json', 'readback_json'):
                                self.assertEqual(before[key], after[key])

    def test_storage_unknown_reports_and_resumes_remaining_only(self):
        for index, value in enumerate(self.storage_values()):
            with self.subTest(value=value):
                self.fresh('unknown' + str(index))
                self.close(JiraSpy())
                self.corrupt('readback_json', value, 'UNKNOWN')
                with self.store.transaction() as db:
                    db.execute("UPDATE controller_batch_jira_operation SET status='PENDING',operation_json=NULL,readback_json=NULL WHERE issue_id='AWF-2'")
                original = self.rows()[0]
                self.reopen()
                spy = JiraSpy()
                result = self.close(spy)
                self.assertFalse(result['complete'])
                self.assertEqual(['AWF-2'], result['reconciled'])
                self.assertEqual('AWF-1', result['conflict']['issue_id'])
                self.assertEqual([('read', 'AWF-2'), ('write', 'AWF-2'),
                                  ('read_back', 'AWF-2')], spy.calls)
                self.assertEqual(original, self.rows()[0])
                replay = JiraSpy()
                self.assertFalse(self.close(replay)['complete'])
                self.assertEqual([], replay.calls)

    def test_storage_success_ownership_denial_preserves_bytes(self):
        from contextlib import contextmanager
        real_ownership = self.controller._batch_jira_ownership
        for blocked in ('batch', 'issue'):
            for index, value in enumerate(self.storage_values()):
                with self.subTest(blocked=blocked, value=value):
                    self.fresh(blocked + str(index))
                    self.close(JiraSpy())
                    self.corrupt('readback_json', value)
                    original = self.rows()
                    @contextmanager
                    def ownership(store, *, issue_id=None):
                        if (blocked == 'batch' and issue_id is None) or (blocked == 'issue' and issue_id == 'AWF-1'):
                            yield False
                        else:
                            with real_ownership(store, issue_id=issue_id) as owned:
                                yield owned
                    spy = JiraSpy()
                    with patch.object(self.controller, '_batch_jira_ownership', ownership):
                        result = self.close(spy)
                    self.assertFalse(result['complete'])
                    self.assertEqual(['AWF-2'], result['reconciled'])
                    self.assertEqual(original, self.rows())
                    self.assertEqual([], spy.calls)
                    self.assertFalse(self.close(JiraSpy())['complete'])
                    self.assertEqual('UNKNOWN', self.rows()[0]['status'])

    def ordinary(self, run_id=1, writes=None):
        import uuid
        provider = deepcopy(PROVIDER)
        binding = dict(issue_id='AWF-1', project_id='local-project',
                       repository_id='101', requirements_hash='a' * 64)
        def write(record):
            if writes is not None:
                writes.append(record)
            return dict(operation_id=record['operation_id'], issue_id='AWF-1',
                        status='ATTEMPTED', observed_at=NOW, jira_provider=provider)
        return self.controller.production_jira_lifecycle(self.store,
            config=self.config, contract={}, event='WORKER_STARTED',
            facts=dict(run_registered=True, worktree_verified=True), binding=binding,
            issue_type='LEAF', state='DISPATCHED', producer_id='controller-1',
            run_id=str(uuid.UUID(int=run_id)), now=NOW, evidence=['urn:fixture:test'],
            transition_id='31', observe_provider_identity=lambda: provider,
            observe_issue_identity=lambda key: dict(issue_key=key, observed_at=NOW),
            read_current_status=lambda b: dict(issue_id='AWF-1', status_id='Ready',
                observed_at=NOW, jira_provider=provider), write_transition=write,
            read_transition=lambda r, o: dict(issue_id='AWF-1', status='In Progress',
                actor='controller-1', observed_at=NOW, jira_provider=provider))

    def test_storage_prior_stop_from_either_table_blocks_ticket(self):
        for table in ('controller_batch_jira_operation', 'controller_jira_operation'):
            for index, value in enumerate(self.storage_values()):
                with self.subTest(table=table, value=value):
                    self.fresh(table + str(index))
                    if table == 'controller_batch_jira_operation':
                        self.close(JiraSpy())
                    else:
                        self.assertEqual('SUCCEEDED', self.ordinary()['record']['status'])
                    with self.store.transaction() as db:
                        db.execute('UPDATE ' + table + " SET status='UNKNOWN',readback_json=? WHERE issue_id='AWF-1'", (value,))
                    with self.store.connection() as db:
                        original = [dict(r) for r in db.execute('SELECT * FROM ' + table)]
                    batch = deepcopy(self.batch)
                    batch['batch_id'] = 'batch-2'
                    for member in batch['constituents']:
                        member['batch_id'] = 'batch-2'
                    self.reopen()
                    spy = JiraSpy()
                    result = BatchReconciliationSafetyTests.run_batch(self, spy,
                        batch=batch, observed_merge={**self.merge, 'batch_id': 'batch-2'})
                    self.assertFalse(result['complete'])
                    self.assertEqual('AWF-1', result['conflict']['issue_id'])
                    self.assertFalse(any(issue == 'AWF-1' for _, issue in spy.calls))
                    with self.store.connection() as db:
                        for row in original:
                            self.assertEqual(row, dict(db.execute('SELECT * FROM ' + table +
                                ' WHERE operation_id=?', (row['operation_id'],)).fetchone()))

    def test_storage_ordinary_history_never_crashes_or_replays(self):
        for index, value in enumerate(self.storage_values()):
            with self.subTest(value=value):
                self.fresh('ordinary' + str(index))
                self.assertEqual('SUCCEEDED', self.ordinary()['record']['status'])
                with self.store.transaction() as db:
                    db.execute("UPDATE controller_jira_operation SET readback_json=?", (value,))
                original = self.store.jira_operations('AWF-1')
                self.reopen()
                writes = []
                # Same operation cannot be attempted again, even with bad storage.
                self.assertFalse(self.ordinary(writes=writes)['planned'])
                self.assertEqual([], writes)
                self.assertEqual(original, self.store.jira_operations('AWF-1'))


if __name__ == "__main__":
    unittest.main()
