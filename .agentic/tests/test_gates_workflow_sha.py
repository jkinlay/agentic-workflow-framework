"""Content-bound CI workflow provenance remains stable across merge methods."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.canonical import load
from agentic.configuration import valid_workflow_blob_sha
from agentic.contracts import Contracts
from agentic.gates import (candidate_workflow_blob_sha,
                           ci_workflow_provenance_matches, evaluate,
                           expected_binding)
from agentic.lifecycle import definition
from agentic.policy import policy_hash
from test_review_policy import (RuntimeAssignedGateConfig,
                                bind_production_posting_fixture,
                                bind_review_admission)


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-09T12:00:00Z"


class WorkflowContentBindingTests(unittest.TestCase):
    path = ".github/workflows/ci.yml"
    reviewed_blob = "1" * 40

    def required(self, **updates):
        value = {
            "name": "unit-tests",
            "app_id": 42,
            "workflow_path": self.path,
            "workflow_sha": "a" * 40,
            "workflow_blob_sha": self.reviewed_blob,
        }
        value.update(updates)
        return value

    def observed(self, workflow_sha):
        return {
            "name": "unit-tests",
            "app_id": 42,
            "workflow_path": self.path,
            "workflow_sha": workflow_sha,
        }

    def test_reviewed_workflow_content_survives_merge_commit_identity_changes(self):
        # Squash, merge-commit, and rebase merges can all assign a different
        # commit identity while retaining the exact reviewed workflow bytes.
        for merged_workflow_sha in ("b" * 40, "c" * 40, "d" * 40):
            with self.subTest(merged_workflow_sha=merged_workflow_sha):
                self.assertTrue(ci_workflow_provenance_matches(
                    self.required(), self.observed(merged_workflow_sha),
                    self.reviewed_blob))

    def test_modified_or_unreviewed_workflow_fails_closed(self):
        self.assertFalse(ci_workflow_provenance_matches(
            self.required(), self.observed("b" * 40), "2" * 40))

    def test_missing_or_malformed_content_binding_fails_closed(self):
        without_either_pin = self.required()
        without_either_pin.pop("workflow_blob_sha")
        without_either_pin.pop("workflow_sha")
        self.assertFalse(ci_workflow_provenance_matches(
            without_either_pin, self.observed("a" * 40), None))

        for malformed in (None, "", "1" * 39, "G" * 40, "0" * 40, 1):
            with self.subTest(malformed=malformed):
                required = self.required(workflow_blob_sha=malformed)
                self.assertFalse(valid_workflow_blob_sha(malformed))
                self.assertFalse(ci_workflow_provenance_matches(
                    required, self.observed(required["workflow_sha"]), None))

    def test_unchanged_workflow_passes_without_changed_file_entry(self):
        # The candidate-tree blob is evidence independent of the changed-file
        # manifest, so an ordinary PR can retain the reviewed workflow bytes.
        self.assertTrue(ci_workflow_provenance_matches(
            self.required(), self.observed("b" * 40), self.reviewed_blob))

    def test_missing_candidate_tree_blob_evidence_fails_closed(self):
        self.assertFalse(ci_workflow_provenance_matches(
            self.required(), self.observed("b" * 40), None))

    @mock.patch("agentic.gates.git")
    def test_candidate_blob_parses_exact_integration_tree_entry(self, git):
        git.return_value = (
            b"100644 blob " + self.reviewed_blob.encode("ascii")
            + b"\t.github/workflows/ci.yml\0"
        )

        self.assertEqual(candidate_workflow_blob_sha(
            "repository", "4" * 40, self.path), self.reviewed_blob)
        git.assert_called_once_with(
            "repository", "ls-tree", "-z", "--full-tree", "4" * 40,
            "--", self.path, binary=True)

    def test_candidate_blob_is_resolved_from_integration_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = Path(folder)
            subprocess.run(["git", "init", "--quiet"], cwd=repository,
                           check=True, capture_output=True)
            workflow = repository / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: CI\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "add", "--", self.path], cwd=repository,
                           check=True, capture_output=True)
            tree_sha = subprocess.run(
                ["git", "write-tree"], cwd=repository, check=True,
                capture_output=True, text=True).stdout.strip()
            blob_sha = subprocess.run(
                ["git", "rev-parse", ":" + self.path], cwd=repository,
                check=True, capture_output=True, text=True).stdout.strip()

            self.assertEqual(candidate_workflow_blob_sha(
                repository, tree_sha, self.path), blob_sha)

    @mock.patch("agentic.gates.git", return_value=b"")
    def test_missing_candidate_tree_path_fails_closed(self, _git):
        self.assertIsNone(candidate_workflow_blob_sha(
            "repository", "4" * 40, self.path))

    def test_legacy_exact_workflow_sha_comparison_remains_valid(self):
        required = self.required()
        required.pop("workflow_blob_sha")

        self.assertTrue(ci_workflow_provenance_matches(
            required, self.observed(required["workflow_sha"]), None))
        self.assertFalse(ci_workflow_provenance_matches(
            required, self.observed("b" * 40), None))

    def test_identity_mismatch_fails_for_both_pin_modes(self):
        for field, value in (("name", "other"), ("app_id", 7),
                             ("workflow_path", ".github/workflows/other.yml")):
            with self.subTest(field=field):
                observed = self.observed("a" * 40)
                observed[field] = value
                self.assertFalse(ci_workflow_provenance_matches(
                    self.required(), observed, self.reviewed_blob))


class EvaluateWorkflowContentBindingTests(unittest.TestCase):
    """Exercise the production evaluator against real candidate Git objects."""

    path = ".github/workflows/ci.yml"
    reviewed_bytes = b"name: CI\non: [push]\n"
    changed_bytes = b"name: unreviewed\non: [push]\n"

    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")
        cls.config0 = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        cls.bundle0 = load(ROOT / ".agentic/examples/evidence-bundle.json")

    def git(self, repository, *args):
        return subprocess.run(
            ["git", *args], cwd=repository, check=True, capture_output=True,
            text=True,
        ).stdout.strip()

    def write_tree_commit(self, repository, workflow_bytes, label, *parents):
        workflow = repository / self.path
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_bytes(workflow_bytes)
        (repository / "payload.txt").write_text(
            label + "\n", encoding="utf-8", newline="\n")
        self.git(repository, "add", "-A")
        tree_sha = self.git(repository, "write-tree")
        parent_args = [item for parent in parents for item in ("-p", parent)]
        commit_sha = self.git(
            repository, "commit-tree", tree_sha, *parent_args, "-m", label)
        return commit_sha, tree_sha

    def topology_cases(self, repository):
        self.git(repository, "init", "--quiet")
        self.git(repository, "config", "user.name", "AWF fixture")
        self.git(repository, "config", "user.email", "fixture@example.invalid")

        root, _ = self.write_tree_commit(
            repository, self.reviewed_bytes, "root")
        target, _ = self.write_tree_commit(
            repository, self.reviewed_bytes, "target", root)
        topic, _ = self.write_tree_commit(
            repository, self.reviewed_bytes, "topic", root)
        topic_changed, _ = self.write_tree_commit(
            repository, self.changed_bytes, "topic changed", root)

        squash, squash_tree = self.write_tree_commit(
            repository, self.reviewed_bytes, "squash", target)
        squash_changed, squash_changed_tree = self.write_tree_commit(
            repository, self.changed_bytes, "squash changed", target)

        merge, merge_tree = self.write_tree_commit(
            repository, self.reviewed_bytes, "merge", target, topic)
        merge_changed, merge_changed_tree = self.write_tree_commit(
            repository, self.changed_bytes, "merge changed", target,
            topic_changed)

        rebased_first, _ = self.write_tree_commit(
            repository, self.reviewed_bytes, "rebased first", target)
        rebase, rebase_tree = self.write_tree_commit(
            repository, self.reviewed_bytes, "rebased second", rebased_first)
        rebased_changed_first, _ = self.write_tree_commit(
            repository, self.changed_bytes, "rebased changed first", target)
        rebase_changed, rebase_changed_tree = self.write_tree_commit(
            repository, self.changed_bytes, "rebased changed second",
            rebased_changed_first)

        cases = {
            "squash": (squash, squash_tree, squash_changed,
                       squash_changed_tree, [target]),
            "merge": (merge, merge_tree, merge_changed,
                      merge_changed_tree, [target, topic]),
            "rebase": (rebase, rebase_tree, rebase_changed,
                       rebase_changed_tree, [rebased_first]),
        }
        for name, (commit_sha, tree_sha, _, _, expected_parents) in cases.items():
            with self.subTest(topology=name):
                commit_line = self.git(
                    repository, "rev-list", "--parents", "-n", "1",
                    commit_sha).split()
                self.assertEqual(commit_line[1:], expected_parents)
                self.assertEqual(
                    self.git(repository, "rev-parse", commit_sha + "^{tree}"),
                    tree_sha)
        self.assertEqual(
            self.git(repository, "rev-list", "--parents", "-n", "1",
                     rebased_first).split()[1:], [target])
        return cases

    def evaluate_case(self, repository, merge_method, commit_sha, tree_sha,
                      *, configured_blob_sha):
        self.bundle = copy.deepcopy(self.bundle0)
        config = RuntimeAssignedGateConfig(copy.deepcopy(self.config0), self)
        config["github"]["merge_method"] = merge_method
        required = config["validation"]["required_ci_checks"][0]
        required.update(
            workflow_path=self.path,
            workflow_blob_sha=configured_blob_sha,
        )

        candidate = self.bundle["candidate"]
        candidate.update(
            integration_tree_sha=tree_sha,
            tested_merge_sha=commit_sha,
            merge_method=merge_method,
        )
        check = self.bundle["ci"]["checks"][0]
        check.update(
            workflow_path=self.path,
            # Content mode must not accept or reject based on this legacy pin.
            workflow_sha=commit_sha,
            tested_tree_sha=tree_sha,
            tested_commit_sha=commit_sha,
        )

        self.bundle["contract"]["policy_hash"] = policy_hash(
            config, definition())
        binding = expected_binding(config, definition(), self.bundle)
        for key in ("dispatch", "worker", "critic", "ci", "pr"):
            self.bundle[key]["binding"] = copy.deepcopy(binding)
        for record in (self.bundle["runs"] + self.bundle["specialists"]
                       + self.bundle["review_verdicts"]):
            record["binding"] = copy.deepcopy(binding)
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(config, self.bundle)
        return evaluate(
            config, definition(), self.bundle, self.contracts, NOW,
            posting_collector_registry=registry, repository=repository)

    def test_unmocked_evaluate_matches_and_fails_closed_for_real_git_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = Path(folder)
            cases = self.topology_cases(repository)
            commit_sha, tree_sha, _, _, _ = cases["squash"]
            reviewed_blob = self.git(
                repository, "rev-parse", tree_sha + ":" + self.path)

            matching = self.evaluate_case(
                repository, "squash", commit_sha, tree_sha,
                configured_blob_sha=reviewed_blob)
            self.assertEqual(matching["gates"]["required_ci"]["result"], "PASS")
            self.assertEqual(
                matching["gates"]["ci_candidate_binding"]["result"], "PASS")

            mismatch = self.evaluate_case(
                repository, "squash", commit_sha, tree_sha,
                configured_blob_sha="1" * 40)
            self.assertEqual(mismatch["gates"]["required_ci"]["result"], "FAIL")
            self.assertEqual(
                mismatch["gates"]["ci_candidate_binding"]["result"], "FAIL")

            missing_tree = self.evaluate_case(
                repository, "squash", commit_sha, "2" * 40,
                configured_blob_sha=reviewed_blob)
            self.assertEqual(
                missing_tree["gates"]["required_ci"]["result"], "FAIL")
            self.assertEqual(
                missing_tree["gates"]["ci_candidate_binding"]["result"],
                "FAIL")

            malformed_cases = (
                ("not-a-git-blob", "workflow_blob_sha"),
                ("0" * 40,
                 "workflow_blob_sha: zero workflow blob SHA placeholder"),
            )
            for malformed, diagnostic in malformed_cases:
                with self.subTest(malformed=malformed), self.assertRaisesRegex(
                        ValidationError, diagnostic):
                    self.evaluate_case(
                        repository, "squash", commit_sha, tree_sha,
                        configured_blob_sha=malformed)

    def test_real_squash_merge_and_rebase_trees_bind_reviewed_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = Path(folder)
            cases = self.topology_cases(repository)
            for merge_method, (commit_sha, tree_sha, changed_commit,
                               changed_tree, _) in cases.items():
                reviewed_blob = self.git(
                    repository, "rev-parse", tree_sha + ":" + self.path)
                with self.subTest(merge_method=merge_method, content="reviewed"):
                    gate = self.evaluate_case(
                        repository, merge_method, commit_sha, tree_sha,
                        configured_blob_sha=reviewed_blob)
                    for gate_name in ("required_ci", "ci_candidate_binding"):
                        self.assertEqual(
                            gate["gates"][gate_name]["result"], "PASS")
                with self.subTest(merge_method=merge_method,
                                  content="unreviewed"):
                    gate = self.evaluate_case(
                        repository, merge_method, changed_commit, changed_tree,
                        configured_blob_sha=reviewed_blob)
                    for gate_name in ("required_ci", "ci_candidate_binding"):
                        self.assertEqual(
                            gate["gates"][gate_name]["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()
