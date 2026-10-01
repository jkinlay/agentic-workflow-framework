"""Strict offline authorization grammar and authenticated webhook utility.

These functions do not fetch comments, issue execution permits, or call merge.
Source freshness/revocation must be reconciled by a certified external adapter.
"""
from __future__ import annotations
import hashlib
import hmac
import re
import uuid

from . import ValidationError
from .canonical import fingerprint, fresh, sha256, timestamp, loads

ORDER = ["request", "gate", "gatehash", "project", "issue", "host", "repoid", "repo", "pr", "branch",
         "head", "base", "tree", "method", "policy", "contract", "requirements", "expires"]


def request_fields(request):
    c, b = request["candidate"], request["binding"]
    return dict(zip(ORDER, [request["request_id"], request["gate_id"], request["gate_hash"], b["project_id"], b["issue_id"],
        c["host"], str(c["repository_id"]), c["repository"], str(c["pr_number"]), c["target_base_branch"],
        c["head_sha"], c["target_base_sha"], c["integration_tree_sha"], c["merge_method"], b["policy_hash"],
        b["contract_hash"], b["requirements_hash"], request["expires_at"]]))


def render_request(request, decision="AUTHORIZE"):
    if decision not in {"AUTHORIZE", "DENY", "REVOKE"}:
        raise ValidationError("Unknown authorization decision")
    fields = request_fields(request)
    if any(not value or any(char.isspace() for char in value) or "=" in value for value in fields.values()):
        raise ValidationError("Authorization field contains unsupported whitespace/delimiter")
    return "AWF1.2 " + decision + " " + " ".join(f"{key}={fields[key]}" for key in ORDER)


def parse_text(raw):
    if not isinstance(raw, str) or len(raw) > 4096 or raw != raw.strip() or "\n" in raw or "\r" in raw:
        raise ValidationError("Authorization must be one exact unquoted line")
    pieces = raw.split(" ")
    if len(pieces) != 2 + len(ORDER) or pieces[0] != "AWF1.2" or pieces[1] not in {"AUTHORIZE", "DENY", "REVOKE"}:
        raise ValidationError("Authorization grammar mismatch")
    values = {}
    for expected, token in zip(ORDER, pieces[2:]):
        key, separator, value = token.partition("=")
        if key != expected or separator != "=" or not value or any(c.isspace() for c in value):
            raise ValidationError("Authorization field order/value mismatch")
        values[key] = value
    return pieces[1], values


def make_request(gate, contracts, now):
    contracts.validate("final-gate", gate)
    if gate["conclusion"] != "READY_FOR_OWNER_AUTHORIZATION" or timestamp(gate["expires_at"]) <= timestamp(now):
        raise ValidationError("Cannot request authorization for a failed or expired gate")
    request = {key: gate[key] for key in ["schema_version", "binding", "candidate", "expires_at"]}
    request.update(record_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), gate_id=gate["record_id"],
        gate_hash=fingerprint("gate", gate), created_at=now, producer_id="offline-reference-evaluator", run_id=str(uuid.uuid4()),
        expected_text="pending", decision="REQUEST_ONLY")
    request["expected_text"] = render_request(request)
    contracts.validate("authorization-request", request)
    return request


