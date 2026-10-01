#!/usr/bin/env python3
"""Read the protected review policy and emit workflow outputs for one engine.

Usage: policy.py --policy .agentic/project.yaml --engine claude [--require-required]

Prints `key=value` lines suitable for appending to $GITHUB_OUTPUT. Fails closed
when the engine is not configured, a placeholder token is unresolved, or the
reviewer identity is not a GitHub App bot login.
"""

from __future__ import annotations

import argparse
import re
import sys

from awf_review_common import BOT_LOGIN, AdapterError, get_nested, load_yaml_lite

PLACEHOLDER = re.compile(r"__AWF_[A-Z0-9_]+__")
COPILOT_IDENTITY = "copilot-pull-request-reviewer[bot]"
DEFAULTS = {
    "base_url": "https://api.anthropic.com",
    "task_contract_path": ".agentic/task-contract.yaml",
    "execution_report_path": ".agentic/execution-report.json",
}


def resolve(policy: dict, engine: str, require_required: bool) -> dict[str, str]:
    review = policy.get("review")
    if not isinstance(review, dict):
        raise AdapterError("policy has no review block")
    required = str(review.get("required_external_engine") or "")
    optional = review.get("optional_engines") or []
    if PLACEHOLDER.search(required):
        raise AdapterError("review.required_external_engine is an unresolved placeholder")
    if engine != required and engine not in optional:
        raise AdapterError(f"engine {engine!r} is neither required nor optional in policy")
    if require_required and engine != required:
        raise AdapterError(f"engine {engine!r} is not the required engine ({required!r})")

    if engine == "copilot":
        identity = COPILOT_IDENTITY
    else:
        identity = str(get_nested(review, f"reviewer_identities.{engine}") or "")
        if PLACEHOLDER.search(identity) or not BOT_LOGIN.match(identity):
            raise AdapterError(f"review.reviewer_identities.{engine} must be a GitHub App bot login, got {identity!r}")

    outputs = {"required_engine": required, "engine": engine, "reviewer_identity": identity}
    if engine == "claude":
        block = review.get("claude") or {}
        model = str(block.get("model") or "")
        if not model or PLACEHOLDER.search(model):
            raise AdapterError("review.claude.model must be a pinned model id")
        outputs["model"] = model
        for key, default in DEFAULTS.items():
            value = str(block.get(key) or default)
            if PLACEHOLDER.search(value):
                raise AdapterError(f"review.claude.{key} is an unresolved placeholder")
            outputs[key] = value
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--require-required", action="store_true",
                        help="fail unless the engine is the required external engine")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        with open(args.policy, encoding="utf-8") as handle:
            policy = load_yaml_lite(handle.read())
        outputs = resolve(policy, args.engine, args.require_required)
    except (AdapterError, OSError) as err:
        print(f"policy: {err}", file=sys.stderr)
        return 1
    for key, value in outputs.items():
        if "\n" in value:
            print(f"policy: value for {key} contains a newline", file=sys.stderr)
            return 1
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
