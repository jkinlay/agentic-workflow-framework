"""AWF-16 1.9.5 final-gate regressions from the split #70 review."""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.gates import critic_artifact_receipt_sha256
from agentic.review_tiers import review_decision
from test_review_policy import Fixture, NOW


class ReviewTiers195GateTests(Fixture):
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
            if not (entry["uri"].endswith("/posting") or "/posting-" in entry["uri"])
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

    def _owner_review(self):
        verifier = self.verifier_run()
        terminal = copy.deepcopy(self.bundle["review_verdicts"][-1])
        terminal.update(record_id=str(uuid.uuid4()), producer_id=verifier["producer_id"],
                        run_id=verifier["run_id"], reviewer_id="fixture-owner",
                        owner_review=True, owner_id=1001)
        self.bundle["owner_review"] = terminal

    def test_awf16_r2c_002_terminal_findings_sha256_must_match_critic_artifact(self):
        self._review_history(1)
        verdict = self.bundle["review_verdicts"][-1]
        verdict["critic_review"]["findings_sha256"] = "f" * 64
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_r2c_002_awf16_195_r1c_001_legacy_terminal_hash_must_match(self):
        """The optional compatibility shape must still hash the substantive review."""
        self._review_history(1)
        self.bundle["critic"]["findings"] = [self.finding("SUBSTANTIVE-NIT", "NIT", None)]
        terminal = self.bundle["review_verdicts"][-1]
        artifact_uri = f"urn:awf:critic-review:{terminal['critic_review']['record_id']}"
        terminal["evidence"].remove(artifact_uri)
        terminal["critic_review"]["record_id"] = next(
            run["record_id"] for run in self.bundle["runs"]
            if run["run_id"] == terminal["run_id"]
        )
        terminal["critic_review"]["findings_sha256"] = fingerprint("critic-findings", [])
        self.assertEqual("NOT_READY", self.gate()["conclusion"])
        terminal["critic_review"]["findings_sha256"] = fingerprint(
            "critic-findings", self.bundle["critic"]["findings"]
        )
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_r3_001_historical_run_keeps_original_candidate_binding(self):
        self._review_history(2)
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])
        first = self.bundle["review_verdicts"][0]
        first_run = next(run for run in self.bundle["runs"] if run["run_id"] == first["run_id"])
        first_run["binding"]["candidate_id"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "critic artifact"):
            self.gate()

    def test_awf16_195_r3_004_pre_escalation_round_keeps_old_tier_and_binding(self):
        self._tier_three_candidate()
        self._review_history(2, historical_tier=2, terminal_tier=3)
        self._owner_review()
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", self.gate()["conclusion"])

    def test_awf16_195_r4_001_policy_continues_before_cap_with_open_p2(self):
        finding = {"id": "OPEN-P2", "severity": "MINOR"}
        result = review_decision(2, 1, latest_pass=True, open_findings=[finding])
        self.assertEqual("CONTINUE", result["status"])

    def test_awf16_195_r4_001_gate_is_not_ready_before_cap_with_open_p2(self):
        self._minor_finding()
        self.assertEqual("NOT_READY", self.gate()["conclusion"])

    def test_awf16_195_r3_001_at_cap_requires_retained_ticket_mapping(self):
        self._review_history(3)
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
