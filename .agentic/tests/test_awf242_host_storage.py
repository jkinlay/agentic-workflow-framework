"""Independent protected database containment remains mandatory."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import source_only
from agentic import ValidationError
from agentic.budget_enrollment import ProtectedDatabase
from agentic.path_containment import is_reparse_point


class HostStorageTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows junction detection')
    def test_junction_is_refused_without_path_is_junction(self):
        import _winapi
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / 'target'
            target.mkdir()
            junction = base / 'junction'
            _winapi.CreateJunction(str(target), str(junction))
            self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)
            # Exercise the lstat fallback even when pathlib has is_junction.
            with patch.object(Path, 'is_junction', None, create=True):
                self.assertTrue(is_reparse_point(junction))
                with self.assertRaisesRegex(ValidationError, 'link/reparse point'):
                    ProtectedDatabase(junction / 'state.sqlite').validate_path()
