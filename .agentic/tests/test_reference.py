"""Negative safety cases and local end-to-end reference validation."""
from __future__ import annotations
import copy
from datetime import timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid

from agentic import CapabilityUnavailable, ValidationError
from agentic.authorization import make_request, parse_text, render_request, verify_record, verify_webhook
from agentic.canonical import canonical, fingerprint, load, loads, sha256, timestamp
from agentic.cli import local_semantics
from agentic.contracts import Contracts
from agentic.gates import evaluate, expected_binding
from agentic.installer import install, recover, rollback, verify_installed, verify_release, ensure_usable, json_bytes, JOURNAL, MARKER
from agentic.lifecycle import definition, transition, STATES, NORMAL, INVALIDATING, CONTROL_EVENTS
from agentic.policy import validate_config, topological_order, require_reference_capability, inside_scope
from agentic.safeio import Tree
from agentic.store import Store

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-09-09T12:00:00Z"


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")
        cls.config0 = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        cls.bundle0 = load(ROOT / ".agentic/examples/evidence-bundle.json")

    def setUp(self):
        self.config = copy.deepcopy(self.config0)
        self.bundle = copy.deepcopy(self.bundle0)

    def gate(self):
        return evaluate(self.config, definition(), self.bundle, self.contracts, NOW)

    def authorize(self, decision="AUTHORIZE"):
        gate = self.gate()
        request = make_request(gate, self.contracts, NOW)
        raw = render_request(request, decision)
        result = {key: request[key] for key in ["schema_version", "request_id", "gate_id", "gate_hash", "candidate", "binding", "expires_at"]}
        result.update(record_id=str(uuid.uuid4()), created_at=NOW, producer_id="fixture-verifier", run_id=str(uuid.uuid4()),
            source={"channel": "github_pr_comment", "event_id": str(uuid.uuid4()), "comment_id": 12, "repository_id": 101,
                "pr_number": 7, "created_at": NOW, "updated_at": NOW, "raw_body": raw, "raw_body_sha256": sha256(raw.encode()),
                "actor_id": 1001, "actor_login": "fixture-owner", "deleted": False, "authentication_evidence": ["urn:awf:fixture:webhook"]},
            decision=decision, parser_version="awf-auth-1", execution_authority=False)
        return gate, request, result


class SerializationTests(Fixture):
    def test_duplicate_json_keys(self):
        with self.assertRaises(ValidationError):
            loads('{"approval": false, "approval": true}')

    def test_nonfinite_and_float_values(self):
        for value in ['{"cost": NaN}', '{"cost": Infinity}', '{"cost": 0.1}']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                loads(value)

    def test_canonical_order_and_domains(self):
        self.assertEqual(canonical({"a": 1, "b": 2}), canonical({"b": 2, "a": 1}))
        self.assertNotEqual(fingerprint("candidate", {"a": 1}), fingerprint("requirements", {"a": 1}))

    def test_bad_timestamps_rejected_without_optional_extras(self):
        for date in ["not-a-date", "2026-02-30T12:00:00Z", "2026-09-09T12:00:00", "2026-09-09"]:
            value = copy.deepcopy(self.bundle["critic"])
            value["created_at"] = date
            with self.subTest(date=date), self.assertRaises(ValidationError):
                self.contracts.validate("critic-review", value)

    def test_duplicate_yaml_and_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "record.yaml"
            for text in ["a: 1\na: 2\n", "a: &x [1]\nb: *x\n"]:
                p.write_text(text)
                with self.assertRaises(ValidationError):
                    load(p)


