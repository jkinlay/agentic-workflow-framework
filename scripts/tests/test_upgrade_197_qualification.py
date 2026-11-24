"""AWF-310: authentic baselines and explicitly constructed future targets.

These development checks do not qualify a release or an installed runtime.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from agentic import VERSION, ValidationError, upgrade
from agentic.child_process import child_env
from agentic.safeio import run_bounded
import qualify_upgrade

STAGED = '.agentic/upgrade/known-versions-1.9.7.json'


class Upgrade197PolicyTests(unittest.TestCase):
    def test_explicit_null_default_changes_real_policy_digest(self):
        from copy import deepcopy
        from agentic.canonical import load_yaml
        from agentic.lifecycle import definition
        from agentic.policy import policy_hash
        config = load_yaml((ROOT / '.agentic/defaults/PROJECT_CONFIG.yaml').read_bytes())
        key = 'shared_owner_automation_actor_id'
        self.assertIsNone(config['merge_gate'][key])
        absent = deepcopy(config)
        del absent['merge_gate'][key]
        self.assertNotEqual(policy_hash(config, definition()), policy_hash(absent, definition()))

    def test_staged_registry_preserves_active_release_and_authenticates_tag(self):
        import upgrade_release
        current = json.loads((ROOT / '.agentic/upgrade/known-versions.json').read_bytes())
        staged = json.loads((ROOT / STAGED).read_bytes())
        self.assertEqual(VERSION, current['target'])
        self.assertEqual('1.9.7', staged['target'])
        self.assertEqual(current['versions'], staged['versions'][:len(current['versions'])])
        entry = staged['versions'][-1]
        self.assertEqual('1.9.6', entry['version'])
        self.assertEqual({'kind': 'version-only', 'to': '1.9.7', 'new_required_settings': []}, entry['migration'])
        base = Path(tempfile.mkdtemp(prefix='awf-310-authentication-'))
        release = upgrade_release.materialize_registered(ROOT, staged, '1.9.6', base / 'release')
        derived = upgrade_release.release_entry(ROOT, 'v1.9.6', entry['provenance']['commit'],
                                               '1.9.7', release=release)
        self.assertEqual(entry, derived)
        upgrade.load_known_versions((ROOT / STAGED).read_bytes())

    def test_budget_milestone_is_crossed_once(self):
        for previous, target, expected in (('1.9.5', '1.9.6', True), ('1.9.5', '1.9.7', True),
                ('1.8.3', '1.9.7', True), ('1.9.6', '1.9.7', False),
                ('1.9.7', '1.9.7', False), ('1.9.4', '1.9.5', False)):
            with self.subTest(previous=previous, target=target):
                self.assertEqual(expected, upgrade.crosses_budget_milestone(previous, target))
        for previous, target in (('unknown', '1.9.7'), ('1.9.7', '1.9.6')):
            with self.assertRaises(ValidationError):
                upgrade.crosses_budget_milestone(previous, target)

    def test_qualification_routes_are_closed(self):
        for route in (('1.9.5', '1.9.6'), ('1.9.5', '1.9.7'), ('1.9.6', '1.9.7')):
            qualify_upgrade.require_qualification_route(*route)
        for route in (('1.9.6', '1.9.6'), ('1.9.4', '1.9.7'), ('1.9.5', '1.9.8')):
            with self.assertRaises(ValueError):
                qualify_upgrade.require_qualification_route(*route)

    def test_installed_cli_selects_authentic_baseline(self):
        from unittest.mock import patch
        with patch.object(qualify_upgrade, 'qualify', return_value={'status': 'NOT_QUALIFIED'}) as run, \
                patch.object(qualify_upgrade, 'preflight_report'), patch.object(qualify_upgrade, 'publish_report'):
            self.assertEqual(1, qualify_upgrade.main(['installed', '--source', '.', '--work-dir', 'cohort',
                '--baseline-version', '1.9.6', '--output', 'report.json']))
        self.assertEqual('1.9.6', run.call_args.kwargs['baseline_version'])


class Upgrade197ConstructedTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import upgrade_release
        from upgrade_195_fixtures import materialize_candidate
        import build_release
        from agentic.safeio import Tree
        cls.base = Path(tempfile.mkdtemp(prefix='awf-310-constructed-'))
        cls.target = materialize_candidate(ROOT, cls.base / 'constructed-target')
        # This is deliberately a development target, not fabricated release evidence.
        version_paths = {'VERSION', '.agentic/lib/agentic/__init__.py',
                '.agentic/defaults/PROJECT_CONFIG.yaml', '.agentic/PROJECT_CONFIG.yaml',
                '.agentic/workflow-version.yaml', *build_release.SOURCE_CONFIGS.values()}
        for relative in version_paths:
            path = cls.target / relative
            path.write_bytes(path.read_bytes().replace(VERSION.encode(), b'1.9.7'))
        (cls.target / '.agentic/upgrade/known-versions.json').write_bytes((ROOT / STAGED).read_bytes())
        with Tree(cls.target) as tree:
            files = {p: hashlib.sha256(build_release.release_bytes(tree, p)).hexdigest()
                     for p in build_release.release_paths(tree) if p not in {'MANIFEST.json', 'MANIFEST.md'}}
        machine, advisory = build_release.render_manifests(files, version='1.9.7')
        build_release.write_manifest_files(cls.target, machine, advisory)
        cls.pin = hashlib.sha256(machine).hexdigest()
        registry = json.loads((ROOT / STAGED).read_bytes())
        cls.releases = {}
        for version in ('1.9.5', '1.9.6'):
            cls.releases[version] = upgrade_release.materialize_registered(
                ROOT, registry, version, cls.base / ('published-' + version))

    def child(self, source, code, *arguments):
        prelude = ('import sys, json\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n'
                   "sys.path.insert(0, str(Path(sys.argv[1]).parents[1] / 'scripts'))\n")
        result = run_bounded([sys.executable, '-B', '-c', prelude + code,
            str(source / '.agentic/lib'), *map(str, arguments)], cwd=self.base, env=child_env(),
            timeout=120, stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors='replace'))
        return json.loads(result.stdout)

    def baseline(self, version, label):
        source = self.releases[version]
        project = self.base / label
        pin = hashlib.sha256((source / 'MANIFEST.json').read_bytes()).hexdigest()
        self.child(source, '''from agentic.installer import install
print(json.dumps(install(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], configure=True,
    discover=False, codeowner='@fixture-owner', overrides={'repository': 'example-org/example-repo',
    'repository_id': 101, 'base_branch': 'main', 'test_command': 'python -m unittest'})))
''', source, project, pin)
        return project

    def test_tampered_and_unknown_196_are_refused(self):
        from agentic.safeio import Tree
        table = upgrade.load_known_versions((ROOT / STAGED).read_bytes())
        project = self.baseline('1.9.6', 'tampered-project')
        config = (project / upgrade.CONFIG).read_bytes()
        receipt_raw = (project / upgrade.INSTALLED).read_bytes()
        with Tree(project) as tree:
            self.assertEqual('1.9.6', upgrade.identify_installation(tree, table, config, receipt_raw)['version'])
            receipt = json.loads(receipt_raw)
            receipt['source_manifest_sha256'] = hashlib.sha256(b'unknown development distribution').hexdigest()
            with self.assertRaisesRegex(ValidationError, 'not registered'):
                upgrade.identify_installation(tree, table, config, json.dumps(receipt).encode())
        managed = project / '.agentic/lib/agentic/__init__.py'
        managed.write_bytes(managed.read_bytes() + b'\n# deliberate tamper regression\n')
        with Tree(project) as tree, self.assertRaisesRegex(ValidationError, 'Locally modified'):
            upgrade.identify_installation(tree, table, config, receipt_raw)

    def test_both_routes_receipts_disposition_and_defaults(self):
        for version in ('1.9.5', '1.9.6'):
            with self.subTest(version=version):
                project = self.baseline(version, 'upgrade-' + version)
                old_id = json.loads((project / upgrade.INSTALLED).read_bytes())['install_id']
                result = self.child(self.target, '''from agentic.installer import install, verify_installed
project = Path(sys.argv[3])
result = install(Path(sys.argv[2]), project, sys.argv[4], mode='upgrade', configure=True,
    discover=False, conflict='backup')
result['verified_pin'] = verify_installed(project)
print(json.dumps(result))
''', self.target, project, self.pin)
                report = result['upgrade']
                expected = [('1.9.6', '1.9.7')]
                if version == '1.9.5':
                    expected.insert(0, ('1.9.5', '1.9.6'))
                self.assertEqual(expected, [(s['from'], s['to']) for s in report['steps']])
                self.assertTrue(report['single_reviewed_change_set'])
                self.assertEqual(self.pin, result['verified_pin'])
                receipt = json.loads((project / upgrade.INSTALLED).read_bytes())
                self.assertEqual('1.9.7', receipt['template_version'])
                self.assertEqual(old_id, receipt['install_id'])
                config = json.loads((project / upgrade.CONFIG).read_bytes())
                self.assertEqual('1.9.7', config['template']['expected_workflow_version'])
                self.assertIsNone(config['merge_gate'].get('shared_owner_automation_actor_id'))
                self.assertEqual((self.target / '.agentic/defaults/PROJECT_CONFIG.yaml').read_bytes(),
                                 (project / '.agentic/defaults/PROJECT_CONFIG.yaml').read_bytes())
                disposition = project / upgrade.BUDGET_DISPOSITION
                self.assertEqual(version == '1.9.5', disposition.exists())
                if disposition.exists():
                    self.assertEqual('1.9.6', json.loads(disposition.read_bytes())['target_version'])
                    original = disposition.read_bytes()
                    self.child(self.target, '''from agentic.installer import install
print(json.dumps(install(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], mode='upgrade',
    configure=True, discover=False, conflict='backup')))
''', self.target, project, self.pin)
                    self.assertEqual(original, disposition.read_bytes())
                    self.assertEqual([disposition], list(disposition.parent.glob('budgeting-*.json')))

    def test_preservation_policy_for_both_routes(self):
        for version in ('1.9.5', '1.9.6'):
            with self.subTest(version=version):
                project = self.baseline(version, 'policy-' + version)
                result = self.child(self.target, '''import qualify_upgrade
project = Path(sys.argv[2])
policy = qualify_upgrade.prepare_upgrade(Path(sys.argv[3]), project, sys.argv[4],
    qualify_upgrade.inventory(project))
print(json.dumps({'source': policy['source_version'], 'target': policy['target_version'],
    'has_disposition': '.agentic-state/upgrade/budgeting-1.9.6.json' in policy['files']}))
''', project, self.target, self.pin)
                self.assertEqual(version, result['source'])
                self.assertEqual('1.9.7', result['target'])
                self.assertEqual(version == '1.9.5', result['has_disposition'])

    def test_existing_196_disposition_is_preserved_without_recreation(self):
        project = self.baseline('1.9.5', 'sequential-project')
        release = self.releases['1.9.6']
        pin = hashlib.sha256((release / 'MANIFEST.json').read_bytes()).hexdigest()
        code = '''from agentic.installer import install
print(json.dumps(install(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], mode='upgrade',
    configure=True, discover=False, conflict='backup')))
'''
        self.child(release, code, release, project, pin)
        disposition = project / upgrade.BUDGET_DISPOSITION
        original = disposition.read_bytes()
        before_stat = disposition.stat()
        result = self.child(self.target, code, self.target, project, self.pin)
        self.assertNotIn('budgeting', result['upgrade'])
        self.assertEqual(original, disposition.read_bytes())
        self.assertEqual(before_stat.st_mtime_ns, disposition.stat().st_mtime_ns)
        self.assertEqual(before_stat.st_ino, disposition.stat().st_ino)

    def test_196_qualification_history_uses_real_installed_api(self):
        project = self.baseline('1.9.6', 'history-project')
        result = self.child(self.target, '''import qualify_upgrade
from agentic.safeio import run_bounded
from agentic.child_process import child_env
project = Path(sys.argv[2])
def execute(stage, arguments):
    # Manifest-only development baseline: exercise the same isolated code with
    # this interpreter; this is explicitly not an installed-runtime receipt.
    arguments[0] = sys.executable
    return run_bounded(arguments, cwd=project, env=child_env(), timeout=120,
        stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
child = qualify_upgrade._seed_current_qualification_state(project, execute)
if child.returncode:
    raise AssertionError(child.stderr.decode(errors='replace'))
print(json.dumps(qualify_upgrade._historical_state({
    '.agentic-state/routing/ledger.sqlite': (project / '.agentic-state/routing/ledger.sqlite').read_bytes()})))
''', project)
        tables = result['.agentic-state/routing/ledger.sqlite']['tables']
        self.assertTrue(any(record['rows'] for record in tables.values()))


if __name__ == '__main__':
    unittest.main()
