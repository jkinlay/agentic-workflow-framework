"""Accepted default policy at each authorization owner-authority entry point."""
from copy import deepcopy
import unittest

from test_awf307_provider_owner_policy import ProviderPolicyFixture, KEY
import test_reference as reference
import test_authorization_awf98 as dispositions
import test_ready_authorization as ready
from agentic import ValidationError, authorization as auth, merge_train
from agentic.canonical import canonical, fingerprint, sha256
from agentic.policy import policy_hash
from agentic.shared_owner_policy import accepted_owner_evidence, OWNER_EVIDENCE_FIELDS

NOW = reference.NOW
EXPIRES = "2026-09-09T12:15:00Z"


class AuthorizationPolicyFixture(ProviderPolicyFixture):
    @classmethod
    def setUpClass(cls):
        reference.Fixture.setUpClass()
        fixture = reference.Fixture(methodName="runTest")
        fixture.setUp()
        try:
            cls.base_gate, cls.base_request, cls.base_record = deepcopy(fixture.authorize())
            cls.base_config = deepcopy(dict(fixture.config))
        finally:
            fixture.doCleanups()

    def setUp(self):
        super().setUp()
        self.config = deepcopy(self.base_config)
        self.config["merge_gate"]["trusted_owner_ids"] = [1001, 1002]
        self.config["merge_gate"][KEY] = 1002
        self.accepted = deepcopy(self.config)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        self.gate, self.request, self.record = deepcopy((self.base_gate, self.base_request, self.base_record))
        self.gate["binding"]["policy_hash"] = policy_hash(self.config, self.workflow)
        self.request["binding"] = deepcopy(self.gate["binding"])
        self.request["gate_hash"] = fingerprint("gate", self.gate)
        self.request["expected_text"] = auth.render_request(self.request)
        self.record.update(binding=deepcopy(self.request["binding"]), gate_hash=self.request["gate_hash"])
        self.record["source"].update(actor_id=1002, raw_body=self.request["expected_text"],
            raw_body_sha256=sha256(self.request["expected_text"].encode()))
        fixture = dispositions.OwnerDispositionTests(methodName="runTest")
        fixture.setUp()
        self.disposition = fixture._signed_record()
        self.disposition["binding"] = deepcopy(self.gate["binding"])
        self.disposition["created_at"] = NOW
        self.disposition["owner_source"].update(actor_id=1002, created_at=NOW, updated_at=NOW)
        raw = auth.render_owner_record(self.disposition, "cap", dispositions.HEAD)
        self.disposition["owner_source"].update(raw_body=raw, raw_body_sha256=sha256(raw.encode()))
        request = auth.make_ready_authorization_request(repository_id=101, pr=7,
            head_sha=self.gate["candidate"]["head_sha"], role="owner", request_id="ready-policy-1",
            requested_at=NOW, expires_at=EXPIRES)
        self.provider = ready.FakeImmutableCommentProvider(self.config)
        self.provider.observed_at = NOW
        self.ready_record = {"format": auth.READY_AUTHORIZATION_FORMAT, "request": request,
            "owner_source": self.provider.create(body=auth.render_ready_authorization(request),
                actor_id=1002, created_at=NOW, repository_id=101, pr_number=7)}
        self.candidate = deepcopy(self.gate["candidate"])
        self.generation = {"format": "awf-merge-train-generation-1", "batch_id": "policy-batch",
            "target_branch": self.candidate["target_base_branch"],
            "target_sha": self.candidate["target_base_sha"], "generation": 1,
            "provider_identity": {key: self.candidate[key] for key in ("host", "repository", "repository_id")},
            "sources": [{"pr_number": 8, "head_sha": "1" * 40, "tree_sha": "2" * 40,
                         "admission_evidence_sha256": "3" * 64}],
            "authorization_scope": "CONSTRUCTION_ONLY", "may_publish": False, "may_merge": False}
        self.generation["generation_id"] = fingerprint("merge_train_generation", self.generation)
        self.events = []
        self.batch_actor = 1002
        self.batch_requested_at, self.batch_expires_at = NOW, EXPIRES
        self.batch_corrupt = None
        self.after_log = None
        self.registration = {"producer_kind": "merge_train_observer", "implementation_sha256": "a" * 64,
            "release_sha256": "b" * 64, "provider_identity": self.generation["provider_identity"],
            "receipt_namespace": merge_train.OBSERVER_NAMESPACE,
            "capabilities": ["BATCH_AUTHORIZATION", "BATCH_READY_STATE"], "receipts": {},
            "observer": self.observe_batch}
        self.registry = {"policy-observer": self.registration}
        with self.policy_reads():
            self.annotation = accepted_owner_evidence(self.project, self.config, 1002)
        self.record.update(deepcopy(self.annotation))
        self.disposition.update(deepcopy(self.annotation))
        self.ready_record.update(deepcopy(self.annotation))
        self.batch_request = {"format": "awf-batch-authorization-request-1", "action": "MARK_READY",
            "request_id": "batch-policy-1", "requested_at": NOW, "expires_at": EXPIRES,
            "generation_id": self.generation["generation_id"], "candidate": deepcopy(self.candidate),
            "configuration_sha256": fingerprint("batch_configuration", self.config), "batch_gate_id": None,
            **deepcopy(self.annotation)}

    def observe_batch(self, request):
        self.events.append(request["operation"])
        if request["operation"] == "BATCH_READY_STATE":
            result = {"candidate": deepcopy(self.candidate), "draft": False}
        else:
            result = {"source_id": request["source_id"], "actor_id": self.batch_actor,
                "created_at": NOW, "updated_at": NOW,
                "raw_body": auth.render_batch_authorization(request["request"]),
                "configuration_sha256": request["request"]["configuration_sha256"],
                "configuration_status": "ACCEPTED", "candidate": deepcopy(self.candidate),
                "generation_id": self.generation["generation_id"]}
            if self.batch_corrupt:
                self.batch_corrupt(result)
        material = {"producer_id": "policy-observer", "observed_at": NOW,
            "request_sha256": fingerprint("merge_train_provider_request", request), "result": result}
        observation = dict(material, status="OBSERVED",
            observation_id=fingerprint("merge_train_provider_observation", material))
        self.registration["receipts"][observation["observation_id"]] = merge_train.provider_receipt_sha256(
            self.registration, request, observation)
        return observation

    def log(self, record):
        self.events.append("LOG:" + record["decision"])
        if self.after_log:
            self.after_log()

    def call(self, *, rooted=True):
        options = {"accepted_policy_root": self.project} if rooted else {}
        if self.consumer == "verify_record":
            return auth.verify_record(self.record, self.request, self.gate, self.config, self.contracts, NOW, **options)
        if self.consumer == "make_owner_disposition_request":
            fields = {key: deepcopy(value) for key, value in self.disposition.items() if key != "owner_source"}
            return auth.make_owner_disposition_request(fields, dispositions.SCHEMA, self.config,
                head_sha=dispositions.HEAD, **options)
        if self.consumer == "verify_owner_record":
            return auth.verify_owner_record(self.disposition, dispositions.SCHEMA, self.config, self.contracts, NOW,
                head_sha=dispositions.HEAD, binding=self.gate["binding"], **options)
        if self.consumer == "verify_ready_authorization":
            return auth.verify_ready_authorization(self.ready_record, self.config, pr=7,
                head_sha=self.candidate["head_sha"], verify_owner_source=self.provider.verify, **options)
        if self.consumer == "make_batch_authorization_request":
            return auth.make_batch_authorization_request(self.generation, self.candidate, self.config,
                action="MARK_READY", request_id="batch-policy-1", requested_at=self.batch_requested_at,
                expires_at=self.batch_expires_at, **options)
        args = (self.batch_request, "comment-1", self.generation, self.candidate, self.config)
        kwargs = dict(observer_id="policy-observer", observer_registry=self.registry, now=NOW, **options)
        if self.consumer == "batch_mark_ready":
            return auth.batch_mark_ready(*args, log_record=self.log,
                mark_ready=lambda conditions: self.events.append("MARK_READY"), **kwargs)
        return auth.verify_batch_authorization(*args, **kwargs)

    def assert_refused(self, *, rooted=True, message=None):
        if self.consumer == "batch_mark_ready":
            result = self.call(rooted=rooted)
            self.assertEqual(result["decision"], "REFUSED")
            self.assertNotIn("MARK_READY", self.events)
            if message:
                self.assertRegex(result["reason"], message)
        elif message:
            with self.assertRaisesRegex(ValidationError, message):
                self.call(rooted=rooted)
        else:
            with self.assertRaises(ValidationError):
                self.call(rooted=rooted)


