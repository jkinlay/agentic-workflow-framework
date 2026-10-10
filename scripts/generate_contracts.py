#!/usr/bin/env python3
"""Generate the versioned schema catalog. No runtime schema downloads."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION


def obj(properties, **extra):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties, **extra}


def arr(items, minimum=0, **extra):
    return {"type": "array", "items": items, "minItems": minimum, **extra}


def text(minimum=1, **extra):
    return {"type": "string", "minLength": minimum, **extra}


def integer(minimum=0, **extra):
    return {"type": "integer", "minimum": minimum, "maximum": 9007199254740991, **extra}


def enum(*values):
    return {"enum": list(values)}


def const(value):
    return {"const": value}


def nullable(value):
    return {"oneOf": [value, {"type": "null"}]}


def ref(name):
    return {"$ref": f"urn:awf:1.2:{name}"}


def when(field, value, condition):
    return {"if": {"properties": {field: const(value)}, "required": [field]}, "then": condition}


SHA = text(pattern="^[0-9a-f]{40}$")
OBJECT_ID = text(pattern="^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DIGEST = text(pattern="^[0-9a-f]{64}$")
UUID = text(format="uuid")
TIME = text(format="date-time")
BOOL = {"type": "boolean"}
STRINGS = arr(text(), uniqueItems=True)
BASE64_BYTES = obj({
    "encoding": const("base64"),
    "data": text(4, pattern="^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"),
})
TRUE, FALSE = const(True), const(False)
ROLES = ["controller", "worker", "critic", "specialist", "collector", "owner", "verifier"]
STATES = ["BACKLOG", "READY", "DISPATCHED", "IN_PROGRESS", "PR_DRAFT", "READY_FOR_CRITIC",
          "CHANGES_REQUESTED", "AMENDING", "SPECIALIST_REVIEW", "FINAL_REVIEW",
          "READY_FOR_OWNER_AUTHORIZATION", "OWNER_AUTHORIZED", "MERGING", "MERGE_UNKNOWN",
          "MERGED", "MERGED_PENDING_JIRA", "DONE", "BLOCKED", "FAILED", "CANCELLED", "SUPERSEDED", "REOPENED",
          "REVIEW_CAP_REACHED", "MERGED_PENDING_OWNER_CLOSURE"]
RESUME_STATES = ["READY", "IN_PROGRESS", "READY_FOR_CRITIC", "CHANGES_REQUESTED", "SPECIALIST_REVIEW", "FINAL_REVIEW",
                 "MERGED_PENDING_JIRA", "REVIEW_CAP_REACHED", "MERGED_PENDING_OWNER_CLOSURE"]
BOUNDARY_CODES = ["PROTECTED_PATH", "SCOPE_ESCAPE", "CREDENTIAL_EXPOSURE", "UNSAFE_PATH", "UNREGISTERED_PRODUCER",
                  "CI_BINDING", "INDEPENDENCE"]
SKIP_REASONS = ["PLATFORM_PRIVILEGE", "EXTERNAL_DATA", "LICENCE_ABSENT", "TOOLCHAIN_ABSENT"]
LIFECYCLE_EVENTS = ["WORKER_STARTED", "PR_READY", "OWNER_CHANGES_REQUESTED", "HEAD_CHANGED", "JIRA_RECONCILED"]
CONTROLLER_EVENT_TYPES = ["STATE_TRANSITION", "INVALIDATION", "CONTROL", "MERGED", "MERGE_ATTEMPTED", "OWNER_AUTHORIZED",
                          "JIRA_DONE", "evidence_comment", "lifecycle_transition", "authorization_request",
                          "cap_disposition", "finding_disposition", "post_merge_finding", "owner_closure"]
EVIDENCE = arr(text(format="uri"), 1, uniqueItems=True)
BINDING = obj({"project_id": UUID, "repository_id": integer(1), "issue_id": text(),
               "requirements_hash": DIGEST, "contract_hash": DIGEST, "policy_hash": DIGEST,
               "candidate_id": DIGEST})
CRITIC_ARTIFACT_BINDING = obj({"critic_run_id": UUID, "result_sha256": DIGEST,
                               "head_sha": SHA, "round": integer(1)})
REVIEW_ROUND_RECEIPT = obj({
    "critic_artifact_binding": CRITIC_ARTIFACT_BINDING,
    "result_json": text(2), "completion_cycle_id": UUID,
    "completion_tuple_sha256": DIGEST, "completion_reviewer_set_sha256": DIGEST,
    "completion_snapshot_json": text(2), "completion_snapshot_sha256": DIGEST,
    "review_verdict_json": text(2), "review_verdict_sha256": DIGEST,
    "posting_observation_json": BASE64_BYTES, "posting_observation_sha256": DIGEST,
})
# Legacy receipts stay schema-readable only so the semantic evaluator can
# return NOT_READY for absent retained bytes.  Current exports always include it.
REVIEW_ROUND_RECEIPT["required"].remove("result_json")
JIRA_PROVIDER = obj({"cloud_id": text(), "site": text(format="uri"), "project_id": text(),
                     "project_key": text(pattern="^[A-Z][A-Z0-9]*$"),
                     "controller_actor_id": text()})


def bound(properties, **extra):
    return obj({"schema_version": const(3), "record_id": UUID, "created_at": TIME,
                "producer_id": text(), "run_id": UUID, "binding": BINDING, **properties}, **extra)


BASIS = {"oneOf": [obj({"criterion_id": text()}), obj({"boundary_code": enum(*BOUNDARY_CODES)})]}
FINDING = obj({"id": text(), "severity": enum("BLOCKER", "MAJOR", "MINOR", "NIT"),
               "summary": text(), "evidence": EVIDENCE,
               "status": enum("OPEN", "RESOLVED", "DISPUTED"),
               "resolution_evidence": arr(text(format="uri"), uniqueItems=True),
               "basis": nullable(BASIS), "supersedes_finding_id": nullable(text()), "path": nullable(text())},
              allOf=[{"if": {"properties": {"severity": enum("BLOCKER", "MAJOR")}},
                      "then": {"properties": {"basis": BASIS}}}])
CLOSURE = obj({"result": enum("MET", "NOT_MET", "UNKNOWN"), "evidence": EVIDENCE})
NO_BLOCKERS = {"not": {"contains": {"type": "object", "required": ["severity", "status"],
                                     "properties": {"severity": enum("BLOCKER", "MAJOR"),
                                                    "status": enum("OPEN", "DISPUTED")}}}}
AC_RESULT = obj({"id": text(), "verdict": enum("PASS", "FAIL", "UNKNOWN"), "evidence": EVIDENCE})
GATE_NAMES = ["review_completion", "verdict_posting", "acceptance_criteria", "scope", "critic_current_tuple", "specialist_reviews",
              "required_ci", "ci_candidate_binding", "blocking_threads_zero", "dependencies",
              "merge_compatibility", "ticket_snapshot_current", "review_coverage", "provenance", "local_ci_parity",
              "publication_safety"]


def catalog():
    schemas = {}
    operating_model = obj({
        "model": text(maxLength=256),
        "reasoning_effort": enum("low", "medium", "high", "xhigh", "max", "ultra"),
        "pinned": BOOL,
    }, required=["model", "reasoning_effort"])
    operating_stream = obj({
        "worker": operating_model,
        "reviewer": operating_model,
    }, required=["worker"])
    operating_epic_stream = {
        "type": "object", "minProperties": 1,
        "properties": {
        "worker": {"$ref": "#/properties/controller"},
        "reviewer": {"$ref": "#/properties/controller"},
        },
        "additionalProperties": False,
    }
    operating_epic_override = {
        "type": "object", "minProperties": 1,
        "properties": {
        "streams": {
            "type": "object", "minProperties": 1,
            "patternProperties": {"^[A-F]$": operating_epic_stream},
            "additionalProperties": False,
        },
        "controller": {"$ref": "#/properties/controller"},
        "specialist": {"$ref": "#/properties/specialist"},
        "critic": {"$ref": "#/properties/critic"},
        },
        "additionalProperties": False,
    }
    operating_streams = obj({
        "count": integer(1, maximum=6),
        **{label: operating_stream for label in "ABCDEF"},
    }, required=["count"])
    reviewer_requirements = []
    for count, label in enumerate("ABCDEF", 1):
        reviewer_requirements.append({
            "if": {"properties": {"streams": {"properties": {
                "count": {"minimum": count},
            }}}, "required": ["streams"]},
            "then": {"properties": {"streams": {"properties": {
                label: {"required": ["reviewer"]},
            }}}},
        })
    schemas["operating-config"] = obj({
        "version": const(1),
        "source": {"type": "string", "pattern": "^(default|user|recommendation:R-[0-9a-f]{24})$"},
        "epic_overrides": {
            "type": "object", "maxProperties": 1000,
            "propertyNames": {"maxLength": 80},
            "patternProperties": {
                "^[A-Z][A-Z0-9_]*-[1-9][0-9]*$": operating_epic_override,
            },
            "additionalProperties": False,
        },
        "streams": operating_streams,
        "controller": operating_model,
        "specialist": operating_model,
        "simple_worker": obj({**operating_model["properties"], "enabled": BOOL},
                             required=["model", "reasoning_effort", "enabled"]),
        "critic": operating_model,
    }, required=["version", "source", "streams", "controller", "specialist", "simple_worker"],
       allOf=[{"if": {"not": {"required": ["critic"]}},
               "then": {"allOf": reviewer_requirements}}])
    schemas["operating-change"] = obj({
        "id": {"type": "string", "pattern": "^C-[0-9a-f]{32}$"},
        "created_at": text(20, format="date-time", maxLength=40),
        "instruction": {"type": ["string", "null"], "maxLength": 65536},
        "before_hash": {"anyOf": [{"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                    {"type": "null"}]},
        "after_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "changes": {"type": "array", "maxItems": 64,
                    "items": obj({"path": text(maxLength=128), "before": {}, "after": {}})},
        "source": {"type": "string", "pattern": "^(default|user|recommendation:R-[0-9a-f]{24})$"},
        "applied_from": enum("chat", "bootstrap"),
        "sequence": {"type": "integer", "minimum": 1},
        "previous_change_id": {"type": ["string", "null"],
                               "pattern": "^C-[0-9a-f]{32}$"},
        "governance_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }, allOf=[{
        "if": {"properties": {"applied_from": const("chat")}},
        "then": {"properties": {"instruction": text()}},
        "else": {"properties": {"instruction": {"type": "null"}}},
    }])
    schemas["operating-epics"] = obj({
        "schema_version": const(1),
        "epics": {"type": "array", "maxItems": 1000, "items": obj({
            "id": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*-[1-9][0-9]*$"},
            "title": text(maxLength=2048),
            "scope": {"anyOf": [
                {"type": "null"},
                {"type": "array", "maxItems": 64,
                 "items": {"type": "string", "maxLength": 512}},
            ]},
            "risk_flags": {"type": "array", "uniqueItems": True, "maxItems": 32,
                           "items": text(maxLength=128)},
            "complexity": enum("low", "medium", "high"),
            "uncertainty": enum("low", "medium", "high"),
            "verification": enum("strong", "weak", "none"),
        }, required=["id", "title", "scope"])},
    })
    schemas["operating-recommendation"] = obj({
        "id": {"type": "string", "pattern": "^R-[0-9a-f]{24}$"},
        "created_at": text(20, format="date-time", maxLength=40),
        "operating_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "governance_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "inputs": obj({
            "epics": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "inventory": {"anyOf": [
                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                {"type": "null"},
            ]},
        }),
        "input_files": obj({
            "epics": {"type": ["string", "null"]},
            "inventory": {"type": ["string", "null"]},
        }),
        "input_content": obj({
            "epics": {"type": "string", "maxLength": 1048576},
            "inventory": {"type": ["string", "null"], "maxLength": 1048576},
        }),
        "rows": {"type": "array", "maxItems": 14016, "items": obj({
            "path": text(maxLength=128), "current": {}, "recommended": {},
            "reason": text(maxLength=4096),
        })},
        "epic_count": {"type": "integer", "minimum": 0},
        "ticket_count": {"type": "integer", "minimum": 0},
        "execution_authority": FALSE,
        "applied": FALSE,
    })
    private_sha = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    private_count = integer(0, maximum=1000000)
    private_candidate = obj({
        "repository_id": {"type": "integer", "minimum": 1},
        "pr_number": {"type": "integer", "minimum": 1},
        "base_sha": {"type": "string", "pattern": "^[0-9a-f]{40,64}$"},
        "head_sha": {"type": "string", "pattern": "^[0-9a-f]{40,64}$"},
        "tree_sha": {"type": "string", "pattern": "^[0-9a-f]{40,64}$"},
        "manifest_sha256": {"$ref": "#/$defs/sha256"},
        "provider_pr_body_sha256": {"$ref": "#/$defs/sha256"},
    })
    schemas["private-deny-scan"] = obj({
        "format": const("awf-private-deny-scan-receipt-1"),
        "policy_id": const("exact-current-tree-baseline-v1"),
        "status": enum("PASS", "BLOCKED"),
        "classification": enum("NO_MATCHES", "BASE_PREEXISTING_UNCHANGED"),
        "candidate": {"$ref": "#/$defs/candidate"},
        "provider_pr_body_sha256": {"$ref": "#/$defs/sha256"},
        "mapping_sha256": {"$ref": "#/$defs/sha256"},
        "scanner_sha256": {"$ref": "#/$defs/sha256"},
        "counts": obj({name: private_count for name in (
            "history_matches", "diff_added_matches", "diff_deleted_matches",
            "body_matches", "base_matches", "head_matches",
        )}),
        "paths": {"type": "array", "items": obj({
            "path": text(maxLength=512, pattern="^[A-Za-z0-9._ -]+(/[A-Za-z0-9._ -]+)*$"),
            "base_count": private_count, "head_count": private_count,
            "multiset_equal": BOOL,
        })},
        "unscanned": {"type": "array", "maxItems": 10000,
                      "items": text(maxLength=1024)},
        "scan_complete": BOOL,
        "baseline_authorization_sha256": {"oneOf": [
            {"type": "null"}, {"$ref": "#/$defs/sha256"},
        ]},
        "private_match_values_included": FALSE,
        "execution_authority": FALSE,
    }, allOf=[
        {"if": {"properties": {"status": const("PASS")}},
         "then": {"properties": {
            "unscanned": {"maxItems": 0}, "scan_complete": const(True),
        }}},
        {"if": {"properties": {"classification": const("NO_MATCHES")}},
         "then": {"properties": {
            "counts": {"properties": {"base_matches": const(0),
                                         "head_matches": const(0)}},
            "paths": {"maxItems": 0},
            "baseline_authorization_sha256": {"type": "null"},
        }}},
        {"if": {"properties": {"status": const("PASS"),
                                  "classification": const("BASE_PREEXISTING_UNCHANGED")}},
         "then": {"properties": {
             "counts": {"properties": {
                 "base_matches": {"minimum": 1}, "head_matches": {"minimum": 1},
                 "history_matches": const(0), "diff_added_matches": const(0),
                 "diff_deleted_matches": const(0), "body_matches": const(0),
             }},
             "paths": {"minItems": 1},
             "baseline_authorization_sha256": {"$ref": "#/$defs/sha256"},
         }}},
    ], **{"$defs": {"sha256": private_sha, "candidate": private_candidate}})
    schemas["private-deny-baseline-authorization"] = obj({
        "format": const("awf-private-deny-baseline-authorization-1"),
        "candidate": {"$ref": "urn:awf:1.2:private-deny-scan#/$defs/candidate"},
        "provider_pr_body_sha256": {"$ref": "urn:awf:1.2:private-deny-scan#/$defs/sha256"},
        "mapping_sha256": {"$ref": "urn:awf:1.2:private-deny-scan#/$defs/sha256"},
        "authorized_by": text(maxLength=512),
        "authorization_ref": text(maxLength=512),
    })
    schemas["candidate"] = obj({"host": text(format="uri"), "repository_id": integer(1),
        "repository": text(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
        "pr_number": integer(1), "target_base_branch": text(), "head_sha": SHA,
        "target_base_sha": SHA, "merge_base_sha": SHA, "head_tree_sha": SHA,
        "integration_tree_sha": SHA, "tested_merge_sha": nullable(SHA),
        "diff_sha256": DIGEST, "merge_method": enum("merge", "squash", "rebase")})
    review_tuple = obj({"repository": text(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
        "base_sha": SHA, "head_sha": SHA, "head_tree_sha": SHA,
        "contract_sha256": DIGEST, "review_input_sha256": DIGEST})
    review_counts = obj({name: integer() for name in
        ["required", "completed", "acceptable", "failed", "stale", "outstanding"]})
    reviewer_state = obj({"reviewer_id": text(),
        "state": enum("MISSING", "RUNNING", "ACCEPTABLE", "FAILED", "TIMED_OUT", "MALFORMED", "CANCELLED", "STALE", "DUPLICATE")})
    schemas["review-completion"] = obj({"schema_version": const(3), "cycle_id": UUID,
        "state": enum("COLLECTING", "SUBMITTING", "SUBMITTED", "SUBMISSION_UNKNOWN", "INVALIDATED"),
        "current": BOOL, "ready": BOOL, "tuple": review_tuple, "tuple_sha256": DIGEST,
        "required_reviewers": arr(text(), 1, uniqueItems=True), "reviewer_set_sha256": DIGEST,
        "counts": review_counts, "reviewers": arr(reviewer_state, 1, uniqueItems=True),
        "completion_snapshot_sha256": nullable(DIGEST)}, allOf=[
            {"if": {"properties": {"ready": const(True)}}, "then": {"properties": {
                "state": const("COLLECTING"), "current": const(True),
                "counts": {"properties": {"failed": const(0), "stale": const(0), "outstanding": const(0)}},
                "reviewers": {"items": {"properties": {"state": const("ACCEPTABLE")}}}}}}])
    schemas["review-completion"]["properties"]["exported_round_receipts"] = arr(
        REVIEW_ROUND_RECEIPT, uniqueItems=True)
    completion_snapshot = obj({"cycle_id": UUID, "tuple": review_tuple, "tuple_sha256": DIGEST,
        "required_reviewers": arr(text(), 1, uniqueItems=True), "reviewer_set_sha256": DIGEST,
        "counts": review_counts, "results": arr(obj({"reviewer_id": text(), "state": const("ACCEPTABLE"),
            "result_sha256": DIGEST, "terminal_at": TIME}), 1, uniqueItems=True)})
    schemas["review-submission"] = obj({"submission_id": UUID, "cycle_id": UUID,
        "completion_snapshot": completion_snapshot, "completion_snapshot_sha256": DIGEST,
        "aggregate": {}, "aggregate_sha256": DIGEST,
        "provider_preconditions": obj({"operation_id": UUID, "prepared_at": TIME,
            "repository": review_tuple["properties"]["repository"],
            "base_sha": SHA, "head_sha": SHA, "head_tree_sha": SHA,
            "tuple_sha256": DIGEST, "reviewer_set_sha256": DIGEST,
            "completion_snapshot_sha256": DIGEST, "aggregate_sha256": DIGEST}),
        "execution_authority": FALSE})
    classification_evidence = {str(tier): STRINGS for tier in (1, 2, 3)}
    classification_evidence_schema = obj(classification_evidence, minProperties=1)
    classification_evidence_schema["required"] = []
    schemas["risk-classification"] = obj({
        "tier": enum(1, 2, 3), "matched_tiers": arr(enum(1, 2, 3), 1, uniqueItems=True),
        "evidence": classification_evidence_schema, "rule": text(), "risk_flags": STRINGS})
    schemas["review-verdict"] = obj({
        "record_id": UUID, "created_at": TIME, "producer_id": text(), "run_id": UUID, "binding": BINDING,
        "verdict": enum("PASS", "P1", "P2", "REQUEST_CHANGES"),
        "tier": enum(1, 2, 3), "round": integer(1), "head_sha": SHA,
        "critic_artifact_binding": CRITIC_ARTIFACT_BINDING,
        "reviewer_id": text(), "pr_comment_url": text(format="uri"),
        "pr_body_link": text(format="uri"), "evidence": STRINGS,
        "posting_observation": obj({"source": const("host_observation"), "observed_at": TIME,
            "producer_id": text(), "run_id": UUID, "provider_kind": const("github"),
            "critic_artifact_binding": CRITIC_ARTIFACT_BINDING,
            "review_verdict_record_id": UUID, "review_verdict_sha256": DIGEST,
            "repository_id": integer(1), "pr_number": integer(1), "comment_id": integer(1),
            "comment_url": text(format="uri"), "comment_bytes": text(2), "comment_sha256": DIGEST,
            "body_link": text(format="uri"), "body_bytes": text(2), "body_sha256": DIGEST,
            "provider_response_bytes": BASE64_BYTES, "provider_response_sha256": DIGEST,
            "collector_receipt_sha256": DIGEST}),
        "owner_review": BOOL,
        "owner_id": integer(1),
        "candidate_binding": obj({"repository_id": integer(1), "pr_number": integer(1),
                                   "base_sha": SHA, "head_sha": SHA}),
        "critic_review": obj({"record_id": UUID, "run_id": UUID, "round": integer(1), "head_sha": SHA,
                               "verdict": enum("APPROVE", "REQUEST_CHANGES"), "findings_sha256": DIGEST}),
        })
    stream_status = obj({"stream": text(), "state": enum("WORKING", "PAUSED_INPUT", "BLOCKED", "COMPLETE"),
        "ticket": nullable(text()), "actor": text(), "reason": text(), "next_action": text(),
        "resume_trigger": text(), "exact_tuple": text(), "activity": text(), "verification_gate": text(),
        "reviewer_completion": review_counts, "open_findings": integer(), "jira_status": text(),
        "updated_at": TIME})
    unresolved_dispatch = obj({"dispatch_id": text(), "stream": text(), "ticket": text(),
        "exact_tuple": text(), "status": const("UNKNOWN"), "detached": BOOL, "updated_at": TIME})
    controller_status_digest = obj({"schema_version": const(3), "delivery_id": DIGEST, "observed_at": TIME,
        "kind": enum("REGULAR", "CHANGE"), "cadence_seconds": integer(1), "all_complete": BOOL,
        "unresolved_dispatches": {"type": "array", "items": unresolved_dispatch},
        "streams": arr(stream_status, 1, uniqueItems=True)}, allOf=[
            {"if": {"properties": {"all_complete": const(True)}},
             "then": {"properties": {"streams": {"items": {"properties": {"state": const("COMPLETE")}}}}}},
            {"if": {"properties": {"all_complete": const(False)}},
             "then": {"properties": {"streams": {"contains": {"properties": {"state": {"not": const("COMPLETE")}}}}}}}])
    controller_status_digest["required"].remove("unresolved_dispatches")
    schemas["controller-status-digest"] = controller_status_digest
    count_or_unobserved = {"oneOf": [integer(), const("UNOBSERVED")]}
    schemas["jira-progress"] = obj({"schema_version": const(3), "merged_ticket": text(), "scope": text(),
        "scope_sha256": DIGEST, "include_epics": BOOL, "snapshot_id": nullable(text()),
        "observed_at": TIME, "closed": count_or_unobserved, "remaining_open": count_or_unobserved,
        "jira_state": enum("COUNTED", "RECONCILED", "UNOBSERVED", "JIRA_DISABLED"), "reason": text()}, allOf=[
            {"if": {"properties": {"jira_state": const("COUNTED")}}, "then": {"properties": {
                "closed": integer(), "remaining_open": integer(), "snapshot_id": text()}}},
            {"if": {"properties": {"jira_state": {"enum": ["RECONCILED", "UNOBSERVED", "JIRA_DISABLED"]}}},
             "then": {"properties": {"closed": const("UNOBSERVED"), "remaining_open": const("UNOBSERVED")}}}])
    ac = obj({"id": text(), "text": text(), "validation": text()})
    dependency = obj({"issue_id": text(), "ticket": text(), "kind": enum("code", "deployment", "migration", "environment"),
        "direction": const("requires"), "satisfied": BOOL, "target": text(), "evidence": arr(text(format="uri"), uniqueItems=True)})
    schemas["jira-snapshot"] = obj({"schema_version": const(3), "issue_id": text(), "project_key": text(),
        "summary": text(), "description": text(0), "acceptance_criteria": arr(ac, 1),
        "dependencies": arr(dependency), "linked_requirements": arr(obj({"uri": text(format="uri"), "version": text(), "sha256": DIGEST})),
        "epic_ids": STRINGS, "labels": STRINGS, "component_ids": STRINGS})
    schemas["ticket-contract"] = obj({"schema_version": const(3), "contract_id": UUID, "contract_version": integer(1),
        "created_at": TIME, "project_id": UUID, "repository_id": integer(1), "issue_id": text(), "ticket": text(),
        "requirements_hash": DIGEST, "policy_hash": DIGEST, "objective": text(), "acceptance_criteria": arr(ac, 1),
        "dependencies": arr(dependency), "branch_origin_sha": SHA, "target_base_branch": text(),
        "scope": obj({"expected_paths": STRINGS, "allowed_adjacent_paths": STRINGS,
                      "protected_interfaces": STRINGS, "forbidden_changes": STRINGS,
                      "governance_change_authorization": nullable(UUID)}),
        "validation": obj({"commands": arr(text(), 1, uniqueItems=True), "test_cases": STRINGS,
                           "tests_not_applicable_reason": nullable(text()),
                           "required_resources": STRINGS,
                           "platform_distinction": nullable(obj({"reason": text(), "regression_test_id": text()}))}),
        "risk_flags": obj({k: BOOL for k in ["security", "schema_or_migration", "public_api", "data_loss", "concurrency", "production", "permissions", "release", "merge_gate", "ci_gate", "high_complexity", "high_uncertainty"]}),
        "specialist_domains": STRINGS, "disposition": enum("DRAFT", "BLOCKED", "READY"),
        "risk_tier": enum(1, 2, 3), "tier_justification": text(),
        "closure_standard": obj({"kind": enum("FULL", "DECLARED_LIMITATIONS"),
                                 "accepted_limitations": arr(obj({"id": text(), "text": text(), "attribution": text()})),
                                 "evidence_required": STRINGS}),
        "owner_closure_required": BOOL, "corrects": nullable(obj({"ticket": text(), "merge_commit_sha": SHA}))},
        allOf=[when("disposition", "READY", {"properties": {"dependencies": {"items": {"properties": {"satisfied": TRUE, "evidence": {"minItems": 1}}}}}}),
               {"if": {"properties": {"closure_standard": {"properties": {"kind": const("DECLARED_LIMITATIONS")}}}},
                "then": {"properties": {"closure_standard": {"properties": {"accepted_limitations": {"minItems": 1}}}}}}])
    schemas["ticket-contract"]["properties"]["scope"]["properties"]["evidence_paths"] = STRINGS
    schemas["ticket-contract"]["properties"]["scope"]["required"].append("evidence_paths")
    schemas["ticket-contract"]["properties"]["risk_flags"]["required"] = [
        key for key in schemas["ticket-contract"]["properties"]["risk_flags"]["required"]
        if key not in {"permissions", "release", "merge_gate", "ci_gate", "high_complexity", "high_uncertainty"}]
    contract_classification_evidence = obj({str(tier): STRINGS for tier in (1, 2, 3)}, minProperties=1)
    contract_classification_evidence["required"] = []
    schemas["ticket-contract"]["properties"]["risk_classification"] = obj({
        "tier": enum(1, 2, 3), "matched_tiers": arr(enum(1, 2, 3), 1, uniqueItems=True),
        "evidence": contract_classification_evidence,
        "rule": text(), "risk_flags": STRINGS})
    schemas["ticket-contract"]["required"].append("risk_classification")
    schemas["work-dispatch"] = bound({"contract_id": UUID, "contract_version": integer(1),
        "role": enum("worker", "amendment"), "branch": text(), "worktree_id": UUID,
        "lease": obj({"required": BOOL, "lease_id": nullable(UUID), "fencing_token": nullable(integer(1)), "expires_at": nullable(TIME)},
             allOf=[when("required", True, {"properties": {"lease_id": UUID, "fencing_token": integer(1), "expires_at": TIME}})]),
        "collision_check": enum("PASS", "FAIL", "UNKNOWN"), "collision_evidence": arr(text(format="uri")),
        "disposition": enum("PROPOSAL", "DENIED", "PERMITTED"), "control_generation": integer(1),
        "required_resources": STRINGS},
        allOf=[when("disposition", "PERMITTED", {"properties": {"collision_check": const("PASS"), "collision_evidence": {"minItems": 1}}})])
    schemas["run-attestation"] = bound({"role": enum(*ROLES), "context_id": UUID, "parent_run_id": nullable(UUID),
        "model": text(), "runtime_version": text(), "instruction_hash": DIGEST, "permission_profile_hash": DIGEST,
        "capabilities": STRINGS, "status": enum("COMPLETE", "FAILED", "CANCELLED"),
        "attestation_evidence": EVIDENCE,
        "resources_held": arr(obj({"name": text(pattern="^[a-z][a-z0-9_]*$"), "slots": integer(1), "from": TIME, "until": TIME}))})
    change = obj({"path": text(), "action": enum("added", "modified", "deleted")})
    schemas["worker-result"] = bound({"status": enum("COMPLETE", "BLOCKED", "FAILED"), "commit_route": enum("WORKER", "PUBLISHER"), "dispatch_id": UUID,
        "files_changed": STRINGS, "tested_tree": OBJECT_ID, "changes": arr(change, 1, uniqueItems=True),
        "ignored_untracked": STRINGS, "acceptance_criteria": arr(AC_RESULT, 1),
        "validation": arr(obj({"command": text(), "started_at": TIME, "finished_at": TIME, "exit_code": integer(-2147483648),
            "tested_tree_sha": SHA, "clean_checkout": BOOL, "evidence": EVIDENCE,
            "tests_discovered": integer(), "tests_executed": integer(),
            "declared_skips": arr(obj({"id": text(), "reason_code": enum(*SKIP_REASONS)})),
            "unevaluable_files": STRINGS}), 1),
        "self_review_complete": BOOL, "findings_addressed": STRINGS, "blockers": STRINGS, "closure": CLOSURE},
        allOf=[when("commit_route", "PUBLISHER", {"required": ["tested_tree", "changes"]})])
    schemas["amendment-result"] = copy.deepcopy(schemas["worker-result"])
    for name in ("worker-result", "amendment-result"):
        for field in ("commit_route", "tested_tree", "changes", "ignored_untracked"):
            schemas[name]["required"].remove(field)
    review = {"verdict": enum("APPROVE", "REQUEST_CHANGES", "INCOMPLETE"), "acceptance_criteria": arr(AC_RESULT, 1),
              "findings": arr(FINDING), "prior_finding_ids": STRINGS, "closure": CLOSURE,
              "coverage": obj({"complete": BOOL, "file_manifest_sha256": DIGEST, "reviewed_paths": STRINGS, "omissions": STRINGS}),
              "evidence_checked": EVIDENCE}
    schemas["critic-review"] = bound(review, allOf=[when("verdict", "APPROVE", {"properties": {
        "acceptance_criteria": {"items": {"properties": {"verdict": const("PASS")}}}, "findings": NO_BLOCKERS,
        "closure": {"properties": {"result": const("MET")}},
        "coverage": {"properties": {"complete": TRUE, "omissions": {"maxItems": 0}}}}})])
    schemas["specialist-review"] = bound({"domain": text(), "reviewer_identity": text(), "trigger_evidence": EVIDENCE,
        "verdict": enum("PASS", "FAIL", "NOT_APPLICABLE", "INCOMPLETE"), "findings": arr(FINDING),
        "evidence_checked": EVIDENCE}, allOf=[when("verdict", "PASS", {"properties": {"findings": NO_BLOCKERS}})])
    schemas["specialist-dispatch"] = bound({"domain": text(), "required": TRUE, "trigger_evidence": EVIDENCE,
        "reviewer_identity": text(), "permission_profile_hash": DIGEST, "deadline": TIME})
    check = obj({"name": text(), "check_id": text(), "app_id": integer(1), "workflow_path": text(), "workflow_sha": SHA,
        "attempt": integer(1), "event": const("pull_request"), "conclusion": enum("success", "failure", "skipped", "neutral", "pending", "cancelled"),
        "tested_tree_sha": SHA, "tested_commit_sha": SHA, "tests_executed": integer(), "completed_at": TIME, "evidence": EVIDENCE,
        "checkout_depth": enum("full", "shallow", "unknown")})
    schemas["ci-evidence"] = bound({"retrieval_complete": BOOL, "candidate_type": const("synthetic_merge"), "checks": arr(check, 1),
        "collector_attestation_id": UUID})
    schemas["pr-snapshot"] = bound({"state": enum("OPEN", "CLOSED", "MERGED"), "draft": BOOL, "mergeable": BOOL,
        "retrieval_complete": BOOL, "file_manifest": arr(obj({"path": text(), "blob_sha": SHA}), 1),
        "body_sha256": DIGEST,
        "blocking_threads": arr(obj({"id": text(), "status": enum("OPEN", "RESOLVED"), "evidence": EVIDENCE})),
        "scope_pass": BOOL, "dependency_compatibility_pass": BOOL, "ruleset_verified": BOOL,
        "specialist_domains": STRINGS, "classification_complete": BOOL, "collector_attestation_id": UUID,
        "evidence": EVIDENCE})
    scan_finding = obj({"commit": OBJECT_ID, "path": text(), "line": nullable(integer(1)), "source": text(),
        "change": nullable(enum("added", "deleted")), "detector_id": text(),
        "classification": enum("BLOCKING", "PRE_EXISTING"), "redacted_excerpt": text()})
    unscanned = obj({"commit": OBJECT_ID, "path": text(), "source": text(),
        "reason": enum("binary", "oversize", "invalid-utf8"),
        "parent": nullable(OBJECT_ID)})
    schemas["publication-scan"] = obj({"schema_version": const(3), "status": enum("PASS", "BLOCKED"),
        "base_sha": OBJECT_ID, "head_sha": OBJECT_ID, "pr_body_sha256": nullable(DIGEST),
        "additional_pr_body_sha256": arr(DIGEST), "comment_sha256": arr(DIGEST),
        "mapping_sha256": nullable(DIGEST), "project_config_sha256": nullable(DIGEST),
        "mapping_loaded": BOOL, "mapping_location": const(".agentic-state/publication-deny.json"),
        "commits_scanned": arr(OBJECT_ID), "findings": arr(scan_finding),
        "total_findings": integer(), "blocking_findings": integer(), "pre_existing_findings": integer(),
        "unscanned": arr(unscanned), "unscanned_count": integer(),
        "coverage": obj({"current_files": TRUE, "commit_messages": TRUE, "every_patch": TRUE,
            "generated_reports": text(), "captured_command_output": text(), "strict_utf8": TRUE,
            "pr_bodies": BOOL, "pr_comments": BOOL}),
        "execution_authority": FALSE})
    schemas["rules-activation-decision"] = obj({
        "format": const("awf-rules-activation-decision-1"),
        "status": enum("BLOCKED", "RULES_OBSERVED"),
        "configuration": obj({"status": const("ACCEPTED"), "policy_sha256": DIGEST,
                              "merge_method": enum("merge", "squash", "rebase")}),
        "binding": obj({"repository": text(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
                        "repository_id": integer(1), "default_branch": text()}),
        "observed_rules_state": enum("APPLIED", "MISSING", "UNOBSERVED"),
        "observed_rules": {"type": "object"},
        "publication_consequence": obj({"code": enum("PUBLICATION_GOVERNANCE_OBSERVED",
            "PUBLICATION_GOVERNANCE_MISSING", "PUBLICATION_GOVERNANCE_UNOBSERVED"), "message": text()}),
        "proposed_ruleset": {"type": "object"},
        "proposed_ruleset_sha256": DIGEST,
        "owner_approval_action": obj({"required": BOOL,
            "state": enum("PENDING", "DECLINED", "APPLIED", "NOT_REQUIRED", "NOT_AVAILABLE"),
            "actor": const("repository_owner_or_administrator"), "approval": text(),
            "provider_request": obj({"method": const("POST"), "endpoint": text(),
                                     "body_sha256": DIGEST, "body": {"type": "object"}})}),
        "post_action_observation": obj({"required": BOOL,
            "state": enum("APPLIED", "MISSING", "UNOBSERVED", "NOT_APPLICABLE"),
            "observation_sha256": nullable(DIGEST), "observed_at": nullable(TIME),
            "configuration_compatible": nullable(BOOL)}),
        "final_rules_state": enum("APPLIED", "MISSING", "UNOBSERVED"),
        "operational_blocker": BOOL,
        "blocker_codes": arr(enum("RULES_UNOBSERVED", "RULES_OBSERVATION_NOT_LIVE", "RULES_OBSERVATION_INVALID",
            "RULES_OBSERVATION_WRONG_REPOSITORY", "RULES_OBSERVATION_WRONG_REPOSITORY_ID",
            "RULES_OBSERVATION_WRONG_DEFAULT_BRANCH", "RULES_OBSERVATION_STALE_OR_FUTURE",
            "RULES_MISSING", "OWNER_APPROVAL_PENDING",
            "OWNER_DECLINED", "POST_ACTION_OBSERVATION_MISSING", "POST_ACTION_OBSERVATION_INVALID",
            "POST_ACTION_OBSERVATION_NOT_LIVE", "POST_ACTION_OBSERVATION_WRONG_REPOSITORY",
            "POST_ACTION_OBSERVATION_WRONG_REPOSITORY_ID",
            "POST_ACTION_OBSERVATION_WRONG_DEFAULT_BRANCH", "POST_ACTION_OBSERVATION_STALE_OR_FUTURE",
            "POST_ACTION_OBSERVATION_NOT_FRESH", "RULES_MISSING_AFTER_ACTION"), uniqueItems=True),
        "provider_mutation_performed": FALSE,
        "execution_authority": FALSE,
    }, allOf=[when("status", "RULES_OBSERVED", {"properties": {
        "operational_blocker": FALSE, "blocker_codes": {"maxItems": 0},
        "final_rules_state": const("APPLIED")}}),
        when("status", "BLOCKED", {"properties": {
            "operational_blocker": TRUE, "blocker_codes": {"minItems": 1}}})])
    schemas["run-disposition-request"] = obj({
        "format": const("awf-run-disposition-request-1"), "request_id": UUID,
        "project_id": text(), "ticket_id": text(), "run_id": UUID,
        "runs_used": integer(1), "run_cap": integer(1),
        "open_findings": STRINGS,
        "recommended_action": enum("continue", "rescope", "park"),
        "recorded_at": TIME, "owner_delivery_required": TRUE})
    schemas["doctor-output"] = obj({
        "format": const("awf-doctor-1"), "template_version": const(VERSION), "root": text(),
        "runtime": obj({"interpreter": text(), "entry_point": text(),
                        "interpreter_exists": BOOL, "entry_point_exists": BOOL}),
        "shell": const("powershell"),
        "commands": arr(obj({"purpose": enum("adoption", "verification", "validation", "status", "operating"),
                             "command": text(), "expected_exit_codes": arr(integer(), 1, uniqueItems=True)}), 5,
                        maxItems=5, allOf=[
                            {"contains": {"properties": {"purpose": const(purpose)}, "required": ["purpose"]},
                             "minContains": 1, "maxContains": 1}
                            for purpose in ("adoption", "verification", "validation", "status", "operating")]),
        "long_argument_transport": obj({"method": const("literal_argv"),
            "detail": const("Every supported value, including long hashes and paths, is passed as one literal argv element with PowerShell single-quote escaping. No generic JSON carrier or response-file transport is implemented.")}),
        "activation_summary": obj({"status": const("EXTENSION_POINT"), "detail": text()}),
        "execution_authority": FALSE})
    gate_results = obj({name: obj({"result": enum("PASS", "FAIL", "N_A"), "evidence": EVIDENCE}) for name in GATE_NAMES})
    gate_pass = {"properties": {"gates": {"properties": {name: {"properties": {"result": const("PASS")}}
                    for name in GATE_NAMES if name != "specialist_reviews"}}, "execution_authority": FALSE}}
    gate_pass["properties"]["gates"]["properties"]["specialist_reviews"] = {"properties": {"result": enum("PASS", "N_A")}}
    schemas["final-gate"] = bound({"candidate": ref("candidate"), "review_submission": ref("review-submission"), "gates": gate_results,
        "record_ids": STRINGS, "required_specialist_domains": STRINGS, "residual_risks": STRINGS,
        "risk_tier": enum(1, 2, 3), "tier_justification": text(), "closure_standard": enum("FULL", "DECLARED_LIMITATIONS"),
        "risk_classification": ref("risk-classification"),
        "accepted_findings": STRINGS,
        "verified_critic_artifact_bindings": arr(CRITIC_ARTIFACT_BINDING, uniqueItems=True),
        "conclusion": enum("READY_FOR_OWNER_AUTHORIZATION", "NOT_READY"),
        "execution_authority": FALSE, "evaluation_mode": const("offline_reference"), "expires_at": TIME},
        allOf=[when("conclusion", "READY_FOR_OWNER_AUTHORIZATION", gate_pass), {
            "if": {"properties": {"required_specialist_domains": {"minItems": 1}, "conclusion": const("READY_FOR_OWNER_AUTHORIZATION")}},
            "then": {"properties": {"gates": {"properties": {"specialist_reviews": {"properties": {"result": const("PASS")}}}}}}}])
    schemas["authorization-request"] = bound({"request_id": UUID, "gate_id": UUID, "gate_hash": DIGEST,
        "candidate": ref("candidate"), "expires_at": TIME, "expected_text": text(), "decision": const("REQUEST_ONLY")})
    schemas["owner-authorization"] = bound({"request_id": UUID, "gate_id": UUID, "gate_hash": DIGEST,
        "source": obj({"channel": const("github_pr_comment"), "event_id": UUID, "comment_id": integer(1),
             "repository_id": integer(1), "pr_number": integer(1), "created_at": TIME, "updated_at": TIME,
             "raw_body": text(), "raw_body_sha256": DIGEST, "actor_id": integer(1), "actor_login": text(),
             "deleted": FALSE, "authentication_evidence": EVIDENCE}),
        "decision": enum("AUTHORIZE", "DENY", "REVOKE"), "parser_version": const("awf-auth-1"),
        "candidate": ref("candidate"), "expires_at": TIME, "execution_authority": FALSE})
    schemas["blocker"] = bound({"reason_code": text(), "summary": text(), "evidence": EVIDENCE,
        "resume_state": enum(*RESUME_STATES),
        "resolved": BOOL, "resolution_evidence": arr(text(format="uri"))})
    schemas["failure"] = bound({"reason_code": text(), "summary": text(), "recoverable": BOOL, "outcome_uncertain": BOOL, "evidence": EVIDENCE})
    schemas["recovery"] = bound({"previous_state": enum(*STATES), "resume_state": enum(*STATES), "blocker_id": UUID,
        "reconciliation_id": UUID, "evidence": EVIDENCE})
    schemas["merge-attempt"] = bound({"operation_id": UUID, "authorization_id": UUID, "candidate": ref("candidate"),
        "status": enum("PROPOSED", "UNKNOWN", "DENIED"), "execution_authority": FALSE, "evidence": EVIDENCE})
    schemas["merge-result"] = bound({"operation_id": UUID, "candidate": ref("candidate"), "merged": BOOL,
        "result_commit_sha": nullable(SHA), "result_tree_sha": nullable(SHA), "observed_base_before": nullable(SHA),
        "authorized_candidate_matched": BOOL, "evidence": EVIDENCE}, allOf=[when("merged", True, {"properties": {"result_commit_sha": SHA, "result_tree_sha": SHA}})])
    schemas["jira-transition"] = bound({"operation_id": UUID, "merge_result_id": nullable(UUID),
        "jira_provider": JIRA_PROVIDER,
        "lifecycle_event": enum(*LIFECYCLE_EVENTS),
        "from_status_id": text(), "to_status_id": text(), "transition_id": text(),
        "status": enum("PROPOSED", "UNKNOWN", "SUCCEEDED", "FAILED"), "evidence": EVIDENCE,
        "read_back": obj({"observed_status": nullable(text()), "observed_actor": nullable(text()), "observed_at": nullable(TIME)})},
        allOf=[when("lifecycle_event", "JIRA_RECONCILED", {"properties": {"merge_result_id": UUID}})])
    signed = {"authorization_request_id": UUID, "owner_source": obj({"channel": const("github_pr_comment"), "comment_id": integer(1),
        "actor_id": integer(1), "actor_login": text(), "raw_body": text(), "raw_body_sha256": DIGEST, "created_at": TIME, "updated_at": TIME})}
    schemas["review-cap-disposition"] = bound({"record_id": DIGEST,
        "decision": enum("MERGE_WITH_NOTES", "PARK", "RESCOPE", "EXTEND_ONE_CYCLE"),
        "critic_artifact_binding": CRITIC_ARTIFACT_BINDING,
        "review_verdict_record_id": UUID, "review_verdict_sha256": DIGEST,
        "open_finding_ids": STRINGS, "notes": text(0), "cycles": integer(), "cap_extensions": integer(),
        "successor_ticket": nullable(text()), **signed, "evidence": EVIDENCE},
        allOf=[when("decision", "RESCOPE", {"properties": {"successor_ticket": text()}})])
    schemas["finding-disposition"] = bound({"finding_id": text(), "decision": enum("ACCEPT_RISK", "REQUIRE_FIX", "NOT_A_DEFECT"),
        "rationale": text(), "head_sha": SHA, **signed, "evidence": EVIDENCE})
    schemas["tier-reassignment"] = bound({"contract_id": UUID, "contract_version": integer(1), "from_tier": enum(1, 2),
        "to_tier": enum(1, 2), "rationale": text(), **signed, "evidence": EVIDENCE})
    schemas["owner-closure"] = bound({"merge_result_id": UUID, "closure_kind": enum("release", "publication", "qualification", "cutover", "production_acceptance", "other"),
        "rationale": text(), **signed, "evidence": EVIDENCE})
    schemas["closeout-record"] = bound({"pr_number": integer(1), "reviewed_head_sha": SHA, "base_sha": SHA,
        "merge_commit_sha": SHA, "merge_tree_sha": SHA,
        "bound_files": arr(obj({"path": text(), "blob_sha": SHA, "sha256": DIGEST}), 1),
        "gate_record_id": UUID, "review_record_ids": arr(UUID, 1, uniqueItems=True),
        "jira_transition_record_id": nullable(UUID), "evidence": EVIDENCE})
    schemas["scope-change"] = bound({"request_id": UUID, "reason": text(), "requested_changes": arr(text(), 1),
        "decision": enum("REQUESTED", "APPROVED", "DENIED"), "owner_ids": arr(integer(1), uniqueItems=True),
        "expires_at": TIME, "evidence": EVIDENCE}, allOf=[when("decision", "APPROVED", {"properties": {"owner_ids": {"minItems": 1}}})])
    schemas["controller-event"] = obj({"schema_version": const(3), "event_id": UUID, "project_id": UUID,
        "sequence": integer(1), "timestamp": TIME, "actor": enum(*ROLES), "event_type": enum(*CONTROLLER_EVENT_TYPES),
        "digest_sha256": nullable(DIGEST),
        "ticket": nullable(text()), "previous_state": nullable(enum(*STATES)), "state": nullable(enum(*STATES)),
        "candidate": nullable(ref("candidate")), "external_event_id": nullable(text()),
        "correlation_id": UUID, "causation_id": nullable(UUID), "payload_hash": DIGEST,
        "previous_hash": DIGEST, "event_hash": DIGEST, "evidence": EVIDENCE}, allOf=[{
            "if": {"properties": {"event_type": enum("MERGED", "MERGE_ATTEMPTED", "OWNER_AUTHORIZED", "JIRA_DONE")}},
            "then": {"properties": {"candidate": ref("candidate"), "ticket": text(), "state": enum(*STATES)}}},
            when("event_type", "evidence_comment", {"properties": {"digest_sha256": DIGEST, "ticket": text()}}),
            when("event_type", "lifecycle_transition", {"properties": {"ticket": text(), "state": enum(*STATES)}})])
    schemas["host-lease"] = obj({"schema_version": const(3), "lease_id": UUID, "project_id": UUID, "task_id": UUID,
        "fencing_token": integer(1), "control_generation": integer(1), "resource_class": enum("standard", "heavy", "gpu"),
        "worker_slots": integer(1), "heavy_slots": integer(), "gpu_slots": integer(), "issued_at": TIME,
        "expires_at": TIME, "status": enum("ACTIVE", "EXPIRED", "RELEASED", "REVOKED"), "evidence": EVIDENCE,
        "resources_held": arr(obj({"name": text(pattern="^[a-z][a-z0-9_]*$"), "slots": integer(1), "from": TIME, "until": TIME}))})
    schemas["reconciliation"] = obj({"schema_version": const(3), "record_id": UUID, "created_at": TIME, "project_id": UUID,
        "state_revision": integer(), "complete": BOOL, "sources": arr(obj({"source": text(), "complete": BOOL, "observed_at": TIME, "evidence": EVIDENCE}), 1),
        "unknown_operation_ids": STRINGS, "orphan_run_ids": STRINGS, "required_actions": STRINGS})
    schemas["checkpoint"] = obj({"schema_version": const(3), "record_id": UUID, "created_at": TIME, "project_id": UUID,
        "sequence": integer(), "event_hash": DIGEST, "snapshot_hash": DIGEST, "database_backup_uri": text(format="uri")})
    schemas["project-state"] = obj({"schema_version": const(3), "project_id": UUID, "refreshed_at": TIME,
        "sequence": integer(), "event_hash": DIGEST, "control_mode": enum("PAUSED", "RUNNING", "STOPPED"),
        "control_generation": integer(1), "tickets": arr(obj({"issue_id": text(), "state": enum(*STATES), "revision": integer()})),
        "pending_operation_ids": STRINGS, "unknown_operation_ids": STRINGS, "active_lease_ids": STRINGS,
        "owner_actions": STRINGS, "authoritative": FALSE})
    activation_check = obj({"code": text(pattern="^[A-Z][A-Z0-9_]*$"), "stage": text(),
        "state": enum("PASS", "INVALID", "MISMATCH", "UNAVAILABLE", "UNOBSERVED", "NOT_APPLICABLE"),
        "evidence": text(), "remedy": text(), "observed_at": TIME})
    capability = obj({"state": enum("AVAILABLE", "UNAVAILABLE", "UNOBSERVED", "NOT_APPLICABLE"),
        "evidence": text(), "observed_at": TIME}, required=["state", "evidence", "observed_at"])
    capability["properties"]["completed_by"] = text()
    schemas["activation-status"] = {"type": "object", "additionalProperties": True,
        "required": ["version", "project_state", "checks", "capabilities", "activation", "execution_authority"],
        "properties": {"version": const(VERSION),
            "project_state": nullable(enum("INSTALLED", "CONFIGURED", "ACTIVE")),
            "checks": arr(activation_check),
            "capabilities": obj({name: capability for name in ["local_work", "external_data_read",
                "branch_publication", "pr_creation", "independent_review", "jira_read", "jira_write", "merge_execution"]}),
            "activation": obj({"blockers": arr(activation_check), "next_command": text()}),
            "execution_authority": FALSE}}
    schemas["evidence-bundle"] = obj({"schema_version": const(3), "candidate": ref("candidate"),
        "snapshot": ref("jira-snapshot"), "contract": ref("ticket-contract"), "dispatch": ref("work-dispatch"),
        "worker": ref("worker-result"), "critic": ref("critic-review"), "specialists": arr(ref("specialist-review")),
        "review_submission": ref("review-submission"),
        "ci": ref("ci-evidence"), "pr": ref("pr-snapshot"), "runs": arr(ref("run-attestation"), 3),
        "prior_findings": arr(FINDING), "finding_dispositions": arr(ref("finding-disposition")),
        "cap_disposition": nullable(ref("review-cap-disposition")), "publication_scan": ref("publication-scan"),
        "tier_classification": nullable(ref("risk-classification")), "review_verdicts": arr(ref("review-verdict"), 1),
        "review_round_receipts": arr(REVIEW_ROUND_RECEIPT),
        "ticketed_p2_records": arr(obj({"finding_id": text(), "ticket_key": text()})),
        "owner_review": nullable(ref("review-verdict")),
        "evidence_registry": arr(obj({"uri": text(format="uri"), "sha256": DIGEST,
            "producer_id": text(), "retained_until": TIME}), 1),
        "provenance_mode": enum("offline_fixture", "production_observation")})
    schemas["evidence-bundle"]["required"].remove("owner_review")
    schemas["evidence-bundle"]["required"].remove("review_round_receipts")
    role_policy = obj({"model": text(), "reasoning_effort": enum("low", "medium", "high", "xhigh", "max", "ultra"),
        "fallback": const("deny"), "approved_model_ids": arr(text(), 1, uniqueItems=True),
        "permission_profile": text(), "network_allowlist": STRINGS})
    schemas["project-config"] = obj({"version": const(3), "template": obj({"expected_workflow_version": const(VERSION)}),
        "project": obj({"id": UUID, "name": text(), "short_name": text()}),
        "jira": obj({"cloud_id": text(), "site": text(format="uri"), "provider_project_id": text(),
          "project_key": text(pattern="^[A-Z][A-Z0-9]*$"), "controller_actor_id": text(),
          "scope": obj({"allow_entire_project": BOOL, "selector_mode": enum("all", "any"),
              "included_epics": STRINGS, "labels_any": STRINGS, "components_any": STRINGS,
              "additional_jql": const(""), "ownership_required": TRUE}),
          "status_map": obj({k: text() for k in ["backlog", "ready", "in_progress", "in_review", "done"]}),
          "lifecycle_writes": obj({k: BOOL for k in ["in_progress", "in_review", "done"]}),
          "owner_closure_keywords": STRINGS,
          "dependency_direction": const("requires"), "mutations_owner": const("controller")}),
        "github": obj({"host": text(format="uri"), "repository_id": integer(1), "repository": text(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
           "expected_actor_id": nullable(integer(1)), "expected_actor_login": nullable(text()),
           "auth_profile": nullable(text()),
           "base_branch": text(), "branch_pattern": text(), "merge_method": enum("merge", "squash", "rebase"),
           "draft_pr_first": TRUE, "one_repository_per_controller": TRUE}),
        "execution": obj({"profile": const("manual_reference"), "max_parallel_tickets": integer(1),
           "child_env_strip_extra": arr(text(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"), 0, uniqueItems=True),
           "native_streams": obj({"enabled": BOOL, "dispatch_policy": const("ready_independent")}),
           "max_parallel_tickets_per_stream": integer(1), "max_agent_runs_per_ticket": integer(1),
           "max_spawn_depth": integer(0, maximum=1), "max_amendment_cycles": integer(1),
           "max_cap_extensions": integer(0, maximum=3),
           "risk_tiers": obj({"tier1_eligible_paths": STRINGS, "tier1_excluded_paths": STRINGS,
               "tier1_review": obj({"roles": arr(enum("critic", "specialist"), 1, uniqueItems=True),
                                    "specialist_when_touching": STRINGS, "findings": const("advisory")}),
               "tier2_review": obj({"roles": arr(enum("critic", "specialist"), 2, uniqueItems=True), "findings": const("blocking")}),
                "tier3_review": obj({"roles": arr(enum("critic", "specialist"), 2, uniqueItems=True), "findings": const("blocking"), "max_rounds": integer(1, maximum=3)})}),
           "transient_retry_limit": integer(0, maximum=5), "max_run_seconds": integer(1),
           "max_tool_calls_per_run": integer(1), "max_tokens_per_ticket": integer(1),
           "max_cost_microusd_per_ticket": nullable(integer(1)), "daily_project_cost_microusd": nullable(integer(1)),
           "one_writer_per_ticket": TRUE, "roles": obj({r: role_policy for r in ["controller", "worker", "critic", "specialist"]}),
           "route_capabilities": obj({"observation_path": text(), "max_age_days": integer(1)}),
           "host_broker": obj({"enabled": BOOL, "broker_id": text(0), "lease_before_dispatch": TRUE,
                              "max_workers": integer(1), "max_heavy_jobs": integer(), "max_gpu_jobs": integer(),
                              "resources": {"type": "object", "propertyNames": {"pattern": "^[a-z][a-z0-9_]*$"},
                                            "additionalProperties": integer(1)}})}),
        "validation": obj({"commands": arr(text(), 1, uniqueItems=True), "ci_candidate_policy": const("synthetic_merge_required"),
          "require_candidate_bound_ci_evidence": TRUE, "required_ci_checks": arr(obj({"name": text(), "app_id": integer(1),
            "workflow_path": text(), "workflow_sha": SHA, "min_tests_executed": integer(1), "verifies_history": BOOL,
            "local_command": text()}), 0),
          "max_evidence_age_seconds": integer(1), "test_timeout_seconds": integer(1)}),
        "scope": obj({"protected_paths": arr(text(), 1, uniqueItems=True), "generated_paths": STRINGS,
            "architecture_docs": STRINGS, "product_docs": STRINGS}),
        "critic": obj({"required": TRUE, "independent_context": TRUE, "blocking_severities": arr(enum("BLOCKER", "MAJOR", "MINOR", "NIT"), 2, uniqueItems=True),
            "require_re_review_after_any_head_change": TRUE, "require_re_review_after_any_target_base_change": TRUE,
            "require_full_final_review": TRUE}),
        "specialist_reviews": {"type": "object", "minProperties": 1, "additionalProperties": obj({"required_when_triggered": TRUE,
            "paths": STRINGS, "keywords": STRINGS, "risk_flags": STRINGS, "reviewer_identity": text()})},
        "merge_gate": obj({"human_authorization_required": TRUE, "execution_after_authorization": const("owner_manual"),
          "automatic_merge_enabled": FALSE, "require_critic_approval_current_tuple": TRUE, "require_specialist_reviews_current_tuple": TRUE,
          "require_required_ci_green": TRUE, "require_zero_unresolved_blocking_threads": TRUE,
          "invalidate_on_head_change": TRUE, "invalidate_on_target_base_change": TRUE,
          "authorization_ttl_seconds": integer(1, maximum=86400), "trusted_owner_ids": arr(integer(1), 0, uniqueItems=True),
          "production_posting_collector_ids": arr(text(), 0, uniqueItems=True),
          "high_risk_owner_quorum": integer(1)}),
        "controller": obj({**{k: FALSE for k in ["dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments"]},
                           "auto_transition_jira": BOOL, "status_cadence_seconds": integer(1)}),
        "audit": obj({"store_must_be_outside_worktrees": TRUE, "retention_days": integer(1), "redact_secrets": TRUE}),
        "portfolio": obj({"read_only": TRUE, "cross_project_dispatch": FALSE})})
    jira = schemas["project-config"]["properties"]["jira"]
    # Current governance keys are optional for upgraded 1.8.9 configurations; the
    # code applies the documented defaults when they are absent. Bootstrap writes them.
    execution = schemas["project-config"]["properties"]["execution"]
    for container, keys in ((execution, ["risk_tiers", "max_cap_extensions", "route_capabilities", "child_env_strip_extra"]),
                            (execution["properties"]["host_broker"], ["resources"]),
                            (jira, ["lifecycle_writes", "owner_closure_keywords"]),
                            (schemas["project-config"]["properties"]["validation"]["properties"]["required_ci_checks"]["items"], ["verifies_history", "local_command"])):
        container["required"] = [key for key in container["required"] if key not in keys]
    execution["properties"]["risk_tiers"]["required"] = [key for key in execution["properties"]["risk_tiers"]["required"]
                                                            if key != "tier3_review"]
    # Null is an explicit installation residue, rejected by semantic acceptance.
    schemas["project-config"]["properties"]["github"]["properties"]["repository_id"] = {"anyOf": [integer(1), {"type": "null"}]}
    github = schemas["project-config"]["properties"]["github"]
    github["required"] = [key for key in github["required"]
                          if key not in {"expected_actor_id", "expected_actor_login", "auth_profile"}]
    jira["properties"]["enabled"] = BOOL
    for key in ("cloud_id", "provider_project_id", "controller_actor_id"):
        jira["properties"][key] = {"anyOf": [text(), {"type": "null"}]}
    jira["properties"]["site"] = {"anyOf": [text(format="uri"), {"type": "null"}]}
    jira["properties"]["project_key"] = {"anyOf": [text(pattern="^[A-Z][A-Z0-9]*$"), {"type": "null"}]}
    jira["allOf"] = [{"if": {"properties": {"enabled": {"const": False}}, "required": ["enabled"]},
        "then": {"properties": {key: {"type": "null"} for key in
                                  ("cloud_id", "site", "provider_project_id", "project_key", "controller_actor_id")}},
        "else": {"properties": {"cloud_id": nullable(text()), "site": text(format="uri"),
                                  "provider_project_id": nullable(text()),
                                  "project_key": text(pattern="^[A-Z][A-Z0-9]*$"),
                                  "controller_actor_id": nullable(text())}}}]
    # Optional for migrated static configurations. Routing has a strict semantic
    # validator in model_routing.py, shared by configuration validation and CLI.
    schemas["project-config"]["properties"]["execution"]["properties"]["model_routing"] = {"type": "object"}
    # New projects derive reviewer count from OPERATING_CONFIG streams.count.
    # A retained explicit legacy count remains binding and must agree; chat
    # operating changes never rewrite this protected governance field.
    schemas["project-config"]["properties"]["execution"]["properties"]["independent_reviewers"] = obj({
        "count": integer(0), "allocation": enum("one_per_stream", "shared_critic")}, required=["allocation"])
    # Optional for upgraded configurations; absence means the documented
    # 15-minute controller status cadence.
    schemas["project-config"]["properties"]["controller"]["required"].remove("status_cadence_seconds")
    # This allowlist can only narrow a runtime-owned registry.  It is optional
    # for migrated configurations and cannot register a collector by itself.
    schemas["project-config"]["properties"]["merge_gate"]["required"].remove(
        "production_posting_collector_ids")
    # Optional and append-only for upgrades: tracked declarations add detectors;
    # built-ins can be tuned only by the ignored operator-local mapping.
    schemas["project-config"]["properties"]["publication"] = obj({
        "deny_literals": arr(text(), uniqueItems=True),
        "deny_regexes": arr(obj({"id": text(pattern="^[a-z][a-z0-9_]*$"), "pattern": text(format="regex")}), uniqueItems=True),
        "internal_hostnames": arr(text(pattern="^[A-Za-z0-9][A-Za-z0-9.-]*$"), uniqueItems=True)})
    # Additive evidence: new producers bind their separately observed operating
    # snapshot; historical records can remain absent/null, never relabelled.
    for name in ("work-dispatch", "specialist-dispatch"):
        schemas[name]["properties"]["operating_hash"] = nullable(DIGEST)
    for name, schema in schemas.items():
        schema.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"urn:awf:1.2:{name}",
                       "title": f"Agentic Workflow 1.2 — {name}"})
    return schemas


def main():
    path = ROOT / ".agentic/schemas"
    path.mkdir(parents=True, exist_ok=True)
    for name, schema in catalog().items():
        (path / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Generated {len(catalog())} schemas")


if __name__ == "__main__":
    main()
