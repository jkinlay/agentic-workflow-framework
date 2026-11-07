"""Legacy diagnostics and owner boundaries on disposable published installs."""
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests')]
from agentic import owner_admission
from agentic.canonical import load_yaml
from agentic.installer import install
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import materialize_published, materialize_candidate, install_baseline


@skip_unless_source_repo('requires authentic published v1.9.5 Git objects', '.git')
class LegacyOwnerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
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
        self.owner_home = self.work / 'owner-home'
        self.addCleanup(patch.stopall)
        patch.object(owner_admission, '_owner_state_home', return_value=self.owner_home).start()

    def upgrade(self):
        return install(self.candidate, self.project, self.pin, mode='upgrade', configure=True, discover=False)

    def cli(self, *arguments):
        child = subprocess.run([sys.executable, '-B', '-I',
            str(self.project / '.agentic/scripts/workflow.py'), '--root', str(self.project),
            *arguments], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, child.returncode, child.stdout + child.stderr)
        return json.loads(child.stdout)

    def assert_legacy(self, report):
        self.assertEqual('ACCEPTED', report['status'])
        self.assertIsNone(report['budget_admission'])
        budget = report['budgeting']
        self.assertEqual('LEGACY_RETAINED', budget['disposition'])
        self.assertFalse(budget['settings_persisted'])
        self.assertEqual('PRESERVE_INSTALLED_POLICY', budget['diagnostic_fallback']['legacy_behavior'])
        self.assertEqual('UNOBSERVED', budget['legacy_enforcement'])
        self.assertEqual('EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED', budget['history_import'])
        self.assertTrue(budget['owner_adoption_required'])

    def test_show_reports_nonpersisted_fallback_and_required_adoption(self):
        self.upgrade()
        original = (self.project / 'OPERATING_CONFIG.yaml').read_bytes()
        report = self.cli('operating', 'show', '--json')
        self.assert_legacy(report)
        for text in ('LEGACY_RETAINED', 'non-persisted diagnostic fallback', 'UNOBSERVED',
                     'EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED', 'owner adoption'):
            self.assertIn(text, report['table'])
        self.assertEqual(original, (self.project / 'OPERATING_CONFIG.yaml').read_bytes())
        self.assertNotIn('budgeting', load_yaml(original))

    def test_status_projects_the_same_legacy_distinction(self):
        self.upgrade()
        projected = self.cli('validate-config')
        self.assertEqual('ACCEPTED', projected['status'], projected)
        self.assert_legacy(projected['operating'])
        status = self.cli('status', '--json')
        self.assert_legacy(status['operating'])
        self.assertNotEqual('ACTIVE', status['project_state'])

    def test_gitless_upgrade_never_checks_git_gh_or_writes_owner_records(self):
        self.assertFalse((self.project / '.git').exists())
        original = (self.project / 'OPERATING_CONFIG.yaml').read_bytes()
        with contextlib.ExitStack() as stack:
            for name in ('git_common_identity', '_git_executable', '_git_metadata_identity',
                         '_gh_executable', '_github_executable', 'propose', 'confirm'):
                if hasattr(owner_admission, name):
                    stack.enter_context(patch.object(owner_admission, name,
                        side_effect=AssertionError('upgrade reached owner check: ' + name)))
            for name in ('save', 'consume', 'transaction'):
                stack.enter_context(patch.object(owner_admission.ProposalStore, name,
                    side_effect=AssertionError('upgrade wrote owner record: ' + name)))
            plan = install(self.candidate, self.project, self.pin, mode='upgrade',
                           configure=True, discover=False, dry_run=True)
            self.assertFalse(plan['installed'])
            result = self.upgrade()
        self.assertEqual('UPGRADED', result['status'])
        self.assertIsNone(result['operating']['budget_admission'])
        self.assertEqual(original, (self.project / 'OPERATING_CONFIG.yaml').read_bytes())
        self.assertFalse(self.owner_home.exists())
        self.assertFalse((self.project / '.git').exists())


if __name__ == '__main__':
    unittest.main()
