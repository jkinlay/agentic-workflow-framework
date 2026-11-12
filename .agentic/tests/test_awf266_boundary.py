"""The owner-approved quiescent namespace boundary still rejects replacements."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401
from agentic import ValidationError
from agentic.safeio import Tree


class BoundaryTests(unittest.TestCase):
    def test_metadata_probe_does_not_authenticate_unrelated_temp_entries(self):
        if os.name != 'nt':
            # This probe is Windows-only; POSIX never enters its implementation.
            with Tree(Path(tempfile.gettempdir()).resolve(), authenticate_root=False) as tree:
                self.assertEqual({}, tree._authenticated)
            return
        with tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
            tree.write_exclusive('managed', b'awf')
            with patch('agentic.safeio.os.scandir',
                       side_effect=AssertionError('unrelated directory enumeration')):
                self.assertIsNotNone(tree.windows_metadata('managed', preflight=True))

    def test_named_limitation_final_window_requires_quiescence(self):
        # Demonstration, not a safety claim: a non-cooperating writer operating
        # after the final check is outside the owner-approved 1.9.6 boundary.
        with tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
            root = Path(temporary).resolve()
            tree.write_exclusive('source', b'awf')
            native = os.replace
            hits = []
            def race(source, destination, *args, **kwargs):
                (root / 'destination').write_bytes(b'late unrelated entry')
                hits.append(destination)
                return native(source, destination, *args, **kwargs)
            with patch.object(os, 'replace', race):
                tree.rename_checked('source', 'destination', tree._authenticated['source'])
            self.assertEqual(1, len(hits))
            self.assertEqual(b'awf', (root / 'destination').read_bytes())

    def test_generic_write_requires_authority(self):
        with tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
            owner = Path(temporary).resolve() / 'owner'
            owner.write_bytes(b'owner')
            with self.assertRaisesRegex(ValidationError, 'authority'):
                tree.write('owner', b'changed')
            self.assertEqual(b'owner', owner.read_bytes())

    def test_write_carries_creating_identity_until_retirement(self):
        with tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
            tree.write('managed', b'first', authority='transaction')
            tree.write('managed', b'second', authority='transaction')
            self.assertIn('managed', tree._authenticated)
            tree.unlink('managed')
            self.assertFalse((Path(temporary).resolve() / 'managed').exists())

    def test_unlink_missing_identity_refuses_current_occupant(self):
        with tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
            owner = Path(temporary).resolve() / 'owner'
            owner.write_bytes(b'owner')
            with self.assertRaisesRegex(ValidationError, 'identity.*missing'):
                tree.unlink('owner')
            self.assertEqual(b'owner', owner.read_bytes())

    def test_unlink_substitution_at_final_check_retains_both_objects(self):
        with tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
            tree.write_exclusive('managed', b'awf')
            path = Path(temporary).resolve() / 'managed'
            path.rename(path.with_name('original'))
            path.write_bytes(b'owner')
            with self.assertRaisesRegex(ValidationError, 'identity changed'):
                tree.unlink('managed')
            self.assertEqual(b'owner', path.read_bytes())
            self.assertEqual(b'awf', path.with_name('original').read_bytes())

    def test_rename_normal_and_final_check_substitutions(self):
        for target in (None, 'source', 'destination'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary, Tree(Path(temporary).resolve()) as tree:
                root = Path(temporary).resolve()
                (root / 'source').mkdir()
                identity = tree.entry_identity('source', directory=True)
                original = tree.check_entry
                injected = []
                def check(relative, expected, **kwargs):
                    if relative == target and not injected:
                        injected.append(relative)
                        if (root / relative).exists():
                            (root / relative).rename(root / 'original')
                        (root / relative).mkdir()
                        (root / relative / 'owner').write_bytes(b'owner')
                    return original(relative, expected, **kwargs)
                with patch.object(tree, 'check_entry', check):
                    if target:
                        with self.assertRaisesRegex(ValidationError, 'identity changed|unexpected occupancy'):
                            tree.rename_checked('source', 'destination', identity, directory=True)
                        self.assertEqual([target], injected)
                        self.assertEqual(b'owner', (root / target / 'owner').read_bytes())
                    else:
                        tree.rename_checked('source', 'destination', identity, directory=True)
                        self.assertFalse((root / 'source').exists())
                        self.assertTrue((root / 'destination').is_dir())


if __name__ == '__main__':
    unittest.main()
