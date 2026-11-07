"""Jira lifecycle and controller-owned tier-assignment writes with read-back.

The controller is the sole Jira writer. Lifecycle helpers plan and record
transitions; tier assignment uses a supplied adapter. Epics are never
transitioned and BLOCK/PARK never write.
"""
from __future__ import annotations
import copy
import re
import uuid

from . import ValidationError
from .lifecycle import JIRA_WRITES, REVIEW

EVENT_GUARDS = {"WORKER_STARTED": ["run_registered", "worktree_verified"],
                "PR_READY": ["draft_cleared"],
                "OWNER_CHANGES_REQUESTED": ["external_event_recorded", "prior_evidence_invalidated"],
                "HEAD_CHANGED": ["external_event_recorded", "prior_evidence_invalidated"],
                "JIRA_RECONCILED": ["merge_confirmed", "closeout_valid"]}
NO_WRITE_EVENTS = {"BLOCK", "CAP_PARK", "FAIL", "CANCEL", "SUPERSEDE"}
DEFAULT_OWNER_CLOSURE_KEYWORDS = ("release", "publication", "qualification", "cutover", "production acceptance")
_TIER_LABEL = re.compile(r"^tier-[123]$")


class TierAssignmentOutcomeError(ValidationError):
    """A Jira assignment mutation whose final provider state is not trustworthy."""

    def __init__(self, message, outcome):
        super().__init__(message)
        self.outcome = copy.deepcopy(outcome)


def owner_closure_required(config, snapshot):
    """Release/publication/qualification/cutover/production items stay In Review until the owner closes."""
    keywords = [k.casefold() for k in config["jira"].get("owner_closure_keywords", DEFAULT_OWNER_CLOSURE_KEYWORDS)]
    text = " ".join([snapshot["summary"], *snapshot.get("labels", [])]).casefold()
    return any(keyword and keyword in text for keyword in keywords)


def planned_write(config, contract, event, facts, *, issue_type="LEAF", current_status=None, prior_writes=(), state=None):
    """Return the mapped status_map key for an event, or None with the reason no write is planned."""
    if config["jira"].get("enabled", True) is False:
        return None, "Jira is disabled; no writes"
    if event == "HEAD_CHANGED" and state not in REVIEW:
        return None, "HEAD_CHANGED outside review states has no Jira write"
    if issue_type == "EPIC":
        raise ValidationError("Epics are never transitioned")
    if event in NO_WRITE_EVENTS:
        return None, "Blocked/Parked/cancelled work appears in the owner digest; no Jira write"
    key = JIRA_WRITES.get(event)
    if key is None:
        return None, f"{event} has no mapped Jira write"
    if not config["jira"].get("lifecycle_writes", {"in_progress": True, "in_review": True, "done": True}).get(key, True):
        return None, f"jira.lifecycle_writes.{key} is false"
    missing = [name for name in EVENT_GUARDS[event] if facts.get(name) is not True]
    if missing:
        raise ValidationError(f"{event} write needs verified guards: " + ", ".join(missing))
    if key == "done" and contract.get("owner_closure_required"):
        return None, "owner_closure_required: owner closes in Jira; then run jira-lifecycle JIRA_RECONCILED to observe actor-bound Done evidence"
    for record in prior_writes:
        if record["status"] in {"UNKNOWN", "FAILED"} or (record["read_back"]["observed_status"] not in (None, record["to_status_id"])):
            raise ValidationError("Jira writes are stopped for this ticket after a mismatched or unknown result; report the suspected external automation conflict")
    target = config["jira"]["status_map"][key]
    if current_status == target:
        return None, f"already {target}; no repeat write"
    return key, target


def transition_record(binding, event, from_status, to_status, transition_id, *, jira_provider,
                      merge_result_id=None, producer_id, run_id, now, evidence):
    if event == "JIRA_RECONCILED" and not merge_result_id:
        raise ValidationError("The Done write must reference the observed merge result")
    from .canonical import canonical
    if not isinstance(jira_provider, dict):
        raise ValidationError("Jira transition needs an immutable provider binding")
    intent = {"schema_version": 3, "producer_id": producer_id, "run_id": run_id,
              "binding": binding, "jira_provider": jira_provider,
              "merge_result_id": merge_result_id, "lifecycle_event": event,
              "from_status_id": from_status, "to_status_id": to_status,
              "transition_id": transition_id, "evidence": list(evidence)}
    seed = canonical(intent).decode("utf-8")
    operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "awf-jira-operation:" + seed))
    record_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "awf-jira-record:" + seed))
    return {"schema_version": 3, "record_id": record_id, "created_at": now, "producer_id": producer_id,
            "run_id": run_id, "binding": binding, "operation_id": operation_id, "merge_result_id": merge_result_id,
            "jira_provider": jira_provider,
            "lifecycle_event": event, "from_status_id": from_status, "to_status_id": to_status, "transition_id": transition_id,
            "status": "PROPOSED", "evidence": list(evidence),
            "read_back": {"observed_status": None, "observed_actor": None, "observed_at": None}}


