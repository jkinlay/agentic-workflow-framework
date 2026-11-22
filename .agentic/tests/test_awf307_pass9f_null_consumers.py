"""Pass 9f: legacy null/absent account consumers retain main's outcomes."""
from copy import deepcopy
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401
import test_ready_authorization as ready
import test_provider_identity as provider
import test_streams as streams_fixture
from agentic import ValidationError, authorization, integration_queue, policy, review_completion, streams
from agentic.canonical import canonical, load, sha256
from agentic.contracts import Contracts
from agentic.lifecycle import definition
OWNER_EVIDENCE_FIELDS = {"shared_owner_automation_account", "owner_policy_sha256", "accepted_owner_policy"}
from shipped_defaults import shipped_routing

ROOT = Path(__file__).resolve().parents[2]


def optional_root(function, root):
    return ({"accepted_policy_root": root}
            if "accepted_policy_root" in inspect.signature(function).parameters else {})


class LegacyNullConsumers(unittest.TestCase):
    def setUp(self):
        self.enterContext(shipped_routing())
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.folder)

    def config(self, absent):
        value = provider.config()
        value["merge_gate"]["shared_owner_automation_actor_id"] = None
        value["github"]["expected_actor_id"] = None
        if absent:
            del value["github"]["expected_actor_id"]
        return value

    def ready_case(self, absent):
        fixture = ready.ReadyAuthorizationTests("runTest")
        fixture.setUp()
        fixture.config["merge_gate"]["shared_owner_automation_actor_id"] = None
        if not absent:
            fixture.config["github"]["expected_actor_id"] = None
        fixture.provider = ready.FakeImmutableCommentProvider(fixture.config)
        record = fixture.authorization()
        result = authorization.verify_ready_authorization(record, fixture.config,
            pr=67, head_sha=ready.HEAD, verify_owner_source=fixture.provider.verify,
            **optional_root(authorization.verify_ready_authorization, self.root))
        self.assertEqual("MARK_READY", result["action"])
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))
        record["owner_source"]["actor_id"] = 9999
        with self.assertRaises(ValidationError) as raised:
            authorization.verify_ready_authorization(record, fixture.config,
                pr=67, head_sha=ready.HEAD, verify_owner_source=fixture.provider.verify,
                **optional_root(authorization.verify_ready_authorization, self.root))
        self.assertEqual("Ready authorization uses an untrusted numeric owner/manager identity", str(raised.exception))

    def binding_case(self, absent):
        value = self.config(absent)
        plan = provider.jira_binding_plan(value, provider.connections(), now=provider.NOW)
        candidate = plan["candidates"][0]
        confirmation = {"format": "awf-jira-binding-confirmation-1", "decision": "BIND",
            "candidate_sha256": plan["candidate_sha256"],
            **{key: candidate[key] for key in ("cloud_id", "site", "project_id", "project_key", "account_id")},
            "owner_id": 1001, "confirmed_at": provider.NOW,
            "authentication_evidence": ["urn:awf:fixture:owner-auth"]}
        for root in (None, self.root):
            options = optional_root(provider.jira_binding_plan, root)
            result = provider.jira_binding_plan(value, provider.connections(), confirmation, now=provider.NOW, **options)
            self.assertEqual("CONFIRMED", result["status"])
            self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))
            bad = deepcopy(confirmation)
            bad["authentication_evidence"] = []
            with self.assertRaises(provider.ProviderIdentityError) as raised:
                provider.jira_binding_plan(value, provider.connections(), bad, now=provider.NOW, **options)
            self.assertEqual("Jira binding confirmation needs host authentication evidence", str(raised.exception))

    def review_case(self, absent):
        value = self.config(absent)
        for root in (None, self.root):
            result = review_completion.review_authority_from_config(value,
                **optional_root(review_completion.review_authority_from_config, root))
            self.assertEqual({"trusted_owner_ids": (1001,), "provider_identity": {
                "host": "https://github.com", "repository": "fixture/example", "repository_id": 101}}, result)
            bad = deepcopy(value)
            bad["merge_gate"]["trusted_owner_ids"] = []
            with self.assertRaises(ValidationError) as raised:
                review_completion.review_authority_from_config(bad,
                    **optional_root(review_completion.review_authority_from_config, root))
            self.assertEqual("Accepted PROJECT_CONFIG needs unique positive trusted owner IDs", str(raised.exception))

    def queue_case(self, absent):
        value = self.config(absent)
        value["validation"]["required_ci_checks"][0]["name"] = "awf-integration"
        for root in (None, self.root):
            result = integration_queue._authority(value, **optional_root(integration_queue._authority, root))
            self.assertEqual((1001,), result["trusted_owner_ids"])
            self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))
            bad = deepcopy(value)
            bad["validation"]["required_ci_checks"] = []
            with self.assertRaises(ValidationError) as raised:
                integration_queue._authority(bad, **optional_root(integration_queue._authority, root))
            self.assertEqual("accepted configuration requires exactly one awf-integration CI check binding", str(raised.exception))

    def stream_case(self, absent):
        value = self.config(absent)
        value["github"]["repository"] = "fixture-only/awf-demo"
        value["jira"]["project_key"] = "DEMO"
        inventory = load(ROOT / ".agentic/examples/stream-input.json")
        inventory["inventory"]["source"] = "jira_snapshot"
        raw = canonical(inventory)
        now = "2026-09-11T10:00:00Z"
        prior = streams.plan_inventory(raw, sha256(raw), now, allow_synthetic=True)
        assignment = streams_fixture.intake_assignment()
        directive = {"ticket_id": "DEMO-31", "who": 1001, "when": now,
                     "why": "Synthetic owner stream decision", "old": "C", "new": "B"}
        directive["owner_source"] = streams_fixture.stream_owner_source(
            "DEMO-31", assignment["assignment_id"], "C", "B", directive["why"], actor_id=1001)
        for root in (None, self.root):
            result = streams.plan_inventory(raw, sha256(raw), now, allow_synthetic=True,
                previous=prior, governance=value, intake_assignments={"DEMO-31": assignment},
                owner_reassignments=[directive], verify_owner_reassignment=streams_fixture.verify_stream_owner_source,
                **optional_root(streams.plan_inventory, root))
            event = next(event for event in result["stream_assignment_history"] if event["kind"] == "owner_reassignment")
            self.assertEqual(1001, event["who"])
            self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(event))

    def preflight_case(self, absent):
        value = self.config(absent)
        result = provider.github_identity_preflight(value, provider.github_observation(),
            **optional_root(provider.github_identity_preflight, self.root))
        self.assertEqual("PASS", result["status"])
        with self.assertRaises(provider.ProviderIdentityError) as raised:
            provider.github_identity_preflight(value, provider.github_observation(actor_id=1001),
                **optional_root(provider.github_identity_preflight, self.root))
        self.assertEqual("The configured automation actor must not be a trusted owner", str(raised.exception))

    def policy_case(self, absent):
        value = self.config(absent)
        result = policy.validate_config(value, definition(), Contracts(ROOT / ".agentic/schemas"),
            **optional_root(policy.validate_config, self.root))
        self.assertEqual(policy.policy_hash(value, definition()), result)


    def intake_case(self, absent):
        import test_intake_assignment as fixture
        from agentic import intake_assignment
        value = fixture.config()
        value["jira"]["enabled"] = False
        value["github"]["expected_actor_id"] = None
        value["merge_gate"]["shared_owner_automation_actor_id"] = None
        if absent:
            del value["github"]["expected_actor_id"]
        state = {}
        current = intake_assignment.assign_intake(value, fixture.ticket(), state, None,
            now=fixture.NOW, controller_actor_id="fixture-controller")
        intake_assignment.inherit_pr_assignment(state, "AWF-100", 48, "Fixture PR body")
        reason = "Synthetic owner decision"
        source = fixture.owner_source("AWF-100", current["assignment_id"], 3, 3, reason)
        result = intake_assignment.change_assignment(value, "AWF-100", state, None,
            requested_tier=3, requested_cap=3, owner_source=source,
            verify_owner_source=fixture.verify_owner_source, reason=reason,
            now=fixture.NOW, controller_actor_id="fixture-controller",
            **optional_root(intake_assignment.change_assignment, None))
        self.assertTrue(result)
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))

    def reference_fixture(self, absent):
        import test_reference as fixture
        import test_review_policy as binding_fixture
        class ReferenceCase(fixture.Fixture, binding_fixture.Fixture):
            pass
        ReferenceCase.setUpClass()
        case = ReferenceCase("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.config["github"]["expected_actor_id"] = None
        case.config["merge_gate"]["shared_owner_automation_actor_id"] = None
        if absent:
            del case.config["github"]["expected_actor_id"]
        case.rebind()
        return case, fixture

    def merge_authorization_case(self, absent):
        case, fixture = self.reference_fixture(absent)
        gate, request, record = case.authorize()
        result = authorization.verify_record(record, request, gate, case.config, case.contracts, fixture.NOW,
            **optional_root(authorization.verify_record, self.root))
        self.assertEqual({"decision": "AUTHORIZE", "record_consistent": True,
            "live_source_verified": False, "owner_quorum_verified": False,
            "execution_authority": False}, result)

    def evaluate_case(self, absent):
        from agentic import gates
        case, fixture = self.reference_fixture(absent)
        fixture.bind_review_admission(case.bundle)
        registry = fixture.bind_production_posting_fixture(case.config, case.bundle)
        result = gates.evaluate(case.config, definition(), case.bundle, case.contracts, fixture.NOW,
            posting_collector_registry=registry, **optional_root(gates.evaluate, self.root))
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", result["conclusion"])

    def overcap_case(self, absent):
        import test_review_tiers_195_overcap as fixture
        from agentic import gates
        fixture.Tier3OvercapGateTests.setUpClass()
        case = fixture.Tier3OvercapGateTests("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.config["github"]["expected_actor_id"] = None
        case.config["merge_gate"]["shared_owner_automation_actor_id"] = None
        if absent:
            del case.config["github"]["expected_actor_id"]
        fixture.bind_review_admission(case.bundle)
        registry = fixture.bind_production_posting_fixture(case.config, case.bundle)
        approval = case.authenticated_approval(registry)
        runs = {run["run_id"]: run for run in case.bundle["runs"]}
        result = gates.verified_overcap_approvals(case.config, [approval], case.bundle["candidate"],
            runs, registry, **optional_root(gates.verified_overcap_approvals, self.root))
        self.assertEqual([approval], result)


    def rootless_train_case(self, absent):
        from contextlib import nullcontext
        import test_integration_queue as provider_fixture
        import test_merge_train as train_fixture
        from agentic import merge_train, provenance
        value = deepcopy(provider_fixture.PROJECT_CONFIG)
        value["github"]["expected_actor_id"] = None
        value["merge_gate"]["shared_owner_automation_actor_id"] = None
        if absent:
            del value["github"]["expected_actor_id"]
        registry = {}
        provider_fixture.register_integration_observer("fixture-null-policy",
            lambda request: provider_fixture.accepted_config_result(value), registry,
            observed_at=provider_fixture.NOW)
        observed = {}
        options = ({"owner_observation": observed}
                   if "owner_observation" in inspect.signature(integration_queue._accepted_project_config).parameters else {})
        integration_queue._accepted_project_config(value, {"producer_id": "fixture-null-policy"},
            registry, now=provider_fixture.NOW, return_attestation=True, **options)
        case = train_fixture.MergeTrainTests("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        self.addCleanup(case.doCleanups)
        source = case._source()
        evidence = case._evidence(source, reviewer_id=1001)
        context = (provenance._provider_owner_authority(value, observed, registry, now=provider_fixture.NOW)
                   if hasattr(provenance, "_provider_owner_authority") else nullcontext())
        with context:
            result = merge_train._validate_admission_evidence(source, evidence, [1001],
                now=train_fixture.NOW, max_age_seconds=300)
            self.assertTrue(result)


    def unreadable_head_case(self, absent):
        with patch("agentic.shared_owner_policy._default_ref", return_value=("refs/remotes/origin/main", "fixture")), \
                patch("agentic.shared_owner_policy._git", side_effect=ValidationError("Fixture unreadable HEAD")):
            self.stream_case(absent)



for _consumer in ("ready", "binding", "review", "queue", "stream", "preflight", "policy", "intake", "merge_authorization", "evaluate", "overcap", "rootless_train", "unreadable_head"):
    for _absent in (False, True):
        def regression(self, consumer=_consumer, absent=_absent):
            getattr(self, consumer + "_case")(absent)
        setattr(LegacyNullConsumers, "test_" + _consumer + ("_absent_key" if _absent else "_null_key"), regression)


if __name__ == "__main__":
    unittest.main()
