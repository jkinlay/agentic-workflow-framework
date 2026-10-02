"""Portable manifest ordering is defined by POSIX relative path text."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "build_skill_distribution_ordering", ROOT / "scripts/build_skill_distribution.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PortableManifestOrderingTests(unittest.TestCase):
    def test_mixed_case_fixture_has_explicit_posix_order(self):
        with tempfile.TemporaryDirectory(prefix="awf-portable-order-") as raw:
            root = Path(raw)
            for relative in ("alpha/item.txt", "Zoo.txt", "awf/item.txt", "INSTALL.md"):
                path = root.joinpath(*relative.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            observed = [path.relative_to(root).as_posix() for path in builder.sorted_files(root)]
            self.assertEqual(observed, ["INSTALL.md", "Zoo.txt", "alpha/item.txt", "awf/item.txt"])


if __name__ == "__main__":
    unittest.main()
