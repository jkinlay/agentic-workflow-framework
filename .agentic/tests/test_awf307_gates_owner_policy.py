"""Accepted owner policy at gate consumers; no provider body is rewritten."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_awf307_provider_owner_policy import ProviderPolicyFixture, KEY
import test_review_tiers_195_gates as tiers
import test_review_tiers_195_overcap as overcap
from test_review_policy import NOW, sign, signed, bind_review_admission, bind_production_posting_fixture
from agentic import ValidationError, gates
from agentic.canonical import canonical, fingerprint, sha256
from agentic.shared_owner_policy import accepted_owner_evidence, OWNER_EVIDENCE_FIELDS


class GatePolicyFixture(ProviderPolicyFixture):
    @classmethod
    def setUpClass(cls):
        tiers.ReviewTiers195GateTests.setUpClass()

    def setUp(self):
        super().setUp()
        self.fixture = tiers.ReviewTiers195GateTests(methodName="runTest")
        self.fixture.setUp()
        self.config = self.fixture.config
        self.config["merge_gate"].update(trusted_owner_ids=[1001, 1002], **{KEY: 1002})
        self.fixture.rebind()
        if self.consumer == "evaluate":
            self.fixture._tier_three_candidate()
        self.fixture._review_history(2, terminal_tier=3 if self.consumer == "evaluate" else 2)
        self.accepted = deepcopy(dict(self.config))
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        with self.policy_reads():
            self.annotation = accepted_owner_evidence(self.project, self.config, 1002)
        self.bundle = self.fixture.bundle
        self.excluded_contexts, self.excluded_producers = set(), set()
        self.candidate = self.bundle["candidate"]
        bind_review_admission(self.bundle)
        self.registry = bind_production_posting_fixture(self.config, self.bundle)
        if self.consumer == "evaluate":
            self.fixture._owner_review()
            self.record = self.bundle["owner_review"]
            self.record["owner_id"] = 1002
            observation = self.record["posting_observation"]
            from agentic.canonical import loads
            import base64
            provider = loads(gates.decode_base64_bytes(observation["provider_response_bytes"], "provider").decode())
            provider["owner_approval"]["user"]["id"] = 1002
            raw = canonical(provider)
            observation.update(provider_response_bytes={"encoding": "base64", "data": base64.b64encode(raw).decode()},
                               provider_response_sha256=sha256(raw))
            self.record.update(deepcopy(self.annotation))
            self.fixture._refresh_owner_posting_receipt(self.registry)
        elif self.consumer == "verified_overcap_approvals":
            from types import MethodType
            self.fixture.approval = MethodType(overcap.Tier3OvercapGateTests.approval, self.fixture)
            self.record = overcap.Tier3OvercapGateTests.authenticated_approval(
                self.fixture, self.registry, actor_id=1002,
                body=overcap.GATE_CANONICAL.replace("1001", "1002"))
            self.record.update(deepcopy(self.annotation))
            self.seal_approval()
        else:
            if self.consumer == "finding":
                self.record = self.fixture.record(finding_id="F1", decision="REQUIRE_FIX",
                    rationale="Owner requires correction.", head_sha=self.candidate["head_sha"], **signed())
                self.bundle["finding_dispositions"] = [self.record]
                kind = "finding"
            else:
                self.record = self.fixture.record(decision="MERGE_WITH_NOTES", cycles=2, cap_extensions=0,
                    open_finding_ids=[], successor_ticket=None, notes="Owner reviewed closeout.", **signed())
                self.bundle["cap_disposition"] = self.record
                kind = "cap"
            self.record.update(deepcopy(self.annotation))
            sign(self.record, kind, self.candidate["head_sha"], actor_id=1002)
        self.original_body = deepcopy(self.record.get("posting_observation",
            self.record.get("provider_response_bytes", self.record.get("owner_source"))))

    def seal_approval(self):
        basis = dict(self.record, collector_receipt_sha256="0" * 64)
        registration = self.registry[self.record["producer_id"]]
        receipt = gates.overcap_approval_collector_receipt_sha256(self.record, sha256(canonical(basis)), registration)
        self.record["collector_receipt_sha256"] = receipt
        registration["receipts"][self.record["run_id"]] = receipt

    def call(self, *, rooted=True):
        kwargs = {"accepted_policy_root": self.project} if rooted else {}
        runs = {run["run_id"]: run for run in self.bundle["runs"]}
        if self.consumer == "evaluate":
            return gates.evaluate(self.config, self.workflow, self.bundle, self.contracts, NOW,
                                  posting_collector_registry=self.registry, **kwargs)
        if self.consumer == "verified_overcap_approvals":
            return gates.verified_overcap_approvals(self.config, [self.record], self.candidate, runs, self.registry, **kwargs)
        from agentic.review_tiers import runtime_assignment
        return gates.verified_owner_records(self.config, self.contracts, self.bundle,
            gates.expected_binding(self.config, self.workflow, self.bundle), runs,
            self.excluded_contexts, self.excluded_producers, NOW,
            assignment_record=runtime_assignment(self.config, ticket_key=self.bundle["contract"]["ticket"]), **kwargs)

    def refuses(self, *, rooted=True, message=None):
        with self.assertRaisesRegex(ValidationError, message or "."):
            self.call(rooted=rooted)


class ConsumerCases:
    def test_shared_account_accepted_with_complete_annotation(self):
        with self.policy_reads():
            result = self.call()
        if self.consumer == "evaluate":
            self.assertEqual(result["conclusion"], "READY_FOR_OWNER_AUTHORIZATION")
            self.assert_annotation(result)
            self.assertIn("SHARED_OWNER_AUTOMATION_ACCOUNT", " ".join(result["residual_risks"]))
        elif self.consumer == "verified_overcap_approvals":
            self.assertEqual(result, [self.record])
        elif self.consumer == "finding":
            self.assertEqual(result, ([self.record], None))
        else:
            self.assertEqual(result, ([], self.record))
        self.assertEqual(self.original_body, self.record.get("posting_observation",
            self.record.get("provider_response_bytes", self.record.get("owner_source"))))

    def test_no_root_and_unset_keep_separation_refusal(self):
        # The disposition path already refused through authorization. Gate helpers
        # that formerly trusted caller policy now also enforce this boundary.
        self.refuses(rooted=False, message="^The configured automation actor must not be a trusted owner$")
        self.config["merge_gate"][KEY] = None
        self.refuses(rooted=False, message="^The configured automation actor must not be a trusted owner$")

    def test_supplied_working_feature_and_missing_policy_refuse(self):
        original = self.blobs[".agentic/PROJECT_CONFIG.yaml"]
        self.accepted["merge_gate"].update(trusted_owner_ids=[1001], **{KEY: None})
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(dict(self.config)))
        with self.policy_reads():
            self.refuses()
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = original
        self.ref = "refs/remotes/origin/feature"
        with self.policy_reads():
            self.refuses()
        self.ref = "refs/remotes/origin/main"
        self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        with self.policy_reads():
            self.refuses()

    def test_account_relationship_and_hidden_setting_refuse(self):
        original = deepcopy(dict(self.config))
        for section, key, value in [("github", "expected_actor_id", 1003), ("github", "expected_actor_id", None),
                ("merge_gate", "trusted_owner_ids", [1001]),
                *[("merge_gate", KEY, v) for v in (True, 0, -1, "1002", 1001, None)]]:
            self.config[section][key] = value
            with self.subTest(key=key, value=value), self.policy_reads():
                self.refuses()
            self.config[section][key] = deepcopy(original[section][key])

    def test_drift_between_observation_and_use_refuses(self):
        with self.policy_reads(drift=True):
            self.refuses(message="policy changed")

    def test_incomplete_altered_and_absent_annotation_refuse(self):
        original = deepcopy(self.record)
        mutations = [(key, None) for key in OWNER_EVIDENCE_FIELDS] + [
            ("shared_owner_automation_account", 1), ("owner_policy_sha256", "e" * 64),
            ("accepted_owner_policy", self.annotation["accepted_owner_policy"] | {"default_branch_commit": "e" * 40}),
            ("accepted_owner_policy", self.annotation["accepted_owner_policy"] | {"extra": True})]
        for key, value in mutations + [(None, None)]:
            self.record.clear()
            self.record.update(deepcopy(original))
            if key is None:
                for field in OWNER_EVIDENCE_FIELDS:
                    self.record.pop(field)
            elif value is None:
                self.record.pop(key)
            else:
                self.record[key] = value
            if self.consumer == "verified_overcap_approvals":
                self.seal_approval()
            with self.subTest(key=key, value=value), self.policy_reads():
                self.refuses()

    def test_different_account_cannot_use_annotation(self):
        key = "owner_id" if self.consumer == "evaluate" else "actor_id"
        target = self.record.get("owner_source", self.record)
        target[key] = 1001
        with self.policy_reads():
            self.refuses()


class FindingOwnerPolicyTests(ConsumerCases, GatePolicyFixture):
    consumer = "finding"

    def test_signature_candidate_expiry_replay_and_independence_preserved(self):
        original = deepcopy(self.record)
        for change in (lambda r: r["owner_source"].update(raw_body_sha256="e" * 64),
                lambda r: r["owner_source"].update(updated_at="2026-09-09T12:00:01Z"),
                lambda r: r["binding"].update(candidate_id="e" * 64),
                lambda r: r.update(created_at="2026-09-09T10:00:00Z")):
            self.record.clear()
            self.record.update(deepcopy(original))
            change(self.record)
            with self.subTest(change=change), self.policy_reads():
                self.refuses()
        self.record.clear()
        self.record.update(original)
        self.bundle["finding_dispositions"].append(deepcopy(original))
        with self.policy_reads():
            self.refuses(message="already signs another record")
        self.bundle["finding_dispositions"] = [self.record]
        verifier = next(run for run in self.bundle["runs"] if run["run_id"] == self.record["run_id"])
        self.excluded_contexts = {verifier["context_id"]}
        with self.policy_reads():
            self.refuses(message="shares an implementation")


class CapOwnerPolicyTests(ConsumerCases, GatePolicyFixture):
    consumer = "cap"

    def test_assignment_floor_and_cap_binding_preserved(self):
        self.record["cycles"] = 1
        sign(self.record, "cap", self.candidate["head_sha"], actor_id=1002)
        with self.policy_reads():
            self.refuses(message="precedes the cap")


class OvercapOwnerPolicyTests(ConsumerCases, GatePolicyFixture):
    consumer = "verified_overcap_approvals"

    def test_unset_profile_only_policy_keeps_ordinary_provider_verification(self):
        self.config["merge_gate"].update(trusted_owner_ids=[1001], **{KEY: None})
        self.config["github"]["expected_actor_id"] = None
        self.record = overcap.Tier3OvercapGateTests.authenticated_approval(self.fixture, self.registry)
        self.assertEqual(self.call(rooted=False), [self.record])
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(self.record))

    def test_provider_observation_and_receipt_preserved(self):
        for key, value in (("actor_id", 9999), ("pr_number", 99), ("collector_receipt_sha256", "e" * 64),
                           ("raw_body", "APPROVED"), ("provider_response_sha256", "e" * 64)):
            original = self.record[key]
            self.record[key] = value
            with self.subTest(key=key), self.policy_reads():
                self.refuses()
            self.record[key] = original


class EvaluateOwnerPolicyTests(ConsumerCases, GatePolicyFixture):
    consumer = "evaluate"

    def test_barrier_and_independent_reviewer_controls_preserved(self):
        self.bundle["review_submission"]["completion_snapshot"]["results"].pop()
        with self.policy_reads():
            self.refuses()

    def test_policy_drift_during_gate_callback_refuses(self):
        original = gates.provenance.check_commit_range
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            self.commit = "d" * 40
            return result
        with self.policy_reads(), patch.object(gates.provenance, "check_commit_range", side_effect=observe):
            self.refuses(message="policy changed")

    def test_annotation_mutation_during_gate_callback_refuses(self):
        original = gates.provenance.check_commit_range
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            self.record.pop("owner_policy_sha256")
            return result
        with self.policy_reads(), patch.object(gates.provenance, "check_commit_range", side_effect=observe):
            self.refuses(message="incomplete")


if __name__ == "__main__":
    unittest.main()
