from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.safeio import Tree


class TreeUnlinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-safeio-unlink-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_parent_or_ancestor_is_idempotent(self):
        with Tree(self.root) as tree:
            for relative in ("absent/leaf.txt", "deeper/missing/leaf.txt"):
                with self.subTest(relative=relative):
                    tree.unlink(relative)

    def test_existing_file_is_removed(self):
        target = self.root / "present.txt"
        target.write_bytes(b"fixture\n")

        with Tree(self.root) as tree:
            tree.unlink("present.txt")

        self.assertFalse(target.exists())

    def test_symlinked_leaf_is_refused(self):
        outside = self.base / "outside.txt"
        outside.write_bytes(b"outside\n")
        linked = self.root / "linked.txt"
        try:
            linked.symlink_to(outside)
        except OSError as exc:
            self.skipTest("file symlinks unavailable: " + type(exc).__name__)

        with Tree(self.root) as tree:
            with self.assertRaises(ValidationError):
                tree.unlink("linked.txt")

        self.assertEqual(b"outside\n", outside.read_bytes())
        self.assertTrue(linked.is_symlink())

    def test_out_of_tree_path_is_refused(self):
        outside = self.base / "outside.txt"
        outside.write_bytes(b"outside\n")

        with Tree(self.root) as tree:
            with self.assertRaises(ValidationError):
                tree.unlink("../outside.txt")

        self.assertEqual(b"outside\n", outside.read_bytes())


if __name__ == "__main__":
    unittest.main()
