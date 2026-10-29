"""Generation regressions for the additive workflow content binding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

import generate_contracts  # noqa: E402
import generate_examples  # noqa: E402
from agentic.contracts import Contracts  # noqa: E402
from agentic.model_routing import default_policy  # noqa: E402
from agentic.operating import default_operating  # noqa: E402
from routing_fixtures import fixture_policy  # noqa: E402
from source_only import COMMITTED_GIT_REASON, committed_git_tuple  # noqa: E402


class WorkflowBlobGenerationTests(unittest.TestCase):
    def test_routing_only_switch_does_not_change_generated_artifacts(self):
        fixture = ROOT / ".agentic/tests/fixtures/routing/valid.yaml"
        original = fixture.read_text(encoding="utf-8")
        switched = original.replace(
            '"worker": {"model": "gpt-5.6-luna", "effort": "medium"}',
            '"worker": {"model": "gpt-5.6-sol", "effort": "medium"}',
            1,
        )
        with tempfile.TemporaryDirectory(prefix="awf-routing-generation-") as raw:
            root = Path(raw)
            routing = root / "routing.yaml"
            before = root / "before"
            after = root / "after"
            routing.write_text(original, encoding="utf-8")
            with patch("agentic.model_routing._default_routing_path", return_value=routing):
                generate_examples.generate(before, ROOT)
            routing.write_text(switched, encoding="utf-8")
            switched_model = default_policy(routing)["role_defaults"]["worker"]["model"]
            with patch("agentic.model_routing._default_routing_path", return_value=routing):
                generate_examples.generate(after, ROOT)
            before_files = {
                path.relative_to(before).as_posix(): path.read_bytes()
                for path in before.rglob("*") if path.is_file()
            }
            after_files = {
                path.relative_to(after).as_posix(): path.read_bytes()
                for path in after.rglob("*") if path.is_file()
            }
        self.assertEqual("gpt-5.6-sol", switched_model)
        self.assertEqual(before_files, after_files)

    def test_generated_config_and_operating_defaults_follow_routing_file(self):
        policy = default_policy()
        generated = generate_examples.config(False)
        configured = generated["execution"]["model_routing"]
        self.assertEqual(".agentic/routing.yaml", configured["routing"])
        for key in ("high_risk_flags", "agent_overrides", "ticket_overrides",
                    "escalation", "budgets", "adaptive", "reconciliation"):
            self.assertEqual(policy[key], configured[key])
        operating = default_operating()
        for role in ("controller", "critic", "specialist"):
            self.assertEqual(policy["role_defaults"][role], operating[role])
        self.assertEqual(
            policy["role_defaults"]["worker"],
            operating["streams"]["A"]["worker"],
        )
        self.assertEqual(
            {"enabled": True, **policy["simple_worker"]},
            operating["simple_worker"],
        )

    def test_project_schema_accepts_optional_workflow_blob_sha(self):
        check = generate_contracts.catalog()["project-config"]["properties"][
            "validation"
        ]["properties"]["required_ci_checks"]["items"]
        self.assertFalse(check["additionalProperties"])
        self.assertIn("workflow_sha", check["required"])
        self.assertNotIn("workflow_blob_sha", check["required"])
        self.assertEqual(
            "^[0-9a-f]{40}$",
            check["properties"]["workflow_blob_sha"]["pattern"],
        )

    def test_checked_in_workflow_pin_is_byte_exact_and_git_observed(self):
        if committed_git_tuple(ROOT) is None:
            self.skipTest(COMMITTED_GIT_REASON)
        workflow = (ROOT / ".github/workflows/ci.yml").read_bytes()
        expected = hashlib.sha1(
            b"blob " + str(len(workflow)).encode("ascii") + b"\0" + workflow
        ).hexdigest()
        self.assertEqual(expected, generate_examples.workflow_blob_sha(ROOT))
        observed = subprocess.run(
            ["git", "ls-tree", "HEAD", "--", ".github/workflows/ci.yml"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[2]
        self.assertEqual(observed, expected)

    def test_workflow_pin_rejects_worktree_disagreement_with_git(self):
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            workflow = repository / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_bytes(b"name: reviewed\n")
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.name", "AWF Test"],
                ["git", "config", "user.email", "awf@example.invalid"],
                ["git", "add", ".github/workflows/ci.yml"],
                ["git", "commit", "-qm", "fixture"],
            ):
                subprocess.run(command, cwd=repository, check=True, capture_output=True)
            workflow.write_bytes(b"name: unreviewed\n")
            with self.assertRaisesRegex(ValueError, "checked-in workflow"):
                generate_examples.workflow_blob_sha(repository)

    def test_exported_source_without_git_metadata_uses_git_blob_observation(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw)
            workflow = source / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_bytes(b"name: exported\n")
            expected = subprocess.run(
                ["git", "hash-object", "--stdin"],
                input=workflow.read_bytes(),
                check=True,
                capture_output=True,
            ).stdout.decode("ascii").strip()
            self.assertEqual(expected, generate_examples.workflow_blob_sha(source))

    def test_content_example_is_separate_and_legacy_examples_stay_exact(self):
        legacy = generate_examples.config(True)
        unconfigured = generate_examples.config(False)
        content = generate_examples.content_binding_config(ROOT)
        self.assertNotIn(
            "workflow_blob_sha", legacy["validation"]["required_ci_checks"][0]
        )
        self.assertEqual([], unconfigured["validation"]["required_ci_checks"])
        check = content["validation"]["required_ci_checks"][0]
        self.assertEqual(".github/workflows/ci.yml", check["workflow_path"])
        self.assertEqual(generate_examples.workflow_blob_sha(ROOT), check["workflow_blob_sha"])
        self.assertEqual("a" * 40, check["workflow_sha"])
        Contracts(ROOT / ".agentic/schemas").validate("project-config", content)

    def test_unconfigured_generator_twins_remain_exact_and_unpinned(self):
        expected = generate_examples.config(False, default_policy(), fresh_defaults=True)
        for relative in (
            ".agentic/PROJECT_CONFIG.yaml",
            ".agentic/templates/source-config/PROJECT_CONFIG.yaml",
        ):
            with self.subTest(relative=relative):
                actual = json.loads((ROOT / relative).read_text(encoding="utf-8"))
                self.assertEqual(expected, actual)
                self.assertEqual([], actual["validation"]["required_ci_checks"])
        unconfigured = json.loads(
            (ROOT / ".agentic/examples/unconfigured-project.yaml").read_text(
                encoding="utf-8"))
        self.assertEqual(fixture_policy(), unconfigured["execution"]["model_routing"])
        self.assertEqual([], unconfigured["validation"]["required_ci_checks"])

    def test_generated_content_example_matches_and_check_mode_is_clean(self):
        expected = generate_examples.content_binding_config(ROOT, fixture_policy())
        actual = json.loads(
            (ROOT / ".agentic/examples/workflow-content-binding-project.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(expected, actual)
        self.assertEqual(0, generate_examples.main(["--check"]))


if __name__ == "__main__":
    unittest.main()
