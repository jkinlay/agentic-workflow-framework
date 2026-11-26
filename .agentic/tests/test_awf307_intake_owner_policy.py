"""Accepted default-branch owner policy at the intake assignment boundary."""
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
from agentic.canonical import canonical, fingerprint, load, sha256
from agentic.continuous_controller import ContinuousControllerStore
from agentic.intake_assignment import (assign_intake, change_assignment,
    inherit_pr_assignment, load_runtime_assignments, render_pr_assignment, verify_assignment_record)
from agentic.lifecycle import definition
from shipped_defaults import shipped_routing
from agentic.policy import policy_hash
from test_intake_assignment import NOW, owner_source, ticket, verify_owner_source

ROOT = Path(__file__).resolve().parents[2]
KEY = "shared_owner_automation_actor_id"
EVIDENCE = ("shared_owner_automation_account", "owner_policy_sha256", "accepted_owner_policy")


class IntakeOwnerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(shipped_routing())
        self.config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.config["jira"]["enabled"] = False
        for key in ("cloud_id", "site", "provider_project_id", "project_key", "controller_actor_id"):
            self.config["jira"][key] = None
        self.config["github"]["repository"] = "fixture-only/awf-demo"
        self.config["merge_gate"]["trusted_owner_ids"] = [1001, 1002]
        self.config["merge_gate"][KEY] = 1002
        self.accepted = deepcopy(self.config)
        self.workflow = definition()
        self.blobs = {".agentic/PROJECT_CONFIG.yaml": canonical(self.accepted),
                      ".agentic/workflow.yaml": canonical(self.workflow)}
        self.commit = "a" * 40
        self.default_ref = "refs/remotes/origin/main"
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.store = ContinuousControllerStore(Path(self.temporary.name) / "state.sqlite", ["A"],
                                                worktree_roots=[self.project])
        self.state = {}
        self.current = assign_intake(self.config, ticket(), self.state, None, now=NOW,
                                    controller_actor_id="fixture-controller")
        inherit_pr_assignment(self.state, "AWF-100", 48, "Fixture PR body")
        self.reason = "Synthetic owner decision"
        self.source = owner_source("AWF-100", self.current["assignment_id"], 3, 3,
                                   self.reason, actor_id=1002)

    @contextmanager
    def policy_reads(self):
        """Replace immutable Git I/O only; run the real policy decision."""
        def git(root, *args, **kwargs):
            self.assertEqual(Path(root).resolve(), self.project.resolve())
            if args[0] == "rev-parse":
                return self.commit.encode()
            if args[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + args[-1].encode()
            if args[:3] == ("config", "--local", "--no-includes"):
                github = self.accepted["github"]
                return (github["host"] + "/" + github["repository"] + ".git\0").encode()
            self.fail("Unexpected Git read: " + repr(args))
        with patch("agentic.shared_owner_policy._default_ref", side_effect=lambda root: (self.default_ref, "fixture")), \
                patch("agentic.shared_owner_policy._git", side_effect=git), \
                patch("agentic.protected_settings._git", side_effect=git), \
                patch("agentic.shared_owner_policy._blob", side_effect=lambda root, commit, path: self.blobs.get(path)):
            yield

    def change(self, *, verifier=verify_owner_source, rooted=True, store=None, **options):
        arguments = dict(requested_tier=3, requested_cap=3, owner_source=self.source,
                         verify_owner_source=verifier, reason=self.reason, now=NOW,
                         controller_actor_id="fixture-controller", store=store)
        if rooted:
            arguments["accepted_policy_root"] = self.project
        arguments.update(options)
        return change_assignment(self.config, "AWF-100", self.state, None, **arguments)

    def test_shared_assignment_retains_accepted_bytes_and_unmodified_source(self):
        source = deepcopy(self.source)
        def verify(source, expected):
            self.assertEqual(expected["owner_policy_sha256"], policy_hash(self.accepted, self.workflow))
            return verify_owner_source(source, expected)
        with self.policy_reads():
            result = self.change(verifier=verify, rooted=False, store=self.store)
            loaded = load_runtime_assignments(self.config, self.state, accepted_policy_root=self.project)
        audit = result["change_audit"]
        self.assertIs(audit["shared_owner_automation_account"], True)
        self.assertEqual(audit["owner_policy_sha256"], policy_hash(self.accepted, self.workflow))
        self.assertEqual(audit["accepted_owner_policy"]["config_sha256"], sha256(self.blobs[".agentic/PROJECT_CONFIG.yaml"]))
        self.assertEqual(audit["accepted_owner_policy"]["workflow_sha256"], sha256(self.blobs[".agentic/workflow.yaml"]))
        self.assertEqual(audit["accepted_owner_policy"]["default_branch_commit"], self.commit)
        self.assertIs(audit["accepted_owner_policy"]["execution_authority"], False)
        self.assertEqual(result, loaded["AWF-100"])
        self.assertEqual(self.source, source)
        self.assertEqual(audit["owner_source_sha256"], source["raw_body_sha256"])
        self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", self.state["pr_assignments"]["48"]["body"])
        self.assertIn("1002", self.state["pr_assignments"]["48"]["body"])
        retained = deepcopy(result)
        retained["change_audit"].pop("owner_policy_warnings")
        self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", render_pr_assignment(retained))

    def test_controller_owned_single_root_binds_the_existing_entry_point(self):
        with self.policy_reads():
            result = self.change(rooted=False, store=self.store)
        self.assertIs(result["change_audit"]["shared_owner_automation_account"], True)

    def test_rootless_and_ambiguous_roots_refuse_without_state_changes(self):
        original = deepcopy(self.state)
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        ambiguous = ContinuousControllerStore(Path(self.temporary.name) / "ambiguous.sqlite", ["A"],
                                              worktree_roots=[self.project, other])
        for store in (None, ambiguous):
            with self.subTest(store=store), self.assertRaisesRegex(ValidationError, "accepted default-branch policy observation"):
                self.change(rooted=False, store=store)
            self.assertEqual(original, self.state)

    def test_supplied_feature_and_working_configuration_cannot_enable(self):
        self.accepted["merge_gate"][KEY] = None
        self.accepted["merge_gate"]["trusted_owner_ids"] = [1001]
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
        before = deepcopy(self.state)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            self.change()
        self.assertEqual(before, self.state)

    def test_hidden_setting_identity_and_policy_drift_refuse_at_entry_point(self):
        original = deepcopy(self.config)
        for mutation in ("hidden", "actor", "repository", "login", "owners"):
            self.config = deepcopy(original)
            with self.subTest(mutation=mutation):
                if mutation == "hidden":
                    self.config["merge_gate"][KEY] = None
                    self.config["github"]["expected_actor_id"] = 1003
                elif mutation == "actor":
                    self.config["github"]["expected_actor_id"] = 1003
                elif mutation == "repository":
                    self.config["github"]["repository_id"] = 202
                elif mutation == "login":
                    self.config["github"]["expected_actor_login"] = "other-fixture"
                else:
                    self.config["merge_gate"]["trusted_owner_ids"] = [1001]
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.change()

    def test_rootless_null_and_absent_setting_preserve_legacy_owner_authority(self):
        self.config["github"]["expected_actor_id"] = 1003
        self.config["merge_gate"][KEY] = None
        before = deepcopy(self.state)
        for present in (True, False):
            self.state = deepcopy(before)
            if not present:
                self.config["merge_gate"].pop(KEY)
            with self.subTest(present=present):
                result = self.change(rooted=False)
                self.assertEqual(result, self.state["tier_assignments"]["AWF-100"])
                self.assertEqual(before["tier_assignments"]["AWF-100"],
                                 self.state["tier_assignment_history"]["AWF-100"][0])
                self.assertEqual(1002, result["change_audit"]["who"])
                self.assertEqual({"tier": 3, "round_cap": 3}, result["change_audit"]["new"])
                self.assertNotEqual(before, self.state)
                self.assertEqual(result["assignment_sha256"],
                                 self.state["pr_assignments"]["48"]["assignment_sha256"])
                self.assertNotIn("shared_owner_automation_account", result["change_audit"])

    def test_missing_unobserved_and_changed_policy_refuse(self):
        for path in tuple(self.blobs):
            with self.subTest(path=path):
                blob = self.blobs.pop(path)
                with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy is missing"):
                    self.change()
                self.blobs[path] = blob
        self.default_ref = None
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "default branch is unobserved"):
            self.change()
        self.default_ref = "refs/remotes/origin/main"
        self.accepted["project"]["name"] = "Changed synthetic policy"
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            self.change()

    def test_exact_account_invalid_values_and_null_overlap_refuse(self):
        original = deepcopy(self.config)
        for value in (True, False, 0, -1, "1002", 1001, None):
            self.config = deepcopy(original)
            self.config["merge_gate"][KEY] = value
            with self.subTest(value=value), self.policy_reads(), self.assertRaises(ValidationError):
                self.change()

    def test_source_binding_edit_time_and_provider_mismatch_still_refuse(self):
        original = deepcopy(self.source)
        for mutation in ("binding", "body", "edited", "future", "observed_future", "provider_actor"):
            self.source = deepcopy(original)
            verifier = verify_owner_source
            if mutation == "binding":
                self.source["binding"]["current_assignment_id"] = "other"
            elif mutation == "body":
                self.source["raw_body"] += " changed"
            elif mutation == "edited":
                self.source["updated_at"] = "2026-10-10T09:00:00Z"
            elif mutation == "future":
                self.source["created_at"] = self.source["updated_at"] = "2026-10-10T09:00:00Z"
            elif mutation == "observed_future":
                verifier = lambda source, expected: dict(verify_owner_source(source, expected), observed_at="2026-10-10T09:00:00Z")
            else:
                verifier = lambda source, expected: dict(verify_owner_source(source, expected), actor_id=1001)
            with self.subTest(mutation=mutation), self.policy_reads(), self.assertRaises(ValidationError):
                self.change(verifier=verifier)

    def test_policy_change_during_authentication_refuses_before_mutation(self):
        def verify(source, expected):
            self.commit = "b" * 40
            return verify_owner_source(source, expected)
        before = deepcopy(self.state)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy changed during owner-source"):
            self.change(verifier=verify)
        self.assertEqual(before, self.state)

    def test_replayed_source_refuses_even_when_resigned_for_successor(self):
        with self.policy_reads():
            first = self.change()
            self.source = owner_source("AWF-100", first["assignment_id"], 3, 3, self.reason, actor_id=1002)
            before = deepcopy(self.state)
            with self.assertRaisesRegex(ValidationError, "already consumed"):
                self.change()
        self.assertEqual(before, self.state)

    def test_protected_floor_and_reviewed_caps_remain_enforced(self):
        self.state = {}
        self.current = assign_intake(self.config, ticket(write_paths=[".agentic/labels.json"]),
                                    self.state, None, now=NOW,
                                    controller_actor_id="fixture-controller")
        for tier, cap in ((1, 1), (2, 3), (3, 4)):
            with self.subTest(tier=tier, cap=cap):
                self.source = owner_source("AWF-100", self.current["assignment_id"], tier, cap, self.reason, actor_id=1002)
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.change(requested_tier=tier, requested_cap=cap)

    def test_retained_evidence_is_complete_digest_bound_and_reobserved(self):
        with self.policy_reads():
            result = self.change()
        for field in EVIDENCE:
            mutated = deepcopy(result)
            mutated["change_audit"].pop(field)
            mutated["assignment_sha256"] = fingerprint("intake_assignment_record", {
                key: value for key, value in mutated.items() if key != "assignment_sha256"})
            with self.subTest(field=field), self.assertRaises(ValidationError):
                verify_assignment_record(mutated)
        stripped = deepcopy(self.state)
        for item in EVIDENCE:
            stripped["tier_assignments"]["AWF-100"]["change_audit"].pop(item)
        record = stripped["tier_assignments"]["AWF-100"]
        record["assignment_sha256"] = fingerprint("intake_assignment_record", {
            key: value for key, value in record.items() if key != "assignment_sha256"})
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "differs from accepted policy"):
            load_runtime_assignments(self.config, stripped, accepted_policy_root=self.project)
        self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy is missing"):
            load_runtime_assignments(self.config, self.state, accepted_policy_root=self.project)

    def test_distinct_owner_is_not_labelled_shared(self):
        self.source["actor_id"] = 1001
        with self.policy_reads():
            result = self.change()
        self.assertNotIn("shared_owner_automation_account", result["change_audit"])
        self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", self.state["pr_assignments"]["48"]["body"])

    def test_foreign_root_cannot_borrow_the_controller_policy(self):
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        with self.assertRaisesRegex(ValidationError, "differs from protected controller"):
            self.change(store=self.store, accepted_policy_root=other)

    def test_retained_policy_drift_and_offline_reload_refuse(self):
        with self.policy_reads():
            self.change()
        with self.assertRaisesRegex(ValidationError, "accepted default-branch policy observation"):
            load_runtime_assignments(self.config, self.state)
        self.commit = "b" * 40
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "differs from accepted policy"):
            load_runtime_assignments(self.config, self.state, accepted_policy_root=self.project)

    def test_retained_audit_has_a_closed_shape_and_authentic_warning(self):
        with self.policy_reads():
            result = self.change(rooted=False, store=self.store)
        for mutation in ("extra_audit", "extra_policy", "warning", "missing_audit", "null_audit", "scalar_audit"):
            retained = deepcopy(result)
            audit = retained["change_audit"]
            if mutation == "extra_audit":
                audit["unreviewed_authority"] = True
            elif mutation == "extra_policy":
                audit["accepted_owner_policy"]["unreviewed_authority"] = True
            elif mutation == "warning":
                audit["owner_policy_warnings"] = []
            elif mutation == "missing_audit":
                audit.pop("owner_source_id")
            else:
                retained["change_audit"] = None if mutation == "null_audit" else "unbound"
            retained["assignment_sha256"] = fingerprint("intake_assignment_record", {
                key: value for key, value in retained.items() if key != "assignment_sha256"})
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                verify_assignment_record(retained)

    def test_annotation_is_part_of_the_assignment_hash(self):
        with self.policy_reads():
            result = self.change(rooted=False, store=self.store)
        self.assertIs(result["change_audit"]["shared_owner_automation_account"], True)
        result["change_audit"]["owner_policy_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "digest is missing or stale"):
            verify_assignment_record(result)

    def test_boolean_source_and_provider_actor_cannot_alias_the_shared_account(self):
        self.config["github"]["expected_actor_id"] = 1
        self.config["merge_gate"][KEY] = 1
        self.config["merge_gate"]["trusted_owner_ids"] = [1, 1001]
        self.accepted = {key: deepcopy(value) for key, value in self.config.items() if key != "_awf_runtime"}
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        before = deepcopy(self.state)
        for mutation in ("source", "provider"):
            self.state = deepcopy(before)
            self.source["actor_id"] = True if mutation == "source" else 1
            verifier = verify_owner_source
            if mutation == "provider":
                verifier = lambda source, expected: dict(verify_owner_source(source, expected), actor_id=True)
            with self.subTest(mutation=mutation):
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.change(rooted=False, store=self.store, verifier=verifier)
                self.assertEqual(before, self.state)

    def test_profile_only_and_feature_default_ref_refuse(self):
        self.config["github"]["expected_actor_id"] = None
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "profile-only"):
            self.change(rooted=False, store=self.store)
        self.config["github"]["expected_actor_id"] = 1002
        self.default_ref = "refs/remotes/origin/feature"
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "default-branch identity differs"):
            self.change(rooted=False, store=self.store)

    def test_retained_audit_rejects_boolean_owner_after_annotation_stripping(self):
        with self.policy_reads():
            record = self.change(rooted=False, store=self.store)
        record["change_audit"]["who"] = True
        for field in EVIDENCE:
            record["change_audit"].pop(field, None)
        record["assignment_sha256"] = fingerprint("intake_assignment_record", {
            key: value for key, value in record.items() if key != "assignment_sha256"})
        with self.assertRaises(ValidationError):
            verify_assignment_record(record)


if __name__ == "__main__":
    unittest.main()
