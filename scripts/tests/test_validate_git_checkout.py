"""Regression tests for command-scoped Git checkout configuration."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "awf_validate_git_checkout", ROOT / "scripts/validate_git_checkout.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class AutocrlfScopeTests(unittest.TestCase):
    def test_fixture_checkout_preserves_false_and_input_without_global_writes(self):
        for value in ("false", "input"):
            with self.subTest(value=value), tempfile.TemporaryDirectory(prefix="awf-git-checkout-") as raw:
                root = Path(raw)
                home = root / "home"
                home.mkdir()
                global_config = home / "global.gitconfig"
                global_config.write_text("[core]\n\tautocrlf = input\n", encoding="utf-8", newline="\n")
                global_before = global_config.read_bytes()
                caller_env = dict(os.environ, HOME=str(home), GIT_CONFIG_GLOBAL=str(global_config),
                                  GIT_CONFIG_NOSYSTEM="1")
                source = root / "source"
                source.mkdir()

                def command(arguments, cwd=source, env=caller_env, check=True):
                    return subprocess.run([str(item) for item in arguments], cwd=cwd, env=env,
                                          capture_output=True, check=check, text=False)

                command(["git", "init", "-q", "-b", "main"])
                command(["git", "config", "--local", "core.autocrlf", value])
                (source / "plain.txt").write_bytes(b"one\ntwo\n")
                command(["git", "add", "plain.txt"])
                command(["git", "-c", "user.name=fixture", "-c",
                         "user.email=fixture@example.invalid", "commit", "-q", "-m", "fixture"])

                controls = root / "controls"
                controls.mkdir()
                empty = controls / "empty"
                empty.write_bytes(b"")
                hooks = controls / "hooks"
                hooks.mkdir()
                isolated_env, git_prefix = validator.isolated_git_invocation(
                    "git", empty, hooks, caller_env)
                clone = root / "clone"
                command([*git_prefix, "clone", "--local", "--no-hardlinks", "--no-checkout",
                         "--", source, clone], cwd=root, env=isolated_env)
                command([*git_prefix, "checkout", "--force", "HEAD"], cwd=clone, env=isolated_env)

                self.assertEqual(b"one\r\ntwo\r\n", (clone / "plain.txt").read_bytes())
                source_setting = command(
                    ["git", "config", "--local", "--get", "core.autocrlf"]).stdout.decode().strip()
                self.assertEqual(value, source_setting)
                clone_setting = command(
                    ["git", "config", "--local", "--get", "core.autocrlf"],
                    cwd=clone, check=False)
                self.assertEqual(1, clone_setting.returncode)
                self.assertEqual(global_before, global_config.read_bytes())


if __name__ == "__main__":
    unittest.main()
