"""Regressions for native Windows pinning errors and their callers."""
from contextlib import redirect_stderr
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import safeio


@unittest.skipUnless(os.name == "nt", "Windows native error translation")
class WindowsOpenErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf-safeio-errors-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def assert_native_error(self, error, path, code, exception_type, number):
        self.assertEqual(error.filename, str(path))
        self.assertIsInstance(error, exception_type)
        self.assertEqual(error.errno, number)
        self.assertEqual(error.winerror, code)

    def test_path_not_found_is_file_not_found(self):
        path = self.root / "absent"
        with patch.object(safeio.kernel, "CreateFileW", return_value=safeio.INVALID_HANDLE), \
                patch.object(safeio.ctypes, "get_last_error", return_value=3):
            with self.assertRaises(OSError) as caught:
                safeio.win_open(path, directory=True)
        self.assertIsInstance(caught.exception, FileNotFoundError)
        self.assert_native_error(caught.exception, path, 3, FileNotFoundError, errno.ENOENT)

    def test_inspect_treats_path_not_found_as_absent(self):
        path = self.root / "absent"
        original_open = safeio.kernel.CreateFileW

        def open_path(name, *args):
            if name == str(path):
                return safeio.INVALID_HANDLE
            return original_open(name, *args)

        with safeio.Tree(self.root) as tree:
            with patch.object(safeio.kernel, "CreateFileW", side_effect=open_path), \
                    patch.object(safeio.ctypes, "get_last_error", return_value=3):
                self.assertIsNone(tree.inspect("absent/journal.json"))

    def test_missing_leaf_preserves_filename(self):
        path = self.root / "missing.json"
        with patch.object(safeio.kernel, "CreateFileW", return_value=safeio.INVALID_HANDLE), \
                patch.object(safeio.ctypes, "get_last_error", return_value=2):
            with self.assertRaises(OSError) as caught:
                safeio.win_open(path)
        self.assert_native_error(caught.exception, path, 2, FileNotFoundError, errno.ENOENT)

    def test_snapshot_preserves_missing_leaf_filename(self):
        path = self.root / "missing.json"
        original_open = safeio.kernel.CreateFileW

        def open_path(name, *args):
            if name == str(path):
                return safeio.INVALID_HANDLE
            return original_open(name, *args)

        with safeio.Tree(self.root) as tree:
            with patch.object(safeio.kernel, "CreateFileW", side_effect=open_path), \
                    patch.object(safeio.ctypes, "get_last_error", return_value=2):
                with self.assertRaises(OSError) as caught:
                    tree.read("missing.json")
        self.assert_native_error(caught.exception, path, 2, FileNotFoundError, errno.ENOENT)

    def test_access_denied_preserves_native_exception_fields(self):
        path = self.root / "denied.json"
        with patch.object(safeio.kernel, "CreateFileW", return_value=safeio.INVALID_HANDLE), \
                patch.object(safeio.ctypes, "get_last_error", return_value=5):
            with self.assertRaises(OSError) as caught:
                safeio.win_open(path)
        self.assert_native_error(caught.exception, path, 5, PermissionError, errno.EACCES)

    def test_access_denied_remains_cli_access_unavailable(self):
        from agentic import cli

        path = self.root / "denied.json"
        stderr = io.StringIO()
        with patch.object(cli, "verify_installed", side_effect=lambda root: safeio.win_open(path)), \
                patch.object(safeio.kernel, "CreateFileW", return_value=safeio.INVALID_HANDLE), \
                patch.object(safeio.ctypes, "get_last_error", return_value=5), \
                redirect_stderr(stderr):
            result = cli.main(["--root", str(self.root), "verify-installation"])
        report = json.loads(stderr.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(report["status"], "ACCESS_UNAVAILABLE")
        self.assertIs(report["execution_authority"], False)
        self.assertIn(path.name, report["reason"])
        self.assertEqual(report["diagnostic"]["os_error_code"], 5)
        self.assertEqual(report["diagnostic"]["error_type"], "PermissionError")

    def test_inspection_failure_preserves_error_before_close(self):
        path = self.root / "present.json"
        handle = 12345
        last_error = [5]
        events = []

        def get_error():
            events.append("get_last_error")
            return last_error[0]

        def close(value):
            events.append("close")
            last_error[0] = 6
            return 1

        with patch.object(safeio.kernel, "CreateFileW", return_value=handle), \
                patch.object(safeio.kernel, "GetFileInformationByHandle", return_value=0), \
                patch.object(safeio.ctypes, "get_last_error", side_effect=get_error), \
                patch.object(safeio.kernel, "CloseHandle", side_effect=close) as close_handle:
            with self.assertRaises(OSError) as caught:
                safeio.win_open(path)
        close_handle.assert_called_once_with(handle)
        self.assertEqual(events, ["get_last_error", "close"])
        self.assert_native_error(caught.exception, path, 5, PermissionError, errno.EACCES)

    def test_real_absent_parent_is_absent(self):
        path = self.root / "absent" / "leaf.json"
        with self.assertRaises(OSError) as caught:
            safeio.win_open(path)
        self.assertEqual(caught.exception.filename, str(path))
        self.assertIsInstance(caught.exception, FileNotFoundError)
        self.assertEqual(caught.exception.errno, errno.ENOENT)
        with safeio.Tree(self.root) as tree:
            self.assertIsNone(tree.inspect("absent/journal.json"))

    def test_real_missing_leaf_preserves_filename(self):
        path = self.root / "missing.json"
        with safeio.Tree(self.root) as tree:
            with self.assertRaises(OSError) as caught:
                tree.read("missing.json")
        self.assertEqual(caught.exception.filename, str(path))
        self.assertIsInstance(caught.exception, FileNotFoundError)
        self.assertEqual(caught.exception.errno, errno.ENOENT)


if __name__ == "__main__":
    unittest.main()
