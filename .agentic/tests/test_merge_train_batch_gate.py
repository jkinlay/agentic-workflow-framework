"""T4–T6: batch gates retain normal review policy and human authority."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentic import ValidationError, authorization, gates, merge_train
from agentic.canonical import fingerprint
from agentic.lifecycle import definition
import test_gates_owner_review as owner_fixture
from test_review_policy import NOW


class BatchGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owner_fixture.OwnerReviewGateTests.setUpClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-batch-gate-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = owner_fixture.OwnerReviewGateTests(methodName="runTest")
        self.fixture.setUp()
        self.f = self.fixture.fixture
        self.candidate = self.f.bundle["candidate"]
        self.candidate["tested_merge_sha"] = self.candidate["head_sha"]
        for check in self.f.bundle["ci"]["checks"]:
            check["tested_commit_sha"] = self.candidate["head_sha"]
        self.f.rebind()
        self.f._review_history(2, historical_tier=2, terminal_tier=3)
        self.posting = self.fixture.prepared_gate()
        self.config = self.f.config
        self.provider = {k: self.candidate[k] for k in ("host", "repository", "repository_id")}
        self.generation = {
            "format": "awf-merge-train-generation-1", "batch_id": "batch-11-12",
            "target_branch": self.candidate["target_base_branch"],
            "target_sha": self.candidate["target_base_sha"], "generation": 1,
            "provider_identity": self.provider,
            "sources": [{"pr_number": self.candidate["pr_number"] + 1,
                         "head_sha": "1" * 40, "tree_sha": "2" * 40,
                         "admission_evidence_sha256": "3" * 64}],
            "authorization_scope": "CONSTRUCTION_ONLY", "may_publish": False, "may_merge": False,
        }
        self.generation["generation_id"] = fingerprint("merge_train_generation", self.generation)
        self.events = []
        self.corrupt = None
        self.registry = {"batch-observer": self.registration()}

    def registration(self):
        registration = {
            "producer_kind": "merge_train_observer", "implementation_sha256": "a" * 64,
            "release_sha256": "b" * 64, "provider_identity": self.provider,
            "receipt_namespace": merge_train.OBSERVER_NAMESPACE,
            "capabilities": ["BATCH_FINAL_GATE", "BATCH_AUTHORIZATION", "BATCH_READY_STATE"],
            "receipts": {}, "observer": None,
        }

        def observe(request):
            self.events.append(request["operation"])
            if request["operation"] == "BATCH_FINAL_GATE":
                result = {"candidate": copy.deepcopy(request["candidate"]),
                          "generation_id": request["generation_id"],
                          "gate_sha256": request["gate_sha256"],
                          "review_submission_sha256": request["review_submission_sha256"],
                          "ci_sha256": request["ci_sha256"], "ci_scope": "FULL"}
            elif request["operation"] == "BATCH_READY_STATE":
                result = {"candidate": copy.deepcopy(request["candidate"]), "draft": False}
            else:
                result = {"source_id": request["source_id"], "actor_id": self.config["merge_gate"]["trusted_owner_ids"][0],
                          "created_at": NOW, "updated_at": NOW,
                          "raw_body": authorization.render_batch_authorization(request["request"]),
                          "configuration_sha256": request["request"]["configuration_sha256"],
                          "configuration_status": "ACCEPTED", "candidate": copy.deepcopy(request["candidate"]),
                          "generation_id": request["generation_id"]}
            if self.corrupt:
                self.corrupt(result)
            material = {"producer_id": "batch-observer", "observed_at": NOW,
                        "request_sha256": fingerprint("merge_train_provider_request", request), "result": result}
            observation = dict(material, status="OBSERVED",
                               observation_id=fingerprint("merge_train_provider_observation", material))
            registration["receipts"][observation["observation_id"]] = merge_train.provider_receipt_sha256(
                registration, request, observation)
            return observation

        registration["observer"] = observe
        return registration

    def gate(self):
        return gates.evaluate_batch_final_gate(
            self.config, definition(), self.f.bundle, self.f.contracts, NOW,
            generation=self.generation, observer_id="batch-observer", observer_registry=self.registry,
            posting_collector_registry=self.posting)

    def request(self, action="MARK_READY", gate=None):
        return authorization.make_batch_authorization_request(
            self.generation, self.candidate, self.config, action=action,
            request_id="batch-request-1", requested_at=NOW, expires_at="2026-09-09T12:15:00Z",
            gate=gate, contracts=self.f.contracts, observer_registry=self.registry)

    def verify(self, request, gate=None):
        return authorization.verify_batch_authorization(
            request, "comment-101", self.generation, self.candidate, self.config,
            observer_id="batch-observer", observer_registry=self.registry, now=NOW,
            gate=gate, contracts=self.f.contracts)

    def test_batch_final_gate_requires_full_exact_head_ci_and_frozen_reviews(self):
        gate = self.gate()
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertFalse(gate["execution_authority"])
        original = copy.deepcopy(self.f.bundle)
        for mutate in (
            lambda b: b["ci"].update(retrieval_complete=False),
            lambda b: b["ci"]["checks"][0].update(tested_commit_sha="f" * 40),
            lambda b: b["ci"]["checks"][0].update(completed_at="2025-01-01T00:00:00Z"),
            lambda b: b["candidate"].update(head_sha="f" * 40),
            lambda b: b["review_submission"]["completion_snapshot"].update(required_reviewers=[]),
            lambda b: b["pr"].update(draft=True),
        ):
            with self.subTest(mutation=mutate):
                self.f.bundle = copy.deepcopy(original)
                mutate(self.f.bundle)
                with self.assertRaises(ValidationError):
                    self.gate()
        self.f.bundle = original
        for field, value in (("ci_scope", "DRAFT"), ("gate_sha256", "f" * 64),
                             ("review_submission_sha256", "f" * 64)):
            self.corrupt = lambda r, k=field, v=value: r.update({k: v})
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.gate()

    def test_batch_mark_ready_requires_authenticated_exact_head_authorization(self):
        request = self.request()
        records = []

        def log(record):
            records.append(record)
            self.events.append("LOG:" + record["decision"])

        def mutate(preconditions):
            self.assertEqual(self.candidate, preconditions["candidate"])
            self.events.append("MARK_READY")

        def ready():
            return authorization.batch_mark_ready(
                request, "comment-101", self.generation, self.candidate, self.config,
                observer_id="batch-observer", observer_registry=self.registry, now=NOW,
                log_record=log, mark_ready=mutate)

        for corrupt in (
            lambda r: r.update(actor_id=999999),
            lambda r: r.update(updated_at="2025-01-01T00:00:00Z"),
            lambda r: r["candidate"].update(head_sha="f" * 40),
            lambda r: r.update(raw_body="source review APPROVED"),
            lambda r: r.update(configuration_status="UNOBSERVED"),
        ):
            self.corrupt = corrupt
            self.events.clear()
            result = ready()
            self.assertEqual("REFUSED", result["decision"])
            self.assertTrue(result["draft"])
            self.assertNotIn("MARK_READY", self.events)
            self.assertEqual("REFUSED", records[-1]["decision"])
        self.corrupt = None
        self.events.clear()
        result = ready()
        self.assertEqual("READY_FOR_CRITIC", result["status"])
        self.assertEqual(["BATCH_AUTHORIZATION", "LOG:AUTHORIZED", "MARK_READY", "BATCH_READY_STATE"], self.events)
        self.assertFalse(result["draft"])

    def test_source_review_cannot_authorize_batch_merge(self):
        gate = self.gate()
        with self.assertRaises(ValidationError):
            self.request("HUMAN_MERGE_HANDOFF")
        request = self.request("HUMAN_MERGE_HANDOFF", gate)
        before = copy.deepcopy(request)
        for corrupt in (
            lambda r: r.update(raw_body="source review APPROVED"),
            lambda r: r.update(generation_id="f" * 64),
            lambda r: r["candidate"].update(head_sha="f" * 40),
        ):
            self.corrupt = corrupt
            with self.assertRaises(ValidationError):
                self.verify(request, gate)
        self.corrupt = None
        result = self.verify(request, gate)
        self.assertEqual("HUMAN_MERGE_HANDOFF", result["action"])
        self.assertFalse(result["execution_authority"])
        self.assertFalse(result["may_merge"])
        self.assertEqual(before, request)
        changed_gate = copy.deepcopy(gate)
        changed_gate["final_gate"]["record_id"] = "different-gate"
        with self.assertRaises(ValidationError):
            self.verify(request, changed_gate)
        with self.assertRaises(ValidationError):
            self.verify(self.request(), gate)


if __name__ == "__main__":
    unittest.main()
