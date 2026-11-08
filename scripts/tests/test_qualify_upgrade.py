"""Preservation evidence is read-only, exact and cannot waive target identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import (compare, inventory, write_report, plan, checked_bootstrap, qualify,
                             _creation_state, _json_identity, _historical_state, _check_state_continuity)
import os
from copy import deepcopy


class PreservationReportTests(unittest.TestCase):
    def test_budget_import_continuity_requires_baseline_and_real_sources(self):
        from qualify_upgrade import prepare_budget_import, check_budget_import
        with self.assertRaises(ValueError):
            prepare_budget_import({}, project_id='project')
        result = check_budget_import(None, {}, self.root / 'missing.sqlite')
        self.assertFalse(result['preserved'])
        self.assertFalse((self.root / 'missing.sqlite').exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        (self.root / '.agentic').mkdir(parents=True)
        self.config = self.root / '.agentic/PROJECT_CONFIG.yaml'
        self.config.write_text(json.dumps({'github': {'repository': 'example-org/example-repo',
            'repository_id': 101, 'base_branch': 'trunk'}}), encoding='utf-8')
        (self.root / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')

    def test_installed_receipts_require_canonical_commands_and_bound_outputs(self):
        # Unit validation inputs, not executed qualification evidence.
        pin, policy = 'a' * 64, 'b' * 64
        runtime = self.root / '.agentic/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        script = self.root / '.agentic/scripts/workflow.py'
        result = {'status': 'CONFIGURED', 'source_manifest_sha256': pin,
                  'configuration': {'policy_sha256': policy},
                  'pre_install_configuration': {'policy_sha256': policy}, 'post_install_checks': []}
        for action, output in [('verify-installation', {'integrity_valid': True, 'source_manifest_sha256': pin}),
                               ('validate-config', {'status': 'ACCEPTED', 'policy_sha256': policy})]:
            result['post_install_checks'].append({'command': [str(runtime), '-B', '-I', str(script),
                '--root', str(self.root), action], 'exit_code': 0, 'diagnostic': None, 'output': output})
        for name in ('installed-manifest.json', 'workflow-version.yaml'):
            (self.root / '.agentic' / name).write_bytes(b'unit validation input')
        self.assertEqual('CONFIGURED', checked_bootstrap(result, self.root, pin)['status'])
        mutations = [lambda r: r.update(status='ACTIVE'),
            lambda r: r['post_install_checks'][0].update(exit_code=1),
            lambda r: r['post_install_checks'][1].update(exit_code=None),
            lambda r: r['post_install_checks'][0]['command'].remove('-I'),
            lambda r: r['post_install_checks'][0]['output'].update(integrity_valid=False),
            lambda r: r['post_install_checks'][0]['output'].update(source_manifest_sha256='c' * 64),
            lambda r: r['post_install_checks'][1]['output'].update(status='REJECTED'),
            lambda r: r['post_install_checks'][1]['output'].update(policy_sha256='c' * 64),
            lambda r: r['pre_install_configuration'].update(policy_sha256='c' * 64),
            lambda r: r['post_install_checks'].pop()]
        for mutate in mutations:
            damaged = deepcopy(result)
            mutate(damaged)
            with self.assertRaises(ValueError):
                checked_bootstrap(damaged, self.root, pin)

    def test_inventory_is_nonwriting_and_contains_modes_and_history(self):
        state = self.root / '.agentic-state/operating/changes/record.json'
        state.parent.mkdir(parents=True)
        state.write_bytes(b'synthetic history')
        before = {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                  for p in self.root.rglob('*') if p.is_file()}
        report = inventory(self.root)
        self.assertEqual('SYNTHETIC_OWNER_CUSTOMIZATION', report['evidence_kind'])
        self.assertIn('.agentic-state/operating/changes/record.json', report['files'])
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual([], compare(report, report, {})['violations'])

    def test_inventory_retains_canonical_owner_paths_and_managed_link_refusals(self):
        owners = ('.github/CODEOWNERS', '.github/PULL_REQUEST_TEMPLATE.md', 'docs/CODEOWNERS')
        for relative in owners:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'owner content\n')
        before = inventory(self.root)
        self.assertTrue(set(owners) <= before['files'].keys())
        for relative in owners:
            (self.root / relative).unlink()
        self.assertTrue(set(owners) <= compare(before, inventory(self.root), {})['changes'].keys())
        managed = self.root / 'AGENTS.md'
        managed.write_bytes(b'managed content\n')
        os.link(managed, Path(self.temporary.name) / 'alias')
        with self.assertRaisesRegex(ValueError, 'AGENTS.md; preserve'):
            inventory(self.root)

    def test_creation_permission_probes_match_installer_and_leave_no_delta(self):
        from agentic.safeio import Tree
        import stat
        before = inventory(self.root)
        file_path = '.agentic/new/nested/managed.txt'
        directory_path = '.agentic/new/nested/'
        file_state = _creation_state(self.root, file_path)
        directory_state = _creation_state(self.root, directory_path, directory=True)
        readonly_state = _creation_state(self.root, file_path, readonly=True)
        self.assertEqual(before, inventory(self.root))
        with Tree(self.root) as tree:
            tree.write(file_path, b'permission creation test\n')
        after = inventory(self.root)
        self.assertEqual(file_state, {key: after['files'][file_path][key]
                                     for key in ('mode', 'permissions', 'named_streams')})
        self.assertEqual(directory_state, after['directories'][directory_path])
        (self.root / file_path).chmod(stat.S_IREAD)
        readonly = inventory(self.root)['files'][file_path]
        self.assertEqual(readonly_state, {key: readonly[key] for key in ('mode', 'permissions', 'named_streams')})

    def test_config_value_identity_preserves_types(self):
        self.assertNotEqual(_json_identity({'enabled': True}), _json_identity({'enabled': 1}))
        self.assertNotEqual(_json_identity({'count': 12}), _json_identity({'count': 12.0}))
        self.assertEqual(_json_identity({'b': 2, 'a': 1}), _json_identity({'a': 1, 'b': 2}))

    def test_exact_delta_and_identity_cannot_be_waived(self):
        before = inventory(self.root)
        path = 'PROJECT_INSTRUCTIONS.md'
        (self.root / path).write_bytes(b'changed')
        after = inventory(self.root)
        delta = json.loads(json.dumps({path: {'before': before['files'][path], 'after': after['files'][path]}}))
        self.assertFalse(compare(before, after, {})['preserved'])
        self.assertTrue(compare(before, after, delta)['preserved'])
        after['identity']['base_branch'] = 'main'
        self.assertFalse(compare(before, after, delta)['preserved'])
        after['identity']['base_branch'] = 'trunk'
        after['files'][path]['sha256'] = '0' * 64
        self.assertFalse(compare(before, after, delta)['preserved'])

    def test_unused_delta_is_not_silently_accepted(self):
        report = inventory(self.root)
        self.assertFalse(compare(report, report, {'missing': {'before': None, 'after': None}})['preserved'])

    def test_empty_directory_creation_is_detected(self):
        before = inventory(self.root)
        (self.root / 'unexpected').mkdir()
        self.assertFalse(compare(before, inventory(self.root), {})['preserved'])

    @unittest.skipUnless(os.name == 'nt', 'Windows named-stream preservation regression')
    def test_inventory_detects_owner_stream_loss(self):
        stream = Path(str(self.root / 'PROJECT_INSTRUCTIONS.md') + ':owner-receipt')
        stream.write_bytes(b'synthetic stream evidence')
        before = inventory(self.root)
        stream.unlink()
        result = compare(before, inventory(self.root), {})
        self.assertFalse(result['preserved'], result)

    def test_missing_named_stream_evidence_cannot_pass(self):
        report = inventory(self.root)
        report['files']['PROJECT_INSTRUCTIONS.md'].pop('named_streams', None)
        self.assertFalse(compare(report, report, {})['preserved'])

    def test_unreadable_named_streams_cannot_be_waived(self):
        before = inventory(self.root)
        with patch('qualify_upgrade._named_streams', return_value={
                'verified': False, 'method': 'windows-find-streams'}):
            after = inventory(self.root)
        delta = compare(before, after, {})['changes']
        self.assertFalse(compare(before, after, delta)['preserved'])

    def test_independent_state_oracle_preserves_all_historical_values(self):
        import sqlite3
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript('''
            CREATE TABLE ledger (identity TEXT PRIMARY KEY, charge, settlement BLOB);
            INSERT INTO ledger VALUES ('reserved', 100, NULL);
            INSERT INTO ledger VALUES ('settled', 20, X'0001');
            INSERT INTO ledger VALUES ('unknown', NULL, NULL);
            CREATE TABLE audit (identity TEXT, value TEXT);
            INSERT INTO audit VALUES ('entry', 'unchanged');
            INSERT INTO audit VALUES ('entry', 'unchanged');
            CREATE TABLE empty_history (identity TEXT);
        ''')
        original = connection.serialize()
        path = '.agentic-state/routing/ledger.sqlite'
        state = {path: original, '.agentic-state/reservations/run.json': b'{"id":"reserved","charge":100}',
                 '.agentic-state/operating/changes/audit.json': b'{"id":"entry","value":"unchanged"}'}
        # This oracle must not invoke upgrade code, even for a clean result.
        with patch('agentic.upgrade.apply_chain', side_effect=AssertionError('migration called')), \
                patch('agentic.upgrade._migrate_routing_ledger', side_effect=AssertionError('migration called')):
            historical = _historical_state(state)
            self.assertEqual([], _check_state_continuity(historical, set(state), state.__getitem__))
            for sql in ("DELETE FROM ledger WHERE identity='settled'",
                        "UPDATE ledger SET identity='replacement' WHERE identity='reserved'",
                        "UPDATE ledger SET charge=0 WHERE identity='unknown'",
                        "UPDATE ledger SET settlement=X'0002' WHERE identity='settled'",
                        "UPDATE ledger SET charge='100' WHERE identity='reserved'",
                        "DELETE FROM audit WHERE rowid=2",
                        "UPDATE audit SET rowid=3 WHERE rowid=1",
                        "ALTER TABLE empty_history RENAME COLUMN identity TO replacement"):
                with self.subTest(sql=sql):
                    connection.deserialize(original)
                    connection.execute(sql)
                    connection.commit()
                    changed = {**state, path: connection.serialize()}
                    self.assertNotEqual(original, changed[path])
                    self.assertTrue(_check_state_continuity(historical, set(state), changed.__getitem__))
            for changed_path in set(state) - {path}:
                changed = {**state, changed_path: b'{}'}
                self.assertTrue(_check_state_continuity(historical, set(state), changed.__getitem__))
            self.assertTrue(_check_state_continuity(None, set(state), state.__getitem__))
            self.assertTrue(_check_state_continuity({}, set(state), state.__getitem__))
            connection.deserialize(original)
            connection.execute('ALTER TABLE ledger ADD COLUMN additive TEXT')
            connection.commit()
            changed = {**state, path: connection.serialize()}
            self.assertEqual([], _check_state_continuity(historical, set(state), changed.__getitem__))

    def test_archive_nested_runtime_git_and_cache_names_are_evidence(self):
        for name in ('.venv', '.git', '__pycache__'):
            with self.subTest(name=name):
                receipt = self.root / '.agentic-state/archive/owner-backup' / name / 'owner-receipt.json'
                receipt.parent.mkdir(parents=True)
                receipt.write_bytes(b'synthetic archived evidence')
                before = inventory(self.root)
                receipt.unlink()
                result = compare(before, inventory(self.root), {})
                self.assertFalse(result['preserved'])
                self.assertIn(receipt.relative_to(self.root).as_posix(), result['changes'])

    def test_missing_permission_evidence_cannot_be_accepted_by_delta(self):
        before = inventory(self.root)
        after = deepcopy(before)
        before['files']['PROJECT_INSTRUCTIONS.md'].pop('permissions', None)
        after['files']['PROJECT_INSTRUCTIONS.md'].pop('permissions', None)
        self.assertFalse(compare(before, after, {})['preserved'])

    def test_unavailable_permission_capture_blocks_even_an_exact_delta(self):
        before = inventory(self.root)
        with patch('qualify_upgrade._permissions', return_value={
                'verified': False, 'method': 'windows-owner-group-dacl-sddl',
                'diagnostic': 'permission capture unavailable'}):
            after = inventory(self.root)
        unapproved = compare(before, after, {})
        accepted = compare(before, after, unapproved['changes'])
        self.assertFalse(accepted['preserved'])
        self.assertTrue(any('permissions unverified' in item for item in accepted['violations']))

    def test_only_actual_git_and_runtime_locations_are_excluded(self):
        for relative in ('.git/objects/ignored', '.agentic/.venv/runtime'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'outside preservation inventory')
        report = inventory(self.root)
        self.assertEqual(['.agentic/.venv', '.git'], report['excluded_paths'])
        self.assertFalse(any(path.startswith(('.git/', '.agentic/.venv/')) for path in report['files']))

    def test_unreadable_archive_cannot_silently_skip_preservation(self):
        archive = self.root / '.agentic-state/archive'
        archive.mkdir(parents=True)
        (archive / 'owner-receipt.json').write_bytes(b'synthetic archived evidence')
        before = inventory(self.root)
        original_bytes = (archive / 'owner-receipt.json').read_bytes()
        original = os.scandir
        archive_identity = archive.resolve()
        attempts = []

        def deny_archive(path):
            if Path(path).resolve() == archive_identity:
                attempts.append(path)
                raise PermissionError(13, 'Synthetic enumeration refusal', str(archive_identity))
            return original(path)

        with patch('qualify_upgrade.os.scandir', side_effect=deny_archive):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                inventory(self.root)
        self.assertEqual(1, len(attempts))
        self.assertEqual(archive_identity, Path(attempts[0]).resolve())
        self.assertEqual(original_bytes, (archive / 'owner-receipt.json').read_bytes())
        self.assertEqual(before, inventory(self.root))

    @unittest.skipUnless(os.name == 'nt', 'Windows ownership/DACL regression')
    def test_windows_added_acl_entry_is_detected_without_disclosing_sids(self):
        import subprocess
        before = inventory(self.root)
        child = subprocess.run(['icacls', str(self.root / 'PROJECT_INSTRUCTIONS.md'),
                                '/grant', '*S-1-1-0:(R)'], capture_output=True)
        self.assertEqual(0, child.returncode, 'Disposable ACL mutation failed')
        after = inventory(self.root)
        self.assertFalse(compare(before, after, {})['preserved'])
        rendered = json.dumps(after)
        self.assertNotIn('S-1-', rendered)
        self.assertNotIn(str(self.root), rendered)

    def qualification_with_unit_children(self, mutation, *, omit_checks=False, budget_preserved=True, omit_ledger=False):
        # Mocked child receipts exercise orchestration only, never installed evidence.
        from types import SimpleNamespace
        sys.path.insert(0, str(ROOT / '.agentic/tests'))
        import upgrade_195_fixtures
        work = Path(self.temporary.name) / 'qualification'

        def child(command, **kwargs):
            if '--manifest-only' in command:
                (work / 'candidate/MANIFEST.json').write_bytes(b'unit candidate manifest')
            elif '--mode' in command:
                project = work / 'project'
                if command[command.index('--mode') + 1] == 'install':
                    (project / '.agentic').mkdir(parents=True)
                    (project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(self.config.read_bytes())
                    (project / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'synthetic owner customization\n')
                else:
                    self.assertIn('--on-conflict', command)
                    self.assertEqual('backup', command[command.index('--on-conflict') + 1])
                    mutation(project)
            return SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')

        def comparison(before, after, policy, project, **kwargs):
            result = compare(before, after, {})
            if not omit_checks:
                result['checks'] = {name: 'PASS' for name in
                    ('source_managed_membership', 'historical_state_continuity', 'named_streams')}
            if not omit_ledger:
                result['migration'] = {'state_migrations': [{'path': '.agentic-state/routing/ledger.sqlite',
                    'action': 'migrated_schema'}], 'historical_continuity': {
                    'sqlite_paths': ['.agentic-state/routing/ledger.sqlite']}}
            return result

        import hashlib
        with patch('qualify_upgrade.source_identity', return_value={'commit': 'a' * 40, 'tree': 'b' * 40,
                'manifest_sha256': hashlib.sha256(b'unit candidate manifest').hexdigest()}), \
                patch('build_release.release_paths', return_value=['MANIFEST.json']), \
                patch('build_release.release_bytes', return_value=b'unit candidate manifest'), \
                patch('build_release.SOURCE_CONFIGS', {}), \
                patch('qualify_upgrade.verify_release'), \
                patch.object(upgrade_195_fixtures, 'materialize_published', return_value=ROOT), \
                patch('qualify_upgrade.run_bounded', side_effect=child), \
                patch('qualify_upgrade.prepare_upgrade', return_value='unit policy') as prepare, \
                patch('qualify_upgrade.compare_upgrade', side_effect=comparison) as preservation, \
                patch('qualify_upgrade._prepare_qualification_budget', return_value='unit budget baseline', create=True), \
                patch('qualify_upgrade._seed_qualification_state'), \
                patch('qualify_upgrade._qualify_budget_import', return_value={'preserved': budget_preserved}, create=True), \
                patch('qualify_upgrade.checked_bootstrap', return_value={'status': 'CONFIGURED'}):
            result = qualify(ROOT, work)
            prepare.assert_called_once()
            preservation.assert_called_once()
            self.assertEqual('unit policy', preservation.call_args.args[2])
            return result

    def test_qualification_rejects_deleted_owner_instructions_after_valid_bootstrap(self):
        result = self.qualification_with_unit_children(
            lambda project: (project / 'PROJECT_INSTRUCTIONS.md').unlink())
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('preservation', result['failed_stage'])
        self.assertIn('PROJECT_INSTRUCTIONS.md', result['preservation']['changes'])

    def test_qualification_requires_executed_accepted_preservation(self):
        result = self.qualification_with_unit_children(lambda project: None)
        self.assertEqual('QUALIFIED_DISPOSABLE_UPGRADE', result['status'])
        self.assertTrue(result['preservation']['preserved'])
        self.assertEqual(result['before'], result['after'])

    def test_qualification_cannot_pass_missing_oracle_checks(self):
        result = self.qualification_with_unit_children(lambda project: None, omit_checks=True)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('preservation', result['failed_stage'])

    def test_qualification_cannot_pass_a_lost_budget_import(self):
        result = self.qualification_with_unit_children(lambda project: None, budget_preserved=False)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('budget_import', result['failed_stage'])

    def test_qualification_cannot_pass_unexercised_ledger_migration(self):
        result = self.qualification_with_unit_children(lambda project: None, omit_ledger=True)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('ledger_migration_coverage', result['failed_stage'])

    def test_qualification_cannot_pass_unverified_permissions(self):
        with patch('qualify_upgrade._permissions', return_value={
                'verified': False, 'method': 'windows-owner-group-dacl-sddl'}):
            result = self.qualification_with_unit_children(lambda project: None)
        self.assertEqual('NOT_QUALIFIED', result['status'])
        self.assertEqual('preservation', result['failed_stage'])

    def test_plan_refuses_a_mutating_installer(self):
        def mutate(*args, **kwargs):
            (self.root / 'unexpected').mkdir()
            return {'source_manifest_sha256': '0' * 64}
        with patch('qualify_upgrade.install', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'mutated'):
                plan(ROOT, self.root, '0' * 64)

    def test_report_cannot_write_inside_source_or_project_or_overwrite(self):
        for path in (self.root / 'report.json', ROOT / 'report.json'):
            with self.assertRaises(ValueError):
                write_report(path, {}, project=self.root)
            self.assertFalse(path.exists())
        other_source = Path(self.temporary.name) / 'other-source'
        other_source.mkdir()
        with self.assertRaises(ValueError):
            write_report(other_source / 'report.json', {}, project=self.root, source=other_source)
        output = Path(self.temporary.name) / 'external.json'
        write_report(output, {'status': 'NOT_QUALIFIED'}, project=self.root)
        with self.assertRaises(FileExistsError):
            write_report(output, {}, project=self.root)


if __name__ == '__main__':
    unittest.main()
