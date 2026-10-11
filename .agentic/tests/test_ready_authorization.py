"""PR ready transitions require explicit owner/manager authorization."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.authorization import render_ready_authorization
from agentic.canonical import sha256
from agentic.lifecycle import ready_transition_record, transition


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
NOW = "2026-10-11T15:04:00Z"
AUTHORIZED_AT = "2026-10-11T15:03:00Z"
REPOSITORY_ID = 987654
TRUSTED_ACTOR_ID = 1001


class ReadyAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "github": {"repository_id": REPOSITORY_ID},
            "merge_gate": {
                "trusted_owner_ids": [TRUSTED_ACTOR_ID],
                "authorization_ttl_seconds": 900,
            },
        }

    def authorization(self, *, role="owner", pr=67, head_sha=HEAD,
                      actor_id=TRUSTED_ACTOR_ID, authorized_at=AUTHORIZED_AT):
        record = {
            "format": "awf-pr-ready-authorization-1",
            "authorization_id": "owner-comment-1042",
            "role": role,
            "action": "MARK_READY",
            "repository_id": REPOSITORY_ID,
            "pr": pr,
            "head_sha": head_sha,
            "authorized_at": authorized_at,
            "owner_source": {
                "channel": "github_pr_comment",
                "comment_id": 1042,
                "actor_id": actor_id,
                "actor_login": "release-owner",
                "repository_id": REPOSITORY_ID,
                "pr_number": pr,
                "created_at": authorized_at,
                "updated_at": authorized_at,
                "raw_body": "pending",
                "raw_body_sha256": "0" * 64,
            },
        }
        body = render_ready_authorization(record)
        record["owner_source"].update(
            raw_body=body,
            raw_body_sha256=sha256(body.encode("utf-8")),
        )
        return record

    def ready_facts(self, record):
        return {
            "validation_current": True,
            "requirements_current": True,
            "draft_cleared": True,
            "ci_green": True,
            "pr": 67,
            "head_sha": HEAD,
            "ready_transition_record": record,
            "ready_authorization_config": self.config,
            "ready_authorization_checked_at": NOW,
        }

    def test_ci_green_without_authorization_refuses_and_records(self):
        record = ready_transition_record(67, HEAD, None, config=self.config, now=NOW)

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("OWNER_MANAGER_AUTHORIZATION_REQUIRED", record["reason"])
        self.assertTrue(record["logged"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_verified_owner_or_manager_authorization_allows_and_logs(self):
        for role in ("owner", "manager"):
            with self.subTest(role=role):
                authorization = self.authorization(role=role)
                record = ready_transition_record(
                    67, HEAD, authorization, config=self.config, now=NOW
                )

                self.assertEqual("AUTHORIZED", record["decision"])
                self.assertEqual("owner-comment-1042", record["authorization_id"])
                self.assertEqual(TRUSTED_ACTOR_ID, record["actor_id"])
                self.assertEqual("release-owner", record["actor"])
                self.assertTrue(record["logged"])
                self.assertEqual(
                    "READY_FOR_CRITIC",
                    transition("PR_DRAFT", "PR_READY", self.ready_facts(record)),
                )

    def test_caller_authored_role_and_invalid_time_do_not_authorize(self):
        authorization = {
            "format": "awf-pr-ready-authorization-1",
            "authorization_id": "not-a-provider-record",
            "actor": "not-verified",
            "role": "owner",
            "action": "MARK_READY",
            "pr": 67,
            "head_sha": HEAD,
            "authorized_at": "not-a-time",
        }

        record = ready_transition_record(
            67, HEAD, authorization, config=self.config, now=NOW
        )

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("AUTHORIZATION_RECORD_INVALID", record["reason"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_authorization_is_bound_to_pr_and_head(self):
        for authorization in (
            self.authorization(pr=68),
            self.authorization(head_sha=OTHER_HEAD),
        ):
            with self.subTest(pr=authorization["pr"], head_sha=authorization["head_sha"]):
                record = ready_transition_record(
                    67, HEAD, authorization, config=self.config, now=NOW
                )

                self.assertEqual("REFUSED", record["decision"])
                self.assertEqual("AUTHORIZATION_CANDIDATE_MISMATCH", record["reason"])
                with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
                    transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_stale_authorization_is_refused(self):
        authorization = self.authorization(authorized_at="2026-10-11T14:00:00Z")

        record = ready_transition_record(
            67, HEAD, authorization, config=self.config, now=NOW
        )

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("AUTHORIZATION_RECORD_INVALID", record["reason"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_untrusted_actor_or_tampered_raw_source_is_refused(self):
        untrusted = self.authorization(actor_id=2002)
        tampered = self.authorization()
        tampered["owner_source"]["raw_body"] += " altered"
        for authorization in (untrusted, tampered):
            with self.subTest(actor_id=authorization["owner_source"]["actor_id"]):
                record = ready_transition_record(
                    67, HEAD, authorization, config=self.config, now=NOW
                )
                self.assertEqual("REFUSED", record["decision"])
                self.assertEqual("AUTHORIZATION_RECORD_INVALID", record["reason"])

    def test_lifecycle_reverifies_authorization_instead_of_trusting_transition_mapping(self):
        authorization = self.authorization()
        record = ready_transition_record(
            67, HEAD, authorization, config=self.config, now=NOW
        )
        forged = deepcopy(record)
        forged["authorization"]["owner_source"]["actor_id"] = 2002
        forged["authorization_sha256"] = sha256(
            json.dumps(
                forged["authorization"], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

        with self.assertRaisesRegex(ValidationError, "authorization decision record is invalid"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(forged))


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
