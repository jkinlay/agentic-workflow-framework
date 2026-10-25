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


def _rebind_posting_comment(bundle, posting, verdict, comment):
    import base64
    from agentic.canonical import canonical, loads, sha256
    from agentic.gates import (
        posting_collector_receipt_sha256, review_round_receipt_sha256,
    )
    observation = verdict["posting_observation"]
    observation.update(comment_bytes=comment,
                       comment_sha256=sha256(comment.encode("utf-8")))
    provider = loads(base64.b64decode(
        observation["provider_response_bytes"]["data"]).decode("utf-8"))
    provider["comment_body"] = comment
    raw = canonical(provider)
    observation.update(provider_response_bytes={"encoding": "base64",
        "data": base64.b64encode(raw).decode("ascii")},
        provider_response_sha256=sha256(raw), collector_receipt_sha256="0" * 64)
    registration = posting[observation["producer_id"]]
    digest = posting_collector_receipt_sha256(
        observation, sha256(canonical(observation)), registration)
    observation["collector_receipt_sha256"] = digest
    registration["receipts"][observation["run_id"]] = digest
    receipt = next(item for item in bundle["review_round_receipts"]
                   if item["critic_artifact_binding"]["round"] == verdict["round"])
    raw = canonical(observation)
    receipt.update(posting_observation_json={"encoding": "base64",
        "data": base64.b64encode(raw).decode("ascii")},
        posting_observation_sha256=sha256(raw))
    entry = next(item for item in bundle["evidence_registry"]
                 if item["uri"] == f"urn:awf:review-round-receipt:{verdict['round']}")
    entry["sha256"] = review_round_receipt_sha256(receipt)


