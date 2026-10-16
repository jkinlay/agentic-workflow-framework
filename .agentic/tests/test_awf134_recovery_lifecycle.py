"""AWF-134 regressions for recoverable side-store creation and retirement."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import agentic.installer as installer  # noqa: E402
import test_awf134_installer_journal as journal_tests  # noqa: E402


CapturedIntent = journal_tests.CapturedIntent


class SideStoreLifecycleTests(unittest.TestCase):
    PAYLOAD_SIZE = journal_tests.CompactInstallerJournalTests.PAYLOAD_SIZE
    prepare_install = journal_tests.CompactInstallerJournalTests.prepare_install
    interrupt_after_journal_publish = (
        journal_tests.CompactInstallerJournalTests.interrupt_after_journal_publish)
    snapshot_project = staticmethod(
        journal_tests.CompactInstallerJournalTests.snapshot_project)

    def setUp(self):
        journal_tests.CompactInstallerJournalTests.setUp(self)

    @staticmethod
    def recovery_object_files(destination):
        root = destination / installer.RECOVERY_OBJECTS
        return sorted(path for path in root.glob("*") if path.is_file())

    def test_awf134_interrupted_object_creation_retains_recoverable_intent(self):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=2)
        # Ensure the two prior files produce distinct side-store objects so the
        # interruption leaves one verified object and one missing object.
        (destination / ".agentic/adopted-clone/0001.bin").write_bytes(
            b"p" * self.PAYLOAD_SIZE)
        before = self.snapshot_project(destination)
        original_read_object = installer._read_recovery_object
        interrupted = False

        def interrupt_after_first_verified_object(tree, digest, size):
            nonlocal interrupted
            data = original_read_object(tree, digest, size)
            if not interrupted:
                interrupted = True
                raise CapturedIntent
            return data

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer, "_read_recovery_object",
                             side_effect=interrupt_after_first_verified_object), \
                patch.object(installer, "_cleanup_recovery_objects", return_value=None), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup")

        self.assertTrue(self.recovery_object_files(destination))
        self.assertTrue((destination / installer.JOURNAL).exists())
        self.assertTrue((destination / installer.MARKER).exists())

        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))
        self.assertFalse(self.recovery_object_files(destination))

    def test_awf134_interrupted_rollback_retirement_remains_recoverable(self):
        destination, _path, before, _journal = self.interrupt_after_journal_publish()

        with patch.object(installer, "_cleanup_recovery_objects",
                          side_effect=CapturedIntent), \
                self.assertRaises(CapturedIntent):
            installer.recover(destination)

        self.assertTrue(self.recovery_object_files(destination))
        self.assertTrue((destination / installer.JOURNAL).exists())
        self.assertTrue((destination / installer.MARKER).exists())

        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))
        self.assertFalse(self.recovery_object_files(destination))

    def test_awf134_pending_rollback_phase_is_not_mistaken_for_runtime_update(self):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=1)
        before = self.snapshot_project(destination)
        original_update = installer._write_bound_journal_update

        def interrupt_rollback_phase(tree, journal, update_kind=None):
            if journal["phase"] != "rollback_cleanup":
                return original_update(tree, journal, update_kind)
            current, current_raw, _marker = installer._read_bound_journal(tree)
            updated_raw = installer.json_bytes(journal)
            tree.write(installer.MARKER, installer.json_bytes(installer._journal_marker(
                current["transaction_id"], installer.sha256(current_raw),
                installer.sha256(updated_raw), "rollback_cleanup")))
            raise CapturedIntent

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer, "_write_bound_journal_update",
                             side_effect=interrupt_rollback_phase), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup",
                              defer_runtime=True, fail_after=1)

        marker = installer.loads(
            (destination / installer.MARKER).read_text(encoding="utf-8"))
        journal = installer.loads(
            (destination / installer.JOURNAL).read_text(encoding="utf-8"))
        self.assertEqual("active", journal["phase"])
        self.assertIsNotNone(marker["pending_journal_sha256"])
        self.assertEqual("rollback_cleanup", marker["pending_update_kind"])

        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))
        self.assertFalse(self.recovery_object_files(destination))


if __name__ == "__main__":
    unittest.main()
