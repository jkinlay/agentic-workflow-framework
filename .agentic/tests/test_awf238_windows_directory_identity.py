"""Directory ownership must not depend on Python's Windows stat representation."""
import ast
from contextlib import contextmanager
import ctypes
import inspect
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError
from agentic import installer, safeio


# Synthetic values deliberately exercise both truncated high halves.
FULL = ['00000000000000008877665544332211',
        '112233445566778899aabbccddeeff00']
TRUNCATED = ['00000000000000000000000044332211',
             '000000000000000099aabbccddeeff00']


class WindowsDirectoryIdentityTests(unittest.TestCase):
    def test_shared_api_reads_full_volume_and_128_bit_id(self):
        class Attributes(ctypes.Structure):
            _fields_ = [('attributes', ctypes.c_uint32)]

        def attributes(handle, output):
            output._obj.attributes = 0x10
            return True

        def file_id(handle, information_class, output, size):
            self.assertEqual(18, information_class)  # FileIdInfo
            self.assertEqual(ctypes.sizeof(output._obj), size)
            output._obj.volume = int(FULL[0], 16)
            output._obj.file_id[:] = int(FULL[1], 16).to_bytes(16, 'little')
            return True

        kernel = Mock()
        kernel.GetFileInformationByHandle.side_effect = attributes
        kernel.GetFileInformationByHandleEx.side_effect = file_id
        with patch.object(safeio, 'kernel', kernel, create=True), \
                patch.object(safeio, 'FileInformation', Attributes, create=True):
            self.assertEqual(FULL, safeio.windows_directory_identity(456))
        kernel.GetFileInformationByHandleEx.assert_called_once()

    @contextmanager
    def windows(self):
        # Explicitly model 3.11 even when the test runs on a newer interpreter
        # or POSIX. No real Windows calls or filesystem privileges are needed.
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700,
                               st_file_attributes=0x10,
                               st_dev=int(TRUNCATED[0], 16),
                               st_ino=int(TRUNCATED[1], 16))
        platform = SimpleNamespace(name='nt', lstat=Mock(return_value=info))
        kernel = Mock()
        tree = Mock()
        tree.parent.return_value = (Path('.'), 123, 'owned')
        with patch.object(installer, 'os', platform), \
                patch.object(installer, '_windows_directory_identity', return_value=FULL), \
                patch.object(safeio, 'win_open', return_value=456, create=True), \
                patch.object(safeio, 'kernel', kernel, create=True):
            yield tree, platform, kernel

    def journal(self, identity=None):
        return {'format': 'awf-install-journal-4',
                'transaction_id': str(uuid.uuid4()), 'phase': 'object_prepare',
                'runtime': None, 'files': [],
                'managed_before_sha256': 'a' * 64, 'managed_after_sha256': None,
                'created_directories': [{'path': 'owned', 'identity': identity}]}

    def test_capture_and_lookup_agree_with_311_shaped_stat(self):
        with self.windows() as (tree, platform, _):
            journal = self.journal()
            with patch.object(installer, '_windows_create_directory', return_value=FULL), \
                    patch.object(installer, '_write_bound_journal_update'):
                installer._prepare_transaction_directories(tree, journal)
            self.assertEqual(FULL, journal['created_directories'][0]['identity'])
            self.assertEqual(FULL, installer._directory_identity(tree, 'owned'))
            platform.lstat.assert_not_called()

    def test_old_pending_directory_prepare_reconstructs_exact_digest_without_ownership(self):
        with self.windows() as (tree, _, _):
            journal = self.journal()
            candidate = installer._copy_journal(journal)
            candidate['created_directories'][0]['identity'] = FULL
            raw = installer.json_bytes(journal)
            marker = installer._journal_marker(journal['transaction_id'],
                installer.sha256(raw), installer.sha256(installer.json_bytes(candidate)),
                'directory_prepare')
            result = installer._recover_pending_journal_update(tree, journal, raw, marker)
            self.assertIsNone(result['created_directories'][0]['identity'])
            tree.write.assert_called_once()
            self.assertEqual(installer.MARKER, tree.write.call_args.args[0])

    def test_new_pending_directory_prepare_verifies_handle_identity(self):
        with self.windows() as (tree, _, _):
            installer._validate_pending_new_journal(tree, self.journal(FULL), 'directory_prepare')

    def test_cleanup_uses_same_handle_identity_including_final_deletion(self):
        with self.windows() as (tree, platform, kernel), \
                patch.object(safeio, 'INVALID_HANDLE', -1, create=True), \
                patch.object(safeio, 'FileDispositionInformation', create=True), \
                patch.object(safeio, 'FILE_DISPOSITION_INFO', 4, create=True), \
                patch.object(safeio, '_win_set_file_information', create=True) as delete:
            kernel.CreateFileW.return_value = 789
            self.assertEqual([], installer._cleanup_transaction_directories(tree, self.journal(FULL)))
            delete.assert_called_once()
            platform.lstat.assert_not_called()

    def test_existing_full_identity_is_compatible_truncated_identity_refuses(self):
        with self.windows() as (tree, _, _):
            installer._validate_pending_new_journal(tree, self.journal(FULL), 'directory_prepare')
            with self.assertRaisesRegex(ValidationError, 'Created directory changed'):
                installer._cleanup_transaction_directories(tree, self.journal(TRUNCATED))

    def test_directory_lookup_has_no_stat_identity_builder(self):
        # Guard the ownership entry point itself, including accidental fallbacks.
        node = ast.parse(inspect.getsource(installer._directory_identity))
        self.assertEqual([], [item.attr for item in ast.walk(node)
                              if isinstance(item, ast.Attribute)
                              and item.attr in {'st_dev', 'st_ino'}])

    def test_runtime_root_and_member_directory_use_handle_source(self):
        with self.windows() as (_, platform, _):
            entry = Mock(name='entry')
            entry.name, entry.path = 'child', 'runtime/child'
            entry.stat.return_value = platform.lstat.return_value
            platform.scandir = Mock(side_effect=[[entry], []])
            proof = installer._runtime_cleanup_proof('runtime')
            self.assertEqual(FULL, proof['identity'])
            self.assertEqual(FULL, proof['members'][0][3])

    def test_legacy_runtime_stat_proof_refuses_safely(self):
        with self.windows() as (_, platform, _):
            platform.scandir = Mock(return_value=[])
            legacy = {'identity': [format(int(value, 16), '016x') for value in TRUNCATED],
                      'members': []}
            with patch.object(installer.Path, 'exists', return_value=True), \
                    self.assertRaisesRegex(ValidationError, 'directory identity changed'):
                installer._verify_runtime_cleanup_proof('runtime', legacy)

    def test_runtime_journal_reads_legacy_and_full_directory_proofs_without_conversion(self):
        journal = self.journal()
        transaction = journal['transaction_id']
        runtime = {'path': installer.RUNTIME,
                   'stage': installer.RUNTIME + '.staging-' + transaction,
                   'backup': installer.RUNTIME + '.backup-' + transaction,
                   'previous_sha256': 'a' * 64, 'new_sha256': 'b' * 64,
                   'cleanup_identity': FULL,
                   'cleanup_members': [['child', 'directory', 0o700, FULL, None]]}
        journal['runtime'] = runtime
        for identity in (FULL, ['0000000044332211', '99aabbccddeeff00']):
            with self.subTest(identity=identity):
                runtime['cleanup_identity'] = identity
                runtime['cleanup_members'][0][3] = identity
                self.assertIs(runtime, installer._validated_runtime_journal(journal))
                self.assertEqual(identity, runtime['cleanup_identity'])
        runtime['cleanup_identity'] = [FULL[0], '99aabbccddeeff00']
        with self.assertRaisesRegex(ValidationError, 'Invalid authenticated cleanup identity'):
            installer._validated_runtime_journal(journal)


if __name__ == '__main__':
    unittest.main()
