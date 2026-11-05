"""Preservation evidence is read-only, exact and cannot waive target identity."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import compare, inventory, write_report, plan, checked_bootstrap, qualify
import os
from copy import deepcopy


class PreservationReportTests(unittest.TestCase):
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
        original = os.scandir

        def deny_archive(path):
            if Path(path) == archive:
                raise PermissionError('Synthetic enumeration refusal')
            return original(path)

        with patch('qualify_upgrade.os.scandir', side_effect=deny_archive):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                inventory(self.root)

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

    def qualification_with_unit_children(self, mutation):
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
                    mutation(project)
            return SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')

        with patch('build_release.release_paths', return_value=[]), \
                patch('build_release.SOURCE_CONFIGS', {}), \
                patch.object(upgrade_195_fixtures, 'materialize_published', return_value=ROOT), \
                patch('qualify_upgrade.run_bounded', side_effect=child), \
                patch('qualify_upgrade.checked_bootstrap', return_value={'status': 'CONFIGURED'}):
            return qualify(ROOT, work)

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
        output = Path(self.temporary.name) / 'external.json'
        write_report(output, {'status': 'NOT_QUALIFIED'}, project=self.root)
        with self.assertRaises(FileExistsError):
            write_report(output, {}, project=self.root)


if __name__ == '__main__':
    unittest.main()
