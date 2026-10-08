"""Observe a stream's completion path before dispatch; never grant authority.

The reviewed host supplies these observations from its own bounded read-only
provider adapters. Candidate files and self-asserted permission flags are not
trusted observations. No check performs a push, creates a PR, or changes rules.
"""
from __future__ import annotations

from string import Formatter
import re

from . import ValidationError
from .canonical import fingerprint, fresh, now_text
from .provider_identity import github_identity_preflight
from .providers.github import branch_name, repository_name


FIELDS = {"format", "source", "host", "observed_at", "ticket", "slug", "branch",
          "identity", "authenticated", "remote_reachable", "rules",
          "push_permitted", "draft_pr_permitted"}
CHECKS = ("AUTHENTICATION", "REMOTE_REACHABILITY", "BRANCH_ELIGIBILITY",
          "APPLICABLE_RULES", "PUSH_PERMISSION", "DRAFT_PR_PERMISSION")


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _eligible_branch(github, ticket, slug, branch):
    _require(isinstance(ticket, str) and re.fullmatch(r"[A-Z][A-Z0-9]*-[1-9][0-9]*", ticket),
             "A concrete assigned ticket is required")
    _require(isinstance(slug, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug),
             "A concrete publication slug is required")
    branch_name(branch)
    _require(branch != github.get("base_branch") and not branch.startswith("-"),
             "Publication must target an eligible feature branch")
    pattern = github.get("branch_pattern")
    _require(isinstance(pattern, str) and 0 < len(pattern) <= 255, "Missing accepted branch pattern")
    parts = list(Formatter().parse(pattern))
    _require(sorted(field for _, field, _, _ in parts if field is not None) == ["slug", "ticket"]
             and all(not spec and conversion is None for _, _, spec, conversion in parts),
             "The accepted branch pattern must map ticket and slug exactly once")
    _require(branch == pattern.format(ticket=ticket, slug=slug),
             "Branch differs from the accepted ticket pattern")


def publication_readiness(config, observation, *, ticket=None, now=None):
    """Return six precise checks bound to accepted identity and a feature ref.

    A supplied record is a host assertion, not authenticated by this helper.
    The production controller calls its reviewed observer before worker launch.
    Owner publication is never inferred as a fallback from a failed push check.
    """
    at = now or now_text()
    _require(isinstance(config, dict) and isinstance(config.get("github"), dict),
             "Publication readiness needs accepted GitHub configuration")
    github = config["github"]
    _require(isinstance(observation, dict) and set(observation) == FIELDS,
             "Publication readiness observation is missing or malformed")
    _require(observation["format"] == "awf-publication-readiness-1"
             and observation["source"] == "host_observation",
             "Publication readiness needs a reviewed host observation")
    _require(observation["host"] == github.get("host") == "https://github.com",
             "Publication host differs from accepted GitHub identity")
    fresh(observation["observed_at"], at, 300, skew_seconds=0)
    _require(ticket is None or ticket == observation["ticket"],
             "Publication observation belongs to another ticket")
    for field in ("authenticated", "remote_reachable", "push_permitted", "draft_pr_permitted"):
        _require(type(observation[field]) is bool, field + " must be an observed Boolean")
    rules = observation["rules"]
    _require(isinstance(rules, dict) and set(rules) == {"state", "evidence"}
             and rules["state"] in {"ALLOWED", "BLOCKED", "UNOBSERVED"}
             and isinstance(rules["evidence"], str) and 0 < len(rules["evidence"]) <= 2048,
             "Applicable feature-ref rules need a bounded observation")
    checks = []

    def add(code, passed, evidence):
        checks.append({"code": code, "state": "PASS" if passed else "BLOCKED",
                       "evidence": evidence, "observed_at": observation["observed_at"]})

    identity = None
    if observation["authenticated"]:
        try:
            identity = github_identity_preflight(config, observation["identity"])
            fresh(observation["identity"]["observed_at"], at, 300, skew_seconds=0)
            add("AUTHENTICATION", True, "Configured actor and numeric repository identity match")
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            add("AUTHENTICATION", False, str(exc))
    else:
        add("AUTHENTICATION", False, "Repository authentication is unavailable")
    add("REMOTE_REACHABILITY", observation["remote_reachable"],
        "Remote is reachable" if observation["remote_reachable"] else "Remote is unreachable")
    try:
        _eligible_branch(github, observation["ticket"], observation["slug"], observation["branch"])
        add("BRANCH_ELIGIBILITY", True, "Feature branch matches the accepted ticket pattern")
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        add("BRANCH_ELIGIBILITY", False, str(exc))
    add("APPLICABLE_RULES", rules["state"] == "ALLOWED", rules["evidence"])
    add("PUSH_PERMISSION", observation["push_permitted"],
        "Push permission observed" if observation["push_permitted"] else "Push permission unavailable")
    add("DRAFT_PR_PERMISSION", observation["draft_pr_permitted"],
        "Draft PR permission observed" if observation["draft_pr_permitted"] else "Draft PR creation permission unavailable")
    blockers = [row for row in checks if row["state"] != "PASS"]
    return {"format": "awf-publication-readiness-result-1", "status": "BLOCKED" if blockers else "READY",
            "repository": repository_name(github.get("repository")), "repository_id": github.get("repository_id"),
            "ticket": observation["ticket"], "branch": observation["branch"], "checks": checks,
            "blockers": blockers, "observed_at": observation["observed_at"],
            "observation_sha256": fingerprint("publication-readiness", observation),
            "identity_sha256": fingerprint("publication-identity", identity) if identity else None,
            "execution_authority": False}


def require_publication_readiness(config, observation, *, ticket, now):
    report = publication_readiness(config, observation, ticket=ticket, now=now)
    if report["blockers"]:
        raise ValidationError("PUBLICATION_UNAVAILABLE: " + "; ".join(
            row["code"] + ": " + row["evidence"] for row in report["blockers"]))
    return report


def publication_capabilities(report):
    """Derive publication capability rows from the checked completion path."""
    common = {"AUTHENTICATION", "REMOTE_REACHABILITY", "BRANCH_ELIGIBILITY", "APPLICABLE_RULES"}
    result = {}
    for name, required in (("branch_publication", common | {"PUSH_PERMISSION"}),
                           ("pr_creation", common | {"DRAFT_PR_PERMISSION"})):
        blocked = [row for row in report["checks"] if row["code"] in required and row["state"] != "PASS"]
        result[name] = {"state": "UNAVAILABLE" if blocked else "AVAILABLE",
                        "evidence": "; ".join(row["code"] + ": " + row["evidence"] for row in blocked)
                        if blocked else "Observed completion path for " + report["branch"],
                        "observed_at": report["observed_at"]}
    return result
