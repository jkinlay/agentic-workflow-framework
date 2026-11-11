"""Upgrade compatibility and dormant adoption use the settings PR boundary."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError, operating as op
from agentic.budgeting import fresh_settings
from agentic.canonical import load_yaml
from settings_pr_fixtures import publish_fixture_settings
from test_operating import governance


class UpgradeSettingsPRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gov = governance()
        self.legacy = op.default_operating(self.gov['execution']['model_routing'])
        self.legacy.pop('budgeting')
        publish_fixture_settings(self, self.root, self.gov, self.legacy)
        op.initialize_operating(self.root, self.gov)

    def show(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = op.main(['--root', str(self.root), 'show', '--json'])
        self.assertEqual(0, code, output.getvalue())
        return json.loads(output.getvalue())

    def branch(self):
        from agentic.protected_settings import _git
        _git(self.root, 'checkout', '-qb', 'codex/upgrade')

    def test_legacy_show_reports_absence_without_claiming_controls_enabled(self):
        before = (self.root / op.CONFIG).read_bytes()
        report = self.show()
        self.assertNotIn('budgeting', report['configuration'])
        self.assertEqual('LEGACY_RETAINED', report['budgeting']['disposition'])
        self.assertIsNone(report['budget_admission'])
        self.assertIn('legacy', report['protected_settings']['source'])
        self.assertIn('LEGACY_RETAINED', report['table'])
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())

    def test_owner_schema_refusals_do_not_echo_contents_or_parser_causes(self):
        from agentic.contracts import Contracts
        import traceback
        directory = self.root / 'owner-schemas'
        directory.mkdir()
        marker = 'AWF238_REJECTED_OWNER_SCHEMA_CONTENT'
        cases = {
            'schema': op._json({'$id': 'urn:awf:1.2:owner', 'type': marker}),
            'duplicate': ('{"' + marker + '":1,"' + marker + '":2}').encode(),
        }
        for kind, raw in cases.items():
            with self.subTest(kind=kind):
                (directory / 'owner.schema.json').write_bytes(raw)
                try:
                    Contracts(directory)
                except ValidationError as error:
                    self.assertIn('Invalid schema: .agentic/schemas/owner.schema.json', str(error))
                    self.assertNotIn(marker, ''.join(traceback.format_exception(error)))
                else:
                    self.fail('Invalid owner schema was accepted')

    def test_legacy_diagnostics_preserve_policy_refusal(self):
        from dataclasses import replace
        snapshot = op.read_operating(self.root, self.gov)
        provenance = snapshot.provenance
        provenance['protected_settings']['budget_admission'] = False
        snapshot = replace(snapshot, _provenance_json=op._json(provenance))
        with patch.object(op, 'read_operating', return_value=snapshot):
            report = op.inspect_operating(self.root, self.gov)
        self.assertEqual('LEGACY_RETAINED', report['budgeting']['disposition'])
        self.assertIs(False, report['budget_admission'])
        self.assertIs(False, report['protected_settings']['budget_admission'])

    def test_adoption_prepares_off_block_keeps_comments_and_waits_for_default(self):
        self.branch()
        import yaml
        original = ('# owner header\n' + yaml.safe_dump(self.legacy, sort_keys=False)
                    + '# owner tail\n').encode()
        (self.root / op.CONFIG).write_bytes(original)
        result = op.set_operating(self.root, self.gov, 'Adopt dormant controls', adopt_budgeting=True)
        prepared = (self.root / op.CONFIG).read_bytes()
        self.assertTrue(prepared.startswith(original))
        self.assertEqual(fresh_settings(), load_yaml(prepared)['budgeting'])
        self.assertNotIn('budgeting', result.config)
        self.assertNotIn('budgeting', self.show()['configuration'])
        self.assertTrue(self.show()['protected_settings']['differences'])
        proposed = load_yaml(prepared)
        publish_fixture_settings(self, self.root, self.gov, proposed)
        self.assertEqual(fresh_settings(), self.show()['configuration']['budgeting'])
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_adoption_refuses_default_branch_and_cannot_enable(self):
        original = (self.root / op.CONFIG).read_bytes()
        with self.assertRaisesRegex(ValidationError, 'branch'):
            op.set_operating(self.root, self.gov, 'Adopt', adopt_budgeting=True)
        self.branch()
        with self.assertRaisesRegex(ValidationError, 'OFF|separate'):
            op.set_operating(self.root, self.gov, 'Enable', ['budgeting.accounting_enabled=true'], adopt_budgeting=True)
        self.assertEqual(original, (self.root / op.CONFIG).read_bytes())

    def test_adoption_accepts_git_valid_utf8_branch(self):
        from agentic.protected_settings import _git
        import yaml
        _git(self.root, 'checkout', '-qb', 'codex/adopt-\u00e9')
        original = ('# owner header\n' + yaml.safe_dump(self.legacy, sort_keys=False)).encode()
        (self.root / op.CONFIG).write_bytes(original)
        result = op.set_operating(self.root, self.gov, 'Adopt dormant controls', adopt_budgeting=True)
        prepared = (self.root / op.CONFIG).read_bytes()
        self.assertTrue(prepared.startswith(original))
        self.assertEqual(fresh_settings(), load_yaml(prepared)['budgeting'])
        self.assertNotIn('budgeting', result.config)

    def test_adoption_refuses_invalid_utf8_or_git_reference_without_writes(self):
        from agentic.protected_settings import _git
        self.branch()
        original = (self.root / op.CONFIG).read_bytes()
        for branch in (b'refs/heads/invalid\xff\n', b'refs/heads/invalid..name\n'):
            def observed(root, *args, **kwargs):
                if args == ('symbolic-ref', '-q', 'HEAD'):
                    return branch
                return _git(root, *args, **kwargs)
            with self.subTest(branch=branch), patch('agentic.protected_settings._git', side_effect=observed):
                with self.assertRaises(ValidationError):
                    op.set_operating(self.root, self.gov, 'Adopt dormant controls', adopt_budgeting=True)
            self.assertEqual(original, (self.root / op.CONFIG).read_bytes())

    def test_merged_enabled_policy_then_unprotected_change_does_not_lock_upgrade(self):
        enabled = deepcopy(self.legacy)
        enabled['budgeting'] = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        publish_fixture_settings(self, self.root, self.gov, enabled)
        self.assertEqual(enabled['budgeting'], self.show()['configuration']['budgeting'])
        changed = op.set_operating(self.root, self.gov, 'Two streams', ['streams.count=2'])
        self.assertEqual(2, changed.count)
        self.assertTrue(changed.config['budgeting']['enforcement_enabled'])
        self.assertTrue(op.read_operating(self.root, self.gov).config['budgeting']['enforcement_enabled'])

    def test_worker_edit_cannot_enable_or_disable_upgraded_policy(self):
        enabled = deepcopy(self.legacy)
        enabled['budgeting'] = dict(fresh_settings(), accounting_enabled=True, enforcement_enabled=True)
        for accepted, edited in ((self.legacy, enabled), (enabled, self.legacy)):
            with self.subTest(accepted=accepted.get('budgeting')):
                publish_fixture_settings(self, self.root, self.gov, accepted)
                (self.root / op.CONFIG).write_bytes(op._json(edited))
                effective = op.read_operating(self.root, self.gov)
                self.assertEqual(accepted.get('budgeting'), effective.config.get('budgeting'))

    def test_absent_accepted_file_ignores_enabled_working_budget_on_upgrade_branch(self):
        from agentic.protected_settings import _git
        _git(self.root, 'rm', '-q', op.CONFIG)
        _git(self.root, '-c', 'user.name=Fixture', '-c',
             'user.email=fixture@example.invalid', 'commit', '-qm', 'Absent accepted file')
        _git(self.root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        commit = _git(self.root, 'rev-parse', 'HEAD').decode().strip()
        for legacy in (False, True):
            value = deepcopy(self.legacy)
            if not legacy:
                value['budgeting'] = dict(fresh_settings(), accounting_enabled=True,
                                          enforcement_enabled=True)
            (self.root / op.CONFIG).write_bytes(op._json(value))
            snapshot = op.read_operating(self.root, self.gov)
            self.assertEqual(None if legacy else fresh_settings(), snapshot.config.get('budgeting'))
            report = snapshot.provenance['protected_settings']
            self.assertEqual(commit, report['commit'])
            self.assertTrue(report['budget_admission'])
            self.assertIn('accepted', report['policy_observation'])
            self.assertIs(True, self.show()['budget_admission'])

    def test_nondefault_policy_checks_use_normalized_values(self):
        from agentic.protected_settings import resolve_protected, require_mutation_policy
        with tempfile.TemporaryDirectory() as raw:
            for block in (None, fresh_settings()):
                value = deepcopy(self.legacy)
                if block is not None:
                    value['budgeting'] = block
                # Compare only valid normalized defaults. An incomplete block
                # is refused below rather than treated as accepted OFF policy.
                effective, _ = resolve_protected(Path(raw), self.gov, value)
                self.assertEqual(value, effective)
                require_mutation_policy({'unverified': True, 'next_action': 'fetch'}, value)
            for block in ({}, dict(fresh_settings(), accounting_enabled=True)):
                with self.subTest(refused=block):
                    value = dict(self.legacy, budgeting=block)
                    with self.assertRaisesRegex(ValidationError, 'Nondefault'):
                        resolve_protected(Path(raw), self.gov, value)
                    with self.assertRaisesRegex(ValidationError, 'Nondefault'):
                        require_mutation_policy({'unverified': True, 'next_action': 'fetch'}, value)

    def test_prerelease_confirmation_record_is_refused_plainly_and_retained(self):
        path = next((self.root / op.STATE / 'changes').glob('*.json'))
        event = json.loads(path.read_bytes())
        event['owner_admission'] = {'digest': 'a' * 64}
        raw = op._json(event)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValidationError, 'pre-release.*unsupported'):
            op.read_operating(self.root, self.gov)
        self.assertEqual(raw, path.read_bytes())
