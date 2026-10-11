#!/usr/bin/env python3
"""Operate the provider-neutral reviewed-epic integration queue."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import argparse
import json
import sqlite3

from agentic import ValidationError
from agentic.canonical import load
from agentic.integration_queue import (IntegrationQueueStore, WORKER_GUIDANCE,
    delta_review_requirement, integration_status, resolve_mode, steward_policy)


def _document(path, label):
    if path is None:
        raise ValidationError(f"{label} is required")
    value = load(path)
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must contain an object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path,
                        help="protected SQLite state outside every worktree")
    sub = parser.add_subparsers(dest="command", required=True)

    mode = sub.add_parser("mode")
    mode.add_argument("--config", type=Path)
    mode.add_argument("--owner-instruction", type=Path)
    mode.add_argument("--app-binding", type=Path)

    for name in ("status", "audit", "close"):
        command = sub.add_parser(name)
        command.add_argument("--epic", required=True)

    initialize = sub.add_parser("initialize")
    initialize.add_argument("--epic", required=True)
    initialize.add_argument("--target-branch", required=True)
    initialize.add_argument("--target-sha", required=True)
    initialize.add_argument("--mode-record", type=Path, required=True)

    for name in ("admit", "conflict"):
        command = sub.add_parser(name)
        command.add_argument("--epic", required=True)
        command.add_argument("--entry", type=Path, required=True)
        if name == "admit":
            command.add_argument("--outcome", type=Path, required=True)
        else:
            command.add_argument("--paths", type=Path, required=True)

    rebuild = sub.add_parser("rebuild")
    rebuild.add_argument("--epic", required=True)
    rebuild.add_argument("--target-sha", required=True)
    rebuild.add_argument("--reason", required=True,
                         choices=("TARGET_MOVED", "HEAD_CHANGED", "REMOVAL", "POST_MERGE"))
    rebuild.add_argument("--outcome", type=Path, required=True)

    head_change = sub.add_parser("head-change")
    head_change.add_argument("--epic", required=True)
    head_change.add_argument("--entry", type=Path, required=True)
    head_change.add_argument("--outcome", type=Path, required=True)

    remove = sub.add_parser("remove")
    remove.add_argument("--epic", required=True)
    remove.add_argument("--pr", type=int, required=True)
    remove.add_argument("--reason", default="REMOVAL",
                        choices=("REVIEW_FAILED", "AMENDMENT", "REMOVAL"))
    remove.add_argument("--outcome", type=Path, required=True)

    reorder = sub.add_parser("reorder")
    reorder.add_argument("--epic", required=True)
    reorder.add_argument("--order", type=Path, required=True)
    reorder.add_argument("--instruction", type=Path, required=True)
    reorder.add_argument("--outcome", type=Path, required=True)

    skip = sub.add_parser("skip")
    skip.add_argument("--epic", required=True)
    skip.add_argument("--pr", type=int, required=True)
    skip.add_argument("--instruction", type=Path, required=True)
    skip.add_argument("--outcome", type=Path, required=True)

    stall = sub.add_parser("stall")
    stall.add_argument("--epic", required=True)
    stall.add_argument("--hours", type=int, default=24)
    stall.add_argument("--outcome", type=Path, required=True)

    retarget = sub.add_parser("retarget")
    retarget.add_argument("--epic", required=True)
    retarget.add_argument("--merged-pr", type=int, required=True)
    retarget.add_argument("--target-sha", required=True)
    retarget.add_argument("--patch-ids", type=Path, required=True)
    retarget.add_argument("--outcome", type=Path, required=True)

    check = sub.add_parser("check")
    check.add_argument("--epic", required=True)
    check.add_argument("--pr", type=int, required=True)
    for flag in ("review", "final-gate", "authorization", "delta"):
        check.add_argument("--" + flag, type=Path, required=True)

    delta = sub.add_parser("delta")
    delta.add_argument("--reviewed-paths", type=Path, required=True)
    delta.add_argument("--intervening-paths", type=Path, required=True)
    delta.add_argument("--review", type=Path)

    sub.add_parser("steward-policy")
    sub.add_parser("worker-guidance")

    try:
        args = parser.parse_args(argv)
        if args.command == "mode":
            result = resolve_mode(
                load(args.config) if args.config else None,
                load(args.owner_instruction) if args.owner_instruction else None,
                load(args.app_binding) if args.app_binding else None)
        elif args.command == "steward-policy":
            result = steward_policy()
        elif args.command == "worker-guidance":
            result = {"guidance": WORKER_GUIDANCE}
        elif args.command == "delta":
            reviewed = load(args.reviewed_paths)
            intervening = load(args.intervening_paths)
            if not isinstance(reviewed, list) or not isinstance(intervening, list):
                raise ValidationError("path documents must contain lists")
            result = delta_review_requirement(
                reviewed, intervening, delta_review=load(args.review) if args.review else None)
        else:
            if args.state is None:
                raise ValidationError("--state is required for queue commands")
            store = IntegrationQueueStore(args.state, worktree_roots=[ROOT])
            if args.command == "initialize":
                result = store.initialize(args.epic, args.target_branch, args.target_sha,
                                          mode_record=_document(args.mode_record, "mode record"))
            elif args.command == "status":
                result = store.snapshot(args.epic)
            elif args.command == "audit":
                result = {"events": store.audit(args.epic)}
            elif args.command == "admit":
                result = store.admit(args.epic, _document(args.entry, "entry"),
                                     _document(args.outcome, "outcome"))
            elif args.command == "conflict":
                paths = load(args.paths)
                if not isinstance(paths, list):
                    raise ValidationError("paths must contain a list")
                result = store.conflict(args.epic, _document(args.entry, "entry"), paths)
            elif args.command == "rebuild":
                result = store.rebuild(args.epic, args.target_sha,
                                       _document(args.outcome, "outcome"), args.reason)
            elif args.command == "head-change":
                result = store.head_changed(args.epic, _document(args.entry, "entry"),
                                            _document(args.outcome, "outcome"))
            elif args.command == "remove":
                result = store.remove(args.epic, args.pr,
                                      _document(args.outcome, "outcome"), reason=args.reason)
            elif args.command == "reorder":
                order = load(args.order)
                if not isinstance(order, list):
                    raise ValidationError("order must contain a list")
                result = store.owner_reorder(args.epic, order,
                    _document(args.instruction, "instruction"),
                    _document(args.outcome, "outcome"))
            elif args.command == "skip":
                result = store.owner_skip(args.epic, args.pr,
                    _document(args.instruction, "instruction"),
                    _document(args.outcome, "outcome"))
            elif args.command == "stall":
                result = store.move_stalled_front(args.epic,
                    _document(args.outcome, "outcome"), stall_after_hours=args.hours)
            elif args.command == "retarget":
                result = store.retarget_after_merge(args.epic, args.merged_pr, args.target_sha,
                    _document(args.patch_ids, "patch IDs"),
                    _document(args.outcome, "outcome"))
            elif args.command == "check":
                result = integration_status(store.snapshot(args.epic), args.pr,
                    _document(args.review, "review"),
                    _document(args.final_gate, "final gate"),
                    _document(args.authorization, "authorization"),
                    _document(args.delta, "delta"))
            else:
                result = store.close(args.epic)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") not in {"blocked", "RED"} else 2
    except (ValidationError, OSError, UnicodeError, sqlite3.Error, ValueError,
            TypeError, KeyError, AttributeError, OverflowError, RecursionError, ImportError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc),
                          "error_type": type(exc).__name__}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
