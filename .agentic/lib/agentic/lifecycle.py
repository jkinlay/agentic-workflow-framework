"""Exhaustive reference transition rules; no external side effects."""
from __future__ import annotations
import copy
import hashlib
import json
from . import ValidationError, VERSION
from .review_tiers import diff_effect, review_decision, validate_round

STATES = ["BACKLOG", "READY", "DISPATCHED", "IN_PROGRESS", "PR_DRAFT", "READY_FOR_CRITIC",
          "CHANGES_REQUESTED", "AMENDING", "SPECIALIST_REVIEW", "FINAL_REVIEW", "READY_FOR_OWNER_AUTHORIZATION",
          "OWNER_AUTHORIZED", "MERGING", "MERGE_UNKNOWN", "MERGED", "MERGED_PENDING_JIRA", "DONE",
          "BLOCKED", "FAILED", "CANCELLED", "SUPERSEDED", "REOPENED",
          "REVIEW_CAP_REACHED", "MERGED_PENDING_OWNER_CLOSURE"]
TERMINAL = {"DONE", "CANCELLED", "SUPERSEDED"}
PRE_REVIEW = {"BACKLOG", "READY", "DISPATCHED", "IN_PROGRESS", "PR_DRAFT", "AMENDING"}
REVIEW = {"READY_FOR_CRITIC", "CHANGES_REQUESTED", "SPECIALIST_REVIEW", "FINAL_REVIEW", "READY_FOR_OWNER_AUTHORIZATION",
          "OWNER_AUTHORIZED", "REVIEW_CAP_REACHED"}
AFTER_MERGE = {"MERGED", "MERGED_PENDING_JIRA", "MERGED_PENDING_OWNER_CLOSURE", "DONE"}
RESUME = {"READY", "IN_PROGRESS", "READY_FOR_CRITIC", "CHANGES_REQUESTED", "SPECIALIST_REVIEW", "FINAL_REVIEW",
          "MERGED_PENDING_JIRA", "REVIEW_CAP_REACHED", "MERGED_PENDING_OWNER_CLOSURE"}
# Jira lifecycle mirroring (1.9.3): the controller is the sole writer and each
# mapped event has exactly one status_map target; BLOCK/PARK never write.
JIRA_WRITES = {"WORKER_STARTED": "in_progress", "PR_READY": "in_review",
               "OWNER_CHANGES_REQUESTED": "in_progress", "HEAD_CHANGED": "in_progress", "JIRA_RECONCILED": "done"}
