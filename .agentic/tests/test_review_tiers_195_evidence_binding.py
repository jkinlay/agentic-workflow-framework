"""AWF-16 approved evidence-binding design regressions."""
from __future__ import annotations

import copy
import base64
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.canonical import canonical, fingerprint, sha256
from agentic.gates import (decode_base64_bytes, evaluate,
                           posting_collector_receipt_sha256,
                           review_round_receipt_sha256)
from agentic.lifecycle import definition
from source_only import skip_unless_source_repo
from test_review_policy import (Fixture, NOW, bind_production_posting_fixture,
                                bind_review_admission)


def _generator_catalog():
    path = ROOT / "scripts/generate_contracts.py"
    spec = importlib.util.spec_from_file_location("awf_generate_contracts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.catalog()


class EvidenceBindingDesignTests(Fixture):
    """Adversarial probes from AWF16-R2C-002, AWF16-R1-003, and AWF-60."""

    def _prepare_test_registry(self):
        bind_review_admission(self.bundle)
        return bind_production_posting_fixture(self.config, self.bundle)

    def _evaluate_with_test_registry(self, registry):
        return evaluate(
            self.config,
            definition(),
            self.bundle,
            self.contracts,
            NOW,
            posting_collector_registry=registry,
        )

    def _two_round_bundle(self):
        # The existing fixture helper deliberately models an old and a current
        # candidate.  The implementation must replace its self-attested receipt
        # with retained completion bytes for both rounds.
        from test_review_tiers_195_gates import ReviewTiers195GateTests

        ReviewTiers195GateTests._review_history(self, 2)

    def test_awf16_r2c_002_historical_arbitrary_findings_hash_is_not_ready(self):
        self._two_round_bundle()
        registry = self._prepare_test_registry()
        historical = self.bundle["review_verdicts"][0]
        historical["critic_review"]["findings_sha256"] = "f" * 64
        artifact = next(
            entry for entry in self.bundle["evidence_registry"]
            if entry["uri"] == f"urn:awf:critic-review:{historical['critic_review']['record_id']}"
        )
        from agentic.gates import critic_artifact_receipt_sha256
        artifact["sha256"] = critic_artifact_receipt_sha256(historical)
        gate = self._evaluate_with_test_registry(registry)
        self.assertEqual("FAIL", gate["gates"]["critic_current_tuple"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r2c_002_each_round_requires_retained_result_bytes(self):
        self._two_round_bundle()
        registry = self._prepare_test_registry()
        receipts = self.bundle.get("review_round_receipts", [])
        self.assertEqual(2, len(receipts), "every round must retain its completion result bytes")
        baseline = copy.deepcopy(self.bundle)
        for round_index in range(2):
            for mutation in ("remove", "alter"):
                with self.subTest(round=round_index + 1, mutation=mutation):
                    probe = copy.deepcopy(baseline)
                    if mutation == "remove":
                        del probe["review_round_receipts"][round_index]["result_json"]
                    else:
                        probe["review_round_receipts"][round_index]["result_json"] = "{}"
                    self.bundle = probe
                    self.assertEqual(
                        "NOT_READY", self._evaluate_with_test_registry(registry)["conclusion"])

    def test_awf16_r1_003_valid_looking_fabricated_comment_is_not_ready(self):
        registry = self._prepare_test_registry()
        verdict = self.bundle["review_verdicts"][-1]
        observation = verdict["posting_observation"]
        fabricated = "https://github.com/fixture/example/pull/7#issuecomment-999999"
        verdict["pr_comment_url"] = fabricated
        observation["comment_url"] = fabricated
        basis = copy.deepcopy(observation)
        basis["collector_receipt_sha256"] = "0" * 64
        registration = registry[observation["producer_id"]]
        observation["collector_receipt_sha256"] = posting_collector_receipt_sha256(
            observation, sha256(canonical(basis)), registration)
        receipt = self.bundle["review_round_receipts"][-1]
        posting_bytes = canonical(observation)
        receipt["posting_observation_json"] = {
            "encoding": "base64",
            "data": base64.b64encode(posting_bytes).decode("ascii"),
        }
        receipt["posting_observation_sha256"] = sha256(posting_bytes)
        receipt_entry = next(entry for entry in self.bundle["evidence_registry"]
                             if entry["uri"].startswith("urn:awf:review-round-receipt:"))
        receipt_entry["sha256"] = review_round_receipt_sha256(receipt)
        gate = self._evaluate_with_test_registry(registry)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_r1_003_posting_binds_verdict_run_and_artifact(self):
        registry = self._prepare_test_registry()
        baseline = copy.deepcopy(self.bundle)
        required = ("critic_artifact_binding", "review_verdict_record_id", "review_verdict_sha256")
        for field in required:
            with self.subTest(field=field):
                probe = copy.deepcopy(baseline)
                mutated = probe["review_verdicts"][-1]["posting_observation"]
                self.assertIn(field, mutated)
                if field == "critic_artifact_binding":
                    mutated[field]["critic_run_id"] = str(uuid.uuid4())
                elif field == "review_verdict_record_id":
                    mutated[field] = str(uuid.uuid4())
                else:
                    mutated[field] = "f" * 64
                self.bundle = probe
                gate = self._evaluate_with_test_registry(registry)
                self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])

    def test_awf60_missing_registered_production_collector_is_not_ready(self):
        self._prepare_test_registry()
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf60_fixture_or_self_declared_collector_is_not_ready(self):
        self._prepare_test_registry()
        os.environ["AWF_POSTING_COLLECTOR"] = self.config["merge_gate"][
            "production_posting_collector_ids"
        ][0]
        try:
            gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        finally:
            os.environ.pop("AWF_POSTING_COLLECTOR", None)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf60_production_config_and_default_registry_cannot_enable_test_collector(self):
        self._prepare_test_registry()
        gate = evaluate(self.config, definition(), self.bundle, self.contracts, NOW)
        self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])

    def test_awf16_fixup_base64_round_trips_exact_provider_and_posting_bytes(self):
        receipt = self.bundle["review_round_receipts"][-1]
        posting_bytes = decode_base64_bytes(
            receipt["posting_observation_json"], "posting_observation_json")
        self.assertEqual(receipt["posting_observation_sha256"], sha256(posting_bytes))
        posting = json.loads(posting_bytes.decode("utf-8"))
        self.assertEqual(self.bundle["review_verdicts"][-1]["posting_observation"], posting)

        provider_bytes = decode_base64_bytes(
            posting["provider_response_bytes"], "provider_response_bytes")
        self.assertEqual(posting["provider_response_sha256"], sha256(provider_bytes))
        self.assertEqual(canonical(json.loads(provider_bytes.decode("utf-8"))), provider_bytes)

    def test_awf16_fixup_noncanonical_or_altered_base64_is_not_ready(self):
        registry = self._prepare_test_registry()
        baseline = copy.deepcopy(self.bundle)
        baseline_registry = copy.deepcopy(registry)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

        def noncanonical_pad_bits(encoded):
            self.assertTrue(encoded.endswith("="), "fixture must exercise padded base64")
            offset = -3 if encoded.endswith("==") else -2
            index = alphabet.index(encoded[offset])
            replacement = alphabet[index | 1]
            self.assertNotEqual(encoded[offset], replacement)
            return encoded[:offset] + replacement + encoded[offset + 1:]

        for mutation in ("noncanonical", "altered"):
            with self.subTest(mutation=mutation):
                self.bundle = copy.deepcopy(baseline)
                registry = copy.deepcopy(baseline_registry)
                verdict = self.bundle["review_verdicts"][-1]
                observation = verdict["posting_observation"]
                envelope = observation["provider_response_bytes"]
                if mutation == "noncanonical":
                    envelope["data"] = noncanonical_pad_bits(envelope["data"])
                else:
                    envelope["data"] = ("A" if envelope["data"][0] != "A" else "B") + envelope["data"][1:]

                registration = registry[observation["producer_id"]]
                basis = copy.deepcopy(observation)
                basis["collector_receipt_sha256"] = "0" * 64
                observation["collector_receipt_sha256"] = posting_collector_receipt_sha256(
                    observation, sha256(canonical(basis)), registration)
                registration["receipts"][observation["run_id"]] = observation[
                    "collector_receipt_sha256"]

                posting_bytes = canonical(observation)
                receipt = self.bundle["review_round_receipts"][-1]
                receipt["posting_observation_json"] = {
                    "encoding": "base64",
                    "data": base64.b64encode(posting_bytes).decode("ascii"),
                }
                receipt["posting_observation_sha256"] = sha256(posting_bytes)
                receipt_entry = next(
                    entry for entry in self.bundle["evidence_registry"]
                    if entry["uri"].startswith("urn:awf:review-round-receipt:"))
                receipt_entry["sha256"] = review_round_receipt_sha256(receipt)

                gate = self._evaluate_with_test_registry(registry)
                self.assertEqual("FAIL", gate["gates"]["verdict_posting"]["result"])
                self.assertEqual("NOT_READY", gate["conclusion"])


