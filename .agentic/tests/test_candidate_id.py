import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.canonical import candidate_id, reject_git_sha_candidate_ids, sha256
from agentic import gates, interaction, review_completion
from agentic.providers.github_review_host import HostDriver


ROOT = Path(__file__).resolve().parents[2]
GIT_SHA_MESSAGE = "candidate_id is the AWF candidate fingerprint, not a Git SHA"
EMPTY_CANDIDATE_ID = "f6ad6529bb10204be62fc7b66e03cb77c1a1be8f73ce4471767659efcef8bb40"


class CandidateIdTests(unittest.TestCase):
    def test_candidate_id_uses_candidate_domain_and_requires_object(self):
        self.assertEqual(candidate_id({}), EMPTY_CANDIDATE_ID)
        with self.assertRaisesRegex(ValidationError, "Candidate must be a JSON object"):
            candidate_id([])

    def test_workflow_candidate_id_reads_stdin_and_documents_bindings(self):
        command = [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                   "candidate-id"]
        result = subprocess.run(command, input="{}", text=True, capture_output=True,
                                cwd=ROOT, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), EMPTY_CANDIDATE_ID)

        help_result = subprocess.run(command + ["--help"], text=True, capture_output=True,
                                     cwd=ROOT, check=False)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        help_text = " ".join(help_result.stdout.split())
        self.assertIn("candidate_id = fingerprint('candidate', candidate)", help_text)
        self.assertIn("requirements_hash = fingerprint('requirements', snapshot)", help_text)
        self.assertIn("contract_hash = fingerprint('contract', contract)", help_text)
        self.assertIn("policy_hash = fingerprint('policy'", help_text)
        self.assertIn(EMPTY_CANDIDATE_ID, help_text)

    def test_workflow_candidate_id_reads_file_and_rejects_non_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text('{"head_sha":"' + "a" * 40 + '"}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                 "candidate-id", str(path)],
                text=True, capture_output=True, cwd=ROOT, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), candidate_id({"head_sha": "a" * 40}))

        invalid = subprocess.run(
            [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
             "candidate-id"],
            input="[]", text=True, capture_output=True, cwd=ROOT, check=False)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stderr.strip(), "Candidate must be a JSON object")

    def test_git_sha_diagnostic_precedes_gate_schema_validation(self):
        bundle = {"record": {"binding": {"candidate_id": "A" * 40}}}
        config = {"jira": {"enabled": True},
                  "merge_gate": {"trusted_owner_ids": ["owner"]}}

        class Contracts:
            def validate(self, *_args):
                self.fail("schema validation must not obscure the candidate_id diagnostic")

        with patch.object(gates, "validate_config", return_value=None):
            with self.assertRaisesRegex(ValidationError, GIT_SHA_MESSAGE):
                gates.evaluate(config, {}, bundle, Contracts(), "2026-10-11T12:00:00Z")

    def test_git_sha_diagnostic_is_used_by_handoff_and_completion(self):
        bad = {"binding": {"candidate_id": "0" * 40}}
        with self.assertRaisesRegex(ValidationError, GIT_SHA_MESSAGE):
            interaction.gate_handoff(bad, {}, {}, object(), "2026-10-11T12:00:00Z")
        with self.assertRaisesRegex(ValidationError, GIT_SHA_MESSAGE):
            review_completion.validate_gate_submission(bad, {}, {}, {}, [])
        with self.assertRaisesRegex(ValidationError, GIT_SHA_MESSAGE):
            reject_git_sha_candidate_ids({"nested": [bad]})

    def test_controller_supplies_candidate_fingerprint_and_critic_instruction(self):
        candidate = {
            "repository_id": 12,
            "pr": 7,
            "head": "a" * 40,
            "base": "b" * 40,
            "head_ref": "awf/test",
            "base_ref": "main",
        }
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            contract = Path(directory) / "contract.txt"
            contract.write_text("C1: candidate identity", encoding="utf-8")
            driver = HostDriver.__new__(HostDriver)
            driver.root = ROOT
            driver.state = state
            driver.critic = Path(directory) / "critic"
            driver.worker = Path(directory) / "worker"
            driver.c = {
                "allowed_paths": ["src/a.py"],
                "contract_path": str(contract),
                "contract_sha256": sha256(contract.read_bytes()),
                "reasoning_effort": {"critic": "high"},
                "codex_config_overrides": {},
                "models": {"critic": "test-critic"},
                "agent_timeout_seconds": 30,
            }
            captured = {}

            def fake_run(_name, args, **kwargs):
                captured["prompt"] = kwargs["stdin"]
                output = Path(args[args.index("--output-last-message") + 1])
                output.write_text(json.dumps({
                    "candidate": candidate,
                    "verdict": "APPROVE",
                    "reviewed_files": ["src/a.py"],
                    "findings": [],
                    "summary": "Candidate satisfies the frozen contract.",
                }), encoding="utf-8")

            driver.run = fake_run
            result = driver.agent("critic", candidate, [], "critic-run", ["src/a.py"])
            payload = json.loads((state / "runs/critic-run/input.json").read_text(encoding="utf-8"))

        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(payload["binding_fingerprints"],
                         {"candidate_id": candidate_id(candidate)})
        self.assertIn("The controller supplies binding_fingerprints", captured["prompt"])
        self.assertIn("Do not compute, replace, or reinterpret them", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
