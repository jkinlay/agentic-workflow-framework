"""Missing-API and unsupported-filesystem custody; POSIX branches simulated on Windows."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError, installer, safeio


def posix_os_without_xattrs():
    # Only the module under test gets this private os view. Never delete or
    # patch attributes on the interpreter's globally shared os module.
    view = SimpleNamespace(**vars(os))
    view.name = 'posix'
    for name in ('getxattr', 'setxattr', 'removexattr', 'listxattr'):
        if hasattr(view, name):
            delattr(view, name)
    return view


@contextmanager
def missing_custody_apis():
    native = installer._temporary_custody_metadata
    view = posix_os_without_xattrs()
    @contextmanager
    def metadata(tree, relative, descriptor):
        with patch.object(installer, 'os', view):
            context = native(tree, relative, descriptor)
            value = context.__enter__()
        try:
            yield value
        finally:
            with patch.object(installer, 'os', view):
                context.__exit__(*sys.exc_info())
    with patch.object(installer, '_temporary_custody_metadata', metadata):
        yield


class CustodyApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journal = {'format': 'awf-install-journal-4', 'transaction_id': str(uuid.uuid4()),
                        'temporary_files': 'sibling-uuid5-v2', 'files': []}

    def test_normal_installer_transaction_without_xattr_apis(self):
        from test_awf266_sites import SiteTests
        fixture = SiteTests('test_normal_install_retires_authenticated_objects_marker_and_journal')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source, project, pin, content = fixture.fixture()
        with missing_custody_apis(), patch.object(installer, 'verify_release', return_value=(pin, content)):
            result = installer.install(source, project, pin, conflict='backup')
        self.assertEqual('INSTALLED', result['status'])
        self.assertEqual(b'new', (project / '.agentic/adopted-clone/file').read_bytes())
        self.assertFalse((project / installer.JOURNAL).exists())
        self.assertFalse((project / installer.MARKER).exists())

    def test_normal_bound_publications_and_cleanup_without_xattr_apis(self):
        with missing_custody_apis(), installer.Tree(self.root) as tree, installer.install_lock(tree):
            tree.write_exclusive(installer.JOURNAL, installer.json_bytes(self.journal))
            installer._bind_transaction_temporaries(tree, self.journal, initial=True)
            tree.write(installer.JOURNAL, b'updated journal')
            self.assertTrue(tree._custody_metadata_unavailable)
            self.assertIsNone(tree._live_temporary_custody)
            installer._cleanup_transaction_temporaries(tree, self.journal)
            self.assertEqual(b'updated journal', tree.read(installer.JOURNAL))
        self.assertEqual(b'0' if os.name == 'nt' else b'', (self.root / installer.LOCK).read_bytes())

    def test_interrupted_temporaries_and_substitutes_refuse_fresh_recovery_without_apis(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve()
                view = SimpleNamespace(**vars(os))
                def stop(fd, mode='r', *args, **kwargs):
                    if 'w' in mode:
                        os.close(fd)
                        raise RuntimeError('interrupted before content write')
                    return os.fdopen(fd, mode, *args, **kwargs)
                view.fdopen = stop
                with missing_custody_apis(), installer.Tree(root) as tree, installer.install_lock(tree):
                    tree.write_exclusive(installer.JOURNAL, installer.json_bytes(self.journal))
                    installer._bind_transaction_temporaries(tree, self.journal, initial=True)
                    with patch.object(safeio, 'os', view), self.assertRaisesRegex(RuntimeError, 'interrupted'):
                        tree.write(installer.JOURNAL, b'unpublished')
                path = root / installer._transaction_temporary_paths(self.journal)[installer.JOURNAL]
                if replacement:
                    path.rename(path.with_suffix('.original'))
                    path.write_bytes(b'OWNER REPLACEMENT')
                before = path.read_bytes()
                with missing_custody_apis(), installer.Tree(root) as tree, installer.install_lock(tree):
                    with self.assertRaisesRegex(ValidationError, 'lacks.*xattr.*quarantine'):
                        installer._bind_transaction_temporaries(tree, self.journal)
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(installer.json_bytes(self.journal), (root / installer.JOURNAL).read_bytes())

    def test_live_custody_still_refuses_substitution_without_apis(self):
        with missing_custody_apis(), installer.Tree(self.root) as tree, installer.install_lock(tree):
            tree.write_exclusive(installer.JOURNAL, installer.json_bytes(self.journal))
            installer._bind_transaction_temporaries(tree, self.journal, initial=True)
            path = installer._transaction_temporary_paths(self.journal)[installer.JOURNAL]
            tree.write_exclusive(path, b'created')
            tree.record_temporary_identity(path, safeio.object_key(tree._authenticated[path]))
            target = self.root / path
            target.rename(target.with_suffix('.original'))
            target.write_bytes(b'OWNER REPLACEMENT')
            with self.assertRaisesRegex(ValidationError, 'identity changed'):
                installer._bind_transaction_temporaries(tree, self.journal)
            self.assertEqual(b'OWNER REPLACEMENT', target.read_bytes())

    def test_each_missing_custody_api_selects_fail_closed_recovery(self):
        target = self.root / 'lock'
        target.write_bytes(b'unchanged')
        with target.open('rb') as stream:
            for missing in ('getxattr', 'setxattr', 'removexattr'):
                view = posix_os_without_xattrs()
                for name in ('getxattr', 'setxattr', 'removexattr'):
                    setattr(view, name, lambda *args: self.fail('incomplete xattr API called'))
                delattr(view, missing)
                tree = SimpleNamespace()
                with patch.object(installer, 'os', view), installer._temporary_custody_metadata(tree, 'lock', stream.fileno()) as (active, save):
                    self.assertIsNone(active)
                    save(['transaction', 'temporary', ['dev', 'inode']])
                    self.assertTrue(tree._custody_metadata_unavailable)
        self.assertEqual(b'unchanged', target.read_bytes())

    def test_setxattr_enotsup_refuses_with_filesystem_remedy_and_retains_bytes(self):
        target = self.root / 'lock'
        target.write_bytes(b'unchanged')
        for code in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            view = posix_os_without_xattrs()
            def missing(*args):
                raise OSError(errno.ENODATA, 'no attribute')
            def unsupported(*args):
                raise OSError(code, 'not supported')
            view.getxattr, view.setxattr = missing, unsupported
            view.removexattr = lambda *args: self.fail('failed write must not retire custody')
            with target.open('rb') as stream, patch.object(installer, 'os', view):
                with self.assertRaisesRegex(ValidationError, 'filesystem supporting user xattrs'):
                    with installer._temporary_custody_metadata(SimpleNamespace(), 'lock', stream.fileno()) as (_, save):
                        save([str(uuid.uuid4()), 'temporary', ['dev', 'inode']])
            self.assertEqual(b'unchanged', target.read_bytes())


if __name__ == '__main__':
    unittest.main()
