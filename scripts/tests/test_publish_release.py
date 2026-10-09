"""Release publication, reproducibility, clean-source, and local-remote proofs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("awf_publish_release", ROOT / "scripts/publish_release.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def command(args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout.strip()


class PublishReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="awf-publish-release-")
        cls.base = Path(cls.temporary.name)
        cls.repository = cls.base / "source"
        shutil.copytree(ROOT, cls.repository,
                        ignore=shutil.ignore_patterns(".git", ".tmp", ".tmp-tests", "__pycache__", "*.pyc"))
        command([sys.executable, "-B", str(cls.repository / "scripts/build_release.py"), "--manifest-only"], cls.repository)
        cls.git_env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_AUTHOR_DATE": "2026-09-24T00:00:00+00:00",
                       "GIT_COMMITTER_DATE": "2026-09-24T00:00:00+00:00"}
        command(["git", "init", "-b", "main"], cls.repository, cls.git_env)
        command(["git", "config", "user.name", "Synthetic Release Test"], cls.repository, cls.git_env)
        command(["git", "config", "user.email", "release@example.invalid"], cls.repository, cls.git_env)
        command(["git", "config", "core.autocrlf", "false"], cls.repository, cls.git_env)
        command(["git", "config", "core.filemode", "false"], cls.repository, cls.git_env)
        command(["git", "add", "."], cls.repository, cls.git_env)
        # Mode fidelity must come from Git, not content: make a non-shebang
        # executable while leaving Python shebang files non-executable.
        command(["git", "update-index", "--chmod=+x", "LICENSE"], cls.repository, cls.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic release fixture"],
                cls.repository, cls.git_env)
        cls.commit = command(["git", "rev-parse", "HEAD"], cls.repository, cls.git_env)
        cls.epoch = int(command(["git", "show", "-s", "--format=%ct", cls.commit], cls.repository, cls.git_env))
        cls.windows_check = cls.base / "windows-check.json"
        cls.windows_check.write_text(json.dumps({
            "format": "awf-clean-windows-portable-check-1", "status": "PASS", "version": "1.9.4",
            "source_commit": cls.commit,
            "checks": {"portable_build": "PASS", "host_skill_install": "PASS", "host_skill_verify": "PASS"},
        }, sort_keys=True), encoding="utf-8")
        cls.windows_pin = hashlib.sha256(cls.windows_check.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @staticmethod
    def fake_validation(source, output, env):
        return {"self_test": {"run": 1, "failures": 0, "errors": 0},
                "release_hygiene": {"status": "PASS"},
                "portable_suites": {"portable_core": 1, "portable_host": 1}}

    def build(self, name):
        source = self.base / (name + "-source")
        modes = self.base / (name + "-git-modes.json")
        temporary = self.base / (name + "-tmp")
        temporary.mkdir()
        publisher.materialize_commit(self.repository, self.commit, source, mode_manifest=modes)
        previous = {key: os.environ.get(key) for key in ("TMP", "TEMP")}
        os.environ.update(TMP=str(temporary), TEMP=str(temporary))
        try:
            return publisher.build_assets(source, self.base / name, self.epoch, modes)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_ac10_two_builds_are_byte_identical_and_metadata_is_normalized(self):
        first, first_proof = self.build("first-build")
        second, second_proof = self.build("second-build")
        first_hashes = {path.name: publisher.sha256(path) for path in first}
        second_hashes = {path.name: publisher.sha256(path) for path in second}
        self.assertEqual(first_hashes, second_hashes)
        print("AC10_ASSET_SHA256=" + json.dumps(first_hashes, sort_keys=True))
        self.assertEqual(first_proof["manifest_sha256"], second_proof["manifest_sha256"])
        metadata = None
        for path in first:
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                current = [(entry.date_time, entry.create_system, entry.external_attr,
                            entry.compress_type, entry.extra, entry.comment) for entry in archive.infolist()]
                self.assertTrue(current)
                self.assertTrue(all(item[3] == zipfile.ZIP_STORED for item in current))
                expected_modes = [entry.external_attr >> 16 for entry in archive.infolist()]
                self.assertTrue(all(mode in {0o100644, 0o100755} for mode in expected_modes))
                self.assertTrue(all(item[1] == 3 for item in current))
                self.assertIn(0o100644, expected_modes)
                self.assertTrue(all(item[4:] == (b"", b"") for item in current))
                if metadata is None:
                    metadata = current[0][0]
                self.assertTrue(all(item[0] == metadata for item in current))
                if path.name.startswith("agentic-workflow-template-v"):
                    raw_modes = {entry.path: int(entry.mode, 8)
                                 for entry in publisher._tree_entries(self.repository, self.commit)}
                    prefix = archive.infolist()[0].filename.split("/", 1)[0] + "/"
                    observed = {entry.filename.removeprefix(prefix): entry.external_attr >> 16
                                for entry in archive.infolist()}
                    self.assertEqual(observed, {name: raw_modes[name] for name in observed})
                    self.assertEqual(observed["LICENSE"], 0o100755)
                    self.assertEqual(observed["scripts/build_release.py"], 0o100644)
                if path.name.startswith("AWF-v"):
                    launcher = next(entry for entry in archive.infolist()
                                    if entry.filename.endswith("/install_awf.py"))
                    self.assertTrue(archive.read(launcher).startswith(b"#!"))
                    self.assertEqual(launcher.external_attr >> 16, 0o100644)

    def test_dry_run_records_hashes_and_makes_no_remote_change(self):
        result = publisher.publish(self.repository, self.commit, self.base / "dry-run-output",
                                   self.windows_check, self.windows_pin, dry_run=True,
                                   validation_runner=self.fake_validation)
        self.assertEqual("DRY_RUN", result["status"])
        for name, digest in result["record"]["assets"].items():
            self.assertIn(name, result["tag_message"])
            self.assertIn(digest, result["tag_message"])
            self.assertIn(digest, result["release_body"])
        self.assertFalse(result["remote_changes"])
        self.assertEqual("", command(["git", "tag", "--list", "v1.9.4"], self.repository))

    def test_tag_push_uses_only_the_explicit_gh_credential_helper(self):
        isolated = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        gh = "/tools/with spaces/gh"
        origin_url = "https://embedded-secret@github.com/jkinlay/awf-fixture.git"
        with patch.object(publisher, "isolated_git_env", return_value=isolated) as isolated_git_env, \
                patch.object(publisher, "_repository_http_extra_header_keys", return_value=[]), \
                patch.object(publisher, "run", return_value=SimpleNamespace(stdout="")) as run:
            publisher.push_release_tag(self.repository, "v1.9.4", gh=gh, origin_url=origin_url)

        isolated_git_env.assert_called_once_with()
        run.assert_called_once_with(
            ["git", *publisher.RAW_GIT_ARGUMENTS,
             "-c", "credential.helper=",
             "-c", "remote.origin.pushurl=",
             "-c", "remote.origin.pushurl=https://github.com/jkinlay/awf-fixture.git",
             "-c", "http.extraHeader=",
             "-c", "http.https://github.com/jkinlay/awf-fixture.git.extraHeader=",
             "-c", "credential.https://github.com/jkinlay/awf-fixture.git.helper=",
             "-c", ("credential.https://github.com/jkinlay/awf-fixture.git.helper="
                    f"!{publisher.shlex.quote(gh)} auth git-credential"),
             "push", "origin", "refs/tags/v1.9.4"],
            cwd=self.repository, env=isolated, text=True, input_data=None)
        self.assertNotIn("embedded-secret", repr(run.call_args))

    def test_non_push_git_commands_remain_isolated(self):
        isolated = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        with patch.object(publisher, "isolated_git_env", return_value=isolated), \
                patch.object(publisher, "_repository_http_extra_header_keys", return_value=[]), \
                patch.object(publisher, "run", return_value=SimpleNamespace(stdout="")) as run:
            publisher.git_run(self.repository, "status", "--porcelain=v1")
            publisher.push_release_tag(
                self.repository, "v1.9.4", gh="gh",
                origin_url="https://github.com/jkinlay/awf-fixture.git")

        status_call, push_call = run.call_args_list
        self.assertEqual(isolated, status_call.kwargs["env"])
        self.assertEqual(isolated, push_call.kwargs["env"])
        self.assertNotIn("credential.helper=", status_call.args[0])
        self.assertEqual(
            ["-c", "credential.helper=",
             "-c", "remote.origin.pushurl=",
             "-c", "remote.origin.pushurl=https://github.com/jkinlay/awf-fixture.git",
             "-c", "http.extraHeader=",
             "-c", "http.https://github.com/jkinlay/awf-fixture.git.extraHeader=",
             "-c", "credential.https://github.com/jkinlay/awf-fixture.git.helper=",
             "-c", ("credential.https://github.com/jkinlay/awf-fixture.git.helper="
                    "!gh auth git-credential")],
            push_call.args[0][4:18])

    def test_push_and_recovery_clear_path_specific_local_http_headers(self):
        scope = "https://github.com/jkinlay/awf-fixture.git"
        request_url = scope + "/info/refs"
        header_keys = [
            f"http.{scope}.extraHeader",
            f"http.{request_url}.extraHeader",
        ]
        for index, key in enumerate(header_keys):
            command(["git", "config", key, f"Authorization: Bearer LOCAL_SECRET_{index}"],
                    self.repository, self.git_env)
        self.addCleanup(
            lambda: [subprocess.run(["git", "config", "--unset-all", key], cwd=self.repository,
                                    env=self.git_env, capture_output=True) for key in header_keys])

        local_header_keys = publisher._repository_http_extra_header_keys(self.repository)
        push_arguments, _scope = publisher._push_credential_arguments(
            scope, "gh", local_header_keys=local_header_keys)

        def matching_header(arguments):
            result = subprocess.run(
                ["git", *publisher.RAW_GIT_ARGUMENTS, *arguments,
                 "config", "--get-urlmatch", "http.extraHeader", request_url],
                cwd=self.repository, env=publisher.isolated_git_env(), capture_output=True,
                text=True, timeout=300)
            self.assertIn(result.returncode, {0, 1}, result.stdout + result.stderr)
            return result.stdout.strip()

        self.assertEqual("", matching_header(push_arguments))

        real_git_run = publisher.git_run

        def fail_push(repository, *arguments, **kwargs):
            if "push" in arguments:
                raise publisher.ReleaseError("failed")
            return real_git_run(repository, *arguments, **kwargs)

        with patch.object(publisher, "git_run", side_effect=fail_push):
            with self.assertRaises(publisher.ReleaseError) as raised:
                publisher.push_release_tag(
                    self.repository, "v1.9.4", gh="gh", origin_url=scope)
        recovery = str(raised.exception).rsplit("\n", 1)[1]
        recovery_arguments = publisher.shlex.split(recovery)[1:]
        self.assertEqual(["push", "origin", "refs/tags/v1.9.4"], recovery_arguments[-3:])
        self.assertEqual("", matching_header(recovery_arguments[:-3]))

    def test_included_local_path_header_is_inventoried_and_cannot_reach_push(self):
        scope = "https://github.com/o/r.git"
        request_url = scope + "/info/refs"
        header_key = f"http.{request_url}.extraHeader"
        included_config = self.base / "included-local-headers.gitconfig"
        included_config.write_text(
            f"[http \"{request_url}\"]\n\textraHeader = Authorization: Bearer INCLUDED_SECRET\n",
            encoding="utf-8")
        command(["git", "config", "--local", "include.path", str(included_config)],
                self.repository, self.git_env)
        self.addCleanup(
            subprocess.run, ["git", "config", "--local", "--unset-all", "include.path"],
            cwd=self.repository, env=self.git_env, capture_output=True)

        local_header_keys = publisher._repository_http_extra_header_keys(self.repository)
        self.assertIn(header_key.casefold(), {key.casefold() for key in local_header_keys})
        real_run = publisher.run
        inspected_push = []

        def inspect_push(command_line, *, cwd, env=None, text=True, input_data=None,
                         failure_message=None):
            if command_line[0] == "git" and "push" in command_line:
                push_index = command_line.index("push")
                lookup = real_run(
                    command_line[:push_index]
                    + ["config", "--get-urlmatch", "http.extraHeader", request_url],
                    cwd=cwd, env=env, text=True)
                self.assertEqual("", lookup.stdout.strip())
                self.assertNotIn("INCLUDED_SECRET", lookup.stdout + lookup.stderr)
                inspected_push.append(command_line)
                return SimpleNamespace(stdout="")
            return real_run(command_line, cwd=cwd, env=env, text=text,
                            input_data=input_data, failure_message=failure_message)

        with patch.object(publisher, "run", side_effect=inspect_push):
            publisher.push_release_tag(
                self.repository, "v1.9.4", gh="gh", origin_url=scope)
        self.assertEqual(1, len(inspected_push))

    def test_rejected_local_header_is_checked_before_local_tag_creation(self):
        remote = "https://github.com/o/r.git"
        rejected_key = "http.https://user@github.com/o/r.git.extraHeader"
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repository,
                       env=self.git_env, capture_output=True)
        subprocess.run(["git", "tag", "-d", "v1.9.4"], cwd=self.repository,
                       env=self.git_env, capture_output=True)
        command(["git", "remote", "add", "origin", remote], self.repository, self.git_env)
        command(["git", "config", "--local", rejected_key,
                 "Authorization: Bearer REJECTED_SECRET"], self.repository, self.git_env)
        self.addCleanup(subprocess.run, ["git", "config", "--local", "--unset-all", rejected_key],
                        cwd=self.repository, env=self.git_env, capture_output=True)
        self.addCleanup(subprocess.run, ["git", "remote", "remove", "origin"],
                        cwd=self.repository, env=self.git_env, capture_output=True)
        self.addCleanup(subprocess.run, ["git", "tag", "-d", "v1.9.4"],
                        cwd=self.repository, env=self.git_env, capture_output=True)

        with self.assertRaises(publisher.ReleaseError) as raised:
            publisher.publish(
                self.repository, self.commit, self.base / "rejected-header-preflight",
                self.windows_check, self.windows_pin,
                validation_runner=self.fake_validation, gh="gh")

        self.assertEqual("", command(["git", "tag", "--list", "v1.9.4"],
                                     self.repository, self.git_env))
        message = str(raised.exception)
        self.assertIn("remove every repository-local `http.*.extraHeader` entry", message)
        self.assertIn("retry the release", message)
        self.assertNotIn("REJECTED_SECRET", message)

    def test_tag_push_failure_explains_that_release_creation_does_not_follow(self):
        secret = "ghs_must_not_reach_release_output"
        with patch.object(publisher, "_repository_http_extra_header_keys", return_value=[]), \
                patch.object(publisher, "git_run",
                          side_effect=publisher.ReleaseError("push failed: " + secret)):
            with self.assertRaises(publisher.ReleaseError) as raised:
                publisher.push_release_tag(
                    self.repository, "v1.9.4", gh="gh",
                    origin_url="https://github.com/jkinlay/awf-fixture.git")
        message = str(raised.exception)
        self.assertEqual(
            "Release tag push failed for refs/tags/v1.9.4. The local annotated tag remains at "
            "refs/tags/v1.9.4; the remote tag status is unknown and no GitHub release was created. "
            "Run `gh auth status`; after fixing authentication, retry exactly:\n"
            "git -c credential.helper= "
            "-c remote.origin.pushurl= "
            "-c remote.origin.pushurl=https://github.com/jkinlay/awf-fixture.git "
            "-c http.extraHeader= "
            "-c http.https://github.com/jkinlay/awf-fixture.git.extraHeader= "
            "-c credential.https://github.com/jkinlay/awf-fixture.git.helper= "
            "-c 'credential.https://github.com/jkinlay/awf-fixture.git.helper="
            "!gh auth git-credential' "
            "push origin refs/tags/v1.9.4",
            message)
        self.assertNotIn(secret, message)

    def test_tag_push_timeout_uses_the_same_redacted_recovery_error(self):
        secret = "ghs_timeout_capture_must_not_reach_release_output"
        timeout = subprocess.TimeoutExpired(
            ["git", "push"], 3600, output="transport " + secret, stderr="credential " + secret)
        with patch.object(publisher, "_repository_http_extra_header_keys", return_value=[]), \
                patch.object(publisher, "git_run", side_effect=timeout):
            with self.assertRaises(publisher.ReleaseError) as raised:
                publisher.push_release_tag(
                    self.repository, "v1.9.4", gh="gh",
                    origin_url="https://github.com/jkinlay/awf-fixture.git")
        message = str(raised.exception)
        self.assertIn("local annotated tag remains at refs/tags/v1.9.4", message)
        self.assertIn("remote tag status is unknown", message)
        self.assertIn("no GitHub release was created", message)
        self.assertIn("http.https://github.com/jkinlay/awf-fixture.git.extraHeader=", message)
        self.assertIn("credential.https://github.com/jkinlay/awf-fixture.git.helper=", message)
        self.assertNotIn(secret, message)

    def test_awf26_failed_publish_scopes_helper_preserves_config_and_skips_release(self):
        remote = "https://embedded-secret@github.com/jkinlay/awf-fixture.git"
        safe_remote = "https://github.com/jkinlay/awf-fixture.git"
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repository,
                       capture_output=True)
        command(["git", "remote", "add", "origin", remote], self.repository, self.git_env)
        repository_helper_log = self.base / "awf26-repository-helper-called.txt"
        repository_helper = self.make_fake_repository_helper(
            self.base / "awf26-repository-helper", repository_helper_log)
        scoped_keys = [f"credential.{remote}.helper", f"credential.{safe_remote}.helper"]
        for scoped_key in scoped_keys:
            command(["git", "config", scoped_key,
                     f"!{publisher.shlex.quote(str(repository_helper))}"], self.repository, self.git_env)
            self.addCleanup(subprocess.run, ["git", "config", "--unset-all", scoped_key],
                            cwd=self.repository, capture_output=True)
        header_secret = "SYNTHETIC_LOCAL_HEADER"
        header_key = f"http.{safe_remote}.extraHeader"
        request_url = safe_remote + "/info/refs"
        request_header_key = f"http.{request_url}.extraHeader"
        for key in (header_key, request_header_key):
            command(["git", "config", key, f"Authorization: Bearer {header_secret}"],
                    self.repository, self.git_env)
            self.addCleanup(subprocess.run, ["git", "config", "--unset-all", key],
                            cwd=self.repository, capture_output=True)
        self.addCleanup(subprocess.run, ["git", "remote", "remove", "origin"],
                        cwd=self.repository, capture_output=True)
        self.addCleanup(subprocess.run, ["git", "tag", "-d", "v1.9.4"],
                        cwd=self.repository, capture_output=True)
        repository_config = self.repository / ".git/config"
        repository_config_before = repository_config.read_bytes()
        ambient_config = self.base / "awf26-ambient-global.gitconfig"
        ambient_config.write_text("[credential]\n\thelper = ambient-fixture\n", encoding="utf-8")
        ambient_config_before = ambient_config.read_bytes()
        fake_bin = self.base / "awf26-fake-gh"
        fake_bin.mkdir()
        fake_gh_store = self.base / "awf26-fake-gh-store"
        fake_gh_store.mkdir()
        fake_gh_log = self.base / "awf26-fake-gh.jsonl"
        fake_gh = str(self.make_fake_gh(fake_bin, fake_gh_store, fake_gh_log))
        helper_secret = "ghs_fake_helper_secret"
        transport_secret = "ghs_fake_push_secret"
        calls = []
        real_run = publisher.run

        def fake_git_and_gh(command_line, *, cwd, env=None, text=True, input_data=None,
                            failure_message=None):
            command_line = [str(item) for item in command_line]
            calls.append((command_line, dict(env) if env is not None else None))
            if command_line[0] == "git" and "push" in command_line:
                push_index = command_line.index("push")
                effective_remote = real_run(
                    command_line[:push_index] + ["remote", "get-url", "--push", "--all", "origin"],
                    cwd=cwd, env=env, text=True)
                self.assertEqual(safe_remote, effective_remote.stdout.strip())
                header_lookup = real_run(
                    command_line[:push_index]
                    + ["config", "--get-urlmatch", "http.extraHeader", request_url],
                    cwd=cwd, env=env, text=True)
                self.assertEqual("", header_lookup.stdout.strip())
                self.assertNotIn(header_secret, header_lookup.stdout)
                credential_lookup = command_line[:push_index] + ["credential", "fill"]
                lookup = real_run(
                    credential_lookup, cwd=cwd, env=env, text=True,
                    input_data="protocol=https\nhost=github.com\npath=jkinlay/awf-fixture.git\n\n")
                self.assertIn("username=awf-fake-gh", lookup.stdout)
                self.assertIn("password=" + helper_secret, lookup.stdout)
                raise publisher.ReleaseError(
                    "fatal: unable to access "
                    f"'https://x-access-token:{transport_secret}@github.com/jkinlay/awf-fixture.git/'")
            if command_line[0] == fake_gh:
                self.fail("gh release create must not run after a failed tag push")
            return real_run(command_line, cwd=cwd, env=env, text=text, input_data=input_data,
                            failure_message=failure_message)

        with patch.object(publisher, "run", side_effect=fake_git_and_gh), \
                patch.dict(os.environ, {
                    "GIT_CONFIG_GLOBAL": str(ambient_config),
                    "AWF_FAKE_GH_LOG": str(fake_gh_log),
                    "AWF_FAKE_GH_STORE": str(fake_gh_store),
                    "AWF_FAKE_GH_PASSWORD": helper_secret,
                }), \
                self.assertRaises(publisher.ReleaseError) as raised:
            publisher.publish(self.repository, self.commit, self.base / "awf26-failed-publish",
                              self.windows_check, self.windows_pin,
                              validation_runner=self.fake_validation, gh=fake_gh)

        git_calls = [(line, env) for line, env in calls if line[0] == "git"]
        push_calls = [(line, env) for line, env in git_calls if "push" in line]
        self.assertEqual(1, len(push_calls))
        push, push_env = push_calls[0]
        helper_key = "credential.https://github.com/jkinlay/awf-fixture.git.helper"
        header_key = "http.https://github.com/jkinlay/awf-fixture.git.extraHeader"
        listed_request_header_key = "http.https://github.com/jkinlay/awf-fixture.git/info/refs.extraheader"
        helper = f"{helper_key}=!{publisher.shlex.quote(fake_gh)} auth git-credential"
        push_index = push.index("push")
        self.assertEqual(
            ["-c", "credential.helper=", "-c", "remote.origin.pushurl=",
             "-c", f"remote.origin.pushurl={safe_remote}",
             "-c", "http.extraHeader=",
             "-c", f"{header_key}=",
             "-c", f"{listed_request_header_key}=",
             "-c", f"{helper_key}=", "-c", helper],
            push[push_index - 16:push_index])
        self.assertEqual(["push", "origin", "refs/tags/v1.9.4"], push[push_index:])
        for line, env in git_calls:
            with self.subTest(command=line):
                self.assertEqual(os.devnull, env["GIT_CONFIG_GLOBAL"])
                self.assertEqual("1", env["GIT_CONFIG_NOSYSTEM"])
                self.assertEqual("0", env["GIT_TERMINAL_PROMPT"])
                if line is not push:
                    self.assertFalse(any("credential.helper" in argument for argument in line))
        self.assertEqual(repository_config_before, repository_config.read_bytes())
        self.assertEqual(ambient_config_before, ambient_config.read_bytes())
        self.assertEqual("v1.9.4", command(["git", "tag", "--list", "v1.9.4"], self.repository))
        message = str(raised.exception)
        self.assertIn("local annotated tag remains at refs/tags/v1.9.4", message)
        self.assertIn("no GitHub release was created", message)
        self.assertIn(
            "git -c credential.helper= "
            "-c remote.origin.pushurl= "
            "-c remote.origin.pushurl=https://github.com/jkinlay/awf-fixture.git "
            "-c http.extraHeader= "
            "-c http.https://github.com/jkinlay/awf-fixture.git.extraHeader= "
            "-c http.https://github.com/jkinlay/awf-fixture.git/info/refs.extraheader= "
            "-c credential.https://github.com/jkinlay/awf-fixture.git.helper= "
            "-c 'credential.https://github.com/jkinlay/awf-fixture.git.helper="
            "!gh auth git-credential' "
            "push origin refs/tags/v1.9.4", message)
        self.assertNotIn(helper_secret, message)
        self.assertNotIn(transport_secret, message)
        self.assertNotIn(header_secret, message)
        self.assertNotIn("embedded-secret", message)
        self.assertNotIn("x-access-token", message)
        self.assertFalse(repository_helper_log.exists())
        credential_calls = [json.loads(line) for line in fake_gh_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([["auth", "git-credential", "get"]], credential_calls)
        self.assertFalse([line for line, _env in calls if line[0] == fake_gh])

    def worktree_paths(self):
        listing = command(["git", "worktree", "list", "--porcelain"], self.repository, self.git_env)
        return [line[len("worktree "):] for line in listing.splitlines() if line.startswith("worktree ")]

    def test_validation_runs_real_git_dependent_tests_in_a_removed_checkout_of_the_commit(self):
        # Regression: validation ran in the Git-less projection, so self-test
        # cases needing HEAD, attributes or ls-tree failed with "not a git
        # repository".  Run real cases of that kind through publish's own path.
        before = self.worktree_paths()
        seen = {}

        def real_git_validation(source, output, env):
            seen["source"] = Path(source)
            seen["head"] = command(["git", "rev-parse", "HEAD"], source)
            seen["status"] = command(["git", "status", "--porcelain", "--untracked-files=all"], source)
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest",
                 "test_upgrade_matrix.UpgradeMatrixTests.test_fixture_blobs_are_stored_byte_exactly_by_git",
                 "test_publication.PublicationScanTests.test_alias_renderer_and_shipped_ignore_rule"],
                cwd=Path(source) / ".agentic/tests", env=env, capture_output=True, text=True, timeout=600)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("Ran 2 tests", result.stderr)
            return self.fake_validation(source, output, env)

        result = publisher.publish(self.repository, self.commit, self.base / "git-validation-output",
                                   self.windows_check, self.windows_pin, dry_run=True,
                                   validation_runner=real_git_validation)
        self.assertEqual("DRY_RUN", result["status"])
        self.assertEqual(self.commit, seen["head"])
        self.assertEqual("", seen["status"])
        self.assertFalse(seen["source"].exists())
        self.assertEqual(before, self.worktree_paths())

        def failing_validation(source, output, env):
            seen["failed_source"] = Path(source)
            raise publisher.ReleaseError("synthetic validation failure")

        with self.assertRaisesRegex(publisher.ReleaseError, "synthetic validation failure"):
            publisher.publish(self.repository, self.commit, self.base / "git-validation-failure-output",
                              self.windows_check, self.windows_pin, dry_run=True,
                              validation_runner=failing_validation)
        self.assertFalse(seen["failed_source"].exists())
        self.assertEqual(before, self.worktree_paths())

    def test_k6_reports_untracked_tracked_and_version_disagreement_together(self):
        readme = self.repository / "README.md"
        original = readme.read_bytes()
        untracked = self.repository / "synthetic-untracked.txt"
        try:
            readme.write_text(readme.read_text(encoding="utf-8").replace("Release 1.9.4", "Release 9.9.9", 1), encoding="utf-8")
            untracked.write_text("fixture", encoding="utf-8")
            report = publisher.source_report(self.repository, self.commit)
            joined = "\n".join(report["problems"])
            self.assertIn("tracked modification", joined)
            self.assertIn("untracked file", joined)
            self.assertIn("version disagreement", joined)
        finally:
            readme.write_bytes(original)
            untracked.unlink()

    def test_k6_reports_every_stale_generated_file(self):
        source = self.base / "stale-generated-source"
        publisher.materialize_commit(self.repository, self.commit, source)
        schema = source / ".agentic/schemas/run-disposition-request.schema.json"
        example = source / ".agentic/examples/project-status.json"
        schema.write_text("{}\n", encoding="utf-8")
        example.write_text("{}\n", encoding="utf-8")
        problems = publisher.generated_file_problems(source)
        self.assertIn("stale generated file: .agentic/schemas/run-disposition-request.schema.json", problems)
        self.assertIn("stale generated file: .agentic/examples/project-status.json", problems)

    def test_g001_replacement_objects_cannot_substitute_release_bytes_or_identity(self):
        repository = self.base / "replacement-object-source"
        repository.mkdir()
        command(["git", "init", "-b", "main"], repository, self.git_env)
        command(["git", "config", "user.name", "Original Publisher"], repository, self.git_env)
        command(["git", "config", "user.email", "original@example.invalid"], repository, self.git_env)
        command(["git", "config", "core.autocrlf", "false"], repository, self.git_env)
        released = repository / "released.txt"
        released.write_bytes(b"reviewed release bytes\n")
        command(["git", "add", "released.txt"], repository, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Reviewed release identity"],
                repository, self.git_env)
        reviewed = command(["git", "rev-parse", "HEAD"], repository, self.git_env)

        command(["git", "config", "user.name", "Replacement Publisher"], repository, self.git_env)
        command(["git", "config", "user.email", "replacement@example.invalid"], repository, self.git_env)
        released.write_bytes(b"substituted release bytes\n")
        command(["git", "add", "released.txt"], repository, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Replacement release identity"],
                repository, self.git_env)
        replacement = command(["git", "rev-parse", "HEAD"], repository, self.git_env)
        command(["git", "replace", reviewed, replacement], repository, self.git_env)
        command(["git", "update-ref", "refs/heads/main", reviewed], repository, self.git_env)

        self.assertEqual("Replacement release identity",
                         command(["git", "show", "-s", "--format=%s", reviewed], repository, self.git_env))
        self.assertEqual("substituted release bytes",
                         command(["git", "show", f"{reviewed}:released.txt"], repository, self.git_env))
        identity = publisher.git(
            repository, "show", "-s", "--format=%H%x00%an%x00%ae%x00%s", reviewed).strip().split("\0")
        self.assertEqual([reviewed, "Original Publisher", "original@example.invalid",
                          "Reviewed release identity"], identity)
        materialized = self.base / "replacement-object-materialized"
        publisher.materialize_commit(repository, reviewed, materialized)
        self.assertEqual(b"reviewed release bytes\n", (materialized / "released.txt").read_bytes())

    def test_g001_inherited_git_redirection_cannot_substitute_repository(self):
        decoy = self.base / "git-env-decoy"
        decoy.mkdir()
        command(["git", "init", "-b", "main"], decoy, self.git_env)
        command(["git", "config", "user.name", "Decoy"], decoy, self.git_env)
        command(["git", "config", "user.email", "decoy@example.invalid"], decoy, self.git_env)
        (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
        command(["git", "add", "."], decoy, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "decoy"], decoy, self.git_env)
        inherited = {
            "GIT_DIR": str(decoy / ".git"), "git_work_tree": str(decoy),
            "GIT_OBJECT_DIRECTORY": str(decoy / ".git/objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / ".git/objects"),
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.useReplaceRefs",
            "GIT_CONFIG_VALUE_0": "true",
        }
        with patch.dict(os.environ, inherited, clear=False):
            self.assertEqual(self.commit, publisher.git(self.repository, "rev-parse", "HEAD").strip())
            self.assertIn("VERSION", {entry.path for entry in publisher._tree_entries(self.repository, self.commit)})

    def test_b001_raced_directory_alias_and_hardlink_fail_before_escape_or_overwrite(self):
        entries = publisher._tree_entries(self.repository, self.commit)
        alias_entry = next(entry for entry in entries if "/" in entry.path)
        top = alias_entry.path.split("/", 1)[0]
        alias_entries = [entry for entry in entries if entry.path == alias_entry.path]
        alias_blobs = publisher._tree_blobs(self.repository, alias_entries)
        destination = self.base / "raced-alias-materialized"
        escape = self.base / "raced-alias-escape"
        escape.mkdir()

        def alias_hook(kind, root, relative):
            if kind != "directory" or relative != top:
                return
            alias = root / top
            if os.name == "nt":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(escape)],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise OSError("exclusive root prevented raced junction creation")
            else:
                os.symlink(escape, alias, target_is_directory=True)

        with patch.object(publisher.safe_materialize, "RACE_HOOK", alias_hook):
            with self.assertRaises(OSError):
                publisher.safe_materialize.materialize(destination, alias_entries, alias_blobs)
        self.assertEqual([], list(escape.iterdir()))

        victim = self.base / "hardlink-victim.txt"
        original = b"victim must not change\n"
        victim.write_bytes(original)
        flat = next(entry for entry in entries if "/" not in entry.path)
        flat_entries = [flat]
        flat_blobs = publisher._tree_blobs(self.repository, flat_entries)
        destination = self.base / "raced-hardlink-materialized"

        def hardlink_hook(kind, root, relative):
            if kind == "file" and relative == flat.path:
                os.link(victim, root / relative)

        with patch.object(publisher.safe_materialize, "RACE_HOOK", hardlink_hook):
            with self.assertRaises(OSError):
                publisher.safe_materialize.materialize(destination, flat_entries, flat_blobs)
        self.assertEqual(original, victim.read_bytes())

    def test_b001_materialize_commit_preserves_concurrent_destination_winner(self):
        destination = self.base / "concurrent-winner-materialized"
        sentinel = destination / "owner-data.txt"
        original_tree_entries = publisher._tree_entries

        def concurrent_winner(repository, commit):
            destination.mkdir()
            sentinel.write_bytes(b"created by the concurrent winner\n")
            return original_tree_entries(repository, commit)

        with patch.object(publisher, "_tree_entries", side_effect=concurrent_winner):
            with self.assertRaises(publisher.ReleaseError):
                publisher.materialize_commit(self.repository, self.commit, destination)

        self.assertEqual(b"created by the concurrent winner\n", sentinel.read_bytes())

    def test_b001_materialization_ignores_export_attributes_and_verifies_raw_tree(self):
        repository = self.base / "raw-tree-source"
        repository.mkdir()
        command(["git", "init", "-b", "main"], repository, self.git_env)
        command(["git", "config", "user.name", "Raw Tree Publisher"], repository, self.git_env)
        command(["git", "config", "user.email", "raw-tree@example.invalid"], repository, self.git_env)
        (repository / ".gitattributes").write_text("omitted.txt export-ignore\n", encoding="utf-8")
        (repository / "omitted.txt").write_bytes(b"must remain in the tagged tree projection\n")
        (repository / "ordinary.txt").write_bytes(b"ordinary\n")
        (repository / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
        command(["git", "add", "."], repository, self.git_env)
        command(["git", "update-index", "--chmod=+x", "run.sh"], repository, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Raw release tree"],
                repository, self.git_env)
        commit = command(["git", "rev-parse", "HEAD"], repository, self.git_env)
        entries = publisher._tree_entries(repository, commit)
        blobs = publisher._tree_blobs(repository, entries)
        source = self.base / "raw-tree-materialized"
        publisher.materialize_commit(repository, commit, source)
        self.assertEqual({".gitattributes", "omitted.txt", "ordinary.txt", "run.sh"},
                         {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()})
        self.assertEqual("100755", next(entry.mode for entry in entries if entry.path == "run.sh"))
        self.assertEqual(b"must remain in the tagged tree projection\n", (source / "omitted.txt").read_bytes())

        (source / "extra.txt").write_bytes(b"extra\n")
        with self.assertRaisesRegex(publisher.ReleaseError, "inventory"):
            publisher._verify_materialized_tree(source, entries, blobs)
        (source / "extra.txt").unlink()
        original = (source / "ordinary.txt").read_bytes()
        (source / "ordinary.txt").write_bytes(b"changed\n")
        with self.assertRaisesRegex(publisher.ReleaseError, "bytes"):
            publisher._verify_materialized_tree(source, entries, blobs)
        (source / "ordinary.txt").write_bytes(original)
        if os.name != "nt":
            (source / "run.sh").chmod(0o644)
            with self.assertRaisesRegex(publisher.ReleaseError, "mode"):
                publisher._verify_materialized_tree(source, entries, blobs)

    def test_b001_unsafe_modes_and_aliases_fail_before_materialization(self):
        oid = "a" * 40
        cases = {
            "symlink": (f"120000 blob {oid}\tlink\0".encode(), "non-regular"),
            "case files": ((f"100644 blob {oid}\tName.txt\0"
                            f"100644 blob {oid}\tname.txt\0").encode(), "case-folding"),
            "case directories": ((f"100644 blob {oid}\tFoo/a.txt\0"
                                  f"100644 blob {oid}\tfoo/b.txt\0").encode(), "case-folding"),
            "reserved": (f"100644 blob {oid}\tdir/CON.txt\0".encode(), "nonportable"),
            "trailing dot": (f"100644 blob {oid}\talias.\0".encode(), "nonportable"),
        }
        for label, (tree, message) in cases.items():
            destination = self.base / ("unsafe-" + label.replace(" ", "-"))
            def fake_git_run(_repository, *arguments, **_kwargs):
                if arguments[0] == "rev-parse":
                    return SimpleNamespace(stdout=oid + "\n")
                self.assertEqual(arguments[0], "ls-tree")
                return SimpleNamespace(stdout=tree)
            with self.subTest(label=label), patch.object(publisher, "git_run", side_effect=fake_git_run):
                with self.assertRaisesRegex(publisher.ReleaseError, message):
                    publisher.materialize_commit(self.repository, self.commit, destination)
                self.assertFalse(destination.exists())

    def make_fake_gh(self, directory, storage, log):
        script = directory / "fake_gh.py"
        script.write_text("""import json, os, pathlib, shutil, sys
