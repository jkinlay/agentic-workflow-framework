"""Bootstrap, qualification and ADS boundary regressions."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/tests'))
sys.path.insert(0, str(ROOT / 'scripts'))
import bootstrap_project as bootstrap
import qualify_upgrade as qualification
from agentic import safeio, ValidationError


class AuxiliaryBoundaryTests(unittest.TestCase):
    def test_assignment_without_xattr_apis_normal_and_substitution(self):
        from test_awf266_xattr import missing_custody_apis
        with missing_custody_apis():
            self.test_assignment_normal_probe_and_repeated_state_publication()
            self.test_assignment_state_destination_substitution_after_close_is_preserved()

    def test_public_bootstrap_canonicalizes_linked_project_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real, alias = root / 'real', root / 'alias'
            real.mkdir()
            def link(target, path):
                if os.name == 'nt':
                    import _winapi
                    _winapi.CreateJunction(str(target), str(path))
                else:
                    path.symlink_to(target, target_is_directory=True)
            def unlink(path):
                if os.name == 'nt':
                    path.rmdir()
                else:
                    path.unlink()
            link(real, alias)
            try:
                project = alias / 'project'
                project.mkdir()
                self.assertNotEqual(project, project.resolve())
                pin = hashlib.sha256((ROOT / 'MANIFEST.json').read_bytes()).hexdigest()
                # Real public parser and product calls; no path or installer mocks.
                for arguments, status in ((['--recover'], 'NO_PENDING_INSTALL'),
                        (['--dry-run', '--expected-manifest-sha256', pin], 'PLAN')):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(0, bootstrap.main(['--dest', str(project), *arguments]))
                    self.assertEqual(status, json.loads(output.getvalue())['status'])
                # Links below the canonical project root must still be refused.
                inside = real / 'project/linked'
                link(real, inside)
                try:
                    with self.assertRaises((ValidationError, OSError)):
                        with safeio.Tree(project.resolve()) as tree:
                            tree.parent('linked/owner')
                finally:
                    unlink(inside)
            finally:
                unlink(alias)

    def materialize_batch(self, destination):
        adapter = runpy.run_path(str(ROOT / '.agentic/scripts/merge_train.py'))
        function = adapter['_materialize']
        namespace = function.__globals__
        oid = b'1' * 40
        entries = b'100644 blob ' + oid + b'\tfixture.txt\0'
        objects = oid + b' blob 7\npayload\n'
        with patch.dict(namespace, _git=lambda *a, **kw: objects if kw else entries,
                        verify_release=lambda tree: None):
            function('mock-native-git', destination, 'frozen-head', destination)

    def test_batch_materialization_declares_transaction_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve()
            self.materialize_batch(destination)
            self.assertEqual(b'payload', (destination / 'fixture.txt').read_bytes())

    def test_batch_materialization_retains_unexpected_destination(self):
        original = safeio.Tree.rename_checked
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve()
            def substitute(tree, source, target, *args, **kwargs):
                (destination / target).write_bytes(b'owner replacement')
                return original(tree, source, target, *args, **kwargs)
            with patch.object(safeio.Tree, 'rename_checked', substitute):
                with self.assertRaisesRegex(ValidationError, 'Namespace unexpected occupancy; no mutation; retained: fixture.txt'):
                    self.materialize_batch(destination)
            self.assertEqual(b'owner replacement', (destination / 'fixture.txt').read_bytes())
            self.assertEqual([b'payload'], [p.read_bytes() for p in destination.glob('.awf-*.tmp')])

    def test_owned_archive_fixture_declares_its_transaction_authority(self):
        import validate_archive
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / 'source'
            source.mkdir()
            (source / 'MANIFEST.json').write_bytes(b'{}')
            (source / 'MANIFEST.md').write_bytes(b'fixture manifest')
            destination = Path(temporary).resolve() / 'owned-copy'
            content = {'AGENTS.md': b'fixture',
                       '.agentic/examples/OPERATING_CONFIG.yaml': b'current policy'}
            def seed(path):
                (path / 'OPERATING_CONFIG.yaml').write_bytes(b'previous fixture policy')
            with patch.object(validate_archive, 'verify_release', return_value=('fixture', content)), \
                    patch.object(validate_archive, 'seed_source_configuration', seed):
                result = validate_archive.example_configuration_fixture(source, destination)
            self.assertEqual(destination / '.agentic/examples/PROJECT_CONFIG.yaml', result)
            self.assertEqual(b'fixture', (destination / 'AGENTS.md').read_bytes())
            self.assertEqual(b'current policy', (destination / 'OPERATING_CONFIG.yaml').read_bytes())

    def test_post_install_capture_needs_no_named_temporary_fallback(self):
        from agentic import adoption_config
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(tempfile, 'TemporaryFile', side_effect=AssertionError('named fallback reached')):
            result = adoption_config._post_command([sys.executable, '-B', '-c', 'print("{}")'], Path(temporary).resolve())
        self.assertEqual(0, result['exit_code'])
        self.assertEqual({}, result['output'])
        self.assertIsNone(result['diagnostic'])

    def test_assignment_state_destination_substitution_after_close_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); state = root / 'state.json'
            state.write_bytes(b'{"old":1}')
            native = os.fdopen
            hits = []
            class Closing:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self.stream
                def __exit__(self, *args):
                    self.stream.close()
                    if list(root.glob('*.tmp')):
                        state.rename(root / 'original-state')
                        state.write_bytes(b'owner replacement'); hits.append(state)
            def opened(fd, *args, **kwargs):
                stream = native(fd, *args, **kwargs)
                return Closing(stream) if args and args[0] in {'w', 'wb'} else stream
            with patch.object(os, 'fdopen', opened), self.assertRaises(ValidationError):
                bootstrap._write_assignment_state(state, {'new': 2}, root / 'project')
            self.assertEqual([state], hits)
            self.assertEqual(b'owner replacement', state.read_bytes())
            self.assertEqual(b'{"old":1}', (root / 'original-state').read_bytes())

    def test_assignment_freed_temporary_replacement_is_never_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); state = root / 'state.json'
            native = os.replace
            hits = []
            def replace(source, destination, *args, **kwargs):
                result = native(source, destination, *args, **kwargs)
                source = Path(source)
                if not source.is_absolute(): source = root / source
                source.write_bytes(b'owner replacement'); hits.append(source)
                return result
            with patch.object(os, 'replace', replace):
                bootstrap._write_assignment_state(state, {'new': 2}, root / 'project')
            self.assertEqual(1, len(hits))
            self.assertEqual(b'owner replacement', hits[0].read_bytes())
            self.assertEqual({'new': 2}, __import__('json').loads(state.read_bytes()))

    def test_assignment_probe_replacement_after_close_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            native_open, native_close, native_mkstemp = os.fdopen, os.close, tempfile.mkstemp
            descriptors, hits = {}, []
            def substitute(path):
                path = Path(path)
                path.rename(root / 'original-probe')
                path.write_bytes(b'owner replacement'); hits.append(path)
            def mkstemp(*args, **kwargs):
                fd, path = native_mkstemp(*args, **kwargs)
                descriptors[fd] = path
                return fd, path
            def close(fd):
                result = native_close(fd)
                if fd in descriptors: substitute(descriptors.pop(fd))
                return result
            class Closing:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self.stream
                def __exit__(self, *args):
                    self.stream.close()
                    paths = list(root.glob('*.probe'))
                    if paths and not hits: substitute(paths[0])
            def opened(fd, *args, **kwargs): return Closing(native_open(fd, *args, **kwargs))
            with patch.object(tempfile, 'mkstemp', mkstemp), patch.object(os, 'close', close), \
                    patch.object(os, 'fdopen', opened), self.assertRaises(ValidationError):
                bootstrap._prepare_assignment_state_path(root / 'state.json', root / 'project')
            self.assertEqual(1, len(hits))
            self.assertEqual(b'owner replacement', hits[0].read_bytes())

    def test_assignment_normal_probe_and_repeated_state_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); state = root / 'state.json'
            bootstrap._prepare_assignment_state_path(state, root / 'project')
            bootstrap._write_assignment_state(state, {'value': 1}, root / 'project')
            bootstrap._write_assignment_state(state, {'value': 2}, root / 'project')
            self.assertEqual({'value': 2}, __import__('json').loads(state.read_bytes()))
            self.assertEqual([], list(root.glob('*.probe')))

    def test_posix_replica_identity_precedes_all_metadata_mutation(self):
        if os.name == 'nt':
            self.assertTrue(callable(qualification._posix_probe_parent))
            return  # Native Linux must execute the following fd/xattr assertions.
        if not all(hasattr(os, name) for name in ('listxattr', 'getxattr', 'setxattr', 'removexattr')):
            with tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                before = parent.stat()
                with self.assertRaisesRegex(ValidationError, 'lacks xattr APIs.*xattr-capable'):
                    qualification._posix_probe_parent(parent)
                self.assertEqual([], list(parent.iterdir()))
                self.assertEqual((before.st_ino, before.st_mode), (parent.stat().st_ino, parent.stat().st_mode))
            return  # Missing-API POSIX platforms refuse exact qualification.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); parent = root / 'parent'; parent.mkdir(mode=0o755)
            hits = []
            original = qualification._remember_permission_probe
            def substitute(path):
                original(path)
                path = Path(path)
                path.rename(path.with_name('original-replica'))
                path.mkdir(mode=0o750); path.chmod(0o750)
                os.setxattr(path, 'user.awf-owner', b'owner')
                hits.append(path)
            with patch.object(qualification.tempfile, 'tempdir', str(root)), \
                    patch.object(qualification, '_remember_permission_probe', substitute), \
                    self.assertRaisesRegex(ValueError, 'identity changed'):
                qualification._posix_probe_parent(parent)
            self.assertEqual(1, len(hits))
            self.assertEqual(0o750, stat.S_IMODE(hits[0].stat().st_mode))
            self.assertEqual(b'owner', os.getxattr(hits[0], 'user.awf-owner'))

    def test_qualification_seed_refuses_existing_hardlink(self):
        import upgrade_195_fixtures
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); host = root / '.agentic-state/routing/published-host.sqlite'
            host.parent.mkdir(parents=True)
            owner = root / 'owner'; owner.write_bytes(b'owner bytes'); os.link(owner, host)
            source = root / 'budget-source'; source.write_bytes(b'qualification bytes')
            with patch.object(upgrade_195_fixtures, 'seed_legacy_accounting'), self.assertRaises((FileExistsError, ValidationError)):
                qualification._seed_qualification_state(None, root, {'sources': {'host': {'path': source}}})
            self.assertEqual(b'owner bytes', owner.read_bytes())

    def test_qualification_seed_normal_exclusive_publication(self):
        import upgrade_195_fixtures
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.agentic-state/routing').mkdir(parents=True)
            source = root / 'budget-source'; source.write_bytes(b'qualification bytes')
            with patch.object(upgrade_195_fixtures, 'seed_legacy_accounting'):
                qualification._seed_qualification_state(None, root, {'sources': {'host': {'path': source}}})
            self.assertEqual(b'qualification bytes', (root / '.agentic-state/routing/published-host.sqlite').read_bytes())

    def test_windows_ads_creation_never_truncates_an_existing_stream(self):
        if os.name != 'nt':
            self.assertTrue(callable(safeio.validate_windows_metadata))
            return
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'created'; path.write_bytes(b'primary')
            ads = str(path) + ':awf-owner:$DATA'
            with safeio._windows_stream(ads, 'wb') as stream: stream.write(b'owner stream')
            with self.assertRaises(OSError):
                with safeio._windows_stream(ads, 'wb') as stream: stream.write(b'changed')
            with safeio._windows_stream(ads, 'rb') as stream: self.assertEqual(b'owner stream', stream.read())
            self.assertEqual(b'primary', path.read_bytes())


if __name__ == '__main__':
    unittest.main()
