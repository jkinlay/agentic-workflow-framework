"""Merge-train 54A contracts, admission, ordering, and invalidation.

The module is deliberately library-only.  It cannot push refs, create or edit
pull requests, authorize a batch merge, merge a target, or write Jira.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import re

from . import ValidationError
from .canonical import canonical, fingerprint, fresh, timestamp


SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_EVIDENCE_AGE_SECONDS = 1800
OBSERVER_NAMESPACE = "awf-merge-train-observer-v1"
PUBLISHER_NAMESPACE = "awf-merge-train-publisher-v1"
PUBLISHER_CAPABILITIES = (
    "PUSH_BATCH_REF", "CREATE_DRAFT_PR", "MARK_DRAFT_PR_READY")


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _identifier(value, field):
    _require(isinstance(value, str) and value and value == value.strip(),
             f"{field} must be a nonempty trimmed string")
    _require(not any(ord(character) < 32 or ord(character) == 127
                     for character in value),
             f"{field} contains control characters")
    return value


def _sha(value, field):
    _require(isinstance(value, str) and SHA.fullmatch(value) is not None,
             f"{field} must be a full lowercase Git SHA")
    return value


def _digest(value, field):
    _require(isinstance(value, str) and DIGEST.fullmatch(value) is not None,
             f"{field} must be lowercase 64-hex")
    return value


def _positive(value, field):
    _require(type(value) is int and value > 0,
             f"{field} must be a positive integer")
    return value


def _is_owner_identity(value):
    return ((isinstance(value, str) and bool(value))
            or (type(value) is int and value > 0))


def _trusted_owner_identity_set(values):
    _require(isinstance(values, (list, tuple, set, frozenset))
             and all(_is_owner_identity(value) for value in values),
             "trusted_owner_ids must be an identity collection")
    return set(values)


def _portable_paths(values, field):
    _require(isinstance(values, (list, tuple, set, frozenset)),
             f"{field} must be a path collection")
    normalized = []
    for value in values:
        _require(isinstance(value, str) and value and "\\" not in value
                 and not value.startswith(("/", "./")) and ":" not in value
                 and all(part not in {"", ".", ".."}
                         for part in value.split("/")),
                 f"{field} contains a non-portable repository path")
        normalized.append(value)
    _require(len(normalized) == len(set(normalized)),
             f"{field} contains duplicate paths")
    return tuple(sorted(normalized))


def default_enabled(*, is_awf_repository):
    """Apply Q2=B: adopters default on while the AWF repository defaults off."""
    _require(type(is_awf_repository) is bool,
             "is_awf_repository must be Boolean")
    return not is_awf_repository


def _provider_identity(value, field="provider identity"):
    _require(isinstance(value, dict)
             and set(value) == {"host", "repository", "repository_id"},
             f"{field} is invalid")
    _identifier(value["host"], f"{field} host")
    _identifier(value["repository"], f"{field} repository")
    _positive(value["repository_id"], f"{field} repository_id")
    return deepcopy(value)


def _owner_review_required(risk_tier):
    """Derive the normal final-gate owner-review policy from the source tier."""
    _require(type(risk_tier) is int and risk_tier in {1, 2, 3},
             "source risk_tier must be 1, 2, or 3")
    return risk_tier == 3


def _validate_source(source):
    fields = {"pr_number", "ticket", "head_sha", "tree_sha", "base_branch",
              "base_sha", "depends_on", "commit_range", "risk_tier",
              "normal_final_gate_requires_owner_review"}
    _require(isinstance(source, dict) and set(source) == fields,
             "source fields differ from the closed merge-train record shape")
    normalized = deepcopy(source)
    _positive(normalized["pr_number"], "source pr_number")
    _identifier(normalized["ticket"], "source ticket")
    _sha(normalized["head_sha"], "source head_sha")
    _sha(normalized["tree_sha"], "source tree_sha")
    _identifier(normalized["base_branch"], "source base_branch")
    _sha(normalized["base_sha"], "source base_sha")
    dependencies = normalized["depends_on"]
    _require(isinstance(dependencies, list)
             and all(type(value) is int and value > 0 for value in dependencies)
             and len(dependencies) == len(set(dependencies))
             and normalized["pr_number"] not in dependencies,
             "source depends_on must contain unique other positive PR numbers")
    commits = normalized["commit_range"]
    _require(isinstance(commits, list) and commits
             and len(commits) == len(set(commits)),
             "source commit_range must be a nonempty unique list")
    for commit in commits:
        _sha(commit, "source commit_range SHA")
    _require(commits[-1] == normalized["head_sha"],
             "source head_sha must end its commit_range")
    owner_review_required = _owner_review_required(normalized["risk_tier"])
    _require(type(normalized["normal_final_gate_requires_owner_review"]) is bool,
             "source owner-review gate flag must be Boolean")
    _require(normalized["normal_final_gate_requires_owner_review"]
             is owner_review_required,
             (f"Tier {normalized['risk_tier']} normal final gate "
              + ("requires" if owner_review_required else "does not require")
              + " owner review"))
    normalized["normal_final_gate_requires_owner_review"] = (
        owner_review_required)
    return normalized


def source_admission_request(source, target_branch, expected_provider_identity):
    """Create the exact immutable selector used for admission observation."""
    source = _validate_source(source)
    _identifier(target_branch, "target_branch")
    expected_provider_identity = _provider_identity(
        expected_provider_identity, "expected provider identity")
    if not source["depends_on"]:
        _require(source["base_branch"] == target_branch,
                 "independent source base must match the train target branch")
    request = {
        "operation": "SOURCE_ADMISSION",
        "provider_identity": expected_provider_identity,
        "target_branch": target_branch,
        "source": source,
    }
    request["request_id"] = fingerprint("merge_train_source_admission_request",
                                        request)
    return request


def _validate_registration(registration, capability):
    fields = {"producer_kind", "implementation_sha256", "release_sha256",
              "capabilities", "provider_identity", "receipt_namespace",
              "observer", "receipts"}
    _require(isinstance(registration, dict) and set(registration) == fields,
             "merge-train observer registration has a non-closed shape")
    _require(registration["producer_kind"] == "merge_train_observer",
             "registered producer is not a merge-train observer")
    _digest(registration["implementation_sha256"], "observer implementation_sha256")
    _digest(registration["release_sha256"], "observer release_sha256")
    capabilities = registration["capabilities"]
    _require(isinstance(capabilities, list) and capabilities
             and all(isinstance(value, str) and value for value in capabilities)
             and len(capabilities) == len(set(capabilities)),
             "merge-train observer capabilities are invalid")
    _require(capability in capabilities,
             f"merge-train observer lacks {capability} capability")
    _provider_identity(registration["provider_identity"],
                       "merge-train observer provider identity")
    _require(registration["receipt_namespace"] == OBSERVER_NAMESPACE,
             "merge-train observer receipt namespace is invalid")
    _require(callable(registration["observer"])
             and isinstance(registration["receipts"], dict),
             "merge-train observer runtime registration is invalid")
    return registration


def provider_receipt_sha256(registration, request, observation):
    """Bind registered implementation/release bytes to exact request and result."""
    capability = request.get("operation") if isinstance(request, dict) else None
    _validate_registration(registration, capability)
    return fingerprint("merge_train_provider_receipt", {
        "producer_id": observation["producer_id"],
        "observation_id": observation["observation_id"],
        "observed_at": observation["observed_at"],
        "request_sha256": observation["request_sha256"],
        "result_sha256": fingerprint(
            "merge_train_provider_result", observation["result"]),
        "operation": capability,
        "registration_trust": _registration_trust(
            registration, capability),
    })


def _registration_trust(registration, capability):
    _validate_registration(registration, capability)
    return {
        "producer_kind": registration["producer_kind"],
        "implementation_sha256": registration["implementation_sha256"],
        "release_sha256": registration["release_sha256"],
        "capability": capability,
        "capabilities": deepcopy(registration["capabilities"]),
        "provider_identity": deepcopy(registration["provider_identity"]),
        "receipt_namespace": registration["receipt_namespace"],
    }


def _require_registration_identity(registration, request):
    expected = _provider_identity(
        request.get("provider_identity"), "request provider identity")
    _require(registration["provider_identity"] == expected,
             "registered observer provider identity differs from the "
             "expected provider identity")
    return expected


def _validate_registered_observation(registration, producer_id, request,
                                     observation, *, now, max_age_seconds):
    _identifier(producer_id, "producer_id")
    _validate_registration(registration, request["operation"])
    _require_registration_identity(registration, request)
    fields = {"status", "producer_id", "observation_id", "observed_at",
              "request_sha256", "result"}
    _require(isinstance(observation, dict) and set(observation) == fields,
             "merge-train provider observation has a non-closed shape")
    timestamp(observation["observed_at"])
    try:
        fresh(observation["observed_at"], now, max_age_seconds)
    except ValidationError as exc:
        raise ValidationError("provider observation is not fresh") from exc
    request_sha = fingerprint("merge_train_provider_request", request)
    material = {
        "producer_id": producer_id,
        "observed_at": observation["observed_at"],
        "request_sha256": request_sha,
        "result": observation["result"],
    }
    observation_id = fingerprint("merge_train_provider_observation", material)
    _require(observation["status"] == "OBSERVED"
             and observation["producer_id"] == producer_id
             and observation["request_sha256"] == request_sha
             and observation["observation_id"] == observation_id,
             "provider observation is not bound to the exact request")
    receipt = provider_receipt_sha256(registration, request, observation)
    _require(registration["receipts"].get(observation_id) == receipt,
             "provider observation has no matching registered receipt")
    attestation = {
        "producer_id": producer_id,
        "observation_id": observation_id,
        "observed_at": observation["observed_at"],
        "request_sha256": request_sha,
        "receipt_sha256": receipt,
        "implementation_sha256": registration["implementation_sha256"],
        "release_sha256": registration["release_sha256"],
    }
    return deepcopy(observation["result"]), attestation


def _registered_observation(registry, producer_id, request, *, now,
                            max_age_seconds):
    _identifier(producer_id, "producer_id")
    registration = registry.get(producer_id) if isinstance(registry, dict) else None
    _require(isinstance(registration, dict),
             "merge-train evidence requires a registered provider observer")
    _validate_registration(registration, request["operation"])
    _require_registration_identity(registration, request)
    argument = deepcopy(request)
    frozen = canonical(argument)
    observation = registration["observer"](argument)
    _require(canonical(argument) == frozen,
             "merge-train observer mutated its frozen request")
    result, attestation = _validate_registered_observation(
        registration, producer_id, request, observation, now=now,
        max_age_seconds=max_age_seconds)
    return result, attestation, deepcopy(observation)


def _evidence_digest(value, field):
    _require(isinstance(value, dict), f"{field} evidence must be a mapping")
    _digest(value.get("evidence_sha256"), f"{field} evidence_sha256")
    return value["evidence_sha256"]


def _validate_admission_evidence(source, evidence, trusted_owner_ids,
                                 *, now, max_age_seconds):
    fields = {"observed_at", "source_state", "final_gate", "critic", "ci",
              "provenance", "owner_review"}
    _require(isinstance(evidence, dict) and set(evidence) == fields,
             "source admission evidence has a non-closed shape")
    timestamp(evidence["observed_at"])
    try:
        fresh(evidence["observed_at"], now, max_age_seconds)
    except ValidationError as exc:
        raise ValidationError("source admission evidence is not fresh") from exc

    state = evidence["source_state"]
    state_fields = {"pr_number", "head_sha", "tree_sha", "base_branch", "draft"}
    _require(isinstance(state, dict) and set(state) == state_fields,
             "source state observation has a non-closed shape")
    _require(state["pr_number"] == source["pr_number"],
             "source PR identity drifted")
    _require(state["head_sha"] == source["head_sha"],
             "source head drifted after selection")
    _require(state["tree_sha"] == source["tree_sha"],
             "source tree drifted after selection")
    _require(state["base_branch"] == source["base_branch"],
             "source base drifted after selection")
    _require(state["draft"] is False,
             "draft source cannot enter a merge train")

    gate = evidence["final_gate"]
    gate_fields = {"status", "pr_number", "head_sha", "tree_sha", "base_branch",
                   "risk_tier", "owner_review_required", "completed_at",
                   "evidence_sha256"}
    _require(isinstance(gate, dict) and set(gate) == gate_fields,
             "source final gate has a non-closed shape")
    _require(gate["status"] == "PASS", "source final gate did not pass")
    _require(gate["pr_number"] == source["pr_number"]
             and gate["head_sha"] == source["head_sha"]
             and gate["tree_sha"] == source["tree_sha"]
             and gate["base_branch"] == source["base_branch"],
             "source final gate is not bound to the exact PR/base/head/tree")
    _require(gate["risk_tier"] == source["risk_tier"],
             "source final gate risk tier drifted")
    owner_review_required = _owner_review_required(source["risk_tier"])
    _require(type(gate["owner_review_required"]) is bool
             and gate["owner_review_required"] is owner_review_required,
             "source final gate owner-review policy drifted")
    timestamp(gate["completed_at"])
    gate_digest = _evidence_digest(gate, "final gate")

    evidence_digests = {"final_gate": gate_digest}
    for field in ("critic", "ci"):
        item = evidence[field]
        item_fields = {"status", "head_sha", "evidence_sha256"}
        _require(isinstance(item, dict) and set(item) == item_fields,
                 f"source {field} evidence has a non-closed shape")
        _require(item["status"] == "PASS", f"source {field} did not pass")
        _require(item["head_sha"] == source["head_sha"],
                 f"source {field} is not bound to the exact head")
        evidence_digests[field] = _evidence_digest(item, field)

    provenance = evidence["provenance"]
    provenance_fields = {"status", "base_sha", "head_sha", "evidence_sha256"}
    _require(isinstance(provenance, dict)
             and set(provenance) == provenance_fields,
             "source provenance evidence has a non-closed shape")
    _require(provenance["status"] == "PASS",
             "source provenance did not pass")
    _require(provenance["base_sha"] == source["base_sha"]
             and provenance["head_sha"] == source["head_sha"],
             "source provenance is not bound to the exact base/head range")
    evidence_digests["provenance"] = _evidence_digest(
        provenance, "provenance")

    owner_review = evidence["owner_review"]
    owner_digest = None
    approval_time = gate["completed_at"]
    if owner_review_required:
        owner_fields = {"status", "pr_number", "head_sha", "reviewer_id",
                        "submitted_at", "observed_at", "provider_artifact_id",
                        "evidence_sha256"}
        _require(isinstance(owner_review, dict)
                 and set(owner_review) == owner_fields,
                 "source requires a provider-observed owner review")
        _require(owner_review["status"] == "APPROVED",
                 "source owner review is not approved")
        _require(owner_review["pr_number"] == source["pr_number"],
                 "source owner review is bound to another PR")
        _require(owner_review["head_sha"] == source["head_sha"],
                 "source owner review is bound to the wrong head")
        _require(_is_owner_identity(owner_review["reviewer_id"]),
                 "source owner review actor identity is invalid")
        _require(owner_review["reviewer_id"] in trusted_owner_ids,
                 "source owner review actor is not trusted")
        _identifier(owner_review["provider_artifact_id"],
                    "owner review provider_artifact_id")
        timestamp(owner_review["submitted_at"])
        timestamp(owner_review["observed_at"])
        try:
            fresh(owner_review["observed_at"], now, max_age_seconds)
        except ValidationError as exc:
            raise ValidationError("source owner review is not fresh") from exc
        _require(owner_review["observed_at"] == evidence["observed_at"],
                 "source owner review was not part of the fresh provider observation")
        owner_digest = _evidence_digest(owner_review, "owner review")
        approval_time = owner_review["submitted_at"]
    else:
        _require(owner_review is None,
                 "a source without a normal owner-review gate cannot import review authority")
    return evidence_digests, owner_digest, approval_time


def admit_source(source, target_branch, expected_provider_identity, observer_id,
                 observer_registry, *, now, trusted_owner_ids,
                 max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Admit one exact source; its evidence never authorizes the batch."""
    source = _validate_source(source)
    trusted_owner_ids = _trusted_owner_identity_set(trusted_owner_ids)
    _require(type(max_age_seconds) is int and max_age_seconds >= 0,
             "max_age_seconds must be a nonnegative integer")
    request = source_admission_request(
        source, target_branch, expected_provider_identity)
    evidence, observation, raw_observation = _registered_observation(
        observer_registry, observer_id, request, now=now,
        max_age_seconds=max_age_seconds)
    digests, owner_digest, approval_time = _validate_admission_evidence(
        source, evidence, trusted_owner_ids, now=now,
        max_age_seconds=max_age_seconds)
    evidence_material = {
        "source": source,
        "provider_observation": observation,
        "evidence_digests": digests,
        "owner_review_evidence_sha256": owner_digest,
    }
    registration = observer_registry[observer_id]
    record = {
        "format": "awf-merge-train-source-admission-1",
        "status": "ADMITTED",
        **source,
        "approval_time": approval_time,
        "evidence_digests": digests,
        "owner_review_evidence_sha256": owner_digest,
        "provider_observation": observation,
        "provider_admission": {
            "request": deepcopy(request),
            "observation": raw_observation,
            "registration_trust": _registration_trust(
                registration, "SOURCE_ADMISSION"),
        },
        "authorization_scope": "SOURCE_ADMISSION_ONLY",
        "batch_merge_authorized": False,
        "admission_evidence_sha256": fingerprint(
            "merge_train_source_admission_evidence", evidence_material),
    }
    record["admission_id"] = fingerprint("merge_train_source_admission", record)
    return record


