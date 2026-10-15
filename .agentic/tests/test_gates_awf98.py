"""AWF-98 gate regressions for critic and owner-disposition records."""
from __future__ import annotations

import copy
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentic import ValidationError  # noqa: E402
from agentic.gates import evaluate, verified_owner_records  # noqa: E402
from agentic.lifecycle import definition  # noqa: E402
import test_review_tiers_195_gates as tier_fixture  # noqa: E402
from test_review_policy import (  # noqa: E402
    BasisAndDispositionTests,
    NOW,
    bind_production_posting_fixture,
    bind_review_admission,
    sign,
    signed,
)


class CriticRunVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tier_fixture.ReviewTiers195GateTests.setUpClass()

    def setUp(self):
        self.fixture = tier_fixture.ReviewTiers195GateTests(methodName="runTest")
        self.fixture.setUp()
        self.fixture._review_history(1)

    def _additional_critic_run(self):
        unrepresented = copy.deepcopy(next(
            run for run in self.fixture.bundle["runs"] if run["role"] == "critic"
        ))
        unrepresented["record_id"] = str(uuid.uuid4())
        unrepresented["run_id"] = str(uuid.uuid4())
        self.fixture.bundle["runs"].append(unrepresented)
        return unrepresented

    def _evaluate(self):
        bind_review_admission(self.fixture.bundle)
        registry = bind_production_posting_fixture(
            self.fixture.config, self.fixture.bundle
        )
        return evaluate(
            self.fixture.config,
            definition(),
            self.fixture.bundle,
            self.fixture.contracts,
            NOW,
            posting_collector_registry=registry,
        )

    def test_current_lane_critic_run_requires_a_review_verdict(self):
        unrepresented = self._additional_critic_run()
        self.fixture.bundle["critic"]["run_id"] = unrepresented["run_id"]
        self.fixture.bundle["critic"]["producer_id"] = unrepresented["producer_id"]

        with self.assertRaisesRegex(ValidationError, "critic run.*review-verdict"):
            self._evaluate()

    def test_auxiliary_critic_run_does_not_require_lane_review_verdict(self):
        self._additional_critic_run()

        result = self._evaluate()

        self.assertIn("gates", result)


class HistoricalFindingDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BasisAndDispositionTests.setUpClass()

    def setUp(self):
        self.fixture = BasisAndDispositionTests(methodName="runTest")
        self.fixture.setUp()

    def test_stale_head_disposition_is_verified_then_voided(self):
        self.fixture.tier1_bundle()
        self.fixture.bundle["critic"].update(
            verdict="REQUEST_CHANGES",
            findings=[self.fixture.finding(
                "F1", "MAJOR", {"criterion_id": "AC1"},
                path="tests/test_example.py",
            )],
        )
        self.fixture.sync_critic_artifact()
        stale_head = "9" * 40
        stale = sign(
            self.fixture.record(
                finding_id="F1",
                decision="ACCEPT_RISK",
                rationale="Superseded candidate head.",
                head_sha=stale_head,
                **signed(),
            ),
            "finding",
            stale_head,
        )
        stale["binding"]["candidate_id"] = "8" * 64
        self.fixture.bundle["finding_dispositions"] = [stale]

        result = self.fixture.gate()

        self.assertEqual("FAIL", result["gates"]["critic_current_tuple"]["result"])
        self.assertEqual([], result["accepted_findings"])


class DispositionRequestIdentityTests(unittest.TestCase):
    def test_one_authorization_request_cannot_sign_two_dispositions(self):
        request_id = str(uuid.uuid4())
        records = []
        for index in (1, 2):
            records.append({
                "record_id": str(uuid.uuid4()),
                "authorization_request_id": request_id,
                "finding_id": f"F-{index}",
                "owner_source": {"comment_id": 100 + index},
            })
        bundle = {
            "candidate": {"head_sha": "b" * 40},
            "finding_dispositions": records,
            "cap_disposition": None,
        }

        with mock.patch("agentic.authorization.verify_owner_record"), \
                self.assertRaisesRegex(ValidationError, "authorization request"):
            verified_owner_records(
                {}, None, bundle, {}, {}, set(), set(), NOW
            )


if __name__ == "__main__":
    unittest.main()
