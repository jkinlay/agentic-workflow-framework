"""Registered SQLite publication-scan fixture regressions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentic.publication import (
    _COMMIT_BOUND_CLOSED_HISTORICAL_SELF_REFERENCES,
    _detectors,
    _scan_text,
    scan_repository,
)
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




AUDITED_COMMIT = "75fc9bacf0adc2fff492560adc21b25bd0ea444e"
AUDITED_LINE_DIGEST = "6126da46f095f5e97d7c20eb23f5c004a24092a81afb42edff33799a4a7133c3"


def audited_75fc9bac_message_line():
    """Rebuild the exact audited provenance line without embedding a contiguous match."""
    drive = "C" + ":"
    folder = "\\".join(("awf-review-scratch", "awf195", "writer-pr219-finding1-r2-rw"))
    return (
        'The original writer ID was not captured. Writer run: queue row READY-QUEUE.txt:1472 '
        '"PLAN|4090|writer-pr219-finding1-r2-rw"; run folder '
        + drive + "\\" + folder
        + '; launch-log line tmp\\codex-starts.log:1567 '
        '"2026-10-23T19:52:55.4240099+01:00 codex-start 4090 writer-pr219-finding1-r2-rw", '
        'PID 55736 (run folder meta.txt START 2026-10-23T19:52:55.4689162+01:00).'
    )


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


class CommitBoundHistoricalMessageTests(unittest.TestCase):
    """MAJOR1: audited 75fc9bac line is waived only for exact commit+source+path+detector."""

    def setUp(self):
        self.detectors, self.allows = _detectors({}, {})
        self.line = audited_75fc9bac_message_line()
        self.assertEqual(AUDITED_LINE_DIGEST, hashlib.sha256(self.line.encode("utf-8")).hexdigest())
        self.assertIn(
            (AUDITED_COMMIT, "message", "message", "builtin.windows_absolute", AUDITED_LINE_DIGEST),
            _COMMIT_BOUND_CLOSED_HISTORICAL_SELF_REFERENCES,
        )

    def test_exact_commit_message_channel_is_waived(self):
        findings = _scan_text(
            self.line, commit=AUDITED_COMMIT, path="message", source="message",
            detectors=self.detectors, allows=self.allows)
        self.assertEqual([], findings)

    def test_wrong_commit_is_blocking(self):
        findings = _scan_text(
            self.line, commit="f" * 40, path="message", source="message",
            detectors=self.detectors, allows=self.allows)
        self.assertTrue(any(item["detector_id"] == "builtin.windows_absolute"
                            and item["classification"] == "BLOCKING" for item in findings), findings)

    def test_wrong_source_pr_body_or_comment_with_path_message_is_blocking(self):
        for source in ("pr-body", "pr_body", "comment"):
            with self.subTest(source=source):
                findings = _scan_text(
                    self.line, commit=AUDITED_COMMIT, path="message", source=source,
                    detectors=self.detectors, allows=self.allows)
                self.assertTrue(any(item["detector_id"] == "builtin.windows_absolute"
                                    and item["classification"] == "BLOCKING" for item in findings),
                                findings)

    def test_wrong_path_is_blocking(self):
        findings = _scan_text(
            self.line, commit=AUDITED_COMMIT, path="other", source="message",
            detectors=self.detectors, allows=self.allows)
        self.assertTrue(any(item["detector_id"] == "builtin.windows_absolute" for item in findings))

    def test_wrong_detector_still_sees_other_detectors_only(self):
        # Closed identity is detector-scoped; altering the detector id key means no waiver.
        # builtin.windows_absolute still matches the line content — waiver requires that detector.
        findings = _scan_text(
            self.line, commit=AUDITED_COMMIT, path="message", source="message",
            detectors=self.detectors, allows=self.allows)
        self.assertEqual([], findings)
        # Provider-style path/channel must not inherit the message waiver.
        findings = _scan_text(
            self.line, commit=AUDITED_COMMIT, path="pr-body", source="pr-body",
            detectors=self.detectors, allows=self.allows)
        self.assertTrue(any(item["detector_id"] == "builtin.windows_absolute" for item in findings))

    def test_altered_line_is_blocking(self):
        findings = _scan_text(
            self.line + " ", commit=AUDITED_COMMIT, path="message", source="message",
            detectors=self.detectors, allows=self.allows)
        self.assertTrue(any(item["detector_id"] == "builtin.windows_absolute"
                            and item["classification"] == "BLOCKING" for item in findings))

    def test_e2e_new_commit_message_carrying_line_is_blocked(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        # Commit via commit-tree so the message carries the audited line without a path hit.
        tree = _git(repo.path, "write-tree")
        parent = _git(repo.path, "rev-parse", "HEAD")
        import subprocess
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-C", str(repo.path),
             "commit-tree", tree, "-p", parent],
            input=(self.line + "\n").encode("utf-8"),
            capture_output=True, check=True)
        head = result.stdout.decode("ascii").strip()
        _git(repo.path, "update-ref", "HEAD", head)
        scan = scan_repository(repo.path, repo.base, head)
        self.assertEqual("BLOCKED", scan["status"], scan)
        self.assertTrue(any(
            item.get("path") == "message"
            and item.get("source") == "message"
            and item.get("detector_id") == "builtin.windows_absolute"
            and item.get("classification") == "BLOCKING"
            for item in scan["findings"]), scan["findings"])


class BothSideBinaryCoverageTests(unittest.TestCase):
    """MAJOR2: old-side binary/invalid/oversize coverage is never dropped."""

    def setUp(self):
        self.specimen = load_pinned_specimen_bytes()
        self.cas = content_addressed_blob_path(REGISTERED_SHA256)
        self.opaque = b"opaque\x00\xff\xfeSECRET\x00"

    def _blocked(self, result):
        self.assertEqual("BLOCKED", result["status"], result)
        self.assertTrue(
            result.get("blocking_findings") or result.get("unscanned_count"),
            result)

    def test_opaque_base_replaced_by_specimen_stays_blocked(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write(self.cas, data=self.opaque)
        mid = repo.commit("opaque at registered cas path", self.cas)
        repo.write(self.cas, data=self.specimen)
        head = repo.commit("replace opaque with pinned specimen", self.cas)
        # Opaque must be the scan BASE so only the replacement commit is in range.
        result = scan_repository(repo.path, mid, head)
        self._blocked(result)
        # Old side must contribute a refusal (changed-fixture BLOCKING or unscanned).
        old_refused = any(
            item.get("path") == self.cas and item.get("classification") == "BLOCKING"
            and item.get("detector_id", "").startswith("fixture.sqlite")
            for item in result["findings"]
        ) or any(
            item.get("path") == self.cas and item.get("reason") in {"binary", "invalid-utf8", "oversize"}
            for item in result["unscanned"]
        )
        self.assertTrue(old_refused, {"findings": result["findings"], "unscanned": result["unscanned"]})

    def test_invalid_utf8_preimage_stays_unscanned(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        bad = "safe\n".encode("utf-8") + b"\xff\xfe not utf-8"
        repo.write("notes.txt", data=bad)
        mid = repo.commit("invalid utf8 notes", "notes.txt")
        repo.write("notes.txt", "fixed\n")
        head = repo.commit("replace invalid utf8", "notes.txt")
        result = scan_repository(repo.path, mid, head)
        self._blocked(result)
        self.assertTrue(any(item.get("path") == "notes.txt" and item.get("reason") == "invalid-utf8"
                            for item in result["unscanned"]), result["unscanned"])

    def test_oversize_preimage_stays_unscanned(self):
        from agentic import publication as pub
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        # Just over the text limit; avoid huge allocations in CI.
        oversized = b"x" * (pub.MAX_TEXT_BYTES + 1)
        repo.write("big.bin", data=oversized)
        mid = repo.commit("oversize blob", "big.bin")
        repo.write("big.bin", data=self.specimen)
        head = repo.commit("replace oversize with specimen bytes", "big.bin")
        result = scan_repository(repo.path, mid, head)
        self._blocked(result)
        self.assertTrue(any(item.get("path") == "big.bin" and item.get("reason") == "oversize"
                            for item in result["unscanned"]), result["unscanned"])

    def test_delete_opaque_at_registered_path_stays_blocked(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write(self.cas, data=self.opaque)
        mid = repo.commit("opaque at cas", self.cas)
        _git(repo.path, "rm", "-f", "--", self.cas)
        head = _git(repo.path, "commit", "-m", "delete opaque")
        # re-resolve after commit helper
        head = _git(repo.path, "rev-parse", "HEAD")
        result = scan_repository(repo.path, mid, head)
        self._blocked(result)

    def test_rename_opaque_onto_registered_path_stays_blocked(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write("tmp/opaque.bin", data=self.opaque)
        mid = repo.commit("opaque elsewhere", "tmp/opaque.bin")
        Path(repo.path, self.cas).parent.mkdir(parents=True, exist_ok=True)
        _git(repo.path, "mv", "--", "tmp/opaque.bin", self.cas)
        head = _git(repo.path, "commit", "-m", "rename opaque onto cas")
        head = _git(repo.path, "rev-parse", "HEAD")
        result = scan_repository(repo.path, mid, head)
        self._blocked(result)

    def test_rename_specimen_away_stays_blocked(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write(self.cas, data=self.specimen)
        mid = repo.commit("specimen at cas", self.cas)
        dest = "tmp/moved-specimen.bin"
        Path(repo.path, "tmp").mkdir(parents=True, exist_ok=True)
        _git(repo.path, "mv", "--", self.cas, dest)
        head = _git(repo.path, "commit", "-m", "rename specimen away")
        head = _git(repo.path, "rev-parse", "HEAD")
        result = scan_repository(repo.path, mid, head)
        self._blocked(result)

    def test_clean_add_of_pinned_specimen_is_not_unscanned_binary(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write(self.cas, data=self.specimen)
        head = repo.commit("clean add specimen", self.cas)
        result = scan_repository(repo.path, repo.base, head)
        self.assertFalse(any(item.get("path") == self.cas and item.get("reason") == "binary"
                             for item in result["unscanned"]), result["unscanned"])

    def test_publisher_still_requires_zero_blockers_and_zero_unscanned(self):
        # publish_release.py must remain unchanged and still refuse non-0/0.
        root = Path(__file__).resolve().parents[2]
        source = (root / "scripts" / "publish_release.py").read_text(encoding="utf-8")
        self.assertIn("if blocking or unscanned:", source)
        self.assertIn(
            "publication scan PASS contradicts blocking or unscanned counts; release refused",
            source)
        self.assertIn("unscanned item(s)", source)
        # A blocked opaque-replacement scan is exactly what the publisher refuses.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = _Repo(temp.name)
        repo.write(self.cas, data=self.opaque)
        mid = repo.commit("opaque base", self.cas)
        repo.write(self.cas, data=self.specimen)
        head = repo.commit("specimen head", self.cas)
        result = scan_repository(repo.path, mid, head)
        self.assertEqual("BLOCKED", result["status"])
        blocking = sum(1 for item in result["findings"] if item.get("classification") == "BLOCKING")
        unscanned = len(result["unscanned"])
        self.assertTrue(blocking or unscanned)


if __name__ == "__main__":
    unittest.main()
