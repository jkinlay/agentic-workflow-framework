"""Real-Git regressions for narrowly trusted other-owner repositories."""
from __future__ import annotations

import io
import json
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
from agentic import child_process
from agentic.heavy_validation_controller import GitCheckoutAttestor


class SafeDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve(strict=True)
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

    def status_filter_fixture(self, late=False, split=False):
        candidate = {"head_sha": self.git_run(self.root, "rev-parse", "HEAD").stdout.decode().strip(),
                     "tree_sha": self.git_run(self.root, "rev-parse", "HEAD^{tree}").stdout.decode().strip()}
        attestor = GitCheckoutAttestor(self.root)
        self.assertEqual(attestor(candidate, str(self.root))["status"], "CLEAN")
        (self.root / ".gitignore").write_text("ignored\n", encoding="utf-8")
        self.git_run(self.root, "add", ".gitignore")
        if split:
            self.git_run(self.root, "update-index", "--split-index")
        # Same length, different mtime: force Git's content conversion path.
        payload = self.root / "payload"
        original = payload.stat()
        payload.write_bytes(b"other\nsecond\n")
        os.utime(payload, ns=(original.st_atime_ns, original.st_mtime_ns + 2000000000))
        (self.root / "untracked").write_text("new", encoding="utf-8")
        (self.root / "ignored").write_text("ignored", encoding="utf-8")
        (self.root / ".git/info/exclude").write_text("local-ignored\n", encoding="utf-8")
        (self.root / "local-ignored").write_text("ignored", encoding="utf-8")
        index_before = (self.root / ".git/index").read_bytes()
        marker = self.base / "filter-marker"
        script = self.base / "clean_filter.py"
        script.write_text("from pathlib import Path\nimport sys\n"
                          "Path(sys.argv[1]).write_text('executed')\n"
                          "sys.stdout.buffer.write(sys.stdin.buffer.read())\n", encoding="utf-8")
        command = f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}" "{marker.as_posix()}"'

        def install():
            self.git_run(self.root, "config", "filter.hostile.clean", command)

        if not late:
            install()
        outputs = []

        def child(argv, **kwargs):
            if "status" in argv and late:
                install()
            result = self.other_owner_child(argv, **kwargs)
            if "status" in argv:
                outputs.append(result.stdout)
            return result

        with mock.patch.object(isolation.subprocess, "run", side_effect=child):
            with self.assertRaisesRegex(ValidationError, "checkout is dirty"):
                attestor(candidate, str(self.root))
        self.assertEqual(len(outputs), 1, "default attestor must reach status")
        self.assertFalse(marker.exists(), "configured clean filter executed during attestation")
        self.assertIn(b" M payload\n", outputs[0])
        self.assertIn(b"A  .gitignore\n", outputs[0])
        self.assertIn(b"?? untracked\n", outputs[0])
        self.assertIn(b"!! ignored\n", outputs[0])
        self.assertIn(b"!! local-ignored\n", outputs[0])
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

    def test_default_attestor_status_never_executes_clean_filter(self):
        self.status_filter_fixture()

    def test_status_clean_filter_added_after_discovery_never_executes(self):
        self.status_filter_fixture(late=True)

    def test_status_preserves_split_index(self):
        self.status_filter_fixture(split=True)

    def test_executable_index_with_filemode_false_is_clean(self):
        self.git_run(self.root, "update-index", "--chmod=+x", "payload")
        self.git_run(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "executable fixture")
        self.assertTrue(self.git_run(self.root, "ls-files", "--stage", "payload").stdout.startswith(b"100755 "))
        (self.root / "payload").chmod(0o644)
        self.git_run(self.root, "config", "core.filemode", "false")
        self.assertEqual(self.git_run(self.root, "status", "--porcelain=v1").stdout, b"")
        candidate = {"head_sha": self.git_run(self.root, "rev-parse", "HEAD").stdout.decode().strip(),
                     "tree_sha": self.git_run(self.root, "rev-parse", "HEAD^{tree}").stdout.decode().strip()}
        index_before = (self.root / ".git/index").read_bytes()
        with mock.patch.object(isolation.subprocess, "run", side_effect=self.other_owner_child):
            self.assertEqual(self.read(["status", "--porcelain=v1"]), b"")
            self.assertEqual(GitCheckoutAttestor(self.root)(candidate, str(self.root))["status"], "CLEAN")
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
        (self.root / "staged").write_bytes(b"new\n")
        self.git_run(self.root, "add", "staged")
        self.assertIn(b"A  staged\n", self.read(["status", "--porcelain=v1"]))
        with self.assertRaisesRegex(ValidationError, "checkout is dirty"):
            GitCheckoutAttestor(self.root)(candidate, str(self.root))

    def test_private_status_preserves_effective_filemode_boolean_only(self):
        for value, expected in (("false", "false"), ("true", "true"), (None, "true")):
            with self.subTest(value=value):
                if value is None:
                    self.git_run(self.root, "config", "--unset", "core.filemode")
                else:
                    self.git_run(self.root, "config", "core.filemode", value)
                seen = []

                def child(command, **kwargs):
                    if "status" in command:
                        metadata = Path(next(arg.split("=", 1)[1] for arg in command
                                             if arg.startswith("--git-dir=")))
                        seen.append((metadata / "config").read_text(encoding="ascii"))
                    return self.other_owner_child(command, **kwargs)

                with mock.patch.object(isolation.subprocess, "run", side_effect=child):
                    self.read(["status", "--porcelain=v1"])
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0], "[core]\n\tbare = false\n\tfilemode = " + expected
                                 + "\n\trepositoryformatversion = 0\n")

    def test_invalid_filemode_policy_refuses_private_status(self):
        self.git_run(self.root, "config", "core.filemode", "invalid")
        with self.assertRaisesRegex(ValidationError, "filemode policy is unavailable"):
            self.read(["status", "--porcelain=v1"])

    def strip_controls_configuration(self, names):
        config = self.base / "environment-policy.json"
        config.write_text(json.dumps({"execution": {"child_env_strip_extra": list(names)}}), encoding="utf-8")
        return mock.patch.object(child_process, "_DEFAULT_CONFIG", config)

    def test_configured_exclusions_cannot_remove_mandatory_git_controls(self):
        expected = isolation.isolated_git_environment()
        mandatory = {key: value for key, value in expected.items()
                     if key.startswith("GIT_") or key in {"LANG", "LC_ALL"}}
        calls = []

        def child(command, **kwargs):
            calls.append(command)
            for key, value in mandatory.items():
                self.assertEqual(kwargs["env"].get(key), value, key)
            self.assertNotIn("TEMP", kwargs["env"])
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            return self.other_owner_child(command, **kwargs)

        with self.strip_controls_configuration([*mandatory, "GIT_OBJECT_DIRECTORY", "TEMP"]), mock.patch.dict(
                os.environ, {"TEMP": self.base.as_posix(), "OPENAI_API_KEY": "fixture"}):
            environment = isolation.isolated_git_environment()
            for key, value in mandatory.items():
                self.assertEqual(environment.get(key), value, key)
            self.assertNotIn("TEMP", environment)
            with mock.patch.object(isolation.subprocess, "run", side_effect=child):
                self.assertEqual(self.read(["cat-file", "blob", "HEAD:payload"]), b"first\nsecond\n")
                self.assertTrue(self.read(["archive", "--format=tar", "HEAD"]))
                self.assertEqual(self.read(["status", "--porcelain=v1"]), b"")
        self.assertTrue(any("rev-parse" in command for command in calls))
        self.assertTrue(any("status" in command for command in calls))
        self.assertTrue(any("archive" in command for command in calls))

    def test_configured_exclusions_cannot_enable_promisor_remote_helper(self):
        with self.strip_controls_configuration(["GIT_ALLOW_PROTOCOL", "GIT_NO_LAZY_FETCH"]):
            self.test_missing_promisor_blob_never_executes_other_owner_remote_helper()

    def late_filter_archive(self, route):
        baseline = self.read(["archive", "--format=tar", "HEAD"])
        marker = self.base / "executed"
        installed = False

        def child(command, **kwargs):
            nonlocal installed
            # The source has no filter configuration during discovery. Install
            # a new driver at the final archive boundary, after all discovery.
            if "archive" in command and not installed:
                installed = True
                self.hostile_config()
                other = "smudge" if route == "process" else "process"
                self.git_run(self.root, "config", "--unset", f"filter.hostile.{other}")
            return self.other_owner_child(command, **kwargs)

        archived = None
        with mock.patch.object(isolation.subprocess, "run", side_effect=child):
            try:
                archived = self.read(["archive", "--format=tar", "HEAD"])
            except ValidationError:
                pass  # Refusal cannot undo a helper that has already executed.
        self.assertTrue(installed, "archive execution boundary was not exercised")
        self.assertFalse(marker.exists(), f"late {route} filter executed")
        if archived is not None:
            self.assertEqual(archived, baseline)
            with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
                self.assertEqual(archive.extractfile("payload").read(), b"first\nsecond\n")
                self.assertEqual(archive.getmember("payload").mode, 0o664)

    def test_process_filter_introduced_after_discovery_never_executes(self):
        self.late_filter_archive("process")

    def test_smudge_filter_introduced_after_discovery_never_executes(self):
        self.late_filter_archive("smudge")

    def test_missing_promisor_blob_never_executes_other_owner_remote_helper(self):
        marker = self.hostile_config()
        script = self.base / "hostile.py"
        # remote-ext splits arguments itself (not shell quoting); percent-space
        # and percent-percent preserve portable paths as literal arguments.
        helper = "ext::" + " ".join(
            path.as_posix().replace("%", "%%").replace(" ", "% ")
            for path in (Path(sys.executable), script, marker))
        self.git_run(self.root, "config", "extensions.partialClone", "origin")
        self.git_run(self.root, "config", "remote.origin.promisor", "true")
        self.git_run(self.root, "config", "remote.origin.url", helper)
        self.git_run(self.root, "config", "protocol.ext.allow", "always")
        missing = "1234567890123456789012345678901234567890"
        # The protocol barrier must still prevent execution on Git versions
        # that do not implement GIT_NO_LAZY_FETCH. Remove it in the test only.
        for legacy_git in (False, True):
            marker.unlink(missing_ok=True)
            def child(command, **kwargs):
                if legacy_git:
                    kwargs["env"] = dict(kwargs["env"])
                    kwargs["env"].pop("GIT_NO_LAZY_FETCH", None)
                return self.other_owner_child(command, **kwargs)

            with self.subTest(legacy_git=legacy_git), mock.patch.object(
                    isolation.subprocess, "run", side_effect=child):
                with self.assertRaises(ValidationError):
                    self.read(["cat-file", "blob", missing])
                self.assertFalse(marker.exists(), "promisor remote helper executed")

    def test_remote_execution_controls_are_fixed_not_inherited(self):
        with mock.patch.dict(os.environ, {"GIT_NO_LAZY_FETCH": "0",
                                          "GIT_ALLOW_PROTOCOL": "ext:file:https"}):
            environment = isolation.isolated_git_environment()
        self.assertEqual(environment.get("GIT_NO_LAZY_FETCH"), "1")
        self.assertEqual(environment.get("GIT_ALLOW_PROTOCOL"), "")

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


class WindowsAliasFixtureTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows junction regression")
    def test_setup_resolves_aliased_temporary_directory_for_attestor(self):
        import _winapi

        temporary_directory = tempfile.TemporaryDirectory
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve(strict=True)
        target = base / "target"
        target.mkdir()
        alias = base / "alias"
        try:
            _winapi.CreateJunction(str(target), str(alias))
        except OSError as error:
            self.skipTest(f"Windows directory junction unavailable: {error}")
        self.addCleanup(alias.rmdir)
        self.assertNotEqual(alias, alias.resolve(strict=True))

        fixture = SafeDirectoryTests("test_executable_index_with_filemode_false_is_clean")
        self.addCleanup(fixture.doCleanups)
        with mock.patch.object(tempfile, "TemporaryDirectory", side_effect=
                               lambda: temporary_directory(dir=alias.as_posix())):
            fixture.setUp()
        lexical_root = Path(fixture.temporary.name) / "repo"
        with self.assertRaisesRegex(ValidationError, "execution checkout uses a symlink or reparse alias"):
            GitCheckoutAttestor(lexical_root)
        candidate = {
            "head_sha": fixture.git_run(fixture.root, "rev-parse", "HEAD").stdout.decode().strip(),
            "tree_sha": fixture.git_run(fixture.root, "rev-parse", "HEAD^{tree}").stdout.decode().strip(),
        }
        self.assertEqual(GitCheckoutAttestor(fixture.root)(candidate, str(fixture.root))["status"],
                         "CLEAN")
        self.assertEqual(fixture.root, fixture.root.resolve(strict=True))


if __name__ == "__main__":
    unittest.main()
