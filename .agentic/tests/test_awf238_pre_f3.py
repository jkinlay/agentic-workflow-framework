"""Pre-F3 regressions through published APIs and disposable upgrade targets."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
sys.path.insert(0, str(ROOT / 'scripts'))
from agentic import ValidationError
from agentic.installer import install, recover, verify_installed
from agentic.budgeting import ControllerBudgeting, fresh_settings
from agentic.model_routing import RoutingLedger, policy_from_config, select_route
from agentic.operating import default_operating, validate_operating
from source_only import skip_unless_source_repo
from upgrade_195_fixtures import (materialize_published, materialize_candidate,
    install_baseline, published_call)
from test_operating import governance
from test_awf242_controller_wiring import ObservedBudgetHost
from settings_pr_fixtures import accepted_fixture_snapshot
from test_model_routing import capabilities
from qualify_upgrade import _permissions, _named_streams


@unittest.skipUnless(os.name == 'nt', 'Windows inherited security regression')
class WindowsInheritedMetadataTests(unittest.TestCase):
    def test_replacement_retains_inherited_acl_outside_private_temp_parent(self):
        from agentic.safeio import Tree
        parent = Path(tempfile.gettempdir()).resolve()
        root = parent / ('awf238-inherited-' + uuid.uuid4().hex)
        root.mkdir()

        def cleanup():
            self.assertEqual(parent, root.resolve().parent)
            self.assertTrue(root.name.startswith('awf238-inherited-'))
            shutil.rmtree(root)
        self.addCleanup(cleanup)
        target = root / 'config'
        target.write_bytes(b'original')
        Path(str(target) + ':receipt').write_bytes(b'owner receipt')
        permissions, streams = _permissions(target), _named_streams(target)
        self.assertTrue(permissions['verified'])
        self.assertTrue(streams['verified'])
        with Tree(root) as tree:
            tree.windows_metadata('config', preflight=True)
            tree.write('config', b'replacement')
        self.assertEqual(b'replacement', target.read_bytes())
        self.assertEqual(permissions, _permissions(target))
        self.assertEqual(streams, _named_streams(target))


@skip_unless_source_repo('requires authentic published v1.9.5 Git objects', '.git')
class PreF3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = materialize_published(ROOT, cls.base / 'published')
        cls.candidate = materialize_candidate(ROOT, cls.base / 'candidate')
        cls.pin = hashlib.sha256((cls.candidate / 'MANIFEST.json').read_bytes()).hexdigest()
        cls.baseline = cls.base / 'baseline'
        install_baseline(cls.source, cls.baseline)

    def setUp(self):
        self.work = self.base / self._testMethodName
        self.work.mkdir()

    def legacy_store(self, path, project):
        published_call(self.source, """
from agentic.store import Store
store = Store(Path(sys.argv[3]), sys.argv[4])
store.control('RUNNING', 'Disposable regression history', reconciled=True)
store.reserve_budget('22222222-2222-4222-8222-222222222222', 'EX-195',
    '2026-10-01T00:00:00Z', 100, 10,
    dict(runs=20, tokens=10000, cost=10000, daily_cost=10000))