args=sys.argv[1:]
with pathlib.Path(os.environ['AWF_FAKE_GH_LOG']).open('a', encoding='utf-8') as out:
    out.write(json.dumps(args)+'\\n')
store=pathlib.Path(os.environ['AWF_FAKE_GH_STORE'])
store.mkdir(exist_ok=True)
if args[:2] == ['release','create']:
    for value in args[3:]:
        path=pathlib.Path(value)
        if path.suffix == '.zip' and path.is_file(): shutil.copyfile(path, store/path.name)
elif args[:2] == ['release','download']:
    target=pathlib.Path(args[args.index('--dir')+1]); target.mkdir(exist_ok=True)
    for path in store.glob('*.zip'): shutil.copyfile(path, target/path.name)
elif args[:2] == ['auth','git-credential'] and args[2:] == ['get']:
    sys.stdin.read()
    print('username=awf-fake-gh')
    print('password='+os.environ['AWF_FAKE_GH_PASSWORD'])
else: raise SystemExit(2)
""", encoding="utf-8")
        if os.name == "nt":
            executable = directory / "gh.cmd"
            executable.write_text('@"' + sys.executable + '" "%~dp0fake_gh.py" %*\n', encoding="utf-8")
        else:
            executable = directory / "gh"
            executable.write_text("#!/bin/sh\nexec \"" + sys.executable + "\" \"$(dirname \"$0\")/fake_gh.py\" \"$@\"\n", encoding="utf-8")
            executable.chmod(0o755)
        return executable

    def make_fake_repository_helper(self, directory, log):
        directory.mkdir()
        script = directory / "repository_helper.py"
        script.write_text("""import pathlib, sys