def apply_read_back(record, observed_status, observed_actor=None, observed_at=None):
    """Read after write. Mismatch or unknown stops further writes for this ticket only."""
    result = dict(record)
    result["read_back"] = {"observed_status": observed_status, "observed_actor": observed_actor, "observed_at": observed_at}
    conflict = None
    if observed_status is None:
        result["status"] = "UNKNOWN"
        conflict = "Unknown read-back result; Jira writes for this ticket are stopped; other streams continue"
    elif observed_status == record["to_status_id"]:
        result["status"] = "SUCCEEDED"
    else:
        result["status"] = "FAILED"
        conflict = (f"Observed {observed_status!r} after writing {record['to_status_id']!r} "
                    f"(actor {observed_actor or 'unknown'}, at {observed_at or 'unknown'}); suspected external automation conflict; never reissue")
    return {"record": result, "conflict": conflict, "writes_stopped": conflict is not None}


def closing_comment(pr_number, reviewed_head_sha, merge_commit_sha):
    return f"Done: PR #{pr_number}, reviewed head {reviewed_head_sha}, merge commit {merge_commit_sha}."


def tier_assignment_comment(assignment):
    """Render the controller-owned, auditable Jira assignment comment."""
    lines = [
        f"AWF review assignment `{assignment['assignment_id']}`",
        f"Tier: {assignment['tier']}",
        f"Round cap: {assignment['round_cap']}",
        "Trusted-owner review required: " + ("yes" if assignment["owner_review_required"] else "no"),
        "Reasons: " + "; ".join(assignment.get("reasons", [])),
    ]
    audit = assignment.get("change_audit")
    if audit:
        lines.extend([f"Changed by trusted owner: {audit['who']}", f"Changed at: {audit['when']}",
                      f"Change reason: {audit['why']}",
                      f"Old: T{audit['old']['tier']} cap {audit['old']['round_cap']}",
                      f"New: T{audit['new']['tier']} cap {audit['new']['round_cap']}"])
    return "\n".join(lines)


