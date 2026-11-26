"""Jira accepted policy decides even when the working proposal unsets it."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import tempfile
import unittest

import source_only  # noqa: F401
from shipped_defaults import shipped_routing
import test_awf307_o1c_regressions as github_fixture
from test_awf307_jira_owner_policy import JiraFixture, ISSUE, NOW
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.jira_owner_policy import closure_scope, project_policy_scope
from agentic.shared_owner_policy import observe_accepted_owner_policy
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from agentic.policy import policy_hash
import test_provider_identity as provider

KEY = 'shared_owner_controller_actor_id'


class JiraAcceptedPolicyTests(unittest.TestCase):
    policy_repository = github_fixture.DistinctOwnerCompatibility.policy_repository

    def setUp(self):
        self.enterContext(shipped_routing())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config = provider.config()
        self.config['merge_gate']['shared_owner_automation_actor_id'] = None
        self.config['jira'][KEY] = self.config['jira']['controller_actor_id'] = 'fixture-controller'
        self.project = self.policy_repository(self.config, 'accepted')
        self.store = SimpleNamespace(accepted_policy_root=self.project, worktree_roots=[self.project])

    def proposals(self):
        for form in ('null', 'absent', 'changed-identity', 'empty-jira', 'malformed-jira'):
            config = deepcopy(self.config)
            config['jira'][KEY] = None
            if form == 'absent':
                config['jira'].pop(KEY)
            elif form == 'changed-identity':
                config['jira']['controller_actor_id'] = 'another-controller'
            elif form == 'empty-jira':
                config['jira'] = {}
            elif form == 'malformed-jira':
                config['jira'] = []
            yield form, config

    def test_project_scope_refuses_hidden_accepted_setting_whatever_proposal(self):
        for form, config in self.proposals():
            with self.subTest(form=form), self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
                with project_policy_scope(self.store, config):
                    self.fail('A proposal hid accepted Jira policy')

    def test_closure_scope_refuses_hidden_setting_before_early_return(self):
        for form, config in self.proposals():
            with self.subTest(form=form), self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
                with closure_scope(self.store, config, ISSUE, {}, {}, owned=True):
                    self.fail('An ineligible closure hid accepted Jira policy')

    def test_relative_root_and_unusable_working_file_cannot_hide_policy(self):
        (self.project / '.agentic').mkdir()
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_text('execution: [broken', encoding='utf-8')
        config = deepcopy(self.config)
        config['jira'][KEY] = None
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            self.store.accepted_policy_root = Path('accepted')
            with self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
                with project_policy_scope(self.store, config):
                    self.fail('Working policy or relative spelling hid the setting')
        finally:
            os.chdir(previous)

    def test_accepted_unset_or_unobserved_keeps_legacy_scope(self):
        for observed in (True, False):
            for form in ('null', 'absent'):
                config = deepcopy(self.config)
                config['jira'][KEY] = None
                if form == 'absent':
                    config['jira'].pop(KEY)
                root = self.policy_repository(config, form + str(observed), observed=observed)
                store = SimpleNamespace(accepted_policy_root=root, worktree_roots=[root])
                with project_policy_scope(store, config), closure_scope(store, config, ISSUE, {}, {}, owned=True):
                    pass

    def test_jira_only_subfolder_observation_keeps_existing_scope(self):
        # No owner decision has extended GitHub's whole-repository rule to Jira.
        sub = self.project / 'application'
        sub.mkdir()
        def git(*args, raw=None):
            return subprocess.run(['git', '-C', str(self.project), *args], input=raw,
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
        original = git('rev-parse', 'refs/remotes/origin/main^{tree}')
        tree = git('mktree', raw=b'040000 tree ' + original + b'\tapplication\n')
        environment = os.environ.copy()
        environment.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                           GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        commit = subprocess.run(['git', '-C', str(self.project), 'commit-tree', tree.decode()],
            input=b'Synthetic subfolder policy\n', env=environment, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()
        git('update-ref', 'refs/remotes/origin/main', commit.decode())
        result = observe_accepted_owner_policy(sub, self.config, definition(),
            Contracts(Path(__file__).resolve().parents[1] / 'schemas'),
            policy_sha256=policy_hash(self.config, definition()))
        self.assertEqual('fixture-controller', result[KEY])
        from agentic.jira_owner_visibility import jira_owner_warnings
        warnings = jira_owner_warnings(sub, {'jira': {KEY: None}})
        self.assertTrue(any('Accepted default-branch policy' in row['message'] for row in warnings))

    def test_jira_scope_fix_preserves_github_validation_order(self):
        config = deepcopy(self.config)
        config['merge_gate'].update(shared_owner_automation_actor_id=1002, trusted_owner_ids=[1001])
        with self.assertRaisesRegex(ValidationError, '^Shared-owner accepted policy digest changed$'):
            observe_accepted_owner_policy(self.project, config, definition(),
                Contracts(Path(__file__).resolve().parents[1] / 'schemas'),
                policy_sha256=policy_hash(config, definition()))

    def test_reporting_uses_independent_policy_read(self):
        from agentic.jira_owner_visibility import jira_owner_warnings
        (self.project / '.agentic').mkdir()
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_text('execution: [broken', encoding='utf-8')
        warnings = jira_owner_warnings(self.project, {'jira': {KEY: None}})
        self.assertTrue(any('Accepted default-branch policy' in row['message'] for row in warnings))


class JiraLifecycleSyncTests(JiraFixture):
    policy_repository = github_fixture.DistinctOwnerCompatibility.policy_repository

    def real_policy(self):
        self.root = self.outer
        self.project = self.policy_repository(self.config, 'real-policy')
        subprocess.run(['git', '-C', str(self.project), 'config', 'remote.origin.url',
            'https://github.com/example/project.git'], check=True)
        self.store.worktree_roots = [self.project]
        self.store.accepted_policy_root = self.project

    def test_lifecycle_hidden_setting_refuses_distinct_owner_before_provider_reads(self):
        from agentic import continuous_controller as controller
        self.real_policy()
        self.config['jira'][KEY] = None
        self.http.actor = 'fixture-distinct-owner'
        before = len(self.http.calls)
        with self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
            controller.production_jira_lifecycle(self.store, config=self.config, contract=self.contract,
                event='JIRA_RECONCILED', facts={'merge_confirmed': True, 'closeout_valid': True},
                binding={'issue_id': ISSUE}, issue_type='LEAF', state='MERGED', producer_id='controller',
                run_id=self.started['run_id'], now=NOW, evidence=self.started['evidence'], transition_id='31',
                **{key: self.adapters[key] for key in ('read_current_status', 'write_transition',
                    'read_transition', 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
        self.assertEqual(before, len(self.http.calls))

    def test_real_reference_closure_ignores_unusable_proposed_file(self):
        from agentic import continuous_controller as controller
        self.real_policy()
        (self.project / '.agentic').mkdir()
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_text('execution: [broken', encoding='utf-8')
        result = controller.production_jira_lifecycle(self.store, config=self.config, contract=self.contract,
            event='JIRA_RECONCILED', facts={'merge_confirmed': True, 'closeout_valid': True},
            binding={'issue_id': ISSUE}, issue_type='LEAF', state='MERGED', producer_id='controller',
            run_id=self.started['run_id'], now=NOW, evidence=self.started['evidence'], transition_id='31',
            merge_result_id='00000000-0000-4000-8000-000000000043',
            **{key: self.adapters[key] for key in ('read_current_status', 'write_transition',
                'read_transition', 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
        self.assertIn('owner_closure', result, result)
        self.assertTrue(all(call[0] == 'GET' for call in self.http.calls))

    def test_direct_validator_and_retained_scope_refuse_hidden_setting(self):
        from agentic import continuous_controller as controller
        from agentic.jira_owner_policy import retained_scope
        self.http.actor = 'fixture-distinct-owner'
        result = self.close()
        value = {key: result[key] for key in ('record', 'owner_closure')}
        self.real_policy()
        self.config['jira'][KEY] = None
        with self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
            controller.validate_jira_owner_closure(value, self.request(result), now=NOW,
                store=self.store, config=self.config)
        with self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
            with retained_scope(self.store, self.config, value):
                self.fail('Retained scope hid accepted Jira policy')
