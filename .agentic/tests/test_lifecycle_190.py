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
from agentic.closeout import integration_tree, render_markdown, validate_closeout
from agentic.contracts import Contracts
from agentic.host_preflight import preflight, render_markdown as render_preflight, route_models_observed
from agentic.interaction import decide_action, jira_write_classification
from agentic.jira_lifecycle import apply_read_back, closing_comment, owner_closure_required, planned_write, transition_record
from agentic.lifecycle import JIRA_WRITES, definition, transition
from agentic.review_loop import LoopStore, enroll, resume, tick, validate_review
from agentic.store import Store
from review_admission_fixture import bind_review_admission

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
        jira = self.config["jira"]
        provider = {"cloud_id": jira["cloud_id"], "site": jira["site"],
                    "project_id": jira["provider_project_id"], "project_key": jira["project_key"],
                    "controller_actor_id": jira["controller_actor_id"]}
        return transition_record(self.binding, event, "To Do", to_status, "31", jira_provider=provider,
                                 merge_result_id=merge_result_id,
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
        bind_review_admission(bundle)
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

    def bound(self, merge, **over):
        content = self.git("cat-file", "blob", f"{merge}:src/a.py").encode("utf-8") + b"\n"
        value = {"merge_commit_sha": merge, "merge_tree_sha": self.git("rev-parse", merge + "^{tree}"),
                 "bound_files": [{"path": "src/a.py", "blob_sha": self.git("rev-parse", f"{merge}:src/a.py"),
                                  "sha256": hashlib.sha256(content).hexdigest()}]}
        value.update(over)
        return self.record(**value)

    def squash(self, parent, source, branch, extra=None):
        self.git("checkout", "-q", "-b", branch, parent)
        self.git("merge", "-q", "--squash", source)
        if extra:
            (self.repo / "src/a.py").write_bytes(extra)
            self.git("add", "src/a.py")
        self.git("commit", "-q", "-m", "squash")
        sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return sha

    def test_merge_commit_must_integrate_the_reviewed_head_onto_the_base(self):
        self.assertEqual("merge_commit", validate_closeout(self.record(), self.repo, self.contracts)["merge_relationship"])
        self.git("checkout", "-q", "-b", "stale", self.base)
        (self.repo / "src/b.py").write_bytes(b"unreviewed = True\n")
        self.git("add", "src/b.py")
        self.git("commit", "-q", "-m", "unreviewed")
        stale = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.record(reviewed_head_sha=stale), self.repo, self.contracts)
        with self.assertRaisesRegex(ValidationError, "base_sha .* is not an ancestor"):
            validate_closeout(self.record(base_sha=stale), self.repo, self.contracts)
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.record(reviewed_head_sha=self.base), self.repo, self.contracts)
        # A merge of some other branch is not the reviewed merge, even on the same base.
        self.git("checkout", "-q", "-b", "other-merge", self.base)
        self.git("merge", "-q", "--no-ff", "-m", "merge other", "stale")
        other = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.bound(other), self.repo, self.contracts)

    def test_squash_merge_tree_must_equal_the_reviewed_head_merged_onto_the_base(self):
        squash = self.squash(self.base, "codex/x", "squash")
        self.assertEqual("squash", validate_closeout(self.bound(squash), self.repo, self.contracts)["merge_relationship"])
        tampered = self.squash(self.base, "codex/x", "tampered", extra=b"value = 99\n")
        with self.assertRaisesRegex(ValidationError, "tree is not reviewed_head_sha"):
            validate_closeout(self.bound(tampered), self.repo, self.contracts)
        # The base advanced before the squash: the expected tree is a real three-way merge.
        self.git("checkout", "-q", "-b", "advanced", self.base)
        (self.repo / "src/c.py").write_bytes(b"other = 1\n")
        self.git("add", "src/c.py")
        self.git("commit", "-q", "-m", "advance base")
        advanced = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        # Computing the expected tree (not yet in the store) leaves the repository's objects untouched.
        before = self.git("count-objects", "-v")
        expected = integration_tree(self.repo, advanced, self.head)
        self.assertEqual(before, self.git("count-objects", "-v"))
        merged = self.squash(advanced, "codex/x", "squash-advanced")
        self.assertEqual(expected, self.git("rev-parse", merged + "^{tree}"))
        report = validate_closeout(self.bound(merged, base_sha=advanced), self.repo, self.contracts)
        self.assertEqual("squash", report["merge_relationship"])
        skipped = self.squash(advanced, "codex/x", "squash-skipped", extra=b"value = 3\n")
        with self.assertRaisesRegex(ValidationError, "tree is not reviewed_head_sha"):
            validate_closeout(self.bound(skipped, base_sha=advanced), self.repo, self.contracts)
        # A squash whose parent is not the recorded base fails closed.
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.bound(merged), self.repo, self.contracts)

    def advance(self, branch="advanced", start=None):
        self.git("checkout", "-q", "-b", branch, start or self.base)
        (self.repo / "src/c.py").write_bytes(b"other = 1\n")
        self.git("add", "src/c.py")
        self.git("commit", "-q", "-m", "advance base")
        sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return sha

    def test_merge_commit_tree_must_be_the_reviewed_integration(self):
        forged = self.git("commit-tree", self.base + "^{tree}", "-p", self.base, "-p", self.head, "-m", "forged")
        with self.assertRaisesRegex(ValidationError, "tree is not reviewed_head_sha"):
            validate_closeout(self.bound(forged), self.repo, self.contracts)
        advanced = self.advance()
        self.git("checkout", "-q", "-b", "merged-advanced", advanced)
        self.git("merge", "-q", "--no-ff", "-m", "merge", "codex/x")
        merged = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        report = validate_closeout(self.bound(merged, base_sha=advanced), self.repo, self.contracts)
        self.assertEqual("merge_commit", report["merge_relationship"])
        # The first parent must be the recorded base itself, not merely a descendant of it.
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.bound(merged), self.repo, self.contracts)
        forged = self.git("commit-tree", advanced + "^{tree}", "-p", advanced, "-p", self.head, "-m", "forged")
        with self.assertRaisesRegex(ValidationError, "tree is not reviewed_head_sha"):
            validate_closeout(self.bound(forged, base_sha=advanced), self.repo, self.contracts)

    def test_rebase_merge_is_a_bounded_linear_chain_onto_the_base(self):
        self.git("checkout", "-q", "-b", "codex/y", self.base)
        for value in (b"value = 5\n", b"value = 6\n"):
            (self.repo / "src/a.py").write_bytes(value)
            self.git("commit", "-q", "-am", "step")
        head = self.git("rev-parse", "HEAD")
        advanced = self.advance()
        self.git("checkout", "-q", "-b", "rebased", head)
        self.git("rebase", "-q", advanced)
        rebased = self.git("rev-parse", "HEAD")
        (self.repo / "src/a.py").write_bytes(b"value = 7\n")
        self.git("commit", "-q", "-am", "unreviewed tip")
        extended = self.git("rev-parse", "HEAD")
        self.git("reset", "-q", "--hard", rebased)
        (self.repo / "src/a.py").write_bytes(b"value = 8\n")
        self.git("commit", "-q", "--amend", "--no-edit", "-a")
        tampered = self.git("rev-parse", "HEAD")
        # Final tree correct, but the first replayed commit carries unreviewed content.
        first, second = self.git("rev-list", "--reverse", f"{self.base}..{head}").split()
        self.git("reset", "-q", "--hard", advanced)
        self.git("cherry-pick", first)
        (self.repo / "src/secret.py").write_bytes(b"token = 'unreviewed'\n")
        self.git("add", "src/secret.py")
        self.git("commit", "-q", "--amend", "--no-edit")
        self.git("cherry-pick", second)
        self.git("rm", "-q", "src/secret.py")
        self.git("commit", "-q", "--amend", "--no-edit")
        laundered = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-parse", rebased + "^{tree}"), self.git("rev-parse", laundered + "^{tree}"))
        # Same trees, but a replayed commit message differs from the reviewed one.
        self.git("reset", "-q", "--hard", rebased)
        self.git("commit", "-q", "--amend", "-m", "rewritten message")
        renamed = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        report = validate_closeout(self.bound(rebased, reviewed_head_sha=head, base_sha=advanced), self.repo, self.contracts)
        self.assertEqual("rebase", report["merge_relationship"])
        with self.assertRaisesRegex(ValidationError, "tree is not reviewed_head_sha"):
            validate_closeout(self.bound(tampered, reviewed_head_sha=head, base_sha=advanced), self.repo, self.contracts)
        with self.assertRaisesRegex(ValidationError, "tree is not reviewed_head_sha"):
            validate_closeout(self.bound(laundered, reviewed_head_sha=head, base_sha=advanced), self.repo, self.contracts)
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.bound(renamed, reviewed_head_sha=head, base_sha=advanced), self.repo, self.contracts)
        # A chain longer than the reviewed commits is not the rebased review.
        with self.assertRaisesRegex(ValidationError, "does not merge reviewed_head_sha"):
            validate_closeout(self.bound(extended, reviewed_head_sha=head, base_sha=advanced), self.repo, self.contracts)

    def test_merge_computation_never_runs_repository_merge_drivers(self):
        lines = b"".join(b"line %d\n" % n for n in range(1, 9))
        self.git("checkout", "-q", "-b", "drivers", self.base)
        (self.repo / "src/m.txt").write_bytes(lines)
        self.git("add", "src/m.txt")
        self.git("commit", "-q", "-m", "multi-line base")
        base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "feature-m")
        (self.repo / "src/m.txt").write_bytes(lines.replace(b"line 1\n", b"line one\n"))
        self.git("commit", "-q", "-am", "feature edit")
        head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "drivers")
        (self.repo / "src/m.txt").write_bytes(lines.replace(b"line 8\n", b"line eight\n"))
        self.git("commit", "-q", "-am", "base edit")
        advanced = self.git("rev-parse", "HEAD")
        squashed = self.squash(advanced, "feature-m", "squash-drivers")
        marker = Path(self.temp.name) / "driver-ran"
        (self.repo / ".git/info").mkdir(exist_ok=True)
        (self.repo / ".git/info/attributes").write_text("* merge=probe\n", encoding="utf-8")
        self.git("config", "merge.probe.name", "probe")
        self.git("config", "merge.probe.driver", f"echo ran > '{marker.as_posix()}'; exit 1")
        report = validate_closeout(self.bound(squashed, reviewed_head_sha=head, base_sha=advanced), self.repo, self.contracts)
        self.assertEqual("squash", report["merge_relationship"])
        self.assertFalse(marker.exists())
        self.assertNotEqual(base, advanced)

    def test_fast_forward_is_the_reviewed_head_itself(self):
        report = validate_closeout(self.bound(self.head), self.repo, self.contracts)
        self.assertEqual("fast_forward", report["merge_relationship"])

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
    def route_config(self, model="gpt-5.6-sol", effort="high", age=30):
        return {"execution": {"roles": {
                                  "controller": {"model": "gpt-6-astra", "reasoning_effort": "high"},
                                  "worker": {"model": "gpt-6-luna", "reasoning_effort": "medium"},
                                  "critic": {"model": "gpt-6-astra", "reasoning_effort": "high"}},
                              "model_routing": {"risk_route": {"model": model, "reasoning_effort": effort}},
                              "route_capabilities": {"observation_path": ".agentic/route-capabilities.json", "max_age_days": age}}}

    def test_route_models_observed_passes_reference_defaults_and_warns_missing_refused_stale(self):
        observed = copy.deepcopy(load(ROOT / ".agentic/examples/routing-capabilities.json"))
        observed["models"]["gpt-6-luna"] = {"status": "observed", "reasoning_efforts": ["low", "medium"],
                                               "host_id": "synthetic-host", "host_software": "Codex",
                                               "host_software_version": "test", "method": "successful_probe",
                                               "observed_at": "2026-09-24T00:00:00Z"}
        observed["models"]["gpt-6-astra"] = {"status": "observed", "reasoning_efforts": ["high"],
                                                "host_id": "synthetic-host", "host_software": "Codex",
                                                "host_software_version": "test", "method": "successful_probe",
                                                "observed_at": "2026-09-24T00:00:00Z"}
        with tempfile.TemporaryDirectory() as temporary:
            passed = route_models_observed(temporary, config=self.route_config(), capabilities=observed,
                                           now="2026-09-24T12:00:00Z")
        self.assertEqual("PASS", passed["status"])

        with tempfile.TemporaryDirectory() as temporary:
            missing = route_models_observed(temporary, config=self.route_config("gpt-missing"), capabilities=observed,
                                            now="2026-09-24T12:00:00Z")
        self.assertEqual("WARN", missing["status"])
        self.assertIn("missing", missing["detail"])

        refused_observation = copy.deepcopy(observed)
        refused_observation["models"]["gpt-6-astra"].update(status="refused", reasoning_efforts=[],
                                                             method="recorded_refusal")
        with tempfile.TemporaryDirectory() as temporary:
            refused = route_models_observed(temporary, config=self.route_config("gpt-6-astra"),
                                            capabilities=refused_observation, now="2026-09-24T12:00:00Z")
        self.assertEqual("WARN", refused["status"])
        self.assertIn("refused", refused["detail"])

        with tempfile.TemporaryDirectory() as temporary:
            stale = route_models_observed(temporary, config=self.route_config(), capabilities=observed,
                                          now="2026-11-01T00:00:00Z")
        self.assertEqual("WARN", stale["status"])
        self.assertIn("stale", stale["detail"])
        self.assertTrue(all(field in observed["models"]["gpt-5.6-sol"] for field in
                            ("host_id", "host_software", "host_software_version", "method", "observed_at")))

    def test_project_lint_scope_ruff_warn_pass_and_not_applicable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            row = {item["check"]: item for item in preflight(root, platform="posix")["rows"]}["project_lint_scope"]
            self.assertEqual("N_A", row["status"])
            (root / "pyproject.toml").write_text('[tool.ruff]\nline-length = 100\n', encoding="utf-8")
            row = {item["check"]: item for item in preflight(root, platform="posix")["rows"]}["project_lint_scope"]
            self.assertEqual("WARN", row["status"])
            self.assertIn('extend-exclude = [".agentic"]', row["remedy"])
            (root / "pyproject.toml").write_text('[tool.ruff]\nextend-exclude = [".agentic"]\n', encoding="utf-8")
            row = {item["check"]: item for item in preflight(root, platform="posix")["rows"]}["project_lint_scope"]
            self.assertEqual("PASS", row["status"])

    def test_project_lint_scope_flake8_warns_if_any_config_does_not_exclude(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".flake8").write_text("[flake8]\nextend-exclude = .agentic\n", encoding="utf-8")
            row = {item["check"]: item for item in preflight(root, platform="posix")["rows"]}["project_lint_scope"]
            self.assertEqual("PASS", row["status"])
            (root / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
            row = {item["check"]: item for item in preflight(root, platform="posix")["rows"]}["project_lint_scope"]
            self.assertEqual("WARN", row["status"])

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
