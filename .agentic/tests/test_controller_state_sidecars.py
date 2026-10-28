"""AWF-201: SQLite sidecar races must not hide ledger permission failures."""
from pathlib import Path
import errno
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import controller_state


class ControllerStateSidecarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf201-sidecars-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.sqlite3"
        self.path.touch()
        self.sidecars = [Path(str(self.path) + suffix)
                         for suffix in ("-wal", "-shm", "-journal")]
        for sidecar in self.sidecars:
            sidecar.touch()

    @staticmethod
    def windows_error(code):
        error = PermissionError(errno.EACCES, "fixture sidecar is in use")
        error.winerror = code
        return error

    def restrict(self, platform, chmod):
        # Replace this module's OS reference only; pathlib keeps the real host OS.
        with patch.object(controller_state, "os",
                          SimpleNamespace(name=platform, chmod=chmod)):
            controller_state.restrict_state_permissions(self.path)

    def test_existing_database_and_all_sidecars_get_owner_read_write(self):
        calls = []
        self.restrict("nt", lambda path, mode: calls.append((path, mode)))
        self.assertEqual([(path, stat.S_IRUSR | stat.S_IWUSR)
                          for path in (self.path, *self.sidecars)], calls)

    def test_missing_sidecars_are_optional(self):
        for sidecar in self.sidecars:
            sidecar.unlink()
        calls = []
        self.restrict("nt", lambda path, mode: calls.append(path))
        self.assertEqual([self.path], calls)

    def test_sidecar_vanishing_between_exists_and_chmod_is_tolerated(self):
        for platform in ("nt", "posix"):
            for selected in self.sidecars:
                with self.subTest(platform=platform, suffix=selected.suffix,
                                  sidecar=selected.name):
                    calls = []
                    def chmod(path, mode):
                        calls.append(path)
                        if path == selected:
                            raise FileNotFoundError(errno.ENOENT, "sidecar vanished")
                    self.restrict(platform, chmod)
                    self.assertEqual([self.path, *self.sidecars], calls)

    def test_windows_in_use_sidecars_do_not_prevent_other_permissions(self):
        for code in (5, 32, 33):
            for selected in self.sidecars:
                with self.subTest(winerror=code, sidecar=selected.name):
                    calls = []
                    def chmod(path, mode):
                        calls.append(path)
                        if path == selected:
                            raise self.windows_error(code)
                    self.restrict("nt", chmod)
                    self.assertEqual([self.path, *self.sidecars], calls)

    def test_main_database_permission_and_disappearance_errors_propagate(self):
        errors = [self.windows_error(code) for code in (5, 32, 33)]
        errors.append(FileNotFoundError(errno.ENOENT, "database vanished"))
        for error in errors:
            with self.subTest(error=repr(error)):
                def chmod(path, mode):
                    if path == self.path:
                        raise error
                with self.assertRaises(type(error)) as caught:
                    self.restrict("nt", chmod)
                self.assertIs(error, caught.exception)

    def test_posix_sidecar_permission_errors_remain_fatal(self):
        for selected in self.sidecars:
            error = self.windows_error(5)
            def chmod(path, mode):
                if path == selected:
                    raise error
            with self.subTest(sidecar=selected.name), self.assertRaises(PermissionError) as caught:
                self.restrict("posix", chmod)
            self.assertIs(error, caught.exception)

    def test_unrelated_windows_sidecar_errors_remain_fatal(self):
        errors = [self.windows_error(87),
                  PermissionError(errno.EACCES, "no Windows error identity"),
                  OSError(errno.EIO, "fixture I/O failure")]
        for error in errors:
            def chmod(path, mode):
                if path == self.sidecars[0]:
                    raise error
            with self.subTest(error=repr(error)), self.assertRaises(type(error)) as caught:
                self.restrict("nt", chmod)
            self.assertIs(error, caught.exception)


if __name__ == "__main__":
    unittest.main()