NORMAL = [
    ("BACKLOG", "TICKET_READY", "READY", ["config_valid", "scope_verified", "ownership_claimed", "dependencies_satisfied", "contract_ready"]),
    ("READY", "DISPATCH_ACCEPTED", "DISPATCHED", ["dispatch_permitted", "lease_current", "budget_reserved", "ownership_current"]),
    ("DISPATCHED", "WORKER_STARTED", "IN_PROGRESS", ["run_registered", "worktree_verified"]),
    ("IN_PROGRESS", "WORKER_COMPLETED", "PR_DRAFT", ["worker_result_valid", "branch_pushed", "pr_exists"]),
    ("PR_DRAFT", "PR_READY", "READY_FOR_CRITIC", ["validation_current", "ready_authorization_current", "draft_cleared", "requirements_current"]),
    ("READY_FOR_CRITIC", "CRITIC_REJECTED", "CHANGES_REQUESTED", ["review_current"]),
    ("CHANGES_REQUESTED", "AMENDMENT_ACCEPTED", "AMENDING", ["dispatch_permitted", "lease_current", "budget_reserved", "ownership_current", "amendment_cycles_available"]),
    ("CHANGES_REQUESTED", "CAP_REACHED", "REVIEW_CAP_REACHED", ["amendment_cycles_exhausted", "open_findings_presented"]),
    ("REVIEW_CAP_REACHED", "CAP_MERGE_WITH_NOTES", "FINAL_REVIEW", ["cap_disposition_verified", "residual_risks_recorded", "no_open_boundary_findings"]),
    ("REVIEW_CAP_REACHED", "CAP_PARK", "BLOCKED", ["cap_disposition_verified", "blocker_recorded"]),
    ("REVIEW_CAP_REACHED", "CAP_RESCOPE", "SUPERSEDED", ["cap_disposition_verified", "successor_recorded"]),
    ("REVIEW_CAP_REACHED", "CAP_EXTEND_ONE_CYCLE", "CHANGES_REQUESTED", ["cap_disposition_verified", "cap_extension_available"]),
    ("AMENDING", "AMENDMENT_COMPLETED", "READY_FOR_CRITIC", ["worker_result_valid", "validation_current", "new_head_registered"]),
    ("READY_FOR_CRITIC", "CRITIC_APPROVED_SPECIALISTS", "SPECIALIST_REVIEW", ["critic_current", "specialists_required"]),
    ("READY_FOR_CRITIC", "CRITIC_APPROVED_FINAL", "FINAL_REVIEW", ["critic_current", "no_specialists_required"]),
    ("SPECIALIST_REVIEW", "SPECIALIST_REJECTED", "CHANGES_REQUESTED", ["review_current"]),
    ("SPECIALIST_REVIEW", "SPECIALISTS_APPROVED", "FINAL_REVIEW", ["specialists_current", "critic_current"]),
    ("FINAL_REVIEW", "FINAL_GATE_PASSED", "READY_FOR_OWNER_AUTHORIZATION", ["derived_gate_ready", "requirements_current", "final_gate_current"]),
    ("READY_FOR_OWNER_AUTHORIZATION", "OWNER_AUTHORIZED", "OWNER_AUTHORIZED", ["authorization_verified", "request_unused", "gate_current"]),
    ("OWNER_AUTHORIZED", "MERGE_STARTED", "MERGING", ["executor_certified", "atomic_candidate_protocol", "permit_consumed", "gate_current"]),
    ("MERGING", "MERGE_OBSERVED", "MERGED", ["merge_confirmed", "candidate_matched"]),
    ("MERGE_UNKNOWN", "MERGE_OBSERVED", "MERGED", ["merge_confirmed", "candidate_matched"]),
    ("MERGE_UNKNOWN", "NO_MERGE_CONFIRMED", "FINAL_REVIEW", ["no_merge_confirmed", "external_state_current", "old_permit_revoked"]),
    ("MERGED", "JIRA_UNAVAILABLE", "MERGED_PENDING_JIRA", ["merge_confirmed"]),
    ("MERGED", "OWNER_CLOSURE_REQUIRED", "MERGED_PENDING_OWNER_CLOSURE", ["merge_confirmed", "owner_closure_required"]),
    ("MERGED_PENDING_JIRA", "OWNER_CLOSURE_REQUIRED", "MERGED_PENDING_OWNER_CLOSURE", ["merge_confirmed", "owner_closure_required"]),
    ("MERGED", "JIRA_RECONCILED", "DONE", ["merge_confirmed", "closeout_valid", "jira_done_confirmed", "dependencies_refreshed", "owner_closure_not_required"]),
    ("MERGED_PENDING_JIRA", "JIRA_RECONCILED", "DONE", ["merge_confirmed", "closeout_valid", "jira_done_confirmed", "dependencies_refreshed", "owner_closure_not_required"]),
    ("MERGED_PENDING_OWNER_CLOSURE", "JIRA_RECONCILED", "DONE", ["merge_confirmed", "closeout_valid", "jira_done_confirmed", "dependencies_refreshed", "owner_closure_verified"]),
    ("FAILED", "RECOVERABLE_FAILURE", "BLOCKED", ["failure_recoverable", "blocker_recorded"]),
    ("REOPENED", "REOPEN_DISPOSITION", "CANCELLED", ["owner_disposition", "successor_recorded"]),
]
INVALIDATING = {"HEAD_CHANGED", "BASE_CHANGED", "BASE_RETARGETED", "MERGE_METHOD_CHANGED", "REQUIREMENTS_CHANGED",
                "POLICY_CHANGED", "CI_UNAVAILABLE", "CI_FAILED", "BLOCKING_THREAD_REOPENED", "AUTHORIZATION_REVOKED"}
