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

from agentic import ValidationError  # noqa: E402
from agentic.canonical import MAX_DOCUMENT_BYTES, sha256  # noqa: E402
import agentic.installer as installer  # noqa: E402


class CapturedIntent(BaseException):
    """Model a process interruption without invoking installer rollback."""


class CompactInstallerJournalTests(unittest.TestCase):
    PAYLOAD_SIZE = 64 * 1024

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf134-journal-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture_index = 0

    def prepare_install(self, *, unchanged_files, changed_files, payload_size=None):
        self.fixture_index += 1
        payload_size = payload_size or self.PAYLOAD_SIZE
        source = self.root / f"source-{self.fixture_index}"
        destination = self.root / f"destination-{self.fixture_index}"
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
        new_bytes = b"n" * payload_size
        old_bytes = b"o" * payload_size
        total = unchanged_files + changed_files
        for index in range(total):
            relative = f".agentic/adopted-clone/{index:04d}.bin"
            content[relative] = new_bytes
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(new_bytes if index >= changed_files else old_bytes)
        for relative in (installer.CONFIG, installer.PROVENANCE):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content[relative])
        return source, destination, pin, content

    def capture_journal(self, *, unchanged_files, changed_files, payload_size=None):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=unchanged_files,
            changed_files=changed_files,
            payload_size=payload_size,
        )
        captured = {}

        original_update = installer._write_bound_journal_update

        def capture(tree, journal, update_kind=None):
            if journal["phase"] != "active" or journal["managed_after_sha256"] is not None:
                return original_update(tree, journal, update_kind)
            captured["journal"] = json.loads(installer.json_bytes(journal))
            captured["raw"] = installer.json_bytes(journal)
            captured["objects"] = {
                item["old_sha256"]: tree.read(
                    f"{installer.RECOVERY_OBJECTS}/{item['old_sha256']}")
                for item in journal["files"] if item["old_sha256"] is not None
            }
            raise CapturedIntent

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer, "_write_bound_journal_update", side_effect=capture), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup")
        return captured["journal"], captured["raw"], captured["objects"]

    def interrupt_after_journal_publish(self):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=1)
        interrupted_path = ".agentic/adopted-clone/0000.bin"
        original_write = installer.Tree.write

        def interrupt_write(tree, relative, data):
            if relative == interrupted_path and tree.inspect(installer.MARKER) is not None:
                raise CapturedIntent
            return original_write(tree, relative, data)

        before = self.snapshot_project(destination)
        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer.Tree, "write", new=interrupt_write), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup")
        journal = json.loads((destination / installer.JOURNAL).read_text(encoding="utf-8"))
        return destination, interrupted_path, before, journal

    @staticmethod
    def snapshot_project(destination):
        return {
            path.relative_to(destination).as_posix(): path.read_bytes()
            for path in destination.rglob("*")
            if path.is_file() and not path.relative_to(destination).as_posix().startswith(
                ".agentic-install/")
        }

    def test_awf134_adopted_clone_journal_has_headroom_and_ignores_unchanged_scale(self):
        small, small_raw, _objects = self.capture_journal(unchanged_files=128, changed_files=1)
        large, large_raw, _objects = self.capture_journal(unchanged_files=255, changed_files=1)

        self.assertEqual("awf-install-journal-4", small["format"])
        self.assertLess(len(small_raw), MAX_DOCUMENT_BYTES // 8)
        self.assertLess(len(large_raw), MAX_DOCUMENT_BYTES // 8)
        self.assertLess(abs(len(large_raw) - len(small_raw)), 1024)

    def test_awf134_journal_entries_reference_side_store_and_growth_tracks_changes(self):
        one, one_raw, objects = self.capture_journal(unchanged_files=255, changed_files=1)
        four, four_raw, _objects = self.capture_journal(unchanged_files=252, changed_files=4)
        one_paths = {item["path"] for item in one["files"]}
        changed = next(item for item in one["files"]
                       if item["path"] == ".agentic/adopted-clone/0000.bin")

        self.assertIn(".agentic/adopted-clone/0000.bin", one_paths)
        self.assertNotIn(".agentic/adopted-clone/0001.bin", one_paths)
        self.assertEqual({"path", "old_sha256", "old_size", "old_mode",
                          "new_sha256", "new_mode"}, set(changed))
        self.assertNotIn("old", changed)
        self.assertEqual(self.PAYLOAD_SIZE, changed["old_size"])
        self.assertEqual(b"o" * self.PAYLOAD_SIZE, objects[changed["old_sha256"]])
        one_project_paths = {path for path in one_paths if not path.startswith(".agentic-backup/")}
        four_project_paths = {item["path"] for item in four["files"]
                              if not item["path"].startswith(".agentic-backup/")}
        self.assertEqual(3, len(four_project_paths) - len(one_project_paths))
        self.assertGreater(len(four_raw), len(one_raw))
        self.assertLess(len(four_raw) - len(one_raw), 3 * 1024)

    def test_awf134_journal_size_is_independent_of_recovery_payload_bytes(self):
        _small, small_raw, _objects = self.capture_journal(
            unchanged_files=0, changed_files=1, payload_size=64 * 1024)
        _large, large_raw, _objects = self.capture_journal(
            unchanged_files=0, changed_files=1, payload_size=96 * 1024)

        self.assertEqual(len(small_raw), len(large_raw))

    def test_awf134_recover_round_trip_restores_prior_bytes_from_side_store(self):
        destination, _path, before, _journal = self.interrupt_after_journal_publish()

        result = installer.recover(destination)

        self.assertEqual({"status": "ROLLED_BACK"}, result)
        self.assertEqual(before, self.snapshot_project(destination))
        self.assertFalse((destination / installer.JOURNAL).exists())
        self.assertFalse((destination / installer.MARKER).exists())

    def test_awf134_missing_or_tampered_recovery_object_fails_closed(self):
        for damage in ("missing", "hash", "size"):
            with self.subTest(damage=damage):
                destination, path, _before, journal = self.interrupt_after_journal_publish()
                entry = next(item for item in journal["files"] if item["path"] == path)
                object_path = destination / installer.RECOVERY_OBJECTS / entry["old_sha256"]
                if damage == "missing":
                    object_path.unlink()
                elif damage == "hash":
                    object_path.write_bytes(b"x" * entry["old_size"])
                else:
                    object_path.write_bytes(object_path.read_bytes() + b"x")
                state_before_recovery = self.snapshot_project(destination)

                with self.assertRaisesRegex(ValidationError, "recovery object"):
                    installer.recover(destination)

                self.assertEqual(state_before_recovery, self.snapshot_project(destination))
                self.assertTrue((destination / installer.JOURNAL).exists())
                self.assertTrue((destination / installer.MARKER).exists())


if __name__ == "__main__":
    unittest.main()
