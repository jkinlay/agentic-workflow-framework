"""O1c: reviewer differentials, diagnostic warnings and closed digest schema."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import inspect
import json
import os
from pathlib import Path
import sys
import subprocess
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


@contextmanager
def replacing_during_policy_read(number, replace):
    """Inject one caller mutation while the real immutable policy read runs."""
    if number is None:
        yield
        return
    from agentic import shared_owner_policy
    original, reads = shared_owner_policy._policy_blob, []

    def read(*arguments):
        reads.append(arguments)
        if len(reads) == number:
            replace()
        return original(*arguments)
    with patch.object(shared_owner_policy, "_policy_blob", side_effect=read):
        yield
    if len(reads) < number:
        raise AssertionError("policy read was not reached")


def review_removal_record(current, old, provider_identity, signer):
    from datetime import datetime, timedelta, timezone
    from agentic.canonical import fingerprint
    now = datetime.now(timezone.utc)
    return {"schema_version": 1, "disposition_id": "11111111-1111-4111-8111-111111111111",
            "decision": "REMOVE_REQUIRED_REVIEWERS", "old_cycle_id": old["cycle_id"],
            "tuple": deepcopy(current), "tuple_sha256": old["tuple_sha256"],
            "old_required_reviewers": ["a", "b", "c"],
            "old_reviewer_set_sha256": old["reviewer_set_sha256"],
            "new_required_reviewers": ["a", "b"],
            "new_reviewer_set_sha256": fingerprint("reviewer-set", ["a", "b"]),
            "removed_reviewers": ["c"], "reason": "Owner approved the revised set.",
            "owner_actor_id": signer, "provider_identity": deepcopy(provider_identity),
            "issued_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=4)).isoformat().replace("+00:00", "Z")}


def review_projection_scenario(consumer, config, call, root, signer, replace_on_read):
    import test_review_completion as completion_fixture
    from agentic.canonical import fingerprint
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        worktree = folder / "worktree"
        worktree.mkdir()
        project = folder if root is None else root
        current = completion_fixture.candidate()
        current["repository"] = config["github"]["repository"]
        binding = call(review_completion.review_authority_binding, config, project,
                       [worktree], current)
        if consumer == "review-binding":
            paths = [os.path.normcase(str(Path(item).resolve())) for item in (project, worktree)]
            if [binding["project_root"], *binding["worktree_roots"]] != paths:
                raise AssertionError("binding paths differ")
            return {**binding, "project_root": "ROOT", "worktree_roots": ["WORKTREE"]}
        observations = []

        def observer(request):
            observations.append(request)
            return completion_fixture.ReviewCompletionTests.provider_review(request)
        ledger = call(review_completion.ReviewCompletionStore, folder / "state.sqlite3", [worktree],
            authority=call(review_completion.review_authority_from_config, config),
            authority_binding=binding, provider_observer=observer)
        old = ledger.freeze(current, ["a", "b", "c"])
        ledger.dispatch("c", current, ["a", "b", "c"])
        record = review_removal_record(current, old, ledger.authority["provider_identity"], signer)
        authenticated = deepcopy(record)
        disposition = {"record": record, "record_sha256": fingerprint(
            "reviewer-removal-disposition", record)}
        replaced = []

        def replace():
            # Only after the provider authenticated this record.
            record.update(owner_actor_id=1002 if signer == 1001 else 1001)
            replaced.append(True)
        if replace_on_read:
            from agentic import shared_owner_policy
            original = shared_owner_policy._policy_blob

            def read(*arguments):
                if observations and not replaced:
                    replace()
                return original(*arguments)
            with patch.object(shared_owner_policy, "_policy_blob", side_effect=read):
                result = ledger.freeze(current, ["a", "b"], disposition)
        else:
            result = ledger.freeze(current, ["a", "b"], disposition)
        audit = ledger.reviewer_set_audit()
        return {"reviewers": result["required_reviewers"], "entries": len(audit),
                "authenticated_record_retained": audit[0]["record"] == authenticated,
                "replaced": bool(replaced)}


def projection_scenario(consumer, config, root=None, signer=1001, *, replace_on_read=False):
    """Run the two unsigned projections and what accepts owner records from them."""
    import test_integration_queue as queue_fixture
    from agentic.canonical import fingerprint

    def call(function, *arguments, **keywords):
        names = inspect.signature(function).parameters
        optional = {"accepted_policy_root": root, "project_config": config,
                    "accepted_policy_config": config if root is not None else None}
        for key, value in optional.items():
            if (key in names and value is not None and key not in keywords
                    and list(names).index(key) >= len(arguments)):
                keywords[key] = value
        return function(*arguments, **keywords)

    if consumer == "review-authority":
        # The owner list is a tuple, which canonical JSON cannot represent.
        return {"repr": repr(call(review_completion.review_authority_from_config, config))}
    if consumer.startswith("review-"):
        return review_projection_scenario(consumer, config, call, root, signer, replace_on_read)
    config = deepcopy(config)
    config["validation"]["required_ci_checks"][0]["name"] = "awf-integration"
    if consumer == "queue-authority":
        return {"repr": repr(call(integration_queue._authority, config))}
    if consumer == "queue-projection":
        return call(integration_queue._authority_projection, config)
    registry = {}

    def observe(request):
        if request["operation"] == "ACCEPTED_PROJECT_CONFIG":
            return queue_fixture.accepted_config_result(config)
        return {"instruction": deepcopy(request["instruction"])}
    queue_fixture.register_integration_observer("fixture-mode-provider", observe, registry,
                                                observed_at=queue_fixture.NOW)
    if consumer == "queue-accepted":
        return call(integration_queue._accepted_project_config, config,
            {"producer_id": "fixture-mode-provider"}, registry, now=queue_fixture.NOW)
    settings = {"test_command": "python -B -m unittest", "stall_after_hours": 24,
                "max_stack_depth": 2}
    value = queue_fixture.instruction("ENABLE_EPIC_MODE", settings, "projection-1")
    value["owner_actor_id"] = signer
    value["instruction_id"] = fingerprint("integration_owner_instruction",
        {key: item for key, item in value.items() if key != "instruction_id"})
    authority = call(integration_queue._authority, config)
    return call(integration_queue._owner_instruction, value, "ENABLE_EPIC_MODE", "AWF-EPIC",
        settings, authority=authority, registry=registry, now=queue_fixture.NOW, max_age=3600)


def stream_owner_scenario(config, root=None, signer=1001, *, retained=False, replace_during=None):
    """A live owner move, or a later plan over a move retained before the overlap."""
    from agentic.streams import plan_inventory
    config = deepcopy(config)
    config["github"]["repository"] = "fixture-only/awf-demo"
    config["jira"]["project_key"] = "DEMO"
    raw = canonical(load(ROOT / ".agentic/examples/stream-input.json"))
    now, reason = "2026-09-11T10:00:00Z", "Synthetic owner stream decision"
    assignment = stream_fixture.intake_assignment()
    directive = {"ticket_id": "DEMO-31", "who": signer, "when": now, "why": reason,
                 "old": "C", "new": "B", "owner_source": stream_fixture.stream_owner_source(
                     "DEMO-31", assignment["assignment_id"], "C", "B", reason, actor_id=signer)}

    def replace():
        directive.update(who=1002, owner_source={**directive["owner_source"], "actor_id": 1002})

    def verify(source, expected):
        result = stream_fixture.verify_stream_owner_source(source, expected)
        if replace_during == "verifier":
            replace()
        return result

    def plan(governance, previous, directives):
        options = ({"accepted_policy_root": root}
                   if "accepted_policy_root" in inspect.signature(plan_inventory).parameters else {})
        return plan_inventory(raw, sha256(raw), now, allow_synthetic=True, previous=previous,
            governance=governance, intake_assignments={"DEMO-31": assignment},
            owner_reassignments=directives, verify_owner_reassignment=verify, **options)
    before = plan_inventory(raw, sha256(raw), now, allow_synthetic=True)
    if not retained:
        with replacing_during_policy_read(1 if replace_during == "read" else None, replace):
            return plan(config, before, [directive])
    prior = plan_inventory(raw, sha256(raw), now, allow_synthetic=True, previous=before,
        governance={"merge_gate": {"trusted_owner_ids": [1001, 1002, 1003]}},
        intake_assignments={"DEMO-31": assignment}, owner_reassignments=[directive],
        verify_owner_reassignment=verify)
    return plan(config, prior, None)


SEPARATION = "The configured automation actor must not be a trusted owner"


def distinct_owner_scenario(consumer, config, root=None, signer=1001, *, replace_on_read=None):
    """Execute unchanged public entry points on the parent and corrective tree."""
    def options(function):
        return ({"accepted_policy_root": root}
                if "accepted_policy_root" in inspect.signature(function).parameters else {})
    if consumer in ("ready", "lifecycle-ready", "lifecycle-revalidate", "lifecycle-transition"):
        case = ready.ReadyAuthorizationTests("runTest")
        case.setUp()
        try:
            case.config = deepcopy(config)
            case.config["github"]["repository_id"] = ready.REPOSITORY_ID
            case.provider = ready.FakeImmutableCommentProvider(case.config)
            record = case.authorization(actor_id=signer)
            if consumer != "ready":
                from agentic import lifecycle
                transition_record = lifecycle.ready_transition_record(67, ready.HEAD, record,
                    config=case.config, verify_owner_source=case.provider.verify)
                if consumer == "lifecycle-ready":
                    return transition_record
                if consumer == "lifecycle-revalidate":
                    return lifecycle.revalidate_ready_transition_record(transition_record, 67, ready.HEAD,
                        config=case.config, verify_owner_source=case.provider.verify)
                return lifecycle.transition("PR_DRAFT", "PR_READY", case.ready_facts(transition_record))
            replacement = case.authorization(actor_id=1002) if replace_on_read else {}
            with replacing_during_policy_read(replace_on_read, lambda: record.update(replacement)):
                return authorization.verify_ready_authorization(record, case.config, pr=67,
                    head_sha=ready.HEAD, verify_owner_source=case.provider.verify,
                    **options(authorization.verify_ready_authorization))
        finally:
            case.doCleanups()
    if consumer == "jira-binding":
        from agentic.provider_identity import jira_binding_plan
        plan = jira_binding_plan(config, provider.connections(), now=provider.NOW)
        candidate = plan["candidates"][0]
        confirmation = {"format": "awf-jira-binding-confirmation-1", "decision": "BIND",
            "candidate_sha256": plan["candidate_sha256"],
            **{k: candidate[k] for k in ("cloud_id", "site", "project_id", "project_key", "account_id")},
            "owner_id": signer, "confirmed_at": provider.NOW,
            "authentication_evidence": ["urn:awf:fixture:owner-auth"]}
        with replacing_during_policy_read(replace_on_read, lambda: confirmation.update(owner_id=1002)):
            return jira_binding_plan(config, provider.connections(), confirmation, now=provider.NOW,
                                     **options(jira_binding_plan))
    if consumer == "intake":
        value = intake_fixture.config()
        value["github"]["expected_actor_id"] = config["github"].get("expected_actor_id")
        if "expected_actor_id" not in config["github"]:
            value["github"].pop("expected_actor_id")
        value["merge_gate"] = deepcopy(config["merge_gate"])
        value["jira"]["enabled"] = False
        state = {}
        current = intake_assignment.assign_intake(value, intake_fixture.ticket(), state, None,
            now=intake_fixture.NOW, controller_actor_id="fixture-controller")
        intake_assignment.inherit_pr_assignment(state, "AWF-100", 48, "Fixture PR body")
        reason = "Synthetic owner decision"
        source = intake_fixture.owner_source("AWF-100", current["assignment_id"], 3, 3, reason,
                                            actor_id=signer)
        with replacing_during_policy_read(replace_on_read, lambda: source.update(actor_id=1002)):
            return intake_assignment.change_assignment(value, "AWF-100", state, None,
                requested_tier=3, requested_cap=3, owner_source=source,
                verify_owner_source=intake_fixture.verify_owner_source, reason=reason,
                now=intake_fixture.NOW, controller_actor_id="fixture-controller",
                **options(intake_assignment.change_assignment))
    raise AssertionError(consumer)


def overcap_owner_scenario(setting, root=None, signer=1001, *, identity="numeric", overlap=True, append_automation_during_policy=False,
                           replace_automation_during_recheck=False):
    """Retain the exact authenticated parent approval list, without admitting a round."""
    import test_review_tiers_195_overcap as fixture
    from agentic import gates
    fixture.Tier3OvercapGateTests.setUpClass()
    case = fixture.Tier3OvercapGateTests("runTest")
    case.setUp()
    try:
        case.config["github"]["expected_actor_id"] = 1002 if identity == "numeric" else None
        if identity == "absent":
            case.config["github"].pop("expected_actor_id")
        case.config["merge_gate"].update(trusted_owner_ids=[1001, 1002] if overlap else [1001],
            shared_owner_automation_actor_id=1002 if setting == "set" else None)
        if setting == "absent":
            case.config["merge_gate"].pop("shared_owner_automation_actor_id")
        fixture.bind_review_admission(case.bundle)
        registry = fixture.bind_production_posting_fixture(case.config, case.bundle)
        approval = case.authenticated_approval(registry, actor_id=signer,
            body=fixture.GATE_CANONICAL.replace("1001", str(signer)))
        expected = deepcopy([approval])
        options = ({"accepted_policy_root": root}
                   if "accepted_policy_root" in inspect.signature(gates.verified_overcap_approvals).parameters else {})
        approvals = [approval]
        if append_automation_during_policy or replace_automation_during_recheck:
            from agentic import shared_owner_policy
            automation = case.authenticated_approval(registry, actor_id=1002,
                body=fixture.GATE_CANONICAL.replace("1001", "1002"))
            read_policy = shared_owner_policy._policy_blob
            reads = 0
            def append_during_read(*args, **kwargs):
                nonlocal reads
                reads += 1
                if append_automation_during_policy and len(approvals) == 1:
                    approvals.append(automation)
                if replace_automation_during_recheck and reads == 2:
                    approval.clear()
                    approval.update(automation)
                return read_policy(*args, **kwargs)
            with patch.object(shared_owner_policy, "_policy_blob", side_effect=append_during_read):
                result = gates.verified_overcap_approvals(case.config, approvals,
                    case.bundle["candidate"], {run["run_id"]: run for run in case.bundle["runs"]},
                    registry, **options)
        else:
            result = gates.verified_overcap_approvals(case.config, approvals, case.bundle["candidate"],
                {run["run_id"]: run for run in case.bundle["runs"]}, registry, **options)
        return {"actual": result, "expected": expected}
    finally:
        case.doCleanups()


def empty_gate_scenario(consumer, config, root=None):
    from agentic import gates
    function = (gates.verified_owner_records if consumer == "owner-records"
                else gates.verified_overcap_approvals)
    options = ({"accepted_policy_root": root}
               if "accepted_policy_root" in inspect.signature(function).parameters else {})
    if consumer == "owner-records":
        return function(config, Contracts(ROOT / ".agentic/schemas"),
            {"candidate": {"head_sha": ready.HEAD}}, {}, {}, [], [], provider.NOW, **options)
    return function(config, [], {}, {}, {}, **options)


def retained_intake_scenario(setting, root=None, *, followup=False, signer=1001,
                             replace_on_read=None):
    """Use a genuinely generated ordinary audit before changing owner context."""
    config = intake_fixture.config()
    config["jira"]["enabled"] = False
    config["merge_gate"]["shared_owner_automation_actor_id"] = None
    if setting == "absent":
        config["merge_gate"].pop("shared_owner_automation_actor_id")
    state = {}
    current = intake_assignment.assign_intake(config, intake_fixture.ticket(), state, None,
        now=intake_fixture.NOW, controller_actor_id="fixture-controller")
    reason = "Synthetic owner decision"
    source = intake_fixture.owner_source("AWF-100", current["assignment_id"], 3, 3, reason, actor_id=signer)
    prior = intake_assignment.change_assignment(config, "AWF-100", state, None,
        requested_tier=3, requested_cap=3, owner_source=source,
        verify_owner_source=intake_fixture.verify_owner_source, reason=reason,
        now=intake_fixture.NOW, controller_actor_id="fixture-controller")
    config["github"]["expected_actor_id"] = 1002
    function = intake_assignment.change_assignment if followup else intake_assignment.load_runtime_assignments
    options = ({"accepted_policy_root": root}
               if "accepted_policy_root" in inspect.signature(function).parameters else {})
    if not followup:
        with replacing_during_policy_read(replace_on_read,
                lambda: state["tier_assignments"]["AWF-100"].update(round_cap=1)):
            actual = function(config, state, **options)
        return {"actual": actual, "expected": {"AWF-100": prior}}
    reason = "Second synthetic owner decision"
    source = intake_fixture.owner_source("AWF-100", prior["assignment_id"], 2, 2, reason, actor_id=signer)
    source["comment_id"] = 18
    return function(config, "AWF-100", state, None, requested_tier=2, requested_cap=2,
        owner_source=source, verify_owner_source=intake_fixture.verify_owner_source, reason=reason,
        now=intake_fixture.NOW, controller_actor_id="fixture-controller", **options)


class DistinctOwnerCompatibility(unittest.TestCase):
    def setUp(self):
        self.enterContext(shipped_routing())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def config(self, setting):
        value = provider.config()
        value["merge_gate"].update(trusted_owner_ids=[1001, 1002],
                                    shared_owner_automation_actor_id=None)
        if setting == "absent":
            value["merge_gate"].pop("shared_owner_automation_actor_id")
        return value

    def restored(self, consumer):
        # Canonical hashes cover the complete deterministic parent results.
        expected = PARENT_DISTINCT_RESULTS[consumer]
        for setting in ("null", "absent"):
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    result = distinct_owner_scenario(consumer, self.config(setting), root)
                    self.assertEqual(expected[setting], sha256(canonical(result)))

    def test_ready_distinct_owner_exact_parent_result(self):
        self.restored("ready")

    def test_jira_binding_distinct_owner_exact_parent_result(self):
        self.restored("jira-binding")

    def test_intake_distinct_owner_exact_parent_result(self):
        self.restored("intake")

    def test_lifecycle_ready_exact_parent_result(self):
        self.restored("lifecycle-ready")

    def test_lifecycle_revalidation_exact_parent_result(self):
        self.restored("lifecycle-revalidate")

    def test_lifecycle_transition_exact_parent_result(self):
        self.restored("lifecycle-transition")

    def test_overcap_collector_preserves_exact_parent_approval_list(self):
        for setting in ("null", "absent"):
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    value = overcap_owner_scenario(setting, root)
                    self.assertEqual(value["expected"], value["actual"])

    def test_untargeted_collections_retain_head_validation_order(self):
        from agentic import gates
        config = self.config("null")
        config["merge_gate"]["shared_owner_automation_actor_id"] = 1002
        with self.assertRaises(ValidationError) as raised:
            gates.verified_overcap_approvals(config, 5, {}, {}, {})
        self.assertEqual("The configured automation actor must not be a trusted owner",
                         str(raised.exception))
        for identity in ("null", "absent"):
            config = self.config("null")
            config["github"]["expected_actor_id"] = None
            if identity == "absent":
                config["github"].pop("expected_actor_id")
            with self.subTest(identity=identity):
                with self.assertRaises(TypeError) as raised:
                    gates.verified_overcap_approvals(config, 5, {}, {}, {})
                self.assertEqual("'int' object is not iterable", str(raised.exception))

    def test_overcap_automation_appended_during_policy_read_is_refused(self):
        # The initial list is a valid distinct-owner approval. The immutable
        # Git read remains real; only concurrent input mutation is injected.
        for setting in ("null", "absent"):
            root = self.policy_repository(self.config(setting), "mutating-" + setting)
            with self.subTest(setting=setting):
                with self.assertRaises(ValidationError) as raised:
                    overcap_owner_scenario(setting, root, append_automation_during_policy=True)
                self.assertEqual("The configured automation actor must not be a trusted owner",
                                 str(raised.exception))

    def test_overcap_automation_replacement_during_recheck_is_refused(self):
        for setting in ("null", "absent"):
            root = self.policy_repository(self.config(setting), "replaced-" + setting)
            with self.subTest(setting=setting):
                with self.assertRaises(ValidationError) as raised:
                    overcap_owner_scenario(setting, root, replace_automation_during_recheck=True)
                self.assertEqual("The configured automation actor must not be a trusted owner",
                                 str(raised.exception))

    def test_overcap_automation_signer_retains_head_refusal(self):
        for setting in ("null", "absent"):
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    with self.assertRaises(ValidationError) as raised:
                        overcap_owner_scenario(setting, root, 1002)
                    self.assertEqual("The configured automation actor must not be a trusted owner",
                                     str(raised.exception))

    def empty_collection(self, consumer, expected):
        for setting in ("null", "absent"):
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    self.assertEqual(expected, empty_gate_scenario(consumer, self.config(setting), root))

    def test_empty_owner_record_collection_exact_parent_result(self):
        self.empty_collection("owner-records", ([], None))

    def test_empty_overcap_collection_exact_parent_result(self):
        self.empty_collection("overcap", [])

    def test_retained_intake_reload_exact_parent_result(self):
        for setting in ("null", "absent"):
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    value = retained_intake_scenario(setting, root)
                    self.assertEqual(value["expected"], value["actual"])

    def test_intake_followup_exact_parent_result(self):
        for setting in ("null", "absent"):
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    value = retained_intake_scenario(setting, root, followup=True)
                    self.assertEqual(PARENT_INTAKE_FOLLOWUP, sha256(canonical(value)))

    def test_retained_intake_automation_refusals_remain(self):
        for setting in ("null", "absent"):
            for followup in (False, True):
                with self.subTest(setting=setting, followup=followup):
                    with self.assertRaises(ValidationError) as raised:
                        retained_intake_scenario(setting, self.root, followup=followup, signer=1002)
                    self.assertEqual("The configured automation actor must not be a trusted owner",
                                     str(raised.exception))

    def policy_repository(self, config, name, *, observed=True, origin=True):
        """Build immutable policy bytes in disposable Git metadata only."""
        project = self.root / name
        project.mkdir()
        environment = dict(os.environ, GIT_AUTHOR_NAME="Fixture owner",
            GIT_AUTHOR_EMAIL="owner@example.invalid", GIT_COMMITTER_NAME="Fixture owner",
            GIT_COMMITTER_EMAIL="owner@example.invalid")
        def git(*arguments, raw=None):
            return subprocess.run(["git", "-C", str(project), *arguments], input=raw,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=environment).stdout.strip()
        git("init", "-q")
        if origin:
            git("config", "remote.origin.url", "https://github.com/fixture/example.git")
        if observed:
            config_blob = git("hash-object", "-w", "--stdin", raw=canonical(config))
            workflow_blob = git("hash-object", "-w", "--stdin", raw=canonical(definition()))
            subtree = git("mktree", raw=b"100644 blob " + config_blob + b"\tPROJECT_CONFIG.yaml\n"
                          + b"100644 blob " + workflow_blob + b"\tworkflow.yaml\n")
            tree = git("mktree", raw=b"040000 tree " + subtree + b"\t.agentic\n")
            commit = git("commit-tree", tree.decode(), raw=b"Synthetic accepted policy\n")
            git("update-ref", "refs/remotes/origin/main", commit.decode())
            git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        return project

    def test_real_git_roots_preserve_exact_parent_results(self):
        for setting in ("null", "absent"):
            config = self.config(setting)
            for observed, origin in ((False, False), (True, False), (True, True)):
                root = self.policy_repository(config, f"{setting}-{observed}-{origin}",
                                              observed=observed, origin=origin)
                for consumer in ("ready", "jira-binding", "intake"):
                    with self.subTest(setting=setting, observed=observed, origin=origin,
                                      consumer=consumer):
                        result = distinct_owner_scenario(consumer, config, root)
                        self.assertEqual(PARENT_DISTINCT_RESULTS[consumer][setting], sha256(canonical(result)))
                        with self.assertRaises(ValidationError) as raised:
                            distinct_owner_scenario(consumer, config, root, 1002)
                        self.assertEqual("The configured automation actor must not be a trusted owner",
                                         str(raised.exception))
                value = overcap_owner_scenario(setting, root)
                self.assertEqual(value["expected"], value["actual"])
                self.assertEqual(([], None), empty_gate_scenario("owner-records", config, root))
                self.assertEqual([], empty_gate_scenario("overcap", config, root))
                retained = retained_intake_scenario(setting, root)
                self.assertEqual(retained["expected"], retained["actual"])

    def test_automation_signer_retains_head_exact_refusal(self):
        for consumer in ("ready", "jira-binding", "intake"):
            for setting in ("null", "absent"):
                for root in (None, self.root):
                    with self.subTest(consumer=consumer, setting=setting, rooted=root is not None):
                        with self.assertRaises(ValidationError) as raised:
                            distinct_owner_scenario(consumer, self.config(setting), root, 1002)
                        self.assertEqual("The configured automation actor must not be a trusted owner",
                                         str(raised.exception))

    def test_lifecycle_automation_signer_retains_head_refusals(self):
        for setting in ("null", "absent"):
            config = self.config(setting)
            with self.subTest(setting=setting):
                record = distinct_owner_scenario("lifecycle-ready", config, signer=1002)
                self.assertEqual(("REFUSED", "AUTHORIZATION_RECORD_INVALID"),
                                 (record["decision"], record["reason"]))
                for consumer, message in (
                    ("lifecycle-revalidate", "PR ready transition retains a refusal, not authorization"),
                    ("lifecycle-transition", "PR ready refused: explicit owner/manager authorization record is required; retain the refusal record"),
                ):
                    with self.assertRaises(ValidationError) as raised:
                        distinct_owner_scenario(consumer, config, signer=1002)
                    self.assertEqual(message, str(raised.exception))

    def test_hidden_accepted_shared_policy_still_refuses_distinct_signer(self):
        from test_awf307_provider_owner_policy import ProviderPolicyFixture
        case = ProviderPolicyFixture("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        for consumer in ("ready", "jira-binding", "intake"):
            for setting in ("null", "absent"):
                with self.subTest(consumer=consumer, setting=setting), case.policy_reads():
                    with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                        distinct_owner_scenario(consumer, self.config(setting), case.project)
        for setting in ("null", "absent"):
            with self.subTest(consumer="overcap", setting=setting), case.policy_reads():
                with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                    overcap_owner_scenario(setting, case.project)
            for consumer in ("owner-records", "overcap"):
                with self.subTest(consumer=consumer, setting=setting), case.policy_reads():
                    with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                        empty_gate_scenario(consumer, self.config(setting), case.project)
            for followup in (False, True):
                with self.subTest(consumer="retained-intake", followup=followup), case.policy_reads():
                    with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                        retained_intake_scenario(setting, case.project, followup=followup)


    def test_record_replaced_during_either_policy_read_is_refused(self):
        """Ready, Jira binding, intake, reload and streams drop replaced records."""
        for setting in ("null", "absent"):
            config = self.config(setting)
            root = self.policy_repository(config, "windows-" + setting)
            for number in (1, 2):
                for consumer in ("ready", "jira-binding", "intake"):
                    with self.subTest(setting=setting, read=number, consumer=consumer):
                        with self.assertRaises(ValidationError) as raised:
                            distinct_owner_scenario(consumer, config, root, replace_on_read=number)
                        self.assertEqual(SEPARATION, str(raised.exception))
            with self.subTest(setting=setting, consumer="intake-reload"):
                with self.assertRaises(ValidationError) as raised:
                    retained_intake_scenario(setting, root, replace_on_read=1)
                self.assertEqual(SEPARATION, str(raised.exception))
            for moment in ("read", "verifier"):
                with self.subTest(setting=setting, consumer="streams", moment=moment):
                    with self.assertRaises(ValidationError) as raised:
                        stream_owner_scenario(config, root, replace_during=moment)
                    self.assertEqual(SEPARATION, str(raised.exception))

    def roots(self, config, name):
        yield "rootless", None
        yield "empty", self.root
        for observed, origin in ((False, False), (True, False), (True, True)):
            yield f"git-{observed}-{origin}", self.policy_repository(
                config, f"{name}-{observed}-{origin}", observed=observed, origin=origin)

    def test_streams_distinct_owner_exact_parent_result(self):
        for setting in ("null", "absent"):
            config = self.config(setting)
            for label, root in self.roots(config, "streams-" + setting):
                for retained in (False, True):
                    with self.subTest(setting=setting, root=label, retained=retained):
                        result = stream_owner_scenario(config, root, retained=retained)
                        self.assertEqual(PARENT_STREAM_RESULTS[retained], sha256(canonical(result)))

    def test_untrusted_signers_exact_parent_results(self):
        # A removed owner's retained move stays readable; a new one refuses.
        # Over-cap collection keeps its stricter existing trusted-owner refusal.
        for setting in ("null", "absent"):
            config = self.config(setting)
            for root in (None, self.root):
                with self.subTest(setting=setting, rooted=root is not None):
                    result = stream_owner_scenario(config, root, 1003, retained=True)
                    self.assertEqual(PARENT_FORMER_OWNER_STREAM, sha256(canonical(result)))
                    for message, call in (
                            ("Ready authorization uses an untrusted numeric owner/manager identity",
                             lambda: distinct_owner_scenario("ready", config, root, 1003)),
                            ("Jira binding confirmation owner is not trusted",
                             lambda: distinct_owner_scenario("jira-binding", config, root, 1003)),
                            ("Review assignment changes require an authenticated trusted owner",
                             lambda: distinct_owner_scenario("intake", config, root, 1003)),
                            ("Owner stream reassignment requires an authenticated trusted owner",
                             lambda: stream_owner_scenario(config, root, 1003)),
                            ("Tier 3 over-cap approval lacks a matching authenticated provider observation",
                             lambda: overcap_owner_scenario(setting, root, 1003))):
                        with self.assertRaises(ValidationError) as raised:
                            call()
                        self.assertEqual(message, str(raised.exception))

    def test_streams_automation_signer_retains_head_refusal(self):
        for setting in ("null", "absent"):
            config = self.config(setting)
            for label, root in self.roots(config, "stream-automation-" + setting):
                for retained in (False, True):
                    with self.subTest(setting=setting, root=label, retained=retained):
                        with self.assertRaises(ValidationError) as raised:
                            stream_owner_scenario(config, root, 1002, retained=retained)
                        self.assertEqual(SEPARATION, str(raised.exception))

    def test_review_and_queue_projections_exact_parent_results(self):
        # Removal and instruction accept the automation account too, as the
        # parent did. No other consumer of the two projections does.
        for setting in ("null", "absent"):
            config = self.config(setting)
            for label, root in self.roots(config, "projections-" + setting):
                for consumer, signer, expected in PARENT_PROJECTION_RESULTS:
                    with self.subTest(setting=setting, root=label, consumer=consumer, signer=signer):
                        result = projection_scenario(consumer, config, root, signer)
                        self.assertEqual(expected[setting], sha256(canonical(result)))
                with self.subTest(setting=setting, root=label, consumer="queue-accepted"):
                    with self.assertRaises(ValidationError) as raised:
                        projection_scenario("queue-accepted", config, root)
                    self.assertEqual(SEPARATION, str(raised.exception))

    def test_review_removal_retains_only_the_authenticated_record(self):
        config = self.config("null")
        root = self.policy_repository(config, "removal-window")
        for signer in (1001, 1002):
            with self.subTest(signer=signer):
                result = projection_scenario("review-removal", config, root, signer,
                                             replace_on_read=True)
                self.assertEqual({"reviewers": ["a", "b"], "entries": 1, "replaced": True,
                                  "authenticated_record_retained": True}, result)

    def test_projections_still_refuse_untrusted_signers(self):
        config = self.config("null")
        for consumer, message in PARENT_UNTRUSTED_REFUSALS.items():
            with self.subTest(consumer=consumer):
                with self.assertRaises(ValidationError) as raised:
                    projection_scenario(consumer, config, signer=1003)
                self.assertEqual(message, str(raised.exception))

    def test_hidden_accepted_shared_policy_refuses_projections_and_streams(self):
        from test_awf307_provider_owner_policy import ProviderPolicyFixture
        case = ProviderPolicyFixture("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        for setting in ("null", "absent"):
            for consumer in ("review-authority", "review-binding", "queue-authority",
                             "queue-projection"):
                with self.subTest(consumer=consumer, setting=setting), case.policy_reads():
                    with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                        projection_scenario(consumer, self.config(setting), case.project)
            for retained in (False, True):
                with self.subTest(consumer="streams", setting=setting, retained=retained), \
                        case.policy_reads():
                    with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                        stream_owner_scenario(self.config(setting), case.project, retained=retained)

    def test_configuration_validation_still_refuses_the_overlap(self):
        for setting in ("null", "absent"):
            with self.subTest(setting=setting), self.assertRaises(ValidationError) as raised:
                policy.validate_config(self.config(setting), definition(),
                                       Contracts(ROOT / ".agentic/schemas"))
            self.assertEqual(SEPARATION, str(raised.exception))


PARENT_STREAM_RESULTS = dict.fromkeys((False, True),
    "934b786230df96b82d27f1029d488f24ed68c6328432eeeb70ca8eb5dd8e87d3")

PARENT_FORMER_OWNER_STREAM = (
    "b5530dbce3e83eba4899fb512b628a8ba6f1ada20df2d2f40d304cbc3d226b13")

PARENT_PROJECTION_RESULTS = (
    ("review-authority", 1001, dict.fromkeys(("null", "absent"),
        "d3e326c1af0d522f72b7a7a45485118bf682db1335607d347c7be3b4ff2b06cf")),
    ("review-binding", 1001, {
        "null": "2a3462f69d2e90d39bdc0de8e74f5a3d2c90f43c99c07025a4f93221f91f66d5",
        "absent": "804386dd5e688be901c16753d842fda4615d6c07469e6346c813044a402f6292"}),
    ("review-removal", 1001, dict.fromkeys(("null", "absent"),
        "34a19f79c441347b35509e60f9856b6d4ea13be210e1dcdb708f76dfbca898e7")),
    ("review-removal", 1002, dict.fromkeys(("null", "absent"),
        "34a19f79c441347b35509e60f9856b6d4ea13be210e1dcdb708f76dfbca898e7")),
    ("queue-authority", 1001, dict.fromkeys(("null", "absent"),
        "bce8e3f6d63d7c04ba0d9ef6e6bb2c93a60c3598381458fe2aca513040c6903e")),
    ("queue-projection", 1001, dict.fromkeys(("null", "absent"),
        "a83aad8ccd52c6968f98e920b287e1c80100b529076e09427887ae3e4b8be17f")),
    ("queue-instruction", 1001, dict.fromkeys(("null", "absent"),
        "dd49062b3eb0cefca680646c02152b38aee2dbf5c20c11f23883a1161c7e9e3e")),
    ("queue-instruction", 1002, dict.fromkeys(("null", "absent"),
        "d415144f726b208bde28e2916689c637225275963a53a42c3a60647f0a359ada")),
)

PARENT_UNTRUSTED_REFUSALS = {
    "review-removal": "Reviewer removal actor is not a configured trusted owner",
    "queue-instruction": "owner instruction actor is not a configured trusted owner"}

PARENT_INTAKE_FOLLOWUP = "8fb137c310ca3e3ec0c1b1be452e0e19b79832d9509a7fa3ef00f5a6dfc882b4"

PARENT_DISTINCT_RESULTS = {
    'lifecycle-ready': {'null': '9c70cb34965a108272a1d09adbe6607c408c2d0eca2e342c6c03131afd5da703', 'absent': '35eedc52bbe28b7865de2164fc6226482a78cc18df92d153f8e02f3883619971'},
    'lifecycle-revalidate': {'null': 'b5bea41b6c623f7c09f1bf24dcae58ebab3c0cdd90ad966bc43a45b44867e12b', 'absent': 'b5bea41b6c623f7c09f1bf24dcae58ebab3c0cdd90ad966bc43a45b44867e12b'},
    'lifecycle-transition': {'null': '034310671cca84ff0b00fca3e9e6ae4bfd75df088638bffb77e1790d29362f58', 'absent': '034310671cca84ff0b00fca3e9e6ae4bfd75df088638bffb77e1790d29362f58'},
    "ready": {"null": "07d7deff3b3ee27393aa99cac06c07fc20b256653bbc9fc91df71c7954d6df4c",
              "absent": "aec3dce05ab20779378643d6f83db6daf41805ceb6205b456daee8c0ca9f1e0d"},
    "jira-binding": dict.fromkeys(("null", "absent"),
        "4fcf2c9c92b8b20d3cadcd2cce474d2d04d1127ae6fd1a7a73a9faeac788eac8"),
    "intake": dict.fromkeys(("null", "absent"),
        "83d23d375704f2f91c7501e0dc4e02a18e0ce8677fa6e60527bbc1eab297eef1"),
}


if __name__ == "__main__":
    unittest.main()
