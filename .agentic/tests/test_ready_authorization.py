"""PR ready transitions require authenticated owner/manager authorization."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.authorization import (
    make_ready_authorization_request,
    ready_authorization_config_sha256,
    render_ready_authorization,
)
from agentic.canonical import sha256
from agentic.lifecycle import ready_transition_record, transition


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
REQUESTED_AT = "2026-10-11T15:02:00Z"
AUTHORIZED_AT = "2026-10-11T15:03:00Z"
NOW = "2026-10-11T15:04:00Z"
EXPIRES_AT = "2026-10-11T15:12:00Z"
REPOSITORY_ID = 987654
TRUSTED_ACTOR_ID = 1001


class FakeImmutableCommentProvider:
    """Small provider double: creation assigns time/id, observation re-reads it."""

    def __init__(self, config):
        self.config_sha256 = ready_authorization_config_sha256(config)
        self.comments = {}
        self.next_id = 1042
        self.observed_at = NOW

    def create(self, *, body, actor_id=TRUSTED_ACTOR_ID, created_at=AUTHORIZED_AT,
               repository_id=REPOSITORY_ID, pr_number=67):
        comment_id = self.next_id
        self.next_id += 1
        source = {
            "channel": "github_pr_comment",
            "comment_id": comment_id,
            "actor_id": actor_id,
            "actor_login": "release-owner",
            "repository_id": repository_id,
            "pr_number": pr_number,
            "created_at": created_at,
            "updated_at": created_at,
            "raw_body": body,
            "raw_body_sha256": sha256(body.encode("utf-8")),
        }
        self.comments[comment_id] = deepcopy(source)
        return deepcopy(source)

    def verify(self, source, request):
        observed = self.comments.get(source.get("comment_id"))
        if observed != source:
            raise ValidationError("Provider comment is missing, edited or not the observed object")
        if request["configuration_sha256"] != self.config_sha256:
            raise ValidationError("Provider host has not accepted this configuration")
        return {
            "status": "VERIFIED",
            "source_id": f"github-pr-comment:{source['comment_id']}",
            "actor_id": source["actor_id"],
            "actor_login": source["actor_login"],
            "repository_id": source["repository_id"],
            "pr_number": source["pr_number"],
            "created_at": source["created_at"],
            "updated_at": source["updated_at"],
            "raw_body_sha256": source["raw_body_sha256"],
            "observed_at": self.observed_at,
            "configuration_status": "ACCEPTED",
            "configuration_sha256": self.config_sha256,
        }


class ReadyAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "github": {"repository_id": REPOSITORY_ID},
            "merge_gate": {
                "trusted_owner_ids": [TRUSTED_ACTOR_ID],
                "authorization_ttl_seconds": 900,
            },
        }
        self.provider = FakeImmutableCommentProvider(self.config)

    def authorization(self, *, role="owner", pr=67, head_sha=HEAD,
                      actor_id=TRUSTED_ACTOR_ID, requested_at=REQUESTED_AT,
                      expires_at=EXPIRES_AT, created_at=AUTHORIZED_AT):
        request = make_ready_authorization_request(
            repository_id=REPOSITORY_ID,
            pr=pr,
            head_sha=head_sha,
            role=role,
            request_id="ready-request-67-a",
            requested_at=requested_at,
            expires_at=expires_at,
        )
        # The complete signed body exists before the provider assigns the
        # comment id and timestamps. This models the real create/observe order.
        body = render_ready_authorization(request)
        source = self.provider.create(
            body=body,
            actor_id=actor_id,
            created_at=created_at,
            pr_number=pr,
        )
        return {
            "format": "awf-pr-ready-authorization-2",
            "request": request,
            "owner_source": source,
        }

    def transition_record(self, authorization):
        return ready_transition_record(
            67,
            HEAD,
            authorization,
            config=self.config,
            verify_owner_source=self.provider.verify,
        )

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
            "verify_ready_owner_source": self.provider.verify,
        }

    def test_ci_green_without_authorization_refuses_and_records(self):
        record = ready_transition_record(67, HEAD, None, config=self.config)

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("OWNER_MANAGER_AUTHORIZATION_REQUIRED", record["reason"])
        self.assertTrue(record["logged"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_offline_record_without_authenticated_provider_observation_refuses(self):
        authorization = self.authorization()
        record = ready_transition_record(67, HEAD, authorization, config=self.config)

        self.assertEqual("REFUSED", record["decision"])
        self.assertEqual("AUTHENTICATED_PROVIDER_OBSERVATION_REQUIRED", record["reason"])
        with self.assertRaisesRegex(ValidationError, "owner/manager authorization"):
            transition("PR_DRAFT", "PR_READY", self.ready_facts(record))

    def test_real_create_then_observe_allows_and_logs_owner_or_manager(self):
        for role in ("owner", "manager"):
            with self.subTest(role=role):
                authorization = self.authorization(role=role)
                record = self.transition_record(authorization)

                self.assertEqual("AUTHORIZED", record["decision"])
                self.assertEqual("ready-request-67-a", record["authorization_id"])
                self.assertEqual(TRUSTED_ACTOR_ID, record["actor_id"])
                self.assertEqual("release-owner", record["actor"])
                self.assertEqual(AUTHORIZED_AT, record["authorized_at"])
                self.assertTrue(record["logged"])
                verification = record["authorization_verification"]
                self.assertTrue(verification["live_source_verified"])
                self.assertTrue(verification["configuration_accepted"])
                self.assertTrue(verification["trusted_time_verified"])
                self.assertFalse(verification["execution_authority"])
                self.assertEqual(
                    "READY_FOR_CRITIC",
                    transition("PR_DRAFT", "PR_READY", self.ready_facts(record)),
                )

    def test_request_body_does_not_depend_on_provider_assigned_values(self):
        request = make_ready_authorization_request(
            repository_id=REPOSITORY_ID,
            pr=67,
            head_sha=HEAD,
            role="owner",
            request_id="known-before-create",
            requested_at=REQUESTED_AT,
            expires_at=EXPIRES_AT,
        )
        body = render_ready_authorization(request)

        self.assertIn("request=known-before-create", body)
        self.assertIn(f"expires={EXPIRES_AT}", body)
        self.assertNotIn("comment", body)
        self.assertNotIn(AUTHORIZED_AT, body)
        source = self.provider.create(body=body)
        self.assertEqual(AUTHORIZED_AT, source["created_at"])

    def test_authorization_is_bound_to_pr_and_head(self):
        for authorization in (
            self.authorization(pr=68),
            self.authorization(head_sha=OTHER_HEAD),
        ):
            with self.subTest(request=authorization["request"]):
                record = self.transition_record(authorization)
                self.assertEqual("REFUSED", record["decision"])
                self.assertEqual("AUTHORIZATION_CANDIDATE_MISMATCH", record["reason"])

    def test_expired_request_untrusted_actor_or_tampered_source_is_refused(self):
        expired = self.authorization(expires_at="2026-10-11T15:03:30Z")
        untrusted = self.authorization(actor_id=2002)
        tampered = self.authorization()
        tampered["owner_source"]["raw_body"] += " altered"
        for authorization in (expired, untrusted, tampered):
            with self.subTest(request=authorization["request"]):
                record = self.transition_record(authorization)
                self.assertEqual("REFUSED", record["decision"])
                self.assertEqual("AUTHORIZATION_RECORD_INVALID", record["reason"])

    def test_unaccepted_configuration_or_untrusted_observation_time_is_refused(self):
        authorization = self.authorization()
        changed_config = deepcopy(self.config)
        changed_config["merge_gate"]["authorization_ttl_seconds"] = 600
        config_record = ready_transition_record(
            67,
            HEAD,
            authorization,
            config=changed_config,
            verify_owner_source=self.provider.verify,
        )
        self.assertEqual("REFUSED", config_record["decision"])
        self.assertEqual("AUTHORIZATION_RECORD_INVALID", config_record["reason"])

        self.provider.observed_at = EXPIRES_AT
        time_record = self.transition_record(authorization)
        self.assertEqual("REFUSED", time_record["decision"])
        self.assertEqual("AUTHORIZATION_RECORD_INVALID", time_record["reason"])

    def test_lifecycle_reauthenticates_source_instead_of_trusting_saved_flags(self):
        authorization = self.authorization()
        record = self.transition_record(authorization)
        self.provider.comments.clear()

        with self.assertRaisesRegex(ValidationError, "authorization decision record is invalid"):
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
                normalized = " ".join(text.split())
                self.assertIn("explicit owner/manager authorization record", normalized)
                self.assertIn("pre-issued request", normalized)
                self.assertIn("authenticated immutable provider observation", normalized)


if __name__ == "__main__":
    unittest.main()
