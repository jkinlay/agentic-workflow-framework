"""Content-bound CI workflow provenance remains stable across merge methods."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from agentic.configuration import valid_workflow_blob_sha
from agentic.gates import (candidate_workflow_blob_sha,
                           ci_workflow_provenance_matches)


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


if __name__ == "__main__":
    unittest.main()
