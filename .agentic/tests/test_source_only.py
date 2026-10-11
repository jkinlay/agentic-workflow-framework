"""Source-repository detection must not trust an adopter-owned MANIFEST.json."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402
from source_only import (INSTALLATION_RECEIPT, SOURCE_MARKER, SOURCE_ONLY_REASON,
                         committed_git_tuple, is_awf_source_repository)  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
AWF_MANIFEST = {
    "files": {SOURCE_MARKER: "0" * 64},
    "format": "awf-manifest-1",
    "template_version": "1.9.4",
}


class SourceRepositoryDetectionTests(unittest.TestCase):
    def tree(self, *paths):
        raw = tempfile.TemporaryDirectory(prefix="awf-source-only-")
        self.addCleanup(raw.cleanup)
        root = Path(raw.name)
        for path in paths:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(AWF_MANIFEST) if path == "MANIFEST.json" else "{}"
            (root / path).write_text(content + "\n", encoding="utf-8")
        return root

    def test_source_tree_is_recognised(self):
        self.assertTrue(is_awf_source_repository(self.tree("MANIFEST.json", SOURCE_MARKER)))

    @source_only.skip_unless_source_repo()
    def test_current_awf_source_checkout_is_detected(self):
        self.assertTrue(source_only.AWF_SOURCE_REPOSITORY)
        self.assertTrue(is_awf_source_repository(ROOT))

    def test_installed_project_owning_root_manifest_is_not_source(self):
        self.assertFalse(is_awf_source_repository(self.tree("MANIFEST.json", INSTALLATION_RECEIPT)))
        self.assertFalse(is_awf_source_repository(self.tree("MANIFEST.json")))

    def test_adopter_manifest_and_same_named_script_are_not_source(self):
        root = self.tree("MANIFEST.json", SOURCE_MARKER)
        (root / "MANIFEST.json").write_text('{"project": "adopter"}\n', encoding="utf-8")
        self.assertFalse(is_awf_source_repository(root))

    def test_installation_receipt_wins_over_source_markers(self):
        self.assertFalse(is_awf_source_repository(
            self.tree("MANIFEST.json", SOURCE_MARKER, INSTALLATION_RECEIPT)))

    def test_skip_decorator_uses_source_detection_and_reports_reason(self):
        downstream = self.tree("MANIFEST.json")
        with mock.patch.object(source_only, "ROOT", downstream):
            decorated = source_only.skip_unless_source_repo()(lambda: None)
        self.assertTrue(decorated.__unittest_skip__)
        self.assertEqual(SOURCE_ONLY_REASON, decorated.__unittest_skip_why__)

        source = self.tree("MANIFEST.json", SOURCE_MARKER)
        with mock.patch.object(source_only, "ROOT", source):
            decorated = source_only.skip_unless_source_repo()(lambda: None)
        self.assertFalse(getattr(decorated, "__unittest_skip__", False))

    def test_self_test_uses_shared_source_detection(self):
        spec = importlib.util.spec_from_file_location(
            "awf_self_test_source_detection", ROOT / ".agentic/scripts/self_test.py")
        module = importlib.util.module_from_spec(spec)
        with mock.patch("source_only.is_awf_source_repository", return_value=False) as detector:
            spec.loader.exec_module(module)
        detector.assert_called_once_with(ROOT)
        self.assertFalse(module.SOURCE_REPOSITORY)

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
