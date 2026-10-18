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
    def exercise_refusal(self, trailer, reason):
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
            checked = check_commit_range(
                repository, base, head, reviewed_policy=policy,
                provider_observation=provider, runtime_registry=registry,
            )
            self.assertEqual("REFUSE", checked["status"], checked)
            self.assertEqual(head, checked["refusals"][0]["sha"])
            self.assertIn(reason, checked["refusals"][0]["reason"])

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
                        # Doc 45 owner question 2: closed record vs recomputation
                        # remains open. Supply the existing checker inputs through
                        # the runtime channel, without inventing a bundle schema.
                        value["commit_provenance"] = {
                            "reviewed_policy": policy,
                            "provider_observation": provider,
                            "runtime_registry": registry,
                        }
                    return value

            config = ProvenanceConfig(config_values, self)
            binding = expected_binding(config, definition(), bundle)
            for name in ("dispatch", "worker", "critic", "ci", "pr"):
                bundle[name]["binding"] = copy.deepcopy(binding)
            for record in bundle["runs"] + bundle["review_verdicts"]:
                record["binding"] = copy.deepcopy(binding)
            bind_review_admission(bundle)
            posting = bind_production_posting_fixture(config, bundle)
            # Doc 45 owner question 1: use the fixed merge_base..head fixture;
            # do not decide whether an additional observed default OID is needed.
            # Questions 3/4 (override authority/carrier location) are out of scope.
            gate = evaluate(config, definition(), bundle,
                Contracts(ROOT / ".agentic/schemas"), NOW,
                repository=repository, posting_collector_registry=posting)
            unrelated_failures = {name: result for name, result in gate["gates"].items()
                                  if name != "provenance"
                                  and result["result"] not in {"PASS", "N_A"}}
            self.assertEqual({}, unrelated_failures,
                             "The fixture must pass every unrelated gate")
            self.assertEqual("FAIL", gate["gates"]["provenance"]["result"],
                "AWF-117 missing range enforcement: offending commit " + head)
            self.assertEqual("NOT_READY", gate["conclusion"])
            # Owner question 2 allows either the refusal itself or a content
            # address of the recomputed checker result. The candidate's head
            # alone is insufficient evidence of the commit refusal.
            retained = "\n".join(gate["gates"]["provenance"]["evidence"]
                                 + gate["residual_risks"])
            addresses = (fingerprint("gate-input", checked), sha256(canonical(checked)))
            self.assertTrue((head in retained and reason in retained)
                            or any(address in retained for address in addresses),
                            "Provenance evidence must bind the offending commit/refusal")

    @skip_unless_source_repo("AC2/AC3 build the example bundle with the source-only generator", "scripts/generate_examples.py")
    def test_ac2_merge_gate_refuses_untagged_code_commit(self):
        self.exercise_refusal(None, "missing Writer trailer")

    @skip_unless_source_repo("AC2/AC3 build the example bundle with the source-only generator", "scripts/generate_examples.py")
    def test_ac3_merge_gate_refuses_nonmechanical_script_commit(self):
        self.exercise_refusal("Writer: script/manifest-regen",
                              "not reproducibly mechanical")


if __name__ == "__main__":
    unittest.main()
