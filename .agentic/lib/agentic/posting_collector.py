"""Trusted review-verdict posting observation for the production final gate.

Configuration may narrow ``COLLECTOR_ID`` but cannot create this registry.
Membership is produced only after this installed module re-reads the comment
and pull-request body through the enrolled review-host provider.
"""
from __future__ import annotations

import base64
from pathlib import Path
import re

from . import ValidationError
from .canonical import canonical, sha256
from .gates import posting_collector_receipt_sha256
from .providers.github_review_host import HostDriver, load_config


COLLECTOR_ID = "github-production-posting-collector"
PROVIDER_KIND = "github"
_DIGEST = re.compile(r"[0-9a-f]{64}")


class PostingCollectorMismatch(ValidationError):
    """A live provider observation differs from the retained posting claim."""


def _mismatch(code, detail):
    raise PostingCollectorMismatch(f"{code}: {detail}")


def _digest(value, label):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _mismatch("POSTING_REGISTRATION_MISMATCH", f"{label} is not a SHA-256 digest")
    return value


def _registration(release_sha256, repository_id):
    return {
        "collector_id": COLLECTOR_ID,
        "provider_kind": PROVIDER_KIND,
        "implementation_sha256": sha256(Path(__file__).read_bytes()),
        "release_sha256": _digest(release_sha256, "release_sha256"),
        "repository_ids": [repository_id],
        "receipts": {},
    }


def _gate_entry(registration):
    return {key: value for key, value in registration.items() if key != "collector_id"}


def _provider_response(comment_id, comment_url, comment_body, pr_body):
    # Retain only the provider fields used by the gate.  This deliberately
    # excludes actor profiles, headers and other unrelated provider data.
    return canonical({
        "comment_id": comment_id,
        "comment_url": comment_url,
        "comment_body": comment_body,
        "pr_body": pr_body,
    })


def collect_posting_observation(review_host, *, candidate, verdict,
                                review_round_receipt, collector_run_id,
                                observed_at, release_sha256):
    """Read one posted verdict and return its observation and registration.

    ``review_host`` is the existing GitHub review-host provider (or a
    test-local double).  The caller owns the collector run and timestamp; this
    function owns provider reads and the trusted collector implementation pin.
    """
    if not isinstance(candidate, dict) or not isinstance(verdict, dict):
        _mismatch("POSTING_ROUND_BINDING_MISMATCH", "candidate and verdict are required")
    if not isinstance(review_round_receipt, dict):
        _mismatch("POSTING_ROUND_BINDING_MISMATCH", "retained round receipt is required")
    artifact_binding = verdict.get("critic_artifact_binding")
    if (review_round_receipt.get("critic_artifact_binding") != artifact_binding
            or review_round_receipt.get("review_verdict_sha256")
            != sha256(review_round_receipt.get("review_verdict_json", "").encode("utf-8"))):
        _mismatch("POSTING_ROUND_BINDING_MISMATCH",
                  "verdict and retained round receipt do not share one critic artifact")
    verdict_json = review_round_receipt["review_verdict_json"]
    verdict_sha256 = review_round_receipt["review_verdict_sha256"]
    prefix = (
        f"{candidate['host']}/{candidate['repository']}/pull/{candidate['pr_number']}"
    )
    match = re.fullmatch(re.escape(prefix) + r"#issuecomment-([1-9][0-9]*)",
                         verdict.get("pr_comment_url", ""))
    if match is None:
        _mismatch("POSTING_COMMENT_MISMATCH",
                  "verdict comment URL is not the candidate's immutable comment URL")
    comment_id = int(match.group(1))
    expected_body_link = (
        f"{prefix}#review-verdict-{verdict['round']}-{verdict['record_id']}"
    )
    if verdict.get("pr_body_link") != expected_body_link:
        _mismatch("POSTING_ANCHOR_MISMATCH",
                  "verdict PR-body link does not identify its round and record")

    comment = review_host.api(f"issues/comments/{comment_id}")
    pull = review_host.api(f"pulls/{candidate['pr_number']}")
    if (not isinstance(comment, dict) or comment.get("id") != comment_id
            or comment.get("html_url") != verdict["pr_comment_url"]
            or not isinstance(comment.get("body"), str)
            or verdict_json not in comment["body"]):
        _mismatch("POSTING_COMMENT_MISMATCH",
                  "live immutable comment identity, URL or exact body differs")
    if (not isinstance(pull, dict) or pull.get("number") != candidate["pr_number"]
            or pull.get("html_url") != prefix
            or pull.get("base", {}).get("repo", {}).get("id")
            != candidate["repository_id"]):
        _mismatch("POSTING_BODY_MISMATCH",
                  "live pull request identity differs from the candidate")
    body = pull.get("body")
    if not isinstance(body, str):
        _mismatch("POSTING_BODY_MISMATCH", "live pull request body is not text")
    anchor = f"review-verdict:{verdict['round']}:{verdict['record_id']}:{verdict_sha256}"
    if anchor not in body:
        _mismatch("POSTING_ANCHOR_MISMATCH",
                  "live pull request body omits the exact verdict anchor")

    provider_response = _provider_response(
        comment_id, comment["html_url"], comment["body"], body
    )
    registration = _registration(release_sha256, candidate["repository_id"])
    observation = {
        "source": "host_observation",
        "observed_at": observed_at,
        "producer_id": COLLECTOR_ID,
        "run_id": collector_run_id,
        "provider_kind": PROVIDER_KIND,
        "critic_artifact_binding": artifact_binding,
        "review_verdict_record_id": verdict["record_id"],
        "review_verdict_sha256": verdict_sha256,
        "repository_id": candidate["repository_id"],
        "pr_number": candidate["pr_number"],
        "comment_id": comment_id,
        "comment_url": comment["html_url"],
        "comment_bytes": comment["body"],
        "comment_sha256": sha256(comment["body"].encode("utf-8")),
        "body_link": expected_body_link,
        "body_bytes": body,
        "body_sha256": sha256(body.encode("utf-8")),
        "provider_response_bytes": {
            "encoding": "base64",
            "data": base64.b64encode(provider_response).decode("ascii"),
        },
        "provider_response_sha256": sha256(provider_response),
        "collector_receipt_sha256": "0" * 64,
    }
    binding_sha256 = sha256(canonical(observation))
    receipt = posting_collector_receipt_sha256(
        observation, binding_sha256, _gate_entry(registration)
    )
    observation["collector_receipt_sha256"] = receipt
    registration["receipts"][collector_run_id] = receipt
    return {"observation": observation, "registration": registration}