def _validate_admitted_source(source, expected_provider_identity,
                              observer_registry, *, now, trusted_owner_ids,
                              max_age_seconds):
    fields = {"format", "status", "pr_number", "ticket", "head_sha",
              "tree_sha", "base_branch", "base_sha", "depends_on",
              "commit_range", "risk_tier",
              "normal_final_gate_requires_owner_review", "approval_time",
              "evidence_digests", "owner_review_evidence_sha256",
              "provider_observation", "provider_admission",
              "authorization_scope",
              "batch_merge_authorized", "admission_evidence_sha256",
              "admission_id"}
    _require(isinstance(source, dict) and set(source) == fields
             and source.get("format") == "awf-merge-train-source-admission-1"
             and source.get("status") == "ADMITTED"
             and source.get("authorization_scope") == "SOURCE_ADMISSION_ONLY"
             and source.get("batch_merge_authorized") is False,
             "ordered source is not a source-only admission contract")
    trusted_owner_ids = _trusted_owner_identity_set(trusted_owner_ids)
    _require(type(max_age_seconds) is int and max_age_seconds >= 0,
             "max_age_seconds must be a nonnegative integer")
    normalized_source = _validate_source({
        key: deepcopy(source[key]) for key in (
        "pr_number", "ticket", "head_sha", "tree_sha", "base_branch",
        "base_sha", "depends_on", "commit_range", "risk_tier",
        "normal_final_gate_requires_owner_review")})
    _digest(source.get("admission_evidence_sha256"),
            "admission_evidence_sha256")
    timestamp(source.get("approval_time"))
    digests = source["evidence_digests"]
    _require(isinstance(digests, dict)
             and set(digests) == {"final_gate", "critic", "ci", "provenance"},
             "admitted source evidence digests have a non-closed shape")
    for field, value in digests.items():
        _digest(value, f"admitted {field} digest")
    owner_digest = source["owner_review_evidence_sha256"]
    if _owner_review_required(source["risk_tier"]):
        _digest(owner_digest, "admitted owner review digest")
    else:
        _require(owner_digest is None,
                 "lower-tier admission retained owner-review authority")
    observation = source["provider_observation"]
    observation_fields = {"producer_id", "observation_id", "observed_at",
                          "request_sha256", "receipt_sha256",
                          "implementation_sha256", "release_sha256"}
    _require(isinstance(observation, dict)
             and set(observation) == observation_fields,
             "admitted provider observation has a non-closed shape")
    _identifier(observation["producer_id"], "admitted producer_id")
    _identifier(observation["observation_id"], "admitted observation_id")
    timestamp(observation["observed_at"])
    for field in ("request_sha256", "receipt_sha256", "implementation_sha256",
                  "release_sha256"):
        _digest(observation[field], f"admitted {field}")

    provider_admission = source["provider_admission"]
    _require(isinstance(provider_admission, dict)
             and set(provider_admission) == {
                 "request", "observation", "registration_trust"},
             "provider admission has a non-closed shape")
    request = provider_admission["request"]
    _require(isinstance(request, dict)
             and request.get("operation") == "SOURCE_ADMISSION"
             and isinstance(request.get("target_branch"), str),
             "provider admission request is invalid")
    admitted_provider_identity = _provider_identity(
        request.get("provider_identity"),
        "provider admission provider identity")
    expected_provider_identity = _provider_identity(
        expected_provider_identity, "expected provider identity")
    _require(admitted_provider_identity == expected_provider_identity,
             "provider admission provider identity differs from the "
             "expected provider identity")
    expected_request = source_admission_request(
        normalized_source, request["target_branch"],
        expected_provider_identity)
    _require(request == expected_request,
             "provider admission request is not bound to the persisted source")
    raw_observation = provider_admission["observation"]
    producer_id = (raw_observation.get("producer_id")
                   if isinstance(raw_observation, dict) else None)
    _identifier(producer_id, "provider admission producer_id")
    registration = (observer_registry.get(producer_id)
                    if isinstance(observer_registry, dict) else None)
    _require(isinstance(registration, dict),
             "provider admission requires its protected observer registration")
    expected_trust = _registration_trust(registration, "SOURCE_ADMISSION")
    _require(provider_admission["registration_trust"] == expected_trust,
             "provider admission registration trust root changed")
    evidence, verified_observation = _validate_registered_observation(
        registration, producer_id, request, raw_observation, now=now,
        max_age_seconds=max_age_seconds)
    _require(observation == verified_observation,
             "provider admission attestation differs from its protected receipt")
    verified_digests, verified_owner_digest, verified_approval_time = (
        _validate_admission_evidence(
            normalized_source, evidence, trusted_owner_ids, now=now,
            max_age_seconds=max_age_seconds))
    _require(digests == verified_digests
             and owner_digest == verified_owner_digest
             and source["approval_time"] == verified_approval_time,
             "provider admission result differs from the persisted evidence")
    evidence_material = {
        "source": normalized_source,
        "provider_observation": verified_observation,
        "evidence_digests": verified_digests,
        "owner_review_evidence_sha256": verified_owner_digest,
    }
    _require(source["admission_evidence_sha256"] == fingerprint(
        "merge_train_source_admission_evidence", evidence_material),
        "provider admission evidence digest does not match its protected receipt")
    expected_id = fingerprint("merge_train_source_admission", {
        key: value for key, value in source.items() if key != "admission_id"
    })
    _require(source["admission_id"] == expected_id,
             "admitted source differs from its closed contract identity")
    return source


