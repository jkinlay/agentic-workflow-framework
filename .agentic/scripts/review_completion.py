#!/usr/bin/env python3
"""Durable production entry point for the all-reviewer completion barrier."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import argparse
import json

from agentic.canonical import load
from agentic.review_completion import ReviewCompletionStore, validate_submission_semantics


def main(argv=None, default_root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--worktree-root", type=Path, action="append", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "dispatch", "status", "prepare"):
        command = sub.add_parser(name)
        command.add_argument("--candidate", type=Path, required=True)
        command.add_argument("--reviewers", type=Path, required=True)
        if name == "dispatch":
            command.add_argument("--reviewer", required=True)
        if name == "prepare":
            command.add_argument("--aggregate", type=Path, required=True)
    record = sub.add_parser("record")
    record.add_argument("--reviewer", required=True)
    record.add_argument("--binding", type=Path, required=True)
    record.add_argument("--outcome", choices=["ACCEPTABLE", "FAILED", "TIMED_OUT", "MALFORMED"], required=True)
    record.add_argument("--result", type=Path, required=True)
    for name in ("complete", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("--admission", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
    sub.add_parser("recover")
    args = parser.parse_args(argv)
    store = ReviewCompletionStore(args.state, worktree_roots=args.worktree_root)
    if args.command in {"freeze", "dispatch", "status", "prepare"}:
        candidate, reviewers = load(args.candidate), load(args.reviewers)
    if args.command == "freeze":
        output = store.freeze(candidate, reviewers)
    elif args.command == "dispatch":
        output = store.dispatch(args.reviewer, candidate, reviewers)
    elif args.command == "status":
        output = store.status(candidate, reviewers)
    elif args.command == "prepare":
        output = store.prepare_submission(candidate, reviewers, load(args.aggregate))
    elif args.command == "record":
        output = store.record_result(args.reviewer, load(args.binding), args.outcome, load(args.result))
    elif args.command == "complete":
        admission = validate_submission_semantics(load(args.admission))
        output = store.complete_submission(admission, load(args.receipt))
    elif args.command == "reconcile":
        admission = validate_submission_semantics(load(args.admission))
        output = store.reconcile_submission(admission, load(args.receipt))
    else:
        output = store.recover()
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
