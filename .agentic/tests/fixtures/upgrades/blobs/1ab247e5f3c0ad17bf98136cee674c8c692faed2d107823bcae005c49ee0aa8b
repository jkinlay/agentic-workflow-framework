"""Prepare twelve PUBLIC synthetic routine cases as four bounded batches; never launch a model."""
from copy import deepcopy
import argparse
from pathlib import Path

from benchmark_native import BenchmarkError, ROOT, encoded, parse, prepare, read, require, sha, write_new, validate_measurement_rubric


def prepare_pilot(output, root=ROOT):
    output, root = Path(output), Path(root)
    require(output.is_absolute() and not output.exists(), "Use a new absolute preparation directory")
    require(not output.resolve().is_relative_to(root.resolve()), "Keep preparation outside the release tree")
    require(output.parent.is_dir(), "Preparation parent must already exist")
    packet, _ = prepare(root, suite="routine")
    rubric = parse(read(root / ".agentic/benchmarks/native/routine-rubric.json"))
    measurement = parse(read(root / '.agentic/benchmarks/native/routine-measurement-rubric.json'))
    validate_measurement_rubric(measurement, packet, rubric)
    require([case["case_id"] for case in packet["cases"]] == [f"NATIVE-{n}" for n in range(34, 46)],
            "Preparation requires all twelve routine cases, in original order")
    output.mkdir()
    batches = []
    for index in range(4):
        subset, target, metrics = deepcopy(packet), deepcopy(rubric), deepcopy(measurement)
        subset["cases"] = subset["cases"][index * 3:index * 3 + 3]
        target["cases"] = target["cases"][index * 3:index * 3 + 3]
        metrics['cases'] = metrics['cases'][index * 3:index * 3 + 3]
        target["visibility"] = "external_public_routine"
        packet_name, rubric_name = f"batch-{index + 1}-packet.json", f"batch-{index + 1}-rubric.json"
        measurement_name = f'batch-{index + 1}-measurement-rubric.json'
        batches.append({"batch": index + 1, "case_ids": [case["case_id"] for case in subset["cases"]],
                        "packet": packet_name, "packet_sha256": write_new(output / packet_name, subset),
                        "rubric": rubric_name, "rubric_sha256": write_new(output / rubric_name, target),
                        'measurement_rubric': measurement_name, 'measurement_rubric_sha256': write_new(output / measurement_name, metrics)})
    plan = {"format": "awf-routine-pilot-preparation-2", "status": "NOT_RUN", "preparation_status": "PREPARED",
            "source": "public_synthetic_routine_cases", "held_out": False,
            "proposed_model": "gpt-5.6-sol", "proposed_effort": "high", "model_availability_verified": False,
            "total_case_cap": 12, "total_call_cap": 12, "calls_launched": 0,
            "execution_order": "sequential; batches 1 then 2 then 3 then 4 (3+3+3+3); at most one invocation per case",
            "case_timeout_seconds": 180, "batch_supervisor_seconds": 600,
            "supervisor_limit": "Per existing evaluator; excludes OS startup and report I/O; not a whole-pilot wall-clock guarantee",
            "automatic_retries": False, "stop_rule": "transport_usage_or_integrity_failure",
            'continue_after_completed_decision_failure': True,
            'grading': 'required_present_and_prohibited_absent; extras and original strict grade reported separately',
            'optional_usage_policy': 'Missing reasoning/cache counters remain unknown; malformed supplied counters stop collection',
            "fresh_authorization_required": True, "prior_v184_authorization_reused": False,
            "execution_authority": False, "decision_grade": "NOT_GRADED", "batches": batches,
            "decision_contract_sha256": packet["decision_contract"]["vocabulary_sha256"],
            "response_schema_sha256": packet["decision_contract"]["response_schema_sha256"],
            "source_prompt_sha256": {case["role"]: case["prompt_sha256"] for case in packet["cases"]},
            "required_before_execution": ["Freeze and independently review final release, case/prompt/contract/rubric pins and grading rules",
                "Verify the actual native Codex executable and model availability; record exact executable pin and fresh output path",
                "Bind current concrete user authorization to these pins, proposed Sol/high route, twelve sequential calls, 180 seconds each and no retries; never reuse a prior grant",
                "Use a reviewed coordinator to stop on transport, usage or input/evidence integrity failure; completed malformed or decision-failing responses get no retry and do not stop later cases"],
            "measurement_limits": ["Public routine regression cases are not novel held-out evaluation",
                "Codes/order/supplied-state consistency do not authenticate facts, rationale quality or executed project behavior",
                "No model calls, Jira writes, PR operations or historical regrading are performed by preparation"]}
    write_new(output / "PREPARATION.json", plan)
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare_pilot(args.output)
        print(encoded({"status": result["status"], "preparation_status": result["preparation_status"],
                       "output": str(args.output), "preparation_sha256": sha(read(args.output / "PREPARATION.json")),
                       "calls_launched": 0, "execution_authority": False}).decode("utf-8"))
        return 0
    except (BenchmarkError, OSError, KeyError, TypeError, ValueError) as error:
        print(encoded({"status": "PREPARATION_REJECTED", "calls_launched": 0, "reason": str(error),
                       "next_action": "Preserve any partial preparation; resolve the cause and use a new path"}).decode("utf-8"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
