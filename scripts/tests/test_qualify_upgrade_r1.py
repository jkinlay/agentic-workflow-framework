"""Red-first round-one probe, canonical hash and retained-tree regressions."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import installer, ValidationError
from agentic.preservation_metadata import runtime_tree_sha256, inventory
import qualify_upgrade as qualification
from test_qualify_upgrade import simulated_runtime_links


class RoundOneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def probe_substitution(self, *, hardlink=False, directory=False):
        project = self.root / 'project'
        project.mkdir()
        owner = self.root / 'owner'
        owner.write_bytes(b'OWNER DATA')
        if hardlink:
            owner.chmod(stat.S_IREAD)
            self.addCleanup(owner.chmod, 0o600)
        before = owner.stat().st_mode
        original = qualification._named_streams
        substituted = []

        def capture(probe):
            result = original(probe)
            try:
                probe.rename(probe.with_name(probe.name + '-original'))
                if directory:
                    probe.mkdir()
                    (probe / 'owner').write_bytes(b'OWNER DATA')
                elif hardlink:
                    os.link(owner, probe)
                else:
                    probe.write_bytes(b'OWNER DATA')
                substituted.append(probe)
            except PermissionError:
                pass  # Native Windows deny-delete sharing pins the original.
            return result

        with patch.object(qualification, '_named_streams', capture):
            try:
                qualification._creation_state(project, 'new/leaf' + ('/' if directory else ''),
                                              directory=directory)
            except (ValueError, OSError):
                pass
        self.assertEqual(b'OWNER DATA', owner.read_bytes())
        self.assertEqual(before, owner.stat().st_mode)
        for probe in substituted:
            self.assertTrue(probe.exists(), 'probe cleanup deleted a substituted owner object')
            if directory:
                self.assertEqual(b'OWNER DATA', (probe / 'owner').read_bytes())

    def test_probe_cleanup_preserves_substituted_file(self):
        self.probe_substitution()

    def test_probe_cleanup_preserves_substituted_directory(self):
        self.probe_substitution(directory=True)

    def test_probe_metadata_preserves_substituted_hardlink_readonly_bit(self):
        self.probe_substitution(hardlink=True)

    def test_probe_directory_is_not_reopened_to_claim_a_replacement(self):
        project = self.root / 'project'
        project.mkdir()
        replacements = []
        hits = []
        if os.name == 'nt':
            original = qualification._windows_probe_directory
            def create(parent, name):
                handle = original(parent, name)
                hits.append(name)
                path = project / name
                try:
                    path.rename(path.with_name(path.name + '-original'))
                    path.mkdir()
                    replacements.append(path)
                except PermissionError:
                    pass  # The actual creating handle denies replacement.
                return handle
            seam = patch.object(qualification, '_windows_probe_directory', create)
        else:
            original = os.mkdir
            def create(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if Path(path).name.startswith('.awf-permission-probe-'):
                    hits.append(path)
                    parent = os.dup(kwargs['dir_fd'])
                    self.addCleanup(os.close, parent)
                    os.rename(path, str(path) + '-original', src_dir_fd=parent, dst_dir_fd=parent)
                    original(path, dir_fd=parent)
                    replacements.append((parent, path, os.stat(path, dir_fd=parent).st_ino))
                return result
            seam = patch.object(os, 'mkdir', create)
        with seam:
            try:
                qualification._creation_state(project, 'new/', directory=True)
            except (ValueError, OSError):
                pass
        self.assertTrue(hits, 'the actual directory creation seam was never exercised')
        for path in replacements:
            if os.name == 'nt':
                self.assertTrue(path.exists(), 'directory reopen claimed and deleted an owner replacement')
            else:
                parent, name, inode = path
                self.assertEqual(inode, os.stat(name, dir_fd=parent, follow_symlinks=False).st_ino,
                                 'directory reopen claimed and deleted an owner replacement')

    def test_large_unicode_empty_directory_hashes_share_one_encoding(self):
        runtime = self.root / 'runtime'
        (runtime / '\u03b4-empty').mkdir(parents=True)
        target = runtime / '\u6587\u4ef6'
        # A real streamed 64 MiB + 1 byte endpoint, not a raised Tree.read cap.
        with target.open('wb') as stream:
            for _ in range(64):
                stream.write(b'x' * (1024 * 1024))
            stream.write(b'y')
        # Independent encoding oracle keeps delegation from making equality a
        # tautology, including root and empty-directory records and Unicode.
        contents = hashlib.sha256()
        for _ in range(64):
            contents.update(b'x' * (1024 * 1024))
        contents.update(b'y')
        encoded = hashlib.sha256()
        for record in (['.', 'directory', stat.S_IMODE(runtime.stat().st_mode)],
                       ['\u03b4-empty', 'directory', stat.S_IMODE((runtime / '\u03b4-empty').stat().st_mode)],
                       ['\u6587\u4ef6', 'file', stat.S_IMODE(target.stat().st_mode),
                        64 * 1024 * 1024 + 1, contents.hexdigest()]):
            raw = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            encoded.update(len(raw).to_bytes(8, 'big'))
            encoded.update(raw)
        expected = encoded.hexdigest()
        self.assertEqual(expected, installer._runtime_tree_sha256(runtime))
        self.assertEqual(expected, runtime_tree_sha256(runtime))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, qualification.main(['runtime-hash', '--path', str(runtime),
                '--expected-sha256', expected]))

    def test_hashes_both_refuse_hardlinks_with_independent_copy_remedy(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'file').write_bytes(b'owner')
        os.link(runtime / 'file', self.root / 'alias')
        for hash_tree in (installer._runtime_tree_sha256, runtime_tree_sha256):
            with self.subTest(function=hash_tree.__module__):
                with self.assertRaisesRegex(ValueError, 'independent regular copy'):
                    hash_tree(runtime)
        self.assertEqual(b'owner', (self.root / 'alias').read_bytes())

    def test_executable_extension_preserves_directory_enumeration_encoding(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        target = runtime / 'activate.bat'
        target.write_bytes(b'@echo off\n')
        with os.scandir(runtime) as scan:
            info = next(scan).stat(follow_symlinks=False)
        expected = hashlib.sha256()
        for record in (['.', 'directory', stat.S_IMODE(runtime.stat().st_mode)],
                       ['activate.bat', 'file', stat.S_IMODE(info.st_mode), info.st_size,
                        hashlib.sha256(target.read_bytes()).hexdigest()]):
            raw = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode()
            expected.update(len(raw).to_bytes(8, 'big'))
            expected.update(raw)
        self.assertEqual(expected.hexdigest(), runtime_tree_sha256(runtime))
        self.assertEqual(expected.hexdigest(), installer._runtime_tree_sha256(runtime))

    def test_hashes_both_refuse_windows_symlink_reparse_observation(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'lib64').write_bytes(b'lib')
        with simulated_runtime_links({runtime}, windows_reparse=True):
            for hash_tree in (installer._runtime_tree_sha256, runtime_tree_sha256):
                with self.subTest(function=hash_tree.__module__):
                    with self.assertRaisesRegex(ValueError, 'reparse points'):
                        hash_tree(runtime)

    def test_each_retained_runtime_form_is_authenticated_without_exemption(self):
        for suffix in ('.venv.backup-11111111-1111-4111-8111-111111111111',
                       '.venv.backup-11111111111141118111111111111111',
                       '.venv.staging-11111111-1111-4111-8111-111111111111',
                       '.venv.cleanup-11111111-1111-4111-8111-111111111111',
                       '.venv.staging-11111111111141118111111111111111',
                       '.venv.cleanup-11111111111141118111111111111111'):
            with self.subTest(suffix=suffix):
                project = self.root / suffix
                (project / '.agentic').mkdir(parents=True)
                (project / '.agentic/PROJECT_CONFIG.yaml').write_text(json.dumps({'github': {
                    'repository': 'example/repository', 'repository_id': 101, 'base_branch': 'main'}}))
                retained = project / '.agentic' / suffix
                (retained / 'lib').mkdir(parents=True)
                (retained / 'lib/file').write_bytes(b'original')
                (retained / 'lib64').write_bytes(b'lib')
                with simulated_runtime_links({retained}):
                    before = inventory(project)
                    key = '.agentic/' + suffix + '/lib64'
                    self.assertEqual('lib', before['files'][key]['link'])
                    (retained / 'lib/file').write_bytes(b'tamper')
                    self.assertNotEqual(before, inventory(project))

    def test_retained_lookalike_suffixes_nested_names_and_linked_roots_refuse(self):
        for index, name in enumerate(('.venv.backup-' + '1' * 31,
                                      '.venv.staging-' + '1' * 33,
                                      '.venv.cleanup-11111111-1111-4111-8111-111111111111.extra',
                                      'nested/.venv.staging-11111111111141118111111111111111')):
            with self.subTest(name=name):
                project = self.root / str(index)
                retained = project / '.agentic' / name
                retained.mkdir(parents=True)
                (retained / 'lib64').write_bytes(b'lib')
                with simulated_runtime_links({retained}):
                    with self.assertRaisesRegex(ValueError, 'Linked/reparse inventory member refused'):
                        inventory(project)
        project = self.root / 'root-link'
        retained = project / '.agentic/.venv.backup-11111111-1111-4111-8111-111111111111'
        retained.mkdir(parents=True)
        (retained / 'lib64').write_bytes(b'lib')
        original = Path.lstat
        def root_link(path, *args, **kwargs):
            if os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(retained)):
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_reparse_tag=0)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', root_link):
            with self.assertRaisesRegex(ValueError, 'Linked/reparse inventory member refused'):
                inventory(project)

    def test_documented_file_authentication_reports_metadata_and_exit_codes(self):
        target = self.root / 'legacy-backup-file'
        target.write_bytes(b'journal-recorded backup')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        before = target.stat()
        command = [sys.executable, '-B', str(ROOT / 'scripts/qualify_upgrade.py'),
                   'file-hash', '--path', str(target), '--expected-sha256']
        for expected, code in ((digest, 0), ('f' * 64, 1), ('malformed', 2)):
            result = subprocess.run(command + [expected], capture_output=True, text=True, timeout=60)
            self.assertEqual(code, result.returncode, result.stdout + result.stderr)
            if code != 2:
                record = json.loads(result.stdout)
                self.assertEqual(digest, record['sha256'])
                self.assertEqual(stat.S_IMODE(before.st_mode), record['mode'])
                self.assertTrue(record['permissions']['verified'])
                self.assertTrue(record['named_streams']['verified'])
                self.assertFalse(record['execution_authority'])
        self.assertEqual(before.st_mode, target.stat().st_mode)
        self.assertEqual(b'journal-recorded backup', target.read_bytes())


if __name__ == '__main__':
    unittest.main()
