"""AWF-117 AC2/AC3: commit refusals must reach the final merge gate."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from source_only import skip_unless_source_repo


class ProvenanceGateTests(unittest.TestCase):
    def exercise_refusal(self, trailer, reason, *, mutate=None, fail=True, before_result=False, after=None):
        # Imports stay inside the test: a planned production member must never
        # make this module undiscoverable.
        from agentic.canonical import canonical, fingerprint, load, sha256
        from agentic.contracts import Contracts
        from agentic.gates import evaluate, expected_binding
        from agentic.lifecycle import definition
        from agentic.provenance import check_commit_range
        from generate_examples import example_bundle
        from test_review_policy import (
            NOW, RuntimeAssignedGateConfig, bind_production_posting_fixture,
            bind_review_admission,
        )

        with tempfile.TemporaryDirectory(prefix="awf117-gate-") as raw:
            repository = Path(raw) / "repository"
            repository.mkdir()
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("GIT_")}
            environment.update(
                GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                GIT_AUTHOR_NAME="AWF Fixture", GIT_COMMITTER_NAME="AWF Fixture",
                GIT_AUTHOR_EMAIL="fixture@example.invalid",
                GIT_COMMITTER_EMAIL="fixture@example.invalid",
                GIT_AUTHOR_DATE="2026-09-09T11:00:00Z",
                GIT_COMMITTER_DATE="2026-09-09T11:00:00Z",
            )

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=repository, env=environment,
                    check=True, capture_output=True, text=True, timeout=30,
                ).stdout.strip()

            git("init", "--quiet", "--initial-branch=main", "--template=")
            (repository / "README.md").write_bytes(b"fixture\n")
            git("add", "--", "README.md")
            base_tree = git("write-tree")
            base = git("commit-tree", base_tree, "-m", "Fixture base")
            (repository / "src").mkdir()
            (repository / "src/feature.py").write_bytes(b"value = 1\n")
            git("add", "--", "src/feature.py")
            tree = git("write-tree")
            message = "Implement feature" + ("\n\n" + trailer if trailer else "")
            head = git("commit-tree", tree, "-p", base, "-m", message)
            for oid in (base, head, tree):
                self.assertRegex(oid, r"^[0-9a-f]{40}$")
            self.assertEqual(base, git("merge-base", base, head))

            def reproduce_manifest(_sha, paths):
                self.assertTrue(set(paths) <= {"MANIFEST.json", "MANIFEST.md"})
                return {path: b"fixture manifest\n" for path in paths}

            digest = fingerprint("awf117-manifest-reproducer", "fixture-v1")
            policy = {
                "require_run_logs": False, "default_ref": "refs/heads/main",
                "generated_paths": ["MANIFEST.json", "MANIFEST.md"],
                "generators": {"manifest-regen": {
                    "paths": ["MANIFEST.json", "MANIFEST.md"],
                    "reproducer_sha256": digest,
                }},
            }
            provider = {"version": 1, "repository_id": 101, "pr": 7,
                        "base_ref": "main", "base": base, "head": head,
                        "labels": [], "trusted_owner_ids": [], "comments": []}
            registry = {"generators": {"manifest-regen": {
                "reproducer_sha256": digest, "callable": reproduce_manifest,
            }}}
            registry["run_log_verifier"] = lambda run, commit, entries: {
                "version": 1, "status": "ABSENT", "run_id": run,
                "commit_sha": commit, "entries": entries,
                "evidence_sha256": None, "reason_code": "OPTIONAL_ABSENT"}
            checked = check_commit_range(
                repository, base, head, reviewed_policy=policy,
                provider_observation=provider, runtime_registry=registry,
            )
            self.assertEqual("REFUSE" if reason else "PASS", checked["status"], checked)
            if reason:
                self.assertEqual(head, checked["refusals"][0]["sha"])
                self.assertIn(reason, checked["refusals"][0]["reason"])
            acquisition = {"reviewed_policy": policy,
                           "provider_observation": provider,
                           "runtime_registry": registry}

            config_values = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
            self.bundle = example_bundle(config_values)
            candidate = self.bundle["candidate"]
            candidate.update(head_sha=head, target_base_sha=base,
                             merge_base_sha=base, head_tree_sha=tree,
                             integration_tree_sha=tree, tested_merge_sha=head)
            bundle = self.bundle
            bundle["contract"]["branch_origin_sha"] = base
            bundle["contract"]["scope"]["expected_paths"] = ["src/feature.py"]
            bundle["worker"].update(files_changed=["src/feature.py"],
                tested_tree=tree, changes=[{"path": "src/feature.py", "action": "added"}])
            bundle["worker"]["validation"][0]["tested_tree_sha"] = tree
            bundle["pr"]["file_manifest"][0].update(path="src/feature.py",
                blob_sha=git("rev-parse", tree + ":src/feature.py"))
            bundle["critic"]["coverage"].update(reviewed_paths=["src/feature.py"],
                file_manifest_sha256=fingerprint("file-manifest", bundle["pr"]["file_manifest"]))
            bundle["ci"]["checks"][0].update(tested_tree_sha=tree, tested_commit_sha=head)
            bundle["publication_scan"].update(base_sha=base, head_sha=head,
                                               commits_scanned=[head])
            for verdict in bundle["review_verdicts"]:
                verdict["head_sha"] = head
                verdict["candidate_binding"].update(base_sha=base, head_sha=head)

            class ProvenanceConfig(RuntimeAssignedGateConfig):
                def get(inner, key, default=None):
                    value = super().get(key, default)
                    if key == "_awf_runtime":
                        value["commit_provenance"] = acquisition
                    if key == "_awf_runtime" and mutate:
                        mutate(inner, bundle, value)
                    return value

            config = ProvenanceConfig(config_values, self)
            self.checked = checked
            self.repository = repository
            if mutate:
                config.get("_awf_runtime")
            from agentic.policy import policy_hash
            bundle["contract"]["policy_hash"] = policy_hash(config, definition())
            binding = expected_binding(config, definition(), bundle)
            for name in ("dispatch", "worker", "critic", "ci", "pr"):
                bundle[name]["binding"] = copy.deepcopy(binding)
            for record in bundle["runs"] + bundle["review_verdicts"]:
                record["binding"] = copy.deepcopy(binding)
            bind_review_admission(bundle)
            posting = bind_production_posting_fixture(config, bundle)
            gate = evaluate(config, definition(), bundle,
                Contracts(ROOT / ".agentic/schemas"), NOW,
                repository=repository, posting_collector_registry=posting)
            unrelated_failures = {name: result for name, result in gate["gates"].items()
                                  if name != "provenance"
                                  and result["result"] not in {"PASS", "N_A"}}
            self.assertEqual({}, unrelated_failures,
                             "The fixture must pass every unrelated gate")
            self.assertEqual("FAIL" if fail else "PASS", gate["gates"]["provenance"]["result"],
                "AWF-117 missing range enforcement: offending commit " + head)
            self.assertEqual("NOT_READY" if fail else "READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
            actual = "urn:awf:input:commit_provenance:" + fingerprint("gate-input", checked)
            retained = [ref for ref in gate["gates"]["provenance"]["evidence"]
                        if ref.startswith("urn:awf:input:commit_provenance:")]
            self.assertEqual([] if before_result else [actual], retained)
            self.assertFalse(gate["execution_authority"])
            if after:
                after(config, bundle, posting, checked, repository)

    @skip_unless_source_repo("AC2/AC3 build the example bundle with the source-only generator", "scripts/generate_examples.py")
    def test_ac2_merge_gate_refuses_untagged_code_commit(self):
        self.exercise_refusal(None, "missing Writer trailer")

    @skip_unless_source_repo("AC2/AC3 build the example bundle with the source-only generator", "scripts/generate_examples.py")
    def test_ac3_merge_gate_refuses_nonmechanical_script_commit(self):
        self.exercise_refusal("Writer: script/manifest-regen",
                              "not reproducibly mechanical")


if __name__ == "__main__":
    unittest.main()


class RuntimeTransportTests(ProvenanceGateTests):
    WRITER = "Writer: codex/11111111-1111-4111-8111-111111111111"

    def test_runtime_policy_omission_passes(self):
        self.exercise_refusal(self.WRITER, None, fail=False)

    def test_five_provider_candidate_comparisons(self):
        for key, value in (("repository_id", 102), ("pr", 8),
                           ("base_ref", "other"), ("base", "a" * 40),
                           ("head", "a" * 40)):
            with self.subTest(field=key):
                def mutate(config, bundle, runtime):
                    runtime["commit_provenance"]["provider_observation"][key] = value
                self.exercise_refusal(self.WRITER, None, mutate=mutate, before_result=True)

    def test_serialized_policy_equal_and_unequal(self):
        for equal in (True, False):
            with self.subTest(equal=equal):
                def mutate(config, bundle, runtime):
                    policy = copy.deepcopy(runtime["commit_provenance"]["reviewed_policy"])
                    if not equal:
                        policy["require_run_logs"] = True
                    config["commit_provenance"] = policy
                self.exercise_refusal(self.WRITER, None, mutate=mutate,
                                      fail=not equal, before_result=not equal)

    def test_complete_result_addressing_and_other_gates_remain_binding(self):
        def after(config, bundle, posting, checked, repository):
            from unittest.mock import patch
            from agentic import ValidationError
            from agentic.canonical import fingerprint
            from agentic.contracts import Contracts
            from agentic.gates import evaluate
            from agentic.lifecycle import definition
            from test_review_policy import NOW
            contracts = Contracts(ROOT / ".agentic/schemas")
            def gate():
                return evaluate(config, definition(), bundle, contracts, NOW,
                                repository=repository, posting_collector_registry=posting)
            for key in checked:
                changed = copy.deepcopy(checked)
                if key == "commits":
                    changed[key][0]["run_log"] = "DIFFERENT_NESTED_CONTENT"
                elif key == "refusals":
                    changed[key].append({"sha": checked["head"], "reason": "nested change"})
                elif key == "status":
                    changed[key] = "REFUSE"
                elif isinstance(changed[key], bool):
                    changed[key] = not changed[key]
                else:
                    changed[key] = "changed"
                with self.subTest(result_field=key), patch("agentic.gates.check_commit_range", return_value=changed) as checker:
                    result = gate()
                    checker.assert_called_once_with(repository,
                        bundle["candidate"]["merge_base_sha"], bundle["candidate"]["head_sha"],
                        **config.get("_awf_runtime")["commit_provenance"])
                    refs = result["gates"]["provenance"]["evidence"]
                    self.assertIn("urn:awf:input:commit_provenance:" + fingerprint("gate-input", changed), refs)
            with patch("agentic.gates.check_commit_range", side_effect=ValidationError("unavailable")):
                result = gate()
                self.assertEqual("FAIL", result["gates"]["provenance"]["result"])
                self.assertFalse(any(ref.startswith("urn:awf:input:commit_provenance:") for ref in result["gates"]["provenance"]["evidence"]))
            good = gate()
            for refs in ([], [ref for ref in good["gates"]["provenance"]["evidence"] if not ref.startswith("urn:awf:input:commit_provenance:")]):
                invalid = copy.deepcopy(good)
                invalid["gates"]["provenance"]["evidence"] = refs
                with self.assertRaises(ValidationError):
                    contracts.validate("final-gate", invalid)
            invalid = copy.deepcopy(good)
            invalid["gates"]["provenance"]["result"] = "N_A"
            with self.assertRaises(ValidationError):
                contracts.validate("final-gate", invalid)
            bundle["pr"]["mergeable"] = False
            result = gate()
            self.assertEqual("PASS", result["gates"]["provenance"]["result"])
            self.assertEqual("FAIL", result["gates"]["merge_compatibility"]["result"])
            self.assertEqual("NOT_READY", result["conclusion"])
        self.exercise_refusal(self.WRITER, None, fail=False, after=after)

    def test_policy_default_and_runtime_path_semantics(self):
        policies = [
            {"default_ref": "refs/heads/other"},
            {"generated_paths": ["../escape"]},
            {"generated_paths": ["a", "A"]},
            {"generated_paths": ["e\u0301"]},
            {"generated_paths": ["con.txt"]},
            {"generated_paths": ["a\x7f"]},
            {"generated_paths": ["a/" + "x" * 256]},
            {"generated_paths": ["\u00e9" * 2049]},
            {"generators": {"fixture": {"paths": ["undeclared"], "reproducer_sha256": "a" * 64}}},
        ]
        for policy in policies:
            with self.subTest(policy=policy):
                def mutate(config, bundle, runtime):
                    runtime["commit_provenance"]["reviewed_policy"].update(policy)
                self.exercise_refusal(self.WRITER, None, mutate=mutate, before_result=True)

    def test_closed_acquisition(self):
        for key in (None, "reviewed_policy", "provider_observation", "runtime_registry", "extra"):
            with self.subTest(field=key):
                def mutate(config, bundle, runtime):
                    if key is None:
                        runtime.pop("commit_provenance", None)
                    elif key == "extra":
                        runtime["commit_provenance"][key] = True
                    else:
                        runtime["commit_provenance"].pop(key, None)
                self.exercise_refusal(self.WRITER, None, mutate=mutate, before_result=True)

    def test_expected_reference_matches_or_retains_actual_on_mismatch(self):
        from agentic.canonical import fingerprint
        from agentic.provenance import check_commit_range
        for match in (True, False):
            with self.subTest(match=match):
                def mutate(config, bundle, runtime):
                    bundle["commit_provenance"] = {"result_ref":
                        "urn:awf:input:commit_provenance:" +
                        (fingerprint("gate-input", self.checked) if match else "0" * 64)}
                self.exercise_refusal(self.WRITER, None, mutate=mutate, fail=not match)


class MissingProvenanceTests(unittest.TestCase):
    def test_missing_acquisition_fails_with_zero_checker_references(self):
        from agentic.contracts import Contracts
        from agentic.gates import evaluate
        from agentic.lifecycle import definition
        from agentic.canonical import load
        from test_review_policy import RuntimeAssignedGateConfig, bind_review_admission, NOW
        from generate_examples import example_bundle
        values = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.bundle = example_bundle(values)
        class MissingConfig(RuntimeAssignedGateConfig):
            def get(inner, key, default=None):
                value = super().get(key, default)
                if key == "_awf_runtime":
                    value.pop("commit_provenance", None)
                return value
        bind_review_admission(self.bundle)
        gate = evaluate(MissingConfig(values, self), definition(), self.bundle,
                        Contracts(ROOT / ".agentic/schemas"), NOW)
        self.assertEqual("FAIL", gate["gates"]["provenance"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual([], [ref for ref in gate["gates"]["provenance"]["evidence"]
                              if ref.startswith("urn:awf:input:commit_provenance:")])


class SerializedContractTests(unittest.TestCase):
    def test_real_catalog_policy_and_expected_reference_shapes(self):
        from agentic import ValidationError
        from agentic.canonical import load
        from agentic.contracts import Contracts
        contracts = Contracts(ROOT / ".agentic/schemas")
        config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        bundle = load(ROOT / ".agentic/examples/evidence-bundle.json")
        policy = {"require_run_logs": False, "default_ref": "refs/heads/main",
                  "generated_paths": ["generated/a.json"], "generators": {
                      "fixture": {"paths": ["generated/a.json"],
                                  "reproducer_sha256": "a" * 64}}}
        contracts.validate("project-config", config)
        contracts.validate("evidence-bundle", bundle)
        config["commit_provenance"] = policy
        contracts.validate("project-config", config)
        for key in policy:
            invalid = copy.deepcopy(config)
            del invalid["commit_provenance"][key]
            with self.subTest(missing=key), self.assertRaises(ValidationError):
                contracts.validate("project-config", invalid)
        malformed = [None, {}, {**policy, "extra": True},
                     {**policy, "require_run_logs": 1},
                     {**policy, "default_ref": "main"},
                     {**policy, "default_ref": "refs/heads/a..b"},
                     {**policy, "default_ref": "refs/heads/a//b"},
                     {**policy, "default_ref": "refs/heads/a.lock"},
                     {**policy, "generated_paths": ["a", "a"]},
                     {**policy, "generators": {"BAD": policy["generators"]["fixture"]}},
                     {**policy, "generators": {"fixture": {"paths": []}}},
                     {**policy, "generators": {"fixture": {"paths": [],
                         "reproducer_sha256": "A" * 64}}},
                     {**policy, "generators": {"fixture": {"paths": [],
                         "reproducer_sha256": "a" * 64, "extra": True}}}]
        for value in malformed:
            with self.subTest(policy=value), self.assertRaises(ValidationError):
                contracts.validate("project-config", {**config, "commit_provenance": value})
        valid = {"result_ref": "urn:awf:input:commit_provenance:" + "a" * 64}
        contracts.validate("evidence-bundle", {**bundle, "commit_provenance": valid})
        for value in (None, {}, {**valid, "extra": True}, {"result_ref": "urn:awf:input:commit_provenance:" + "A" * 64}, {"result_ref": "https://example.invalid"}):
            with self.subTest(reference=value), self.assertRaises(ValidationError):
                contracts.validate("evidence-bundle", {**bundle, "commit_provenance": value})
