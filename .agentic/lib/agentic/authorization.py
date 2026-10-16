"""Strict offline authorization grammar and authenticated webhook utility.

These functions do not fetch comments, issue execution permits, or call merge.
Source freshness/revocation must be reconciled by a certified external adapter.
"""
from __future__ import annotations
import copy
import hashlib
import hmac
import re
import uuid
from urllib.parse import quote

from . import ValidationError
from .canonical import (fingerprint, fresh, loads, reject_git_sha_candidate_ids,
                        sha256, timestamp)

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
    reject_git_sha_candidate_ids(gate)
    contracts.validate("final-gate", gate)
    from .review_completion import validate_ready_gate_completion
    validate_ready_gate_completion(gate)
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
    from .review_completion import validate_ready_gate_completion
    validate_ready_gate_completion(gate)
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
READY_AUTHORIZATION_FORMAT = "awf-pr-ready-authorization-2"
READY_AUTHORIZATION_REQUEST_FORMAT = "awf-pr-ready-authorization-request-1"
READY_AUTHORIZATION_ROLES = {"manager", "owner"}
READY_AUTHORIZATION_ORDER = ["request", "action", "role", "repoid", "pr", "head", "requested", "expires"]


def trusted_owner_ids(config, *, allow_registered_legacy_overlap=False):
    """Return the reviewed numeric owner allowlist or fail closed."""
    try:
        owners = config["merge_gate"]["trusted_owner_ids"]
        automation_actor = config["github"]["expected_actor_id"]
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            "$.merge_gate.trusted_owner_ids and $.github.expected_actor_id: "
            "reviewed owner and automation actor configuration is required"
        ) from exc
    if (not isinstance(owners, list) or not owners
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 1
                   for item in owners)
            or len(owners) != len(set(owners))):
        raise ValidationError(
            "$.merge_gate.trusted_owner_ids: configure unique positive numeric owner IDs"
        )
    if (isinstance(automation_actor, bool) or not isinstance(automation_actor, int)
            or automation_actor < 1):
        raise ValidationError(
            "$.github.expected_actor_id: configure one positive numeric automation actor ID"
        )
    if automation_actor in owners and not allow_registered_legacy_overlap:
        raise ValidationError(
            "The configured automation actor must not be a trusted owner"
        )
    return tuple(owners)


def cap_disposition_record_id(record):
    """Bind a cap disposition identity to its artifact, verdict, and successor."""
    try:
        fields = {
            "critic_artifact_binding": record["critic_artifact_binding"],
            "review_verdict_record_id": record["review_verdict_record_id"],
            "review_verdict_sha256": record["review_verdict_sha256"],
            "successor_ticket": record["successor_ticket"],
        }
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            "Review cap disposition is missing its artifact, verdict, or successor binding"
        ) from exc
    return fingerprint("review-cap-artifact-binding", fields)


def _canonical_uuid(value, field):
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a canonical UUID")
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{field} must be a canonical UUID") from exc
    if parsed != value:
        raise ValidationError(f"{field} must be a canonical UUID")
    return value


def _verify_owner_record_candidate(record, kind, head_sha):
    if kind in {"finding", "cap"}:
        if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValidationError("Owner disposition candidate head must be a full lowercase Git SHA")
    elif head_sha != "-":
        raise ValidationError("Tier and closure dispositions must use head=-")
    if kind == "finding" and record.get("head_sha") != head_sha:
        raise ValidationError("Finding disposition head differs from the current candidate head")
    elif kind == "cap":
        artifact = record.get("critic_artifact_binding")
        if not isinstance(artifact, dict) or artifact.get("head_sha") != head_sha:
            raise ValidationError("Review cap disposition critic artifact head differs from the current candidate head")
        if record.get("record_id") != cap_disposition_record_id(record):
            raise ValidationError(
                "Review cap disposition record ID does not authenticate its critic artifact and verdict binding"
            )


