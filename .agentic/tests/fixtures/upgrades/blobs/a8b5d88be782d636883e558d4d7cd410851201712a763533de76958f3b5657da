"""Publication preflight fixtures; no network, push, PR, or lifecycle calls."""
import copy
import unittest

from agentic import ValidationError
from agentic.publication_readiness import (
    publication_readiness, publication_capabilities, require_publication_readiness,
)


NOW = "2026-10-02T10:00:00Z"


def publication_config():
    return {"github": {"host": "https://github.com", "repository": "fixture/example",
            "repository_id": 101, "expected_actor_id": 1001, "auth_profile": "fixture",
            "base_branch": "main", "branch_pattern": "codex/{ticket}-{slug}"}}


def publication_observation(ticket="EX-1", now=NOW):
    return {"format": "awf-publication-readiness-1", "source": "host_observation",
            "host": "https://github.com", "observed_at": now, "ticket": ticket,
            "slug": "fixture", "branch": "codex/" + ticket + "-fixture",
            "identity": {"actor_id": 1001, "actor_login": "fixture-owner", "auth_profile": "fixture",
                         "repository_id": 101, "repository": "fixture/example", "readable": True,
                         "protocol": "ssh", "observed_at": now},
            "authenticated": True, "remote_reachable": True,
            "rules": {"state": "ALLOWED", "evidence": "Synthetic exact feature-ref rules allow creation/update"},
            "push_permitted": True, "draft_pr_permitted": True}


class PublicationReadinessTests(unittest.TestCase):
    def test_six_checks_are_required_and_name_each_precise_blocker(self):
        changes = [("AUTHENTICATION", lambda x: x.update(authenticated=False)),
                   ("REMOTE_REACHABILITY", lambda x: x.update(remote_reachable=False)),
                   ("BRANCH_ELIGIBILITY", lambda x: x.update(branch="main")),
                   ("APPLICABLE_RULES", lambda x: x["rules"].update(state="BLOCKED")),
                   ("PUSH_PERMISSION", lambda x: x.update(push_permitted=False)),
                   ("DRAFT_PR_PERMISSION", lambda x: x.update(draft_pr_permitted=False))]
        for code, change in changes:
            observed = publication_observation()
            change(observed)
            with self.subTest(code=code):
                report = publication_readiness(publication_config(), observed, ticket="EX-1", now=NOW)
                self.assertEqual([item["code"] for item in report["blockers"]], [code])
                with self.assertRaisesRegex(ValidationError, code):
                    require_publication_readiness(publication_config(), observed, ticket="EX-1", now=NOW)

    def test_ready_is_scoped_and_never_grants_authority(self):
        report = require_publication_readiness(publication_config(), publication_observation(), ticket="EX-1", now=NOW)
        self.assertEqual(report["status"], "READY")
        self.assertEqual(len(report["checks"]), 6)
        self.assertFalse(report["execution_authority"])
        self.assertEqual({row["state"] for row in publication_capabilities(report).values()}, {"AVAILABLE"})

    def test_push_failure_cannot_silently_select_owner_publication(self):
        observed = publication_observation()
        observed["push_permitted"] = False
        report = publication_readiness(publication_config(), observed, now=NOW)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertNotIn("owner_publication", report)
        self.assertEqual(publication_capabilities(report)["branch_publication"]["state"], "UNAVAILABLE")

    def test_wrong_actor_repository_and_profile_fail_before_publication(self):
        for field, value in (("actor_id", 2002), ("repository_id", 202), ("auth_profile", "other")):
            observed = publication_observation()
            observed["identity"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "AUTHENTICATION"):
                require_publication_readiness(publication_config(), observed, ticket="EX-1", now=NOW)

    def test_stale_foreign_malformed_or_unobserved_inputs_are_refused(self):
        mutations = [lambda x: x.update(observed_at="2026-10-02T09:00:00Z"),
                     lambda x: x.update(observed_at="2026-10-02T10:00:01Z"),
                     lambda x: x.update(ticket="EX-2"), lambda x: x.update(host="https://other.invalid"),
                     lambda x: x.update(source="synthetic_fixture"), lambda x: x.update(push_permitted=1),
                     lambda x: x.update(extra="unknown"), lambda x: x["rules"].update(state="UNOBSERVED"),
                     lambda x: x["identity"].update(observed_at="2026-10-02T09:00:00Z")]
        for mutate in mutations:
            observed = publication_observation()
            mutate(observed)
            with self.subTest(observed=observed), self.assertRaises(ValidationError):
                require_publication_readiness(publication_config(), observed, ticket="EX-1", now=NOW)

    def test_branch_patterns_reject_template_expressions_and_ref_injection(self):
        for pattern in ("codex/{ticket.__class__}-{slug}", "codex/{ticket!r}-{slug}", "codex/{ticket}-{ticket}"):
            config = publication_config()
            config["github"]["branch_pattern"] = pattern
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValidationError, "BRANCH_ELIGIBILITY"):
                require_publication_readiness(config, publication_observation(), ticket="EX-1", now=NOW)
        for branch in ("--all", "refs/tags/v1", "codex/EX-1-fixture\nmain", "codex/EX-2-fixture"):
            observed = publication_observation()
            observed["branch"] = branch
            with self.subTest(branch=branch), self.assertRaises(ValidationError):
                require_publication_readiness(publication_config(), observed, ticket="EX-1", now=NOW)


if __name__ == "__main__":
    unittest.main()
