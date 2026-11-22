"""Host-rooted shared-owner policy through controller retention and planning."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from test_awf307_provider_owner_policy import ProviderPolicyFixture, KEY
from test_continuous_controller_planning import (
    NOW, allowance_request, allowance_observation, allowance_inventory)
import test_continuous_controller_planning as controller_fixtures
import test_planning as planning_fixtures
from agentic import ValidationError, continuous_controller as controller, planning
from agentic.canonical import canonical, fingerprint, loads
from agentic.shared_owner_policy import OWNER_EVIDENCE_FIELDS

SEPARATION = "The configured automation actor must not be a trusted owner"


class ControllerPolicyFixture(ProviderPolicyFixture):
    @contextmanager
    def policy_reads(self, **options):
        # This fixture's immutable Git reader represents a repository; no Git
        # metadata is created or modified by these policy-observation tests.
        with patch('agentic.shared_owner_policy._has_git_metadata', return_value=True), \
                super().policy_reads(**options):
            yield

    def setUp(self):
        super().setUp()
        self.accept()
        self.inventory = allowance_inventory()
        self.request = allowance_request()
        self.observed = allowance_observation()
        for pr in self.observed['pull_requests']:
            pr['head_ref'] = self.config['github']['branch_pattern'].format(
                ticket=pr['ticket'], slug=pr['ticket'].lower())
        self.observed['comment']['actor_id'] = 1002
        if self.consumer == 'replan':
            self.request = {'comment_id': 9001, 'repository_id': '101',
                'prior_inventory_sha256': 'a' * 64, 'next_inventory_sha256': 'b' * 64,
                'nonterminal_dispatch_ids': ['dispatch-1']}
            event = {key: value for key, value in self.request.items() if key != 'comment_id'}
            event['round_id'] = 'round-2'
            self.observed.pop('pull_requests')
            self.observed['comment']['body'] = controller.replan_trigger_command(event)
        self.original = deepcopy(self.observed)

    def accept(self):
        self.accepted = deepcopy(self.config)
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)

    def call(self, rooted=True):
        options = {'accepted_policy_root': self.project} if rooted else {}
        if self.consumer == 'replan':
            return controller.validate_replan_trigger_observation(
                self.config, self.request, self.observed, now=NOW, **options)
        return controller.validate_overlap_allowance_observation(
            self.config, self.request, self.observed, self.inventory, now=NOW,
            expected_repository_id='101', expected_cycle_id='cycle-17', **options)

    def annotation(self, record):
        return record['shared_owner_evidence'] if self.consumer == 'replan' else {
            key: record[key] for key in OWNER_EVIDENCE_FIELDS}

    def reseal(self, record):
        key, domain = (('trigger_sha256', 'controller-replan-trigger') if self.consumer == 'replan'
                       else ('allowance_sha256', 'controller-overlap-allowance-record'))
        record[key] = fingerprint(domain, {k: v for k, v in record.items() if k != key})

    def store(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        return controller.ContinuousControllerStore(Path(state.name) / 'state.sqlite',
            ['A', 'B'], worktree_roots=[self.project])


class ConsumerCases:
    def test_accepted_policy_retains_complete_annotation_without_editing_provider_body(self):
        with self.policy_reads():
            record = self.call()
        annotation = self.annotation(record)
        self.assertIs(annotation['shared_owner_automation_account'], True)
        self.assertEqual(self.commit, annotation['accepted_owner_policy']['default_branch_commit'])
        self.assertEqual(annotation['owner_policy_sha256'], annotation['accepted_owner_policy']['policy_sha256'])
        self.assertIn('1002', annotation['accepted_owner_policy']['warnings'][0]['message'])
        self.assertEqual(self.original, self.observed)

    def test_rootless_overlap_keeps_global_separation_message(self):
        with self.assertRaisesRegex(ValidationError, SEPARATION):
            self.call(rooted=False)

    def test_unset_overlap_keeps_global_separation_message(self):
        self.config['merge_gate'][KEY] = None
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            self.call()

    def test_supplied_and_working_policy_never_enable(self):
        self.accepted['merge_gate'][KEY] = None
        self.accepted['merge_gate']['trusted_owner_ids'] = [1001]
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        for working in (False, True):
            with self.subTest(working=working):
                if working:
                    (self.project / '.agentic').mkdir()
                    (self.project / '.agentic/PROJECT_CONFIG.yaml').write_bytes(canonical(self.config))
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.call()

    def test_feature_missing_and_hidden_policy_refuse(self):
        self.ref = 'refs/remotes/origin/feature'
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

        self.ref = 'refs/remotes/origin/main'
        raw = self.blobs.pop('.agentic/workflow.yaml')
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()
        self.blobs['.agentic/workflow.yaml'] = raw
        self.config['merge_gate'][KEY] = None
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_hidden_setting_and_replaced_or_profile_only_identity_refuse(self):
        self.config['merge_gate'][KEY] = None
        for identity in (1003, None):
            self.config['github']['expected_actor_id'] = identity
            with self.subTest(identity=identity), self.policy_reads(), self.assertRaises(ValidationError):
                self.call()

    def test_unset_default_policy_cannot_be_bypassed_by_relabelling_the_accepted_bot(self):
        self.accepted['merge_gate'][KEY] = None
        self.accepted['merge_gate']['trusted_owner_ids'] = [1001]
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical(self.accepted)
        self.config['merge_gate'][KEY] = None
        self.config['github']['expected_actor_id'] = 1003
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()
        self.blobs['.agentic/PROJECT_CONFIG.yaml'] = canonical([])
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_invalid_relationships_and_account_changes_refuse(self):
        original = deepcopy(self.config)
        for setting in (True, 0, -1, '1002', 1001):
            with self.subTest(setting=setting):
                self.config = deepcopy(original)
                self.config['merge_gate'][KEY] = setting
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.call()
        self.config = deepcopy(original)
        self.config['github']['expected_actor_id'] = 1003
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()
        self.config = deepcopy(original)
        self.config['merge_gate']['trusted_owner_ids'] = [1001]
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_another_account_never_receives_shared_annotation(self):
        self.observed['comment']['actor_id'] = 1001
        with self.policy_reads():
            record = self.call()
        self.assertNotIn('shared_owner_evidence', record)
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(record) - {'owner_policy_sha256'})
        self.observed['comment']['actor_id'] = 1003
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_accepted_policy_cannot_authorize_a_different_numeric_repository(self):
        self.config['github']['repository_id'] = 202
        self.accept()
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_policy_drift_between_observation_and_return_refuses(self):
        with self.policy_reads(drift=True), self.assertRaises(ValidationError):
            self.call()

    def test_provider_identity_freshness_body_and_scope_controls_remain(self):
        original = deepcopy(self.observed)
        mutations = [lambda v: v['comment'].update(updated_at=NOW),
            lambda v: v['comment'].update(body=v['comment']['body'] + ' '),
            lambda v: v.update(repository_id=999),
            lambda v: v['comment'].update(author_association='NONE'),
            lambda v: v.update(provider_observed_at='2026-10-15T07:00:00Z')]
        if self.consumer == 'overlap':
            mutations.extend([lambda v: v['pull_requests'][0].update(head_sha='9' * 40),
                lambda v: v['pull_requests'][0].update(changed_files=['src/other.py'])])
        for mutate in mutations:
            self.observed = deepcopy(original)
            mutate(self.observed)
            with self.subTest(observation=self.observed), self.policy_reads(), self.assertRaises(ValidationError):
                self.call()

    def test_annotation_is_hash_bound(self):
        with self.policy_reads():
            record = self.call()
        before = deepcopy(record)
        self.annotation(record)['accepted_owner_policy']['default_branch_commit'] = 'f' * 40
        self.reseal(record)
        self.assertNotEqual(before, record)
        key = 'trigger_sha256' if self.consumer == 'replan' else 'allowance_sha256'
        self.assertNotEqual(before[key], record[key])


class ReplanPolicyTests(ConsumerCases, ControllerPolicyFixture):
    consumer = 'replan'

    def prepare_boundary(self):
        store = self.store()
        store.schedule(self.inventory[:1], NOW, 1, inventory_sha256='a' * 64)
        store.prepare_dispatches(NOW)
        self.request['nonterminal_dispatch_ids'] = [item['dispatch_id'] for item in store.replan_context()['nonterminal']]
        event = {key: value for key, value in self.request.items() if key != 'comment_id'}
        event['round_id'] = 'round-2'
        self.observed['comment']['body'] = controller.replan_trigger_command(event)
        return store

    def test_replan_seal_retains_annotation_through_successor_admission(self):
        store = self.prepare_boundary()
        with self.policy_reads():
            trigger = self.call()
            seal = store.seal_round_boundary_replan(trigger, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
            self.assertEqual(trigger, seal['trigger'])
            self.assertTrue(store.round_boundary_ready(self.inventory, 'b' * 64,
                owner_config=self.config, accepted_policy_root=self.project))
            store.schedule(self.inventory, NOW, 2, inventory_sha256='b' * 64,
                owner_config=self.config, accepted_policy_root=self.project)
        with store.connection() as db:
            retained = loads(db.execute('SELECT record_json FROM controller_replan_seal').fetchone()[0])
        self.assertEqual('CONSUMED', retained['status'])
        self.assertEqual(trigger, retained['trigger'])
        self.assertEqual(fingerprint('controller-replan-owner-policy', {'trusted_owner_ids': [1001, 1002]}),
                         trigger['owner_policy_sha256'])

    def test_replan_retained_missing_altered_and_rootless_annotations_refuse(self):
        store = self.prepare_boundary()
        with self.policy_reads():
            original = self.call()
        for field in OWNER_EVIDENCE_FIELDS:
            trigger = deepcopy(original)
            trigger['shared_owner_evidence'].pop(field)
            self.reseal(trigger)
            with self.subTest(field=field), self.policy_reads(), self.assertRaises(ValidationError):
                store.seal_round_boundary_replan(trigger, NOW, owner_config=self.config,
                    accepted_policy_root=self.project)
        for mutation in ('missing', 'altered', 'rootless', 'repository'):
            trigger = deepcopy(original)
            if mutation == 'missing':
                trigger.pop('shared_owner_evidence')
            if mutation == 'altered':
                trigger['shared_owner_evidence']['accepted_owner_policy']['default_branch_commit'] = 'f' * 40
            if mutation == 'repository':
                trigger['repository_id'] = '202'
                trigger['trigger_id'] = fingerprint('controller-replan-trigger-id', {
                    'repository_id': '202', 'comment_id': trigger['provider_artifact']['comment_id'],
                    'node_id': trigger['provider_artifact']['node_id']})
            self.reseal(trigger)
            with self.subTest(mutation=mutation), self.policy_reads(), self.assertRaises(ValidationError):
                store.seal_round_boundary_replan(trigger, NOW, owner_config=self.config,
                    accepted_policy_root=None if mutation == 'rootless' else self.project)

    def test_replan_policy_drift_refuses_before_retained_seal_use(self):
        store = self.prepare_boundary()
        with self.policy_reads():
            trigger = self.call()
            store.seal_round_boundary_replan(trigger, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        self.commit = 'b' * 40
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.round_boundary_ready(self.inventory, 'b' * 64, owner_config=self.config,
                accepted_policy_root=self.project)


class OverlapPolicyTests(ConsumerCases, ControllerPolicyFixture):
    consumer = 'overlap'

    def expired(self):
        store = self.store()
        with self.policy_reads():
            record = self.call()
            store.register_overlap_allowance(record, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        gate = store.expire_overlap_allowance(record['allowance_id'], record['expires_at'])
        return store, record, gate

    def test_expiry_sync_receipt_and_critic_evidence_retain_complete_annotation(self):
        store, record, gate = self.expired()
        annotation = self.annotation(record)
        self.assertEqual(annotation, gate['shared_owner_evidence'])
        now = record['expires_at']
        observation = controller_fixtures.conflict_observation(gate, now)
        with self.policy_reads():
            synced = store.execute_overlap_sync_gate(gate['gate_id'], observation,
                lambda payload: controller_fixtures.sync_receipt(payload, now), now,
                owner_config=self.config, accepted_policy_root=self.project)
            completed = store.confirm_overlap_sync_inventory(gate['gate_id'],
                controller_fixtures.inventory_at_heads(controller_fixtures.SYNC_HEADS),
                inventory_sha256='d' * 64, observed_at=now,
                provider_observation=controller_fixtures.sync_provider_observation(
                    gate, controller_fixtures.SYNC_HEADS, now), owner_config=self.config,
                accepted_policy_root=self.project)
        self.assertEqual('EXECUTED', completed['status'])
        self.assertEqual(annotation, synced['critic_evidence']['shared_owner_evidence'])
        self.assertEqual(annotation, store.critic_overlap_evidence('AWF-101')[0]['shared_owner_evidence'])
        critics = controller_fixtures.inventory_at_heads(controller_fixtures.SYNC_HEADS)
        for item in critics:
            item.update(dispatch_role='critic', lifecycle_stage='independent_review')
        store.schedule(critics, now, 2)
        operation = store.prepare_dispatches(now)[0]
        payload = operation['payload']
        self.assertEqual(annotation, payload['overlap_sync_evidence'][0]['shared_owner_evidence'])
        altered = deepcopy(payload)
        altered['overlap_sync_evidence'][0].pop('shared_owner_evidence')
        with store.transaction() as db:
            db.execute('UPDATE controller_dispatch SET payload_json=? WHERE dispatch_id=?',
                (canonical(altered).decode(), operation['dispatch_id']))
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.begin_dispatch(operation['dispatch_id'], now, owner_config=self.config,
                accepted_policy_root=self.project)
        with store.transaction() as db:
            db.execute('UPDATE controller_dispatch SET payload_json=? WHERE dispatch_id=?',
                (canonical(payload).decode(), operation['dispatch_id']))
        with self.policy_reads():
            begun = store.begin_dispatch(operation['dispatch_id'], now, owner_config=self.config,
                accepted_policy_root=self.project)
        self.assertEqual(annotation, begun['overlap_sync_evidence'][0]['shared_owner_evidence'])

    def test_expired_sync_rootless_and_policy_drift_refuse_before_writer(self):
        store, record, gate = self.expired()
        now = record['expires_at']
        observation = controller_fixtures.conflict_observation(gate, now)
        def writer(_):
            self.fail('Policy refusal must precede sync writer invocation')
        with self.assertRaises(ValidationError):
            store.execute_overlap_sync_gate(gate['gate_id'], observation, writer, now)
        self.commit = 'b' * 40
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.execute_overlap_sync_gate(gate['gate_id'], observation, writer, now,
                owner_config=self.config, accepted_policy_root=self.project)
        self.assertEqual('PENDING', store.overlap_sync_gate(gate['gate_id'])['status'])

    def test_expired_sync_incomplete_altered_and_missing_annotations_refuse(self):
        store, record, original = self.expired()
        now = record['expires_at']
        for field in (*OWNER_EVIDENCE_FIELDS, 'all', 'altered', 'scope'):
            gate = deepcopy(original)
            if field == 'all':
                gate.pop('shared_owner_evidence')
                gate.pop('owner_actor_id')
            elif field == 'altered':
                gate['shared_owner_evidence']['accepted_owner_policy']['default_branch_commit'] = 'f' * 40
            elif field == 'scope':
                gate['path'] = 'src/other.py'
            else:
                gate['shared_owner_evidence'].pop(field)
            with store.transaction() as db:
                db.execute('UPDATE controller_overlap_sync_gate SET record_json=? WHERE gate_id=?',
                    (canonical(gate).decode(), gate['gate_id']))
            with self.subTest(field=field), self.policy_reads(), self.assertRaises(ValidationError):
                store.execute_overlap_sync_gate(gate['gate_id'],
                    controller_fixtures.conflict_observation(gate, now),
                    lambda _: self.fail('Altered annotation reached sync writer'), now,
                    owner_config=self.config, accepted_policy_root=self.project)

    def test_expired_sync_boolean_actor_alias_refuses(self):
        self.config['merge_gate'][KEY] = 1
        self.config['merge_gate']['trusted_owner_ids'] = [1001, 1]
        self.config['github']['expected_actor_id'] = 1
        self.observed['comment']['actor_id'] = 1
        self.accept()
        store, record, gate = self.expired()
        gate['owner_actor_id'] = True
        with store.transaction() as db:
            db.execute('UPDATE controller_overlap_sync_gate SET record_json=? WHERE gate_id=?',
                (canonical(gate).decode(), gate['gate_id']))
        now = record['expires_at']
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.execute_overlap_sync_gate(gate['gate_id'],
                controller_fixtures.conflict_observation(gate, now),
                lambda _: self.fail('Boolean actor alias reached sync writer'), now,
                owner_config=self.config, accepted_policy_root=self.project)

    def test_sync_callback_policy_drift_retains_unknown_and_receipt_only_recovery(self):
        store, record, gate = self.expired()
        now = record['expires_at']
        receipts = []
        def writer(payload):
            receipts.append(controller_fixtures.sync_receipt(payload, now))
            self.commit = 'b' * 40
            return receipts[0]
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.execute_overlap_sync_gate(gate['gate_id'],
                controller_fixtures.conflict_observation(gate, now), writer, now,
                owner_config=self.config, accepted_policy_root=self.project)
        self.assertEqual('UNKNOWN', store.overlap_sync_gate(gate['gate_id'])['status'])
        self.commit = 'a' * 40
        with self.policy_reads():
            recovered = store.reconcile_overlap_sync_gate(gate['gate_id'],
                lambda _: deepcopy(receipts[0]), now, owner_config=self.config,
                accepted_policy_root=self.project)
        self.assertEqual(1, len(receipts))
        self.assertEqual('SYNCED', recovered['status'])
        self.assertEqual(self.annotation(record), recovered['critic_evidence']['shared_owner_evidence'])

    def test_planner_accepts_and_seals_full_shared_annotation(self):
        with self.policy_reads():
            record = self.call()
        planner_config = planning_fixtures.config(2)
        planner_config['github']['repository_id'] = '101'
        graph = planning.build_backlog_graph([
            planning_fixtures.ticket('AWF-101', 'src/shared.py'),
            planning_fixtures.ticket('AWF-102', 'src/shared.py')], planner_config)
        binding = {'repository_id': '101', 'cycle_id': 'cycle-17',
                   'pull_requests': self.request['pull_requests']}
        accepted = planning._planning_overlap_allowances(graph, planner_config, [record],
            binding, NOW, lambda *_: deepcopy(record))
        self.assertEqual([record], list(accepted.values()))
        self.assertEqual(self.annotation(record), {key: record['approval'][key] for key in OWNER_EVIDENCE_FIELDS})
        state = {}
        items = [planning_fixtures.ticket('AWF-101', 'src/shared.py'),
                 planning_fixtures.ticket('AWF-102', 'src/shared.py')]
        with patch.object(planning_fixtures, 'NOW', NOW):
            capacity = planning_fixtures.capacity_kwargs(planner_config, host_capacity=2, stream_capacity=2)
        sealed = planning.recompute_plan(items, planner_config, state, now=NOW,
            trigger='files_changed', overlap_allowances=[record],
            expected_overlap_binding=binding, verify_overlap_allowance=lambda *_: deepcopy(record),
            **capacity)['record']
        self.assertEqual(2, sealed['lane_count'])
        self.assertEqual([record], sealed['inputs']['overlap_allowances'])
        self.assertTrue(planning.verify_plan_record(sealed))

    def test_production_cycle_supplies_host_root_and_retains_annotation(self):
        fixture = controller_fixtures.OverlapAllowanceBoundaryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.project = (Path(fixture.path).parent / 'operating-fixture').resolve()
        phases = []
        def observe(request, phase):
            self.assertEqual(self.request, request)
            phases.append(phase)
            return deepcopy(self.observed)
        with self.policy_reads():
            result = fixture.cycle(self.inventory, publication=self.config,
                cycle_id='cycle-17', overlap_allowance_requests=[self.request],
                observe_overlap_allowance=observe)
        self.assertEqual(['PLAN', 'LANE_START'], phases)
        self.assertEqual(['ACTIVE', 'ACTIVE'], [row['status'] for row in result['overlap_allowances']])
        with fixture.store.connection() as db:
            retained = loads(db.execute('SELECT record_json FROM controller_overlap_allowance').fetchone()[0])
        self.assertIs(retained['shared_owner_automation_account'], True)
        # The excluded rootless publication boundary still prevents a global lift.
        self.assertEqual([], result['dispatch_receipts'])

    def test_store_retains_and_waiver_hashes_complete_annotation(self):
        store = self.store()
        with self.policy_reads():
            record = self.call()
            store.register_overlap_allowance(record, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
            store.recheck_overlap_allowance(record['allowance_id'], record, NOW,
                owner_config=self.config, accepted_policy_root=self.project)
            store.schedule(self.inventory, NOW, 2, [{'path': record['binding']['path'],
                'tickets': record['binding']['tickets'], 'record': record}],
                owner_config=self.config, accepted_policy_root=self.project)
            store.prepare_dispatches(NOW)
            store.bind_dispatch_waivers([record], NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        retained = store.overlap_allowance(record['allowance_id'])
        retained.pop('status')
        self.assertEqual(record, retained)
        with store.connection() as db:
            waivers = [loads(row[0]) for row in db.execute('SELECT record_json FROM controller_dispatch_waiver')]
        self.assertEqual(2, len(waivers))
        for waiver in waivers:
            self.assertEqual(self.annotation(record), waiver['shared_owner_evidence'])
        with self.policy_reads():
            digest = store.digest(NOW, owner_config=self.config, accepted_policy_root=self.project)
        self.assertIn('1002', digest['owner_policy_warnings'][0]['message'])
        for operation in store.pending_dispatches():
            with self.assertRaises(ValidationError):
                store.begin_dispatch(operation['dispatch_id'], NOW)
            with self.policy_reads():
                begun = store.begin_dispatch(operation['dispatch_id'], NOW,
                    owner_config=self.config, accepted_policy_root=self.project)
            self.assertTrue(begun['lane_start_waivers'])

    def test_shared_overlap_expiry_and_maximum_window_remain(self):
        for expires in (NOW, '2026-10-16T08:00:00Z'):
            self.request['expires_at'] = expires
            self.observed['comment']['body'] = controller.overlap_allowance_command(self.request)
            with self.subTest(expires=expires), self.policy_reads(), self.assertRaises(ValidationError):
                self.call()

    def test_shared_overlap_source_and_scope_replay_remain_refused(self):
        store = self.store()
        with self.policy_reads():
            original = self.call()
            store.register_overlap_allowance(original, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
            for change in ('scope', 'source'):
                record = deepcopy(original)
                if change == 'scope':
                    record['binding']['cycle_id'] = 'other-cycle'
                else:
                    record['source_id'] += ':other'
                self.reseal(record)
                with self.subTest(change=change), self.assertRaises(ValidationError):
                    store.register_overlap_allowance(record, NOW, owner_config=self.config,
                        accepted_policy_root=self.project)

    def test_incomplete_altered_and_entirely_missing_annotations_refuse_at_retention(self):
        with self.policy_reads():
            original = self.call()
        for field in OWNER_EVIDENCE_FIELDS:
            record = deepcopy(original)
            record.pop(field)
            self.reseal(record)
            with self.subTest(field=field), self.policy_reads(), self.assertRaises(ValidationError):
                self.store().register_overlap_allowance(record, NOW, owner_config=self.config,
                    accepted_policy_root=self.project)
        for target in ('record', 'approval', 'both'):
            record = deepcopy(original)
            for value in ([record] if target == 'record' else [record['approval']] if target == 'approval' else [record, record['approval']]):
                for field in OWNER_EVIDENCE_FIELDS:
                    value.pop(field)
            self.reseal(record)
            with self.subTest(target=target), self.policy_reads(), self.assertRaises(ValidationError):
                self.store().register_overlap_allowance(record, NOW, owner_config=self.config,
                    accepted_policy_root=self.project)
        record = deepcopy(original)
        record['accepted_owner_policy']['default_branch_commit'] = 'f' * 40
        self.reseal(record)
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.store().register_overlap_allowance(record, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        record = deepcopy(original)
        record['binding']['repository_id'] = '202'
        self.reseal(record)
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.store().register_overlap_allowance(record, NOW, owner_config=self.config,
                accepted_policy_root=self.project)

    def test_retained_policy_drift_refuses_before_scheduling(self):
        store = self.store()
        with self.policy_reads():
            record = self.call()
            store.register_overlap_allowance(record, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        self.commit = 'b' * 40
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.schedule(self.inventory, NOW, 2, [{'path': record['binding']['path'],
                'tickets': record['binding']['tickets'], 'record': record}],
                owner_config=self.config, accepted_policy_root=self.project)
        self.assertFalse(any(item['state'] == 'WORKING' for item in store.snapshot()))

    def test_shared_scheduling_requires_the_exact_annotated_scope(self):
        with self.policy_reads():
            record = self.call()
        original = {'path': record['binding']['path'], 'tickets': record['binding']['tickets'],
                    'allowance_id': record['allowance_id'], 'record': record}
        for mutation in ('missing_record', 'path', 'tickets', 'allowance_id', 'hidden_policy', 'root_without_config'):
            wrapper = deepcopy(original)
            config = deepcopy(self.config)
            if mutation in ('missing_record', 'hidden_policy', 'root_without_config'):
                wrapper.pop('record')
                if mutation == 'hidden_policy':
                    config['merge_gate'][KEY] = None
                    config['github']['expected_actor_id'] = 1003
                if mutation == 'root_without_config':
                    config = None
            else:
                wrapper[mutation] = {'path': 'src/other.py', 'tickets': ['AWF-101', 'AWF-999'],
                                     'allowance_id': 'f' * 64}[mutation]
            with self.subTest(mutation=mutation), self.policy_reads(), self.assertRaises(ValidationError):
                self.store().schedule(self.inventory, NOW, 2, [wrapper],
                    owner_config=config, accepted_policy_root=self.project)

    def test_policy_drift_before_dispatch_begin_refuses_without_launch(self):
        store = self.store()
        with self.policy_reads():
            record = self.call()
            store.register_overlap_allowance(record, NOW, owner_config=self.config,
                accepted_policy_root=self.project)
            store.schedule(self.inventory, NOW, 2, [{'path': record['binding']['path'],
                'tickets': record['binding']['tickets'], 'record': record}],
                owner_config=self.config, accepted_policy_root=self.project)
            operations = store.prepare_dispatches(NOW)
            store.bind_dispatch_waivers([record], NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        self.commit = 'b' * 40
        with self.policy_reads(), self.assertRaises(ValidationError):
            store.begin_dispatch(operations[0]['dispatch_id'], NOW, owner_config=self.config,
                accepted_policy_root=self.project)
        self.assertTrue(all(item['status'] == 'PENDING' for item in store.prepare_dispatches(NOW)))


if __name__ == '__main__':
    unittest.main()
