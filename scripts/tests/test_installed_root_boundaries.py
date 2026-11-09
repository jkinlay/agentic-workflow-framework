"""Resolved installed roots must stay within their individual boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import test_installed_self_test_shards as fixtures

policy = fixtures.installed


class RootBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf257-boundaries-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def roots(self):
        runtime = self.root / '.agentic/.venv'
        version = f'python{sys.version_info.major}.{sys.version_info.minor}'
        return [(self.root / '.agentic/lib', self.root / '.agentic/lib'),
                (self.root / '.agentic/tests', self.root / '.agentic/tests'),
                (runtime / 'Lib/site-packages', runtime),
                (runtime / 'lib' / version / 'site-packages', runtime),
                (runtime / 'lib64' / version / 'site-packages', runtime),
                (self.root / '.agentic/scripts', self.root / '.agentic/scripts')]

    def refuse(self, root, boundary):
        inventory = policy.isolation_inventory(self.root)
        self.assertIn('junction_dependency', [row['module'] for row in inventory['outside_roots']],
                      'module from escaped resolved root was accepted as installed')
        # Boundary refusal precedes test enumeration and never repairs imports.
        paths = list(sys.path)
        modules = dict(sys.modules)
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Path, 'rglob', side_effect=AssertionError('discovery began')), \
                self.assertRaises(ValueError) as raised:
            policy.assert_isolation(self.root)
        message = str(raised.exception)
        self.assertIn('root boundary refused', message)
        self.assertIn(root.relative_to(self.root).as_posix(), message)
        self.assertIn(boundary.relative_to(self.root).as_posix(), message)
        self.assertEqual(paths, sys.path)
        self.assertEqual(modules, sys.modules)

    def test_simulated_junctions_refuse_each_escaped_root(self):
        original = Path.resolve
        for root, boundary in self.roots():
            with self.subTest(root=root.relative_to(self.root).as_posix()):
                target = self.root / 'vendor' / root.name
                target.mkdir(parents=True, exist_ok=True)
                foreign = types.ModuleType('junction_dependency')
                foreign.__file__ = str(target / 'dependency.py')
                Path(foreign.__file__).write_text('VALUE = 1\n')
                def resolve(path, *args, **kwargs):
                    return target if path == root else original(path, *args, **kwargs)
                with mock.patch.object(Path, 'resolve', resolve), \
                        mock.patch.dict(sys.modules, {foreign.__name__: foreign}):
                    self.refuse(root, boundary)

    def real_link(self, root, target):
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root), str(target)],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    self.skipTest('directory junction creation unavailable: ' + result.stderr.strip())
            else:
                root.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('symlink privilege unavailable (WinError 1314)')
            self.skipTest('directory link creation unavailable: ' + str(exc))
        if os.name == 'nt':
            self.addCleanup(os.rmdir, root)
        else:
            self.addCleanup(root.unlink)

    def real_escape(self, index):
        root, boundary = self.roots()[index]
        target = self.root / 'vendor' / root.name
        target.mkdir(parents=True)
        (target / 'dependency.py').write_text('VALUE = 1\n')
        self.real_link(root, target)
        self.assertEqual(target, root.resolve())
        foreign = types.ModuleType('junction_dependency')
        foreign.__file__ = str(root / 'dependency.py')
        with mock.patch.dict(sys.modules, {foreign.__name__: foreign}):
            self.refuse(root, boundary)

    def test_real_site_packages_escape(self):
        self.real_escape(2)

    def test_real_lib_escape(self):
        self.real_escape(0)

    def test_real_tests_escape(self):
        self.real_escape(1)

    def test_real_scripts_escape(self):
        self.real_escape(5)

    def test_windows_symlink_privilege_refusal_skips(self):
        error = OSError('symlink privilege unavailable')
        error.winerror = 1314
        root, _ = self.roots()[2]
        with mock.patch.object(subprocess, 'run', side_effect=error), \
                mock.patch.object(Path, 'symlink_to', side_effect=error), \
                self.assertRaisesRegex(unittest.SkipTest, 'WinError 1314'):
            self.real_link(root, self.root / 'vendor')

    def test_ordinary_installed_directories_pass(self):
        fixtures.LoaderProcessTests.setUp(self)
        for root, _ in self.roots():
            root.mkdir(parents=True, exist_ok=True)
        done = fixtures.LoaderProcessTests.child(self, '''import pathlib,sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'.agentic/lib'))
from agentic.self_test_shards import assert_isolation,isolation_inventory
assert assert_isolation(root)=='PASS'
checks=isolation_inventory(root)['root_boundary_checks']
assert len(checks)==6 and all(row['status']=='PASS' for row in checks),checks
''')
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
