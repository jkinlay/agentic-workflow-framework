"""Risk-tiered review rounds and their durable, offline policy records.

This module deliberately stops at policy and evidence.  The per-PR provider
loop is owned by AWF-29; it consumes these records after observing the remote
PR.  No function here posts comments, authorizes a merge, or changes Jira.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from . import ValidationError

TIER_1 = 1
TIER_2 = 2
TIER_3 = 3
ROUND_CAPS = {TIER_1: 1, TIER_2: 3, TIER_3: 3}
P1 = {"P1", "BLOCKER", "MAJOR"}
P2 = {"P2", "MINOR"}

_TIER3_WORDS = ("governance", "release", "merge", "qualification", "gate")
_TIER3_FLAGS = {"security", "permissions", "release", "merge_gate", "ci_gate",
                "schema_or_migration", "data_loss", "concurrency", "production",
                "public_api", "high_complexity", "high_uncertainty"}


def _matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _evidence_for(path, flags, explicit):
    lower = path.lower()
    evidence = []
    if any(word in lower for word in _TIER3_WORDS):
        evidence.append("path name indicates release, merge or gate logic")
    for flag in sorted(set(flags) & _TIER3_FLAGS):
        evidence.append("risk flag: " + flag)
    evidence.extend(str(item) for item in explicit if str(item).strip())
    return list(dict.fromkeys(evidence))


def classify(config, paths, *, risk_flags=(), evidence=(), complexity=None):
    """Return the highest matching tier and auditable reasons.

    Tier 1 is opt-in and remains conservative.  Tier 2 is the safe default;
    Tier 3 wins whenever governance, release, merge/qualification, CI-gate,
    or another high-risk signal is observed.
    """
    paths = sorted(set(paths))
    flags = set(risk_flags or ())
    explicit = list(evidence or ())
    if complexity in {"high", "uncertain", "high_complexity", "high_uncertainty"}:
        flags.add("high_complexity" if complexity == "high" else "high_uncertainty")
    matched = {1: [], 2: [], 3: []}
    tiers = (config or {}).get("execution", {}).get("risk_tiers", {})
    eligible = tiers.get("tier1_eligible_paths", [])
    excluded = tiers.get("tier1_excluded_paths", [])
    tier1_ok = bool(paths) and all(_matches(path, eligible) and not _matches(path, excluded) for path in paths)
    if tier1_ok and not flags:
        matched[1] = ["all changed paths are explicitly Tier 1 eligible"]
    matched[2] = ["code or non-Tier-1 change uses the Tier 2 default"]
    tier3_evidence = []
    for path in paths:
        tier3_evidence.extend(_evidence_for(path, flags, explicit))
    if tier3_evidence:
        matched[3] = list(dict.fromkeys(tier3_evidence))
    selected = max(tier for tier, reasons in matched.items() if reasons)
    return {"tier": selected, "matched_tiers": [tier for tier, reasons in matched.items() if reasons],
            "evidence": {str(tier): reasons for tier, reasons in matched.items() if reasons},
            "rule": f"highest matching tier wins; Tier {selected} permits up to {ROUND_CAPS[selected]} round(s)",
            "risk_flags": sorted(flags)}


def round_cap(tier, config=None):
    if tier not in ROUND_CAPS:
        raise ValidationError(f"unknown review tier: {tier}")
    configured = ((config or {}).get("execution", {}).get("risk_tiers", {})
                  .get(f"tier{tier}_review", {}).get("max_rounds"))
    cap = ROUND_CAPS[tier] if configured is None else configured
    if type(cap) is not int or cap < 1:
        raise ValidationError(f"Tier {tier} review max_rounds must be a positive integer")
    return cap


def validate_round(tier, round_number, *, owner_cap_disposition=False, config=None):
    cap = round_cap(tier, config)
    if type(round_number) is not int or round_number < 1:
        raise ValidationError("review round must be a positive integer")
    if round_number > cap and not owner_cap_disposition:
        raise ValidationError(f"Tier {tier} review cap is {cap}; an additional round needs an explicit owner cap disposition")
    return True


def verdict_record(*, pr_comment_url, pr_body_link, verdict, tier, round_number, head_sha,
                   reviewer_id, evidence=None, owner_review=False):
    """Build the policy-level verdict receipt; posting remains a successor concern."""
    if verdict not in {"PASS", "P1", "P2", "REQUEST_CHANGES"}:
        raise ValidationError("unknown review verdict")
    if not pr_comment_url or not pr_body_link:
        raise ValidationError("every verdict needs PR comment URL and PR-body link evidence")
    validate_round(tier, round_number)
    return {"verdict": verdict, "tier": tier, "round": round_number, "head_sha": head_sha,
            "reviewer_id": reviewer_id, "pr_comment_url": pr_comment_url,
            "pr_body_link": pr_body_link, "evidence": list(evidence or []),
            "owner_review": bool(owner_review)}


def review_decision(tier, rounds, *, latest_pass=False, open_findings=(), owner_review=False,
                    owner_cap_disposition=False, ticketed_p2_ids=()):
    """Derive qualification/escalation at the tier-specific round boundary."""
    cap = round_cap(tier)
    if rounds > cap and not owner_cap_disposition:
        raise ValidationError(f"Tier {tier} refuses review round {rounds}; owner cap disposition required")
    findings = list(open_findings or ())
    p1 = [f for f in findings if f.get("severity") in P1 or f.get("priority") in P1]
    p2 = [f for f in findings if f.get("severity") in P2 or f.get("priority") in P2]
    if any(f.get("boundary") or f.get("boundary_code") for f in findings):
        return {"status": "BLOCKED", "reason": "boundary finding blocks in every tier", "cap": cap}
    if tier == TIER_3 and not owner_review:
        return {"status": "OWNER_REVIEW_REQUIRED", "cap": cap}
    if tier == TIER_2 and rounds >= cap and p1:
        return {"status": "ROUTE_TO_OWNER", "reason": "P1 remains open at Tier 2 cap", "cap": cap}
    if tier == TIER_2 and rounds >= cap and p2:
        ids = {f.get("id") for f in p2}
        if not ids.issubset(set(ticketed_p2_ids)):
            return {"status": "TICKET_P2", "ticket_required": sorted(ids), "cap": cap}
        p2 = []
    if latest_pass and not p1 and not p2 and (tier != TIER_3 or owner_review):
        return {"status": "QUALIFIED", "cap": cap}
    return {"status": "CONTINUE", "cap": cap}


def diff_effect(previous_diff_sha, current_diff_sha, *, base_only=False):
    """Classify a base update without confusing it with an effective diff change."""
    if base_only and previous_diff_sha == current_diff_sha:
        return {"changed": False, "invalidate": False, "reason": "base-only update left effective PR diff unchanged"}
    return {"changed": previous_diff_sha != current_diff_sha, "invalidate": True,
            "reason": "effective diff changed; earlier approvals are stale"}


def specialists(config, tier, domains):
    """Specialists are additive and never replace the independent critic."""
    domains = sorted(set(domains or ()))
    if tier == TIER_1:
        allowed = set(config.get("execution", {}).get("risk_tiers", {}).get("tier1_review", {}).get("specialist_when_touching", []))
        return [domain for domain in domains if domain in allowed]
    return domains


def requires_owner_review(tier):
    return tier == TIER_3
