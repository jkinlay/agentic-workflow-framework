"""Candidate-tree and publisher equality tests use temporary repositories outside AWF."""
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

from agentic import ValidationError
from agentic.gittree import candidate_tree, tested_tree, verify_publisher_tree


class GitTreeTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("TOOLCHAIN_ABSENT: Git is required")
        self.temp = tempfile.TemporaryDirectory(prefix="awf-gittree-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init", "--initial-branch=main")
        for key, value in (("user.name", "AWF fixture"), ("user.email", "fixture@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
            self.git("config", key, value)

    def git(self, *args, input_bytes=None, expected=0):
        result = subprocess.run([shutil.which("git"), *args], cwd=self.root, input=input_bytes,
                                capture_output=True, timeout=30)
        self.assertEqual(expected, result.returncode, result.stderr.decode("utf-8", "replace"))
        return result.stdout.decode("utf-8", "replace").strip()

    def write(self, relative, data, executable=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable and os.name != "nt":
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def commit(self, message="fixture"):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_candidate_matches_write_tree_for_content_modes_filters_and_nested_changes(self):
        cleaner = Path(self.temp.name) / "clean.py"
        cleaner.write_text("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read().replace(b'SECRET', b'CLEAN'))\n", encoding="utf-8")
        self.git("config", "filter.awfclean.clean", f'"{sys.executable}" "{cleaner}"')
        self.git("config", "filter.awfclean.smudge", "cat")
        self.write(".gitattributes", b"*.txt text eol=lf\nfiltered.dat filter=awfclean\n")
        self.write("line.txt", b"base\n")
        self.write("filtered.dat", b"base SECRET\n")
        self.write("nested/modify.txt", b"old\n")
        self.write("nested/delete.txt", b"delete\n")
        self.write("bin/run.sh", b"#!/bin/sh\nexit 0\n", executable=False)
        base = self.commit("base")

        self.write("line.txt", b"one\r\ntwo\r\n")
        self.write("filtered.dat", b"changed SECRET\n")
        self.write("nested/modify.txt", b"new\n")
        (self.root / "nested/delete.txt").unlink()
        self.write("nested/deeper/add.txt", b"added\r\n")
        self.write("bin/run.sh", b"#!/bin/sh\nexit 0\n", executable=True)
        if os.name == "nt":
            self.git("update-index", "--chmod=+x", "bin/run.sh")
        self.write("scratch.tmp", b"must not enter candidate\n")
        changes = [
            {"path": "line.txt", "action": "modified"},
            {"path": "filtered.dat", "action": "modified"},
            {"path": "nested/modify.txt", "action": "modified"},
            {"path": "nested/delete.txt", "action": "deleted"},
            {"path": "nested/deeper/add.txt", "action": "added"},
        ]
        changes.append({"path": "bin/run.sh", "action": "modified"})

        objects_before = sorted(p.relative_to(self.root / ".git/objects").as_posix()
                                for p in (self.root / ".git/objects").rglob("*") if p.is_file())
        index_before = (self.root / ".git/index").stat().st_mtime_ns
        result = candidate_tree(self.root, base, changes)
        objects_after = sorted(p.relative_to(self.root / ".git/objects").as_posix()
                               for p in (self.root / ".git/objects").rglob("*") if p.is_file())
        self.assertEqual(objects_before, objects_after)
        self.assertEqual(index_before, (self.root / ".git/index").stat().st_mtime_ns)
        self.assertEqual(("scratch.tmp",), result.ignored_untracked)
        self.git("add", "-A", "--", *(change["path"] for change in changes))
        self.assertEqual(self.git("write-tree"), result.tested_tree)

    def test_symlink_blob_and_mode_match_write_tree(self):
        if os.name == "nt":
            self.skipTest("PLATFORM_PRIVILEGE: Windows symbolic-link creation is not generally available")
        self.write("target.txt", b"target\n")
        base = self.commit("base")
        (self.root / "link.txt").symlink_to("target.txt")
        changes = [{"path": "link.txt", "action": "added"}]
        expected = tested_tree(self.root, base, changes)
        self.git("add", "link.txt")
        self.assertEqual(self.git("write-tree"), expected)
        self.assertEqual("120000", self.git("ls-files", "--stage", "link.txt").split()[0])

    def test_scope_violations_ignored_untracked_and_unsupported_paths(self):
        self.write("tracked.txt", b"base\n")
        base = self.commit("base")
        self.write("tracked.txt", b"changed\n")
        self.write("ignored.txt", b"untracked\n")
        with self.assertRaisesRegex(ValidationError, "undeclared tracked"):
            tested_tree(self.root, base, [])
        for path in ("bad\nname", "bad\rname", "bad\0name"):
            with self.subTest(path=repr(path)), self.assertRaisesRegex(ValidationError, "newline or NUL"):
                tested_tree(self.root, base, [{"path": path, "action": "added"}])
        self.write("tracked.txt", b"base\n")
        with self.assertRaisesRegex(ValidationError, "unchanged"):
            tested_tree(self.root, base, [{"path": "tracked.txt", "action": "modified"}])

    def test_gitlink_is_copied_and_declared_gitlink_change_is_rejected(self):
        sub = self.root / "vendor/sub"
        sub.mkdir(parents=True)
        subprocess.run([shutil.which("git"), "init", "--initial-branch=main"], cwd=sub, check=True, capture_output=True)
        for key, value in (("user.name", "AWF fixture"), ("user.email", "fixture@example.invalid")):
            subprocess.run([shutil.which("git"), "config", key, value], cwd=sub, check=True)
        (sub / "a.txt").write_text("sub\n", encoding="utf-8")
        subprocess.run([shutil.which("git"), "add", "a.txt"], cwd=sub, check=True)
        subprocess.run([shutil.which("git"), "commit", "-m", "sub"], cwd=sub, check=True, capture_output=True)
        oid = subprocess.run([shutil.which("git"), "rev-parse", "HEAD"], cwd=sub, check=True,
                             capture_output=True, text=True).stdout.strip()
        self.write("ordinary.txt", b"base\n")
        self.git("add", "ordinary.txt")
        self.git("update-index", "--add", "--cacheinfo", "160000," + oid + ",vendor/sub")
        self.git("commit", "-m", "base with gitlink")
        base = self.git("rev-parse", "HEAD")
        self.write("ordinary.txt", b"changed\n")
        changes = [{"path": "ordinary.txt", "action": "modified"}]
        expected = tested_tree(self.root, base, changes)
        self.git("add", "ordinary.txt")
        self.assertEqual(self.git("write-tree"), expected)
        self.git("reset", "--hard", base)
        with self.assertRaisesRegex(ValidationError, "gitlinks"):
            tested_tree(self.root, base, [{"path": "vendor/sub", "action": "modified"}])

    def test_publisher_comparison_rejects_content_mode_and_path_differences(self):
        self.write("file.txt", b"base\n")
        base = self.commit("base")
        self.write("file.txt", b"candidate\n")
        changes = [{"path": "file.txt", "action": "modified"}]
        expected = tested_tree(self.root, base, changes)
        self.commit("candidate")
        self.assertEqual(expected, verify_publisher_tree(self.root, expected))

        self.write("file.txt", b"publisher edit\n")
        self.commit("content difference")
        with self.assertRaisesRegex(ValidationError, "scope violation"):
            verify_publisher_tree(self.root, expected)
        self.git("reset", "--hard", "HEAD^")
        self.write("extra.txt", b"publisher path\n")
        self.commit("path difference")
        with self.assertRaisesRegex(ValidationError, "scope violation"):
            verify_publisher_tree(self.root, expected)
        if os.name != "nt":
            self.git("reset", "--hard", "HEAD^")
            path = self.root / "file.txt"
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            self.commit("mode difference")
            with self.assertRaisesRegex(ValidationError, "scope violation"):
                verify_publisher_tree(self.root, expected)

    def test_sha256_repository_uses_detected_object_format(self):
        other = Path(self.temp.name) / "sha256"
        probe = subprocess.run([shutil.which("git"), "init", "--object-format=sha256", "--initial-branch=main", str(other)],
                               capture_output=True, text=True)
        if probe.returncode:
            self.skipTest("TOOLCHAIN_ABSENT: installed Git lacks SHA-256 repository support")
        for key, value in (("user.name", "AWF fixture"), ("user.email", "fixture@example.invalid")):
            subprocess.run([shutil.which("git"), "-C", str(other), "config", key, value], check=True)
        (other / "a.txt").write_text("base\n", encoding="utf-8")
        subprocess.run([shutil.which("git"), "-C", str(other), "add", "a.txt"], check=True)
        subprocess.run([shutil.which("git"), "-C", str(other), "commit", "-m", "base"], check=True, capture_output=True)
        base = subprocess.run([shutil.which("git"), "-C", str(other), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        (other / "a.txt").write_text("changed\n", encoding="utf-8")
        value = candidate_tree(other, base, [{"path": "a.txt", "action": "modified"}])
        self.assertEqual(("sha256", 64), (value.object_format, len(value.tested_tree)))
        subprocess.run([shutil.which("git"), "-C", str(other), "add", "a.txt"], check=True)
        actual = subprocess.run([shutil.which("git"), "-C", str(other), "write-tree"], check=True,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(actual, value.tested_tree)


if __name__ == "__main__":
    unittest.main()
