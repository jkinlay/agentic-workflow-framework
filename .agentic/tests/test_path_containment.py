"""Regression coverage for normalized state-folder containment checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agentic.canonical import sha256
from agentic import path_containment
from agentic.path_containment import is_within, normalize_path, relative_within
from agentic.providers import github_review_host
from agentic.providers.github_review_host import HostDriver


class PathContainmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.long_state = self.base / "runneradmin" / "state"
        self.long_state.mkdir(parents=True)
        self.short_state = self.base / "RUNNE~1" / "state"

    @staticmethod
    def _expand_fake_short_name(value):
        parts = list(Path(value).parts)
        parts = ["runneradmin" if part.casefold() == "runne~1" else part
                 for part in parts]
        return Path(*parts)

    def test_simulated_short_root_contains_long_child_on_every_platform(self):
        child = self.long_state / "config.json"
        child.write_text("{}\n", encoding="utf-8")
        with patch.object(path_containment, "_long_path_expander",
                          side_effect=self._expand_fake_short_name):
            self.assertEqual(normalize_path(self.short_state), self.long_state)
            self.assertTrue(is_within(child, self.short_state))
            self.assertEqual(relative_within(child, self.short_state),
                             Path("config.json"))

    def test_nonexistent_tail_is_normalized_without_raising(self):
        child = self.long_state / "future" / "nested" / "config.json"
        with patch.object(path_containment, "_long_path_expander",
                          side_effect=self._expand_fake_short_name):
            self.assertEqual(normalize_path(self.short_state / "future" / "nested"),
                             self.long_state / "future" / "nested")
            self.assertEqual(relative_within(child, self.short_state),
                             Path("future") / "nested" / "config.json")

    def test_sibling_prefix_is_rejected(self):
        root = self.base / "state"
        sibling = self.base / "state2" / "config.json"
        root.mkdir()
        sibling.parent.mkdir()
        sibling.touch()
        self.assertFalse(is_within(sibling, root))
        with self.assertRaises(ValueError):
            relative_within(sibling, root)

    def test_dot_dot_escape_is_rejected(self):
        root = self.base / "state"
        root.mkdir()
        escaped = root / ".." / "outside" / "config.json"
        self.assertFalse(is_within(escaped, root))
        with self.assertRaises(ValueError):
            relative_within(escaped, root)

    def test_symlink_escape_is_rejected(self):
        root = self.base / "state"
        outside = self.base / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "config.json").touch()
        link = root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            if os.name != "nt":
                self.skipTest(f"directory symlinks unavailable: {error}")
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True, text=True, check=False)
            if created.returncode:
                self.skipTest("directory symlinks and junctions unavailable: "
                              + (created.stderr.strip() or created.stdout.strip()))
        escaped = link / "config.json"
        self.assertFalse(is_within(escaped, root))
        with self.assertRaises(ValueError):
            relative_within(escaped, root)

    @unittest.skipUnless(os.name == "nt", "requires Windows GetShortPathNameW")
    def test_real_windows_short_root_contains_long_child(self):
        import ctypes
        from ctypes import wintypes

        child = self.long_state / "config.json"
        child.touch()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_short = kernel.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        needed = get_short(str(self.long_state), None, 0)
        if not needed:
            self.skipTest("GetShortPathNameW is unsupported on this volume")
        buffer = ctypes.create_unicode_buffer(needed)
        written = get_short(str(self.long_state), buffer, needed)
        if not written:
            self.skipTest("GetShortPathNameW could not produce an alias")
        short_state = Path(buffer.value)
        if os.path.normcase(str(short_state)) == os.path.normcase(str(self.long_state)):
            self.skipTest("8.3 short names are disabled or unavailable on this volume")
        self.assertTrue(is_within(child, short_state))
        self.assertEqual(relative_within(child, short_state), Path("config.json"))

    def test_bind_created_pr_accepts_simulated_short_state_alias(self):
        config_path = self.long_state / "host.json"
        raw = (json.dumps({"first_draft": True, "pr": 0}, indent=2) + "\n").encode()
        config_path.write_bytes(raw)
        config = {
            "_config_path": str(config_path),
            "config_hash": sha256(raw),
            "state_dir": str(self.short_state),
            "worker_checkout": str(self.base / "worker"),
            "critic_checkout": str(self.base / "critic"),
            "repository": "owner/repository",
            "repository_id": 123,
            "executables": {},
        }
        driver = HostDriver(config, self.base)
        real_tree = github_review_host.Tree
        long_state = self.long_state

        class AliasedTree:
            def __init__(self, root):
                self.delegate = real_tree(long_state)

            def __enter__(self):
                return self.delegate

            def __exit__(self, *args):
                self.delegate.close()

        with patch.object(path_containment, "_long_path_expander",
                          side_effect=self._expand_fake_short_name), \
                patch.object(github_review_host, "Tree", AliasedTree):
            driver.bind_created_pr(42)
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["pr"], 42)
        self.assertEqual(driver.c["pr"], 42)


if __name__ == "__main__":
    unittest.main()
