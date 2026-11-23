"""Excluded owner paths must not inherit the enrolled shared-account exception."""
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401
from test_awf307_provider_owner_policy import ProviderPolicyFixture
from agentic import ValidationError, provenance
from agentic.canonical import fingerprint, sha256
from agentic.provider_identity import github_identity_preflight
from agentic import review_policy, review_tiers


SEPARATION = "^The configured automation actor must not be a trusted owner$"


class RoundRefusalTests(ProviderPolicyFixture):
    def approval(self, actor):
        return {"channel": "github_pr_comment", "comment_id": 91,
                "repository_id": 101, "pr_number": 7, "actor_id": actor,
                "created_at": "2026-10-03T09:59:00Z",
                "updated_at": "2026-10-03T09:59:00Z",
                "raw_body": (f"AWF-OVERCAP-APPROVAL: round 4 of cap 3, approver {actor}, "
                             "ruling 2026-10-03T09:58:00Z")}

    def authorize(self, actor, consumer):
        # Baseline supports no accepted identity context. Run the actual old
        # call as well, so RED means shared acceptance, not an unknown keyword.
        import inspect
        kwargs = {"trusted_owner_ids": [1001, 1002],
                  "round_started_at": "2026-10-03T10:00:00Z",
                  "repository_id": 101, "pr_number": 7}
        if "accepted_policy_root" in inspect.signature(consumer).parameters:
            kwargs["accepted_policy_root"] = self.project
        if consumer is review_policy.authorize_tier3_round:
            return consumer(4, 3, pr_comments=[self.approval(actor)], **kwargs)
        return consumer(3, 4, overcap_approvals=[self.approval(actor)], **kwargs)

    def test_authorize_tier3_round_refuses_shared_with_distinct_control(self):
        with self.policy_reads():
            self.assertTrue(self.authorize(1001, review_policy.authorize_tier3_round))
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                self.authorize(1002, review_policy.authorize_tier3_round)

    def test_validate_round_refuses_shared_with_distinct_control(self):
        with self.policy_reads():
            self.assertTrue(self.authorize(1001, review_tiers.validate_round))
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                self.authorize(1002, review_tiers.validate_round)


class ProvenanceRefusalTests(ProviderPolicyFixture):
    def projection(self):
        payload = {"config_sha256": sha256(self.blobs[".agentic/PROJECT_CONFIG.yaml"]),
                   "trusted_owner_ids": self.accepted["merge_gate"]["trusted_owner_ids"],
                   "expected_actor_id": self.accepted["github"]["expected_actor_id"]}
        return {**payload, "binding_sha256": fingerprint("provenance_owner_config", payload)}

    def record(self, actor):
        return provenance.record_owner_override({}, who=actor, when="2026-10-03T10:00:00Z",
            why="Fixture owner decision", pr=7, covered_shas=["a" * 40],
            trusted_owner_ids=[actor])

    def verify(self, actor, projection=None):
        return provenance.verify_owner_override(self.record(actor), pr=7,
            reviewed_owner_config=self.projection() if projection is None else projection,
            accepted_policy_root=self.project)

    def enrolled_control(self):
        result = github_identity_preflight(self.config, self.observation,
                                          accepted_policy_root=self.project)
        self.assertEqual("PASS", result["status"])
        self.assertIs(True, result["shared_owner_automation_account"])

    def test_override_refuses_shared_account_with_accepted_opt_out(self):
        with self.policy_reads():
            self.enrolled_control()
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                self.verify(1002)

    def test_distinct_owner_verifies_with_identity_from_accepted_bytes(self):
        with self.policy_reads():
            self.enrolled_control()
            self.assertEqual(self.record(1001), self.verify(1001))

    def test_automation_signer_retains_separation_message_with_narrowed_owners(self):
        projection = self.projection()
        projection["trusted_owner_ids"] = [1001]
        projection["binding_sha256"] = fingerprint("provenance_owner_config",
            {key: value for key, value in projection.items() if key != "binding_sha256"})
        with self.policy_reads():
            self.assertEqual(self.record(1001), self.verify(1001, projection))
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                self.verify(1002, projection)

    def test_missing_identity_fails_closed_and_old_record_remains_renderable(self):
        projection = self.projection()
        del projection["expected_actor_id"]
        projection["binding_sha256"] = fingerprint("provenance_owner_config",
            {key: value for key, value in projection.items() if key != "binding_sha256"})
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "missing expected_actor_id"):
            self.verify(1001, projection)
        self.assertIn("Who: 1001", provenance.render_owner_override(self.record(1001)))

    def test_resealed_identity_mismatch_with_accepted_configuration_refuses(self):
        projection = self.projection()
        projection["expected_actor_id"] = 1003
        projection["binding_sha256"] = fingerprint("provenance_owner_config",
            {key: value for key, value in projection.items() if key != "binding_sha256"})
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "differs from accepted"):
            self.verify(1002, projection)

    def test_supplied_and_working_configuration_cannot_replace_accepted_identity(self):
        projection = self.projection()
        self.accepted["github"]["expected_actor_id"] = 1003
        self.accepted["merge_gate"]["shared_owner_automation_actor_id"] = None
        from agentic.canonical import canonical
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "differs from accepted"):
            self.verify(1001, projection)

    def test_feature_branch_identity_cannot_establish_authority(self):
        self.ref = "refs/remotes/origin/feature"
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "default-branch identity differs"):
            self.verify(1001)

    def test_provider_verifier_refuses_shared_and_preserves_distinct_owner(self):
        for actor in (1001, 1002):
            with self.subTest(actor=actor), self.policy_reads():
                record = self.record(actor)
                body = provenance.render_owner_override(record)
                observation = {"version": 1, "repository_id": 101, "pr": 7,
                    **{key: value for key, value in self.projection().items()
                       if key != "binding_sha256"},
                    "comments": [{"kind": "issue", "id": 91, "actor_id": actor,
                        "url": "https://github.com/fixture/example/issues/7#issuecomment-91",
                        "body": body, "body_sha256": sha256(body.encode())}]}
                call = lambda: provenance.verify_provider_owner_override(record, observation,
                    repository_id=101, accepted_policy_root=self.project)
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, SEPARATION):
                        call()
                else:
                    self.assertEqual(91, call()["provider_comment_id"])


