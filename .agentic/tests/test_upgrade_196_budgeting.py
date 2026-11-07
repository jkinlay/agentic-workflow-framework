"""Explicit budget adoption using authentic published 1.9.5 execution history."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
sys.path.insert(0, str(ROOT / 'scripts'))
from agentic import ValidationError, upgrade
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import (materialize_published, generate_budget_history,
    materialize_candidate, install_baseline)
import qualify_upgrade

PROJECT = '11111111-1111-4111-8111-111111111111'


class BudgetDispositionTests(unittest.TestCase):
    @skip_unless_source_repo('requires historical upgrade fixtures',
                             '.agentic/tests/fixtures/upgrades/versions.json')
    def test_fixture_configuration_paths_converge_and_preserve_custom_policy(self):
        from agentic.canonical import load_yaml, sha256
        from upgrade_fixtures import (CONFIG, INSTALLED, OPERATING, PROVENANCE,
                                      NEXT, fixture_blob, fixture_index, fixture_manifest)

        table = upgrade.load_known_versions((ROOT / '.agentic/upgrade/known-versions.json').read_bytes())
        manifest_raw = (ROOT / 'MANIFEST.json').read_bytes()
        target = {'source_manifest_sha256': sha256(manifest_raw), '_immutable_files': {}}
        # Only configuration/compatibility bytes are under test here. The full
        # matrix separately exercises real receipts, installed trees and state.
        for version in fixture_index()['versions']:
            fixture = fixture_manifest(version)
            owner = fixture['owner_files']
            def raw(path):
                return fixture_blob(owner[path]['sha256']) if path in owner else None

            results = []
            for customized in (False, True):
                with self.subTest(version=version, customized=customized):
                    config = raw(CONFIG)
                    if customized:
                        value = load_yaml(config)
                        value['execution']['max_agent_runs_per_ticket'] = 7
                        value['execution']['max_tokens_per_ticket'] = 123456
                        config = (json.dumps(value, indent=2) + '\n').encode()
                    original_execution = load_yaml(config)['execution']
                    inputs = (config, raw(OPERATING), raw(INSTALLED), raw(PROVENANCE), {})
                    direct, _ = upgrade.apply_chain(table, version, *inputs, target)
                    results.append(direct.project_config)
                    chained = upgrade.MigrationBundle(*inputs)
                    cursor = version
                    while NEXT[cursor] != table['target']:
                        following = NEXT[cursor]
                        chained = upgrade.migrate_step(chained, cursor, following,
                                                       table['versions'][following])
                        cursor = following
                    direct_record = upgrade.budget_upgrade_disposition(config, raw(OPERATING), version)
                    chained_record = upgrade.budget_upgrade_disposition(
                        chained.project_config, chained.operating_config, cursor)
                    # Compare every field and the exact serialized record, so
                    # newly introduced path-dependent values cannot hide here.
                    self.assertEqual(set(direct_record), set(chained_record))
                    for field in direct_record:
                        with self.subTest(field=field):
                            self.assertEqual(direct_record[field], chained_record[field])
                    self.assertEqual(json.dumps(direct_record, indent=2),
                                     json.dumps(chained_record, indent=2))
                    final, _ = upgrade.apply_chain(table, cursor, chained.project_config,
                        chained.operating_config, chained.receipt, chained.provenance, {}, target)
                    self.assertEqual(direct.project_config, final.project_config)
                    self.assertEqual(raw(OPERATING), final.operating_config)
                    final_execution = load_yaml(final.project_config)['execution']
                    for field in ('max_agent_runs_per_ticket', 'max_tokens_per_ticket'):
                        self.assertEqual(original_execution[field], final_execution[field])
                    self.assertTrue(direct_record['owner_adoption_required'])
            # A real owner policy difference remains visible in the upgraded
            # configuration and its policy identity, rather than in path history.
            self.assertNotEqual(results[0], results[1])
            self.assertNotEqual(sha256(upgrade._canonical_manifest(load_yaml(results[0])['execution'])),
                                sha256(upgrade._canonical_manifest(load_yaml(results[1])['execution'])))

    def test_compatibility_state_is_independent_of_source_release(self):
        config = b'{"execution": {"max_agent_runs_per_ticket": 16}}'
        for operating in (None, b'{"budgeting": {"accounting_enabled": false}}'):
            with self.subTest(operating=operating):
                records = [upgrade.budget_upgrade_disposition(config, operating, version)
                           for version in ('1.8.3', '1.8.9', '1.9.1', '1.9.2',
                                           '1.9.3', '1.9.4', '1.9.5')]
                self.assertNotIn('source_version', records[0])
                self.assertNotIn('legacy_policy_sha256', records[0])
                self.assertEqual(1, len({json.dumps(record, indent=2) for record in records}))
                self.assertTrue(records[0]['owner_adoption_required'])
                self.assertEqual('EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED', records[0]['history_import'])


@skip_unless_source_repo('requires authentic published v1.9.5 Git objects', '.git')
class BudgetUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_published(ROOT, cls.base / 'published')
        cls.generated = cls.base / 'generated'
        cls.routing_run = generate_budget_history(cls.source, cls.generated, PROJECT)['routing_run_id']
        cls.candidate = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.candidate / 'MANIFEST.json').read_bytes()).hexdigest()

    def setUp(self):
        self.work = self.base / self._testMethodName
        self.work.mkdir()
        self.sources = {}
        for name in ('state', 'host'):
            path = self.work / (name + '.sqlite')
            shutil.copyfile(self.generated / path.name, path)
            self.sources[name] = {'path': path, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.target = self.work / 'canonical.sqlite'

    def migrate(self):
        return upgrade.import_budget_history(self.target, self.sources, project_id=PROJECT)

    def rows(self):
        with closing(sqlite3.connect(self.target)) as db, db:
            return db.execute('SELECT run_id,status,reserved_tokens,actual_tokens FROM model_runs ORDER BY run_id').fetchall()

    def adoption(self, *, enforcement=True):
        from agentic.budgeting import BudgetLedger, fresh_settings
        from agentic.budget_enrollment import EnrollmentRegistry
        from agentic.store import Store
        from test_awf242_budgeting import FixtureHost
        store = Store(self.sources['state']['path'], PROJECT)
        host = FixtureHost()
        binding = dict(project_id=PROJECT, ticket_id='EX-NEW',
            run_id='55555555-5555-4555-8555-555555555555', dispatch_id='new-dispatch',
            session_id='new-session', role='worker')
        registry = EnrollmentRegistry(self.work / 'enrollment.sqlite', host)
        host.enrollment = dict(binding)
        registry.enroll(binding)
        ledger = BudgetLedger(self.target, registry)
        settings = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=enforcement)
        return store, ledger, binding, settings

    def reserve_adopted(self, store, ledger, binding, settings):
        return store.reserve_budget(binding['run_id'], binding['ticket_id'],
            '2026-11-01T00:00:00Z', 10, None, {}, budgeting=settings,
            budget_ledger=ledger, budget_binding=binding)

    def test_completed_import_admits_new_work_and_replays_without_recharging(self):
        self.migrate()
        store, ledger, binding, settings = self.adoption()
        with store.connection() as db:
            retained = [tuple(r) for r in db.execute('SELECT * FROM reservations')]
        self.assertEqual('RESERVED', self.reserve_adopted(store, ledger, binding, settings))
        self.assertEqual('RESERVED', self.reserve_adopted(store, ledger, binding, settings))
        self.assertEqual(4, ledger.summary(PROJECT)['reservations'])
        self.assertEqual(210, ledger.summary(PROJECT)['reserved_tokens'])
        self.assertEqual(20, ledger.summary(PROJECT)['known_tokens'])
        with store.connection() as db:
            self.assertEqual(retained, [tuple(r) for r in db.execute('SELECT * FROM reservations')])

    def test_unimported_legacy_reservations_refuse_even_accounting_only(self):
        for enforcement in (False, True):
            with self.subTest(enforcement=enforcement):
                store, ledger, binding, settings = self.adoption(enforcement=enforcement)
                with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
                    self.reserve_adopted(store, ledger, binding, settings)
                self.assertEqual(0, ledger.summary(PROJECT)['reservations'])

    def test_forged_or_mismatched_import_proofs_refuse_without_new_charge(self):
        # Tamper only disposable imported state. A matching forged canonical
        # digest cannot excuse a lost charge or a foreign project/ticket.
        mutations = (
            "DELETE FROM budget_history_imports WHERE source_table='reservations'",
            "UPDATE budget_history_imports SET source_row_sha256='forged' WHERE source_table='reservations'",
            "UPDATE budget_history_imports SET canonical_row_sha256='forged' WHERE source_table='reservations'",
            "UPDATE budget_history_imports SET run_id='forged' WHERE source_table='reservations'",
            "UPDATE budget_history_imports SET source_id='999' || source_id WHERE source_table='reservations'",
            "UPDATE budget_history_imports SET source_table='model_runs' WHERE source_table='reservations'",
            "UPDATE budget_history_imports SET source_sha256='forged' WHERE source_table='reservations'",
            "DELETE FROM model_runs WHERE phase='legacy-state-store'",
            "UPDATE model_runs SET actual_tokens=0 WHERE phase='legacy-state-store'",
            "UPDATE model_runs SET project_id='foreign' WHERE phase='legacy-state-store'",
            "UPDATE model_runs SET ticket_id='foreign' WHERE phase='legacy-state-store'",
        )
        self.migrate()
        original = self.target.read_bytes()
        store, ledger, binding, settings = self.adoption(enforcement=False)
        for sql in mutations:
            with self.subTest(sql=sql):
                self.target.write_bytes(original)
                with closing(sqlite3.connect(self.target)) as db, db:
                    db.execute('DROP TRIGGER budget_import_no_update')
                    db.execute('DROP TRIGGER budget_import_no_delete')
                    db.execute(sql)
                    for row in db.execute("SELECT * FROM model_runs WHERE phase='legacy-state-store'").fetchall():
                        db.row_factory = sqlite3.Row
                        current = db.execute('SELECT * FROM model_runs WHERE run_id=?', (row[0],)).fetchone()
                        if sql.startswith('UPDATE model_runs'):
                            db.execute('UPDATE budget_history_imports SET canonical_row_sha256=? WHERE run_id=?',
                                (upgrade._budget_row_hash(current), row[0]))
                with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
                    self.reserve_adopted(store, ledger, binding, settings)
                with closing(sqlite3.connect(self.target)) as db:
                    self.assertIsNone(db.execute('SELECT 1 FROM model_runs WHERE run_id=?', (binding['run_id'],)).fetchone())

    def test_changed_retained_reservation_requires_new_import(self):
        self.migrate()
        store, ledger, binding, settings = self.adoption(enforcement=False)
        store.settle_budget('22222222-2222-4222-8222-222222222222', 30, 3)
        with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
            self.reserve_adopted(store, ledger, binding, settings)

    def test_off_needs_no_import_and_legacy_mode_keeps_original_limits(self):
        from agentic.budgeting import fresh_settings
        store, ledger, binding, _ = self.adoption()
        with patch.object(ledger, 'reserve', side_effect=AssertionError('OFF opened ledger')):
            self.assertEqual('OFF', self.reserve_adopted(store, None, binding, fresh_settings()))
        self.assertFalse(self.target.exists())
        store.control('RUNNING', 'Disposable legacy admission test', reconciled=True)
        limits = dict(runs=2, tokens=10000, cost=10000, daily_cost=10000)
        with self.assertRaisesRegex(ValidationError, 'hard limit'):
            store.reserve_budget(binding['run_id'], 'EX-195', '2026-11-01T00:00:00Z', 1, 1, limits)
        limits['runs'] = 3
        self.assertEqual('RESERVED', store.reserve_budget(binding['run_id'], 'EX-195',
            '2026-11-01T00:00:00Z', 1, 1, limits))

    def test_import_preserves_sources_unknown_usage_and_exact_deduplication(self):
        before = {name: item['path'].read_bytes() for name, item in self.sources.items()}
        report = self.migrate()
        self.assertEqual(3, len(self.rows()))
        self.assertIn(('22222222-2222-4222-8222-222222222222', 'reserved', 100, None), self.rows())
        self.assertIn(('33333333-3333-4333-8333-333333333333', 'settled', 100, 20), self.rows())
        self.assertEqual('IMPORTED', report['status'])
        # A second per-host copy of the very same run proves deduplication,
        # rather than assuming equal totals or a ticket imply equal identities.
        self.assertEqual(3, self.migrate()['deduplicated_rows'])
        self.sources['host-copy'] = dict(self.sources['host'])
        original = self.target.read_bytes()
        self.assertEqual(4, self.migrate()['deduplicated_rows'])
        self.assertEqual(original, self.target.read_bytes())
        self.assertEqual(before, {name: self.sources[name]['path'].read_bytes() for name in before})
        from agentic.budgeting import admit_totals
        with closing(sqlite3.connect(self.target)) as db, db:
            db.row_factory = sqlite3.Row
            admit_totals(db, PROJECT, 'EX-195', dict(max_tokens_per_project=221, max_tokens_per_ticket=221), 1)
            with self.assertRaisesRegex(ValidationError, 'budget exhausted'):
                admit_totals(db, PROJECT, 'EX-195', dict(max_tokens_per_project=220, max_tokens_per_ticket=220), 1)

    def test_interruption_rolls_back_rows_and_import_proofs_then_recovers(self):
        original = upgrade._import_budget_row
        count = 0
        def interrupt(*args, **kwargs):
            nonlocal count
            count += 1
            result = original(*args, **kwargs)
            if count == 2:
                raise OSError('synthetic interrupted import')
            return result
        with patch.object(upgrade, '_import_budget_row', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.migrate()
        with closing(sqlite3.connect(self.target)) as db, db:
            self.assertEqual([], db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='budget_history_imports'").fetchall())
        self.migrate()
        self.assertEqual(3, len(self.rows()))
        self.assertEqual(3, self.migrate()['deduplicated_rows'])

    def test_changed_source_or_foreign_project_refuses_without_partial_import(self):
        self.sources['host']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValidationError, 'digest'):
            self.migrate()
        self.assertFalse(self.target.exists())
        self.sources['host']['sha256'] = hashlib.sha256(self.sources['host']['path'].read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValidationError, 'project'):
            upgrade.import_budget_history(self.target, self.sources, project_id='foreign')
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual([], db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())

    def test_conflicting_canonical_identity_never_refunds_or_duplicates(self):
        self.migrate()
        with closing(sqlite3.connect(self.target)) as db, db:
            db.execute("UPDATE model_runs SET actual_tokens=0 WHERE run_id='33333333-3333-4333-8333-333333333333'")
        damaged = self.target.read_bytes()
        with self.assertRaisesRegex(ValidationError, 'conflict'):
            self.migrate()
        self.assertEqual(damaged, self.target.read_bytes())

    def test_independent_import_oracle_rejects_lost_charge_even_with_matching_proof(self):
        before = qualify_upgrade.prepare_budget_import(self.sources, project_id=PROJECT)
        self.migrate()
        with patch.object(upgrade, 'import_budget_history', side_effect=AssertionError('migration called')):
            self.assertTrue(qualify_upgrade.check_budget_import(before, self.sources, self.target)['preserved'])
            with closing(sqlite3.connect(self.target)) as db, db:
                db.execute("DELETE FROM model_runs WHERE run_id='33333333-3333-4333-8333-333333333333'")
            result = qualify_upgrade.check_budget_import(before, self.sources, self.target)
            self.assertFalse(result['preserved'], result)
            self.assertTrue(any('charge' in v for v in result['violations']))

    def test_faulty_importer_cannot_approve_its_own_lost_row(self):
        before = qualify_upgrade.prepare_budget_import(self.sources, project_id=PROJECT)
        original = upgrade._import_budget_row
        def lose_settlement(db, row, *args):
            if row['run_id'] == '33333333-3333-4333-8333-333333333333':
                return False  # Fault injection, never a change to published history.
            return original(db, row, *args)
        with patch.object(upgrade, '_import_budget_row', side_effect=lose_settlement):
            self.migrate()
        self.assertFalse(qualify_upgrade.check_budget_import(before, self.sources, self.target)['preserved'])

    def test_independent_oracle_rejects_forged_source_row_proof(self):
        before = qualify_upgrade.prepare_budget_import(self.sources, project_id=PROJECT)
        self.migrate()
        with closing(sqlite3.connect(self.target)) as db, db:
            # Fault injection into the disposable result, never published bytes.
            db.execute('DROP TRIGGER budget_import_no_update')
            db.execute("UPDATE budget_history_imports SET source_row_sha256=?", ('0' * 64,))
        self.assertFalse(qualify_upgrade.check_budget_import(before, self.sources, self.target)['preserved'])

    def test_import_proofs_cannot_be_replaced_or_rewritten(self):
        self.migrate()
        with closing(sqlite3.connect(self.target)) as db, db:
            proof = db.execute('SELECT * FROM budget_history_imports LIMIT 1').fetchone()
            for sql, values in (
                ('UPDATE budget_history_imports SET source_row_sha256=?', ('0' * 64,)),
                ('DELETE FROM budget_history_imports', ()),
                ('INSERT OR REPLACE INTO budget_history_imports VALUES (?,?,?,?,?,?)',
                 (*proof[:3], '0' * 64, *proof[4:]))):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql, values)
        self.assertEqual(3, self.migrate()['deduplicated_rows'])

    def test_concurrent_importers_charge_each_identity_once(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(pool.map(lambda _: self.migrate(), range(2)))
        self.assertEqual([0, 3], sorted(r['imported_rows'] for r in reports))
        self.assertEqual(3, len(self.rows()))

    def test_imported_overrun_remains_charged_and_blocks_enforcement(self):
        path = self.generated / 'overrun.sqlite'
        self.sources = {'overrun': {'path': path, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
        before = qualify_upgrade.prepare_budget_import(self.sources, project_id=PROJECT)
        self.migrate()
        self.assertEqual([('44444444-4444-4444-8444-444444444444', 'quarantined', 100, 120)], self.rows())
        self.assertTrue(qualify_upgrade.check_budget_import(before, self.sources, self.target)['preserved'])
        from agentic.budgeting import admit_totals
        with closing(sqlite3.connect(self.target)) as db:
            db.row_factory = sqlite3.Row
            with self.assertRaisesRegex(ValidationError, 'Unresolved budget overrun'):
                admit_totals(db, PROJECT, 'EX-195', dict(max_tokens_per_project=10000, max_tokens_per_ticket=10000), 1)

    def test_retained_budget_settings_preserve_bytes_without_claiming_pr_acceptance(self):
        from agentic.installer import install
        from agentic.budgeting import fresh_settings
        from agentic.canonical import load_yaml
        project = self.work / 'retained-project'
        install_baseline(self.source, project)
        operating = project / 'OPERATING_CONFIG.yaml'
        value = load_yaml(operating.read_bytes())
        value['budgeting'] = fresh_settings()
        original = (json.dumps(value, indent=2) + '\n').encode()
        operating.write_bytes(original)
        before = qualify_upgrade.inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        path = '.agentic-state/upgrade/budgeting-1.9.6.json'
        from agentic.upgrade import budget_upgrade_disposition
        disposition = budget_upgrade_disposition(
            (project / '.agentic/PROJECT_CONFIG.yaml').read_bytes(), original, '1.9.5')
        # Both independent records retain the settings without claiming that a
        # default-branch settings PR was observed.
        expected = dict(disposition, owner_adoption_required=True)
        self.assertEqual(expected, disposition)
        self.assertEqual(hashlib.sha256((json.dumps(expected, indent=2,
            ensure_ascii=False) + '\n').encode()).hexdigest(), policy['files'][path]['sha256'])
        self.assertEqual('EXPLICIT_SETTINGS_RETAINED', disposition['disposition'])
        self.assertEqual(value['budgeting'], disposition['settings'])
        self.assertEqual(before, qualify_upgrade.inventory(project))
        # Protected-only edits can travel in the upgrade PR. They do not invalidate
        # the authentic legacy audit or authorize effective settings by themselves.
        plan = install(self.candidate, project, self.pin, mode='upgrade',
                       configure=True, discover=False, dry_run=True)
        self.assertFalse(plan['installed'])
        self.assertEqual(before, qualify_upgrade.inventory(project))
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False,
                conflict='backup')
        self.assertEqual(original, operating.read_bytes())
        comparison = qualify_upgrade.compare_upgrade(before, qualify_upgrade.inventory(project), policy, project)
        self.assertTrue(comparison['preserved'], comparison['violations'])

    def test_upgrade_records_legacy_disposition_without_adding_off_settings(self):
        from agentic.installer import install
        from agentic.canonical import load_yaml
        project = self.work / 'project'
        install_baseline(self.source, project)
        original = (project / 'OPERATING_CONFIG.yaml').read_bytes()
        history = {p.name: p.read_bytes() for p in (project / '.agentic-state/operating/changes').glob('*.json')}
        self.assertTrue(history)
        plan = install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False, dry_run=True)
        self.assertEqual('LEGACY_RETAINED', plan['upgrade']['budgeting']['disposition'])
        result = install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual('LEGACY_RETAINED', result['upgrade']['budgeting']['disposition'])
        self.assertEqual('1.9.5', result['upgrade']['detected_version'])
        self.assertEqual([('1.9.5', '1.9.6')],
            [(step['from'], step['to']) for step in result['upgrade']['steps']])
        self.assertEqual(original, (project / 'OPERATING_CONFIG.yaml').read_bytes())
        self.assertNotIn('budgeting', load_yaml(original))
        record = project / '.agentic-state/upgrade/budgeting-1.9.6.json'
        self.assertEqual('LEGACY_RETAINED', json.loads(record.read_bytes())['disposition'])
        import subprocess
        shown = subprocess.run([sys.executable, '-B', '-I', str(project / '.agentic/scripts/workflow.py'),
            '--root', str(project), 'operating', 'show', '--json'], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
        self.assertEqual('LEGACY_RETAINED', json.loads(shown.stdout)['budgeting']['disposition'])
        self.assertEqual(history, {p.name: p.read_bytes() for p in (project / '.agentic-state/operating/changes').glob('*.json')})
        # Real 1.9.5 events have neither the new projection hashes nor confirmation
        # fields. Read and extend their hash chain after a settings PR is merged.
        self.assertTrue(all('unprotected_after_hash' not in json.loads(raw) for raw in history.values()))
        from settings_pr_fixtures import publish_fixture_settings
        from agentic import operating as op
        gov = load_yaml((project / op.GOVERNANCE).read_bytes())
        enabled = load_yaml(original)
        from agentic.budgeting import fresh_settings
        enabled['budgeting'] = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        def cli(*arguments):
            result = subprocess.run([sys.executable, '-B', '-I', str(project / '.agentic/scripts/workflow.py'),
                '--root', str(project), 'operating', *arguments, '--json'],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)
        from agentic.installer import INSTALLED, PROVENANCE
        identity = {name: (project / name).read_bytes() for name in (INSTALLED, PROVENANCE)}
        publish_fixture_settings(self, project, gov, load_yaml(original))
        (project / op.CONFIG).write_bytes(op._json(enabled))
        self.assertNotIn('budgeting', cli('show')['configuration'])
        publish_fixture_settings(self, project, gov, enabled)
        (project / op.CONFIG).write_bytes(original)
        self.assertTrue(cli('show')['configuration']['budgeting']['enforcement_enabled'])
        changed = cli('set', '--instruction', 'Two streams', '--set', 'streams.count=2')
        self.assertEqual(2, changed['streams'])
        self.assertTrue(cli('show')['configuration']['budgeting']['enforcement_enabled'])
        for name, raw in identity.items():
            self.assertEqual(raw, (project / name).read_bytes())
        for name, raw in history.items():
            self.assertEqual(raw, (project / op.STATE / 'changes' / name).read_bytes())



if __name__ == '__main__':
    unittest.main()
