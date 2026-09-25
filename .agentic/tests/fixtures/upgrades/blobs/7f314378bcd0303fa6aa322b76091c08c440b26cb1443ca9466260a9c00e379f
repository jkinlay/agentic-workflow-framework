#!/usr/bin/env python3
"""Permission-negative tests and demonstration record for the AWF Claude adapter.

This is the executable form of the qualification evidence listed in
the external-review runbook. Each subcommand writes a JSON fragment;
`record` merges operator-supplied fragments, not authenticated attestations.
It never changes project qualification. Live negative tests can mutate a
misconfigured repository: run only against an authorized disposable canary.

Negative probes require --disposable-repository-confirmation OWNER/REPO plus
--canary-marker ABS_EXTERNAL_JSON and --canary-marker-sha256 SHA256. The operator
must provision/pin this file outside the current working directory, adapter
source checkout and GITHUB_WORKSPACE. No links/reparse points/hardlinks are
accepted. Exact marker fields: format="awf-disposable-canary-1", repository,
repository_id (positive integer), pr_number (positive integer), head_sha (40 hex),
expires_at (Unix integer, future and at most 24 hours away), disposable=true,
allowed_probes (unique subset of model-negative/publisher-negative including
the invoked probe). This is an operator assertion, not an authenticated grant.
Live repository/PR identities must match before any mutation. Expiry is checked
before each probe; expired remaining probes are recorded as not attempted.

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
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from awf_review_common import GITHUB_API_DEFAULT, AdapterError, http_json, sha256_file, strict_json, is_hex40

EXPECTED_APP_PERMISSIONS = {"pull_requests": "write", "metadata": "read"}
REFUSED = (403,)
CANARY_FORMAT = "awf-disposable-canary-1"
CANARY_PROBES = {"model-negative", "publisher-negative"}


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


def canary_attempt(marker: dict, name: str, method: str, url: str, token: str,
                   body: dict | None, expected: tuple[int, ...] | None) -> dict:
    if time.time() >= marker["expires_at"]:
        return {"name": name, "method": method, "url": url, "pass": False,
                "not_attempted": True, "error": "disposable canary marker expired; remaining probes stopped"}
    return attempt(name, method, url, token, body, expected)


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


def load_canary_marker(args: argparse.Namespace) -> dict:
    """Read a hash-pinned operator assertion outside all candidate/source checkouts."""
    supplied = getattr(args, "canary_marker", None)
    expected = getattr(args, "canary_marker_sha256", None)
    if not supplied or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise AdapterError("Live probes require --canary-marker ABS_EXTERNAL_JSON and --canary-marker-sha256 SHA256 from the canary operator")
    path = Path(supplied)
    if not path.is_absolute() or ".." in path.parts:
        raise AdapterError("canary marker must be an absolute external path without traversal")
    try:
        for item in [*reversed(path.parents), path]:
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise AdapterError("canary marker and ancestors must not be links/reparse points")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16_384:
            raise AdapterError("canary marker must be a bounded, single-link regular file")
        resolved = path.resolve()
        roots = {Path.cwd().resolve(), Path(__file__).resolve().parents[3]}
        if os.environ.get("GITHUB_WORKSPACE"):
            roots.add(Path(os.environ["GITHUB_WORKSPACE"]).resolve())
        if any(resolved == root or resolved.is_relative_to(root) for root in roots):
            raise AdapterError("canary marker must be outside the working directory, adapter source and GITHUB_WORKSPACE")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_nlink, opened.st_size) != (info.st_dev, info.st_ino, 1, info.st_size):
                raise AdapterError("canary marker changed while opening")
            raw = handle.read(16_385)
        if len(raw) > 16_384:
            raise AdapterError("canary marker exceeds its byte cap")
    except OSError as err:
        raise AdapterError("canary marker could not be read safely") from err
    import hashlib
    if hashlib.sha256(raw).hexdigest() != expected:
        raise AdapterError("canary marker does not match its operator-supplied SHA-256")
    marker = strict_json(raw, "canary marker")
    fields = {"format", "repository", "repository_id", "pr_number", "head_sha", "expires_at", "disposable", "allowed_probes"}
    if not isinstance(marker, dict) or set(marker) != fields or marker["format"] != CANARY_FORMAT or marker["disposable"] is not True:
        raise AdapterError("canary marker is not an explicit disposable-canary assertion")
    if marker["repository"] != args.repo or marker["pr_number"] != args.pr or \
            any(not isinstance(marker[key], int) or isinstance(marker[key], bool) or marker[key] < 1
                for key in ("repository_id", "pr_number", "expires_at")) or not is_hex40(marker["head_sha"]):
        raise AdapterError("canary marker repository, numeric identity, PR or head binding is invalid")
    if not time.time() < marker["expires_at"] <= time.time() + 86_400:
        raise AdapterError("canary marker must expire in the next 24 hours")
    probes = marker["allowed_probes"]
    if not isinstance(probes, list) or any(not isinstance(probe, str) for probe in probes) or \
            len(probes) != len(set(probes)) or not set(probes) <= CANARY_PROBES or args.command not in probes:
        raise AdapterError("canary marker does not allow this exact negative probe")
    if args.head_sha is not None and args.head_sha.lower() != marker["head_sha"].lower():
        raise AdapterError("supplied head differs from the disposable canary marker")
    return marker


def verify_canary_target(args: argparse.Namespace, marker: dict, token: str) -> str:
    """Only read metadata until the live repo ID and PR head match the external assertion."""
    base = f"{api_base()}/repos/{args.repo}"
    status, repo, _ = http_json("GET", base, token)
    if status != 200 or not isinstance(repo, dict) or repo.get("id") != marker["repository_id"] or repo.get("full_name") != marker["repository"]:
        raise AdapterError("live repository does not match the disposable canary identity")
    status, pr, _ = http_json("GET", f"{base}/pulls/{args.pr}", token)
    head = pr.get("head") if isinstance(pr, dict) else None
    base_repo = pr.get("base", {}).get("repo", {}) if isinstance(pr, dict) and isinstance(pr.get("base"), dict) else {}
    if status != 200 or not isinstance(head, dict) or head.get("sha") != marker["head_sha"] or \
            not isinstance(head.get("repo"), dict) or head["repo"].get("id") != marker["repository_id"] or \
            not isinstance(base_repo, dict) or base_repo.get("id") != marker["repository_id"] or time.time() >= marker["expires_at"]:
        raise AdapterError("live pull request does not match the unexpired disposable canary binding")
    return marker["head_sha"]


# ---------------------------------------------------------------------------
# model-negative
# ---------------------------------------------------------------------------

def cmd_model_negative(args: argparse.Namespace) -> int:
    if args.disposable_repository_confirmation != args.repo:
        print("Live probes require --disposable-repository-confirmation OWNER/REPO; authorize a disposable canary first", file=sys.stderr)
        return 2
    try:
        marker = load_canary_marker(args)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    base = f"{api_base()}/repos/{args.repo}"
    stamp = int(time.time())
    try:
        head_sha = verify_canary_target(args, marker, token)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 1
    attempts = [
        canary_attempt(marker, "read pull request", "GET", f"{base}/pulls/{args.pr}", token, None, (200,)),
        canary_attempt(marker, "issue comment (must be refused)", "POST", f"{base}/issues/{args.pr}/comments", token,
                {"body": "AWF demonstration: this write must be refused."}, REFUSED),
        canary_attempt(marker, "pull-request review (must be refused)", "POST", f"{base}/pulls/{args.pr}/reviews", token,
                {"event": "COMMENT", "body": "AWF demonstration: this write must be refused."}, REFUSED),
        canary_attempt(marker, "create ref (must be refused)", "POST", f"{base}/git/refs", token,
                {"ref": f"refs/heads/awf-demo-must-fail-{stamp}", "sha": head_sha}, REFUSED),
        canary_attempt(marker, "create check run (must be refused)", "POST", f"{base}/check-runs", token,
                {"name": "awf-demo-must-fail", "head_sha": head_sha}, REFUSED),
    ]
    passed = all(a.get("pass", True) for a in attempts)
    write_fragment(args.out, {"step": "model-negative", "principal": "GITHUB_TOKEN (model job)",
                              "attempts": attempts, "pass": passed, "canary_marker_sha256": args.canary_marker_sha256,
                              "canary_repository_id": marker["repository_id"], "operator_assertion_only": True})
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# publisher-negative
# ---------------------------------------------------------------------------

def cmd_publisher_negative(args: argparse.Namespace) -> int:
    if args.disposable_repository_confirmation != args.repo:
        print("Live probes require --disposable-repository-confirmation OWNER/REPO; authorize a disposable canary first", file=sys.stderr)
        return 2
    try:
        marker = load_canary_marker(args)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 2
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN (reviewer App token) is required", file=sys.stderr)
        return 2
    base = f"{api_base()}/repos/{args.repo}"
    stamp = int(time.time())
    try:
        head_sha = verify_canary_target(args, marker, token)
    except AdapterError as err:
        print(str(err), file=sys.stderr)
        return 1
    attempts = [
        canary_attempt(marker, "read pull request", "GET", f"{base}/pulls/{args.pr}", token, None, (200,)),
        canary_attempt(marker, "create ref (must be refused)", "POST", f"{base}/git/refs", token,
                {"ref": f"refs/heads/awf-demo-must-fail-{stamp}", "sha": head_sha}, REFUSED),
        canary_attempt(marker, "create check run (must be refused)", "POST", f"{base}/check-runs", token,
                {"name": "awf-demo-must-fail", "head_sha": head_sha}, REFUSED),
        canary_attempt(marker, "write file contents (must be refused)", "PUT", f"{base}/contents/awf-demo-must-fail-{stamp}.txt", token,
                {"message": "must fail", "content": base64.b64encode(b"must fail").decode("ascii")}, REFUSED),
        canary_attempt(marker, "update repository settings (must be refused)", "PATCH", f"{base}", token,
                {"description": "must fail"}, REFUSED),
        canary_attempt(marker, "add label (informational)", "POST", f"{base}/issues/{args.pr}/labels", token,
                {"labels": ["awf-demonstration"]}, None),
        canary_attempt(marker, "merge pull request (must be refused)", "PUT", f"{base}/pulls/{args.pr}/merge", token,
                {"commit_title": "must fail", "sha": head_sha}, (403, 405, 404)),
    ]
    passed = all(a.get("pass", True) for a in attempts)
    write_fragment(args.out, {"step": "publisher-negative", "principal": "reviewer App installation token",
                              "attempts": attempts, "pass": passed, "canary_marker_sha256": args.canary_marker_sha256,
                              "canary_repository_id": marker["repository_id"], "operator_assertion_only": True})
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
    suite = unittest.TestLoader().discover(os.path.join(here, "tests"), pattern="test_*.py", top_level_dir=here)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    fragment = {
        "step": "unit",
        "tests_run": result.testsRun,
        "failures": [str(t) for t, _ in result.failures],
        "errors": [str(t) for t, _ in result.errors],
        "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "pass": result.wasSuccessful() and result.testsRun > 0,
    }
    write_fragment(args.out, fragment)
    return 0 if fragment["pass"] else 1


def cmd_hashes(args: argparse.Namespace) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    targets = {
        name: os.path.join(here, name)
        for name in ("awf_review_common.py", "build_input.py", "policy.py", "run_model.py", "model_transport.py", "publish.py", "verify.py")
    }
    for name in ("awf-review-claude.yml", "awf-review-gate.yml"):
        targets["workflows/" + name] = os.path.join(here, "..", "workflows", name)
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
    permissions_ok = (len(by_name) == len(steps) and
                      all(by_name.get(name, {}).get("pass") is True for name in required))
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
            "environment_secret_boundaries_verified": False,
            "instruction_paths_protected": False,
        },
        "head_binding_evidence": args.head_binding_evidence or "",
        "notes": [
            "effective_permissions_verified may be set true only if every permission step passed in this run.",
            "head_binding_verified requires the URL of a gate run that failed after a push made a prior review stale.",
            "environment_secret_boundaries_verified remains false until the owner verifies both actual environments, their secret inventory and exact default-branch deployment restrictions; these probes do not attest those settings.",
            "instruction_paths_protected is set by the human after server-side path rules are confirmed active.",
        ],
    }
    write_fragment(args.out, record)
    return 0 if record["suggested_qualification"]["effective_permissions_verified"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("model-negative")
    p.add_argument("--disposable-repository-confirmation")
    p.add_argument("--canary-marker")
    p.add_argument("--canary-marker-sha256")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument("--head-sha", default=None, help="defaults to the pull request's current head")
    p.add_argument("--out", default="demo-model-negative.json")
    p.set_defaults(func=cmd_model_negative)

    p = sub.add_parser("publisher-negative")
    p.add_argument("--disposable-repository-confirmation")
    p.add_argument("--canary-marker")
    p.add_argument("--canary-marker-sha256")
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