class ConsumerCases:
    def test_accepted_policy_enables_configured_account_with_retained_annotation(self):
        originals = deepcopy((self.record, self.disposition, self.ready_record, self.batch_request))
        with self.policy_reads():
            result = self.call()
        self.assertIs(result.get("execution_authority", False), False)
        self.assert_annotation(dict(result, execution_authority=False))
        self.assertEqual(originals, (self.record, self.disposition, self.ready_record, self.batch_request))
        if self.consumer == "batch_mark_ready":
            self.assertEqual(self.events, ["BATCH_AUTHORIZATION", "LOG:AUTHORIZED", "MARK_READY", "BATCH_READY_STATE"])

    def test_rootless_and_unset_separation_message_is_unchanged(self):
        for record in (self.record, self.disposition, self.ready_record, self.batch_request):
            for key in OWNER_EVIDENCE_FIELDS:
                record.pop(key, None)
        self.assert_refused(rooted=False, message="^The configured automation actor must not be a trusted owner$")
        self.config["merge_gate"][KEY] = None
        self.assert_refused(rooted=False, message="^The configured automation actor must not be a trusted owner$")

    def test_supplied_working_and_feature_configuration_never_enable(self):
        self.accepted["merge_gate"][KEY] = None
        self.accepted["merge_gate"]["trusted_owner_ids"] = [1001]
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
        with self.policy_reads():
            self.assert_refused()
        self.accepted = deepcopy(self.config)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        self.ref = "refs/remotes/origin/feature"
        with self.policy_reads():
            self.assert_refused()

    def test_drift_between_observation_and_use_refuses(self):
        with self.policy_reads(drift=True):
            self.assert_refused(message="policy changed")

    def test_changed_account_and_invalid_relationships_refuse(self):
        original = deepcopy(self.config)
        for section, key, value in [("github", "expected_actor_id", 1003),
                ("github", "expected_actor_id", None), ("merge_gate", "trusted_owner_ids", [1001]),
                *[("merge_gate", KEY, v) for v in (True, 0, -1, "1002", 1001)]]:
            self.config = deepcopy(original)
            self.config[section][key] = value
            with self.subTest(key=key, value=value), self.policy_reads():
                self.assert_refused()

    def test_missing_policy_and_hidden_setting_refuse(self):
        original = self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        with self.policy_reads():
            self.assert_refused()
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = original
        self.config["merge_gate"][KEY] = None
        self.config["github"]["expected_actor_id"] = 1003
        with self.policy_reads():
            self.assert_refused()

    def test_incomplete_and_altered_annotation_refuse(self):
        if self.consumer == "make_batch_authorization_request":
            # A producer has no retained input. Its output is signed as a whole;
            # exercise its verifier with each altered output annotation.
            with self.policy_reads():
                request = self.call()
            self.assertEqual(request, self.batch_request)
            self.consumer = "verify_batch_authorization"
        target = {"verify_record": self.record, "verify_ready_authorization": self.ready_record,
                  "verify_owner_record": self.disposition, "make_owner_disposition_request": self.disposition,
                  "verify_batch_authorization": self.batch_request, "batch_mark_ready": self.batch_request}[self.consumer]
        original = deepcopy(target)
        for key in OWNER_EVIDENCE_FIELDS:
            target.clear()
            target.update(deepcopy(original))
            del target[key]
            with self.subTest(missing=key), self.policy_reads():
                self.assert_refused()
        for key, value in (("shared_owner_automation_account", 1),
                ("shared_owner_automation_account", False), ("owner_policy_sha256", "e" * 64),
                ("accepted_owner_policy", original["accepted_owner_policy"] | {"default_branch_commit": "c" * 40}),
                ("accepted_owner_policy", original["accepted_owner_policy"] | {"unexpected": True})):
            target.clear()
            target.update(deepcopy(original))
            target[key] = value
            with self.subTest(altered=key, value=value), self.policy_reads():
                self.assert_refused()

    def test_different_untrusted_account_refuses(self):
        if self.consumer.startswith("make_"):
            self.config["merge_gate"][KEY] = 1001
        else:
            self.record["source"]["actor_id"] = 9999
            self.disposition["owner_source"]["actor_id"] = 9999
            self.ready_record["owner_source"]["actor_id"] = 9999
            self.batch_actor = 9999
        with self.policy_reads():
            self.assert_refused()

    def test_shared_verified_record_cannot_omit_the_entire_annotation(self):
        if self.consumer == "make_batch_authorization_request":
            with self.policy_reads():
                self.batch_request = self.call()
            self.consumer = "verify_batch_authorization"
        elif self.consumer == "make_owner_disposition_request":
            with self.policy_reads():
                result = self.call()
            self.disposition = dict(result["disposition_fields"], owner_source=self.disposition["owner_source"])
            self.consumer = "verify_owner_record"
        target = {"verify_record": self.record, "verify_ready_authorization": self.ready_record,
                  "verify_owner_record": self.disposition, "verify_batch_authorization": self.batch_request,
                  "batch_mark_ready": self.batch_request}[self.consumer]
        for key in OWNER_EVIDENCE_FIELDS:
            target.pop(key)
        with self.policy_reads():
            self.assert_refused()

    def test_distinct_trusted_owner_keeps_ordinary_authority_without_shared_annotation(self):
        if self.consumer == "make_batch_authorization_request":
            with self.policy_reads():
                self.batch_request = self.call()
            self.consumer = "verify_batch_authorization"
        elif self.consumer == "make_owner_disposition_request":
            with self.policy_reads():
                request = self.call()
            self.disposition = dict(request["disposition_fields"], owner_source=self.disposition["owner_source"])
            self.consumer = "verify_owner_record"
        for record, source_key in ((self.record, "source"), (self.disposition, "owner_source"),
                                   (self.ready_record, "owner_source")):
            record[source_key]["actor_id"] = 1001
            for key in OWNER_EVIDENCE_FIELDS:
                record.pop(key)
        source = self.ready_record["owner_source"]
        self.provider.comments[source["comment_id"]] = deepcopy(source)
        self.batch_actor = 1001
        with self.policy_reads():
            result = self.call()
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))


class VerifyRecordPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "verify_record"

    def test_unset_profile_only_policy_keeps_legacy_offline_verification(self):
        self.config["merge_gate"][KEY] = None
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        self.config["github"].update(expected_actor_id=None, auth_profile="fixture-profile")
        self.gate["binding"]["policy_hash"] = policy_hash(self.config, self.workflow)
        self.request["binding"] = deepcopy(self.gate["binding"])
        self.request["gate_hash"] = fingerprint("gate", self.gate)
        self.request["expected_text"] = auth.render_request(self.request)
        self.record.update(binding=deepcopy(self.request["binding"]), gate_hash=self.request["gate_hash"])
        for key in OWNER_EVIDENCE_FIELDS:
            self.record.pop(key)
        self.record["source"].update(actor_id=1001, raw_body=self.request["expected_text"],
            raw_body_sha256=sha256(self.request["expected_text"].encode()))
        result = self.call(rooted=False)
        self.assertTrue(result["record_consistent"])
        self.assertFalse(result["live_source_verified"])
        self.assertFalse(result["execution_authority"])
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))

    def test_signature_candidate_request_and_expiry_controls_under_opt_out(self):
        original = deepcopy(self.record)
        for change in (lambda r: r["source"].update(raw_body_sha256="f" * 64),
                lambda r: r["source"].update(raw_body="AUTHORIZE"),
                lambda r: r["source"].update(updated_at="2026-09-09T12:00:01Z"),
                lambda r: r.update(request_id="00000000-0000-0000-0000-000000000099"),
                lambda r: r["candidate"].update(head_sha="e" * 40),
                lambda r: r.update(expires_at="2026-09-09T11:59:59Z"),
                lambda r: r.update(execution_authority=True)):
            self.record = deepcopy(original)
            change(self.record)
            with self.subTest(change=change), self.policy_reads():
                self.assert_refused()


class BatchRequestPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "make_batch_authorization_request"

    def test_request_window_candidate_and_separate_handoff_controls_under_opt_out(self):
        for requested, expires in ((NOW, NOW), (NOW, "2026-09-09T12:15:01Z"),
                ("2026-09-09T12:01:00Z", NOW)):
            self.batch_requested_at, self.batch_expires_at = requested, expires
            with self.subTest(expires=expires), self.policy_reads():
                self.assert_refused()
        self.batch_requested_at, self.batch_expires_at = NOW, EXPIRES
        self.candidate["target_base_sha"] = "e" * 40
        with self.policy_reads():
            self.assert_refused()
        self.candidate["target_base_sha"] = self.generation["target_sha"]
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "exact-head final gate"):
            auth.make_batch_authorization_request(self.generation, self.candidate, self.config,
                action="HUMAN_MERGE_HANDOFF", request_id="handoff-policy", requested_at=NOW,
                expires_at=EXPIRES, accepted_policy_root=self.project)


class VerifyBatchPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "verify_batch_authorization"

    def test_rootless_callback_cannot_remove_default_account_separation(self):
        self.config["merge_gate"][KEY] = None
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        self.batch_request = auth.make_batch_authorization_request(self.generation, self.candidate, self.config,
            action="MARK_READY", request_id="batch-policy-1", requested_at=NOW, expires_at=EXPIRES)
        self.batch_corrupt = lambda result: self.config["merge_gate"]["trusted_owner_ids"].append(1002)
        self.assert_refused(rooted=False, message="^The configured automation actor must not be a trusted owner$")

    def test_provider_body_candidate_time_and_configuration_controls_under_opt_out(self):
        for field, value in (("raw_body", "source APPROVED"), ("configuration_status", "UNOBSERVED"),
                ("configuration_sha256", "f" * 64), ("generation_id", "f" * 64),
                ("updated_at", "2026-09-09T12:00:01Z"), ("created_at", "2026-09-09T11:59:59Z"),
                ("candidate", self.candidate | {"head_sha": "e" * 40})):
            self.batch_corrupt = lambda r, k=field, v=value: r.update({k: v})
            with self.subTest(field=field), self.policy_reads():
                self.assert_refused()


class BatchReadyPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "batch_mark_ready"

    def test_policy_drift_in_logging_adapter_refuses_before_mutation(self):
        self.after_log = lambda: setattr(self, "commit", "d" * 40)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "policy changed"):
            self.call()
        self.assertNotIn("MARK_READY", self.events)

    def test_provider_body_candidate_time_and_configuration_controls_under_opt_out(self):
        VerifyBatchPolicyTests.test_provider_body_candidate_time_and_configuration_controls_under_opt_out(self)

    def test_rootless_callback_cannot_remove_default_account_separation(self):
        VerifyBatchPolicyTests.test_rootless_callback_cannot_remove_default_account_separation(self)
        self.assertEqual(self.events, ["BATCH_AUTHORIZATION", "LOG:REFUSED"])


class DispositionRequestPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "make_owner_disposition_request"

    def test_request_id_and_cap_artifact_binding_controls_under_opt_out(self):
        original = deepcopy(self.disposition)
        for key, value in (("authorization_request_id", "invalid"), ("record_id", "f" * 64),
                ("critic_artifact_binding", self.disposition["critic_artifact_binding"] | {"head_sha": "e" * 40})):
            self.disposition = deepcopy(original)
            self.disposition[key] = value
            with self.subTest(key=key), self.policy_reads():
                self.assert_refused()


class VerifyOwnerPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "verify_owner_record"

    def test_rootless_missing_configuration_keeps_original_separation_validator_error(self):
        self.config = None
        for key in OWNER_EVIDENCE_FIELDS:
            self.disposition.pop(key)
        self.assert_refused(rooted=False, message="reviewed owner and automation actor configuration is required")

    def test_all_owner_schemas_retain_closed_annotation_without_altering_signed_body(self):
        common = {key: deepcopy(self.disposition[key]) for key in
            ("schema_version", "created_at", "producer_id", "run_id", "binding",
             "authorization_request_id", "owner_source", "evidence", *OWNER_EVIDENCE_FIELDS)}
        records = {
            "finding-disposition": dict(common, record_id="00000000-0000-0000-0000-000000000019",
                finding_id="F1", decision="ACCEPT_RISK", rationale="Fixture owner decision.", head_sha=dispositions.HEAD),
            "tier-reassignment": dict(common, record_id="00000000-0000-0000-0000-000000000020",
                contract_id="00000000-0000-0000-0000-000000000021", contract_version=1,
                from_tier=2, to_tier=1, rationale="Fixture owner decision."),
            "owner-closure": dict(common, record_id="00000000-0000-0000-0000-000000000022",
                merge_result_id="00000000-0000-0000-0000-000000000023", closure_kind="release",
                rationale="Fixture owner decision."),
        }
        for schema, record in records.items():
            head = dispositions.HEAD if schema == "finding-disposition" else "-"
            raw = auth.render_owner_record(record, auth.OWNER_KINDS[schema], head)
            record["owner_source"] = record["owner_source"] | {"raw_body": raw, "raw_body_sha256": sha256(raw.encode())}
            original = deepcopy(record)
            with self.subTest(schema=schema), self.policy_reads():
                result = auth.verify_owner_record(record, schema, self.config, self.contracts, NOW,
                    head_sha=head, binding=self.gate["binding"], accepted_policy_root=self.project)
                self.assert_annotation(result)
            self.assertEqual(original, record)

    def test_signature_expiry_candidate_and_verifier_independence_controls_under_opt_out(self):
        original = deepcopy(self.disposition)
        for change in (lambda r: r["owner_source"].update(raw_body_sha256="f" * 64),
                lambda r: r["owner_source"].update(updated_at="2026-09-09T12:00:01Z"),
                lambda r: r["owner_source"].update(raw_body="DISPOSE"),
                lambda r: r.update(created_at="2026-09-09T10:00:00Z"),
                lambda r: r["binding"].update(candidate_id="f" * 64),
                lambda r: r.update(successor_ticket="EX-999")):
            self.disposition = deepcopy(original)
            change(self.disposition)
            with self.subTest(change=change), self.policy_reads():
                self.assert_refused()
        self.disposition = original
        record = self.disposition
        runs = {record["run_id"]: {"role": "verifier", "producer_id": record["producer_id"],
            "context_id": "worker-context", "parent_run_id": None}}
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "shares an implementation"):
            auth.verify_owner_record(record, dispositions.SCHEMA, self.config, self.contracts, NOW,
                head_sha=dispositions.HEAD, binding=self.gate["binding"], runs=runs,
                excluded_contexts=["worker-context"], accepted_policy_root=self.project)


class VerifyReadyPolicyTests(ConsumerCases, AuthorizationPolicyFixture):
    consumer = "verify_ready_authorization"

    def test_missing_configuration_keeps_original_validation_error(self):
        record = {key: value for key, value in self.ready_record.items() if key not in OWNER_EVIDENCE_FIELDS}
        for config in (None, {}, {"github": None}):
            with self.subTest(config=config), self.assertRaisesRegex(ValidationError,
                    "^Ready authorization requires reviewed repository and owner configuration$"):
                auth.verify_ready_authorization(record, config, pr=7, head_sha=self.candidate["head_sha"],
                    verify_owner_source=self.provider.verify)

    def test_rootless_provider_configuration_drift_refuses(self):
        self.config["merge_gate"][KEY] = None
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        for key in OWNER_EVIDENCE_FIELDS:
            self.ready_record.pop(key)
        source = self.ready_record["owner_source"]
        source["actor_id"] = 1001
        self.provider.comments[source["comment_id"]] = deepcopy(source)
        self.provider.config_sha256 = auth.ready_authorization_config_sha256(self.config)
        verify = self.provider.verify
        def observe(source, request):
            result = verify(source, request)
            self.config["github"]["expected_actor_id"] = 1001
            return result
        self.provider.verify = observe
        self.assert_refused(rooted=False, message="configuration changed")

    def test_provider_signature_candidate_and_time_controls_under_opt_out(self):
        original = deepcopy(self.ready_record)
        for change in (lambda r: r["owner_source"].update(raw_body_sha256="f" * 64),
                lambda r: r["owner_source"].update(updated_at="2026-09-09T12:00:01Z"),
                lambda r: r["request"].update(head_sha="e" * 40),
                lambda r: r["request"].update(expires_at="2026-09-09T12:15:01Z")):
            self.ready_record = deepcopy(original)
            change(self.ready_record)
            with self.subTest(change=change), self.policy_reads():
                self.assert_refused()
        self.ready_record = original
        self.provider.observed_at = EXPIRES
        with self.policy_reads():
            self.assert_refused()

    def test_policy_drift_during_provider_callback_refuses(self):
        verify = self.provider.verify
        def observe(source, request):
            result = verify(source, request)
            self.commit = "d" * 40
            return result
        self.provider.verify = observe
        with self.policy_reads():
            self.assert_refused(message="policy changed")


if __name__ == "__main__":
    unittest.main()
