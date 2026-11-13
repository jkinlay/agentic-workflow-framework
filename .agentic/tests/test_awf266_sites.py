"""Real publication/retirement dispatch, including the allowlist reproductions."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError, adoption_config as adoption, installer, operating, safeio, streams

ROOT = Path(__file__).resolve().parents[2]


class Interrupted(BaseException):
    pass


def substitute(path, raw=b'owner replacement', *, directory=False):
    path = Path(path)
    original = path.with_name(path.name + '-original')
    if path.exists():
        path.rename(original)
    if directory:
        path.mkdir()
        (path / 'owner').write_bytes(raw)
    else:
        path.write_bytes(raw)
    return original


class SiteTests(unittest.TestCase):
    def test_operating_publication_checks_the_callers_pinned_parent(self):
        # Model POSIX fd identity on Windows as well; native proof is separate.
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                parent = Path(temporary).resolve()
                (parent / 'source').write_bytes(b'awf')
                with safeio.Tree(parent) as tree:
                    expected = tree.entry_identity('source')
                operating_os = SimpleNamespace(**{name: getattr(os, name) for name in dir(os) if not name.startswith('__')})
                operating_os.name = 'posix'
                operating_os.fstat = lambda fd: SimpleNamespace(st_dev=1, st_ino=(2 if fd == -123 else 3) if replacement else 2)
                rename = Mock(return_value=0)
                with patch.object(operating, 'os', operating_os), \
                        patch.object(operating, 'sys', SimpleNamespace(platform='linux')), \
                        patch('ctypes.CDLL', return_value=SimpleNamespace(renameat2=rename)):
                    if replacement:
                        with self.assertRaisesRegex(ValidationError, 'parent identity changed'):
                            operating._publish_new(parent, -123, 'source', 'destination', expected)
                        rename.assert_not_called()
                    else:
                        operating._publish_new(parent, -123, 'source', 'destination', expected)
                        rename.assert_called_once()
                self.assertEqual(b'awf', (parent / 'source').read_bytes())
                self.assertFalse((parent / 'destination').exists())

    def test_operating_publication_parent_substitution_keeps_both_names(self):
        parent = self.root / 'parent'; parent.mkdir()
        parked = self.root / 'parked'
        with safeio.Tree(parent) as tree:
            tree.write_exclusive('source', b'awf')
            _, handle, _ = tree.parent('source')
            expected = tree._authenticated['source']
            if os.name == 'nt':
                with self.assertRaises(PermissionError): parent.rename(parked)
                operating._publish_new(parent, handle, 'source', 'destination', expected)
                self.assertEqual(b'awf', (parent / 'destination').read_bytes())
                return
            parent.rename(parked); parent.mkdir()
            (parked / 'source').rename(parent / 'source')
            (parked / 'source').write_bytes(b'owner replacement')
            with self.assertRaisesRegex(ValidationError, 'parent identity changed'):
                operating._publish_new(parent, handle, 'source', 'destination', expected)
            self.assertEqual(b'awf', (parent / 'source').read_bytes())
            self.assertEqual(b'owner replacement', (parked / 'source').read_bytes())
            self.assertFalse((parked / 'destination').exists())
            self.assertFalse((parent / 'destination').exists())

    def test_retained_index_retirement_checks_the_authenticated_leaf(self):
        from test_awf238_retained_adoption import RetainedAdoptionTests
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                fixture = RetainedAdoptionTests('test_success_adopts_only_reused_managed_directories')
                fixture.setUp()
                try:
                    relative = '.agentic/retained'
                    record = fixture.retain([relative])
                    native = safeio.Tree.unlink; hits = []
                    def unlink(tree, path):
                        if path == installer.RETAINED_JOURNAL:
                            hits.append(path)
                            if replacement: substitute(fixture.root / path)
                        return native(tree, path)
                    with installer.Tree(fixture.root) as tree, installer.install_lock(tree):
                        journal = dict(transaction_id=str(uuid.uuid4()), directory_adoptions=[dict(
                            path=relative, identity=installer._directory_identity(tree, relative),
                            evidence_sha256=installer.sha256(installer.json_bytes(record)))])
                        with patch.object(safeio.Tree, 'unlink', unlink):
                            if replacement:
                                with self.assertRaises(ValidationError): installer._adopt_transaction_directories(tree, journal)
                                self.assertEqual(b'owner replacement', (fixture.root / installer.RETAINED_JOURNAL).read_bytes())
                            else:
                                installer._adopt_transaction_directories(tree, journal)
                                self.assertFalse((fixture.root / installer.RETAINED_JOURNAL).exists())
                    self.assertEqual([installer.RETAINED_JOURNAL], hits)
                    self.assertTrue((fixture.root / relative).is_dir())
                finally:
                    fixture.doCleanups()

    def test_rollback_created_file_retirement_checks_the_authenticated_leaf(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                previous = self.root; self.root = Path(temporary).resolve()
                try:
                    source, project, pin, content = self.fixture()
                    relative = '.agentic/adopted-clone/file'
                    (project / relative).unlink()
                    native = safeio.Tree.unlink; hits = []
                    def unlink(tree, path):
                        if path == relative:
                            hits.append(path)
                            if replacement: substitute(project / path)
                        return native(tree, path)
                    with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                            patch.object(safeio.Tree, 'unlink', unlink):
                        if replacement:
                            with self.assertRaises(ValidationError): installer.install(source, project, pin, conflict='backup', fail_after=1)
                            self.assertEqual(b'owner replacement', (project / relative).read_bytes())
                        else:
                            with self.assertRaisesRegex(OSError, 'Injected installation failure'):
                                installer.install(source, project, pin, conflict='backup', fail_after=1)
                            self.assertFalse((project / relative).exists())
                    self.assertEqual([relative], hits)
                finally:
                    self.root = previous

    def test_journal_only_retirement_checks_the_authenticated_leaf(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                project = Path(temporary).resolve()
                journal = dict(format='awf-install-journal-3', transaction_id=str(uuid.uuid4()),
                    phase='active', files=[], runtime=None, managed_before=[], managed_after=[])
                with installer.Tree(project) as tree:
                    tree.write(installer.JOURNAL, installer.json_bytes(journal))
                native = safeio.Tree.unlink; hits = []
                def unlink(tree, path):
                    if path == installer.JOURNAL:
                        hits.append(path)
                        if replacement: substitute(project / path)
                    return native(tree, path)
                with patch.object(safeio.Tree, 'unlink', unlink):
                    if replacement:
                        with self.assertRaises(ValidationError): installer.recover(project)
                        self.assertEqual(b'owner replacement', (project / installer.JOURNAL).read_bytes())
                    else:
                        self.assertEqual('ROLLED_BACK', installer.recover(project)['status'])
                        self.assertFalse((project / installer.JOURNAL).exists())
                self.assertEqual([installer.JOURNAL], hits)

    def test_obsolete_managed_file_retirement_checks_the_authenticated_leaf(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                previous = self.root; self.root = Path(temporary).resolve()
                try:
                    source, project, pin, content = self.fixture()
                    relative = '.agentic/adopted-clone/file'
                    def release(files):
                        raw = installer.json_bytes(dict(format='awf-manifest-1', template_version=installer.VERSION,
                            files={name: installer.sha256(data) for name, data in files.items()}))
                        (source / installer.MANIFEST).write_bytes(raw)
                        return installer.sha256(raw)
                    pin = release(content)
                    with patch.object(installer, 'verify_release', return_value=(pin, content)):
                        installer.install(source, project, pin, conflict='backup')
                    target_content = {name: data for name, data in content.items() if name != relative}
                    target_pin = release(target_content)
                    native = safeio.Tree.unlink; hits = []
                    def unlink(tree, path):
                        if path == relative and inspect.currentframe().f_back.f_code.co_name == 'install':
                            hits.append(path)
                            if replacement: substitute(project / path)
                        return native(tree, path)
                    with patch.object(installer, 'verify_release', return_value=(target_pin, target_content)), \
                            patch.object(safeio.Tree, 'unlink', unlink):
                        if replacement:
                            with self.assertRaises(ValidationError): installer.install(source, project, target_pin, mode='upgrade')
                            self.assertEqual(b'owner replacement', (project / relative).read_bytes())
                        else:
                            installer.install(source, project, target_pin, mode='upgrade')
                            self.assertFalse((project / relative).exists())
                    self.assertEqual([relative], hits)
                finally:
                    self.root = previous

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def fixture(self):
        source, project = self.root / 'source', self.root / 'project'
        source.mkdir(); project.mkdir()
        manifest = installer.json_bytes({'format': 'awf-manifest-1', 'template_version': installer.VERSION, 'files': {}})
        (source / installer.MANIFEST).write_bytes(manifest)
        content = {p: (ROOT / p).read_bytes() for p in (installer.CONFIG, installer.PROVENANCE)}
        content['.agentic/adopted-clone/file'] = b'new'
        for relative, raw in content.items():
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'old' if relative.endswith('/file') else raw)
        return source, project, installer.sha256(manifest), content

    def test_freed_transaction_temporary_never_acquires_replacement_identity(self):
        # pre-allow/reproduce_cleanup.py: real binder -> writer -> cleanup.
        journal = {'format': 'awf-install-journal-4', 'transaction_id': str(uuid.uuid4()),
                   'temporary_files': 'sibling-uuid5-v2', 'files': []}
        native = os.replace
        replacements = []
        def publish(source, destination, *args, **kwargs):
            result = native(source, destination, *args, **kwargs)
            source = Path(source)
            if not source.is_absolute():
                source = self.root / '.agentic-install' / source
            source.write_bytes(b'owner replacement')
            replacements.append(source)
            return result
        with installer.Tree(self.root) as tree:
            installer._bind_transaction_temporaries(tree, journal, initial=True)
            with patch.object(os, 'replace', publish):
                tree.write(installer.JOURNAL, b'published')
            with self.assertRaises(ValidationError):
                installer._cleanup_transaction_temporaries(tree, journal)
        self.assertEqual(1, len(replacements))
        self.assertEqual(b'owner replacement', replacements[0].read_bytes())

    def test_fresh_temporary_recovery_uses_stored_creating_identity_and_preserves_lock_bytes(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve()
                journal = {'format': 'awf-install-journal-4', 'transaction_id': str(uuid.uuid4()),
                           'temporary_files': 'sibling-uuid5-v2', 'files': []}
                native_fdopen = os.fdopen
                def stop(fd, mode='r', *args, **kwargs):
                    if 'w' in mode:
                        os.close(fd)
                        raise Interrupted()
                    return native_fdopen(fd, mode, *args, **kwargs)
                lock_bytes = b'0' if os.name == 'nt' else b''
                with installer.Tree(root) as tree, installer.install_lock(tree):
                    # This is a bound journal update, not the unclaimed first
                    # intent temporary that recovery deliberately retains.
                    tree.write_exclusive(installer.JOURNAL, installer.json_bytes(journal))
                    installer._bind_transaction_temporaries(tree, journal, initial=True)
                    with patch.object(os, 'fdopen', stop), self.assertRaises(Interrupted):
                        tree.write(installer.JOURNAL, b'journal bytes')
                temporary_path = root / installer._transaction_temporary_paths(journal)[installer.JOURNAL]
                self.assertTrue(temporary_path.exists())
                self.assertEqual(installer.json_bytes(journal), (root / installer.JOURNAL).read_bytes())
                if replacement: substitute(temporary_path)
                with installer.Tree(root) as tree, installer.install_lock(tree):
                    if not getattr(tree, '_custody_metadata_unavailable', False) and not replacement:
                        installer._bind_transaction_temporaries(tree, journal)
                        installer._cleanup_transaction_temporaries(tree, journal)
                        self.assertFalse(temporary_path.exists())
                    elif replacement:
                        with self.assertRaises(ValidationError): installer._bind_transaction_temporaries(tree, journal)
                        self.assertEqual(b'owner replacement', temporary_path.read_bytes())
                    else:
                        with self.assertRaisesRegex(ValidationError, 'lacks.*xattr.*quarantine'):
                            installer._bind_transaction_temporaries(tree, journal)
                        self.assertEqual(b'', temporary_path.read_bytes())
                self.assertEqual(lock_bytes, (root / installer.LOCK).read_bytes())

    def test_unbound_first_journal_temporary_retains_bytes_without_lock_metadata(self):
        journal = {'format': 'awf-install-journal-4', 'transaction_id': str(uuid.uuid4()),
                   'temporary_files': 'sibling-uuid5-v2', 'files': []}
        def stop(fd, *args, **kwargs):
            os.close(fd)
            raise Interrupted()
        with installer.Tree(self.root) as tree, installer.install_lock(tree):
            installer._bind_transaction_temporaries(tree, journal, initial=True)
            with patch.object(os, 'fdopen', stop), self.assertRaises(Interrupted):
                tree.write(installer.JOURNAL, b'not yet a published journal')
        temporary = self.root / installer._transaction_temporary_paths(journal)[installer.JOURNAL]
        self.assertTrue(temporary.exists())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertEqual('NO_PENDING_INSTALL', installer.recover(self.root)['status'])
        self.assertEqual(b'', temporary.read_bytes())
        if os.name == 'nt':
            self.assertEqual({}, safeio._windows_metadata(self.root / installer.LOCK)['streams'])
        elif hasattr(os, 'listxattr'):
            self.assertNotIn('user.awf-temporary-custody', os.listxattr(self.root / installer.LOCK))
        else:
            self.assertEqual(b'', (self.root / installer.LOCK).read_bytes())

    def test_terminal_recovery_authenticates_remaining_object_before_retirement(self):
        # pre-allow/reproduce_terminal_object.py, fresh terminal recovery.
        source, project, pin, content = self.fixture()
        with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                patch.object(installer, '_finalize_committed', side_effect=Interrupted):
            with self.assertRaises(Interrupted):
                installer.install(source, project, pin, conflict='backup')
        journal = json.loads((project / installer.JOURNAL).read_bytes())
        digest = next(row['old_sha256'] for row in journal['files'] if row['old_sha256'])
        target = project / installer._recovery_object_path(digest)
        original = substitute(target)
        with self.assertRaises(ValidationError):
            installer.recover(project)
        self.assertEqual(b'owner replacement', target.read_bytes())
        self.assertTrue(original.exists())
        self.assertTrue((project / installer.JOURNAL).exists())

    def test_committed_marker_and_journal_keep_identity_after_final_update(self):
        for target in (installer.MARKER, installer.JOURNAL):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                previous = self.root
                self.root = Path(temporary).resolve()
                try:
                    source, project, pin, content = self.fixture()
                    native = safeio.Tree.unlink
                    hits = []
                    def unlink(tree, relative):
                        if relative == target:
                            hits.append(relative)
                            substitute(project / relative)
                        return native(tree, relative)
                    with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                            patch.object(safeio.Tree, 'unlink', unlink), self.assertRaises(ValidationError):
                        installer.install(source, project, pin, conflict='backup')
                    self.assertEqual([target], hits)
                    self.assertEqual(b'owner replacement', (project / target).read_bytes())
                finally:
                    self.root = previous

    def test_compact_rollback_marker_and_journal_keep_identity_after_update(self):
        for target in (installer.MARKER, installer.JOURNAL):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                previous = self.root; self.root = Path(temporary).resolve()
                try:
                    source, project, pin, content = self.fixture()
                    native = safeio.Tree.unlink
                    hits = []
                    def unlink(tree, relative):
                        if relative == target and inspect.currentframe().f_back.f_code.co_name == '_finalize_compact_rollback':
                            hits.append(relative); substitute(project / relative)
                        return native(tree, relative)
                    with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                            patch.object(safeio.Tree, 'unlink', unlink), self.assertRaises(ValidationError):
                        installer.install(source, project, pin, conflict='backup', fail_after=1)
                    self.assertEqual([target], hits)
                    self.assertEqual(b'owner replacement', (project / target).read_bytes())
                finally:
                    self.root = previous

    def test_normal_install_retires_authenticated_objects_marker_and_journal(self):
        source, project, pin, content = self.fixture()
        with patch.object(installer, 'verify_release', return_value=(pin, content)):
            installer.install(source, project, pin, conflict='backup')
        self.assertFalse((project / installer.MARKER).exists())
        self.assertFalse((project / installer.JOURNAL).exists())
        self.assertEqual([], list((project / installer.RECOVERY_OBJECTS).iterdir()))
        if os.name == 'nt':
            self.assertEqual({}, safeio._windows_metadata(project / installer.LOCK)['streams'])
        elif hasattr(os, 'listxattr'):
            self.assertNotIn('user.awf-temporary-custody', os.listxattr(project / installer.LOCK))
        else:
            self.assertEqual(b'', (project / installer.LOCK).read_bytes())

    def test_pending_marker_and_journal_reproductions(self):
        # Both pre-allow pending-update reproductions, with substitution at the
        # final unlink after the journal/marker update that formerly lost custody.
        for target in (installer.MARKER, installer.JOURNAL):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                project = Path(temporary).resolve()
                (project / '.agentic').mkdir(); (project / '.agentic-install').mkdir()
                tx = str(uuid.uuid4())
                runtime = installer._runtime_journal(project, tx) if target == installer.JOURNAL else None
                journal = {'format': 'awf-install-journal-3', 'transaction_id': tx,
                           'phase': 'active', 'files': [], 'managed_before': [], 'managed_after': [], 'runtime': runtime}
                if runtime:
                    stage = project / runtime['stage']; stage.mkdir()
                    (stage / 'staged-runtime').write_bytes(b'synthetic stage')
                    candidate = dict(journal, runtime=dict(runtime, new_sha256=installer._runtime_tree_sha256(stage)))
                    kind = 'runtime_stage'
                else:
                    candidate = dict(journal, phase='commit_cleanup'); kind = 'commit_cleanup'
                raw = installer.json_bytes(journal)
                marker = installer._journal_marker(tx, installer.sha256(raw), installer.sha256(installer.json_bytes(candidate)), kind)
                (project / installer.JOURNAL).write_bytes(raw)
                (project / installer.MARKER).write_bytes(installer.json_bytes(marker))
                native_write, native_unlink = safeio.Tree.write, safeio.Tree.unlink
                hits = []
                def write(tree, relative, *args, **kwargs):
                    result = native_write(tree, relative, *args, **kwargs)
                    if runtime and relative == installer.JOURNAL and stage.exists():
                        stage.rename(project / 'preserved-stage')
                    return result
                def unlink(tree, relative):
                    if relative == target:
                        hits.append(relative); substitute(project / relative)
                    return native_unlink(tree, relative)
                with patch.object(safeio.Tree, 'write', write), patch.object(safeio.Tree, 'unlink', unlink), self.assertRaises(ValidationError):
                    installer.recover(project)
                self.assertEqual([target], hits)
                self.assertEqual(b'owner replacement', (project / target).read_bytes())

    def test_operating_source_substitution_is_checked_after_close(self):
        native = operating._publish_new
        hits = []
        def publish(parent, handle, temporary, leaf, *identity):
            hits.append(temporary)
            substitute(parent / temporary)
            return native(parent, handle, temporary, leaf, *identity)
        with safeio.Tree(self.root) as tree, patch.object(operating, '_publish_new', publish), self.assertRaises(ValidationError):
            operating._new(tree, 'record', b'awf intended record')
        self.assertEqual(1, len(hits))
        self.assertFalse((self.root / 'record').exists())
        self.assertEqual(b'owner replacement', (self.root / hits[0]).read_bytes())

    def test_operating_journal_retirement_preserves_replacements_in_commit_and_recovery(self):
        from test_operating import governance
        for recovery in (False, True):
            with self.subTest(recovery=recovery), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve(); (root / '.agentic').mkdir()
                policy = governance()
                (root / operating.GOVERNANCE).write_bytes(operating._json(policy))
                native = safeio.Tree.unlink
                if recovery:
                    def interrupt(tree, path):
                        if path == operating.JOURNAL: raise Interrupted()
                        return native(tree, path)
                    with patch.object(safeio.Tree, 'unlink', interrupt), self.assertRaises(Interrupted):
                        operating.initialize_operating(root, policy)
                    digest = hashlib.sha256((root / operating.JOURNAL).read_bytes()).hexdigest()
                hits = []
                def unlink(tree, path):
                    if path == operating.JOURNAL:
                        hits.append(path); substitute(root / path)
                    return native(tree, path)
                with patch.object(safeio.Tree, 'unlink', unlink), self.assertRaises(ValidationError):
                    if recovery: operating.recover_operating(root, policy, digest)
                    else: operating.initialize_operating(root, policy)
                self.assertEqual([operating.JOURNAL], hits)
                self.assertEqual(b'owner replacement', (root / operating.JOURNAL).read_bytes())

    def test_stream_recovery_checks_created_files_and_journal_before_unlink(self):
        for target in (streams.PLAN, streams.MARKDOWN, streams.JOURNAL, None):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve()
                raw = {streams.PLAN: b'next plan', streams.MARKDOWN: b'next markdown'}
                journal = installer.json_bytes({'previous': {name: None for name in raw},
                    'next_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()}})
                native = safeio.Tree.unlink; hits = []
                def unlink(tree, path):
                    if path == target: hits.append(path); substitute(root / path)
                    return native(tree, path)
                with safeio.Tree(root, mutation_authority='managed') as tree:
                    for name, data in {**raw, streams.JOURNAL: journal}.items(): tree.write(name, data)
                    with patch.object(safeio.Tree, 'unlink', unlink):
                        if target:
                            with self.assertRaises(ValidationError): streams._recover(tree, trusted_journal=journal)
                            self.assertEqual([target], hits)
                            self.assertEqual(b'owner replacement', (root / target).read_bytes())
                        else:
                            streams._recover(tree, trusted_journal=journal)
                            for name in (*raw, streams.JOURNAL): self.assertFalse((root / name).exists())

    def test_stream_commit_journal_retirement_keeps_its_published_identity(self):
        from test_streams import PlanPublicationTests
        fixture = PlanPublicationTests('test_project_plan_is_complete_bound_and_rerunnable_with_explicit_cas')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        native = safeio.Tree.unlink; hits = []
        def unlink(tree, path):
            if path == streams.JOURNAL:
                hits.append(path); substitute(fixture.project / path)
            return native(tree, path)
        with patch.object(safeio.Tree, 'unlink', unlink), self.assertRaises(ValidationError): fixture.publish()
        self.assertEqual([streams.JOURNAL], hits)
        self.assertEqual(b'owner replacement', (fixture.project / streams.JOURNAL).read_bytes())

    def test_managed_write_checks_source_and_destination_after_stream_close(self):
        for side in ('source', 'destination'):
            with self.subTest(side=side), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve()
                (root / 'managed').write_bytes(b'old')
                native = os.fdopen
                hits = []
                class Closing:
                    def __init__(self, stream): self.stream = stream
                    def __enter__(self): return self.stream
                    def __exit__(self, *args):
                        self.stream.close()
                        path = next(root.glob('.awf-*.tmp')) if side == 'source' else root / 'managed'
                        hits.append(path); substitute(path)
                def opened(fd, *args, **kwargs):
                    stream = native(fd, *args, **kwargs)
                    return Closing(stream) if args and args[0] == 'wb' else stream
                with installer.Tree(root) as tree, patch.object(os, 'fdopen', opened), self.assertRaises(ValidationError):
                    tree.write('managed', b'new')
                self.assertEqual(1, len(hits))
                self.assertEqual(b'owner replacement', hits[0].read_bytes())
                if side == 'source': self.assertEqual(b'old', (root / 'managed').read_bytes())

    def test_runtime_restore_checks_backup_and_destination_after_hash(self):
        for side in ('source', 'destination', 'normal'):
            with self.subTest(side=side), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve()
                canonical = root / installer.RUNTIME
                canonical.mkdir(parents=True); (canonical / 'old').write_bytes(b'old runtime')
                runtime = installer._runtime_journal(root, str(uuid.uuid4()))
                backup = root / runtime['backup']; canonical.rename(backup)
                native = installer._runtime_tree_sha256
                hits = []
                def authenticate(path):
                    digest = native(path)
                    if Path(path) == backup and side != 'normal' and not hits:
                        target = backup if side == 'source' else canonical
                        hits.append(target); substitute(target, directory=True)
                    return digest
                with installer.Tree(root) as tree, patch.object(installer, '_runtime_tree_sha256', authenticate):
                    if side == 'normal':
                        installer._rollback_runtime(tree, runtime)
                        self.assertEqual(b'old runtime', (canonical / 'old').read_bytes())
                    else:
                        with self.assertRaises(ValidationError): installer._rollback_runtime(tree, runtime)
                        self.assertEqual(1, len(hits))
                        self.assertTrue(hits[0].exists(), 'the substituted namespace entry must remain in place')
                        self.assertEqual(b'owner replacement', (hits[0] / 'owner').read_bytes())

    def test_partial_stage_relocation_checks_custody_after_inspection(self):
        for replace in (False, True):
            with self.subTest(replace=replace), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                root = Path(temporary).resolve(); canonical = root / installer.RUNTIME
                canonical.mkdir(parents=True); (canonical / 'old').write_bytes(b'old')
                runtime = installer._runtime_journal(root, str(uuid.uuid4()))
                stage = root / runtime['stage']; stage.mkdir(); (stage / 'partial').write_bytes(b'partial')
                native = os.lstat
                hits = []
                def observe(path, *args, **kwargs):
                    info = native(path, *args, **kwargs)
                    if (Path(path) == stage and replace and not hits
                            and inspect.currentframe().f_back.f_code.co_name == '_verify_runtime_state'):
                        hits.append(stage); substitute(stage, directory=True)
                    return info
                with installer.Tree(root) as tree, patch.object(os, 'lstat', observe):
                    if replace:
                        with self.assertRaises(ValidationError):
                            installer._verify_runtime_state(tree, runtime, 'old', require_cleanup=True, preserve_partial_stage=True)
                        self.assertEqual([stage], hits)
                        self.assertEqual(b'owner replacement', (stage / 'owner').read_bytes())
                    else:
                        installer._verify_runtime_state(tree, runtime, 'old', require_cleanup=True, preserve_partial_stage=True)
                        self.assertFalse(stage.exists())
                        self.assertEqual(b'partial', next((root / '_to_delete').glob('*/partial')).read_bytes())


if __name__ == '__main__':
    unittest.main()
