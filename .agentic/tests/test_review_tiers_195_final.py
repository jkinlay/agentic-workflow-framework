"""AWF-16 final over-cap regressions for the combined 1.9.5 candidate."""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint, load_yaml
from agentic.gates import critic_artifact_receipt_sha256, evaluate, expected_binding
from agentic.lifecycle import definition, review_round_transition, transition
from agentic.policy import validate_config
from agentic.review_tiers import review_decision, validate_round
from agentic.upgrade import review_tier_defaults
from test_review_policy import (Fixture, NOW, bind_production_posting_fixture,
                                 bind_review_admission, bind_review_round_receipt,
                                 sign, signed)


POSTING_FIELDS = (
    "source", "observed_at", "producer_id", "run_id", "comment_url",
    "body_link", "comment_sha256", "body_sha256",
)


class ReviewTiers195FinalTests(Fixture):
    def _bind_terminal_artifact(self):
        verdict = self.bundle["review_verdicts"][-1]
        critic = self.bundle["critic"]
        verdict["critic_review"].update(
            record_id=critic["record_id"],
            run_id=critic["run_id"],
            round=verdict["round"],
            head_sha=verdict["head_sha"],
            verdict=critic["verdict"],
            findings_sha256=fingerprint("critic-findings", critic["findings"]),
        )
        uri = f"urn:awf:critic-review:{critic['record_id']}"
        if uri not in verdict["evidence"]:
            verdict["evidence"].append(uri)
        self.bundle["evidence_registry"] = [
            entry for entry in self.bundle["evidence_registry"] if entry["uri"] != uri
        ]
        self.bundle["evidence_registry"].append({
            "uri": uri,
            "sha256": critic_artifact_receipt_sha256(verdict),
            "producer_id": verdict["producer_id"],
            "retained_until": "2030-01-01T00:00:00Z",
        })
        return verdict

    def _legacy_terminal_receipt(self):
        verdict = self._bind_terminal_artifact()
        critic_uri = f"urn:awf:critic-review:{verdict['critic_review']['record_id']}"
        verdict["evidence"].remove(critic_uri)
        self.bundle["evidence_registry"] = [
            entry for entry in self.bundle["evidence_registry"] if entry["uri"] != critic_uri
        ]
        run = next(run for run in self.bundle["runs"] if run["run_id"] == verdict["run_id"])
        verdict["critic_review"]["record_id"] = run["record_id"]
        return verdict

    def _tier_three_candidate_with_owner_review(self):
        path = "src/release_gate.py"
        self.bundle["worker"]["files_changed"] = [path]
        self.bundle["pr"]["file_manifest"] = [{"path": path, "blob_sha": "1" * 40}]
        self.bundle["critic"]["coverage"].update(
            file_manifest_sha256=fingerprint("file-manifest", self.bundle["pr"]["file_manifest"]),
            reviewed_paths=[path],
        )
        self.bundle["contract"]["scope"]["expected_paths"] = [path]
        self.bundle["contract"].update(
            risk_tier=3, tier_justification="Release gate escalation."
        )
        self.rebind()
        verifier = self.verifier_run()
        owner = copy.deepcopy(self.bundle["review_verdicts"][-1])
        owner.update(record_id=str(uuid.uuid4()), producer_id=verifier["producer_id"],
                     run_id=verifier["run_id"], reviewer_id="fixture-owner",
                     owner_review=True, owner_id=1001)
        owner["evidence"] = ["urn:awf:fixture:example-evidence"]
        self.bundle["owner_review"] = owner

    def _review_history(self, rounds):
        template_verdict = copy.deepcopy(self.bundle["review_verdicts"][0])
        template_run = copy.deepcopy(next(
            run for run in self.bundle["runs"] if run["run_id"] == template_verdict["run_id"]
        ))
        template_artifact = copy.deepcopy(self.bundle["critic"])
        current_binding = copy.deepcopy(template_verdict["binding"])
        current_head = self.bundle["candidate"]["head_sha"]
        self.bundle["runs"] = [
            run for run in self.bundle["runs"] if run["run_id"] != template_run["run_id"]
        ]
        self.bundle["evidence_registry"] = [
            entry for entry in self.bundle["evidence_registry"]
            if not (entry["uri"].endswith("/posting") or "/posting-" in entry["uri"]
                    or entry["uri"].startswith("urn:awf:critic-review:"))
        ]
        verdicts = []
        artifacts = []
        for round_number in range(1, rounds + 1):
            terminal = round_number == rounds
            run = copy.deepcopy(template_run)
            artifact = copy.deepcopy(template_artifact)
            verdict = copy.deepcopy(template_verdict)
            run.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
                       producer_id=f"fixture-critic-{round_number}",
                       context_id=str(uuid.uuid4()))
            artifact["record_id"] = str(uuid.uuid4())
            verdict["record_id"] = str(uuid.uuid4())
            if terminal:
                binding = copy.deepcopy(current_binding)
                head_sha = current_head
                created_at = NOW
            else:
                old_candidate = copy.deepcopy(self.bundle["candidate"])
                digit = format(round_number, "x")
                old_candidate.update(head_sha=digit * 40, head_tree_sha=digit * 40,
                                     diff_sha256=digit * 64)
                binding = copy.deepcopy(current_binding)
                binding["candidate_id"] = fingerprint("candidate", old_candidate)
                binding["contract_hash"] = digit * 64
                head_sha = old_candidate["head_sha"]
                created_at = f"2020-01-0{round_number}T00:00:00Z"
            run.update(binding=copy.deepcopy(binding), created_at=created_at)
            artifact.update(run_id=run["run_id"], producer_id=run["producer_id"],
                            binding=copy.deepcopy(binding), created_at=created_at)
            verdict.update(run_id=run["run_id"], producer_id=run["producer_id"],
                           reviewer_id=run["producer_id"], binding=copy.deepcopy(binding),
                           created_at=created_at, round=round_number, tier=2,
                           head_sha=head_sha)
            verdict["candidate_binding"] = {
                **verdict["candidate_binding"], "head_sha": head_sha,
            }
            verdict["pr_comment_url"] = (
                f"https://github.com/fixture/example/pull/7#issuecomment-{round_number}"
            )
            verdict["pr_body_link"] = (
                f"https://github.com/fixture/example/pull/7#review-verdict-{round_number}"
            )
            observation = copy.deepcopy(verdict["posting_observation"])
            observation.update(observed_at=created_at,
                               comment_url=verdict["pr_comment_url"],
                               body_link=verdict["pr_body_link"])
            observation["observation_sha256"] = fingerprint(
                "posting-observation", {key: observation.get(key) for key in POSTING_FIELDS}
            )
            verdict["posting_observation"] = observation
            verdict["critic_review"] = {
                "record_id": artifact["record_id"],
                "run_id": run["run_id"],
                "round": round_number,
                "head_sha": head_sha,
                "verdict": artifact["verdict"],
                "findings_sha256": fingerprint("critic-findings", artifact["findings"]),
            }
            artifact_uri = f"urn:awf:critic-review:{artifact['record_id']}"
            artifact["evidence_checked"].append(artifact_uri)
            verdict["evidence"] = [
                uri for uri in verdict["evidence"]
                if not uri.startswith("urn:awf:critic-review:")
            ]
            verdict["evidence"].append(artifact_uri)
            self.bundle["evidence_registry"].extend(({
                "uri": artifact_uri,
                "sha256": critic_artifact_receipt_sha256(verdict),
                "producer_id": verdict["producer_id"],
                "retained_until": "2030-01-01T00:00:00Z",
            }, {
                "uri": f"urn:awf:fixture:example-evidence/posting-{round_number}",
                "sha256": observation["observation_sha256"],
                "producer_id": observation["producer_id"],
                "retained_until": "2030-01-01T00:00:00Z",
            }))
            self.bundle["runs"].append(run)
            verdicts.append(verdict)
            artifacts.append(artifact)
        self.bundle["critic"] = artifacts[-1]
        self.bundle["review_verdicts"] = verdicts
        self.bundle["review_round_receipts"] = []
        for verdict, artifact in zip(verdicts, artifacts):
            bind_review_round_receipt(self.bundle, verdict, artifact)

    def test_awf16_r2c_002_tier1_empty_legacy_receipt_is_not_ready(self):
        self.tier1_bundle()
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        self._legacy_terminal_receipt()
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_002_tier2_empty_legacy_receipt_is_not_ready(self):
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        self._legacy_terminal_receipt()
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_002_tier3_empty_legacy_receipt_is_not_ready(self):
        self._tier_three_candidate_with_owner_review()
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        self._legacy_terminal_receipt()
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_002_cap_verdict_and_hash_mismatch_is_not_ready(self):
        finding = self.finding("AWF16-CAP", "MAJOR", {"criterion_id": "AC2"})
        self.bundle["critic"].update(verdict="REQUEST_CHANGES", findings=[finding])
        verdict = self._legacy_terminal_receipt()
        verdict["critic_review"].update(verdict="APPROVE", findings_sha256="f" * 64)
        cap = self.record(decision="MERGE_WITH_NOTES", open_finding_ids=[finding["id"]],
                          notes="owner accepts", cycles=3, cap_extensions=0,
                          successor_ticket=None, **signed())
        self.bundle["cap_disposition"] = sign(cap, "cap", self.bundle["candidate"]["head_sha"])
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_004_legacy_security_input_is_rejected_without_mutation(self):
        self.bundle["contract"]["risk_flags"]["security"] = True
        self.rebind()
        self.bundle["contract"]["risk_classification"]["risk_flags"] = []
        self.bundle["tier_classification"]["risk_flags"] = []
        binding = expected_binding(self.config, definition(), self.bundle)
        for key in ("dispatch", "worker", "critic", "ci", "pr"):
            self.bundle[key]["binding"] = copy.deepcopy(binding)
        for run in self.bundle["runs"]:
            run["binding"] = copy.deepcopy(binding)
        for verdict in self.bundle["review_verdicts"]:
            verdict["binding"] = copy.deepcopy(binding)
        bind_production_posting_fixture(self.config, self.bundle)
        bind_review_admission(self.bundle)
        before_contract = copy.deepcopy(self.bundle["contract"])
        before_bundle = copy.deepcopy(self.bundle)
        with self.assertRaisesRegex(ValidationError, "risk_classification|tier_classification"):
            evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual(before_contract, self.bundle["contract"])
        self.assertEqual(before_bundle, self.bundle)

    def test_awf16_r1_003_self_consistent_fabricated_posting_is_not_ready(self):
        self._bind_terminal_artifact()
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        verdict = self.bundle["review_verdicts"][-1]
        verdict["pr_comment_url"] = "https://github.com/fixture/example/pull/7#fabricated-comment"
        verdict["pr_body_link"] = "https://github.com/fixture/example/pull/7#fabricated-body"
        observation = verdict["posting_observation"]
        observation.update(comment_url=verdict["pr_comment_url"],
                           body_link=verdict["pr_body_link"])
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r1_003_fixture_collector_cannot_satisfy_production_gate(self):
        self._bind_terminal_artifact()
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r1_003_unregistered_collector_keeps_gate_not_ready(self):
        self._bind_terminal_artifact()
        bind_production_posting_fixture(self.config, self.bundle)
        self.config["merge_gate"]["production_posting_collector_ids"] = []
        self.rebind()
        bind_review_admission(self.bundle)
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_195_r4c_001_historical_posting_urls_bind_to_own_verdict(self):
        self._review_history(2)
        historical = self.bundle["review_verdicts"][0]
        observation = historical["posting_observation"]
        observation.update(comment_url="https://example.invalid/unrelated-comment",
                           body_link="https://example.invalid/unrelated-body")
        gate = self.gate()
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_195_r3_003_schema_and_policy_reject_tier3_cap_above_three(self):
        self.config["execution"]["risk_tiers"]["tier3_review"]["max_rounds"] = 4
        with self.assertRaises(ValidationError):
            self.contracts.validate("project-config", self.config)
        with self.assertRaises(ValidationError):
            validate_config(self.config, definition(), self.contracts)

    def test_awf16_195_r3_003_tier3_round_four_refused_with_owner_extension(self):
        disposition = {
            "decision": "EXTEND_ONE_CYCLE", "record_id": "owner-record",
            "created_at": NOW, "producer_id": "verifier", "run_id": "run",
            "binding": {}, "open_finding_ids": [], "notes": "extend", "cycles": 3,
            "cap_extensions": 1, "successor_ticket": None,
            "authorization_request_id": "request",
            "owner_source": {"channel": "github_pr_comment", "comment_id": 1},
            "evidence": ["urn:awf:fixture:evidence"],
        }
        with self.assertRaises(ValidationError):
            validate_round(3, 4, owner_cap_disposition=disposition)
        with self.assertRaises(ValidationError):
            review_decision(3, 4, owner_cap_disposition=disposition)
        with self.assertRaises(ValidationError):
            review_round_transition(3, 4, owner_cap_disposition=disposition,
                                    owner_cap_verified=True)
        with self.assertRaises(ValidationError):
            transition("REVIEW_CAP_REACHED", "CAP_EXTEND_ONE_CYCLE", {
                "cap_disposition_verified": True,
                "cap_extension_available": True,
                "risk_tier": 3,
            })
        self.assertEqual("CHANGES_REQUESTED", transition(
            "REVIEW_CAP_REACHED", "CAP_EXTEND_ONE_CYCLE", {
                "cap_disposition_verified": True,
                "cap_extension_available": True,
                "risk_tier": 2,
            }))

    def test_awf16_r2_015_001_approval_bound_round_four_reaches_real_gate(self):
        self._review_history(4)
        cap = self.record(
            decision="EXTEND_ONE_CYCLE",
            open_finding_ids=[],
            notes="AWF-OVERCAP-APPROVAL",
            cycles=3,
            cap_extensions=1,
            successor_ticket=None,
            **signed(),
        )
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        terminal = self.bundle["review_verdicts"][-1]
        receipt = self.bundle["review_round_receipts"][-1]
        cap.update(
            critic_artifact_binding=copy.deepcopy(terminal["critic_artifact_binding"]),
            review_verdict_record_id=terminal["record_id"],
            review_verdict_sha256=receipt["review_verdict_sha256"],
        )
        self.bundle["cap_disposition"] = sign(
            cap, "cap", self.bundle["candidate"]["head_sha"]
        )

        self.contracts.validate("evidence-bundle", self.bundle)
        gate = evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry=registry,
        )
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertEqual(4, gate["verified_critic_artifact_bindings"][-1]["round"])

    def test_awf16_r2_015_001_unbound_and_tier3_overcap_rounds_are_rejected(self):
        self._review_history(4)
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        with self.assertRaisesRegex(ValidationError, "owner cap disposition"):
            evaluate(
                self.config,
                definition(),
                self.bundle,
                self.contracts,
                NOW,
                posting_collector_registry=registry,
            )

        for round_number in (4, 5, 99):
            with self.subTest(tier=3, round=round_number), self.assertRaisesRegex(
                    ValidationError, "Tier 3"):
                validate_round(3, round_number)

    def test_awf16_195_r3_002_unsupported_yaml_styles_are_refused(self):
        cases = {
            "quoted": b"template:\n  expected_workflow_version: 1.9.4\n'execution':\n  risk_tiers: {}\n",
            "multiline-flow": (b"template:\n  expected_workflow_version: 1.9.4\nexecution: {\n"
                               b"  risk_tiers: {tier1_review: {max_rounds: 1}}\n}\n"),
            "explicit-key": (b"template:\n  expected_workflow_version: 1.9.4\n? execution\n: \n"
                             b"  risk_tiers: {}\n"),
        }
        for style, before in cases.items():
            with self.subTest(style=style), self.assertRaisesRegex(
                    ValidationError, "unsupported YAML mapping style"):
                review_tier_defaults(before)

    def test_awf16_195_r3_002_supported_yaml_output_is_reparsed_and_exact(self):
        before = (b"template:\n  expected_workflow_version: 1.9.4\nexecution:\n"
                  b"  risk_tiers:\n    tier1_review: {max_rounds: 1}\n")
        after = review_tier_defaults(before)
        parsed = load_yaml(after)
        self.assertEqual({"roles": ["critic", "specialist"], "findings": "blocking",
                          "max_rounds": 3},
                         parsed["execution"]["risk_tiers"]["tier3_review"])

    def test_awf16_r1_009_schema_requires_every_review_verdict(self):
        del self.bundle["review_verdicts"]
        with self.assertRaises(ValidationError):
            self.contracts.validate("evidence-bundle", self.bundle)

    def test_awf16_r1_009_arbitrary_findings_digest_fails_real_gate(self):
        verdict = self._bind_terminal_artifact()
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        verdict["critic_review"]["findings_sha256"] = "f" * 64
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])

    def test_awf16_r1_009_historical_old_candidate_run_binding_is_retained(self):
        self._review_history(2)
        first = self.bundle["review_verdicts"][0]
        first_run = next(run for run in self.bundle["runs"] if run["run_id"] == first["run_id"])
        self.assertEqual(first["binding"], first_run["binding"])
        first_run["binding"]["candidate_id"] = "f" * 64
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_r1_009_pre_cap_tier2_p2_continues(self):
        result = review_decision(
            2, 1, latest_pass=True,
            open_findings=[{"id": "OPEN-P2", "severity": "MINOR"}],
        )
        self.assertEqual("CONTINUE", result["status"])

    def test_awf16_r1_009_canonical_boundary_shape_blocks(self):
        result = review_decision(
            1, 1, latest_pass=True,
            open_findings=[{"id": "BOUNDARY", "severity": "MINOR",
                            "basis": {"boundary_code": "SCOPE_ESCAPE"}}],
        )
        self.assertEqual("BLOCKED", result["status"])


if __name__ == "__main__":
    import unittest
    unittest.main()
