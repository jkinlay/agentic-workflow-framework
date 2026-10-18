"""Canonical load bounds regressions and supplementary parser compatibility."""
from __future__ import annotations

from contextlib import contextmanager
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError, canonical, safeio


@contextmanager
def observed_reads(*, growth=False, final_size_mismatch=False):
    """Observe real streams; inject growth only at the descriptor metadata edge."""
    requests = []
    read_descriptors = set()
    original_open, original_fdopen, original_fstat = io.open, os.fdopen, os.fstat

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            requests.append(size)
            data = self.stream.read(size)
            read_descriptors.add(self.fileno())
            return data

    def metadata(fd):
        info = original_fstat(fd)
        if growth or final_size_mismatch:
            # Growth changes before/after identity. The second mode keeps
            # identity stable but disagrees with the returned byte count.
            size = info.st_size + int(final_size_mismatch or fd in read_descriptors)
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_size=size, st_mtime_ns=info.st_mtime_ns,
                                   st_nlink=info.st_nlink, st_mode=info.st_mode)
        return info

    with patch.object(io, "open", side_effect=lambda *a, **k: Stream(original_open(*a, **k))), \
            patch.object(safeio.os, "fdopen", side_effect=lambda *a, **k: Stream(original_fdopen(*a, **k))), \
            patch.object(safeio.os, "fstat", side_effect=metadata):
        yield requests


class CanonicalLoadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def document(self, raw, suffix=".json"):
        path = self.root / ("document" + suffix)
        path.write_bytes(raw)
        return path

    def assert_bounded(self, requests):
        self.assertTrue(requests)
        self.assertTrue(all(0 <= size <= canonical.MAX_DOCUMENT_BYTES + 1
                            for size in requests), requests)

    def test_canonical_load_refuses_oversize_before_whole_file_read(self):
        path = self.document(b" " * (8_388_608 + 1))
        with observed_reads() as requests:
            with self.assertRaises(ValidationError):
                canonical.load(path)
        self.assertEqual(requests, [], "stat-known oversize must cause zero payload reads")

    def test_canonical_load_rejects_growth_during_read(self):
        path = self.document(b'{"valid":true}')
        with observed_reads(growth=True) as requests:
            with self.assertRaisesRegex(ValidationError, "changed during bounded read"):
                canonical.load(path)
        self.assert_bounded(requests)

    def test_supplementary_final_size_must_match_returned_bytes(self):
        path = self.document(b"42")
        with observed_reads(growth=True, final_size_mismatch=True) as requests:
            with self.assertRaisesRegex(ValidationError, "changed during bounded read"):
                canonical.load(path)
        self.assert_bounded(requests)

    def test_supplementary_inclusive_json_and_yaml_size_limit(self):
        self.assertEqual(canonical.MAX_DOCUMENT_BYTES, 8_388_608)
        for suffix in (".json", ".yaml"):
            for length in (8_388_608, 8_388_609):
                with self.subTest(suffix=suffix, length=length):
                    path = self.document(b"0" + b" " * (length - 1), suffix)
                    if length == 8_388_608:
                        self.assertEqual(canonical.load(path), 0)
                    else:
                        with self.assertRaises(ValidationError):
                            canonical.load(path)

    def test_supplementary_scalar_roots_and_unicode(self):
        for raw, expected in ((b"null", None), (b"true", True), (b"42", 42),
                              ('"caf\u00e9"'.encode(), "caf\u00e9"), (b"[]", [])):
            for suffix in (".json", ".yaml"):
                with self.subTest(raw=raw, suffix=suffix):
                    self.assertEqual(canonical.load(self.document(raw, suffix)), expected)

    def test_supplementary_invalid_values_are_refused(self):
        for raw in (b'{"key":1,"key":2}', b"1.5", b"NaN", b"9007199254740992",
                    b'"\\ud800"', b"[" * 65 + b"0" + b"]" * 65):
            for suffix in (".json", ".yaml"):
                with self.subTest(raw=raw, suffix=suffix):
                    with self.assertRaises(ValidationError):
                        canonical.load(self.document(
                            b".nan" if raw == b"NaN" and suffix == ".yaml" else raw, suffix))

    def test_supplementary_nesting_boundary(self):
        for suffix in (".json", ".yaml"):
            with self.subTest(suffix=suffix):
                self.assertIsInstance(canonical.load(self.document(
                    b"[" * 64 + b"0" + b"]" * 64, suffix)), list)

    def test_supplementary_suffix_dispatch_and_yaml_restrictions(self):
        self.assertEqual(canonical.load(self.document(b'{"key":1}', ".JSON")), {"key": 1})
        self.assertEqual(canonical.load(self.document(b"key: 1", ".other")), {"key": 1})
        with self.assertRaises(ValidationError):
            canonical.load(self.document(b"key: 1", ".json"))
        for raw in (b"key: 1\nkey: 2", b"a: &value 1\nb: *value", b"1: value"):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                canonical.load(self.document(raw, ".yaml"))


if __name__ == "__main__":
    unittest.main()
