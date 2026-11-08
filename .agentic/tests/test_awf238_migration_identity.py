"""Every installed-project migration caller must supply authenticated identity."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests'), str(ROOT / 'scripts')]
from agentic import upgrade
from agentic.installer import CONFIG, INSTALLED, PROVENANCE, install, verify_installed
from agentic.safeio import Tree
from source_only import AWF_SOURCE_REPOSITORY, skip_unless_source_repo
if AWF_SOURCE_REPOSITORY:
    from qualify_upgrade import inventory, prepare_upgrade
from upgrade_fixtures import advance_one_fixture_step, materialize
from upgrade_195_fixtures import materialize_candidate, materialize_published, install_baseline


@skip_unless_source_repo('requires historical fixture and published Git objects',
                         '.agentic/tests/fixtures/upgrades/versions.json', '.git')
class InstalledMigrationIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.source / 'MANIFEST.json').read_bytes()).hexdigest()
        cls.table = upgrade.load_known_versions((ROOT / '.agentic/upgrade/known-versions.json').read_bytes())

    def authenticated_id(self, project):
        with Tree(project) as tree:
            return upgrade.identify_installation(tree, self.table,
                (project / CONFIG).read_bytes(), (project / INSTALLED).read_bytes())['install_id']

    def guard(self, function, expected, calls):
        def checked(*args, **kwargs):
            self.assertIn('install_id', kwargs,
                          'Installed-project migration omitted authenticated canonical install_id')
            self.assertEqual(expected, kwargs['install_id'])
            calls.append(kwargs['install_id'])
            return function(*args, **kwargs)
        return checked

    def receipt_guard(self, expected, calls):
        original = upgrade._target_receipt
        def checked(*args, **kwargs):
            self.assertEqual(4, len(args), 'Target receipt omitted authenticated canonical install_id')
            self.assertEqual(expected, args[3])
            raw = original(*args, **kwargs)
            self.assertEqual(expected, json.loads(raw)['install_id'])
            calls.append(expected)
            return raw
        return checked

    def test_installer_passes_authenticated_id_through_every_receipt_step(self):
        project = self.base / 'historical'
        materialize('1.9.3', project)
        expected = self.authenticated_id(project)
        chain_calls, receipt_calls = [], []
        with patch.object(upgrade, 'apply_chain', side_effect=self.guard(
                upgrade.apply_chain, expected, chain_calls)), patch.object(upgrade, '_target_receipt',
                side_effect=self.receipt_guard(expected, receipt_calls)):
            result = install(self.source, project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual([expected], chain_calls)
        self.assertEqual(len(result['upgrade']['steps']), len(receipt_calls))
        self.assertGreater(len(receipt_calls), 1)
        self.assertEqual(expected, json.loads((project / INSTALLED).read_bytes())['install_id'])
        self.assertEqual(expected, json.loads((project / PROVENANCE).read_bytes())['installation']['install_id'])
        self.assertEqual(self.pin, verify_installed(project))

    def test_fixture_writer_passes_authenticated_id_to_adjacent_step(self):
        project = self.base / 'adjacent'
        materialize('1.9.3', project)
        expected = self.authenticated_id(project)
        step_calls, receipt_calls = [], []
        with patch.object(upgrade, 'migrate_step', side_effect=self.guard(
                upgrade.migrate_step, expected, step_calls)), patch.object(upgrade, '_target_receipt',
                side_effect=self.receipt_guard(expected, receipt_calls)):
            self.assertEqual('1.9.4', advance_one_fixture_step(project, '1.9.3'))
        self.assertEqual([expected], step_calls)
        self.assertEqual([expected], receipt_calls)
        self.assertEqual(expected, self.authenticated_id(project))

    def test_qualification_planner_passes_authenticated_id_without_writes(self):
        published = materialize_published(ROOT, self.base / 'published')
        project = self.base / 'baseline'
        install_baseline(published, project)
        expected = self.authenticated_id(project)
        before = inventory(project)
        chain_calls, receipt_calls = [], []
        with patch.object(upgrade, 'apply_chain', side_effect=self.guard(
                upgrade.apply_chain, expected, chain_calls)), patch.object(upgrade, '_target_receipt',
                side_effect=self.receipt_guard(expected, receipt_calls)):
            prepare_upgrade(self.source, project, self.pin, before)
        self.assertEqual([expected], chain_calls)
        self.assertEqual([expected], receipt_calls)
        self.assertEqual(before, inventory(project))
