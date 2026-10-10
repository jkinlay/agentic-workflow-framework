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
from agentic.installer import JOURNAL, install, recover, verify_installed  # noqa: E402
from agentic.safeio import Tree  # noqa: E402
from source_only import skip_unless_source_repo  # noqa: E402
from upgrade_fixtures import file_tree, materialize, verify_materialized  # noqa: E402


class SimulatedCrash(BaseException):
    """Escape the installer's Exception rollback like a terminated process."""


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
        shutil.copytree(source, destination, ignore=ignore)

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
