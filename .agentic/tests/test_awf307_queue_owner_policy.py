"""Host-rooted policy at integration queue owner consumers; no global opt-out."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from test_awf307_provider_owner_policy import ProviderPolicyFixture, KEY
import test_integration_queue as fixtures
from agentic import ValidationError, integration_queue as queue
from agentic.canonical import canonical, fingerprint, loads
from agentic.shared_owner_policy import accepted_owner_evidence, OWNER_EVIDENCE_FIELDS

NOW = fixtures.NOW
SEPARATION = "The configured automation actor must not be a trusted owner"


class QueuePolicyFixture(ProviderPolicyFixture):
    def setUp(self):
        super().setUp()
        self.config["validation"]["required_ci_checks"][0]["name"] = "awf-integration"
        self.accept()
        self.registry = {}
        self.after_observe = None
        self.corrupt = None
        fixtures.register_integration_observer("fixture-mode-provider", self.observe,
            self.registry, observed_at=NOW)
        with self.policy_reads():
            self.annotation = accepted_owner_evidence(self.project, self.config, 1002)
        self.instruction = self.make_instruction("ENABLE_EPIC_MODE", self.settings())

    def accept(self):
        self.accepted = deepcopy(self.config)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)

    def settings(self):
        return {"test_command": "python -B -m unittest", "stall_after_hours": 24,
                "max_stack_depth": 2}

    def make_instruction(self, action, payload, identifier="policy-1", actor=1002):
        value = fixtures.instruction(action, payload, identifier)
        value["owner_actor_id"] = actor
        if actor == 1002:
            value.update(deepcopy(self.annotation))
        self.seal_instruction(value)
        return value

    def seal_instruction(self, value):
        value["instruction_id"] = fingerprint("integration_owner_instruction",
            {key: item for key, item in value.items()
             if key != "instruction_id" and key not in OWNER_EVIDENCE_FIELDS})

    def observe(self, request):
        operation = request["operation"]
        if operation == "ACCEPTED_PROJECT_CONFIG":
            result = fixtures.accepted_config_result(self.accepted)
        elif operation == "OWNER_INSTRUCTION":
            result = {"instruction": deepcopy(request["instruction"])}
        else:
            result = fixtures.observed_mode(request)
        if self.corrupt:
            self.corrupt(result)
        if self.after_observe:
            self.after_observe()
        return result

    def authority(self):
        return queue._authority(self.config, accepted_policy_root=self.project)

    def call(self, *, rooted=True):
        options = {"accepted_policy_root": self.project} if rooted else {}
        if self.consumer == "authority":
            return queue._authority(self.config, **options)
        if self.consumer == "projection":
            return queue._authority_projection(self.config, **options)
        if self.consumer == "accepted":
            return queue._accepted_project_config(self.config,
                {"producer_id": "fixture-mode-provider"}, self.registry, now=NOW,
                return_attestation=True, **options)[1]
        authority = (self.authority() if rooted else queue._authority(self.config))
        return queue._owner_instruction(self.instruction, "ENABLE_EPIC_MODE", "AWF-EPIC",
            self.settings(), authority=authority, registry=self.registry, now=NOW,
            max_age=3600, project_config=self.config, **options)

    def assert_evidence(self, record):
        self.assertEqual({key: record[key] for key in OWNER_EVIDENCE_FIELDS}, self.annotation)
        self.assertIn("1002", record["accepted_owner_policy"]["warnings"][0]["message"])


class ConsumerCases:
    def test_accepted_default_policy_accepts_exact_account_and_retains_complete_annotation(self):
        with self.policy_reads():
            result = self.call()
        self.assert_evidence(result)

    def test_supplied_working_and_feature_policy_never_enable(self):
        original = deepcopy(self.config)
        for label in ("supplied", "working"):
            with self.subTest(source=label):
                self.accepted = deepcopy(original)
                self.accepted["merge_gate"][KEY] = None
                self.accepted["merge_gate"]["trusted_owner_ids"] = [1001]
                self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
                if label == "working":
                    (self.project / ".agentic").mkdir()
                    (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(original))
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.call()
        self.accepted = deepcopy(original)
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        self.ref = "refs/remotes/origin/feature"
        with self.subTest(source="feature"), self.policy_reads(), self.assertRaisesRegex(
                ValidationError, "accepted default-branch identity differs"):
            self.call()

    def test_missing_policy_and_hidden_setting_refuse(self):
        self.blobs.pop(".agentic/workflow.yaml")
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()
        self.blobs[".agentic/workflow.yaml"] = canonical(self.workflow)
        self.config["merge_gate"][KEY] = None
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_changed_account_and_invalid_relationships_refuse(self):
        original = deepcopy(self.config)
        for field, value in (("expected_actor_id", 1003), ("expected_actor_id", None),
                             (KEY, True), (KEY, 1003), (KEY, "1002"),
                             ("trusted_owner_ids", [1001])):
            with self.subTest(field=field, value=value):
                self.config = deepcopy(original)
                group = "github" if field == "expected_actor_id" else "merge_gate"
                self.config[group][field] = value
                self.accept()
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.call()

    def test_policy_drift_between_observation_and_use_refuses(self):
        with self.policy_reads(drift=True), self.assertRaises(ValidationError):
            self.call()

    def test_distinct_ordinary_policy_keeps_existing_authority_without_annotation(self):
        self.config["merge_gate"][KEY] = None
        self.config["merge_gate"]["trusted_owner_ids"] = [1001]
        self.accept()
        self.instruction = self.make_instruction("ENABLE_EPIC_MODE", self.settings(), actor=1001)
        with self.policy_reads():
            result = self.call()
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))

    def test_no_root_keeps_global_separation_message(self):
        with self.assertRaisesRegex(ValidationError, SEPARATION):
            self.call(rooted=False)

    def test_unset_keeps_global_separation_message(self):
        self.config["merge_gate"][KEY] = None
        self.accept()
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, SEPARATION):
            self.call()


class AuthorityPolicyTests(ConsumerCases, QueuePolicyFixture):
    consumer = "authority"


class ProjectionPolicyTests(ConsumerCases, QueuePolicyFixture):
    consumer = "projection"


class AcceptedConfigPolicyTests(ConsumerCases, QueuePolicyFixture):
    consumer = "accepted"

    def test_provider_callback_policy_drift_refuses(self):
        self.after_observe = lambda: setattr(self, "commit", "b" * 40)
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_original_provider_config_source_digest_and_receipt_controls_remain(self):
        for field, value in (("ref", "refs/heads/feature"), ("repository_id", 999),
                             ("policy_sha256", "0" * 64), ("config_sha256", "0" * 64),
                             ("acceptance", {"status": "PENDING"})):
            with self.subTest(field=field):
                self.corrupt = lambda result: result.update({field: value})
                with self.policy_reads(), self.assertRaises(ValidationError):
                    self.call()


class InstructionPolicyTests(ConsumerCases, QueuePolicyFixture):
    consumer = "instruction"

    def test_no_root_keeps_global_separation_message(self):
        with self.policy_reads():
            authority = self.authority()
        with self.assertRaisesRegex(ValidationError, SEPARATION):
            queue._owner_instruction(self.instruction, "ENABLE_EPIC_MODE", "AWF-EPIC",
                self.settings(), authority=authority, registry=self.registry, now=NOW,
                max_age=3600, project_config=self.config)

    def test_policy_annotation_cannot_rotate_provider_instruction_replay_identity(self):
        original_identity = fingerprint("integration_owner_instruction",
            {key: value for key, value in self.instruction.items()
             if key != "instruction_id" and key not in OWNER_EVIDENCE_FIELDS})
        with self.policy_reads():
            first = self.call()
            self.assertEqual(first["instruction_id"], original_identity)
            self.commit = "b" * 40
            self.annotation = accepted_owner_evidence(self.project, self.config, 1002)
            self.instruction.update(deepcopy(self.annotation))
            self.seal_instruction(self.instruction)
            second = self.call()
            self.assertEqual(second["instruction_id"], first["instruction_id"])
            self.assertNotEqual(second["accepted_owner_policy"], first["accepted_owner_policy"])

    def test_different_owner_has_ordinary_authority_and_other_actor_refuses(self):
        self.instruction = self.make_instruction("ENABLE_EPIC_MODE", self.settings(), actor=1001)
        with self.policy_reads():
            result = self.call()
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))
        self.instruction = self.make_instruction("ENABLE_EPIC_MODE", self.settings(), actor=1003)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "trusted owner"):
            self.call()

    def test_incomplete_altered_and_entirely_missing_annotations_refuse(self):
        original = deepcopy(self.instruction)
        for field in sorted(OWNER_EVIDENCE_FIELDS):
            self.instruction = deepcopy(original)
            self.instruction.pop(field)
            self.seal_instruction(self.instruction)
            with self.subTest(missing=field), self.policy_reads(), self.assertRaises(ValidationError):
                self.call()
        self.instruction = {key: value for key, value in original.items()
                            if key not in OWNER_EVIDENCE_FIELDS}
        self.seal_instruction(self.instruction)
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()
        for field in ("policy_sha256", "config_sha256", "workflow_sha256", "default_branch_commit", "warnings"):
            self.instruction = deepcopy(original)
            self.instruction["accepted_owner_policy"][field] = [] if field == "warnings" else "c" * len(self.commit if field == "default_branch_commit" else "0" * 64)
            self.seal_instruction(self.instruction)
            with self.subTest(altered=field), self.policy_reads(), self.assertRaises(ValidationError):
                self.call()

    def test_provider_callback_drift_refuses_and_signed_body_is_unchanged(self):
        original = deepcopy(self.instruction)
        self.after_observe = lambda: setattr(self, "commit", "b" * 40)
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()
        self.assertEqual(self.instruction, original)

    def test_altered_or_missing_authority_annotation_refuses(self):
        with self.policy_reads():
            original = self.authority()
            for field in sorted(OWNER_EVIDENCE_FIELDS):
                authority = deepcopy(original)
                authority.pop(field)
                with self.subTest(field=field), self.assertRaises(ValidationError):
                    queue._owner_instruction(self.instruction, "ENABLE_EPIC_MODE", "AWF-EPIC",
                        self.settings(), authority=authority, registry=self.registry, now=NOW,
                        max_age=3600, project_config=self.config, accepted_policy_root=self.project)

    def test_provider_cannot_replace_retained_instruction_annotation(self):
        def corrupt(result):
            self.instruction["accepted_owner_policy"]["warnings"] = []
            result["instruction"] = deepcopy(self.instruction)
        self.corrupt = corrupt
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.call()

    def test_original_body_payload_event_digest_provider_and_freshness_controls_remain(self):
        original = deepcopy(self.instruction)
        for field, value in (("raw_body", "changed"), ("payload", {}),
                             ("source_event_id", ""), ("instruction_id", "0" * 64),
                             ("provider_identity", {}), ("issued_at", "2026-01-01T00:00:00Z")):
            self.instruction = deepcopy(original)
            self.instruction[field] = value
            with self.subTest(field=field), self.policy_reads(), self.assertRaises(ValidationError):
                self.call()


class QueueEntryPolicyTests(QueuePolicyFixture):
    def mode(self):
        return queue.resolve_mode({"test_command": "python -B -m unittest"},
            owner_instruction=self.instruction, epic="AWF-EPIC", project_config=self.config,
            app_binding={"producer_id": "fixture-mode-provider"},
            mode_observer_registry=self.registry, now=NOW, accepted_policy_root=self.project)

    def store(self):
        registry = {}
        fixtures.register_integration_observer("fixture-git-provider", fixtures.observed_git, registry)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return queue.IntegrationQueueStore(Path(folder.name) / "queue.sqlite3",
            project_config=self.config, mode_observer_registry=self.registry,
            git_observer_registry=registry, accepted_policy_root=self.project)

    def initialize(self):
        mode = self.mode()
        store = self.store()
        state = store.initialize("AWF-EPIC", "main", fixtures.SHA1,
            target_observer_id="fixture-git-provider", mode_record=mode, observed_at=NOW)
        return store, state

    def test_mode_and_store_retain_hashed_shared_instruction(self):
        with self.policy_reads():
            store, state = self.initialize()
        self.assert_evidence(state["mode_record"]["activation"]["instruction"])
        self.assert_evidence(state["project_config_trust"])
        self.assertEqual(store.snapshot("AWF-EPIC"), state)

    def test_incomplete_and_altered_mode_annotations_refuse_even_with_rehashed_record(self):
        with self.policy_reads():
            mode = self.mode()
            store = self.store()
            mode["activation"]["instruction"].pop("shared_owner_automation_account")
            self.seal_instruction(mode["activation"]["instruction"])
            mode["record_id"] = fingerprint("integration_mode", {key: value for key, value in mode.items() if key != "record_id"})
            with self.assertRaises(ValidationError):
                store.initialize("AWF-EPIC", "main", fixtures.SHA1,
                    target_observer_id="fixture-git-provider", mode_record=mode, observed_at=NOW)

    def test_store_owner_instruction_is_retained_and_replay_refuses(self):
        with self.policy_reads():
            store, _ = self.initialize()
            instruction = self.make_instruction("REORDER", {"order": []}, "reorder")
            outcome = fixtures.runner()
            store.build_producer_registry = fixtures.BUILD_REGISTRY
            store.owner_reorder("AWF-EPIC", [], instruction, outcome, observed_at=NOW)
            with closing(store._connect()) as db:
                events = [loads(row[0]) for row in db.execute("SELECT event_json FROM events")]
            event = next(event for event in events if event["kind"] == "OWNER_REORDER")
            self.assert_evidence(event["detail"]["instruction"])
            with self.assertRaisesRegex(ValidationError, "consumed|replay"):
                store.owner_reorder("AWF-EPIC", [], instruction, outcome, observed_at=NOW)

    def test_store_policy_drift_in_build_callback_rolls_back_instruction_and_state(self):
        with self.policy_reads():
            store, state = self.initialize()
            instruction = self.make_instruction("REORDER", {"order": []}, "drift")
            runner = fixtures.runner()
            store.build_producer_registry = fixtures.BUILD_REGISTRY
            def outcome(value):
                result = runner(value)
                self.commit = "b" * 40
                return result
            with self.assertRaises(ValidationError):
                store.owner_reorder("AWF-EPIC", [], instruction, outcome, observed_at=NOW)
            self.assertEqual(store.snapshot("AWF-EPIC"), state)

    def test_rootless_store_and_mode_keep_separation_refusal(self):
        with self.assertRaisesRegex(ValidationError, SEPARATION):
            queue.resolve_mode({"mode": "epic", "test_command": "python -B -m unittest"},
                epic="AWF-EPIC", project_config=self.config,
                app_binding={"producer_id": "fixture-mode-provider"},
                mode_observer_registry=self.registry, now=NOW)

    def test_skip_and_owner_removal_retain_annotation_and_dependency_ordering(self):
        with self.policy_reads():
            store, _ = self.initialize()
            store.build_producer_registry = fixtures.BUILD_REGISTRY
            store.admit("AWF-EPIC", fixtures.entry(1, fixtures.SHA2),
                "fixture-git-provider", fixtures.runner(), observed_at=NOW)
            store.admit("AWF-EPIC", fixtures.entry(2, fixtures.SHA3, depends=[1], base="awf/task-1"),
                "fixture-git-provider", fixtures.runner(), observed_at=NOW)
            skip = self.make_instruction("SKIP", {"pr_number": 1}, "skip-1")
            with self.assertRaisesRegex(ValidationError, "depends"):
                store.owner_skip("AWF-EPIC", 1, skip, fixtures.runner(), observed_at=NOW)
            reorder = self.make_instruction("REORDER", {"order": [2, 1]}, "order-2")
            with self.assertRaisesRegex(ValidationError, "dependency"):
                store.owner_reorder("AWF-EPIC", [2, 1], reorder, fixtures.runner(), observed_at=NOW)
            remove = self.make_instruction("REMOVE", {"pr_number": 2}, "remove-2")
            store.remove("AWF-EPIC", 2, fixtures.runner(), reason="REMOVAL",
                trigger=remove, observed_at=NOW)
            store.owner_skip("AWF-EPIC", 1, skip, fixtures.runner(), observed_at=NOW)
            self.assertEqual(store.snapshot("AWF-EPIC")["entries"], [])
            with closing(store._connect()) as db:
                events = [loads(row[0]) for row in db.execute("SELECT event_json FROM events")]
            records = [event["detail"].get("instruction", event["detail"].get("trigger"))
                for event in events if event["kind"] in ("OWNER_SKIP", "ENTRY_REMOVED")]
            self.assertEqual(len(records), 2)
            for record in records:
                self.assert_evidence(record)

    def test_retained_config_annotation_cannot_be_removed_before_refresh(self):
        with self.policy_reads():
            store, _ = self.initialize()
            state = store.snapshot("AWF-EPIC")
            for field in OWNER_EVIDENCE_FIELDS:
                state["project_config_trust"].pop(field)
            state["state_digest"] = fingerprint("integration_queue_state",
                {key: value for key, value in state.items() if key != "state_digest"})
            with closing(store._connect()) as db:
                db.execute("UPDATE epics SET state_json=?", (canonical(state).decode(),))
            with self.assertRaises(ValidationError):
                store.close("AWF-EPIC", observed_at=NOW)


if __name__ == "__main__":
    unittest.main()