def make_owner_disposition_request(record, schema, config, *, head_sha,
                                   authorization_request_id=None):
    """Render non-authoritative fields for later provider verification."""
    kind = OWNER_KINDS.get(schema)
    if kind is None:
        raise ValidationError("Not an owner disposition record type")
    trusted_owner_ids(config)
    if not isinstance(record, dict):
        raise ValidationError("Owner disposition request fields must be an object")
    if "owner_source" in record:
        raise ValidationError("A disposition request must precede the owner comment")
    fields = copy.deepcopy(record)
    existing = fields.get("authorization_request_id")
    if existing is not None and authorization_request_id is not None and existing != authorization_request_id:
        raise ValidationError("Disposition authorization request ID conflicts with the supplied request ID")
    request_id = existing or authorization_request_id or str(uuid.uuid4())
    fields["authorization_request_id"] = _canonical_uuid(
        request_id, "authorization_request_id"
    )
    _verify_owner_record_candidate(fields, kind, head_sha)
    expected_text = render_owner_record(fields, kind, head_sha)
    return {
        "format": "awf-owner-disposition-request-1",
        "record_type": schema,
        "record_id": fields["record_id"],
        "authorization_request_id": request_id,
        "head_sha": head_sha,
        "expected_text": expected_text,
        "disposition_fields": fields,
        "execution_authority": False,
    }


def _ready_request_shape(request):
    required = {"format", "request_id", "role", "action", "repository_id", "pr", "head_sha",
                "requested_at", "expires_at"}
    if not isinstance(request, dict) or set(request) != required:
        raise ValidationError("Ready authorization request has invalid, missing or unexpected fields")
    if (request["format"] != READY_AUTHORIZATION_REQUEST_FORMAT or
            request["role"] not in READY_AUTHORIZATION_ROLES or request["action"] != "MARK_READY"):
        raise ValidationError("Ready authorization request action or role is invalid")
    if (not isinstance(request["request_id"], str) or not request["request_id"].strip() or
            isinstance(request["repository_id"], bool) or not isinstance(request["repository_id"], int) or
            request["repository_id"] < 1 or isinstance(request["pr"], bool) or
            not isinstance(request["pr"], int) or request["pr"] < 1 or
            not isinstance(request["head_sha"], str) or
            not re.fullmatch(r"[0-9a-f]{40}", request["head_sha"])):
        raise ValidationError("Ready authorization request identity or candidate shape is invalid")
    requested = timestamp(request["requested_at"])
    expires = timestamp(request["expires_at"])
    if requested >= expires:
        raise ValidationError("Ready authorization request must expire after it is issued")
    return requested, expires


def make_ready_authorization_request(*, repository_id, pr, head_sha, role, request_id,
                                     requested_at, expires_at):
    """Create the complete owner-signable request before provider publication."""
    request = {
        "format": READY_AUTHORIZATION_REQUEST_FORMAT,
        "request_id": request_id,
        "role": role,
        "action": "MARK_READY",
        "repository_id": repository_id,
        "pr": pr,
        "head_sha": head_sha,
        "requested_at": requested_at,
        "expires_at": expires_at,
    }
    _ready_request_shape(request)
    return request


def ready_authorization_config_sha256(config):
    """Bind host acceptance to the exact configuration supplied for verification."""
    if not isinstance(config, dict):
        raise ValidationError("Ready authorization configuration must be an object")
    return fingerprint("ready_authorization_config", config)


def ready_authorization_fields(request):
    """Return the exact fields signed by a ready-for-review authorization."""
    _ready_request_shape(request)
    return dict(zip(READY_AUTHORIZATION_ORDER, [request["request_id"], request["action"], request["role"],
        str(request["repository_id"]), str(request["pr"]), request["head_sha"], request["requested_at"],
        request["expires_at"]]))


def render_ready_authorization(request):
    fields = ready_authorization_fields(request)
    if any(not isinstance(value, str) or not value or any(char.isspace() for char in value) or "=" in value
           for value in fields.values()):
        raise ValidationError("Ready authorization field contains unsupported whitespace/delimiter")
    return "AWF1.2 AUTHORIZE_READY " + " ".join(f"{key}={fields[key]}" for key in READY_AUTHORIZATION_ORDER)


def parse_ready_authorization(raw):
    if not isinstance(raw, str) or len(raw) > 4096 or raw != raw.strip() or "\n" in raw or "\r" in raw:
        raise ValidationError("Ready authorization must be one exact unquoted line")
    pieces = raw.split(" ")
    if (len(pieces) != 2 + len(READY_AUTHORIZATION_ORDER) or pieces[0] != "AWF1.2" or
            pieces[1] != "AUTHORIZE_READY"):
        raise ValidationError("Ready authorization grammar mismatch")
    values = {}
    for expected, token in zip(READY_AUTHORIZATION_ORDER, pieces[2:]):
        key, separator, value = token.partition("=")
        if key != expected or separator != "=" or not value or any(character.isspace() for character in value):
            raise ValidationError("Ready authorization field order/value mismatch")
        values[key] = value
    return values


