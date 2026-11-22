"""Null automation identity preserves ordinary and distinct-owner behavior."""
from copy import deepcopy
import inspect
from unittest.mock import patch

import test_awf307_excluded_refusal_proofs as proofs
from agentic import ValidationError, provenance, review_policy, review_tiers
from agentic.canonical import canonical


class NullOwnerIdentityTests(proofs.ProviderPolicyFixture):
    record = proofs.ProvenanceRefusalTests.record
    projection = proofs.ProvenanceRefusalTests.projection
    approval = proofs.RoundRefusalTests.approval
    def driver(self, actor):
        driver = proofs.HostProjectionRefusalTests.driver(self, actor)
        driver.c['trusted_owner_ids'] = deepcopy(self.accepted['merge_gate']['trusted_owner_ids'])
        return driver

    def null_account(self):
        self.accepted['github'].update(expected_actor_id=None, auth_profile='fixture')
        self.accepted['merge_gate']['shared_owner_automation_actor_id'] = None
        self.accepted['merge_gate']['trusted_owner_ids'] = [1001]
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        self.config = deepcopy(self.accepted)

    def test_null_projection_verifies_distinct_owner(self):
        self.null_account()
        with self.policy_reads():
            self.assertIsNone(provenance._accepted_owner_config(self.project)['expected_actor_id'])
            self.assertEqual(1001, provenance.verify_owner_override(self.record(1001),
                reviewed_owner_config=self.projection(), accepted_policy_root=self.project)['who'])

    def test_null_provider_projection_verifies_distinct_owner(self):
        self.null_account()
        with self.policy_reads():
            driver = self.driver(1001)
            options = ({'owner_authority': True} if 'owner_authority' in inspect.signature(
                driver.collect_provenance_observation).parameters else {})
            observed = driver.collect_provenance_observation(**options)
            self.assertEqual(91, provenance.verify_provider_owner_override(self.record(1001),
                observed, accepted_policy_root=self.project)['provider_comment_id'])

    def test_null_overcap_round_approval_preserves_success(self):
        self.null_account()
        with self.policy_reads():
            self.assertTrue(review_policy.authorize_tier3_round(4, 3,
                pr_comments=[self.approval(1001)], trusted_owner_ids=[1001],
                round_started_at='2026-10-03T10:00:00Z', accepted_policy_root=self.project))

    def test_null_dispositions_preserve_every_event(self):
        self.null_account()
        for decision in review_policy.CAP_DECISIONS:
            with self.subTest(decision=decision), self.policy_reads():
                result = review_policy.cap_disposition_plan(self.config,
                    {'decision': decision, 'open_finding_ids': [],
                     'owner_source': {'actor_id': 1001}, 'successor_ticket': 'EX-99'},
                    3, 0, [], accepted_policy_root=self.project)
                self.assertEqual(review_policy.CAP_EVENTS[decision], result['event'])

    def extension(self, actor=1001):
        return {'decision': 'EXTEND_ONE_CYCLE', 'record_id': 'fixture-owner-record',
            'created_at': '2026-10-03T10:00:00Z', 'producer_id': 'fixture-verifier',
            'run_id': 'fixture-run', 'binding': {}, 'open_finding_ids': [],
            'notes': 'Fixture extension', 'cycles': 3, 'cap_extensions': 1,
            'successor_ticket': None, 'authorization_request_id': 'fixture-request',
            'owner_source': {'channel': 'github_pr_comment', 'comment_id': 91, 'actor_id': actor},
            'evidence': ['urn:awf:fixture:owner-record']}

    def test_null_tier_two_extension_preserves_success(self):
        self.null_account()
        with self.policy_reads():
            self.assertTrue(review_tiers.validate_round(2, 4,
                owner_cap_disposition=self.extension(), accepted_policy_root=self.project))

    def test_null_merge_train_owner_review_preserves_success(self):
        self.null_account()
        from agentic import merge_train
        from test_merge_train import MergeTrainTests, NOW
        fixture = MergeTrainTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.addCleanup(fixture.doCleanups)
        source = fixture._source()
        with self.policy_reads():
            self.assertTrue(merge_train._validate_admission_evidence(source,
                fixture._evidence(source, reviewer_id=1001), [1001], now=NOW,
                max_age_seconds=300, accepted_policy_root=self.project))

    def ordinary_observation(self, consumer):
        self.null_account()
        from agentic.provider_identity import github_identity_preflight
        self.assertEqual('PASS', github_identity_preflight(self.config, self.observation)['status'])
        driver = self.driver(1001)
        for context in ('ordinary', 'missing_origin_head', 'other_origin_form'):
            with self.subTest(context=context), self.policy_reads():
                if context == 'missing_origin_head':
                    with patch('agentic.shared_owner_policy._default_ref', return_value=(None, 'unobserved')):
                        self.assertEqual(101, getattr(driver, consumer)()['repository_id'])
                elif context == 'other_origin_form':
                    with patch('agentic.shared_owner_policy._origin_url', return_value='ssh://git@github.com/fixture/example.git'):
                        self.assertEqual(101, getattr(driver, consumer)()['repository_id'])
                else:
                    self.assertEqual(101, getattr(driver, consumer)()['repository_id'])

    def test_ordinary_provenance_observation_needs_no_owner_authority(self):
        self.ordinary_observation('collect_provenance_observation')

    def test_ordinary_default_observation_needs_no_owner_authority(self):
        self.ordinary_observation('collect_default_branch_observation')

    def test_configured_merge_train_login_cannot_import_owner_authority(self):
        from agentic import merge_train
        from test_merge_train import MergeTrainTests, NOW
        fixture = MergeTrainTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.addCleanup(fixture.doCleanups)
        source = fixture._source()
        self.accepted['github']['expected_actor_login'] = 'fixture-owner'
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        with self.policy_reads():
            with self.assertRaisesRegex(ValidationError, 'OWNER_TRUST_UNBOUND|trusted owner'):
                merge_train._validate_admission_evidence(source,
                    fixture._evidence(source, reviewer_id='fixture-owner'), ['fixture-owner'],
                    now=NOW, max_age_seconds=300, accepted_policy_root=self.project)
            self.assertTrue(merge_train._validate_admission_evidence(source,
                fixture._evidence(source, reviewer_id=1001), [1001], now=NOW,
                max_age_seconds=300, accepted_policy_root=self.project))

    def test_null_identity_does_not_hide_missing_projection_field(self):
        self.null_account()
        projection = self.projection()
        projection.pop('expected_actor_id')
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, 'missing expected_actor_id'):
            provenance.verify_owner_override(self.record(1001), reviewed_owner_config=projection,
                accepted_policy_root=self.project)

    def test_rootless_queue_scopes_only_registered_accepted_owner_identity(self):
        import test_integration_queue as fixtures
        from agentic import gates
        config = deepcopy(fixtures.PROJECT_CONFIG)
        config['github'].update(expected_actor_id=None, auth_profile='fixture')
        config['merge_gate']['shared_owner_automation_actor_id'] = None
        real_evaluate = gates.evaluate
        checked = []

        def evaluate(*args, **kwargs):
            checked.append(provenance._accepted_owner_config(None)['expected_actor_id'])
            self.assertTrue(review_policy.authorize_tier3_round(4, 3,
                pr_comments=[self.approval(1001)], trusted_owner_ids=[1001],
                round_started_at='2026-10-03T10:00:00Z'))
            self.assertTrue(review_tiers.validate_round(2, 4,
                owner_cap_disposition=self.extension()))
            for decision in review_policy.CAP_DECISIONS:
                result = review_policy.cap_disposition_plan(args[0],
                    {'decision': decision, 'open_finding_ids': [],
                     'owner_source': {'actor_id': 1001}, 'successor_ticket': 'EX-99'}, 3, 0, [])
                self.assertEqual(review_policy.CAP_EVENTS[decision], result['event'])
            return real_evaluate(*args, **kwargs)

        with patch.object(fixtures, 'PROJECT_CONFIG', config):
            (state, bundle, gate, request, authorization, delta, reviewed, contracts,
             registry, delta_registry, mode_registry) = fixtures.trusted_readiness()
            with patch.object(gates, 'evaluate', side_effect=evaluate):
                result = fixtures.integration_status(state, 7, bundle, gate, request,
                    authorization, delta, config=reviewed, contracts=contracts,
                    posting_collector_registry=registry, delta_review_observer_registry=delta_registry,
                    mode_observer_registry=mode_registry, now=fixtures.GATE_NOW)
            self.assertEqual('success', result['status'], result)
        self.assertEqual([None], checked)
        with self.assertRaisesRegex(ValidationError, 'accepted_policy_root'):
            provenance._accepted_owner_config(None)

    def test_fixture_projection_narrows_without_rewriting_accepted_owners(self):
        from accepted_owner_fixture import AcceptedOwnerFixture
        fixture = AcceptedOwnerFixture(self, owners=(1001, 1002))
        accepted = deepcopy(fixture.config)
        projection = fixture.projection((1001,))
        self.assertEqual(accepted, fixture.config)
        self.assertEqual([1001], projection['trusted_owner_ids'])
        self.assertEqual([1001, 1002], fixture.config['merge_gate']['trusted_owner_ids'])

    def test_rootless_gate_round_four_uses_registered_accepted_bytes(self):
        import test_integration_queue as queue_fixtures
        import test_review_tiers_195_overcap as gate_fixtures
        from agentic import integration_queue, gates
        fixture = gate_fixtures.Tier3OvercapGateTests('runTest')
        fixture.setUpClass()
        gate_fixtures.Fixture.setUp(fixture)
        self.addCleanup(fixture.doCleanups)
        fixture.config['github'].update(expected_actor_id=None, auth_profile='fixture')
        from accepted_owner_fixture import AcceptedOwnerFixture
        fixture.owner_fixture = AcceptedOwnerFixture(fixture, owners=(1001,), actor=None)
        gate_fixtures.gate_fixtures.ReviewTiers195GateTests._tier_three_candidate(fixture)
        gate_fixtures.gate_fixtures.ReviewTiers195GateTests._review_history(
            fixture, 4, historical_tier=3, terminal_tier=3)
        registry = {}
        queue_fixtures.register_integration_observer('fixture-accepted-owner',
            lambda request: queue_fixtures.accepted_config_result(fixture.config), registry,
            observed_at=gate_fixtures.gate_fixtures.NOW)
        snapshot = {}
        options = ({'owner_observation': snapshot} if 'owner_observation' in inspect.signature(
            integration_queue._accepted_project_config).parameters else {})
        integration_queue._accepted_project_config(fixture.config,
            {'producer_id': 'fixture-accepted-owner'}, registry,
            now=gate_fixtures.gate_fixtures.NOW, return_attestation=True, **options)
        real_evaluate = gates.evaluate

        def rootless(config, workflow, bundle, contracts, now, **kwargs):
            kwargs.pop('accepted_policy_root', None)
            if hasattr(provenance, '_provider_owner_authority'):
                with provenance._provider_owner_authority(config, snapshot, registry, now=now):
                    return real_evaluate(config, workflow, bundle, contracts, now, **kwargs)
            return real_evaluate(config, workflow, bundle, contracts, now, **kwargs)

        with patch.object(gate_fixtures, 'evaluate', side_effect=rootless):
            result = fixture.gate(fixture.authenticated_approval)
        self.assertEqual('READY_FOR_OWNER_AUTHORIZATION', result['conclusion'])

    def test_null_default_owner_projection_always_carries_identity_field(self):
        self.null_account()
        driver = self.driver(1001)
        options = ({'owner_authority': True} if 'owner_authority' in inspect.signature(
            driver.collect_default_branch_observation).parameters else {})
        with self.policy_reads():
            observed = driver.collect_default_branch_observation(**options)
            self.assertIn('expected_actor_id', observed)
            self.assertIsNone(observed['expected_actor_id'])
            projection = provenance._provider_owner_config(observed, accepted_policy_root=self.project)
            self.assertEqual(1001, provenance.verify_owner_override(self.record(1001),
                reviewed_owner_config=projection, accepted_policy_root=self.project)['who'])

    def test_registered_rootless_identity_refuses_shared_with_distinct_control(self):
        from contextlib import nullcontext
        import test_integration_queue as fixtures
        from agentic import integration_queue
        config = deepcopy(self.accepted)
        config['github']['expected_actor_id'] = 1002
        config['merge_gate'].update(trusted_owner_ids=[1001, 1002],
                                    shared_owner_automation_actor_id=1002)
        registry = {}
        producer = fixtures.register_integration_observer('fixture-rootless-separation',
            lambda request: fixtures.accepted_config_result(config), registry,
            observed_at='2026-10-03T10:00:00Z',
            provider_identity={key: config['github'][key]
                               for key in ('host', 'repository', 'repository_id')},
            repository_identity={key: config['github'][key]
                                 for key in ('repository', 'repository_id')})
        request = {'operation': 'ACCEPTED_PROJECT_CONFIG',
            'source_identity': {'kind': 'installed_project_configuration',
                                'path': '.agentic/PROJECT_CONFIG.yaml'}}
        result, attestation = integration_queue._registered_provider_observation(
            registry, producer, request, 'accepted owner policy',
            now='2026-10-03T10:00:00Z',
            max_age=config['validation']['max_evidence_age_seconds'],
            return_attestation=True)
        snapshot = {'request': request, 'result': result, 'observation': attestation}
        scope = (provenance._provider_owner_authority(config, snapshot, registry,
            now='2026-10-03T10:00:00Z')
            if hasattr(provenance, '_provider_owner_authority') else nullcontext())
        with scope:
            for actor in (1001, 1002):
                operations = [
                    ('round', lambda: review_policy.authorize_tier3_round(4, 3,
                        pr_comments=[self.approval(actor)], trusted_owner_ids=[1001, 1002],
                        round_started_at='2026-10-03T10:00:00Z'), True),
                    ('extension', lambda: review_tiers.validate_round(2, 4,
                        owner_cap_disposition=self.extension(actor)), True),
                ]
                for decision in review_policy.CAP_DECISIONS:
                    operations.append((decision,
                        lambda decision=decision: review_policy.cap_disposition_plan(config,
                            {'decision': decision, 'open_finding_ids': [],
                             'owner_source': {'actor_id': actor}, 'successor_ticket': 'EX-99'},
                            3, 0, [])['event'], review_policy.CAP_EVENTS[decision]))
                for path, operation, expected in operations:
                    with self.subTest(path=path, actor=actor):
                        if actor == 1002:
                            with self.assertRaisesRegex(ValidationError, proofs.SEPARATION):
                                operation()
                        else:
                            self.assertEqual(expected, operation())
