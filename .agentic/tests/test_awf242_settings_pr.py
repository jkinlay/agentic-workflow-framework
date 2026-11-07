"""Protected settings follow fetched default-branch bytes, never a worker edit."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import source_only
from agentic import ValidationError
from agentic import operating as op
from agentic.budgeting import ControllerBudgeting, fresh_settings
from test_operating import governance


class SettingsPRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='awf settings pr ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gov = governance()
        self.value = op.default_operating(self.gov['execution']['model_routing'])
        (self.root / op.GOVERNANCE).parent.mkdir()
        (self.root / op.GOVERNANCE).write_bytes(op._json(self.gov))
        self.write(self.value)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.publish(self.value)

    def git(self, *args):
        from agentic.child_process import isolated_git_env
        return subprocess.run([shutil.which('git'), '-C', str(self.root), *args],
            env=isolated_git_env(), capture_output=True, text=True, check=True).stdout.strip()

    def write(self, value):
        (self.root / op.CONFIG).write_bytes(op._json(value))

    def publish(self, value):
        self.write(value)
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Fixture settings', '--allow-empty')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        return self.git('rev-parse', 'HEAD')

    def enabled(self):
        value = deepcopy(self.value)
        value['budgeting'].update(accounting_enabled=True, enforcement_enabled=True)
        return value

    def test_hand_edits_cannot_enable_or_disable_effective_settings(self):
        for accepted, edited in ((self.value, self.enabled()), (self.enabled(), self.value)):
            with self.subTest(accepted=accepted['budgeting']):
                self.publish(accepted)
                self.write(edited)
                self.assertEqual(accepted['budgeting'], op.read_operating(self.root, self.gov).config['budgeting'])

    def test_pr_branch_waits_for_fetched_default_and_reports_commit(self):
        previous = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-qb', 'codex/settings')
        self.write(self.enabled())
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Proposed budget')
        proposed = self.git('rev-parse', 'HEAD')
        self.assertEqual(fresh_settings(), op.read_operating(self.root, self.gov).config['budgeting'])
        self.git('update-ref', 'refs/remotes/origin/main', proposed)
        snapshot = op.read_operating(self.root, self.gov)
        self.assertEqual(self.enabled()['budgeting'], snapshot.config['budgeting'])
        self.assertEqual(proposed, snapshot.provenance['protected_settings']['commit'])
        self.assertNotEqual(previous, proposed)

    def test_old_worktree_reads_same_default_settings(self):
        older = self.root / 'older'
        self.git('worktree', 'add', '-q', '--detach', str(older))
        (older / op.GOVERNANCE).parent.mkdir(exist_ok=True)
        (older / op.GOVERNANCE).write_bytes(op._json(self.gov))
        self.publish(self.enabled())
        self.assertEqual(self.enabled()['budgeting'], op.read_operating(older, self.gov).config['budgeting'])
        changed = op.set_operating(older, self.gov, 'two streams', ['streams.count=2'])
        self.assertEqual(2, changed.count)
        self.assertTrue(changed.config['budgeting']['enforcement_enabled'])

    def test_malformed_or_missing_default_file_fails_closed(self):
        self.publish(self.enabled())
        for raw in (b'budgeting: [', b'{}', b'budgeting: null'):
            with self.subTest(raw=raw):
                (self.root / op.CONFIG).write_bytes(raw)
                self.git('add', op.CONFIG)
                self.git('commit', '-qm', 'Malformed fixture')
                self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
                self.write(self.value)
                with self.assertRaisesRegex(ValidationError, 'default.branch'):
                    op.read_operating(self.root, self.gov)
        self.git('rm', '-qf', op.CONFIG)
        self.git('commit', '-qm', 'Missing fixture')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.write(self.value)
        with self.assertRaisesRegex(ValidationError, 'default.branch'):
            op.read_operating(self.root, self.gov)

    def test_show_reports_effective_diff_rules_warning_and_pr_next_step(self):
        self.publish(self.enabled())
        self.write(self.value)
        output = io.StringIO()
        with redirect_stdout(output):
            code = op.main(['--root', str(self.root), 'show', '--json'])
        self.assertEqual(0, code, output.getvalue())
        report = json.loads(output.getvalue())
        self.assertEqual(self.enabled()['budgeting'], report['configuration']['budgeting'])
        self.assertTrue(report['protected_settings']['differences'])
        self.assertIn('open a PR', report['protected_settings']['next_action'])
        self.assertEqual('UNOBSERVED', report['repository_rules']['status'])

    def test_protected_set_validates_but_writes_nothing_and_names_pr_route(self):
        before = (self.root / op.CONFIG).read_bytes()
        with self.assertRaisesRegex(ValidationError, 'PR'):
            op.set_operating(self.root, self.gov, 'enable', ['budgeting.accounting_enabled=true'])
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        with self.assertRaisesRegex(ValidationError, 'Enforcement requires accounting'):
            op.set_operating(self.root, self.gov, 'invalid', ['budgeting.enforcement_enabled=true'])
        self.assertFalse((self.root / op.JOURNAL).exists())

    def test_controller_reloads_default_instead_of_trusting_validated_worker_snapshot(self):
        self.publish(self.enabled())
        self.write(self.value)
        from dataclasses import replace
        snapshot = replace(op.validate_operating(self.value, self.gov),
            _provenance_json=op.canonical({'path': str(self.root / op.CONFIG)}))
        context = ControllerBudgeting(self.root.parent / (self.root.name + '.sqlite'),
            self.gov, snapshot, object(), worktree_roots=(self.root,))
        self.assertTrue(context.settings['enforcement_enabled'])

    def test_audited_working_budget_edits_and_merged_changes_leave_unprotected_audit_binding(self):
        op.initialize_operating(self.root, self.gov)
        self.write(self.enabled())
        self.assertEqual(fresh_settings(), op.read_operating(self.root, self.gov).config['budgeting'])
        self.publish(self.enabled())
        self.assertTrue(op.read_operating(self.root, self.gov).config['budgeting']['enforcement_enabled'])
        changed = op.set_operating(self.root, self.gov, 'two streams', ['streams.count=2'])
        self.assertEqual(2, changed.count)
        self.assertTrue(changed.config['budgeting']['enforcement_enabled'])
        self.value['streams']['count'] = 1
        self.write(self.value)
        with self.assertRaisesRegex(ValidationError, 'audit'):
            op.read_operating(self.root, self.gov)

    def test_invalid_working_protected_values_do_not_replace_default_policy(self):
        self.publish(self.enabled())
        value = deepcopy(self.value)
        value['budgeting'] = None
        self.write(value)
        self.assertTrue(op.read_operating(self.root, self.gov).config['budgeting']['enforcement_enabled'])

    def test_nonrepository_defaults_and_legacy_need_no_external_command(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / op.GOVERNANCE).parent.mkdir()
            (root / op.GOVERNANCE).write_bytes(op._json(self.gov))
            for legacy in (False, True):
                value = deepcopy(self.value)
                if legacy:
                    value.pop('budgeting')
                (root / op.CONFIG).write_bytes(op._json(value))
                with patch('subprocess.run', side_effect=AssertionError('external command')):
                    self.assertEqual(value, op.read_operating(root, self.gov).config)
            (root / op.CONFIG).write_bytes(op._json(self.enabled()))
            with self.assertRaisesRegex(ValidationError, 'Git|default.branch'):
                op.read_operating(root, self.gov)

    def test_proposal_validation_is_read_only_and_refuses_invalid_combination(self):
        proposal = self.root / 'proposal.yaml'
        before = (self.root / op.CONFIG).read_bytes()
        for valid in (True, False):
            value = self.enabled()
            if not valid:
                value['budgeting']['accounting_enabled'] = False
            proposal.write_bytes(op._json(value))
            output = io.StringIO()
            with redirect_stdout(output):
                status = op.main(['--root', str(self.root), 'validate', '--file', str(proposal), '--json'])
            self.assertEqual(0 if valid else 2, status, output.getvalue())
            self.assertEqual(before, (self.root / op.CONFIG).read_bytes())

    def test_packed_refs_and_git_environment_do_not_steer_effective_settings(self):
        commit = self.publish(self.enabled())
        self.git('pack-refs', '--all')
        with patch.dict(os.environ, {'GIT_DIR': str(self.root / 'untrusted'),
                'GIT_WORK_TREE': str(self.root / 'untrusted'), 'GIT_NO_REPLACE_OBJECTS': '0'}):
            snapshot = op.read_operating(self.root, self.gov)
        self.assertTrue(snapshot.config['budgeting']['enforcement_enabled'])
        self.assertEqual(commit, snapshot.provenance['protected_settings']['commit'])

    def test_unreadable_default_blob_and_oversize_file_fail_closed(self):
        self.publish(self.enabled())
        blob = self.git('rev-parse', 'HEAD:' + op.CONFIG)
        object_path = self.root / '.git/objects' / blob[:2] / blob[2:]
        object_path.chmod(0o600)
        object_path.unlink()
        self.write(self.value)
        with self.assertRaisesRegex(ValidationError, 'default.branch'):
            op.read_operating(self.root, self.gov)
        (self.root / op.CONFIG).write_bytes(b'#' + b'x' * (op.MAX_BYTES + 1))
        self.git('add', op.CONFIG)
        self.git('commit', '-qm', 'Oversize fixture')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.write(self.value)
        with self.assertRaisesRegex(ValidationError, '1 MiB'):
            op.read_operating(self.root, self.gov)

    def test_validate_config_reports_effective_settings_and_pr_next_step(self):
        from agentic.operating_status import with_operating
        self.publish(self.enabled())
        self.write(self.value)
        report = with_operating(self.root, self.gov, {'status': 'ACCEPTED', 'unresolved': []})
        self.assertEqual('ACCEPTED', report['status'])
        self.assertEqual(self.enabled()['budgeting'], report['operating']['budgeting'])
        self.assertIn('open a PR', report['operating']['protected_settings']['next_action'])

    def test_unconfirmed_journal_cannot_change_budget(self):
        # Retained guarantee: local recovery is never a protected-settings path.
        # Git/PR replaces confirmation; no protected journal is executable.
        before = (self.root / op.CONFIG).read_bytes()
        with op.Tree(self.root) as tree:
            _, _, _, journal = op._prepare_commit(tree, self.gov, before,
                self.enabled(), 'Fixture unauthorized journal',
                [{'path': 'budgeting.accounting_enabled', 'before': False, 'after': True}], 'chat')
            op._new(tree, op.JOURNAL, journal)
        with self.assertRaisesRegex(ValidationError, 'PR'):
            op.recover_operating(self.root, self.gov, op.sha256(journal))
        self.assertEqual(before, (self.root / op.CONFIG).read_bytes())
        self.assertEqual(journal, (self.root / op.JOURNAL).read_bytes())

    def test_assigned_repository_load_does_not_need_global_git_owner_configuration(self):
        # Git's test switch reproduces an owner-created checkout read by the
        # controller sandbox, without changing real OS ownership or global config.
        from agentic import protected_settings
        real_environment = protected_settings.isolated_git_env
        self.publish(self.enabled())
        with patch.object(protected_settings, 'isolated_git_env',
                side_effect=lambda: real_environment(extra={'GIT_TEST_ASSUME_DIFFERENT_OWNER': '1'})):
            snapshot = op.read_operating(self.root, self.gov)
        self.assertTrue(snapshot.config['budgeting']['enforcement_enabled'])


if __name__ == '__main__':
    unittest.main()
