#!/usr/bin/env python3
"""Propose routes, reserve bounded runs, settle host observations, or inspect shadow evidence."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic.canonical import MAX_DOCUMENT_BYTES, loads, load_yaml
from agentic.model_routing import RoutingLedger, default_policy, policy_from_config, select_route


class RoutingParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValidationError(message)


def read_document(path):
    with path.open("rb") as source:
        raw = source.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValidationError("document exceeds 8 MiB")
    text = raw.decode("utf-8-sig")
    # The shipped YAML configuration is JSON-compatible, requiring only Python's
    # standard library. Owner-authored YAML uses the existing optional YAML parser.
    return loads(text) if text.lstrip().startswith(("{", "[")) else load_yaml(text.encode("utf-8"))


def main(argv=None):
    parser = RoutingParser(description=__doc__)
    parser.add_argument("command", choices=("defaults", "suggest", "reserve", "settle", "evidence", "resolve-failure", "record-defect", "reconcile", "reconciliation-history"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--capabilities", type=Path, help="Observed host model IDs mapped to supported effort lists under models")
    parser.add_argument("--ledger", type=Path, help="One protected durable SQLite file outside all worktrees")
    parser.add_argument("--project-root", type=Path, help="Actual target checkout; defaults to CONFIG/../..")
    parser.add_argument("--run-id")
    parser.add_argument("--outcome", type=Path)
    parser.add_argument("--observation", type=Path, help="Verified failure recovery or late escaped-defect observation")
    try:
        args = parser.parse_args(argv)
        if args.command == "defaults":
            result = default_policy()
        else:
            if args.config is None:
                raise ValidationError("--config is required")
            config = read_document(args.config)
            policy = policy_from_config(config)
            project = config.get("project")
            if not isinstance(project, dict):
                raise ValidationError("project must be an object")
            project_id = project.get("id")
            if not isinstance(project_id, str) or not project_id.strip() or project_id.strip() != project_id or len(project_id) > 256:
                raise ValidationError("project.id must be a nonempty bounded identifier")
            if args.command in ("suggest", "reserve"):
                if args.request is None or args.capabilities is None:
                    raise ValidationError("--request and --capabilities are required")
                request = read_document(args.request)
                capabilities = read_document(args.capabilities)
            if args.command == "suggest":
                result = select_route(policy, request, capabilities)
            else:
                if args.ledger is None:
                    raise ValidationError("--ledger is required")
                project_root = (args.project_root or args.config.parent.parent).resolve()
                ledger_path = args.ledger.resolve()
                if ledger_path == project_root or project_root in ledger_path.parents:
                    raise ValidationError("ledger must be outside the target worktree")
                ledger = RoutingLedger(ledger_path)
                if args.command == "reserve":
                    result = ledger.reserve(project_id, policy, request, capabilities)
                elif args.command == "settle":
                    if not args.run_id or args.outcome is None:
                        raise ValidationError("--run-id and --outcome are required")
                    result = ledger.settle(args.run_id, read_document(args.outcome))
                elif args.command in ("resolve-failure", "record-defect", "reconcile"):
                    if not args.run_id or args.observation is None:
                        raise ValidationError("--run-id and --observation are required")
                    observation = read_document(args.observation)
                    if args.command == "reconcile":
                        result = ledger.reconcile(project_id, policy, args.run_id, observation)
                    else:
                        method = ledger.resolve_failure if args.command == "resolve-failure" else ledger.record_defect
                        result = method(args.run_id, observation)
                elif args.command == "reconciliation-history":
                    result = ledger.reconciliation_history(project_id)
                else:
                    result = ledger.evidence(project_id, policy)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") not in ("blocked", "unavailable", "quarantined") else 2
    except (ValidationError, OSError, UnicodeError, sqlite3.Error, ValueError, TypeError,
            AttributeError, KeyError, OverflowError, RecursionError, ImportError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "error_type": type(exc).__name__}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
