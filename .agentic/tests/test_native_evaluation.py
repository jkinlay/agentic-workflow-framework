"""Synthetic evaluator mechanics; no model, provider or project action is run."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".agentic/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("native_evaluation_under_test", SCRIPTS / "evaluate_native.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
import benchmark_native as benchmark


class NativeEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-evaluation-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        packet, _ = benchmark.prepare(ROOT)
        self.packet = deepcopy(packet)
        self.packet["benchmark_id"] = "synthetic-evaluator-fixture-only"
        self.packet["cases"] = self.packet["cases"][:3]
        for case in self.packet["cases"]:
            case["task"] = "Synthetic test fixture; no model execution: " + case["task"]
            original = {key: case[key] for key in ("case_id", "role", "task", "inputs")}
            case["case_sha256"] = benchmark.sha(benchmark.encoded(original))
        self.packet["suite_sha256"] = benchmark.sha(benchmark.encoded(self.packet["cases"]))
        self.rubric = benchmark.parse(benchmark.read(ROOT / ".agentic/benchmarks/native/rubric.json"))
        self.rubric["benchmark_id"] = self.packet["benchmark_id"]
        self.rubric["visibility"] = "external_held_out"  # Synthetic API fixture, not a novelty claim.
        self.rubric["cases"] = self.rubric["cases"][:3]
        for target in self.rubric["cases"]:
            target["allowance_rationale"] = {key: "PRIVATE-RUBRIC-ONLY-SENTINEL" for key in target["allowance_rationale"]}
        self.packet_file, self.rubric_file = self.root / "packet.json", self.root / "rubric.json"
        self.executable, self.output = self.root / "synthetic-codex.exe", self.root / "result"
        self.executable.write_bytes(b"Synthetic executable fixture; never executed\n")
        self.write_inputs()

    def write_inputs(self):
        self.packet_file.write_bytes(benchmark.encoded(self.packet))
        self.rubric_file.write_bytes(benchmark.encoded(self.rubric))

    def args(self):
        args = ["--packet", str(self.packet_file), "--rubric", str(self.rubric_file),
                "--codex", str(self.executable), "--output", str(self.output),
                "--model", "synthetic-model", "--effort", "low", "--case-timeout", "1"]
        for label, path in (("packet", self.packet_file), ("rubric", self.rubric_file), ("codex", self.executable)):
            args += ["--expected-" + label + "-sha256", benchmark.sha(path.read_bytes())]
        return args

    def invoke(self, args=None):
        with redirect_stdout(io.StringIO()) as output:
            code = evaluation.main(args or self.args())
        return code, json.loads(output.getvalue())

    def fake_launcher(self, *, returncode=0, bad_events=None, answer_override=None, raw_answer=None):
        commands, prompts, processes = [], [], []
        def launch(command, **kwargs):
            index = len(commands)
            commands.append((command, kwargs))
            process = mock.Mock(returncode=returncode, pid=1000 + index)
            process.poll.return_value = returncode
            def communicate(raw=None, timeout=None):
                if raw is not None:
                    prompts.append(raw)
                events = bad_events if bad_events is not None else b'\n'.join([
                    benchmark.encoded({"type": "thread.started", "thread_id": "synthetic-thread-" + str(index)}).strip().replace(b'\n', b''),
                    b'{"type":"turn.started"}',
                    b'{"type":"item.completed","item":{"type":"agent_message","text":"synthetic"}}',
                    b'{"type":"turn.completed","usage":{"input_tokens":4,"output_tokens":3}}'])
                target = self.rubric["cases"][index]
                answer = {"decision_codes": target["required_codes"], "evidence_refs": target["required_evidence_refs"],
                          "rationale": "Synthetic mocked observation; no model executed."}
                if answer_override is not None:
                    answer = answer_override(answer, index) if callable(answer_override) else answer_override
                raw = raw_answer(answer, index) if callable(raw_answer) else raw_answer
                raw = raw if raw is not None else benchmark.encoded(answer)
                if bad_events is None:
                    rows = [benchmark.parse(line) for line in events.splitlines() if line]
                    rows[2]['item']['text'] = raw.decode('utf-8')
                    events = b'\n'.join(json.dumps(row).encode('utf-8') for row in rows)
                kwargs['stdout'].write(events)
                kwargs['stdout'].flush()
                Path(command[command.index("--output-last-message") + 1]).write_bytes(raw)
                return None, None
            process.communicate.side_effect = communicate
            processes.append(process)
            return process
        return launch, commands, prompts, processes

    def test_tools_malformed_and_incomplete_jsonl_events_reject(self):
        valid = b'{"type":"thread.started","thread_id":"synthetic"}\n'
        cases = [b'', b'not-json', b'{"type":"turn.started","type":"turn.completed"}',
                 valid + b'{"type":"item.completed","item":{"type":"command_execution"}}',
                 valid + b'{"type":"turn.failed"}',
                 valid + b'{"type":"turn.completed","usage":null}',
                 valid + b'{"type":"turn.completed","usage":{}}\n{"type":"turn.completed","usage":{}}']
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(benchmark.BenchmarkError):
                evaluation.observed_events(raw)

    def use_routine_batch(self):
        self.packet, _ = benchmark.prepare(ROOT, suite="routine")
        self.packet["cases"] = self.packet["cases"][:3]
        self.rubric = benchmark.parse(benchmark.read(ROOT / ".agentic/benchmarks/native/routine-rubric.json"))
        self.rubric["cases"] = self.rubric["cases"][:3]
        self.rubric["visibility"] = "external_public_routine"
        self.write_inputs()

    def test_public_routine_batch_is_not_mislabeled_held_out(self):
        self.use_routine_batch()
        launch, commands, _, _ = self.fake_launcher()
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((0, "MEASURED", 3, "public_routine_not_held_out"),
                         (code, report["status"], len(commands), report["case_exposure"]))
        self.assertEqual("PASS", report["decision_grade"])

    def test_invalid_structured_routine_input_never_launches(self):
        self.use_routine_batch()
        case = self.packet["cases"][0]
        state_input = case["inputs"][0]
        state = json.loads(state_input["content"])
        state["publication_authorized"] = "yes"
        state_input["content"] = json.dumps(state)
        case["case_sha256"] = benchmark.sha(benchmark.encoded({key: case[key] for key in ("case_id", "role", "task", "inputs")}))
        self.write_inputs()
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=AssertionError("No launch on malformed state")):
            code, report = self.invoke()
        self.assertEqual((2, "NOT_RUN", []), (code, report["status"], report["runs"]))
        self.assertFalse(self.output.exists())

    def test_malformed_input_shapes_return_not_run_without_traceback_or_launch(self):
        for inputs in ([1], None, "not-an-input-list", {"ref": "routine.state"}):
            with self.subTest(inputs=inputs):
                self.use_routine_batch()
                case = self.packet["cases"][0]
                case["inputs"] = inputs
                case["case_sha256"] = benchmark.sha(benchmark.encoded({key: case[key] for key in ("case_id", "role", "task", "inputs")}))
                self.write_inputs()
                with mock.patch.object(evaluation.subprocess, "Popen", side_effect=AssertionError("No malformed-input launch")):
                    code, report = self.invoke()
                self.assertEqual((2, "NOT_RUN", []), (code, report["status"], report["runs"]))
                self.assertFalse(self.output.exists())

    def test_routine_precondition_violation_stops_after_first_mocked_response(self):
        self.use_routine_batch()
        # Deliberately weaken the public rubric: independent routine checks still reject.
        self.rubric["cases"][0]["required_codes"] = ["mark_pr_ready"]
        self.rubric["cases"][0]["allowed_decisions"] = ["mark_pr_ready"]
        self.write_inputs()
        launch, commands, _, _ = self.fake_launcher()
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((2, "INVALID_RESPONSE", 1), (code, report["status"], len(commands)))
        self.assertEqual(["NATIVE-35", "NATIVE-36"], report["remaining_cases_not_run"])
        self.assertEqual("NOT_GRADED", report["decision_grade"])

    def test_successful_mocked_execution_retains_unknown_actual_identity_and_evidence(self):
        launch, commands, prompts, _ = self.fake_launcher()
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((0, "MEASURED", 3), (code, report["status"], len(commands)))
        self.assertFalse(report["provider_qualified"])
        self.assertFalse(report["comparative_quality_established"])
        observations = benchmark.parse((self.output / "observations.json").read_bytes())
        for result in observations["results"]:
            self.assertIsNone(result["execution"]["actual_model"])
            self.assertIsNone(result["execution"]["actual_effort"])
            self.assertEqual([], result["observed_host_actions"])
        for index, run in enumerate(report["runs"]):
            self.assertIsNone(run["actual_model"])
            self.assertEqual({"input_tokens": 4, "output_tokens": 3}, run["usage"])
            self.assertTrue((self.output / self.packet["cases"][index]["case_id"] / "events.jsonl").is_file())
        for prompt, (command, kwargs) in zip(prompts, commands):
            self.assertNotIn(b"PRIVATE-RUBRIC-ONLY-SENTINEL", prompt)
            self.assertNotIn(str(self.rubric_file).encode(), prompt)
            self.assertNotIn(str(ROOT).encode(), prompt)
            self.assertIn("--ephemeral", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("features.shell_tool=false", command)
            self.assertNotIn("shell", kwargs)
            self.assertFalse(Path(kwargs["cwd"]).is_relative_to(ROOT))
            self.assertIn(b"decision_definitions", prompt)
            self.assertIn(b"continue_unaffected_work", prompt)
        self.assertFalse(report["provider_schema_live_verified"])
        self.assertIsNone(report["unsafe_action_rate"])

    def test_invalid_completed_response_retains_observed_usage_without_retry(self):
        answer = {"decision_codes": ["pause_affected_work", "continue_affected_work"],
                  "evidence_refs": [], "rationale": "Synthetic contradiction; continue only unaffected work."}
        launch, commands, _, _ = self.fake_launcher(answer_override=answer)
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((2, "INVALID_RESPONSE", "NOT_GRADED", 1),
                         (code, report["status"], report["decision_grade"], len(commands)))
        self.assertFalse(report["automatic_retry"])
        self.assertFalse(report["unresolved_usage"])
        self.assertTrue(report["invocation_completed"])
        self.assertEqual(2, len(report["remaining_cases_not_run"]))
        self.assertEqual({"input_tokens": 4, "output_tokens": 3}, report["runs"][0]["usage"])
        folder = self.output / self.packet["cases"][0]["case_id"]
        self.assertEqual(answer, benchmark.parse((folder / "raw-response.json").read_bytes()))
        self.assertTrue((folder / "provenance.json").exists())
        self.assertEqual("INVALID_RESPONSE", benchmark.parse((folder / "contract-validation.json").read_bytes())["status"])
        self.assertFalse((self.output / "grade.json").exists())

    def test_malformed_json_after_completed_turn_keeps_usage_and_raw_bytes(self):
        launch, commands, _, _ = self.fake_launcher(raw_answer=b'{"not complete"')
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((2, "INVALID_RESPONSE", 1), (code, report["status"], len(commands)))
        self.assertFalse(report["unresolved_usage"])
        self.assertEqual(4, report["runs"][0]["usage"]["input_tokens"])
        self.assertEqual(b'{"not complete"', (self.output / self.packet["cases"][0]["case_id"] / "raw-response.json").read_bytes())

    def test_invalid_completed_response_with_missing_usage_stays_explicitly_unknown(self):
        events = b'{"type":"thread.started","thread_id":"synthetic"}\n{"type":"turn.completed","usage":{}}'
        launch, commands, _, _ = self.fake_launcher(raw_answer=b'null', bad_events=events)
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((2, "INVALID_RESPONSE", 1), (code, report["status"], len(commands)))
        self.assertTrue(report["invocation_completed"])
        self.assertTrue(report["unresolved_usage"])
        self.assertEqual({}, report["runs"][0]["usage"])

    def test_missing_unreadable_oversized_completed_output_retains_known_usage(self):
        for number, error in enumerate((FileNotFoundError("synthetic"), PermissionError("synthetic"),
                                        benchmark.BenchmarkError("Smoke input too large"))):
            self.output = self.root / ("capture-failure-" + str(number))
            launch, commands, _, _ = self.fake_launcher()
            original_read = evaluation.read
            def fail_response(path):
                if Path(path).name == "response.json":
                    raise error
                return original_read(path)
            with self.subTest(error=type(error).__name__), mock.patch.object(evaluation, "read", side_effect=fail_response), \
                    mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
                code, report = self.invoke()
            self.assertEqual((2, "INVALID_RESPONSE", 1), (code, report["status"], len(commands)))
            self.assertFalse(report["unresolved_usage"])
            self.assertTrue(report["invocation_completed"])
            self.assertEqual(4, report["runs"][0]["usage"]["input_tokens"])
            self.assertIsNone(report["runs"][0]["response_sha256"])
            self.assertFalse(report["runs"][0]["raw_response_retained"])
            self.assertIn("response_capture_error", report["runs"][0])
            self.assertTrue((self.output / self.packet["cases"][0]["case_id"] / "provenance.json").exists())

    def test_legacy_and_self_consistent_weakened_contract_never_launch(self):
        original = deepcopy(self.packet)
        for mode in ("legacy", "schema", "definition"):
            self.packet = deepcopy(original)
            if mode == "legacy":
                self.packet["schema_version"] = 1
                self.packet.pop("decision_contract")
            elif mode == "schema":
                self.packet["decision_contract"]["response_schema"]["allOf"] = []
                self.packet["decision_contract"]["response_schema_sha256"] = benchmark.sha(benchmark.encoded(self.packet["decision_contract"]["response_schema"]))
            else:
                self.packet["decision_contract"]["vocabulary"]["codes"]["continue_affected_work"]["preconditions"] = "Always continue."
            self.write_inputs()
            with self.subTest(mode=mode), mock.patch.object(evaluation.subprocess, "Popen") as launch:
                code, report = self.invoke()
            self.assertEqual((2, "NOT_RUN"), (code, report["status"]))
            launch.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_self_consistent_wrong_release_prompt_rejects_before_launch_or_output(self):
        case = self.packet["cases"][0]
        case["prompt"] = "Self-consistent forged prompt; not this release's generated role prompt.\n"
        case["prompt_sha256"] = benchmark.sha(case["prompt"].encode())
        self.write_inputs()
        with mock.patch.object(evaluation.subprocess, "Popen") as launch:
            code, report = self.invoke()
        self.assertEqual((2, "NOT_RUN"), (code, report["status"]))
        self.assertIn("prompt differs", report["reason"])
        launch.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_packet_rubric_and_executable_hashes_bound_before_any_launch(self):
        for option in ("--expected-packet-sha256", "--expected-rubric-sha256", "--expected-codex-sha256"):
            args = self.args()
            args[args.index(option) + 1] = "0" * 64
            with self.subTest(option=option), mock.patch.object(evaluation.subprocess, "Popen") as launch:
                code, report = self.invoke(args)
            self.assertEqual((2, "NOT_RUN"), (code, report["status"]))
            launch.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved_without_adding_an_error_report(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_bytes(b"Existing owner evidence\n")
        with mock.patch.object(evaluation.subprocess, "Popen") as launch:
            code, report = self.invoke()
        self.assertEqual((2, "NOT_RUN"), (code, report["status"]))
        launch.assert_not_called()
        self.assertEqual(["keep.txt"], [path.name for path in self.output.iterdir()])
        self.assertEqual(b"Existing owner evidence\n", (self.output / "keep.txt").read_bytes())

    def test_failure_after_process_launch_is_incomplete_with_unknown_usage(self):
        launch, commands, _, _ = self.fake_launcher(returncode=1)
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((2, "INCOMPLETE", 1), (code, report["status"], len(commands)))
        self.assertTrue(report["attempted_execution"])
        self.assertTrue(report["unresolved_usage"])
        self.assertFalse(report["automatic_retry"])
        self.assertTrue((self.output / "evaluation.json").is_file())
        self.assertTrue((self.output / self.packet["cases"][0]["case_id"] / "launch.json").is_file())

    def test_tool_event_after_launch_stays_incomplete_and_retains_event_bytes(self):
        raw = b'{"type":"item.completed","item":{"type":"command_execution","command":"synthetic"}}'
        launch, commands, _, _ = self.fake_launcher(bad_events=raw)
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch):
            code, report = self.invoke()
        self.assertEqual((2, "INCOMPLETE", 1), (code, report["status"], len(commands)))
        self.assertIn("Tool or unknown item", report["reason"])
        self.assertEqual(raw, (self.output / self.packet["cases"][0]["case_id"] / "events.jsonl").read_bytes())

    def test_launch_record_write_failure_reaps_started_child(self):
        process = mock.Mock(returncode=None, pid=999)
        process.poll.return_value = None
        destination = self.root / "case-output"
        destination.mkdir()
        real_write = evaluation.write_new
        def fail_launch(path, value):
            if Path(path).name == "launch.json":
                raise OSError("synthetic disk failure")
            return real_write(path, value)
        with mock.patch.object(evaluation.subprocess, "Popen", return_value=process), \
                mock.patch.object(evaluation, "write_new", side_effect=fail_launch), \
                self.assertRaises(OSError):
            evaluation.execute_case(self.executable, self.packet["cases"][0], self.packet,
                                    "synthetic-model", "low", 1, destination)
        process.kill.assert_called_once()
        process.communicate.assert_called_once_with(timeout=2)

    def test_launch_record_failure_keeps_in_memory_attempt_and_incomplete_report(self):
        process = mock.Mock(returncode=None, pid=999)
        process.poll.return_value = None
        real_write = evaluation.write_new
        def fail_launch(path, value):
            if Path(path).name == "launch.json":
                raise OSError("synthetic disk failure")
            return real_write(path, value)
        with mock.patch.object(evaluation.subprocess, "Popen", return_value=process), \
                mock.patch.object(evaluation, "write_new", side_effect=fail_launch):
            code, report = self.invoke()
        self.assertEqual((2, "INCOMPLETE"), (code, report["status"]))
        self.assertTrue(report["attempted_execution"])
        self.assertTrue(report["unresolved_usage"])
        self.assertTrue(report["attempts"][0]["attempted"])
        self.assertFalse(list(self.output.glob("*/launch.json")))
        self.assertTrue((self.output / "evaluation.json").is_file())
        process.kill.assert_called_once()
        process.communicate.assert_called_once_with(timeout=2)

    def test_late_process_success_is_rejected_before_accepting_observation(self):
        destination = self.root / "late-case"
        destination.mkdir()
        launch, commands, _, processes = self.fake_launcher()
        attempted = {"attempted": False}
        with mock.patch.object(evaluation.subprocess, "Popen", side_effect=launch), \
                mock.patch.object(evaluation.time, "monotonic", side_effect=[0, 0.1, 1.1]), \
                self.assertRaisesRegex(benchmark.BenchmarkError, "after deadline"):
            evaluation.execute_case(self.executable, self.packet["cases"][0], self.packet,
                                    "synthetic-model", "low", 1, destination, attempted)
        self.assertTrue(attempted["attempted"])
        self.assertEqual(1, len(commands))
        self.assertEqual(1, processes[0].communicate.call_count)
        self.assertFalse((destination / "raw-response.json").exists())

    def measurement_args(self):
        self.use_routine_batch()
        rubric = benchmark.parse(benchmark.read(ROOT / '.agentic/benchmarks/native/routine-measurement-rubric.json'))
        rubric['cases'] = rubric['cases'][:3]
        self.measurement_file = self.root / 'measurement-rubric.json'
        self.measurement_file.write_bytes(benchmark.encoded(rubric))
        return self.args() + ['--measurement-rubric', str(self.measurement_file),
                             '--expected-measurement-rubric-sha256', benchmark.sha(self.measurement_file.read_bytes())]

    def test_prospective_defensive_extras_are_reported_and_all_cases_run(self):
        args = self.measurement_args()
        def extras(answer, index):
            if index == 0:
                answer['decision_codes'] += ['defer_reviewer_launch', 'reconcile_remote_state']
            return answer
        launch, commands, _, _ = self.fake_launcher(answer_override=extras)
        with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
            code, report = self.invoke(args)
        self.assertEqual((0, 'MEASURED', 'PASS', 'FAIL', 3),
                         (code, report['status'], report['decision_grade'], report['strict_grade'], len(commands)))
        first = report['measurements'][0]
        self.assertEqual((2, 2, True, 2, 'INVALID_RESPONSE'), (first['required_present'], first['required_total'],
                         first['all_prohibited_absent'], first['extras_count'], first['strict_grade']))
        self.assertEqual(['defer_reviewer_launch', 'reconcile_remote_state'], first['extras'])
        self.assertFalse(report['unresolved_usage'])
        self.assertFalse((self.output / 'observations.json').exists())

    def test_prospective_missing_required_or_prohibited_codes_fail_without_stopping(self):
        args = self.measurement_args()
        def failing(answer, index):
            if index == 0:
                answer['decision_codes'] = ['publish_branch']
            if index == 1:
                answer['decision_codes'].append('continue_affected_work')
            return answer
        launch, commands, _, _ = self.fake_launcher(answer_override=failing)
        with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
            code, report = self.invoke(args)
        self.assertEqual((0, 'MEASURED', 'FAIL', 1, 3),
                         (code, report['status'], report['decision_grade'], report['passed_cases'], len(commands)))
        self.assertEqual(['open_draft_pr'], report['measurements'][0]['missing_required'])
        self.assertEqual(['continue_affected_work'], report['measurements'][1]['prohibited_selected'])
        self.assertEqual('INVALID_RESPONSE', report['measurements'][1]['strict_grade'])

    def test_prospective_malformed_json_has_unavailable_metrics_and_continues(self):
        args = self.measurement_args()
        launch, commands, _, _ = self.fake_launcher(raw_answer=lambda answer, index: b'{bad' if index == 0 else benchmark.encoded(answer))
        with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
            code, report = self.invoke(args)
        self.assertEqual((0, 'MEASURED', 3, 2), (code, report['status'], len(commands), report['passed_cases']))
        row = report['measurements'][0]
        self.assertEqual('UNAVAILABLE', row['substantive_grade'])
        for key in ('required_present', 'prohibited_absent', 'extras_count', 'all_prohibited_absent'):
            self.assertIsNone(row[key])
        self.assertEqual(b'{bad', (self.output / 'NATIVE-34/raw-response.json').read_bytes())
        self.assertEqual(4, report['runs'][0]['usage']['input_tokens'])

    def test_prospective_invalid_usage_stops_before_next_case_and_preserves_observation(self):
        for index, usage in enumerate(({}, {'input_tokens': True, 'output_tokens': 3},
                                      {'input_tokens': 4, 'output_tokens': -1},
                                      {'input_tokens': 4, 'output_tokens': 3, 'reasoning_output_tokens': 'unknown'})):
            self.output = self.root / f'usage-failure-{index}'
            args = self.measurement_args()
            events = (json.dumps({'type': 'thread.started', 'thread_id': 'synthetic'}) + '\n' +
                      json.dumps({'type': 'turn.completed', 'usage': usage})).encode()
            launch, commands, _, _ = self.fake_launcher(bad_events=events)
            with self.subTest(usage=usage), mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
                code, report = self.invoke(args)
            self.assertEqual((2, 'INCOMPLETE', 1), (code, report['status'], len(commands)))
            self.assertEqual(usage, report['runs'][0]['usage'])
            self.assertEqual(index != 3, report['unresolved_usage'])
            self.assertTrue(report['usage_errors'])
            self.assertFalse((self.output / 'NATIVE-35').exists())

    def test_prospective_capture_failure_stops_but_keeps_completed_usage(self):
        args = self.measurement_args()
        launch, commands, _, _ = self.fake_launcher()
        original_read = evaluation.read
        def capture_failure(path):
            if Path(path).name == 'response.json':
                raise OSError('Synthetic output storage failure')
            return original_read(path)
        with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch), mock.patch.object(evaluation, 'read', side_effect=capture_failure):
            code, report = self.invoke(args)
        self.assertEqual((2, 'INCOMPLETE', 1), (code, report['status'], len(commands)))
        self.assertEqual(4, report['runs'][0]['usage']['input_tokens'])
        self.assertFalse(report['unresolved_usage'])

    def test_prospective_changed_measurement_input_stops_before_second_call(self):
        args = self.measurement_args()
        def mutate(answer, index):
            if index == 0:
                self.measurement_file.write_bytes(b'{}')
            return answer
        launch, commands, _, _ = self.fake_launcher(answer_override=mutate)
        with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
            code, report = self.invoke(args)
        self.assertEqual((2, 'INCOMPLETE', 1), (code, report['status'], len(commands)))
        self.assertEqual(1, len(report['measurements']))

    def test_prospective_rubric_pin_and_required_state_mismatch_prevent_all_launches(self):
        args = self.measurement_args()
        rubric = benchmark.parse(self.measurement_file.read_bytes())
        rubric['cases'][0]['required_codes'] = []
        self.measurement_file.write_bytes(benchmark.encoded(rubric))
        args[-1] = benchmark.sha(self.measurement_file.read_bytes())
        with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=AssertionError('No changed required set launch')):
            code, report = self.invoke(args)
        self.assertEqual((2, 'NOT_RUN', []), (code, report['status'], report['runs']))
        self.assertFalse(self.output.exists())

    def test_prospective_final_message_mismatch_stops_with_known_usage(self):
        args = self.measurement_args()
        for index, item in enumerate((None, {'type': 'agent_message', 'text': 42},
                                     {'type': 'agent_message', 'text': '{"decision_codes":["merge_candidate"]}'})):
            self.output = self.root / f'message-mismatch-{index}'
            args[args.index('--output') + 1] = str(self.output)
            events = [{'type': 'thread.started', 'thread_id': 'synthetic'},
                      {'type': 'turn.completed', 'usage': {'input_tokens': 4, 'output_tokens': 3}}]
            if item is not None:
                events.insert(1, {'type': 'item.completed', 'item': item})
            raw_events = b'\n'.join(json.dumps(event).encode() for event in events)
            launch, commands, _, _ = self.fake_launcher(bad_events=raw_events)
            with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
                code, report = self.invoke(args)
            self.assertEqual((2, 'INCOMPLETE', 1), (code, report['status'], len(commands)))
            self.assertFalse(report['unresolved_usage'])
            self.assertEqual(4, report['runs'][0]['usage']['input_tokens'])
            self.assertFalse((self.output / 'NATIVE-35').exists())

    def test_final_message_binding_only_tolerates_one_terminal_newline(self):
        def event(text):
            return json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}).encode()
        for text in ('{}', '{}\n', '{}\r\n'):
            for raw in (b'{}', b'{}\n', b'{}\r\n'):
                self.assertFalse(evaluation.response_event_errors(event(text), raw))
        for raw in (b'{ }', b'{}\n\n', b' {}', b'{"x":1}'):
            self.assertTrue(evaluation.response_event_errors(event('{}'), raw))
        events = event('earlier message') + b'\n' + event('{}')
        self.assertFalse(evaluation.response_event_errors(events, b'{}'))

    def test_prospective_completed_tool_or_failed_exit_preserves_raw_and_known_usage(self):
        args = self.measurement_args()
        for index, returncode in enumerate((0, 1)):
            self.output = self.root / f'unqualified-completed-{index}'
            args[args.index('--output') + 1] = str(self.output)
            events = [{'type': 'thread.started', 'thread_id': 'synthetic'},
                      {'type': 'item.completed', 'item': {'type': 'command_execution'}},
                      {'type': 'turn.completed', 'usage': {'input_tokens': 4, 'output_tokens': 3}}]
            launch, commands, _, _ = self.fake_launcher(returncode=returncode,
                bad_events=b'\n'.join(json.dumps(event).encode() for event in events))
            with mock.patch.object(evaluation.subprocess, 'Popen', side_effect=launch):
                code, report = self.invoke(args)
            self.assertEqual((2, 'INCOMPLETE', 1), (code, report['status'], len(commands)))
            self.assertFalse(report['unresolved_usage'])
            self.assertEqual(4, report['runs'][0]['usage']['input_tokens'])
            self.assertIn('qualification_error', report['runs'][0])
            self.assertTrue((self.output / 'NATIVE-34/raw-response.json').exists())
            self.assertFalse((self.output / 'NATIVE-34/measurement.json').exists())


if __name__ == "__main__":
    unittest.main()
