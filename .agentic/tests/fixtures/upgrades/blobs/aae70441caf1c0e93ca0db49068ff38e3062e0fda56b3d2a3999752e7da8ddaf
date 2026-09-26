"""Jira lifecycle mirroring, closeout binding, host preflight, named resource leases, post-merge findings."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

from agentic import ValidationError
from agentic.canonical import load
from agentic.closeout import render_markdown, validate_closeout
from agentic.contracts import Contracts
from agentic.host_preflight import preflight, render_markdown as render_preflight
from agentic.interaction import decide_action, jira_write_classification
from agentic.jira_lifecycle import apply_read_back, closing_comment, owner_closure_required, planned_write, transition_record
from agentic.lifecycle import JIRA_WRITES, definition, transition
from agentic.review_loop import LoopStore, enroll, resume, tick, validate_review
from agentic.store import Store

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-09T12:00:00Z"
EVIDENCE = ["urn:awf:fixture:example-evidence"]


class JiraLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")
        cls.config0 = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        cls.bundle0 = load(ROOT / ".agentic/examples/evidence-bundle.json")

    def setUp(self):
        self.config = copy.deepcopy(self.config0)
        self.contract = copy.deepcopy(self.bundle0["contract"])
        self.binding = copy.deepcopy(self.bundle0["critic"]["binding"])

    def record(self, event, to_status, merge_result_id=None):
        return transition_record(self.binding, event, "To Do", to_status, "31", merge_result_id=merge_result_id,
                                 producer_id="fixture-controller", run_id=str(uuid.uuid4()), now=NOW, evidence=EVIDENCE)

    def test_event_map_and_records(self):
        self.assertEqual({"WORKER_STARTED": "in_progress", "PR_READY": "in_review", "OWNER_CHANGES_REQUESTED": "in_progress",
                          "HEAD_CHANGED": "in_progress", "JIRA_RECONCILED": "done"}, JIRA_WRITES)
        facts = {"external_event_recorded": True, "prior_evidence_invalidated": True}
        self.assertEqual(("in_progress", "In Progress"), planned_write(self.config, self.contract, "HEAD_CHANGED", facts, state="FINAL_REVIEW"))
        self.assertIsNone(planned_write(self.config, self.contract, "HEAD_CHANGED", facts, state="IN_PROGRESS")[0])
        self.assertEqual("MERGED_PENDING_OWNER_CLOSURE", transition("MERGED_PENDING_JIRA", "OWNER_CLOSURE_REQUIRED", {"merge_confirmed": True, "owner_closure_required": True}))
        self.assertEqual(JIRA_WRITES, definition()["jira_writes"])
        self.assertEqual(("in_progress", "In Progress"), planned_write(self.config, self.contract, "WORKER_STARTED", {"run_registered": True, "worktree_verified": True}))
        self.assertEqual(("in_review", "In Review"), planned_write(self.config, self.contract, "PR_READY", {"draft_cleared": True}))
        self.assertEqual(("in_progress", "In Progress"), planned_write(self.config, self.contract, "OWNER_CHANGES_REQUESTED", {"external_event_recorded": True, "prior_evidence_invalidated": True}))
        self.assertEqual(("done", "Done"), planned_write(self.config, self.contract, "JIRA_RECONCILED", {"merge_confirmed": True, "closeout_valid": True}))
        with self.assertRaisesRegex(ValidationError, "draft_cleared"):
            planned_write(self.config, self.contract, "PR_READY", {})
        self.assertEqual(None, planned_write(self.config, self.contract, "BLOCK", {})[0])
        self.assertEqual(None, planned_write(self.config, self.contract, "CAP_PARK", {})[0])
        with self.assertRaisesRegex(ValidationError, "Epics"):
            planned_write(self.config, self.contract, "WORKER_STARTED", {"run_registered": True, "worktree_verified": True}, issue_type="EPIC")
        self.assertEqual(None, planned_write(self.config, self.contract, "WORKER_STARTED", {"run_registered": True, "worktree_verified": True}, current_status="In Progress")[0])
        record = self.record("WORKER_STARTED", "In Progress")
        self.assertIsNone(record["merge_result_id"])
        self.contracts.validate("jira-transition", record)
        with self.assertRaises(ValidationError):
            self.contracts.validate("jira-transition", self.record("JIRA_RECONCILED", "Done"))
        done = self.record("JIRA_RECONCILED", "Done", merge_result_id=str(uuid.uuid4()))
        self.contracts.validate("jira-transition", done)
        self.assertIn("reviewed head", closing_comment(7, "b" * 40, "f" * 40))

    def test_lifecycle_writes_can_be_disabled_but_not_extended(self):
        self.config["jira"]["lifecycle_writes"]["in_review"] = False
        self.assertEqual(None, planned_write(self.config, self.contract, "PR_READY", {"draft_cleared": True})[0])
        self.config["jira"]["lifecycle_writes"]["blocked"] = True
        from agentic.policy import validate_config
        with self.assertRaises(ValidationError):
            validate_config(self.config, definition(), self.contracts)

    def test_owner_closure_suppresses_done_and_state_waits(self):
        snapshot = {"summary": "Production acceptance: cut over the nightly build", "labels": []}
        self.assertTrue(owner_closure_required(self.config, snapshot))
        self.assertFalse(owner_closure_required(self.config, {"summary": "Validate a label", "labels": []}))
        del self.config["jira"]["owner_closure_keywords"]
        self.assertTrue(owner_closure_required(self.config, snapshot))  # documented default list applies
        self.contract["owner_closure_required"] = True
        key, reason = planned_write(self.config, self.contract, "JIRA_RECONCILED", {"merge_confirmed": True, "closeout_valid": True})
        self.assertIsNone(key)
        self.assertIn("owner_closure_required", reason)
        self.assertEqual("MERGED_PENDING_OWNER_CLOSURE", transition("MERGED", "OWNER_CLOSURE_REQUIRED", {"merge_confirmed": True, "owner_closure_required": True}))
        with self.assertRaisesRegex(ValidationError, "owner_closure_verified"):
            transition("MERGED_PENDING_OWNER_CLOSURE", "JIRA_RECONCILED", {k: True for k in ["merge_confirmed", "closeout_valid", "jira_done_confirmed", "dependencies_refreshed"]})
        self.assertEqual("DONE", transition("MERGED_PENDING_OWNER_CLOSURE", "JIRA_RECONCILED",
                                            {k: True for k in ["merge_confirmed", "closeout_valid", "jira_done_confirmed", "dependencies_refreshed", "owner_closure_verified"]}))
        with self.assertRaisesRegex(ValidationError, "closeout_valid"):
            transition("MERGED", "JIRA_RECONCILED", {k: True for k in ["merge_confirmed", "jira_done_confirmed", "dependencies_refreshed", "owner_closure_not_required"]})

    def test_gate_refuses_a_contract_that_hides_owner_closure(self):
        from agentic.gates import evaluate
        bundle = copy.deepcopy(self.bundle0)
        bundle["snapshot"]["summary"] = "Cutover: switch the nightly store build"
        bundle["contract"]["requirements_hash"] = __import__("agentic.canonical", fromlist=["fingerprint"]).fingerprint("requirements", bundle["snapshot"])
        with self.assertRaisesRegex(ValidationError, "owner_closure_required|binding"):
            evaluate(self.config, definition(), bundle, self.contracts, NOW)

    def test_read_back_mismatch_stops_writes_for_that_ticket_only(self):
        record = self.record("WORKER_STARTED", "In Progress")
        matched = apply_read_back(record, "In Progress", "controller", NOW)
        self.assertEqual(("SUCCEEDED", False), (matched["record"]["status"], matched["writes_stopped"]))
        self.contracts.validate("jira-transition", matched["record"])
        reverted = apply_read_back(record, "Open", "automation-for-jira", NOW)
        self.assertEqual("FAILED", reverted["record"]["status"])
        self.assertIn("automation-for-jira", reverted["conflict"])
        self.assertIn("never reissue", reverted["conflict"])
        unknown = apply_read_back(record, None)
        self.assertEqual("UNKNOWN", unknown["record"]["status"])
        with self.assertRaisesRegex(ValidationError, "stopped"):
            planned_write(self.config, self.contract, "PR_READY", {"draft_cleared": True}, prior_writes=[reverted["record"]])
        # Another ticket's plan is unaffected.
        self.assertEqual(("in_review", "In Review"), planned_write(self.config, self.contract, "PR_READY", {"draft_cleared": True}))

    def test_owner_changes_requested_returns_to_changes_requested(self):
        facts = {"external_event_recorded": True, "prior_evidence_invalidated": True}
        for state in ("READY_FOR_CRITIC", "FINAL_REVIEW", "READY_FOR_OWNER_AUTHORIZATION"):
            self.assertEqual("CHANGES_REQUESTED", transition(state, "OWNER_CHANGES_REQUESTED", facts))
        with self.assertRaises(ValidationError):
            transition("IN_PROGRESS", "OWNER_CHANGES_REQUESTED", facts)

    def test_auto_transition_flag_classification(self):
        self.assertEqual("EXPLICIT", jira_write_classification(self.config)["classification"])
        self.config["controller"]["auto_transition_jira"] = True
        self.assertEqual("ROUTINE", jira_write_classification(self.config)["classification"])
        self.contracts.validate("project-config", self.config)
        self.assertEqual("CONTINUE", decide_action("post_evidence_comment", "EX-1", scope_authorized=True)["decision"])


class PostMergeTests(unittest.TestCase):
    def test_post_merge_finding_opens_successor_and_leaves_state(self):
        facts = {"finding_recorded": True, "successor_recorded": True}
        for state in ("MERGED", "DONE", "MERGED_PENDING_JIRA"):
            self.assertEqual(state, transition(state, "POST_MERGE_FINDING", facts))
        with self.assertRaises(ValidationError):
            transition("FINAL_REVIEW", "POST_MERGE_FINDING", facts)
        with self.assertRaisesRegex(ValidationError, "successor_recorded"):
            transition("DONE", "POST_MERGE_FINDING", {"finding_recorded": True})
        contracts = Contracts(ROOT / ".agentic/schemas")
        contract = copy.deepcopy(load(ROOT / ".agentic/examples/evidence-bundle.json")["contract"])
        contract["corrects"] = {"ticket": "EX-1", "merge_commit_sha": "f" * 40}
        contracts.validate("ticket-contract", contract)
        contract["corrects"] = {"ticket": "EX-1"}
        with self.assertRaises(ValidationError):
            contracts.validate("ticket-contract", contract)
        from agentic.review_policy import successor_contract
        finding = {"id": "F9", "severity": "MAJOR", "summary": "Rounding regression in packet replay", "basis": {"criterion_id": "AC1"}}
        original = load(ROOT / ".agentic/examples/evidence-bundle.json")["contract"]
        successor = successor_contract(original, finding, "f" * 40, NOW, ticket="EX-2")
        contracts.validate("ticket-contract", successor)
        self.assertEqual(("DRAFT", 2, {"ticket": "EX-1", "merge_commit_sha": "f" * 40}), (successor["disposition"], successor["risk_tier"], successor["corrects"]))
        self.assertNotEqual(original["contract_id"], successor["contract_id"])
        with self.assertRaises(ValidationError):
            successor_contract(original, finding, "not-a-sha", NOW)


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("Git executable unavailable")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "--initial-branch=main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "src").mkdir()
        (self.repo / "src/a.py").write_bytes(b"value = 1\n")
        self.git("add", "src/a.py")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "codex/x")
        (self.repo / "src/a.py").write_bytes(b"value = 2\n")
        self.git("commit", "-q", "-am", "candidate")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "codex/x")
        self.merge = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        self.blob = self.git("rev-parse", "HEAD:src/a.py")
        self.contracts = Contracts(ROOT / ".agentic/schemas")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def record(self, **over):
        binding = copy.deepcopy(load(ROOT / ".agentic/examples/evidence-bundle.json")["critic"]["binding"])
        value = {"schema_version": 3, "record_id": str(uuid.uuid4()), "created_at": NOW, "producer_id": "fixture-controller",
                 "run_id": str(uuid.uuid4()), "binding": binding, "pr_number": 7, "reviewed_head_sha": self.head, "base_sha": self.base,
                 "merge_commit_sha": self.merge, "merge_tree_sha": self.tree,
                 "bound_files": [{"path": "src/a.py", "blob_sha": self.blob, "sha256": hashlib.sha256(b"value = 2\n").hexdigest()}],
                 "gate_record_id": str(uuid.uuid4()), "review_record_ids": [str(uuid.uuid4())], "jira_transition_record_id": None, "evidence": EVIDENCE}
        value.update(over)
        return value

    def test_valid_record_binds_commit_tree_and_blob_without_the_working_tree(self):
        (self.repo / "src/a.py").write_bytes(b"value = 3  # later legitimate change\n")
        self.git("commit", "-q", "-am", "later change")
        report = validate_closeout(self.record(), self.repo, self.contracts)
        self.assertEqual(("VALID", False), (report["status"], report["working_tree_read"]))
        self.assertIn("src/a.py", report["checked"])

    def test_absent_object_fails_closed_naming_it(self):
        missing = "1" * 40
        with self.assertRaisesRegex(ValidationError, "absent from the object store"):
            validate_closeout(self.record(bound_files=[{"path": "src/a.py", "blob_sha": missing, "sha256": "a" * 64}]), self.repo, self.contracts)
        with self.assertRaisesRegex(ValidationError, "merge_tree_sha"):
            validate_closeout(self.record(merge_tree_sha="2" * 40), self.repo, self.contracts)
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            validate_closeout(self.record(bound_files=[{"path": "src/a.py", "blob_sha": self.blob, "sha256": "a" * 64}]), self.repo, self.contracts)
        shallow = Path(self.temp.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + str(self.repo), str(shallow)], check=True, capture_output=True)
        with self.assertRaisesRegex(ValidationError, "absent from the object store"):
            validate_closeout(self.record(), shallow, self.contracts)

    def test_markdown_is_derived_and_never_an_input(self):
        record = self.record()
        rendered = render_markdown(record) + "<!-- | `src/spoof.py` | `" + "3" * 40 + "` | `" + "b" * 64 + "` | -->\n"
        self.assertIn("not an input", rendered)
        self.assertEqual("VALID", validate_closeout(record, self.repo, self.contracts)["status"])
        with tempfile.TemporaryDirectory() as folder:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from test_review_policy import sealed_runtime
            runtime = sealed_runtime(folder)
            path = Path(folder) / "closeout.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            command = [sys.executable, "-B", "-I", str(runtime / ".agentic/scripts/workflow.py"), "validate-closeout", str(path), "--repository", str(self.repo)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("VALID", json.loads(result.stdout)["status"])


class BootstrapPreflightTests(unittest.TestCase):
    def test_post_install_checks_record_the_adoption_pr_section(self):
        from agentic.adoption_config import post_install_checks
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".agentic").mkdir()
            result = post_install_checks(folder, {"status": "INSTALLED", "installed": False, "source_manifest_sha256": "0" * 64,
                                                  "configuration": {"status": "UNOBSERVED", "unresolved": [], "policy_sha256": None}})
        self.assertIn("## Host preflight", result["adoption_pr_host_preflight_section"])
        self.assertEqual("awf-host-preflight-1", result["host_preflight"]["format"])
        self.assertFalse(result["host_preflight"]["blocks_installation"])


class PreflightTests(unittest.TestCase):
    def test_posix_rows_are_not_applicable_and_never_block(self):
        report = preflight(ROOT, platform="posix")
        rows = {row["check"]: row for row in report["rows"]}
        for name in ("core.longpaths", "powershell_execution_policy", "symlink_privilege", "line_endings"):
            self.assertEqual("N_A", rows[name]["status"])
        self.assertFalse(report["blocks_installation"])
        self.assertIn("## Host preflight", render_preflight(report))
        self.assertIn("| N_A |", render_preflight(report))

    def test_windows_rows_are_observations_with_remedies(self):
        report = preflight(ROOT, platform="nt")
        rows = {row["check"]: row for row in report["rows"]}
        self.assertEqual({"PASS", "WARN", "SKIP"} >= {rows["core.longpaths"]["status"]}, True)
        self.assertIn(rows["powershell_execution_policy"]["status"], {"PASS", "WARN", "SKIP"})
        for row in report["rows"]:
            if row["status"] in {"WARN", "SKIP"}:
                self.assertTrue(row["remedy"], row)
        self.assertFalse(report["blocks_installation"])


class ResourceLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite3", str(uuid.uuid4()))
        self.store.control("RUNNING", "fixture", reconciled=True)

    def test_named_resource_refusal_names_the_resource(self):
        limits = {"licensed_analysis_tool": 1, "licensed_compute_kernel": 1}
        first = self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [3, 1, 0], resources={"licensed_analysis_tool": 1}, resource_limits=limits)
        self.assertEqual({"licensed_analysis_tool": 1}, first["resources_held"])
        with self.assertRaisesRegex(ValidationError, "'licensed_analysis_tool' has no free slot"):
            self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [3, 1, 0], resources={"licensed_analysis_tool": 1}, resource_limits=limits)
        other = self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [3, 1, 0], resources={"licensed_compute_kernel": 1}, resource_limits=limits)
        self.assertEqual({"licensed_compute_kernel": 1}, other["resources_held"])
        with self.assertRaisesRegex(ValidationError, "not declared"):
            self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [3, 1, 0], resources={"actions": 1}, resource_limits=limits)
        self.store.release_lease(first["lease_id"], "c")
        again = self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [3, 1, 0], resources={"licensed_analysis_tool": 1}, resource_limits=limits)
        self.assertEqual({"licensed_analysis_tool": 1}, again["resources_held"])

    def test_lease_and_contract_schemas_carry_resources(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        lease = load(ROOT / ".agentic/templates/host-lease.yaml")["record"]
        lease.update(lease_id=str(uuid.uuid4()), project_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()), fencing_token=1, control_generation=1,
                     resource_class="heavy", worker_slots=1, heavy_slots=1, gpu_slots=0, issued_at=NOW, expires_at="2026-09-09T12:30:00Z", status="ACTIVE",
                     evidence=EVIDENCE, resources_held=[{"name": "licensed_analysis_tool", "slots": 1, "from": NOW, "until": "2026-09-09T12:30:00Z"}])
        contracts.validate("host-lease", lease)
        lease["resources_held"][0]["name"] = "Unnormalized Tool Name"
        with self.assertRaises(ValidationError):
            contracts.validate("host-lease", lease)
        config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        config["execution"]["host_broker"].update(enabled=True, broker_id="synthetic", resources={"licensed_analysis_tool": 1, "licensed_compute_kernel": 1, "actions": 1})
        from agentic.policy import validate_config
        validate_config(config, definition(), contracts)
        config["execution"]["host_broker"]["resources"] = {"Unnormalized": 1}
        with self.assertRaises(ValidationError):
            validate_config(config, definition(), contracts)


class HostLoopCapTests(unittest.TestCase):
    """The scheduled host loop pauses at the cap and resumes only through an owner disposition."""
    CANDIDATE = {'repository_id': 12, 'pr': 7, 'head': 'a' * 40, 'base': 'b' * 40, 'head_ref': 'codex/test', 'base_ref': 'main'}

    class Driver:
        def __init__(self, candidate):
            self.current = copy.deepcopy(candidate)
            self.last_amendment_paths = ['src/a.py']

        def snapshot(self):
            return copy.deepcopy(self.current)

        def files(self, candidate):
            return ['src/a.py']

        def review(self, candidate, findings, run_id, files):
            if not findings:
                findings = [{'id': 'F1', 'severity': 'MAJOR', 'status': 'OPEN', 'file': 'src/a.py', 'message': 'Still wrong', 'evidence': '', 'basis': 'criterion:AC1'}]
            return {'candidate': copy.deepcopy(candidate), 'verdict': 'CHANGES_REQUESTED', 'reviewed_files': files, 'findings': findings, 'summary': 'Still broken'}

        def amend(self, candidate, findings, run_id):
            self.current = {**candidate, 'head': hashlib.sha1(candidate['head'].encode()).hexdigest()}
            return copy.deepcopy(self.current)

        def ci(self, candidate):
            return 'PASS'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LoopStore(self.temp.name)
        self.addCleanup(self.store.close)
        self.config = {'key': '12:7', 'repository': 'fixture/project', 'config_hash': 'f' * 64,
                       'max_amendment_cycles': 2, 'max_cap_extensions': 1, 'evidence_paths': ['evidence/**'],
                       'max_ci_wait_ticks': 2, 'max_agent_runs': 40, 'qualification': {'operator': 'maintainer'}}
        self.driver = self.Driver(self.CANDIDATE)
        enroll(self.store, self.config, self.driver.snapshot())

    def step(self):
        return tick(self.store, self.config, self.driver)

    def disposition(self, decision, **extra):
        return {'decision': decision, 'disposition_id': str(uuid.uuid4()), 'owner': 'maintainer', 'open_finding_ids': ['F1'], **extra}

    def run_to_cap(self):
        phases = []
        for _ in range(8):
            state = self.step()
            phases.append(state['phase'])
            if state['phase'] == 'PAUSED':
                return state, phases
        self.fail(phases)

    def test_cap_is_a_paused_state_with_open_findings_and_no_further_amendment(self):
        state, phases = self.run_to_cap()
        self.assertEqual(2, state['cycles'])
        self.assertTrue(state['reason'].startswith('REVIEW_CAP_REACHED'))
        self.assertIn("['F1']", state['reason'])
        self.assertEqual(state, self.step())  # no further runs
        with self.assertRaisesRegex(ValidationError, 'disposition'):
            resume(self.store, self.config, self.driver.snapshot(), 'none')

    def test_extension_is_finite_then_other_dispositions_apply(self):
        self.run_to_cap()
        state = resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('EXTEND_ONE_CYCLE'))
        self.assertEqual(('AMEND', 1), (state['phase'], state['cap_extensions']))
        state, _ = self.run_to_cap()
        self.assertEqual(3, state['cycles'])
        with self.assertRaisesRegex(ValidationError, r'max_cap_extensions'):
            resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('EXTEND_ONE_CYCLE'))
        state = resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('PARK'))
        self.assertTrue(state['reason'].startswith('REVIEW_CAP_PARKED'))
        state = resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('MERGE_WITH_NOTES'))
        self.assertEqual('WAIT_CI', state['phase'])
        self.assertEqual(1, len(state['residual_notes']))

    def test_rescope_closes_and_boundary_findings_cannot_merge_with_notes(self):
        self.run_to_cap()
        with self.assertRaisesRegex(ValidationError, 'exactly'):
            resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('PARK', open_finding_ids=[]))
        with self.assertRaisesRegex(ValidationError, 'operator'):
            resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('PARK', owner='codex-agent'))
        with self.assertRaisesRegex(ValidationError, 'operator'):
            resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('PARK', owner='someone-else'))
        state = resume(self.store, self.config, self.driver.snapshot(), 'none', self.disposition('RESCOPE', successor_ticket='EX-99'))
        self.assertEqual('CLOSED', state['phase'])
        self.assertIn('EX-99', state['reason'])

    def test_evidence_only_amendment_does_not_consume_a_cycle(self):
        self.driver.last_amendment_paths = ['evidence/run.json']
        self.step()  # REVIEW -> AMEND
        state = self.step()  # amendment published
        self.assertEqual((0, 1), (state['cycles'], state['evidence_only_amendments']))

    def test_host_loop_findings_need_basis_and_lineage(self):
        base = {'id': 'F1', 'severity': 'MAJOR', 'status': 'OPEN', 'file': 'src/a.py', 'message': 'm', 'evidence': ''}
        report = {'candidate': self.CANDIDATE, 'verdict': 'CHANGES_REQUESTED', 'reviewed_files': ['src/a.py'], 'findings': [dict(base)], 'summary': 's'}
        with self.assertRaisesRegex(ValidationError, 'basis'):
            validate_review(report, self.CANDIDATE, [], ['src/a.py'])
        report['findings'][0]['basis'] = 'boundary:NOPE'
        with self.assertRaisesRegex(ValidationError, 'basis'):
            validate_review(report, self.CANDIDATE, [], ['src/a.py'])
        report['findings'][0]['basis'] = 'criterion:AC1'
        validate_review(report, self.CANDIDATE, [], ['src/a.py'])
        prior = [{**base, 'basis': 'criterion:AC1', 'status': 'RESOLVED', 'evidence': 'fixed'}]
        report['findings'] = [dict(prior[0]), {**base, 'id': 'F9', 'basis': 'criterion:AC1', 'message': 'reopened differently'}]
        with self.assertRaisesRegex(ValidationError, 'superseded'):
            validate_review(report, self.CANDIDATE, prior, ['src/a.py'])
        report['findings'][1]['supersedes'] = 'F1'
        validate_review(report, self.CANDIDATE, prior, ['src/a.py'])
        # A MINOR boundary finding still prevents APPROVE in the host loop.
        minor_boundary = {**base, 'id': 'F3', 'severity': 'MINOR', 'basis': 'boundary:CREDENTIAL_EXPOSURE'}
        approving = {'candidate': self.CANDIDATE, 'verdict': 'APPROVE', 'reviewed_files': ['src/a.py'], 'findings': [minor_boundary], 'summary': 's'}
        with self.assertRaisesRegex(ValidationError, 'approved unresolved'):
            validate_review(approving, self.CANDIDATE, [], ['src/a.py'])


if __name__ == "__main__":
    unittest.main()
