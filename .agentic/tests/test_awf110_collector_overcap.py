"""AWF-110 production collection of Tier 3 over-cap approvals."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, sha256
from agentic.gates import evaluate, review_round_receipt_sha256
from agentic.lifecycle import definition
from agentic.posting_collector import (
    COLLECTOR_ID,
    PostingCollectorMismatch,
    collect_posting_observation,
    registry_from_review_host,
)
from test_review_policy import (
    Fixture,
    NOW,
    bind_review_admission,
    bind_review_round_receipt,
)
import test_review_tiers_195_gates as gate_fixtures


RELEASE_SHA256 = "d" * 64
APPROVAL_ID = 4104
APPROVAL_CREATED_AT = "2026-09-09T11:58:00Z"
ROUND_STARTED_AT = "2026-09-09T11:59:00Z"
APPROVAL_BODY = (
    "AWF-OVERCAP-APPROVAL: round 4 of cap 3, approver 1001, "
    "ruling 2026-09-09T11:57:00Z"
)


class GitHubTransportDouble:
    """Fake only GitHub GET transport; collection and recording remain real."""

    __test__ = False

    def __init__(self):
        self.responses = {}

    def api(self, suffix):
        return copy.deepcopy(self.responses[suffix])

    def posting_pull(self, number):
        return copy.deepcopy(self.responses[f"pulls/{number}"])


class CollectorOvercapApprovalTests(Fixture):
    def setUp(self):
        super().setUp()
        gate_fixtures.ReviewTiers195GateTests._tier_three_candidate(self)
        gate_fixtures.ReviewTiers195GateTests._review_history(
            self, 4, historical_tier=3, terminal_tier=3,
        )
        bind_review_admission(self.bundle)
        bind_review_round_receipt(
            self.bundle,
            self.bundle["review_verdicts"][-1],
            self.bundle["critic"],
            self.bundle["review_submission"]["completion_snapshot"],
        )
        self.config["merge_gate"]["production_posting_collector_ids"] = [
            COLLECTOR_ID
        ]
        self.bundle["provenance_mode"] = "production_observation"

        terminal = self.bundle["review_verdicts"][-1]
        critic_run = next(
            run for run in self.bundle["runs"] if run["run_id"] == terminal["run_id"]
        )
        critic_run["created_at"] = ROUND_STARTED_AT
        gate_fixtures.ReviewTiers195GateTests._owner_review(self)

        self.host = GitHubTransportDouble()
        candidate = self.bundle["candidate"]
        prefix = (
            f"{candidate['host']}/{candidate['repository']}/pull/"
            f"{candidate['pr_number']}"
        )
        body_lines = []
        for verdict in self.bundle["review_verdicts"]:
            receipt = next(
                item for item in self.bundle["review_round_receipts"]
                if item["critic_artifact_binding"]["round"] == verdict["round"]
            )
            comment_id = verdict["round"]
            comment_url = f"{prefix}#issuecomment-{comment_id}"
            verdict["pr_comment_url"] = comment_url
            self.host.responses[f"issues/comments/{comment_id}"] = {
                "id": comment_id,
                "html_url": comment_url,
                "body": receipt["review_verdict_json"],
            }
            body_lines.extend([
                f'<a id="review-verdict-{verdict["round"]}-{verdict["record_id"]}"></a>',
                (
                    f"review-verdict:{verdict['round']}:{verdict['record_id']}:"
                    f"{receipt['review_verdict_sha256']}"
                ),
            ])
        pr_body = "\n".join(body_lines) + "\n"
        self.bundle["pr"]["body_sha256"] = sha256(pr_body.encode("utf-8"))
        self.bundle["publication_scan"]["pr_body_sha256"] = self.bundle["pr"][
            "body_sha256"
        ]
        self.host.responses[f"pulls/{candidate['pr_number']}"] = {
            "number": candidate["pr_number"],
            "html_url": prefix,
            "body": pr_body,
            # Independently supplied rendered targets for every retained round.
            "body_html": "\n".join(
                f'<a id="review-verdict-{v["round"]}-{v["record_id"]}"></a>'
                for v in self.bundle["review_verdicts"]
            ),
            "head": {"sha": candidate["head_sha"]},
            "base": {
                "sha": candidate["target_base_sha"],
                "repo": {"id": candidate["repository_id"]},
            },
        }

        owner_review = self.bundle["owner_review"]
        owner_provider = json.loads(base64.b64decode(
            owner_review["posting_observation"]["provider_response_bytes"]["data"]
        ).decode("utf-8"))
        formal_approval = owner_provider["owner_approval"]
        self.host.responses[
            f"pulls/{candidate['pr_number']}/reviews/{formal_approval['id']}"
        ] = {
            key: copy.deepcopy(formal_approval[key])
            for key in ("id", "html_url", "state", "commit_id", "user")
        }

        approval_url = f"{prefix}#issuecomment-{APPROVAL_ID}"
        self.approval_comment = {
            "id": APPROVAL_ID,
            "html_url": approval_url,
            "user": {"id": 1001},
            "created_at": APPROVAL_CREATED_AT,
            "updated_at": APPROVAL_CREATED_AT,
            "body": APPROVAL_BODY,
        }
        self.host.responses[
            f"issues/{candidate['pr_number']}/comments?per_page=100&page=1"
        ] = [self.approval_comment]
        self.host.responses[f"issues/comments/{APPROVAL_ID}"] = self.approval_comment

        self._install_real_posting_observations()

    def _install_real_posting_observations(self):
        template = copy.deepcopy(next(
            run for run in self.bundle["runs"] if run["role"] == "collector"
        ))
        terminal_round = max(
            verdict["round"] for verdict in self.bundle["review_verdicts"]
        )
        for verdict in self.bundle["review_verdicts"]:
            run = copy.deepcopy(template)
            run.update(
                record_id=str(uuid.uuid4()),
                run_id=str(uuid.uuid4()),
                context_id=str(uuid.uuid4()),
                producer_id=COLLECTOR_ID,
                binding=copy.deepcopy(
                    self.bundle["review_verdicts"][-1]["binding"]
                ),
                created_at=NOW,
            )
            self.bundle["runs"].append(run)
            receipt = next(
                item for item in self.bundle["review_round_receipts"]
                if item["critic_artifact_binding"]["round"] == verdict["round"]
            )
            collected = collect_posting_observation(
                self.host,
                candidate=self.bundle["candidate"],
                verdict=verdict,
                review_round_receipt=receipt,
                collector_run_id=run["run_id"],
                observed_at=NOW,
                release_sha256=RELEASE_SHA256,
                owner_review=(
                    self.bundle["owner_review"]
                    if verdict["round"] == terminal_round else None
                ),
            )
            verdict["posting_observation"] = collected["observation"]
            if verdict["round"] == terminal_round:
                self.bundle["owner_review"]["posting_observation"] = collected[
                    "observation"
                ]
            posting_bytes = canonical(collected["observation"])
            receipt["posting_observation_json"] = {
                "encoding": "base64",
                "data": base64.b64encode(posting_bytes).decode("ascii"),
            }
            receipt["posting_observation_sha256"] = sha256(posting_bytes)
            entry = next(
                item for item in self.bundle["evidence_registry"]
                if item["uri"] == f"urn:awf:review-round-receipt:{verdict['round']}"
            )
            entry["sha256"] = review_round_receipt_sha256(receipt)

    def _collect_and_gate(self):
        registry = registry_from_review_host(
            self.host, self.bundle, release_sha256=RELEASE_SHA256
        )
        return evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry=registry,
        )

    def test_real_collector_records_comment_and_gate_accepts_round_four(self):
        self.assertNotIn("overcap_approvals", self.bundle)

        gate = self._collect_and_gate()

        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertEqual(1, len(self.bundle["overcap_approvals"]))
        approval = self.bundle["overcap_approvals"][0]
        self.assertEqual(APPROVAL_ID, approval["comment_id"])
        self.assertEqual(self.bundle["candidate"]["head_sha"], json.loads(
            base64.b64decode(approval["provider_response_bytes"]["data"])
        )["head_sha"])

    def test_comment_observed_on_other_head_is_refused(self):
        self.host.responses[
            f"pulls/{self.bundle['candidate']['pr_number']}"
        ]["head"]["sha"] = "9" * 40

        with self.assertRaisesRegex(PostingCollectorMismatch, "head"):
            registry_from_review_host(
                self.host, self.bundle, release_sha256=RELEASE_SHA256
            )
        with self.assertRaisesRegex(ValidationError, "canonical owner approval"):
            evaluate(
                self.config,
                definition(),
                self.bundle,
                self.contracts,
                NOW,
                posting_collector_registry={},
            )

    def test_real_collector_without_approval_still_fails_closed(self):
        self.host.responses[
            f"issues/{self.bundle['candidate']['pr_number']}/"
            "comments?per_page=100&page=1"
        ] = []

        registry = registry_from_review_host(
            self.host, self.bundle, release_sha256=RELEASE_SHA256
        )
        self.assertEqual([], self.bundle["overcap_approvals"])
        with self.assertRaisesRegex(ValidationError, "canonical owner approval"):
            evaluate(
                self.config,
                definition(),
                self.bundle,
                self.contracts,
                NOW,
                posting_collector_registry=registry,
            )


if __name__ == "__main__":
    unittest.main()
