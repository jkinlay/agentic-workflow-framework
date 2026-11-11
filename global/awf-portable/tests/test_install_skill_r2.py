"""Replacement attacks retained by formal round two."""
import os
from pathlib import Path
from unittest.mock import patch
import unittest
import test_install_skill as fixtures

installer = fixtures.installer


class RoundTwoTests(unittest.TestCase):
    setUp = fixtures.InstallerTests.setUp
    pin_manifest = fixtures.InstallerTests.pin_manifest
    run_install = fixtures.InstallerTests.run_install

    def test_exclusive_copy_preserves_times_mode_and_posix_xattrs(self):
        source = self.source / 'SKILL.md'
        stamp = 946684800123456700
        os.utime(source, ns=(stamp, stamp))
        xattrs = os.name != 'nt' and all(hasattr(os, name) for name in ('getxattr', 'setxattr', 'listxattr'))
        if os.name != 'nt':
            source.chmod(0o640)
        if xattrs:
            os.setxattr(source, 'user.awf-private-fixture', b'synthetic metadata')
        before = source.stat()
        target = self.root / 'copied'
        installer.copy_exclusive(source, target)
        self.assertEqual(before.st_mtime_ns, target.stat().st_mtime_ns)
        self.assertEqual(before.st_mode & 0o777, target.stat().st_mode & 0o777)
        self.assertEqual(source.read_bytes(), target.read_bytes())
        if xattrs:
            self.assertEqual(b'synthetic metadata', os.getxattr(target, 'user.awf-private-fixture'))

    def test_stage_directories_request_source_mode_at_creation(self):
        if os.name != 'nt':
            self.source.chmod(0o700)
        expected = self.source.stat().st_mode & 0o7777
        native = Path.mkdir
        requests = []
        def create(path, *args, **kwargs):
            if path.name.startswith('.awf-stage-'):
                requests.append(kwargs.get('mode', args[0] if args else None))
            return native(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', create):
            self.run_install()
        self.assertEqual([expected], requests)
        self.assertEqual(expected, self.dest.stat().st_mode & 0o7777)
    def test_stage_copy_refuses_preexisting_hardlink_before_owner_write(self):
        owner = self.root / 'owner'
        owner.write_bytes(b'OWNER ORIGINAL DATA')
        native = os.open
        calls = []
        def create(path, flags, *args, **kwargs):
            target = Path(path)
            if not target.is_absolute() and kwargs.get('dir_fd') is not None:
                held = os.fstat(kwargs['dir_fd'])
                for directory in self.dest.parent.glob('.awf-stage-*'):
                    info = directory.stat()
                    if (held.st_dev, held.st_ino) == (info.st_dev, info.st_ino):
                        target = directory / target
                        break
            if target.name == 'SKILL.md' and target.parent.name.startswith('.awf-stage-'):
                self.assertEqual(os.O_CREAT | os.O_EXCL, flags & (os.O_CREAT | os.O_EXCL))
                os.link(owner, target)
                calls.append(target)
            return native(path, flags, *args, **kwargs)
        # copy2 uses builtins.open: retain the reviewer's exact copy seam as
        # well as the exclusive-creation seam used by the corrected writer.
        copy2 = installer.shutil.copy2
        def copy(src, dst, *args, **kwargs):
            target = Path(dst)
            if target.name == 'SKILL.md':
                os.link(owner, target)
                calls.append(target)
            return copy2(src, dst, *args, **kwargs)
        copytree = installer.shutil.copytree
        def tree(src, dst, *args, **kwargs):
            if not args:
                kwargs.setdefault('copy_function', copy)
            return copytree(src, dst, *args, **kwargs)
        with patch.object(installer.os, 'open', create), patch.object(installer.shutil, 'copytree', tree):
            with self.assertRaises((OSError, installer.InstallError)):
                self.run_install()
        self.assertEqual(1, len(calls))
        self.assertEqual(b'OWNER ORIGINAL DATA', owner.read_bytes())

    def test_installer_keeps_creating_lock_handle_until_disposal(self):
        native = installer.copy_stage
        attempts = []
        lock = self.dest.parent / '.awf-install.lock'
        def copy(*args, **kwargs):
            if Path(args[0]) == self.source:
                try:
                    if os.name == 'nt':
                        lock.rename(lock.with_name('original-lock'))
                    lock.write_bytes(b'OWNER REPLACEMENT LOCK')
                    attempts.append(True)
                except PermissionError:
                    attempts.append(False)
            return native(*args, **kwargs)
        with patch.object(installer, 'copy_stage', copy):
            self.run_install()
        self.assertEqual(1, len(attempts))
        if attempts[0]:
            self.assertEqual(b'OWNER REPLACEMENT LOCK', lock.read_bytes())

    def test_lock_excludes_second_installer_and_needs_no_pathname_cleanup(self):
        self.dest.parent.mkdir(parents=True)
        lock = self.dest.parent / '.awf-install.lock'
        descriptor = installer.create_install_lock(lock)
        try:
            with self.assertRaises(FileExistsError):
                installer.create_install_lock(lock)
        finally:
            installer.dispose_install_lock(descriptor)
        self.assertFalse(lock.exists())

    def test_lock_descriptor_conversion_failure_leaves_no_named_lock(self):
        self.dest.parent.mkdir(parents=True)
        lock = self.dest.parent / '.awf-install.lock'
        if os.name == 'nt':
            import msvcrt
            seam = patch.object(msvcrt, 'open_osfhandle', side_effect=OSError('descriptor limit'))
        else:
            import fcntl
            seam = patch.object(fcntl, 'flock', side_effect=OSError('lease unavailable'))
        with seam, self.assertRaises(OSError):
            installer.create_install_lock(lock)
        self.assertFalse(lock.exists())
