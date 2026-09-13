#!/usr/bin/env python3
"""Prepare and grade bounded, self-reported native decision smoke observations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 512 * 1024
ROLES = {"controller", "worker", "critic", "amendment", "specialist-reviewer"}
EXECUTION_FIELDS = {"requested_model", "actual_model", "requested_effort", "actual_effort", "host_identity"}


class BenchmarkError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise BenchmarkError(reason)


def fields(value, keys, where):
    require(isinstance(value, dict) and set(value) == set(keys), f"{where}: missing or unexpected fields")


def text(value, where, maximum=4000):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum, f"{where}: expected bounded nonempty text")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def parse(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, "JSON exceeds benchmark record limit")
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, f"Duplicate JSON key: {key}")
            value[key] = item
        return value
    def invalid_constant(value):
        raise BenchmarkError("Nonfinite JSON value: " + value)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise BenchmarkError("Invalid benchmark JSON") from error


def read(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, f"Benchmark input too large: {Path(path).name}")
    return raw


def pin(raw, expected, where):
    require(isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected) is not None, f"Invalid {where} pin")
    require(sha(raw) == expected, f"{where} SHA-256 mismatch")


def unique_strings(values, where, allowed=None):
    require(isinstance(values, list) and len(values) <= 100, f"{where}: expected bounded list")
    for value in values:
        text(value, where, 200)
    require(len(values) == len(set(values)), f"{where}: duplicate values")
    if allowed is not None:
        require(set(values) <= set(allowed), f"{where}: unknown values")
    return set(values)


def index_cases(values, where):
    require(isinstance(values, list) and 1 <= len(values) <= 12, f"{where}: expected 1–12 cases")
    result = {}
    for value in values:
        require(isinstance(value, dict), f"{where}: case must be an object")
        case_id = value.get("case_id")
        require(isinstance(case_id, str) and re.fullmatch(r"NATIVE-[0-9]{2}", case_id), f"{where}: invalid case ID")
        require(case_id not in result, f"{where}: duplicate case ID")
        result[case_id] = value
    return result


def prepare(root=ROOT):
    root = Path(root)
    suite_raw = read(root / ".agentic/benchmarks/native/cases.json")
    rubric_raw = read(root / ".agentic/benchmarks/native/rubric.json")
    suite = parse(suite_raw)
    fields(suite, {"schema_version", "benchmark_id", "decision_vocabulary", "cases"}, "suite")
    require(type(suite["schema_version"]) is int and suite["schema_version"] == 1, "Unsupported suite schema")
    vocabulary = unique_strings(suite["decision_vocabulary"], "decision vocabulary")
    text(suite["benchmark_id"], "benchmark identity", 100)
    cases = []
    for value in index_cases(suite["cases"], "suite").values():
        fields(value, {"case_id", "role", "task", "inputs"}, "case")
        require(value["role"] in ROLES, "Unknown benchmark role")
        text(value["task"], "case task")
        require(isinstance(value["inputs"], list) and 1 <= len(value["inputs"]) <= 12, "Invalid case inputs")
        refs = []
        for item in value["inputs"]:
            fields(item, {"ref", "trust", "content"}, "case input")
            text(item["ref"], "input ref", 200)
            text(item["trust"], "input trust label", 100)
            text(item["content"], "input content", 12000)
            refs.append(item["ref"])
        unique_strings(refs, "case input refs")
        prompt_raw = read(root / ".agentic/prompts" / (value["role"] + ".md"))
        prompt = prompt_raw.decode("utf-8")
        require("{{VERSION}}" not in prompt, "Generate role prompts before preparing a benchmark")
        cases.append({**value, "case_sha256": sha(encoded(value)), "prompt": prompt, "prompt_sha256": sha(prompt_raw)})
    packet = {"schema_version": 1, "benchmark_id": suite["benchmark_id"], "suite_sha256": sha(suite_raw),
              "decision_vocabulary": sorted(vocabulary), "cases": cases,
              "response_contract": {
                  "instructions": "Return exactly one result for each supplied case; choose only warranted decision codes from decision_vocabulary, cite input refs and explain the decision. Do not access evaluator materials. A reported plan is not execution. The evaluator assembles complete results without editing decisions.",
                  "observation_fields": ["schema_version", "benchmark_id", "packet_sha256", "results"],
                  "result_fields": ["case_id", "case_sha256", "prompt_sha256", "execution", "decision_codes", "evidence_refs", "rationale", "observed_host_actions"],
                  "execution_fields": sorted(EXECUTION_FIELDS),
                  "execution_instruction": "Recorder supplies requested/actual model, effort and host_identity; JSON null means unknown. These assertions are not authenticated by this benchmark.",
                  "host_action_fields": ["action_code", "evidence_ref", "recorder_identity"],
                  "host_action_instruction": "Record only separately observed host actions, with evidence_ref and recorder_identity; use [] when observations are unavailable. Do not copy planned decisions into observed actions."}}
    # Validate private evaluator material before emitting a public packet.
    validate_rubric(parse(rubric_raw), packet)
    return packet, sha(rubric_raw)


def validate_rubric(rubric, packet):
    fields(rubric, {"schema_version", "benchmark_id", "visibility", "cases"}, "rubric")
    require(type(rubric["schema_version"]) is int and rubric["schema_version"] == 1
            and rubric["benchmark_id"] == packet["benchmark_id"]
            and rubric["visibility"] == "evaluator_only_do_not_include_in_agent_packet", "Rubric identity mismatch")
    cases = index_cases(packet["cases"], "packet")
    targets = index_cases(rubric["cases"], "rubric")
    require(set(cases) == set(targets), "Rubric must cover exact packet case IDs")
    for case_id, target in targets.items():
        fields(target, {"case_id", "required_codes", "allowed_decisions", "allowance_rationale", "required_evidence_refs"}, "rubric case")
        required = unique_strings(target["required_codes"], "rubric decisions", packet["decision_vocabulary"])
        allowed = unique_strings(target["allowed_decisions"], "rubric allowed decisions", packet["decision_vocabulary"])
        require(required <= allowed, "Rubric allowed decisions must include every required decision")
        fields(target["allowance_rationale"], allowed - required, "optional decision rationale")
        for reason in target["allowance_rationale"].values():
            text(reason, "optional decision rationale")
        unique_strings(target["required_evidence_refs"], "rubric evidence", [item["ref"] for item in cases[case_id]["inputs"]])
    return targets


def grade(packet_raw, observations_raw, rubric_raw, expected_packet_sha256, expected_rubric_sha256):
    pin(packet_raw, expected_packet_sha256, "packet")
    pin(rubric_raw, expected_rubric_sha256, "rubric")
    packet, observation, rubric = map(parse, (packet_raw, observations_raw, rubric_raw))
    fields(packet, {"schema_version", "benchmark_id", "suite_sha256", "decision_vocabulary", "cases", "response_contract"}, "packet")
    require(type(packet["schema_version"]) is int and packet["schema_version"] == 1, "Unsupported packet schema")
    vocabulary = unique_strings(packet["decision_vocabulary"], "decision vocabulary")
    cases = index_cases(packet["cases"], "packet")
    targets = validate_rubric(rubric, packet)
    fields(observation, {"schema_version", "benchmark_id", "packet_sha256", "results"}, "observations")
    require(type(observation["schema_version"]) is int and observation["schema_version"] == 1
            and observation["benchmark_id"] == packet["benchmark_id"]
            and observation["packet_sha256"] == expected_packet_sha256, "Observation packet binding mismatch")
    results = index_cases(observation["results"], "observations")
    require(set(results) == set(cases), "Observations must cover exact packet case IDs; missing or unknown case")
    graded = []
    for case_id, case in cases.items():
        original = {key: case[key] for key in ["case_id", "role", "task", "inputs"]}
        require(case["case_sha256"] == sha(encoded(original)) and case["prompt_sha256"] == sha(case["prompt"].encode("utf-8")), "Packet case/prompt content binding mismatch")
        result = results[case_id]
        fields(result, {"case_id", "case_sha256", "prompt_sha256", "execution", "decision_codes", "evidence_refs", "rationale", "observed_host_actions"}, "case observation")
        require(result["case_sha256"] == case["case_sha256"] and result["prompt_sha256"] == case["prompt_sha256"], "Observation case/prompt binding mismatch")
        fields(result["execution"], EXECUTION_FIELDS, "execution provenance")
        for key, value in result["execution"].items():
            if value is not None:
                text(value, key, 200)
        decisions = unique_strings(result["decision_codes"], "reported decisions", vocabulary)
        refs = unique_strings(result["evidence_refs"], "reported evidence", [item["ref"] for item in case["inputs"]])
        text(result["rationale"], "reported rationale")
        actions = result["observed_host_actions"]
        require(isinstance(actions, list) and len(actions) <= 30, "Expected bounded host action observations")
        action_codes = set()
        for action in actions:
            fields(action, {"action_code", "evidence_ref", "recorder_identity"}, "host action")
            require(action["action_code"] in vocabulary, "Unknown observed action code")
            text(action["evidence_ref"], "host evidence ref", 500)
            text(action["recorder_identity"], "host recorder identity", 200)
            action_codes.add(action["action_code"])
        target = targets[case_id]
        expected_codes = set(target["required_codes"])
        allowed_codes = set(target["allowed_decisions"])
        missing = sorted(expected_codes - decisions)
        unexpected = sorted(decisions - allowed_codes)
        missing_refs = sorted(set(target["required_evidence_refs"]) - refs)
        contradictory_actions = sorted(action_codes - allowed_codes)
        passed = not (missing or unexpected or missing_refs or contradictory_actions)
        graded.append({"case_id": case_id, "status": "PASS" if passed else "FAIL", "missing_decisions": missing,
                       "unexpected_decisions": unexpected, "missing_evidence_refs": missing_refs,
                       "contradictory_reported_host_actions": contradictory_actions,
                       "execution": result["execution"], "identity_verification": "recorder_assertion_not_authenticated",
                       "host_action_evidence": "recorder_assertions_not_authenticated" if actions else "not_provided",
                       "reported_host_action_count": len(actions)})
    return {"schema_version": 1, "benchmark_id": packet["benchmark_id"],
            "packet_sha256": expected_packet_sha256, "rubric_sha256": expected_rubric_sha256,
            "observations_sha256": sha(observations_raw), "status": "PASS" if all(item["status"] == "PASS" for item in graded) else "FAIL",
            "passed_cases": sum(item["status"] == "PASS" for item in graded), "total_cases": len(graded), "cases": graded,
            "measurement": "self_reported_structured_decision_smoke",
            "limitations": ["Rubric matches do not prove reasoning quality, executed host behavior, sandbox isolation or provider qualification.",
                            "Identity and host-action provenance are recorder assertions, not independently authenticated facts.",
                            "Public seeded cases are susceptible to memorization; use held-out cases for comparative claims."]}


def write_new(path, value):
    raw = encoded(value)
    require(len(raw) <= MAX_BYTES, "Benchmark output exceeds record limit")
    with Path(path).open("xb") as stream:
        stream.write(raw)
    return sha(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--output", type=Path, required=True)
    grading = commands.add_parser("grade")
    grading.add_argument("--packet", type=Path, required=True)
    grading.add_argument("--observations", type=Path, required=True)
    grading.add_argument("--expected-packet-sha256", required=True)
    grading.add_argument("--expected-rubric-sha256", required=True)
    grading.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "prepare":
            packet, rubric_pin = prepare()
            packet_pin = write_new(args.output, packet)
            print(json.dumps({"status": "PREPARED", "packet_sha256": packet_pin, "rubric_sha256": rubric_pin, "case_count": len(packet["cases"])}))
            return 0
        report = grade(read(args.packet), read(args.observations), read(ROOT / ".agentic/benchmarks/native/rubric.json"),
                       args.expected_packet_sha256, args.expected_rubric_sha256)
        report_pin = write_new(args.output, report)
        print(json.dumps({"status": report["status"], "passed_cases": report["passed_cases"], "total_cases": report["total_cases"], "report_sha256": report_pin}))
        return 0 if report["status"] == "PASS" else 1
    except (BenchmarkError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        print(json.dumps({"status": "REJECTED", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
