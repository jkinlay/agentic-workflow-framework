"""Interim refusal must precede pathname traversal, including replacement races."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError, installer, adoption_config
from agentic.safeio import Tree


class CleanupStopgapTests(unittest.TestCase):
    def test_replacement_at_recursive_delete_is_never_traversed(self):
        for module, name in ((installer, '_remove_runtime_transaction_path'),
                             (adoption_config, '_remove_runtime_tree')):
            with self.subTest(helper=name), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'runtime'
                path.mkdir()
                (path / 'original').write_bytes(b'original')
                native = shutil.rmtree

                def replace_and_delete(target, *args, **kwargs):
                    target.rename(target.with_name('original-runtime'))
                    target.mkdir()
                    (target / 'owner-data').write_bytes(b'owner replacement')
                    native(target, *args, **kwargs)

                with patch.object(shutil, 'rmtree', side_effect=replace_and_delete) as remove:
                    with self.assertRaisesRegex(ValidationError, 'cleanup pending'):
                        getattr(module, name)(path)
                    remove.assert_not_called()
                self.assertEqual(b'original', (path / 'original').read_bytes())

    def test_absent_runtime_needs_no_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            absent = Path(temporary) / 'absent'
            installer._remove_runtime_transaction_path(absent)
            adoption_config._remove_runtime_tree(absent)

    def journal(self, root, *, previous):
        transaction_id = str(uuid.uuid4())
        runtime = installer._runtime_journal(root, transaction_id)
        runtime['previous_sha256'] = 'a' * 64 if previous else None
        runtime['new_sha256'] = 'b' * 64
        return dict(format='awf-install-journal-3', transaction_id=transaction_id,
                    phase='commit_cleanup', files=[], runtime=runtime,
                    managed_before=[], managed_after=[])

    def test_ordinary_committed_upgrade_retains_backup_and_durable_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal = self.journal(root, previous=True)
            backup = root / journal['runtime']['backup']
            backup.mkdir(parents=True)
            (backup / 'prior-runtime').write_bytes(b'owner backup')
            journal['runtime']['previous_sha256'] = installer._runtime_tree_sha256(backup)
            with Tree(root) as tree, patch.object(shutil, 'rmtree') as remove:
                rows = installer._cleanup_committed_runtime(tree, journal)
                self.assertEqual(12, rows[0]['size_bytes'])
                self.assertEqual(journal['runtime']['backup'], rows[0]['path'])
                self.assertIn('owner', rows[0]['owner_action'])
                self.assertEqual('commit_cleanup', journal['phase'])
                self.assertEqual(rows, installer._cleanup_committed_runtime(tree, journal))
                remove.assert_not_called()
            self.assertEqual(b'owner backup', (backup / 'prior-runtime').read_bytes())
            first = (root / installer.RETAINED_JOURNAL).read_bytes()
            result = installer.retained_directory_status(root)
            self.assertEqual('COMMITTED_CLEANUP_PENDING', result['status'])
            self.assertEqual(rows, result['cleanup_pending'])
            self.assertEqual(first, (root / installer.RETAINED_JOURNAL).read_bytes())
            from agentic.providers.github_status import render_status
            rendered = render_status(dict(line='Pending', installation_recovery=result))
            self.assertIn(rows[0]['path'], rendered)
            self.assertIn('12 bytes', rendered)
            self.assertIn(rows[0]['owner_action'], rendered)

    def test_fresh_commit_without_backup_still_completes_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal = self.journal(root, previous=False)
            with Tree(root) as tree, patch.object(installer, '_write_bound_journal_update'):
                self.assertEqual([], installer._cleanup_committed_runtime(tree, journal))
            self.assertEqual('commit_cleanup_authenticated', journal['phase'])
            self.assertFalse((root / installer.RETAINED_JOURNAL).exists())

    def test_pre_stopgap_authenticated_cleanup_already_absent_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal = self.journal(root, previous=True)
            journal['phase'] = 'commit_cleanup_authenticated'
            journal['runtime'].update(cleanup_identity=['0' * 16, '0' * 16], cleanup_members=[])
            with Tree(root) as tree, patch.object(shutil, 'rmtree') as remove:
                self.assertEqual([], installer._cleanup_committed_runtime(tree, journal))
                remove.assert_not_called()
            self.assertFalse((root / installer.RETAINED_JOURNAL).exists())

    def test_replacement_after_absent_cleanup_observation_is_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal = self.journal(root, previous=True)
            journal['phase'] = 'commit_cleanup_authenticated'
            journal['runtime'].update(cleanup_identity=['0' * 16, '0' * 16], cleanup_members=[])
            native = installer._remove_runtime_transaction_path

            def replace(path):
                path.mkdir(parents=True)
                (path / 'owner-data').write_bytes(b'keep')
                native(path)

            with Tree(root) as tree, patch.object(installer, '_remove_runtime_transaction_path', replace), \
                    patch.object(shutil, 'rmtree') as remove:
                rows = installer._cleanup_committed_runtime(tree, journal)
                self.assertEqual(4, rows[0]['size_bytes'])
                self.assertEqual(b'keep', (root / rows[0]['path'] / 'owner-data').read_bytes())
                remove.assert_not_called()

    def test_rollback_refuses_before_any_managed_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal = self.journal(root, previous=True)
            journal['phase'] = 'active'
            stage = root / journal['runtime']['stage']
            stage.mkdir(parents=True)
            (stage / 'new-runtime').write_bytes(b'new runtime')
            journal['runtime']['new_sha256'] = installer._runtime_tree_sha256(stage)
            managed = root / '.agentic/PROJECT_CONFIG.yaml'
            managed.write_bytes(b'unchanged pending bytes')
            with Tree(root) as tree, patch.object(installer, '_load_recovery_objects') as load, \
                    patch.object(installer, '_cleanup_transaction_temporaries') as clean, \
                    patch.object(shutil, 'rmtree') as remove:
                with self.assertRaisesRegex(ValidationError, 'before managed-file mutation'):
                    installer.rollback(tree, journal)
                load.assert_not_called()
                clean.assert_not_called()
                remove.assert_not_called()
            self.assertEqual(b'unchanged pending bytes', managed.read_bytes())
            self.assertEqual(b'new runtime', (stage / 'new-runtime').read_bytes())
            self.assertEqual('RECOVERY_REFUSED', installer.retained_directory_status(root)['status'])

    def test_size_enumeration_failure_is_unknown_and_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'retained'
            path.mkdir()
            with Tree(root) as tree, patch.object(installer.os, 'walk', side_effect=PermissionError):
                rows = installer._record_cleanup_pending(tree, [path], status='RECOVERY_REFUSED')
            self.assertIsNone(rows[0]['size_bytes'])
            self.assertTrue(path.is_dir())

    def build_standalone(self, root, *, error=None):
        from contextlib import ExitStack
        from agentic import runtime_commands
        runtime = root / '.agentic/.venv'
        entry = root / '.agentic/scripts/workflow.py'
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b'entry')
        interpreter = runtime / 'python'

        def create(stage):
            Path(stage).mkdir()
            (Path(stage) / 'python').write_bytes(b'new runtime')
            if error:
                raise error

        dependencies = {key: {'name': key, 'version': '1'} for key in adoption_config._RUNTIME_IMPORTS}
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime_commands, 'installed_paths',
                                             return_value=(root, interpreter, entry)))
            stack.enter_context(patch.object(adoption_config, '_locked_dependencies',
                                             return_value=(dependencies, 'a' * 64)))
            stack.enter_context(patch.object(adoption_config, '_load_locked_wheels', return_value={}))
            stack.enter_context(patch.object(adoption_config.venv.EnvBuilder, 'create', side_effect=create))
            stack.enter_context(patch.object(adoption_config, '_runtime_interpreter',
                                             side_effect=lambda path: path / 'python'))
            stack.enter_context(patch.object(adoption_config, '_runtime_site_packages',
                                             side_effect=lambda _interpreter, _root, stage: stage / 'site'))
            stack.enter_context(patch.object(adoption_config, '_extract_locked_wheels', return_value={}))
            stack.enter_context(patch.object(adoption_config, '_validate_runtime'))
            remove = stack.enter_context(patch.object(shutil, 'rmtree'))
            try:
                return adoption_config.ensure_installed_runtime(root)
            finally:
                remove.assert_not_called()

    def test_standalone_finally_never_retries_or_masks_original_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            failure = OSError('original staging failure')
            with self.assertRaisesRegex(OSError, 'original staging failure') as raised:
                self.build_standalone(root, error=failure)
            self.assertIs(failure, raised.exception)
            result = installer.retained_directory_status(root)
            self.assertEqual('RECOVERY_REFUSED', result['status'])
            self.assertEqual(1, len(result['retained_directories']))
            self.assertTrue((root / result['retained_directories'][0] / 'python').is_file())

    def test_standalone_fresh_success_and_ordinary_upgrade_retention(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fresh = self.build_standalone(root)
            self.assertEqual('COMPLETE', fresh['transaction_cleanup'])
            self.assertIsNone(installer.retained_directory_status(root))
            marker = root / '.agentic/.venv/owner-marker'
            marker.write_bytes(b'previous runtime')
            upgrade = self.build_standalone(root)
            self.assertEqual('COMMITTED_CLEANUP_PENDING', upgrade['transaction_cleanup'])
            rows = upgrade['transaction_cleanup_failures']
            self.assertEqual(b'previous runtime', (root / rows[0]['path'] / 'owner-marker').read_bytes())
            self.assertFalse(marker.exists())
            again = self.build_standalone(root)
            self.assertEqual('COMMITTED_CLEANUP_PENDING', again['transaction_cleanup'])
            retained = installer.retained_directory_status(root)
            self.assertEqual(2, len(retained['retained_directories']))
            self.assertEqual(b'previous runtime', (root / rows[0]['path'] / 'owner-marker').read_bytes())


if __name__ == '__main__':
    unittest.main()