class CapPlanRefusalTests(ProviderPolicyFixture):
    def test_cap_plan_refuses_shared_with_distinct_control(self):
        import inspect
        kwargs = ({"accepted_policy_root": self.project}
                  if "accepted_policy_root" in inspect.signature(review_policy.cap_disposition_plan).parameters else {})
        for decision in review_policy.CAP_DECISIONS:
            for actor in (1001, 1002):
                record = {"decision": decision, "open_finding_ids": [],
                          "owner_source": {"actor_id": actor}, "successor_ticket": "EX-99"}
                with self.subTest(decision=decision, actor=actor), self.policy_reads():
                    if actor == 1002:
                        with self.assertRaisesRegex(ValidationError, SEPARATION):
                            review_policy.cap_disposition_plan(self.config, record, 3, 0, [], **kwargs)
                    else:
                        self.assertEqual(review_policy.CAP_EVENTS[decision], review_policy.cap_disposition_plan(
                            self.config, record, 3, 0, [], **kwargs)["event"])


class MergeTrainRefusalTests(ProviderPolicyFixture):
    def test_admit_source_refuses_shared_with_distinct_control(self):
        import inspect
        from test_merge_train import MergeTrainTests, merge_train, NOW
        fixture = MergeTrainTests("test_fresh_exact_head_owner_review_admits_required_source_only")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        module = merge_train()
        source = fixture._source()
        kwargs = ({"accepted_policy_root": self.project}
                  if "accepted_policy_root" in inspect.signature(module.admit_source).parameters else {})
        for actor in (1001, 1002):
            observer_id = f"fixture-excluded-{actor}"
            request, registration = fixture._registration(source,
                fixture._evidence(source, reviewer_id=actor), observer_id=observer_id)
            fixture.observer_registry[observer_id] = registration
            def admit():
                return module.admit_source(source, "main", fixture.provider_identity,
                    observer_id, fixture.observer_registry, now=NOW,
                    trusted_owner_ids=[1001, 1002], **kwargs)
            with self.subTest(actor=actor), self.policy_reads():
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, SEPARATION):
                        admit()
                else:
                    self.assertEqual("ADMITTED", admit()["status"])


class MergeTrainRevalidationTests(ProviderPolicyFixture):
    def prove(self, consumer):
        from agentic import merge_train as module
        from agentic.canonical import canonical
        from test_merge_train import MergeTrainTests, NOW
        for actor in (1001, 1002):
            fixture = MergeTrainTests("test_fresh_exact_head_owner_review_admits_required_source_only")
            fixture.setUp()
            self.addCleanup(fixture.tearDown)
            source = fixture._source()
            evidence = fixture._evidence(source, reviewer_id=actor)
            observer_id = f"retained-{actor}"
            _, registration = fixture._registration(source, evidence, observer_id=observer_id)
            registry = {observer_id: registration}
            self.accepted["github"]["expected_actor_id"] = 1003
            self.accepted["merge_gate"]["shared_owner_automation_actor_id"] = None
            self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
            kwargs = dict(now=NOW, trusted_owner_ids=[1001, 1002], accepted_policy_root=self.project)
            with self.policy_reads():
                admitted = module.admit_source(source, "main", fixture.provider_identity,
                    observer_id, registry, **kwargs)
                generation = module.freeze_generation("retained", "main", fixture.target,
                    [admitted], 1, fixture.provider_identity, registry, **kwargs)
            self.accepted["github"]["expected_actor_id"] = 1002
            self.accepted["merge_gate"]["shared_owner_automation_actor_id"] = 1002
            self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
            def call():
                if consumer == "_validate_admission_evidence":
                    return module._validate_admission_evidence(source, evidence, [1001, 1002],
                        now=NOW, max_age_seconds=300, accepted_policy_root=self.project)
                if consumer == "_validate_admitted_source":
                    return module._validate_admitted_source(admitted, fixture.provider_identity,
                        registry, max_age_seconds=300, **kwargs)
                if consumer == "order_sources":
                    return module.order_sources([admitted], fixture.provider_identity, registry, **kwargs)
                if consumer == "freeze_generation":
                    return module.freeze_generation("retained", "main", fixture.target,
                        [admitted], 1, fixture.provider_identity, registry, **kwargs)
                return module.generation_status(generation, fixture.target, [admitted],
                    fixture.provider_identity, registry, **kwargs)
            with self.subTest(actor=actor), self.policy_reads():
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, SEPARATION):
                        call()
                else:
                    self.assertTrue(call())

    def test_validate_admission_evidence_refuses_shared_with_distinct_control(self):
        self.prove("_validate_admission_evidence")

    def test_validate_admitted_source_refuses_shared_with_distinct_control(self):
        self.prove("_validate_admitted_source")

    def test_order_sources_refuses_shared_with_distinct_control(self):
        self.prove("order_sources")

    def test_freeze_generation_refuses_shared_with_distinct_control(self):
        self.prove("freeze_generation")

    def test_generation_status_refuses_shared_with_distinct_control(self):
        self.prove("generation_status")


