"""Content-bound CI workflow provenance remains stable across merge methods."""
from __future__ import annotations

import unittest

from agentic.configuration import valid_workflow_blob_sha
from agentic.gates import ci_workflow_provenance_matches


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
        manifest = [{"path": self.path, "blob_sha": self.reviewed_blob}]

        # Squash, merge-commit, and rebase merges can all assign a different
        # commit identity while retaining the exact reviewed workflow bytes.
        for merged_workflow_sha in ("b" * 40, "c" * 40, "d" * 40):
            with self.subTest(merged_workflow_sha=merged_workflow_sha):
                self.assertTrue(ci_workflow_provenance_matches(
                    self.required(), self.observed(merged_workflow_sha), manifest))

    def test_modified_or_unreviewed_workflow_fails_closed(self):
        manifest = [{"path": self.path, "blob_sha": "2" * 40}]

        self.assertFalse(ci_workflow_provenance_matches(
            self.required(), self.observed("b" * 40), manifest))

    def test_missing_or_malformed_content_binding_fails_closed(self):
        without_either_pin = self.required()
        without_either_pin.pop("workflow_blob_sha")
        without_either_pin.pop("workflow_sha")
        self.assertFalse(ci_workflow_provenance_matches(
            without_either_pin, self.observed("a" * 40), []))

        for malformed in (None, "", "1" * 39, "G" * 40, "0" * 40, 1):
            with self.subTest(malformed=malformed):
                required = self.required(workflow_blob_sha=malformed)
                self.assertFalse(valid_workflow_blob_sha(malformed))
                self.assertFalse(ci_workflow_provenance_matches(
                    required, self.observed(required["workflow_sha"]), []))

    def test_unchanged_workflow_uses_reviewed_content_binding(self):
        self.assertTrue(ci_workflow_provenance_matches(
            self.required(), self.observed("b" * 40),
            [{"path": "src/example.py", "blob_sha": "3" * 40}]))

    def test_legacy_exact_workflow_sha_comparison_remains_valid(self):
        required = self.required()
        required.pop("workflow_blob_sha")

        self.assertTrue(ci_workflow_provenance_matches(
            required, self.observed(required["workflow_sha"]), []))
        self.assertFalse(ci_workflow_provenance_matches(
            required, self.observed("b" * 40), []))

    def test_identity_mismatch_fails_for_both_pin_modes(self):
        manifest = [{"path": self.path, "blob_sha": self.reviewed_blob}]
        for field, value in (("name", "other"), ("app_id", 7),
                             ("workflow_path", ".github/workflows/other.yml")):
            with self.subTest(field=field):
                observed = self.observed("a" * 40)
                observed[field] = value
                self.assertFalse(ci_workflow_provenance_matches(
                    self.required(), observed, manifest))


if __name__ == "__main__":
    unittest.main()
