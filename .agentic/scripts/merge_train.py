#!/usr/bin/env python3
"""Gh-backed merge-train publisher adapter.

Register this adapter with the host and publish through
``agentic.merge_train.publish_batch``. That entry point requires an authenticated
candidate, registered observer and publisher, freeze rechecks and durable intent.
Direct mutation commands are unavailable.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from agentic import ValidationError
from agentic import merge_train
from agentic.child_process import child_env


def _run_gh(gh, arguments):
    try:
        result = subprocess.run(
            [gh, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=120, check=False, env=child_env())
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("gh publication outcome is unknown") from exc
    if result.returncode:
        raise ValidationError(
            f"gh command failed with exit {result.returncode}")
    return result.stdout.strip()


def _batch_branch(branch):
    if (not isinstance(branch, str)
            or not re.fullmatch(r"batch/[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            or any(part in {"", ".", ".."} or part.endswith((".", ".lock"))
                   for part in branch.split("/"))
            or ".." in branch):
        raise ValidationError("publisher may push only a batch ref")
    return branch


def _api(gh, hostname, endpoint, *fields):
    output = _run_gh(gh, ["api", endpoint, "--hostname", hostname, *fields])
    try:
        return json.loads(output)
    except ValueError as exc:
        raise ValidationError("gh returned invalid JSON") from exc


def _pull_request(value):
    try:
        result = {"number": value["number"], "branch": value["head"]["ref"],
                  "base_branch": value["base"]["ref"],
                  "head_sha": value["head"]["sha"], "title": value["title"],
                  "draft": value["draft"]}
    except (KeyError, TypeError) as exc:
        raise ValidationError("gh returned an incomplete PR identity") from exc
    merge_train._positive(result["number"], "PR number")
    merge_train._sha(result["head_sha"], "PR head")
    for field in ("branch", "base_branch", "title"):
        merge_train._identifier(result[field], "PR " + field)
    if type(result["draft"]) is not bool:
        raise ValidationError("gh returned an invalid PR draft state")
    return result


def push_batch_ref(gh, repository, branch, head_sha, *, hostname="github.com"):
    _batch_branch(branch)
    merge_train._sha(head_sha, "batch head")
    value = _api(gh, hostname, f"repos/{repository}/git/refs",
                 "-f", f"ref=refs/heads/{branch}", "-f", f"sha={head_sha}")
    return _batch_ref(value)


def _batch_ref(value):
    try:
        ref, sha = value["ref"], value["object"]["sha"]
        if not ref.startswith("refs/heads/"):
            raise ValueError("unexpected ref")
        branch = _batch_branch(ref[len("refs/heads/"):])
        merge_train._sha(sha, "observed batch head")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("gh returned an invalid batch ref") from exc
    return {"branch": branch, "head_sha": sha}


def observe_batch_ref(gh, repository, branch, *, hostname="github.com"):
    _batch_branch(branch)
    values = _api(gh, hostname,
                  f"repos/{repository}/git/matching-refs/heads/{quote(branch, safe='/')}")
    if not isinstance(values, list):
        raise ValidationError("gh returned an invalid ref observation")
    exact = [value for value in values
             if isinstance(value, dict) and value.get("ref") == "refs/heads/" + branch]
    if len(exact) > 1:
        raise ValidationError("gh returned ambiguous batch refs")
    return _batch_ref(exact[0]) if exact else None


def observe_pull_request(gh, repository, branch, *, hostname="github.com"):
    _batch_branch(branch)
    query = urlencode({"state": "open", "head": repository.split("/")[0] + ":" + branch})
    values = _api(gh, hostname, f"repos/{repository}/pulls?{query}")
    if not isinstance(values, list) or len(values) > 1:
        raise ValidationError("gh returned an ambiguous PR observation")
    return _pull_request(values[0]) if values else None


def create_draft_pr(gh, repository, branch, base_branch, title, *, hostname="github.com"):
    _batch_branch(branch)
    if base_branch != "main":
        raise ValidationError("batch publication target branch must be main")
    merge_train._identifier(title, "draft PR title")
    return _pull_request(_api(
        gh, hostname, f"repos/{repository}/pulls", "-f", f"head={branch}",
        "-f", f"base={base_branch}", "-f", f"title={title}", "-F", "draft=true"))


def mark_draft_pr_ready(gh, repository, pr_number, *, hostname="github.com"):
    merge_train._positive(pr_number, "PR number")
    endpoint = f"repos/{repository}/pulls/{pr_number}"
    before = _pull_request(_api(gh, hostname, endpoint))
    _batch_branch(before["branch"])
    if before["number"] != pr_number or before["base_branch"] != "main":
        raise ValidationError("PR identity differs from the batch publication target")
    _run_gh(gh, ["pr", "ready", str(pr_number),
                 "--repo", f"{hostname}/{repository}"])
    return _pull_request(_api(gh, hostname, endpoint))


def register_publisher(*, gh, provider_identity, implementation_sha256, release_sha256):
    """Build a closed publisher registration after observing its repository.

    The host supplies its accepted implementation/release pins. Registration
    grants only the three publication capabilities and two read callbacks.
    """
    identity = merge_train._provider_identity(provider_identity)
    host = urlsplit(identity["host"])
    if (host.scheme != "https" or not host.hostname or host.netloc != host.hostname
            or host.path or host.query or host.fragment
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", identity["repository"])):
        raise ValidationError("publisher provider identity has an invalid host or repository")
    repository = identity["repository"]
    hostname = host.hostname
    registration = {
        "producer_kind": "merge_train_publisher", "adapter_kind": "gh",
        "implementation_sha256": implementation_sha256,
        "release_sha256": release_sha256,
        "capabilities": list(merge_train.PUBLISHER_CAPABILITIES),
        "provider_identity": deepcopy(identity),
        "receipt_namespace": merge_train.PUBLISHER_NAMESPACE,
        "push_batch_ref": lambda branch, head: push_batch_ref(
            gh, repository, branch, head, hostname=hostname),
        "create_draft_pr": lambda branch, base, title: create_draft_pr(
            gh, repository, branch, base, title, hostname=hostname),
        "mark_draft_pr_ready": lambda number: mark_draft_pr_ready(
            gh, repository, number, hostname=hostname),
        "observe_batch_ref": lambda branch: observe_batch_ref(
            gh, repository, branch, hostname=hostname),
        "observe_pull_request": lambda branch: observe_pull_request(
            gh, repository, branch, hostname=hostname),
    }
    merge_train._validate_publisher(registration, identity)
    observed = _api(gh, hostname, f"repos/{repository}")
    if (not isinstance(observed, dict)
            or type(observed.get("id")) is not int
            or observed["id"] != identity["repository_id"]
            or observed.get("full_name") != repository):
        raise ValidationError("gh repository differs from the publisher provider identity")
    return registration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    parser.error("publication requires the host's registered publish_batch path")


if __name__ == "__main__":
    raise SystemExit(main())