class RootlessIdentityRefusalTests(ProviderPolicyFixture):
    def ordinary(self):
        config = deepcopy(self.config)
        config["merge_gate"]["trusted_owner_ids"] = [1001]
        config["merge_gate"]["shared_owner_automation_actor_id"] = None
        return config

    def test_host_preflight_warns_shared_and_preserves_distinct_configuration(self):
        from agentic.host_preflight import _provider_identity_rows
        with self.policy_reads():
            shared = _provider_identity_rows(self.project, self.config, self.observation, None)[0]
            ordinary = _provider_identity_rows(self.project, self.ordinary(), self.observation, None)[0]
        self.assertEqual("WARN", shared["status"])
        self.assertIn("The configured automation actor must not be a trusted owner", shared["detail"])
        self.assertEqual("PASS", ordinary["status"])

    def test_publication_readiness_blocks_shared_and_preserves_distinct_configuration(self):
        from agentic.publication_readiness import publication_readiness
        from test_publication_readiness import publication_observation
        observation = publication_observation(now=self.observation["observed_at"])
        observation["identity"] = deepcopy(self.observation)
        with self.policy_reads():
            shared = publication_readiness(self.config, observation, now=observation["observed_at"])
            ordinary = publication_readiness(self.ordinary(), observation, now=observation["observed_at"])
        self.assertEqual("BLOCKED", shared["status"])
        self.assertIn("The configured automation actor must not be a trusted owner", shared["checks"][0]["evidence"])
        self.assertEqual("READY", ordinary["status"])
        self.assertFalse(shared["execution_authority"])

    def test_status_report_identity_refuses_shared_with_distinct_control(self):
        from agentic.status_report_store import _identity, report_binding
        for config, refused in ((self.config, True), (self.ordinary(), False)):
            request = {"binding": report_binding(config), "github_identity": self.observation,
                       "jira_connections": self.connections}
            with self.subTest(refused=refused), self.policy_reads():
                result = _identity(config, request)
            if refused:
                self.assertIn("The configured automation actor must not be a trusted owner", result)
            else:
                self.assertIsNone(result)

    def test_observe_github_identity_refuses_shared_with_distinct_control(self):
        from agentic.provider_identity import observe_github_identity
        def call(config):
            return observe_github_identity(config,
                read_actor=lambda: {"id": 1002, "login": "fixture-owner", "auth_profile": "fixture",
                                    "observed_at": self.observation["observed_at"]},
                read_repository=lambda _: {"id": 101, "name_with_owner": "fixture/example", "readable": True},
                observe_protocol=lambda: "ssh")
        with self.policy_reads():
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                call(self.config)
            self.assertEqual("PASS", call(self.ordinary())["status"])

    def test_owner_publication_resume_refuses_before_mutation_with_distinct_control(self):
        from agentic.owner_publication import resume_owner_publication
        from types import SimpleNamespace
        from unittest.mock import Mock
        heads = Mock(return_value={})
        store = SimpleNamespace(snapshot=lambda *_: {"complete": False, "operations": [],
                                                      "request": {"streams": []}}, record_heads=Mock())
        kwargs = dict(now=self.observation["observed_at"], observe_identity=lambda: self.observation,
                      observe_remote_heads=heads, create_draft_pr=Mock(), observe_draft_pr=Mock())
        with self.policy_reads():
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                resume_owner_publication(store, "fixture", self.config, **kwargs)
            heads.assert_not_called()
            store.record_heads.assert_not_called()
            result = resume_owner_publication(store, "fixture", self.ordinary(), **kwargs)
        self.assertEqual([], result["errors"])
        store.record_heads.assert_called_once()

    def test_cli_supplied_config_reports_without_authority(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from agentic.cli import main
        from agentic.canonical import canonical
        from test_reference import Fixture, NOW
        fixture = Fixture("runTest")
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gate, request, record = fixture.authorize()
        files = {}
        for name, value in (("gate", gate), ("request", request), ("record", record)):
            files[name] = self.project / (name + ".json")
            files[name].write_bytes(canonical(value))
        supplied = self.project / "config.json"
        shared = deepcopy(dict(fixture.config))
        shared["merge_gate"].update(trusted_owner_ids=[1001, 1002], shared_owner_automation_actor_id=1002)
        supplied.write_bytes(canonical(shared))
        args = ["--root", str(Path(__file__).resolve().parents[2]), "verify-authorization",
                "--config", str(supplied), "--now", NOW]
        for name, path in files.items():
            args += ["--" + name, str(path)]
        output = io.StringIO()
        with self.policy_reads(), redirect_stdout(output), redirect_stderr(output):
            code = main(args)
        self.assertNotEqual(0, code)
        self.assertIn("The configured automation actor must not be a trusted owner", output.getvalue())
        self.assertNotIn('"execution_authority": true', output.getvalue())
        supplied.write_bytes(canonical(dict(fixture.config)))
        control = io.StringIO()
        with redirect_stdout(control), redirect_stderr(control):
            self.assertEqual(0, main(args))
        self.assertIn('"record_consistent": true', control.getvalue())
        self.assertNotIn('"execution_authority": true', control.getvalue())


class NonAuthoritativeTierTests(ProviderPolicyFixture):
    def test_runtime_assignment_grants_no_owner_authority(self):
        from test_awf307_intake_owner_policy import IntakeOwnerPolicyTests
        from test_intake_assignment import owner_source
        for actor in (1001, 1002):
            fixture = IntakeOwnerPolicyTests("runTest")
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.source = owner_source("AWF-100", fixture.current["assignment_id"], 3, 3,
                                          fixture.reason, actor_id=actor)
            with fixture.policy_reads():
                record = fixture.change()
            with self.subTest(actor=actor):
                selected = review_tiers.runtime_assignment(fixture.config, ticket_key="AWF-100")
                self.assertEqual(record, selected)
                self.assertEqual("OWNER_REVIEW_REQUIRED", review_tiers.review_decision(
                    3, 3, config=fixture.config, owner_review=record["change_audit"],
                    configured_owner_ids=[1001, 1002])["status"])
                with self.assertRaisesRegex(ValidationError, "only round 4"):
                    review_tiers.validate_round(3, 5, config=fixture.config)
                mutated = deepcopy(record)
                mutated["owner_review_required"] = False
                fixture.config["_awf_runtime"]["intake_assignments"]["AWF-100"] = mutated
                with self.assertRaisesRegex(ValidationError, "digest"):
                    review_tiers.runtime_assignment(fixture.config, ticket_key="AWF-100")

    def test_review_decision_overcap_refuses_shared_with_distinct_control(self):
        fixture = RoundRefusalTests("test_validate_round_refuses_shared_with_distinct_control")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for actor in (1001, 1002):
            kwargs = dict(overcap_approvals=[fixture.approval(actor)], trusted_owner_ids=[1001, 1002],
                round_started_at="2026-10-03T10:00:00Z", repository_id=101, pr_number=7,
                accepted_policy_root=fixture.project)
            with self.subTest(actor=actor), fixture.policy_reads():
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, SEPARATION):
                        review_tiers.review_decision(3, 4, **kwargs)
                else:
                    self.assertEqual("OWNER_REVIEW_REQUIRED", review_tiers.review_decision(3, 4, **kwargs)["status"])


