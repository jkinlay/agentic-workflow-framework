"""AWF-60 production posting-collector and final-gate regressions."""
from __future__ import annotations

import base64
import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, load, sha256
from agentic.cli import main as workflow_main
from agentic.gates import evaluate, review_round_receipt_sha256
from agentic.lifecycle import definition
from agentic.posting_collector import (
    COLLECTOR_ID,
    PostingCollectorMismatch,
    collect_posting_observation,
    registry_from_review_host,
)
from test_review_policy import (
    Fixture,
    NOW,
    bind_production_posting_fixture,
    bind_review_admission,
    sealed_runtime,
)
from source_only import skip_unless_source_repo


RELEASE_SHA256 = "d" * 64


class TestOnlyReviewHost:
    """No-network provider double; deliberately confined to the test tree."""

    __test__ = False

    def __init__(self, bundle):
        self.responses = {}
        candidate = bundle["candidate"]
        for verdict in bundle["review_verdicts"]:
            observation = verdict["posting_observation"]
            self.responses[f"issues/comments/{observation['comment_id']}"] = {
                "id": observation["comment_id"],
                "html_url": observation["comment_url"],
                "body": observation["comment_bytes"],
            }
        terminal = max(bundle["review_verdicts"], key=lambda item: item["round"])
        self.responses[f"pulls/{candidate['pr_number']}"] = {
            "number": candidate["pr_number"],
            "html_url": (
                f"{candidate['host']}/{candidate['repository']}/pull/"
                f"{candidate['pr_number']}"
            ),
            "body": terminal["posting_observation"]["body_bytes"],
            "base": {"repo": {"id": candidate["repository_id"]}},
        }

    def api(self, suffix):
        return copy.deepcopy(self.responses[suffix])


