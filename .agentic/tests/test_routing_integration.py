"""Routing is a project-policy extension, not a replacement for existing caps."""
from copy import deepcopy
from pathlib import Path
import unittest

from agentic import ValidationError
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from agentic.model_routing import default_policy, policy_from_config
from agentic.policy import validate_config

ROOT = Path(__file__).resolve().parents[2]


class RoutingConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = load(ROOT / '.agentic/examples/PROJECT_CONFIG.yaml')
        self.contracts = Contracts(ROOT / '.agentic/schemas')

    def configured(self):
        routing = default_policy()
        self.config['execution']['model_routing'] = routing
        for role, pair in routing['role_defaults'].items():
            self.config['execution']['roles'][role].update(deepcopy(pair))
            self.config['execution']['roles'][role]['approved_model_ids'] = list(routing['role_allowed_models'][role])
        return self.config

    def test_balanced_policy_passes_whole_project_validation(self):
        validate_config(self.configured(), definition(), self.contracts)

    def test_legacy_static_configuration_remains_valid(self):
        self.config['execution'].pop('model_routing', None)
        self.assertNotIn('model_routing', self.config['execution'])
        validate_config(self.config, definition(), self.contracts)

    def test_malformed_routing_is_rejected_by_project_validation(self):
        self.configured()['execution']['model_routing']['escalation']['enabled'] = 'yes'
        with self.assertRaises(ValidationError):
            validate_config(self.config, definition(), self.contracts)

    def test_effective_limits_cannot_exceed_existing_project_caps(self):
        self.configured()['execution']['max_tokens_per_ticket'] = 1234
        self.config['execution']['max_agent_runs_per_ticket'] = 2
        effective = policy_from_config(self.config)
        self.assertEqual(1234, effective['budgets']['max_tokens_per_ticket'])
        self.assertEqual(2, effective['budgets']['max_runs_per_ticket'])

    def test_routing_changes_invalidate_policy_fingerprint(self):
        first = validate_config(self.configured(), definition(), self.contracts)
        self.config['execution']['model_routing']['escalation']['max_escalations_per_ticket'] = 1
        self.assertNotEqual(first, validate_config(self.config, definition(), self.contracts))


if __name__ == '__main__':
    unittest.main()
