"""Policy admission requires independently accepted default-branch owner policy."""
from copy import deepcopy
import unittest

from test_awf307_provider_owner_policy import ProviderPolicyFixture, KEY
from agentic import ValidationError
from agentic.canonical import canonical
from agentic.policy import inspect_config, policy_hash, validate_config


class PolicyOwnerPolicyTests(ProviderPolicyFixture):
    def admit(self, *, rooted=True, report=False):
        options = {"accepted_policy_root": self.project} if rooted else {}
        if report:
            options["return_report"] = True
        return validate_config(self.config, self.workflow, self.contracts, **options)

    def test_accepted_policy_admits_exact_account_and_reports_annotation(self):
        with self.policy_reads():
            self.assertEqual(self.admit(), policy_hash(self.accepted, self.workflow))
            report = self.admit(report=True)
        self.assert_annotation(report)
        self.assertEqual(report["status"], "ACCEPTED")
        self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", [item["code"] for item in report["warnings"]])

    def test_rootless_supplied_configuration_keeps_global_refusal_and_inspection_warning(self):
        with self.assertRaisesRegex(ValidationError,
                "^The configured automation actor must not be a trusted owner$"):
            self.admit(rooted=False)
        report = inspect_config(self.config, self.workflow, self.contracts)
        self.assertEqual(report["status"], "ACCEPTED")
        self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", [item["code"] for item in report["warnings"]])

    def test_working_supplied_and_feature_policy_cannot_authorize_admission(self):
        self.accepted["merge_gate"][KEY] = None
        self.accepted["merge_gate"]["trusted_owner_ids"] = [1001]
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            self.admit()
        self.accepted = deepcopy(self.config)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        self.ref = "refs/remotes/origin/feature"
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted default-branch identity differs"):
            self.admit()

    def test_policy_drift_between_observation_and_use_refuses_admission(self):
        with self.policy_reads(drift=True), self.assertRaisesRegex(ValidationError, "policy changed"):
            self.admit()

    def test_missing_policy_and_hidden_setting_refuse_admission(self):
        original = self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted default-branch policy is missing"):
            self.admit()
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = original
        self.config["merge_gate"][KEY] = None
        self.config["github"]["expected_actor_id"] = 1003
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            self.admit()

    def test_workflow_substitution_and_mandatory_tier_cap_still_refuse(self):
        self.workflow = deepcopy(self.workflow)
        self.workflow["version"] = "substituted"
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.admit()
        self.workflow = deepcopy(self.workflow)
        self.config["execution"]["risk_tiers"]["tier3_review"]["max_rounds"] = 4
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "mandatory maximum is 3"):
            self.admit()

    def test_other_account_profile_only_and_invalid_values_refuse(self):
        original = deepcopy(self.config)
        changes = [("merge_gate", KEY, value) for value in (True, 0, -1, "1002", 1001)]
        changes += [("github", "expected_actor_id", None), ("github", "expected_actor_id", 1003),
                    ("merge_gate", "trusted_owner_ids", [1001])]
        for section, key, value in changes:
            self.config = deepcopy(original)
            self.config[section][key] = value
            with self.subTest(key=key, value=value), self.policy_reads(), self.assertRaises(ValidationError):
                self.admit()

    def test_default_separation_message_and_unbound_callers_remain_unchanged(self):
        self.config["merge_gate"][KEY] = None
        with self.assertRaisesRegex(ValidationError,
                "^The configured automation actor must not be a trusted owner$"):
            self.admit(rooted=False)
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        self.assertEqual(self.admit(rooted=False), policy_hash(self.config, self.workflow))


if __name__ == "__main__":
    unittest.main()
