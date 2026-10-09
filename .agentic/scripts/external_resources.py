#!/usr/bin/env python3
"""Validate and admit logical external resources without granting authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import load
from agentic.external_resources import (
    admit_resource, admission_decision, public_evidence, validate_claim,
    validate_registry, validate_scan_request,
)


def _private_load(path, project_root, label):
    resolved = path.resolve(strict=True)
    root = project_root.resolve(strict=True)
    if resolved.is_relative_to(root):
        raise ValidationError(label + " must stay outside the repository")
    return load(resolved)


def _emit(value, *, error=False):
    print(json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True),
          file=sys.stderr if error else sys.stdout)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="command", required=True)
    registry = sub.add_parser("validate-registry")
    registry.add_argument("--registry", type=Path, required=True)

    admit = sub.add_parser("admit")
    admit.add_argument("--project-root", type=Path, required=True)
    admit.add_argument("--registry", type=Path, required=True)
    admit.add_argument("--mapping", type=Path, required=True)
    admit.add_argument("--resource", required=True)
    admit.add_argument("--task", required=True)
    admit.add_argument("--principal", required=True)
    admit.add_argument("--session", required=True)
    admit.add_argument("--permission-scope", choices=("one_command", "task", "application_session", "project"), default="task")
    admit.add_argument("--permission-expires-at")
    admit.add_argument("--now")

    required = sub.add_parser("require")
    required.add_argument("--project-root", type=Path, required=True)
    required.add_argument("--receipt", type=Path, required=True)
    required.add_argument("--mapping", type=Path)
    required.add_argument("--resource", required=True)
    required.add_argument("--task", required=True)
    required.add_argument("--principal", required=True)
    required.add_argument("--session", required=True)
    required.add_argument("--now")

    evidence = sub.add_parser("public-evidence")
    evidence.add_argument("--project-root", type=Path, required=True)
    evidence.add_argument("--receipt", type=Path, required=True)

    scan = sub.add_parser("validate-scan")
    scan.add_argument("--request", type=Path, required=True)

    claim = sub.add_parser("validate-claim")
    claim.add_argument("--project-root", type=Path, required=True)
    claim.add_argument("--receipt", type=Path, required=True)
    claim.add_argument("--claim", type=Path, required=True)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "validate-registry":
            registry = validate_registry(load(args.registry.resolve(strict=True)))
            result = {"format": registry["format"], "status": "ACCEPTED",
                      "resources": sorted(registry["resources"]), "execution_authority": False}
        elif args.command == "admit":
            registry = load(args.registry.resolve(strict=True))
            mapping = _private_load(args.mapping, args.project_root, "operator-local mapping")
            result = admit_resource(
                registry, mapping, args.resource, task_id=args.task, principal=args.principal,
                session_id=args.session, permission_scope=args.permission_scope,
                permission_expires_at=args.permission_expires_at, now=args.now,
                cwd=str(args.project_root.resolve(strict=True)))
        elif args.command == "require":
            receipt = _private_load(args.receipt, args.project_root, "operator-local receipt")
            mapping = (_private_load(args.mapping, args.project_root, "operator-local mapping")
                       if args.mapping else None)
            result = admission_decision(
                receipt, resource_alias=args.resource, task_id=args.task,
                principal=args.principal, session_id=args.session, now=args.now,
                mappings=mapping)
        elif args.command == "public-evidence":
            result = public_evidence(_private_load(
                args.receipt, args.project_root, "operator-local receipt"))
        elif args.command == "validate-scan":
            result = validate_scan_request(load(args.request.resolve(strict=True)))
        else:
            receipt = _private_load(args.receipt, args.project_root, "operator-local receipt")
            result = validate_claim(receipt, load(args.claim.resolve(strict=True)))
        _emit(result)
        accepted = (result.get("status") not in {"REFUSED", "CLAIM_REJECTED"} and
                    result.get("state") not in {
                        "UNOBSERVED", "SANDBOX_BLOCKED", "PROCESS_START_FAILED",
                        "COMMAND_NONZERO", "MAPPING_MISSING", "PATH_NOT_FOUND",
                        "ACCESS_DENIED", "INVALID_OUTPUT", "STALE", "MAPPING_CHANGED"})
        return 0 if accepted else 2
    except (ValidationError, OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        _emit({"format": "awf-external-resource-error-1", "status": "REJECTED",
               "reason": str(exc), "execution_authority": False}, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
