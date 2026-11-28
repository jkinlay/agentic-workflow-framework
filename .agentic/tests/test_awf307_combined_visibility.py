"""O2 replay: additive GitHub/Jira warnings, closed schemas and durable delivery."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_awf307_provider_owner_policy import ProviderPolicyFixture, NOW
import test_awf307_jira_visibility as visibility
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.continuous_controller import ContinuousControllerStore
from agentic.digest import render
from agentic.shared_owner_policy import shared_owner_warnings

GH = 'SHARED_OWNER_AUTOMATION_ACCOUNT'
JIRA = 'SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT'
GH_KEY = 'shared_owner_automation_actor_id'
JIRA_KEY = 'shared_owner_controller_actor_id'
ACCOUNT = 'fixture-shared-controller'


class CombinedVisibilityTests(ProviderPolicyFixture):
    command = visibility.JiraVisibilityTests.command
    status = visibility.JiraVisibilityTests.status

    def setUp(self):
        super().setUp()
        self.enterContext(patch('agentic.shared_owner_policy._has_git_metadata', return_value=True))
        (self.project / '.agentic').mkdir()
        self.configure(True, True)

    def configure(self, github, jira):
        self.config['merge_gate'].update(
            trusted_owner_ids=[1001, 1002] if github else [1001],
            **{GH_KEY: 1002 if github else None})
        self.config['jira'].update(controller_actor_id=ACCOUNT,
                                   **{JIRA_KEY: ACCOUNT if jira else None})
        self.accepted = deepcopy(self.config)
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
        (self.project / '.agentic/workflow.yaml').write_bytes(canonical(self.workflow))

    def assert_composed(self, warnings, github, jira):
        self.assertEqual(len(warnings), len({json.dumps(item, sort_keys=True) for item in warnings}))
        codes = [item['code'] if isinstance(item, dict) else
                 GH if GH in item else JIRA if JIRA in item else '' for item in warnings]
        self.assertEqual(github, GH in codes)
        self.assertEqual(jira, JIRA in codes)
        if github and jira:
            self.assertLess(codes.index(GH), codes.index(JIRA))

    def test_every_command_composes_both_domains_and_each_single_domain(self):
        for github, jira in ((False, False), (True, False), (False, True), (True, True)):
            self.configure(github, jira)
            for name in ('validate-config', 'verify-installation', 'verify-authorization', 'doctor', 'status'):
                with self.subTest(github=github, jira=jira, command=name):
                    with patch('agentic.providers.github_status.installed_bytes',
                               side_effect=ValidationError('fixture integrity refusal')):
                        code, out, err = self.command(name)
                    payload = json.loads(out or err)
                    self.assert_composed(payload.get('warnings', []), github, jira)
            with self.policy_reads():
                body = render({'ticket': 'EX-42', 'awf_state': 'IN_PROGRESS'},
                              owner_config=self.config, accepted_policy_root=self.project)
            lines = [line for line in body.splitlines() if line.startswith('WARNING:')]
            self.assert_composed(lines, github, jira)

    def test_refusals_keep_both_warnings_in_order_without_duplicates(self):
        from agentic import cli
        for github, jira in ((False, False), (True, False), (False, True), (True, True)):
            self.configure(github, jira)
            for name in ('validate-config', 'verify-installation'):
                with self.subTest(github=github, jira=jira, command=name):
                    out, err = io.StringIO(), io.StringIO()
                    with self.policy_reads(), redirect_stdout(out), redirect_stderr(err), \
                            patch.object(cli, 'verify_installed',
                                         side_effect=ValidationError('fixture integrity refusal')):
                        code = cli.main(['--root', str(self.project), name])
                    self.assertEqual(2, code)
                    self.assert_composed(json.loads(err.getvalue()).get('warnings', []), github, jira)
            code, out, err = self.command('verify-authorization', failure=True)
            self.assertEqual(2, code)
            self.assert_composed(json.loads(err).get('warnings', []), github, jira)

    def test_external_supplied_configuration_does_not_probe_unrelated_repository(self):
        from agentic.jira_owner_visibility import jira_owner_warnings
        with tempfile.TemporaryDirectory() as outer:
            supplied = Path(outer) / 'proposed.json'
            supplied.write_bytes(canonical(self.config))
            with patch('agentic.shared_owner_policy._default_ref',
                       side_effect=AssertionError('Unrelated repository must not be read')):
                warnings = jira_owner_warnings(self.project, config_path=supplied)
        self.assertTrue(warnings)
        self.assertTrue(all(item['code'] == JIRA for item in warnings))
        self.assertTrue(all('not authority' in item['message'] for item in warnings))

    def test_both_warning_domains_validate_and_remain_closed(self):
        with tempfile.TemporaryDirectory() as outer, self.policy_reads():
            store = ContinuousControllerStore(Path(outer) / 'controller.sqlite', ['A'],
                                             accepted_policy_root=self.project)
            value = store.digest(NOW, owner_config=self.config)
        self.assert_composed(value['owner_policy_warnings'], True, True)
        self.assertEqual(3, len(value['owner_policy_warnings']))
        self.contracts.validate('controller-status-digest', value)
        for mutate in (
            lambda w: w[0].update(unexpected=True),
            lambda w: w[0].update(code=JIRA),
            lambda w: w.append({**w[-1], 'message': 'fourth distinct warning'}),
        ):
            invalid = deepcopy(value)
            mutate(invalid['owner_policy_warnings'])
            with self.assertRaises(ValidationError):
                self.contracts.validate('controller-status-digest', invalid)

    def test_github_only_warning_supersedes_pending_digest_and_survives_restart(self):
        later = '2026-10-03T10:05:00Z'
        with tempfile.TemporaryDirectory() as outer:
            path = Path(outer) / 'controller.sqlite'
            store = ContinuousControllerStore(path, ['A'], accepted_policy_root=self.project)
            self.configure(False, False)
            with self.policy_reads():
                prior = store.digest(NOW, owner_config=self.config, accepted_policy_root=self.project)
            self.configure(True, False)
            with self.policy_reads():
                current = store.digest(later, owner_config=self.config, accepted_policy_root=self.project)
            self.assert_composed(current['owner_policy_warnings'], True, False)
            self.assertEqual(prior['delivery_id'], current['supersedes_delivery_id'])
            self.assertEqual(prior['observed_at'], current['observed_at'])
            self.assertEqual(prior['streams'], current['streams'])
            self.assertEqual(later, current['owner_policy_observed_at'])
            self.assertNotIn('jira_owner_policy_observed_at', current)
            self.contracts.validate('controller-status-digest', current)
            store = ContinuousControllerStore(path, ['A'], accepted_policy_root=self.project)
            with self.policy_reads():
                self.assertEqual(current, store.digest(later, owner_config=self.config,
                                                       accepted_policy_root=self.project))
            with self.assertRaisesRegex(ValidationError, 'owner policy warning observation'):
                store.acknowledge_digest(current['delivery_id'], NOW)
            self.assertEqual(prior, store.acknowledge_digest(prior['delivery_id'], NOW))
            self.assertEqual(current, store.acknowledge_digest(current['delivery_id'], later))

    def test_supersession_restart_preserves_both_domains_and_historical_bytes(self):
        later = '2026-10-03T10:05:00Z'
        newest = '2026-10-03T10:06:00Z'
        with tempfile.TemporaryDirectory() as outer:
            path = Path(outer) / 'controller.sqlite'
            store = ContinuousControllerStore(path, ['A'], accepted_policy_root=self.project)
            self.configure(False, False)
            with self.policy_reads():
                prior = store.digest(NOW, owner_config=self.config)
            prior_raw = canonical(prior)
            self.configure(True, True)
            self.commit = 'b' * 40
            with self.policy_reads():
                successor = store.digest(later, owner_config=self.config,
                                         accepted_policy_root=self.project)
            successor_raw = canonical(successor)
            self.assert_composed(successor['owner_policy_warnings'], True, True)
            self.assertEqual(prior['observed_at'], successor['observed_at'])
            self.assertEqual(prior['streams'], successor['streams'])
            self.assertEqual(later, successor['jira_owner_policy_observed_at'])
            self.assertEqual(prior['delivery_id'], successor['supersedes_delivery_id'])
            store = ContinuousControllerStore(path, ['A'], accepted_policy_root=self.project)
            with self.policy_reads():
                self.assertEqual(successor_raw, canonical(store.digest(later, owner_config=self.config,
                                                       accepted_policy_root=self.project)))
            with self.assertRaisesRegex(ValidationError, 'Jira policy warning observation'):
                store.acknowledge_digest(successor['delivery_id'], NOW)
            self.assertEqual(prior_raw, canonical(store.acknowledge_digest(prior['delivery_id'], NOW)))
            self.config['project']['name'] = 'Fixture policy revision'
            self.configure(True, True)
            self.commit = 'c' * 40
            with self.policy_reads():
                current = store.digest(newest, owner_config=self.config, accepted_policy_root=self.project)
            self.assert_composed(current['owner_policy_warnings'], True, True)
            self.assertEqual(successor['delivery_id'], current['supersedes_delivery_id'])
            self.assertEqual(successor_raw, canonical(store.acknowledge_digest(successor['delivery_id'], later)))
            self.assertEqual(current, store.acknowledge_digest(current['delivery_id'], newest))
            for value in (prior, successor, current):
                self.contracts.validate('controller-status-digest', value)


class CombinedLateAlertTests(unittest.TestCase):
    setUp = visibility.LateAlertVisibilityTests.setUp
    observe_ready = visibility.LateAlertVisibilityTests.observe_ready
    observe_candidate = visibility.LateAlertVisibilityTests.observe_candidate
    deliver = visibility.LateAlertVisibilityTests.deliver
    readback = visibility.LateAlertVisibilityTests.readback
    cycle = visibility.LateAlertVisibilityTests.cycle

    def test_late_alert_keeps_config_and_root_for_both_domains(self):
        root = Path(self.temp.name) / 'project'
        warnings = shared_owner_warnings({'merge_gate': {GH_KEY: 1002},
                                         'jira': {JIRA_KEY: ACCOUNT}})
        seen = []
        def github(config, actor_id, policy_root=None):
            seen.append((deepcopy(config), policy_root))
            return {'accepted_owner_policy': {'warnings': warnings}}
        def jira(policy_root=None, config=None, **kwargs):
            return [warnings[1]] if policy_root is not None and Path(policy_root).resolve() == root.resolve() else []
        self.negative_delivery = True
        with patch('agentic.shared_owner_policy.configured_shared_owner_actor_id', return_value=1002),                 patch('agentic.continuous_controller._controller_owner_evidence', side_effect=github),                 patch('agentic.jira_owner_visibility.jira_owner_warnings', side_effect=jira):
            self.cycle('2026-11-09T07:00:00Z')
            result = self.cycle('2026-11-09T08:00:00Z')
        self.assertTrue(result['review_batching']['owner_alerts'])
        alerts = [item for item in self.status_digests if item['review_batching']['owner_alerts']]
        self.assertTrue(alerts)
        for alert in alerts:
            self.assertEqual(warnings, alert['owner_policy_warnings'])
        self.assertTrue(seen)
        for config, policy_root in seen:
            self.assertIsInstance(config, dict)
            self.assertEqual(root.resolve(), Path(policy_root).resolve())


if __name__ == '__main__':
    unittest.main()
