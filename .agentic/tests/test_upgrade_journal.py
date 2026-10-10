"""Crash-point coverage for the managed-after upgrade journal update."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic.canonical import sha256  # noqa: E402
from agentic.installer import JOURNAL, MARKER, install, recover, rollback, verify_installed  # noqa: E402
import agentic.safeio as safeio  # noqa: E402
from agentic.safeio import Tree  # noqa: E402
from source_only import skip_unless_source_repo  # noqa: E402
from upgrade_fixtures import file_tree, materialize, verify_materialized  # noqa: E402


class SimulatedCrash(BaseException):
    """Escape the installer's Exception rollback like a terminated process."""


class SafeIoWindowsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows handle semantics")
    def test_unlink_leaf_swap_cannot_mutate_outside_file(self):
        base = Path(tempfile.gettempdir()) / ("awf-unlink-swap-" + uuid.uuid4().hex)
        base.mkdir()
        def cleanup():
            for path in base.rglob("*"):
                if path.is_file():
                    path.chmod(stat.S_IWRITE | stat.S_IREAD)
            shutil.rmtree(base)
        self.addCleanup(cleanup)
        root = base / "root"
        root.mkdir()
        victim = root / "victim.txt"
        replacement = root / "replacement.txt"
        outside = base / "outside.txt"
        victim.write_bytes(b"victim")
        victim.chmod(stat.S_IREAD)
        outside.write_bytes(b"outside")
        outside.chmod(stat.S_IREAD)
        os.link(outside, replacement)

        original = safeio._win_set_file_information
        swap = {"attempted": False, "blocked": False}

        def attempt_swap(handle, information_class, information):
            if not swap["attempted"]:
                swap["attempted"] = True
                try:
                    os.replace(replacement, victim)
                except OSError:
                    swap["blocked"] = True
            return original(handle, information_class, information)

        with patch.object(safeio, "_win_set_file_information", side_effect=attempt_swap):
            with Tree(root) as tree:
                tree.unlink("victim.txt")

        self.assertEqual(swap, {"attempted": True, "blocked": True})
        self.assertFalse(victim.exists())
        self.assertTrue(outside.exists())
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertTrue(os.stat(outside).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)

    @unittest.skipUnless(os.name == "nt", "Windows handle semantics")
    def test_readonly_rollback_crash_before_atomic_disposition_is_retryable(self):
        self.assert_readonly_rollback_crash_retryable("before")

    @unittest.skipUnless(os.name == "nt", "Windows handle semantics")
    def test_readonly_rollback_crash_after_atomic_disposition_is_retryable(self):
        self.assert_readonly_rollback_crash_retryable("after")

    def assert_readonly_rollback_crash_retryable(self, phase):
        base = Path(tempfile.gettempdir()) / (f"awf-unlink-{phase}-" + uuid.uuid4().hex)
        base.mkdir()
        self.addCleanup(self.cleanup_readonly_tree, base)
        archive = base / ".agentic-state" / "archive.json"
        archive.parent.mkdir()
        archive.write_bytes(b"archive")
        archive.chmod(stat.S_IREAD)
        (base / ".agentic").mkdir()
        (base / Path(MARKER)).write_bytes(b"marker")
        (base / Path(JOURNAL)).parent.mkdir()
        (base / Path(JOURNAL)).write_bytes(b"journal")
        mode = stat.S_IMODE(archive.stat().st_mode)
        journal = {
            "format": "awf-install-journal-3",
            "transaction_id": str(uuid.uuid4()),
            "phase": "active",
            "files": [{"path": ".agentic-state/archive.json", "old": None,
                       "old_mode": None, "new_sha256": sha256(b"archive"),
                       "new_mode": mode}],
            "managed_before": [],
            "managed_after": [],
            "runtime": None,
        }
        original = safeio._win_set_file_information
        injected = {"crashed": False}

        def crash_at_disposition(handle, information_class, information):
            if information_class == safeio.FILE_DISPOSITION_INFO_EX:
                injected["crashed"] = True
                if phase == "before":
                    raise SimulatedCrash("before atomic read-only disposition")
                original(handle, information_class, information)
                raise SimulatedCrash("after atomic read-only disposition")
            return original(handle, information_class, information)

        with patch.object(safeio, "_win_set_file_information",
                          side_effect=crash_at_disposition):
            with Tree(base) as tree, self.assertRaises(SimulatedCrash):
                rollback(tree, journal)

        self.assertTrue(injected["crashed"])
        if phase == "before":
            self.assertEqual(archive.read_bytes(), b"archive")
            self.assertTrue(os.stat(archive).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
        else:
            self.assertFalse(archive.exists())
        with Tree(base) as tree:
            rollback(tree, journal)
        self.assertFalse(archive.exists())

    @staticmethod
    def cleanup_readonly_tree(base):
        if not base.exists():
            return
        for path in base.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(base)


class SafeIoWriteTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX mode transition regression")
    def test_identical_write_preserves_existing_mode(self):
        base = Path(tempfile.gettempdir()) / ("awf-identical-write-" + uuid.uuid4().hex)
        base.mkdir()
        self.addCleanup(shutil.rmtree, base)
        target = base / "managed.txt"
        target.write_bytes(b"same")
        target.chmod(0o640)

        with Tree(base) as tree:
            tree.write("managed.txt", b"same")

        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)