class ConfigTests(Fixture):
    def test_fixture_config(self):
        self.assertEqual(len(validate_config(self.config, definition(), self.contracts)), 64)

    def test_default_not_ready(self):
        with self.assertRaises(ValidationError):
            validate_config(load(ROOT / ".agentic/examples/unconfigured-project.yaml"), definition(), self.contracts)

    def test_three_native_writers_without_reference_activation(self):
        from agentic.operating import default_operating
        self.assertEqual(default_operating()["streams"]["count"], 3)
        for config in [self.config, load(ROOT / ".agentic/examples/unconfigured-project.yaml")]:
            with self.subTest(project=config["project"]["name"]):
                execution = config["execution"]
                self.assertEqual(execution["max_parallel_tickets"], 6)
                self.assertEqual(execution["max_parallel_tickets_per_stream"], 1)
                self.assertEqual(execution["native_streams"], {"enabled": True, "dispatch_policy": "ready_independent"})
                self.assertFalse(execution["host_broker"]["enabled"])
                self.assertEqual(execution["host_broker"]["max_workers"], 3)
                self.assertFalse(any(config["controller"].values()))
                self.assertFalse(config["merge_gate"]["automatic_merge_enabled"])

    def test_explicit_lower_native_capacity_is_valid_and_policy_bound(self):
        original = validate_config(self.config, definition(), self.contracts)
        self.config["execution"]["max_parallel_tickets"] = 1
        restricted = validate_config(self.config, definition(), self.contracts)
        self.assertNotEqual(original, restricted)
        self.config["execution"]["native_streams"]["enabled"] = False
        self.assertNotEqual(restricted, validate_config(self.config, definition(), self.contracts))

    def test_native_mode_requires_explicit_valid_configuration(self):
        for value in [None, {"enabled": "true", "dispatch_policy": "ready_independent"},
                      {"enabled": True, "dispatch_policy": "ignore_capacity"}]:
            config = copy.deepcopy(self.config)
            if value is None:
                del config["execution"]["native_streams"]
            else:
                config["execution"]["native_streams"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_config(config, definition(), self.contracts)

    def test_unsafe_config_regressions(self):
        mutations = [lambda c: c["critic"].update(blocking_severities=["NIT"]),
            lambda c: c["validation"].update(require_candidate_bound_ci_evidence=False),
            lambda c: c["critic"].update(require_re_review_after_any_target_base_change=False),
            lambda c: c["merge_gate"].update(require_specialist_reviews_current_tuple=False),
            lambda c: c["template"].update(expected_workflow_version="99.0.0"),
            lambda c: c["controller"].update(dispatch_enabled=True),
            lambda c: c["jira"]["scope"].update(additional_jql="project = EX"),
            lambda c: c["merge_gate"].update(trusted_owner_ids=[None]),
            lambda c: c["scope"].update(protected_paths=[]),
            lambda c: c["execution"]["roles"]["worker"].update(model="unapproved"),
            lambda c: c["merge_gate"].update(automatic_merge_enabled=True)]
        for index, mutate in enumerate(mutations):
            value = copy.deepcopy(self.config)
            mutate(value)
            with self.subTest(case=index), self.assertRaises(ValidationError):
                validate_config(value, definition(), self.contracts)

    def test_missing_or_changed_workflow(self):
        for workflow in [{}, {**definition(), "live_side_effects_supported": True}]:
            with self.assertRaises(ValidationError):
                validate_config(self.config, workflow, self.contracts)

    def test_scope_and_dependencies(self):
        self.assertTrue(inside_scope(self.bundle["snapshot"], self.config))
        self.bundle["snapshot"]["project_key"] = "OUTSIDE"
        self.assertFalse(inside_scope(self.bundle["snapshot"], self.config))
        self.assertEqual(topological_order({"a": ["b"], "b": []}), ["b", "a"])
        for graph in [{"a": ["b"], "b": ["a"]}, {"a": ["missing"]}]:
            with self.assertRaises(ValidationError):
                topological_order(graph)


class GateTests(Fixture):
    def test_required_specialist_positive_path(self):
        self.bundle["contract"]["risk_flags"]["security"] = True
        self.bundle["contract"]["specialist_domains"] = ["security"]
        run = copy.deepcopy(self.bundle["runs"][2])
        run.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), role="specialist",
                   context_id=str(uuid.uuid4()), producer_id="fixture-security-specialist")
        review = {key: run[key] for key in ["schema_version", "created_at", "producer_id", "run_id", "binding"]}
        review.update(record_id=str(uuid.uuid4()), domain="security",
            reviewer_identity=self.config["specialist_reviews"]["security"]["reviewer_identity"],
            trigger_evidence=["urn:awf:fixture:example-evidence"], verdict="PASS", findings=[],
            evidence_checked=["urn:awf:fixture:example-evidence"])
        self.bundle["runs"].append(run)
        self.bundle["specialists"].append(review)
        binding = expected_binding(self.config, definition(), self.bundle)
        for record in [self.bundle[k] for k in ["dispatch", "worker", "critic", "ci", "pr"]] + self.bundle["runs"] + self.bundle["specialists"]:
            record["binding"] = copy.deepcopy(binding)
        gate = self.gate()
        self.assertEqual(gate["conclusion"], "READY_FOR_OWNER_AUTHORIZATION")
        self.assertEqual(gate["gates"]["specialist_reviews"]["result"], "PASS")
        self.assertEqual(gate["required_specialist_domains"], ["security"])

    def test_dispatch_and_collector_provenance(self):
        for mutate in [lambda b: b["dispatch"].update(producer_id="unregistered"),
                       lambda b: b["dispatch"].update(disposition="PROPOSAL"),
                       lambda b: b["dispatch"]["lease"].update(expires_at="2026-09-09T11:59:00Z"),
                       lambda b: b["ci"].update(collector_attestation_id=str(uuid.uuid4())),
                       lambda b: b["pr"].update(collector_attestation_id=str(uuid.uuid4()))]:
            self.bundle = copy.deepcopy(self.bundle0)
            mutate(self.bundle)
            with self.assertRaises(ValidationError):
                self.gate()

    def test_required_specialist_cannot_be_not_applicable(self):
        gate = self.gate()
        gate["required_specialist_domains"] = ["security"]
        with self.assertRaises(ValidationError):
            self.contracts.validate("final-gate", gate)

    def test_negative_exit_code_is_valid_failure_evidence(self):
        self.bundle["worker"]["validation"][0]["exit_code"] = -9
        self.assertEqual(self.gate()["conclusion"], "NOT_READY")

    def test_gate_references_exact_inputs(self):
        gate = self.gate()
        self.assertIn("urn:awf:input:ci:" + fingerprint("gate-input", self.bundle["ci"]), gate["gates"]["required_ci"]["evidence"])

    def test_offline_end_to_end(self):
        gate = self.gate()
        self.assertEqual(gate["conclusion"], "READY_FOR_OWNER_AUTHORIZATION")
        self.assertFalse(gate["execution_authority"])
        request = make_request(gate, self.contracts, NOW)
        self.assertEqual(parse_text(request["expected_text"])[0], "AUTHORIZE")

    def test_schema_rejects_all_failed_or_na_ready_gates(self):
        for verdict in ["FAIL", "N_A"]:
            gate = self.gate()
            for result in gate["gates"].values():
                result["result"] = verdict
            with self.assertRaises(ValidationError):
                self.contracts.validate("final-gate", gate)

    def test_approve_with_failed_ac_or_open_blocker(self):
        for case in ["ac", "blocker"]:
            review = copy.deepcopy(self.bundle["critic"])
            if case == "ac":
                review["acceptance_criteria"][0]["verdict"] = "FAIL"
            else:
                review["findings"] = [{"id": "F1", "severity": "BLOCKER", "summary": "Fixture", "evidence": ["urn:awf:fixture:example-evidence"], "status": "OPEN", "resolution_evidence": []}]
            with self.assertRaises(ValidationError):
                self.contracts.validate("critic-review", review)

    def test_missing_required_lease(self):
        self.bundle["dispatch"]["lease"]["lease_id"] = None
        with self.assertRaises(ValidationError):
            self.gate()

    def test_cross_record_swaps(self):
        for field in ["requirements_hash", "contract_hash", "policy_hash", "candidate_id", "issue_id"]:
            self.bundle = copy.deepcopy(self.bundle0)
            self.bundle["critic"]["binding"][field] = "f" * 64 if field.endswith("hash") or field == "candidate_id" else "9999"
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.gate()

    def test_omitted_and_duplicate_ac(self):
        for items in [self.bundle["critic"]["acceptance_criteria"][:1], [self.bundle["critic"]["acceptance_criteria"][0]] * 2]:
            self.bundle["critic"]["acceptance_criteria"] = items
            with self.assertRaises(ValidationError):
                self.gate()

    def test_unregistered_evidence(self):
        self.bundle["critic"]["evidence_checked"] = ["urn:awf:invented"]
        with self.assertRaises(ValidationError):
            self.gate()

    def test_same_critic_context(self):
        worker = next(r for r in self.bundle["runs"] if r["role"] == "worker")
        critic = next(r for r in self.bundle["runs"] if r["role"] == "critic")
        critic["context_id"] = worker["context_id"]
        with self.assertRaises(ValidationError):
            self.gate()

    def test_worker_cannot_attest_merge_capability(self):
        next(r for r in self.bundle["runs"] if r["role"] == "worker")["capabilities"].append("merge")
        with self.assertRaises(ValidationError):
            self.gate()

    def test_incomplete_retrieval_or_coverage(self):
        self.bundle["pr"]["retrieval_complete"] = False
        self.assertEqual(self.gate()["conclusion"], "NOT_READY")

    def test_wrong_ci_app_skipped_test_count_tree(self):
        for mutation in [{"app_id": 999}, {"conclusion": "skipped"}, {"tests_executed": 0}, {"tested_tree_sha": "0" * 40}]:
            self.bundle = copy.deepcopy(self.bundle0)
            self.bundle["ci"]["checks"][0].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual(self.gate()["conclusion"], "NOT_READY")

    def test_stale_evidence(self):
        with self.assertRaises(ValidationError):
            evaluate(self.config, definition(), self.bundle, self.contracts, "2026-09-10T12:00:00Z")

    def test_missing_specialist(self):
        self.bundle["pr"]["specialist_domains"] = ["security"]
        with self.assertRaises(ValidationError):
            self.gate()

    def test_forgotten_prior_finding(self):
        self.bundle["prior_findings"] = [{"id": "F1", "severity": "MAJOR", "summary": "Earlier blocker", "evidence": ["urn:awf:fixture:example-evidence"], "status": "OPEN", "resolution_evidence": []}]
        with self.assertRaises(ValidationError):
            self.gate()

    def test_live_side_effect_capabilities_fail_closed(self):
        for capability in ["merge", "dispatch", "transition_jira", "publish_review"]:
            with self.assertRaises(CapabilityUnavailable):
                require_reference_capability(capability)


