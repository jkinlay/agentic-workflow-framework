"""Run a reviewed, digest-pinned, resource-bounded heavy validation plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load_yaml, loads
from agentic.heavy_validation import resolve_without_alias, run_validation
from agentic.heavy_validation_controller import (
    FileLeaseBroker, GitHubReviewAuthenticator, broker_limits, write_result_log)


MAX_INPUT_BYTES = 1024 * 1024


def pinned_bytes(path: Path, label: str) -> bytes:
    path = resolve_without_alias(path.absolute(), label, directory=False)
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValidationError(f"{label} exceeds 1 MiB")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / ".agentic/PROJECT_CONFIG.yaml")
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--expected-review-sha256", required=True)
    parser.add_argument("--capacity", type=Path)
    parser.add_argument("--expected-capacity-sha256")
    parser.add_argument("--now", help="Pinned RFC3339 controller time; defaults to local UTC time")
    parser.add_argument("--max-capacity-age-seconds", type=int, default=300)
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--execution-root", type=Path, default=ROOT)
    parser.add_argument("--result-log", type=Path, required=True)
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--github-pr", type=int, required=True)
    parser.add_argument("--github-review-id", type=int, required=True)
    parser.add_argument("--broker-state", type=Path)
    args = parser.parse_args(argv)
    cancelled = threading.Event()

    def cancel(_signum, _frame):
        cancelled.set()

    previous = signal.signal(signal.SIGINT, cancel)
    try:
        if (args.capacity is None) != (args.expected_capacity_sha256 is None):
            raise ValidationError("capacity requires both a file and expected SHA-256")
        plan_raw = pinned_bytes(args.plan, "plan")
        review_raw = pinned_bytes(args.review, "review")
        config_raw = pinned_bytes(args.config, "config")
        capacity_raw = pinned_bytes(args.capacity, "capacity") if args.capacity else None
        config = (loads(config_raw.decode("utf-8")) if config_raw.lstrip().startswith(b"{")
                  else load_yaml(config_raw))
        capacity = loads(capacity_raw.decode("utf-8")) if capacity_raw is not None else None
        broker = None
        if config["execution"]["host_broker"]["enabled"]:
            if args.broker_state is None or capacity is None:
                raise ValidationError("Enabled broker requires --broker-state and pinned capacity")
            broker_id, limits = broker_limits(config, capacity)
            broker = FileLeaseBroker(args.broker_state, broker_id, limits)
        authenticator = GitHubReviewAuthenticator(
            args.github_repository, args.github_pr, args.github_review_id)
        result = run_validation(
            plan_raw=plan_raw,
            expected_plan_sha256=args.expected_plan_sha256,
            review_raw=review_raw,
            expected_review_sha256=args.expected_review_sha256,
            config_raw=config_raw,
            expected_config_sha256=args.expected_config_sha256,
            expected_candidate={"repository_id": args.repository_id, "base_sha": args.base_sha,
                                "head_sha": args.head_sha, "tree_sha": args.tree_sha},
            execution_root=args.execution_root.absolute(),
            review_authenticator=authenticator,
            capacity_raw=capacity_raw,
            expected_capacity_sha256=args.expected_capacity_sha256,
            broker_client=broker,
            now=args.now,
            max_capacity_age_seconds=args.max_capacity_age_seconds,
            cancel_event=cancelled,
        )
        receipt = write_result_log(args.result_log, result)
        print(json.dumps({"format": "awf-heavy-validation-run-receipt-1",
                          "status": result["status"], **receipt},
                         ensure_ascii=True, sort_keys=True, indent=2))
        return 0 if result["status"] == "PASS" else 1
    except (ValidationError, OSError, ValueError, TypeError, RecursionError) as error:
        rejected = {"format": "awf-heavy-validation-result-3", "status": "REJECTED",
                    "all_partitions_terminal": False, "reason": str(error)}
        try:
            receipt = write_result_log(args.result_log, rejected)
        except (OSError, ValidationError, ValueError):
            receipt = None
        print(json.dumps({**rejected, "durable_log": receipt},
                         ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    raise SystemExit(main())
