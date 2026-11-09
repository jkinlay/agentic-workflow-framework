"""Second-look regressions on disposable authentic published installations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests'), str(ROOT / 'scripts')]
from agentic import ValidationError
from agentic.installer import install
from source_only import AWF_SOURCE_REPOSITORY, skip_unless_source_repo
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline, seed_legacy_accounting
# The qualification oracle/CLI is source tooling; installed runtime tests still run.
if AWF_SOURCE_REPOSITORY:
    import qualify_upgrade as qualification


@skip_unless_source_repo('requires authentic published Git objects', '.git')
class SecondLookTests(unittest.TestCase):
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

    def setUp(self):
        self.work = self.base / self._testMethodName
        self.work.mkdir()
        self.project = self.work / 'project'
        shutil.copytree(self.baseline, self.project)

    def bootstrap_plan(self):
        return subprocess.run([sys.executable, '-B', str(self.candidate / 'scripts/bootstrap_project.py'),
            '--dest', str(self.project), '--mode', 'upgrade', '--expected-manifest-sha256', self.pin,
            '--dry-run'], capture_output=True, text=True, timeout=120)

    def test_stray_import_file_refuses_nonwriting_plan_with_remedy(self):
        relative = '.agentic/scripts/my_tool.py'
        (self.project / relative).write_bytes(b'# owner tool\n')
        before = qualification.inventory(self.project)
        child = self.bootstrap_plan()
        self.assertEqual(2, child.returncode, child.stderr)
        self.assertIn(relative, child.stderr)
        self.assertIn('move', child.stderr.lower())
        self.assertEqual(before, qualification.inventory(self.project))
        with self.assertRaisesRegex(ValidationError, 'my_tool.py'):
            install(self.candidate, self.project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual(before, qualification.inventory(self.project))

    def test_rejected_yaml_is_not_repaired_by_upgrade(self):
        target = self.project / '.agentic/PROJECT_CONFIG.yaml'
        config = yaml.safe_load(target.read_bytes())
        config['validation']['commands'] = []
        original = ('# owner comment\n' + yaml.safe_dump(config, sort_keys=False)).encode()
        target.write_bytes(original)
        before = qualification.inventory(self.project)
        child = self.bootstrap_plan()
        self.assertEqual(2, child.returncode, child.stderr)
        self.assertIn('validation.commands', child.stderr)
        self.assertEqual(before, qualification.inventory(self.project))
        with self.assertRaisesRegex(ValidationError, 'validation.commands'):
            install(self.candidate, self.project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual(before, qualification.inventory(self.project))

    def test_accepted_yaml_retains_comments_format_and_reports_actual_diff(self):
        target = self.project / '.agentic/PROJECT_CONFIG.yaml'
        original = ('# owner comment\n' + yaml.safe_dump(yaml.safe_load(target.read_bytes()), sort_keys=False)).encode()
        target.write_bytes(original)
        result = install(self.candidate, self.project, self.pin, mode='upgrade', configure=True, discover=False)
        expected = original.replace(b"expected_workflow_version: 1.9.5", b"expected_workflow_version: 1.9.6")
        self.assertEqual(expected, target.read_bytes())
        self.assertIn('-  expected_workflow_version: 1.9.5', result['upgrade']['configuration_diff_total'])
        # Canonical verification and status must share the import-membership guard.
        (self.project / '.agentic/scripts/my_tool.py').write_bytes(b'# stray owner tool\n')
        for action in ('verify-installation', 'status'):
            command = [sys.executable, '-B', '-I', str(self.project / '.agentic/scripts/workflow.py'),
                       '--root', str(self.project), action]
            if action == 'status':
                command.append('--json')
            child = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if action == 'verify-installation':
                self.assertNotEqual(0, child.returncode)
                self.assertIn('my_tool.py', child.stderr + child.stdout)
            else:
                status = json.loads(child.stdout)
                self.assertIn('project_state', status)
                self.assertNotIn(status.get('project_state'), ('CONFIGURED', 'ACTIVE'))

    def test_unrelated_hardlink_is_outside_upgrade_walk(self):
        (self.project / 'data').mkdir()
        owner = self.project / 'data/original'
        owner.write_bytes(b'owner data')
        os.link(owner, self.project / 'data/linked')
        child = self.bootstrap_plan()
        self.assertEqual(0, child.returncode, child.stderr)
        self.assertEqual(b'owner data', owner.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_unrelated_junction_is_outside_upgrade_walk(self):
        outside = self.work / 'outside'
        outside.mkdir()
        junction = self.project / 'data'
        child = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
        self.assertEqual(0, child.returncode, child.stderr)
        self.addCleanup(lambda: os.rmdir(junction))
        child = self.bootstrap_plan()
        self.assertEqual(0, child.returncode, child.stderr)

    @unittest.skipUnless(os.name == 'nt', 'Windows reserved-name regression')
    def test_unrelated_reserved_name_is_outside_upgrade_walk(self):
        data = self.project / 'data'
        data.mkdir()
        # Extended-path spelling creates the actual filename, not a DOS device.
        owner = Path('\\\\?\\' + str(data / 'CON.txt'))
        owner.write_bytes(b'owner reserved-name data')
        self.addCleanup(owner.unlink)
        child = self.bootstrap_plan()
        self.assertEqual(0, child.returncode, child.stderr)
        self.assertEqual(b'owner reserved-name data', owner.read_bytes())

    def test_oversize_state_names_path_and_remedy_without_writes(self):
        relative = '.agentic-state/oversize.bin'
        state = self.project / relative
        state.parent.mkdir(exist_ok=True)
        with state.open('wb') as stream:
            stream.truncate(65 * 1024 * 1024)
        child = self.bootstrap_plan()
        self.assertEqual(2, child.returncode)
        self.assertIn(relative, child.stderr)
        self.assertIn('move', child.stderr.lower())
        self.assertFalse((self.project / '.agentic/INSTALLING.json').exists())

    def test_public_comparison_cannot_waive_owner_or_history_loss(self):
        owner = self.project / 'PROJECT_INSTRUCTIONS.md'
        owner.write_bytes(b'Owner instructions\n')
        seed_legacy_accounting(self.published, self.project)
        before = qualification.inventory(self.project)
        owner.unlink()
        (self.project / '.agentic-state/routing/ledger.sqlite').unlink()
        after = qualification.inventory(self.project)
        delta = qualification.compare(before, after, {})['changes']
        for name, value in [('before', before), ('after', after), ('delta', delta)]:
            (self.work / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
        child = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/qualify_upgrade.py'), 'compare',
            '--project', str(self.project), '--before', str(self.work / 'before.json'),
            '--after', str(self.work / 'after.json'), '--allowed-delta', str(self.work / 'delta.json'),
            '--output', str(self.work / 'report.json')], capture_output=True, text=True, timeout=120)
        self.assertNotEqual(0, child.returncode)
        report = json.loads((self.work / 'report.json').read_bytes())
        self.assertFalse(report['preserved'])

    def test_public_derived_comparison_approves_release_and_rejects_ledger_loss(self):
        (self.project / 'PROJECT_INSTRUCTIONS.md').write_bytes(b'Owner instructions\n')
        seed_legacy_accounting(self.published, self.project)
        baseline = self.work / 'retained-baseline'
        shutil.copytree(self.project, baseline)
        before = qualification.inventory(baseline)
        (self.work / 'before.json').write_text(json.dumps(before), encoding='utf-8')
        install(self.candidate, self.project, self.pin, mode='upgrade', configure=True, discover=False,
                conflict='backup')
        command = [sys.executable, '-B', str(ROOT / 'scripts/qualify_upgrade.py'), 'compare',
            '--project', str(self.project), '--baseline-project', str(baseline), '--source', str(self.candidate),
            '--expected-manifest-sha256', self.pin, '--before', str(self.work / 'before.json'),
            '--after', str(self.work / 'after.json')]
        for label in ('clean', 'lost-ledger'):
            if label != 'clean':
                (self.project / '.agentic-state/routing/ledger.sqlite').unlink()
                (self.project / 'PROJECT_INSTRUCTIONS.md').unlink()
            after = qualification.inventory(self.project)
            (self.work / 'after.json').write_text(json.dumps(after), encoding='utf-8')
            output = self.work / (label + '.json')
            constraints = []
            if label != 'clean':
                delta = qualification.compare(before, after, {})['changes']
                (self.work / 'delta.json').write_text(json.dumps(delta), encoding='utf-8')
                constraints = ['--allowed-delta', str(self.work / 'delta.json')]
            child = subprocess.run([*command, *constraints, '--output', str(output)], capture_output=True, text=True, timeout=120)
            self.assertEqual(0 if label == 'clean' else 1, child.returncode, child.stderr)
            report = json.loads(output.read_bytes())
            if label == 'clean':
                self.assertTrue(report['preserved'], report['violations'])
                self.assertEqual({}, report['remaining_changes'])
                self.assertEqual(set(report['changes']), set(report['approved_delta']))
                self.assertTrue(qualification.exercised_ledger_migration(report))
            else:
                self.assertFalse(report['preserved'])
                self.assertEqual('FAIL', report['checks']['historical_state_continuity'])


if __name__ == '__main__':
    unittest.main()
