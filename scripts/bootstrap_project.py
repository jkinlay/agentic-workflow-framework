#!/usr/bin/env python3
"""Verify and transactionally install managed v1.9.4 workflow files."""
from pathlib import Path
import argparse
import json
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
import importlib.util
_missing = [name for name in ("yaml", "jsonschema") if importlib.util.find_spec(name) is None]
if _missing:
    raise SystemExit("BOOTSTRAP REJECTED: missing locked dependencies (" + ", ".join(_missing) + "). Install them first: "
                     "python -m pip install --require-hashes --only-binary=:all: -r .agentic/requirements.lock")
from agentic.installer import complete_runtime_transaction, install, recover
from agentic import ValidationError
from agentic.adoption_config import (ensure_installed_runtime, post_install_checks,
                                     prevalidate_runtime_wheelhouse)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--mode", choices=["install", "upgrade"], default="install")
    parser.add_argument("--propose-operating-capacity", action="store_true",
        help="Stage an existing project's ceiling of at least six and derived reviewer count in local governance for an owner-reviewed adoption PR")
    parser.add_argument("--on-conflict", choices=["error", "backup"], default="error")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--runtime-wheelhouse", type=Path,
        help="Mandatory for writing install/upgrade: offline directory containing exactly one complete requirements.lock-pinned wheel per runtime dependency")
    parser.add_argument("--project-name")
    parser.add_argument("--project-short-name")
    parser.add_argument("--jira-key")
    parser.add_argument("--jira-site")
    parser.add_argument("--github-repo")
    parser.add_argument("--repository-id", type=int)
    parser.add_argument("--test-command", help="Record a project validation command; bootstrap does not execute it")
    parser.add_argument("--codeowner", default="@maintainer", help="Seed one @handle or @organization/team only when no project CODEOWNERS exists")
    parser.add_argument("--rules-observation", type=Path, help="Optional pinned read-only rules observation; missing or invalid evidence never blocks adoption")
    parser.add_argument("--expected-rules-observation-sha256")
    parser.add_argument("--default-branch", help="Optional expected branch; live observation discovers the repository's actual default")
    parser.add_argument("--review-app-id", type=int, help="Expected App ID for the awf/review rules prerequisite; not adapter qualification")
    args = parser.parse_args()
    # Resolve platform aliases such as macOS /tmp -> /private/tmp before the
    # installer's alias-refusing path checks run.
    args.dest = args.dest.expanduser().resolve()
    try:
        if args.recover and args.propose_operating_capacity:
            raise ValidationError("Recovery cannot stage a governance proposal; recover first and rerun adoption explicitly")
        if args.recover:
            result = recover(args.dest)
        else:
            prepared_wheelhouse = None
            if not args.dry_run:
                prepared_wheelhouse = prevalidate_runtime_wheelhouse(
                    ROOT, args.expected_manifest_sha256, args.runtime_wheelhouse)
            overrides = {}
            for source, target in [("project_name", "name"), ("project_short_name", "short_name"),
                    ("jira_key", "jira_key"), ("jira_site", "jira_site"), ("github_repo", "repository"),
                    ("repository_id", "repository_id"), ("test_command", "test_command"), ("default_branch", "base_branch")]:
                if getattr(args, source) is not None:
                    overrides[target] = getattr(args, source)
            result = install(ROOT, args.dest, args.expected_manifest_sha256, args.mode, args.on_conflict, overrides, args.dry_run,
                codeowner=args.codeowner, rules_observation=args.rules_observation,
                expected_rules_observation_sha256=args.expected_rules_observation_sha256,
                default_branch=args.default_branch, review_app_id=args.review_app_id, configure=True,
                propose_operating_capacity=args.propose_operating_capacity,
                defer_runtime=not args.dry_run)
            if not args.dry_run:
                transaction_id = result.pop("_runtime_transaction_id", None)
                if transaction_id is None:
                    try:
                        recover(args.dest)
                    except (ValidationError, OSError, ValueError) as rollback_error:
                        raise ValidationError(
                            "Installer lost the bootstrap transaction identity and rollback could not be proved") from rollback_error
                    raise ValidationError("Installer did not retain the bootstrap runtime transaction; managed files were rolled back")
                def build_runtime(transaction):
                    if result.get("source_manifest_sha256") != prepared_wheelhouse.source_manifest_sha256:
                        raise ValidationError("Installed release identity changed after runtime prevalidation")
                    return ensure_installed_runtime(
                        args.dest, prepared_wheelhouse=prepared_wheelhouse,
                        transaction=transaction)
                result["runtime"] = complete_runtime_transaction(
                    args.dest, transaction_id, build_runtime)
                result.update(post_install_checks(args.dest, result))
        print(json.dumps(result, indent=2))
        return 1 if result["status"] == "INSTALLED_UNCONFIGURED" else 2 if result["status"] == "INSTALLATION_VERIFICATION_FAILED" else 0
    except (ValidationError, OSError, ValueError) as exc:
        print(f"BOOTSTRAP REJECTED: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
