"""Stage A regressions from the uncounted independent pre-review."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests'), str(ROOT / 'scripts')]
from agentic import ValidationError
from agentic import installer, safeio
from agentic.canonical import load_yaml
from agentic.installer import INSTALLED, PROVENANCE, install
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline
import test_awf134_pending_updates as pending_tests


@skip_unless_source_repo('requires authentic published v1.9.5 Git objects', '.git')
class PublishedIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_published(ROOT, cls.base / 'published')
        cls.candidate = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.candidate / 'MANIFEST.json').read_bytes()).hexdigest()
        cls.a, cls.b = cls.base / 'a', cls.base / 'b'
        install_baseline(cls.source, cls.a)
        install_baseline(cls.source, cls.b)

    def test_conflicting_published_install_ids_refuse_plan_and_apply_without_writes(self):
        from qualify_upgrade import inventory
        receipts = [json.loads((root / INSTALLED).read_bytes()) for root in (self.a, self.b)]
        self.assertNotEqual(receipts[0]['install_id'], receipts[1]['install_id'])
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                project = self.base / ('mismatch-' + str(dry_run))
                shutil.copytree(self.a, project)
                # Copy unaltered provenance from the other authentic installation.
                (project / PROVENANCE).write_bytes((self.b / PROVENANCE).read_bytes())
                before = inventory(project)
                def all_files():
                    return {p.relative_to(project).as_posix():
                            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
                            for p in project.rglob('*')}
                complete_before = all_files()
                with self.assertRaisesRegex(ValidationError, 'install_id.*(differ|match).*no files changed'):
                    install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                            discover=False, conflict='backup', dry_run=dry_run)
                self.assertEqual(before, inventory(project))
                self.assertEqual(complete_before, all_files())
                self.assertEqual((self.a / INSTALLED).read_bytes(), (project / INSTALLED).read_bytes())

    def test_preservation_requires_retained_original_receipt_and_provenance(self):
        from qualify_upgrade import inventory, prepare_upgrade, compare_upgrade
        for conflict in ('error', 'backup'):
            with self.subTest(conflict=conflict):
                project = self.base / ('retention-' + conflict)
                shutil.copytree(self.a, project)
                before = inventory(project)
                policy = prepare_upgrade(self.candidate, project, self.pin, before)
                install(self.candidate, project, self.pin, mode='upgrade', configure=True,
                        discover=False, conflict=conflict)
                result = compare_upgrade(before, inventory(project), policy, project)
                self.assertEqual(conflict == 'backup', result['preserved'], result['violations'])
                self.assertEqual('PASS' if conflict == 'backup' else 'FAIL',
                                 result['checks']['prior_receipts_retained'])
                if conflict != 'backup':
                    for path in (INSTALLED, PROVENANCE):
                        self.assertTrue(any('prior receipt not retained: ' + path == item
                                            for item in result['violations']), result)
                else:
                    # Losing either old identity must fail even if all new bytes verify.
                    for path in (INSTALLED, PROVENANCE):
                        backup = next(project.glob('.agentic-backup/*/' + path))
                        raw = backup.read_bytes()
                        backup.unlink()
                        damaged = compare_upgrade(before, inventory(project), policy, project)
                        self.assertFalse(damaged['preserved'])
                        self.assertIn('prior receipt not retained: ' + path, damaged['violations'])
                        backup.write_bytes(raw)


    @unittest.skipIf(os.name == 'nt', 'POSIX mode preservation requires POSIX fchmod and permission bits')
    def test_posix_upgrade_content_changing_project_config_retains_0640(self):
        project = self.base / 'posix-modes'
        shutil.copytree(self.a, project)
        target = project / installer.CONFIG
        managed = project / 'AGENTS.md'
        before = target.read_bytes()
        managed_before = managed.read_bytes()
        self.assertEqual('1.9.5', load_yaml(before)['template']['expected_workflow_version'])
        target.chmod(0o640)
        managed.chmod(0o640)
        result = installer.install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual('UPGRADED', result['status'])
        self.assertNotEqual(before, target.read_bytes())
        self.assertNotEqual(managed_before, managed.read_bytes())
        self.assertEqual(installer.VERSION, load_yaml(target.read_bytes())['template']['expected_workflow_version'])
        self.assertEqual(0o640, stat.S_IMODE(target.stat().st_mode))
        self.assertEqual(0o640, stat.S_IMODE(managed.stat().st_mode))
        self.assertEqual(self.pin, installer.verify_installed(project))


class SharedStoreLedgerTests(unittest.TestCase):
    def test_shared_store_history_requires_exact_import_proof(self):
        from agentic.store import Store
        from agentic.budgeting import BudgetLedger, fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        from agentic.upgrade import import_budget_history
        from test_awf242_budgeting import FixtureHost
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = str(uuid.uuid4())
            store = Store(root / 'shared.sqlite', project)
            store.control('RUNNING', 'Disposable test reconciliation', reconciled=True)
            old_run, new_run = str(uuid.uuid4()), str(uuid.uuid4())
            store.reserve_budget(old_run, 'EX-OLD', '2026-11-01T00:00:00Z', 100, 10,
                                 dict(runs=10, tokens=1000, cost=1000, daily_cost=1000))
            with store.connection() as db:
                before = [tuple(r) for r in db.execute('SELECT * FROM reservations')]
            snapshot = root / 'legacy-snapshot.sqlite'
            shutil.copyfile(store.path, snapshot)
            host = FixtureHost()
            registry = EnrollmentRegistry(root / 'enrollment.sqlite', host)
            ledger = BudgetLedger(store.path, registry)
            binding = dict(project_id=project, ticket_id='EX-NEW', run_id=new_run,
                           dispatch_id='dispatch', session_id='session', role='worker')
            host.enrollment = binding
            registry.enroll(binding)
            settings = dict(fresh_settings(), accounting_enabled=True)

            def reserve():
                return store.reserve_budget(new_run, 'EX-NEW', '2026-11-01T00:00:00Z',
                    100, None, {}, budgeting=settings, budget_ledger=ledger, budget_binding=binding)

            for enforcement in (False, True):
                settings['enforcement_enabled'] = enforcement
                with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
                    reserve()
            import_budget_history(store.path, {'state': {'path': snapshot,
                'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}}, project_id=project)
            self.assertEqual('RESERVED', reserve())
            self.assertEqual(2, ledger.summary(project)['reservations'])
            with store.connection() as db:
                self.assertEqual(before, [tuple(r) for r in db.execute('SELECT * FROM reservations')])
                db.execute('UPDATE model_runs SET reserved_tokens=0 WHERE run_id=?', (old_run,))
            with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
                reserve()

    def test_store_reserves_in_shared_canonical_database(self):
        from agentic.store import Store
        from agentic.budgeting import BudgetLedger, fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        from test_awf242_budgeting import FixtureHost
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = str(uuid.uuid4())
            store = Store(root / 'shared.sqlite', project)
            host = FixtureHost()
            registry = EnrollmentRegistry(root / 'enrollment.sqlite', host)
            ledger = BudgetLedger(store.path, registry)
            run = str(uuid.uuid4())
            binding = dict(project_id=project, ticket_id='EX-238', run_id=run,
                           dispatch_id='dispatch', session_id='session', role='worker')
            host.enrollment = binding
            registry.enroll(binding)
            settings = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
            for _ in range(2):
                self.assertEqual('RESERVED', store.reserve_budget(run, 'EX-238',
                    '2026-11-01T00:00:00Z', 100, None, {}, budgeting=settings,
                    budget_ledger=ledger, budget_binding=binding))
            self.assertEqual(1, ledger.summary(project)['reservations'])


class DirectoryContinuityTests(unittest.TestCase):
    PAYLOAD_SIZE = pending_tests.PendingJournalUpdateTests.PAYLOAD_SIZE
    setUp = pending_tests.PendingJournalUpdateTests.setUp
    prepare_install = pending_tests.PendingJournalUpdateTests.prepare_install
    snapshot_project = staticmethod(pending_tests.PendingJournalUpdateTests.snapshot_project)
    interrupt_update = pending_tests.PendingJournalUpdateTests.interrupt_update
    interrupt_after_managed_after_publish = pending_tests.PendingJournalUpdateTests.interrupt_after_managed_after_publish

    def test_unproved_directories_are_retained_in_every_rollback_phase_on_every_os(self):
        paths = ['.agentic-install', installer.RECOVERY_OBJECTS]
        for platform in ('posix', 'nt'):
            for phase in ('object_prepare', 'active', 'rollback_cleanup'):
                with self.subTest(platform=platform, phase=phase):
                    tree = Mock()
                    journal = dict(format='awf-install-journal-4', phase=phase, created_directories=[
                        dict(path=path, identity=None) for path in paths])
                    diagnostics = io.StringIO()
                    with patch.object(installer, 'os', SimpleNamespace(name=platform)), \
                            patch.object(installer, '_directory_identity', return_value=['1' * 32, '2' * 32]), \
                            contextlib.redirect_stderr(diagnostics):
                        retained = installer._cleanup_transaction_directories(tree, journal)
                    self.assertEqual(list(reversed(paths)), retained)
                    tree.parent.assert_not_called()
                    tree.close.assert_not_called()
                    for path in paths:
                        self.assertIn('Directory retained; ownership unproved: ' + path,
                                      diagnostics.getvalue())

    def test_uncreated_unproved_directories_are_not_reported_as_retained(self):
        paths = ['.agentic/existing', '.agentic/never-created']
        for platform in ('posix', 'nt'):
            with self.subTest(platform=platform):
                tree = Mock()
                journal = dict(format='awf-install-journal-4', phase='object_prepare',
                    created_directories=[dict(path=path, identity=None) for path in paths])
                def present(_tree, path):
                    if path == paths[1]:
                        raise FileNotFoundError(path)
                    return ['1' * 32, '2' * 32]
                diagnostics = io.StringIO()
                with patch.object(installer, 'os', SimpleNamespace(name=platform)), \
                        patch.object(installer, '_directory_identity', side_effect=present), \
                        contextlib.redirect_stderr(diagnostics):
                    retained = installer._cleanup_transaction_directories(tree, journal)
                self.assertEqual([paths[0]], retained)
                self.assertIn('Directory retained; ownership unproved: ' + paths[0], diagnostics.getvalue())
                self.assertNotIn(paths[1], diagnostics.getvalue())
                tree.parent.assert_not_called()
                tree.close.assert_not_called()

    def test_journal_only_recovery_reports_actual_cleanup_retention(self):
        journal = dict(format='awf-install-journal-4', phase='object_prepare',
            created_directories=[dict(path=path, identity=None)
                                 for path in ('.agentic/existing', '.agentic/never-created')])
        for retained in (['.agentic/existing'], []):
            with self.subTest(retained=retained), contextlib.ExitStack() as stack:
                tree = stack.enter_context(patch.object(installer, 'Tree')).return_value.__enter__.return_value
                stack.enter_context(patch.object(installer, 'install_lock', return_value=contextlib.nullcontext()))
                stack.enter_context(patch.object(installer, '_exists_read', side_effect=[installer.json_bytes(journal), None]))
                stack.enter_context(patch.object(installer, '_validate_transaction_journal'))
                stack.enter_context(patch.object(installer, '_bind_transaction_temporaries'))
                finalize = stack.enter_context(patch.object(installer, '_finalize_compact_rollback', return_value=retained))
                result = installer.recover('synthetic-project')
                expected = dict(status='ROLLED_BACK')
                if retained:
                    expected['retained_directories'] = retained
                self.assertEqual(expected, result)
                finalize.assert_called_once_with(tree, journal, marker_present=False)

    def test_posix_mkdir_never_publishes_deletion_authority(self):
        tree = Mock()
        tree.parent.return_value = (Path('project'), 123, 'objects')
        operating = Mock(name='posix_operations')
        operating.name = 'posix'
        journal = dict(format='awf-install-journal-4', phase='object_prepare', created_directories=[
            dict(path=installer.RECOVERY_OBJECTS, identity=None)])
        with patch.object(installer, 'os', operating), \
                patch.object(installer, '_directory_identity', return_value=['1' * 32, '2' * 32]), \
                patch.object(installer, '_write_bound_journal_update') as publish:
            installer._prepare_transaction_directories(tree, journal)
            operating.mkdir.assert_called_once_with('objects', dir_fd=123)
            operating.fsync.assert_called_once_with(123)
            publish.assert_called_once_with(tree, journal, 'directory_prepare')
            self.assertIsNone(journal['created_directories'][0]['identity'])
            journal['phase'] = 'rollback_cleanup'
            with contextlib.redirect_stderr(io.StringIO()):
                retained = installer._cleanup_transaction_directories(tree, journal)
        self.assertEqual([installer.RECOVERY_OBJECTS], retained)
        operating.rmdir.assert_not_called()
        operating.unlink.assert_not_called()

    def test_active_recovery_reports_unproved_retention_including_journal_only_retry(self):
        for interrupt_cleanup in (False, True):
            with self.subTest(interrupt_cleanup=interrupt_cleanup):
                create = installer._windows_create_directory if os.name == 'nt' else None

                def unproved_create(handle, name):
                    create(handle, name)
                    return None

                # Exercise real transactions with POSIX's lack of a creating
                # identity, while retaining the host's native safe I/O operations.
                with patch.object(installer, '_windows_create_directory', unproved_create):
                    destination, before, before_mode, before_runtime = (
                        self.interrupt_after_managed_after_publish())
                journal = load_yaml((destination / installer.JOURNAL).read_bytes())
                paths = [row['path'] for row in reversed(journal['created_directories'])]
                self.assertTrue(paths)
                self.assertTrue(all(row['identity'] is None for row in journal['created_directories']))
                unlink = installer.Tree.unlink

                def interrupt(tree, path):
                    unlink(tree, path)
                    if path == installer.MARKER:
                        raise pending_tests.CapturedIntent

                diagnostics = io.StringIO()
                with contextlib.redirect_stderr(diagnostics):
                    if interrupt_cleanup:
                        with patch.object(installer.Tree, 'unlink', interrupt), \
                                self.assertRaises(pending_tests.CapturedIntent):
                            installer.recover(destination)
                    result = installer.recover(destination)
                self.assertEqual(dict(status='ROLLED_BACK', retained_directories=paths), result)
                self.assertEqual(before, self.snapshot_project(destination))
                self.assertEqual(before_mode, stat.S_IMODE(
                    (destination / '.agentic/adopted-clone/0000.bin').stat().st_mode))
                self.assertEqual(before_runtime,
                                 installer._runtime_tree_sha256(destination / installer.RUNTIME))
                for path in paths:
                    self.assertTrue((destination / path).is_dir(), path)
                    self.assertIn('Directory retained; ownership unproved: ' + path,
                                  diagnostics.getvalue())
                self.assertFalse((destination / installer.JOURNAL).exists())
                self.assertFalse((destination / installer.MARKER).exists())

    def test_unproved_retention_does_not_waive_external_directory_content(self):
        create = installer._windows_create_directory if os.name == 'nt' else None

        def unproved_create(handle, name):
            create(handle, name)
            return None

        with patch.object(installer, '_windows_create_directory', unproved_create):
            destination, _before, _mode, _runtime = self.interrupt_after_managed_after_publish()
        journal = load_yaml((destination / installer.JOURNAL).read_bytes())
        relative = next(row['path'] for row in journal['created_directories']
                        if row['path'].startswith('.agentic-state/')) + '/owner-note'
        owner = destination / relative
        owner.write_bytes(b'preserve external owner content\n')
        for _ in range(2):
            with self.assertRaisesRegex(ValidationError, 'external directory member') as refused:
                installer.recover(destination)
            self.assertIn(relative, str(refused.exception))
            self.assertEqual(b'preserve external owner content\n', owner.read_bytes())
            self.assertTrue((destination / installer.JOURNAL).exists())
            self.assertTrue((destination / installer.MARKER).exists())

    def test_posix_unproved_directory_prepare_digest_does_not_grant_ownership(self):
        journal = dict(format='awf-install-journal-4',
            transaction_id='11111111-1111-4111-8111-111111111111',
            phase='object_prepare', files=[], runtime=None,
            managed_before_sha256=installer.sha256(installer.json_bytes([])),
            managed_after_sha256=None,
            created_directories=[dict(path=installer.RECOVERY_OBJECTS, identity=None)])
        raw = installer.json_bytes(journal)
        digest = installer.sha256(raw)
        marker = installer._journal_marker(journal['transaction_id'], digest, digest, 'directory_prepare')
        tree = Mock()
        with patch.object(installer, '_directory_identity', return_value=['1' * 32, '2' * 32]):
            recovered = installer._recover_pending_journal_update(tree, journal, raw, marker)
            with contextlib.redirect_stderr(io.StringIO()):
                retained = installer._cleanup_transaction_directories(tree, recovered)
        self.assertEqual([installer.RECOVERY_OBJECTS], retained)
        self.assertIsNone(recovered['created_directories'][0]['identity'])

    def test_interrupted_directory_prepare_retains_paths_and_reports_recovery(self):
        if os.name != 'nt':
            self.assert_unproved_directory_prepare_recovery()
            return
        for interrupt_cleanup in (False, True):
            with self.subTest(interrupt_cleanup=interrupt_cleanup):
                destination, before, kind = self.interrupt_update('directory_prepare', 'marker')
                self.assertEqual('directory_prepare', kind)
                journal = load_yaml((destination / installer.JOURNAL).read_bytes())
                paths = [row['path'] for row in reversed(journal['created_directories'])]
                original_unlink = installer.Tree.unlink

                def interrupt(tree, path):
                    original_unlink(tree, path)
                    if path == installer.MARKER:
                        raise pending_tests.CapturedIntent

                diagnostics = io.StringIO()
                with contextlib.redirect_stderr(diagnostics):
                    if interrupt_cleanup:
                        with patch.object(installer.Tree, 'unlink', interrupt), self.assertRaises(pending_tests.CapturedIntent):
                            installer.recover(destination)
                    result = installer.recover(destination)
                self.assertEqual(dict(status='ROLLED_BACK', retained_directories=paths), result)
                self.assertEqual(before, self.snapshot_project(destination))
                for path in paths:
                    self.assertTrue((destination / path).is_dir(), path)
                    self.assertIn('Directory retained; ownership unproved: ' + path, diagnostics.getvalue())
                self.assertFalse((destination / installer.JOURNAL).exists())
                self.assertFalse((destination / installer.MARKER).exists())
                self.assertTrue((destination / installer.RECOVERY_OBJECTS).is_dir())

    def assert_unproved_directory_prepare_recovery(self):
        for interrupt_cleanup in (False, True):
            with self.subTest(interrupt_cleanup=interrupt_cleanup):
                source, destination, pin, content = self.prepare_install(
                    unchanged_files=0, changed_files=1)
                before = self.snapshot_project(destination)
                original_update = installer._write_bound_journal_update
                observed = []

                def interrupt_after_prepare(tree, journal, update_kind=None):
                    if update_kind != 'directory_prepare':
                        return original_update(tree, journal, update_kind)
                    intent = (tree.read(installer.JOURNAL), tree.read(installer.MARKER))
                    original_update(tree, journal, update_kind)
                    self.assertEqual(intent, (tree.read(installer.JOURNAL),
                                              tree.read(installer.MARKER)))
                    self.assertTrue(all(row['identity'] is None
                                        for row in journal['created_directories']))
                    observed.append(update_kind)
                    # Physical creation is complete, but no identity transition
                    # exists to precommit. Interrupt before any recovery objects.
                    raise pending_tests.CapturedIntent

                with patch.object(installer, 'verify_release', return_value=(pin, content)), \
                        patch.object(installer, '_write_bound_journal_update', interrupt_after_prepare), \
                        self.assertRaises(pending_tests.CapturedIntent):
                    installer.install(source, destination, pin, conflict='backup')
                self.assertEqual(['directory_prepare'], observed)
                journal = load_yaml((destination / installer.JOURNAL).read_bytes())
                self.assertEqual('object_prepare', journal['phase'])
                self.assertTrue(all(row['identity'] is None
                                    for row in journal['created_directories']))
                marker = load_yaml((destination / installer.MARKER).read_bytes())
                self.assertIsNone(marker['pending_journal_sha256'])
                self.assertIsNone(marker['pending_update_kind'])
                paths = [row['path'] for row in reversed(journal['created_directories'])]
                self.assertTrue(paths)
                original_unlink = installer.Tree.unlink

                def interrupt(tree, path):
                    original_unlink(tree, path)
                    if path == installer.MARKER:
                        raise pending_tests.CapturedIntent

                def forbid_directory_disposition(handle, information_class, information):
                    import ctypes
                    info = safeio.FileInformation()
                    if not safeio.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    self.assertFalse(info.attributes & 0x10, 'Unproved directory deletion')
                    return original_disposition(handle, information_class, information)

                diagnostics = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stderr(diagnostics))
                    rmdir = stack.enter_context(patch.object(installer.os, 'rmdir',
                        side_effect=AssertionError('Unproved directory deletion')))
                    if installer.os.name == 'nt':
                        original_disposition = safeio._win_set_file_information
                        stack.enter_context(patch.object(safeio, '_win_set_file_information',
                                                        forbid_directory_disposition))
                    if interrupt_cleanup:
                        with patch.object(installer.Tree, 'unlink', interrupt), \
                                self.assertRaises(pending_tests.CapturedIntent):
                            installer.recover(destination)
                    result = installer.recover(destination)
                    rmdir.assert_not_called()
                self.assertEqual(dict(status='ROLLED_BACK', retained_directories=paths), result)
                self.assertEqual(before, self.snapshot_project(destination))
                for path in paths:
                    self.assertTrue((destination / path).is_dir(), path)
                    self.assertIn('Directory retained; ownership unproved: ' + path,
                                  diagnostics.getvalue())
                self.assertFalse((destination / installer.JOURNAL).exists())
                self.assertFalse((destination / installer.MARKER).exists())

    def test_unproved_directory_prepare_recovery_on_every_os(self):
        create = installer._windows_create_directory

        def unproved_create(handle, name):
            create(handle, name)
            return None

        # Keep native filesystem operations, but model POSIX's missing creating
        # identity even on Windows, including interrupted retirement and retry.
        with patch.object(installer, '_windows_create_directory', unproved_create), \
                patch.object(sys.modules[__name__], 'os', SimpleNamespace(name='posix')):
            self.test_interrupted_directory_prepare_retains_paths_and_reports_recovery()

    def test_posix_prepare_unchanged_journal_emits_no_pending_marker(self):
        path = installer.RECOVERY_OBJECTS
        journal = dict(format='awf-install-journal-4', phase='object_prepare',
            transaction_id='11111111-1111-4111-8111-111111111111', files=[], runtime=None,
            managed_before_sha256=installer.sha256(installer.json_bytes([])),
            managed_after_sha256=None, created_directories=[dict(path=path, identity=None)])
        raw = installer.json_bytes(journal)
        tree = Mock()
        tree.parent.return_value = (Path('project'), 123, 'objects')
        operating = Mock(name='posix_operations')
        operating.name = 'posix'
        with patch.object(installer, 'os', operating), \
                patch.object(installer, '_read_bound_journal',
                             return_value=(installer._copy_journal(journal), raw, {})), \
                patch.object(installer, '_directory_identity', return_value=['1' * 32, '2' * 32]), \
                contextlib.redirect_stderr(io.StringIO()) as diagnostics:
            installer._prepare_transaction_directories(tree, journal)
            self.assertEqual(raw, installer.json_bytes(journal))
            tree.write.assert_not_called()
            retained = installer._cleanup_transaction_directories(tree, journal)
        self.assertIsNone(journal['created_directories'][0]['identity'])
        self.assertEqual([path], retained)
        self.assertIn('Directory retained; ownership unproved: ' + path, diagnostics.getvalue())
        operating.mkdir.assert_called_once_with('objects', dir_fd=123)
        operating.fsync.assert_called_once_with(123)
        operating.rmdir.assert_not_called()
        operating.unlink.assert_not_called()
        tree.close.assert_not_called()

    def test_reused_id_reconstruction_never_calls_deletion_disposition(self):
        identity = ['1' * 32, '2' * 32]
        path = installer.RECOVERY_OBJECTS
        journal = dict(format='awf-install-journal-4',
            transaction_id='11111111-1111-4111-8111-111111111111',
            phase='object_prepare', files=[], runtime=None,
            managed_before_sha256=installer.sha256(installer.json_bytes([])),
            managed_after_sha256=None,
            created_directories=[dict(path=path, identity=None)])
        proposed = installer._copy_journal(journal)
        proposed['created_directories'][0]['identity'] = identity
        raw = installer.json_bytes(journal)
        marker = installer._journal_marker(journal['transaction_id'], installer.sha256(raw),
            installer.sha256(installer.json_bytes(proposed)), 'directory_prepare')
        tree = Mock()
        tree.parent.return_value = (Path('project'), 123, 'objects')
        disposition = Mock()
        kernel = Mock()
        kernel.CreateFileW.return_value = 456
        # The replacement has recycled the creating object's exact ID numbers.
        # Exercise the Windows disposition branch on every host without deleting.
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(installer, '_directory_identity', return_value=identity))
            stack.enter_context(patch.object(installer, '_windows_directory_identity', return_value=identity))
            stack.enter_context(patch.object(installer, 'os', SimpleNamespace(name='nt')))
            for name, value in dict(kernel=kernel, INVALID_HANDLE=-1,
                    FileDispositionInformation=Mock(), FILE_DISPOSITION_INFO=4,
                    _win_set_file_information=disposition).items():
                stack.enter_context(patch.object(safeio, name, value, create=True))
            recovered = installer._recover_pending_journal_update(tree, journal, raw, marker)
            diagnostics = io.StringIO()
            with contextlib.redirect_stderr(diagnostics):
                retained = installer._cleanup_transaction_directories(tree, recovered)
        disposition.assert_not_called()
        kernel.CreateFileW.assert_not_called()
        self.assertIsNone(recovered['created_directories'][0]['identity'])
        self.assertEqual([path], retained)
        self.assertIn(path, diagnostics.getvalue())
        self.assertIn('retained', diagnostics.getvalue())
        self.assertIn('ownership unproved', diagnostics.getvalue())
        # Never publish reconstructed numeric identity as creating-handle proof.
        self.assertNotIn(installer.JOURNAL, [call.args[0] for call in tree.write.call_args_list])


class ForwardModeTests(unittest.TestCase):
    def write(self, *, path=installer.CONFIG, exists=True, mode=0o640, override=None, read_error=None, apply_error=None):
        events = []
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.fileno.return_value = 456
        operating = Mock()
        operating.name = 'posix'
        for name in ('O_CREAT', 'O_EXCL', 'O_WRONLY', 'O_BINARY', 'O_NOFOLLOW'):
            setattr(operating, name, getattr(os, name, 0))
        operating.open.return_value = 456
        operating.fdopen.return_value = stream
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=101, st_gid=202)
        operating.fstat.side_effect = lambda fd: metadata
        def chmod(fd, value):
            events.append(('mode', value))
            metadata.st_mode = stat.S_IFREG | value
        operating.fchmod = Mock(side_effect=apply_error or chmod)
        operating.replace.side_effect = lambda *a, **kw: events.append(('replace', kw))
        tree = Mock()
        tree.inspect.side_effect = read_error if read_error else None
        tree.inspect.return_value = SimpleNamespace(st_mode=stat.S_IFREG | mode,
                                                    st_uid=101, st_gid=202) if exists else None
        tree.read.return_value = b'old content'
        tree.parent.return_value = (Path('project'), 123, 'PROJECT_CONFIG.yaml')
        tree.write_temporary_names = {}
        tree.write_modes = {} if override is None else {path: override}
        tree.write_ownership = {}
        with patch.object(safeio, 'os', operating):
            if read_error or apply_error:
                with self.assertRaises(OSError):
                    safeio.Tree.write(tree, path, b'changed content')
                operating.replace.assert_not_called()
                if read_error:
                    operating.open.assert_not_called()
            else:
                safeio.Tree.write(tree, path, b'changed content')
        return operating, events

    def test_forward_existing_file_passes_preserved_mode_before_replace(self):
        for path in (installer.CONFIG, 'PROJECT_INSTRUCTIONS.md', '.github/CODEOWNERS',
                     'AGENTS.md', '.agentic-state/routing/ledger.sqlite'):
            with self.subTest(path=path):
                operating, events = self.write(path=path)
                operating.fchmod.assert_called_once_with(456, 0o640)
                self.assertEqual(['mode', 'replace'], [event[0] for event in events])
                self.assertEqual(dict(src_dir_fd=123, dst_dir_fd=123), events[1][1])

    def test_new_file_keeps_private_creation_mode(self):
        operating, _ = self.write(exists=False)
        operating.fchmod.assert_not_called()
        self.assertEqual(0o600, operating.open.call_args.args[2])

    def test_rollback_explicit_mode_takes_precedence(self):
        operating, _ = self.write(mode=0o600, override=0o640)
        operating.fchmod.assert_called_once_with(456, 0o640)

    def test_unreadable_mode_refuses_before_temporary_write(self):
        self.write(read_error=OSError('mode read refused'))

    def test_unapplied_mode_refuses_before_replacement(self):
        self.write(apply_error=OSError('mode application refused'))



if __name__ == '__main__':
    unittest.main()
