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
from upgrade_195_fixtures import materialize_published, verify_published, install_baseline, materialize_candidate
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_upgrade import inventory, plan
from agentic import ValidationError
from agentic.installer import install
import hashlib


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
