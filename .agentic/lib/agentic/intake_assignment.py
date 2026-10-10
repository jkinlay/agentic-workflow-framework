"""Controller-owned review-tier assignment at project start and ticket intake.

The functions in this module create durable offline state and delegate the
only Jira side effect to :mod:`agentic.jira_lifecycle`.  They grant no merge,
review, adapter, or execution authority.
"""
from __future__ import annotations

import copy
import fnmatch
import re
import uuid

from . import ValidationError
from .canonical import fingerprint, timestamp
from .jira_lifecycle import write_tier_assignment

DEFAULT_CAPS = {1: 1, 2: 2, 3: 3}
_TIER3_FLAGS = {
    "security", "credential", "credentials", "permissions", "schema_or_migration",
    "data_loss", "release", "merge_gate", "ci_gate", "governance",
    "install", "upgrade", "packaging", "controller",
}
_TIER3_TEXT = re.compile(
    r"\b(?:security|credentials?|governance|merge[_ -]?gates?|migrations?|"
    r"install(?:er|ation)?|upgrades?|packag(?:e|ing)|controller|release[_ ]+pr)\b",
    re.IGNORECASE,
)
_PR_START = "<!-- awf-review-assignment:start -->"
_PR_END = "<!-- awf-review-assignment:end -->"


def _matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _ticket_value(ticket, name, default=None):
    value = ticket.get(name, default)
    return value


def _review_config(config):
    execution = config.get("execution") if isinstance(config, dict) else None
    tiers = execution.get("risk_tiers") if isinstance(execution, dict) else None
    if not isinstance(tiers, dict):
        raise ValidationError("Review assignment requires reviewed execution.risk_tiers configuration")
    return tiers


def _configured_cap(config, tier):
    review = _review_config(config).get(f"tier{tier}_review", {})
    cap = review.get("max_rounds", DEFAULT_CAPS[tier]) if isinstance(review, dict) else DEFAULT_CAPS[tier]
    if type(cap) is not int or cap < 1 or cap > DEFAULT_CAPS[tier]:
        raise ValidationError(
            f"execution.risk_tiers.tier{tier}_review.max_rounds must be between 1 and {DEFAULT_CAPS[tier]}")
    return cap


def classify_ticket(config, ticket):
    """Apply the shipped 09:50 rule and the protected-path Tier 2 floor."""
    tiers = _review_config(config)
    if not isinstance(ticket, dict) or not isinstance(ticket.get("key"), str) or not ticket["key"].strip():
        raise ValidationError("Ticket intake requires a non-empty ticket key")
    paths = ticket.get("write_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path for path in paths):
        raise ValidationError("Ticket intake requires one or more declared write_paths")
    paths = sorted(set(paths))
    flags = ticket.get("risk_flags", [])
    if not isinstance(flags, list) or not all(isinstance(flag, str) and flag for flag in flags):
        raise ValidationError("Ticket risk_flags must be a list of non-empty strings")
    flags = sorted(set(flags))
    text = " ".join([*(str(ticket.get(name, "")) for name in ("summary", "description")), *paths])
    kind = ticket.get("change_kind")
    protected_patterns = config.get("scope", {}).get("protected_paths", [])
    protected = [path for path in paths if _matches(path, protected_patterns)]
    reasons = []
    tier3 = sorted(set(flags) & _TIER3_FLAGS)
    if tier3:
        reasons.append("Tier 3 risk flags: " + ", ".join(tier3))
    if kind == "release_pr":
        reasons.append("release PRs require Tier 3 and owner review")
    if _TIER3_TEXT.search(text):
        reasons.append("ticket text identifies security, governance, migration, packaging, controller, or release work")
    if tier3 or kind == "release_pr" or _TIER3_TEXT.search(text):
        tier = 3
    else:
        eligible = tiers.get("tier1_eligible_paths", [])
        excluded = tiers.get("tier1_excluded_paths", [])
        configured_tier1 = all(_matches(path, eligible) and not _matches(path, excluded) for path in paths)
        bounded_small = (kind in {"single_file_fix", "small_config", "labels_only"} and len(paths) == 1)
        if (configured_tier1 or bounded_small) and not protected and not flags:
            tier = 1
            reasons.append("docs/tests-only or bounded small single-file change without excluded effects")
        else:
            tier = 2
            reasons.append("moderate code in one area uses the Tier 2 default")
    protected_floor = 2 if protected else 1
    if tier < protected_floor:
        tier = protected_floor
        reasons.append("protected paths impose a Tier 2 floor: " + ", ".join(protected))
    return {
        "tier": tier,
        "round_cap": _configured_cap(config, tier),
        "owner_review_required": tier == 3,
        "protected_path_floor": protected_floor,
        "protected_paths": protected,
        "write_paths": paths,
        "risk_flags": flags,
        "reasons": list(dict.fromkeys(reasons)),
        "rule": "T1 cap 1; T2 cap 2; T3 cap 3 plus trusted-owner review; stop at the first critic PASS",
    }


def _assignment_id(ticket_key, now, source, classification, previous_assignment_id=None):
    seed = {"ticket_key": ticket_key, "created_at": now, "source": source,
            "classification": classification, "previous_assignment_id": previous_assignment_id}
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "awf-intake-assignment:" + fingerprint("intake_assignment", seed)))


