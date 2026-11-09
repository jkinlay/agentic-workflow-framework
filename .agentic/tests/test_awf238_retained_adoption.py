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
from agentic import installer, ValidationError
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

    def test_history_storage_stays_bounded_without_losing_sources(self):
        from copy import deepcopy
        sources = []
        for index in range(3):
            record = self.retain(['.agentic/retained-' + str(index)]) if index == 0 else deepcopy(sources[0])
            path = '.agentic/retained-' + str(index)
            record['retained_directories'] = [path]
            record['journal']['transaction_id'] = str(uuid.uuid4())
            record['journal']['created_directories'] = [dict(path=path, identity=None)]
            record['journal']['files'] = [dict(path=path + '/' + str(n), old_sha256=None,
                old_size=None, old_mode=None, new_sha256='a' * 64, new_mode=0o600) for n in range(10000)]
            sources.append(record)
        (self.root / installer.RETAINED_JOURNAL).unlink()
        with Tree(self.root) as tree:
            for record in sources:
                installer._merge_retained_evidence(tree, record)
                self.assertLessEqual(len(tree.read(installer.RETAINED_JOURNAL)), 1024 * 1024)
            result = installer.retained_directory_status(self.root)
            self.assertEqual([r['retained_directories'][0] for r in sources], result['retained_directories'])
            stored = installer._read_retained_record(tree)
            self.assertEqual(sources, installer._retained_sources(stored))
            # Adoption of large evidence must also retain bounded audit documents.
            for record in sources:
                path = record['retained_directories'][0]
                (self.root / path).mkdir(exist_ok=True)
                installer._adopt_transaction_directories(tree, dict(transaction_id=str(uuid.uuid4()),
                    directory_adoptions=[dict(path=path, identity=installer._directory_identity(tree, path),
                        evidence_sha256=sha256(installer.json_bytes(record)))]))
                self.assertLessEqual(len(tree.read(installer.DIRECTORY_ADOPTIONS)), 1024 * 1024)
            audit = installer._directory_adoption_audit(tree.read(installer.DIRECTORY_ADOPTIONS), tree=tree)
            self.assertEqual(3, len(audit['transactions']))
            self.assertEqual(sources, installer._retained_sources(audit['transactions'][0]['evidence']))
            self.assertIsNone(installer.retained_directory_status(self.root))
            for record in sources:
                self.assertTrue((self.root / record['retained_directories'][0]).is_dir())
            head = installer._evidence_list_put(tree, list(range(300)))
            self.assertEqual(list(range(300)), installer._evidence_list_get(tree, head))
            first_source = installer.EVIDENCE_OBJECTS + '/' + sha256(installer.json_bytes(sources[0])) + '.json'
            for path in tree.file_list(subtree=installer.EVIDENCE_OBJECTS):
                raw = tree.read(path)
                self.assertLessEqual(len(raw), 8 * 1024 * 1024)
                installer.loads(raw.decode('utf-8'))
            tree.write(first_source, b'{}')
            with self.assertRaisesRegex(ValidationError, 'digest mismatch'):
                installer._directory_adoption_audit(tree.read(installer.DIRECTORY_ADOPTIONS), tree=tree)

    def test_successful_recovery_includes_earlier_unresolved_inventory(self):
        self.retain(['.agentic/old-only'])
        native = Tree.unlink
        for journal_only in (False, True):
            with self.subTest(journal_only=journal_only):
                def interrupt(tree, path):
                    if path == (installer.JOURNAL if journal_only else installer.MARKER):
                        raise CapturedIntent
                    return native(tree, path)
                with patch.object(Tree, 'unlink', interrupt), self.assertRaises(CapturedIntent):
                    self.install()
                result = installer.recover(self.root)
                self.assertEqual('COMMITTED', result['status'])
                self.assertEqual(['.agentic/old-only'], result['retained_directories'])
                self.assertEqual(installer.retained_directory_status(self.root), result['installation_recovery'])
                self.assertFalse((self.root / installer.JOURNAL).exists())
                self.assertFalse((self.root / installer.MARKER).exists())

    def test_long_path_evidence_pages_preserve_every_valid_entry(self):
        class MemoryTree:
            def __init__(self): self.files = {}
            def inspect(self, path): return path in self.files or None
            def read(self, path, **kwargs): return self.files[path]
            def write(self, path, raw): self.files[path] = raw
            write_exclusive = write
        # Relative components obey the schema; no filesystem long-path support
        # is needed to preserve this parser-valid historical source evidence.
        paths = ['.agentic/' + '/'.join(['x' * 120] * 75) + '/' + str(n) for n in range(128)]
        journal = dict(format='awf-install-journal-4', transaction_id=str(uuid.uuid4()),
            phase='rollback_cleanup', runtime=None, managed_before_sha256=sha256(b'[]'),
            managed_after_sha256=None, created_directories=[dict(path=p, identity=None) for p in paths],
            files=[dict(path=p + '/old', old_sha256=None, old_size=None, old_mode=None,
                        new_sha256='a' * 64, new_mode=0o600) for p in paths])
        record = dict(format='awf-retained-journal-1', journal=journal, retained_directories=paths)
        installer._retained_record_result(installer.loads(installer.json_bytes(record).decode()))
        tree = MemoryTree()
        packed = installer._pack_retained_record(tree, record)
        restored = installer._unpack_retained_record(tree, packed)
        self.assertEqual([record], installer._retained_sources(restored))
        self.assertEqual(paths, restored['retained_directories'])
        for raw in tree.files.values():
            value = installer.loads(raw.decode())
            if value.get('format') == 'awf-evidence-page-1':
                self.assertLessEqual(len(raw), installer._EVIDENCE_INLINE_MAXIMUM)
                self.assertLessEqual(len(value['items']), 128)

    def test_oversized_source_is_split_without_blocking_transaction_operations(self):
        path = '.agentic/old-only'
        record = self.retain([path])
        # Synthetic historical entries; no long filesystem names are created.
        prefix = path + '/' + '/'.join(['x' * 120] * 71 + ['y' * 73])
        record['journal']['files'] = [dict(path=prefix + '/%04d/old' % n,
            old_sha256=None, old_size=None, old_mode=None, new_sha256='a' * 64,
            new_mode=0o600) for n in range(1000)]
        raw = installer.json_bytes(record)
        self.assertGreater(len(raw), 8 * 1024 * 1024)
        installer._retained_record_result(record)
        (self.root / installer.RETAINED_JOURNAL).unlink()
        with Tree(self.root) as tree:
            installer._merge_retained_evidence(tree, record)
            restored = installer._read_retained_record(tree)
            self.assertEqual([record], installer._retained_sources(restored))
            self.assertEqual(sha256(raw), sha256(installer.json_bytes(
                installer._retained_sources(restored)[0])))
            for relative in tree.file_list(subtree=installer.EVIDENCE_OBJECTS):
                evidence = tree.read(relative)
                self.assertLessEqual(len(evidence), 8 * 1024 * 1024)
                installer.loads(evidence.decode('utf-8'))
            packed = installer.loads(tree.read(installer.RETAINED_JOURNAL).decode('utf-8'))
            reference = installer._evidence_list_get(tree, packed['records'])[0]
            envelope = installer._evidence_get(tree, reference)
            self.assertEqual('awf-evidence-source-1', envelope['format'])
            self.assertEqual(sha256(raw), envelope['source_sha256'])
            self.assertEqual(len(raw), envelope['source_size'])
            envelope['source_sha256'] = 'f' * 64
            forged = installer._evidence_put(tree, envelope)
            with self.assertRaisesRegex(ValidationError, 'source binding mismatch'):
                installer._evidence_source_get(tree, forged)
            tree.unlink(installer.EVIDENCE_OBJECTS + '/' + forged + '.json')
        for operation in (self.install, self.install, lambda: installer.recover(self.root)):
            operation()
            self.assertEqual([path], installer.retained_directory_status(self.root)['retained_directories'])
        with Tree(self.root) as tree:
            installer._adopt_transaction_directories(tree, dict(transaction_id=str(uuid.uuid4()),
                directory_adoptions=[dict(path=path, identity=installer._directory_identity(tree, path),
                    evidence_sha256=sha256(raw))]))
            audit = installer._directory_adoption_audit(tree.read(installer.DIRECTORY_ADOPTIONS), tree=tree)
            self.assertEqual([record], installer._retained_sources(audit['transactions'][0]['evidence']))
            self.assertTrue((self.root / path).is_dir())

    def test_single_oversized_page_item_keeps_exact_value_and_parser_limit(self):
        class MemoryTree:
            def __init__(self): self.files = {}
            def inspect(self, path): return path in self.files or None
            def read(self, path, **kwargs): return self.files[path]
            def write(self, path, raw): self.files[path] = raw
            write_exclusive = write
        value = ('\u0001\U0001f642' * (1024 * 1024))
        tree = MemoryTree()
        with self.assertRaisesRegex(ValidationError, 'Document exceeds 8 MiB'):
            installer.loads(installer.json_bytes(value).decode('utf-8'))
        head = installer._evidence_list_put(tree, [value])
        self.assertEqual([value], installer._evidence_list_get(tree, head))
        for raw in tree.files.values():
            self.assertLessEqual(len(raw), 8 * 1024 * 1024)
            installer.loads(raw.decode('utf-8'))

    def test_evidence_replace_crash_has_only_bound_recoverable_temporaries(self):
        import base64
        import subprocess
        record = self.retain(['.agentic/adopted-clone'])
        record['journal']['files'] = [dict(path='.agentic/adopted-clone/' + str(n),
            old_sha256=None, old_size=None, old_mode=None, new_sha256='a' * 64,
            new_mode=0o600) for n in range(10000)]
        (self.root / installer.RETAINED_JOURNAL).write_bytes(installer.json_bytes(record))
        content = {p: base64.b64encode(raw).decode() for p, raw in self.content.items()}
        code = r'''
import base64,json,os,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()/'.agentic/lib'))
from agentic import installer,safeio
root=Path(sys.argv[1]); source=Path(sys.argv[2]); pin=sys.argv[3]
content={p:base64.b64decode(raw) for p,raw in json.loads(sys.argv[4]).items()}
replace=os.replace
write=safeio.Tree.write
evidence_write=False
def publish(tree,relative,data,*args,**kwargs):
    global evidence_write
    previous=evidence_write
    evidence_write=relative.startswith(installer.EVIDENCE_OBJECTS+'/')
    try:
        return write(tree,relative,data,*args,**kwargs)
    finally:
        evidence_write=previous
def interrupt(src,dst,*args,**kwargs):
    # POSIX replace uses a basename plus dir_fd; Windows uses an absolute path.
    if evidence_write:
        os._exit(73)
    return replace(src,dst,*args,**kwargs)
with patch.object(installer,'verify_release',return_value=(pin,content)), patch.object(os,'replace',interrupt), patch.object(safeio.Tree,'write',publish):
    if os.name=='nt':
        metadata=safeio._windows_metadata(root/installer.CONFIG)
        with patch.object(safeio,'_windows_metadata',return_value=metadata), patch.object(safeio,'_apply_windows_metadata'):
            installer.install(source,root,pin,conflict='backup')
    else:
        installer.install(source,root,pin,conflict='backup')
'''
        child = subprocess.run([sys.executable, '-B', '-c', code, str(self.root), str(self.source),
                                self.pin, json.dumps(content)], capture_output=True, text=True)
        self.assertEqual(73, child.returncode, child.stderr)
        journal = json.loads((self.root / installer.JOURNAL).read_bytes())
        residue = {p.relative_to(self.root).as_posix() for p in self.root.rglob('*.tmp')}
        self.assertEqual(1, len(residue))
        self.assertLessEqual(residue, set(installer._transaction_temporary_paths(journal).values()))
        lookalike = self.root / installer.EVIDENCE_OBJECTS / ('.awf-' + 'f' * 32 + '.tmp')
        self.assertNotIn(lookalike.relative_to(self.root).as_posix(), residue)
        lookalike.write_bytes(b'owner look-alike; preserve exactly')
        self.assertEqual('COMMITTED', installer.recover(self.root)['status'])
        self.assertEqual([lookalike], list(self.root.rglob('*.tmp')))
        self.assertEqual(b'owner look-alike; preserve exactly', lookalike.read_bytes())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())
        with Tree(self.root) as tree:
            audit = installer._directory_adoption_audit(tree.read(installer.DIRECTORY_ADOPTIONS), tree=tree)
        self.assertEqual([record], installer._retained_sources(audit['transactions'][0]['evidence']))

    def test_evidence_temporary_collision_refuses_before_payload(self):
        identity = uuid.uuid4()
        target = installer.EVIDENCE_OBJECTS + '/object.json'
        relative = installer.EVIDENCE_OBJECTS + '/.awf-' + uuid.uuid5(identity, target).hex + '.tmp'
        path = self.root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b'preexisting owner bytes')
        before = self.helper.snapshot_project(self.root)
        with patch.object(installer.uuid, 'uuid4', return_value=identity), \
                self.assertRaisesRegex(ValidationError, 'temporary path already exists'):
            self.install()
        self.assertEqual(before, self.helper.snapshot_project(self.root))
        self.assertEqual(b'preexisting owner bytes', path.read_bytes())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())

    def test_standalone_evidence_publication_never_truncates_existing_leaf(self):
        value = dict(format='synthetic-evidence', retained=['owner bytes'])
        raw = installer.json_bytes(value)
        relative = installer.EVIDENCE_OBJECTS + '/' + sha256(raw) + '.json'
        with Tree(self.root) as tree:
            self.assertEqual(sha256(raw), installer._evidence_put(tree, value))
            self.assertEqual(raw, tree.read(relative))
            with self.assertRaises(FileExistsError):
                tree.write_exclusive(relative, b'destructive replacement')
            self.assertEqual(raw, tree.read(relative))
        self.assertFalse(any(self.root.rglob('*.tmp')))

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