class ProvenanceEntryRefusalTests(ProviderPolicyFixture):
    projection = ProvenanceRefusalTests.projection
    record = ProvenanceRefusalTests.record

    def test_reviewed_owner_ids_only_projects_ids_and_cannot_verify_shared(self):
        with self.policy_reads():
            projection = self.projection()
            self.assertEqual((1001, 1002), provenance._reviewed_owner_ids(
                projection, accepted_policy_root=self.project))
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                provenance.verify_owner_override(self.record(1002), reviewed_owner_config=projection,
                    accepted_policy_root=self.project)
            self.assertEqual(1001, provenance.verify_owner_override(self.record(1001),
                reviewed_owner_config=projection, accepted_policy_root=self.project)["who"])

    def test_provider_owner_config_projects_identity_without_override_authority(self):
        observed = {key: value for key, value in self.projection().items() if key != "binding_sha256"}
        with self.policy_reads():
            projection = provenance._provider_owner_config(observed, accepted_policy_root=self.project)
            self.assertEqual(self.projection(), projection)
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                provenance.verify_owner_override(self.record(1002), reviewed_owner_config=projection,
                    accepted_policy_root=self.project)
            self.assertEqual(1001, provenance.verify_owner_override(self.record(1001),
                reviewed_owner_config=projection, accepted_policy_root=self.project)["who"])

    def test_record_override_refuses_shared_before_state_change_with_distinct_control(self):
        with self.policy_reads():
            state = {}
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                provenance.record_owner_override(state, who=1002, when="2026-10-03T10:00:00Z",
                    why="Fixture owner decision", pr=7, covered_shas=["a" * 40],
                    reviewed_owner_config=self.projection(), accepted_policy_root=self.project)
            self.assertEqual({}, state)
            self.assertEqual(1001, provenance.record_owner_override(state, who=1001,
                when="2026-10-03T10:00:00Z", why="Fixture owner decision", pr=7,
                covered_shas=["a" * 40], reviewed_owner_config=self.projection(),
                accepted_policy_root=self.project)["who"])

    def test_supersede_override_refuses_shared_before_state_change_with_distinct_control(self):
        original = self.record(1001)
        state = {"provenance_overrides": {"7": original}}
        before = deepcopy(state)
        with self.policy_reads():
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                provenance.supersede_owner_override(state, who=1002, when="2026-10-03T10:01:00Z",
                    why="Fixture extension", pr=7, covered_shas=["a" * 40, "b" * 40],
                    supersedes=original["override_sha256"], reviewed_owner_config=self.projection(),
                    accepted_policy_root=self.project)
            self.assertEqual(before, state)
            self.assertEqual(1001, provenance.supersede_owner_override(state, who=1001,
                when="2026-10-03T10:01:00Z", why="Fixture extension", pr=7,
                covered_shas=["a" * 40, "b" * 40], supersedes=original["override_sha256"],
                reviewed_owner_config=self.projection(), accepted_policy_root=self.project)["who"])

    def test_verify_override_chain_refuses_shared_with_distinct_control(self):
        with self.policy_reads():
            self.assertEqual(1001, provenance._verify_override_chain(self.record(1001), [],
                reviewed_owner_config=self.projection(), pr=7, accepted_policy_root=self.project)["who"])
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                provenance._verify_override_chain(self.record(1002), [],
                    reviewed_owner_config=self.projection(), pr=7, accepted_policy_root=self.project)

    def test_check_commit_range_refuses_shared_with_distinct_control(self):
        from test_provenance import RepositoryRangeTests
        fixture = RepositoryRangeTests("test_duplicate_writer_commits_refuse_without_an_override")
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        fixture.owner_fixture.config = deepcopy(self.config)
        projection = fixture.owner_fixture.projection()
        fixture.write("fixture.txt", "exceptional legacy source\n")
        head = fixture.commit("legacy source without writer attribution")
        for actor in (1001, 1002):
            record = provenance.record_owner_override({}, who=actor, when="2026-10-03T10:00:00Z",
                why="Fixture owner decision", pr=7, covered_shas=[head], trusted_owner_ids=[actor])
            with self.subTest(actor=actor):
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, SEPARATION):
                        provenance.check_commit_range(fixture.root, fixture.base, head, override=record,
                            pr_number=7, reviewed_owner_config=projection)
                else:
                    self.assertEqual("PASS", provenance.check_commit_range(fixture.root, fixture.base,
                        head, override=record, pr_number=7, reviewed_owner_config=projection)["status"])


