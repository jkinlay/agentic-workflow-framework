"""AWF-16 1.9.5 final-gate regressions from the split #70 review."""
from __future__ import annotations

import base64
import copy
import contextlib
import io
import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, sha256
from agentic.cli import main as workflow_main
from agentic.gates import (cap_disposition_record_id,
                           critic_artifact_receipt_sha256, evaluate,
                           posting_collector_receipt_sha256,
                           review_round_receipt_sha256,
                           verified_owner_records)
from agentic.intake_assignment import assign_intake, change_assignment
from agentic.lifecycle import definition
from agentic.review_tiers import review_decision
from test_intake_assignment import owner_source, verify_owner_source
from test_review_policy import (Fixture, NOW, bind_production_posting_fixture,
                                bind_review_admission, bind_review_round_receipt,
                                sealed_runtime)


class ReviewTiers195GateTests(Fixture):
    def _runtime_config(self, assignments, *, active_ticket="EX-1"):
        runtime = {"active_ticket_key": active_ticket,
                   "intake_assignments": assignments}
        provenance_runtime = self.config.get("_awf_runtime", {})
        if "commit_provenance" in provenance_runtime:
            runtime.update(commit_provenance=provenance_runtime["commit_provenance"],
                           repository=provenance_runtime["repository"])

        class ReviewedConfigWithRuntime(dict):
            def get(self, key, default=None):
                if key == "_awf_runtime":
                    return runtime
                return super().get(key, default)

        return ReviewedConfigWithRuntime(copy.deepcopy(self.config))

    def _assignment(self, tier, cap, assignment_id="fixture-assignment"):
        record = {"assignment_id": assignment_id, "ticket_key": "EX-1",
                  "tier": tier, "round_cap": cap,
                  "owner_review_required": tier == 3}
        record["assignment_sha256"] = fingerprint("intake_assignment_record", record)
        return record

    def _review_history(self, rounds, *, historical_tier=2, terminal_tier=2):
        """Retain real old-candidate review artifacts and end on the current candidate."""
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
        runs = []
        for round_number in range(1, rounds + 1):
            terminal = round_number == rounds
            run = copy.deepcopy(template_run)
            artifact = copy.deepcopy(template_artifact)
            verdict = copy.deepcopy(template_verdict)
            if round_number > 1:
                run.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
                           producer_id=f"fixture-critic-{round_number}",
                           context_id=str(uuid.uuid4()))
                artifact["record_id"] = str(uuid.uuid4())
                verdict["record_id"] = str(uuid.uuid4())

            if terminal:
                binding = copy.deepcopy(current_binding)
                head_sha = current_head
                created_at = NOW
                tier = terminal_tier
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
                tier = historical_tier

            run.update(binding=copy.deepcopy(binding), created_at=created_at)
            artifact.update(run_id=run["run_id"], producer_id=run["producer_id"],
                            binding=copy.deepcopy(binding), created_at=created_at)
            verdict.update(run_id=run["run_id"], producer_id=run["producer_id"],
                           reviewer_id=run["producer_id"], binding=copy.deepcopy(binding),
                           created_at=created_at, round=round_number, tier=tier,
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
                "posting-observation", {key: observation.get(key) for key in (
                    "source", "observed_at", "producer_id", "run_id", "comment_url",
                    "body_link", "comment_sha256", "body_sha256")}
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
            self.bundle["evidence_registry"].append({
                "uri": artifact_uri,
                "sha256": critic_artifact_receipt_sha256(verdict),
                "producer_id": verdict["producer_id"],
                "retained_until": "2030-01-01T00:00:00Z",
            })
            self.bundle["evidence_registry"].append({
                "uri": f"urn:awf:fixture:example-evidence/posting-{round_number}",
                "sha256": observation["observation_sha256"],
                "producer_id": observation["producer_id"],
                "retained_until": "2030-01-01T00:00:00Z",
            })
            runs.append(run)
            verdicts.append(verdict)
            artifacts.append(artifact)

        self.bundle["runs"].extend(runs)
        self.bundle["critic"] = artifacts[-1]
        self.bundle["review_verdicts"] = verdicts
        self.bundle["review_round_receipts"] = []
        for verdict, artifact in zip(verdicts, artifacts):
            bind_review_round_receipt(self.bundle, verdict, artifact)

    def _minor_finding(self):
        finding = self.finding("OPEN-P2", "MINOR", None)
        self.bundle["critic"]["findings"] = [finding]
        terminal = self.bundle["review_verdicts"][-1]
        terminal["critic_review"]["findings_sha256"] = fingerprint(
            "critic-findings", self.bundle["critic"]["findings"]
        )
        artifact_uri = f"urn:awf:critic-review:{terminal['critic_review']['record_id']}"
        artifact_entries = [entry for entry in self.bundle["evidence_registry"]
                            if entry["uri"] == artifact_uri]
        if artifact_entries:
            artifact_entries[0]["sha256"] = critic_artifact_receipt_sha256(terminal)
        return finding

    def _tier_three_candidate(self):
        path = "src/release_gate.py"
        self.bundle["worker"]["files_changed"] = [path]
        self.bundle["pr"]["file_manifest"] = [{"path": path, "blob_sha": "1" * 40}]
        self.bundle["critic"]["coverage"].update(
            file_manifest_sha256=fingerprint("file-manifest", self.bundle["pr"]["file_manifest"]),
            reviewed_paths=[path],
        )
        self.bundle["contract"]["scope"]["expected_paths"] = [path]
        self.bundle["contract"].update(risk_tier=3, tier_justification="Release gate escalation.")
        self.rebind()

    def _owner_review(self, *, provider_evidence=True):
        verifier = self.verifier_run()
        terminal = copy.deepcopy(self.bundle["review_verdicts"][-1])
        if provider_evidence:
            observation = self.bundle["review_verdicts"][-1]["posting_observation"]
            provider_response = json.loads(base64.b64decode(
                observation["provider_response_bytes"]["data"]
            ).decode("utf-8"))
            approval_id = 9001
            approval_url = (
                f"{self.bundle['candidate']['host']}/"
                f"{self.bundle['candidate']['repository']}/pull/"
                f"{self.bundle['candidate']['pr_number']}#pullrequestreview-{approval_id}"
            )
            provider_response["owner_approval"] = {
                "id": approval_id,
                "html_url": approval_url,
                "state": "APPROVED",
                "commit_id": self.bundle["candidate"]["head_sha"],
                "user": {"id": 1001},
                "candidate": copy.deepcopy(terminal["candidate_binding"]),
            }
            provider_bytes = canonical(provider_response)
            observation["provider_response_bytes"] = {
                "encoding": "base64",
                "data": base64.b64encode(provider_bytes).decode("ascii"),
            }
            observation["provider_response_sha256"] = sha256(provider_bytes)
            # Keep the owner record on the exact registered terminal observation;
            # the fixture binder assigns its collector receipt after this helper.
            terminal["posting_observation"] = observation
            terminal["pr_comment_url"] = approval_url
        terminal.update(record_id=str(uuid.uuid4()), producer_id=verifier["producer_id"],
                        run_id=verifier["run_id"], reviewer_id="fixture-owner",
                        owner_review=True, owner_id=1001)
        self.bundle["owner_review"] = terminal

    def _refresh_owner_posting_receipt(self, registry):
        observation = self.bundle["review_verdicts"][-1]["posting_observation"]
        registration = registry[observation["producer_id"]]
        basis = copy.deepcopy(observation)
        basis["collector_receipt_sha256"] = "0" * 64
        receipt_sha256 = posting_collector_receipt_sha256(
            observation, sha256(canonical(basis)), registration
        )
        observation["collector_receipt_sha256"] = receipt_sha256
        registration["receipts"][observation["run_id"]] = receipt_sha256
        receipt = self.bundle["review_round_receipts"][-1]
        posting_bytes = canonical(observation)
        receipt["posting_observation_json"] = {
            "encoding": "base64",
            "data": base64.b64encode(posting_bytes).decode("ascii"),
        }
        receipt["posting_observation_sha256"] = sha256(posting_bytes)
        entry = next(
            item for item in self.bundle["evidence_registry"]
            if item["uri"] == (
                "urn:awf:review-round-receipt:"
                f"{receipt['critic_artifact_binding']['round']}"
            )
        )
        entry["sha256"] = review_round_receipt_sha256(receipt)

    def _owner_gate(self, *, provider_evidence=True):
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        self._owner_review(provider_evidence=provider_evidence)
        if provider_evidence:
            self._refresh_owner_posting_receipt(registry)
        owner = self.bundle["owner_review"]
        self.bundle["evidence_registry"].append({
            "uri": f"urn:awf:owner-review:{owner['record_id']}",
            "sha256": sha256(canonical(owner)),
            "producer_id": owner["producer_id"],
            "retained_until": "2027-01-01T00:00:00Z",
        })
        return evaluate(
            self.config, definition(), self.bundle, self.contracts, NOW,
            posting_collector_registry=registry,
        )

    def _retain_historical_posting_body(self, body_sha256):
        verdict = self.bundle["review_verdicts"][0]
        receipt = self.bundle["review_round_receipts"][0]
        observation = verdict["posting_observation"]
        observation["body_bytes"] = (
            "Retained historical body.\n\n"
            f"review-verdict:1:{verdict['record_id']}:{receipt['review_verdict_sha256']}\n")
        from agentic.canonical import sha256
        observation["body_sha256"] = sha256(observation["body_bytes"].encode("utf-8"))

    def test_awf74_cap_disposition_uses_verified_ticket_assignment_cap(self):
        assignment = self._assignment(2, 2)
        cap = {
            "critic_artifact_binding": {"run_id": str(uuid.uuid4()),
                                         "result_sha256": "a" * 64,
                                         "head_sha": "b" * 40, "round": 2},
            "review_verdict_record_id": str(uuid.uuid4()),
            "review_verdict_sha256": "c" * 64,
            "authorization_request_id": str(uuid.uuid4()),
            "cycles": 2,
            "cap_extensions": 0,
            "successor_ticket": None,
            "owner_source": {"comment_id": 99},
        }
        cap["record_id"] = cap_disposition_record_id(cap)
        bundle = {"candidate": {"head_sha": "b" * 40},
                  "contract": {"ticket": "EX-1"},
                  "review_verdicts": [{"record_id": cap["review_verdict_record_id"]}],
                  "finding_dispositions": [], "cap_disposition": cap}
        with mock.patch("agentic.authorization.verify_owner_record"), \
                mock.patch("agentic.gates.verify_disposition_review_binding"):
            _, verified = verified_owner_records(
                self.config, self.contracts, bundle, {"policy_hash": "d" * 64},
                {}, set(), set(), NOW,
                assignment_record=assignment)
        self.assertIs(cap, verified)

    def test_awf74_cap_disposition_refuses_global_cap_fallback(self):
        cap = {
            "critic_artifact_binding": {"run_id": str(uuid.uuid4()),
                                         "result_sha256": "a" * 64,
                                         "head_sha": "b" * 40, "round": 2},
            "review_verdict_record_id": str(uuid.uuid4()),
            "review_verdict_sha256": "c" * 64,
            "authorization_request_id": str(uuid.uuid4()),
            "cycles": 2,
            "cap_extensions": 0,
            "successor_ticket": None,
            "owner_source": {"comment_id": 99},
        }
        cap["record_id"] = cap_disposition_record_id(cap)
        bundle = {"candidate": {"head_sha": "b" * 40},
                  "finding_dispositions": [], "cap_disposition": cap}
        with mock.patch("agentic.authorization.verify_owner_record"), \
                self.assertRaisesRegex(ValidationError, "verified per-ticket"):
            verified_owner_records(
                self.config, self.contracts, bundle, {}, {}, set(), set(), NOW
            )

    def test_awf74_recorded_tier_two_cap_two_refuses_round_three(self):
        assignment = self._assignment(2, 2)
        self.config = self._runtime_config({"EX-1": assignment})
        self._review_history(3)
        with self.assertRaisesRegex(ValidationError, "cap is 2"):
            self.gate()

    def test_awf74_gate_fails_closed_without_runtime_assignment(self):
        self.config = self._runtime_config({})
        self._review_history(3)
        with self.assertRaisesRegex(ValidationError, "runtime review assignment"):
            self.gate()

    def test_awf74_first_round_without_assignment_fails_closed(self):
        self.config = copy.deepcopy(self.config0)
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)

        with self.assertRaisesRegex(ValidationError, "runtime review assignment"):
            evaluate(
                self.config, definition(), self.bundle, self.contracts, NOW,
                posting_collector_registry=registry,
            )

    def test_awf74_gate_fails_closed_without_runtime_context(self):
        self.config = copy.deepcopy(self.config0)
        self._review_history(3)
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        with self.assertRaisesRegex(ValidationError, "runtime review assignment"):
            evaluate(
                self.config, definition(), self.bundle, self.contracts, NOW,
                posting_collector_registry=registry,
            )

    def test_awf74_gate_fails_closed_when_runtime_assignment_does_not_verify(self):
        assignment = self._assignment(2, 2)
        assignment["round_cap"] = 1
        self.config = self._runtime_config({"EX-1": assignment})
        with self.assertRaisesRegex(ValidationError, "digest"):
            self.gate()

    def test_awf48_r5_044_001_cli_keeps_historical_t2_and_current_t3(self):
        self._tier_three_candidate()
        self._review_history(2, historical_tier=2, terminal_tier=3)
        bind_review_admission(self.bundle)
        collector_registry = bind_production_posting_fixture(self.config, self.bundle)
        self._owner_review()
        self._refresh_owner_posting_receipt(collector_registry)

        state_config = copy.deepcopy(self.config0)
        state_config["jira"]["enabled"] = False
        state = {}
        first = assign_intake(
            state_config,
            {"key": "EX-1", "summary": "Moderate code", "description": "",
             "write_paths": ["src/example.py"], "risk_flags": []},
            state, None, now="2026-10-10T08:00:00Z",
            controller_actor_id="controller-1")
        changed = change_assignment(
            state_config, "EX-1", state, None, requested_tier=3,
            requested_cap=3,
            owner_source=owner_source(
                "EX-1", first["assignment_id"], 3, 3,
                "Gate scope added"),
            verify_owner_source=verify_owner_source,
            reason="Gate scope added", now="2026-10-10T09:00:00Z",
            controller_actor_id="controller-1")

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            runtime = sealed_runtime(folder)
            config_path = base / "project-config.json"
            bundle_path = base / "bundle.json"
            state_path = base / "controller-state.json"
            config_path.write_text(json.dumps(dict(self.config)), encoding="utf-8")
            bundle_path.write_text(json.dumps(self.bundle), encoding="utf-8")
            state_path.write_text(json.dumps(state), encoding="utf-8")

            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch(
                    "agentic.posting_collector.registry_from_host_config",
                    return_value=collector_registry), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--assignment-state", str(state_path),
                    "--posting-collector-host-config", str(base / "posting-host.json"),
                    "--now", NOW])
            self.assertEqual(0, code, stderr.getvalue())
            self.assertEqual(3, json.loads(stdout.getvalue())["risk_tier"])
            self.assertEqual(changed["assignment_id"],
                             state["tier_assignments"]["EX-1"]["assignment_id"])

            mismatched = copy.deepcopy(self.bundle)
            mismatched["review_verdicts"][-1]["tier"] = 2
            bundle_path.write_text(json.dumps(mismatched), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch(
                    "agentic.posting_collector.registry_from_host_config",
                    return_value=collector_registry), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--assignment-state", str(state_path),
                    "--posting-collector-host-config", str(base / "posting-host.json"),
                    "--now", NOW])
            self.assertEqual(2, code)
            self.assertIn("tier", json.loads(stderr.getvalue())["reason"].lower())

    def test_awf16_r2c_002_terminal_findings_sha256_must_match_critic_artifact(self):
        self._review_history(1)
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        verdict = self.bundle["review_verdicts"][-1]
        verdict["critic_review"]["findings_sha256"] = "f" * 64
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_002_awf16_195_r1c_001_legacy_terminal_hash_must_match(self):
        """The optional compatibility shape must still hash the substantive review."""
        self._review_history(1)
        bind_review_admission(self.bundle)
        registry = bind_production_posting_fixture(self.config, self.bundle)
        self.bundle["critic"]["findings"] = [self.finding("SUBSTANTIVE-NIT", "NIT", None)]
        terminal = self.bundle["review_verdicts"][-1]
        artifact_uri = f"urn:awf:critic-review:{terminal['critic_review']['record_id']}"
        terminal["evidence"].remove(artifact_uri)
        terminal["critic_review"]["record_id"] = next(
            run["record_id"] for run in self.bundle["runs"]
            if run["run_id"] == terminal["run_id"]
        )
        terminal["critic_review"]["findings_sha256"] = fingerprint("critic-findings", [])
        bind_review_admission(self.bundle)
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("NOT_READY", gate["conclusion"])
        terminal["critic_review"]["findings_sha256"] = fingerprint(
            "critic-findings", self.bundle["critic"]["findings"]
        )
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW,
                        posting_collector_registry=registry)
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_002_legacy_run_receipt_cannot_replace_terminal_critic_artifact(self):
        self.tier1_bundle()
        self._review_history(1, historical_tier=1, terminal_tier=1)
        terminal = self.bundle["review_verdicts"][-1]
        artifact_uri = f"urn:awf:critic-review:{terminal['critic_review']['record_id']}"
        terminal["evidence"].remove(artifact_uri)
        terminal["critic_review"]["record_id"] = next(
            run["record_id"] for run in self.bundle["runs"]
            if run["run_id"] == terminal["run_id"]
        )
        self.bundle["critic"].update(
            verdict="REQUEST_CHANGES",
            findings=[self.finding("SUBSTANTIVE-NIT", "NIT", None,
                                   path="tests/test_example.py")],
        )
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_r3_001_historical_run_keeps_original_candidate_binding(self):
        self._review_history(2)
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])
        first = self.bundle["review_verdicts"][0]
        first_run = next(run for run in self.bundle["runs"] if run["run_id"] == first["run_id"])
        first_run["binding"]["candidate_id"] = "f" * 64
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_195_r3_004_pre_escalation_round_keeps_old_tier_and_binding(self):
        self._tier_three_candidate()
        self._review_history(2, historical_tier=2, terminal_tier=3)
        self.assertEqual(
            "READY_FOR_OWNER_AUTHORIZATION", self._owner_gate()["conclusion"]
        )

    def test_ac5_self_asserted_tier_three_owner_record_does_not_qualify(self):
        self._tier_three_candidate()
        self._review_history(2, historical_tier=2, terminal_tier=3)
        self.assertEqual(
            "NOT_READY", self._owner_gate(provider_evidence=False)["conclusion"]
        )

    def test_awf16_r3_001_historical_posting_keeps_original_pr_body(self):
        self._review_history(2)
        self._retain_historical_posting_body("a" * 64)
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])

    def test_awf16_195_r3_004_pre_escalation_posting_keeps_original_pr_body(self):
        self._tier_three_candidate()
        self._review_history(2, historical_tier=2, terminal_tier=3)
        self._retain_historical_posting_body("a" * 64)
        self.assertEqual(
            "READY_FOR_OWNER_AUTHORIZATION", self._owner_gate()["conclusion"]
        )

    def test_awf16_195_r4_001_policy_continues_before_cap_with_open_p2(self):
        finding = {"id": "OPEN-P2", "severity": "MINOR"}
        result = review_decision(2, 1, latest_pass=True, open_findings=[finding])
        self.assertEqual("CONTINUE", result["status"])

    def test_awf16_195_r4_001_gate_is_not_ready_before_cap_with_open_p2(self):
        self._minor_finding()
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_195_r3_001_at_cap_requires_retained_ticket_mapping(self):
        self._review_history(2)
        self._minor_finding()
        self.assertEqual("NOT_READY", self.gate()["conclusion"])
        self.bundle["ticketed_p2_records"] = [
            {"finding_id": "OPEN-P2", "ticket_key": "not-a-ticket"}
        ]
        self.assertEqual("NOT_READY", self.gate()["conclusion"])
        self.bundle["ticketed_p2_records"] = [
            {"finding_id": "OPEN-P2", "ticket_key": "AWF-999"}
        ]
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])


if __name__ == "__main__":
    import unittest
    unittest.main()