pathlib.Path(sys.argv[1]).write_text('repository helper was invoked\\n', encoding='utf-8')
sys.stdin.read()
print('username=repository-helper')
print('password=repository-secret')
""", encoding="utf-8")
        if os.name == "nt":
            executable = directory / "repository-helper.cmd"
            executable.write_text(
                '@"' + sys.executable + '" "%~dp0repository_helper.py" "' + str(log) + '" %*\n',
                encoding="utf-8")
        else:
            executable = directory / "repository-helper"
            executable.write_text(
                "#!/bin/sh\nexec " + publisher.shlex.quote(sys.executable) + " "
                + publisher.shlex.quote(str(script)) + " " + publisher.shlex.quote(str(log)) + " \"$@\"\n",
                encoding="utf-8")
            executable.chmod(0o755)
        return executable

    def test_local_bare_remote_fake_gh_and_verify_changed_byte(self):
        bare = self.base / "remote.git"
        command(["git", "init", "--bare", str(bare)], self.base)
        command(["git", "remote", "add", "origin", str(bare)], self.repository)
        command(["git", "push", "-u", "origin", "main"], self.repository)
        # A local bare origin has no HOST/OWNER/REPO; stand in for the resolver so
        # the fake gh can assert the value is always passed (resolver tested below).
        resolver = patch.object(publisher, "origin_repository", return_value="github.com/jkinlay/awf-fixture")
        push_url = patch.object(publisher, "origin_push_url", return_value=str(bare))
        parser = patch.object(
            publisher, "_parse_repository_url", return_value="github.com/jkinlay/awf-fixture")
        resolver.start(); self.addCleanup(resolver.stop)
        push_url.start(); self.addCleanup(push_url.stop)
        parser.start(); self.addCleanup(parser.stop)
        fake_bin = self.base / "fake-bin"; fake_bin.mkdir()
        store = self.base / "fake-release"; store.mkdir()
        log = self.base / "fake-gh.jsonl"
        executable = self.make_fake_gh(fake_bin, store, log)
        env = os.environ.copy()
        env.update(PATH=str(fake_bin) + os.pathsep + env.get("PATH", ""),
                   AWF_FAKE_GH_LOG=str(log), AWF_FAKE_GH_STORE=str(store),
                   GH_REPO="attacker/elsewhere")
        old = os.environ.copy()
        os.environ.update(env)
        try:
            result = publisher.publish(self.repository, self.commit, self.base / "published-output",
                                       self.windows_check, self.windows_pin,
                                       validation_runner=self.fake_validation, gh=str(executable))
            self.assertEqual("DRAFT_CREATED", result["status"])
            verified = publisher.verify_tag(self.repository, "v1.9.4", self.base / "verify-output",
                                            gh=str(executable))
            self.assertEqual("PASS", verified["status"])
            extra = store / "unexpected-extra.zip"
            extra.write_bytes(b"unrecorded release asset")
            with self.assertRaisesRegex(publisher.ReleaseError, "published release assets differ"):
                publisher.verify_tag(self.repository, "v1.9.4", self.base / "verify-extra-output",
                                     gh=str(executable))
            extra.unlink()
            changed = next(store.glob("*.zip"))
            changed.write_bytes(changed.read_bytes() + b"changed")
            with self.assertRaisesRegex(publisher.ReleaseError, "published release assets differ"):
                publisher.verify_tag(self.repository, "v1.9.4", self.base / "verify-fail-output",
                                     gh=str(executable))
        finally:
            os.environ.clear(); os.environ.update(old)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(call[:2] == ["release", "create"] and "--draft" in call for call in calls))
        self.assertTrue(any(call[:2] == ["release", "download"] for call in calls))
        # Ambient GH_REPO must never choose the release repository.
        for call in calls:
            self.assertIn("--repo", call)
            self.assertEqual("github.com/jkinlay/awf-fixture", call[call.index("--repo") + 1])

    def test_origin_repository_binds_gh_to_the_tagged_origin(self):
        def configure(fetch, *pushes):
            subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repository, capture_output=True)
            command(["git", "remote", "add", "origin", fetch], self.repository)
            for index, url in enumerate(pushes):
                command(["git", "remote", "set-url", "--push"] + (["--add"] if index else []) + ["origin", url],
                        self.repository)
        cases = {
            "https://github.com/jkinlay/agentic-workflow-framework.git": "github.com/jkinlay/agentic-workflow-framework",
            "https://token@github.com/jkinlay/agentic-workflow-framework": "github.com/jkinlay/agentic-workflow-framework",
            "git@github.com:jkinlay/agentic-workflow-framework.git": "github.com/jkinlay/agentic-workflow-framework",
            "ssh://git@ghe.example.com:2222/team/awf.git": "ghe.example.com/team/awf",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                configure(url)
                self.assertEqual(expected, publisher.origin_repository(self.repository))
        same = "https://github.com/jkinlay/agentic-workflow-framework.git"
        configure(same, "git@github.com:jkinlay/agentic-workflow-framework.git")
        self.assertEqual("github.com/jkinlay/agentic-workflow-framework", publisher.origin_repository(self.repository))
        rejected = {
            "not a recognised": (str(self.base / "remote.git"),),
            "different repositories": (same, "https://github.com/attacker/elsewhere.git"),
            "exactly one": (same, same, "https://github.com/attacker/elsewhere.git"),
        }
        for message, urls in rejected.items():
            with self.subTest(message=message):
                configure(*urls)
                with self.assertRaisesRegex(publisher.ReleaseError, message):
                    publisher.origin_repository(self.repository)
        for url in ("file:///tmp/remote.git", "https://github.com/onlyowner"):
            with self.subTest(url=url):
                configure(url)
                with self.assertRaisesRegex(publisher.ReleaseError, "not a recognised"):
                    publisher.origin_repository(self.repository)

    def test_rejected_origin_fails_before_creating_a_tag(self):
        fake_bin = self.base / "fake-bin-reject"; fake_bin.mkdir()
        store = self.base / "fake-release-reject"; store.mkdir()
        log = self.base / "fake-gh-reject.jsonl"
        executable = self.make_fake_gh(fake_bin, store, log)
        same = "https://github.com/jkinlay/agentic-workflow-framework.git"
        def tag_list():
            return subprocess.run(["git", "for-each-ref", "refs/tags", "--format=%(refname) %(objectname)"],
                                  cwd=self.repository, capture_output=True, text=True, check=True).stdout
        before = tag_list()
        for message, pushes in {"different repositories": ("https://github.com/attacker/elsewhere.git",),
                                "exactly one": (same, "https://github.com/attacker/elsewhere.git")}.items():
            with self.subTest(message=message):
                subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repository, capture_output=True)
                command(["git", "remote", "add", "origin", same], self.repository)
                for index, url in enumerate(pushes):
                    command(["git", "remote", "set-url", "--push"] + (["--add"] if index else []) + ["origin", url],
                            self.repository)
                with self.assertRaisesRegex(publisher.ReleaseError, message):
                    publisher.publish(self.repository, self.commit, self.base / f"reject-{len(pushes)}",
                                      self.windows_check, self.windows_pin,
                                      validation_runner=self.fake_validation, gh=str(executable))
                self.assertEqual(before, tag_list())
        self.assertFalse(log.exists() and log.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
