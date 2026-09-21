#!/usr/bin/env python3
"""Verify and transactionally install managed v1.9.1 workflow files."""
from pathlib import Path
import argparse
import json
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.installer import install, recover
from agentic import ValidationError
from agentic.adoption_config import post_install_checks

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
    try:
        if args.recover and args.propose_operating_capacity:
            raise ValidationError("Recovery cannot stage a governance proposal; recover first and rerun adoption explicitly")
        if args.recover:
            result = recover(args.dest)
        else:
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
                propose_operating_capacity=args.propose_operating_capacity)
            if not args.dry_run:
                result.update(post_install_checks(args.dest, result))
        print(json.dumps(result, indent=2))
        return 1 if result["status"] == "INSTALLED_UNCONFIGURED" else 2 if result["status"] == "INSTALLATION_VERIFICATION_FAILED" else 0
    except (ValidationError, OSError, ValueError) as exc:
        print(f"BOOTSTRAP REJECTED: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