def verify_ready_authorization(record, config, *, pr, head_sha, verify_owner_source=None):
    """Verify a trusted, fresh owner/manager authorization for one PR head.

    The schema/template for this new record type is generator-owned.  Until it
    is generated, this function performs closed Python shape validation.  A
    host-injected observer must also re-read the immutable provider object and
    bind an accepted configuration and its trusted observation clock.
    """
    required = {"format", "request", "owner_source"}
    if not isinstance(record, dict) or set(record) != required:
        raise ValidationError("Ready authorization has invalid, missing or unexpected fields")
    if record["format"] != READY_AUTHORIZATION_FORMAT:
        raise ValidationError("Ready authorization format is invalid")
    request = record["request"]
    requested_at, expires_at = _ready_request_shape(request)

    source = record["owner_source"]
    source_fields = {"channel", "comment_id", "actor_id", "actor_login", "repository_id", "pr_number",
                     "created_at", "updated_at", "raw_body", "raw_body_sha256"}
    if not isinstance(source, dict) or set(source) != source_fields:
        raise ValidationError("Ready authorization source has invalid, missing or unexpected fields")
    if (source["channel"] != "github_pr_comment" or isinstance(source["comment_id"], bool) or
            not isinstance(source["comment_id"], int) or source["comment_id"] < 1 or
            isinstance(source["actor_id"], bool) or not isinstance(source["actor_id"], int) or
            source["actor_id"] < 1 or not isinstance(source["actor_login"], str) or
            not source["actor_login"].strip() or not isinstance(source["raw_body"], str) or
            not isinstance(source["raw_body_sha256"], str) or
            not re.fullmatch(r"[0-9a-f]{64}", source["raw_body_sha256"])):
        raise ValidationError("Ready authorization source identity or integrity shape is invalid")
    timestamp(source["created_at"])
    timestamp(source["updated_at"])

    try:
        repository_id = config["github"]["repository_id"]
        trusted_owner_ids = config["merge_gate"]["trusted_owner_ids"]
        ttl = config["merge_gate"]["authorization_ttl_seconds"]
    except (KeyError, TypeError) as exc:
        raise ValidationError("Ready authorization requires reviewed repository and owner configuration") from exc
    if (isinstance(repository_id, bool) or not isinstance(repository_id, int) or repository_id < 1 or
            not isinstance(trusted_owner_ids, list) or isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1):
        raise ValidationError("Ready authorization configuration is invalid")
    if source["actor_id"] not in trusted_owner_ids:
        raise ValidationError("Ready authorization uses an untrusted numeric owner/manager identity")
    if request["repository_id"] != repository_id or source["repository_id"] != repository_id:
        raise ValidationError("Ready authorization came from a different repository")
    if request["pr"] != pr or request["head_sha"] != head_sha:
        raise ValidationError("Ready authorization references a different PR or head")
    if source["pr_number"] != pr:
        raise ValidationError("Ready authorization comment came from a different PR")
    if source["created_at"] != source["updated_at"]:
        raise ValidationError("Edited ready authorization comments are invalid; create a new authorization")
    if sha256(source["raw_body"].encode("utf-8")) != source["raw_body_sha256"]:
        raise ValidationError("Ready authorization raw-body digest mismatch")
    if parse_ready_authorization(source["raw_body"]) != ready_authorization_fields(request):
        raise ValidationError("Raw ready authorization and recorded fields disagree")
    created_at = timestamp(source["created_at"])
    if created_at < requested_at or created_at >= expires_at:
        raise ValidationError("Ready authorization comment is outside its pre-issued request window")
    if (expires_at - requested_at).total_seconds() > ttl:
        raise ValidationError("Ready authorization request exceeds the configured authorization lifetime")
    if not callable(verify_owner_source):
        raise ValidationError("Ready authorization requires authenticated provider observation")

    config_sha256 = ready_authorization_config_sha256(config)
    observation_request = {
        "purpose": "MARK_PR_READY",
        "request_id": request["request_id"],
        "repository_id": repository_id,
        "pr": pr,
        "head_sha": head_sha,
        "raw_body_sha256": source["raw_body_sha256"],
        "configuration_sha256": config_sha256,
    }
    try:
        observed = verify_owner_source(copy.deepcopy(source), copy.deepcopy(observation_request))
    except Exception as exc:
        raise ValidationError("Ready authorization provider observation failed") from exc
    expected_observation = {
        "status", "source_id", "actor_id", "actor_login", "repository_id", "pr_number",
        "created_at", "updated_at", "raw_body_sha256", "observed_at", "configuration_status",
        "configuration_sha256",
    }
    if not isinstance(observed, dict) or set(observed) != expected_observation:
        raise ValidationError("Ready authorization provider observation has invalid fields")
    for field in ("actor_id", "actor_login", "repository_id", "pr_number", "created_at", "updated_at",
                  "raw_body_sha256"):
        if observed[field] != source[field]:
            raise ValidationError("Ready authorization provider observation differs from the recorded source")
    if (observed["status"] != "VERIFIED" or not isinstance(observed["source_id"], str) or
            not observed["source_id"].strip() or observed["configuration_status"] != "ACCEPTED" or
            observed["configuration_sha256"] != config_sha256):
        raise ValidationError("Ready authorization provider/configuration observation is not accepted")
    observed_at = timestamp(observed["observed_at"])
    if observed_at < timestamp(source["updated_at"]) or observed_at >= expires_at:
        raise ValidationError("Ready authorization trusted observation time is outside the request window")
    return {"action": "MARK_READY", "role": request["role"], "actor_id": source["actor_id"],
            "actor_login": source["actor_login"], "source_id": observed["source_id"],
            "observed_at": observed["observed_at"], "configuration_sha256": config_sha256,
            "raw_body_sha256": source["raw_body_sha256"], "record_consistent": True,
            "live_source_verified": True, "configuration_accepted": True,
            "trusted_time_verified": True, "execution_authority": False}


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
        # Bind the cap answered and any RESCOPE successor into the signed text.
        decision = record["decision"]
        successor = (quote(record["successor_ticket"], safe="")
                     if record["successor_ticket"] is not None else "-")
        subject = (f"{record['cycles']}/{record['cap_extensions']}:"
                   + ("+".join(ids) if ids else "none") + f"/{successor}")
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


