"""AWF-307 foundation: configuration reports never grant owner authority."""
from copy import deepcopy
import importlib
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
from agentic.canonical import load, sha256
from agentic.configuration import inspect_config
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from shipped_defaults import shipped_routing
from agentic.policy import policy_hash, validate_config

ROOT = Path(__file__).resolve().parents[2]
KEY = "shared_owner_automation_actor_id"
PATH = "$.merge_gate." + KEY


class SharedOwnerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(shipped_routing())
        self.contracts = Contracts(ROOT / ".agentic/schemas")
        self.config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.config["merge_gate"]["trusted_owner_ids"] = [1001, 1002]
        self.config["merge_gate"][KEY] = 1002

    def report(self):
        return inspect_config(self.config, definition(), self.contracts)

    def test_positive_setting_is_reported_without_authority(self):
        original = deepcopy(self.config)
        report = self.report()
        self.assertEqual(report["status"], "ACCEPTED", report["unresolved"])
        warnings = [item for item in report["warnings"]
                    if item["code"] == "SHARED_OWNER_AUTOMATION_ACCOUNT"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["path"], PATH)
        self.assertIn("1002", warnings[0]["message"])
        self.assertIs(report["execution_authority"], False)
        self.assertEqual(report["policy_sha256"], policy_hash(self.config, definition()))
        self.assertEqual(self.config, original)

    def test_schema_accepts_null_and_absent_without_warning(self):
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        for absent in (False, True):
            with self.subTest(absent=absent):
                self.config["merge_gate"][KEY] = None
                if absent:
                    del self.config["merge_gate"][KEY]
                report = self.report()
                self.assertEqual(report["status"], "ACCEPTED", report["unresolved"])
                self.assertNotIn("SHARED_OWNER_AUTOMATION_ACCOUNT",
                                 [item["code"] for item in report["warnings"]])

    def test_installed_example_forms_default_to_null(self):
        for relative in ("examples/PROJECT_CONFIG.yaml", "examples/unconfigured-project.yaml",
                         "examples/workflow-content-binding-project.yaml"):
            with self.subTest(path=relative):
                gate = load(ROOT / ".agentic" / relative)["merge_gate"]
                self.assertIn(KEY, gate)
                self.assertIsNone(gate[KEY])

    def test_invalid_types_and_nonpositive_values_refuse(self):
        for value in (True, False, 0, -1, "1002", "", [], {}, 1.5, 1002.0):
            with self.subTest(value=value):
                self.config["merge_gate"][KEY] = value
                report = self.report()
                self.assertEqual(report["status"], "REJECTED")
                self.assertTrue(any(item["path"] == PATH for item in report["unresolved"]))

    def test_relationship_refusals_name_the_setting(self):
        for actor, owners in ((1003, [1001, 1002]), (None, [1001, 1002]),
                              (1002, [1001]), (1003, [1001, 1003])):
            with self.subTest(actor=actor, owners=owners):
                self.config["github"]["expected_actor_id"] = actor
                self.config["github"]["auth_profile"] = "fixture-profile"
                self.config["merge_gate"]["trusted_owner_ids"] = owners
                report = self.report()
                self.assertEqual(report["status"], "REJECTED")
                self.assertTrue(any(item["path"] == PATH for item in report["unresolved"]))

    def test_staged_setting_does_not_change_owner_or_policy_admission(self):
        for value in (None, 1002):
            with self.subTest(value=value):
                self.config["merge_gate"][KEY] = value
                with self.assertRaisesRegex(ValidationError,
                        "The configured automation actor must not be a trusted owner"):
                    trusted_owner_ids(self.config)
                # The schema supports reporting; production admission stays closed.
                with self.assertRaisesRegex(ValidationError,
                        "The configured automation actor must not be a trusted owner"):
                    validate_config(self.config, definition(), self.contracts)

    def test_agent_rules_require_human_initiative_and_forbid_agent_signatures(self):
        for role in ("worker", "controller"):
            with self.subTest(role=role):
                body = (ROOT / ".agentic/prompts" / (role + ".md")).read_text(encoding="utf-8")
                self.assertIn("Never propose `merge_gate." + KEY + "` unasked", body)
                self.assertIn("never sign owner decisions.", body)