class HostProjectionRefusalTests(ProviderPolicyFixture):
    record = ProvenanceRefusalTests.record

    def driver(self, actor):
        from agentic.providers.github_review_host import HostDriver
        driver = HostDriver.__new__(HostDriver)
        driver.worker = self.project
        driver.c = {"repository_id": 101, "repository": "fixture/example", "pr": 7,
                    "base_branch": "main", "trusted_owner_ids": [1001, 1002],
                    "config_hash": "f" * 64, "provenance": {"default_ref": "refs/heads/main"}}
        body = provenance.render_owner_override(self.record(actor))
        def api(suffix, **kwargs):
            if not suffix:
                return {"id": 101}
            if suffix == "branches/main":
                return {"commit": {"sha": self.commit}}
            if suffix == "pulls/7":
                return {"number": 7, "updated_at": "same", "body": "", "labels": [],
                        "head": {"sha": "c" * 40, "ref": "codex/fixture", "repo": {"id": 101}},
                        "base": {"sha": self.commit, "ref": "main", "repo": {"id": 101}}}
            if suffix == "issues/7/comments?per_page=100&page=1":
                return [{"id": 91, "body": body, "user": {"id": actor},
                         "html_url": "https://github.com/fixture/example/issues/7#issuecomment-91"}]
            return []
        driver.api = api
        return driver

    def prove(self, consumer):
        for actor in (1001, 1002):
            with self.subTest(actor=actor), self.policy_reads():
                observed = getattr(self.driver(actor), consumer)(owner_authority=True)
                self.assertEqual(1002, observed["expected_actor_id"])
                self.assertEqual(sha256(self.blobs[".agentic/PROJECT_CONFIG.yaml"]), observed["config_sha256"])
                self.assertNotIn("execution_authority", observed)
                projection = provenance._provider_owner_config(observed, accepted_policy_root=self.project)
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, SEPARATION):
                        provenance.verify_owner_override(self.record(actor), reviewed_owner_config=projection,
                            accepted_policy_root=self.project)
                else:
                    self.assertEqual(actor, provenance.verify_owner_override(self.record(actor),
                        reviewed_owner_config=projection, accepted_policy_root=self.project)["who"])
                    if consumer == "collect_provenance_observation":
                        self.assertEqual(91, provenance.verify_provider_owner_override(self.record(actor), observed,
                            accepted_policy_root=self.project)["provider_comment_id"])

    def test_collect_provenance_observation_grants_no_shared_override(self):
        self.prove("collect_provenance_observation")

    def test_collect_default_branch_observation_grants_no_shared_override(self):
        self.prove("collect_default_branch_observation")


