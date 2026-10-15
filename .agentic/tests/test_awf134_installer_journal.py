"""AWF-134 regressions for change-sized installer recovery journals."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.canonical import MAX_DOCUMENT_BYTES, sha256  # noqa: E402
import agentic.installer as installer  # noqa: E402


class CapturedIntent(BaseException):
    """Stop after the journal has been assembled but before project writes."""


class CompactInstallerJournalTests(unittest.TestCase):
    PAYLOAD = b"n" * (64 * 1024)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf134-journal-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def capture_journal(self, *, unchanged_files, changed_files):
        source = self.root / f"source-{unchanged_files}-{changed_files}"
        destination = self.root / f"destination-{unchanged_files}-{changed_files}"
        source.mkdir()
        destination.mkdir()
        manifest_raw = json.dumps({
            "format": "awf-manifest-1",
            "template_version": installer.VERSION,
            "files": {},
        }, indent=2).encode("utf-8") + b"\n"
        (source / installer.MANIFEST).write_bytes(manifest_raw)
        pin = sha256(manifest_raw)
        content = {
            installer.CONFIG: (ROOT / installer.CONFIG).read_bytes(),
            installer.PROVENANCE: (ROOT / installer.PROVENANCE).read_bytes(),
        }
        total = unchanged_files + changed_files
        for index in range(total):
            relative = f".agentic/adopted-clone/{index:04d}.bin"
            content[relative] = self.PAYLOAD
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.PAYLOAD if index >= changed_files else b"o" * len(self.PAYLOAD))
        for relative in (installer.CONFIG, installer.PROVENANCE):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content[relative])

        captured = {}

        def capture(_tree, journal):
            captured["journal"] = json.loads(installer.json_bytes(journal))
            captured["raw"] = installer.json_bytes(journal)
            raise CapturedIntent

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer, "_publish_initial_intent", side_effect=capture), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup")
        return captured["journal"], captured["raw"]

    def test_awf134_adopted_clone_journal_has_headroom_and_ignores_unchanged_scale(self):
        small, small_raw = self.capture_journal(unchanged_files=128, changed_files=1)
        large, large_raw = self.capture_journal(unchanged_files=255, changed_files=1)

        self.assertEqual("awf-install-journal-4", small["format"])
        self.assertLess(len(small_raw), MAX_DOCUMENT_BYTES // 8)
        self.assertLess(len(large_raw), MAX_DOCUMENT_BYTES // 8)
        self.assertLess(abs(len(large_raw) - len(small_raw)), 1024)

    def test_awf134_journal_entries_are_only_changed_files_and_growth_tracks_changes(self):
        one, one_raw = self.capture_journal(unchanged_files=255, changed_files=1)
        four, four_raw = self.capture_journal(unchanged_files=252, changed_files=4)
        one_paths = {item["path"] for item in one["files"]}

        self.assertIn(".agentic/adopted-clone/0000.bin", one_paths)
        self.assertNotIn(".agentic/adopted-clone/0001.bin", one_paths)
        self.assertEqual({"path", "old", "old_mode", "new_sha256", "new_mode"},
                         set(next(item for item in one["files"]
                                  if item["path"] == ".agentic/adopted-clone/0000.bin")))
        one_project_paths = {path for path in one_paths if not path.startswith(".agentic-backup/")}
        four_project_paths = {item["path"] for item in four["files"]
                              if not item["path"].startswith(".agentic-backup/")}
        self.assertEqual(3, len(four_project_paths) - len(one_project_paths))
        self.assertGreater(len(four_raw) - len(one_raw), 3 * len(self.PAYLOAD))


if __name__ == "__main__":
    unittest.main()