def verify_owner_record(record, schema, config, contracts, now, *, head_sha, binding,
                        runs=None, excluded_contexts=(), excluded_producers=(),
                        allow_historical_finding=False):
    """Authenticate the shape, digest, grammar, trusted actor and producer of an owner record.

    Offline verification establishes consistency; the trusted host verifies the live comment.
    """
    kind = OWNER_KINDS.get(schema)
    if kind is None:
        raise ValidationError("Not an owner record schema")
    contracts.validate(schema, record)
    historical_finding = (allow_historical_finding and kind == "finding"
                          and record.get("head_sha") != head_sha)
    historical_binding_matches = (historical_finding
        and all(record["binding"].get(key) == value
                for key, value in binding.items() if key != "candidate_id"))
    if record["binding"] != binding and not historical_binding_matches:
        raise ValidationError(f"{schema} binding differs from the evaluated candidate")
    signed_head = head_sha
    if historical_finding:
        # A head change voids a finding disposition; it does not erase the
        # authenticated historical record.  Verify the exact head the owner
        # signed, then let review_policy.effective_dispositions ignore it.
        signed_head = record.get("head_sha")
    _verify_owner_record_candidate(record, kind, signed_head)
    source = record["owner_source"]
    # A registered verifier run can re-evaluate retained legacy evidence for a
    # fail-closed gate result. New request issuance and standalone disposition
    # verification never permit the automation/owner overlap.
    if source["actor_id"] not in trusted_owner_ids(
            config, allow_registered_legacy_overlap=runs is not None):
        raise ValidationError(f"{schema}: untrusted numeric owner identity {source['actor_id']}")
    if sha256(source["raw_body"].encode("utf-8")) != source["raw_body_sha256"]:
        raise ValidationError(f"{schema}: raw-body digest mismatch")
    fields = parse_owner_text(source["raw_body"])
    if fields != owner_fields(record, kind, signed_head):
        raise ValidationError(f"{schema}: raw owner text and recorded fields disagree")
    if timestamp(source["created_at"]) > timestamp(record["created_at"]):
        raise ValidationError(f"{schema}: comment postdates its record")
    if source.get("updated_at", source["created_at"]) != source["created_at"]:
        raise ValidationError(f"{schema}: edited owner comments are invalid; record a new comment")
    if not historical_finding:
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
    return {"kind": kind, "decision": fields["decision"], "expected_text": source["raw_body"],
            "authorization_request_id": record["authorization_request_id"],
            "record_consistent": True, "live_source_verified": False,
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
