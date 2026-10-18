"""Real-Git regressions for narrowly trusted other-owner repositories."""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".agentic/lib"))

from agentic import ValidationError
from agentic import git_isolation as isolation


class SafeDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repo"
        self.git = shutil.which("git")
        self.assertIsNotNone(self.git, "native Git is required, not a skipped test")
        self.native_run = subprocess.run
        self.make_repo(self.root)

    def git_run(self, root, *args, other_owner=False):
        environment = isolation.isolated_git_environment()
        if other_owner:
            environment["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        return self.native_run(
            [self.git, "-c", f"safe.directory={root.resolve().as_posix()}",
             "-C", str(root), *args], env=environment, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def make_repo(self, root):
        root.mkdir()
        self.git_run(root, "init", "--quiet")
        (root / "payload").write_bytes(b"first\nsecond\n")
        (root / ".gitattributes").write_text("payload filter=hostile text\n", encoding="utf-8")
        self.git_run(root, "-c", "core.autocrlf=false", "add", ".")
        self.git_run(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "fixture")

    def read(self, args, *, root=None, maximum=1024 * 1024):
        return isolation.run_isolated_git(
            root or self.root, args, maximum=maximum, timeout=15, label="fixture read")

    def other_owner_child(self, command, **kwargs):
        environment = dict(kwargs["env"])
        environment["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        kwargs["env"] = environment
        return self.native_run(command, **kwargs)

    def test_other_owner_is_readable_with_exact_resolved_command_scope_trust(self):
        # First prove that this Git really enforces the synthetic ownership check.
        environment = isolation.isolated_git_environment()
        environment["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        refused = self.native_run([self.git, "-C", str(self.root), "cat-file", "blob", "HEAD:payload"],
                                  env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(b"dubious ownership", refused.stderr)
        alias = self.root / ".." / "repo"
        with mock.patch.object(isolation.subprocess, "run", side_effect=self.other_owner_child) as child:
            self.assertEqual(self.read(["cat-file", "blob", "HEAD:payload"], root=alias), b"first\nsecond\n")
        command = child.call_args.args[0]
        self.assertEqual([value for value in command if value.startswith("safe.directory=")],
                         [f"safe.directory={self.root.resolve().as_posix()}"])
        self.assertEqual(command[command.index("safe.directory=" + self.root.resolve().as_posix()) - 1], "-c")
        self.assertEqual(child.call_args.kwargs["env"]["GIT_CONFIG_COUNT"], "0")

    def test_different_sibling_prefix_and_parent_paths_remain_untrusted(self):
        targets = [self.base / "sibling", self.base / "repo-prefix"]
        for target in targets:
            self.make_repo(target)
        parent_repo = self.base / "parent"
        self.make_repo(parent_repo)
        nested = parent_repo / "nested"
        self.make_repo(nested)
        for trusted, target in [(self.root, item) for item in targets] + [(nested, parent_repo)]:
            with self.subTest(target=target.name), mock.patch.object(
                    isolation.subprocess, "run", side_effect=self.other_owner_child):
                # A successful read proves the pin exists before probing its boundary.
                self.assertEqual(self.read(["cat-file", "blob", "HEAD:payload"], root=trusted), b"first\nsecond\n")
                with self.assertRaises(ValidationError):
                    self.read(["-C", str(target), "cat-file", "blob", "HEAD:payload"], root=trusted)

    def hostile_config(self, driver="hostile"):
        marker = self.base / "executed"
        script = self.base / "hostile.py"
        script.write_text("from pathlib import Path\nimport sys\n"
                          "Path(sys.argv[1]).write_text('executed')\n"
                          "sys.stdout.buffer.write(b'HOSTILE')\n", encoding="utf-8")
        command = f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}" "{marker.as_posix()}"'
        with (self.root / ".git/config").open("a", encoding="utf-8") as config:
            # Escape as a Git-config quoted string, independent of the host shell.
            escaped = command.replace("\\", "\\\\").replace('"', '\\"')
            config.write('\n[tar]\n\tumask = 0777\n[core]\n\tautocrlf = true\n\teol = crlf\n'
                         f'[filter "{driver}"]\n\tsmudge = "{escaped}"\n\tprocess = "{escaped}"\n'
                         f'[tar "tar"]\n\tcommand = "{escaped}"\n'
                         f'[tar "hostile"]\n\tcommand = "{escaped}"\n')
        return marker

    def test_hostile_local_config_preserves_archive_blob_bytes_and_modes(self):
        baseline = self.read(["archive", "--format=tar", "HEAD"])
        marker = self.hostile_config()
        with mock.patch.object(isolation.subprocess, "run", side_effect=self.other_owner_child):
            self.assertEqual(self.read(["cat-file", "blob", "HEAD:payload"]), b"first\nsecond\n")
            archived = self.read(["archive", "--format=tar", "HEAD"])
        self.assertEqual(archived, baseline)
        with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
            self.assertEqual(archive.extractfile("payload").read(), b"first\nsecond\n")
            self.assertEqual(archive.getmember("payload").mode, 0o664)
        self.assertFalse(marker.exists())

    def test_tar_umask_is_pinned_even_without_ownership_failure(self):
        baseline = self.read(["archive", "--format=tar", "HEAD"])
        self.hostile_config()
        self.assertEqual(self.read(["archive", "--format=tar", "HEAD"]), baseline)

    def test_conversion_pins_are_command_scoped(self):
        self.hostile_config()
        for key, expected in (("tar.umask", b"0002\n"), ("core.autocrlf", b"false\n"),
                              ("core.eol", b"lf\n")):
            with self.subTest(key=key):
                self.assertEqual(self.read(["config", "--get", key]), expected)

    def test_custom_archive_format_refuses_before_hostile_command_executes(self):
        marker = self.hostile_config()
        with self.assertRaises(ValidationError):
            self.read(["archive", "--format=hostile", "HEAD"])
        self.assertFalse(marker.exists())

    def test_wildcard_shaped_resolved_root_is_refused_before_git_runs(self):
        root = mock.MagicMock(spec=Path)
        root.resolve.return_value = Path("/fixture/*")
        with mock.patch.object(isolation.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, b"", b"")) as child:
            with self.assertRaisesRegex(ValidationError, "wildcard"):
                self.read(["cat-file", "blob", "HEAD:payload"], root=root)
        child.assert_not_called()

    def test_unpinnable_filter_name_refuses_before_command_executes(self):
        (self.root / ".gitattributes").write_text("payload filter=hostile=alias text\n", encoding="utf-8")
        self.git_run(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgSign=false", "commit", "--quiet", "-am", "filter fixture")
        marker = self.hostile_config(driver="hostile=alias")
        with self.assertRaisesRegex(ValidationError, "cannot be pinned"):
            self.read(["archive", "--format=tar", "HEAD"])
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
