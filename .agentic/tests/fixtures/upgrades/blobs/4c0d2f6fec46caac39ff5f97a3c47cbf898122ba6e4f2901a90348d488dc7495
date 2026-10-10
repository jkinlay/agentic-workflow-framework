#!/usr/bin/env python3
"""Render a configuration-bound repository-rules activation decision; never mutate GitHub."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load, loads, sha256
from agentic.contracts import Contracts
from agentic.installer import verify_installed
from agentic.providers.github import MAX_TOTAL_BYTES
from agentic.rules_activation import activation_rules_decision
from agentic.safeio import Tree


def pinned_observation(path, expected):
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValidationError("Observation requires an exact lowercase SHA-256 pin")
    path = path.resolve(strict=True)
    with Tree(path.parent) as tree:
        raw = tree.read(path.name, maximum=MAX_TOTAL_BYTES)
    if sha256(raw) != expected:
        raise ValidationError("Observation SHA-256 differs from the expected pin")
    return loads(raw.decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / ".agentic/PROJECT_CONFIG.yaml")
    parser.add_argument("--workflow", type=Path, default=ROOT / ".agentic/workflow.yaml")
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--expected-observation-sha256", required=True)
    parser.add_argument("--post-observation", type=Path)
    parser.add_argument("--expected-post-observation-sha256")
    parser.add_argument("--owner-outcome", choices=("PENDING", "DECLINED", "APPLIED"), default="PENDING")
    parser.add_argument("--review-app-id", type=int)
    parser.add_argument("--now", required=True, help="Trusted controller RFC3339 decision time")
    args = parser.parse_args(argv)
    try:
        verify_installed(ROOT)
        if (args.post_observation is None) != (args.expected_post_observation_sha256 is None):
            raise ValidationError("Post-action observation requires both path and SHA-256 pin")
        config = load(args.config.resolve(strict=True))
        workflow = load(args.workflow.resolve(strict=True))
        contracts = Contracts(ROOT / ".agentic/schemas")
        value = activation_rules_decision(
            config, workflow, contracts,
            before_observation=pinned_observation(args.observation, args.expected_observation_sha256),
            post_observation=(pinned_observation(args.post_observation,
                              args.expected_post_observation_sha256)
                              if args.post_observation else None),
            owner_outcome=args.owner_outcome, review_app_id=args.review_app_id, now=args.now)
        contracts.validate("rules-activation-decision", value)
        print(json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True))
        return 0 if value["status"] == "RULES_OBSERVED" else 2
    except (ValidationError, OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"format": "awf-rules-activation-decision-1", "status": "REJECTED",
                          "provider_mutation_performed": False, "execution_authority": False,
                          "reason": str(error)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
