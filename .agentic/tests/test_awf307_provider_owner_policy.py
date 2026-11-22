"""GitHub-only accepted owner policy at provider identity entry points."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.canonical import canonical, load, sha256
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from shipped_defaults import shipped_routing
from agentic.policy import policy_hash
from agentic.provider_identity import (github_identity_preflight, jira_binding_plan,
                                      observe_github_identity)
from agentic.shared_owner_policy import validate_retained_owner_evidence

ROOT = Path(__file__).resolve().parents[2]
KEY = "shared_owner_automation_actor_id"
NOW = "2026-10-03T10:00:00Z"


class ProviderPolicyFixture(unittest.TestCase):
    def setUp(self):
        self.enterContext(shipped_routing())
        self.config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.config["merge_gate"]["trusted_owner_ids"] = [1001, 1002]
        self.config["merge_gate"][KEY] = 1002
        self.accepted = deepcopy(self.config)
        self.workflow = definition()
        self.contracts = Contracts(ROOT / ".agentic/schemas")
        self.commit = "a" * 40
        self.ref = "refs/remotes/origin/main"
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.project = Path(self.folder.name)
        self.blobs = {".agentic/PROJECT_CONFIG.yaml": canonical(self.accepted),
                      ".agentic/workflow.yaml": canonical(self.workflow)}
        self.observation = {"actor_id": 1002, "actor_login": "fixture-owner",
            "auth_profile": "fixture", "repository_id": 101, "repository": "fixture/example",
            "readable": True, "protocol": "ssh", "observed_at": NOW}
        self.connections = [{"connection_id": "personal", "cloud_id": "fixture-cloud",
            "site": "https://jira.example.invalid", "account_id": "fixture-controller",
            "account_login": "fixture-user", "observed_at": NOW,
            "projects": [{"project_id": "fixture-project", "project_key": "EX", "browse": True}]}]
        discovered = jira_binding_plan(self.config, self.connections, now=NOW)
        candidate = discovered["candidates"][0]
        self.confirmation = {"format": "awf-jira-binding-confirmation-1", "decision": "BIND",
            "candidate_sha256": discovered["candidate_sha256"],
            **{key: candidate[key] for key in
               ("cloud_id", "site", "project_id", "project_key", "account_id")},
            "owner_id": 1002, "confirmed_at": NOW,
            "authentication_evidence": ["urn:awf:fixture:owner-auth"]}

    @contextmanager
    def policy_reads(self, *, drift=False):
        reads = 0
        def git(root, *args, **kwargs):
            nonlocal reads
            self.assertEqual(Path(root).resolve(), self.project.resolve())
            if args[0] == "rev-parse":
                reads += 1
                return ("b" * 40 if drift and reads > 2 else self.commit).encode()
            if args[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + args[-1].encode()
            if args[:3] == ("config", "--local", "--no-includes"):
                github = self.accepted["github"]
                return (github["host"] + "/" + github["repository"] + ".git\0").encode()
            self.fail("Unexpected immutable policy read: " + repr(args))
        with patch("agentic.shared_owner_policy._default_ref", side_effect=lambda root: (self.ref, "fixture")), \
                patch("agentic.shared_owner_policy._git", side_effect=git), \
                patch("agentic.protected_settings._git", side_effect=git), \
                patch("agentic.shared_owner_policy._blob", side_effect=lambda root, commit, path: self.blobs.get(path)):
            yield

    def assert_annotation(self, result):
        self.assertIs(result["shared_owner_automation_account"], True)
        self.assertEqual(result["owner_policy_sha256"], policy_hash(self.accepted, self.workflow))
        policy = result["accepted_owner_policy"]
        self.assertEqual(policy["config_sha256"], sha256(self.blobs[".agentic/PROJECT_CONFIG.yaml"]))
        self.assertEqual(policy["workflow_sha256"], sha256(self.blobs[".agentic/workflow.yaml"]))
        self.assertEqual(policy["default_branch_commit"], self.commit)
        self.assertEqual(policy["warnings"][0]["code"], "SHARED_OWNER_AUTOMATION_ACCOUNT")
        self.assertIn("1002", policy["warnings"][0]["message"])
        self.assertFalse(result["execution_authority"])
        self.assertTrue(validate_retained_owner_evidence(result, 1002))


class ProviderOwnerPolicyTests(ProviderPolicyFixture):
    def preflight(self, *, rooted=True):
        options = {"accepted_policy_root": self.project} if rooted else {}
        return github_identity_preflight(self.config, self.observation, **options)

    def binding(self, *, rooted=True):
        options = {"accepted_policy_root": self.project} if rooted else {}
        return jira_binding_plan(self.config, self.connections, self.confirmation, now=NOW, **options)

    def test_preflight_accepted_policy_retains_annotation_and_unedited_observation(self):
        original = deepcopy(self.observation)
        with self.policy_reads():
            result = self.preflight()
        self.assertEqual(result["status"], "PASS")
        self.assert_annotation(result)
        self.assertEqual(result["observed"], original)
        self.assertEqual(self.observation, original)

    def test_binding_accepted_github_owner_retains_annotation_without_jira_opt_out(self):
        original = deepcopy(self.confirmation)
        with self.policy_reads():
            result = self.binding()
        self.assertEqual(result["status"], "CONFIRMED")
        self.assert_annotation(result)
        self.assertEqual(result["confirmation"], original)
        self.assertEqual(result["binding_patch"]["controller_actor_id"], "fixture-controller")
        self.assertNotIn("shared_owner_controller_account", result)
        self.assertEqual(self.confirmation, original)

    def test_existing_rootless_consumers_keep_global_refusal(self):
        for call in (self.preflight, self.binding):
            with self.subTest(consumer=call.__name__), self.assertRaisesRegex(
                    ValidationError, "The configured automation actor must not be a trusted owner"):
                call(rooted=False)

    def test_supplied_working_and_feature_configuration_cannot_enable_either_consumer(self):
        self.accepted["merge_gate"][KEY] = None
        self.accepted["merge_gate"]["trusted_owner_ids"] = [1001]
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
        for call in (self.preflight, self.binding):
            with self.subTest(consumer=call.__name__), self.policy_reads(), self.assertRaisesRegex(
                    ValidationError, "accepted policy digest changed"):
                call()
        self.accepted = deepcopy(self.config)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        self.ref = "refs/remotes/origin/feature"
        for call in (self.preflight, self.binding):
            with self.subTest(consumer=call.__name__), self.policy_reads(), self.assertRaisesRegex(
                    ValidationError, "accepted default-branch identity differs"):
                call()

    def test_different_overlapping_actor_and_identity_mismatches_refuse(self):
        for field, value in (("actor_id", 1001), ("actor_id", True), ("actor_id", 9999),
                             ("actor_login", "other"), ("repository_id", 202),
                             ("repository", "other/example"), ("auth_profile", "other"),
                             ("readable", False), ("protocol", "file")):
            with self.subTest(field=field, value=value), self.policy_reads(), self.assertRaises(ValidationError):
                github_identity_preflight(self.config, self.observation | {field: value},
                                          accepted_policy_root=self.project)
        self.confirmation["owner_id"] = True
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "owner is not trusted"):
            self.binding()

    def test_policy_drift_between_observation_and_use_refuses_both_consumers(self):
        for call in (self.preflight, self.binding):
            with self.subTest(consumer=call.__name__), self.policy_reads(drift=True), self.assertRaisesRegex(
                    ValidationError, "policy changed"):
                call()

    def test_missing_policy_and_hidden_setting_refuse_both_consumers(self):
        original = self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        for call in (self.preflight, self.binding):
            with self.subTest(consumer=call.__name__), self.policy_reads(), self.assertRaisesRegex(
                    ValidationError, "accepted default-branch policy is missing"):
                call()
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = original
        self.config["merge_gate"][KEY] = None
        self.config["github"]["expected_actor_id"] = 1003
        for call in (self.preflight, self.binding):
            with self.subTest(consumer=call.__name__), self.policy_reads(), self.assertRaisesRegex(
                    ValidationError, "accepted policy digest changed"):
                call()

    def test_distinct_binding_owner_is_not_annotated_as_shared(self):
        self.confirmation["owner_id"] = 1001
        with self.policy_reads():
            result = self.binding()
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertNotIn("shared_owner_automation_account", result)

    def test_default_separation_applies_to_binding_without_a_setting(self):
        self.config["merge_gate"][KEY] = None
        with self.assertRaisesRegex(ValidationError,
                "^The configured automation actor must not be a trusted owner$"):
            self.binding(rooted=False)

    def test_invalid_exact_account_relationships_refuse_both_consumers(self):
        original = deepcopy(self.config)
        changes = [("merge_gate", KEY, value) for value in (True, 0, -1, "1002", 1001)]
        changes += [("github", "expected_actor_id", None), ("github", "expected_actor_id", 1003),
                    ("merge_gate", "trusted_owner_ids", [1001])]
        for section, key, value in changes:
            self.config = deepcopy(original)
            self.config[section][key] = value
            for call in (self.preflight, self.binding):
                with self.subTest(consumer=call.__name__, key=key, value=value), self.policy_reads(), \
                        self.assertRaises(ValidationError):
                    call()

    def test_default_and_profile_only_messages_are_unchanged(self):
        self.config["merge_gate"][KEY] = None
        with self.assertRaisesRegex(ValidationError,
                "^The configured automation actor must not be a trusted owner$"):
            self.preflight(rooted=False)
        self.config["github"]["expected_actor_id"] = None
        with self.assertRaisesRegex(ValidationError,
                "^The configured automation actor must not be a trusted owner$"):
            self.preflight(rooted=False)

    def test_binding_candidate_time_authentication_and_browse_protections_remain(self):
        original = deepcopy(self.confirmation)
        for field, value in (("candidate_sha256", "0" * 64), ("confirmed_at", "2026-10-03T09:00:00Z"),
                             ("confirmed_at", "2026-10-03T10:01:00Z"),
                             ("authentication_evidence", []), ("account_id", "other-account")):
            self.confirmation = original | {field: value}
            with self.subTest(field=field), self.policy_reads(), self.assertRaises(ValidationError):
                self.binding()
        self.confirmation = original
        self.connections[0]["projects"][0]["browse"] = False
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.binding()

    def test_observer_wrapper_stays_unbound_and_cannot_inherit_opt_out(self):
        with self.policy_reads(), self.assertRaisesRegex(ValidationError,
                "The configured automation actor must not be a trusted owner"):
            observe_github_identity(self.config,
                read_actor=lambda: {"id": 1002, "login": "fixture-owner", "auth_profile": "fixture", "observed_at": NOW},
                read_repository=lambda repository_id: {"id": 101, "name_with_owner": "fixture/example", "readable": True},
                observe_protocol=lambda: "ssh")


if __name__ == "__main__":
    unittest.main()
