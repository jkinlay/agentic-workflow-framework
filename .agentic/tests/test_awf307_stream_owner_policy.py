"""Accepted-policy binding for the stream owner path; global admission stays shut."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic.authorization import trusted_owner_ids
from agentic.canonical import canonical, load, fingerprint, sha256
from agentic.lifecycle import definition
from shipped_defaults import shipped_routing
from agentic.policy import policy_hash, validate_config
from agentic.contracts import Contracts
from agentic.provider_identity import github_identity_preflight
from agentic.streams import plan_inventory, render_markdown, write_project_plan, PLAN
from test_streams import (intake_assignment, stream_owner_source,
                          verify_stream_owner_source)

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-11T10:00:00Z"
KEY = "shared_owner_automation_actor_id"


class StreamOwnerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(shipped_routing())
        self.config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.config["merge_gate"]["trusted_owner_ids"] = [1001, 1002]
        self.config["merge_gate"][KEY] = 1002
        self.config["github"]["repository"] = "fixture-only/awf-demo"
        self.config["jira"]["project_key"] = "DEMO"
        self.accepted = deepcopy(self.config)
        self.workflow = definition()
        self.raw_config = canonical(self.accepted)
        self.raw_workflow = canonical(self.workflow)
        self.default_ref = "refs/remotes/origin/main"
        self.commit = "a" * 40
        self.blobs = {".agentic/PROJECT_CONFIG.yaml": self.raw_config,
                      ".agentic/workflow.yaml": self.raw_workflow}
        self.value = load(ROOT / ".agentic/examples/stream-input.json")
        self.raw = canonical(self.value)
        self.before = plan_inventory(self.raw, sha256(self.raw), NOW, allow_synthetic=True)
        self.assignment = intake_assignment()
        self.directive = {
            "ticket_id": "DEMO-31", "who": 1002, "when": NOW,
            "why": "Synthetic owner stream decision", "old": "C", "new": "B",
        }
        self.directive["owner_source"] = stream_owner_source(
            "DEMO-31", self.assignment["assignment_id"], "C", "B",
            self.directive["why"], actor_id=1002)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()

    @contextmanager
    def policy_reads(self):
        """Synthetic immutable Git boundary; never stub the policy decision."""
        def git(root, *arguments, **kwargs):
            self.assertEqual(Path(root).resolve(), self.project.resolve())
            if arguments[0] == "rev-parse":
                return self.commit.encode()
            if arguments[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + arguments[-1].encode()
            if arguments[:3] == ("config", "--local", "--no-includes"):
                github = self.accepted["github"]
                return (github["host"] + "/" + github["repository"] + ".git\0").encode()
            self.fail("Unexpected Git operation: " + repr(arguments))
        with patch("agentic.shared_owner_policy._default_ref",
                   side_effect=lambda root: (self.default_ref, "synthetic observation")), \
                patch("agentic.shared_owner_policy._has_git_metadata", return_value=True, create=True), \
                patch("agentic.shared_owner_policy._git", side_effect=git), \
                patch("agentic.protected_settings._git", side_effect=git), \
                patch("agentic.shared_owner_policy._blob",
                      side_effect=lambda root, commit, path: self.blobs.get(path)):
            yield

    def plan(self, *, previous=None, verifier=verify_stream_owner_source,
             directives=None, rooted=True, now=NOW):
        options = {"governance": self.config,
                   "intake_assignments": {"DEMO-31": self.assignment},
                   "owner_reassignments": [self.directive] if directives is None else directives,
                   "verify_owner_reassignment": verifier}
        if rooted:
            options["accepted_policy_root"] = self.project
        return plan_inventory(self.raw, sha256(self.raw), now, allow_synthetic=True,
                              previous=self.before if previous is None else previous, **options)

    def owner_event(self, plan):
        return next(event for event in plan["stream_assignment_history"]
                    if event["kind"] == "owner_reassignment")

    def test_shared_owner_plan_retains_policy_and_unedited_source(self):
        source = deepcopy(self.directive["owner_source"])
        config = deepcopy(self.config)
        def verify(source, expected):
            self.assertEqual(expected["owner_policy_sha256"], policy_hash(self.accepted, self.workflow))
            return verify_stream_owner_source(source, expected)
        with self.policy_reads():
            result = self.plan(verifier=verify)
        event = self.owner_event(result)
        self.assertIs(event["shared_owner_automation_account"], True)
        digest = policy_hash(self.accepted, self.workflow)
        self.assertEqual(event["owner_policy_sha256"], digest)
        evidence = event["accepted_owner_policy"]
        self.assertEqual(evidence["policy_sha256"], digest)
        self.assertEqual(evidence["config_sha256"], sha256(self.raw_config))
        self.assertEqual(evidence["workflow_sha256"], sha256(self.raw_workflow))
        self.assertEqual(evidence["default_branch_ref"], self.default_ref)
        self.assertEqual(evidence["default_branch_commit"], self.commit)
        self.assertIs(evidence["execution_authority"], False)
        self.assertEqual(event["owner_source_sha256"], source["raw_body_sha256"])
        self.assertEqual(self.directive["owner_source"], source)
        self.assertEqual(self.config, config)
        self.assertEqual(result["tickets"]["DEMO-31"]["stream"], "B")
        self.assertIs(result["execution_authority"], False)
        self.assertEqual(event["assignment_id"], fingerprint("stream_assignment_event", {
            key: value for key, value in event.items() if key != "assignment_id"}))

    def test_warning_and_owner_audit_remain_visible_on_retained_plan(self):
        with self.policy_reads():
            result = self.plan()
            retained = self.plan(previous=result, directives=[])
        for value in (result, retained):
            warning = value["warnings"][0]
            self.assertEqual(warning["code"], "SHARED_OWNER_AUTOMATION_ACCOUNT")
            self.assertIn("1002", warning["message"])
            markdown = render_markdown(value).decode()
            self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", markdown)
            self.assertIn("shared_owner_automation_account=true", markdown)
            self.assertIn(self.owner_event(value)["owner_policy_sha256"], markdown)

    def test_supplied_configuration_without_project_observation_refuses(self):
        with self.assertRaisesRegex(ValidationError, "accepted default-branch policy observation"):
            self.plan(rooted=False)

    def test_feature_working_and_supplied_edits_cannot_enable_the_exception(self):
        self.accepted["merge_gate"][KEY] = None
        self.accepted["merge_gate"]["trusted_owner_ids"] = [1001]
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        for source in ("feature", "working", "supplied"):
            with self.subTest(source=source):
                (self.project / ".agentic").mkdir(exist_ok=True)
                (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
                with self.policy_reads(), self.assertRaisesRegex(
                        ValidationError, "accepted policy digest changed"):
                    self.plan()

    def test_missing_changed_and_unobserved_policy_refuse_at_planner(self):
        for path in tuple(self.blobs):
            with self.subTest(path=path):
                original = self.blobs.pop(path)
                with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy is missing"):
                    self.plan()
                self.blobs[path] = original
        self.default_ref = None
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "default branch is unobserved"):
            self.plan()
        self.default_ref = "refs/remotes/origin/main"
        self.accepted["project"]["name"] = "Changed synthetic policy"
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            self.plan()

    def test_changed_identities_and_invalid_settings_refuse_at_planner(self):
        original = deepcopy(self.config)
        mutations = [("merge_gate", KEY, value) for value in
                     (True, False, 0, -1, "1002", 1001)] + [
                         ("github", "expected_actor_id", None),
                         ("github", "expected_actor_id", 1003),
                         ("github", "repository_id", 202),
                         ("github", "expected_actor_login", "different-synthetic-login"),
                         ("merge_gate", "trusted_owner_ids", [1001]),
                     ]
        for section, name, value in mutations:
            with self.subTest(section=section, name=name, value=value):
                self.config = deepcopy(original)
                self.config[section][name] = value
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.plan()

    def test_null_overlap_keeps_separation_refusal(self):
        self.config["merge_gate"][KEY] = None
        for who in (1001, 1002):
            with self.subTest(who=who):
                self.directive["who"] = self.directive["owner_source"]["actor_id"] = who
                with self.assertRaisesRegex(ValidationError,
                                           "The configured automation actor must not be a trusted owner"):
                    self.plan(rooted=False)

    def test_distinct_owner_is_not_mislabelled_as_shared(self):
        self.directive["who"] = 1001
        self.directive["owner_source"]["actor_id"] = 1001
        with self.policy_reads():
            result = self.plan()
        self.assertNotIn("shared_owner_automation_account", self.owner_event(result))
        self.assertEqual(result["warnings"][0]["code"], "SHARED_OWNER_AUTOMATION_ACCOUNT")

    def test_shared_policy_does_not_open_global_admission(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        for operation in (lambda: trusted_owner_ids(self.config),
                          lambda: validate_config(self.config, self.workflow, contracts),
                          lambda: github_identity_preflight(self.config, {
                              "actor_id": 1002, "actor_login": "fixture-owner", "auth_profile": "fixture",
                              "repository_id": 101, "repository": "fixture-only/awf-demo", "readable": True,
                              "protocol": "ssh", "observed_at": NOW})):
            with self.subTest(operation=operation), self.policy_reads(), self.assertRaisesRegex(
                    ValidationError, "automation actor must not be a trusted owner"):
                operation()

    def test_another_repository_cannot_borrow_accepted_owner_policy(self):
        self.value["project"]["repository"] = "other/synthetic-repository"
        self.raw = canonical(self.value)
        for shared, actor in ((1002, 1002), (None, 1003)):
            with self.subTest(shared=shared):
                self.config["merge_gate"][KEY] = shared
                self.config["github"]["expected_actor_id"] = actor
                self.accepted = deepcopy(self.config)
                self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
                with self.policy_reads(), self.assertRaisesRegex(
                        ValidationError, "^Existing plan belongs to a different project/schema$"
                        if shared is None else "inventory.*accepted.*repository"):
                    self.plan()

    def test_null_proposal_cannot_hide_default_branch_shared_policy(self):
        original = deepcopy(self.config)
        for actor in (None, 1003):
            with self.subTest(actor=actor):
                self.config = deepcopy(original)
                self.config["merge_gate"][KEY] = None
                self.config["github"]["expected_actor_id"] = actor
                with self.policy_reads(), self.assertRaisesRegex(
                        ValidationError, "accepted policy digest changed"):
                    self.plan()

    def test_real_inventory_null_setting_preserves_legacy_owner_reassignment(self):
        self.config["merge_gate"][KEY] = None
        self.config["github"]["expected_actor_id"] = 1003
        self.value["inventory"]["source"] = "jira_snapshot"
        self.raw = canonical(self.value)
        self.before = plan_inventory(self.raw, sha256(self.raw), NOW)
        result = self.plan(rooted=False)
        self.assertEqual(result["tickets"]["DEMO-31"]["stream"], "B")
        self.assertEqual(self.owner_event(result), {
            "ticket_id": "DEMO-31", "who": 1002, "when": NOW,
            "why": "Synthetic owner stream decision", "old": "C", "new": "B",
            "kind": "owner_reassignment",
            "previous_assignment_id": "7f3cd430-468a-5b42-a07a-2f23078b1318",
            "source_id": "github_pr_comment:48",
            "intake_assignment_id": "7f3cd430-468a-5b42-a07a-2f23078b1318",
            "previous_stream_assignment_id": "d9e1c964fbee8076aa4b60993d7ec7bda46909664afbc821b4db683df452dfb7",
            "owner_source_sha256": "4f691a7e83ab5c39c3c0f56560b3997d5f782dcccf5aaed897aeedf38cfa9d8c",
            "assignment_id": "bf380e916d40eac849899fc9be3487c5fac9080d80e7f8446ee4deacf511f38e",
        })
        retained = self.plan(previous=result, directives=[], rooted=False)
        self.assertEqual(retained["stream_assignment_history"], result["stream_assignment_history"])

    def test_project_writer_refuses_hidden_setting_without_changing_plan(self):
        options = dict(allow_synthetic=True, governance=self.config,
                       intake_assignments={"DEMO-31": self.assignment})
        first = write_project_plan(self.project, self.raw, sha256(self.raw), NOW, ROOT, **options)
        previous = (self.project / PLAN).read_bytes()
        self.config["merge_gate"][KEY] = None
        self.config["github"]["expected_actor_id"] = 1003
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            write_project_plan(
                self.project, self.raw, sha256(self.raw), NOW, ROOT,
                expected_plan_sha256=first["plan_sha256"],
                owner_reassignments=[self.directive],
                verify_owner_reassignment=verify_stream_owner_source, **options)
        self.assertEqual((self.project / PLAN).read_bytes(), previous)

    def test_policy_change_during_source_verification_refuses(self):
        def verify(source, expected):
            self.commit = "b" * 40
            return verify_stream_owner_source(source, expected)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy changed during owner-source"):
            self.plan(verifier=verify)

    def test_provider_binding_edit_and_future_time_still_refuse(self):
        original = deepcopy(self.directive)
        for change in ("actor", "binding", "body", "edited", "future", "receipt"):
            with self.subTest(change=change):
                self.directive = deepcopy(original)
                source = self.directive["owner_source"]
                verifier = verify_stream_owner_source
                if change == "actor":
                    source["actor_id"] = 1001
                elif change == "binding":
                    source["binding"]["current_assignment_id"] = "other-assignment"
                elif change == "body":
                    source["raw_body"] += " changed"
                elif change == "edited":
                    source["updated_at"] = "2026-09-11T10:01:00Z"
                elif change == "future":
                    source["created_at"] = source["updated_at"] = "2026-09-11T10:01:00Z"
                else:
                    verifier = lambda source, expected: dict(
                        verify_stream_owner_source(source, expected), actor_id=1001)
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.plan(verifier=verifier)

    def test_replay_protection_survives_shared_owner_exception(self):
        with self.policy_reads():
            first = self.plan()
            replay = deepcopy(self.directive)
            replay.update(old="B", new="C")
            replay["owner_source"] = stream_owner_source(
                "DEMO-31", self.assignment["assignment_id"], "B", "C", replay["why"], actor_id=1002)
            with self.assertRaisesRegex(ValidationError, "already consumed"):
                self.plan(previous=first, directives=[replay])

    def test_retained_shared_evidence_is_closed_and_revalidated(self):
        with self.policy_reads():
            result = self.plan()
        original = deepcopy(result)
        for field in ("shared_owner_automation_account", "owner_policy_sha256", "accepted_owner_policy"):
            with self.subTest(field=field):
                value = deepcopy(original)
                event = self.owner_event(value)
                event.pop(field)
                event["assignment_id"] = fingerprint("stream_assignment_event", {
                    key: item for key, item in event.items() if key != "assignment_id"})
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.plan(previous=value, directives=[])
        self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy is missing"):
            self.plan(previous=original, directives=[])

    def test_project_writer_observes_its_own_root_and_retains_warning(self):
        options = dict(allow_synthetic=True, governance=self.config,
                       intake_assignments={"DEMO-31": self.assignment})
        with self.policy_reads():
            first = write_project_plan(self.project, self.raw, sha256(self.raw), NOW, ROOT, **options)
            result = write_project_plan(
                self.project, self.raw, sha256(self.raw), NOW, ROOT,
                expected_plan_sha256=first["plan_sha256"],
                owner_reassignments=[self.directive],
                verify_owner_reassignment=verify_stream_owner_source, **options)
        retained = json.loads((self.project / PLAN).read_bytes())
        self.assertIs(self.owner_event(retained)["shared_owner_automation_account"], True)
        self.assertEqual(result["warnings"], retained["warnings"])
        self.assertIs(result["execution_authority"], False)


if __name__ == "__main__":
    unittest.main()
