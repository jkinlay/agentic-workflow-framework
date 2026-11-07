"""Guide regressions on disposable copies of authentic published 1.9.5."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests'), str(ROOT / 'scripts')]
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline
from source_only import skip_unless_source_repo
from agentic.installer import install
import qualify_upgrade as q


@skip_unless_source_repo('requires authentic published Git objects', '.git')
class ThirdLookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.published = materialize_published(ROOT, cls.base / 'published')
        cls.candidate = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.candidate / 'MANIFEST.json').read_bytes()).hexdigest()
        cls.baseline = cls.base / 'baseline'
        install_baseline(cls.published, cls.baseline)
        (cls.baseline / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'Owner instructions\n')

    def setUp(self):
        self.work = self.base / self._testMethodName
        self.work.mkdir()
        self.project = self.work / 'project'
        shutil.copytree(self.baseline, self.project)

    def bootstrap(self, *arguments):
        return subprocess.run([sys.executable, '-B', str(self.candidate / 'scripts/bootstrap_project.py'),
            '--dest', str(self.project), '--mode', 'upgrade', '--expected-manifest-sha256', self.pin,
            *arguments], capture_output=True, text=True, timeout=120)

    def workflow(self, *arguments):
        return subprocess.run([sys.executable, '-B', '-I', str(self.project / '.agentic/scripts/workflow.py'),
            '--root', str(self.project), *arguments], capture_output=True, text=True, timeout=120)

    def upgrade(self, *, backup=False):
        before = q.inventory(self.project)
        policy = q.prepare_upgrade(self.candidate, self.project, self.pin, before)
        install(self.candidate, self.project, self.pin, mode='upgrade', conflict='backup' if backup else 'error',
                configure=True, discover=False)
        return before, q.inventory(self.project), policy

    def test_backup_is_exact_upgrade_output_and_cannot_hide_damage(self):
        prior = self.project / '.agentic-backup/11111111-1111-4111-8111-111111111111'
        prior.mkdir(parents=True)
        before, after, policy = self.upgrade(backup=True)
        result = q.compare_upgrade(before, after, policy, self.project)
        self.assertTrue(result['preserved'], result['violations'])
        self.assertTrue(prior.is_dir())
        backups = [path for path in (self.project / '.agentic-backup').glob('*') if path != prior]
        self.assertEqual(1, len(backups))
        prior.rmdir()
        backups[0].rename(prior)
        self.assertFalse(q.compare_upgrade(before, q.inventory(self.project), policy, self.project)['preserved'],
                         'An existing backup UUID must not be reused')
        prior.rename(backups[0])
        prior.mkdir()
        extra = backups[0] / 'extra.txt'
        extra.write_bytes(b'not an installer backup')
        self.assertFalse(q.compare_upgrade(before, q.inventory(self.project), policy, self.project)['preserved'])
        extra.unlink()
        backup = next(p for p in backups[0].rglob('*') if p.is_file())
        original = backup.read_bytes()
        backup.write_bytes(b'tampered backup')
        self.assertFalse(q.compare_upgrade(before, q.inventory(self.project), policy, self.project)['preserved'])
        backup.write_bytes(original)
        (self.project / 'PROJECT_INSTRUCTIONS.md').unlink()
        self.assertFalse(q.compare_upgrade(before, q.inventory(self.project), policy, self.project)['preserved'])

    def test_after_is_rebound_to_owner_bytes(self):
        before, after, policy = self.upgrade()
        (self.project / 'PROJECT_INSTRUCTIONS.md').unlink()
        self.assertFalse(q.compare_upgrade(before, after, policy, self.project)['preserved'])

    def test_snapshot_ignores_unrelated_hardlink(self):
        owner = self.project / 'owner.txt'
        owner.write_bytes(b'owner data')
        os.link(owner, self.work / 'hardlink')
        self.assertEqual(0, self.bootstrap('--dry-run').returncode)
        q.inventory(self.project)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_snapshot_ignores_unrelated_junction(self):
        outside = self.work / 'outside'
        outside.mkdir()
        junction = self.project / 'data'
        child = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
        self.assertEqual(0, child.returncode, child.stderr)
        self.addCleanup(lambda: os.rmdir(junction))
        self.assertEqual(0, self.bootstrap('--dry-run').returncode)
        q.inventory(self.project)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_plan_refuses_managed_runtime_and_backup_junctions_with_remedy(self):
        for relative in ('.agentic/docs/sub', '.agentic/.venv', '.agentic-backup'):
            with self.subTest(path=relative):
                outside = self.work / ('outside-' + relative.replace('/', '-'))
                outside.mkdir()
                junction = self.project / relative
                child = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
                self.assertEqual(0, child.returncode, child.stderr)
                try:
                    child = self.bootstrap('--dry-run', '--on-conflict', 'backup')
                    self.assertEqual(2, child.returncode, child.stdout)
                    self.assertIn(relative, child.stderr)
                    self.assertIn('preserve', child.stderr.lower())
                    outside.rmdir()  # A dangling junction must refuse too.
                    child = self.bootstrap('--dry-run', '--on-conflict', 'backup')
                    self.assertEqual(2, child.returncode, child.stdout)
                    self.assertIn(relative, child.stderr)
                    self.assertIn('preserve', child.stderr.lower())
                finally:
                    os.rmdir(junction)

    def test_plan_names_invalid_owner_schema(self):
        path = '.agentic/schemas/owner.schema.json'
        (self.project / path).write_bytes(b'{invalid')
        child = self.bootstrap('--dry-run')
        self.assertEqual(2, child.returncode, child.stdout)
        self.assertIn(path, child.stderr)

    def test_status_rejects_hand_edited_operating_and_text_explains_legacy(self):
        self.upgrade()
        child = self.workflow('status')
        self.assertIn('LEGACY_RETAINED', child.stdout)
        self.assertIn('EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED', child.stdout)
        self.assertNotIn('Next: python ', child.stdout)
        operating = self.project / 'OPERATING_CONFIG.yaml'
        import yaml
        value = yaml.safe_load(operating.read_bytes())
        value['streams']['count'] = 1 if value['streams']['count'] != 1 else 2
        operating.write_text(json.dumps(value))
        self.assertEqual(2, self.workflow('validate-config').returncode)
        child = self.workflow('status', '--json')
        self.assertNotIn(json.loads(child.stdout)['project_state'], ('CONFIGURED', 'ACTIVE'))

    def test_legacy_budget_adoption_prepares_off_pr_and_preserves_import(self):
        self.upgrade()
        from agentic import operating as op
        from agentic.canonical import load_yaml
        from agentic.budgeting import fresh_settings
        from settings_pr_fixtures import publish_fixture_settings
        gov = load_yaml((self.project / op.GOVERNANCE).read_bytes())
        legacy = load_yaml((self.project / op.CONFIG).read_bytes())
        publish_fixture_settings(self, self.project, gov, legacy)
        from agentic.protected_settings import _git
        _git(self.project, 'checkout', '-qb', 'codex/upgrade')
        from upgrade_195_fixtures import generate_budget_history
        from agentic.upgrade import import_budget_history
        generate_budget_history(self.published, self.work / 'history', gov['project']['id'])
        sources = {p.stem: {'path': p, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in (self.work / 'history').glob('*.sqlite')}
        before = q.prepare_budget_import(sources, project_id=gov['project']['id'])
        canonical = self.work / 'canonical.sqlite'
        import_budget_history(canonical, sources, project_id=gov['project']['id'])
        retained = canonical.read_bytes()
        audit = {p.name: p.read_bytes() for p in (self.project / op.STATE / 'changes').glob('*.json')}
        child = self.workflow('operating', 'set', '--instruction', 'Adopt dormant budget controls',
                              '--adopt-budgeting', '--json')
        self.assertEqual(0, child.returncode, child.stdout + child.stderr)
        self.assertNotIn('budgeting', json.loads(child.stdout)['configuration'])
        prepared = load_yaml((self.project / op.CONFIG).read_bytes())
        self.assertEqual(fresh_settings(), prepared['budgeting'])
        self.assertEqual(retained, canonical.read_bytes())
        self.assertTrue(q.check_budget_import(before, sources, canonical)['preserved'])
        self.assertEqual(audit, {p.name: p.read_bytes() for p in (self.project / op.STATE / 'changes').glob('*.json')})
        publish_fixture_settings(self, self.project, gov, prepared)
        shown = json.loads(self.workflow('operating', 'show', '--json').stdout)
        self.assertFalse(shown['configuration']['budgeting']['accounting_enabled'])
        self.assertFalse(shown['configuration']['budgeting']['enforcement_enabled'])

    def test_plan_calls_out_jira_status_change(self):
        import yaml
        path = self.project / '.agentic/PROJECT_CONFIG.yaml'
        value = yaml.safe_load(path.read_bytes())
        value['jira'].update(enabled=True, site=None, project_key=None)
        path.write_text(json.dumps(value))
        legacy = self.workflow('validate-config')
        self.assertEqual(2, legacy.returncode, legacy.stdout + legacy.stderr)
        self.assertEqual('REJECTED', json.loads(legacy.stdout)['status'])
        child = self.bootstrap('--dry-run')
        self.assertEqual(0, child.returncode, child.stderr)
        change = json.loads(child.stdout)['upgrade']['configuration_status_changes'][0]
        self.assertEqual('REJECTED', change['prior_validation'])
        self.assertEqual('ACCEPTED', change['target_validation'])
        self.assertFalse(change['settings_changed'])
        self.assertFalse(change['jira_write_authority'])

    def test_snapshot_refusal_is_path_bound_and_has_no_traceback(self):
        path = self.project / '.agentic-state/linked'
        path.parent.mkdir(exist_ok=True)
        original = self.work / 'original'
        original.write_bytes(b'state')
        os.link(original, path)
        child = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/qualify_upgrade.py'), 'snapshot',
            '--project', str(self.project), '--output', str(self.work / 'snapshot.json')],
            capture_output=True, text=True, timeout=120)
        self.assertNotEqual(0, child.returncode)
        self.assertNotIn('Traceback', child.stderr)
        self.assertIn('.agentic-state/linked', child.stderr)
        self.assertIn('preserve', child.stderr.lower())

    def test_mismatched_before_refusal_explains_untouched_baseline(self):
        before = q.inventory(self.project)
        (self.project / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'Changed since snapshot\n')
        with self.assertRaisesRegex(ValueError, 'BEFORE.*--baseline-project.*before apply'):
            q.prepare_upgrade(self.candidate, self.project, self.pin, before)


if __name__ == '__main__':
    unittest.main()