CONTROL_EVENTS = {"BLOCK", "FAIL", "RECOVER", "CANCEL", "SUPERSEDE", "MERGE_TIMEOUT", "MANUAL_MERGE_OBSERVED",
                  "REVERT_OBSERVED", "PR_CLOSED", "BRANCH_DELETED", "REVIEW_DISMISSED",
                  "OWNER_CHANGES_REQUESTED", "POST_MERGE_FINDING"}
READY_TRANSITION_FORMAT = "awf-pr-ready-transition-1"


def _ready_candidate(pr, head_sha):
    if isinstance(pr, bool) or not isinstance(pr, int) or pr < 1:
        raise ValidationError("PR ready transition requires a positive integer PR number")
    if (not isinstance(head_sha, str) or len(head_sha) != 40 or
            any(character not in "0123456789abcdef" for character in head_sha)):
        raise ValidationError("PR ready transition requires a lowercase 40-character head SHA")


def _authorization_digest(authorization):
    encoded = json.dumps(authorization, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ready_transition_record(pr, head_sha, authorization_record, *, config=None,
                            verify_owner_source=None):
    """Return the mandatory audit record for one draft-to-ready decision.

    The authorization verifier checks trusted provider identity evidence,
    candidate binding, source integrity and freshness.  A host must persist the
    returned record before attempting any provider mutation.
    """
    _ready_candidate(pr, head_sha)
    base = {"format": READY_TRANSITION_FORMAT, "pr": pr, "head_sha": head_sha,
            "logged": True}
    if authorization_record is None:
        return {**base, "decision": "REFUSED",
                "reason": "OWNER_MANAGER_AUTHORIZATION_REQUIRED",
                "authorization": None, "authorization_sha256": None}
    if not isinstance(authorization_record, dict):
        return {**base, "decision": "REFUSED", "reason": "AUTHORIZATION_RECORD_INVALID",
                "authorization": None, "authorization_sha256": None}

    authorization = copy.deepcopy(authorization_record)
    digest = _authorization_digest(authorization)
    request = authorization.get("request")
    if (isinstance(request, dict) and
            (request.get("pr") != pr or request.get("head_sha") != head_sha)):
        return {**base, "decision": "REFUSED", "reason": "AUTHORIZATION_CANDIDATE_MISMATCH",
                "authorization": authorization, "authorization_sha256": digest}
    if not callable(verify_owner_source):
        return {**base, "decision": "REFUSED",
                "reason": "AUTHENTICATED_PROVIDER_OBSERVATION_REQUIRED",
                "authorization": authorization, "authorization_sha256": digest}
    try:
        from .authorization import verify_ready_authorization
        verification = verify_ready_authorization(
            authorization, config, pr=pr, head_sha=head_sha,
            verify_owner_source=verify_owner_source,
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        return {**base, "decision": "REFUSED", "reason": "AUTHORIZATION_RECORD_INVALID",
                "authorization": authorization, "authorization_sha256": digest}
    source = authorization["owner_source"]
    request = authorization["request"]
    return {**base, "decision": "AUTHORIZED", "reason": "EXPLICIT_OWNER_MANAGER_AUTHORIZATION",
            "authorization_id": request["request_id"], "actor_id": source["actor_id"],
            "actor": source["actor_login"],
            "role": request["role"], "authorized_at": source["created_at"],
            "authorization": authorization, "authorization_sha256": digest,
            "authorization_verification": verification}


def validate_ready_transition_record(record, pr, head_sha, *, require_authorized=False):
    """Validate persisted readiness evidence without treating it as live authority."""
    _ready_candidate(pr, head_sha)
    if not isinstance(record, dict) or record.get("format") != READY_TRANSITION_FORMAT or not record.get("logged"):
        raise ValidationError("PR ready transition record is missing or malformed")
    if record.get("pr") != pr or record.get("head_sha") != head_sha:
        raise ValidationError("PR ready transition record references a different candidate")
    if record.get("decision") == "REFUSED":
        if require_authorized:
            raise ValidationError("PR ready transition retains a refusal, not authorization")
        if not isinstance(record.get("reason"), str) or not record["reason"]:
            raise ValidationError("PR ready refusal lacks its retained reason")
        return True
    if record.get("decision") != "AUTHORIZED":
        raise ValidationError("PR ready transition decision is invalid")
    authorization = record.get("authorization")
    verification = record.get("authorization_verification")
    if (not isinstance(authorization, dict) or
            record.get("authorization_sha256") != _authorization_digest(authorization) or
            not isinstance(verification, dict)):
        raise ValidationError("PR ready authorization record or digest is invalid")
    request = authorization.get("request")
    source = authorization.get("owner_source")
    if not isinstance(request, dict) or not isinstance(source, dict):
        raise ValidationError("PR ready authorization source/request is incomplete")
    if (request.get("pr") != pr or request.get("head_sha") != head_sha or
            record.get("authorization_id") != request.get("request_id") or
            record.get("actor_id") != source.get("actor_id") or
            record.get("actor") != source.get("actor_login") or
            record.get("role") != request.get("role") or
            record.get("authorized_at") != source.get("created_at")):
        raise ValidationError("PR ready authorization audit fields do not bind its source and request")
    if any(verification.get(field) is not True for field in
           ("record_consistent", "live_source_verified", "configuration_accepted", "trusted_time_verified")):
        raise ValidationError("PR ready authorization lacks authenticated provider/configuration/time evidence")
    if (verification.get("actor_id") != source.get("actor_id") or
            verification.get("actor_login") != source.get("actor_login") or
            verification.get("role") != request.get("role") or
            verification.get("raw_body_sha256") != source.get("raw_body_sha256") or
            not isinstance(verification.get("source_id"), str) or not verification["source_id"] or
            not isinstance(verification.get("configuration_sha256"), str) or
            not isinstance(verification.get("observed_at"), str) or
            verification.get("execution_authority") is not False):
        raise ValidationError("PR ready authorization verification is internally inconsistent")
    return True


def revalidate_ready_transition_record(record, pr, head_sha, *, config=None,
                                       verify_owner_source=None):
    """Re-read provider/configuration/time evidence before a ready mutation."""
    validate_ready_transition_record(record, pr, head_sha, require_authorized=True)
    from .authorization import verify_ready_authorization
    current = verify_ready_authorization(
        record.get("authorization"),
        config,
        pr=pr,
        head_sha=head_sha,
        verify_owner_source=verify_owner_source,
    )
    saved = record["authorization_verification"]
    stable_fields = ("action", "role", "actor_id", "actor_login", "source_id",
                     "configuration_sha256", "raw_body_sha256", "record_consistent",
                     "live_source_verified", "configuration_accepted", "trusted_time_verified",
                     "execution_authority")
    if any(current.get(field) != saved.get(field) for field in stable_fields):
        raise ValidationError("PR ready authorization provider observation changed")
    return True


def _require_ready_authorization(facts):
    pr = facts.get("pr")
    head_sha = facts.get("head_sha")
    _ready_candidate(pr, head_sha)
    record = facts.get("ready_transition_record")
    if not isinstance(record, dict):
        raise ValidationError("PR ready refused: explicit owner/manager authorization record is required and the refusal must be recorded")
    if record.get("decision") != "AUTHORIZED":
        raise ValidationError("PR ready refused: explicit owner/manager authorization record is required; retain the refusal record")
    try:
        revalidate_ready_transition_record(
            record,
            pr,
            head_sha,
            config=facts.get("ready_authorization_config"),
            verify_owner_source=facts.get("verify_ready_owner_source"),
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        raise ValidationError("PR ready refused: authorization decision record is invalid")


def definition():
    return {"version": 3, "template_version": VERSION, "live_side_effects_supported": False,
            "states": STATES, "terminal_states": sorted(TERMINAL), "resume_states": sorted(RESUME),
            "normal_transitions": [{"from": source, "event": event, "to": target, "actor": "controller", "requires": guards}
                                   for source, event, target, guards in NORMAL],
            "invalidation_events": sorted(INVALIDATING), "control_events": sorted(CONTROL_EVENTS),
            "jira_writes": dict(sorted(JIRA_WRITES.items())),
            "controller_execution_contract": {
                "stream_states": ["WORKING", "PAUSED_INPUT", "BLOCKED", "COMPLETE"],
                "unlabelled_idle_allowed": False,
                "refill_after_run_completion": "immediate_next_eligible_or_explicit_pause_block_complete",
                "blocked_streams_pause_independent_streams": False,
                "status_cadence_seconds_default": 600,
                "periodic_status_disable_flag": "--disable-periodic-status",
                "status_change_digest": "immediate_without_resetting_regular_cadence",
                "review_submission": "all_frozen_reviewers_terminal_and_acceptable_for_exact_tuple",
                "post_merge_reporting_order": ["reconcile_merged_ticket_jira", "count_complete_scoped_jira", "report_closed_and_remaining"]},
            "unspecified_event_policy": "reject", "terminal_invalidation_policy": "audit_without_state_change",
            "merge_uncertainty_policy": "reconcile_before_retry", "implementation": ".agentic/lib/agentic/lifecycle.py"}


def validate_workflow(value):
    if value != definition():
        raise ValidationError("Workflow does not match the installed version's transition contract")
    return True


def transition(state, event, facts=None, resume_state=None):
    facts = facts or {}
    if state not in STATES:
        raise ValidationError("Unknown state")
    known_events = {row[1] for row in NORMAL} | INVALIDATING | CONTROL_EVENTS
    if event not in known_events:
        raise ValidationError("Unknown event")
    def require(*names):
        missing = [name for name in names if facts.get(name) is not True]
        if missing:
            raise ValidationError("Missing verified guards: " + ", ".join(missing))
    if state == "PR_DRAFT" and event == "PR_READY":
        _require_ready_authorization(facts)
    if event == "CAP_EXTEND_ONE_CYCLE" and facts.get("risk_tier") != 2:
        raise ValidationError("CAP_EXTEND_ONE_CYCLE is a Tier 2-only transition; Tier 3 is hard-capped at three rounds")
    if state == "FINAL_REVIEW" and event == "FINAL_GATE_PASSED":
        from .review_completion import validate_ready_gate_completion
        validate_ready_gate_completion(facts.get("final_gate"))
    if event == "REVERT_OBSERVED" and state in AFTER_MERGE:
        require("revert_confirmed")
        return "REOPENED"
    if event == "POST_MERGE_FINDING":
        # Merged work never reopens on a finding; the finding gets a successor
        # ticket whose contract names what it corrects. State is unchanged.
        if state not in AFTER_MERGE:
            raise ValidationError("POST_MERGE_FINDING applies only to merged work")
        require("finding_recorded", "successor_recorded")
        return state
    if state in TERMINAL:
        if event in INVALIDATING:
            return state
        raise ValidationError("Terminal work requires explicit successor/reopen disposition")
    if event == "OWNER_CHANGES_REQUESTED":
        if state not in REVIEW:
            raise ValidationError("Owner change requests apply only to work under review")
        require("external_event_recorded", "prior_evidence_invalidated")
        return "CHANGES_REQUESTED"
    if event == "MANUAL_MERGE_OBSERVED" and state not in AFTER_MERGE:
        require("merge_confirmed", "candidate_matched", "authorization_verified")
        return "MERGED"
    if state in {"MERGING", "MERGE_UNKNOWN"} and event in (INVALIDATING | {"CANCEL", "SUPERSEDE", "FAIL", "BLOCK", "MERGE_TIMEOUT", "PR_CLOSED", "BRANCH_DELETED"}):
        require("external_event_recorded")
        return "MERGE_UNKNOWN"
    if event in {"CANCEL", "SUPERSEDE", "PR_CLOSED", "BRANCH_DELETED"}:
        if state in AFTER_MERGE:
            raise ValidationError("Merged work cannot be cancelled; reconcile a revert or successor")
        require("owner_or_source_disposition", "external_state_current", "tasks_revoked")
        return "SUPERSEDED" if event == "SUPERSEDE" else "CANCELLED"
    if event in INVALIDATING or event == "REVIEW_DISMISSED":
        require("external_event_recorded")
        if state in AFTER_MERGE:
            return state
        if event in {"REQUIREMENTS_CHANGED", "POLICY_CHANGED"}:
            require("blocker_recorded", "prior_evidence_invalidated")
            return "BLOCKED"
        if event in {"HEAD_CHANGED", "BASE_CHANGED", "BASE_RETARGETED", "MERGE_METHOD_CHANGED"}:
            require("prior_evidence_invalidated")
            return "READY_FOR_CRITIC" if state in REVIEW else state
        if event == "AUTHORIZATION_REVOKED":
            require("prior_evidence_invalidated")
            return "READY_FOR_OWNER_AUTHORIZATION" if state in {"OWNER_AUTHORIZED", "READY_FOR_OWNER_AUTHORIZATION"} else state
        if event == "CI_UNAVAILABLE":
            require("blocker_recorded")
            return "BLOCKED"
        if event in {"CI_FAILED", "BLOCKING_THREAD_REOPENED", "REVIEW_DISMISSED"}:
            require("prior_evidence_invalidated")
            return "CHANGES_REQUESTED" if state in REVIEW else state
    if event == "BLOCK" and state not in {"MERGED", "MERGED_PENDING_JIRA", "MERGED_PENDING_OWNER_CLOSURE"}:
        require("blocker_recorded")
        return "BLOCKED"
    if event == "FAIL" and state not in AFTER_MERGE:
        require("failure_recorded")
        return "FAILED"
    if state == "BLOCKED" and event == "RECOVER":
        require("blocker_resolved", "external_state_current", "resume_guards_verified", "old_permit_revoked")
        if resume_state not in RESUME:
            raise ValidationError("Unsafe resume state")
        if resume_state in {"MERGED_PENDING_JIRA", "MERGED_PENDING_OWNER_CLOSURE"}:
            require("merge_confirmed")
        return resume_state
    for source, name, target, guards in NORMAL:
        if source == state and name == event:
            require(*guards)
            return target
    raise ValidationError(f"Event {event} is not applicable in {state}")


def review_round_transition(tier, round_number, *, verdict="PENDING", open_findings=(),
                            owner_review=False, ticketed_p2_records=(), owner_cap_disposition=None,
                            owner_cap_verified=False, owner_cap_context=None,
                            configured_owner_ids=None, expected_candidate_binding=None,
                            previous_diff_sha=None, current_diff_sha=None, base_only=False, config=None):
    """Lifecycle-facing review policy API; provider posting remains AWF-29."""
    if previous_diff_sha is not None and current_diff_sha is not None:
        diff = diff_effect(previous_diff_sha, current_diff_sha, base_only=base_only)
    else:
        diff = {"changed": False, "invalidate": False, "reason": "no diff comparison supplied"}
    # Validate the requested round before any diff shortcut.  The lifecycle
    # API must not accept a schema-shaped mapping plus a caller-supplied
    # boolean as authentication.  The controller supplies the full evidence
    # context so the same owner-record verifier used by the real gate can
    # authenticate the disposition.
    if owner_cap_disposition is not None:
        if owner_cap_verified:
            raise ValidationError("Lifecycle does not trust caller-supplied cap verification")
        if owner_cap_context is None:
            raise ValidationError("Lifecycle requires an authenticated owner cap-disposition context")
        from .authorization import verify_owner_record
        verify_owner_record(owner_cap_disposition, "review-cap-disposition",
                            owner_cap_context["config"], owner_cap_context["contracts"],
                            owner_cap_context["now"],
                            head_sha=owner_cap_context["head_sha"],
                            binding=owner_cap_context["binding"],
                            runs=owner_cap_context.get("runs"),
                            excluded_contexts=owner_cap_context.get("excluded_contexts", ()),
                            excluded_producers=owner_cap_context.get("excluded_producers", ()))
    validate_round(tier, round_number,
                   owner_cap_disposition=owner_cap_disposition, config=config)
    if diff["invalidate"]:
        return {"status": "REVIEW_REQUIRED", "round": round_number, "history_preserved": True,
                "invalidated": True, "diff": diff}
    decision = review_decision(tier, round_number, latest_pass=verdict in {"PASS", "APPROVE"},
                               open_findings=open_findings, owner_review=owner_review,
                               owner_cap_disposition=owner_cap_disposition,
                               ticketed_p2_records=ticketed_p2_records,
                               configured_owner_ids=configured_owner_ids,
                               expected_candidate_binding=expected_candidate_binding,
                               config=config)
    return {**decision, "round": round_number, "history_preserved": True,
            "invalidated": False, "diff": diff, "verdict": verdict}
