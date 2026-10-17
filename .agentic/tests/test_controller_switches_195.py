"""AWF-36 schema, quiescence, and real adjacent-upgrade preservation."""
import copy
import json
from pathlib import Path
import unittest

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


if __name__ == '__main__':
    unittest.main()