store.control('PAUSED', 'Disposable regression complete')
print('{}')
""", path, project)

    def budget_case(self, *, same, controller, enabled=True, imported=False):
        from agentic.store import Store
        from agentic.upgrade import import_budget_history
        gov = governance()
        path = self.work / 'canonical.sqlite'
        legacy = path if same else self.work / 'legacy.sqlite'
        self.legacy_store(legacy, gov['project']['id'])
        if imported:
            import_budget_history(path, {'state': {'path': legacy,
                'sha256': hashlib.sha256(legacy.read_bytes()).hexdigest()}},
                project_id=gov['project']['id'])
        value = default_operating(gov['execution']['model_routing'])
        value['budgeting'] = dict(fresh_settings(), accounting_enabled=enabled,
            enforcement_enabled=enabled, max_tokens_per_ticket=100, max_tokens_per_project=100)
        snapshot = accepted_fixture_snapshot(self, self.work / 'worker', value, gov)
        host = ObservedBudgetHost()
        run = str(uuid.uuid4())
        binding = dict(project_id=gov['project']['id'], ticket_id='EX-NEW', run_id=run,
            dispatch_id='dispatch-' + run, session_id='session-' + run, role='worker')
        observed = host.observe_budget_admission(binding, None)
        selected = select_route(policy_from_config(gov), observed['request'], capabilities(),
            operating=snapshot, governance=gov)
        if controller:
            # The real controller must bind its existing Store at configuration.
            options = {} if same else {'legacy_store': Store(legacy, gov['project']['id'])}
            context = ControllerBudgeting(path if enabled else None, gov, snapshot, host, **options)
            return context.admit(binding, selected)
        from agentic.budget_enrollment import EnrollmentRegistry
        registry = EnrollmentRegistry(self.work / 'enrollment.sqlite', host)
        registry.enroll(binding)
        options = {} if same else {'legacy_store': Store(legacy, gov['project']['id'])}
        return RoutingLedger(path).reserve(gov['project']['id'], policy_from_config(gov),
            observed['request'], capabilities(), operating=snapshot, governance=gov,
            budget_registry=registry, budget_binding=binding, **options)

    def test_controller_same_database_requires_legacy_import(self):
        with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
            self.budget_case(same=True, controller=True)

    def test_controller_separate_store_requires_legacy_import(self):
        with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
            self.budget_case(same=False, controller=True)

    def test_routing_same_database_requires_legacy_import(self):
        with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
            self.budget_case(same=True, controller=False)

    def test_routing_separate_store_requires_legacy_import(self):
        with self.assertRaisesRegex(ValidationError, 'legacy.*import|import.*legacy'):
            self.budget_case(same=False, controller=False)

    def test_controller_imported_history_enforces_lifetime_ceiling(self):
        with self.assertRaisesRegex(ValidationError, 'Lifetime project token budget exhausted'):
            self.budget_case(same=False, controller=True, imported=True)

    def test_off_requires_no_ledger_or_import(self):
        self.assertIsNone(self.budget_case(same=False, controller=True, enabled=False))
        self.assertFalse((self.work / 'canonical.sqlite').exists())

    def metadata_upgrade(self, fail_after=None):
        project = self.work / 'project'
        shutil.copytree(self.baseline, project)
        target = project / '.agentic/PROJECT_CONFIG.yaml'
        old = target.read_bytes()
        stream = Path(str(target) + ':owner-receipt')
        stream.write_bytes(b'Disposable owner metadata receipt\n')
        child = subprocess.run(['icacls', str(target), '/grant', '*S-1-1-0:(R)'],
            capture_output=True)
        self.assertEqual(0, child.returncode, 'Disposable ACL setup failed')
        permissions, streams = _permissions(target), _named_streams(target)
        self.assertTrue(permissions['verified'])
        self.assertTrue(streams['verified'])
        self.assertTrue(streams['streams'])
        if fail_after is None:
            result = install(self.candidate, project, self.pin, mode='upgrade',
                configure=True, discover=False)
            self.assertEqual('UPGRADED', result['status'])
            self.assertEqual(self.pin, verify_installed(project))
        else:
            with self.assertRaisesRegex(OSError, 'Injected installation failure'):
                install(self.candidate, project, self.pin, mode='upgrade',
                    configure=True, discover=False, fail_after=fail_after)
            self.assertEqual(old, target.read_bytes())
            self.assertEqual('NO_PENDING_INSTALL', recover(project)['status'])
        self.assertEqual(permissions, _permissions(target))
        self.assertEqual(streams, _named_streams(target))
        self.assertEqual(b'Disposable owner metadata receipt\n', stream.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows metadata regression')
    def test_upgrade_preserves_configuration_acl_and_ads(self):
        self.metadata_upgrade()

    @unittest.skipUnless(os.name == 'nt', 'Windows metadata regression')
    def test_rollback_preserves_configuration_acl_and_ads(self):
        self.metadata_upgrade(fail_after=100)

    @unittest.skipUnless(os.name == 'nt', 'Windows metadata regression')
    def test_unpreservable_metadata_refuses_before_target_writes(self):
        from agentic.safeio import WINDOWS_METADATA_MAX_BYTES
        from qualify_upgrade import inventory
        project = self.work / 'project'
        shutil.copytree(self.baseline, project)
        # Remove only the disposable installer's released coordination lock so
        # this test detects even a new lock write before metadata refusal.
        (project / '.agentic-install/lock').unlink()
        target = project / '.agentic/PROJECT_CONFIG.yaml'
        Path(str(target) + ':large-receipt').write_bytes(b'x' * (WINDOWS_METADATA_MAX_BYTES + 1))
        before = inventory(project)
        with self.assertRaisesRegex(ValidationError, 'metadata exceeds preservation cap'):
            install(self.candidate, project, self.pin, mode='upgrade', configure=True, discover=False)
        self.assertEqual(before, inventory(project))
        self.assertFalse((project / '.agentic-install/lock').exists())


if __name__ == '__main__':
    unittest.main()
