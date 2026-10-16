"""AWF-134 crash recovery regressions for explicitly typed journal updates."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError  # noqa: E402
import agentic.installer as installer  # noqa: E402
import test_awf134_installer_journal as journal_tests  # noqa: E402


CapturedIntent = journal_tests.CapturedIntent


class PendingJournalUpdateTests(unittest.TestCase):
    PAYLOAD_SIZE = journal_tests.CompactInstallerJournalTests.PAYLOAD_SIZE
    prepare_install = journal_tests.CompactInstallerJournalTests.prepare_install
    snapshot_project = staticmethod(
        journal_tests.CompactInstallerJournalTests.snapshot_project)

    def setUp(self):
        journal_tests.CompactInstallerJournalTests.setUp(self)

    def snapshot_transaction(self, destination):
        return {
            relative: (destination / relative).read_bytes()
            for relative in (installer.JOURNAL, installer.MARKER)
            if (destination / relative).exists()
        } | {
            path.relative_to(destination).as_posix(): path.read_bytes()
            for path in (destination / installer.RECOVERY_OBJECTS).glob("*")
            if path.is_file()
        }

    def interrupt_managed_after_update(self, crash_point="marker"):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=1)
        runtime = destination / installer.RUNTIME
        runtime.mkdir(parents=True)
        (runtime / "runtime.bin").write_bytes(b"exact prior runtime\n")
        before = self.snapshot_project(destination)
        original_write = installer.Tree.write
        pending_updates = 0
        managed_precommitted = False

        def interrupt_write(tree, relative, data):
            nonlocal pending_updates, managed_precommitted
            if relative == installer.MARKER:
                marker = json.loads(data.decode("utf-8"))
                if marker.get("pending_journal_sha256") is not None:
                    pending_updates += 1
                    if pending_updates == 2:
                        managed_precommitted = True
                        original_write(tree, relative, data)
                        if crash_point == "marker":
                            raise CapturedIntent
                        return None
            if (relative == installer.JOURNAL and managed_precommitted and
                    crash_point == "journal"):
                original_write(tree, relative, data)
                raise CapturedIntent
            return original_write(tree, relative, data)

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer.Tree, "write", new=interrupt_write), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup",
                              defer_runtime=True)
        return destination, before

    def interrupt_after_managed_after_publish(self):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=1)
        target = destination / ".agentic/adopted-clone/0000.bin"
        os.chmod(target, 0o600)
        runtime = destination / installer.RUNTIME
        runtime.mkdir(parents=True)
        runtime_file = runtime / "runtime.bin"
        runtime_file.write_bytes(b"exact prior runtime\n")
        before = self.snapshot_project(destination)
        before_mode = stat.S_IMODE(target.stat().st_mode)
        before_runtime = installer._runtime_tree_sha256(runtime)
        original_verify = installer._verify_journal_managed_state

        def interrupt_verify(tree, journal, which, context):
            if context == "after managed writes":
                raise CapturedIntent
            return original_verify(tree, journal, which, context)

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer, "_verify_journal_managed_state",
                             side_effect=interrupt_verify), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup",
                              defer_runtime=True)
        return destination, before, before_mode, before_runtime

    def active_object_state(self):
        destination, _path, _before, journal = (
            journal_tests.CompactInstallerJournalTests.interrupt_after_journal_publish(self))
        entry = next(item for item in journal["files"]
                     if item["old_sha256"] is not None)
        object_path = destination / installer.RECOVERY_OBJECTS / entry["old_sha256"]
        return destination, entry, object_path

    def interrupt_numbered_update(self, pending_number, crash_point, *, fail_after=None):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=1)
        before = self.snapshot_project(destination)
        original_write = installer.Tree.write
        observed = {"pending": 0, "selected": False, "kind": None}

        def interrupt_write(tree, relative, data):
            if relative == installer.MARKER:
                marker = json.loads(data.decode("utf-8"))
                if marker.get("pending_journal_sha256") is not None:
                    observed["pending"] += 1
                    if observed["pending"] == pending_number:
                        observed["selected"] = True
                        observed["kind"] = marker.get("pending_update_kind")
                        original_write(tree, relative, data)
                        if crash_point == "marker":
                            raise CapturedIntent
                        return None
            if (relative == installer.JOURNAL and observed["selected"] and
                    crash_point == "journal"):
                original_write(tree, relative, data)
                raise CapturedIntent
            return original_write(tree, relative, data)

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer.Tree, "write", new=interrupt_write), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup",
                              fail_after=fail_after)
        return destination, before, observed["kind"]

    def assert_recovery_damage_fails_closed(self, destination, message):
        project_before = self.snapshot_project(destination)
        transaction_before = self.snapshot_transaction(destination)
        with self.assertRaisesRegex(ValidationError, message):
            installer.recover(destination)
        self.assertEqual(project_before, self.snapshot_project(destination))
        self.assertEqual(transaction_before, self.snapshot_transaction(destination))

    def test_awf134_r3_001_pending_managed_after_without_runtime_stage_rolls_back(self):
        destination, before = self.interrupt_managed_after_update("marker")
        journal = json.loads((destination / installer.JOURNAL).read_text(encoding="utf-8"))
        marker = json.loads((destination / installer.MARKER).read_text(encoding="utf-8"))

        self.assertEqual("active", journal["phase"])
        self.assertIsNone(journal["managed_after_sha256"])
        self.assertIsNone(journal["runtime"]["new_sha256"])
        self.assertFalse((destination / journal["runtime"]["stage"]).exists())
        self.assertEqual("managed_after", marker["pending_update_kind"])
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))
        self.assertFalse((destination / installer.JOURNAL).exists())
        self.assertFalse((destination / installer.MARKER).exists())

    def test_awf134_crash_after_activation_precommit_before_journal_replace_rolls_back(self):
        destination, before, kind = self.interrupt_numbered_update(1, "marker")
        self.assertEqual("activate", kind)
        self.assertEqual("object_prepare", json.loads(
            (destination / installer.JOURNAL).read_text(encoding="utf-8"))["phase"])
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))

    def test_awf134_crash_after_activation_journal_replace_before_final_marker_rolls_back(self):
        destination, before, kind = self.interrupt_numbered_update(1, "journal")
        self.assertEqual("activate", kind)
        self.assertEqual("active", json.loads(
            (destination / installer.JOURNAL).read_text(encoding="utf-8"))["phase"])
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))

    def test_awf134_crash_after_managed_after_journal_replace_before_final_marker_rolls_back(self):
        destination, before = self.interrupt_managed_after_update("journal")
        journal = json.loads((destination / installer.JOURNAL).read_text(encoding="utf-8"))
        marker = json.loads((destination / installer.MARKER).read_text(encoding="utf-8"))

        self.assertIsNotNone(journal["managed_after_sha256"])
        self.assertEqual("managed_after", marker["pending_update_kind"])
        self.assertEqual(marker["pending_journal_sha256"],
                         installer.sha256(installer.json_bytes(journal)))
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))

    def test_awf134_recovery_round_trip_restores_exact_bytes_modes_and_runtime(self):
        destination, before, before_mode, before_runtime = (
            self.interrupt_after_managed_after_publish())

        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))

        target = destination / ".agentic/adopted-clone/0000.bin"
        self.assertEqual(before, self.snapshot_project(destination))
        self.assertEqual(before_mode, stat.S_IMODE(target.stat().st_mode))
        self.assertEqual(before_runtime,
                         installer._runtime_tree_sha256(destination / installer.RUNTIME))

    def test_awf134_crash_after_runtime_stage_precommit_recovers_by_explicit_kind(self):
        destination, before, _before_mode, _before_runtime = (
            self.interrupt_after_managed_after_publish())
        original_write = installer.Tree.write

        def interrupt_write(tree, relative, data):
            if relative == installer.MARKER:
                marker = json.loads(data.decode("utf-8"))
                if marker.get("pending_update_kind") == "runtime_stage":
                    original_write(tree, relative, data)
                    raise CapturedIntent
            return original_write(tree, relative, data)

        with installer.Tree(destination) as tree, \
                patch.object(installer.Tree, "write", new=interrupt_write), \
                self.assertRaises(CapturedIntent):
            journal, _raw, _marker = installer._read_bound_journal(tree)
            transaction = installer._RuntimeInstallTransaction(tree, journal)
            transaction.stage.mkdir()
            (transaction.stage / "runtime.bin").write_bytes(b"new runtime\n")
            transaction.record_staged_runtime()

        marker = json.loads((destination / installer.MARKER).read_text(encoding="utf-8"))
        self.assertEqual("runtime_stage", marker["pending_update_kind"])
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))

    def test_awf134_crash_after_commit_precommit_before_journal_replace_rolls_back(self):
        destination, before, kind = self.interrupt_numbered_update(3, "marker")
        self.assertEqual("commit_cleanup", kind)
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))

    def test_awf134_crash_after_commit_journal_replace_before_final_marker_commits(self):
        destination, _before, kind = self.interrupt_numbered_update(3, "journal")
        target = destination / ".agentic/adopted-clone/0000.bin"
        self.assertEqual("commit_cleanup", kind)
        self.assertEqual(b"n" * self.PAYLOAD_SIZE, target.read_bytes())
        self.assertEqual({"status": "COMMITTED"}, installer.recover(destination))
        self.assertEqual(b"n" * self.PAYLOAD_SIZE, target.read_bytes())

    def test_awf134_crash_after_cleanup_auth_precommit_resumes_commit(self):
        destination, _before, _before_mode, _before_runtime = (
            self.interrupt_after_managed_after_publish())
        journal = json.loads((destination / installer.JOURNAL).read_text(encoding="utf-8"))
        transaction_id = journal["transaction_id"]
        original_write = installer.Tree.write

        def interrupt_write(tree, relative, data):
            if relative == installer.MARKER:
                marker = json.loads(data.decode("utf-8"))
                if marker.get("pending_update_kind") == "commit_cleanup_authenticated":
                    original_write(tree, relative, data)
                    raise CapturedIntent
            return original_write(tree, relative, data)

        def build_runtime(transaction):
            transaction.stage.mkdir()
            (transaction.stage / "runtime.bin").write_bytes(b"new runtime\n")
            transaction.record_staged_runtime()
            os.replace(transaction.runtime_root, transaction.backup)
            os.replace(transaction.stage, transaction.runtime_root)
            return {"status": "BUILT"}

        with patch.object(installer.Tree, "write", new=interrupt_write), \
                self.assertRaises(CapturedIntent):
            installer.complete_runtime_transaction(
                destination, transaction_id, build_runtime)

        marker = json.loads((destination / installer.MARKER).read_text(encoding="utf-8"))
        self.assertEqual("commit_cleanup_authenticated", marker["pending_update_kind"])
        self.assertEqual({"status": "COMMITTED"}, installer.recover(destination))
        self.assertEqual(b"new runtime\n",
                         (destination / installer.RUNTIME / "runtime.bin").read_bytes())

    def test_awf134_crash_after_rollback_cleanup_journal_replace_finishes_retirement(self):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=1)
        before = self.snapshot_project(destination)
        original_write = installer.Tree.write
        rollback_precommitted = False

        def interrupt_write(tree, relative, data):
            nonlocal rollback_precommitted
            if relative == installer.MARKER:
                marker = json.loads(data.decode("utf-8"))
                if marker.get("pending_update_kind") == "rollback_cleanup":
                    rollback_precommitted = True
                    return original_write(tree, relative, data)
            if relative == installer.JOURNAL and rollback_precommitted:
                original_write(tree, relative, data)
                raise CapturedIntent
            return original_write(tree, relative, data)

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer.Tree, "write", new=interrupt_write), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup", fail_after=1)

        marker = json.loads((destination / installer.MARKER).read_text(encoding="utf-8"))
        self.assertEqual("rollback_cleanup", marker["pending_update_kind"])
        self.assertEqual({"status": "ROLLED_BACK"}, installer.recover(destination))
        self.assertEqual(before, self.snapshot_project(destination))

    def test_awf134_managed_after_pending_digest_mismatch_fails_closed(self):
        destination, _before = self.interrupt_managed_after_update("marker")
        marker_path = destination / installer.MARKER
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["pending_journal_sha256"] = "f" * 64
        marker_path.write_bytes(installer.json_bytes(marker))

        self.assert_recovery_damage_fails_closed(
            destination, "Pending managed-after journal update digest mismatch; journal retained")

    def test_awf134_pending_managed_after_missing_object_retains_exact_artifacts(self):
        destination, _before = self.interrupt_managed_after_update("marker")
        journal = json.loads((destination / installer.JOURNAL).read_text(encoding="utf-8"))
        entry = next(item for item in journal["files"]
                     if item["old_sha256"] is not None)
        (destination / installer.RECOVERY_OBJECTS / entry["old_sha256"]).unlink()

        self.assert_recovery_damage_fails_closed(
            destination,
            f"Recovery object is missing; journal retained: {entry['old_sha256']}")

    def test_awf134_missing_recovery_object_fails_closed(self):
        destination, entry, object_path = self.active_object_state()
        object_path.unlink()
        self.assert_recovery_damage_fails_closed(
            destination,
            f"Recovery object is missing; journal retained: {entry['old_sha256']}")

    def test_awf134_recovery_object_hash_mismatch_fails_closed(self):
        destination, entry, object_path = self.active_object_state()
        object_path.write_bytes(b"x" * entry["old_size"])
        self.assert_recovery_damage_fails_closed(
            destination,
            f"Recovery object hash mismatch; journal retained: {entry['old_sha256']}")

    def test_awf134_recovery_object_size_mismatch_fails_closed(self):
        destination, entry, object_path = self.active_object_state()
        object_path.write_bytes(object_path.read_bytes() + b"x")
        self.assert_recovery_damage_fails_closed(
            destination,
            f"Recovery object size mismatch; journal retained: {entry['old_sha256']}")

    def test_awf134_inconsistent_duplicate_object_size_reference_fails_closed(self):
        source, destination, pin, content = self.prepare_install(
            unchanged_files=0, changed_files=2)
        second = destination / ".agentic/adopted-clone/0001.bin"
        second.write_bytes(b"p" * (self.PAYLOAD_SIZE + 1))
        original_write = installer.Tree.write

        def interrupt_first_payload(tree, relative, data):
            if relative == ".agentic/adopted-clone/0000.bin":
                raise CapturedIntent
            return original_write(tree, relative, data)

        with patch.object(installer, "verify_release", return_value=(pin, content)), \
                patch.object(installer.Tree, "write", new=interrupt_first_payload), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict="backup")

        journal_path = destination / installer.JOURNAL
        marker_path = destination / installer.MARKER
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        references = [item for item in journal["files"]
                      if item["old_sha256"] is not None]
        references[1]["old_sha256"] = references[0]["old_sha256"]
        journal_raw = installer.json_bytes(journal)
        journal_path.write_bytes(journal_raw)
        marker_path.write_bytes(installer.json_bytes(installer._journal_marker(
            journal["transaction_id"], installer.sha256(journal_raw))))

        self.assert_recovery_damage_fails_closed(
            destination,
            "Recovery object size references disagree; journal retained: " +
            references[0]["old_sha256"])

    def test_awf134_unknown_pending_update_kind_fails_closed(self):
        destination, _before = self.interrupt_managed_after_update("marker")
        marker_path = destination / installer.MARKER
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["pending_update_kind"] = "unknown"
        marker_path.write_bytes(installer.json_bytes(marker))

        self.assert_recovery_damage_fails_closed(
            destination,
            "Unknown or ambiguous pending journal update kind; journal retained")

    def test_awf134_pending_digest_without_kind_fails_closed(self):
        destination, _before = self.interrupt_managed_after_update("marker")
        marker_path = destination / installer.MARKER
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["pending_update_kind"] = None
        marker_path.write_bytes(installer.json_bytes(marker))

        self.assert_recovery_damage_fails_closed(
            destination,
            "Unknown or ambiguous pending journal update kind; journal retained")

    def test_awf134_marker_kind_inconsistent_with_journal_transition_fails_closed(self):
        destination, _before = self.interrupt_managed_after_update("marker")
        marker_path = destination / installer.MARKER
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["pending_update_kind"] = "runtime_stage"
        marker_path.write_bytes(installer.json_bytes(marker))

        self.assert_recovery_damage_fails_closed(
            destination,
            "Pending journal update kind does not match the bound transition; journal retained")


if __name__ == "__main__":
    unittest.main()
