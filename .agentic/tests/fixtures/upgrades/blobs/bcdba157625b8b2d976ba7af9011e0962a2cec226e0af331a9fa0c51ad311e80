"""Jira lifecycle mirroring: one mapped write per event, read back, never reissued.

The controller is the sole Jira writer. This module plans and records writes;
it performs none. Epics are never transitioned and BLOCK/PARK never write.
"""
from __future__ import annotations
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
        return None, "owner_closure_required: ticket stays In Review until an owner-closure record arrives"
    for record in prior_writes:
        if record["status"] in {"UNKNOWN", "FAILED"} or (record["read_back"]["observed_status"] not in (None, record["to_status_id"])):
            raise ValidationError("Jira writes are stopped for this ticket after a mismatched or unknown result; report the suspected external automation conflict")
    target = config["jira"]["status_map"][key]
    if current_status == target:
        return None, f"already {target}; no repeat write"
    return key, target


def transition_record(binding, event, from_status, to_status, transition_id, *, merge_result_id=None, producer_id, run_id, now, evidence):
    if event == "JIRA_RECONCILED" and not merge_result_id:
        raise ValidationError("The Done write must reference the observed merge result")
    return {"schema_version": 3, "record_id": str(uuid.uuid4()), "created_at": now, "producer_id": producer_id,
            "run_id": run_id, "binding": binding, "operation_id": str(uuid.uuid4()), "merge_result_id": merge_result_id,
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
