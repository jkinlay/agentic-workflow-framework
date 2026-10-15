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
from agentic.gates import evaluate  # noqa: E402
from agentic.lifecycle import definition  # noqa: E402
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
        gate = self.fixture._owner_gate()

        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])

    def test_forged_owner_receipt_without_provider_observation_is_refused(self):
        gate = self.fixture._owner_gate(provider_evidence=False)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_stale_or_mismatched_candidate_is_refused(self):
        registry = self.prepared_gate()
        owner = self.fixture.bundle["owner_review"]
        owner["candidate_binding"] = {
            **owner["candidate_binding"],
            "head_sha": "f" * 40,
        }

        gate = self.evaluate(registry)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_wrong_owner_is_refused(self):
        registry = self.prepared_gate()
        self.fixture.bundle["owner_review"]["owner_id"] = 2002

        gate = self.evaluate(registry)

        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_verified_owner_record_and_provider_receipt_are_final_gate_evidence(self):
        gate = self.fixture._owner_gate()
        owner = self.fixture.bundle["owner_review"]
        terminal_round = self.fixture.bundle["review_verdicts"][-1]["round"]
        owner_input = (
            "urn:awf:input:owner_review:"
            + fingerprint("gate-input", owner)
        )
        provider_receipt = f"urn:awf:review-round-receipt:{terminal_round}"
        registry_uris = {
            entry["uri"] for entry in self.fixture.bundle["evidence_registry"]
        }

        self.assertIn(owner["record_id"], gate["record_ids"])
        self.assertIn(provider_receipt, registry_uris)
        for gate_name in ("critic_current_tuple", "verdict_posting"):
            evidence = gate["gates"][gate_name]["evidence"]
            self.assertIn(owner_input, evidence)
            self.assertIn(provider_receipt, evidence)


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

    def test_validated_record_is_not_reduced_to_truthiness(self):
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

        self.assertEqual("QUALIFIED", result["status"])


if __name__ == "__main__":
    unittest.main()
