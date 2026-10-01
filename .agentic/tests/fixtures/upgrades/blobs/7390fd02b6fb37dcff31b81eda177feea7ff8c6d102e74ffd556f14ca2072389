"""Adoption accepts absent services without weakening evidence/identity checks."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError
from agentic.canonical import load
from agentic.configuration import inspect_config, require_enablement_config
from agentic.contracts import Contracts
from agentic.gates import evaluate
from agentic.lifecycle import definition
from agentic.policy import inside_scope, policy_hash, validate_config


class AdoptionValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = load(ROOT / '.agentic/examples/PROJECT_CONFIG.yaml')
        self.contracts = Contracts(ROOT / '.agentic/schemas')

    def test_no_ci_adoption_is_accepted_but_enablement_refused(self):
        self.config['validation']['required_ci_checks'] = []
        result = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual((result['status'], result['ci_gate']), ('ACCEPTED', 'NOT_CONFIGURED'))
        with self.assertRaisesRegex(ValidationError, 'CI_NOT_CONFIGURED'):
            require_enablement_config(self.config, definition(), self.contracts)

    def test_null_repository_identity_is_reported_alongside_schema_residue(self):
        self.config['github']['repository_id'] = None
        self.config['github']['base_branch'] = None
        self.config['validation']['commands'] = []
        result = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertIsNone(result['policy_sha256'])
        self.assertEqual({item['path'] for item in result['unresolved']},
                         {'$.github.repository_id', '$.github.base_branch', '$.validation.commands'})
        identity = next(item for item in result['unresolved'] if item['path'] == '$.github.repository_id')
        self.assertEqual(identity['flag'], '--repository-id')

    def test_empty_ci_cannot_make_evidence_gate_ready(self):
        self.config['validation']['required_ci_checks'] = []
        bundle = load(ROOT / '.agentic/examples/evidence-bundle.json')
        old = bundle['contract']['policy_hash']
        new = policy_hash(self.config, definition())
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(child) for key, child in value.items()}
            if isinstance(value, list):
                return [replace(child) for child in value]
            return new if value == old else value
        bundle = replace(bundle)
        # The changed contract needs its own bound hash; no other evidence is weakened.
        from agentic.canonical import fingerprint
        contract_hash = fingerprint('contract', bundle['contract'])
        def bind(value):
            if isinstance(value, dict):
                if 'contract_hash' in value:
                    value['contract_hash'] = contract_hash
                for child in value.values():
                    bind(child)
            elif isinstance(value, list):
                for child in value:
                    bind(child)
        bind(bundle)
        result = evaluate(self.config, definition(), bundle, self.contracts, '2026-09-09T12:00:00Z')
        self.assertNotEqual(result['conclusion'], 'READY_FOR_OWNER_AUTHORIZATION')
        self.assertFalse(result['execution_authority'])
        self.assertEqual(result['gates']['required_ci']['result'], 'FAIL')
        self.assertEqual(result['gates']['ci_candidate_binding']['result'], 'FAIL')

    def test_disabled_jira_is_null_and_not_in_scope(self):
        self.config['jira'].update(enabled=False, site=None, project_key=None)
        self.assertEqual(inspect_config(self.config, definition(), self.contracts)['status'], 'ACCEPTED')
        self.assertFalse(inside_scope({'project_key': None}, self.config))
        with self.assertRaisesRegex(ValidationError, 'Jira is disabled'):
            evaluate(self.config, definition(), {}, self.contracts, '2026-09-09T12:00:00Z')

    def test_disabled_jira_cannot_hide_stale_placeholder(self):
        self.config['jira'].update(enabled=False, project_key=None, site='https://CHANGE_ME.invalid')
        result = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertIn('$.jira.site', {item['path'] for item in result['unresolved']})

    def test_legacy_jira_without_enabled_remains_enabled(self):
        self.config['jira'].pop('enabled', None)
        self.assertEqual(len(validate_config(self.config, definition(), self.contracts)), 64)
        self.config['jira']['site'] = None
        self.assertEqual(inspect_config(self.config, definition(), self.contracts)['status'], 'REJECTED')

    def test_empty_scope_warns_but_does_not_expand_jira_scope(self):
        self.config['jira']['scope']['labels_any'] = []
        report = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual(report['status'], 'ACCEPTED')
        self.assertTrue(any(item['code'] == 'JIRA_SCOPE_NOT_CONFIGURED' for item in report['warnings']))
        self.assertFalse(inside_scope({'project_key': 'EX', 'epic_ids': [], 'labels': [], 'component_ids': []}, self.config))

    def test_no_owner_identity_warns_and_cannot_authorize_merge(self):
        self.config['merge_gate']['trusted_owner_ids'] = []
        self.assertEqual(inspect_config(self.config, definition(), self.contracts)['status'], 'ACCEPTED')
        with self.assertRaisesRegex(ValidationError, 'OWNER_GATE_NOT_CONFIGURED'):
            require_enablement_config(self.config, definition(), self.contracts)
        with self.assertRaisesRegex(ValidationError, 'trusted owner IDs'):
            evaluate(self.config, definition(), {}, self.contracts, '2026-09-09T12:00:00Z')

    def test_residue_names_all_known_fields_and_flags(self):
        self.config['github']['repository_id'] = None
        self.config['project']['id'] = '00000000-0000-0000-0000-000000000000'
        self.config['validation']['commands'] = ['CHANGE_ME_TEST_COMMAND']
        report = inspect_config(self.config, definition(), self.contracts)
        remaining = {item['path']: item['flag'] for item in report['unresolved']}
        self.assertEqual(set(remaining), {'$.github.repository_id', '$.project.id', '$.validation.commands[0]'})
        self.assertEqual(remaining['$.github.repository_id'], '--repository-id')
        self.assertEqual(remaining['$.validation.commands[0]'], '--test-command')

    def test_schema_reports_all_missing_values(self):
        del self.config['github']['repository']
        del self.config['project']['short_name']
        report = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual({item['path'] for item in report['unresolved']}, {'$.github.repository', '$.project.short_name'})

    def test_multiple_semantic_errors_are_reported(self):
        self.config['github']['host'] = 'https://github.com/query?bad=1'
        self.config['jira']['site'] = 'https://jira.example.invalid/path'
        report = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual({item['path'] for item in report['unresolved']}, {'$.github.host', '$.jira.site'})

    def test_no_mutation_of_configuration(self):
        prior = copy.deepcopy(self.config)
        inspect_config(self.config, definition(), self.contracts)
        self.assertEqual(prior, self.config)

    def test_malformed_uri_returns_precise_residue(self):
        self.config['jira']['site'] = 'https://[bad'
        report = inspect_config(self.config, definition(), self.contracts)
        self.assertEqual(report['status'], 'REJECTED')
        self.assertEqual({item['path'] for item in report['unresolved']}, {'$.jira.site'})


if __name__ == '__main__':
    unittest.main()