@skip_unless_source_repo(
    "historical upgrade fixtures are intentionally excluded from portable releases",
    ".agentic/tests/fixtures/upgrades/versions.json")
class UpgradeJournalCrashTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.gettempdir()) / ("awf-upgrade-journal-" + uuid.uuid4().hex)
        self.base.mkdir()
        self.addCleanup(self.cleanup_base)
        self.source = self.base / "release-source"
        self.source.mkdir()
        manifest_raw = (ROOT / "MANIFEST.json").read_bytes()
        manifest = json.loads(manifest_raw)
        (self.source / "MANIFEST.json").write_bytes(manifest_raw)
        for relative, expected in manifest["files"].items():
            target = self.source / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = (ROOT / Path(relative)).read_bytes()
            if sha256(raw) != expected:
                completed = subprocess.run(
                    ["git", "show", "HEAD:" + relative], cwd=ROOT,
                    capture_output=True, check=True)
                raw = completed.stdout
            self.assertEqual(sha256(raw), expected)
            target.write_bytes(raw)
        self.pin = sha256(manifest_raw)

    def cleanup_base(self):
        if not self.base.exists():
            return
        for path in self.base.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.base)

    @staticmethod
    def application_tree(root):
        """Exclude installer-private lock/temp artifacts from the project tree."""
        return {path: digest for path, digest in file_tree(root).items()
                if not path.startswith(".agentic-install/")}

    @staticmethod
    def snapshot(source, destination):
        """Capture the durable crash state, excluding an uncommitted temp inode."""
        def ignore(directory, names):
            ignored = [name for name in names
                       if name.startswith(".awf-") and name.endswith(".tmp")]
            if Path(directory).name == ".agentic-install" and "lock" in names:
                ignored.append("lock")
            return ignored
        shutil.copytree(source, destination, ignore=ignore, copy_function=shutil.copyfile)
        # copyfile deliberately leaves metadata unspecified. Apply the captured
        # modes explicitly so recovery sees the exact durable state on every
        # supported Python/platform combination.
        for original in source.rglob("*"):
            copied = destination / original.relative_to(source)
            if original.is_file() and copied.is_file():
                copied.chmod(stat.S_IMODE(original.stat().st_mode))

    def crash_during_managed_after_update(self, phase):
        destination = self.base / ("destination-" + phase)
        crashed = self.base / ("crashed-" + phase)
        materialize("1.9.3", destination)
        verify_materialized("1.9.3", destination)
        before = self.application_tree(destination)

        original_write = Tree.write
        original_fsync = os.fsync
        original_replace = os.replace
        state = {"relative": None, "journal_writes": 0, "captured": False}

        def capture():
            if state["captured"]:
                return
            self.snapshot(destination, crashed)
            state["captured"] = True
            raise SimulatedCrash(phase)

        def write(tree, relative, data):
            if relative == JOURNAL:
                state["journal_writes"] += 1
            previous = state["relative"]
            state["relative"] = relative
            try:
                if (relative == JOURNAL and state["journal_writes"] == 2
                        and phase == "before_write"):
                    capture()
                return original_write(tree, relative, data)
            finally:
                state["relative"] = previous

        def fsync(fd):
            result = original_fsync(fd)
            if (state["relative"] == JOURNAL and state["journal_writes"] == 2
                    and phase == "after_temp_write"):
                capture()
            return result

        def replace(source, target, *args, **kwargs):
            target_name = os.fspath(target)
            selected = (state["relative"] == JOURNAL and state["journal_writes"] == 2
                        and Path(target_name).name == Path(JOURNAL).name)
            if selected and phase == "before_replace":
                capture()
            result = original_replace(source, target, *args, **kwargs)
            if selected and phase == "after_replace":
                capture()
            return result

        with patch.object(Tree, "write", new=write), \
                patch("agentic.safeio.os.fsync", side_effect=fsync), \
                patch("agentic.safeio.os.replace", side_effect=replace):
            with self.assertRaises(SimulatedCrash):
                install(self.source, destination, self.pin, mode="upgrade",
                        configure=True, discover=False)

        self.assertTrue(state["captured"], "requested journal crash point was not reached")
        self.assertEqual(recover(crashed)["status"], "ROLLED_BACK")
        self.assertEqual(self.application_tree(crashed), before)

        result = install(self.source, crashed, self.pin, mode="upgrade",
                         configure=True, discover=False)
        self.assertEqual(result["status"], "UPGRADED")
        verify_installed(crashed)

    def test_crash_before_managed_after_journal_write_recovers_and_resumes(self):
        self.crash_during_managed_after_update("before_write")

    def test_crash_after_managed_after_temp_write_recovers_and_resumes(self):
        self.crash_during_managed_after_update("after_temp_write")

    def test_crash_before_managed_after_atomic_replace_recovers_and_resumes(self):
        self.crash_during_managed_after_update("before_replace")

    def test_crash_after_managed_after_atomic_replace_recovers_and_resumes(self):
        self.crash_during_managed_after_update("after_replace")


if __name__ == "__main__":
    unittest.main()