class ProvenanceGateTests(unittest.TestCase):
    def exercise_refusal(self, trailer, reason, *, mutate=None, fail=True,
                         before_result=False, after=None):
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
            if reason:
                self.assertEqual("REFUSE", checked["status"], checked)
                self.assertEqual(head, checked["refusals"][0]["sha"])
                self.assertIn(reason, checked["refusals"][0]["reason"])
            else:
                self.assertEqual("PASS", checked["status"], checked)
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
                    if key == "_awf_runtime" and mutate:
                        mutate(inner, bundle, value)
                    return value

            config = ProvenanceConfig(config_values, self,
                commit_provenance=acquisition, repository=repository)
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
            for verdict in bundle["review_verdicts"]:
                _rebind_posting_comment(bundle, posting, verdict,
                    verdict["posting_observation"]["comment_bytes"])
            # Exercise the retained config-instance repository transport.
            from unittest.mock import patch
            with patch("agentic.gates.provenance.check_commit_range",
                       wraps=check_commit_range) as checker:
                gate = evaluate(config, definition(), bundle,
                    Contracts(ROOT / ".agentic/schemas"), NOW,
                    posting_collector_registry=posting)
            unrelated_failures = {name: result for name, result in gate["gates"].items()
                                  if name != "provenance"
                                  and result["result"] not in {"PASS", "N_A"}}
            self.assertEqual({}, unrelated_failures,
                             "The fixture must pass every unrelated gate")
            if before_result:
                checker.assert_not_called()
            else:
                checker.assert_called_once_with(repository, base, head,
                    reviewed_policy=policy, provider_observation=provider,
                    runtime_registry=registry, override=None,
                    override_predecessors=[], reviewed_owner_config=None)
            if fail:
                self.assertEqual("FAIL", gate["gates"]["provenance"]["result"],
                    "AWF-117 missing range enforcement: offending commit " + head)
                self.assertEqual("NOT_READY", gate["conclusion"])
            else:
                self.assertEqual("PASS", gate["gates"]["provenance"]["result"])
                self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
            if reason:
                # Owner question 2 allows either the refusal itself or a content
                # address of the recomputed checker result. The candidate's head
                # alone is insufficient evidence of the commit refusal.
                retained = "\n".join(gate["gates"]["provenance"]["evidence"]
                                     + gate["residual_risks"])
                addresses = (fingerprint("gate-input", checked), sha256(canonical(checked)))
                self.assertTrue((head in retained and reason in retained)
                                or any(address in retained for address in addresses),
                                "Provenance evidence must bind the offending commit/refusal")
            actual = "urn:awf:input:commit_provenance:" + fingerprint("gate-input", checked)
            retained_refs = [ref for ref in gate["gates"]["provenance"]["evidence"]
                             if ref.startswith("urn:awf:input:commit_provenance:")]
            self.assertEqual([] if before_result else [actual], retained_refs)
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
                with self.subTest(result_field=key), patch("agentic.gates.provenance.check_commit_range", return_value=changed) as checker:
                    result = gate()
                    checker.assert_called_once_with(repository,
                        bundle["candidate"]["merge_base_sha"], bundle["candidate"]["head_sha"],
                        **config.get("_awf_runtime")["commit_provenance"],
                        override=None, override_predecessors=[], reviewed_owner_config=None)
                    refs = result["gates"]["provenance"]["evidence"]
                    self.assertIn("urn:awf:input:commit_provenance:" + fingerprint("gate-input", changed), refs)
                    self.assertEqual(["urn:awf:input:commit_provenance:" + fingerprint("gate-input", changed)],
                        [ref for ref in refs if ref.startswith("urn:awf:input:commit_provenance:")])
                    self.assertNotEqual(fingerprint("gate-input", checked), fingerprint("gate-input", changed))
                    self.assertFalse(result["execution_authority"])
            with patch("agentic.gates.provenance.check_commit_range", side_effect=ValidationError("unavailable")):
                result = gate()
                self.assertEqual("FAIL", result["gates"]["provenance"]["result"])
                self.assertFalse(any(ref.startswith("urn:awf:input:commit_provenance:") for ref in result["gates"]["provenance"]["evidence"]))
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

    def test_provider_candidate_types_must_match(self):
        class SameText(str):
            pass
        for key in ("repository_id", "pr", "base_ref", "base", "head"):
            with self.subTest(field=key):
                def mutate(config, bundle, runtime):
                    provider = runtime["commit_provenance"]["provider_observation"]
                    selected = {"repository_id": "repository_id", "pr": "pr_number",
                                "base_ref": "target_base_branch", "base": "target_base_sha",
                                "head": "head_sha"}[key]
                    value = bundle["candidate"][selected]
                    provider[key] = float(value) if type(value) is int else SameText(value)
                self.exercise_refusal(self.WRITER, None, mutate=mutate, before_result=True)

    def test_explicit_repository_precedes_runtime_repository(self):
        def after(config, bundle, posting, checked, repository):
            from unittest.mock import patch
            from agentic.gates import evaluate
            from agentic.lifecycle import definition
            from agentic.contracts import Contracts
            from test_review_policy import NOW
            config.repository = repository / "not-selected"
            with patch("agentic.gates.provenance.check_commit_range", return_value=checked) as checker:
                result = evaluate(config, definition(), bundle,
                    Contracts(ROOT / ".agentic/schemas"), NOW,
                    repository=repository, posting_collector_registry=posting)
                checker.assert_called_once_with(repository,
                    bundle["candidate"]["merge_base_sha"], bundle["candidate"]["head_sha"],
                    **config.commit_provenance, override=None,
                    override_predecessors=[], reviewed_owner_config=None)
                self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", result["conclusion"])
        self.exercise_refusal(self.WRITER, None, fail=False, after=after)


