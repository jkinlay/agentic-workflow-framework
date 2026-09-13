#!/usr/bin/env python3
"""Verify and transactionally install managed v1.7.0 workflow files."""
from pathlib import Path
import argparse
import json
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.installer import install, recover
from agentic import ValidationError

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--mode", choices=["install", "upgrade"], default="install")
    parser.add_argument("--on-conflict", choices=["error", "backup"], default="error")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--project-name")
    parser.add_argument("--jira-key")
    parser.add_argument("--github-repo")
    args = parser.parse_args()
    try:
        if args.recover:
            result = recover(args.dest)
        else:
            overrides = {}
            for source, target in [("project_name", "name"), ("jira_key", "jira_key"), ("github_repo", "repository")]:
                if getattr(args, source):
                    overrides[target] = getattr(args, source)
            result = install(ROOT, args.dest, args.expected_manifest_sha256, args.mode, args.on_conflict, overrides, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    except (ValidationError, OSError, ValueError) as exc:
        print(f"BOOTSTRAP REJECTED: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
