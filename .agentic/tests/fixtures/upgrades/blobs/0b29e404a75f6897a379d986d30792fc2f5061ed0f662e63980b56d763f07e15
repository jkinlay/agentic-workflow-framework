"""Configuration errors identify actionable fields without disclosing values."""
from copy import deepcopy
from pathlib import Path
import unittest

from agentic import ValidationError
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from agentic.policy import validate_config

ROOT = Path(__file__).resolve().parents[2]


class ConfigDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")
        cls.fixture = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")

    def setUp(self):
        self.config = deepcopy(self.fixture)

    def reason(self, config=None):
        with self.assertRaises(ValidationError) as caught:
            validate_config(self.config if config is None else config, definition(), self.contracts)
        return str(caught.exception)

    def test_default_reports_all_placeholder_paths_in_one_response(self):
        reason = self.reason(load(ROOT / ".agentic/PROJECT_CONFIG.yaml"))
        for path in ["$.project.id", "$.project.name", "$.project.short_name", "$.jira.site",
                     "$.github.repository", "$.validation.required_ci_checks[0].workflow_sha"]:
            self.assertIn(path, reason)
        self.assertIn("zero UUID placeholder", reason)
        self.assertIn("zero commit SHA placeholder", reason)

    def test_nested_list_and_bootstrap_placeholder_paths_do_not_echo_values(self):
        self.config["validation"]["commands"] = ["SET_BY_BOOTSTRAP_PRIVATE_COMMAND"]
        reason = self.reason()
        self.assertIn("$.validation.commands[0]", reason)
        self.assertNotIn("PRIVATE_COMMAND", reason)

    def test_whitespace_reports_exact_path_but_optional_empty_strings_stay_valid(self):
        original = validate_config(self.config, definition(), self.contracts)
        self.assertEqual(self.config["jira"]["scope"]["additional_jql"], "")
        self.assertEqual(original, validate_config(self.config, definition(), self.contracts))
        self.config["project"]["name"] = "  \t"
        self.assertIn("$.project.name: whitespace-only", self.reason())

    def test_each_zero_identity_is_rejected_without_other_placeholders(self):
        self.config["project"]["id"] = "00000000-0000-0000-0000-000000000000"
        self.assertIn("$.project.id", self.reason())
        self.config = deepcopy(self.fixture)
        self.config["validation"]["required_ci_checks"][0]["workflow_sha"] = "0" * 40
        self.assertIn("$.validation.required_ci_checks[0].workflow_sha", self.reason())

    def test_semantic_cap_and_origin_errors_include_paths(self):
        self.config["execution"]["max_parallel_tickets_per_stream"] = 4
        self.assertIn("$.execution.max_parallel_tickets_per_stream", self.reason())
        self.config = deepcopy(self.fixture)
        self.config["jira"]["site"] = "https://jira.example.invalid/path"
        self.assertIn("$.jira.site", self.reason())

    def test_schema_type_errors_keep_field_locations(self):
        self.config["execution"]["max_parallel_tickets"] = "five"
        self.assertIn("execution.max_parallel_tickets", self.reason())

    def test_nonplaceholder_fixture_origins_and_commands_are_accepted(self):
        baseline = validate_config(self.config, definition(), self.contracts)
        self.config["project"]["name"] = "Configured fixture"
        self.assertNotEqual(baseline, validate_config(self.config, definition(), self.contracts))

    def test_shipped_reviewer_config_schema_is_optional_and_preserves_lower_caps(self):
        # Adopters receive the versioned schema, not source release generators.
        # Exercise the same configuration entrypoint in both runtime layouts.
        self.config["execution"].pop("independent_reviewers", None)
        validate_config(self.config, definition(), self.contracts)
        for count in [0, 1, 2, 3, 5]:
            self.config["execution"]["independent_reviewers"] = {"count": count, "allocation": "one_per_stream"}
            validate_config(self.config, definition(), self.contracts)
            self.assertEqual(self.config["execution"]["independent_reviewers"]["count"], count)
        for value in [{"count": -1, "allocation": "one_per_stream"},
                      {"count": True, "allocation": "one_per_stream"},
                      {"count": 3, "allocation": "three_per_ticket"}, {"count": 3}]:
            self.config["execution"]["independent_reviewers"] = value
            with self.assertRaises(ValidationError):
                validate_config(self.config, definition(), self.contracts)


if __name__ == "__main__":
    unittest.main()
