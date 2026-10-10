"""Offline evidence evaluation. Returns analysis, never an execution permit."""
from __future__ import annotations
import base64
import binascii
from datetime import timedelta
import fnmatch
import re
import uuid

from . import ValidationError
from .canonical import canonical, fingerprint, fresh, loads, sha256, timestamp, unique
from .policy import (CAPABILITIES, dependencies_satisfied, inside_scope, safe_path,
                     specialist_domains, validate_config)
from .review_policy import (blocking_findings, check_tier_declaration, closure_met, is_boundary,
                            tier1_specialist_domains, validate_findings)
from .review_tiers import classify


def decode_base64_bytes(value, field):
    """Decode one canonical base64 envelope without changing the bound bytes."""
    if (not isinstance(value, dict) or set(value) != {"encoding", "data"}
            or value.get("encoding") != "base64" or not isinstance(value.get("data"), str)):
        raise ValidationError(f"{field} must be an exact base64 envelope")
    encoded = value["data"]
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, binascii.Error) as exc:
        raise ValidationError(f"{field} is not strict base64") from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise ValidationError(f"{field} is not canonical base64")
    return raw


def resource_overlaps(runs, limits):
    """Concurrent COMPLETE runs from different contexts exceeding a named resource's slots.

    Undeclared resources are treated as one exclusive slot. Records within one
    bundle share a ticket binding; cross-ticket overlap is the broker's ledger.
    """
    held = []
    for run in runs:
        for item in run.get("resources_held", []):
            held.append((item["name"], timestamp(item["from"]), timestamp(item["until"]), item["slots"], run["record_id"], run["context_id"]))
    overlaps = []
    for index, first in enumerate(held):
        for second in held[index + 1:]:
            if first[0] != second[0] or first[5] == second[5] or not (first[1] < second[2] and second[1] < first[2]):
                continue
            if first[3] + second[3] > limits.get(first[0], 1):
                overlaps.append((first[0], first[4], second[4]))
    return overlaps


def local_ci_parity(config, contract, worker, ci):
    """Each required check's executed count equals its matching local command's count,
    or the contract declares the platform distinction with a regression test."""
    distinction = contract["validation"].get("platform_distinction")
    if distinction is not None:
        return True, [f"declared platform distinction: {distinction['reason']} (regression test {distinction['regression_test_id']})"]
    local = {command["command"]: command["tests_executed"] for command in worker["validation"]}
    mismatches = []
    checks = {check["name"]: check for check in ci["checks"]}
    for index, required in enumerate(config["validation"]["required_ci_checks"]):
        check = checks.get(required["name"])
        if check is None:
            mismatches.append(f"{required['name']}: no CI evidence")
            continue
        # The matching command: an explicit local_command, else the contract's command at the same index, else its first.
        commands = contract["validation"]["commands"]
        command = required.get("local_command") or (commands[index] if index < len(commands) else commands[0])
        if command not in local:
            mismatches.append(f"{required['name']}: local command {command!r} has no validation entry")
        elif check["tests_executed"] != local[command]:
            mismatches.append(f"{required['name']}: CI executed {check['tests_executed']} tests, local {command!r} executed {local[command]}")
    return not mismatches, mismatches


def verified_owner_records(config, contracts, bundle, binding, runs, excluded_contexts, excluded_producers, now):
    """Validate owner dispositions before any of them can change a gate."""
    from .authorization import verify_owner_record
    head = bundle["candidate"]["head_sha"]
    dispositions, comments = [], set()
    records = [("finding-disposition", r) for r in bundle.get("finding_dispositions", [])]
    cap = bundle.get("cap_disposition")
    if cap is not None:
        records.append(("review-cap-disposition", cap))
    for schema, record in records:
        verify_owner_record(record, schema, config, contracts, now, head_sha=head, binding=binding,
                            runs=runs, excluded_contexts=excluded_contexts, excluded_producers=excluded_producers)
        if schema == "review-cap-disposition" and record["record_id"] != cap_disposition_record_id(record):
            raise ValidationError(
                "Review cap disposition record ID does not authenticate its critic artifact and verdict binding")
        comment = record["owner_source"]["comment_id"]
        if comment in comments:
            raise ValidationError(f"{schema}: owner comment {comment} already signs another record")
        comments.add(comment)
        if schema == "finding-disposition":
            dispositions.append(record)
    if cap is not None:
        limit = config["execution"]["max_amendment_cycles"]
        if cap["cycles"] < limit:
            raise ValidationError(f"Cap disposition at cycles {cap['cycles']} precedes the cap ({limit}); present REVIEW_CAP_REACHED first")
        if cap["cap_extensions"] > config["execution"].get("max_cap_extensions", 2):
            raise ValidationError("Cap disposition records more extensions than $.execution.max_cap_extensions allows")
    return dispositions, cap


def expected_binding(config, workflow, bundle):
    from .policy import policy_hash
    return {"project_id": config["project"]["id"], "repository_id": config["github"]["repository_id"],
            "issue_id": bundle["snapshot"]["issue_id"], "requirements_hash": fingerprint("requirements", bundle["snapshot"]),
            "contract_hash": fingerprint("contract", bundle["contract"]), "policy_hash": policy_hash(config, workflow),
            "candidate_id": fingerprint("candidate", bundle["candidate"])}


def publication_receipt_consistent(publication):
    """Validate semantic counts and PASS/BLOCKED classification, beyond JSON shape."""
    findings = publication["findings"]
    blocking = sum(item["classification"] == "BLOCKING" for item in findings)
    pre_existing = sum(item["classification"] == "PRE_EXISTING" for item in findings)
    blocked = bool(blocking or publication["unscanned"])
    return (publication["total_findings"] == len(findings)
            and publication["blocking_findings"] == blocking
            and publication["pre_existing_findings"] == pre_existing
            and publication["total_findings"] == blocking + pre_existing
            and publication["unscanned_count"] == len(publication["unscanned"])
            and publication["status"] == ("BLOCKED" if blocked else "PASS"))