def order_sources(sources, expected_provider_identity, observer_registry, *,
                  now, trusted_owner_ids,
                  max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Topologically order by dependencies, then approval time and PR number."""
    _require(isinstance(sources, list) and sources,
             "merge train requires at least one admitted source")
    for source in sources:
        _validate_admitted_source(
            source, expected_provider_identity, observer_registry, now=now,
            trusted_owner_ids=trusted_owner_ids,
            max_age_seconds=max_age_seconds)
    prs = [source["pr_number"] for source in sources]
    _require(len(prs) == len(set(prs)), "duplicate PR in merge train")
    heads = [source["head_sha"] for source in sources]
    _require(len(heads) == len(set(heads)), "duplicate head in merge train")
    commits = set()
    for source in sources:
        shared = commits.intersection(source["commit_range"])
        _require(not shared, "shared commit range in merge train")
        commits.update(source["commit_range"])
    by_pr = {source["pr_number"]: source for source in sources}
    for source in sources:
        missing = set(source["depends_on"]) - set(by_pr)
        _require(not missing, "merge-train source dependency is not admitted")
    remaining = dict(by_pr)
    ordered = []
    completed = set()
    while remaining:
        ready = [source for source in remaining.values()
                 if set(source["depends_on"]).issubset(completed)]
        _require(ready, "merge-train dependencies contain a cycle")
        ready.sort(key=lambda item: (
            timestamp(item["approval_time"]), item["pr_number"]))
        selected = ready[0]
        ordered.append(deepcopy(selected))
        completed.add(selected["pr_number"])
        del remaining[selected["pr_number"]]
    return ordered


def require_batch_authorization(record):
    """Make source evidence unusable as batch merge authorization in 54A."""
    if isinstance(record, dict) and record.get("authorization_scope") == (
            "SOURCE_ADMISSION_ONLY"):
        raise ValidationError("source admission is never batch authorization")
    raise ValidationError("54A defines no batch authorization or live publication path")


def freeze_generation(batch_id, target_branch, target_sha, sources, generation,
                      expected_provider_identity, observer_registry, *, now,
                      trusted_owner_ids,
                      max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Freeze target and ordered admitted source/evidence identities."""
    _identifier(batch_id, "batch_id")
    _identifier(target_branch, "target_branch")
    _sha(target_sha, "target_sha")
    _positive(generation, "generation")
    expected_provider_identity = _provider_identity(
        expected_provider_identity, "expected provider identity")
    ordered = order_sources(
        sources, expected_provider_identity, observer_registry, now=now,
        trusted_owner_ids=trusted_owner_ids,
        max_age_seconds=max_age_seconds)
    admission_targets = {
        item["provider_admission"]["request"]["target_branch"]
        for item in ordered
    }
    _require(len(admission_targets) == 1,
             "merge-train sources have mixed admission target branches")
    _require(admission_targets == {target_branch},
             "source admission target branch differs from generation target branch")
    frozen_sources = [{
        "pr_number": item["pr_number"],
        "head_sha": item["head_sha"],
        "tree_sha": item["tree_sha"],
        "admission_evidence_sha256": item["admission_evidence_sha256"],
    } for item in ordered]
    record = {
        "format": "awf-merge-train-generation-1",
        "batch_id": batch_id,
        "target_branch": target_branch,
        "target_sha": target_sha,
        "generation": generation,
        "provider_identity": expected_provider_identity,
        "sources": frozen_sources,
        "authorization_scope": "CONSTRUCTION_ONLY",
        "may_publish": False,
        "may_merge": False,
    }
    record["generation_id"] = fingerprint("merge_train_generation", record)
    return record


def _validate_generation(generation):
    fields = {"format", "batch_id", "target_branch", "target_sha", "generation",
              "provider_identity", "sources", "authorization_scope",
              "may_publish", "may_merge", "generation_id"}
    _require(isinstance(generation, dict) and set(generation) == fields
             and generation.get("format") == "awf-merge-train-generation-1",
             "generation contract is invalid")
    _identifier(generation["batch_id"], "generation batch_id")
    _identifier(generation["target_branch"], "generation target_branch")
    _sha(generation["target_sha"], "generation target_sha")
    _positive(generation["generation"], "generation number")
    _provider_identity(generation["provider_identity"],
                       "generation provider identity")
    _require(generation["authorization_scope"] == "CONSTRUCTION_ONLY"
             and generation["may_publish"] is False
             and generation["may_merge"] is False,
             "generation contract grants unsupported authority")
    sources = generation["sources"]
    _require(isinstance(sources, list) and sources,
             "generation contract has no frozen sources")
    seen = set()
    for source in sources:
        _require(isinstance(source, dict)
                 and set(source) == {"pr_number", "head_sha", "tree_sha",
                                     "admission_evidence_sha256"},
                 "frozen generation source has a non-closed shape")
        number = _positive(source["pr_number"], "frozen source pr_number")
        _require(number not in seen,
                 "generation contract contains a duplicate source PR")
        seen.add(number)
        _sha(source["head_sha"], "frozen source head_sha")
        _sha(source["tree_sha"], "frozen source tree_sha")
        _digest(source["admission_evidence_sha256"],
                "frozen source admission_evidence_sha256")
    expected_id = fingerprint("merge_train_generation", {
        key: value for key, value in generation.items() if key != "generation_id"
    })
    _require(generation["generation_id"] == expected_id,
             "generation differs from its closed contract identity")
    return generation


def generation_status(generation, current_target_sha, current_sources,
                      expected_provider_identity, observer_registry, *, now,
                      trusted_owner_ids,
                      max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Abandon a generation for target, source, or evidence drift."""
    _validate_generation(generation)
    expected_provider_identity = _provider_identity(
        expected_provider_identity, "expected provider identity")
    _require(generation["provider_identity"] == expected_provider_identity,
             "generation provider identity differs from the expected "
             "provider identity")
    _sha(current_target_sha, "current_target_sha")
    ordered = order_sources(
        current_sources, expected_provider_identity, observer_registry, now=now,
        trusted_owner_ids=trusted_owner_ids,
        max_age_seconds=max_age_seconds)
    current = {item["pr_number"]: item for item in ordered}
    frozen = {item["pr_number"]: item for item in generation["sources"]}
    reasons = []
    if current_target_sha != generation["target_sha"]:
        reasons.append("TARGET_DRIFT")
    if set(current) != set(frozen):
        reasons.append("SOURCE_SET_DRIFT")
    for number in sorted(set(current).intersection(frozen)):
        if current[number]["head_sha"] != frozen[number]["head_sha"]:
            reasons.append("SOURCE_HEAD_DRIFT")
        elif current[number]["tree_sha"] != frozen[number]["tree_sha"]:
            reasons.append("SOURCE_TREE_DRIFT")
        if (current[number]["admission_evidence_sha256"]
                != frozen[number]["admission_evidence_sha256"]):
            reasons.append("SOURCE_EVIDENCE_DRIFT")
    reasons = list(dict.fromkeys(reasons))
    return {
        "status": "ABANDONED" if reasons else "CURRENT",
        "generation_id": generation["generation_id"],
        "reasons": reasons,
        "may_publish": False,
        "may_merge": False,
    }


def conflict_action(*, conflict_paths, generated_paths, reproduced,
                    unexpected_generated_bytes, repair_attempts, failure_kind):
    """Return the closed 54A conflict/failure action without mutating refs."""
    conflicts = set(_portable_paths(conflict_paths, "conflict_paths"))
    generated = set(_portable_paths(generated_paths, "generated_paths"))
    _require(type(reproduced) is bool
             and type(unexpected_generated_bytes) is bool,
             "conflict reproduction flags must be Boolean")
    _require(type(repair_attempts) is int and repair_attempts >= 0,
             "repair_attempts must be a nonnegative integer")
    _require(failure_kind in {"MERGE_CONFLICT", "LOCAL_PREFIX_FAILURE",
                              "REMOTE_ONLY_CI_FAILURE"},
             "unsupported merge-train failure kind")
    if failure_kind == "REMOTE_ONLY_CI_FAILURE":
        return "ABANDON_GENERATION"
    if failure_kind == "LOCAL_PREFIX_FAILURE":
        return "EJECT_SOURCE_REBUILD_FROM_TARGET"
    if (conflicts and conflicts.issubset(generated) and reproduced
            and not unexpected_generated_bytes and repair_attempts == 0):
        return "REGENERATE_ONCE_FROM_COMBINED_TREE"
    return "EJECT_SOURCE_REBUILD_FROM_TARGET"


def rebuild_after_failed_source(*, frozen_target_sha, ordered_sources,
                                failed_pr_number, build_prefix):
    """Eject a failed source and its dependents, then rebuild from the target.

    ``build_prefix`` is a local construction callback.  Every call receives the
    same frozen target and a complete retained prefix; this function exposes no
    provider mutation or publication authority.
    """
    _sha(frozen_target_sha, "frozen_target_sha")
    _positive(failed_pr_number, "failed_pr_number")
    _require(isinstance(ordered_sources, list) and ordered_sources,
             "ordered_sources must be a nonempty list")
    _require(callable(build_prefix), "build_prefix must be callable")
    normalized = []
    seen = set()
    for source in ordered_sources:
        _require(isinstance(source, dict)
                 and {"pr_number", "head_sha", "depends_on"}.issubset(source),
                 "rebuild source lacks its required frozen fields")
        number = _positive(source["pr_number"], "rebuild source pr_number")
        _require(number not in seen, "rebuild sources contain a duplicate PR")
        seen.add(number)
        _sha(source["head_sha"], "rebuild source head_sha")
        dependencies = source["depends_on"]
        _require(isinstance(dependencies, list)
                 and all(type(item) is int and item > 0 for item in dependencies)
                 and len(dependencies) == len(set(dependencies)),
                 "rebuild source dependencies are invalid")
        normalized.append(deepcopy(source))
    _require(failed_pr_number in seen,
             "failed source is absent from the frozen source order")
    ejected = {failed_pr_number}
    changed = True
    while changed:
        changed = False
        for source in normalized:
            if (source["pr_number"] not in ejected
                    and ejected.intersection(source["depends_on"])):
                ejected.add(source["pr_number"])
                changed = True
    retained = [source for source in normalized
                if source["pr_number"] not in ejected]
    prefixes = []
    for length in range(1, len(retained) + 1):
        included = deepcopy(retained[:length])
        commit_sha = build_prefix(frozen_target_sha, included)
        _sha(commit_sha, "rebuilt prefix commit_sha")
        prefixes.append({
            "length": length,
            "started_from_sha": frozen_target_sha,
            "source_pr_numbers": [item["pr_number"] for item in included],
            "commit_sha": commit_sha,
        })
    return {
        "status": "REBUILT_FROM_FROZEN_TARGET",
        "frozen_target_sha": frozen_target_sha,
        "failed_pr_number": failed_pr_number,
        "ejected_pr_numbers": [source["pr_number"] for source in normalized
                                if source["pr_number"] in ejected],
        "retained_pr_numbers": [source["pr_number"] for source in retained],
        "rebuilt_prefixes": prefixes,
        "may_publish": False,
        "may_merge": False,
    }


def _batch_names(release, scheduled_at, pr_numbers):
    _require(isinstance(release, str)
             and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release),
             "release must be a portable branch component")
    moment = timestamp(scheduled_at)
    _require(scheduled_at.endswith("Z") and moment.second == 0
             and moment.microsecond == 0 and (moment.hour, moment.minute)
             in {(10, 0), (15, 0)},
             "scheduled_at must be a fixed batch slot (10:00 or 15:00 UTC)")
    batch_id = f"{release}-{moment.strftime('%Y%m%d-%H%M')}"
    title = f"{batch_id} (PRs " + ", ".join(
        f"#{number}" for number in pr_numbers) + ")"
    return batch_id, "batch/" + batch_id, title


def _publication_request(generation, branch, head, tree, title):
    return {
        "operation": "BATCH_PUBLICATION_CANDIDATE",
        "provider_identity": deepcopy(generation["provider_identity"]),
        "expected_candidate": {
            "generation_id": generation["generation_id"],
            "batch_branch": branch,
            "batch_head_sha": head,
            "batch_tree_sha": tree,
            "base_branch": generation["target_branch"],
            "base_sha": generation["target_sha"],
            "constituent_pr_numbers": [item["pr_number"]
                                       for item in generation["sources"]],
            "draft_pr_title": title,
        },
    }


def freeze_publication_candidate(generation, batch_head_sha, batch_tree_sha, *,
                                 release, scheduled_at, max_batch_size,
                                 observer_id, observer_registry, now,
                                 max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Authenticate and freeze the exact batch publication candidate."""
    _validate_generation(generation)
    _require(generation["target_branch"] == "main",
             "batch publication target branch must be main")
    _sha(batch_head_sha, "batch_head_sha")
    _sha(batch_tree_sha, "batch_tree_sha")
    _positive(max_batch_size, "max_batch_size")
    _require(len(generation["sources"]) <= max_batch_size,
             "generation exceeds the configured maximum batch size")
    pr_numbers = [item["pr_number"] for item in generation["sources"]]
    batch_id, branch, title = _batch_names(
        release, scheduled_at, pr_numbers)
    _require(generation["batch_id"] == batch_id,
             "generation batch_id differs from the named publication slot")
    request = _publication_request(
        generation, branch, batch_head_sha, batch_tree_sha, title)
    expected = request["expected_candidate"]
    result, attestation, raw_observation = _registered_observation(
        observer_registry, observer_id, request, now=now,
        max_age_seconds=max_age_seconds)
    _require(result == expected,
             "provider did not observe the exact batch publication candidate")
    candidate = {
        "format": "awf-merge-train-publication-candidate-1",
        "generation": deepcopy(generation),
        "batch_id": batch_id,
        "batch_branch": branch,
        "batch_head_sha": batch_head_sha,
        "batch_tree_sha": batch_tree_sha,
        "base_branch": generation["target_branch"],
        "base_sha": generation["target_sha"],
        "scheduled_at": scheduled_at,
        "max_batch_size": max_batch_size,
        "constituent_pr_numbers": pr_numbers,
        "draft_pr_title": title,
        "provider_candidate": {
            "request": request,
            "observation": raw_observation,
            "attestation": attestation,
        },
        "may_merge": False,
    }
    candidate["publication_candidate_id"] = fingerprint(
        "merge_train_publication_candidate", candidate)
    return candidate


def _validate_publication_candidate(candidate, observer_registry, *, now,
                                    max_age_seconds):
    fields = {
        "format", "generation", "batch_id", "batch_branch",
        "batch_head_sha", "batch_tree_sha", "base_branch", "base_sha",
        "scheduled_at", "max_batch_size", "constituent_pr_numbers",
        "draft_pr_title", "provider_candidate", "may_merge",
        "publication_candidate_id",
    }
    _require(isinstance(candidate, dict) and set(candidate) == fields
             and candidate.get("format") ==
             "awf-merge-train-publication-candidate-1",
             "publication candidate has a non-closed shape")
    generation = _validate_generation(candidate["generation"])
    _sha(candidate["batch_head_sha"], "candidate batch_head_sha")
    _sha(candidate["batch_tree_sha"], "candidate batch_tree_sha")
    _positive(candidate["max_batch_size"], "candidate max_batch_size")
    _require(len(generation["sources"]) <= candidate["max_batch_size"],
             "publication candidate exceeds its maximum batch size")
    expected_prs = [item["pr_number"] for item in generation["sources"]]
    release = candidate["batch_id"].rsplit("-", 2)[0]
    batch_id, branch, title = _batch_names(
        release, candidate["scheduled_at"], expected_prs)
    _require(candidate["batch_id"] == batch_id
             and generation["batch_id"] == batch_id
             and generation["target_branch"] == "main"
             and candidate["batch_branch"] == branch
             and candidate["draft_pr_title"] == title
             and candidate["base_branch"] == generation["target_branch"]
             and candidate["base_sha"] == generation["target_sha"]
             and candidate["constituent_pr_numbers"] == expected_prs
             and candidate["may_merge"] is False,
             "publication candidate differs from its frozen generation")
    expected_id = fingerprint("merge_train_publication_candidate", {
        key: value for key, value in candidate.items()
        if key != "publication_candidate_id"
    })
    _require(candidate["publication_candidate_id"] == expected_id,
             "publication candidate differs from its identity")
    provider = candidate["provider_candidate"]
    _require(isinstance(provider, dict)
             and set(provider) == {"request", "observation", "attestation"},
             "publication candidate provider evidence is invalid")
    request = _publication_request(
        generation, branch, candidate["batch_head_sha"],
        candidate["batch_tree_sha"], title)
    _require(provider["request"] == request,
             "publication candidate request does not bind authenticated exact bytes")
    producer_id = provider["attestation"].get("producer_id")
    registration = (observer_registry.get(producer_id)
                    if isinstance(observer_registry, dict) else None)
    _require(isinstance(registration, dict),
             "publication candidate requires its registered provider observer")
    result, attestation = _validate_registered_observation(
        registration, producer_id, request,
        provider["observation"], now=now, max_age_seconds=max_age_seconds)
    _require(attestation == provider["attestation"]
             and result == request["expected_candidate"],
             "publication candidate is not bound to authenticated exact bytes")
    return candidate


def _validate_publisher(registration, expected_provider_identity):
    fields = {
        "producer_kind", "adapter_kind", "implementation_sha256",
        "release_sha256", "capabilities", "provider_identity",
        "receipt_namespace", "push_batch_ref", "create_draft_pr",
        "mark_draft_pr_ready", "observe_batch_ref", "observe_pull_request",
    }
    _require(isinstance(registration, dict) and set(registration) == fields,
             "merge-train publisher registration has a non-closed shape")
    _require(registration["producer_kind"] == "merge_train_publisher"
             and registration["adapter_kind"] == "gh",
             "merge-train publisher must be registered as gh-backed")
    _digest(registration["implementation_sha256"],
            "publisher implementation_sha256")
    _digest(registration["release_sha256"], "publisher release_sha256")
    _require(registration["capabilities"] == list(PUBLISHER_CAPABILITIES),
             "publisher capabilities must be limited to batch publication")
    _require(registration["provider_identity"] == expected_provider_identity,
             "publisher provider identity differs from the candidate")
    _require(registration["receipt_namespace"] == PUBLISHER_NAMESPACE,
             "publisher receipt namespace is invalid")
    for name in ("push_batch_ref", "create_draft_pr",
                 "mark_draft_pr_ready", "observe_batch_ref",
                 "observe_pull_request"):
        _require(callable(registration[name]),
                 f"publisher {name} adapter is not callable")
    return registration


def _save_publication_journal(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _load_publication_journal(path, candidate):
    path = Path(path)
    if not path.exists():
        value = {
            "format": "awf-merge-train-publication-journal-1",
            "publication_candidate_id": candidate["publication_candidate_id"],
            "batch_id": candidate["batch_id"],
            "generation": candidate["generation"]["generation"],
            "expected_head": candidate["batch_head_sha"],
            "status": "ACTIVE",
            "operations": [],
        }
        _save_publication_journal(path, value)
        return value
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError("publication intent journal is unreadable") from exc
    _require(isinstance(value, dict)
             and value.get("format") == "awf-merge-train-publication-journal-1"
             and value.get("publication_candidate_id") ==
             candidate["publication_candidate_id"]
             and value.get("batch_id") == candidate["batch_id"]
             and value.get("generation") == candidate["generation"]["generation"]
             and value.get("expected_head") == candidate["batch_head_sha"]
             and value.get("status", "ACTIVE") in {"ACTIVE", "ABANDONED"}
             and isinstance(value.get("operations"), list),
             "publication intent journal belongs to another candidate")
    return value


def _require_current_publication(candidate, observer_id, observer_registry, *,
                                 now, max_age_seconds, intent_path, journal):
    generation = candidate["generation"]
    expected = {
        "generation_id": generation["generation_id"],
        "target_branch": generation["target_branch"],
        "target_sha": generation["target_sha"],
        "sources": deepcopy(generation["sources"]),
    }
    request = {
        "operation": "BATCH_PUBLICATION_STATE",
        "provider_identity": deepcopy(generation["provider_identity"]),
        "generation_id": generation["generation_id"],
        "batch_branch": candidate["batch_branch"],
        "expected_head": candidate["batch_head_sha"],
    }
    result, _, _ = _registered_observation(
        observer_registry, observer_id, request, now=now,
        max_age_seconds=max_age_seconds)
    if result != expected:
        journal["status"] = "ABANDONED"
        _save_publication_journal(intent_path, journal)
        raise ValidationError(
            "publication generation is abandoned because its freeze drifted")


def _publication_pr_matches(value, candidate, *, draft, number=None):
    return (isinstance(value, dict)
            and type(value.get("number")) is int and value["number"] > 0
            and (number is None or value["number"] == number)
            and value.get("branch") == candidate["batch_branch"]
            and value.get("base_branch") == candidate["base_branch"]
            and value.get("head_sha") == candidate["batch_head_sha"]
            and value.get("title") == candidate["draft_pr_title"]
            and value.get("draft") is draft)


def publish_batch(candidate, *, observer_id, observer_registry, publisher_id,
                  publisher_registry, intent_path, now, mark_ready=False,
                  max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Push only the batch ref and create/optionally ready its draft PR.

    A durable intent is written before each mutation.  Unknown operations are
    only reconciled by branch/head observation and are never replayed.
    """
    _validate_publication_candidate(
        candidate, observer_registry, now=now,
        max_age_seconds=max_age_seconds)
    _require(type(mark_ready) is bool, "mark_ready must be Boolean")
    _identifier(publisher_id, "publisher_id")
    registration = (publisher_registry.get(publisher_id)
                    if isinstance(publisher_registry, dict) else None)
    _require(isinstance(registration, dict),
             "publication requires a registered publisher")
    _validate_publisher(registration,
                        candidate["generation"]["provider_identity"])
    journal = _load_publication_journal(intent_path, candidate)
    _require(journal.get("status", "ACTIVE") != "ABANDONED",
             "publication generation is abandoned because its freeze drifted")

    operations = ["PUSH_BATCH_REF", "CREATE_DRAFT_PR"]
    if mark_ready:
        operations.append("MARK_DRAFT_PR_READY")
    for operation in operations:
        created_pr = next((item.get("provider_result")
                           for item in journal["operations"]
                           if item.get("operation") == "CREATE_DRAFT_PR"
                           and item.get("status") in {"DONE", "RECONCILED"}), None)
        if operation == "MARK_DRAFT_PR_READY":
            _require(_publication_pr_matches(created_pr, candidate, draft=True),
                     "created PR does not match the publication candidate")
        existing = next((item for item in journal["operations"]
                         if item.get("operation") == operation), None)
        if existing is not None and existing.get("status") in {
                "DONE", "RECONCILED"}:
            continue
        if existing is not None:
            _require(existing.get("status") in {"INTENT", "UNKNOWN"},
                     "publication journal has an invalid operation state")
            if operation == "PUSH_BATCH_REF":
                observed = registration["observe_batch_ref"](
                    candidate["batch_branch"])
                matched = observed == {
                    "branch": candidate["batch_branch"],
                    "head_sha": candidate["batch_head_sha"],
                }
            else:
                observed = registration["observe_pull_request"](
                    candidate["batch_branch"])
                matched = _publication_pr_matches(
                    observed, candidate,
                    draft=operation != "MARK_DRAFT_PR_READY",
                    number=(created_pr["number"]
                            if operation == "MARK_DRAFT_PR_READY" else None))
            _require(matched,
                     "publication outcome is unknown; inspect provider state without replay")
            existing["status"] = "RECONCILED"
            existing["provider_result"] = deepcopy(observed)
            _save_publication_journal(intent_path, journal)
            continue

        _require_current_publication(
            candidate, observer_id, observer_registry, now=now,
            max_age_seconds=max_age_seconds, intent_path=intent_path,
            journal=journal)
        intent = {
            "operation": operation,
            "status": "INTENT",
            "batch_id": candidate["batch_id"],
            "generation": candidate["generation"]["generation"],
            "expected_head": candidate["batch_head_sha"],
        }
        journal["operations"].append(intent)
        _save_publication_journal(intent_path, journal)
        try:
            if operation == "PUSH_BATCH_REF":
                result = registration["push_batch_ref"](
                    candidate["batch_branch"], candidate["batch_head_sha"])
                valid = result == {"branch": candidate["batch_branch"],
                                   "head_sha": candidate["batch_head_sha"]}
            elif operation == "CREATE_DRAFT_PR":
                result = registration["create_draft_pr"](
                    candidate["batch_branch"], candidate["base_branch"],
                    candidate["draft_pr_title"])
                valid = _publication_pr_matches(result, candidate, draft=True)
            else:
                pull_request = registration["observe_pull_request"](
                    candidate["batch_branch"])
                _require(_publication_pr_matches(
                    pull_request, candidate, draft=True, number=created_pr["number"]),
                    "observed PR differs from the publication candidate")
                result = registration["mark_draft_pr_ready"](
                    pull_request["number"])
                valid = _publication_pr_matches(
                    result, candidate, draft=False, number=created_pr["number"])
        except (TimeoutError, ConnectionError, OSError) as exc:
            intent["status"] = "UNKNOWN"
            _save_publication_journal(intent_path, journal)
            raise ValidationError(
                "publication outcome is unknown; restart to observe without replay") from exc
        _require(valid, "publisher returned a result for a different batch candidate")
        intent["status"] = "DONE"
        intent["provider_result"] = deepcopy(result)
        _save_publication_journal(intent_path, journal)
    pull_request = registration["observe_pull_request"](
        candidate["batch_branch"])
    created_pr = next(item["provider_result"] for item in journal["operations"]
                      if item["operation"] == "CREATE_DRAFT_PR")
    _require(_publication_pr_matches(
        pull_request, candidate, draft=not mark_ready, number=created_pr["number"]),
        "observed PR differs from the publication candidate")
    return {
        "status": ("PUBLISHED_READY" if mark_ready else "PUBLISHED_DRAFT"),
        "batch_id": candidate["batch_id"],
        "generation": candidate["generation"]["generation"],
        "batch_branch": candidate["batch_branch"],
        "batch_head_sha": candidate["batch_head_sha"],
        "pull_request": pull_request,
        "journal": deepcopy(journal),
        "may_merge": False,
    }


def verify_batch_parent_provenance(generation, commit_shas, observer_id,
                                   expected_provider_identity,
                                   observer_registry, *, now,
                                   max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Require registered exact base/source parents for every batch prefix."""
    _validate_generation(generation)
    expected_provider_identity = _provider_identity(
        expected_provider_identity, "expected provider identity")
    _require(generation["provider_identity"] == expected_provider_identity,
             "generation provider identity differs from the expected "
             "provider identity")
    _require(isinstance(commit_shas, list)
             and len(commit_shas) == len(generation["sources"]),
             "batch-parent provenance needs one commit per source")
    for commit in commit_shas:
        _sha(commit, "batch commit SHA")
    _require(len(commit_shas) == len(set(commit_shas)),
             "batch-parent provenance contains duplicate commits")
    request = {
        "operation": "BATCH_PARENT_PROVENANCE",
        "provider_identity": expected_provider_identity,
        "generation_id": generation["generation_id"],
        "target_branch": generation["target_branch"],
        "target_sha": generation["target_sha"],
        "sources": deepcopy(generation["sources"]),
        "commit_shas": list(commit_shas),
    }
    result, observation, _ = _registered_observation(
        observer_registry, observer_id, request, now=now,
        max_age_seconds=max_age_seconds)
    _require(isinstance(result, dict)
             and set(result) == {"generation_id", "commits"}
             and result["generation_id"] == generation["generation_id"],
             "batch-parent provenance result is not bound to the generation")
    commits = result["commits"]
    _require(isinstance(commits, list)
             and len(commits) == len(generation["sources"]),
             "batch-parent provenance result has incomplete commits")
    prior = generation["target_sha"]
    normalized = []
    for index, (item, source, expected_commit) in enumerate(
            zip(commits, generation["sources"], commit_shas), 1):
        _require(isinstance(item, dict)
                 and set(item) == {"length", "commit_sha", "parents"}
                 and item["length"] == index,
                 "batch-parent provenance commits are not closed and ordered")
        _sha(item["commit_sha"], "observed batch commit SHA")
        _require(item["commit_sha"] == expected_commit,
                 "batch-parent provenance observed a different commit")
        _require(item["parents"] == [prior, source["head_sha"]],
                 "observed batch parents are not exact base then source")
        normalized.append(deepcopy(item))
        prior = item["commit_sha"]
    record = {
        "format": "awf-merge-train-batch-parent-provenance-1",
        "status": "PASS",
        "generation_id": generation["generation_id"],
        "commits": normalized,
        "provider_observation": observation,
        "authorization_scope": "PROVENANCE_ONLY",
        "batch_merge_authorized": False,
    }
    record["provenance_id"] = fingerprint(
        "merge_train_batch_parent_provenance", record)
    return record
