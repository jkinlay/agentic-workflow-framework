"""PR ready transitions require explicit owner/manager authorization."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.lifecycle import ready_transition_record, transition


HEAD = "a" * 40


class ReadyAuthorizationTests(unittest.TestCase):
    def ready_facts(self, record):
        return {
            "validation_current": True,
            "requirements_current": True,
            "draft_cleared": True,
            "ci_green": True,
            "pr": 67,
            "head_sha": HEAD,
            "ready_transition_record": record,
        }

    def test_ci_green_without_authorization_refuses_and_records(self):
        record = ready_transition_record(67, HEAD, None)

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("OWNER_MANAGER_AUTHORIZATION_REQUIRED", record["reason"])
        self.assertTrue(record["logged"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_matching_owner_authorization_allows_and_logs(self):
        authorization = {
            "format": "awf-pr-ready-authorization-1",
            "authorization_id": "owner-comment-1042",
            "actor": "release-owner",
            "role": "owner",
            "action": "MARK_READY",
            "pr": 67,
            "head_sha": HEAD,
            "authorized_at": "2026-10-11T12:48:00Z",
        }

        record = ready_transition_record(67, HEAD, authorization)

        self.assertEqual("AUTHORIZED", record["decision"])
        self.assertEqual("owner-comment-1042", record["authorization_id"])
        self.assertEqual("release-owner", record["actor"])
        self.assertTrue(record["logged"])
        self.assertEqual(
            "READY_FOR_CRITIC",
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record)),
        )

    def test_authorization_is_bound_to_pr_and_head(self):
        authorization = {
            "format": "awf-pr-ready-authorization-1",
            "authorization_id": "manager-approval-9",
            "actor": "delivery-manager",
            "role": "manager",
            "action": "MARK_READY",
            "pr": 68,
            "head_sha": HEAD,
            "authorized_at": "2026-10-11T12:48:00Z",
        }

        record = ready_transition_record(67, HEAD, authorization)

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("AUTHORIZATION_CANDIDATE_MISMATCH", record["reason"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))


class ReadyGuidanceTests(unittest.TestCase):
    def test_active_ready_guidance_requires_explicit_authorization_record(self):
        paths = (
            ".agentic/SPECIFICATION.md",
            ".agentic/docs/22-AUTOMATED-REVIEW-LOOP.md",
            ".agentic/docs/23-TICKET-LIFECYCLE.md",
            ".agentic/docs/24-STREAM-STARTUP.md",
            ".agentic/docs/31-PUBLICATION-SAFETY.md",
            ".agentic/prompts/controller.md",
            ".agentic/prompts/worker.md",
        )
        for relative in paths:
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("explicit owner/manager authorization record", " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
