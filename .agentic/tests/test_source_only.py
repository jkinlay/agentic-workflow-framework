"""Source-repository detection must not trust an adopter-owned MANIFEST.json."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_only import (INSTALLATION_RECEIPT, SOURCE_MARKER, committed_git_tuple,
                         is_awf_source_repository)  # noqa: E402


class SourceRepositoryDetectionTests(unittest.TestCase):
    def tree(self, *paths):
        raw = tempfile.TemporaryDirectory(prefix="awf-source-only-")
        self.addCleanup(raw.cleanup)
        root = Path(raw.name)
        for path in paths:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("{}\n", encoding="utf-8")
        return root

    def test_source_tree_is_recognised(self):
        self.assertTrue(is_awf_source_repository(self.tree("MANIFEST.json", SOURCE_MARKER)))

    def test_installed_project_owning_root_manifest_is_not_source(self):
        self.assertFalse(is_awf_source_repository(self.tree("MANIFEST.json", INSTALLATION_RECEIPT)))
        self.assertFalse(is_awf_source_repository(self.tree("MANIFEST.json")))

    def test_installation_receipt_wins_over_source_markers(self):
        self.assertFalse(is_awf_source_repository(
            self.tree("MANIFEST.json", SOURCE_MARKER, INSTALLATION_RECEIPT)))

    def test_commitless_git_repository_has_no_tuple(self):
        root = self.tree()
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        self.assertIsNone(committed_git_tuple(root))

    def test_committed_git_repository_returns_head_and_tree(self):
        root = self.tree("tracked.txt")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
        subprocess.run([
            "git", "-c", "user.name=AWF Test", "-c", "user.email=awf@example.invalid",
            "-C", str(root), "commit", "-qm", "fixture"], check=True)
        head, tree = committed_git_tuple(root)
        self.assertIn(len(head), (40, 64))
        self.assertIn(len(tree), (40, 64))

    def test_source_checkout_git_failure_is_not_a_commitless_skip(self):
        root = self.tree("MANIFEST.json", SOURCE_MARKER)
        self.assertTrue(is_awf_source_repository(root))
        failed = subprocess.CompletedProcess(
            ["git"], 128, stdout="", stderr="fatal: detected dubious ownership")
        with mock.patch("source_only.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "does not have a verified unborn HEAD"):
                committed_git_tuple(root)

    def test_unavailable_git_is_not_a_commitless_skip(self):
        with mock.patch("source_only.subprocess.run", side_effect=OSError("git unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Git execution failed"):
                committed_git_tuple(self.tree())


if __name__ == "__main__":
    unittest.main()