class MissingProvenanceTests(unittest.TestCase):
    def test_shared_helper_default_refuses_missing_provenance(self):
        from agentic.gates import evaluate
        from agentic.lifecycle import definition
        from test_review_policy import (
            Fixture, NOW, RuntimeAssignedGateConfig,
            bind_production_posting_fixture, bind_review_admission,
        )
        Fixture.setUpClass()
        fixture = Fixture(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.config = RuntimeAssignedGateConfig(fixture.config, fixture)
        self.assertNotIn("commit_provenance", fixture.config.get("_awf_runtime"))
        bind_review_admission(fixture.bundle)
        registry = bind_production_posting_fixture(fixture.config, fixture.bundle)
        gate = evaluate(fixture.config, definition(), fixture.bundle,
            fixture.contracts, NOW, posting_collector_registry=registry)
        self.assertEqual("FAIL", gate["gates"]["provenance"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertTrue(any("Commit provenance runtime inputs are missing" in risk
                            for risk in gate["residual_risks"]))
        self.assertEqual([], [ref for ref in gate["gates"]["provenance"]["evidence"]
                              if ref.startswith("urn:awf:input:commit_provenance:")])
        self.assertEqual({}, {name: result for name, result in gate["gates"].items()
                             if name != "provenance" and result["result"] not in {"PASS", "N_A"}})


    def test_missing_acquisition_fails_with_zero_checker_references(self):
        from agentic.contracts import Contracts
        from agentic.gates import evaluate
        from agentic.lifecycle import definition
        from agentic.canonical import load
        from test_review_policy import (RuntimeAssignedGateConfig, bind_review_admission,
                                        bind_production_posting_fixture, NOW)
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
        config = MissingConfig(values, self)
        registry = bind_production_posting_fixture(config, self.bundle)
        gate = evaluate(config, definition(), self.bundle,
                        Contracts(ROOT / ".agentic/schemas"), NOW,
                        posting_collector_registry=registry)
        self.assertEqual("FAIL", gate["gates"]["provenance"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual([], [ref for ref in gate["gates"]["provenance"]["evidence"]
                              if ref.startswith("urn:awf:input:commit_provenance:")])
        self.assertEqual({}, {name: result for name, result in gate["gates"].items()
                             if name != "provenance" and result["result"] not in {"PASS", "N_A"}})


class ReconciliationDiscoveryTests(unittest.TestCase):
    def test_both_missing_provenance_methods_are_discovered_once(self):
        import ast
        module = sys.modules[__name__]
        expected = ["test_missing_acquisition_fails_with_zero_checker_references",
                    "test_shared_helper_default_refuses_missing_provenance"]
        self.assertEqual(expected, unittest.defaultTestLoader.getTestCaseNames(MissingProvenanceTests))
        def ids(suite):
            for test in suite:
                if isinstance(test, unittest.TestSuite):
                    yield from ids(test)
                else:
                    yield test.id()
        collected = list(ids(unittest.defaultTestLoader.loadTestsFromModule(module)))
        for method in expected:
            self.assertEqual(1, collected.count(__name__ + ".MissingProvenanceTests." + method))
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        self.assertEqual(1, sum(isinstance(node, ast.ClassDef) and node.name == "MissingProvenanceTests"
                                for node in tree.body))


class RemainderGateTests(unittest.TestCase):
    """AC4/AC5 use real commit objects and fully rebound posting receipts."""

    def prepare(self, *, rounds=1):
        from test_review_policy import (
            Fixture, bind_production_posting_fixture, bind_review_admission,
        )
        Fixture.setUpClass()
        self.fixture = Fixture(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.bundle = self.fixture.bundle
        self.config = self.fixture.config
        if rounds > 1:
            from test_review_tiers_195_gates import ReviewTiers195GateTests
            ReviewTiers195GateTests._review_history(self.fixture, rounds)
        bind_review_admission(self.bundle)
        self.posting = bind_production_posting_fixture(self.config, self.bundle)
        for verdict in self.bundle["review_verdicts"]:
            self.set_comment(verdict, verdict["posting_observation"]["comment_bytes"])

    def set_comment(self, verdict, comment):
        _rebind_posting_comment(self.bundle, self.posting, verdict, comment)

    def gate(self):
        from agentic.gates import evaluate
        from agentic.lifecycle import definition
        from test_review_policy import NOW
        return evaluate(self.config, definition(), self.bundle,
                        self.fixture.contracts, NOW,
                        posting_collector_registry=self.posting)

    def assert_ready(self):
        gate = self.gate()
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"], gate)
        return gate

    def assert_carrier_refused(self):
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("PASS", gate["gates"]["provenance"]["result"])

    def test_ac4_matching_carriers_accept_historical_heads(self):
        self.prepare(rounds=2)
        self.assertNotEqual(self.bundle["candidate"]["head_sha"],
                            self.bundle["review_verdicts"][0]["head_sha"])
        self.assert_ready()

    def test_ac4_missing_terminal_carrier_refuses_rebound_evidence(self):
        self.prepare()
        self.assert_ready()
        verdict = self.bundle["review_verdicts"][-1]
        self.set_comment(verdict, self.bundle["review_round_receipts"][-1]["review_verdict_json"])
        self.assert_carrier_refused()

    def test_ac4_missing_nonterminal_carrier_refuses_rebound_evidence(self):
        self.prepare(rounds=2)
        self.assert_ready()
        verdict = self.bundle["review_verdicts"][0]
        self.set_comment(verdict, self.bundle["review_round_receipts"][0]["review_verdict_json"])
        self.assert_carrier_refused()

    def test_ac4_wrong_uuid_duplicate_misplaced_and_lookalike_carriers(self):
        self.prepare(rounds=2)
        self.assert_ready()
        verdict = self.bundle["review_verdicts"][0]
        text = self.bundle["review_round_receipts"][0]["review_verdict_json"]
        carrier = "Critic run: " + verdict["run_id"]
        variants = {
            "wrong_uuid": text + "\n\nCritic run: 11111111-1111-4111-8111-111111111111",
            "duplicate": text + "\n\n" + carrier + "\n" + carrier,
            "misplaced": carrier + "\n\n" + text,
            "lookalike": text + "\n\n" + carrier.replace("Critic", "Cr\u0456tic"),
            "variant": text + "\n\n" + carrier.replace("Critic", "critic"),
            "nil_uuid": text + "\n\nCritic run: 00000000-0000-0000-0000-000000000000",
        }
        for name, comment in variants.items():
            with self.subTest(case=name):
                self.set_comment(verdict, comment)
                self.assert_carrier_refused()

    def test_ac4_missing_run_id_already_refuses(self):
        from agentic import ValidationError
        self.prepare()
        self.assert_ready()
        del self.bundle["review_verdicts"][0]["run_id"]
        with self.assertRaises(ValidationError):
            self.gate()

    def test_ac4_wrong_head_or_result_digest_already_refuses(self):
        for field, value in (("head_sha", "9" * 40), ("result_sha256", "f" * 64)):
            with self.subTest(field=field):
                self.prepare()
                self.assert_ready()
                self.bundle["review_verdicts"][0]["critic_artifact_binding"][field] = value
                self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def prepare_override(self, *, superseding=False, labels=True, refusing=True):
        from agentic.canonical import fingerprint
        from agentic.provenance import (
            check_commit_range, record_owner_override,
            supersede_owner_override,
        )
        from test_review_policy import bind_production_posting_fixture, bind_review_admission
        self.prepare()
        self.context = self.config.commit_provenance
        self.repository = self.config.repository
        candidate = self.bundle["candidate"]
        if refusing:
            environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                GIT_AUTHOR_NAME="AWF Fixture", GIT_COMMITTER_NAME="AWF Fixture",
                GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                GIT_AUTHOR_DATE="2026-09-09T11:00:00Z", GIT_COMMITTER_DATE="2026-09-09T11:00:00Z")
            result = subprocess.run(["git", "commit-tree", candidate["head_tree_sha"],
                "-p", candidate["merge_base_sha"], "-m", "Untagged fixture implementation"],
                cwd=self.repository, env=environment, check=True, capture_output=True,
                text=True, timeout=30)
            head = result.stdout.strip()
            candidate.update(head_sha=head, tested_merge_sha=head)
            self.context["provider_observation"]["head"] = head
            self.bundle["ci"]["checks"][0]["tested_commit_sha"] = head
            self.bundle["publication_scan"].update(head_sha=head, commits_scanned=[head])
            for verdict in self.bundle["review_verdicts"]:
                verdict["head_sha"] = head
                verdict["candidate_binding"]["head_sha"] = head
            self.fixture.rebind()
            bind_review_admission(self.bundle)
            self.posting = bind_production_posting_fixture(self.config, self.bundle)
            verdict = self.bundle["review_verdicts"][0]
            self.set_comment(verdict, verdict["posting_observation"]["comment_bytes"])
        checked = check_commit_range(self.repository, candidate["merge_base_sha"],
                                    candidate["head_sha"], **self.context)
        self.assertEqual("REFUSE" if refusing else "PASS", checked["status"])
        if refusing:
            self.assertIn("missing Writer trailer", checked["refusals"][0]["reason"])
        payload = {"config_sha256": "a" * 64, "trusted_owner_ids": [1001]}
        self.owner_config = {**payload,
            "binding_sha256": fingerprint("provenance_owner_config", payload)}
        state = {}
        record = record_owner_override(state, who=1001, when="2026-09-09T11:30:00Z",
            why="Reviewed exceptional provenance", pr=candidate["pr_number"],
            covered_shas=[candidate["head_sha"]], reviewed_owner_config=self.owner_config)
        predecessors = []
        if superseding:
            predecessors = [record]
            record = supersede_owner_override(state, who=1001, when="2026-09-09T11:40:00Z",
                why="Reviewed superseding provenance", pr=candidate["pr_number"],
                covered_shas=[candidate["head_sha"]], supersedes=record["override_sha256"],
                reviewed_owner_config=self.owner_config)
        self.context.update(override=record, override_predecessors=predecessors,
                            reviewed_owner_config=self.owner_config)
        provider = self.context["provider_observation"]
        provider.update(config_sha256=payload["config_sha256"], trusted_owner_ids=[9999],
                        labels=["fallback: bot-written"] if labels else [])
        self.refresh_override_comment()

    def refresh_override_comment(self):
        from agentic.canonical import sha256
        from agentic.provenance import render_owner_override
        body = render_owner_override(self.context["override"])
        self.context["provider_observation"]["comments"] = [{
            "id": 501, "url": "https://github.com/fixture/example/pull/7#issuecomment-501",
            "actor_id": self.context["override"]["who"], "body": body,
            "body_sha256": sha256(body.encode("utf-8")),
        }]

    def reseal_override(self):
        from agentic.canonical import fingerprint
        record = self.context["override"]
        record["override_sha256"] = fingerprint("provenance_override",
            {key: value for key, value in record.items() if key != "override_sha256"})
        self.refresh_override_comment()

    def assert_override_refused(self):
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["provenance"]["result"], gate)
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertTrue(all(value["result"] in {"PASS", "N_A"}
                            for name, value in gate["gates"].items() if name != "provenance"), gate)

    def test_ac5_recorded_provider_override_passes_real_refusal(self):
        self.prepare_override()
        from agentic.canonical import fingerprint
        from agentic.provenance import check_commit_range
        candidate = self.bundle["candidate"]
        checked = check_commit_range(self.repository, candidate["merge_base_sha"],
                                    candidate["head_sha"], **self.context)
        self.assertEqual("PASS", checked["status"])
        self.assertEqual("OVERRIDDEN", checked["commits"][0]["status"])
        gate = self.assert_ready()
        self.assertIn("urn:awf:input:commit_provenance:" + fingerprint("gate-input", checked),
                      gate["gates"]["provenance"]["evidence"])

    def test_ac5_override_authority_remains_label_independent(self):
        self.prepare_override(labels=False)
        self.assert_ready()

    def test_ac5_ordered_supersession_chain_passes(self):
        self.prepare_override(superseding=True)
        self.assert_ready()

    def test_ac5_closed_explicit_no_override_context_passes(self):
        self.prepare()
        self.config.commit_provenance.update(override=None, override_predecessors=[],
                                             reviewed_owner_config=None)
        self.assert_ready()

    def test_ac5_exact_fallback_label_alone_refuses(self):
        self.prepare_override()
        for key in ("override", "override_predecessors", "reviewed_owner_config"):
            del self.context[key]
        self.assert_override_refused()

    def test_ac5_invalid_authority_and_coverage_refuse(self):
        cases = ("stale_digest", "malformed_digest", "wrong_pr", "wrong_repository",
                 "untrusted_actor", "missing_comment", "ambiguous_comment",
                 "comment_digest", "owner_binding", "owner_config_mismatch",
                 "caller_owner_list", "missing_owner_config", "outside_range",
                 "unnecessary_coverage", "unknown_context_key", "partial_context",
                 "orphan_predecessors", "nonlist_predecessors")
        for case in cases:
            with self.subTest(case=case):
                self.prepare_override(refusing=case != "unnecessary_coverage")
                record = self.context["override"]
                provider = self.context["provider_observation"]
                if case in {"stale_digest", "malformed_digest"}:
                    record["override_sha256"] = "f" * 64 if case == "stale_digest" else "invalid"
                elif case in {"wrong_pr", "untrusted_actor", "outside_range"}:
                    field, value = {"wrong_pr": ("pr", 8), "untrusted_actor": ("who", 9999),
                                    "outside_range": ("covered_shas", ["9" * 40])}[case]
                    record[field] = value
                    self.reseal_override()
                elif case == "wrong_repository":
                    provider["repository_id"] += 1
                elif case == "missing_comment":
                    provider["comments"] = []
                elif case == "ambiguous_comment":
                    provider["comments"].append(dict(provider["comments"][0], id=502))
                elif case == "comment_digest":
                    provider["comments"][0]["body_sha256"] = "f" * 64
                elif case == "owner_binding":
                    self.owner_config["binding_sha256"] = "f" * 64
                elif case == "owner_config_mismatch":
                    provider["config_sha256"] = "b" * 64
                elif case == "caller_owner_list":
                    self.context["trusted_owner_ids"] = [1001]
                elif case == "missing_owner_config":
                    self.context["reviewed_owner_config"] = None
                elif case == "unknown_context_key":
                    self.context["candidate_summary"] = {"status": "PASS"}
                elif case == "partial_context":
                    del self.context["override_predecessors"]
                elif case == "orphan_predecessors":
                    self.context.update(override=None, override_predecessors=[record])
                elif case == "nonlist_predecessors":
                    self.context["override_predecessors"] = {}
                self.assert_override_refused()

    def test_ac5_invalid_predecessor_chain_refuses(self):
        for case in ("missing", "wrong_link", "stale_predecessor", "reversed"):
            with self.subTest(case=case):
                self.prepare_override(superseding=True)
                if case == "missing":
                    self.context["override_predecessors"] = []
                elif case == "wrong_link":
                    self.context["override"]["supersedes"] = "f" * 64
                    self.reseal_override()
                elif case == "stale_predecessor":
                    self.context["override_predecessors"][0]["why"] = "Changed after sealing"
                elif case == "reversed":
                    self.context["override_predecessors"] = [self.context["override"],
                        self.context["override_predecessors"][0]]
                self.assert_override_refused()

    def test_ac5_valid_override_keeps_unrelated_ci_gate_required(self):
        self.prepare_override()
        self.bundle["ci"]["checks"][0]["conclusion"] = "failure"
        gate = self.gate()
        self.assertEqual("PASS", gate["gates"]["provenance"]["result"])
        self.assertEqual("FAIL", gate["gates"]["required_ci"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])


class GeneratedProvenanceParityTests(unittest.TestCase):
    @skip_unless_source_repo("AC6 compares source producers with generated contracts", "scripts/generate_contracts.py")
    def test_ac6_inspected_schema_template_and_envelope_parity(self):
        import json
        from generate_contracts import catalog
        from generate_examples import sample
        schemas = catalog()
        for name in ("evidence-bundle", "final-gate", "review-cap-disposition",
                     "review-completion", "review-verdict"):
            with self.subTest(schema=name):
                self.assertEqual(schemas[name], json.loads(
                    (ROOT / f".agentic/schemas/{name}.schema.json").read_text(encoding="utf-8")))
        for name in ("review-verdict", "review-cap-disposition"):
            with self.subTest(template=name):
                template = json.loads((ROOT / f".agentic/templates/{name}.yaml").read_text(encoding="utf-8"))
                self.assertEqual("UNFILLED", template["status"])
                self.assertEqual(sample(schemas[name], schemas), template["record"])
        envelope = json.loads((ROOT / ".agentic/review-loop/critic-result.schema.json").read_text(encoding="utf-8"))
        self.assertEqual("#/$defs/criticBinding/properties/critic_run_carrier",
                         envelope["x-awf-sole-critic-run-carrier"])
        self.assertIn("critic_run_carrier", envelope["$defs"]["criticBinding"]["required"])
