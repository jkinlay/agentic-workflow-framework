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
    NOW,
    bind_production_posting_fixture,
    bind_review_admission,
)


class CriticRunVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tier_fixture.ReviewTiers195GateTests.setUpClass()

    def setUp(self):
        self.fixture = tier_fixture.ReviewTiers195GateTests(methodName="runTest")
        self.fixture.setUp()
        self.fixture._review_history(1)

    def test_every_registered_critic_run_requires_a_review_verdict(self):
        unrepresented = copy.deepcopy(next(
            run for run in self.fixture.bundle["runs"] if run["role"] == "critic"
        ))
        unrepresented["record_id"] = str(uuid.uuid4())
        unrepresented["run_id"] = str(uuid.uuid4())
        self.fixture.bundle["runs"].append(unrepresented)
        bind_review_admission(self.fixture.bundle)
        registry = bind_production_posting_fixture(
            self.fixture.config, self.fixture.bundle
        )

        with self.assertRaisesRegex(ValidationError, "critic run.*review-verdict"):
            evaluate(
                self.fixture.config,
                definition(),
                self.fixture.bundle,
                self.fixture.contracts,
                NOW,
                posting_collector_registry=registry,
            )


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
