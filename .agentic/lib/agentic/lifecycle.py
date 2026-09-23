"""Exhaustive reference transition rules; no external side effects."""
from __future__ import annotations
from . import ValidationError, VERSION

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
# Jira lifecycle mirroring (1.9.2): the controller is the sole writer and each
# mapped event has exactly one status_map target; BLOCK/PARK never write.
JIRA_WRITES = {"WORKER_STARTED": "in_progress", "PR_READY": "in_review",
               "OWNER_CHANGES_REQUESTED": "in_progress", "HEAD_CHANGED": "in_progress", "JIRA_RECONCILED": "done"}
NORMAL = [
    ("BACKLOG", "TICKET_READY", "READY", ["config_valid", "scope_verified", "ownership_claimed", "dependencies_satisfied", "contract_ready"]),
    ("READY", "DISPATCH_ACCEPTED", "DISPATCHED", ["dispatch_permitted", "lease_current", "budget_reserved", "ownership_current"]),
    ("DISPATCHED", "WORKER_STARTED", "IN_PROGRESS", ["run_registered", "worktree_verified"]),
    ("IN_PROGRESS", "WORKER_COMPLETED", "PR_DRAFT", ["worker_result_valid", "branch_pushed", "pr_exists"]),
    ("PR_DRAFT", "PR_READY", "READY_FOR_CRITIC", ["validation_current", "draft_cleared", "requirements_current"]),
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
    ("FINAL_REVIEW", "FINAL_GATE_PASSED", "READY_FOR_OWNER_AUTHORIZATION", ["derived_gate_ready", "requirements_current"]),
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


def definition():
    return {"version": 3, "template_version": VERSION, "live_side_effects_supported": False,
            "states": STATES, "terminal_states": sorted(TERMINAL), "resume_states": sorted(RESUME),
            "normal_transitions": [{"from": source, "event": event, "to": target, "actor": "controller", "requires": guards}
                                   for source, event, target, guards in NORMAL],
            "invalidation_events": sorted(INVALIDATING), "control_events": sorted(CONTROL_EVENTS),
            "jira_writes": dict(sorted(JIRA_WRITES.items())),
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
