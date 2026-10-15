"""Tier 3 owner-review provider evidence regressions for AWF-41."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentic.canonical import fingerprint  # noqa: E402
from agentic.gates import (  # noqa: E402
    evaluate,
    owner_review_record_sha256,
    owner_review_registry_uri,
)
from agentic.lifecycle import definition, review_round_transition  # noqa: E402
from agentic import review_tiers  # noqa: E402
from agentic.review_tiers import review_decision  # noqa: E402
import test_review_tiers_195_gates as tier_fixture  # noqa: E402
from test_review_policy import (  # noqa: E402
    NOW,
    bind_production_posting_fixture,
    bind_review_admission,
)


class OwnerReviewGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tier_fixture.ReviewTiers195GateTests.setUpClass()

    def setUp(self):
        self.fixture = tier_fixture.ReviewTiers195GateTests(methodName="runTest")
        self.fixture.setUp()
        self.fixture._tier_three_candidate()
        self.fixture._review_history(2, historical_tier=2, terminal_tier=3)

    def prepared_gate(self):
        bind_review_admission(self.fixture.bundle)
        registry = bind_production_posting_fixture(
            self.fixture.config, self.fixture.bundle
        )
        self.fixture._owner_review()
        self.fixture._refresh_owner_posting_receipt(registry)
        return registry

    def retain_owner_review(self):
        owner = self.fixture.bundle["owner_review"]
        uri = owner_review_registry_uri(owner)
        self.fixture.bundle["evidence_registry"] = [
            entry for entry in self.fixture.bundle["evidence_registry"]
            if entry["uri"] != uri
        ]
        self.fixture.bundle["evidence_registry"].append({
            "uri": uri,
            "sha256": owner_review_record_sha256(owner),
            "producer_id": owner["producer_id"],
            "retained_until": "2027-01-01T00:00:00Z",
        })

    def evaluate(self, registry):
        return evaluate(
            self.fixture.config,
            definition(),
            self.fixture.bundle,
            self.fixture.contracts,
            NOW,
            posting_collector_registry=registry,
        )

    def test_provider_observed_trusted_owner_review_qualifies(self):
        gate = self.evaluate(self.prepared_gate())

        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])

    def test_forged_owner_receipt_without_provider_observation_is_refused(self):
        bind_review_admission(self.fixture.bundle)
        registry = bind_production_posting_fixture(
            self.fixture.config, self.fixture.bundle
        )
        self.fixture._owner_review(provider_evidence=False)
        self.retain_owner_review()

        gate = self.evaluate(registry)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_stale_or_mismatched_candidate_is_refused(self):
        registry = self.prepared_gate()
        owner = self.fixture.bundle["owner_review"]
        owner["candidate_binding"] = {
            **owner["candidate_binding"],
            "head_sha": "f" * 40,
        }
        self.retain_owner_review()

        gate = self.evaluate(registry)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_wrong_owner_is_refused(self):
        registry = self.prepared_gate()
        self.fixture.bundle["owner_review"]["owner_id"] = 2002
        self.retain_owner_review()

        gate = self.evaluate(registry)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_provider_verified_owner_record_is_retained_automatically(self):
        bind_review_admission(self.fixture.bundle)
        registry = bind_production_posting_fixture(
            self.fixture.config, self.fixture.bundle
        )
        self.fixture._owner_review()
        self.fixture._refresh_owner_posting_receipt(registry)

        gate = self.evaluate(registry)
        owner = self.fixture.bundle["owner_review"]
        owner_uri = owner_review_registry_uri(owner)
        owner_entries = [
            entry for entry in self.fixture.bundle["evidence_registry"]
            if entry["uri"] == owner_uri
        ]

        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertEqual(1, len(owner_entries))
        self.assertEqual(owner_review_record_sha256(owner), owner_entries[0]["sha256"])
        self.assertEqual(owner["producer_id"], owner_entries[0]["producer_id"])

    def test_forged_owner_registry_digest_is_refused(self):
        registry = self.prepared_gate()
        self.retain_owner_review()
        owner_uri = owner_review_registry_uri(self.fixture.bundle["owner_review"])
        owner_entry = next(
            entry for entry in self.fixture.bundle["evidence_registry"]
            if entry["uri"] == owner_uri
        )
        owner_entry["sha256"] = "f" * 64

        gate = self.evaluate(registry)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_verified_owner_record_and_provider_receipt_are_final_gate_evidence(self):
        gate = self.evaluate(self.prepared_gate())
        owner = self.fixture.bundle["owner_review"]
        terminal_round = self.fixture.bundle["review_verdicts"][-1]["round"]
        owner_input = (
            "urn:awf:input:owner_review:"
            + fingerprint("gate-input", owner)
        )
        provider_receipt = f"urn:awf:review-round-receipt:{terminal_round}"
        owner_receipt = owner_review_registry_uri(owner)
        registry_uris = {
            entry["uri"] for entry in self.fixture.bundle["evidence_registry"]
        }
        owner_entry = next(
            entry for entry in self.fixture.bundle["evidence_registry"]
            if entry["uri"] == owner_receipt
        )

        self.assertIn(owner["record_id"], gate["record_ids"])
        self.assertIn(provider_receipt, registry_uris)
        self.assertIn(owner_receipt, registry_uris)
        self.assertEqual(owner_review_record_sha256(owner), owner_entry["sha256"])
        for gate_name in ("critic_current_tuple", "verdict_posting"):
            evidence = gate["gates"][gate_name]["evidence"]
            self.assertIn(owner_input, evidence)
            self.assertIn(provider_receipt, evidence)
            self.assertIn(owner_receipt, evidence)


class OwnerReviewPolicyTests(unittest.TestCase):
    binding = {
        "repository_id": 123,
        "pr_number": 7,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
    }

    def test_bare_boolean_never_qualifies(self):
        result = review_decision(
            3,
            3,
            latest_pass=True,
            owner_review=True,
            configured_owner_ids=[1001],
            expected_candidate_binding=self.binding,
        )

        self.assertEqual("OWNER_REVIEW_REQUIRED", result["status"])

    def test_policy_exposes_no_unchecked_owner_review_sealer(self):
        self.assertFalse(hasattr(review_tiers, "_seal_verified_owner_review"))

    def test_structured_caller_record_without_provider_context_never_qualifies(self):
        class NoTruthiness(dict):
            def __bool__(self):
                raise AssertionError("validated owner-review record used as a boolean")

        owner = NoTruthiness({
            "record_id": "owner-record",
            "run_id": "owner-run",
            "producer_id": "owner-verifier",
            "owner_id": 1001,
            "owner_review": True,
            "verdict": "PASS",
            "candidate_binding": copy.deepcopy(self.binding),
        })

        result = review_decision(
            3,
            3,
            latest_pass=True,
            owner_review=owner,
            configured_owner_ids=[1001],
            expected_candidate_binding=self.binding,
        )

        self.assertEqual("OWNER_REVIEW_REQUIRED", result["status"])

    def test_lifecycle_refuses_structured_caller_record_without_provider_context(self):
        owner = {
            "record_id": "owner-record",
            "run_id": "owner-run",
            "producer_id": "owner-verifier",
            "owner_id": 1001,
            "owner_review": True,
            "verdict": "PASS",
            "candidate_binding": copy.deepcopy(self.binding),
        }

        result = review_round_transition(
            3,
            3,
            verdict="PASS",
            owner_review=owner,
            configured_owner_ids=[1001],
            expected_candidate_binding=self.binding,
        )

        self.assertEqual("OWNER_REVIEW_REQUIRED", result["status"])


if __name__ == "__main__":
    unittest.main()
