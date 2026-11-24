"""Jira shared-account visibility at production report entry points."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_awf307_provider_owner_policy import ProviderPolicyFixture, NOW
from test_awf307_jira_owner_policy import JiraFixture, FIELD
from agentic import ValidationError, cli
from agentic.canonical import canonical, load
from agentic.continuous_controller import ContinuousControllerStore
from agentic.digest import render
from agentic.providers.github_status import project_status, render_status
from agentic.runtime_commands import command_catalog, render_doctor

KEY = 'shared_owner_controller_actor_id'
CODE = 'SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT'
ACCOUNT = 'fixture-shared-controller'


class JiraVisibilityTests(ProviderPolicyFixture):
    def setUp(self):
        super().setUp()
        self.config['merge_gate'].update(shared_owner_automation_actor_id=None, trusted_owner_ids=[1001])
        self.config['jira'].update(controller_actor_id=ACCOUNT, **{KEY: ACCOUNT})
        self.accept()
        metadata = patch('agentic.shared_owner_policy._has_git_metadata', return_value=True)
        metadata.start()
        self.addCleanup(metadata.stop)
        folder = self.project / '.agentic'
        folder.mkdir()
        (folder / 'PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        (folder / 'workflow.yaml').write_bytes(canonical(self.workflow))

    def accept(self):
        self.accepted = deepcopy(self.config)
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)

    def assert_warning(self, value):
        body = value if isinstance(value, str) else json.dumps(value)
        self.assertIn(CODE, body)
        self.assertIn(ACCOUNT, body)
        self.assertNotIn('SHARED_OWNER_AUTOMATION_ACCOUNT', body)
        return body

    def command(self, name, *, supplied=False, failure=False, human=False):
        config = self.project / '.agentic/PROJECT_CONFIG.yaml'
        argv = ['--root', str(self.project), name]
        if name == 'verify-authorization':
            argv += ['--config', str(config), '--record', str(config), '--request', str(config), '--gate', str(config)]
        if name == 'validate-config' and supplied:
            argv += ['--config', str(config)]
        if name in ('doctor', 'status') and not human:
            argv += ['--json']
        out, err = io.StringIO(), io.StringIO()
        with self.policy_reads(), patch.object(cli, 'verify_installed', return_value='f' * 64), \
                patch.object(cli, 'Contracts', return_value=self.contracts), \
                patch.object(cli, 'verify_record', side_effect=ValidationError('fixture refusal') if failure else None,
                             return_value={'execution_authority': False}), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_validate_config(self):
        self.assert_warning(self.command('validate-config')[1])

    def test_verify_installation(self):
        self.assert_warning(self.command('verify-installation')[1])

    def test_verify_authorization(self):
        self.assert_warning(self.command('verify-authorization')[1])

    def test_verify_authorization_refusal_is_visible(self):
        code, out, err = self.command('verify-authorization', failure=True)
        self.assertEqual(code, 2)
        self.assert_warning(err)

    def test_doctor_human_and_json(self):
        for human in (False, True):
            with self.subTest(human=human):
                self.assert_warning(self.command('doctor', human=human)[1])

    def status(self):
        with self.policy_reads(), patch('agentic.providers.github_status.installed_bytes',
                                       side_effect=ValidationError('fixture installation unavailable')):
            return project_status(self.project)

    def test_status_and_adoption_activation_human_and_json(self):
        result = self.status()
        self.assert_warning(result)
        self.assert_warning(render_status(result))
        self.assert_warning(result['activation'])
        self.assertIs(result['execution_authority'], False)

    def test_workflow_status_human_and_json(self):
        with patch('agentic.providers.github_status.installed_bytes',
                   side_effect=ValidationError('fixture installation unavailable')):
            for human in (False, True):
                with self.subTest(human=human):
                    self.assert_warning(self.command('status', human=human)[1])

    def test_controller_status_digest_closed_schema_and_replay(self):
        with tempfile.TemporaryDirectory() as outer:
            store = ContinuousControllerStore(Path(outer) / 'controller.sqlite', ['A'],
                                             accepted_policy_root=self.project)
            with self.policy_reads():
                result = store.digest(NOW, owner_config=self.config, accepted_policy_root=self.project)
                self.assert_warning(result)
                self.contracts.validate('controller-status-digest', result)
                self.assertEqual(result, store.digest(NOW, owner_config=self.config,
                                                     accepted_policy_root=self.project))

    def test_owner_digest(self):
        with self.policy_reads(), patch('agentic.cli.load', wraps=load), \
                patch.object(cli, 'Contracts', return_value=self.contracts):
            value = self.project / 'digest.json'
            value.write_bytes(canonical({'state': {'ticket': 'EX-42', 'awf_state': 'IN_PROGRESS'}}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['--root', str(self.project), 'digest', '--input', str(value),
                                           '--ticket', 'EX-42', '--for', 'owner']), 0)
        body = self.assert_warning(out.getvalue())
        self.assertIn('not authority', body)

    def test_null_absence_on_every_surface(self):
        self.config['jira'][KEY] = None
        self.accept()
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        for name in ('validate-config', 'verify-installation', 'doctor', 'verify-authorization', 'status'):
            with self.subTest(surface=name):
                with patch('agentic.providers.github_status.installed_bytes',
                           side_effect=ValidationError('fixture installation unavailable')):
                    result = self.command(name)
                self.assertNotIn(CODE, result[1] + result[2])
        self.assertNotIn(CODE, json.dumps(self.status()))
        with tempfile.TemporaryDirectory() as outer, self.policy_reads():
            store = ContinuousControllerStore(Path(outer) / 'controller.sqlite', ['A'])
            self.assertNotIn(CODE, json.dumps(store.digest(NOW, owner_config=self.config,
                                                         accepted_policy_root=self.project)))
        self.assertNotIn(CODE, render({'ticket': 'EX-42', 'awf_state': 'IN_PROGRESS'}))

    def test_null_accepted_setting_does_not_revalidate_unrelated_policy_for_warning(self):
        from agentic.shared_owner_policy import observe_accepted_owner_policy
        self.config['jira'][KEY] = None
        self.accept()
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        with patch('agentic.shared_owner_policy.observe_accepted_owner_policy',
                   wraps=observe_accepted_owner_policy) as observed:
            code, out, err = self.command('verify-installation')
        self.assertEqual(code, 0, err)
        self.assertNotIn(CODE, out + err)
        observed.assert_not_called()

    def test_supplied_working_and_feature_configuration_never_enables(self):
        self.accepted['jira'][KEY] = None
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        for supplied in (False, True):
            with self.subTest(supplied=supplied):
                body = self.assert_warning(self.command('validate-config', supplied=supplied)[1])
                self.assertIn('not authority', body)
                self.assertNotIn('"enabled": true', body.split(CODE)[-1])
        self.ref = 'refs/remotes/origin/feature'
        with self.policy_reads():
            report = command_catalog(self.project)
        body = self.assert_warning(report)
        self.assertIn('not authority', body)
        self.assertNotIn('Accepted default-branch policy', body)

    def test_accepted_setting_cannot_be_hidden_by_working_config(self):
        self.config['jira'][KEY] = None
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        for name in ('validate-config', 'verify-installation', 'verify-authorization', 'doctor'):
            with self.subTest(surface=name):
                self.assert_warning(self.command(name)[1])

    def test_catalog_library_human_and_json(self):
        with self.policy_reads():
            report = command_catalog(self.project)
        self.contracts.validate('doctor-output', report)
        self.assert_warning(report)
        self.assert_warning(render_doctor(report))

    def test_controller_warning_survives_an_older_pending_delivery(self):
        with tempfile.TemporaryDirectory() as outer:
            store = ContinuousControllerStore(Path(outer) / 'controller.sqlite', ['A'],
                                             accepted_policy_root=self.project)
            self.config['jira'][KEY] = None
            self.accept()
            (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
            with self.policy_reads():
                prior = store.digest(NOW, owner_config=self.config)
            self.assertNotIn(CODE, json.dumps(prior))
            self.config['jira'][KEY] = ACCOUNT
            self.accept()
            (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
            with self.policy_reads():
                result = store.digest(NOW, owner_config=self.config)
                self.assert_warning(result)
                self.contracts.validate('controller-status-digest', result)
                self.assertEqual(result['supersedes_delivery_id'], prior['delivery_id'])
                self.assertNotEqual(result['delivery_id'], prior['delivery_id'])
                self.assertEqual(store.digest(NOW, owner_config=self.config), result)
                self.assertEqual(store.acknowledge_digest(prior['delivery_id'], NOW), prior)
                self.assertEqual(store.acknowledge_digest(result['delivery_id'], NOW), result)

    def test_pending_warning_preserves_snapshot_time_and_current_policy_time(self):
        later = '2026-10-03T10:05:00Z'
        newest = '2026-10-03T10:06:00Z'
        with tempfile.TemporaryDirectory() as outer:
            store = ContinuousControllerStore(Path(outer) / 'controller.sqlite', ['A'],
                                             accepted_policy_root=self.project)
            self.config['jira'][KEY] = None
            self.accept()
            (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
            with self.policy_reads():
                prior = store.digest(NOW, owner_config=self.config)
            self.config['jira'][KEY] = ACCOUNT
            self.commit = 'b' * 40
            self.accept()
            (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
            with self.policy_reads():
                notice = store.digest(later, owner_config=self.config)
            self.assertEqual(notice['observed_at'], prior['observed_at'])
            self.assertEqual(notice['streams'], prior['streams'])
            self.assertEqual(notice['jira_owner_policy_observed_at'], later)
            self.contracts.validate('controller-status-digest', notice)
            with self.assertRaisesRegex(ValidationError, 'Jira policy warning observation'):
                store.acknowledge_digest(notice['delivery_id'], NOW)
            old_digest = notice['owner_policy_warnings'][0]['message']
            self.config['project']['name'] = 'Fixture policy revision'
            self.commit = 'c' * 40
            self.accept()
            (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
            with self.policy_reads():
                current = store.digest(newest, owner_config=self.config)
                self.assertEqual(store.digest(newest, owner_config=self.config), current)
            self.assertEqual(current['observed_at'], prior['observed_at'])
            self.assertEqual(current['jira_owner_policy_observed_at'], newest)
            self.assertEqual(current['supersedes_delivery_id'], notice['delivery_id'])
            self.assertNotIn(old_digest, [item['message'] for item in current['owner_policy_warnings']])
            self.contracts.validate('controller-status-digest', current)
            self.assertEqual(store.acknowledge_digest(prior['delivery_id'], NOW), prior)
            self.assertEqual(store.acknowledge_digest(notice['delivery_id'], later), notice)
            self.assertEqual(store.acknowledge_digest(current['delivery_id'], newest), current)


class RetainedClosureVisibilityTests(JiraFixture):
    def test_retained_closure_owner_digest_warns_without_enabling(self):
        closure = self.success()['owner_closure']
        evidence = closure[FIELD]
        self.contracts.validate('jira-shared-owner-evidence', evidence)
        state = {'ticket': 'EX-42', 'awf_state': 'DONE', FIELD: evidence}
        original = deepcopy(state)
        kwargs = {'owner_config': self.config} if 'owner_config' in inspect.signature(render).parameters else {}
        body = render(state, **kwargs)
        self.assertIn(CODE, body)
        self.assertIn(closure['actor'], body)
        self.assertIn('not authority', body)
        self.assertIn(evidence['owner_policy_sha256'], body)
        self.assertEqual(state, original)

    def test_retained_closure_with_current_null_keeps_default_digest(self):
        evidence = self.success()['owner_closure'][FIELD]
        self.assertIs(evidence['shared_owner_controller_account'], True)
        state = {'ticket': 'EX-42', 'awf_state': 'DONE', FIELD: evidence}
        original = deepcopy(state)
        default = render({'ticket': 'EX-42', 'awf_state': 'DONE'})
        for config in (None, {'jira': {KEY: None}}):
            with self.subTest(config=config):
                kwargs = {'owner_config': config} if 'owner_config' in inspect.signature(render).parameters else {}
                self.assertEqual(render(state, **kwargs), default)
        self.assertEqual(state, original)


class LateAlertVisibilityTests(unittest.TestCase):
    # Reuse the real production-cycle observers and local delivery port without
    # inheriting or editing the protected review-batching tests.
    import test_awf244_controller_wiring as fixture
    setUp = fixture.ControllerBatchingTests.setUp
    observe_ready = fixture.ControllerBatchingTests.observe_ready
    observe_candidate = fixture.ControllerBatchingTests.observe_candidate
    deliver = fixture.ControllerBatchingTests.deliver
    readback = fixture.ControllerBatchingTests.readback
    cycle = fixture.ControllerBatchingTests.cycle

    def test_late_owner_alert_uses_cycle_policy_root(self):
        from agentic.shared_owner_policy import shared_owner_warnings
        root = Path(self.temp.name) / 'project'
        warning = shared_owner_warnings({'jira': {KEY: ACCOUNT}})[0]

        def observed(policy_root=None, config=None, **kwargs):
            return ([warning] if policy_root is not None
                    and Path(policy_root).resolve() == root.resolve() else [])

        self.negative_delivery = True
        with patch('agentic.jira_owner_visibility.jira_owner_warnings', side_effect=observed):
            self.cycle('2026-11-09T07:00:00Z')
            result = self.cycle('2026-11-09T08:00:00Z')
        self.assertTrue(result['review_batching']['owner_alerts'])
        alerts = [item for item in self.status_digests if item['review_batching']['owner_alerts']]
        self.assertTrue(alerts)
        for alert in alerts:
            self.assertIn(warning, alert.get('owner_policy_warnings', []))
            self.assertNotIn('SHARED_OWNER_AUTOMATION_ACCOUNT', json.dumps(alert))


if __name__ == '__main__':
    unittest.main()
