import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint


NOW = "2026-10-17T10:05:00Z"


def merge_train():
    return importlib.import_module("agentic.merge_train")


class MergeTrainPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-train-publish-")
        self.root = Path(self.temporary.name)
        self.provider = {
            "host": "https://example.invalid",
            "repository": "owner/repository",
            "repository_id": 42,
        }
        self.target = hashlib.sha1(b"target").hexdigest()
        self.head = hashlib.sha1(b"batch-head").hexdigest()
        self.tree = hashlib.sha1(b"batch-tree").hexdigest()
        self.source_head = hashlib.sha1(b"source-head").hexdigest()
        self.source_tree = hashlib.sha1(b"source-tree").hexdigest()
        self.generation = self._generation()
        self.provider_state = self._expected_state()
        self.observer_registry = {}
        self.publisher_registry = {}
        self.provider_calls = []
        self.remote = {"ref": None, "pr": None, "ready": False}
        self._register_observer()
        self._register_publisher()

    def tearDown(self):
        self.temporary.cleanup()

    def _generation(self):
        module = merge_train()
        value = {
            "format": "awf-merge-train-generation-1",
            "batch_id": "1.9.5-20261017-1000",
            "target_branch": "main",
            "target_sha": self.target,
            "generation": 1,
            "provider_identity": copy.deepcopy(self.provider),
            "sources": [{
                "pr_number": 174,
                "head_sha": self.source_head,
                "tree_sha": self.source_tree,
                "admission_evidence_sha256": "a" * 64,
            }],
            "authorization_scope": "CONSTRUCTION_ONLY",
            "may_publish": False,
            "may_merge": False,
        }
        value["generation_id"] = fingerprint("merge_train_generation", value)
        return value

    def _expected_state(self):
        return {
            "generation_id": self.generation["generation_id"],
            "target_branch": "main",
            "target_sha": self.target,
            "sources": copy.deepcopy(self.generation["sources"]),
        }

    def _register_observer(self, observed_at=NOW):
        module = merge_train()
        receipts = {}

        def observer(request):
            if request["operation"] == "BATCH_PUBLICATION_CANDIDATE":
                result = copy.deepcopy(request["expected_candidate"])
            elif request["operation"] == "BATCH_PUBLICATION_STATE":
                result = copy.deepcopy(self.provider_state)
            else:
                raise AssertionError(request["operation"])
            request_sha = fingerprint("merge_train_provider_request", request)
            material = {
                "producer_id": "fixture-observer",
                "observed_at": observed_at,
                "request_sha256": request_sha,
                "result": result,
            }
            observation = {
                "status": "OBSERVED",
                "producer_id": "fixture-observer",
                "observation_id": fingerprint(
                    "merge_train_provider_observation", material),
                "observed_at": observed_at,
                "request_sha256": request_sha,
                "result": result,
            }
            receipts[observation["observation_id"]] = (
                module.provider_receipt_sha256(registration, request, observation))
            return observation

        registration = {
            "producer_kind": "merge_train_observer",
            "implementation_sha256": "b" * 64,
            "release_sha256": "c" * 64,
            "capabilities": ["BATCH_PUBLICATION_CANDIDATE",
                             "BATCH_PUBLICATION_STATE"],
            "provider_identity": copy.deepcopy(self.provider),
            "receipt_namespace": "awf-merge-train-observer-v1",
            "observer": observer,
            "receipts": receipts,
        }
        self.observer_registry["fixture-observer"] = registration

    def _register_publisher(self, *, lose_pr_response=False):
        def push_batch_ref(branch, head):
            self.provider_calls.append(("push_batch_ref", branch, head))
            self.remote["ref"] = {"branch": branch, "head_sha": head}
            return copy.deepcopy(self.remote["ref"])

        def create_draft_pr(branch, base, title):
            self.provider_calls.append(("create_draft_pr", branch, base, title))
            self.remote["pr"] = {
                "number": 211, "branch": branch, "base_branch": base,
                "head_sha": self.head, "title": title, "draft": True,
            }
            if lose_pr_response:
                raise TimeoutError("provider response lost")
            return copy.deepcopy(self.remote["pr"])

        def mark_draft_pr_ready(number):
            self.provider_calls.append(("mark_draft_pr_ready", number))
            self.remote["ready"] = True
            self.remote["pr"]["draft"] = False
            return copy.deepcopy(self.remote["pr"])

        registration = {
            "producer_kind": "merge_train_publisher",
            "adapter_kind": "gh",
            "implementation_sha256": "d" * 64,
            "release_sha256": "e" * 64,
            "capabilities": ["PUSH_BATCH_REF", "CREATE_DRAFT_PR",
                             "MARK_DRAFT_PR_READY"],
            "provider_identity": copy.deepcopy(self.provider),
            "receipt_namespace": "awf-merge-train-publisher-v1",
            "push_batch_ref": push_batch_ref,
            "create_draft_pr": create_draft_pr,
            "mark_draft_pr_ready": mark_draft_pr_ready,
            "observe_batch_ref": lambda branch: copy.deepcopy(self.remote["ref"]),
            "observe_pull_request": lambda branch: copy.deepcopy(self.remote["pr"]),
        }
        self.publisher_registry["fixture-publisher"] = registration

    def _candidate(self, *, now=NOW, max_batch_size=4):
        return merge_train().freeze_publication_candidate(
            self.generation, self.head, self.tree, release="1.9.5",
            scheduled_at="2026-10-17T10:00:00Z", max_batch_size=max_batch_size,
            observer_id="fixture-observer", observer_registry=self.observer_registry,
            now=now)

    def _publish(self, candidate, journal="journal.json", mark_ready=False):
        return merge_train().publish_batch(
            candidate, observer_id="fixture-observer",
            observer_registry=self.observer_registry,
            publisher_id="fixture-publisher",
            publisher_registry=self.publisher_registry,
            intent_path=self.root / journal, now=NOW, mark_ready=mark_ready)

    def test_publication_requires_authenticated_exact_batch_candidate(self):
        candidate = self._candidate()
        forged = copy.deepcopy(candidate)
        forged["batch_head_sha"] = hashlib.sha1(b"forged").hexdigest()
        with self.assertRaisesRegex(ValidationError, "candidate"):
            self._publish(forged)
        self.assertEqual([], self.provider_calls)

        stale_registry = self.observer_registry
        self.observer_registry = {}
        self._register_observer(observed_at="2026-10-17T08:00:00Z")
        with self.assertRaisesRegex(ValidationError, "fresh"):
            self._candidate()
        self.observer_registry = stale_registry

        result = self._publish(candidate)
        self.assertEqual("PUBLISHED_DRAFT", result["status"])
        self.assertEqual(["push_batch_ref", "create_draft_pr"],
                         [call[0] for call in self.provider_calls])

    def test_batch_branch_title_cadence_and_maximum_size(self):
        candidate = self._candidate(max_batch_size=1)
        self.assertEqual("batch/1.9.5-20261017-1000",
                         candidate["batch_branch"])
        self.assertEqual("1.9.5-20261017-1000 (PRs #174)",
                         candidate["draft_pr_title"])
        with self.assertRaisesRegex(ValidationError, "fixed batch slot"):
            merge_train().freeze_publication_candidate(
                self.generation, self.head, self.tree, release="1.9.5",
                scheduled_at="2026-10-17T11:00:00Z", max_batch_size=1,
                observer_id="fixture-observer",
                observer_registry=self.observer_registry, now=NOW)
        too_many = copy.deepcopy(self.generation)
        too_many["sources"].append({
            "pr_number": 175, "head_sha": "1" * 40,
            "tree_sha": "2" * 40, "admission_evidence_sha256": "3" * 64})
        too_many["generation_id"] = fingerprint(
            "merge_train_generation",
            {key: value for key, value in too_many.items()
             if key != "generation_id"})
        with self.assertRaisesRegex(ValidationError, "maximum batch size"):
            merge_train().freeze_publication_candidate(
                too_many, self.head, self.tree, release="1.9.5",
                scheduled_at="2026-10-17T10:00:00Z", max_batch_size=1,
                observer_id="fixture-observer",
                observer_registry=self.observer_registry, now=NOW)

    def test_freeze_is_rechecked_before_every_provider_mutation(self):
        for drift_after, expected_calls in [(0, []), (1, ["push_batch_ref"]),
                                            (2, ["push_batch_ref",
                                                 "create_draft_pr"])]:
            with self.subTest(drift_after=drift_after):
                self.provider_calls.clear()
                self.remote = {"ref": None, "pr": None, "ready": False}
                candidate = self._candidate()
                observations = {"count": 0}
                original = self.observer_registry["fixture-observer"]["observer"]

                def drifting(request):
                    if request["operation"] == "BATCH_PUBLICATION_STATE":
                        if observations["count"] >= drift_after:
                            self.provider_state["target_sha"] = "f" * 40
                        observations["count"] += 1
                    return original(request)

                self.observer_registry["fixture-observer"]["observer"] = drifting
                try:
                    with self.assertRaisesRegex(ValidationError, "abandoned"):
                        self._publish(candidate, journal=f"drift-{drift_after}.json",
                                      mark_ready=True)
                finally:
                    self.observer_registry["fixture-observer"]["observer"] = original
                    self.provider_state = self._expected_state()
                self.assertEqual(expected_calls,
                                 [call[0] for call in self.provider_calls])

    def test_push_pr_creation_orphan_race_is_recovered_without_replay(self):
        candidate = self._candidate()
        self.publisher_registry.clear()
        self._register_publisher(lose_pr_response=True)
        with self.assertRaisesRegex(ValidationError, "outcome is unknown"):
            self._publish(candidate)
        self.assertEqual(["push_batch_ref", "create_draft_pr"],
                         [call[0] for call in self.provider_calls])

        self.publisher_registry.clear()
        self._register_publisher(lose_pr_response=False)
        result = self._publish(candidate)
        self.assertEqual("PUBLISHED_DRAFT", result["status"])
        self.assertEqual(["push_batch_ref", "create_draft_pr"],
                         [call[0] for call in self.provider_calls])
        journal = json.loads((self.root / "journal.json").read_text("utf-8"))
        self.assertEqual("RECONCILED", journal["operations"][1]["status"])

    def test_merge_train_exposes_no_merge_or_target_push_api(self):
        module = merge_train()
        forbidden = {"merge", "merge_batch", "push_target", "push_target_ref"}
        self.assertTrue(forbidden.isdisjoint(set(dir(module))))
        registration = self.publisher_registry["fixture-publisher"]
        self.assertNotIn("merge", registration)
        self.assertNotIn("push_target_ref", registration)
        overpowered = copy.deepcopy(registration)
        overpowered["merge"] = lambda: None
        self.publisher_registry["overpowered"] = overpowered
        with self.assertRaisesRegex(ValidationError, "non-closed shape"):
            merge_train().publish_batch(
                self._candidate(), observer_id="fixture-observer",
                observer_registry=self.observer_registry,
                publisher_id="overpowered",
                publisher_registry=self.publisher_registry,
                intent_path=self.root / "overpowered.json", now=NOW)
        self.assertEqual([], self.provider_calls)
        self._publish(self._candidate())
        pushed = [call for call in self.provider_calls
                  if call[0] == "push_batch_ref"]
        self.assertEqual("batch/1.9.5-20261017-1000", pushed[0][1])
        script = (ROOT / ".agentic/scripts/merge_train.py").read_text("utf-8")
        self.assertNotIn("push-target", script)
        self.assertNotIn("merge-batch", script)


if __name__ == "__main__":
    unittest.main()
