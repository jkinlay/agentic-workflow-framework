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
from .canonical import fingerprint, sha256, timestamp
from .jira_lifecycle import TierAssignmentOutcomeError, write_tier_assignment

DEFAULT_CAPS = {1: 1, 2: 2, 3: 3}
_TIER3_FLAGS = {
    "security", "credential", "credentials", "permissions", "schema_or_migration",
    "data_loss", "release", "merge_gate", "ci_gate", "governance",
    "install", "upgrade", "packaging", "controller",
}
_TIER3_TEXT = re.compile(
    r"\b(?:security|credentials?|governance|(?:ci|merge)[_ -]?gates?|migrations?|"
    r"install(?:er|ation)?|upgrades?|packag(?:e|ing)|controller|release[_ ]+pr)\b",
    re.IGNORECASE,
)
_TIER3_PATHS = (
    ".github/workflows/**",
    ".agentic/lib/agentic/gates.py",
    ".agentic/lib/agentic/*controller*.py",
    ".agentic/lib/agentic/authorization.py",
    ".agentic/lib/agentic/lifecycle.py",
    ".agentic/lib/agentic/review_policy.py",
    ".agentic/lib/agentic/review_tiers.py",
    ".agentic/lib/agentic/publication.py",
    ".agentic/lib/agentic/installer.py",
    ".agentic/lib/agentic/upgrade.py",
    "scripts/bootstrap_project.py",
    "scripts/build_release.py",
    "scripts/publish_release.py",
)
_PR_START = "<!-- awf-review-assignment:start -->"
_PR_END = "<!-- awf-review-assignment:end -->"