class GeneratorParityTests(Fixture):
    @skip_unless_source_repo(
        "schema parity needs the source-only contract generator",
        "scripts/generate_contracts.py",
    )
    def test_awf16_r1_009_generated_schema_catalog_has_byte_parity(self):
        catalog = _generator_catalog()
        checked_in = {
            path.name.removesuffix(".schema.json"): path
            for path in (ROOT / ".agentic/schemas").glob("*.schema.json")
        }
        self.assertEqual(
            52,
            len(checked_in),
            "AWF16-R1-009 must enumerate all 52 checked-in schemas",
        )
        self.assertEqual(
            set(checked_in),
            set(catalog),
            "every checked-in schema must be generated, with no generator-only entries",
        )
        for name, path in sorted(checked_in.items()):
            with self.subTest(schema=name):
                expected = json.dumps(catalog[name], indent=2).encode("utf-8") + b"\n"
                actual = path.read_bytes()
                self.assertEqual(expected, actual, f"first differing generated schema: {name}")
        tier3 = catalog["project-config"]["properties"]["execution"]["properties"][
            "risk_tiers"
        ]["properties"]["tier3_review"]["properties"]["max_rounds"]
        self.assertEqual(3, tier3["maximum"])


if __name__ == "__main__":
    import unittest
    unittest.main()