def critic_artifact_receipt_sha256(verdict):
    """Legacy helper retained for migration diagnostics, never gate trust."""
    receipt = verdict["critic_review"]
    return fingerprint("critic-review-artifact-receipt", {
        "record_id": receipt["record_id"], "run_id": verdict["run_id"],
        "producer_id": verdict["producer_id"], "binding": verdict["binding"],
        "created_at": verdict["created_at"], "round": verdict["round"],
        "head_sha": verdict["head_sha"], "verdict": receipt["verdict"],
        "findings_sha256": receipt["findings_sha256"],
    })


def review_verdict_json(verdict):
    """Canonical retained verdict bytes; provider observation is separate."""
    return canonical({key: value for key, value in verdict.items()
                      if key != "posting_observation"}).decode("utf-8")


def posting_collector_receipt_sha256(observation, observation_binding_sha256, registration):
    """Digest provider bytes and the complete runtime-owned posting binding."""
    payload = {
        "collector_id": observation["producer_id"],
        "collector_run_id": observation["run_id"],
        "provider_kind": registration["provider_kind"],
        "implementation_sha256": registration["implementation_sha256"],
        "release_sha256": registration["release_sha256"],
        "repository_id": observation["repository_id"],
        "pr_number": observation["pr_number"],
        "provider_response_sha256": observation["provider_response_sha256"],
        "posting_binding_sha256": observation_binding_sha256,
        "critic_artifact_binding": observation["critic_artifact_binding"],
        "review_verdict_record_id": observation["review_verdict_record_id"],
        "review_verdict_sha256": observation["review_verdict_sha256"],
        "comment_id": observation["comment_id"],
        "comment_url": observation["comment_url"],
        "comment_sha256": observation["comment_sha256"],
        "body_link": observation["body_link"],
        "body_sha256": observation["body_sha256"],
        "observed_at": observation["observed_at"],
    }
    return fingerprint("production-posting-collector-receipt", payload)


def review_round_receipt_sha256(receipt):
    """Hash the exact canonical retained round receipt bytes."""
    return sha256(canonical(receipt))


def cap_disposition_record_id(record):
    """Content-address cap binding fields through the signed owner record ID."""
    return fingerprint("review-cap-artifact-binding", {
        "critic_artifact_binding": record["critic_artifact_binding"],
        "review_verdict_record_id": record["review_verdict_record_id"],
        "review_verdict_sha256": record["review_verdict_sha256"],
    })