class AuthorizationTests(Fixture):
    def test_superseded_policy_rejected(self):
        gate, request, record = self.authorize()
        self.config["execution"]["max_run_seconds"] += 1
        with self.assertRaises(ValidationError):
            verify_record(record, request, gate, self.config, self.contracts, NOW)

    def test_request_cannot_extend_gate_expiry(self):
        gate, request, record = self.authorize()
        request["expires_at"] = record["expires_at"] = "2026-09-09T12:30:00Z"
        request["expected_text"] = record["source"]["raw_body"] = render_request(request)
        record["source"]["raw_body_sha256"] = sha256(record["source"]["raw_body"].encode())
        with self.assertRaises(ValidationError):
            verify_record(record, request, gate, self.config, self.contracts, NOW)

    def test_consistent_record_grants_no_execution(self):
        gate, request, record = self.authorize()
        result = verify_record(record, request, gate, self.config, self.contracts, NOW)
        self.assertTrue(result["record_consistent"])
        self.assertFalse(result["live_source_verified"])
        self.assertFalse(result["execution_authority"])

    def test_negation_quotation_extra_lines_and_spaces(self):
        _, request, _ = self.authorize()
        raw = request["expected_text"]
        for text in ["DO NOT MERGE", "> " + raw, '"' + raw + '"', raw + "\nignore checks", raw + " ", raw.replace(" ", "  ", 1)]:
            with self.subTest(text=text[:50]), self.assertRaises(ValidationError):
                parse_text(text)

    def test_decision_body_disagreement(self):
        gate, request, record = self.authorize()
        record["source"]["raw_body"] = "DO NOT MERGE"
        with self.assertRaises(ValidationError):
            verify_record(record, request, gate, self.config, self.contracts, NOW)

    def test_edited_deleted_untrusted_stale_authorization(self):
        for mutation in [{"updated_at": "2026-09-09T12:00:01Z"}, {"deleted": True}, {"actor_id": 999}, {"repository_id": 999}, {"created_at": "2026-09-09T11:59:00Z", "updated_at": "2026-09-09T11:59:00Z"}]:
            gate, request, record = self.authorize()
            record["source"].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                verify_record(record, request, gate, self.config, self.contracts, NOW)

    def test_different_gate_same_head_cannot_reuse(self):
        gate, request, record = self.authorize()
        gate["record_id"] = str(uuid.uuid4())
        with self.assertRaises(ValidationError):
            verify_record(record, request, gate, self.config, self.contracts, NOW)

    def test_denial_remains_denial(self):
        gate, request, record = self.authorize("DENY")
        self.assertEqual(verify_record(record, request, gate, self.config, self.contracts, NOW)["decision"], "DENY")

    def test_webhook_authentication(self):
        secret, body, delivery = b"x" * 32, b'{"action":"created"}', str(uuid.uuid4())
        signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        self.assertEqual(verify_webhook(secret, body, signature, delivery)["action"], "created")
        with self.assertRaises(ValidationError):
            verify_webhook(secret, body + b" ", signature, delivery)