class LifecycleRefusalTests(ProviderPolicyFixture):
    def ready_fixture(self):
        from test_awf307_authorization_owner_policy import VerifyReadyPolicyTests
        VerifyReadyPolicyTests.setUpClass()
        fixture = VerifyReadyPolicyTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def ordinary_ready(self, fixture):
        from agentic.shared_owner_policy import OWNER_EVIDENCE_FIELDS
        fixture.config["merge_gate"].update(trusted_owner_ids=[1001], shared_owner_automation_actor_id=None)
        source = fixture.ready_record["owner_source"]
        source["actor_id"] = 1001
        for key in OWNER_EVIDENCE_FIELDS:
            fixture.ready_record.pop(key, None)
        fixture.provider.comments[source["comment_id"]] = deepcopy(source)
        from agentic.authorization import ready_authorization_config_sha256
        fixture.provider.config_sha256 = ready_authorization_config_sha256(fixture.config)

    def test_ready_transition_record_refuses_shared_with_distinct_control(self):
        from agentic.lifecycle import ready_transition_record
        fixture = self.ready_fixture()
        head = fixture.candidate["head_sha"]
        with fixture.policy_reads():
            self.assertTrue(fixture.call()["record_consistent"])
            refused = ready_transition_record(7, head, fixture.ready_record, config=fixture.config,
                                             verify_owner_source=fixture.provider.verify)
        self.assertEqual(("REFUSED", "AUTHORIZATION_RECORD_INVALID"),
                         (refused["decision"], refused["reason"]))
        self.ordinary_ready(fixture)
        control = ready_transition_record(7, head, fixture.ready_record, config=fixture.config,
                                          verify_owner_source=fixture.provider.verify)
        self.assertEqual("AUTHORIZED", control["decision"])

    def test_revalidate_ready_transition_record_refuses_shared_with_distinct_control(self):
        from agentic.lifecycle import ready_transition_record, revalidate_ready_transition_record
        fixture = self.ready_fixture()
        self.ordinary_ready(fixture)
        head = fixture.candidate["head_sha"]
        retained = ready_transition_record(7, head, fixture.ready_record, config=fixture.config,
                                           verify_owner_source=fixture.provider.verify)
        self.assertTrue(revalidate_ready_transition_record(retained, 7, head, config=fixture.config,
                                                           verify_owner_source=fixture.provider.verify))
        fixture.config["github"]["expected_actor_id"] = 1001
        fixture.config["merge_gate"]["shared_owner_automation_actor_id"] = 1001
        fixture.accepted = deepcopy(fixture.config)
        from agentic.canonical import canonical
        fixture.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(fixture.accepted)
        with fixture.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            revalidate_ready_transition_record(retained, 7, head, config=fixture.config,
                                               verify_owner_source=fixture.provider.verify)

    def test_review_round_transition_refuses_shared_with_distinct_control(self):
        from test_review_tiers_195_overcap import Tier3OvercapGateTests, ROUND
        from test_review_policy import bind_review_admission, bind_production_posting_fixture
        from agentic.lifecycle import review_round_transition
        fixture = Tier3OvercapGateTests("runTest")
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bind_review_admission(fixture.bundle)
        registry = bind_production_posting_fixture(fixture.config, fixture.bundle)
        def call(approval):
            return review_round_transition(3, ROUND, accepted_policy_root=fixture.owner_fixture.root,
                overcap_approval_context={"config": fixture.config, "approvals": [approval],
                    "candidate": fixture.bundle["candidate"],
                    "runs": {run["run_id"]: run for run in fixture.bundle["runs"]},
                    "trusted_posting_collectors": registry})
        self.assertEqual("OWNER_REVIEW_REQUIRED", call(fixture.authenticated_approval(registry))["status"])
        fixture.config["merge_gate"].update(trusted_owner_ids=[1001, 1002], shared_owner_automation_actor_id=1002)
        fixture.owner_fixture.config = deepcopy(dict(fixture.config))
        approval = fixture.authenticated_approval(registry, actor_id=1002,
            body=fixture.approval()["raw_body"].replace("1001", "1002"))
        with self.assertRaisesRegex(ValidationError, SEPARATION):
            call(approval)


class RootlessGateRefusalTests(ProviderPolicyFixture):
    def test_closeout_counts_refuse_shared_with_distinct_control(self):
        from test_integration_queue_closeout import IntegrationQueueCloseoutTests
        fixture = IntegrationQueueCloseoutTests("runTest")
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        config = deepcopy(self.config)
        config["jira"]["enabled"] = False
        for key in ("cloud_id", "site", "provider_project_id", "project_key", "controller_actor_id"):
            config["jira"][key] = None
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            fixture.gate(fixture.record(None, config=config), config=config, accepted_config=config)
        config["merge_gate"].update(trusted_owner_ids=[1001], shared_owner_automation_actor_id=None)
        result = fixture.gate(fixture.record(None, config=config), config=config, accepted_config=config)
        self.assertTrue(result["count_allowed"])
        self.assertFalse(result["execution_authority"])

    def test_interaction_gate_handoff_refuses_shared_with_distinct_control(self):
        from test_reference import Fixture, NOW
        from agentic.interaction import gate_handoff
        fixture = Fixture("runTest")
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gate, request, _ = fixture.authorize()
        ordinary = gate_handoff(gate, request, fixture.config, fixture.contracts, NOW)
        self.assertFalse(ordinary["live_source_verified"])
        fixture.config["merge_gate"].update(trusted_owner_ids=[1001, 1002], shared_owner_automation_actor_id=1002)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            gate_handoff(gate, request, fixture.config, fixture.contracts, NOW)


