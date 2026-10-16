"""Merge-train 54A contracts, admission, ordering, and invalidation.

The module is deliberately library-only.  It cannot push refs, create or edit
pull requests, authorize a batch merge, merge a target, or write Jira.
"""

from copy import deepcopy
import re

from . import ValidationError
from .canonical import canonical, fingerprint, fresh, timestamp


SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_EVIDENCE_AGE_SECONDS = 1800
OBSERVER_NAMESPACE = "awf-merge-train-observer-v1"


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
    _require(normalized["risk_tier"] in {1, 2, 3},
             "source risk_tier must be 1, 2, or 3")
    _require(type(normalized["normal_final_gate_requires_owner_review"]) is bool,
             "source owner-review gate flag must be Boolean")
    return normalized


def source_admission_request(source, target_branch):
    """Create the exact immutable selector used for admission observation."""
    source = _validate_source(source)
    _identifier(target_branch, "target_branch")
    if not source["depends_on"]:
        _require(source["base_branch"] == target_branch,
                 "independent source base must match the train target branch")
    request = {
        "operation": "SOURCE_ADMISSION",
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
    provider = registration["provider_identity"]
    _require(isinstance(provider, dict)
             and set(provider) == {"host", "repository", "repository_id"}
             and isinstance(provider["host"], str) and provider["host"]
             and isinstance(provider["repository"], str) and provider["repository"]
             and type(provider["repository_id"]) is int
             and provider["repository_id"] > 0,
             "merge-train observer provider identity is invalid")
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
        "implementation_sha256": registration["implementation_sha256"],
        "release_sha256": registration["release_sha256"],
        "receipt_namespace": registration["receipt_namespace"],
    })


def _registered_observation(registry, producer_id, request, *, now,
                            max_age_seconds):
    _identifier(producer_id, "producer_id")
    registration = registry.get(producer_id) if isinstance(registry, dict) else None
    _require(isinstance(registration, dict),
             "merge-train evidence requires a registered provider observer")
    _validate_registration(registration, request["operation"])
    argument = deepcopy(request)
    frozen = canonical(argument)
    observation = registration["observer"](argument)
    _require(canonical(argument) == frozen,
             "merge-train observer mutated its frozen request")
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
    _require(gate["owner_review_required"]
             == source["normal_final_gate_requires_owner_review"],
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
    if source["normal_final_gate_requires_owner_review"]:
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
        _require(owner_review["reviewer_id"] in set(trusted_owner_ids),
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


def admit_source(source, target_branch, observer_id, observer_registry, *, now,
                 trusted_owner_ids, max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Admit one exact source; its evidence never authorizes the batch."""
    source = _validate_source(source)
    _require(isinstance(trusted_owner_ids, (list, tuple, set, frozenset))
             and all(isinstance(value, str) and value for value in trusted_owner_ids),
             "trusted_owner_ids must be an identity collection")
    _require(type(max_age_seconds) is int and max_age_seconds >= 0,
             "max_age_seconds must be a nonnegative integer")
    request = source_admission_request(source, target_branch)
    evidence, observation = _registered_observation(
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
    record = {
        "format": "awf-merge-train-source-admission-1",
        "status": "ADMITTED",
        **source,
        "approval_time": approval_time,
        "evidence_digests": digests,
        "owner_review_evidence_sha256": owner_digest,
        "provider_observation": observation,
        "authorization_scope": "SOURCE_ADMISSION_ONLY",
        "batch_merge_authorized": False,
        "admission_evidence_sha256": fingerprint(
            "merge_train_source_admission_evidence", evidence_material),
    }
    record["admission_id"] = fingerprint("merge_train_source_admission", record)
    return record


def _validate_admitted_source(source):
    fields = {"format", "status", "pr_number", "ticket", "head_sha",
              "tree_sha", "base_branch", "base_sha", "depends_on",
              "commit_range", "risk_tier",
              "normal_final_gate_requires_owner_review", "approval_time",
              "evidence_digests", "owner_review_evidence_sha256",
              "provider_observation", "authorization_scope",
              "batch_merge_authorized", "admission_evidence_sha256",
              "admission_id"}
    _require(isinstance(source, dict) and set(source) == fields
             and source.get("format") == "awf-merge-train-source-admission-1"
             and source.get("status") == "ADMITTED"
             and source.get("authorization_scope") == "SOURCE_ADMISSION_ONLY"
             and source.get("batch_merge_authorized") is False,
             "ordered source is not a source-only admission contract")
    _validate_source({key: deepcopy(source[key]) for key in (
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
    if source["normal_final_gate_requires_owner_review"]:
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
    expected_id = fingerprint("merge_train_source_admission", {
        key: value for key, value in source.items() if key != "admission_id"
    })
    _require(source["admission_id"] == expected_id,
             "admitted source differs from its closed contract identity")
    return source


def order_sources(sources):
    """Topologically order by dependencies, then approval time and PR number."""
    _require(isinstance(sources, list) and sources,
             "merge train requires at least one admitted source")
    for source in sources:
        _validate_admitted_source(source)
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


def freeze_generation(batch_id, target_branch, target_sha, sources, generation):
    """Freeze target and ordered admitted source/evidence identities."""
    _identifier(batch_id, "batch_id")
    _identifier(target_branch, "target_branch")
    _sha(target_sha, "target_sha")
    _positive(generation, "generation")
    ordered = order_sources(sources)
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
        "sources": frozen_sources,
        "authorization_scope": "CONSTRUCTION_ONLY",
        "may_publish": False,
        "may_merge": False,
    }
    record["generation_id"] = fingerprint("merge_train_generation", record)
    return record


def _validate_generation(generation):
    fields = {"format", "batch_id", "target_branch", "target_sha", "generation",
              "sources", "authorization_scope", "may_publish", "may_merge",
              "generation_id"}
    _require(isinstance(generation, dict) and set(generation) == fields
             and generation.get("format") == "awf-merge-train-generation-1",
             "generation contract is invalid")
    _identifier(generation["batch_id"], "generation batch_id")
    _identifier(generation["target_branch"], "generation target_branch")
    _sha(generation["target_sha"], "generation target_sha")
    _positive(generation["generation"], "generation number")
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


def generation_status(generation, current_target_sha, current_sources):
    """Abandon a generation for target, source, or evidence drift."""
    _validate_generation(generation)
    _sha(current_target_sha, "current_target_sha")
    ordered = order_sources(current_sources)
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


def verify_batch_parent_provenance(generation, commit_shas, observer_id,
                                   observer_registry, *, now,
                                   max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Require registered exact base/source parents for every batch prefix."""
    _validate_generation(generation)
    _require(isinstance(commit_shas, list)
             and len(commit_shas) == len(generation["sources"]),
             "batch-parent provenance needs one commit per source")
    for commit in commit_shas:
        _sha(commit, "batch commit SHA")
    _require(len(commit_shas) == len(set(commit_shas)),
             "batch-parent provenance contains duplicate commits")
    request = {
        "operation": "BATCH_PARENT_PROVENANCE",
        "generation_id": generation["generation_id"],
        "target_branch": generation["target_branch"],
        "target_sha": generation["target_sha"],
        "sources": deepcopy(generation["sources"]),
        "commit_shas": list(commit_shas),
    }
    result, observation = _registered_observation(
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
