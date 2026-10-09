"""Synthetic two-account and two-connection provider identity regressions."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentic.cli import main as workflow_main
from agentic.host_preflight import preflight
from agentic.provider_identity import (
    ProviderIdentityError,
    discover_jira_binding_candidates,
    github_identity_preflight,
    jira_binding_plan,
    jira_identity_preflight,
    observe_github_identity,
    observe_jira_identity,
    require_jira_write_identity,
)
from agentic.upgrade import _insert_unbound_jira_identity


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-10-03T10:00:00Z"


def config():
    return json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))


def github_observation(**overrides):
    value = {"actor_id": 1001, "actor_login": "fixture-owner", "auth_profile": "fixture",
             "repository_id": 101, "repository": "fixture/example", "readable": True,
             "protocol": "ssh", "observed_at": NOW}
    value.update(overrides)
    return value


def connections():
    return [
        {"connection_id": "corporate", "cloud_id": "other-cloud",
         "site": "https://corporate.example.invalid", "account_id": "other-account",
         "account_login": "corporate-user", "observed_at": NOW,
         "projects": [{"project_id": "other-project", "project_key": "EX", "browse": True}]},
        {"connection_id": "personal", "cloud_id": "fixture-cloud",
         "site": "https://jira.example.invalid", "account_id": "fixture-controller",
         "account_login": "fixture-user", "observed_at": NOW,
         "projects": [{"project_id": "fixture-project", "project_key": "EX", "browse": True}]},
    ]


class ProviderIdentityTests(unittest.TestCase):
    def test_expected_github_actor_repository_and_protocol_are_reported_without_tokens(self):
        result = github_identity_preflight(config(), github_observation())
        self.assertEqual((result["status"], result["observed"]["actor_id"],
                          result["observed"]["protocol"]), ("PASS", 1001, "ssh"))
        self.assertFalse(result["changes_attempted"])
        self.assertFalse(result["tokens_reported"])

    def test_wrong_github_actor_or_repository_fails_by_immutable_id(self):
        for changes, field in (({"actor_id": 2002}, "actor_id"),
                               ({"repository_id": 202}, "repository_id"),
                               ({"auth_profile": "other"}, "auth_profile")):
            with self.subTest(field=field), self.assertRaises(ProviderIdentityError) as raised:
                github_identity_preflight(config(), github_observation(**changes))
            self.assertEqual(raised.exception.code, "IDENTITY_MISMATCH")
            self.assertIn(field, str(raised.exception))

    def test_github_read_denial_and_extra_secret_field_fail_closed(self):
        with self.assertRaises(ProviderIdentityError) as denied:
            github_identity_preflight(config(), github_observation(readable=False))
        self.assertEqual(denied.exception.code, "ACCESS_DENIED")
        leaked = github_observation() | {"token": "never-accept"}
        with self.assertRaises(ProviderIdentityError) as malformed:
            github_identity_preflight(config(), leaked)
        self.assertEqual(malformed.exception.code, "INVALID_OBSERVATION")
        with self.assertRaises(ProviderIdentityError) as malformed_profile:
            github_identity_preflight(config(), github_observation(auth_profile={"secret": True}))
        self.assertEqual(malformed_profile.exception.code, "INVALID_OBSERVATION")

    def test_github_adapter_reads_configured_numeric_repository_without_switching(self):
        calls = []
        result = observe_github_identity(config(),
            read_actor=lambda: {"id": 1001, "login": "fixture-owner",
                "auth_profile": "fixture", "observed_at": NOW},
            read_repository=lambda repository_id: calls.append(repository_id) or {
                "id": 101, "name_with_owner": "fixture/example", "readable": True},
            observe_protocol=lambda: "https")
        self.assertEqual(calls, [101])
        self.assertEqual(result["observed"]["protocol"], "https")
        self.assertFalse(result["changes_attempted"])

    def test_profile_can_select_account_when_expected_actor_id_is_unset(self):
        value = config()
        value["github"]["expected_actor_id"] = None
        value["github"]["expected_actor_login"] = None
        self.assertEqual(github_identity_preflight(value, github_observation())["status"], "PASS")
        value["github"]["auth_profile"] = None
        with self.assertRaises(ProviderIdentityError) as raised:
            github_identity_preflight(value, github_observation())
        self.assertEqual(raised.exception.code, "IDENTITY_UNBOUND")

    def test_jira_resolves_configured_cloud_not_first_connection(self):
        result = jira_identity_preflight(config(), connections())
        self.assertEqual((result["status"], result["observed"]["connection_id"]),
                         ("PASS", "personal"))
        self.assertEqual((result["issue_lookups"], result["writes_attempted"]), (0, 0))
        via_adapter = observe_jira_identity(config(), list_connections=connections)
        self.assertEqual(via_adapter["observed"]["cloud_id"], "fixture-cloud")

    def test_host_preflight_reports_both_bound_provider_identities(self):
        report = preflight(ROOT, platform="posix", config=config(),
                           github_observation=github_observation(), jira_connections=connections())
        rows = {item["check"]: item for item in report["rows"]}
        self.assertEqual(rows["github_identity"]["status"], "PASS")
        self.assertIn("actor_id=1001", rows["github_identity"]["detail"])
        self.assertEqual(rows["jira_identity"]["status"], "PASS")
        self.assertIn("cloud_id=fixture-cloud", rows["jira_identity"]["detail"])

    def test_wrong_jira_cloud_account_site_project_or_browse_fails(self):
        cases = []
        wrong_cloud = config(); wrong_cloud["jira"]["cloud_id"] = "missing-cloud"
        cases.append((wrong_cloud, connections(), "IDENTITY_MISMATCH"))
        wrong_account = connections(); wrong_account[1]["account_id"] = "wrong-account"
        cases.append((config(), wrong_account, "IDENTITY_MISMATCH"))
        wrong_site = connections(); wrong_site[1]["site"] = "https://wrong.example.invalid"
        cases.append((config(), wrong_site, "IDENTITY_MISMATCH"))
        wrong_project = connections(); wrong_project[1]["projects"][0]["project_id"] = "wrong-project"
        cases.append((config(), wrong_project, "IDENTITY_MISMATCH"))
        no_browse = connections(); no_browse[1]["projects"][0]["browse"] = False
        cases.append((config(), no_browse, "ACCESS_DENIED"))
        for cfg, observed, code in cases:
            with self.subTest(code=code), self.assertRaises(ProviderIdentityError) as raised:
                jira_identity_preflight(cfg, observed)
            self.assertEqual(raised.exception.code, code)

    def test_unbound_upgraded_jira_refuses_writes_while_discovery_reads_continue(self):
        value = config()
        value["jira"].update(cloud_id=None, provider_project_id=None, controller_actor_id=None)
        candidates = discover_jira_binding_candidates(value, connections())
        self.assertEqual(len(candidates), 1)
        with self.assertRaises(ProviderIdentityError) as raised:
            require_jira_write_identity(value, {})
        self.assertEqual(raised.exception.code, "IDENTITY_UNBOUND")

    def test_binding_plan_needs_exact_current_candidates_and_trusted_owner(self):
        value = config()
        value["jira"].update(cloud_id=None, provider_project_id=None, controller_actor_id=None)
        discovered = jira_binding_plan(value, connections(), now=NOW)
        candidate = discovered["candidates"][0]
        confirmation = {"format": "awf-jira-binding-confirmation-1", "decision": "BIND",
            "candidate_sha256": discovered["candidate_sha256"],
            "cloud_id": candidate["cloud_id"], "site": candidate["site"],
            "project_id": candidate["project_id"], "project_key": candidate["project_key"],
            "account_id": candidate["account_id"], "owner_id": 1001,
            "confirmed_at": NOW, "authentication_evidence": ["urn:awf:fixture:owner-auth"]}
        confirmed = jira_binding_plan(value, connections(), confirmation, now=NOW)
        self.assertEqual(confirmed["status"], "CONFIRMED")
        self.assertEqual(confirmed["binding_patch"]["cloud_id"], "fixture-cloud")
        for field, replacement in (("candidate_sha256", "0" * 64), ("owner_id", 9999)):
            wrong = dict(confirmation); wrong[field] = replacement
            with self.subTest(field=field), self.assertRaises(ProviderIdentityError):
                jira_binding_plan(value, connections(), wrong, now=NOW)

    def test_workflow_jira_bind_creates_new_proposal_only_after_confirmation(self):
        value = config()
        value["jira"].update(cloud_id=None, provider_project_id=None, controller_actor_id=None)
        plan = jira_binding_plan(value, connections(), now=NOW)
        candidate = plan["candidates"][0]
        confirmation = {"format": "awf-jira-binding-confirmation-1", "decision": "BIND",
            "candidate_sha256": plan["candidate_sha256"], "cloud_id": candidate["cloud_id"],
            "site": candidate["site"], "project_id": candidate["project_id"],
            "project_key": candidate["project_key"], "account_id": candidate["account_id"],
            "owner_id": 1001, "confirmed_at": NOW,
            "authentication_evidence": ["urn:awf:fixture:owner-auth"]}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = {}
            for name, payload in (("config.json", value), ("connections.json", connections()),
                                  ("confirmation.json", confirmation)):
                paths[name] = folder / name
                paths[name].write_text(json.dumps(payload), encoding="utf-8")
            output = folder / "proposed.json"
            with patch("agentic.cli.verify_installed", return_value="a" * 64), \
                    patch("agentic.cli.now_text", return_value=NOW):
                code = workflow_main(["--root", str(ROOT), "jira", "bind",
                    "--config", str(paths["config.json"]), "--connections", str(paths["connections.json"]),
                    "--confirmation", str(paths["confirmation.json"]), "--output", str(output)])
            self.assertEqual(code, 0)
            proposed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(proposed["jira"]["cloud_id"], "fixture-cloud")
            self.assertIsNone(value["jira"]["cloud_id"])
            with patch("agentic.cli.verify_installed", return_value="a" * 64), \
                    patch("agentic.cli.now_text", return_value=NOW):
                self.assertEqual(workflow_main(["--root", str(ROOT), "jira", "bind",
                    "--config", str(paths["config.json"]), "--connections", str(paths["connections.json"]),
                    "--output", str(folder / "unconfirmed.json")]), 2)
            self.assertFalse((folder / "unconfirmed.json").exists())

    def test_192_migration_adds_unbound_jira_identity_and_preserves_crlf(self):
        raw = (b'{\r\n  "jira": {\r\n    "enabled": true,\r\n'
               b'    "site": "https://jira.example.invalid",\r\n'
               b'    "project_key": "EX"\r\n  }\r\n}\r\n')
        migrated = _insert_unbound_jira_identity(raw)
        value = json.loads(migrated)
        self.assertEqual({name: value["jira"][name] for name in
            ("cloud_id", "provider_project_id", "controller_actor_id")}, {
                "cloud_id": None, "provider_project_id": None, "controller_actor_id": None})
        self.assertNotIn(b"\n", migrated.replace(b"\r\n", b""))
        self.assertEqual(_insert_unbound_jira_identity(migrated), migrated)


if __name__ == "__main__":
    unittest.main()
