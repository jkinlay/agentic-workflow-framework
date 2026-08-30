#!/usr/bin/env python3
"""Publish a validated AWF review report as a COMMENT pull-request review.

Runs in the publisher job. It contains no model. It holds the only
write-capable credential in the adapter: a per-run installation token for the
dedicated AWF Reviewer App, whose installation has `pull_requests: write` and
`metadata: read` and nothing else. The token reaches this process through
GH_TOKEN and is used for exactly one write: creating a review whose event is
the constant COMMENT and whose commit_id is the reviewed head SHA.

Fail-closed conditions (no review is posted, non-zero exit):
  - the report is not schema-valid or is for another repository/engine/identity;
  - the report's head SHA differs from the expected head or the live head (stale);
  - the report status is "error";
Idempotence: if a non-dismissed review by this identity already covers the
head SHA with a status marker, nothing is posted and the exit code is 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from awf_review_common import (
    BOT_LOGIN,
    GITHUB_API_DEFAULT,
    REVIEW_STATUS_MARKER,
    AdapterError,
    get_nested,
    github_paginate,
    http_json,
    is_hex40,
    load_yaml_lite,
    validate_report,
)

EVENT_COMMENT = "COMMENT"          # the only event this publisher can ever send
FORBIDDEN_EVENTS = ("APPROVE", "REQUEST_CHANGES")
MAX_BODY_CHARS = 60_000
MAX_COMMENT_CHARS = 8_000
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_STALE = 3
EXIT_ERROR_REPORT = 4


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--expected-head", required=True, help="head SHA the workflow event carried")
    parser.add_argument("--reviewer-identity", required=True, help="e.g. awf-reviewer[bot]")
    parser.add_argument("--policy", default=None, help="optional .agentic/project.yaml from the base checkout")
    parser.add_argument("--max-inline", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", help="build the payload but do not POST")
    parser.add_argument("--api", default=os.environ.get("GITHUB_API_URL", GITHUB_API_DEFAULT))
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def load_and_check_report(args: argparse.Namespace) -> dict:
    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)
    with open(args.schema, encoding="utf-8") as handle:
        schema = json.load(handle)
    problems = validate_report(report, schema)
    if problems:
        raise AdapterError("report is not schema-valid: " + "; ".join(problems[:8]))
    if report["engine"] != "claude":
        raise AdapterError(f"report engine is {report['engine']!r}, expected 'claude'")
    if report["repository"].lower() != args.repo.lower():
        raise AdapterError(f"report repository {report['repository']!r} does not match {args.repo!r}")
    if not is_hex40(args.expected_head) or report["head_sha"].lower() != args.expected_head.lower():
        raise AdapterError(f"report head {report['head_sha'][:12]} does not match expected head {args.expected_head[:12]}")
    if not BOT_LOGIN.match(args.reviewer_identity):
        raise AdapterError(f"reviewer identity {args.reviewer_identity!r} is not a GitHub App bot login")
    if report["reviewer_identity"] != args.reviewer_identity:
        raise AdapterError("report reviewer_identity does not match the configured reviewer identity")
    if args.policy:
        with open(args.policy, encoding="utf-8") as handle:
            policy = load_yaml_lite(handle.read())
        configured = get_nested(policy, "review.reviewer_identities.claude")
        if configured != args.reviewer_identity:
            raise AdapterError("configured review.reviewer_identities.claude does not match the reviewer identity")
    return report


# ---------------------------------------------------------------------------
# Diff positions
# ---------------------------------------------------------------------------

def valid_locations(patch: str | None) -> set[tuple[str, int]]:
    """Return the set of (side, line) pairs a review comment may attach to."""
    locations: set[tuple[str, int]] = set()
    if not patch:
        return locations
    old_line = new_line = 0
    for raw in patch.splitlines():
        header = HUNK_HEADER.match(raw)
        if header:
            old_line = int(header.group(1))
            new_line = int(header.group(3))
            continue
        if raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            locations.add(("RIGHT", new_line))
            new_line += 1
        elif raw.startswith("-"):
            locations.add(("LEFT", old_line))
            old_line += 1
        else:
            locations.add(("RIGHT", new_line))
            locations.add(("LEFT", old_line))
            new_line += 1
            old_line += 1
    return locations


def location_map(api: str, repo: str, pr: int, token: str) -> dict[str, set[tuple[str, int]]]:
    files = github_paginate(f"{api}/repos/{repo}/pulls/{pr}/files?per_page=100", token, max_pages=30)
    return {entry.get("filename", ""): valid_locations(entry.get("patch")) for entry in files}


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 40] + "\n[truncated by AWF publisher]"


def finding_text(finding: dict) -> str:
    flag = " (blocking)" if finding.get("blocking") else ""
    phase = finding.get("phase", "blind")
    return (f"**[{finding['severity']}] {finding['title']}**{flag}\n\n"
            f"{finding['description']}\n\n_AWF finding {finding['id']}, phase {phase}_")


def build_payload(report: dict, locations: dict[str, set[tuple[str, int]]], max_inline: int) -> dict:
    inline: list[dict] = []
    in_body: list[str] = []
    for finding in report["findings"]:
        path = finding["file"]
        line = finding.get("line")
        side = finding.get("side", "RIGHT")
        placeable = (line is not None and path in locations and (side, line) in locations[path])
        if placeable and len(inline) < max_inline:
            inline.append({"path": path, "line": line, "side": side,
                           "body": clip(finding_text(finding), MAX_COMMENT_CHARS)})
        else:
            where = f"{path}:{line}" if line else path
            note = "" if placeable else " (location not in diff)"
            in_body.append(f"- {where}{note} - " + clip(finding_text(finding), MAX_COMMENT_CHARS).replace("\n", "\n  "))

    blockers = sum(1 for f in report["findings"] if f.get("blocking"))
    lines = [
        "## AWF independent review",
        "",
        f"awf-review-status: {report['status']}",
        f"awf-review-head: {report['head_sha']}",
        f"awf-review-engine: {report['engine']} (model: {report.get('model') or 'unknown'}, "
        f"vendor: {report.get('vendor') or 'unknown'})",
        "",
        f"Findings: {len(report['findings'])} (blocking: {blockers}). "
        f"Inline comments: {len(inline)}. This is a COMMENT review; it is evidence for the awf/review "
        "check and the human merge decision, never an approval.",
    ]
    if report["status"] == "no_findings":
        lines.append("")
        lines.append(f"No findings for head {report['head_sha']}. Limitations below state what was not examined.")
    if in_body:
        lines += ["", "### Findings not placed inline", ""] + in_body
    if report.get("claim_assessments"):
        lines += ["", "### Developer claim assessments (claims treated as untrusted)", ""]
        lines += [f"- {c['claim_id']}: **{c['status']}** - {clip(c['evidence'], 1000)}" for c in report["claim_assessments"]]
    if report.get("limitations"):
        lines += ["", "### Limitations", ""] + [f"- {clip(l, 500)}" for l in report["limitations"]]
    body = clip("\n".join(lines), MAX_BODY_CHARS)

    payload = {"commit_id": report["head_sha"], "event": EVENT_COMMENT, "body": body, "comments": inline}
    assert payload["event"] == EVENT_COMMENT and payload["event"] not in FORBIDDEN_EVENTS
    return payload


# ---------------------------------------------------------------------------
# GitHub interaction
# ---------------------------------------------------------------------------

def live_head(api: str, repo: str, pr: int, token: str) -> str:
    status, data, _ = http_json("GET", f"{api}/repos/{repo}/pulls/{pr}", token)
    if status != 200 or not isinstance(data, dict):
        raise AdapterError(f"could not read pull request #{pr}: HTTP {status}")
    return str(get_nested(data, "head.sha", ""))


def already_published(api: str, repo: str, pr: int, token: str, identity: str, head: str) -> bool:
    reviews = github_paginate(f"{api}/repos/{repo}/pulls/{pr}/reviews?per_page=100", token)
    for review in reviews:
        login = str(get_nested(review, "user.login", ""))
        if (login.lower() == identity.lower() and str(review.get("commit_id", "")).lower() == head.lower()
                and review.get("state") != "DISMISSED" and REVIEW_STATUS_MARKER.search(review.get("body") or "")):
            return True
    return False


def post_review(api: str, repo: str, pr: int, token: str, payload: dict) -> dict:
    if payload["event"] != EVENT_COMMENT:
        raise AdapterError("refusing to send a non-COMMENT review event")
    url = f"{api}/repos/{repo}/pulls/{pr}/reviews"
    status, data, _ = http_json("POST", url, token, payload)
    if status == 422 and payload.get("comments"):
        # An inline position GitHub rejected: keep the evidence, move findings into the body.
        moved = "\n".join(f"- {c['path']}:{c['line']} ({c['side']}) - " + c["body"].replace("\n", "\n  ")
                          for c in payload["comments"])
        fallback = {"commit_id": payload["commit_id"], "event": EVENT_COMMENT,
                    "body": clip(payload["body"] + "\n\n### Findings (inline placement rejected)\n\n" + moved, MAX_BODY_CHARS),
                    "comments": []}
        status, data, _ = http_json("POST", url, token, fallback)
    if status not in (200, 201) or not isinstance(data, dict):
        raise AdapterError(f"review creation failed: HTTP {status}: {str(data)[:300]}")
    if data.get("state") not in ("COMMENTED", None):
        raise AdapterError(f"unexpected review state {data.get('state')!r}")
    return data


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    token = os.environ.get("GH_TOKEN", "")
    if not token and not args.dry_run:
        print("publish: GH_TOKEN (reviewer App installation token) is required", file=sys.stderr)
        return EXIT_INVALID
    try:
        report = load_and_check_report(args)
    except (AdapterError, OSError, json.JSONDecodeError) as err:
        print(f"publish: refusing to publish: {err}", file=sys.stderr)
        return EXIT_INVALID
    if report["status"] == "error":
        print(f"publish: report status is error; nothing is published: {report.get('error')}", file=sys.stderr)
        return EXIT_ERROR_REPORT
    try:
        if not args.dry_run:
            head = live_head(args.api, args.repo, args.pr, token)
            if head.lower() != report["head_sha"].lower():
                print(f"publish: live head {head[:12]} differs from reviewed head {report['head_sha'][:12]}; stale", file=sys.stderr)
                return EXIT_STALE
            if already_published(args.api, args.repo, args.pr, token, args.reviewer_identity, head):
                print("publish: a review by this identity already covers the head SHA; nothing to do")
                return EXIT_OK
            locations = location_map(args.api, args.repo, args.pr, token)
        else:
            locations = {}
        payload = build_payload(report, locations, args.max_inline)
        if args.dry_run:
            print(json.dumps(payload, indent=1))
            return EXIT_OK
        result = post_review(args.api, args.repo, args.pr, token, payload)
    except AdapterError as err:
        print(f"publish: {err}", file=sys.stderr)
        return EXIT_INVALID
    print(f"publish: posted COMMENT review {result.get('id')} for head {report['head_sha'][:12]}: {result.get('html_url', '')}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