def require_jira_issue_scope(config, issue_key, snapshot):
    """Refuse every write without a complete adapter-observed scoped issue."""
    from .policy import inside_scope
    if not isinstance(config, dict) or config.get("jira", {}).get("enabled") is not True:
        raise ValidationError("Jira scope refuses writes without enabled reviewed configuration")
    scope = config["jira"].get("scope")
    scope_fields = {"allow_entire_project", "selector_mode", "included_epics",
                    "labels_any", "components_any", "additional_jql", "ownership_required"}
    if (not isinstance(scope, dict) or set(scope) != scope_fields or
            scope.get("ownership_required") is not True or scope.get("additional_jql") != "" or
            type(scope.get("allow_entire_project")) is not bool or
            scope.get("selector_mode") not in {"all", "any"} or
            not isinstance(config["jira"].get("project_key"), str) or not config["jira"]["project_key"].strip() or
            config["jira"]["project_key"].strip() != config["jira"]["project_key"]):
        raise ValidationError("Jira scope configuration is incomplete or malformed")
    for selector in ("included_epics", "labels_any", "components_any"):
        values = scope.get(selector)
        if (not isinstance(values, list) or
                not all(isinstance(item, str) and item.strip() and item == item.strip() for item in values) or
                len(values) != len(set(values))):
            raise ValidationError("Jira scope configuration has malformed " + selector)
    required = {"issue_id", "project_key", "epic_ids", "labels", "component_ids"}
    if not isinstance(snapshot, dict) or not required.issubset(snapshot):
        raise ValidationError("Jira scope observation is missing required issue fields")
    if (snapshot["issue_id"] != issue_key or
            not isinstance(snapshot["project_key"], str) or not snapshot["project_key"].strip() or
            snapshot["project_key"] != snapshot["project_key"].strip() or
            not isinstance(issue_key, str) or re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", issue_key) is None):
        raise ValidationError("Jira scope observation names a different or unbound issue")
    for field in ("epic_ids", "labels", "component_ids"):
        value = snapshot[field]
        if (not isinstance(value, list) or
                not all(isinstance(item, str) and item.strip() and item == item.strip() for item in value) or
                len(value) != len(set(value))):
            raise ValidationError("Jira scope observation has malformed " + field)
    if config.get("jira", {}).get("scope", {}).get("additional_jql"):
        raise ValidationError("Jira writes require qualified additional-JQL scope evidence")
    try:
        allowed = inside_scope(snapshot, config)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Jira scope configuration is incomplete or malformed") from exc
    if not allowed:
        raise ValidationError("Jira scope refuses an unrelated or out-of-scope issue")
    return copy.deepcopy(snapshot)


def _write_tier_assignment(client, assignment, *, controller_actor_id, acting_actor_id, config=None, store=None):
    """Write one tier label/comment and require an exact controller read-back.

    The adapter surface is deliberately tiny and testable: ``read_issue``,
    ``set_labels`` and ``add_comment``. The protected journal must be supplied
    explicitly or through the reviewed adapter's ``controller_store``; absence
    refuses before either write. This function never transitions Jira.
    """
    if not controller_actor_id or acting_actor_id != controller_actor_id:
        raise ValidationError("Tier assignment label/comment writes are controller-only")
    if not isinstance(assignment, dict) or assignment.get("tier") not in (1, 2, 3):
        raise ValidationError("Jira tier assignment requires a valid assignment record")
    key = assignment.get("ticket_key")
    if not isinstance(key, str) or not key:
        raise ValidationError("Jira tier assignment requires a ticket key")
    if not all(callable(getattr(client, name, None)) for name in
               ("read_issue", "set_labels", "add_comment")):
        raise ValidationError("Jira tier assignment requires read_issue, set_labels and add_comment capabilities")
    config = copy.deepcopy(config)
    try:
        before = client.read_issue(key)
    except Exception as exc:
        outcome = {"status": "UNKNOWN", "write_stage": "read_before", "writes_stopped": True,
                   "mutation_attempted": False,
                   "ticket_key": key, "assignment_id": assignment.get("assignment_id"),
                   "observed_actor": None, "observed_at": None,
                   "reason": "Jira tier assignment read-before-write failed"}
        raise TierAssignmentOutcomeError(outcome["reason"], outcome) from exc
    require_jira_issue_scope(config, key, before)
    from .continuous_controller import (require_jira_ticket_writes_allowed,
                                        save_jira_assignment)
    binding = copy.deepcopy(assignment.get("binding", {"issue_id": key}))
    if not isinstance(binding, dict) or binding.get("issue_id") != key:
        raise ValidationError("Jira assignment binding names a different ticket")
    # Reviewed intake adapters may carry the same protected controller journal;
    # explicit callers can supply it without exposing storage through config.
    store = store if store is not None else getattr(client, "controller_store", None)
    require_jira_ticket_writes_allowed(store, config, binding, assignment_route=True)
    if acting_actor_id != config['jira'].get('controller_actor_id'):
        raise ValidationError('Tier assignment actor differs from the configured controller identity')
    labels = before.get("labels") if isinstance(before, dict) else None
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValidationError("Jira tier assignment read did not return labels")
    target = f"tier-{assignment['tier']}"
    updated = [label for label in labels if not _TIER_LABEL.fullmatch(label)] + [target]
    comment = tier_assignment_comment(assignment)
    save_jira_assignment(store, config, assignment, {
        'status': 'UNKNOWN', 'write_stage': 'labels', 'writes_stopped': True,
        'ticket_key': key, 'assignment_id': assignment['assignment_id']},
        expected={'labels': updated, 'comment': comment})
    stage = "labels"
    try:
        client.set_labels(key, updated, actor_id=acting_actor_id)
        stage = "labels_read_back"
        intermediate = require_jira_issue_scope(config, key, client.read_issue(key))
        if sorted(intermediate["labels"]) != sorted(updated):
            outcome = {"status": "FAILED", "write_stage": stage, "writes_stopped": True,
                       "ticket_key": key, "assignment_id": assignment.get("assignment_id"),
                       "observed_actor": intermediate.get("observed_actor"),
                       "observed_at": intermediate.get("observed_at"),
                       "before": {"labels": copy.deepcopy(labels)},
                       "after": {"labels": copy.deepcopy(intermediate["labels"])},
                       "reason": "Jira tier assignment label read-back mismatch; suspected external automation conflict; never reissue"}
            raise TierAssignmentOutcomeError(outcome["reason"], outcome)
        stage = "comment"
        require_jira_ticket_writes_allowed(store, config, binding, assignment_id=assignment["assignment_id"], assignment_route=True)
        client.add_comment(key, comment, actor_id=acting_actor_id)
        stage = "read_back"
        after = client.read_issue(key)
    except TierAssignmentOutcomeError:
        raise
    except Exception as exc:
        outcome = {"status": "UNKNOWN", "write_stage": stage, "writes_stopped": True,
                   "ticket_key": key, "assignment_id": assignment.get("assignment_id"),
                   "observed_actor": None, "observed_at": None,
                   "before": {"labels": copy.deepcopy(labels)},
                   "reason": "Jira tier assignment write/read-back outcome is unknown; stop writes for this ticket"}
        raise TierAssignmentOutcomeError(outcome["reason"], outcome) from exc
    observed_labels = after.get("labels") if isinstance(after, dict) else None
    comments = after.get("comments") if isinstance(after, dict) else None
    matching = [item for item in comments or [] if isinstance(item, dict)
                and item.get("body") == comment and item.get("author_id") == acting_actor_id]
    tier_labels = [label for label in observed_labels or [] if isinstance(label, str) and _TIER_LABEL.fullmatch(label)]
    if tier_labels != [target] or len(matching) != 1:
        outcome = {"status": "FAILED", "write_stage": "read_back", "writes_stopped": True,
                   "ticket_key": key, "assignment_id": assignment.get("assignment_id"),
                   "observed_actor": None, "observed_at": None,
                   "before": {"labels": copy.deepcopy(labels)},
                   "after": {"labels": copy.deepcopy(observed_labels)},
                   "reason": "Jira tier assignment read-back mismatch; stop writes for this ticket"}
        raise TierAssignmentOutcomeError(outcome["reason"], outcome)
    comment_id = matching[0].get("id")
    if not isinstance(comment_id, (str, int)) or str(comment_id) == "":
        outcome = {"status": "FAILED", "write_stage": "read_back", "writes_stopped": True,
                   "ticket_key": key, "assignment_id": assignment.get("assignment_id"),
                   "observed_actor": matching[0].get("author_id"), "observed_at": None,
                   "before": {"labels": copy.deepcopy(labels)},
                   "after": {"labels": copy.deepcopy(observed_labels)},
                   "reason": "Jira tier assignment read-back omitted the comment identity; stop writes for this ticket"}
        raise TierAssignmentOutcomeError(outcome["reason"], outcome)
    return {"read_back_status": "VERIFIED", "label": target,
            "comment_id": str(comment_id),
            "controller_actor_id": acting_actor_id,
            "before": {"labels": copy.deepcopy(labels)},
            "after": {"labels": copy.deepcopy(observed_labels)}}


def write_tier_assignment(client, assignment, *, controller_actor_id, acting_actor_id, config=None, store=None):
    """Serialize assignment with lifecycle/batch ownership and retain its intake receipt."""
    from .continuous_controller import (ContinuousControllerStore, _batch_jira_ownership,
                                        jira_assignment_state, require_jira_ticket_writes_allowed, save_jira_assignment)
    store = store if store is not None else getattr(client, 'controller_store', None)
    arguments = dict(controller_actor_id=controller_actor_id, acting_actor_id=acting_actor_id,
                     config=config, store=store)
    if not isinstance(store, ContinuousControllerStore):
        return _write_tier_assignment(client, assignment, **arguments)
    config, assignment = copy.deepcopy(config), copy.deepcopy(assignment)
    from .canonical import now_text
    if isinstance(assignment, dict):
        assignment.setdefault('created_at', now_text())
    arguments.update(config=config)
    key = assignment.get('ticket_key') if isinstance(assignment, dict) else None
    if not isinstance(key, str) or not key:
        raise ValidationError('Jira tier assignment requires a ticket key')
    jira_assignment_state(store)  # Validate protected storage before creating the ownership file.
    with _batch_jira_ownership(store, issue_id=key) as owned:
        if not owned:
            raise ValidationError('Jira issue is owned by another operation')
        require_jira_ticket_writes_allowed(store, config,
            copy.deepcopy(assignment.get('binding', {'issue_id': key})), assignment_route=True)
        try:
            receipt = _write_tier_assignment(client, assignment, **arguments)
        except TierAssignmentOutcomeError as exc:
            save_jira_assignment(store, config, assignment, exc.outcome)
            raise
        save_jira_assignment(store, config, assignment, receipt)
        return receipt