def _record(ticket_key, classification, *, source, assigned_by, now,
            previous_assignment_id=None, change_audit=None):
    timestamp(now)
    record = {
        "schema_version": 1,
        "assignment_id": _assignment_id(ticket_key, now, source, classification, previous_assignment_id),
        "assignment_sha256": "",
        "ticket_key": ticket_key,
        "created_at": now,
        "source": source,
        "assigned_by": assigned_by,
        "tier": classification["tier"],
        "round_cap": classification["round_cap"],
        "owner_review_required": classification["owner_review_required"],
        "protected_path_floor": classification["protected_path_floor"],
        "protected_paths": list(classification["protected_paths"]),
        "write_paths": list(classification["write_paths"]),
        "risk_flags": list(classification["risk_flags"]),
        "reasons": list(classification["reasons"]),
        "rule": classification["rule"],
        "previous_assignment_id": previous_assignment_id,
        "change_audit": copy.deepcopy(change_audit),
        "jira": None,
    }
    return record


def _seal(record):
    sealed = copy.deepcopy(record)
    sealed["assignment_sha256"] = fingerprint(
        "intake_assignment_record", {key: value for key, value in sealed.items() if key != "assignment_sha256"})
    return sealed


def verify_assignment_record(record):
    """Reject a malformed or mutated assignment before policy consumes it."""
    if not isinstance(record, dict):
        raise ValidationError("Review assignment record must be an object")
    digest = record.get("assignment_sha256")
    expected = fingerprint(
        "intake_assignment_record", {key: value for key, value in record.items() if key != "assignment_sha256"})
    if not isinstance(digest, str) or digest != expected:
        raise ValidationError("Review assignment record digest is missing or stale")
    tier, cap = record.get("tier"), record.get("round_cap")
    if tier not in DEFAULT_CAPS or type(cap) is not int or cap < 1 or cap > DEFAULT_CAPS[tier]:
        raise ValidationError("Review assignment record has an invalid tier/cap")
    owner_review = record.get("owner_review_required")
    if type(owner_review) is not bool or owner_review != (tier == 3):
        raise ValidationError("Review assignment record owner-review requirement disagrees with its tier")
    if not isinstance(record.get("assignment_id"), str) or not record["assignment_id"]:
        raise ValidationError("Review assignment record needs assignment_id")
    return record


def _jira_receipt(config, jira_client, record, controller_actor_id):
    jira = config.get("jira", {})
    configured_actor = jira.get("controller_actor_id")
    if configured_actor is not None and configured_actor != controller_actor_id:
        raise ValidationError("Tier assignment Jira writes require the configured controller identity")
    if jira.get("enabled", True) is False:
        return {"read_back_status": "DISABLED", "label": None, "comment_id": None,
                "reason": "Jira is disabled; local provisional assignment only"}
    if jira_client is None:
        raise ValidationError("Enabled Jira tier assignment requires the controller Jira adapter")
    return write_tier_assignment(jira_client, record, controller_actor_id=controller_actor_id,
                                 acting_actor_id=controller_actor_id)


def _store(state, record):
    state.setdefault("tier_assignments", {})[record["ticket_key"]] = copy.deepcopy(record)
    state.setdefault("tier_assignment_history", {}).setdefault(record["ticket_key"], []).append(copy.deepcopy(record))


def assign_intake(config, ticket, state, jira_client, *, now, controller_actor_id, source="intake"):
    """Assign once, verify the Jira receipt, then make the record current in state."""
    if not isinstance(state, dict):
        raise ValidationError("AWF state must be an object")
    if not isinstance(controller_actor_id, (str, int)) or controller_actor_id == "":
        raise ValidationError("Review assignment requires the controller actor identity")
    if ticket.get("key") in state.get("tier_assignments", {}):
        raise ValidationError("Existing review assignments change only through the trusted-owner audit path")
    classification = classify_ticket(config, ticket)
    record = _record(ticket["key"], classification, source=source,
                     assigned_by=controller_actor_id, now=now)
    record["jira"] = _jira_receipt(config, jira_client, record, controller_actor_id)
    sealed = _seal(record)
    _store(state, sealed)
    return copy.deepcopy(sealed)


def render_pr_assignment(record):
    lines = [
        _PR_START,
        "## AWF review assignment",
        "",
        f"- Ticket: {record['ticket_key']}",
        f"- Tier: {record['tier']}",
        f"- Round cap: {record['round_cap']}",
        f"- Trusted-owner review required: {'yes' if record['owner_review_required'] else 'no'}",
        f"- Assignment record: `{record['assignment_id']}`",
        f"- Assignment SHA-256: `{record['assignment_sha256']}`",
    ]
    audit = record.get("change_audit")
    if audit:
        lines.extend([f"- Changed by trusted owner: {audit['who']}",
                      f"- Changed at: {audit['when']}", f"- Change reason: {audit['why']}"])
        lines.extend([f"- Old assignment: T{audit['old']['tier']} cap {audit['old']['round_cap']}",
                      f"- New assignment: T{audit['new']['tier']} cap {audit['new']['round_cap']}"])
    lines.extend([_PR_END, ""])
    return "\n".join(lines)