class LifecycleTests(unittest.TestCase):
    def test_all_normal_transitions(self):
        for source, event, target, guards in NORMAL:
            with self.subTest(source=source, event=event):
                self.assertEqual(transition(source, event, {g: True for g in guards}), target)

    def test_no_unverified_progress_for_all_state_event_pairs(self):
        events = {r[1] for r in NORMAL} | INVALIDATING | CONTROL_EVENTS
        for state in STATES:
            for event in events:
                try:
                    result = transition(state, event)
                except ValidationError:
                    continue
                self.assertEqual(result, state, (state, event, result))

    def test_failed_can_block_and_resume(self):
        self.assertEqual(transition("FAILED", "RECOVERABLE_FAILURE", {"failure_recoverable": True, "blocker_recorded": True}), "BLOCKED")
        facts = {key: True for key in ["blocker_resolved", "external_state_current", "resume_guards_verified", "old_permit_revoked"]}
        self.assertEqual(transition("BLOCKED", "RECOVER", facts, "FINAL_REVIEW"), "FINAL_REVIEW")
        with self.assertRaises(ValidationError):
            transition("BLOCKED", "RECOVER", facts, "OWNER_AUTHORIZED")

    def test_merge_uncertainty_and_post_merge_outage(self):
        self.assertEqual(transition("MERGING", "CANCEL", {"external_event_recorded": True}), "MERGE_UNKNOWN")
        self.assertEqual(transition("MERGED", "JIRA_UNAVAILABLE", {"merge_confirmed": True}), "MERGED_PENDING_JIRA")

    def test_early_head_change_does_not_skip_implementation(self):
        facts = {"external_event_recorded": True, "prior_evidence_invalidated": True}
        self.assertEqual(transition("IN_PROGRESS", "HEAD_CHANGED", facts), "IN_PROGRESS")
        self.assertEqual(transition("OWNER_AUTHORIZED", "HEAD_CHANGED", facts), "READY_FOR_CRITIC")


