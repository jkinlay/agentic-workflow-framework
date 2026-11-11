"""Formal round-one substitutions at the last metadata/cleanup boundary."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import safeio, ValidationError


@unittest.skipUnless(os.name == 'nt', 'native Windows sharing and ADS regression')
class WindowsProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / 'managed'
        self.target.write_bytes(b'old managed')
        Path(str(self.target) + ':receipt').write_bytes(b'managed stream')

    def test_creating_handle_pins_metadata_application_and_verification(self):
        owner = self.root / 'owner'
        owner.write_bytes(b'owner content')
        owner_stream = Path(str(owner) + ':receipt')
        owner_stream.write_bytes(b'OWNER STREAM')
        original = safeio._apply_windows_metadata
        events = []

        def substitute(path, value):
            try:
                path.rename(path.with_suffix('.retained'))
                os.link(owner, path)
                events.append('substituted')
            except PermissionError:
                events.append('pinned')
            return original(path, value)

        with safeio.Tree(self.root) as tree, patch.object(safeio, '_apply_windows_metadata', substitute):
            try:
                tree.write('managed', b'new managed')
            except ValidationError:
                pass
        self.assertEqual(b'OWNER STREAM', owner_stream.read_bytes())
        self.assertEqual(['pinned'], events)
        self.assertEqual(b'new managed', self.target.read_bytes())

    def test_preflight_never_recursively_deletes_a_replacement_directory(self):
        original = safeio._windows_metadata
        events = []
        replacement = []

        def captured(path, **kwargs):
            result = original(path, **kwargs)
            if path.name.startswith('awf-metadata-probe-'):
                parent = path
                try:
                    parent.rename(parent.with_name(parent.name + '-original'))
                    parent.mkdir()
                    (parent / 'probe').write_bytes(b'')
                    (parent / 'owner').write_bytes(b'OWNER DATA')
                    events.append('substituted')
                    replacement.append(parent / 'owner')
                except PermissionError:
                    events.append('pinned')
            return result

        with safeio.Tree(self.root) as tree, patch.object(safeio, '_windows_metadata', captured):
            tree.windows_metadata('managed', preflight=True)
        self.assertTrue(events)
        for path in replacement:
            self.assertTrue(path.exists(), 'preflight cleanup deleted substituted owner directory')
            self.assertEqual(b'OWNER DATA', path.read_bytes())
        self.assertEqual(b'old managed', self.target.read_bytes())

    def test_creating_handle_remains_pinned_during_metadata_verification(self):
        owner = self.root / 'owner'
        owner.write_bytes(b'owner content')
        original = safeio._windows_metadata
        events = []

        def captured(path, **kwargs):
            if path.name.startswith('.awf-'):
                try:
                    path.rename(path.with_suffix('.retained'))
                    os.link(owner, path)
                    events.append('substituted')
                except PermissionError:
                    events.append('pinned')
            return original(path, **kwargs)

        with safeio.Tree(self.root) as tree, patch.object(safeio, '_windows_metadata', captured):
            tree.write('managed', b'replacement')
        self.assertEqual(['pinned'], events)
        self.assertEqual(b'owner content', owner.read_bytes())


if __name__ == '__main__':
    unittest.main()