def _replace_pr_section(body, section):
    if not isinstance(body, str):
        raise ValidationError("PR body must be text")
    start = body.find(_PR_START)
    end = body.find(_PR_END)
    if (start < 0) != (end < 0) or (start >= 0 and end < start):
        raise ValidationError("PR body has an incomplete AWF review assignment section")
    if start >= 0:
        end += len(_PR_END)
        suffix = body[end:]
        if suffix.startswith("\n"):
            suffix = suffix[1:]
        return body[:start].rstrip() + "\n\n" + section + suffix
    return body.rstrip() + "\n\n" + section


def inherit_pr_assignment(state, ticket_key, pr_number, body):
    assignment = state.get("tier_assignments", {}).get(ticket_key)
    if not isinstance(assignment, dict):
        raise ValidationError(f"No current review assignment for {ticket_key}")
    rendered = _replace_pr_section(body, render_pr_assignment(assignment))
    binding = {"ticket_key": ticket_key, "pr_number": pr_number,
               "assignment_id": assignment["assignment_id"],
               "assignment_sha256": assignment["assignment_sha256"], "body": rendered}
    state.setdefault("pr_assignments", {})[str(pr_number)] = copy.deepcopy(binding)
    return rendered


def _update_bound_prs(state, ticket_key, record):
    for key, binding in list(state.get("pr_assignments", {}).items()):
        if binding.get("ticket_key") != ticket_key:
            continue
        body = _replace_pr_section(binding["body"], render_pr_assignment(record))
        state["pr_assignments"][key] = {**binding, "assignment_id": record["assignment_id"],
            "assignment_sha256": record["assignment_sha256"], "body": body}


def change_assignment(config, ticket_key, state, jira_client, *, requested_tier,
                      requested_cap, requested_by, reason, now, controller_actor_id):
    """Apply a trusted-owner change and retain its old/new audit everywhere."""
    current = state.get("tier_assignments", {}).get(ticket_key)
    if not isinstance(current, dict):
        raise ValidationError(f"No current review assignment for {ticket_key}")
    owners = config.get("merge_gate", {}).get("trusted_owner_ids", [])
    if requested_by not in owners:
        raise ValidationError("Review assignment changes require an authenticated trusted owner")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("Review assignment changes require a non-empty reason")
    if requested_tier not in DEFAULT_CAPS or type(requested_cap) is not int:
        raise ValidationError("Review assignment changes require tier 1, 2 or 3 and an integer cap")
    floor = 2 if current.get("protected_paths") else 1
    if requested_tier < floor:
        rejection = {"ticket_key": ticket_key, "who": requested_by, "when": now,
                     "requested": {"tier": requested_tier, "round_cap": requested_cap},
                     "reason_code": "PROTECTED_PATH_FLOOR",
                     "reason": "protected-path floor is Tier 2 for: " + ", ".join(current.get("protected_paths", []))}
        state.setdefault("assignment_rejections", []).append(rejection)
        raise ValidationError(rejection["reason"])
    allowed_cap = _configured_cap(config, requested_tier)
    if requested_cap < 1 or requested_cap > allowed_cap:
        raise ValidationError(f"Requested cap exceeds the reviewed Tier {requested_tier} cap of {allowed_cap}")
    audit = {"who": requested_by, "when": now, "why": reason.strip(),
             "old": {"tier": current["tier"], "round_cap": current["round_cap"]},
             "new": {"tier": requested_tier, "round_cap": requested_cap}}
    classification = {
        "tier": requested_tier, "round_cap": requested_cap,
        "owner_review_required": requested_tier == 3,
        "protected_path_floor": floor,
        "protected_paths": list(current.get("protected_paths", [])),
        "write_paths": list(current["write_paths"]),
        "risk_flags": list(current.get("risk_flags", [])),
        "reasons": list(current.get("reasons", [])) + ["trusted-owner change: " + reason.strip()],
        "rule": current["rule"],
    }
    record = _record(ticket_key, classification, source="owner_change",
                     assigned_by=controller_actor_id, now=now,
                     previous_assignment_id=current["assignment_id"], change_audit=audit)
    record["jira"] = _jira_receipt(config, jira_client, record, controller_actor_id)
    sealed = _seal(record)
    _store(state, sealed)
    _update_bound_prs(state, ticket_key, sealed)
    return copy.deepcopy(sealed)


def assign_inventory(config, inventory, state, jira_client, *, now, controller_actor_id,
                     source="project_start"):
    if not isinstance(inventory, list):
        raise ValidationError("Project-start ticket inventory must be a list")
    keys = [item.get("key") for item in inventory if isinstance(item, dict)]
    if len(keys) != len(inventory) or len(set(keys)) != len(keys):
        raise ValidationError("Project-start ticket inventory needs unique ticket keys")
    return {item["key"]: assign_intake(config, item, state, jira_client, now=now,
                                        controller_actor_id=controller_actor_id, source=source)
            for item in inventory}
