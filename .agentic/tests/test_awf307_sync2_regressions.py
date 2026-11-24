"""SYNC-2 gaps: emitted schemas, preserved journals and independent opt-outs."""
from copy import deepcopy
import json
from pathlib import Path
import runpy
from unittest.mock import patch

GH = "SHARED_OWNER_AUTOMATION_ACCOUNT"
JIRA = "SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT"
from test_awf307_provider_owner_policy import ProviderPolicyFixture
from test_awf307_jira_owner_policy import JiraFixture, ISSUE, NOW
from agentic import continuous_controller as controller
from agentic.canonical import canonical, sha256


class SyncVisibilityTests(ProviderPolicyFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(patch('agentic.shared_owner_policy._has_git_metadata', return_value=True))
        self.config['merge_gate'].update(trusted_owner_ids=[1001,1002], shared_owner_automation_actor_id=1002)
        self.config['jira'].update(controller_actor_id='fixture-shared-controller', shared_owner_controller_actor_id='fixture-shared-controller')
        self.accepted = deepcopy(self.config)
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        (self.project / '.agentic').mkdir()
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        (self.project / '.agentic/workflow.yaml').write_bytes(canonical(self.workflow))

    def status(self):
        from agentic.providers.github_status import project_status
        from agentic import ValidationError
        with self.policy_reads(), patch('agentic.providers.github_status.installed_bytes', side_effect=ValidationError('fixture integrity refusal')):
            return project_status(self.project)

    def test_activation_warnings_fit_closed_nested_schema(self):
        report = self.status()
        self.assertIn(GH, json.dumps(report['activation']))
        self.assertIn(JIRA, json.dumps(report['activation']))
        self.contracts.validate('activation-status', report)
        invalid = deepcopy(report)
        invalid['activation']['unexpected'] = True
        from agentic import ValidationError
        with self.assertRaises(ValidationError):
            self.contracts.validate('activation-status', invalid)

class SyncJiraTests(JiraFixture):
    def configure_accounts(self, github, jira):
        self.config['merge_gate'].update(shared_owner_automation_actor_id=1002 if github else None,
                                        trusted_owner_ids=[1001, 1002] if github else [7777])
        self.config['jira']['shared_owner_controller_actor_id'] = 'controller' if jira else None
        self.accept()
        project_path = Path(self.adapter_config['observations']['accepted_project_config']['path'])
        project_path.write_bytes(canonical(self.config))
        self.adapter_config['observations']['accepted_project_config']['sha256'] = sha256(project_path.read_bytes())
        ns = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'adapters/reference_controller_adapter.py'))
        self.adapters = ns['build_adapters']({**self.adapter_config, '_http_transport': self.http,
                                            '_clock': self.clock, '_runner': __import__('test_reference_controller_adapter').FakeRunner()})

    def ordinary_write(self, readback_problem=None):
        self.configure_accounts(True, False)
        self.http.owner_done = False
        order = []
        attempted = []
        ports = {key: self.adapters[key] for key in ('read_current_status', 'write_transition', 'read_transition',
                 'observe_provider_identity', 'observe_issue_identity', 'observe_issue_policy')}
        for key, label in (('read_current_status', 'before'), ('write_transition', 'post'), ('read_transition', 'after')):
            original = ports[key]
            def observe(*args, _original=original, _label=label, **kwargs):
                order.append(_label)
                if _label == 'post':
                    attempted.append(True)
                if _label == 'after' and readback_problem == 'unknown':
                    raise TimeoutError('fixture unknown outcome')
                result = _original(*args, **kwargs)
                if _label == 'after' and readback_problem == 'mismatch':
                    return {**result, 'status': 'foreign-status'}
                return result
            ports[key] = observe
        def invoke():
            with self.policy_reads():
                return controller.production_jira_lifecycle(self.store, config=self.config, contract=self.contract,
                    event='WORKER_STARTED', facts={'run_registered': True, 'worktree_verified': True},
                    binding={'issue_id': ISSUE}, issue_type='LEAF', state='DISPATCHED', producer_id='controller',
                    run_id=self.started['run_id'], now=NOW, evidence=self.started['evidence'], transition_id='21', **ports)
        result = invoke()
        self.assertEqual(['before', 'post', 'after'], order, result)
        if readback_problem is None:
            self.assertEqual('SUCCEEDED', result['record']['status'])
        else:
            invoke()
            self.assertEqual(1, len(attempted))
            self.assertNotEqual('SUCCEEDED', result['record']['status'])

    def test_github_only_opt_out_preserves_ordinary_jira_write_and_readback(self):
        self.ordinary_write()

    def test_github_only_readback_mismatch_stops_without_reposting(self):
        self.ordinary_write('mismatch')

    def test_github_only_unknown_readback_stops_without_reposting(self):
        self.ordinary_write('unknown')

    def test_github_project_context_does_not_enable_shared_jira_closure(self):
        from agentic.jira_owner_policy import project_policy_scope, project_context_available, context_available
        self.configure_accounts(True, False)
        with self.policy_reads(), project_policy_scope(self.store, self.config):
            self.assertTrue(project_context_available())
            self.assertFalse(context_available())
        self.assertFalse(project_context_available())
        self.assertFalse(context_available())

    def test_github_project_context_requires_observed_default_policy(self):
        from agentic.jira_owner_policy import project_policy_scope
        from agentic import ValidationError
        self.configure_accounts(True, False)
        self.ref = None
        with self.policy_reads(), self.assertRaises(ValidationError):
            with project_policy_scope(self.store, self.config):
                self.fail('Unobserved shared policy cannot enter the project context')

    def historical_held_intent(self, absent=False):
        self.configure_accounts(False, False)
        if absent:
            self.config['jira'].pop('shared_owner_controller_actor_id')
            self.accept()
        self.held_batch()
        with self.store.connection() as db:
            markers = db.execute("SELECT key FROM controller_meta WHERE key LIKE 'jira-held-before-write:%'").fetchall()
        self.assertEqual([], markers)

    def test_jira_null_held_intent_keeps_historical_journal_bytes(self):
        self.historical_held_intent()

    def test_jira_absent_held_intent_keeps_historical_journal_bytes(self):
        self.historical_held_intent(absent=True)