class ExcludedIdentitySourceTests(ProviderPolicyFixture):
    def test_missing_accepted_identity_fails_closed_on_round_and_cap(self):
        from agentic.canonical import canonical
        fixture = RoundRefusalTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.accepted["github"]["expected_actor_id"] = None
        fixture.accepted["merge_gate"]["shared_owner_automation_actor_id"] = None
        fixture.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(fixture.accepted)
        with fixture.policy_reads():
            self.assertTrue(fixture.authorize(1001, review_policy.authorize_tier3_round))
        with fixture.policy_reads():
            self.assertEqual('CAP_PARK', review_policy.cap_disposition_plan(fixture.config,
                {"decision": "PARK", "open_finding_ids": [], "owner_source": {"actor_id": 1001}},
                3, 0, [], accepted_policy_root=fixture.project)['event'])

    def test_rootless_round_and_cap_fail_closed_with_missing_identity_context(self):
        fixture = RoundRefusalTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with self.assertRaisesRegex(ValidationError, "accepted_policy_root"):
            review_policy.authorize_tier3_round(4, 3, pr_comments=[fixture.approval(1001)],
                trusted_owner_ids=[1001], round_started_at="2026-10-03T10:00:00Z")
        with self.assertRaisesRegex(ValidationError, "accepted_policy_root"):
            review_policy.cap_disposition_plan(self.config,
                {"decision": "PARK", "open_finding_ids": [], "owner_source": {"actor_id": 1001}}, 3, 0, [])

    def test_supplied_identity_cannot_enable_round_or_cap_shared_signer(self):
        self.config["github"]["expected_actor_id"] = 1003
        self.config["merge_gate"]["shared_owner_automation_actor_id"] = None
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            review_policy.cap_disposition_plan(self.config,
                {"decision": "PARK", "open_finding_ids": [], "owner_source": {"actor_id": 1002}},
                3, 0, [], accepted_policy_root=self.project)
        approval = RoundRefusalTests.approval(self, 1002)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            review_tiers.validate_round(3, 4, config=self.config, overcap_approvals=[approval],
                trusted_owner_ids=[1001, 1002], round_started_at="2026-10-03T10:00:00Z",
                accepted_policy_root=self.project)

    def test_missing_provider_identity_and_alias_sealing_grant_no_override(self):
        record = ProvenanceRefusalTests.record(self, 1002)
        projection = ProvenanceRefusalTests.projection(self)
        projection.pop("expected_actor_id")
        observed = {key: value for key, value in projection.items() if key != "binding_sha256"}
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "missing expected_actor_id"):
            provenance._provider_owner_config(observed, accepted_policy_root=self.project)
        with self.assertRaisesRegex(ValidationError, "OVERRIDE_VERSION_AUTHORITY_UNBOUND"):
            provenance.verify_owner_override(record, trusted_owner_ids=[1002])
        self.assertIn("Who: 1002", provenance.render_owner_override(record))


class ProviderProjectionShapeTests(ProviderPolicyFixture):
    def test_provider_identity_rejects_boolean_alias_with_distinct_control(self):
        from agentic.canonical import canonical
        self.accepted["github"]["expected_actor_id"] = 1
        self.accepted["merge_gate"].update(trusted_owner_ids=[1001, 1],
                                          shared_owner_automation_actor_id=1)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        projection = ProvenanceRefusalTests.projection(self)
        observed = {key: value for key, value in projection.items() if key != "binding_sha256"}
        observed["expected_actor_id"] = True
        with self.policy_reads():
            with self.assertRaisesRegex(ValidationError, "expected_actor_id"):
                provenance._provider_owner_config(observed, projection, accepted_policy_root=self.project)
            observed["expected_actor_id"] = 1
            self.assertEqual(projection, provenance._provider_owner_config(
                observed, projection, accepted_policy_root=self.project))
            self.assertEqual(1001, provenance.verify_owner_override(
                ProvenanceRefusalTests.record(self, 1001), reviewed_owner_config=projection,
                accepted_policy_root=self.project)["who"])
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                provenance.verify_owner_override(ProvenanceRefusalTests.record(self, 1),
                    reviewed_owner_config=projection, accepted_policy_root=self.project)

    def test_explicit_reviewed_projection_cannot_hide_missing_provider_identity(self):
        projection = ProvenanceRefusalTests.projection(self)
        observed = {key: value for key, value in projection.items()
                    if key not in {"binding_sha256", "expected_actor_id"}}
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "missing expected_actor_id"):
            provenance._provider_owner_config(observed, projection, accepted_policy_root=self.project)

    def test_explicit_reviewed_projection_cannot_hide_provider_identity_mismatch(self):
        projection = ProvenanceRefusalTests.projection(self)
        observed = {key: value for key, value in projection.items() if key != "binding_sha256"}
        observed["expected_actor_id"] = 1003
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "expected_actor_id.*differs"):
            provenance._provider_owner_config(observed, projection, accepted_policy_root=self.project)


