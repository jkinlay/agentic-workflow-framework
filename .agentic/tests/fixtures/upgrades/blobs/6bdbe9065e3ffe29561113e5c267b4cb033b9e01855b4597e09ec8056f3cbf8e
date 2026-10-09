"""Fresh GitHub observation for a reviewed reviewer-set removal.

The caller supplies only a GitHub issue-comment identifier.  Approval content,
actor identity, repository identity, and provider timestamps are read back from
GitHub and compared with the exact disposition before the protected review
ledger can weaken a dispatched reviewer set.
"""
from __future__ import annotations

import re
import time

from .. import ValidationError
from ..canonical import canonical, fresh, now_text, timestamp
from .github import HOST, _gh_get


APPROVAL_MARKER = "AWF-REVIEWER-REMOVAL-APPROVAL-1\n"
MAX_AGE_SECONDS = 300


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def approval_comment_body(request):
    """Return the one exact owner-authored GitHub approval body."""
    payload = {
        "status": "APPROVED",
        "record": request["record"],
        "record_sha256": request["record_sha256"],
    }
    return APPROVAL_MARKER + canonical(payload).decode("utf-8")


def github_reviewer_removal_observer(authority, artifact_id, *, get_json=None, now=None):
    """Build a fail-closed observer for one live GitHub issue comment."""
    _require(isinstance(authority, dict), "GitHub removal observation needs reviewed authority")
    identity = authority.get("provider_identity")
    _require(isinstance(identity, dict) and identity.get("host") == "https://github.com",
             "Reviewer removal reference adapter supports GitHub.com only")
    repository, repository_id = identity.get("repository"), identity.get("repository_id")
    _require(isinstance(repository, str) and re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
        "Reviewer removal repository identity is invalid")
    _require(type(repository_id) is int and repository_id > 0,
             "Reviewer removal needs an immutable repository ID")
    _require(type(artifact_id) is int and artifact_id > 0,
             "Reviewer removal artifact ID must be a positive GitHub comment ID")

    def default_get(endpoint, deadline):
        return _gh_get(endpoint, deadline)[0]

    read = get_json or default_get

    def observe(request):
        _require(isinstance(request, dict) and set(request) == {
            "record", "record_sha256", "expected_provider_identity"},
            "Reviewer removal observation request is malformed")
        _require(request["expected_provider_identity"] == identity,
                 "Reviewer removal observation authority changed")
        deadline = time.monotonic() + 60
        metadata = read("repos/" + repository, deadline)
        _require(isinstance(metadata, dict) and metadata.get("id") == repository_id and
                 isinstance(metadata.get("full_name"), str) and
                 metadata["full_name"].casefold() == repository.casefold(),
                 "Live GitHub repository identity differs from PROJECT_CONFIG")
        comment = read(f"repos/{repository}/issues/comments/{artifact_id}", deadline)
        expected_api_repository = f"https://api.{HOST}/repos/{repository}"
        _require(isinstance(comment, dict) and comment.get("id") == artifact_id and
                 isinstance(comment.get("node_id"), str) and comment["node_id"].strip() and
                 comment.get("url") ==
                 f"{expected_api_repository}/issues/comments/{artifact_id}" and
                 isinstance(comment.get("issue_url"), str) and
                 re.fullmatch(re.escape(expected_api_repository) + r"/issues/[1-9][0-9]*",
                              comment["issue_url"]),
                 "Live GitHub artifact is not an issue comment in the configured repository")
        actor = comment.get("user")
        record = request["record"]
        _require(isinstance(actor, dict) and actor.get("id") == record.get("owner_actor_id"),
                 "Live GitHub approval actor differs from the configured owner disposition")
        _require(comment.get("body") == approval_comment_body(request),
                 "Live GitHub approval body differs from the exact disposition")
        _require(comment.get("created_at") == comment.get("updated_at"),
                 "Edited GitHub approvals are not accepted; create a fresh immutable artifact")
        observed = now() if now else now_text()
        created = comment.get("created_at")
        fresh(created, observed, MAX_AGE_SECONDS)
        _require(timestamp(record["issued_at"]) <= timestamp(created) <= timestamp(record["expires_at"]),
                 "GitHub approval time is outside the disposition freshness window")
        return {
            "status": "APPROVED",
            "artifact_id": f"github-issue-comment:{repository_id}:{artifact_id}:{comment['node_id']}",
            "record_sha256": request["record_sha256"],
            "disposition_id": record["disposition_id"],
            "old_cycle_id": record["old_cycle_id"],
            "tuple_sha256": record["tuple_sha256"],
            "old_reviewer_set_sha256": record["old_reviewer_set_sha256"],
            "new_reviewer_set_sha256": record["new_reviewer_set_sha256"],
            "removed_reviewers": record["removed_reviewers"],
            "reason": record["reason"],
            "owner_actor_id": actor["id"],
            "provider_identity": request["expected_provider_identity"],
            "observed_at": observed,
        }

    return observe
