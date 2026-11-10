"""Pre-F5 preservation refusals and durable unresolved-directory diagnostics."""
from contextlib import ExitStack
import json
from contextlib import nullcontext
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests')]
from agentic import ValidationError
from agentic import installer, safeio
import test_awf134_installer_journal as journal_tests

CapturedIntent = journal_tests.CapturedIntent


class TemporaryMetadataTests(unittest.TestCase):
    """Model POSIX descriptor operations on Windows too; never claim native proof."""

    def write(self, *, uid=101, gid=202, mode=0o2640, owner_error=None,
              mode_loss=False, owner_loss=False, override=None, identical=False):
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.fileno.return_value = 456
        state = dict(st_uid=101, st_gid=101, st_mode=stat.S_IFREG | 0o600)
        operating = Mock()
        operating.name = 'posix'
        for name in ('O_CREAT', 'O_EXCL', 'O_WRONLY', 'O_BINARY', 'O_NOFOLLOW'):
            setattr(operating, name, getattr(os, name, 0))
        operating.open.return_value = 456
        operating.fdopen.return_value = stream
        operating.fstat.side_effect = lambda fd: SimpleNamespace(**state)

        def chown(fd, owner, group):
            if owner_error:
                raise owner_error
            if not owner_loss:
                if owner != -1:
                    state['st_uid'] = owner
                state['st_gid'] = group

        operating.fchown.side_effect = chown
        operating.fchmod.side_effect = lambda fd, bits: state.update(
            st_mode=stat.S_IFREG | (bits & ~stat.S_ISGID if mode_loss else bits))
        tree = Mock()
        tree.inspect.return_value = SimpleNamespace(st_mode=stat.S_IFREG | mode,
                                                    st_uid=uid, st_gid=gid)
        tree.read.return_value = b'new' if identical else b'old'
        tree.parent.return_value = (Path('project'), 123, 'config')
        tree.write_temporary_names = {}
        tree.write_modes = {} if override is None else {'config': override}
        tree.write_ownership = {}
        with patch.object(safeio, 'os', operating):
            if owner_error or mode_loss or owner_loss:
                with self.assertRaises((ValidationError, OSError)):
                    safeio.Tree.write(tree, 'config', b'new')
                operating.replace.assert_not_called()
            else:
                safeio.Tree.write(tree, 'config', b'new')
        return operating, state

    def test_different_project_group_preserved_before_publication(self):
        operating, state = self.write(mode=0o640)
        operating.fchown.assert_called_once_with(456, -1, 202)
        self.assertEqual((101, 202), (state['st_uid'], state['st_gid']))
        operating.replace.assert_called_once()

    def test_different_uid_and_gid_preserved(self):
        operating, state = self.write(uid=303)
        operating.fchown.assert_called_once_with(456, 303, 202)
        self.assertEqual((303, 202), (state['st_uid'], state['st_gid']))

    def test_ownership_permission_failure_retains_original(self):
        self.write(owner_error=PermissionError('ownership refused'))

    def test_silent_ownership_loss_retains_original(self):
        self.write(owner_loss=True)

    def test_successful_fchmod_with_silent_setgid_loss_refuses(self):
        self.write(mode_loss=True)

    def test_all_special_mode_bits_verified(self):
        operating, state = self.write(mode=0o7640)
        self.assertEqual(0o7640, stat.S_IMODE(state['st_mode']))
        operating.fstat.assert_called()

    def test_identical_bytes_still_restore_explicit_rollback_mode(self):
        operating, state = self.write(mode=0o600, override=0o640, identical=True)
        operating.replace.assert_called_once()
        self.assertEqual(0o640, stat.S_IMODE(state['st_mode']))

    @unittest.skipIf(os.name == 'nt', 'POSIX group preservation runs natively on POSIX; Windows uses descriptor mocks')
    def test_native_different_project_group_retained(self):
        groups = [gid for gid in os.getgroups() if gid != os.getgid()]
        if not groups:
            self.skipTest('No supplementary project group available for native ownership change')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config'
            path.write_bytes(b'old')
            os.chown(path, -1, groups[0])
            path.chmod(0o640)
            before = path.stat()
            with safeio.Tree(temporary) as tree:
                tree.write('config', b'new')
            after = path.stat()
            self.assertEqual((before.st_uid, before.st_gid, 0o640),
                             (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)))