class MergeTrainIdentitySourceTests(ProviderPolicyFixture):
    def test_owner_review_requires_accepted_automation_identity(self):
        from agentic import merge_train
        from agentic.canonical import canonical
        from test_merge_train import MergeTrainTests, NOW
        fixture = MergeTrainTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        source = fixture._source()
        evidence = fixture._evidence(source, reviewer_id=1001)
        with self.assertRaisesRegex(ValidationError, "accepted_policy_root"):
            merge_train._validate_admission_evidence(source, evidence, [1001], now=NOW,
                                                     max_age_seconds=300)
        self.accepted["github"]["expected_actor_id"] = None
        self.accepted["merge_gate"]["shared_owner_automation_actor_id"] = None
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        with self.policy_reads():
            self.assertTrue(merge_train._validate_admission_evidence(source, evidence, [1001], now=NOW,
                max_age_seconds=300, accepted_policy_root=self.project))


class TierTwoExtensionRefusalTests(ProviderPolicyFixture):
    def test_tier_two_extension_refuses_shared_with_distinct_control(self):
        import inspect
        for actor in (1001, 1002):
            disposition = {"decision": "EXTEND_ONE_CYCLE", "record_id": "fixture-owner-record",
                "created_at": "2026-10-03T10:00:00Z", "producer_id": "fixture-verifier", "run_id": "fixture-run",
                "binding": {}, "open_finding_ids": [], "notes": "Fixture extension", "cycles": 3,
                "cap_extensions": 1, "successor_ticket": None, "authorization_request_id": "fixture-request",
                "owner_source": {"channel": "github_pr_comment", "comment_id": 91, "actor_id": actor},
                "evidence": ["urn:awf:fixture:owner-record"]}
            for consumer in (review_tiers.validate_round, review_tiers.review_decision, review_tiers.verdict_record):
                kwargs = {"owner_cap_disposition": disposition}
                if "accepted_policy_root" in inspect.signature(consumer).parameters:
                    kwargs["accepted_policy_root"] = self.project
                def call():
                    if consumer is review_tiers.verdict_record:
                        return consumer(pr_comment_url="https://example.invalid/comment/1",
                            pr_body_link="https://example.invalid/pr#review", verdict="PASS", tier=2,
                            round_number=4, head_sha="a" * 40, reviewer_id="fixture-critic", **kwargs)
                    return consumer(2, 4, **kwargs)
                with self.subTest(actor=actor, consumer=consumer.__name__), self.policy_reads():
                    if actor == 1002:
                        with self.assertRaisesRegex(ValidationError, SEPARATION):
                            call()
                    else:
                        result = call()
                        self.assertTrue(result)
                        if consumer is review_tiers.review_decision:
                            self.assertEqual("CONTINUE", result["status"])
                        elif consumer is review_tiers.verdict_record:
                            self.assertEqual(4, result["round"])


class AcceptedWorkflowSourceTests(ProviderPolicyFixture):
    record = ProvenanceRefusalTests.record
    projection = ProvenanceRefusalTests.projection
    verify = ProvenanceRefusalTests.verify

    def test_identity_uses_accepted_workflow_and_preserves_distinct_owner(self):
        from agentic.canonical import canonical
        folder = self.project / ".agentic"
        folder.mkdir()
        working = folder / "workflow.yaml"
        raw = canonical({"feature_branch_marker": "unaccepted workflow bytes"})
        working.write_bytes(raw)
        with self.policy_reads():
            self.assertEqual(self.record(1001), self.verify(1001))
            with self.assertRaisesRegex(ValidationError, SEPARATION):
                self.verify(1002)
        self.assertEqual(raw, working.read_bytes())


class HostOwnerNarrowingTests(ProviderPolicyFixture):
    driver = HostProjectionRefusalTests.driver
    record = ProvenanceRefusalTests.record
    projection = ProvenanceRefusalTests.projection

    def test_host_narrowing_preserves_distinct_owner_and_grants_nothing_to_shared(self):
        for actor in (1001, 1002):
            driver = self.driver(actor)
            driver.c["trusted_owner_ids"] = [1001]
            with self.subTest(actor=actor), self.policy_reads():
                observed = driver.collect_provenance_observation(owner_authority=True)
                self.assertEqual([1001], observed["trusted_owner_ids"])
                if actor == 1002:
                    with self.assertRaisesRegex(ValidationError, "trusted owner"):
                        provenance.verify_provider_owner_override(self.record(actor), observed,
                            accepted_policy_root=self.project)
                else:
                    self.assertEqual(91, provenance.verify_provider_owner_override(self.record(actor), observed,
                        accepted_policy_root=self.project)["provider_comment_id"])

    def test_projection_may_narrow_but_never_add_accepted_owners(self):
        projection = self.projection()
        projection["trusted_owner_ids"] = [1001]
        projection["binding_sha256"] = fingerprint("provenance_owner_config",
            {key: value for key, value in projection.items() if key != "binding_sha256"})
        with self.policy_reads():
            self.assertEqual((1001,), provenance._reviewed_owner_ids(projection,
                accepted_policy_root=self.project))
            self.assertEqual(1001, provenance.verify_owner_override(self.record(1001),
                reviewed_owner_config=projection, accepted_policy_root=self.project)["who"])
        projection["trusted_owner_ids"] = [1001, 1003]
        projection["binding_sha256"] = fingerprint("provenance_owner_config",
            {key: value for key, value in projection.items() if key != "binding_sha256"})
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "differs from accepted"):
            provenance._reviewed_owner_ids(projection, accepted_policy_root=self.project)
