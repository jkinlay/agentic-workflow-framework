"""POSIX unlink's documented quiescence boundary; private simulation on Windows."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError, safeio


class PosixUnlinkSemanticsTests(unittest.TestCase):
    def test_identity_check_rejects_prior_write_but_cannot_deny_a_final_racing_write(self):
        # POSIX open/unlink do not supply Windows share-deny semantics. Model
        # only dir_fd on Windows; content writes and unlink use real files.
        for timing in ('quiescent', 'before_check', 'after_check'):
            with self.subTest(timing=timing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / 'managed'
                target.write_bytes(b'journal-bound')
                tree = object.__new__(safeio.Tree)
                tree._authenticated = {'managed': safeio._file_identity(target.stat())}
                tree.parent = lambda relative: (root, 123, relative)
                tree.check_parents = lambda: None
                tree.entry_identity = lambda relative, **kwargs: safeio._file_identity(target.stat()) if target.exists() else None
                view = SimpleNamespace(**vars(os))
                view.name = 'posix'
                events = []
                def unlink(name, *, dir_fd):
                    self.assertEqual(('managed', 123), (name, dir_fd))
                    if timing == 'after_check':
                        target.write_bytes(b'OWNER LATE WRITE')
                        events.append(target.read_bytes())
                    os.unlink(target)
                view.unlink, view.fsync = unlink, lambda descriptor: None
                if timing == 'before_check':
                    target.write_bytes(b'OWNER LATE WRITE')
                with patch.object(safeio, 'os', view):
                    if timing == 'before_check':
                        with self.assertRaisesRegex(ValidationError, 'identity changed'):
                            tree.unlink('managed')
                        self.assertEqual(b'OWNER LATE WRITE', target.read_bytes())
                    else:
                        tree.unlink('managed')
                        self.assertFalse(target.exists())
                self.assertEqual([b'OWNER LATE WRITE'] if timing == 'after_check' else [], events)


if __name__ == '__main__':
    unittest.main()