class RollbackMetadataTests(unittest.TestCase):
    def test_archive_mode_published_with_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'archive'
            try:
                with safeio.Tree(temporary) as tree:
                    tree.write_modes = {'archive': stat.S_IREAD}
                    tree.write('archive', b'archive')
                    self.assertEqual(b'archive', tree.read('archive'))
                self.assertFalse(path.stat().st_mode & stat.S_IWRITE)
            finally:
                if path.exists():
                    path.chmod(0o600)

    def test_rollback_uses_recorded_original_uid_and_gid(self):
        journal = dict(format='awf-install-journal-4', transaction_id=str(uuid.uuid4()),
            phase='active', runtime=None, managed_before_sha256='a' * 64,
            managed_after_sha256=None,
            posix_ownership={installer.CONFIG: [303, 202]},
            files=[dict(path=installer.CONFIG, old_sha256=installer.sha256(b'old'),
                        old_size=3, old_mode=0o2640,
                        new_sha256=installer.sha256(b'new'), new_mode=0o2640)])
        tree = Mock()
        tree.root = Path('project')
        tree.inspect.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o2640)
        tree.read.return_value = b'new'
        with ExitStack() as stack:
            stack.enter_context(patch.object(installer, 'os', SimpleNamespace(name='posix')))
            for name in ('_verify_journal_managed_state', '_verify_runtime_state',
                         '_rollback_runtime', '_cleanup_transaction_temporaries',
                         '_verify_compact_unchanged_inventory', '_write_bound_journal_update',
                         '_finalize_compact_rollback', '_managed_file_inventory'):
                stack.enter_context(patch.object(installer, name, return_value=[]))
            stack.enter_context(patch.object(installer, '_load_recovery_objects',
                return_value={installer.sha256(b'old'): b'old'}))
            installer.rollback(tree, journal)
        self.assertEqual({installer.CONFIG: [303, 202]}, tree.write_ownership)
        tree.write.assert_called_once_with(installer.CONFIG, b'old')

    def test_substituted_symlink_leaf_cannot_change_target_mode(self):
        # Model substitution immediately after the confined write on Windows,
        # where an unprivileged token cannot necessarily create a real symlink.
        victim = {'mode': 0o600}
        tree = Mock()
        tree.root = Path('project')
        tree.inspect.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
        tree.read.return_value = b'new'
        journal = dict(format='awf-install-journal-3', transaction_id=str(uuid.uuid4()),
            phase='active', runtime=None, managed_before=[], managed_after=[],
            files=[dict(path=installer.CONFIG, old='b2xk', old_mode=0o640,
                        new_sha256=installer.sha256(b'new'), new_mode=0o600)])
        with ExitStack() as stack:
            for name in ('_validate_transaction_journal', '_verify_journal_managed_state',
                         '_verify_runtime_state', '_rollback_runtime',
                         '_cleanup_transaction_temporaries', '_managed_file_inventory'):
                stack.enter_context(patch.object(installer, name, return_value=[]))
            stack.enter_context(patch.object(installer.os, 'walk', return_value=[]))
            stack.enter_context(patch.object(installer.shutil, 'rmtree'))
            chmod = stack.enter_context(patch.object(installer.os, 'chmod',
                side_effect=lambda path, mode: victim.update(mode=mode)))
            installer.rollback(tree, journal)
        self.assertEqual(0o600, victim['mode'], 'Post-write chmod followed the substituted symlink')
        chmod.assert_not_called()
        self.assertEqual({installer.CONFIG: 0o640}, tree.write_modes)


