"""Archive destinations follow the selected release and retain every prior archive."""
import json
from pathlib import Path
import sys
import unittest
import os
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError
from agentic.upgrade import state_archive_plan


class ReleaseArchiveTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows creating handle pins identity')
    def test_creating_handle_denies_replacement_through_identity_capture(self):
        import agentic.installer as installer
        from agentic.safeio import Tree
        with tempfile.TemporaryDirectory() as temporary, Tree(temporary) as tree:
            root = Path(temporary)
            original = installer._windows_directory_identity
            attempts = []

            def try_replace(handle):
                with self.assertRaises(OSError):
                    (root / 'created').rename(root / 'displaced')
                attempts.append(True)
                return original(handle)

            row = {'path': 'created', 'identity': None}
            with patch.object(installer, '_windows_directory_identity', side_effect=try_replace), \
                    patch.object(installer, '_write_bound_journal_update'):
                installer._prepare_transaction_directories(tree, {'created_directories': [row]})
            self.assertEqual([True], attempts)
            self.assertEqual(installer._directory_identity(tree, 'created'), row['identity'])
            installer._cleanup_transaction_directories(tree, {'created_directories': [row]})
            self.assertFalse((root / 'created').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows directory creation ownership')
    def test_creation_does_not_authorize_a_replacement_before_identity_capture(self):
        import agentic.installer as installer
        from agentic.safeio import Tree
        with tempfile.TemporaryDirectory() as temporary, Tree(temporary) as tree:
            root = Path(temporary)
            (root / 'owner').mkdir()
            original = installer._directory_identity
            replaced = []

            def swap_before_capture(tree, relative):
                if relative == 'created' and not replaced:
                    (root / 'created').rename(root / 'displaced')
                    (root / 'owner').rename(root / 'created')
                    replaced.append(True)
                return original(tree, relative)

            row = {'path': 'created', 'identity': None}
            with patch.object(installer, '_directory_identity', side_effect=swap_before_capture), \
                    patch.object(installer, '_write_bound_journal_update'):
                installer._prepare_transaction_directories(tree, {'created_directories': [row]})
            if not replaced:
                tree.close()
                (root / 'created').rename(root / 'displaced')
                (root / 'owner').rename(root / 'created')
            with self.assertRaises(ValidationError):
                installer._cleanup_transaction_directories(tree, {'created_directories': [row]})
            self.assertTrue((root / 'created').is_dir())
            self.assertTrue((root / 'displaced').is_dir())

    @unittest.skipIf(os.name == 'nt', 'POSIX pathname deletion has no identity condition')
    def test_posix_cleanup_refuses_pathname_race_without_deleting(self):
        import agentic.installer as installer
        from agentic.safeio import Tree
        with tempfile.TemporaryDirectory() as temporary, Tree(temporary) as tree:
            root = Path(temporary)
            (root / 'created').mkdir()
            row = {'path': 'created', 'identity': installer._directory_identity(tree, 'created')}
            with patch.object(installer.os, 'rmdir') as deletion:
                with self.assertRaisesRegex(ValidationError, 'cannot prove'):
                    installer._cleanup_transaction_directories(tree, {'created_directories': [row]})
            deletion.assert_not_called()
            self.assertTrue((root / 'created').is_dir())

    def test_selected_target_and_all_prior_archives_are_preserved(self):
        state = {'.agentic-state/note.txt': b'synthetic owner note\n',
                 '.agentic-state/archive/upgrade-to-1.9.3/files/old.txt': b'old',
                 '.agentic-state/archive/owner-backup/record': b'owner'}
        before = dict(state)
        additions, actions = state_archive_plan(state, target_version='1.9.5')
        self.assertEqual(before, state)
        self.assertEqual(1, len(actions))
        state.update(additions)
        next_additions, next_actions = state_archive_plan(state, target_version='1.9.6')
        self.assertEqual(1, len(next_actions))
        self.assertFalse(any('/files/archive/' in path for path in next_additions))
        manifest = json.loads(next_additions['.agentic-state/archive/upgrade-to-1.9.6/manifest.json'])
        self.assertEqual('1.9.6', manifest['target_version'])
        self.assertEqual(b'synthetic owner note\n', next_additions[manifest['files'][0]['archive']])
        self.assertEqual(b'old', state['.agentic-state/archive/upgrade-to-1.9.3/files/old.txt'])

    def test_invalid_target_refuses_without_mutation(self):
        state = {'.agentic-state/note': b'owner'}
        for target in ('../escape', '1.9.6/elsewhere', None):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                state_archive_plan(state, target_version=target)
        self.assertEqual({'.agentic-state/note': b'owner'}, state)


if __name__ == '__main__':
    unittest.main()
