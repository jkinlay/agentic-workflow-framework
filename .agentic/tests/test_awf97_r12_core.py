"""Tier 3 AWF-97 PR1: complete host run-log envelopes and byte validation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError, safeio
from agentic.canonical import sha256
from agentic.providers.github_review_host import HostDriver


class CoreRecordCapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.run_id = '3dd68622-6223-48bc-b236-8ed6df9a55c2'
        self.binding = {
            'codex_run_id': '01a1317f-dadb-7071-80de-12832161734c',
            'result_sha256': 'd' * 64,
        }
        self.driver = HostDriver.__new__(HostDriver)
        self.driver.state = Path(self.directory.name)
        self.driver.codex_binding = Mock(return_value=self.binding)
        self.run_root = self.driver.state / 'runs' / self.run_id
        self.run_root.mkdir(parents=True)
        self.identity = b'{"version":1}\n'
        (self.run_root / 'codex-identity.json').write_bytes(self.identity)
        self.record_path = self.run_root / 'commit-run-log.json'
        self.cap = 1_048_576

    def tearDown(self):
        self.directory.cleanup()

    def _fit_run_log(self, size):
        # Many ordinary, unique POSIX paths keep the inventory realistic.
        entries = [
            {'path': f'src/caf\u00e9-{index:04d}.py', 'action': 'M',
             'old_mode': '100644', 'new_mode': '100644',
             'old_oid': 'a' * 40, 'new_oid': 'b' * 40,
             'old_type': 'blob', 'new_type': 'blob'}
            for index in range(2000)
        ]

        def encode():
            core = {
                'version': 1, 'controller_run_id': self.run_id,
                'codex_run_id': self.binding['codex_run_id'],
                'commit_sha': 'c' * 40, 'entries': entries,
                'identity_sha256': sha256(self.identity),
                'result_sha256': self.binding['result_sha256'],
            }
            record = {**core, 'evidence_sha256': sha256(json.dumps(
                core, ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8'))}
            return (json.dumps(record, ensure_ascii=False, indent=2,
                               sort_keys=True) + '\n').encode('utf-8')

        padding, extra = divmod(size - len(encode()), len(entries))
        self.assertGreaterEqual(padding, 0)
        for index, entry in enumerate(entries):
            entry['path'] += 'x' * (padding + (index < extra))
            self.assertLessEqual(len(entry['path'].encode('utf-8')), 255)
        raw = encode()
        self.assertEqual(len(raw), size)
        self.assertTrue(raw.endswith(b'\n'))
        return entries, raw

    def test_run_log_complete_envelope_boundary(self):
        entries, raw = self._fit_run_log(self.cap)
        record = self.driver._record_run_log(self.run_id, 'c' * 40, entries)
        self.assertEqual(self.record_path.read_bytes(), raw)
        self.assertEqual(json.loads(raw), record)
        self.assertEqual(self.driver._record_run_log(
            self.run_id, 'c' * 40, entries), record)
        self.record_path.unlink()

        entries, _ = self._fit_run_log(self.cap + 1)
        with patch.object(safeio.Tree, 'inspect', autospec=True,
                          side_effect=safeio.Tree.inspect) as inspect:
            with self.assertRaisesRegex(ValidationError, 'RECORD_SIZE'):
                self.driver._record_run_log(self.run_id, 'c' * 40, entries)
            inspect.assert_not_called()
        self.assertFalse(self.record_path.exists())

    def test_run_log_oversize_preserves_existing_evidence(self):
        self.driver._record_run_log(self.run_id, 'c' * 40, [])
        original = self.record_path.read_bytes()
        entries, _ = self._fit_run_log(self.cap + 1)
        with patch.object(safeio.Tree, 'inspect', autospec=True,
                          side_effect=safeio.Tree.inspect) as inspect:
            with self.assertRaisesRegex(ValidationError, 'RECORD_SIZE'):
                self.driver._record_run_log(self.run_id, 'c' * 40, entries)
            inspect.assert_not_called()
        self.assertEqual(self.record_path.read_bytes(), original)

    def test_record_byte_validator_is_inclusive_and_preserves_bytes(self):
        raw = b'x' * (self.cap - 1) + b'\n'
        self.assertIs(safeio.require_record_bytes(raw), raw)
        self.assertIs(safeio.require_record_bytes(b''), b'')
        with self.assertRaisesRegex(ValidationError, 'RECORD_SIZE'):
            safeio.require_record_bytes(raw + b'\n')
        for invalid in (None, '', 'x', 1, True, [], {},
                        bytearray(b'x'), memoryview(b'x')):
            with self.subTest(input_type=type(invalid).__name__):
                with self.assertRaisesRegex(ValidationError, 'RECORD_TYPE'):
                    safeio.require_record_bytes(invalid)
