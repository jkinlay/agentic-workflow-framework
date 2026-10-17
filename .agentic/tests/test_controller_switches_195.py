"""AWF-36 schema, quiescence, and real adjacent-upgrade preservation."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.contracts import Contracts
from agentic.installer import assert_quiescent
from agentic.upgrade import apply_chain
from test_awf36_controller_automation import (
    ControllerAutomationTests, controller_config, ticket,
)

ROOT = Path(__file__).resolve().parents[2]
SWITCHES = tuple(controller_config())


class ControllerSwitchContractTests(unittest.TestCase):
    def test_boolean_schema_and_shipped_defaults(self):
        contracts = Contracts(ROOT / '.agentic/schemas')
        for path in ('.agentic/PROJECT_CONFIG.yaml', '.agentic/examples/PROJECT_CONFIG.yaml'):
            config = json.loads((ROOT / path).read_text(encoding='utf-8'))
            for key in SWITCHES:
                self.assertIs(config['controller'][key], False)
                for value in (False, True):
                    candidate = copy.deepcopy(config)
                    candidate['controller'][key] = value
                    contracts.validate('project-config', candidate)
                for value in ('true', None, 0, 1, [], {}):
                    with self.subTest(path=path, key=key, value=value):
                        candidate = copy.deepcopy(config)
                        candidate['controller'][key] = value
                        with self.assertRaises(ValidationError):
                            contracts.validate('project-config', candidate)

    def test_installer_requires_all_five_switches_exactly_false(self):
        switches = (*SWITCHES, 'auto_transition_jira')
        config = {'controller': dict.fromkeys(switches, False)}
        assert_quiescent(json.dumps(config).encode())
        for key in switches:
            for value in (True, 1, None, 'false'):
                with self.subTest(key=key, value=value):
                    candidate = copy.deepcopy(config)
                    candidate['controller'][key] = value
                    with self.assertRaises(ValidationError):
                        assert_quiescent(json.dumps(candidate).encode())
            candidate = copy.deepcopy(config)
            del candidate['controller'][key]
            with self.assertRaises(ValidationError):
                assert_quiescent(json.dumps(candidate).encode())

    def test_real_apply_chain_preserves_controller_values(self):
        table = {'target': '1.9.5', 'versions': {'1.9.4': {'migration': {
            'to': '1.9.5', 'new_required_settings': []}}}}
        target = {'source_manifest_sha256': 'a' * 64, '_immutable_files': {}}
        for enabled in (False, True):
            config = json.loads((ROOT / '.agentic/PROJECT_CONFIG.yaml').read_text(encoding='utf-8'))
            config['controller'].update(dict.fromkeys(SWITCHES, enabled))
            migrated, reports = apply_chain(table, '1.9.4', json.dumps(config).encode(),
                                            None, b'{}', b'{}', {}, target)
            actual = json.loads(migrated.project_config)
            self.assertEqual(config['controller'], actual['controller'])
            self.assertEqual('1.9.5', actual['template']['expected_workflow_version'])
            self.assertEqual(1, len(reports))


class IndependentPathSwitchTests(ControllerAutomationTests):
    def test_unrelated_disabled_switches_do_not_block_selected_path(self):
        for role, stage, required in (
            ('writer', 'implementation', 'auto_dispatch'),
            ('critic', 'independent_review', 'auto_request_critic'),
            ('writer', 'amendment', 'auto_resume_amendments'),
        ):
            with self.subTest(stage=stage):
                policy = dict.fromkeys(SWITCHES, False)
                policy.update(dispatch_enabled=True, **{required: True})
                item = ticket('AWF-365', role=role, stage=stage)
                result, _, dispatched, _ = self.cycle(self.store(), item, policy)
                self.assertEqual([item['ticket']], dispatched)
                self.assertEqual(1, len(result['dispatch_receipts']))

    def test_all_nonboolean_runtime_values_refuse_before_recovery(self):
        from unittest.mock import patch
        from agentic.continuous_controller import production_controller_cycle
        for key in SWITCHES:
            for value in ('true', None, 0, 1, [], {}):
                with self.subTest(key=key, value=value):
                    store = self.store()
                    with patch.object(store, 'recover_dispatches', side_effect=AssertionError('recovery reached')):
                        with self.assertRaisesRegex(ValidationError, 'controller.' + key):
                            self.cycle(store, ticket('AWF-366'), controller_config(**{key: value}))


class AmendmentBoundaryTests(ControllerAutomationTests):
    def run_inventory(self, store, items, policy, now, **operations):
        from agentic.continuous_controller import production_controller_cycle
        from test_awf36_controller_automation import BINDING, REPOSITORY, inventory_observation, dispatch_receipt
        from test_publication_readiness import publication_config, publication_observation
        arguments = dict(
            controller_config=policy, now=now, host_capacity=1,
            inventory_binding=BINDING, **REPOSITORY,
            observe_inventory=lambda: inventory_observation(now, items),
            observe_reservations=lambda request: {},
            dispatch_ticket=lambda payload: dispatch_receipt(payload, now),
            observe_dispatch=lambda payload: self.fail('unexpected reconciliation'),
            deliver_status=lambda digest: dict(delivery_id=digest['delivery_id'], status='DELIVERED', observed_at=now),
            publication_config=publication_config(),
            observe_publication=lambda item: publication_observation(item['ticket'], now),
            controller_clock=lambda: now,
        )
        arguments.update(operations)
        return production_controller_cycle(store, **arguments)

    def expired_overlap(self):
        from test_continuous_controller_planning import (
            NOW, OWNER_CONFIG, allowance_request, allowance_observation,
            allowance_inventory, validate_overlap_allowance_observation,
        )
        store = self.store('overlap')
        record = validate_overlap_allowance_observation(
            OWNER_CONFIG, allowance_request(), allowance_observation(),
            allowance_inventory(), now=NOW, expected_repository_id='101', expected_cycle_id='cycle-17')
        store.register_overlap_allowance(record, NOW)
        later = '2026-10-16T08:00:00Z'
        gate = store.expire_overlap_allowance(record['allowance_id'], later)
        return store, gate, later, allowance_inventory()

    def test_overlap_sync_writer_requires_master_and_auto_dispatch(self):
        from unittest.mock import Mock
        from test_continuous_controller_planning import conflict_observation, sync_receipt
        for disabled in ('dispatch_enabled', 'auto_dispatch', None):
            with self.subTest(disabled=disabled):
                store, gate, now, items = self.expired_overlap()
                writer = Mock(side_effect=lambda payload: sync_receipt(payload, now))
                result = self.run_inventory(
                    store, items, controller_config(**({disabled: False} if disabled else {})), now,
                    observe_overlap_conflict=lambda gate: conflict_observation(gate, now), sync_writer=writer)
                if disabled:
                    writer.assert_not_called()
                    self.assertEqual('PENDING', store.unresolved_overlap_sync_gates()[0]['status'])
                    refusal = next(error for error in result['errors'] if error['operation'] == 'overlap_sync')
                    self.assertIn('CONTROLLER_AUTOMATION_DISABLED: controller.' + disabled, refusal['reason'])
                    self.assertEqual('REFUSED', next(audit for audit in result['overlap_allowances'] if audit['phase'] == 'SYNC_DECISION')['status'])
                else:
                    writer.assert_called_once()
                    self.assertEqual('SYNCED', store.unresolved_overlap_sync_gates()[0]['status'])

    def test_disabled_overlap_sync_still_reconciles_unknown_receipt_only(self):
        from unittest.mock import Mock
        from test_continuous_controller_planning import conflict_observation, sync_receipt
        for disabled in ('dispatch_enabled', 'auto_dispatch'):
            with self.subTest(disabled=disabled):
                store, gate, now, items = self.expired_overlap()
                payloads = []
                def uncertain(payload):
                    payloads.append(payload)
                    raise RuntimeError('unknown provider outcome')
                with self.assertRaises(RuntimeError):
                    store.execute_overlap_sync_gate(gate['gate_id'], conflict_observation(gate, now), uncertain, now)
                writer = Mock(side_effect=AssertionError('must not relaunch'))
                reader = Mock(side_effect=lambda payload: sync_receipt(payload, now))
                result = self.run_inventory(store, items, controller_config(**{disabled: False}), now,
                                           sync_writer=writer, reconcile_overlap_sync=reader)
                writer.assert_not_called()
                reader.assert_called_once_with(payloads[0])
                self.assertTrue(any(audit['phase'] == 'SYNC_RECEIPT_RECONCILIATION' and audit['status'] == 'SYNCED'
                                    for audit in result['overlap_allowances']))

    def test_disabled_pending_intent_cannot_authorize_successor_refill_on_restart(self):
        from test_awf36_controller_automation import NOW, inventory_observation
        from agentic.continuous_controller import ContinuousControllerStore
        store = self.store('boundary')
        prior = ticket('AWF-101')
        old = inventory_observation(NOW, [prior])
        store.schedule([prior], NOW, 1, inventory_sha256=old['inventory_sha256'])
        intent = store.prepare_dispatches(NOW)[0]
        items = [prior, ticket('AWF-102', role='critic', stage='independent_review')]
        next_sha = inventory_observation(NOW, items)['inventory_sha256']
        for iteration in range(2):
            self.assertFalse(store.round_boundary_ready(items, next_sha))
            result = self.run_inventory(store, items, controller_config(auto_dispatch=False), NOW)
            self.assertEqual([], result['dispatch_receipts'])
            self.assertEqual([intent['dispatch_id']], [row['dispatch_id'] for row in store.pending_dispatches()])
            self.assertFalse(store.round_boundary_ready(items, next_sha))
            store = ContinuousControllerStore(store.path, ['A'])


if __name__ == '__main__':
    unittest.main()
