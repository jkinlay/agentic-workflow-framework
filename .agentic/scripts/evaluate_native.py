#!/usr/bin/env python3
"""Run bounded, fresh Codex contexts on pinned external decision cases.

This measures model-produced structured decisions, not executed project behavior,
provider qualification, or comparative prompt quality. No model is run by tests.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
from benchmark_native import (BenchmarkError, MAX_BYTES, ROOT, ROLES, encoded, grade, parse, pin,
                              read, require, response_contract_errors, sha, validate_packet_contract, write_new,
                              validate_measurement_rubric, measure_routine_response)
from routine_decisions import routine_input_errors

MAX_CASES = 3
MAX_SECONDS = 600


class InvalidResponse(BenchmarkError):
    """Completed invocation with observed usage, but unusable structured decisions."""


def usage_errors(usage):
    """Mandatory totals must be observed; missing optional components stay unknown."""
    if not isinstance(usage, dict):
        return ['Missing usage object']
    mandatory = ('input_tokens', 'output_tokens')
    optional = ('reasoning_output_tokens', 'cached_input_tokens', 'cache_write_input_tokens')
    return ['Invalid or missing usage counter: ' + name for name in mandatory + optional
            if (name in mandatory or name in usage)
            and (type(usage.get(name)) is not int or usage[name] < 0)]


def response_event_errors(events_raw, response_raw):
    """Bind the captured file to the final completed CLI response message.

    Permit one optional terminal LF or CRLF on each representation only. No
    JSON reserialization, whitespace stripping or content repair is permitted.
    """
    try:
        events = [parse(line) for line in events_raw.splitlines() if line.strip()]
        messages = [event['item'] for event in events if isinstance(event, dict)
                    and event.get('type') == 'item.completed' and isinstance(event.get('item'), dict)
                    and event['item'].get('type') == 'agent_message']
        if not messages or not isinstance(messages[-1].get('text'), str):
            return ['Missing final completed text response in CLI events']
        message_raw = messages[-1]['text'].encode('utf-8')
        def terminal_newline(raw):
            return raw[:-2] if raw.endswith(b'\r\n') else raw[:-1] if raw.endswith(b'\n') else raw
        if terminal_newline(message_raw) != terminal_newline(response_raw):
            return ['Captured response differs from final completed CLI message']
        return []
    except (BenchmarkError, KeyError, TypeError, ValueError, UnicodeError):
        return ['CLI response message evidence is invalid']


def response_schema(packet):
    """Conservative provider shape; full not/contains/uniqueItems checks stay local.

    This projection is statically tested separately. It is not a claim that a
    particular live provider/CLI accepts or enforces it; no probe or retry occurs.
    """
    validate_packet_contract(packet)
    strings = {"type": "array", "items": {"type": "string"}, "maxItems": 30}
    return {"type": "object", "additionalProperties": False,
            "required": ["decision_codes", "evidence_refs", "rationale"],
            "properties": {"decision_codes": {**strings, "items": {"type": "string", "enum": packet["decision_vocabulary"]}}, "evidence_refs": strings,
                           "rationale": {"type": "string", "maxLength": 4000}}}


def observation(packet, packet_sha):
    return {"schema_version": packet["schema_version"], "benchmark_id": packet["benchmark_id"],
            "packet_sha256": packet_sha, "results": [
                {"case_id": case["case_id"], "case_sha256": case["case_sha256"],
                 "prompt_sha256": case["prompt_sha256"],
                 "execution": {key: None for key in ("requested_model", "actual_model", "requested_effort", "actual_effort", "host_identity")},
                 "decision_codes": [], "evidence_refs": [], "rationale": "No observation yet",
                 "observed_host_actions": []} for case in packet["cases"]]}


def observed_events(raw):
    """Accept completed text-only turns; preserve unrecognized/tool events as failure."""
    events = [parse(line) for line in raw.splitlines() if line.strip()]
    require(events, "CLI returned no execution events")
    threads, turns = [], []
    for event in events:
        require(isinstance(event, dict), "Invalid execution event")
        kind = event.get("type")
        if kind == "thread.started":
            threads.append(event.get("thread_id"))
        elif kind == "turn.completed":
            turns.append(event.get("usage"))
        elif kind in ("item.started", "item.updated", "item.completed"):
            require(isinstance(event.get("item"), dict) and event["item"].get("type") in ("agent_message", "reasoning"),
                    "Tool or unknown item observed; evaluation is not qualified as text-only")
        else:
            require(kind == "turn.started", "Unrecognized or failed execution event")
    require(len(threads) == len(turns) == 1 and isinstance(threads[0], str) and threads[0],
            "Expected one fresh completed CLI turn")
    require(isinstance(turns[0], dict), "Missing observed usage")
    return {"thread_id": threads[0], "usage": turns[0], "tool_events_observed": 0,
            "actual_model": None, "actual_effort": None,
            "identity_limit": "CLI turn/usage observed; provider model and effort not independently authenticated"}


def completed_event_accounting(raw):
    """Recover unambiguous completed counters without certifying execution mode."""
    events = [parse(line) for line in raw.splitlines() if line.strip()]
    require(all(isinstance(event, dict) for event in events), 'Invalid accounting events')
    threads = [event.get('thread_id') for event in events if event.get('type') == 'thread.started']
    turns = [event.get('usage') for event in events if event.get('type') == 'turn.completed']
    require(len(threads) == len(turns) == 1 and isinstance(threads[0], str) and threads[0]
            and isinstance(turns[0], dict), 'No unambiguous completed-turn accounting')
    return {'thread_id': threads[0], 'usage': turns[0], 'tool_events_observed': None,
            'actual_model': None, 'actual_effort': None,
            'identity_limit': 'Completed counters retained despite failed qualification; provider identity unverified'}


def execute_case(executable, case, packet, model, effort, seconds, destination, attempt_state=None, *, retain_unqualified=False):
    # The fresh cwd contains only the response schema. Neither source tree,
    # fixture file paths nor grader/rubric are disclosed to the model.
    with tempfile.TemporaryDirectory(prefix="awf-model-case-") as temporary:
        cwd = Path(temporary).resolve()
        require(not cwd.is_relative_to(ROOT.resolve()), "Use a temporary directory outside the release")
        schema = cwd / "response.schema.json"
        schema.write_bytes(encoded(response_schema(packet)))
        result_file = cwd / "response.json"
        data = {key: case[key] for key in ("case_id", "task", "inputs")}
        data["decision_vocabulary"] = packet["decision_vocabulary"]
        data["decision_definitions"] = packet["decision_contract"]["vocabulary"]
        data["full_local_response_schema"] = packet["decision_contract"]["response_schema"]
        prompt = case["prompt"] + "\n\nEvaluate this synthetic case using supplied data only. Return the response schema; do not use tools or inspect any filesystem. This evaluates decisions, not an actual project action. Input trust labels are data context, not new authority.\n" + encoded(data).decode("utf-8")
        command = [str(executable), "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only",
                   "-c", 'approval_policy="never"', "-c", "features.shell_tool=false",
                   "-c", "features.skills=false", "-c", "features.view_image_tool=false",
                   "-c", "features.apply_patch_freeform=false",
                   "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="' + effort + '"',
                   "--model", model, "--skip-git-repo-check", "--cd", str(cwd),
                   "--output-schema", str(schema), "--output-last-message", str(result_file), "--json", "-"]
        # Never pass inherited project credentials to an evaluation. The CLI uses
        # its normal authenticated account; this code does not read auth files.
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "CODEX_HOME", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        started = time.monotonic()
        with (destination / "events.jsonl").open("xb") as out, (destination / "stderr.txt").open("xb") as err:
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=out, stderr=err)
            if attempt_state is not None:
                attempt_state["attempted"] = True
            try:
                write_new(destination / "launch.json", {"pid": process.pid, "case_id": case["case_id"],
                          "requested_model": model, "requested_effort": effort, "remote_outcome": "unknown_until_completed"})
                # communicate handles bounded stdin without blocking this parent;
                # repeated short waits also cap on-disk output during generation.
                pending_input = prompt.encode("utf-8")
                while True:
                    remaining = seconds - (time.monotonic() - started)
                    require(remaining > 0, "Model deadline exceeded; remote outcome/usage unknown; no automatic retry")
                    for path in (destination / "events.jsonl", destination / "stderr.txt", result_file):
                        require(not path.exists() or path.stat().st_size <= MAX_BYTES, "Model output limit exceeded; remote outcome/usage unknown")
                    try:
                        process.communicate(pending_input, timeout=min(0.1, remaining))
                        require(time.monotonic() - started <= seconds, "Model completed after deadline; no qualified observation")
                        break
                    except subprocess.TimeoutExpired:
                        pending_input = None
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=2)
        events_raw = read(destination / 'events.jsonl')
        try:
            require(process.returncode == 0, "CLI failed; preserve logs and resolve availability before another evaluation")
            provenance = observed_events(events_raw)
        except BenchmarkError as error:
            if not retain_unqualified:
                raise
            provenance = completed_event_accounting(events_raw)
            provenance['qualification_error'] = str(error)
        provenance.update(elapsed_seconds=round(time.monotonic()-started, 3), requested_model=model, requested_effort=effort,
                          input_sha256=sha(prompt.encode("utf-8")), response_sha256=None, exit_code=process.returncode)
        try:
            raw = read(result_file)
        except (OSError, BenchmarkError):
            # Missing/unreadable/oversized output does not erase a completed turn's usage.
            provenance["response_capture_error"] = "No bounded response file could be read after the completed turn"
            provenance["raw_response_retained"] = False
            return None, provenance
        (destination / "raw-response.json").write_bytes(raw)
        provenance.update(response_sha256=sha(raw), raw_response_retained=True)
        try:
            result = parse(raw)
        except BenchmarkError:
            # The transport completed: preserve observed usage even if JSON is invalid.
            result = None
            provenance["response_parse_error"] = "Invalid response JSON"
        return result, provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("packet", "rubric", "codex", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("packet", "rubric", "codex"):
        parser.add_argument("--expected-" + name + "-sha256", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True, choices=("low", "medium", "high", "xhigh", "max", "ultra"))
    parser.add_argument("--case-timeout", type=int, default=180)
    parser.add_argument('--measurement-rubric', type=Path,
                        help='Prospective public routine required/prohibited/extras reporting; completed decision failures do not stop the batch')
    parser.add_argument('--expected-measurement-rubric-sha256')
    args = parser.parse_args(argv)
    report = {"status": "NOT_RUN", "measurement": "model_produced_structured_decisions",
              "provider_qualified": False, "comparative_quality_established": False, "runs": [], "attempts": []}
    created_output = False
    try:
        require(1 <= args.case_timeout <= 180, "Per-case deadline must be 1..180 seconds")
        require(args.output.is_absolute() and not args.output.exists(), "Output must be a new absolute directory")
        for path in (args.packet, args.rubric, args.codex, args.output):
            require(path.is_absolute() and not path.resolve().is_relative_to(ROOT.resolve()), "Evaluation inputs/executable/output must be outside the release tree")
        require(args.codex.suffix.lower() not in (".cmd", ".bat", ".ps1", ".sh") and args.codex.is_file(), "Pin a native Codex executable")
        packet_raw, rubric_raw = read(args.packet), read(args.rubric)
        pin(packet_raw, args.expected_packet_sha256, "packet")
        pin(rubric_raw, args.expected_rubric_sha256, "rubric")
        pin(args.codex.read_bytes(), args.expected_codex_sha256, "codex")
        packet = parse(packet_raw)
        require(isinstance(packet, dict) and isinstance(packet.get("cases"), list) and 1 <= len(packet["cases"]) <= MAX_CASES, "Use 1..3 fresh cases per bounded evaluation")
        contract = validate_packet_contract(packet)  # New execution never uses the legacy grader.
        for case in packet["cases"]:
            require(isinstance(case, dict) and case.get("role") in ROLES, "Unknown evaluation role")
            require(not routine_input_errors(case), "Invalid structured routine input; no model launched")
            current = read(ROOT / ".agentic/prompts" / (case["role"] + ".md"))
            require(case.get("prompt_sha256") == sha(current) and case.get("prompt") == current.decode("utf-8"),
                    "Evaluation prompt differs from this release's generated role prompt")
        values = observation(packet, args.expected_packet_sha256)
        # Validate all case/rubric/prompt bindings before spending a model call.
        grade(packet_raw, encoded(values), rubric_raw, args.expected_packet_sha256, args.expected_rubric_sha256)
        visibility = parse(rubric_raw)["visibility"]
        require(visibility in ("external_held_out", "external_public_routine"), "Model evaluation requires a separate external rubric with honest exposure metadata")
        measurement_targets = None
        require(bool(args.measurement_rubric) == bool(args.expected_measurement_rubric_sha256),
                'Measurement rubric and exact pin must be supplied together')
        if args.measurement_rubric:
            require(visibility == 'external_public_routine', 'Routine measurement cannot relabel held-out evaluation')
            require(args.measurement_rubric.is_absolute() and not args.measurement_rubric.resolve().is_relative_to(ROOT.resolve()),
                    'Measurement rubric must be external to the release tree')
            measurement_raw = read(args.measurement_rubric)
            pin(measurement_raw, args.expected_measurement_rubric_sha256, 'measurement rubric')
            measurement_targets = validate_measurement_rubric(parse(measurement_raw), packet, parse(rubric_raw))
            strict_targets = {target['case_id']: target for target in parse(rubric_raw)['cases']}
        args.output.mkdir(parents=False)
        created_output = True
        report.update(packet_sha256=args.expected_packet_sha256, rubric_sha256=args.expected_rubric_sha256,
                      executable_sha256=args.expected_codex_sha256, source_prompt_sha256=[c["prompt_sha256"] for c in packet["cases"]],
                      rubric_disclosure="not passed to model; external file placement is not OS isolation",
                      case_exposure="public_routine_not_held_out" if visibility == "external_public_routine" else "operator_asserted_held_out",
                      host_isolation_verified=False, capability_limit="Selected CLI tools disabled; managed/system integration availability is not independently attested",
                      decision_contract_sha256=contract["vocabulary_sha256"], response_schema_sha256=contract["response_schema_sha256"],
                      provider_schema_sha256=sha(encoded(response_schema(packet))), provider_schema_live_verified=False,
                      provider_schema_scope="Conservative shape/enums; complete mutual exclusions are enforced locally after one response",
                      unsafe_action_rate=None, substantive_rejection_rate=None,
                      metric_status="UNKNOWN: no precommitted safety/adversarial classification or authenticated action observations")
        if measurement_targets is not None:
            report.update(measurement_rubric_sha256=args.expected_measurement_rubric_sha256,
                          measurements=[], stop_rule='transport_usage_or_integrity_failure',
                          metric_status='Prospective required/prohibited code selection; extras and original strict grade reported separately',
                          optional_usage_policy='Missing reasoning/cache counters remain unknown; malformed supplied counters stop collection')
        began = time.monotonic()
        for index, case in enumerate(packet["cases"]):
            require(time.monotonic()-began + args.case_timeout + 2 <= MAX_SECONDS, "Overall evaluation deadline exhausted")
            pin(args.codex.read_bytes(), args.expected_codex_sha256, "codex")
            if measurement_targets is not None:
                for path, expected in ((args.packet, args.expected_packet_sha256), (args.rubric, args.expected_rubric_sha256),
                                       (args.measurement_rubric, args.expected_measurement_rubric_sha256)):
                    pin(read(path), expected, 'frozen evaluation input')
                pin(read(ROOT / '.agentic/prompts' / (case['role'] + '.md')), case['prompt_sha256'], 'current prompt')
            folder = args.output / case["case_id"]
            folder.mkdir()
            attempt = {"case_id": case["case_id"], "attempted": False}
            report["attempts"].append(attempt)
            answer, provenance = execute_case(args.codex, case, packet, args.model, args.effort, args.case_timeout, folder, attempt,
                                              retain_unqualified=measurement_targets is not None)
            report["runs"].append(provenance)
            write_new(folder / "provenance.json", provenance)
            require(not provenance.get('qualification_error'), 'Execution qualification failed; retain completed usage and stop')
            errors = response_contract_errors(answer, packet, case=case)
            if not errors:
                known_refs = {item["ref"] for item in case["inputs"]}
                if not set(answer["evidence_refs"]) <= known_refs:
                    errors.append("Response cites unknown case evidence")
            write_new(folder / "contract-validation.json", {
                "status": "INVALID_RESPONSE" if errors else "VALID", "errors": errors,
                "response_sha256": provenance["response_sha256"],
                "response_schema_sha256": contract["response_schema_sha256"],
                "factual_preconditions_verified": False, "execution_authority": False})
            if measurement_targets is not None:
                # Capture/storage/usage failures are not model decision quality.
                # Preserve completed usage and stop before another admission.
                require(not provenance.get('response_capture_error') and provenance.get('raw_response_retained') is True,
                        'Response capture failed; retain observed usage and stop collection')
                require(not usage_errors(provenance.get('usage')), 'Usage is invalid or incomplete; no next model call')
                require(not response_event_errors(read(folder / 'events.jsonl'), read(folder / 'raw-response.json')),
                        'Response file and final CLI message evidence disagree; no next model call')
                measured = measure_routine_response(answer, packet, case, strict_targets[case['case_id']],
                                                   measurement_targets[case['case_id']])
                measured.update(response_sha256=provenance['response_sha256'],
                                measurement_rubric_sha256=args.expected_measurement_rubric_sha256)
                write_new(folder / 'measurement.json', measured)
                report['measurements'].append(measured)
                continue
            if errors:
                report.update(invalid_case_id=case["case_id"], contract_errors=errors,
                              remaining_cases_not_run=[item["case_id"] for item in packet["cases"][index + 1:]])
                raise InvalidResponse("Completed response failed local contract; preserve evidence; no automatic retry")
            values["results"][index].update(answer)
            values["results"][index]["execution"].update(requested_model=args.model, requested_effort=args.effort,
                                                       host_identity=provenance["thread_id"])
        if measurement_targets is not None:
            measured = report['measurements']
            report.update(status='MEASURED', decision_grade='PASS' if all(row['substantive_grade'] == 'PASS' for row in measured) else 'FAIL',
                          strict_grade='PASS' if all(row['strict_grade'] == 'PASS' for row in measured) else 'FAIL',
                          passed_cases=sum(row['substantive_grade'] == 'PASS' for row in measured),
                          strict_passed_cases=sum(row['strict_grade'] == 'PASS' for row in measured), total_cases=len(packet['cases']),
                          unresolved_usage=False, automatic_retry=False,
                          malformed_response_cases=[row['case_id'] for row in measured if row['format_status'] == 'INVALID'])
            write_new(args.output / 'routine-measurements.json', {'packet_sha256': args.expected_packet_sha256,
                      'rubric_sha256': args.expected_rubric_sha256, 'measurement_rubric_sha256': args.expected_measurement_rubric_sha256,
                      'cases': measured})
        else:
            result = grade(packet_raw, encoded(values), rubric_raw, args.expected_packet_sha256, args.expected_rubric_sha256)
            write_new(args.output / "observations.json", values)
            write_new(args.output / "grade.json", result)
            report.update(status="MEASURED", decision_grade=result["status"], passed_cases=result["passed_cases"], total_cases=result["total_cases"])
        report.update(
                      limitations=["Structured decisions only; no project actions executed or independently authenticated provider identity.",
                                   "Fresh external cases are operator assertions of novelty, not proof of no training contamination.",
                                   "No prompt equivalence, general model quality, host isolation or external-engine qualification inferred."])
    except InvalidResponse as error:
        usage_known = all(all(type(run["usage"].get(name)) is int and run["usage"][name] >= 0
                              for name in ("input_tokens", "output_tokens")) for run in report["runs"])
        report.update(status="INVALID_RESPONSE", decision_grade="NOT_GRADED", reason=str(error),
                      automatic_retry=False, attempted_execution=True, unresolved_usage=not usage_known,
                      invocation_completed=True, observed_completed_runs=len(report["runs"]))
    except (BenchmarkError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        attempted = any(item["attempted"] for item in report["attempts"])
        report.update(status="INCOMPLETE" if attempted else "NOT_RUN", reason=str(error), automatic_retry=False,
                      attempted_execution=bool(attempted), unresolved_usage=bool(attempted))
        if args.measurement_rubric and 'measurements' in report:
            # A later capture/pin/report failure cannot erase known completed
            # totals. Optional malformed components remain a collection error.
            report['unresolved_usage'] = (sum(item['attempted'] for item in report['attempts']) > len(report['runs'])
                or any(any(type(run.get('usage', {}).get(name)) is not int or run['usage'][name] < 0
                           for name in ('input_tokens', 'output_tokens')) for run in report['runs']))
            report['usage_errors'] = [error for run in report['runs'] for error in usage_errors(run.get('usage'))]
    if created_output:
        write_new(args.output / "evaluation.json", report)
    print(encoded(report).decode("utf-8"))
    return 0 if report["status"] == "MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
