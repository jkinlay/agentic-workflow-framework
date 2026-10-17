"""Shared deterministic prefix construction for integration candidates.

This module validates construction performed by a narrowly registered runner.
It has no ref mutation, push, pull-request, merge, or provider API.
"""

from copy import deepcopy
import re

from . import ValidationError
from .canonical import canonical, fingerprint, fresh, timestamp


SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_EVIDENCE_AGE_SECONDS = 1800
CAPABILITY = "DETERMINISTIC_PREFIX_BUILD"


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


def _sources(values):
    _require(isinstance(values, list) and values,
             "sources must be a nonempty ordered list")
    normalized = []
    seen_prs = set()
    seen_heads = set()
    for source in values:
        _require(isinstance(source, dict)
                 and set(source) == {"pr_number", "head_sha"},
                 "build source fields differ from the closed record shape")
        number = _positive(source["pr_number"], "source pr_number")
        head = _sha(source["head_sha"], "source head_sha")
        _require(number not in seen_prs, "build sources contain a duplicate PR")
        _require(head not in seen_heads, "build sources contain a duplicate head")
        seen_prs.add(number)
        seen_heads.add(head)
        normalized.append({"pr_number": number, "head_sha": head})
    return normalized


def build_input_id(batch_id, target_sha, sources, test_command):
    """Return the stable identity for target, ordered source heads, and tests."""
    _identifier(batch_id, "batch_id")
    _sha(target_sha, "target_sha")
    normalized = _sources(sources)
    _identifier(test_command, "test_command")
    return fingerprint("integration_build_input", {
        "batch_id": batch_id,
        "target_sha": target_sha,
        "sources": [[item["pr_number"], item["head_sha"]]
                    for item in normalized],
        "test_command": test_command,
    })


def build_request(batch_id, target_sha, sources, test_command,
                  integration_branch, generation):
    """Create the closed, immutable input handed to a registered builder."""
    normalized = _sources(sources)
    _identifier(integration_branch, "integration_branch")
    _positive(generation, "generation")
    request = {
        "format": "awf-integration-build-request-1",
        "batch_id": _identifier(batch_id, "batch_id"),
        "target_sha": _sha(target_sha, "target_sha"),
        "sources": normalized,
        "test_command": _identifier(test_command, "test_command"),
        "integration_branch": integration_branch,
        "generation": generation,
    }
    request["input_id"] = build_input_id(
        batch_id, target_sha, normalized, test_command)
    return request


def _validate_request(request):
    fields = {"format", "batch_id", "target_sha", "sources", "test_command",
              "integration_branch", "generation", "input_id"}
    _require(isinstance(request, dict) and set(request) == fields,
             "build request fields differ from the closed record shape")
    _require(request["format"] == "awf-integration-build-request-1",
             "build request format is unsupported")
    rebuilt = build_request(
        request["batch_id"], request["target_sha"], request["sources"],
        request["test_command"], request["integration_branch"],
        request["generation"])
    _require(canonical(rebuilt) == canonical(request),
             "build request differs from its deterministic reconstruction")
    return rebuilt


def _validate_registration(registration):
    fields = {"producer_kind", "implementation_sha256", "release_sha256",
              "capabilities", "runner", "receipts"}
    _require(isinstance(registration, dict) and set(registration) == fields,
             "integration-build producer registration has a non-closed shape")
    _require(registration["producer_kind"] == "integration_build",
             "registered producer is not an integration-build producer")
    _digest(registration["implementation_sha256"], "producer implementation_sha256")
    _digest(registration["release_sha256"], "producer release_sha256")
    capabilities = registration["capabilities"]
    _require(isinstance(capabilities, list) and capabilities
             and all(isinstance(value, str) and value for value in capabilities)
             and len(capabilities) == len(set(capabilities)),
             "integration-build producer capabilities are invalid")
    _require(CAPABILITY in capabilities,
             "integration-build producer lacks deterministic-prefix capability")
    _require(callable(registration["runner"]),
             "integration-build producer runner is not callable")
    _require(isinstance(registration["receipts"], dict),
             "integration-build producer receipts are invalid")
    return registration


