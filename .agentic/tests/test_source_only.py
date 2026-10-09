"""Source-repository detection must not trust an adopter-owned MANIFEST.json."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_only import INSTALLATION_RECEIPT, SOURCE_MARKER, is_awf_source_repository  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
