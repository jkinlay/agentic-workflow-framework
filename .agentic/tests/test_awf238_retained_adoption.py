"""Retained evidence reports residue without preventing transactional reuse."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests')]
from agentic import installer
from agentic.canonical import sha256
from agentic.safeio import Tree
from agentic import safeio
import test_awf134_installer_journal as fixtures
from retained_directory_expectations import assert_rollback_evidence
CapturedIntent = fixtures.CapturedIntent


class RetainedAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.CompactInstallerJournalTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.source, self.root, self.pin, self.content = self.helper.prepare_install(
            unchanged_files=0, changed_files=1, payload_size=32)
        if os.name == 'nt':
            # These tests cover adoption, not Windows ACL restoration. Model
            # metadata on Windows; bytes and directory identities remain real.
            metadata = safeio._windows_metadata(self.root / installer.CONFIG)
            self.enterContext(patch.object(safeio, '_windows_metadata', return_value=metadata))
            self.enterContext(patch.object(safeio, '_apply_windows_metadata'))

    def retain(self, paths):
        journal = dict(format='awf-install-journal-4', transaction_id=str(uuid.uuid4()),
                       phase='rollback_cleanup', runtime=None,
                       managed_before_sha256=sha256(b'[]'), managed_after_sha256=None,
                       created_directories=[dict(path=path, identity=None) for path in paths],
                       files=[dict(path=path + '/previous.bin', old_sha256=None, old_size=None,
                                   old_mode=None, new_sha256=sha256(b'previous'), new_mode=0o600)
                              for path in paths])
        record = dict(format='awf-retained-journal-1', journal=journal,
                      retained_directories=paths)
        for path in paths:
            (self.root / path).mkdir(parents=True, exist_ok=True)
        with Tree(self.root) as tree:
            tree.write(installer.RETAINED_JOURNAL, installer.json_bytes(record))
        return record

    def install(self, **kwargs):
        with patch.object(installer, 'verify_release', return_value=(self.pin, self.content)):
            return installer.install(self.source, self.root, self.pin, conflict='backup', **kwargs)

    def test_success_adopts_only_reused_managed_directories(self):
        original = self.retain(['.agentic/adopted-clone', '.agentic/old-only'])
        before = (self.root / installer.RETAINED_JOURNAL).read_bytes()
        self.assertEqual('PLAN', self.install(dry_run=True)['status'])
        self.assertEqual(before, (self.root / installer.RETAINED_JOURNAL).read_bytes())
        self.assertEqual('INSTALLED', self.install()['status'])
        result = installer.retained_directory_status(self.root)
        self.assertEqual(['.agentic/old-only'], result['retained_directories'])
        audit = json.loads((self.root / installer.DIRECTORY_ADOPTIONS).read_bytes())
        event = audit['transactions'][0]
        self.assertEqual(['.agentic/adopted-clone'], event['directories'])
        self.assertEqual('adopted by transaction ' + event['transaction_id'], event['resolution'])
        self.assertEqual(original, event['evidence'])
        for path in original['retained_directories']:
            self.assertTrue((self.root / path).is_dir())
        before_audit = (self.root / installer.DIRECTORY_ADOPTIONS).read_bytes()
        self.assertEqual('INSTALLED', self.install()['status'])
        self.assertEqual(before_audit, (self.root / installer.DIRECTORY_ADOPTIONS).read_bytes())

    def test_all_adopted_removes_only_unresolved_evidence_file(self):
        original = self.retain(['.agentic/adopted-clone'])
        self.assertEqual('INSTALLED', self.install()['status'])
        self.assertIsNone(installer.retained_directory_status(self.root))
        self.assertFalse((self.root / installer.RETAINED_JOURNAL).exists())
        self.assertTrue((self.root / '.agentic/adopted-clone').is_dir())
        audit = json.loads((self.root / installer.DIRECTORY_ADOPTIONS).read_bytes())
        self.assertEqual(original, audit['transactions'][0]['evidence'])

    def test_stopgap_cleanup_and_pre5_evidence_merge_without_blocking(self):
        original = self.retain(['.agentic/adopted-clone'])
        backup = self.root / '.agentic/.venv.backup-prior'
        backup.mkdir()
        (backup / 'old').write_bytes(b'keep')
        with Tree(self.root) as tree:
            installer._record_cleanup_pending(tree, [backup], status='COMMITTED_CLEANUP_PENDING')
        self.assertEqual('INSTALLED', self.install()['status'])
        result = installer.retained_directory_status(self.root)
        self.assertEqual(['.agentic/.venv.backup-prior'], result['retained_directories'])
        self.assertEqual(4, result['cleanup_pending'][0]['size_bytes'])
        self.assertEqual(b'keep', (backup / 'old').read_bytes())

    def test_resolution_is_bound_and_idempotent_across_commit_interruption(self):
        self.retain(['.agentic/adopted-clone'])
        write = Tree.write

        def interrupt(tree, path, data, **kwargs):
            write(tree, path, data, **kwargs)
            if path == installer.DIRECTORY_ADOPTIONS:
                raise CapturedIntent

        with patch.object(Tree, 'write', interrupt), self.assertRaises(CapturedIntent):
            self.install()
        journal = json.loads((self.root / installer.JOURNAL).read_bytes())
        self.assertEqual(['.agentic/adopted-clone'],
                         [row['path'] for row in journal['directory_adoptions']])
        self.assertEqual('COMMITTED', installer.recover(self.root)['status'])
        audit = json.loads((self.root / installer.DIRECTORY_ADOPTIONS).read_bytes())
        self.assertEqual(1, len(audit['transactions']))
        self.assertEqual(journal['transaction_id'], audit['transactions'][0]['transaction_id'])
        self.assertFalse((self.root / installer.RETAINED_JOURNAL).exists())

    def test_failed_transaction_does_not_adopt_and_keeps_exact_prior_evidence(self):
        original = self.retain(['.agentic/adopted-clone'])
        rollback = installer.rollback
        observed = {}

        def capture(tree, journal):
            result = rollback(tree, journal)
            observed.update(json.loads(installer.json_bytes(journal)))
            return result

        with patch.object(installer, 'rollback', capture), self.assertRaisesRegex(OSError, 'Injected installation failure'):
            self.install(fail_after=1)
        self.assertFalse((self.root / installer.DIRECTORY_ADOPTIONS).exists())
        assert_rollback_evidence(self, self.root, observed, original)
        self.assertTrue((self.root / '.agentic/adopted-clone').is_dir())

    def test_changed_directory_identity_stays_reported_without_refusing_install(self):
        self.retain(['.agentic/adopted-clone'])
        identity = installer._directory_identity
        calls = []

        def changed(tree, path):
            value = identity(tree, path)
            if path == '.agentic/adopted-clone':
                calls.append(value)
                if len(calls) > 1:
                    return ['f' * 32, 'f' * 32]
            return value

        with patch.object(installer, '_directory_identity', changed):
            self.assertEqual('INSTALLED', self.install()['status'])
        self.assertEqual(['.agentic/adopted-clone'],
                         installer.retained_directory_status(self.root)['retained_directories'])
        self.assertFalse((self.root / installer.DIRECTORY_ADOPTIONS).exists())

    def test_rebootstrap_with_committed_runtime_backups_never_refuses(self):
        def build(transaction):
            transaction.stage.mkdir()
            (transaction.stage / 'runtime').write_bytes(payload)
            transaction.record_staged_runtime()
            if transaction.had_previous:
                os.replace(transaction.runtime_root, transaction.backup)
            os.replace(transaction.stage, transaction.runtime_root)
            return {'built': True}

        for index in range(3):
            payload = str(index).encode('ascii')
            installed = self.install(defer_runtime=True)
            result = installer.complete_runtime_transaction(
                self.root, installed['_runtime_transaction_id'], build)
            self.assertTrue(result['built'])
            self.assertFalse((self.root / installer.JOURNAL).exists())
            self.assertFalse((self.root / installer.MARKER).exists())
            self.assertEqual(payload, (self.root / installer.RUNTIME / 'runtime').read_bytes())
            if index == 0:
                self.assertEqual('COMPLETE', result['transaction_cleanup'])
            else:
                self.assertEqual('COMMITTED_CLEANUP_PENDING', result['transaction_cleanup'])
                self.assertEqual(index, len(result['installation_recovery']['retained_directories']))
                for retained in result['installation_recovery']['retained_directories']:
                    self.assertTrue((self.root / retained / 'runtime').is_file())


if __name__ == '__main__':
    unittest.main()
