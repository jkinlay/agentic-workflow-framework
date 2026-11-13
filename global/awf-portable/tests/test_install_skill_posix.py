"""Explicit POSIX simulation on Windows; native POSIX still exercises its real APIs."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_install_skill as fixtures
import test_install_skill_r2 as round_two

installer = fixtures.installer


@contextmanager
def posix_installation():
    """Model only dir_fd and flock on Windows; exclusive file I/O stays real.

    O_CREAT/O_EXCL, hardlinks, rename and all file bytes use the host filesystem.
    Synthetic directory descriptors and advisory leases are simulation evidence.
    No attributes on the globally shared os module are patched.
    """
    if os.name != 'nt':
        yield
        return
    view = SimpleNamespace(**vars(os))
    view.name, view.O_DIRECTORY, view.O_NOFOLLOW = 'posix', 0x10000000, 0x20000000
    directories, files, leases = {}, {}, set()
    def resolve(path, directory=None):
        path = Path(path)
        return directories[directory] / path if directory is not None else path
    def opened(path, flags, mode=0o777, *, dir_fd=None):
        path = resolve(path, dir_fd)
        if flags & view.O_DIRECTORY:
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise NotADirectoryError(str(path))
            descriptor = 1000000 + len(directories)
            directories[descriptor] = path
            return descriptor
        descriptor = os.open(path, (flags & ~(view.O_DIRECTORY | view.O_NOFOLLOW)) | os.O_BINARY, mode)
        files[descriptor] = path
        return descriptor
    def closed(descriptor):
        if descriptor in directories:
            path = directories.pop(descriptor)
            leases.discard(path)
        else:
            files.pop(descriptor, None)
            os.close(descriptor)
    def flock(descriptor, flags):
        if directories[descriptor] in leases:
            raise BlockingIOError('simulated advisory directory lease')
        leases.add(directories[descriptor])
    view.open, view.close = opened, closed
    view.fstat = lambda fd: directories[fd].stat() if fd in directories else os.fstat(fd)
    view.stat = lambda path, *, dir_fd=None, follow_symlinks=True: os.stat(resolve(path, dir_fd), follow_symlinks=follow_symlinks)
    view.fsync = lambda fd: None if fd in directories else os.fsync(fd)
    view.fchmod = lambda fd, mode: os.chmod(files[fd], mode)
    view.utime = lambda path, **kwargs: os.utime(files[path] if isinstance(path, int) else path, **kwargs)
    view.replace = lambda src, dst, *, src_dir_fd=None, dst_dir_fd=None: os.replace(resolve(src, src_dir_fd), resolve(dst, dst_dir_fd))
    for name in ('getxattr', 'setxattr', 'removexattr', 'listxattr'):
        view.__dict__.pop(name, None)
    fcntl = SimpleNamespace(flock=flock, LOCK_EX=2, LOCK_NB=4)
    with patch.dict(sys.modules, fcntl=fcntl), patch.object(installer, 'os', view), patch.object(fixtures, 'os', view):
        yield


class PosixInstallationTests(unittest.TestCase):
    def fixture(self, name):
        test = fixtures.InstallerTests(name)
        test.setUp()
        self.addCleanup(test.doCleanups)
        return test

    def test_replacement_failure_restores_previous_skill_via_dir_fd(self):
        test = self.fixture('test_replacement_failure_restores_previous_skill')
        with posix_installation():
            test.test_replacement_failure_restores_previous_skill()

    def test_stage_hardlink_injection_reaches_basename_plus_dir_fd(self):
        test = self.fixture('test_new_install_and_idempotence')
        with posix_installation(), patch.object(round_two, 'os', installer.os):
            round_two.RoundTwoTests.test_stage_copy_refuses_preexisting_hardlink_before_owner_write(test)

    def test_normal_install_without_xattr_apis(self):
        test = self.fixture('test_new_install_and_idempotence')
        with posix_installation():
            # On native POSIX, remove APIs from this module's private view too.
            view = SimpleNamespace(**vars(installer.os))
            for name in ('getxattr', 'setxattr', 'removexattr', 'listxattr'):
                view.__dict__.pop(name, None)
            with patch.object(installer, 'os', view):
                test.test_new_install_and_idempotence()

    def test_no_xattr_api_copy_still_refuses_owner_hardlink(self):
        test = self.fixture('test_new_install_and_idempotence')
        target = test.root / 'target'
        owner = test.root / 'owner'
        owner.write_bytes(b'OWNER ORIGINAL')
        os.link(owner, target)
        with posix_installation():
            view = SimpleNamespace(**vars(installer.os))
            for name in ('getxattr', 'setxattr', 'removexattr', 'listxattr'):
                view.__dict__.pop(name, None)
            with patch.object(installer, 'os', view), self.assertRaises(FileExistsError):
                installer.copy_exclusive(test.source / 'SKILL.md', target)
        self.assertEqual(b'OWNER ORIGINAL', owner.read_bytes())
        self.assertEqual(b'OWNER ORIGINAL', target.read_bytes())


if __name__ == '__main__':
    unittest.main()
