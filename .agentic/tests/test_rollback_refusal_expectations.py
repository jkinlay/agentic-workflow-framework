"""Platform simulations for exact rollback-refusal test expectations."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))

from agentic import installer
from agentic import safeio
import rollback_refusal_expectations as expectations


class RollbackRefusalPlatformTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        runtime = root / installer.RUNTIME
        runtime.mkdir(parents=True)
        (runtime / 'failed-runtime').write_bytes(b'failed runtime\n')
        (root / installer.LOCK).parent.mkdir(parents=True, exist_ok=True)
        before = expectations.inventory(root)
        record = expectations.cleanup_record(before, [installer.RUNTIME])
        (root / installer.RETAINED_JOURNAL).write_bytes(installer.json_bytes(record))
        return root, before

    @contextmanager
    def platform_lock(self, root, platform):
        # Patch only installer's OS reference: changing process-wide os.name
        # would also change pathlib's concrete path class on the native host.
        operating = SimpleNamespace(**vars(os))
        operating.name = platform
        operating.close = Mock(wraps=os.close)
        operating.write = Mock(wraps=os.write)
        operating.O_BINARY = getattr(os, 'O_BINARY', 0)
        lock = root / installer.LOCK
        tree = Mock()
        tree.parent.return_value = lock.parent, 71, lock.name
        fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=Mock())
        msvcrt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0,
                                open_osfhandle=Mock(side_effect=lambda fd, flags: fd),
                                locking=Mock())
        def opened(name, flags, mode, *, dir_fd):
            self.assertEqual(lock.name, name)
            self.assertEqual(71, dir_fd)
            self.assertEqual(0o600, mode)
            return os.open(lock, flags, mode)
        operating.open = Mock(side_effect=opened)
        def win_open(path, *, lock):
            self.assertEqual(root / installer.LOCK, path)
            self.assertTrue(lock)
            return os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_BINARY', 0))
        with patch.object(installer, 'os', operating):
            with patch.dict(sys.modules, fcntl=fcntl, msvcrt=msvcrt), \
                    patch.object(safeio, 'win_open', side_effect=win_open, create=True) as windows_open:
                with installer.install_lock(tree):
                    pass
                tree.parent.assert_called_once_with(installer.LOCK, create=True)
                tree.inspect.assert_called_once_with(installer.LOCK)
                if platform == 'posix':
                    operating.open.assert_called_once()
                    windows_open.assert_not_called()
                    fd = fcntl.flock.call_args_list[0].args[0]
                    self.assertEqual([call(fd, 6), call(fd, 8)], fcntl.flock.call_args_list)
                    operating.write.assert_not_called()
                    msvcrt.locking.assert_not_called()
                else:
                    operating.open.assert_not_called()
                    windows_open.assert_called_once_with(lock, lock=True)
                    fd = msvcrt.locking.call_args_list[0].args[0]
                    self.assertEqual([call(fd, 2, 1), call(fd, 0, 1)], msvcrt.locking.call_args_list)
                    operating.write.assert_called_once_with(fd, b'0')
                    fcntl.flock.assert_not_called()
                operating.close.assert_called_once_with(fd)
            yield

    def test_posix_lock_and_exact_retained_assertions(self):
        root, before = self.fixture()
        with self.platform_lock(root, 'posix'):
            self.assertEqual(b'', (root / installer.LOCK).read_bytes())
            expectations.assert_retained(self, root, before, [installer.RUNTIME])

    def test_windows_lock_and_exact_retained_assertions(self):
        root, before = self.fixture()
        with self.platform_lock(root, 'nt'):
            self.assertEqual(b'0', (root / installer.LOCK).read_bytes())
            expectations.assert_retained(self, root, before, [installer.RUNTIME])

    def test_wrong_lock_bytes_are_rejected_on_both_platforms(self):
        for platform, wrong in (('posix', b'0'), ('nt', b'')):
            with self.subTest(platform=platform):
                root, before = self.fixture()
                with self.platform_lock(root, platform):
                    (root / installer.LOCK).write_bytes(wrong)
                    with self.assertRaises(AssertionError):
                        expectations.assert_retained(self, root, before, [installer.RUNTIME])

    def test_posix_inventory_preserves_modes_identities_case_and_links(self):
        # Synthetic POSIX names and metadata avoid Windows case folding,
        # symlink privileges and Windows chmod semantics altogether.
        metadata = {
            '/project/runtime': (stat.S_IFDIR | 0o750, 1),
            '/project/runtime/Owner': (stat.S_IFREG | 0o640, 2),
            '/project/runtime/owner': (stat.S_IFREG | 0o600, 3),
            '/project/runtime/lib64': (stat.S_IFLNK | 0o777, 4),
        }
        data = {'/project/runtime/Owner': b'upper', '/project/runtime/owner': b'lower'}
        class EvidencePath(PurePosixPath):
            def lstat(self):
                mode, inode = metadata[str(self)]
                return SimpleNamespace(st_mode=mode, st_dev=9, st_ino=inode)
            def is_symlink(self):
                return stat.S_ISLNK(self.lstat().st_mode)
            def read_bytes(self):
                return data[str(self)]
        operating = Mock(wraps=os)
        operating.name = 'posix'
        operating.walk.return_value = [('/project', ['runtime'], []),
            ('/project/runtime', ['lib64'], ['Owner', 'owner'])]
        operating.readlink.return_value = 'lib'
        with patch.object(expectations, 'Path', EvidencePath), patch.object(expectations, 'os', operating):
            actual = expectations.inventory('/project')
        operating.walk.assert_called_once_with(EvidencePath('/project'), followlinks=False)
        operating.readlink.assert_called_once_with(EvidencePath('/project/runtime/lib64'))
        self.assertEqual({'runtime': ('directory', (9, 1, 0o750)),
            'runtime/Owner': ('file', (9, 2, 0o640), b'upper'),
            'runtime/owner': ('file', (9, 3, 0o600), b'lower'),
            'runtime/lib64': ('link', (9, 4, 0o777), 'lib')}, actual)
        self.assertEqual(None, expectations.cleanup_record(actual, ['runtime'])['cleanup_pending'][0]['size_bytes'])
        # Rebound tests compare observed identities/modes, not Windows literals:
        # each change must remain visible even if all content bytes survive.
        metadata['/project/runtime/owner'] = (stat.S_IFREG | 0o640, 30)
        with patch.object(expectations, 'Path', EvidencePath), patch.object(expectations, 'os', operating):
            changed = expectations.inventory('/project')
        self.assertNotEqual(actual, changed)
        self.assertEqual(('file', (9, 30, 0o640), b'lower'), changed['runtime/owner'])

    def test_posix_mutation_guard_resolves_aliases_without_case_folding(self):
        journal = installer.json_bytes({'files': [{'path': 'managed/Owner'}]})
        class EvidencePath(PurePosixPath):
            def resolve(self):
                return EvidencePath('/project') if str(self) == '/alias' else self
            def read_bytes(self):
                return journal
        tree = SimpleNamespace(root=EvidencePath('/alias'))
        with patch.object(expectations, 'Path', EvidencePath), \
                patch.object(installer.Tree, 'write') as write, \
                patch.object(installer.Tree, 'unlink') as unlink:
            with expectations.forbid_managed_mutation('/project'):
                with self.assertRaisesRegex(AssertionError, '^Managed-file write before refusal: managed/Owner$'):
                    installer.Tree.write(tree, 'managed/Owner', b'changed')
                with self.assertRaisesRegex(AssertionError, '^Managed-file unlink before refusal: managed/Owner$'):
                    installer.Tree.unlink(tree, 'managed/Owner')
                write.assert_not_called()
                unlink.assert_not_called()
                installer.Tree.write(tree, 'managed/owner', b'distinct POSIX path')
                installer.Tree.unlink(tree, 'managed/owner')
            write.assert_called_once_with(tree, 'managed/owner', b'distinct POSIX path')
            unlink.assert_called_once_with(tree, 'managed/owner')

    def test_posix_stage_aliases_keep_one_exact_relative_inventory(self):
        from test_bootstrap_configuration import retained_stage_paths
        class EvidencePath(PurePosixPath):
            def resolve(self, *, strict):
                self_test.assertTrue(strict)
                return expanded if self == alias else self
            def glob(self, pattern):
                self_test.assertEqual(expanded / '.agentic', self)
                self_test.assertEqual('.venv.staging-*', pattern)
                return iter([stage])
        self_test = self
        alias = EvidencePath('/alias/project')
        expanded = EvidencePath('/expanded/project')
        stage = expanded / '.agentic/.venv.staging-synthetic'
        actual, stages, relative = retained_stage_paths(alias, stage)
        self.assertEqual(stage, actual)
        self.assertEqual([stage], stages)
        self.assertEqual('.agentic/.venv.staging-synthetic', relative)


if __name__ == '__main__':
    unittest.main()
