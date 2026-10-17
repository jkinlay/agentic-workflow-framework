#!/usr/bin/env python3
"""Gh-backed merge-train publisher adapter.

This surface can create/update only ``refs/heads/batch/*`` and draft pull
requests.  It intentionally has no target-ref push or merge command.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from agentic import ValidationError


def _run_gh(gh, arguments):
    result = subprocess.run(
        [gh, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=120, check=False)
    if result.returncode:
        raise ValidationError(
            f"gh command failed with exit {result.returncode}")
    return result.stdout.strip()


def push_batch_ref(gh, repository, branch, head_sha):
    if not branch.startswith("batch/"):
        raise ValidationError("publisher may push only a batch ref")
    output = _run_gh(gh, ["api", f"repos/{repository}/git/refs",
                          "-f", f"ref=refs/heads/{branch}",
                          "-f", f"sha={head_sha}"])
    return json.loads(output)


def create_draft_pr(gh, repository, branch, base_branch, title):
    output = _run_gh(gh, ["pr", "create", "--repo", repository, "--draft",
                          "--head", branch, "--base", base_branch,
                          "--title", title, "--body", ""])
    return {"url": output}


def mark_draft_pr_ready(gh, repository, pr_number):
    output = _run_gh(gh, ["pr", "ready", str(pr_number),
                          "--repo", repository])
    return {"output": output}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--repository", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    push = actions.add_parser("push-batch-ref")
    push.add_argument("--branch", required=True)
    push.add_argument("--head", required=True)
    create = actions.add_parser("create-draft-pr")
    create.add_argument("--branch", required=True)
    create.add_argument("--base", required=True)
    create.add_argument("--title", required=True)
    ready = actions.add_parser("mark-draft-pr-ready")
    ready.add_argument("--pr", required=True, type=int)
    args = parser.parse_args(argv)
    if args.action == "push-batch-ref":
        result = push_batch_ref(args.gh, args.repository, args.branch, args.head)
    elif args.action == "create-draft-pr":
        result = create_draft_pr(args.gh, args.repository, args.branch,
                                 args.base, args.title)
    else:
        result = mark_draft_pr_ready(args.gh, args.repository, args.pr)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