class PostingCollectorTests(Fixture):
    def setUp(self):
        super().setUp()
        self.config["github"].update(
            host="https://example.invalid", repository="example/project"
        )
        self.bundle["candidate"].update(
            host="https://example.invalid", repository="example/project"
        )
        for verdict in self.bundle["review_verdicts"]:
            verdict["pr_comment_url"] = (
                f"https://example.invalid/example/project/pull/"
                f"{self.bundle['candidate']['pr_number']}#issuecomment-{verdict['round']}"
            )
        self.rebind()
        bind_review_admission(self.bundle)
        bind_production_posting_fixture(self.config, self.bundle)
        self.host = TestOnlyReviewHost(self.bundle)

    def _install_runtime_observations(self):
        registrations = []
        for verdict in sorted(self.bundle["review_verdicts"], key=lambda item: item["round"]):
            receipt = next(
                item for item in self.bundle["review_round_receipts"]
                if item["critic_artifact_binding"]["round"] == verdict["round"]
            )
            collected = collect_posting_observation(
                self.host,
                candidate=self.bundle["candidate"],
                verdict=verdict,
                review_round_receipt=receipt,
                collector_run_id=verdict["posting_observation"]["run_id"],
                observed_at=verdict["posting_observation"]["observed_at"],
                release_sha256=RELEASE_SHA256,
            )
            verdict["posting_observation"] = collected["observation"]
            registrations.append(collected["registration"])
            posting_bytes = canonical(collected["observation"])
            receipt["posting_observation_json"] = {
                "encoding": "base64",
                "data": base64.b64encode(posting_bytes).decode("ascii"),
            }
            receipt["posting_observation_sha256"] = sha256(posting_bytes)
            uri = f"urn:awf:review-round-receipt:{verdict['round']}"
            entry = next(
                item for item in self.bundle["evidence_registry"] if item["uri"] == uri
            )
            entry["sha256"] = review_round_receipt_sha256(receipt)
        registry = registry_from_review_host(
            self.host, self.bundle, release_sha256=RELEASE_SHA256
        )
        return registrations, registry

    def test_ac1_live_observation_and_runtime_registry_reach_owner_authorization(self):
        registrations, registry = self._install_runtime_observations()
        gate = evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry=registry,
        )
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", gate["conclusion"])
        self.assertEqual("PASS", gate["gates"]["verdict_posting"]["result"])
        self.assertTrue(all(item["collector_id"] == COLLECTOR_ID for item in registrations))

    def test_ac2_comment_mismatch_has_specific_reason_and_gate_stays_not_ready(self):
        self._install_runtime_observations()
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        suffix = f"issues/comments/{terminal['posting_observation']['comment_id']}"
        self.host.responses[suffix]["body"] += "\nchanged"
        with self.assertRaisesRegex(PostingCollectorMismatch, "POSTING_COMMENT_MISMATCH"):
            registry_from_review_host(
                self.host, self.bundle, release_sha256=RELEASE_SHA256
            )
        gate = evaluate(
            self.config, definition(), self.bundle, self.contracts, NOW,
            posting_collector_registry={},
        )
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_ac2_body_and_anchor_mismatches_are_specific(self):
        self._install_runtime_observations()
        pr_suffix = f"pulls/{self.bundle['candidate']['pr_number']}"
        original = self.host.responses[pr_suffix]["body"]
        cases = {
            "POSTING_BODY_MISMATCH": original + "\nchanged",
            "POSTING_ANCHOR_MISMATCH": original.replace("review-verdict:", "wrong-anchor:"),
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                self.host.responses[pr_suffix]["body"] = body
                with self.assertRaisesRegex(PostingCollectorMismatch, reason):
                    registry_from_review_host(
                        self.host, self.bundle, release_sha256=RELEASE_SHA256
                    )
        self.host.responses[pr_suffix]["body"] = original

    def test_ac2_digest_round_and_collector_run_mismatches_are_specific(self):
        self._install_runtime_observations()
        terminal = max(self.bundle["review_verdicts"], key=lambda item: item["round"])
        cases = (
            ("review_verdict_sha256", "e" * 64, "POSTING_VERDICT_DIGEST_MISMATCH"),
            (
                "critic_artifact_binding",
                {**terminal["critic_artifact_binding"], "round": 99},
                "POSTING_ROUND_BINDING_MISMATCH",
            ),
            ("run_id", "00000000-0000-0000-0000-000000000099", "POSTING_COLLECTOR_RUN_MISMATCH"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.bundle)
                changed_terminal = max(
                    changed["review_verdicts"], key=lambda item: item["round"]
                )
                changed_terminal["posting_observation"][field] = value
                with self.assertRaisesRegex(PostingCollectorMismatch, reason):
                    registry_from_review_host(
                        self.host, changed, release_sha256=RELEASE_SHA256
                    )

    def test_ac3_config_cannot_register_without_runtime_observation(self):
        self._install_runtime_observations()
        self.config["merge_gate"]["production_posting_collector_ids"] = [COLLECTOR_ID]
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])

    def test_ac4_test_double_is_not_exposed_by_production_module(self):
        import agentic.posting_collector as production

        self.assertFalse(hasattr(production, "TestOnlyReviewHost"))
        self.assertNotIn("TestOnlyReviewHost", Path(production.__file__).read_text(encoding="utf-8"))

    @skip_unless_source_repo(
        "AC5 byte parity needs the source-only contract and example generators",
        "scripts/generate_contracts.py",
        "scripts/generate_examples.py",
    )
    def test_ac5_generated_registration_schema_and_template_have_byte_parity(self):
        scripts_path = str(ROOT / "scripts")
        inserted_scripts_path = scripts_path not in sys.path
        if inserted_scripts_path:
            sys.path.insert(0, scripts_path)
        try:
            import generate_contracts
            import generate_examples

            with tempfile.TemporaryDirectory() as folder:
                generated_root = Path(folder)
                with mock.patch.object(generate_contracts, "ROOT", generated_root):
                    with redirect_stdout(io.StringIO()):
                        generate_contracts.main()
                with mock.patch.object(generate_examples, "ROOT", generated_root):
                    with redirect_stdout(io.StringIO()):
                        generate_examples.main()

                checked_schema = (
                    ROOT / ".agentic/schemas/posting-collector-registration.schema.json"
                )
                generated_schema = (
                    generated_root
                    / ".agentic/schemas/posting-collector-registration.schema.json"
                )
                self.assertEqual(checked_schema.read_bytes(), generated_schema.read_bytes())

                checked_template = (
                    ROOT / ".agentic/templates/posting-collector-registration.yaml"
                )
                generated_template = (
                    generated_root
                    / ".agentic/templates/posting-collector-registration.yaml"
                )
                self.assertEqual(
                    checked_template.read_bytes(), generated_template.read_bytes()
                )
        finally:
            if inserted_scripts_path:
                sys.path.remove(scripts_path)

    def test_ac5_shipped_registration_template_validates_and_refuses_empty_receipts(self):
        checked_template = (
            ROOT / ".agentic/templates/posting-collector-registration.yaml"
        )
        template = load(checked_template)
        self.assertEqual(
            "posting-collector-registration", template["template_for"]
        )
        filled_record = copy.deepcopy(template["record"])
        filled_record["receipts"] = {
            "00000000-0000-0000-0000-000000000001": "f" * 64,
        }
        self.contracts.validate("posting-collector-registration", filled_record)

        refused_record = copy.deepcopy(filled_record)
        refused_record["receipts"] = {}
        with self.assertRaises(ValidationError) as refused:
            self.contracts.validate(
                "posting-collector-registration", refused_record
            )
        self.assertIn("receipts", str(refused.exception))
        self.assertIn("non-empty", str(refused.exception))

    def test_cli_supplies_only_runtime_collector_registry_to_gate(self):
        _registrations, registry = self._install_runtime_observations()
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            bundle_path = Path(folder) / "bundle.json"
            bundle_path.write_text(
                json.dumps(self.bundle, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            config_path = Path(folder) / "project-config.json"
            config_path.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            host_config_path = Path(folder) / "review-host.json"
            host_config_path.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config",
                return_value=registry,
            ) as collector, redirect_stdout(stdout):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--posting-collector-host-config", str(host_config_path),
                    "--now", NOW,
                ])
        self.assertEqual(0, exit_code)
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", json.loads(stdout.getvalue())["conclusion"])
        collector.assert_called_once()

    def test_cli_reports_collector_mismatch_and_keeps_gate_not_ready(self):
        self._install_runtime_observations()
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            bundle_path = Path(folder) / "bundle.json"
            bundle_path.write_text(
                json.dumps(self.bundle, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            config_path = Path(folder) / "project-config.json"
            config_path.write_text(
                json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            host_config_path = Path(folder) / "review-host.json"
            host_config_path.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch(
                "agentic.posting_collector.registry_from_host_config",
                side_effect=PostingCollectorMismatch(
                    "POSTING_BODY_MISMATCH: live PR body differs"
                ),
            ), redirect_stdout(stdout):
                exit_code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--posting-collector-host-config", str(host_config_path),
                    "--now", NOW,
                ])
        gate = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("NOT_READY", gate["conclusion"])
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertIn("POSTING_BODY_MISMATCH", gate["residual_risks"][-1])


if __name__ == "__main__":
    unittest.main()