def _retained_mismatch(expected, retained):
    if not isinstance(retained, dict):
        _mismatch("POSTING_OBSERVATION_MISMATCH", "retained observation is missing")
    groups = (
        ("POSTING_COLLECTOR_RUN_MISMATCH",
         ("source", "producer_id", "run_id", "provider_kind")),
        ("POSTING_ROUND_BINDING_MISMATCH",
         ("critic_artifact_binding", "review_verdict_record_id", "repository_id", "pr_number")),
        ("POSTING_VERDICT_DIGEST_MISMATCH", ("review_verdict_sha256",)),
        ("POSTING_COMMENT_MISMATCH",
         ("comment_id", "comment_url", "comment_bytes", "comment_sha256")),
        ("POSTING_ANCHOR_MISMATCH", ("body_link",)),
        ("POSTING_BODY_MISMATCH", ("body_bytes", "body_sha256")),
        ("POSTING_PROVIDER_RESPONSE_MISMATCH",
         ("provider_response_bytes", "provider_response_sha256")),
        ("POSTING_COLLECTOR_RUN_MISMATCH", ("collector_receipt_sha256",)),
    )
    for code, fields in groups:
        differing = [field for field in fields if retained.get(field) != expected.get(field)]
        if differing:
            _mismatch(code, "retained fields differ: " + ", ".join(differing))
    if set(retained) != set(expected) or retained.get("observed_at") != expected.get("observed_at"):
        _mismatch("POSTING_OBSERVATION_MISMATCH",
                  "retained observation shape or observation time differs")


def registry_from_review_host(review_host, bundle, *, release_sha256):
    """Re-observe every retained round and return the trusted gate registry."""
    candidate = bundle.get("candidate", {})
    runs = {item.get("run_id"): item for item in bundle.get("runs", [])
            if isinstance(item, dict)}
    receipts = bundle.get("review_round_receipts", [])
    merged = _registration(release_sha256, candidate.get("repository_id"))
    for verdict in sorted(bundle.get("review_verdicts", []),
                          key=lambda item: item.get("round", 0)):
        observation = verdict.get("posting_observation")
        if not isinstance(observation, dict):
            _mismatch("POSTING_OBSERVATION_MISMATCH", "retained observation is missing")
        run = runs.get(observation.get("run_id"))
        if (run is None or run.get("role") != "collector"
                or run.get("producer_id") != COLLECTOR_ID
                or observation.get("producer_id") != COLLECTOR_ID
                or run.get("created_at") != observation.get("observed_at")):
            _mismatch("POSTING_COLLECTOR_RUN_MISMATCH",
                      "observation has no runtime-owned collector run at its recorded time")
        matching = [item for item in receipts
                    if item.get("critic_artifact_binding")
                    == verdict.get("critic_artifact_binding")]
        if len(matching) != 1:
            _mismatch("POSTING_ROUND_BINDING_MISMATCH",
                      "round has no unique retained receipt")
        collected = collect_posting_observation(
            review_host,
            candidate=candidate,
            verdict=verdict,
            review_round_receipt=matching[0],
            collector_run_id=observation["run_id"],
            observed_at=observation.get("observed_at"),
            release_sha256=release_sha256,
        )
        _retained_mismatch(collected["observation"], observation)
        merged["receipts"].update(collected["registration"]["receipts"])
    if not merged["receipts"]:
        _mismatch("POSTING_OBSERVATION_MISMATCH", "no review verdict postings were observed")
    return {COLLECTOR_ID: _gate_entry(merged)}


def registry_from_host_config(root, host_config_path, bundle, release_sha256):
    """Create a registry through the enrolled, pinned GitHub review host."""
    host_config = load_config(host_config_path, root)
    candidate = bundle.get("candidate", {})
    if (host_config.get("repository_id") != candidate.get("repository_id")
            or host_config.get("repository") != candidate.get("repository")
            or host_config.get("pr") != candidate.get("pr_number")):
        _mismatch("POSTING_CANDIDATE_MISMATCH",
                  "review-host enrollment differs from the final-gate candidate")
    return registry_from_review_host(
        HostDriver(host_config, root), bundle, release_sha256=release_sha256
    )
