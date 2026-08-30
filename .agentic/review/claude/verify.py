#!/usr/bin/env python3
"""Permission-negative tests and demonstration record for the AWF Claude adapter.

This is the executable form of the qualification evidence listed in
docs/providers/claude-code-action.md. Each subcommand writes a JSON fragment;
`record` merges them into demonstration.json, which bootstrap attaches to the
report and uses to set the `review.qualification` flags in project policy.

Subcommands
  model-negative      run in the model job's context (read-only GITHUB_TOKEN):
                      every write attempt must be refused (HTTP 403)
  publisher-negative  run with the reviewer App token (GH_TOKEN): writes outside
                      pull-request reviews must be refused; reads must succeed
  app-permissions     read the reviewer App's installation permissions with an
                      App JWT (signed with openssl, no third-party packages)
                      and require exactly pull_requests: write, metadata: read
  unit                run the adapter's offline unit tests
  hashes              sha256 of the protected adapter code and schema
  record              merge fragments into demonstration.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from awf_review_common import GITHUB_API_DEFAULT, AdapterError, http_json, sha256_file

EXPECTED_APP_PERMISSIONS = {"pull_requests": "write", "metadata": "read"}
REFUSED = (403,)


def api_base() -> str:
    return os.environ.get("GITHUB_API_URL", GITHUB_API_DEFAULT)


def attempt(name: str, method: str, url: str, token: str, body: dict | None,
            expected: tuple[int, ...] | None) -> dict:
    status, data, _ = http_json(method, url, token, body)
    message = ""
    if isinstance(data, dict):
        message = str(data.get("message", ""))[:200]
    result = {"name": name, "method": method, "url": url, "status": status, "message": message}
    if expected is None:
        result["informational"] = True
    else:
        result["expected"] = list(expected)
        result["pass"] = status in expected
    return result


def resolve_head(repo: str, pr: int, token: str, given: str | None) -> str:
    """Use the supplied head SHA or read it from the pull request."""
    if given:
        return given
    status, data, _ = http_json("GET", f"{api_base()}/repos/{repo}/pulls/{pr}", token)
    if status != 200 or not isinstance(data, dict):
        raise AdapterError(f"could not read pull request #{pr}: HTTP {status}")
    return str(data.get("head", {}).get("sha", ""))


def write_fragment(path: str, fragment: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(fragment, handle, indent=1)
    print(json.dumps(fragment, indent=1))


# ---------------------------------------------------------------------------
# model-negative
# ---------------------------------------------------------------------------

def cmd_model_negative(args: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    base = f"{api_base()}/repos/{args.repo}"
    stamp = int(time.time())
    try:
        head_sha = resolve_head(args.repo, args.pr, token, args.head_sha)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 1
    attempts = [
        attempt("read pull request", "GET", f"{base}/pulls/{args.pr}", token, None, (200,)),
        attempt("issue comment (must be refused)", "POST", f"{base}/issues/{args.pr}/comments", token,
                {"body": "AWF demonstration: this write must be refused."}, REFUSED),
        attempt("pull-request review (must be refused)", "POST", f"{base}/pulls/{args.pr}/reviews", token,
                {"event": "COMMENT", "body": "AWF demonstration: this write must be refused."}, REFUSED),
        attempt("create ref (must be refused)", "POST", f"{base}/git/refs", token,
                {"ref": f"refs/heads/awf-demo-must-fail-{stamp}", "sha": head_sha}, REFUSED),
        attempt("create check run (must be refused)", "POST", f"{base}/check-runs", token,
                {"name": "awf-demo-must-fail", "head_sha": head_sha}, REFUSED),
    ]
    passed = all(a.get("pass", True) for a in attempts)
    write_fragment(args.out, {"step": "model-negative", "principal": "GITHUB_TOKEN (model job)",
                              "attempts": attempts, "pass": passed})
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# publisher-negative
# ---------------------------------------------------------------------------

def cmd_publisher_negative(args: argparse.Namespace) -> int:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN (reviewer App token) is required", file=sys.stderr)
        return 2
    base = f"{api_base()}/repos/{args.repo}"
    stamp = int(time.time())
    try:
        head_sha = resolve_head(args.repo, args.pr, token, args.head_sha)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 1
    attempts = [
        attempt("read pull request", "GET", f"{base}/pulls/{args.pr}", token, None, (200,)),
        attempt("create ref (must be refused)", "POST", f"{base}/git/refs", token,
                {"ref": f"refs/heads/awf-demo-must-fail-{stamp}", "sha": head_sha}, REFUSED),
        attempt("create check run (must be refused)", "POST", f"{base}/check-runs", token,
                {"name": "awf-demo-must-fail", "head_sha": head_sha}, REFUSED),
        attempt("write file contents (must be refused)", "PUT", f"{base}/contents/awf-demo-must-fail-{stamp}.txt", token,
                {"message": "must fail", "content": base64.b64encode(b"must fail").decode("ascii")}, REFUSED),
        attempt("update repository settings (must be refused)", "PATCH", f"{base}", token,
                {"description": "must fail"}, REFUSED),
        attempt("add label (informational)", "POST", f"{base}/issues/{args.pr}/labels", token,
                {"labels": ["awf-demonstration"]}, None),
        attempt("merge pull request (must be refused)", "PUT", f"{base}/pulls/{args.pr}/merge", token,
                {"commit_title": "must fail"}, (403, 405, 404)),
    ]
    passed = all(a.get("pass", True) for a in attempts)
    write_fragment(args.out, {"step": "publisher-negative", "principal": "reviewer App installation token",
                              "attempts": attempts, "pass": passed})
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# app-permissions
# ---------------------------------------------------------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_app_jwt(app_id: str, private_key_pem: str) -> str:
    """RS256 JWT for GitHub App authentication, signed with the openssl CLI."""
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    key_file = tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem")
    try:
        os.chmod(key_file.name, 0o600)
        key_file.write(private_key_pem)
        key_file.close()
        completed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_file.name],
                                   input=signing_input, capture_output=True, check=False)
    finally:
        try:
            os.unlink(key_file.name)
        except OSError:
            pass
    if completed.returncode != 0:
        raise AdapterError("openssl signing failed: " + completed.stderr.decode("utf-8", errors="replace")[:200])
    return f"{header}.{payload}.{b64url(completed.stdout)}"


def cmd_app_permissions(args: argparse.Namespace) -> int:
    app_id = os.environ.get("AWF_REVIEWER_APP_ID", "")
    pem = os.environ.get("AWF_REVIEWER_APP_PRIVATE_KEY", "")
    if not app_id or not pem:
        print("AWF_REVIEWER_APP_ID and AWF_REVIEWER_APP_PRIVATE_KEY are required", file=sys.stderr)
        return 2
    try:
        jwt = make_app_jwt(app_id, pem)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 1
    status, data, _ = http_json("GET", f"{api_base()}/repos/{args.repo}/installation", jwt)
    if status != 200 or not isinstance(data, dict):
        write_fragment(args.out, {"step": "app-permissions", "pass": False, "status": status,
                                  "message": str(data)[:300]})
        return 1
    permissions = data.get("permissions", {})
    fragment = {
        "step": "app-permissions",
        "app_id": app_id,
        "app_slug": data.get("app_slug"),
        "installation_id": data.get("id"),
        "repository_selection": data.get("repository_selection"),
        "permissions": permissions,
        "expected": EXPECTED_APP_PERMISSIONS,
        "pass": permissions == EXPECTED_APP_PERMISSIONS,
    }
    write_fragment(args.out, fragment)
    return 0 if fragment["pass"] else 1


# ---------------------------------------------------------------------------
# unit, hashes, record
# ---------------------------------------------------------------------------

def cmd_unit(args: argparse.Namespace) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    suite = unittest.defaultTestLoader.discover(os.path.join(here, "tests"), pattern="test_*.py", top_level_dir=here)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    fragment = {
        "step": "unit",
        "tests_run": result.testsRun,
        "failures": [str(t) for t, _ in result.failures],
        "errors": [str(t) for t, _ in result.errors],
        "pass": result.wasSuccessful() and result.testsRun > 0,
    }
    write_fragment(args.out, fragment)
    return 0 if fragment["pass"] else 1


def cmd_hashes(args: argparse.Namespace) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    targets = {
        name: os.path.join(here, name)
        for name in ("awf_review_common.py", "build_input.py", "run_model.py", "publish.py", "verify.py")
    }
    targets["review-report.schema.json"] = args.schema
    fragment = {"step": "hashes", "sha256": {name: sha256_file(path) for name, path in targets.items()}, "pass": True}
    write_fragment(args.out, fragment)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    steps = []
    for path in args.inputs:
        with open(path, encoding="utf-8") as handle:
            steps.append(json.load(handle))
    by_name = {step.get("step"): step for step in steps}
    required = ("model-negative", "publisher-negative", "app-permissions", "unit", "hashes")
    missing = [name for name in required if name not in by_name]
    permissions_ok = all(by_name.get(name, {}).get("pass") for name in ("model-negative", "publisher-negative", "app-permissions"))
    run_url = ""
    if os.environ.get("GITHUB_SERVER_URL") and os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID"):
        run_url = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    record = {
        "schema_version": 1,
        "adapter": "claude",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_url": run_url,
        "steps": steps,
        "missing_steps": missing,
        "suggested_qualification": {
            "effective_permissions_verified": permissions_ok and not missing,
            "head_binding_verified": bool(args.head_binding_evidence),
            "instruction_paths_protected": False,
        },
        "head_binding_evidence": args.head_binding_evidence or "",
        "notes": [
            "effective_permissions_verified may be set true only if every permission step passed in this run.",
            "head_binding_verified requires the URL of a gate run that failed after a push made a prior review stale.",
            "instruction_paths_protected is set by the human after server-side path rules are confirmed active.",
        ],
    }
    write_fragment(args.out, record)
    return 0 if record["suggested_qualification"]["effective_permissions_verified"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("model-negative")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--head-sha", default=None, help="defaults to the pull request's current head")
    p.add_argument("--out", default="demo-model-negative.json")
    p.set_defaults(func=cmd_model_negative)

    p = sub.add_parser("publisher-negative")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--head-sha", default=None, help="defaults to the pull request's current head")
    p.add_argument("--out", default="demo-publisher-negative.json")
    p.set_defaults(func=cmd_publisher_negative)

    p = sub.add_parser("app-permissions")
    p.add_argument("--repo", required=True)
    p.add_argument("--out", default="demo-app-permissions.json")
    p.set_defaults(func=cmd_app_permissions)

    p = sub.add_parser("unit")
    p.add_argument("--out", default="demo-unit.json")
    p.set_defaults(func=cmd_unit)

    p = sub.add_parser("hashes")
    p.add_argument("--schema", required=True)
    p.add_argument("--out", default="demo-hashes.json")
    p.set_defaults(func=cmd_hashes)

    p = sub.add_parser("record")
    p.add_argument("--inputs", nargs="+", required=True)
    p.add_argument("--head-binding-evidence", default="")
    p.add_argument("--out", default="demonstration.json")
    p.set_defaults(func=cmd_record)

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
