"""Owner decision: Jira opt-out uses the shared whole-repository boundary."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import source_only  # noqa: F401
import test_awf307_o1c_regressions as github
import test_provider_identity as provider
from shipped_defaults import shipped_routing
from agentic import ValidationError
from agentic.configuration import inspect_config
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from agentic.policy import policy_hash
from agentic import shared_owner_policy as policy
from agentic.jira_owner_policy import project_policy_scope, closure_scope, retained_scope, refuse_hidden_policy

KEY = policy.JIRA_SETTING
RULE = 'top level of its own repository'


class JiraWholeRepositoryTests(unittest.TestCase):
    policy_repository = github.DistinctOwnerCompatibility.policy_repository

    def setUp(self):
        self.enterContext(shipped_routing())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.config = provider.config()
        self.config['merge_gate']['shared_owner_automation_actor_id'] = None
        self.config['jira'].update(controller_actor_id='fixture-controller',
                                   shared_owner_controller_actor_id='fixture-controller')
        self.project = self.policy_repository(self.config, 'whole')
        self.contracts = Contracts(Path(__file__).resolve().parents[1] / 'schemas')

    def git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True).stdout.strip()

    def observe(self, root, config=None):
        config = self.config if config is None else config
        return policy.observe_accepted_owner_policy(root, config, definition(), self.contracts,
                                                    policy_sha256=policy_hash(config, definition()))

    def layouts(self):
        sub = self.project / 'application'
        sub.mkdir()
        nested = self.project / 'nested'
        nested.mkdir()
        shutil.copytree(self.project / '.git', nested / '.git')
        bare = self.root / 'bare'
        shutil.copytree(self.project / '.git', bare)
        self.git(bare, '--git-dir', str(bare), 'config', 'core.bare', 'true')
        return (sub, nested, bare)

    def test_rooted_validation_refuses_jira_setting_at_each_disallowed_layout(self):
        for root in self.layouts():
            with self.subTest(layout=root.name):
                report = inspect_config(self.config, definition(), self.contracts, project_root=root)
                self.assertEqual('REJECTED', report['status'])
                issues = [row for row in report['unresolved'] if row['path'] == policy.JIRA_SETTING_PATH]
                self.assertTrue(any(RULE in row['reason'] for row in issues), issues)
                from agentic.policy import validate_config
                with self.assertRaisesRegex(ValidationError, RULE):
                    validate_config(self.config, definition(), self.contracts, accepted_policy_root=root)

    def test_observation_refuses_jira_setting_at_each_disallowed_layout(self):
        for root in self.layouts():
            with self.subTest(layout=root.name), self.assertRaisesRegex(ValidationError, RULE):
                self.observe(root)

    def test_every_scope_refuses_before_distinct_or_ineligible_early_returns(self):
        for root in self.layouts():
            store = SimpleNamespace(accepted_policy_root=root, worktree_roots=[root])
            for name, scope in (
                    ('project', lambda: project_policy_scope(store, self.config)),
                    ('closure', lambda: closure_scope(store, self.config, 'EX-42', {}, {}, owned=True)),
                    ('retained', lambda: retained_scope(store, self.config, {'owner_closure': {}}))):
                with self.subTest(layout=root.name, consumer=name), self.assertRaisesRegex(ValidationError, RULE):
                    with scope():
                        self.fail('Disallowed root reached consumer body')
            with self.subTest(layout=root.name, consumer='direct validator guard'), \
                    self.assertRaisesRegex(ValidationError, RULE):
                refuse_hidden_policy(store, self.config)

    def test_null_and_absent_subfolder_scopes_keep_legacy_result(self):
        sub = self.project / 'application'
        sub.mkdir()
        store = SimpleNamespace(accepted_policy_root=sub, worktree_roots=[sub])
        for form in ('null', 'absent'):
            config = deepcopy(self.config)
            config['jira'][KEY] = None
            if form == 'absent':
                config['jira'].pop(KEY)
            with self.subTest(form=form):
                self.assertEqual('ACCEPTED', inspect_config(config, definition(), self.contracts,
                                                           project_root=sub)['status'])
                with project_policy_scope(store, config), closure_scope(store, config, 'EX-42', {}, {}, owned=True), \
                        retained_scope(store, config, {'owner_closure': {}}):
                    pass

    def test_whole_and_linked_worktrees_observe_and_refuse_hidden_policy(self):
        inside = self.project / '.claude/worktrees/inside'
        outside = self.root / 'linked'
        for root in (inside, outside):
            self.git(self.project, 'worktree', 'add', '-q', '--detach', str(root), 'refs/remotes/origin/main')
        for root in (self.project, inside, outside):
            with self.subTest(root=str(root)):
                self.assertEqual('fixture-controller', self.observe(root)[KEY])
                self.assertEqual('ACCEPTED', inspect_config(self.config, definition(), self.contracts,
                                                           project_root=root)['status'])
                for form in ('null', 'absent'):
                    config = deepcopy(self.config)
                    config['jira'][KEY] = None
                    if form == 'absent':
                        config['jira'].pop(KEY)
                    with self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
                        policy._refuse_hidden_jira_opt_out(config, root)

    def test_linked_components_cannot_hide_accepted_jira_setting(self):
        hidden = deepcopy(self.config)
        hidden['jira'][KEY] = None
        for index, component in enumerate(('.agentic', '.agentic/scripts', '.agentic/lib')):
            outer = self.policy_repository(self.config, 'link-' + str(index))
            target = outer / 'moved/deep' / component.removeprefix('.agentic').strip('/')
            target.mkdir(parents=True)
            link = outer / component
            link.parent.mkdir(exist_ok=True)
            if os.name == 'nt':
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            else:
                link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
            apparent = link.resolve().parents[component.count('/')]
            with self.subTest(component=component), \
                    self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
                policy._refuse_hidden_jira_opt_out(hidden, apparent)

    def test_unreadable_enclosing_scan_refuses_each_set_accepted_setting_only(self):
        real = os.scandir
        for section, setting, guard in (
                ('merge_gate', policy.SETTING, policy._refuse_hidden_opt_out),
                ('jira', KEY, policy._refuse_hidden_jira_opt_out)):
            for state in ('set', 'null', 'absent'):
                accepted = deepcopy(self.config)
                accepted['merge_gate'][policy.SETTING] = None
                accepted['jira'][KEY] = None
                if state == 'set':
                    accepted[section][setting] = (1002 if section == 'merge_gate' else 'fixture-controller')
                elif state == 'absent':
                    accepted[section].pop(setting)
                hidden = deepcopy(accepted)
                hidden[section][setting] = None
                outer = self.policy_repository(accepted, section + '-' + state)
                (outer / '.agentic/scripts').mkdir(parents=True)
                target = outer / 'deep/target'
                target.mkdir(parents=True)
                unset = self.policy_repository(hidden, section + '-' + state + '-unset')
                shutil.copytree(unset / '.git', target / '.git')
                guard(hidden, target)  # A readable, unlinked enclosing project adds no policy.

                def denied(path='.', refused=os.path.normcase(os.path.realpath(outer / '.agentic'))):
                    spelled = path if isinstance(path, int) else os.fspath(path)
                    if isinstance(spelled, str):
                        spelled = os.path.normcase(os.path.realpath(spelled))
                        try:
                            inside = os.path.commonpath((spelled, refused)) == refused
                        except ValueError:
                            inside = False
                        if inside:
                            raise PermissionError(13, 'Permission denied', str(path))
                    return real(path)

                with self.subTest(section=section, accepted=state), patch.object(os, 'scandir', denied):
                    if state == 'set':
                        with self.assertRaisesRegex(ValidationError, 'could not be read'):
                            policy._accepted_opt_out(target, section=section, setting=setting)
                        with self.assertRaisesRegex(ValidationError, 'could not be checked'):
                            guard(hidden, target)
                    else:
                        self.assertIsNone(policy._accepted_opt_out(target, section=section, setting=setting))
                        self.assertIsNone(guard(hidden, target))

    def test_hidden_jira_read_ignores_unusable_proposed_child_configuration(self):
        from agentic import child_process
        config = deepcopy(self.config)
        config['jira'][KEY] = None
        proposed = self.root / 'PROPOSED.yaml'
        proposed.write_bytes(b'{')
        with patch.object(child_process, '_DEFAULT_CONFIG', proposed), \
                self.assertRaisesRegex(ValidationError, 'accepted policy digest changed'):
            policy._refuse_hidden_jira_opt_out(config, self.project)


class JiraProductionRootTests(unittest.TestCase):
    def case(self):
        import test_awf307_jira_owner_policy as fixture
        import test_awf307_sync5_regressions as sync
        case = sync.JiraLifecycleSyncTests('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        sync.JiraLifecycleSyncTests.real_policy(case)
        return case

    def test_real_controller_and_adapter_refuse_subfolder_before_reads_or_writes(self):
        from agentic import continuous_controller as controller
        import test_awf307_jira_owner_policy as fixture
        case = self.case()
        sub = case.project / 'application'
        sub.mkdir()
        case.store.worktree_roots = [sub]
        case.store.accepted_policy_root = sub
        for actor in ('controller', 'distinct-owner'):
            case.http.actor = actor
            before = len(case.http.calls)
            with self.subTest(actor=actor), self.assertRaisesRegex(ValidationError, RULE):
                controller.production_jira_lifecycle(case.store, config=case.config, contract=case.contract,
                    event='JIRA_RECONCILED', facts={'merge_confirmed': True, 'closeout_valid': True},
                    binding={'issue_id': fixture.ISSUE}, issue_type='LEAF', state='MERGED',
                    producer_id='controller', run_id=case.started['run_id'], now=fixture.NOW,
                    evidence=case.started['evidence'], transition_id='31',
                    **{key: case.adapters[key] for key in ('read_current_status', 'write_transition',
                        'read_transition', 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
            self.assertEqual(before, len(case.http.calls))

    def test_real_batch_refuses_subfolder_before_any_transition(self):
        case = self.case()
        sub = case.project / 'application'
        sub.mkdir()
        case.store.worktree_roots = [sub]
        case.store.accepted_policy_root = sub
        from contextlib import nullcontext
        case.policy_reads = nullcontext
        with self.assertRaisesRegex(ValidationError, RULE):
            case.held_batch()
        self.assertTrue(all(call[0] == 'GET' for call in case.http.calls))

    def test_real_retained_and_direct_validation_refuse_disallowed_root(self):
        from agentic import continuous_controller as controller
        import test_awf307_jira_owner_policy as fixture
        case = self.case()
        # Obtain a real shared closure at a qualifying root, then change host scope.
        result = controller.production_jira_lifecycle(case.store, config=case.config, contract=case.contract,
            event='JIRA_RECONCILED', facts={'merge_confirmed': True, 'closeout_valid': True},
            binding={'issue_id': fixture.ISSUE}, issue_type='LEAF', state='MERGED', producer_id='controller',
            run_id=case.started['run_id'], now=fixture.NOW, evidence=case.started['evidence'], transition_id='31',
            merge_result_id='00000000-0000-4000-8000-000000000043',
            **{key: case.adapters[key] for key in ('read_current_status', 'write_transition',
                'read_transition', 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')})
        self.assertIn('owner_closure', result, result)
        value = {key: result[key] for key in ('record', 'owner_closure')}
        sub = case.project / 'application'
        sub.mkdir()
        case.store.worktree_roots = [sub]
        case.store.accepted_policy_root = sub
        with self.assertRaisesRegex(ValidationError, RULE):
            controller.validate_jira_owner_closure(value, case.request(result), now=fixture.NOW,
                                                  store=case.store, config=case.config)
        with self.assertRaisesRegex(ValidationError, RULE), retained_scope(case.store, case.config, value):
            self.fail('Retained adapter acquired disallowed policy')
        self.assertTrue(all(call[0] == 'GET' for call in case.http.calls))