def verify_record(record, request, gate, config, contracts, now):
    from .policy import validate_config
    from .lifecycle import definition
    current_policy = validate_config(config, definition(), contracts)
    contracts.validate("owner-authorization", record)
    contracts.validate("authorization-request", request)
    contracts.validate("final-gate", gate)
    if request["gate_hash"] != fingerprint("gate", gate) or request["gate_id"] != gate["record_id"]:
        raise ValidationError("Authorization request references a different gate")
    if gate["conclusion"] != "READY_FOR_OWNER_AUTHORIZATION":
        raise ValidationError("Gate is not ready")
    if gate["binding"]["policy_hash"] != current_policy or gate["binding"]["project_id"] != config["project"]["id"] or gate["binding"]["repository_id"] != config["github"]["repository_id"]:
        raise ValidationError("Authorization uses a different project or superseded policy")
    if request["expires_at"] != gate["expires_at"] or (timestamp(gate["expires_at"]) - timestamp(gate["created_at"])).total_seconds() > config["merge_gate"]["authorization_ttl_seconds"]:
        raise ValidationError("Authorization expiry exceeds its gate or policy")
    fresh(gate["created_at"], now, config["merge_gate"]["authorization_ttl_seconds"])
    for field in ["request_id", "gate_id", "gate_hash", "candidate", "binding", "expires_at"]:
        if record[field] != request[field]:
            raise ValidationError(f"Authorization binding mismatch: {field}")
    if request["binding"] != gate["binding"] or request["candidate"] != gate["candidate"]:
        raise ValidationError("Gate/request candidate or policy mismatch")
    if request["expected_text"] != render_request(request):
        raise ValidationError("Request render does not match its fields")
    if timestamp(now) >= timestamp(request["expires_at"]) or timestamp(request["created_at"]) < timestamp(gate["created_at"]):
        raise ValidationError("Expired or temporally invalid request")
    source = record["source"]
    if source["actor_id"] not in config["merge_gate"]["trusted_owner_ids"]:
        raise ValidationError("Untrusted numeric owner identity")
    if source["repository_id"] != request["candidate"]["repository_id"] or source["pr_number"] != request["candidate"]["pr_number"]:
        raise ValidationError("Comment came from a different repository or PR")
    if source["created_at"] != source["updated_at"]:
        raise ValidationError("Edited authorization comments are invalid; create a new request")
    if timestamp(source["created_at"]) < timestamp(request["created_at"]):
        raise ValidationError("Comment predates the single-use request")
    fresh(source["updated_at"], now, config["merge_gate"]["authorization_ttl_seconds"])
    if sha256(source["raw_body"].encode("utf-8")) != source["raw_body_sha256"]:
        raise ValidationError("Authorization raw-body digest mismatch")
    decision, fields = parse_text(source["raw_body"])
    if fields != request_fields(request) or decision != record["decision"]:
        raise ValidationError("Raw authorization and recorded fields disagree")
    if record["execution_authority"] is not False:
        raise ValidationError("Offline verification cannot grant execution authority")
    return {"decision": decision, "record_consistent": True, "live_source_verified": False, "owner_quorum_verified": False, "execution_authority": False}


# Owner records other than merge authorization (1.9.3): finding and cap
# dispositions, tier reassignment and owner closure use the same one-line
# grammar, bound to the record, candidate head, contract and project. A record
# whose raw body does not render exactly from its fields is refused.
OWNER_KINDS = {"finding-disposition": "finding", "review-cap-disposition": "cap",
               "tier-reassignment": "tier", "owner-closure": "closure"}
OWNER_ORDER = ["kind", "record", "request", "decision", "subject", "head", "contract", "project"]


def owner_fields(record, kind, head_sha):
    if kind not in OWNER_KINDS.values():
        raise ValidationError("Unknown owner record kind")
    if kind == "finding":
        decision, subject = record["decision"], record["finding_id"]
        head = record["head_sha"]
    elif kind == "cap":
        ids = sorted(record["open_finding_ids"])
        if any("+" in item or "/" in item or ":" in item for item in ids):
            raise ValidationError("Finding IDs in a cap disposition cannot contain '+', '/' or ':'")
        # cycles/extensions are part of the signed text so a cap decision binds the cap it answers.
        decision = record["decision"]
        subject = f"{record['cycles']}/{record['cap_extensions']}:" + ("+".join(ids) if ids else "none")
        head = head_sha
    elif kind == "tier":
        decision, subject, head = f"{record['from_tier']}-to-{record['to_tier']}", record["contract_id"], head_sha
    else:
        decision, subject, head = record["closure_kind"], record["merge_result_id"], head_sha
    return dict(zip(OWNER_ORDER, [kind, record["record_id"], record["authorization_request_id"], decision, subject, head,
                                  record["binding"]["contract_hash"], record["binding"]["project_id"]]))


def render_owner_record(record, kind, head_sha):
    fields = owner_fields(record, kind, head_sha)
    if any(not value or any(char.isspace() for char in value) or "=" in value for value in fields.values()):
        raise ValidationError("Owner record field contains unsupported whitespace/delimiter")
    return "AWF1.2 DISPOSE " + " ".join(f"{key}={fields[key]}" for key in OWNER_ORDER)


