"""Fresh provisioning is explicit; compatibility never infers a project's age."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

import source_only  # noqa: F401
from agentic import ValidationError, budgeting, operating as op, upgrade
from agentic.canonical import load_yaml, sha256
from agentic.contracts import Contracts
from agentic.operating_status import with_operating
from agentic.protected_settings import resolve_protected
from agentic.runtime_commands import command_catalog, render_doctor

ROOT = Path(__file__).resolve().parents[2]
NEW = dict(accounting_enabled=True, enforcement_enabled=True, enforcement_mode='pre_run',
           max_tokens_per_ticket=6_000_000_000, max_tokens_per_project=600_000_000_000)
OFF = dict(accounting_enabled=False, enforcement_enabled=False,
           max_tokens_per_ticket=4_000_000, max_tokens_per_project=1_000_000_000)


class NewProjectDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('awf315-defaults-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.gov = load_yaml((ROOT / '.agentic/defaults/PROJECT_CONFIG.yaml').read_bytes())
        self.value = load_yaml((ROOT / '.agentic/defaults/NEW_PROJECT_OPERATING_CONFIG.yaml').read_bytes())
        patcher = mock.patch.dict(os.environ, {'AWF_STATE_HOME': str(self.root / 'state')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_plan(self, value=None):
        plan = op.plan_initialize_operating(self.root, self.gov, initial_operating=value)
        for name, raw in plan['files'].items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        config = self.root / '.agentic/PROJECT_CONFIG.yaml'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(self.gov), encoding='utf-8')
        return plan

    def test_provisioning_templates_and_example_explicitly_enable_new_limits(self):
        for name in ('.agentic/defaults/NEW_PROJECT_OPERATING_CONFIG.yaml',
                     '.agentic/examples/NEW_PROJECT_OPERATING_CONFIG.yaml'):
            with self.subTest(path=name):
                self.assertEqual(NEW, load_yaml((ROOT / name).read_bytes())['budgeting'])
        self.assertEqual(OFF, load_yaml((ROOT / '.agentic/defaults/OPERATING_CONFIG.yaml').read_bytes())['budgeting'])

    def test_fresh_install_copies_shipped_defaults_into_reviewed_configuration(self):
        from test_bootstrap_configuration import ConfiguredInstallerTests
        from agentic import installer
        fixture = ConfiguredInstallerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Use the real install path with a bounded source fixture, including the
        # shipped governance and defaults rather than ambient operating choices.
        fixture.files[installer.CONFIG] = (ROOT / '.agentic/defaults/PROJECT_CONFIG.yaml').read_bytes()
        fixture.files['.agentic/routing.yaml'] = (ROOT / '.agentic/defaults/routing.yaml').read_bytes()
        for name in ('.agentic/defaults/OPERATING_CONFIG.yaml',
                     '.agentic/defaults/NEW_PROJECT_OPERATING_CONFIG.yaml'):
            fixture.files[name] = (ROOT / name).read_bytes()
        for name, raw in fixture.files.items():
            path = fixture.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        manifest = installer.json_bytes(dict(format='awf-manifest-1',
            template_version=installer.VERSION,
            files={name: sha256(raw) for name, raw in fixture.files.items()}))
        (fixture.source / 'MANIFEST.json').write_bytes(manifest)
        fixture.pin = sha256(manifest)
        wheelhouse = fixture.prepare_runtime_source()
        from agentic import adoption_config
        installation = fixture.perform()
        adoption_config.ensure_installed_runtime(fixture.dest, wheelhouse)
        installed = adoption_config.post_install_checks(fixture.dest, installation)
        self.assertEqual('CONFIGURED', installed['status'], installed)
        checks = installed['post_install_checks']
        self.assertEqual([0, 0], [item['exit_code'] for item in checks])
        self.assertTrue(checks[0]['output']['integrity_valid'])
        self.assertEqual('ACCEPTED', checks[1]['output']['status'])
        self.assertEqual(NEW, load_yaml((fixture.dest / 'OPERATING_CONFIG.yaml').read_bytes())['budgeting'])
        self.assertEqual(NEW, op.read_operating(fixture.dest,
            load_yaml((fixture.dest / installer.CONFIG).read_bytes())).config['budgeting'])
        from agentic.cli import main
        for command, suffix in (('validate-config', []), ('status', ['--json']), ('doctor', ['--json'])):
            with self.subTest(command=command):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(['--root', str(fixture.dest), command, *suffix])
                result = json.loads(out.getvalue())
                if command == 'doctor':
                    self.assertEqual(0, code)
                    self.assertIn('600,000,000,000', out.getvalue())
                    self.assertIn('pre_run', out.getvalue())
                else:
                    self.assertEqual(0, code, result)
                    self.assertEqual(NEW, result['operating']['budgeting'])

    def test_new_baseline_is_readable_without_changing_legacy_factory(self):
        self.assertEqual(OFF, budgeting.fresh_settings())
        self.assertEqual(OFF, op.default_operating()['budgeting'])
        self.assertEqual(NEW, self.value['budgeting'])
        self.write_plan(self.value)
        self.assertEqual(NEW, op.read_operating(self.root, self.gov).config['budgeting'])

    def test_only_exact_new_baseline_is_allowed_without_accepted_policy(self):
        value = deepcopy(self.value)
        value['budgeting'] = dict(NEW, max_tokens_per_ticket=6_000_000_001)
        with self.assertRaisesRegex(ValidationError, 'Nondefault protected settings'):
            resolve_protected(self.root, self.gov, value)

    def test_absent_block_keeps_legacy_engine_and_upgrade_initialization(self):
        self.assertEqual(budgeting.legacy_settings(), budgeting.resolve_settings({}))
        self.assertEqual(OFF, budgeting.resolve_settings({}, fresh=True))
        plan = self.write_plan()
        self.assertNotIn('budgeting', load_yaml(plan['files']['OPERATING_CONFIG.yaml']))
        report = op.inspect_operating(self.root, self.gov)
        self.assertEqual('LEGACY_RETAINED', report['budgeting']['disposition'])

    def test_all_partial_blocks_remain_rejected_and_unchanged(self):
        for block in ({}, {'accounting_enabled': True},
                      {key: value for key, value in OFF.items() if key != 'max_tokens_per_ticket'}):
            with self.subTest(block=block):
                before = deepcopy(block)
                with self.assertRaises(ValidationError):
                    budgeting.resolve_settings({'budgeting': block})
                value = dict(self.value, budgeting=block)
                with self.assertRaises(ValidationError):
                    op.validate_operating(value, self.gov)
                self.assertEqual(before, block)

    def test_explicit_off_and_complete_settings_without_mode_stay_unchanged(self):
        value = dict(self.value, budgeting=OFF)
        self.write_plan(value)
        self.assertEqual(OFF, op.read_operating(self.root, self.gov).config['budgeting'])
        self.assertEqual('OFF', budgeting.budget_readiness(OFF)['status'])
        strict = dict(OFF, accounting_enabled=True, enforcement_enabled=True)
        self.assertEqual(strict, budgeting.resolve_settings({'budgeting': strict}))
        self.assertIn('qualification_receipt', budgeting.budget_readiness(strict)['missing'])

    def test_upgrade_steps_preserve_absent_partial_and_off_bytes_from_195_and_196(self):
        for previous, following in (('1.9.5', '1.9.6'), ('1.9.6', '1.9.7')):
            variants = (None, b'{}\n', *(json.dumps({'budgeting': block}).encode() + b'\n'
                         for block in ({}, {'accounting_enabled': False}, OFF, NEW)))
            for operating in variants:
                with self.subTest(previous=previous, operating=operating):
                    config = json.dumps({'template': {'expected_workflow_version': previous}}).encode()
                    # Configuration-only migration: empty synthetic metadata,
                    # with no claim of authenticated installation provenance.
                    bundle = upgrade.MigrationBundle(config, operating, b'{}', b'{}', {})
                    result = upgrade.migrate_step(bundle, previous, following,
                        {'source_manifest_sha256': 'a' * 64, '_immutable_files': {}})
                    self.assertEqual(operating, result.operating_config)
                    self.assertNotIn('budgeting', load_yaml(result.project_config))

    def test_validate_config_and_status_projection_show_active_limits_and_mode(self):
        self.write_plan(self.value)
        for report in (op.inspect_operating(self.root, self.gov),
                       with_operating(self.root, self.gov, {'status': 'ACCEPTED', 'unresolved': []})['operating']):
            self.assertEqual('ACCEPTED', report['status'], report)
            self.assertEqual(NEW, report['budgeting'])

    def test_doctor_shows_effective_limits_and_mode(self):
        self.write_plan(self.value)
        report = command_catalog(self.root)
        Contracts(ROOT / '.agentic/schemas').validate('doctor-output', report)
        for text in (json.dumps(report), render_doctor(report)):
            self.assertIn('pre_run', text)
            self.assertIn('6,000,000,000', text)
            self.assertIn('600,000,000,000', text)

    def test_text_status_shows_active_limits_and_mode(self):
        from agentic.providers.github_status import render_status
        self.write_plan(self.value)
        text = render_status({'line': 'AWF synthetic status',
                              'operating': op.inspect_operating(self.root, self.gov)})
        self.assertIn('pre_run', text)
        self.assertIn('6,000,000,000', text)
        self.assertIn('600,000,000,000', text)


if __name__ == '__main__':
    unittest.main()