def evaluate(config, workflow, bundle, contracts, now, *, posting_collector_registry=None):
    # Production intentionally has no built-in collector registration.  Only a
    # runtime adapter may pass a registry.  Configuration and environment data
    # can narrow that registry but can never create membership.
    trusted_posting_collectors = posting_collector_registry or {}
    validate_config(config, workflow, contracts)
    if config["jira"].get("enabled", True) is False:
        raise ValidationError("Jira is disabled; this Jira evidence evaluator requires Jira scope. Continue provisional local planning through the native host.")
    if not config["merge_gate"]["trusted_owner_ids"]:
        raise ValidationError("$.merge_gate.trusted_owner_ids: configure actual trusted owner IDs before merge authorization")
    contracts.validate("evidence-bundle", bundle)
    from .review_completion import validate_gate_submission
    review_submission = validate_gate_submission(
        bundle["review_submission"], bundle["candidate"], bundle["contract"],
        bundle["critic"], bundle["specialists"])
    timestamp(now)
    binding = expected_binding(config, workflow, bundle)
    candidate, contract, snapshot = bundle["candidate"], bundle["contract"], bundle["snapshot"]
    for name, expected in [("host", config["github"]["host"]), ("repository_id", config["github"]["repository_id"]),
                           ("repository", config["github"]["repository"]), ("target_base_branch", config["github"]["base_branch"]),
                           ("merge_method", config["github"]["merge_method"])]:
        if candidate[name] != expected:
            raise ValidationError(f"Candidate {name} does not match trusted project policy")
    for name in ["project_id", "repository_id", "issue_id", "requirements_hash", "policy_hash"]:
        if contract[name] != binding[name]:
            raise ValidationError(f"Contract binding mismatch: {name}")
    if contract["target_base_branch"] != candidate["target_base_branch"]:
        raise ValidationError("Contract targets a different branch")
    if contract["acceptance_criteria"] != snapshot["acceptance_criteria"] or contract["dependencies"] != snapshot["dependencies"]:
        raise ValidationError("Contract omitted or altered snapshot requirements/dependencies")
    from .jira_lifecycle import owner_closure_required
    if owner_closure_required(config, snapshot) and not contract["owner_closure_required"]:
        raise ValidationError("Snapshot summary/labels match jira.owner_closure_keywords; the contract must set owner_closure_required")
    criteria = unique(contract["acceptance_criteria"], "id", "contract acceptance criterion")
    verdicts = bundle.get("review_verdicts", [])
    terminal_round_hint = max((v["round"] for v in verdicts), default=0)
    historical_review_run_ids = {v["run_id"] for v in verdicts if v["round"] != terminal_round_hint}
    records = ([bundle[k] for k in ["dispatch", "worker", "critic", "ci", "pr"]]
               + bundle["specialists"] + bundle["runs"])
    if bundle.get("owner_review") is not None:
        records.append(bundle["owner_review"])
    records += bundle.get("finding_dispositions", []) + ([bundle["cap_disposition"]] if bundle.get("cap_disposition") else [])
    unique(records, "record_id", "record ID")
    for record in records:
        historical_review_record = (record.get("run_id") in historical_review_run_ids
                                    and record in bundle.get("runs", []))
        if not historical_review_record and record["binding"] != binding:
            raise ValidationError(f"Cross-record binding mismatch: {record['record_id']}")
        if not historical_review_record:
            fresh(record["created_at"], now, config["validation"]["max_evidence_age_seconds"])
    unique(bundle["runs"], "run_id", "run ID")
    runs = {run["run_id"]: run for run in bundle["runs"]}
    for run in runs.values():
        if not set(run["capabilities"]).issubset(CAPABILITIES[run["role"]]):
            raise ValidationError("Run attestation grants capabilities outside its role")
        if run["status"] != "COMPLETE":
            raise ValidationError("Incomplete run cannot provide gate evidence")
        if run["role"] in config["execution"]["roles"] and run["model"] not in config["execution"]["roles"][run["role"]]["approved_model_ids"]:
            raise ValidationError("Run model not approved for this role")
    for name, role in [("dispatch", "controller"), ("worker", "worker"), ("critic", "critic"), ("ci", "collector"), ("pr", "collector")]:
        record = bundle[name]
        run = runs.get(record["run_id"])
        if not run or run["role"] != role or run["producer_id"] != record["producer_id"]:
            raise ValidationError(f"Unregistered/wrong-role producer for {name}")
        if name in {"ci", "pr"} and record["collector_attestation_id"] != run["record_id"]:
            raise ValidationError("Collector attestation reference mismatch")
    worker_run, critic_run = runs[bundle["worker"]["run_id"]], runs[bundle["critic"]["run_id"]]
    if worker_run["context_id"] == critic_run["context_id"] or worker_run["producer_id"] == critic_run["producer_id"]:
        raise ValidationError("Critic is not independent of implementation")
    dispositions, cap_disposition = verified_owner_records(config, contracts, bundle, binding, runs,
                                                            {worker_run["context_id"], critic_run["context_id"]},
                                                            {worker_run["producer_id"], critic_run["producer_id"]}, now)
    if bundle["worker"]["dispatch_id"] != bundle["dispatch"]["record_id"]:
        raise ValidationError("Worker result references a different dispatch")
    dispatch = bundle["dispatch"]
    if dispatch["disposition"] != "PERMITTED" or dispatch["collision_check"] != "PASS":
        raise ValidationError("Worker evidence has no permitted, collision-checked dispatch")
    if dispatch["lease"]["required"] and timestamp(dispatch["lease"]["expires_at"]) <= timestamp(dispatch["created_at"]):
        raise ValidationError("Dispatch lease was already expired when dispatch was recorded")
    if config["execution"]["host_broker"]["enabled"] and not dispatch["lease"]["required"]:
        raise ValidationError("Configured host broker requires a dispatch lease")
    if dispatch["contract_id"] != contract["contract_id"] or dispatch["contract_version"] != contract["contract_version"]:
        raise ValidationError("Dispatch references the wrong contract version")
    if sorted(dispatch.get("required_resources", [])) != sorted(contract["validation"].get("required_resources", [])):
        raise ValidationError("Dispatch did not copy the contract's required_resources")
    evidence_ids = unique(bundle["evidence_registry"], "uri", "evidence URI")
    for entry in bundle["evidence_registry"]:
        if timestamp(entry["retained_until"]) <= timestamp(now):
            raise ValidationError("Evidence retention has expired")
    def check_refs(value, field=""):
        if isinstance(value, dict):
            for key, child in value.items():
                check_refs(child, key)
        elif isinstance(value, list):
            if field in {"evidence", "evidence_checked", "attestation_evidence", "trigger_evidence", "resolution_evidence", "collision_evidence"}:
                if not set(value).issubset(evidence_ids):
                    raise ValidationError("Unresolved evidence reference")
            else:
                for child in value:
                    check_refs(child)
    check_refs(records)
    check_refs(bundle["prior_findings"])
    check_refs(contract["dependencies"])
    for name in ["worker", "critic"]:
        if unique(bundle[name]["acceptance_criteria"], "id", name + " acceptance criterion") != criteria:
            raise ValidationError("Acceptance-criterion coverage is incomplete")
    pr, critic, worker, ci = bundle["pr"], bundle["critic"], bundle["worker"], bundle["ci"]
    publication = bundle["publication_scan"]
    file_paths = unique(pr["file_manifest"], "path", "candidate file")
    if len({p.casefold() for p in file_paths}) != len(file_paths):
        raise ValidationError("Candidate contains case-colliding paths")
    for path in file_paths:
        safe_path(path)
    if set(worker["files_changed"]) != file_paths:
        raise ValidationError("Worker changed-file manifest does not match PR")
    tier = check_tier_declaration(config, contract, file_paths)
    classification = classify(config, file_paths,
                              risk_flags=[key for key, value in contract.get("risk_flags", {}).items() if value])
    # The declaration check remains authoritative for legacy records; only a
    # separately observed Tier 3 signal can raise the computed classification.
    if classification["tier"] != 3:
        classification["tier"] = tier
    if classification["tier"] != tier:
        raise ValidationError(f"Contract risk_tier {tier} does not match highest observed tier {classification['tier']}")
    declared_classification = contract.get("risk_classification")
    if declared_classification is None:
        raise ValidationError("Contract risk_classification is required for a current contract")
    durable_classification = classification
    bundle_classification = bundle.get("tier_classification")
    if declared_classification != durable_classification:
        raise ValidationError("Contract risk_classification does not match observed paths and risk evidence")
    if bundle_classification != durable_classification:
        raise ValidationError("Evidence bundle tier_classification is missing or stale")
    if not verdicts:
        raise ValidationError("Every consumed review round needs a current posted verdict record")
    unique(verdicts, "round", "review verdict round")
    expected_rounds = set(range(1, max(v["round"] for v in verdicts) + 1))
    if {v["round"] for v in verdicts} != expected_rounds:
        raise ValidationError("Review verdicts must cover every consumed round without gaps")
    terminal_round = max(v["round"] for v in verdicts)
    critic_run_ids = set()
    completion_cycle_ids = set()
    critic_artifacts_ok = True
    critic_artifact_verdicts_match = True
    posting_evidence_ok = True
    verified_artifact_bindings = []
    round_receipts = bundle.get("review_round_receipts", [])
    receipt_rounds = [item["critic_artifact_binding"]["round"] for item in round_receipts]
    if len(round_receipts) != len(verdicts) or len(receipt_rounds) != len(set(receipt_rounds)):
        critic_artifacts_ok = False
    receipts_by_round = {item["critic_artifact_binding"]["round"]: item
                         for item in round_receipts}
    if set(receipts_by_round) != expected_rounds:
        critic_artifacts_ok = False
    for verdict in sorted(verdicts, key=lambda item: item["round"]):
        # Verdicts are retained review records. Their candidate identity and
        # freshness are current-gate requirements, while the contract hash
        # may legitimately reflect the contract revision that the round
        # reviewed (legacy 1.9.3 records retain that hash).
        if (verdict["binding"]["project_id"] != binding["project_id"]
                or verdict["binding"]["repository_id"] != binding["repository_id"]
                or verdict["binding"]["issue_id"] != binding["issue_id"]
                or verdict["binding"]["requirements_hash"] != binding["requirements_hash"]
                or verdict["binding"]["policy_hash"] != binding["policy_hash"]
                or (verdict["round"] == terminal_round and verdict["binding"]["candidate_id"] != binding["candidate_id"])):
            raise ValidationError("Review verdict binding is stale for the evaluated candidate")
        if verdict["round"] == terminal_round:
            fresh(verdict["created_at"], now, config["validation"]["max_evidence_age_seconds"])
        if verdict["round"] == terminal_round and verdict["head_sha"] != candidate["head_sha"]:
            raise ValidationError("Review verdict is stale for the candidate head")
        if verdict["round"] == terminal_round and verdict["tier"] != tier:
            raise ValidationError("Review verdict tier does not match the recomputed contract tier")
        from .review_tiers import validate_round
        validate_round(verdict["tier"], verdict["round"], owner_cap_disposition=bundle.get("cap_disposition"), config=config)
        artifact_binding = verdict["critic_artifact_binding"]
        receipt = receipts_by_round.get(verdict["round"])
        critic_receipt = verdict.get("critic_review")
        run = runs.get(verdict["run_id"])
        if verdict["run_id"] in critic_run_ids:
            raise ValidationError("Each consumed review round needs a distinct independent critic run")
        if receipt is None:
            critic_artifacts_ok = False
            posting_evidence_ok = False
            if not run or run["producer_id"] != verdict["producer_id"]:
                raise ValidationError("Review verdict has no matching registered review run")
            critic_run_ids.add(verdict["run_id"])
            continue
        retained_result_json = receipt.get("result_json", "")
        result_bytes = retained_result_json.encode("utf-8")
        result_sha256 = sha256(result_bytes)
        observation_bytes = b""
        try:
            result = loads(retained_result_json)
            completion_snapshot = loads(receipt["completion_snapshot_json"])
            retained_verdict = loads(receipt["review_verdict_json"])
            observation_bytes = decode_base64_bytes(
                receipt["posting_observation_json"], "posting_observation_json")
            retained_observation = loads(observation_bytes.decode("utf-8"))
        except (ValidationError, UnicodeError):
            critic_artifacts_ok = False
            posting_evidence_ok = False
            result = completion_snapshot = retained_verdict = retained_observation = {}
        result_findings_valid = False
        if (isinstance(result, dict) and isinstance(result.get("findings"), list)
                and all(isinstance(item, dict) for item in result["findings"])):
            try:
                validate_findings(result["findings"], bundle["prior_findings"],
                                  [item["id"] for item in contract["acceptance_criteria"]])
            except ValidationError:
                pass
            else:
                result_findings_valid = True
        canonical_result = (isinstance(result, dict)
                            and set(result) == {"reviewer", "verdict", "findings"}
                            and result.get("reviewer") == verdict["reviewer_id"]
                            and result.get("verdict") in {"APPROVE", "REQUEST_CHANGES"}
                            and result_findings_valid
                            and canonical(result).decode("utf-8") == retained_result_json)
        completion_results = completion_snapshot.get("results", []) if isinstance(completion_snapshot, dict) else []
        completion_rows = [row for row in completion_results
                           if isinstance(row, dict) and row.get("reviewer_id") == verdict["reviewer_id"]]
        completion_tuple = completion_snapshot.get("tuple", {}) if isinstance(completion_snapshot, dict) else {}
        completion_bound = (
            isinstance(completion_snapshot, dict)
            and canonical(completion_snapshot).decode("utf-8") == receipt["completion_snapshot_json"]
            and fingerprint("review-completion", completion_snapshot) == receipt["completion_snapshot_sha256"]
            and completion_snapshot.get("cycle_id") == receipt["completion_cycle_id"]
            and completion_snapshot.get("tuple_sha256") == receipt["completion_tuple_sha256"]
            and completion_snapshot.get("reviewer_set_sha256") == receipt["completion_reviewer_set_sha256"]
            and fingerprint("review-tuple", completion_tuple) == receipt["completion_tuple_sha256"]
            and completion_tuple.get("head_sha") == verdict["head_sha"]
            and len(completion_rows) == 1
            and completion_rows[0].get("state") == "ACCEPTABLE"
            and completion_rows[0].get("result_sha256") == result_sha256
        )
        verdict_bytes = receipt["review_verdict_json"].encode("utf-8")
        expected_verdict_json = review_verdict_json(verdict)
        retained_records_bound = (
            retained_verdict == loads(expected_verdict_json)
            and receipt["review_verdict_json"] == expected_verdict_json
            and receipt["review_verdict_sha256"] == sha256(verdict_bytes)
            and retained_observation == verdict.get("posting_observation")
            and (isinstance(retained_observation, dict)
                 and canonical(retained_observation) == observation_bytes)
            and receipt["posting_observation_sha256"] == sha256(observation_bytes)
        )
        run_binding_matches = run is not None and run.get("binding") == verdict["binding"]
        artifact_bound = (
            canonical_result
            and artifact_binding == receipt["critic_artifact_binding"]
            and artifact_binding == {
                "critic_run_id": verdict["run_id"],
                "result_sha256": result_sha256,
                "head_sha": verdict["head_sha"],
                "round": verdict["round"],
            }
            and isinstance(critic_receipt, dict)
            and run is not None
            and run_binding_matches
            and run.get("created_at") == verdict["created_at"]
            and critic_receipt.get("run_id") == verdict["run_id"]
            and critic_receipt.get("round") == verdict["round"]
            and critic_receipt.get("head_sha") == verdict["head_sha"]
            and verdict["candidate_binding"]["repository_id"] == candidate["repository_id"]
            and verdict["candidate_binding"]["pr_number"] == candidate["pr_number"]
            and verdict["candidate_binding"]["head_sha"] == verdict["head_sha"]
            and (verdict["round"] != terminal_round
                 or verdict["candidate_binding"]["base_sha"] == candidate["target_base_sha"])
            and critic_receipt.get("verdict") == result.get("verdict")
            and critic_receipt.get("findings_sha256")
                == fingerprint("critic-findings", result.get("findings"))
            and result.get("verdict")
                == ("APPROVE" if verdict["verdict"] == "PASS" else "REQUEST_CHANGES")
            and completion_bound
            and retained_records_bound
        )
        critic_artifacts_ok = critic_artifacts_ok and artifact_bound
        if artifact_bound:
            if receipt["completion_cycle_id"] in completion_cycle_ids:
                critic_artifacts_ok = False
            completion_cycle_ids.add(receipt["completion_cycle_id"])
            receipt_uri = f"urn:awf:review-round-receipt:{verdict['round']}"
            receipt_entries = [entry for entry in bundle["evidence_registry"]
                               if entry["uri"] == receipt_uri]
            critic_artifacts_ok = critic_artifacts_ok and bool(receipt_entries) and all(
                entry["sha256"] == review_round_receipt_sha256(receipt)
                and entry["producer_id"] == verdict["producer_id"]
                for entry in receipt_entries
            )
            verified_artifact_bindings.append(artifact_binding)
        if verdict["round"] == terminal_round:
            terminal_findings_match = (
                result.get("findings") == critic["findings"]
            )
            critic_artifacts_ok = (critic_artifacts_ok
                                   and critic_receipt["record_id"] == critic["record_id"]
                                   and terminal_findings_match
                                   and completion_snapshot == review_submission["completion_snapshot"]
                                   and receipt["completion_snapshot_sha256"]
                                       == review_submission["completion_snapshot_sha256"])
            critic_artifact_verdicts_match = critic_artifact_verdicts_match and (
                result.get("verdict") == critic.get("verdict")
                and critic.get("run_id") == verdict["run_id"]
                and critic.get("producer_id") == verdict["producer_id"]
                and critic.get("binding") == verdict["binding"]
                and critic.get("created_at") == verdict["created_at"]
            )
        if not verdict["pr_comment_url"] or not verdict["pr_body_link"]:
            raise ValidationError("Review verdict must carry PR comment and body-link evidence")
        observation = verdict.get("posting_observation")
        if not isinstance(observation, dict) or observation.get("source") != "host_observation":
            posting_evidence_ok = False
        else:
            observation_run = runs.get(observation.get("run_id"))
            registration = trusted_posting_collectors.get(observation.get("producer_id"))
            configured_collectors = set(config["merge_gate"].get(
                "production_posting_collector_ids", []))
            candidate_prefix = (
                f"{candidate['host']}/{candidate['repository']}/pull/{candidate['pr_number']}"
            )
            expected_comment_url = f"{candidate_prefix}#issuecomment-{observation['comment_id']}"
            expected_body_link = (f"{candidate_prefix}#review-verdict-{verdict['round']}-"
                                  f"{verdict['record_id']}")
            registration_shape_ok = (isinstance(registration, dict)
                and set(registration) == {"provider_kind", "implementation_sha256",
                                          "release_sha256", "repository_ids", "receipts"}
                and registration.get("provider_kind") == observation.get("provider_kind")
                and re.fullmatch(r"[0-9a-f]{64}", registration.get("implementation_sha256", ""))
                and re.fullmatch(r"[0-9a-f]{64}", registration.get("release_sha256", ""))
                and candidate["repository_id"] in registration.get("repository_ids", [])
                and isinstance(registration.get("receipts"), dict))
            observation_receipt_basis = dict(observation)
            observation_receipt_basis["collector_receipt_sha256"] = "0" * 64
            observation_binding_sha256 = sha256(canonical(observation_receipt_basis))
            try:
                provider_response_bytes = decode_base64_bytes(
                    observation.get("provider_response_bytes"), "provider_response_bytes")
            except ValidationError:
                provider_response_matches = False
            else:
                provider_response_matches = (
                    observation.get("provider_response_sha256")
                    == sha256(provider_response_bytes))
            expected_collector_receipt = (posting_collector_receipt_sha256(
                observation, observation_binding_sha256, registration)
                if registration_shape_ok else None)
            if (bundle.get("provenance_mode") != "production_observation"
                    or observation_run is None or observation_run["role"] != "collector"
                    or observation_run["producer_id"] != observation.get("producer_id")
                    or not registration_shape_ok
                    or (configured_collectors
                        and observation.get("producer_id") not in configured_collectors)
                    or registration["receipts"].get(observation.get("run_id"))
                        != expected_collector_receipt
                    or observation.get("collector_receipt_sha256") != expected_collector_receipt
                    or observation.get("critic_artifact_binding") != artifact_binding
                    or observation.get("review_verdict_record_id") != verdict["record_id"]
                    or observation.get("review_verdict_sha256") != receipt["review_verdict_sha256"]
                    or observation.get("repository_id") != candidate["repository_id"]
                    or observation.get("pr_number") != candidate["pr_number"]
                    or observation.get("comment_url") != verdict["pr_comment_url"]
                    or observation.get("body_link") != verdict["pr_body_link"]
                    or observation.get("comment_url") != expected_comment_url
                    or observation.get("body_link") != expected_body_link
                    or observation.get("comment_sha256")
                        != sha256(observation.get("comment_bytes", "").encode("utf-8"))
                    or receipt["review_verdict_json"] not in observation.get("comment_bytes", "")
                    or observation.get("body_sha256")
                        != sha256(observation.get("body_bytes", "").encode("utf-8"))
                    or (f"review-verdict:{verdict['round']}:{verdict['record_id']}:"
                        f"{receipt['review_verdict_sha256']}" not in observation.get("body_bytes", ""))
                    or not provider_response_matches):
                posting_evidence_ok = False
        if verdict["round"] == terminal_round:
            if isinstance(observation, dict):
                fresh(observation["observed_at"], now, config["validation"]["max_evidence_age_seconds"])
                posting_evidence_ok = posting_evidence_ok and (
                    observation.get("body_sha256") == pr["body_sha256"])
        run = runs.get(verdict["run_id"])
        if not run or run["producer_id"] != verdict["producer_id"]:
            raise ValidationError("Review verdict has no matching registered review run")
        if verdict["owner_review"]:
            raise ValidationError("Owner/verifier assertions do not consume numbered critic review rounds")
        if run["role"] != "critic" or run["producer_id"] != verdict["reviewer_id"]:
            raise ValidationError("Review verdict must be bound to its independent critic run")
        if run["context_id"] == worker_run["context_id"] or run["producer_id"] == worker_run["producer_id"]:
            raise ValidationError("Review verdict critic run is not independent of the worker")
        if verdict["run_id"] in critic_run_ids:
            raise ValidationError("Each consumed review round needs a distinct independent critic run")
        critic_run_ids.add(verdict["run_id"])
        if not verdict["evidence"] or not set(verdict["evidence"]).issubset(evidence_ids):
            raise ValidationError("Review verdict evidence must resolve through the evidence registry")
    if critic["coverage"]["file_manifest_sha256"] != fingerprint("file-manifest", pr["file_manifest"]):
        raise ValidationError("Critic file manifest does not match PR")
    required_domains = tier1_specialist_domains(config, tier, specialist_domains(config, contract, file_paths,
            snapshot["summary"] + "\n" + snapshot["description"], pr["specialist_domains"]))
    unique(bundle["specialists"], "domain", "specialist domain")
    specialist_map = {r["domain"]: r for r in bundle["specialists"]}
    if set(specialist_map) != required_domains:
        raise ValidationError("Specialist coverage differs from required domains")
    for domain, review in specialist_map.items():
        run = runs.get(review["run_id"])
        if not run or run["role"] != "specialist" or run["producer_id"] != review["producer_id"]:
            raise ValidationError("Unregistered specialist run")
        if run["context_id"] == worker_run["context_id"] or run["producer_id"] == worker_run["producer_id"]:
            raise ValidationError("Specialist is not independent")
        if review["reviewer_identity"] != config["specialist_reviews"][domain]["reviewer_identity"]:
            raise ValidationError("Specialist identity is not configured")
    prior = unique(bundle["prior_findings"], "id", "prior finding ID")
    current_findings = critic["findings"] + [f for r in bundle["specialists"] for f in r["findings"]]
    current = unique(current_findings, "id", "current finding ID")
    from .review_tiers import round_cap
    from .review_tiers import _ticketed_finding_ids
    ticketed_p2 = _ticketed_finding_ids(bundle.get("ticketed_p2_records", []))
    open_p2 = {finding["id"] for finding in current_findings
               if finding["status"] != "RESOLVED" and finding["severity"] in {"P2", "MINOR"}}
    p2_ticketing_ok = (tier != 2 or not open_p2
                       or (terminal_round >= round_cap(2, config)
                           and open_p2.issubset(ticketed_p2)))
    if set(critic["prior_finding_ids"]) != prior or not prior.issubset(current):
        raise ValidationError("Prior findings were omitted from review lineage")
    for finding in current_findings:
        if finding["status"] == "RESOLVED" and not finding["resolution_evidence"]:
            raise ValidationError("Resolved finding has no resolution evidence")
    validate_findings(current_findings, bundle["prior_findings"], [c["id"] for c in contract["acceptance_criteria"]])
    for record in dispositions:
        if record["finding_id"] not in current:
            raise ValidationError(f"Finding disposition names an unknown finding: {record['finding_id']}")
    cap_accepted = ()
    if cap_disposition is not None:
        terminal_verdict_record = next(v for v in verdicts if v["round"] == terminal_round)
        terminal_receipt = receipts_by_round.get(terminal_round)
        if terminal_receipt is None:
            critic_artifacts_ok = False
        elif (cap_disposition["critic_artifact_binding"]
                != terminal_verdict_record["critic_artifact_binding"]
                or cap_disposition["review_verdict_record_id"]
                    != terminal_verdict_record["record_id"]
                or cap_disposition["review_verdict_sha256"]
                    != terminal_receipt["review_verdict_sha256"]):
            raise ValidationError(
                "Review cap disposition must bind the exact terminal verdict and critic artifact")
        if cap_disposition["decision"] == "EXTEND_ONE_CYCLE":
            # An authenticated extension is consumed as round-cap evidence;
            # it does not itself accept residual findings into the final gate.
            pass
        elif cap_disposition["decision"] != "MERGE_WITH_NOTES":
            raise ValidationError("Only MERGE_WITH_NOTES or an authenticated EXTEND_ONE_CYCLE cap disposition belongs in a gate bundle")
        else:
            open_ids = {f["id"] for f in current_findings if f["status"] != "RESOLVED"
                        and (f["severity"] in set(config["critic"]["blocking_severities"]) or is_boundary(f))}
            if not open_ids:
                raise ValidationError("MERGE_WITH_NOTES needs open serious findings to carry; with none open, the ordinary gate applies")
            if set(cap_disposition["open_finding_ids"]) != open_ids:
                raise ValidationError(f"Cap disposition lists {sorted(cap_disposition['open_finding_ids'])} but the open serious findings are {sorted(open_ids)}")
            cap_accepted = tuple(cap_disposition["open_finding_ids"])
    open_blocking, accepted = blocking_findings(config, tier, current_findings, dispositions, candidate["head_sha"], cap_accepted)
    no_blockers = not open_blocking
    # An ordinary terminal critic result must approve.  Only an authenticated
    # MERGE_WITH_NOTES cap disposition may carry the same bound artifact's
    # REQUEST_CHANGES result forward.
    lenient = (cap_disposition is not None
               and cap_disposition["decision"] == "MERGE_WITH_NOTES")
    owner = bundle.get("owner_review")
    owner_run = runs.get(owner.get("run_id")) if owner is not None else None
    owner_review_ok = tier != 3 or (owner is not None
                                    and owner.get("owner_review") is True
                                    and owner.get("verdict") == "PASS"
                                    and owner.get("head_sha") == candidate["head_sha"]
                                     and owner_run is not None
                                     and owner_run["role"] == "verifier"
                                     and owner_run["producer_id"] == owner.get("producer_id")
                                     and owner.get("owner_id") in config["merge_gate"]["trusted_owner_ids"]
                                     and owner.get("binding") == binding
                                     and owner.get("candidate_binding") == {
                                        "repository_id": candidate["repository_id"],
                                        "pr_number": candidate["pr_number"],
                                        "base_sha": candidate["target_base_sha"],
                                         "head_sha": candidate["head_sha"]}
                                     and owner.get("record_id") in {v.get("record_id") for v in records})
    critic_verdicts = [v for v in verdicts if not v["owner_review"]]
    terminal_critic = max(critic_verdicts, key=lambda v: v["round"]) if critic_verdicts else None
    terminal_verdict = next(v for v in verdicts if v["round"] == terminal_round)
    terminal_verdict_ok = (terminal_verdict["verdict"] in {"PASS", "REQUEST_CHANGES"}
                           if lenient else terminal_verdict["verdict"] == "PASS")
    critic_ok = (no_blockers and p2_ticketing_ok and critic_artifacts_ok
                 and critic_artifact_verdicts_match
                 and terminal_critic is not None
                 and terminal_verdict is terminal_critic
                 and terminal_verdict_ok
                 and critic["verdict"] in ({"APPROVE", "REQUEST_CHANGES"} if lenient else {"APPROVE"})
                 and owner_review_ok)
    provenance_problems = [f"exclusive resource {name} held by overlapping COMPLETE runs {a} and {b}"
                           for name, a, b in resource_overlaps(bundle["runs"], config["execution"]["host_broker"].get("resources", {}))]
    unique(ci["checks"], "name", "CI check name")
    checks = {check["name"]: check for check in ci["checks"]}
    ci_ok = bool(config["validation"]["required_ci_checks"]) and ci["retrieval_complete"] and candidate["tested_merge_sha"] is not None
    for required in config["validation"]["required_ci_checks"]:
        check = checks.get(required["name"])
        if not check:
            ci_ok = False
            continue
        for key in ["app_id", "workflow_path", "workflow_sha"]:
            if check[key] != required[key]:
                ci_ok = False
        fresh(check["completed_at"], now, config["validation"]["max_evidence_age_seconds"])
        ci_ok = ci_ok and check["conclusion"] == "success" and check["tests_executed"] >= required["min_tests_executed"]
        ci_ok = ci_ok and check["tested_tree_sha"] == candidate["integration_tree_sha"] and check["tested_commit_sha"] == candidate["tested_merge_sha"]
        if required.get("verifies_history") and check["checkout_depth"] != "full":
            provenance_problems.append(f"{required['name']}: verifies_history requires a full-history checkout, observed {check['checkout_depth']}")
    commands = unique(worker["validation"], "command", "validation command")
    commands_ok = set(contract["validation"]["commands"]).issubset(commands) and set(config["validation"]["commands"]).issubset(commands)
    for command in worker["validation"]:
        started, ended = timestamp(command["started_at"]), timestamp(command["finished_at"])
        if ended < started or (ended - started).total_seconds() > config["validation"]["test_timeout_seconds"]:
            raise ValidationError("Validation timing invalid or over budget")
        commands_ok = commands_ok and command["exit_code"] == 0 and command["clean_checkout"] and command["tested_tree_sha"] == candidate["head_tree_sha"]
        # Unevaluable files and undeclared skips are failures, never "zero tests".
        commands_ok = commands_ok and not command["unevaluable_files"]
        commands_ok = commands_ok and command["tests_executed"] >= command["tests_discovered"] - len(command["declared_skips"])
    paths_in_scope = all(any(fnmatch.fnmatchcase(path, pattern) for pattern in
                        contract["scope"]["expected_paths"] + contract["scope"]["allowed_adjacent_paths"]) for path in file_paths)
    protected = any(fnmatch.fnmatchcase(path, pattern) for path in file_paths for pattern in config["scope"]["protected_paths"])
    # Offline reference has no owner-scope-exception verifier: governance changes
    # require human review and are never auto-declared ready by this evaluator.
    scope_ok = paths_in_scope and pr["scope_pass"] and not protected and inside_scope(snapshot, config)
    ac_ok = all(ac["verdict"] == "PASS" for name in ["worker", "critic"] for ac in bundle[name]["acceptance_criteria"])
    ac_ok = ac_ok and closure_met(contract, worker, critic)
    parity_ok, parity_problems = local_ci_parity(config, contract, worker, ci)
    outcomes = {
        "review_completion": True,
        "verdict_posting": posting_evidence_ok and bool(verdicts) and all(
            v["pr_comment_url"].startswith(f"{candidate['host']}/{candidate['repository']}/pull/{candidate['pr_number']}#")
            and v["pr_body_link"].startswith(f"{candidate['host']}/{candidate['repository']}/pull/{candidate['pr_number']}#")
            for v in verdicts if v["round"] == terminal_round),
        "acceptance_criteria": ac_ok and commands_ok and worker["status"] == "COMPLETE" and worker["self_review_complete"] and not worker["blockers"],
        "scope": scope_ok,
        "critic_current_tuple": critic_ok,
        "specialist_reviews": all(r["verdict"] == "PASS" for r in bundle["specialists"]) and no_blockers,
        "required_ci": ci_ok, "ci_candidate_binding": ci_ok,
        "blocking_threads_zero": not any(t["status"] == "OPEN" for t in pr["blocking_threads"]),
        "dependencies": dependencies_satisfied(contract["dependencies"]) and pr["dependency_compatibility_pass"],
        "merge_compatibility": pr["mergeable"] and pr["ruleset_verified"] and pr["state"] == "OPEN" and not pr["draft"],
        "ticket_snapshot_current": contract["disposition"] == "READY",
        "review_coverage": pr["retrieval_complete"] and pr["classification_complete"] and critic["coverage"]["complete"] and not critic["coverage"]["omissions"] and set(critic["coverage"]["reviewed_paths"]) == file_paths,
        "provenance": not provenance_problems,
        "local_ci_parity": parity_ok,
        "publication_safety": publication_receipt_consistent(publication)
            and publication["status"] == "PASS" and publication["blocking_findings"] == 0
            and publication["unscanned_count"] == 0 and publication["base_sha"] == candidate["target_base_sha"]
            and publication["head_sha"] == candidate["head_sha"]
            and publication["pr_body_sha256"] == pr["body_sha256"],
    }
    # These are content-addressed references to the actual evaluated inputs.
    # They prove derivation identity, not the truth of externally supplied data.
    inputs = {
        "review_completion": ["review_submission", "candidate", "contract", "critic", "specialists"],
        "verdict_posting": ["review_verdicts", "pr", "candidate"],
        "acceptance_criteria": ["contract", "worker", "critic"], "scope": ["contract", "snapshot", "pr"],
        "critic_current_tuple": ["critic", "review_verdicts", "runs", "prior_findings"],
        "specialist_reviews": ["contract", "pr", "specialists"],
        "required_ci": ["ci"], "ci_candidate_binding": ["ci", "candidate"], "blocking_threads_zero": ["pr"],
        "dependencies": ["contract", "snapshot", "pr"], "merge_compatibility": ["pr", "candidate"],
        "ticket_snapshot_current": ["contract", "snapshot"], "review_coverage": ["critic", "pr"],
        "provenance": ["runs", "dispatch", "evidence_registry", "ci"],
        "local_ci_parity": ["worker", "ci", "contract"],
        "publication_safety": ["publication_scan", "candidate", "pr"],
    }
    refs = {name: ["urn:awf:input:" + key + ":" + fingerprint("gate-input", bundle[key]) for key in keys]
            for name, keys in inputs.items()}
    expires = timestamp(now) + timedelta(seconds=config["merge_gate"]["authorization_ttl_seconds"])
    gate = {"schema_version": 3, "record_id": str(uuid.uuid4()), "created_at": now,
        "producer_id": "offline-reference-evaluator", "run_id": str(uuid.uuid4()), "binding": binding,
        "candidate": candidate, "gates": {key: {"result": "PASS" if ok else "FAIL", "evidence": refs[key]} for key, ok in outcomes.items()},
        "record_ids": [r["record_id"] for r in records], "required_specialist_domains": sorted(required_domains),
        "risk_tier": tier, "tier_justification": contract["tier_justification"],
        "risk_classification": durable_classification,
        "closure_standard": contract["closure_standard"]["kind"],
        "residual_risks": ["Offline records have not been independently fetched from live services; this output grants no execution authority."]
            + [f"Accepted by owner disposition ({item['decision']}): {item['finding_id']} — {item['summary']}; {item['rationale']}" for item in accepted]
            + ([f"{'Extended one cycle' if cap_disposition['decision'] == 'EXTEND_ONE_CYCLE' else 'Merged with notes'} under owner cap disposition {cap_disposition['record_id']}: {item}"
                for item in cap_disposition["open_finding_ids"]]
               if cap_disposition is not None else [])
            + [f"Provenance: {item}" for item in provenance_problems] + [f"Local/CI parity: {item}" for item in parity_problems],
        "accepted_findings": [item["finding_id"] for item in accepted],
        "verified_critic_artifact_bindings": verified_artifact_bindings,
        "review_submission": review_submission,
        "conclusion": "READY_FOR_OWNER_AUTHORIZATION" if all(outcomes.values()) else "NOT_READY",
        "execution_authority": False, "evaluation_mode": "offline_reference", "expires_at": expires.isoformat().replace("+00:00", "Z")}
    if not required_domains:
        gate["gates"]["specialist_reviews"]["result"] = "N_A"
    contracts.validate("final-gate", gate)
    return gate
