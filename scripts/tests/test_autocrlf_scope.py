"""Cross-tool regressions for repository-scoped ``core.autocrlf`` handling."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load_script(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load_script("awf_autocrlf_publish_release", "scripts/publish_release.py")
ac42 = load_script("awf_autocrlf_ac42_e2e", "scripts/ac42_e2e.py")
checkout_validator = load_script(
    "awf_autocrlf_validate_git_checkout", "scripts/validate_git_checkout.py")


def git(repository, environment, *arguments, check=True):
    return subprocess.run(
        ["git", *map(str, arguments)], cwd=repository, env=environment,
        capture_output=True, check=check, text=False, timeout=120)


@contextmanager
def configured_repository(value):
    with tempfile.TemporaryDirectory(prefix="awf-autocrlf-scope-") as raw:
        root = Path(raw)
        home = root / "home"
        home.mkdir()
        global_config = home / "global.gitconfig"
        global_config.write_bytes(
            b"[core]\n\tautocrlf = true\n[init]\n\tdefaultBranch = main\n")
        global_before = global_config.read_bytes()
        environment = dict(
            os.environ,
            HOME=str(home),
            GIT_CONFIG_GLOBAL=str(global_config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
        )
        source = root / "source"
        source.mkdir()
        git(source, environment, "init", "-q", "-b", "main")
        git(source, environment, "config", "--local", "core.autocrlf", value)
        (source / "plain.txt").write_bytes(b"one\ntwo\n")
        git(source, environment, "add", "plain.txt")
        git(
            source, environment,
            "-c", "user.name=fixture",
            "-c", "user.email=fixture@example.invalid",
            "commit", "-q", "-m", "fixture",
        )
        yield SimpleNamespace(
            root=root,
            source=source,
            environment=environment,
            global_config=global_config,
            global_before=global_before,
        )


@contextmanager
def globally_configured_repository(value):
    with tempfile.TemporaryDirectory(prefix="awf-global-autocrlf-scope-") as raw:
        root = Path(raw)
        home = root / "home"
        home.mkdir()
        global_config = home / "global.gitconfig"
        global_config.write_bytes(
            f"[core]\n\tautocrlf = {value}\n[init]\n\tdefaultBranch = main\n".encode("ascii"))
        global_before = global_config.read_bytes()
        environment = dict(
            os.environ,
            HOME=str(home),
            GIT_CONFIG_GLOBAL=str(global_config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
        )
        source = root / "source"
        source.mkdir()
        git(source, environment, "init", "-q", "-b", "main")
        (source / "plain.txt").write_bytes(b"one\ntwo\n")
        git(source, environment, "add", "plain.txt")
        git(
            source, environment,
            "-c", "user.name=fixture",
            "-c", "user.email=fixture@example.invalid",
            "commit", "-q", "-m", "fixture",
        )
        yield SimpleNamespace(
            root=root,
            source=source,
            environment=environment,
            global_config=global_config,
            global_before=global_before,
        )


class AutocrlfScopeTests(unittest.TestCase):
    def assert_configuration_unchanged(self, fixture, value):
        configured = git(
            fixture.source, fixture.environment,
            "config", "--local", "--get", "core.autocrlf")
        self.assertEqual(value, configured.stdout.decode().strip())
        self.assertEqual(fixture.global_before, fixture.global_config.read_bytes())

    def assert_clone_has_no_persisted_autocrlf(self, fixture, clone):
        configured = git(
            clone, fixture.environment,
            "config", "--local", "--get", "core.autocrlf", check=False)
        self.assertEqual(1, configured.returncode, configured.stderr.decode(errors="replace"))

    def assert_global_configuration_unchanged(self, fixture, value):
        effective = git(
            fixture.source, fixture.environment,
            "config", "--get", "core.autocrlf")
        self.assertEqual(value, effective.stdout.decode().strip())
        local = git(
            fixture.source, fixture.environment,
            "config", "--local", "--get", "core.autocrlf", check=False)
        self.assertEqual(1, local.returncode, local.stderr.decode(errors="replace"))
        self.assertEqual(fixture.global_before, fixture.global_config.read_bytes())

    def test_publish_validation_checkout_preserves_explicit_configuration(self):
        for value in ("false", "input"):
            with self.subTest(value=value), configured_repository(value) as fixture:
                commit = git(
                    fixture.source, fixture.environment, "rev-parse", "HEAD").stdout.decode().strip()
                destination = fixture.root / "validation-checkout"
                with patch.dict(os.environ, fixture.environment, clear=True):
                    with publisher.validation_checkout(
                            fixture.source, commit, destination) as checkout:
                        self.assertEqual(b"one\ntwo\n", (checkout / "plain.txt").read_bytes())
                self.assertFalse(destination.exists())
                self.assert_configuration_unchanged(fixture, value)

    def test_ac42_clone_scopes_required_conversion_to_clone_command(self):
        for value in ("false", "input"):
            with self.subTest(value=value), configured_repository(value) as fixture:
                clone = fixture.root / "ac42-clone"
                overrides = {
                    "HOME": fixture.environment["HOME"],
                    "GIT_CONFIG_GLOBAL": fixture.environment["GIT_CONFIG_GLOBAL"],
                    "GIT_CONFIG_NOSYSTEM": "1",
                }
                result = ac42.clone_with_autocrlf(
                    fixture.source, clone, fixture.root, extra=overrides)
                self.assertEqual(0, result["exit_code"], result)
                self.assertEqual(b"one\r\ntwo\r\n", (clone / "plain.txt").read_bytes())
                self.assert_clone_has_no_persisted_autocrlf(fixture, clone)
                self.assert_configuration_unchanged(fixture, value)

    def test_fixture_checkout_scopes_required_conversion_to_disposable_clone(self):
        for value in ("false", "input"):
            with self.subTest(value=value), configured_repository(value) as fixture:
                controls = fixture.root / "controls"
                controls.mkdir()
                empty = controls / "empty"
                empty.write_bytes(b"")
                hooks = controls / "hooks"
                hooks.mkdir()
                isolated_environment, git_prefix = checkout_validator.isolated_git_invocation(
                    "git", empty, hooks, fixture.environment)
                clone = fixture.root / "fixture-clone"
                subprocess.run(
                    [*git_prefix, "clone", "--local", "--no-hardlinks", "--no-checkout",
                     "--", str(fixture.source), str(clone)],
                    cwd=fixture.root, env=isolated_environment,
                    capture_output=True, check=True, timeout=120)
                subprocess.run(
                    [*git_prefix, "checkout", "--force", "HEAD"],
                    cwd=clone, env=isolated_environment,
                    capture_output=True, check=True, timeout=120)
                self.assertEqual(b"one\r\ntwo\r\n", (clone / "plain.txt").read_bytes())
                self.assert_clone_has_no_persisted_autocrlf(fixture, clone)
                self.assert_configuration_unchanged(fixture, value)

    def test_windows_publish_checkout_honours_explicit_global_configuration(self):
        windows_os = SimpleNamespace(name="nt", walk=os.walk)
        for value in ("false", "input"):
            with self.subTest(value=value), globally_configured_repository(value) as fixture:
                commit = git(
                    fixture.source, fixture.environment, "rev-parse", "HEAD").stdout.decode().strip()
                destination = fixture.root / "validation-checkout"
                with patch.dict(os.environ, fixture.environment, clear=True), \
                        patch.object(publisher, "os", windows_os):
                    with publisher.validation_checkout(
                            fixture.source, commit, destination) as checkout:
                        self.assertEqual(b"one\ntwo\n", (checkout / "plain.txt").read_bytes())
                self.assertFalse(destination.exists())
                self.assert_global_configuration_unchanged(fixture, value)

    def test_windows_ac42_clone_honours_explicit_global_configuration(self):
        for value in ("false", "input"):
            with self.subTest(value=value), globally_configured_repository(value) as fixture:
                clone = fixture.root / "ac42-clone"
                overrides = {
                    "HOME": fixture.environment["HOME"],
                    "GIT_CONFIG_GLOBAL": fixture.environment["GIT_CONFIG_GLOBAL"],
                    "GIT_CONFIG_NOSYSTEM": "1",
                }
                with patch.object(ac42.platform, "system", return_value="Windows"):
                    result = ac42.clone_with_autocrlf(
                        fixture.source, clone, fixture.root, extra=overrides)
                self.assertEqual(0, result["exit_code"], result)
                self.assertEqual(b"one\r\ntwo\r\n", (clone / "plain.txt").read_bytes())
                self.assert_clone_has_no_persisted_autocrlf(fixture, clone)
                self.assert_global_configuration_unchanged(fixture, value)

    def test_windows_fixture_checkout_honours_explicit_global_configuration(self):
        for value in ("false", "input"):
            with self.subTest(value=value), globally_configured_repository(value) as fixture:
                controls = fixture.root / "controls"
                controls.mkdir()
                empty = controls / "empty"
                empty.write_bytes(b"")
                hooks = controls / "hooks"
                hooks.mkdir()
                windows_os = SimpleNamespace(name="nt", environ=fixture.environment)
                with patch.object(checkout_validator, "os", windows_os):
                    isolated_environment, git_prefix = checkout_validator.isolated_git_invocation(
                        "git", empty, hooks)
                clone = fixture.root / "fixture-clone"
                subprocess.run(
                    [*git_prefix, "clone", "--local", "--no-hardlinks", "--no-checkout",
                     "--", str(fixture.source), str(clone)],
                    cwd=fixture.root, env=isolated_environment,
                    capture_output=True, check=True, timeout=120)
                subprocess.run(
                    [*git_prefix, "checkout", "--force", "HEAD"],
                    cwd=clone, env=isolated_environment,
                    capture_output=True, check=True, timeout=120)
                self.assertEqual(b"one\r\ntwo\r\n", (clone / "plain.txt").read_bytes())
                self.assert_clone_has_no_persisted_autocrlf(fixture, clone)
                self.assert_global_configuration_unchanged(fixture, value)


if __name__ == "__main__":
    unittest.main()
