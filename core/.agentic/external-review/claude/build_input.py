#!/usr/bin/env python3
"""Build the review input for the AWF Claude model job.

Runs in the model job with a read-only GITHUB_TOKEN. It gathers:

  - the pull request's per-file patches from the GitHub API (no git command is
    executed against pull-request content);
  - the full text of changed files, read as data from the read-only checkout
    of the pull-request head in --pr-dir;
  - the task contract and, for the second review phase, the developer's
    execution report, both read as opaque text if present.

It deliberately excludes the pull-request title, body and comments: they are
untrusted free text and the blind phase must not see the developer's framing.
Every bound that truncates input is recorded in `limitations` so the report
states what the reviewer did not see.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from awf_review_common import (
    GITHUB_API_DEFAULT,
    AdapterError,
    bounded_text,
    github_paginate,
    is_hex40,
    read_text_file,
)

INPUT_SCHEMA_VERSION = 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--pr-dir", required=True, help="read-only checkout of the PR head")
    parser.add_argument("--task-contract", default=".agentic/task-contract.yaml",
                        help="path inside --pr-dir; missing is recorded, not fatal")
    parser.add_argument("--execution-report", default=".agentic/execution-report.json",
                        help="path inside --pr-dir; missing disables the claims phase")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-diff-bytes", type=int, default=400_000)
    parser.add_argument("--max-file-bytes", type=int, default=200_000)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-contract-bytes", type=int, default=64_000)
    parser.add_argument("--max-report-bytes", type=int, default=128_000)
    parser.add_argument("--api", default=os.environ.get("GITHUB_API_URL", GITHUB_API_DEFAULT))
    return parser.parse_args(argv)


def verify_checkout_head(pr_dir: str, head_sha: str) -> None:
    """Confirm the read-only checkout is at the expected head without running git."""
    head_path = os.path.join(pr_dir, ".git", "HEAD")
    if not os.path.isfile(head_path):
        raise AdapterError(f"{pr_dir} is not a git checkout (no .git/HEAD)")
    with open(head_path, encoding="utf-8", errors="replace") as handle:
        content = handle.read().strip()
    if content.startswith("ref: "):
        ref_path = os.path.join(pr_dir, ".git", content[5:].strip())
        if os.path.isfile(ref_path):
            with open(ref_path, encoding="utf-8", errors="replace") as handle:
                content = handle.read().strip()
    if content.lower() != head_sha.lower():
        raise AdapterError(f"checkout HEAD {content[:12]} does not match expected head {head_sha[:12]}")


def collect_changed_files(api: str, repo: str, pr: int, token: str) -> list[dict]:
    url = f"{api}/repos/{repo}/pulls/{pr}/files?per_page=100"
    files = github_paginate(url, token, max_pages=30)
    result = []
    for entry in files:
        result.append({
            "path": entry.get("filename", ""),
            "previous_path": entry.get("previous_filename"),
            "status": entry.get("status", ""),
            "additions": int(entry.get("additions", 0) or 0),
            "deletions": int(entry.get("deletions", 0) or 0),
            "patch": entry.get("patch"),
        })
    return result


def safe_join(root: str, relative: str) -> str | None:
    """Join and refuse paths that escape the checkout (symlink or ..)."""
    candidate = os.path.realpath(os.path.join(root, relative))
    root_real = os.path.realpath(root)
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        return None
    return candidate


def build(args: argparse.Namespace, token: str) -> dict:
    if not is_hex40(args.base_sha) or not is_hex40(args.head_sha):
        raise AdapterError("base and head SHAs must be 40 hex characters")
    verify_checkout_head(args.pr_dir, args.head_sha)

    limitations: list[str] = []
    changed = collect_changed_files(args.api, args.repo, args.pr, token)
    if len(changed) > args.max_files:
        limitations.append(f"Only the first {args.max_files} of {len(changed)} changed files were included.")
        changed = changed[: args.max_files]

    diff_parts: list[str] = []
    for entry in changed:
        header = f"### {entry['status']} {entry['path']}"
        if entry.get("previous_path"):
            header += f" (from {entry['previous_path']})"
        if entry["patch"]:
            diff_parts.append(header + "\n" + entry["patch"] + "\n")
        else:
            diff_parts.append(header + "\n[no textual patch available: binary, generated, or too large]\n")
            limitations.append(f"No textual patch was available for {entry['path']}.")
    diff_text, diff_truncated = bounded_text("\n".join(diff_parts), args.max_diff_bytes)
    if diff_truncated:
        limitations.append(f"The unified diff was truncated at {args.max_diff_bytes} characters.")

    files_out: list[dict] = []
    for entry in changed:
        if entry["status"] == "removed":
            continue
        target = safe_join(args.pr_dir, entry["path"])
        if target is None or not os.path.isfile(target) or os.path.islink(target):
            limitations.append(f"Full content of {entry['path']} was not readable from the checkout.")
            continue
        text, truncated, binary = read_text_file(target, args.max_file_bytes)
        if binary:
            limitations.append(f"{entry['path']} is binary; only its patch header was included.")
            continue
        if truncated:
            limitations.append(f"{entry['path']} was truncated at {args.max_file_bytes} bytes.")
        files_out.append({"path": entry["path"], "content": text, "truncated": truncated})

    task_contract = None
    contract_path = safe_join(args.pr_dir, args.task_contract)
    if contract_path and os.path.isfile(contract_path):
        text, truncated, binary = read_text_file(contract_path, args.max_contract_bytes)
        if not binary:
            task_contract = text
            if truncated:
                limitations.append("The task contract was truncated.")
    if task_contract is None:
        limitations.append("No task contract was supplied; acceptance criteria could not be traced.")

    execution_report = None
    report_path = safe_join(args.pr_dir, args.execution_report)
    if report_path and os.path.isfile(report_path):
        text, truncated, binary = read_text_file(report_path, args.max_report_bytes)
        if not binary:
            execution_report = text
            if truncated:
                limitations.append("The developer execution report was truncated.")
    if execution_report is None:
        limitations.append("No developer execution report was supplied; the claims phase was skipped.")

    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "repository": args.repo,
        "pr_number": args.pr,
        "base_sha": args.base_sha,
        "head_sha": args.head_sha,
        "changed_files": [
            {k: v for k, v in entry.items() if k != "patch"} | {"patch_included": bool(entry["patch"])}
            for entry in changed
        ],
        "diff": diff_text,
        "files": files_out,
        "task_contract": task_contract,
        "execution_report": execution_report,
        "limitations": limitations,
        "bounds": {
            "max_diff_bytes": args.max_diff_bytes,
            "max_file_bytes": args.max_file_bytes,
            "max_files": args.max_files,
        },
        "excluded_by_design": ["pull request title", "pull request body", "pull request comments"],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("build_input: GITHUB_TOKEN (read-only) is required", file=sys.stderr)
        return 2
    try:
        payload = build(args, token)
    except AdapterError as err:
        print(f"build_input: {err}", file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"build_input: {len(payload['changed_files'])} changed files, "
          f"{len(payload['files'])} full files, {len(payload['limitations'])} limitations -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