def _matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _canonical_repo_path(value):
    """Return one portable repository-relative spelling or reject ambiguity."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValidationError("Ticket write_paths must contain non-empty repository paths")
    path = value.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if (not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path)
            or any(part in {"", ".", ".."} for part in path.split("/"))):
        raise ValidationError(f"Ticket write path is not canonical repository-relative text: {value!r}")
    return path


def _protected_paths(config, paths):
    patterns = config.get("scope", {}).get("protected_paths", [])
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
        raise ValidationError("scope.protected_paths must be a list of non-empty patterns")
    return [path for path in paths if _matches(path, patterns)]


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
    paths = sorted({_canonical_repo_path(path) for path in paths})
    flags = ticket.get("risk_flags", [])
    if not isinstance(flags, list) or not all(isinstance(flag, str) and flag for flag in flags):
        raise ValidationError("Ticket risk_flags must be a list of non-empty strings")
    flags = sorted(set(flags))
    text = " ".join([*(str(ticket.get(name, "")) for name in ("summary", "description")), *paths])
    kind = ticket.get("change_kind")
    protected = _protected_paths(config, paths)
    tier3_paths = [path for path in paths if _matches(path, _TIER3_PATHS)]
    reasons = []
    tier3 = sorted(set(flags) & _TIER3_FLAGS)
    if tier3:
        reasons.append("Tier 3 risk flags: " + ", ".join(tier3))
    if kind == "release_pr":
        reasons.append("release PRs require Tier 3 and owner review")
    if _TIER3_TEXT.search(text):
        reasons.append(
            "ticket text identifies security, governance, CI/merge-gate, migration, "
            "packaging, controller, or release work")
    if tier3_paths:
        reasons.append("governance, controller, release, or CI-gate paths require Tier 3: "
                       + ", ".join(tier3_paths))
    if tier3 or tier3_paths or kind == "release_pr" or _TIER3_TEXT.search(text):
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


def _jira_receipt(config, jira_client, record, controller_actor_id, state):
    jira = config.get("jira", {})
    configured_actor = jira.get("controller_actor_id")
    if configured_actor is not None and configured_actor != controller_actor_id:
        raise ValidationError("Tier assignment Jira writes require the configured controller identity")
    if jira.get("enabled", True) is False:
        return {"read_back_status": "DISABLED", "label": None, "comment_id": None,
                "reason": "Jira is disabled; local provisional assignment only"}
    if jira_client is None:
        raise ValidationError("Enabled Jira tier assignment requires the controller Jira adapter")
    try:
        return write_tier_assignment(jira_client, record, controller_actor_id=controller_actor_id,
                                     acting_actor_id=controller_actor_id)
    except TierAssignmentOutcomeError as exc:
        state.setdefault("jira_assignment_write_stops", {})[record["ticket_key"]] = copy.deepcopy(exc.outcome)
        raise


def _store(state, record):
    state.setdefault("tier_assignments", {})[record["ticket_key"]] = copy.deepcopy(record)
    state.setdefault("tier_assignment_history", {}).setdefault(record["ticket_key"], []).append(copy.deepcopy(record))


def _ensure_jira_writes_allowed(state, ticket_key):
    stop = state.get("jira_assignment_write_stops", {}).get(ticket_key)
    if isinstance(stop, dict) and stop.get("writes_stopped") is True:
        raise ValidationError(
            f"Jira tier-assignment writes are stopped for {ticket_key} after an unknown or mismatched outcome; never reissue")


def _bind_runtime_assignment(config, record):
    """Add one ticket record to the in-memory map used by live policy calls.

    The ``_awf_runtime`` member is process-local context and must never be written
    back as reviewed project configuration.
    """
    runtime = config.setdefault("_awf_runtime", {})
    if not isinstance(runtime, dict):
        raise ValidationError("AWF runtime configuration context must be an object")
    assignments = runtime.setdefault("intake_assignments", {})
    if not isinstance(assignments, dict):
        raise ValidationError("AWF runtime intake assignments must be an object keyed by ticket")
    verify_assignment_record(record)
    assignments[record["ticket_key"]] = copy.deepcopy(record)


def load_runtime_assignments(config, state, *, ticket_key=None):
    """Reload durable ticket assignments and optionally select one gate context.

    Controllers call this after loading their external state.  Selecting a
    ticket lets existing AWF-16 gate calls consume the persisted assignment
    without accepting a caller-supplied record or reverting to legacy caps.
    """
    if not isinstance(state, dict):
        raise ValidationError("AWF state must be an object")
    assignments = state.get("tier_assignments", {})
    if not isinstance(assignments, dict):
        raise ValidationError("AWF state tier_assignments must be an object keyed by ticket")
    loaded = {}
    for key, record in assignments.items():
        if not isinstance(key, str) or not key or not isinstance(record, dict):
            raise ValidationError("AWF state tier_assignments must contain ticket-keyed records")
        verify_assignment_record(record)
        if record.get("ticket_key") != key:
            raise ValidationError("AWF state tier assignment key disagrees with its record")
        loaded[key] = copy.deepcopy(record)
    runtime = config.setdefault("_awf_runtime", {})
    if not isinstance(runtime, dict):
        raise ValidationError("AWF runtime configuration context must be an object")
    runtime["intake_assignments"] = loaded
    runtime.pop("active_ticket_key", None)
    if ticket_key is not None:
        if not isinstance(ticket_key, str) or ticket_key not in loaded:
            raise ValidationError(f"No persisted review assignment for {ticket_key}")
        runtime["active_ticket_key"] = ticket_key
    return copy.deepcopy(loaded)


def assign_intake(config, ticket, state, jira_client, *, now, controller_actor_id, source="intake"):
    """Assign once, verify the Jira receipt, then make the record current in state."""
    if not isinstance(state, dict):
        raise ValidationError("AWF state must be an object")
    if not isinstance(controller_actor_id, (str, int)) or controller_actor_id == "":
        raise ValidationError("Review assignment requires the controller actor identity")
    _ensure_jira_writes_allowed(state, ticket.get("key"))
    if ticket.get("key") in state.get("tier_assignments", {}):
        raise ValidationError("Existing review assignments change only through the trusted-owner audit path")
    classification = classify_ticket(config, ticket)
    record = _record(ticket["key"], classification, source=source,
                     assigned_by=controller_actor_id, now=now)
    record["jira"] = _jira_receipt(config, jira_client, record, controller_actor_id, state)
    sealed = _seal(record)
    _store(state, sealed)
    _bind_runtime_assignment(config, sealed)
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


def _authenticate_owner_source(config, ticket_key, current, *, requested_tier, requested_cap,
                               reason, now, owner_source, verify_owner_source):
    if not isinstance(owner_source, dict) or not callable(verify_owner_source):
        raise ValidationError("Review assignment changes require a verified immutable owner-source record")
    if (owner_source.get("channel") != "github_pr_comment"
            or type(owner_source.get("comment_id")) is not int or owner_source["comment_id"] < 1
            or not isinstance(owner_source.get("actor_login"), str)
            or not owner_source["actor_login"]):
        raise ValidationError("Owner-source record lacks an immutable provider comment identity")
    binding = {"ticket_key": ticket_key, "current_assignment_id": current["assignment_id"]}
    if owner_source.get("binding") != binding:
        raise ValidationError("Owner-source binding differs from the current ticket assignment")
    actor = owner_source.get("actor_id")
    owners = config.get("merge_gate", {}).get("trusted_owner_ids", [])
    if actor not in owners:
        raise ValidationError("Review assignment changes require an authenticated trusted owner")
    created = owner_source.get("created_at")
    if owner_source.get("updated_at") != created:
        raise ValidationError("Edited owner-source records are invalid; obtain a new immutable observation")
    timestamp(created)
    expected_body = (
        f"AWF REVIEW ASSIGNMENT ticket={ticket_key} current={current['assignment_id']} "
        f"tier={requested_tier} cap={requested_cap} reason_sha256={sha256(reason.strip().encode('utf-8'))}")
    raw_body = owner_source.get("raw_body")
    raw_digest = owner_source.get("raw_body_sha256")
    if raw_body != expected_body or raw_digest != sha256(expected_body.encode("utf-8")):
        raise ValidationError("Owner-source raw command does not bind the requested assignment change")
    expected = {"binding": binding, "actor_id": actor, "raw_body_sha256": raw_digest,
                "requested": {"tier": requested_tier, "round_cap": requested_cap}, "now": now}
    try:
        verified = verify_owner_source(copy.deepcopy(owner_source), copy.deepcopy(expected))
    except Exception as exc:
        raise ValidationError("Owner-source authentication failed") from exc
    if (not isinstance(verified, dict) or verified.get("status") != "VERIFIED"
            or verified.get("actor_id") != actor or verified.get("binding") != binding
            or verified.get("raw_body_sha256") != raw_digest
            or not isinstance(verified.get("source_id"), str) or not verified["source_id"]
            or not isinstance(verified.get("observed_at"), str)):
        raise ValidationError("Owner-source verifier did not authenticate the exact assignment request")
    timestamp(verified["observed_at"])
    return actor, verified


def change_assignment(config, ticket_key, state, jira_client, *, requested_tier,
                      requested_cap, owner_source, verify_owner_source, reason, now,
                      controller_actor_id):
    """Apply a trusted-owner change and retain its old/new audit everywhere."""
    current = state.get("tier_assignments", {}).get(ticket_key)
    if not isinstance(current, dict):
        raise ValidationError(f"No current review assignment for {ticket_key}")
    _ensure_jira_writes_allowed(state, ticket_key)
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("Review assignment changes require a non-empty reason")
    if requested_tier not in DEFAULT_CAPS or type(requested_cap) is not int:
        raise ValidationError("Review assignment changes require tier 1, 2 or 3 and an integer cap")
    requested_by, verified_owner = _authenticate_owner_source(
        config, ticket_key, current, requested_tier=requested_tier,
        requested_cap=requested_cap, reason=reason, now=now,
        owner_source=owner_source, verify_owner_source=verify_owner_source)
    protected = _protected_paths(config, [_canonical_repo_path(path) for path in current["write_paths"]])
    floor = 2 if protected else 1
    if requested_tier < floor:
        rejection = {"ticket_key": ticket_key, "who": requested_by, "when": now,
                     "requested": {"tier": requested_tier, "round_cap": requested_cap},
                     "reason_code": "PROTECTED_PATH_FLOOR",
                     "owner_source_id": verified_owner["source_id"],
                     "reason": "protected-path floor is Tier 2 for: " + ", ".join(protected)}
        state.setdefault("assignment_rejections", []).append(rejection)
        raise ValidationError(rejection["reason"])
    allowed_cap = _configured_cap(config, requested_tier)
    if requested_cap < 1 or requested_cap > allowed_cap:
        raise ValidationError(f"Requested cap exceeds the reviewed Tier {requested_tier} cap of {allowed_cap}")
    audit = {"who": requested_by, "when": now, "why": reason.strip(),
             "owner_source_id": verified_owner["source_id"],
             "owner_source_sha256": verified_owner["raw_body_sha256"],
             "old": {"tier": current["tier"], "round_cap": current["round_cap"]},
             "new": {"tier": requested_tier, "round_cap": requested_cap}}
    classification = {
        "tier": requested_tier, "round_cap": requested_cap,
        "owner_review_required": requested_tier == 3,
        "protected_path_floor": floor,
        "protected_paths": list(protected),
        "write_paths": list(current["write_paths"]),
        "risk_flags": list(current.get("risk_flags", [])),
        "reasons": list(current.get("reasons", [])) + ["trusted-owner change: " + reason.strip()],
        "rule": current["rule"],
    }
    record = _record(ticket_key, classification, source="owner_change",
                     assigned_by=controller_actor_id, now=now,
                     previous_assignment_id=current["assignment_id"], change_audit=audit)
    record["jira"] = _jira_receipt(config, jira_client, record, controller_actor_id, state)
    sealed = _seal(record)
    _store(state, sealed)
    _bind_runtime_assignment(config, sealed)
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