class RetainedDiagnosticsTests(unittest.TestCase):
    def test_posix_original_ownership_is_journalled(self):
        helper = journal_tests.CompactInstallerJournalTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        source, destination, pin, content = helper.prepare_install(
            unchanged_files=0, changed_files=1)
        (destination / installer.LOCK).parent.mkdir(parents=True)
        expected = (destination / '.agentic/adopted-clone/0000.bin').stat()
        captured = {}

        def capture(tree, journal):
            captured.update(journal)
            raise CapturedIntent

        operating = Mock(wraps=os)
        operating.name = 'posix'
        native_inventory = installer._managed_file_inventory
        def inventory(tree):
            # Windows DirEntry link counts differ from POSIX. Keep native
            # inventory semantics while exercising POSIX journal capture.
            with patch.object(installer, 'os', os):
                return native_inventory(tree)
        with patch.object(installer, 'os', operating), \
                patch.object(installer, 'install_lock', side_effect=lambda tree: nullcontext()), \
                patch.object(installer, '_managed_file_inventory', inventory), \
                patch.object(installer, 'verify_release', return_value=(pin, content)), \
                patch.object(installer, '_publish_initial_intent', capture), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict='backup')
        self.assertEqual([expected.st_uid, expected.st_gid],
                         captured['posix_ownership']['.agentic/adopted-clone/0000.bin'])
        installer._validate_transaction_journal(captured)

    def test_interrupt_after_journal_deletion_retains_idempotent_diagnostics(self):
        helper = journal_tests.CompactInstallerJournalTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        source, destination, pin, content = helper.prepare_install(
            unchanged_files=0, changed_files=0)
        original_update = installer._write_bound_journal_update
        create = installer._windows_create_directory

        def unproved(handle, name):
            create(handle, name)
            return None

        def interrupt_prepare(tree, journal, update_kind=None):
            original_update(tree, journal, update_kind)
            if update_kind == 'directory_prepare':
                raise CapturedIntent

        with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                patch.object(installer, '_windows_create_directory', unproved), \
                patch.object(installer, '_write_bound_journal_update', interrupt_prepare), \
                self.assertRaises(CapturedIntent):
            installer.install(source, destination, pin, conflict='backup')
        journal = json.loads((destination / installer.JOURNAL).read_bytes())
        expected = [row['path'] for row in reversed(journal['created_directories'])
                    if row['identity'] is None]
        self.assertTrue(expected)
        unlink = installer.Tree.unlink

        def interrupt_delete(tree, path):
            unlink(tree, path)
            if path == installer.JOURNAL:
                raise CapturedIntent

        with patch.object(installer.Tree, 'unlink', interrupt_delete), self.assertRaises(CapturedIntent):
            installer.recover(destination)
        self.assertFalse((destination / installer.JOURNAL).exists())
        result = installer.recover(destination)
        self.assertEqual(dict(status='ROLLED_BACK', retained_directories=expected), result)
        before = {p.relative_to(destination).as_posix(): p.read_bytes()
                  for p in destination.rglob('*') if p.is_file()}
        self.assertEqual(result, installer.recover(destination))
        self.assertEqual(before, {p.relative_to(destination).as_posix(): p.read_bytes()
                                 for p in destination.rglob('*') if p.is_file()})
        self.assertEqual(result, installer.retained_directory_status(destination))
        from agentic.providers.github_status import project_status, render_status
        status = project_status(destination, gh=Mock())
        self.assertEqual(result, status['installation_recovery'])
        self.assertIsNone(status['project_state'])
        for path in expected:
            self.assertIn(path, render_status(status))
        retained_path = destination / installer.RETAINED_JOURNAL
        record = json.loads(retained_path.read_bytes())
        self.assertEqual(journal, record['journal'])
        retained_path.unlink()  # Explicit owner action clears diagnostics.
        self.assertEqual({'status': 'NO_PENDING_INSTALL'}, installer.recover(destination))


if __name__ == '__main__':
    unittest.main()