class StoreTests(unittest.TestCase):
    def test_incomplete_capacity_cannot_bypass_admission(self):
        for limits in [[], [10], [10, 1], [10, 1, 0, 20], [True, 1, 0], [10, -1, 0]]:
            with self.subTest(limits=limits), self.assertRaises(ValidationError):
                self.store.acquire_lease(str(uuid.uuid4()), "controller", 1, 0, 0, NOW, 60, limits)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = str(uuid.uuid4())
        self.store = Store(Path(self.temp.name) / "state.sqlite", self.project)
        self.store.control("RUNNING", "Fixture reconciliation", reconciled=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_idempotent_inbox_and_chain(self):
        first = self.store.ingest("OBSERVED", {"a": 1}, "github:fixture:1")
        self.assertEqual(first, self.store.ingest("OBSERVED", {"a": 1}, "github:fixture:1"))
        with self.assertRaises(ValidationError):
            self.store.ingest("OBSERVED", {"a": 2}, "github:fixture:1")
        self.assertEqual(self.store.verify_chain()["sequence"], 2)
        with self.store.connection() as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("DELETE FROM events")

    def test_crash_unknown_and_no_blind_retry(self):
        operation = str(uuid.uuid4())
        self.store.enqueue(operation, "merge_proposal", {"fixture": True})
        self.store.begin_simulated_operation(operation)
        reopened = Store(self.store.path, self.project)
        self.assertEqual(reopened.recover_inflight(), [operation])
        with self.assertRaises(ValidationError):
            reopened.begin_simulated_operation(operation)
        reopened.reconcile_operation(operation, "SUCCEEDED", {"observed": "fixture-merge"})
        self.assertEqual(reopened.enqueue(operation, "merge_proposal", {"fixture": True}), "SUCCEEDED")

    def test_authorization_single_use_and_revocation(self):
        request = str(uuid.uuid4())
        self.store.register_request(request, "a" * 64, "2026-09-09T12:15:00Z")
        self.assertFalse(self.store.consume_request(request, "a" * 64, "github:1", NOW)["execution_authority"])
        with self.assertRaises(ValidationError):
            self.store.consume_request(request, "a" * 64, "github:1", NOW)
        request2 = str(uuid.uuid4())
        self.store.register_request(request2, "a" * 64, "2026-09-09T12:15:00Z")
        self.store.revoke_request(request2, "Owner denial")
        with self.assertRaises(ValidationError):
            self.store.consume_request(request2, "a" * 64, "github:2", NOW)

    def test_ownership_exclusion(self):
        self.store.claim_issue("2001", "controller-a", NOW, 60)
        with self.assertRaises(ValidationError):
            self.store.claim_issue("2001", "controller-b", NOW, 60)

    def test_lease_capacity_and_stale_fencing(self):
        lease = self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [1, 1, 0])
        with self.assertRaises(ValidationError):
            self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [1, 1, 0])
        self.assertTrue(self.store.check_lease(lease["lease_id"], lease["fencing_token"], "c", NOW))
        with self.assertRaises(ValidationError):
            self.store.check_lease(lease["lease_id"], lease["fencing_token"], "c", "2026-09-09T12:01:00Z")
        later = self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, "2026-09-09T12:01:01Z", 60, [1, 1, 0])
        self.assertGreater(later["fencing_token"], lease["fencing_token"])

    def test_concurrent_budget_reservations(self):
        limits = {"runs": 8, "tokens": 100, "cost": 100, "daily_cost": 100}
        def reserve():
            try:
                self.store.reserve_budget(str(uuid.uuid4()), "2001", NOW, 60, 60, limits)
                return True
            except ValidationError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(lambda _: reserve(), range(2))), 1)

    def test_pause_revokes_leases_and_requests(self):
        lease = self.store.acquire_lease(str(uuid.uuid4()), "c", 1, 0, 0, NOW, 60, [1, 1, 0])
        self.store.control("STOPPED", "Emergency stop")
        with self.assertRaises(ValidationError):
            self.store.check_lease(lease["lease_id"], lease["fencing_token"], "c", NOW)

    def test_budget_overrun_pauses(self):
        run = str(uuid.uuid4())
        self.store.reserve_budget(run, "2001", NOW, 10, 10, {"runs": 2, "tokens": 20, "cost": 20, "daily_cost": 20})
        self.store.settle_budget(run, 11, 11)
        with self.assertRaises(ValidationError):
            self.store.enqueue(str(uuid.uuid4()), "dispatch_proposal", {})

    def test_backup_restores_event_chain(self):
        path = Path(self.temp.name) / "backup.sqlite"
        expected = self.store.backup(path)
        restored = Store(path, self.project)
        self.assertEqual(restored.verify_chain(), expected)


