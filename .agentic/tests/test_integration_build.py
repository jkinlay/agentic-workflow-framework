import copy
import hashlib
import importlib
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint


NOW = "2026-10-16T18:00:00Z"


def integration_build():
    return importlib.import_module("agentic.integration_build")


class IntegrationBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-build-test-")
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self._git("init", "--initial-branch=main")
        self._git("config", "user.name", "AWF Fixture")
        self._git("config", "user.email", "fixture@example.invalid")
        self.target = self._commit("src/base.txt", "base\n", "base")
        self.head1 = self._commit("src/one.txt", "one\n", "one")
        self.head2 = self._commit("src/two.txt", "two\n", "two")
        self.sources = [
            {"pr_number": 11, "head_sha": self.head1},
            {"pr_number": 12, "head_sha": self.head2},
        ]

    def tearDown(self):
        self.temporary.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ["git", *arguments], cwd=self.repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def _commit(self, path, content, message):
        destination = self.repo / Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
        self._git("add", "--", path)
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _request(self):
        module = integration_build()
        return module.build_request(
            "AWF-54-batch", self.target, self.sources,
            "python -B -m unittest", "refs/heads/awf/integration/AWF-54", 1,
        )

    def _build_outcome(self, request, *, tree_variant="stable"):
        module = integration_build()
        prior = request["target_sha"]
        construction = []
        prefix_results = []
        for index, source in enumerate(request["sources"], 1):
            commit_sha = hashlib.sha1(
                f"{prior}:{source['head_sha']}:{index}".encode("ascii")
            ).hexdigest()
            tree_sha = hashlib.sha1(
                f"{commit_sha}:{tree_variant}".encode("ascii")
            ).hexdigest()
            construction.append({
                "length": index,
                "base_commit_sha": prior,
                "head_sha": source["head_sha"],
                "parents": [prior, source["head_sha"]],
                "result_commit_sha": commit_sha,
                "result_tree_sha": tree_sha,
            })
            prefix_results.append({
                "length": index,
                "commit_sha": commit_sha,
                "tree_sha": tree_sha,
                "passed": True,
                "result_digest": hashlib.sha256(
                    f"tests:{index}:{tree_variant}".encode("ascii")
                ).hexdigest(),
                "receipt_sha256": "0" * 64,
            })
            prior = commit_sha
        deterministic = {
            "input_id": request["input_id"],
            "result_commit_sha": construction[-1]["result_commit_sha"],
            "result_tree_sha": construction[-1]["result_tree_sha"],
            "construction": construction,
            "prefix_results": [
                {key: value for key, value in item.items()
                 if key != "receipt_sha256"}
                for item in prefix_results
            ],
        }
        observation = {
            "producer_id": "fixture-builder",
            "run_id": str(uuid.uuid4()),
            "observed_at": NOW,
            "input_id": request["input_id"],
            "result_id": fingerprint("integration_build_result", deterministic),
        }
        observation["observation_id"] = fingerprint(
            "integration_build_observation", observation)
        return {
            "input_id": request["input_id"],
            "result_commit_sha": construction[-1]["result_commit_sha"],
            "result_tree_sha": construction[-1]["result_tree_sha"],
            "test_command": request["test_command"],
            "construction": construction,
            "prefix_results": prefix_results,
            "observation": observation,
        }

    def _registration(self, request, outcome):
        module = integration_build()
        registration = {
            "producer_kind": "integration_build",
            "implementation_sha256": "a" * 64,
            "release_sha256": "b" * 64,
            "capabilities": ["DETERMINISTIC_PREFIX_BUILD"],
            "runner": lambda value: copy.deepcopy(outcome),
            "receipts": {},
        }
        for prefix in outcome["prefix_results"]:
            receipt = module.prefix_receipt_sha256(
                registration, request, outcome["observation"], prefix)
            prefix["receipt_sha256"] = receipt
            registration["receipts"][
                f"{outcome['observation']['run_id']}:{prefix['length']}"
            ] = receipt
        return registration

    def test_same_inputs_have_the_same_input_id(self):
        first = self._request()
        second = self._request()
        self.assertEqual(first["input_id"], second["input_id"])

    def test_registered_build_binds_every_prefix_receipt(self):
        module = integration_build()
        request = self._request()
        outcome = self._build_outcome(request)
        registration = self._registration(request, outcome)
        record = module.run_registered_build(
            request, {"fixture-builder": registration}, "fixture-builder", now=NOW)
        self.assertTrue(record["green"])
        self.assertEqual(2, len(record["prefix_results"]))
        self.assertEqual(
            [self.target, record["construction"][0]["result_commit_sha"]],
            [step["parents"][0] for step in record["construction"]],
        )

    def test_missing_prefix_receipt_is_refused(self):
        module = integration_build()
        request = self._request()
        outcome = self._build_outcome(request)
        registration = self._registration(request, outcome)
        registration["receipts"].pop(
            f"{outcome['observation']['run_id']}:2")
        with self.assertRaisesRegex(ValidationError, "prefix receipt"):
            module.run_registered_build(
                request, {"fixture-builder": registration},
                "fixture-builder", now=NOW)

    def test_non_two_parent_prefix_is_refused(self):
        module = integration_build()
        request = self._request()
        outcome = self._build_outcome(request)
        outcome["construction"][1]["parents"] = [self.head2, self.head1]
        registration = self._registration(request, outcome)
        with self.assertRaisesRegex(ValidationError, "parents"):
            module.run_registered_build(
                request, {"fixture-builder": registration},
                "fixture-builder", now=NOW)

    def test_same_input_with_different_tree_is_refused(self):
        module = integration_build()
        request = self._request()
        first_outcome = self._build_outcome(request, tree_variant="first")
        second_outcome = self._build_outcome(request, tree_variant="second")
        first = module.validate_build(
            request, first_outcome,
            self._registration(request, first_outcome), now=NOW)
        second = module.validate_build(
            request, second_outcome,
            self._registration(request, second_outcome), now=NOW)
        with self.assertRaisesRegex(ValidationError, "same input.*different tree"):
            module.require_same_input_tree(first, second)

    def test_same_input_with_same_tree_is_accepted(self):
        module = integration_build()
        request = self._request()
        first_outcome = self._build_outcome(request)
        second_outcome = self._build_outcome(request)
        first = module.validate_build(
            request, first_outcome,
            self._registration(request, first_outcome), now=NOW)
        second = module.validate_build(
            request, second_outcome,
            self._registration(request, second_outcome), now=NOW)
        self.assertEqual(first["result_tree_sha"],
                         module.require_same_input_tree(first, second))

    def test_wrong_producer_kind_cannot_build(self):
        module = integration_build()
        request = self._request()
        outcome = self._build_outcome(request)
        registration = self._registration(request, outcome)
        registration["producer_kind"] = "merge_train_observer"
        with self.assertRaisesRegex(ValidationError, "integration-build producer"):
            module.run_registered_build(
                request, {"fixture-builder": registration},
                "fixture-builder", now=NOW)

    def test_runner_cannot_mutate_the_frozen_request(self):
        module = integration_build()
        request = self._request()
        outcome = self._build_outcome(request)
        registration = self._registration(request, outcome)

        def mutate(value):
            value["sources"].reverse()
            return copy.deepcopy(outcome)

        registration["runner"] = mutate
        with self.assertRaisesRegex(ValidationError, "mutated"):
            module.run_registered_build(
                request, {"fixture-builder": registration},
                "fixture-builder", now=NOW)


if __name__ == "__main__":
    unittest.main()