def parse_owner_text(raw):
    if not isinstance(raw, str) or len(raw) > 4096 or raw != raw.strip() or "\n" in raw or "\r" in raw:
        raise ValidationError("Owner record must be one exact unquoted line")
    pieces = raw.split(" ")
    if len(pieces) != 2 + len(OWNER_ORDER) or pieces[0] != "AWF1.2" or pieces[1] != "DISPOSE":
        raise ValidationError("Owner record grammar mismatch")
    values = {}
    for expected, token in zip(OWNER_ORDER, pieces[2:]):
        key, separator, value = token.partition("=")
        if key != expected or separator != "=" or not value or any(c.isspace() for c in value):
            raise ValidationError("Owner record field order/value mismatch")
        values[key] = value
    return values


def verify_owner_record(record, schema, config, contracts, now, *, head_sha, binding, runs=None, excluded_contexts=(), excluded_producers=()):
    """Authenticate the shape, digest, grammar, trusted actor and producer of an owner record.

    Offline verification establishes consistency; the trusted host verifies the live comment.
    """
    kind = OWNER_KINDS.get(schema)
    if kind is None:
        raise ValidationError("Not an owner record schema")
    contracts.validate(schema, record)
    if record["binding"] != binding:
        raise ValidationError(f"{schema} binding differs from the evaluated candidate")
    source = record["owner_source"]
    if source["actor_id"] not in config["merge_gate"]["trusted_owner_ids"]:
        raise ValidationError(f"{schema}: untrusted numeric owner identity {source['actor_id']}")
    if sha256(source["raw_body"].encode("utf-8")) != source["raw_body_sha256"]:
        raise ValidationError(f"{schema}: raw-body digest mismatch")
    fields = parse_owner_text(source["raw_body"])
    if fields != owner_fields(record, kind, head_sha):
        raise ValidationError(f"{schema}: raw owner text and recorded fields disagree")
    if timestamp(source["created_at"]) > timestamp(record["created_at"]):
        raise ValidationError(f"{schema}: comment postdates its record")
    if source.get("updated_at", source["created_at"]) != source["created_at"]:
        raise ValidationError(f"{schema}: edited owner comments are invalid; record a new comment")
    fresh(record["created_at"], now, config["validation"]["max_evidence_age_seconds"])
    if runs is not None:
        run = runs.get(record["run_id"])
        if run is None or run["role"] != "verifier" or run["producer_id"] != record["producer_id"]:
            raise ValidationError(f"{schema}: producer is not a registered verifier run")
        excluded_contexts, excluded_producers = set(excluded_contexts or ()), set(excluded_producers or ())
        if run["context_id"] in excluded_contexts or run["producer_id"] in excluded_producers:
            raise ValidationError(f"{schema}: verifier shares an implementation or review context or producer")
        # Follow the parent chain: a child of an excluded run is not independent.
        seen, parent = set(), run.get("parent_run_id")
        while parent is not None and parent not in seen:
            seen.add(parent)
            ancestor = runs.get(parent)
            if ancestor is None:
                raise ValidationError(f"{schema}: verifier lineage names an unregistered run")
            if ancestor["context_id"] in excluded_contexts or ancestor["producer_id"] in excluded_producers:
                raise ValidationError(f"{schema}: verifier descends from an implementation or review run")
            parent = ancestor.get("parent_run_id")
    return {"kind": kind, "decision": fields["decision"], "record_consistent": True, "live_source_verified": False,
            "execution_authority": False}


def verify_webhook(secret: bytes, body: bytes, signature: str, delivery_id: str):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValidationError("Webhook secret must contain at least 32 bytes")
    if len(body) > 8 * 1024 * 1024:
        raise ValidationError("Webhook exceeds configured size limit")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature or ""):
        raise ValidationError("Missing/invalid webhook signature")
    if str(uuid.UUID(delivery_id)) != delivery_id:
        raise ValidationError("Delivery identity must be a canonical UUID")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValidationError("Webhook signature does not match raw bytes")
    return loads(body.decode("utf-8"))
