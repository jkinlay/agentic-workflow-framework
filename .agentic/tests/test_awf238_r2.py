"""Round-two replacement-object regressions, using the retained review seams."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import safeio, adoption_config, host_preflight, ValidationError
from agentic.preservation_metadata import runtime_inventory


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_published_write_never_cleans_a_replacement_at_freed_temp_name(self):
        native = os.replace
        replacements = []
        def publish(src, dst, *args, **kwargs):
            result = native(src, dst, *args, **kwargs)
            name = Path(src)
            if name.name.startswith('.awf-'):
                target = name if name.is_absolute() else self.root / name
                target.write_bytes(b'OWNER REPLACEMENT')
                replacements.append(target)
            return result
        with safeio.Tree(self.root) as tree, patch.object(os, 'replace', publish):
            tree.write('managed', b'published', authority='transaction')
        self.assertEqual(1, len(replacements))
        self.assertEqual(b'OWNER REPLACEMENT', replacements[0].read_bytes())
        self.assertEqual(b'published', (self.root / 'managed').read_bytes())

    def test_operating_publication_never_cleans_replacement_at_freed_temp_name(self):
        from agentic import operating
        native = operating._publish_new
        replacements = []
        def publish(parent, handle, temp, leaf, expected):
            result = native(parent, handle, temp, leaf, expected)
            target = parent / temp
            target.write_bytes(b'OWNER REPLACEMENT')
            replacements.append(target)
            return result
        with safeio.Tree(self.root) as tree, patch.object(operating, '_publish_new', publish):
            operating._new(tree, 'record', b'published')
        self.assertEqual(1, len(replacements))
        self.assertEqual(b'OWNER REPLACEMENT', replacements[0].read_bytes())

    def test_unlink_refuses_replacement_after_authentication(self):
        target = self.root / 'managed'
        target.write_bytes(b'journal-bound')
        with safeio.Tree(self.root) as tree:
            tree.read('managed')
            if os.name == 'nt':
                native = safeio.win_unlink
                def substitute(path, *args, **kwargs):
                    path.rename(self.root / 'original')
                    path.write_bytes(b'OWNER REPLACEMENT')
                    return native(path, *args, **kwargs)
                with patch.object(safeio, 'win_unlink', substitute):
                    with self.assertRaisesRegex(ValidationError, 'identity|changed|replaced'):
                        tree.unlink('managed')
            else:
                target.rename(self.root / 'original')
                target.write_bytes(b'OWNER REPLACEMENT')
                with self.assertRaises(ValidationError):
                    tree.unlink('managed')
        self.assertEqual(b'OWNER REPLACEMENT', target.read_bytes())

    def test_type_inspection_cannot_rebind_an_authenticated_deletion_identity(self):
        target = self.root / 'managed'
        target.write_bytes(b'journal-bound')
        with safeio.Tree(self.root) as tree:
            tree.read('managed')
            target.rename(self.root / 'original')
            target.write_bytes(b'OWNER REPLACEMENT')
            tree.inspect('managed')  # A later type/mode query grants no ownership.
            with self.assertRaises(ValidationError):
                tree.unlink('managed')
        self.assertEqual(b'OWNER REPLACEMENT', target.read_bytes())

    def test_deletion_handle_denies_late_content_writes(self):
        target = self.root / 'managed'
        target.write_bytes(b'journal-bound')
        with safeio.Tree(self.root) as tree:
            tree.read('managed')
            if os.name != 'nt':
                with self.assertRaises(ValidationError):
                    tree.unlink('managed')
                self.assertEqual(b'journal-bound', target.read_bytes())
                return
            native = safeio._win_set_file_information
            events = []
            def disposition(*args):
                writer = safeio.kernel.CreateFileW(str(target), 0x40000000, 7,
                                                   None, 3, 0x00200000, None)
                if writer == safeio.INVALID_HANDLE:
                    events.append('pinned:' + str(safeio.ctypes.get_last_error()))
                else:
                    descriptor = safeio.msvcrt.open_osfhandle(writer, os.O_WRONLY | os.O_BINARY)
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(b'OWNER LATE WRITE')
                    events.append('modified')
                return native(*args)
            with patch.object(safeio, '_win_set_file_information', disposition):
                tree.unlink('managed')
        self.assertEqual(['pinned:32'], events)
        self.assertFalse(target.exists())

    def test_host_write_probe_never_recursively_deletes_substitution(self):
        native = shutil.rmtree
        calls = []
        def substitute(path, *args, **kwargs):
            target = Path(path)
            if target.name.startswith('.awf-write-probe-'):
                target.rename(self.root / 'original-probe')
                target.mkdir()
                (target / 'owner').write_bytes(b'OWNER DATA')
                calls.append(target)
            return native(path, *args, **kwargs)
        with patch.object(shutil, 'rmtree', substitute):
            result = host_preflight._repository_writable_row(self.root)
        self.assertEqual([], calls, 'preflight attempted pathname recursive cleanup')
        self.assertIn(result['status'], ('PASS', 'WARN'))

    def test_posix_write_probe_without_anonymous_support_leaves_no_project_residue(self):
        before = list(self.root.iterdir())
        with patch.object(host_preflight.os, 'name', 'posix'), \
                patch.object(host_preflight.os, 'O_TMPFILE', None, create=True):
            result = host_preflight._repository_writable_row(self.root)
        self.assertEqual('WARN', result['status'])
        self.assertEqual(before, list(self.root.iterdir()))

    def test_unproved_host_probe_is_a_warning_instead_of_blocking_installation(self):
        def refuse(root):
            raise ValueError('creation ownership unproved')
        result = host_preflight._repository_writable_row(self.root, probe=refuse)
        self.assertEqual('WARN', result['status'])
        self.assertEqual([], list(self.root.iterdir()))

    def test_symlink_probe_never_recursively_deletes_substitution(self):
        with patch.object(shutil, 'rmtree', side_effect=AssertionError('pathname cleanup')):
            status, detail, remedy = host_preflight.symlink_privilege()
        self.assertIn(status, ('PASS', 'WARN', 'SKIP'))

    def test_builder_metadata_is_applied_before_creating_stream_closes(self):
        owner = self.root / 'owner'
        owner.write_bytes(b'OWNER BYTES')
        owner.chmod(stat.S_IREAD)
        self.addCleanup(owner.chmod, 0o600)
        before = owner.stat().st_mode
        stage = self.root / 'stage'
        stage.mkdir()
        native = Path.open
        hits = []
        class Stream:
            def __init__(self, path, stream):
                self.path, self.stream = path, stream
            def __enter__(self):
                return self.stream.__enter__()
            def __exit__(self, *args):
                result = self.stream.__exit__(*args)
                self.path.rename(self.path.with_name('created-original'))
                os.link(owner, self.path)
                hits.append(self.path)
                return result
        def open_leaf(path, *args, **kwargs):
            stream = native(path, *args, **kwargs)
            return Stream(path, stream) if path.parent == stage and args == ('xb',) else stream
        with patch.object(Path, 'open', open_leaf):
            try:
                adoption_config._extract_locked_wheels({'fixture': {'files': {'dep.py': b'wheel'}}}, stage)
            except ValidationError:
                pass
        self.assertEqual(1, len(hits))
        self.assertEqual(before, owner.stat().st_mode)
        self.assertEqual(b'OWNER BYTES', owner.read_bytes())

    def test_runtime_inventory_streams_64_mib_plus_one(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        with (runtime / 'large').open('wb') as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        record = runtime_inventory(runtime)['entries']['large']
        self.assertEqual(64 * 1024 * 1024 + 1, record['size'])
        digest = hashlib.sha256()
        for _ in range(64):
            digest.update(b'\0' * (1024 * 1024))
        digest.update(b'\0')
        self.assertEqual(digest.hexdigest(), record['sha256'])

    def test_repeated_metadata_preflights_dispose_all_created_probes(self):
        target = self.root / 'managed'
        target.write_bytes(b'old managed')
        probes = self.root / 'external-probes'
        probes.mkdir()
        if os.name == 'nt':
            Path(str(target) + ':private').write_bytes(b'private synthetic stream')
        with patch.object(tempfile, 'tempdir', str(probes)), safeio.Tree(self.root) as tree:
            for _ in range(5):
                tree.windows_metadata('managed', preflight=True)
            self.assertEqual([], list(probes.iterdir()))
            if os.name == 'nt':
                with patch.object(safeio, '_apply_windows_metadata', side_effect=ValidationError('probe failure')):
                    with self.assertRaisesRegex(ValidationError, 'probe failure'):
                        tree.windows_metadata('managed', preflight=True)
                self.assertEqual([], list(probes.iterdir()))

    def test_repeated_real_installer_dry_runs_create_no_metadata_probes(self):
        import test_awf134_installer_journal as fixtures
        fixture = fixtures.CompactInstallerJournalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source, destination, pin, content = fixture.prepare_install(unchanged_files=0, changed_files=1)
        from agentic import installer
        native = safeio.Tree.windows_metadata
        restoration_calls = []
        def metadata(tree, relative, *, preflight=False):
            if preflight:
                restoration_calls.append(relative)
            return native(tree, relative, preflight=preflight)
        probes = self.root / 'dry-run-probes'
        probes.mkdir()
        with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                patch.object(safeio.Tree, 'windows_metadata', metadata), \
                patch.object(tempfile, 'tempdir', str(probes)):
            for _ in range(5):
                self.assertEqual('PLAN', installer.install(source, destination, pin, conflict='backup', dry_run=True)['status'])
        self.assertEqual([], restoration_calls)
        self.assertEqual([], list(probes.iterdir()))



if __name__ == '__main__':
    unittest.main()
