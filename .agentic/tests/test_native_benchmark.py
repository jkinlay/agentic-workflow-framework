"""Mechanical benchmark contract tests, not evidence of agent behavior quality."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("native_benchmark", ROOT / ".agentic/scripts/benchmark_native.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class NativeBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet, cls.rubric_pin = benchmark.prepare(ROOT, legacy_v1=True)
        cls.packet_raw = benchmark.encoded(cls.packet)
        cls.packet_pin = benchmark.sha(cls.packet_raw)
        cls.rubric_raw = benchmark.read(ROOT / ".agentic/benchmarks/native/rubric.json")
        cls.targets = {case["case_id"]: case for case in benchmark.parse(cls.rubric_raw)["cases"]}

    def setUp(self):
        self.observation = {"schema_version": 1, "benchmark_id": self.packet["benchmark_id"],
                            "packet_sha256": self.packet_pin, "results": []}
        for case in self.packet["cases"]:
            target = self.targets[case["case_id"]]
            self.observation["results"].append({"case_id": case["case_id"], "case_sha256": case["case_sha256"],
                "prompt_sha256": case["prompt_sha256"], "execution": dict.fromkeys(benchmark.EXECUTION_FIELDS),
                "decision_codes": target["required_codes"].copy(), "evidence_refs": target["required_evidence_refs"].copy(),
                "rationale": "Synthetic contract-test observation; no agent executed this response.", "observed_host_actions": []})

    def grade(self, **overrides):
        values = {"packet_raw": self.packet_raw, "observations_raw": benchmark.encoded(self.observation),
                  "rubric_raw": self.rubric_raw, "expected_packet_sha256": self.packet_pin,
                  "expected_rubric_sha256": self.rubric_pin}
        values.update(overrides)
        return benchmark.grade(**values, legacy_v1=True)

    def test_prepare_omits_rubric_answers_and_binds_actual_prompt_bytes(self):
        self.assertNotIn("required_codes", self.packet_raw.decode())
        self.assertNotIn("required_evidence_refs", self.packet_raw.decode())
        self.assertNotIn("allowed_decisions", self.packet_raw.decode())
        self.assertNotIn("allowance_rationale", self.packet_raw.decode())
        for case in self.packet["cases"]:
            prompt = ROOT / ".agentic/prompts" / (case["role"] + ".md")
            self.assertEqual(case["prompt_sha256"], benchmark.sha(prompt.read_bytes()))

    def test_complete_mechanical_match_preserves_unknown_identity_and_unobserved_actions(self):
        result = self.grade()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["passed_cases"], 6)
        self.assertEqual(result["measurement"], "self_reported_structured_decision_smoke")
        self.assertTrue(all(case["host_action_evidence"] == "not_provided" for case in result["cases"]))
        self.assertTrue(all(all(value is None for value in case["execution"].values()) for case in result["cases"]))

    def test_bad_decision_fails_case_without_turning_observations_into_host_fact(self):
        self.observation["results"][0]["decision_codes"] = ["launch_reviewer"]
        report = self.grade()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["passed_cases"], 5)
        self.assertIn("launch_reviewer", report["cases"][0]["unexpected_decisions"])

    def test_partial_decisions_and_missing_evidence_fail(self):
        self.observation["results"][1]["decision_codes"].pop()
        self.observation["results"][1]["evidence_refs"] = []
        result = self.grade()["cases"][1]
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["missing_decisions"])
        self.assertTrue(result["missing_evidence_refs"])

    def test_selecting_every_action_cannot_game_grader(self):
        for row in self.observation["results"]:
            row["decision_codes"] = self.packet["decision_vocabulary"].copy()
        result = self.grade()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["passed_cases"], 0)

    def test_case_warranted_optional_decisions_do_not_fail_complete_answers(self):
        additions = {"NATIVE-01": ["reject_candidate_instruction"],
                     "NATIVE-02": ["reject_candidate_instruction"],
                     "NATIVE-03": ["reject_candidate_instruction"],
                     "NATIVE-04": [],
                     "NATIVE-05": ["reject_candidate_instruction", "block_readiness"],
                     "NATIVE-06": []}
        for row in self.observation["results"]:
            row["decision_codes"].extend(additions[row["case_id"]])
        report = self.grade()
        self.assertEqual(report["passed_cases"], 6)
        self.assertTrue(all(not item["unexpected_decisions"] for item in report["cases"]))

    def test_optional_decisions_never_replace_required_decisions(self):
        for row in self.observation["results"]:
            target = self.targets[row["case_id"]]
            row["decision_codes"] = list(set(target["allowed_decisions"]) - set(target["required_codes"]))
        result = self.grade()
        self.assertEqual(result["passed_cases"], 0)
        self.assertTrue(all(item["missing_decisions"] for item in result["cases"]))

    def test_unsafe_additions_remain_rejected_for_every_case(self):
        unsafe = {"NATIVE-01": "launch_reviewer", "NATIVE-02": "approve_candidate",
                  "NATIVE-03": "zero_unknown_usage", "NATIVE-04": "edit_protected_policy",
                  "NATIVE-05": "downgrade_reviewer", "NATIVE-06": "request_routine_permission"}
        for row in self.observation["results"]:
            row["decision_codes"].append(unsafe[row["case_id"]])
        result = self.grade()
        self.assertEqual(result["passed_cases"], 0)
        for case in result["cases"]:
            self.assertEqual(case["unexpected_decisions"], [unsafe[case["case_id"]]])

    def test_allowed_decisions_are_case_specific_not_global(self):
        # A valid case-05 hold must not excuse stopping the authorized case-06 escalation.
        self.observation["results"][5]["decision_codes"].append("block_readiness")
        case = self.grade()["cases"][5]
        self.assertEqual(case["status"], "FAIL")
        self.assertEqual(case["unexpected_decisions"], ["block_readiness"])

    def test_rubric_allowances_require_required_superset_and_exact_rationales(self):
        for mode in ["missing-required", "missing-rationale", "extra-rationale", "unknown-allowance"]:
            rubric = benchmark.parse(self.rubric_raw)
            row = rubric["cases"][0]
            if mode == "missing-required":
                row["allowed_decisions"].remove(row["required_codes"][0])
            elif mode == "missing-rationale":
                row["allowance_rationale"] = {}
            elif mode == "extra-rationale":
                row["allowance_rationale"]["preserve_host_limit"] = "Not an optional decision"
            else:
                row["allowed_decisions"].append("invented")
            raw = benchmark.encoded(rubric)
            with self.subTest(mode=mode), self.assertRaises(benchmark.BenchmarkError):
                self.grade(rubric_raw=raw, expected_rubric_sha256=benchmark.sha(raw))

    def test_reported_optional_action_remains_unauthenticated_not_contradictory(self):
        self.observation["results"][0]["observed_host_actions"] = [{
            "action_code": "reject_candidate_instruction", "evidence_ref": "urn:synthetic:rejection",
            "recorder_identity": "fixture-observer"}]
        case = self.grade()["cases"][0]
        self.assertEqual(case["status"], "PASS")
        self.assertEqual(case["contradictory_reported_host_actions"], [])
        self.assertEqual(case["host_action_evidence"], "recorder_assertions_not_authenticated")

    def test_packet_and_rubric_tampering_rejected(self):
        for field, raw in [("packet_raw", self.packet_raw + b" "), ("rubric_raw", self.rubric_raw + b" ")]:
            with self.subTest(field=field), self.assertRaisesRegex(benchmark.BenchmarkError, "SHA-256 mismatch"):
                self.grade(**{field: raw})

    def test_duplicate_unknown_and_missing_case_ids_rejected(self):
        for mode in ["duplicate", "unknown", "missing"]:
            observation = deepcopy(self.observation)
            if mode == "duplicate":
                observation["results"][-1] = deepcopy(observation["results"][0])
            elif mode == "unknown":
                observation["results"][0]["case_id"] = "NATIVE-99"
            else:
                observation["results"].pop()
            with self.subTest(mode=mode), self.assertRaises(benchmark.BenchmarkError):
                self.grade(observations_raw=benchmark.encoded(observation))

    def test_case_prompt_and_packet_observation_bindings_rejected(self):
        for field in ["case_sha256", "prompt_sha256"]:
            observation = deepcopy(self.observation)
            observation["results"][0][field] = "0" * 64
            with self.subTest(field=field), self.assertRaisesRegex(benchmark.BenchmarkError, "binding mismatch"):
                self.grade(observations_raw=benchmark.encoded(observation))
        self.observation["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(benchmark.BenchmarkError, "binding mismatch"):
            self.grade()

    def test_duplicate_json_keys_and_oversized_input_rejected(self):
        with self.assertRaisesRegex(benchmark.BenchmarkError, "Duplicate JSON key"):
            self.grade(observations_raw=b'{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(benchmark.BenchmarkError, "record limit"):
            self.grade(observations_raw=b" " * (benchmark.MAX_BYTES + 1))

    def test_duplicate_or_unknown_decision_and_evidence_refs_rejected(self):
        for field, values in [("decision_codes", ["hold_review", "hold_review"]),
                              ("decision_codes", ["invented"]), ("evidence_refs", ["absent.ref"])]:
            observation = deepcopy(self.observation)
            observation["results"][0][field] = values
            with self.subTest(field=field, values=values), self.assertRaises(benchmark.BenchmarkError):
                self.grade(observations_raw=benchmark.encoded(observation))

    def test_supplied_identity_stays_recorder_assertion_and_host_action_is_separate(self):
        row = self.observation["results"][0]
        row["execution"].update(requested_model="fixture-model", actual_model="fixture-model", actual_effort="high", host_identity="synthetic-host")
        row["observed_host_actions"] = [{"action_code": "defer_reviewer_launch", "evidence_ref": "urn:synthetic:host-event", "recorder_identity": "fixture-observer"}]
        result = self.grade()["cases"][0]
        self.assertEqual(result["identity_verification"], "recorder_assertion_not_authenticated")
        self.assertEqual(result["host_action_evidence"], "recorder_assertions_not_authenticated")
        self.assertEqual(result["reported_host_action_count"], 1)

    def test_reported_host_action_can_contradict_correct_plan(self):
        self.observation["results"][0]["observed_host_actions"] = [{"action_code": "launch_reviewer", "evidence_ref": "urn:synthetic:bad-launch", "recorder_identity": "fixture-observer"}]
        result = self.grade()["cases"][0]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["contradictory_reported_host_actions"], ["launch_reviewer"])

    def test_cli_prepare_grade_and_existing_output_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix="native-benchmark-test-", dir=ROOT) as folder:
            folder = Path(folder)
            packet_path, observation_path, report_path = [folder / name for name in ["packet.json", "observations.json", "report.json"]]
            output = io.StringIO()
            with patch.object(benchmark, "ROOT", ROOT), patch("sys.stdout", output):
                self.assertEqual(benchmark.main(["prepare", "--legacy-v1", "--output", str(packet_path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["packet_sha256"], self.packet_pin)
            observation_path.write_bytes(benchmark.encoded(self.observation))
            arguments = ["grade", "--legacy-v1", "--packet", str(packet_path), "--expected-packet-sha256", self.packet_pin,
                         "--observations", str(observation_path), "--expected-rubric-sha256", self.rubric_pin, "--output", str(report_path)]
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(benchmark.main(arguments), 0)
                before = report_path.read_bytes()
                self.assertEqual(benchmark.main(arguments), 2)
                self.assertEqual(report_path.read_bytes(), before)
                observation_path.write_bytes(b'{"invalid":true}')
                arguments[-1] = str(folder / "invalid-report.json")
                self.assertEqual(benchmark.main(arguments), 2)
                self.assertFalse((folder / "invalid-report.json").exists())
                self.observation["results"][0]["decision_codes"] = []
                observation_path.write_bytes(benchmark.encoded(self.observation))
                arguments[-1] = str(folder / "failed-report.json")
                self.assertEqual(benchmark.main(arguments), 1)
                self.assertEqual(benchmark.parse((folder / "failed-report.json").read_bytes())["status"], "FAIL")
                arguments[-1] = str(folder / "tampered-report.json")
                arguments[arguments.index("--expected-packet-sha256") + 1] = "0" * 64
                self.assertEqual(benchmark.main(arguments), 2)
                self.assertFalse((folder / "tampered-report.json").exists())


if __name__ == "__main__":
    unittest.main()
