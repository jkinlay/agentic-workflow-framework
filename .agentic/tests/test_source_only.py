"""Source-repository detection must not trust an adopter-owned MANIFEST.json."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402
from source_only import (INSTALLATION_RECEIPT, SOURCE_MARKER, SOURCE_ONLY_REASON,
                         committed_git_tuple, is_awf_source_repository,
                         write_awf_source_fixture)  # noqa: E402

from agentic import operating, operating_status, streams  # noqa: E402
from agentic.providers import github_review_host  # noqa: E402
from agentic.safeio import Tree  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class SourceRepositoryDetectionTests(unittest.TestCase):
    def tree(self, *paths):
        raw = tempfile.TemporaryDirectory(prefix="awf-source-only-")
        self.addCleanup(raw.cleanup)
        root = Path(raw.name)
        for path in paths:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("{}\n", encoding="utf-8")
        return root

    def installed_downstream_tree(self):
        root = self.tree(
            "MANIFEST.json",
            INSTALLATION_RECEIPT,
            ".agentic/SPECIFICATION.md",
            ".agentic/lib/agentic/operating.py",
        )
        (root / "MANIFEST.json").write_text(
            '{"format":"adopter-manifest-1","project":"downstream"}\n',
            encoding="utf-8",
        )
        return root

    @staticmethod
    def reader(root):
        def read(name):
            path = root / name
            try:
                return path.read_bytes() if path.is_file() else None
            except OSError:
                return None
        return read

    @staticmethod
    def git_runner(root, *arguments):
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True, text=True, check=True,
        )
        return completed.stdout.strip()

    def commit_tree(self, root):
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run([
            "git", "-c", "user.name=AWF Test", "-c",
            "user.email=awf@example.invalid", "-C", str(root),
            "commit", "-qm", "fixture",
        ], check=True)

    def test_source_tree_is_recognised(self):
        root = self.tree()
        write_awf_source_fixture(root)
        self.assertTrue(is_awf_source_repository(root))

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
        source = self.tree(INSTALLATION_RECEIPT)
        write_awf_source_fixture(source)
        self.assertFalse(is_awf_source_repository(source))

    def test_canonical_library_predicate_distinguishes_source_and_installed_downstream(self):
        downstream = self.installed_downstream_tree()
        source = self.tree()
        write_awf_source_fixture(source)
        self.assertFalse(operating.is_awf_source_repository(self.reader(downstream)))
        self.assertTrue(operating.is_awf_source_repository(self.reader(source)))

    @source_only.skip_unless_source_repo()
    def test_revision_detector_distinguishes_installed_downstream_and_real_source(self):
        downstream = self.installed_downstream_tree()
        self.commit_tree(downstream)
        self.assertFalse(github_review_host.is_source_repository(
            downstream, git_runner=self.git_runner))
        self.assertTrue(github_review_host.is_source_repository(
            ROOT, git_runner=self.git_runner))

    def test_revision_detector_fails_closed_without_a_readable_git_tree(self):
        root = self.tree()
        self.assertFalse(github_review_host.is_source_repository(root))
        self.assertFalse(github_review_host.is_source_repository(
            root, git_runner=mock.Mock(side_effect=OSError("git unavailable"))))
        self.assertFalse(github_review_host.is_source_repository(
            root, git_runner=mock.Mock(return_value=b"unreadable tree")))

        def unreadable_blob(_root, *arguments):
            if arguments[0] == "ls-tree":
                return "MANIFEST.json\nscripts/build_release.py"
            return object()

        self.assertFalse(github_review_host.is_source_repository(
            root, git_runner=unreadable_blob))

    def test_operating_lock_uses_canonical_source_predicate(self):
        downstream = self.installed_downstream_tree()
        detector = operating.is_awf_source_repository
        with mock.patch.object(operating, "is_awf_source_repository", wraps=detector) as shared:
            with mock.patch.object(operating, "verify_installed"):
                with Tree(downstream) as tree, operating._lock(tree):
                    pass
        shared.assert_called_once()

        source = self.tree()
        write_awf_source_fixture(source)
        with Tree(source) as tree:
            with self.assertRaisesRegex(operating.OperatingError, "immutable release source"):
                with operating._lock(tree):
                    pass

    def test_operating_status_uses_canonical_source_predicate(self):
        downstream = self.installed_downstream_tree()
        report = {"status": "ACCEPTED", "unresolved": []}
        inspected = {
            "status": "ACCEPTED", "hash": "0" * 64, "source": "default",
            "streams": 3, "last_change_id": None, "refusals": [],
        }
        detector = operating.is_awf_source_repository
        with mock.patch.object(operating_status, "is_awf_source_repository", wraps=detector) as shared:
            with mock.patch.object(operating_status, "inspect_operating", return_value=inspected):
                result = operating_status.with_operating(downstream, {}, report)
        shared.assert_called_once()
        self.assertEqual(inspected, result["operating"])

        source = self.tree()
        write_awf_source_fixture(source)
        snapshot = mock.Mock(operating_hash="1" * 64, source="default", count=3)
        with mock.patch.object(operating_status, "validate_operating", return_value=snapshot):
            with mock.patch.object(operating_status, "operating_ceiling", return_value={}):
                result = operating_status.with_operating(source, {}, report)
        self.assertEqual("source_template", result["operating"]["provenance"])

    def test_stream_publication_uses_canonical_source_predicate(self):
        downstream = self.installed_downstream_tree()
        project = downstream / "project"
        project.mkdir()
        raw = (ROOT / ".agentic/examples/stream-input.json").read_bytes()
        result = streams.write_project_plan(
            project, raw, streams.sha256(raw), "2026-09-11T10:00:00Z",
            downstream, allow_synthetic=True,
        )
        self.assertEqual("PLANNED", result["status"])

        source = self.tree()
        write_awf_source_fixture(source)
        project = source / "project"
        project.mkdir()
        with self.assertRaisesRegex(operating.ValidationError, "immutable release source"):
            streams.write_project_plan(
                project, raw, streams.sha256(raw), "2026-09-11T10:00:00Z",
                source, allow_synthetic=True,
            )

    def test_skip_decorator_uses_source_detection_and_reports_reason(self):
        downstream = self.tree("MANIFEST.json")
        with mock.patch.object(source_only, "ROOT", downstream):
            decorated = source_only.skip_unless_source_repo()(lambda: None)
        self.assertTrue(decorated.__unittest_skip__)
        self.assertEqual(SOURCE_ONLY_REASON, decorated.__unittest_skip_why__)

        source = self.tree()
        write_awf_source_fixture(source)
        with mock.patch.object(source_only, "ROOT", source):
            decorated = source_only.skip_unless_source_repo()(lambda: None)
        self.assertFalse(getattr(decorated, "__unittest_skip__", False))

    def test_self_test_uses_shared_source_detection(self):
        spec = importlib.util.spec_from_file_location(
            "awf_self_test_source_detection", ROOT / ".agentic/scripts/self_test.py")
        module = importlib.util.module_from_spec(spec)
        with mock.patch("agentic.operating.is_awf_source_repository", return_value=False) as detector:
            spec.loader.exec_module(module)
        detector.assert_called_once()
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
        root = self.tree()
        write_awf_source_fixture(root)
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
