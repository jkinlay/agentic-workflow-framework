#!/usr/bin/env python3
"""Observe default-branch rules with GETs or assess a pinned offline observation.

Installation/adoption always remains allowed. --require-enablement checks only
the repository-rules prerequisite; it does not enable or qualify live automation.
Local output paths must be new absolute files outside this runtime tree.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic.canonical import sha256
from agentic.installer import verify_installed
from agentic.providers.github import (MAX_TOTAL_BYTES, assess_repository_rules, load_observation_report,
                                      observe_repository_rules, synthetic_observation)
from agentic.safeio import Tree


def output_path(path):
    if path is None:
        return None
    if not path.is_absolute() or path.resolve().is_relative_to(ROOT.resolve()):
        raise ValidationError("Output must be an absolute path outside this runtime tree")
    with Tree(path.parent) as tree:
        if tree.inspect(path.name) is not None:
            raise ValidationError("Preserve existing output; select a new report/observation path")
    return path


def write_new(path, value):
    raw = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_TOTAL_BYTES:
        raise ValidationError("Output exceeds the aggregate byte limit")
    with Tree(path.parent) as tree:
        parent, handle, name = tree.parent(path.name)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(parent / name, flags, 0o600) if os.name == "nt" else os.open(name, flags, 0o600, dir_fd=handle)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
    return sha256(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--observe", action="store_true", help="GET actual repository metadata, paginated effective default-branch rules and ruleset details")
    source.add_argument("--observation", type=Path)
    source.add_argument("--synthetic-none", action="store_true", help="Produce an explicitly synthetic empty-rules fixture; never live authority")
    parser.add_argument("--expected-observation-sha256")
    parser.add_argument("--default-branch", help="Optional expected branch; required for synthetic fixture, not a live default override")
    parser.add_argument("--review-app-id", type=int, help="Expected positive GitHub App ID for the required awf/review check")
    parser.add_argument("--gh", default="gh", help="Trusted local GitHub CLI executable; only GET requests are issued")
    parser.add_argument("--save-observation", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-enablement", action="store_true", help="Exit 2 unless rules prerequisite is observed ready; existing live qualification remains required")
    args = parser.parse_args(argv)
    try:
        verify_installed(ROOT)
        output_path(args.save_observation)
        output_path(args.report)
        if args.save_observation and args.report and args.save_observation.resolve() == args.report.resolve():
            raise ValidationError("Observation and report must use distinct output files")
        binding = {"repository": args.repository, "default_branch": args.default_branch, "review_app_id": args.review_app_id}
        observation = None
        if args.observe:
            observation = observe_repository_rules(args.repository, default_branch=args.default_branch, gh=args.gh)
            result = assess_repository_rules(observation, **binding)
        elif args.synthetic_none:
            observation = synthetic_observation(args.repository, args.default_branch)
            result = assess_repository_rules(observation, **binding)
        elif args.observation:
            result = load_observation_report(args.observation, args.expected_observation_sha256, **binding)
            # Imported files are never rewritten as fresh live observations.
            if args.save_observation:
                raise ValidationError("Preserve the original imported observation instead of rewriting its source/time")
        else:
            result = assess_repository_rules(**binding)
        if args.save_observation and observation is not None:
            result["observation_file_sha256"] = write_new(args.save_observation, observation)
        result["observation_saved"] = bool(args.save_observation and observation is not None)
        if args.report:
            write_new(args.report, result)
        print(json.dumps(result, indent=2))
        return 2 if args.require_enablement and not result["rules_enablement_ready"] else 0
    except (ValidationError, OSError, ValueError, TypeError):
        result = assess_repository_rules(repository=args.repository, default_branch=args.default_branch)
        result["warnings"].append("Rules CLI input, integrity or output preflight failed; preserve existing files and correct the technical problem")
        print(json.dumps(result, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