def prefix_receipt_sha256(registration, request, observation, prefix):
    """Bind one prefix test result to its registered builder and frozen input."""
    _validate_registration(registration)
    _validate_request(request)
    _require(isinstance(observation, dict), "build observation must be a mapping")
    material = {key: prefix[key] for key in (
        "length", "commit_sha", "tree_sha", "passed", "result_digest")}
    return fingerprint("integration_build_prefix_receipt", {
        "producer_id": observation["producer_id"],
        "run_id": observation["run_id"],
        "observation_id": observation["observation_id"],
        "observed_at": observation["observed_at"],
        "input_id": request["input_id"],
        "integration_branch": request["integration_branch"],
        "generation": request["generation"],
        "prefix": material,
        "implementation_sha256": registration["implementation_sha256"],
        "release_sha256": registration["release_sha256"],
    })


def validate_build(request, outcome, registration, *, now,
                   max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Validate exact construction, prefix tests, freshness, and receipts."""
    request = _validate_request(request)
    registration = _validate_registration(registration)
    _require(type(max_age_seconds) is int and max_age_seconds >= 0,
             "max_age_seconds must be a nonnegative integer")
    fields = {"input_id", "result_commit_sha", "result_tree_sha", "test_command",
              "construction", "prefix_results", "observation"}
    _require(isinstance(outcome, dict) and set(outcome) == fields,
             "build outcome fields differ from the closed record shape")
    _require(outcome["input_id"] == request["input_id"],
             "build outcome is not bound to the frozen input")
    _require(outcome["test_command"] == request["test_command"],
             "build outcome used a different test command")
    _sha(outcome["result_commit_sha"], "result_commit_sha")
    _sha(outcome["result_tree_sha"], "result_tree_sha")

    observation = outcome["observation"]
    observation_fields = {"observation_id", "producer_id", "run_id",
                          "observed_at", "input_id", "result_id"}
    _require(isinstance(observation, dict)
             and set(observation) == observation_fields,
             "build observation has a non-closed shape")
    for field in ("producer_id", "run_id", "observation_id"):
        _identifier(observation[field], field)
    timestamp(observation["observed_at"])
    fresh(observation["observed_at"], now, max_age_seconds)
    _require(observation["input_id"] == request["input_id"],
             "build producer observed a different input")

    construction = outcome["construction"]
    sources = request["sources"]
    _require(isinstance(construction, list)
             and len(construction) == len(sources),
             "build construction must contain every ordered source")
    prior = request["target_sha"]
    normalized_construction = []
    for index, (step, source) in enumerate(zip(construction, sources), 1):
        step_fields = {"length", "base_commit_sha", "head_sha", "parents",
                       "result_commit_sha", "result_tree_sha"}
        _require(isinstance(step, dict) and set(step) == step_fields
                 and step["length"] == index,
                 "build construction steps are not closed and ordered")
        for field in ("base_commit_sha", "head_sha", "result_commit_sha",
                      "result_tree_sha"):
            _sha(step[field], f"construction {field}")
        _require(step["base_commit_sha"] == prior
                 and step["head_sha"] == source["head_sha"],
                 "build construction does not apply frozen heads in order")
        _require(step["parents"] == [prior, source["head_sha"]],
                 "constructed merge commit parents must be exact base then source")
        prior = step["result_commit_sha"]
        normalized_construction.append(deepcopy(step))
    _require(outcome["result_commit_sha"] == prior,
             "result_commit_sha is not the final prefix commit")
    _require(outcome["result_tree_sha"] == construction[-1]["result_tree_sha"],
             "result_tree_sha is not the final prefix tree")

    prefixes = outcome["prefix_results"]
    _require(isinstance(prefixes, list) and len(prefixes) == len(sources),
             "a build must record every ordered prefix result")
    normalized_prefixes = []
    failed = False
    for index, (prefix, step) in enumerate(zip(prefixes, construction), 1):
        prefix_fields = {"length", "commit_sha", "tree_sha", "passed",
                         "result_digest", "receipt_sha256"}
        _require(isinstance(prefix, dict) and set(prefix) == prefix_fields,
                 "prefix result fields differ from the closed record shape")
        _require(prefix["length"] == index and type(prefix["passed"]) is bool,
                 "prefix results must be complete and ordered")
        _sha(prefix["commit_sha"], "prefix commit_sha")
        _sha(prefix["tree_sha"], "prefix tree_sha")
        _digest(prefix["result_digest"], "prefix result_digest")
        _digest(prefix["receipt_sha256"], "prefix receipt_sha256")
        _require(prefix["commit_sha"] == step["result_commit_sha"]
                 and prefix["tree_sha"] == step["result_tree_sha"],
                 "prefix test result is not bound to its constructed commit and tree")
        _require(not (failed and prefix["passed"]),
                 "a later prefix cannot pass after the first failing prefix")
        failed = failed or not prefix["passed"]
        expected_receipt = prefix_receipt_sha256(
            registration, request, observation, prefix)
        receipt_key = f"{observation['run_id']}:{index}"
        _require(prefix["receipt_sha256"] == expected_receipt
                 and registration["receipts"].get(receipt_key) == expected_receipt,
                 "prefix receipt is absent or does not match registered execution")
        normalized_prefixes.append({
            key: deepcopy(prefix[key]) for key in (
                "length", "commit_sha", "tree_sha", "passed", "result_digest")
        })

    deterministic = {
        "input_id": request["input_id"],
        "result_commit_sha": outcome["result_commit_sha"],
        "result_tree_sha": outcome["result_tree_sha"],
        "construction": normalized_construction,
        "prefix_results": normalized_prefixes,
    }
    result_id = fingerprint("integration_build_result", deterministic)
    _require(observation["result_id"] == result_id,
             "build observation is not bound to the deterministic result")
    observation_material = {key: value for key, value in observation.items()
                            if key != "observation_id"}
    _require(observation["observation_id"] == fingerprint(
        "integration_build_observation", observation_material),
        "build observation identity does not match its closed bytes")
    record = {
        "format": "awf-integration-build-record-1",
        "batch_id": request["batch_id"],
        "generation": request["generation"],
        "integration_branch": request["integration_branch"],
        **deepcopy(outcome),
        "green": all(item["passed"] for item in prefixes),
    }
    record["build_id"] = fingerprint("integration_build_record", record)
    return record


def run_registered_build(request, registry, producer_id, *, now,
                         max_age_seconds=MAX_EVIDENCE_AGE_SECONDS):
    """Invoke one registered builder without granting any publication ability."""
    request = _validate_request(request)
    _identifier(producer_id, "producer_id")
    registration = registry.get(producer_id) if isinstance(registry, dict) else None
    _require(isinstance(registration, dict),
             "deterministic build requires a registered producer")
    _validate_registration(registration)
    argument = deepcopy(request)
    frozen = canonical(argument)
    outcome = registration["runner"](argument)
    _require(canonical(argument) == frozen,
             "registered build runner mutated the frozen request")
    _require(isinstance(outcome, dict),
             "registered build runner returned no outcome")
    observation = outcome.get("observation")
    _require(isinstance(observation, dict)
             and observation.get("producer_id") == producer_id,
             "build outcome is bound to another producer")
    return validate_build(
        request, outcome, registration, now=now,
        max_age_seconds=max_age_seconds)


def require_same_input_tree(first, second):
    """Refuse nondeterministic trees for an identical construction input."""
    _require(isinstance(first, dict) and isinstance(second, dict),
             "build records must be mappings")
    _require(first.get("input_id") == second.get("input_id"),
             "tree comparison requires the same input")
    _require(first.get("result_tree_sha") == second.get("result_tree_sha"),
             "same input produced a different tree")
    return first["result_tree_sha"]