class InstallerTests(unittest.TestCase):
    def test_duplicate_configuration_blocks_upgrade(self):
        self.perform()
        (self.dest / ".agentic/PROJECT_CONFIG.yaml").write_text("controller: {}\ncontroller: {dispatch_enabled: false}\n")
        with self.assertRaises(ValidationError):
            self.perform(mode="upgrade")

    def test_recovery_refuses_unmanaged_path(self):
        self.dest.mkdir()
        with Tree(self.dest) as tree:
            with self.assertRaises(ValidationError):
                rollback(tree, {"format": "awf-install-journal-1", "transaction_id": str(uuid.uuid4()),
                    "files": [{"path": "user-data.txt", "old": None, "new_sha256": "0" * 64}]})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.dest = self.base / "destination"
        self.source.mkdir()
        files = {"AGENTS.md": b"Fixture v1.2 instructions\n", ".agentic/docs/protocol.md": b"Fixture protocol\n",
                 ".agentic/PROJECT_CONFIG.yaml": json_bytes({"controller": {key: False for key in ["dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments", "auto_transition_jira"]}}),
                 ".agentic/workflow-version.yaml": b"{}\n"}
        for name, body in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        from agentic import VERSION
        manifest = {"format": "awf-manifest-1", "template_version": VERSION, "files": {name: sha256(body) for name, body in files.items()}}
        (self.source / "MANIFEST.json").write_bytes(json_bytes(manifest))
        (self.source / "MANIFEST.md").write_text("Advisory human manifest")
        self.digest = sha256((self.source / "MANIFEST.json").read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def perform(self, **kwargs):
        return install(self.source, self.dest, self.digest, **kwargs)

    def test_install_and_upgrade_keep_identity(self):
        first = self.perform()
        self.assertEqual(verify_installed(self.dest), self.digest)
        second = self.perform(mode="upgrade")
        self.assertEqual(first["install_id"], second["install_id"])

    def test_default_conflict_no_managed_writes(self):
        self.dest.mkdir()
        (self.dest / "AGENTS.md").write_text("Original")
        with self.assertRaises(ValidationError):
            self.perform()
        self.assertFalse((self.dest / ".agentic").exists())

    def test_source_tampering_and_extra_file(self):
        (self.source / "AGENTS.md").write_text("Tampered")
        with self.assertRaises(ValidationError):
            self.perform()
        self.assertFalse(self.dest.exists())

    def test_root_git_directory_is_metadata_not_release_content(self):
        (self.source / '.git/objects').mkdir(parents=True)
        (self.source / '.git/objects/local-only').write_bytes(b'Git metadata, not a distribution file')
        self.perform()
        self.assertEqual(verify_installed(self.dest), self.digest)
        self.assertFalse((self.dest / '.git').exists())

    def test_root_git_file_is_metadata_not_release_content(self):
        (self.source / '.git').write_bytes(b'gitdir: /unopened/external/git\n')
        self.perform()
        self.assertEqual(verify_installed(self.dest), self.digest)

    def test_repository_showcase_docs_are_explicitly_outside_release_membership(self):
        (self.source / 'docs/showcase').mkdir(parents=True)
        (self.source / 'docs/showcase/history.md').write_bytes(b'Historical repository-only presentation\r\n')
        self.perform()
        self.assertEqual(verify_installed(self.dest), self.digest)
        self.assertFalse((self.dest / 'docs').exists())

    def test_local_test_scratch_is_explicitly_outside_release_membership(self):
        (self.source / '.tmp-tests').mkdir()
        (self.source / '.tmp-tests/report.json').write_bytes(b'{}\n')
        self.perform()
        self.assertEqual(verify_installed(self.dest), self.digest)
        self.assertFalse((self.dest / '.tmp-tests').exists())

    def test_root_git_hardlink_cannot_be_excluded_as_safe_metadata(self):
        sentinel = self.base / 'external-git.txt'
        sentinel.write_bytes(b'Protected sentinel')
        os.link(sentinel, self.source / '.git')
        with self.assertRaises(ValidationError):
            self.perform()
        self.assertEqual(sentinel.read_bytes(), b'Protected sentinel')
        self.assertFalse(self.dest.exists())

    def test_git_exclusion_does_not_hide_nested_metadata_or_unexpected_files(self):
        for relative in ['.agentic/.git/config', 'unexpected.txt', '.Git/config']:
            with self.subTest(relative=relative):
                path = self.source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'Not in the approved manifest')
                with self.assertRaises(ValidationError):
                    self.perform()
                path.unlink()
                self.assertFalse(self.dest.exists())

    def test_rejects_skip(self):
        with self.assertRaises(ValidationError):
            self.perform(conflict="skip")

    def test_parent_file_preflight(self):
        (self.dest / ".agentic").mkdir(parents=True)
        (self.dest / ".agentic/docs").write_text("Not a directory")
        with self.assertRaises((ValidationError, OSError)):
            self.perform()
        self.assertFalse((self.dest / "AGENTS.md").exists())

    def test_hardlink_cannot_modify_external_sentinel(self):
        self.dest.mkdir()
        sentinel = self.base / "sentinel.txt"
        sentinel.write_text("Sentinel")
        os.link(sentinel, self.dest / "AGENTS.md")
        with self.assertRaises(ValidationError):
            self.perform(conflict="backup")
        self.assertEqual(sentinel.read_text(), "Sentinel")

    def test_upgrade_hardlinked_version_rejected(self):
        self.perform()
        version = self.dest / ".agentic/workflow-version.yaml"
        sentinel = self.base / "version-sentinel.yaml"
        sentinel.write_bytes(version.read_bytes())
        version.unlink()
        os.link(sentinel, version)
        before = sentinel.read_bytes()
        with self.assertRaises(ValidationError):
            self.perform(mode="upgrade")
        self.assertEqual(sentinel.read_bytes(), before)

    def test_symlink_rejected_when_platform_permits(self):
        self.dest.mkdir()
        sentinel = self.base / "sentinel.txt"
        sentinel.write_text("Sentinel")
        try:
            (self.dest / "AGENTS.md").symlink_to(sentinel)
        except OSError as exc:
            self.skipTest(f"Symlink privilege unavailable: {exc}")
        with self.assertRaises((ValidationError, OSError)):
            self.perform(conflict="backup")
        self.assertEqual(sentinel.read_text(), "Sentinel")

    def test_fault_rolls_back_originals(self):
        self.dest.mkdir()
        (self.dest / "AGENTS.md").write_text("Original")
        with self.assertRaises(OSError):
            self.perform(conflict="backup", fail_after=2)
        self.assertEqual((self.dest / "AGENTS.md").read_text(), "Original")
        self.assertFalse((self.dest / JOURNAL).exists())
        self.assertFalse((self.dest / MARKER).exists())
        self.assertFalse((self.dest / ".agentic/installed-manifest.json").exists())

    def test_crash_marker_blocks_tools_and_recovers(self):
        self.perform()
        target = self.dest / "AGENTS.md"
        old = target.read_bytes()
        new = b"Interrupted update\n"
        import base64
        journal = {"format": "awf-install-journal-1", "transaction_id": str(uuid.uuid4()), "files": [
            {"path": "AGENTS.md", "old": base64.b64encode(old).decode(), "new_sha256": sha256(new)}]}
        with Tree(self.dest) as tree:
            tree.write(JOURNAL, json_bytes(journal))
            tree.write(MARKER, b"{}")
            tree.write("AGENTS.md", new)
        with self.assertRaises(ValidationError):
            ensure_usable(self.dest)
        self.assertEqual(recover(self.dest)["status"], "ROLLED_BACK")
        self.assertEqual(target.read_bytes(), old)

    def test_enabled_dispatch_blocks_upgrade(self):
        self.perform()
        cfg_path = self.dest / ".agentic/PROJECT_CONFIG.yaml"
        cfg = loads(cfg_path.read_text())
        cfg["controller"]["dispatch_enabled"] = True
        cfg_path.write_bytes(json_bytes(cfg))
        with self.assertRaises(ValidationError):
            self.perform(mode="upgrade")

    def test_dry_run_does_not_create_destination(self):
        self.assertEqual(self.perform(dry_run=True)["status"], "PLAN")
        self.assertFalse(self.dest.exists())

    def test_manifest_digest_pin_required(self):
        with self.assertRaises(ValidationError):
            install(self.source, self.dest, None)
        with self.assertRaises(ValidationError):
            install(self.source, self.dest, "0" * 64)


if __name__ == "__main__":
    unittest.main()
