"""Registered SQLite publication-scan fixture regressions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentic.publication import _detectors, _scan_text, scan_repository
from agentic import publication_sqlite_fixture as sqlite_fixture
from agentic.publication_sqlite_fixture import (
    REGISTERED_PATH,
    REGISTERED_SHA256,
    REGISTERED_SIZE,
    content_addressed_blob_path,
    load_pinned_specimen_bytes,
    registered_fixture_identity,
    validate_registered_sqlite_fixture,
)

# Reuse the publication test repository helper without importing its full suite module side effects.
import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "publication_test_helpers",
    Path(__file__).resolve().parent / "test_publication.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
# Loading the full test_publication module is heavy; inline a minimal repo helper instead.


def _git(repository, *args, check=True):
    import subprocess
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
        capture_output=True, check=False)
    if check and result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8", "replace").strip()


class _Repo:
    def __init__(self, parent):
        self.path = Path(parent) / "repository"
        self.path.mkdir()
        _git(self.path, "init", "-b", "main")
        _git(self.path, "config", "user.name", "Synthetic User")
        _git(self.path, "config", "user.email", "synthetic@example.invalid")
        self.write("seed.txt", "seed\n")
        _git(self.path, "add", "seed.txt")
        _git(self.path, "commit", "-m", "base")
        self.base = _git(self.path, "rev-parse", "HEAD")
        _git(self.path, "switch", "-c", "awf/EX-sqlite-fixture")

    def write(self, relative, text=None, data=None):
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            path.write_bytes(data)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message, *paths):
        _git(self.path, "add", "--", *paths)
        _git(self.path, "commit", "-m", message)
        return _git(self.path, "rev-parse", "HEAD")


class RegisteredSqliteFixtureTests(unittest.TestCase):
    def setUp(self):
        self.specimen = load_pinned_specimen_bytes()
        self.detectors, self.allows = _detectors({}, {})

    def test_pinned_specimen_passes_validation_and_scan_views(self):
        self.assertEqual(REGISTERED_SIZE, len(self.specimen))
        self.assertEqual(REGISTERED_SHA256, hashlib.sha256(self.specimen).hexdigest())
        path = content_addressed_blob_path(REGISTERED_SHA256)
        self.assertEqual(REGISTERED_PATH, registered_fixture_identity(path, REGISTERED_SHA256))
        views, reason = validate_registered_sqlite_fixture(self.specimen, path=path)
        self.assertIsNone(reason)
        self.assertGreaterEqual(len(views), 3)
        findings = []
        for view in views:
            findings += _scan_text(view, commit="fixture", path=path, source="current-file",
                                   detectors=self.detectors, allows=self.allows)
        self.assertEqual([], findings)

    def test_one_byte_mutation_blocks(self):
        mutated = bytearray(self.specimen)
        mutated[-1] = (mutated[-1] + 1) % 256
        views, reason = validate_registered_sqlite_fixture(
            bytes(mutated), path=content_addressed_blob_path(REGISTERED_SHA256))
        self.assertIsNone(views)
        self.assertIsNotNone(reason)

    def test_unregistered_sqlite_blocks(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE t(x TEXT)")
        connection.execute("INSERT INTO t VALUES ('hello')")
        connection.commit()
        raw = connection.serialize()
        connection.close()
        views, reason = validate_registered_sqlite_fixture(
            raw, path=".agentic/tests/fixtures/upgrades/blobs/" + hashlib.sha256(raw).hexdigest())
        self.assertIsNone(views)
        self.assertEqual("unregistered-binary", reason)

    def test_blob_column_blocks(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE model_runs(run_id TEXT PRIMARY KEY, payload BLOB)")
        connection.execute("INSERT INTO model_runs VALUES ('1', X'0001')")
        connection.commit()
        raw = connection.serialize()
        connection.close()
        digest = hashlib.sha256(raw).hexdigest()
        with mock.patch.object(sqlite_fixture, "REGISTERED_SHA256", digest), \
                mock.patch.object(sqlite_fixture, "REGISTERED_SIZE", len(raw)):
            views, reason = validate_registered_sqlite_fixture(raw, path=REGISTERED_PATH)
        self.assertIsNone(views)
        self.assertEqual("malformed", reason)

    def test_utf16_marker_blocks(self):
        raw = bytearray(self.specimen)
        raw[16:18] = b"\xfe\xff"
        views, reason = validate_registered_sqlite_fixture(
            bytes(raw), path=content_addressed_blob_path(REGISTERED_SHA256),
            sha256=REGISTERED_SHA256)
        self.assertIsNone(views)
        self.assertEqual("utf16", reason)

    def test_budget_overrun_blocks(self):
        raw = self.specimen + b"\x00" * 64
        views, reason = validate_registered_sqlite_fixture(
            raw, path=content_addressed_blob_path(REGISTERED_SHA256),
            sha256=REGISTERED_SHA256)
        self.assertIsNone(views)
        self.assertEqual("budget-overrun", reason)

    def test_slack_deny_token_blocks(self):
        token = "C" + ":" + "\\" + "Users" + "\\" + "fixture-user" + "\\" + "secret.txt"
        raw = bytearray(self.specimen)
        needle = token.encode("ascii")
        placed = False
        for index in range(1024, len(raw) - len(needle)):
            if raw[index:index + len(needle)] == b"\x00" * len(needle):
                raw[index:index + len(needle)] = needle
                placed = True
                break
        self.assertTrue(placed)
        mutated = bytes(raw)
        digest = hashlib.sha256(mutated).hexdigest()
        with mock.patch.object(sqlite_fixture, "REGISTERED_SHA256", digest), \
                mock.patch.object(sqlite_fixture, "REGISTERED_SIZE", len(mutated)):
            views, reason = validate_registered_sqlite_fixture(mutated, path=REGISTERED_PATH)
        self.assertIsNone(reason, reason)
        findings = []
        for view in views:
            findings += _scan_text(view, commit="fixture", path=REGISTERED_PATH, source="current-file",
                                   detectors=self.detectors, allows=self.allows)
        self.assertTrue(any(item["detector_id"] in {"builtin.windows_absolute", "builtin.home_path"}
                            for item in findings), findings)

    def test_unknown_binary_stays_unscanned(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write("opaque.bin", data=b"\x00\x01\x02\x03secret\x00")
        repo.commit("add opaque binary", "opaque.bin")
        result = scan_repository(repo.path, repo.base, "HEAD")
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item.get("path") == "opaque.bin" and item.get("reason") == "binary"
                            for item in result["unscanned"]))

    def test_pinned_specimen_in_repo_scan_is_not_unscanned(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        relative = content_addressed_blob_path(REGISTERED_SHA256)
        repo.write(relative, data=self.specimen)
        repo.commit("add registered sqlite specimen", relative)
        result = scan_repository(repo.path, repo.base, "HEAD")
        self.assertFalse(any(item.get("path") == relative for item in result["unscanned"]),
                         result["unscanned"])
        # May still BLOCK on other findings; the fixture itself must be scanned.
        self.assertFalse(any(item.get("path") == relative and item.get("reason") == "binary"
                             for item in result["unscanned"]))


if __name__ == "__main__":
    unittest.main()
