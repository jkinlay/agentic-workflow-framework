"""O1c: reviewer differentials, diagnostic warnings and closed digest schema."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401
import test_provider_identity as provider
import test_ready_authorization as ready
import test_intake_assignment as intake_fixture
import test_streams as stream_fixture
import test_awf307_controller_owner_policy as controller_fixture
import test_adoption_status as status_fixture
from shipped_defaults import shipped_routing
from agentic import (ValidationError, authorization, intake_assignment,
                     integration_queue, policy, review_completion, streams)
from agentic.canonical import canonical, load, sha256
from agentic.cli import main
from agentic.contracts import Contracts
from agentic.lifecycle import definition
from agentic.providers.github_status import render_status

ROOT = Path(__file__).resolve().parents[2]
CODE = "SHARED_OWNER_AUTOMATION_ACCOUNT"


class LegacyNumericConsumers(unittest.TestCase):
    """Same fixtures and outcomes as compare.py and supplement_compare.py."""
    def setUp(self):
        self.enterContext(shipped_routing())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = provider.config()
        self.config["merge_gate"]["shared_owner_automation_actor_id"] = None

    def test_empty_root_preflight(self):
        result = provider.github_identity_preflight(self.config, provider.github_observation(),
                                                    accepted_policy_root=self.root)
        self.assertEqual("PASS", result["status"])

    def test_empty_root_profile_overlap_exact_parent_message(self):
        with self.assertRaises(ValidationError) as raised:
            provider.github_identity_preflight(self.config, provider.github_observation(actor_id=1001),
                                               accepted_policy_root=self.root)
        self.assertEqual("The observed automation actor must not be a trusted owner", str(raised.exception))

    def test_empty_root_policy(self):
        result = policy.validate_config(self.config, definition(), Contracts(ROOT / ".agentic/schemas"),
                                        accepted_policy_root=self.root)
        self.assertEqual(policy.policy_hash(self.config, definition()), result)

    def test_empty_root_review(self):
        result = review_completion.review_authority_from_config(self.config, accepted_policy_root=self.root)
        self.assertEqual({"trusted_owner_ids": (1001,), "provider_identity": {
            "host": "https://github.com", "repository": "fixture/example", "repository_id": 101}}, result)

    def test_empty_root_queue(self):
        self.config["validation"]["required_ci_checks"][0]["name"] = "awf-integration"
        result = integration_queue._authority(self.config, accepted_policy_root=self.root)
        self.assertEqual((1001,), result["trusted_owner_ids"])
        self.assertNotIn("shared_owner_automation_account", result)

    def test_empty_root_jira_binding(self):
        plan = provider.jira_binding_plan(self.config, provider.connections(), now=provider.NOW)
        candidate = plan["candidates"][0]
        confirmation = {"format": "awf-jira-binding-confirmation-1", "decision": "BIND",
            "candidate_sha256": plan["candidate_sha256"],
            **{k: candidate[k] for k in ("cloud_id", "site", "project_id", "project_key", "account_id")},
            "owner_id": 1001, "confirmed_at": provider.NOW,
            "authentication_evidence": ["urn:awf:fixture:owner-auth"]}
        result = provider.jira_binding_plan(self.config, provider.connections(), confirmation,
                                            now=provider.NOW, accepted_policy_root=self.root)
        self.assertEqual("CONFIRMED", result["status"])

    def test_empty_root_ready(self):
        fixture = ready.ReadyAuthorizationTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.config = deepcopy(self.config)
        fixture.config["github"]["repository_id"] = ready.REPOSITORY_ID
        fixture.provider = ready.FakeImmutableCommentProvider(fixture.config)
        result = authorization.verify_ready_authorization(fixture.authorization(), fixture.config,
            pr=67, head_sha=ready.HEAD, verify_owner_source=fixture.provider.verify,
            accepted_policy_root=self.root)
        self.assertEqual("MARK_READY", result["action"])

    def test_rootless_intake(self):
        value = intake_fixture.config()
        value["jira"]["enabled"] = False
        value["merge_gate"]["shared_owner_automation_actor_id"] = None
        state = {}
        current = intake_assignment.assign_intake(value, intake_fixture.ticket(), state, None,
            now=intake_fixture.NOW, controller_actor_id="fixture-controller")
        intake_assignment.inherit_pr_assignment(state, "AWF-100", 48, "Fixture PR body")
        reason = "Synthetic owner decision"
        source = intake_fixture.owner_source("AWF-100", current["assignment_id"], 3, 3, reason)
        result = intake_assignment.change_assignment(value, "AWF-100", state, None,
            requested_tier=3, requested_cap=3, owner_source=source,
            verify_owner_source=intake_fixture.verify_owner_source, reason=reason,
            now=intake_fixture.NOW, controller_actor_id="fixture-controller")
        self.assertTrue(result)
        self.assertNotIn("shared_owner_automation_account", result)

    def test_rootless_stream_jira_snapshot(self):
        value = load(ROOT / ".agentic/examples/stream-input.json")
        value["inventory"]["source"] = "jira_snapshot"
        raw = canonical(value)
        now = "2026-09-11T10:00:00Z"
        before = streams.plan_inventory(raw, sha256(raw), now, allow_synthetic=True)
        self.config["github"]["repository"] = "fixture-only/awf-demo"
        self.config["jira"]["project_key"] = "DEMO"
        assignment = stream_fixture.intake_assignment()
        reason = "Synthetic owner stream decision"
        directive = {"ticket_id": "DEMO-31", "who": 1001, "when": now,
                     "why": reason, "old": "C", "new": "B"}
        directive["owner_source"] = stream_fixture.stream_owner_source(
            "DEMO-31", assignment["assignment_id"], "C", "B", reason, actor_id=1001)
        result = streams.plan_inventory(raw, sha256(raw), now, allow_synthetic=True,
            previous=before, governance=self.config, intake_assignments={"DEMO-31": assignment},
            owner_reassignments=[directive], verify_owner_reassignment=stream_fixture.verify_stream_owner_source)
        event = next(row for row in result["stream_assignment_history"] if row["kind"] == "owner_reassignment")
        self.assertEqual(1001, event["who"])
        self.assertNotIn("shared_owner_automation_account", event)


class WarningSurfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status_fixture.AdoptionStatusTests.setUpClass()
        cls.project = status_fixture.AdoptionStatusTests.root
        value = load(cls.project / ".agentic/PROJECT_CONFIG.yaml")
        value["merge_gate"].update(trusted_owner_ids=[1001, 1002], shared_owner_automation_actor_id=1002)
        (cls.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(value))

    @classmethod
    def tearDownClass(cls):
        status_fixture.AdoptionStatusTests.tearDownClass()

    def invoke(self, command, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--root", str(self.project), command, *arguments])
        return code, stdout.getvalue() + stderr.getvalue()

    def assert_warning(self, text):
        self.assertIn(CODE, text)
        self.assertIn("account 1002", text)

    def test_verify_installation_warning_does_not_block(self):
        code, text = self.invoke("verify-installation")
        self.assertEqual(0, code)
        self.assert_warning(text)

    def test_verify_installation_refusal_still_warns(self):
        with patch("agentic.cli.verify_installed", side_effect=ValidationError("Synthetic integrity refusal")):
            code, text = self.invoke("verify-installation")
        self.assertEqual(2, code)
        self.assertIn("Synthetic integrity refusal", text)
        self.assert_warning(text)

    def test_validate_config_integrity_refusal_still_warns(self):
        with patch("agentic.cli.verify_installed", side_effect=ValidationError("Synthetic integrity refusal")):
            code, text = self.invoke("validate-config")
        self.assertEqual(2, code)
        self.assert_warning(text)

    def test_verify_authorization_refusal_still_warns(self):
        config = str(self.project / ".agentic/PROJECT_CONFIG.yaml")
        # Admission refuses before the unused authorization files are read.
        code, text = self.invoke("verify-authorization", "--config", config,
                                 "--record", config, "--request", config, "--gate", config)
        self.assertEqual(2, code)
        self.assertIn("The configured automation actor must not be a trusted owner", text)
        self.assert_warning(text)

    def test_doctor_json_and_text_warn_even_with_missing_runtime(self):
        for arguments in ((), ("--json",)):
            with self.subTest(arguments=arguments):
                code, text = self.invoke("doctor", *arguments)
                self.assertEqual(2, code)
                self.assert_warning(text)

    def test_status_json_and_default_text_and_activation_warn(self):
        fixture = status_fixture.AdoptionStatusTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch("agentic.providers.github_status._gh_get", side_effect=AssertionError("Network forbidden")):
            report = fixture.observe()
        self.assert_warning(json.dumps(report))
        self.assert_warning(render_status(report))
        # CLI default text renders the same adoption/activation observation.
        with patch("agentic.providers.github_status.project_status", return_value=report):
            _, text = self.invoke("status")
        self.assert_warning(text)

    def test_status_integrity_refusal_still_warns(self):
        fixture = status_fixture.AdoptionStatusTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch("agentic.providers.github_status.verify_installed",
                   side_effect=ValidationError("Synthetic integrity refusal")), \
                patch("agentic.providers.github_status._gh_get", side_effect=AssertionError("Network forbidden")):
            report = fixture.observe()
        self.assertFalse(report["integrity_valid"])
        self.assert_warning(json.dumps(report))
        self.assert_warning(render_status(report))


class ControllerSeparationException(unittest.TestCase):
    def test_null_and_absent_overlap_and_replan_refusals_are_documented(self):
        for consumer, fixture_class in (("overlap", controller_fixture.OverlapPolicyTests),
                                        ("replan", controller_fixture.ReplanPolicyTests)):
            for present in (True, False):
                with self.subTest(consumer=consumer, setting_present=present):
                    case = fixture_class("runTest")
                    case.setUp()
                    try:
                        if present:
                            case.config["merge_gate"]["shared_owner_automation_actor_id"] = None
                        else:
                            case.config["merge_gate"].pop("shared_owner_automation_actor_id")
                        with self.assertRaises(ValidationError) as raised:
                            case.call(rooted=False)
                        self.assertEqual("The configured automation actor must not be a trusted owner",
                                         str(raised.exception))
                        case.accept()
                        with case.policy_reads(), self.assertRaises(ValidationError) as rooted:
                            case.call()
                        self.assertEqual(str(raised.exception), str(rooted.exception))
                    finally:
                        case.doCleanups()
        text = (ROOT / ".agentic/docs/38-PROVIDER-IDENTITY-AND-CREDENTIALS.md").read_text()
        self.assertIn("Null/absent exception to legacy preservation: controller overlap/replan approvals", text)
        self.assertIn("refuse configured automation actors in `trusted_owner_ids` for account separation.", text)


class DigestSchema(unittest.TestCase):
    def test_warned_controller_digest_validates_closed_schema(self):
        case = controller_fixture.OverlapPolicyTests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        store = case.store()
        with case.policy_reads():
            record = case.call()
            store.register_overlap_allowance(record, controller_fixture.NOW,
                owner_config=case.config, accepted_policy_root=case.project)
            store.schedule(case.inventory, controller_fixture.NOW, 2, [{"path": record["binding"]["path"],
                "tickets": record["binding"]["tickets"], "record": record}],
                owner_config=case.config, accepted_policy_root=case.project)
            digest = store.digest(controller_fixture.NOW, owner_config=case.config,
                                  accepted_policy_root=case.project)
        self.assertEqual(CODE, digest["owner_policy_warnings"][0]["code"])
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("controller-status-digest", digest)
        invalid = deepcopy(digest)
        invalid["owner_policy_warnings"][0]["unexpected"] = True
        with self.assertRaises(ValidationError):
            contracts.validate("controller-status-digest", invalid)


if __name__ == "__main__":
    unittest.main()
