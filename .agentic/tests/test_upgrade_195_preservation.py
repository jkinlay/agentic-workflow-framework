"""Authentic published baseline; synthetic owner customization is not CMC evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import shutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import (materialize_published, verify_published, install_baseline,
                                  materialize_candidate, customize_preservation_project)
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import inventory, plan
import qualify_upgrade
import os
from agentic import ValidationError
from agentic.installer import install
import hashlib
import sqlite3
from unittest.mock import patch


@skip_unless_source_repo('requires published Git objects', 'scripts/publish_release.py', '.git')
class AuthenticBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_published(ROOT, cls.base / 'published')
        cls.candidate = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.candidate / 'MANIFEST.json').read_bytes()).hexdigest()

    def test_manifest_and_published_tag_identity(self):
        self.assertEqual('1.9.5', verify_published(self.source)['template_version'])
        with self.assertRaisesRegex(ValueError, 'published commit'):
            materialize_published(ROOT, self.base / 'wrong', commit='HEAD')
        self.assertFalse((self.base / 'wrong').exists())

    def test_corrupt_disposable_projection_is_rejected(self):
        # Corrupt only throwaway copies, never Git objects, the pinned baseline
        # or any installation receipt.
        for relative in ('MANIFEST.json', '.agentic/SPECIFICATION.md'):
            with self.subTest(relative=relative):
                copy = self.base / ('corrupt-' + Path(relative).name)
                shutil.copytree(self.source, copy)
                (copy / relative).write_bytes(b'corrupt disposable projection\n')
                with self.assertRaises((ValueError, AssertionError)):
                    verify_published(copy)
                self.assertEqual('1.9.5', verify_published(self.source)['template_version'])

    def test_newly_managed_owner_collision_cannot_be_approved_after_deletion(self):
        project = self.base / 'owner-managed-collision'
        install_baseline(self.source, project)
        relative = '.agentic/lib/agentic/lane_labels.py'
        owner = project / relative
        self.assertFalse(owner.exists())
        owner.write_bytes(b'Synthetic owner evidence at a future managed path.\n')
        before = inventory(project)
        try:
            policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        except ValueError as error:
            self.assertIn('owner collision', str(error))
            self.assertIn(relative, str(error))
            self.assertEqual(before, inventory(project))
            return
        # Simulate the faulty upgrade deleting owner evidence before installation.
        owner.unlink()
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(result['preserved'], result)

    def test_faulty_migration_cannot_approve_its_own_deleted_settlement(self):
        from upgrade_195_fixtures import seed_legacy_accounting
        from agentic import upgrade
        project = self.base / 'deleted-settlement'
        install_baseline(self.source, project)
        seed_legacy_accounting(self.source, project)
        ledger = project / '.agentic-state/routing/ledger.sqlite'

        def identities():
            connection = sqlite3.connect(':memory:')
            try:
                connection.deserialize(ledger.read_bytes())
                return connection.execute('SELECT run_id FROM model_runs ORDER BY run_id').fetchall()
            finally:
                connection.close()

        self.assertEqual([('synthetic-settled',), ('synthetic-unknown',)], identities())
        before = inventory(project)
        clean_project = self.base / 'clean-accounting'
        shutil.copytree(project, clean_project)
        clean_before = inventory(clean_project)
        clean_policy = qualify_upgrade.prepare_upgrade(self.candidate, clean_project, self.pin, clean_before)
        install(self.candidate, clean_project, self.pin, mode='upgrade', configure=True, discover=False)
        clean = qualify_upgrade.compare_upgrade(clean_before, inventory(clean_project), clean_policy, clean_project)
        self.assertTrue(clean['preserved'], clean['violations'])
        self.assertEqual('PASS', clean['checks']['historical_state_continuity'])
        original = upgrade._migrate_routing_ledger

        def lose_settlement(raw):
            connection = sqlite3.connect(':memory:')
            try:
                connection.deserialize(original(raw))
                connection.execute("DELETE FROM model_runs WHERE run_id='synthetic-settled'")
                connection.commit()
                return connection.serialize()
            finally:
                connection.close()

        with patch.object(upgrade, '_migrate_routing_ledger', side_effect=lose_settlement):
            policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
            install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual([('synthetic-unknown',)], identities())
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(result['preserved'], result)
        self.assertTrue(any('continuity' in item for item in result['violations']), result)

    @unittest.skipUnless(os.name == 'nt', 'Windows named-stream preservation regression')
    def test_owner_named_stream_deleted_across_upgrade_is_rejected(self):
        project = self.base / 'deleted-stream'
        install_baseline(self.source, project)
        customize_preservation_project(project)
        stream = Path(str(project / 'owner.txt') + ':owner-receipt')
        stream.write_bytes(b'Synthetic owner stream evidence.\n')
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        stream.unlink()
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        result = qualify_upgrade.compare_upgrade(before, inventory(project), policy, project)
        self.assertFalse(result['preserved'], result)
        self.assertIn('owner.txt', result['changes'])

    def test_derived_delta_accepts_only_the_clean_authentic_upgrade(self):
        project = self.base / 'preservation-upgrade'
        install_baseline(self.source, project)
        customize_preservation_project(project)
        before = inventory(project)
        policy = qualify_upgrade.prepare_upgrade(self.candidate, project, self.pin, before)
        install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        clean = inventory(project)
        result = qualify_upgrade.compare_upgrade(before, clean, policy, project)
        self.assertTrue(result['preserved'], result['violations'])
        self.assertEqual(set(result['changes']), set(result['approved_changes']))
        self.assertEqual('target-manifest', result['approved_changes']['AGENTS.md']['rule'])
        self.assertEqual('project-config-version-only',
                         result['approved_changes']['.agentic/PROJECT_CONFIG.yaml']['rule'])
        mutations = {
            'owner deleted': ('owner.txt', None),
            'owner changed': ('owner.txt', b'changed owner bytes\n'),
            'instructions changed': ('PROJECT_INSTRUCTIONS.md', b'changed instructions\n'),
            'managed mismatch': ('AGENTS.md', b'not target manifest bytes\n'),
            'unexpected file': ('unexpected.txt', b'extra\n'),
            'unexpected archive': ('.agentic-state/archive/upgrade-to-1.9.6/files/extra', b'extra\n'),
            'unexpected object': ('.agentic-install/objects/' + '0' * 64, b'not an installer object\n'),
            'prior archive': ('.agentic-state/archive/prior/receipt.json', b'changed prior archive\n'),
            'archive contents': ('.agentic-state/archive/upgrade-to-1.9.6/files/owner-note.txt', b'wrong copy\n'),
            'receipt': ('.agentic/installed-manifest.json', b'{}\n'),
            'provenance': ('.agentic/workflow-version.yaml', b'{}\n'),
            'codeowners': ('.github/CODEOWNERS', b'* @unexpected-owner\n'),
            'routing': ('.agentic/routing.yaml', b'{}\n'),
            'operating': ('OPERATING_CONFIG.yaml', b'{}\n'),
        }
        for label, (relative, raw) in mutations.items():
            with self.subTest(mutation=label):
                copy = self.base / ('mutation-' + label.replace(' ', '-'))
                shutil.copytree(project, copy)
                path = copy / relative
                if raw is None:
                    path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.chmod(0o600) if path.exists() else None
                    path.write_bytes(raw)
                rejected = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy)
                self.assertFalse(rejected['preserved'], label)
                self.assertTrue(any(relative in item for item in rejected['violations']), rejected)
                if label == 'owner changed':
                    # Even an externally supplied exact observed delta cannot waive ownership.
                    waived = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy,
                                                             allowed_delta=rejected['changes'])
                    self.assertFalse(waived['preserved'])
                    self.assertIn('external delta is not an approved migration: owner.txt', waived['violations'])
        copy = self.base / 'mutation-config'
        shutil.copytree(project, copy)
        config_path = copy / '.agentic/PROJECT_CONFIG.yaml'
        config = json.loads(config_path.read_bytes())
        config['execution']['max_parallel_tickets'] += 1
        config_path.write_text(json.dumps(config), encoding='utf-8')
        rejected = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy)
        self.assertFalse(rejected['preserved'])
        self.assertTrue(any('.agentic/PROJECT_CONFIG.yaml' in item for item in rejected['violations']))
        if os.name == 'nt':
            import subprocess
            copy = self.base / 'mutation-acl'
            shutil.copytree(project, copy)
            child = subprocess.run(['icacls', str(copy / 'owner.txt'), '/grant', '*S-1-1-0:(R)'],
                                   capture_output=True)
            self.assertEqual(0, child.returncode, 'Disposable ACL mutation failed')
            rejected = qualify_upgrade.compare_upgrade(before, inventory(copy), policy, copy)
            self.assertFalse(rejected['preserved'])
            self.assertIn('unapproved delta: owner.txt', rejected['violations'])
        self.assertEqual(clean, inventory(project))

    def test_published_installer_generates_receipt_for_main_master_trunk(self):
        for branch in ('main', 'master', 'trunk'):
            with self.subTest(branch=branch):
                project = self.base / branch
                result = install_baseline(self.source, project, branch=branch)
                self.assertEqual('INSTALLED', result['status'])
                receipt = json.loads((project / '.agentic/installed-manifest.json').read_bytes())
                self.assertEqual('1.9.5', receipt['template_version'])
                config = json.loads((project / '.agentic/PROJECT_CONFIG.yaml').read_bytes())
                self.assertEqual(branch, config['github']['base_branch'])
                self.assertEqual(101, config['github']['repository_id'])
                self.assertFalse(result['live_automation_enabled'])
                owner = project / 'PROJECT_INSTRUCTIONS.md'
                owner.write_bytes(b'synthetic owner customization; not CMC evidence\n')
                before = inventory(project)
                planned = plan(self.candidate, project, self.pin)
                self.assertTrue(planned['preservation']['preserved'])
                self.assertEqual(before, inventory(project))
                if branch != 'main':
                    continue
                for override in ({'repository': 'another-org/another-repo'},
                                 {'repository_id': 202}, {'base_branch': 'another-branch'}):
                    with self.subTest(override=override), self.assertRaises(ValidationError):
                        install(self.candidate, project, self.pin, mode='upgrade',
                                configure=True, discover=False, overrides=override, dry_run=True)
                    self.assertEqual(before, inventory(project))
                note = project / '.agentic-state/unknown-owner-note.txt'
                note.write_bytes(b'synthetic retained owner note\n')
                collision = project / '.agentic-state/archive/upgrade-to-1.9.6/files/unknown-owner-note.txt'
                collision.parent.mkdir(parents=True)
                collision.write_bytes(b'conflicting retained archive\n')
                before_conflict = inventory(project)
                with self.assertRaisesRegex(ValidationError, 'State archive path already exists'):
                    install(self.candidate, project, self.pin, mode='upgrade',
                            configure=True, discover=False)
                self.assertEqual(before_conflict, inventory(project))


if __name__ == '__main__':
    unittest.main()
