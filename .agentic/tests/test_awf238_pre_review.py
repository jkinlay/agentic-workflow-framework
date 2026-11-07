"""Stage A regressions from the uncounted independent pre-review."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests'), str(ROOT / 'scripts')]
from agentic import ValidationError
from agentic.installer import INSTALLED, PROVENANCE, install
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline


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


if __name__ == '__main__':
    unittest.main()
