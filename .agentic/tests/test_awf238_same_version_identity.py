"""Same-release identity admission uses authentic disposable candidate installs."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.agentic/lib'), str(ROOT / '.agentic/tests'), str(ROOT / 'scripts')]
from agentic import ValidationError
from agentic import installer
from agentic.canonical import load_yaml
from agentic.installer import CONFIG, INSTALLED, PROVENANCE, install, verify_installed
from agentic.safeio import Tree
from agentic.store import Store
from source_only import skip_unless_source_repo
from agentic.preservation_metadata import inventory, _permissions, _named_streams
from upgrade_195_fixtures import materialize_candidate


def complete_inventory(root):
    """Include every entry and supported metadata, excluding read access times."""
    result = {}
    for path in [root, *root.rglob('*')]:
        info = path.lstat()
        result[path.relative_to(root).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            info.st_mode, info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, 'st_birthtime_ns', None), getattr(info, 'st_file_attributes', None),
            getattr(info, 'st_reparse_tag', None), _permissions(path),
            _named_streams(path))
    return result


@skip_unless_source_repo('requires source candidate builder', 'scripts/build_release.py')
class SameVersionIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.source / 'MANIFEST.json').read_bytes()).hexdigest()
        cls.a, cls.b = cls.base / 'a', cls.base / 'b'
        for root in (cls.a, cls.b):
            install(cls.source, root, cls.pin, configure=True, discover=False,
                    codeowner='@fixture-owner', overrides={
                        'repository': 'example-org/example-repo', 'repository_id': 101,
                        'base_branch': 'main', 'test_command': 'python -m unittest'})
        cls.receipt_id = json.loads((cls.a / INSTALLED).read_bytes())['install_id']
        assert cls.receipt_id != json.loads((cls.b / INSTALLED).read_bytes())['install_id']
        for relative, raw in {
            'PROJECT_INSTRUCTIONS.md': b'Synthetic owner instructions.\n',
            'owner.txt': b'Synthetic owner bytes.\n',
            '.agentic-state/owner-accounting.json': b'{"synthetic_unknown_usage":null}\n',
        }.items():
            target = cls.a / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        project_id = load_yaml((cls.a / CONFIG).read_bytes())['project']['id']
        store = Store(cls.a / '.agentic-state/state.sqlite', project_id)
        store.control('RUNNING', 'Synthetic disposable accounting', reconciled=True)
        store.reserve_budget(str(uuid.uuid4()), 'EX-238', '2026-11-01T00:00:00Z',
                             100, 10, dict(runs=10, tokens=1000, cost=1000, daily_cost=1000))
        store.control('PAUSED', 'Synthetic fixture complete')

    def project(self):
        project = self.base / uuid.uuid4().hex
        shutil.copytree(self.a, project)
        return project

    def upgrade(self, project, **kwargs):
        return install(self.source, project, self.pin, mode='upgrade', configure=True,
                       discover=False, **kwargs)

    def assert_refused_unchanged(self, project, action):
        before, complete = inventory(project), complete_inventory(project)
        receipt, provenance = (project / INSTALLED).read_bytes(), (project / PROVENANCE).read_bytes()
        with self.assertRaisesRegex(ValidationError, 'install_id.*(differ|match).*no files changed'):
            action()
        self.assertEqual(before, inventory(project))
        self.assertEqual(complete, complete_inventory(project))
        self.assertEqual(receipt, (project / INSTALLED).read_bytes())
        self.assertEqual(provenance, (project / PROVENANCE).read_bytes())

    def test_copied_provenance_refuses_all_upgrade_modes_without_any_write(self):
        for dry_run in (True, False):
            for conflict in ('error', 'backup'):
                with self.subTest(dry_run=dry_run, conflict=conflict):
                    project = self.project()
                    (project / PROVENANCE).write_bytes((self.b / PROVENANCE).read_bytes())
                    self.assert_refused_unchanged(project, lambda: self.upgrade(
                        project, dry_run=dry_run, conflict=conflict))

    def test_direct_verifier_rejects_copied_provenance(self):
        project = self.project()
        (project / PROVENANCE).write_bytes((self.b / PROVENANCE).read_bytes())
        self.assert_refused_unchanged(project, lambda: verify_installed(project))

    def test_missing_and_empty_ids_refuse_verification_plan_and_apply(self):
        for relative in (INSTALLED, PROVENANCE):
            for missing in (True, False):
                for action in ('verify', 'plan', 'apply'):
                    with self.subTest(path=relative, missing=missing, action=action):
                        project = self.project()
                        value = json.loads((project / relative).read_bytes())
                        section = value if relative == INSTALLED else value['installation']
                        if missing:
                            del section['install_id']
                        else:
                            section['install_id'] = ''
                        (project / relative).write_bytes((json.dumps(value) + '\n').encode())
                        self.assert_refused_unchanged(project,
                            lambda: verify_installed(project) if action == 'verify' else
                            self.upgrade(project, dry_run=action == 'plan'))

    def test_legitimate_reinstall_preserves_identity_and_owner_state(self):
        preserved = (CONFIG, 'OPERATING_CONFIG.yaml', 'PROJECT_INSTRUCTIONS.md',
                     '.github/CODEOWNERS', 'owner.txt', '.agentic-state/owner-accounting.json',
                     '.agentic-state/state.sqlite')
        for dry_run in (True, False):
            for conflict in ('error', 'backup'):
                with self.subTest(dry_run=dry_run, conflict=conflict):
                    project = self.project()
                    before, complete = inventory(project), complete_inventory(project)
                    owner = {path: (project / path).read_bytes() for path in preserved}
                    result = self.upgrade(project, dry_run=dry_run, conflict=conflict)
                    self.assertEqual('PLAN' if dry_run else 'UPGRADED', result['status'])
                    if dry_run:
                        self.assertEqual(before, inventory(project))
                        self.assertEqual(complete, complete_inventory(project))
                    self.assertEqual(owner, {path: (project / path).read_bytes() for path in preserved})
                    self.assertEqual(self.receipt_id, json.loads((project / INSTALLED).read_bytes())['install_id'])
                    self.assertEqual(self.receipt_id, json.loads((project / PROVENANCE).read_bytes())['installation']['install_id'])
                    self.assertEqual(self.pin, verify_installed(project))

    def test_provenance_drift_after_verification_refuses_before_installer_writes(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                project = self.project()
                original = installer._verify_installed
                contaminated = {}
                def substitute(root, version):
                    result = original(root, version)
                    (project / PROVENANCE).write_bytes((self.b / PROVENANCE).read_bytes())
                    contaminated.update(inventory=inventory(project), complete=complete_inventory(project))
                    return result
                with patch.object(installer, '_verify_installed', side_effect=substitute):
                    with self.assertRaisesRegex(ValidationError, 'Destination changed.*workflow-version.yaml'):
                        self.upgrade(project, dry_run=dry_run, conflict='backup')
                self.assertEqual(contaminated['inventory'], inventory(project))
                self.assertEqual(contaminated['complete'], complete_inventory(project))

    def test_recovered_clean_endpoint_still_rejects_copied_identity(self):
        class Interrupted(BaseException):
            pass
        project = self.project()
        original = Tree.write
        def interrupt(tree, relative, data, **kwargs):
            result = original(tree, relative, data, **kwargs)
            if relative == INSTALLED:
                raise Interrupted()
            return result
        with patch.object(Tree, 'write', new=interrupt), self.assertRaises(Interrupted):
            self.upgrade(project)
        with patch.object(installer, '_verify_installed', side_effect=AssertionError('identity checked before recovery')):
            with self.assertRaisesRegex(ValidationError, 'requires --recover'):
                self.upgrade(project)
        installer.recover(project)
        self.assertEqual(self.pin, verify_installed(project))
        (project / PROVENANCE).write_bytes((self.b / PROVENANCE).read_bytes())
        self.assert_refused_unchanged(project, lambda: self.upgrade(project, conflict='backup'))