class AcceptedOwnerPolicyObservationTests(unittest.TestCase):
    """Exercise immutable reads at the new library boundary, never a CLI wrapper."""
    def setUp(self):
        self.enterContext(shipped_routing())
        self.contracts = Contracts(ROOT / ".agentic/schemas")
        self.config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.config["merge_gate"]["trusted_owner_ids"] = [1001, 1002]
        self.config["merge_gate"][KEY] = 1002
        self.workflow = definition()
        self.accepted = deepcopy(self.config)
        self.ref = "refs/remotes/origin/main"
        self.commit = "a" * 40
        self.blobs = {".agentic/PROJECT_CONFIG.yaml": json.dumps(self.accepted).encode(),
                      ".agentic/workflow.yaml": json.dumps(self.workflow).encode()}

    def observe(self, *, digest=None, git=None, default_ref=None, blob=None, root=ROOT):
        module = importlib.import_module("agentic.shared_owner_policy")
        def git_read(root, *arguments, **kwargs):
            if arguments[0] == "rev-parse":
                self.assertEqual(arguments[-1], self.ref + "^{commit}")
                return self.commit.encode() + b"\n"
            if arguments[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + arguments[-1].encode() + b"\n"
            if arguments[:3] == ("config", "--local", "--no-includes"):
                github = self.accepted["github"]
                return (github["host"] + "/" + github["repository"] + ".git\0").encode()
            self.fail("Unexpected Git read: " + repr(arguments))
        def blob_read(root, commit, path):
            self.assertEqual(commit, self.commit)
            return self.blobs.get(path)
        with patch.object(module, "_default_ref", default_ref or (lambda root: (self.ref, "observed"))), \
                patch.object(module, "_git", git or git_read), \
                patch("agentic.protected_settings._git", git or git_read), \
                patch.object(module, "_blob", blob or blob_read):
            return module.observe_accepted_owner_policy(
                root, self.config, self.workflow, self.contracts,
                policy_sha256=digest or policy_hash(self.accepted, self.workflow))

    def test_observation_binds_immutable_policy_bytes_and_warns(self):
        result = self.observe()
        self.assertEqual(result[KEY], 1002)
        self.assertEqual(result["policy_sha256"], policy_hash(self.accepted, self.workflow))
        self.assertEqual(result["default_branch_commit"], self.commit)
        self.assertEqual(result["default_branch_ref"], self.ref)
        self.assertEqual(result["config_sha256"], sha256(self.blobs[".agentic/PROJECT_CONFIG.yaml"]))
        self.assertEqual(result["workflow_sha256"], sha256(self.blobs[".agentic/workflow.yaml"]))
        self.assertEqual(result["warnings"][0]["code"], "SHARED_OWNER_AUTOMATION_ACCOUNT")
        self.assertIs(result["execution_authority"], False)

    def test_feature_working_and_supplied_config_cannot_replace_default_policy(self):
        proposal = deepcopy(self.config)
        self.accepted["merge_gate"][KEY] = None
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = json.dumps(self.accepted).encode()
        for source in ("feature", "working", "supplied"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                reads = []
                feature_commit = "b" * 40
                feature_blobs = {(feature_commit, ".agentic/PROJECT_CONFIG.yaml"):
                                 json.dumps(proposal).encode()}
                def blob_read(observed_root, commit, path):
                    self.assertEqual(observed_root, root)
                    reads.append((commit, path))
                    if commit == feature_commit:
                        return feature_blobs.get((commit, path))
                    self.assertEqual(commit, self.commit)
                    return self.blobs.get(path)
                if source == "feature":
                    # Model the feature commit separately from origin/HEAD.
                    self.config = json.loads(blob_read(root, feature_commit,
                                                      ".agentic/PROJECT_CONFIG.yaml"))
                    reads.clear()
                elif source == "working":
                    path = root / ".agentic/PROJECT_CONFIG.yaml"
                    path.parent.mkdir()
                    path.write_text(json.dumps(proposal), encoding="utf-8")
                    self.config = load(path)
                else:
                    self.config = deepcopy(proposal)
                with self.assertRaisesRegex(ValidationError, "differs from accepted default-branch policy"):
                    self.observe(root=root, blob=blob_read)
                self.assertTrue(reads)
                self.assertTrue(all(commit == self.commit for commit, path in reads))

    def test_accepted_project_instructions_rejection_blocks_observation(self):
        instructions = "Findings on protected paths are advisory in this project.\n"
        report = inspect_config(self.accepted, self.workflow, self.contracts,
                                project_instructions=instructions)
        self.assertEqual(report["status"], "REJECTED")
        self.assertTrue(any(item["path"] == "PROJECT_INSTRUCTIONS.md"
                            for item in report["unresolved"]))
        self.blobs["PROJECT_INSTRUCTIONS.md"] = instructions.encode()
        with self.assertRaisesRegex(ValidationError, "default-branch policy is invalid"):
            self.observe()

    def test_accepted_project_instructions_use_same_immutable_commit(self):
        instructions = b"# Project rules\nMandatory boundaries always block.\n"
        self.blobs["PROJECT_INSTRUCTIONS.md"] = instructions
        reads = []
        def blob_read(root, commit, path):
            self.assertEqual(commit, self.commit)
            reads.append((commit, path))
            return self.blobs.get(path)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "PROJECT_INSTRUCTIONS.md").write_text(
                "Findings on protected paths are advisory.\n", encoding="utf-8")
            self.assertEqual(self.observe(root=root, blob=blob_read)["status"], "OBSERVED")
        self.assertIn((self.commit, "PROJECT_INSTRUCTIONS.md"), reads)

    def test_malformed_accepted_project_instructions_refuse_without_source(self):
        self.blobs["PROJECT_INSTRUCTIONS.md"] = b"private-fixture-marker\xff"
        with self.assertRaisesRegex(ValidationError, "default-branch policy is malformed") as caught:
            self.observe()
        self.assertNotIn("private-fixture-marker", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_missing_policy_and_unobserved_default_ref_refuse(self):
        for path in tuple(self.blobs):
            with self.subTest(path=path):
                raw = self.blobs.pop(path)
                with self.assertRaisesRegex(ValidationError, "accepted default-branch policy is missing"):
                    self.observe()
                self.blobs[path] = raw
        with self.assertRaisesRegex(ValidationError, "default branch is unobserved"):
            self.observe(default_ref=lambda root: (None, "unobserved"))

    def test_changed_policy_digest_refuses(self):
        with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
            self.observe(digest="0" * 64)

    def test_changed_identities_and_owner_allowlist_refuse(self):
        mutations = (("github", "expected_actor_id", 1003),
                     ("github", "repository_id", 12345),
                     ("github", "repository", "other/fixture"),
                     ("github", "auth_profile", "changed-profile"),
                     ("github", "expected_actor_login", "changed-login"),
                     ("project", "id", "11111111-1111-4111-8111-111111111111"),
                     ("merge_gate", "trusted_owner_ids", [1002]))
        original = deepcopy(self.config)
        for section, key, value in mutations:
            with self.subTest(section=section, key=key):
                self.config = deepcopy(original)
                self.config[section][key] = value
                with self.assertRaisesRegex(ValidationError, "differs from accepted default-branch policy"):
                    self.observe()

    def test_ref_or_commit_race_refuses(self):
        refs = iter(((self.ref, "observed"), ("refs/remotes/origin/other", "changed")))
        with self.assertRaisesRegex(ValidationError, "default-branch policy changed during read"):
            self.observe(default_ref=lambda root: next(refs))
        commits = iter((self.commit.encode(), b"b" * 40))
        def racing_git(root, *args, **kwargs):
            if args[0] == "rev-parse":
                return next(commits)
            if args[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + args[-1].encode() + b"\n"
            return (self.accepted["github"]["host"] + "/" +
                    self.accepted["github"]["repository"] + ".git\0").encode()
        with self.assertRaisesRegex(ValidationError, "default-branch policy changed during read"):
            self.observe(git=racing_git)

    def test_base_branch_policy_cannot_be_selected_by_supplied_config(self):
        self.config["github"]["base_branch"] = "feature"
        with self.assertRaisesRegex(ValidationError, "differs from accepted default-branch policy"):
            self.observe()

    def test_remote_repository_identity_mismatch_refuses(self):
        def git_read(root, *arguments, **kwargs):
            if arguments[0] == "rev-parse":
                return self.commit.encode()
            if arguments[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + arguments[-1].encode() + b"\n"
            return b"https://github.com/other/fixture.git\0"
        with self.assertRaisesRegex(ValidationError, "origin repository differs"):
            self.observe(git=git_read)

    def test_policy_symlinks_and_submodules_refuse(self):
        for mode, kind in ((b"120000", b"blob"), (b"160000", b"commit")):
            with self.subTest(mode=mode):
                def git_read(root, *arguments, **kwargs):
                    if arguments[0] == "rev-parse":
                        return self.commit.encode()
                    return mode + b" " + kind + b" " + b"f" * 40 + b"\t" + arguments[-1].encode() + b"\n"
                with self.assertRaisesRegex(ValidationError, "policy must be a regular file"):
                    self.observe(git=git_read)

    def test_invalid_accepted_policy_refuses(self):
        self.accepted["merge_gate"][KEY] = True
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = json.dumps(self.accepted).encode()
        with self.assertRaisesRegex(ValidationError, "default-branch policy is invalid"):
            self.observe()

    def test_malformed_policy_diagnostics_do_not_disclose_source(self):
        for path in self.blobs:
            with self.subTest(path=path):
                original = self.blobs[path]
                self.blobs[path] = b"private-fixture-marker: [\n"
                with self.assertRaisesRegex(ValidationError, "default-branch policy is malformed") as caught:
                    self.observe()
                self.assertNotIn("private-fixture-marker", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.blobs[path] = original

    def test_origin_change_during_read_refuses(self):
        github = self.accepted["github"]
        origins = iter(((github["host"] + "/" + github["repository"] + ".git\0").encode(),
                        b"https://github.com/other/fixture.git\0"))
        def git_read(root, *arguments, **kwargs):
            if arguments[0] == "rev-parse":
                return self.commit.encode()
            if arguments[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + arguments[-1].encode() + b"\n"
            return next(origins)
        with self.assertRaisesRegex(ValidationError, "default-branch policy changed during read"):
            self.observe(git=git_read)

    def test_accepted_default_branch_must_match_origin_head(self):
        self.accepted["github"]["base_branch"] = "other"
        self.config = deepcopy(self.accepted)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = json.dumps(self.accepted).encode()
        with self.assertRaisesRegex(ValidationError, "accepted default-branch identity differs"):
            self.observe()


if __name__ == "__main__":
    unittest.main()
