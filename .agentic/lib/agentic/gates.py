"""Offline evidence evaluation. Returns analysis, never an execution permit."""
from __future__ import annotations
from datetime import timedelta
import fnmatch
import uuid

from . import ValidationError
from .canonical import fingerprint, fresh, timestamp, unique
from .policy import (CAPABILITIES, dependencies_satisfied, inside_scope, safe_path,
                     specialist_domains, validate_config)
from .review_policy import (blocking_findings, check_tier_declaration, closure_met, is_boundary,
                            tier1_specialist_domains, validate_findings)
from .review_tiers import classify


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
    """Authenticate every owner record in the bundle before any of them can change a gate."""
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


def evaluate(config, workflow, bundle, contracts, now):
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
    records = ([bundle[k] for k in ["dispatch", "worker", "critic", "ci", "pr"]]
               + bundle["specialists"] + bundle["runs"])
    if bundle.get("owner_review") is not None:
        records.append(bundle["owner_review"])
    records += bundle.get("finding_dispositions", []) + ([bundle["cap_disposition"]] if bundle.get("cap_disposition") else [])
    unique(records, "record_id", "record ID")
    for record in records:
        if record["binding"] != binding:
            raise ValidationError(f"Cross-record binding mismatch: {record['record_id']}")
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
    # 1.9.3 reference bundles may predate durable retention of the additive
    # security specialist signal.  Preserve their established tier/path
    # classification while carrying the observed signal in the recomputed
    # classification; every other classification change remains stale.
    legacy_security_classification = (
        declared_classification != classification
        and all(declared_classification.get(key) == classification.get(key)
                for key in ("tier", "matched_tiers", "evidence", "rule"))
        and set(declared_classification.get("risk_flags", []))
            | {"security"} == set(classification.get("risk_flags", []))
        and "security" not in declared_classification.get("risk_flags", []))
    # A 1.9.3 retained classification may lack only the additive security
    # signal.  Normalize that one historical omission for comparison, while
    # still requiring the bundle to carry an exact durable classification.
    durable_classification = classification
    if legacy_security_classification:
        durable_classification = dict(classification)
        durable_classification["risk_flags"] = list(declared_classification.get("risk_flags", []))
    if declared_classification != durable_classification:
        raise ValidationError("Contract risk_classification does not match observed paths and risk evidence")
    if bundle.get("tier_classification") != durable_classification:
        raise ValidationError("Evidence bundle tier_classification is missing or stale")
    if not verdicts:
        raise ValidationError("Every consumed review round needs a current posted verdict record")
    unique(verdicts, "round", "review verdict round")
    expected_rounds = set(range(1, max(v["round"] for v in verdicts) + 1))
    if {v["round"] for v in verdicts} != expected_rounds:
        raise ValidationError("Review verdicts must cover every consumed round without gaps")
    for verdict in verdicts:
        # Verdicts are retained provider observations.  Their candidate
        # identity and freshness are current-gate requirements, while the
        # contract hash may legitimately reflect the contract revision that
        # the observed round reviewed (legacy 1.9.3 records retain that hash).
        if (verdict["binding"]["project_id"] != binding["project_id"]
                or verdict["binding"]["repository_id"] != binding["repository_id"]
                or verdict["binding"]["issue_id"] != binding["issue_id"]
                or verdict["binding"]["requirements_hash"] != binding["requirements_hash"]
                or verdict["binding"]["policy_hash"] != binding["policy_hash"]
                or verdict["binding"]["candidate_id"] != binding["candidate_id"]):
            raise ValidationError("Review verdict binding is stale for the evaluated candidate")
        fresh(verdict["created_at"], now, config["validation"]["max_evidence_age_seconds"])
        if verdict["head_sha"] != candidate["head_sha"]:
            raise ValidationError("Review verdict is stale for the candidate head")
        if verdict["tier"] != tier:
            raise ValidationError("Review verdict tier does not match the recomputed contract tier")
        from .review_tiers import validate_round
        validate_round(tier, verdict["round"], owner_cap_disposition=bundle.get("cap_disposition"))
        if not verdict["pr_comment_url"] or not verdict["pr_body_link"]:
            raise ValidationError("Review verdict must carry PR comment and body-link evidence")
        run = runs.get(verdict["run_id"])
        if not run or run["producer_id"] != verdict["producer_id"]:
            raise ValidationError("Review verdict has no matching registered provider run")
        if verdict["owner_review"]:
            if run["role"] != "verifier":
                raise ValidationError("Owner review verdict must come from a verifier run")
        else:
            if run["role"] != "critic" or run["producer_id"] != verdict["reviewer_id"]:
                raise ValidationError("Review verdict must be bound to its independent critic run")
        if verdict["owner_review"]:
            receipt = verdict.get("provider_receipt")
            if (run["role"] != "verifier" or verdict["provider_observed"] is not True
                    or not isinstance(receipt, dict)
                    or receipt.get("candidate_binding") != verdict["candidate_binding"]
                    or receipt.get("owner_id") != verdict["owner_id"]
                    or not receipt.get("immutable_id")
                    or not receipt.get("provider")):
                raise ValidationError("Owner review needs an immutable provider receipt bound to the candidate and verifier run")
            if verdict["owner_id"] not in config["merge_gate"]["trusted_owner_ids"]:
                raise ValidationError("Owner review actor is not a configured trusted owner")
        registry_by_sha = {entry["sha256"] for entry in bundle["evidence_registry"]}
        if not verdict["evidence"] or not set(verdict["evidence"]).issubset(evidence_ids):
            raise ValidationError("Review verdict evidence must resolve through the evidence registry")
        if verdict["provider_receipt"]["evidence_sha256"] not in registry_by_sha:
            raise ValidationError("Review verdict provider receipt is not resolved by the evidence registry")
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
    # Tier 2 needs the critic's APPROVE; Tier 1 findings advise the owner, so a
    # REQUEST_CHANGES verdict passes once every serious finding is dispositioned.
    # An owner's verified MERGE_WITH_NOTES carries the listed findings as notes in either tier.
    lenient = tier == 1 or cap_disposition is not None
    owner = bundle.get("owner_review")
    owner_run = runs.get(owner.get("run_id")) if owner is not None else None
    owner_receipt = owner.get("provider_receipt") if owner is not None else None
    owner_review_ok = tier != 3 or (owner is not None
                                    and owner.get("owner_review") is True
                                    and owner.get("verdict") == "PASS"
                                    and owner.get("head_sha") == candidate["head_sha"]
                                    and owner.get("provider_observed") is True
                                    and isinstance(owner.get("provider_receipt"), dict)
                                    and owner["provider_receipt"].get("candidate_binding") == owner.get("candidate_binding")
                                    and owner["provider_receipt"].get("owner_id") == owner.get("owner_id")
                                    and owner["provider_receipt"].get("immutable_id")
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
    if tier == 3 and owner_review_ok:
        if owner_receipt["evidence_sha256"] not in {entry["sha256"] for entry in bundle["evidence_registry"]}:
            raise ValidationError("Owner review provider receipt is not resolved by the evidence registry")
    critic_verdicts = [v for v in verdicts if not v["owner_review"]]
    terminal_critic = max(critic_verdicts, key=lambda v: v["round"]) if critic_verdicts else None
    critic_ok = (no_blockers and terminal_critic is not None and terminal_critic["verdict"] == "PASS"
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
        "verdict_posting": bool(verdicts) and all(v["head_sha"] == candidate["head_sha"]
                                                   and v["pr_comment_url"] and v["pr_body_link"] for v in verdicts),
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
        "critic_current_tuple": ["critic", "prior_findings"], "specialist_reviews": ["contract", "pr", "specialists"],
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
        "closure_standard": contract["closure_standard"]["kind"],
        "residual_risks": ["Offline records have not been independently fetched from live services; this output grants no execution authority."]
            + [f"Accepted by owner disposition ({item['decision']}): {item['finding_id']} — {item['summary']}; {item['rationale']}" for item in accepted]
            + ([f"Merged with notes under owner cap disposition {cap_disposition['record_id']}: {item}" for item in cap_disposition["open_finding_ids"]]
               if cap_disposition is not None else [])
            + [f"Provenance: {item}" for item in provenance_problems] + [f"Local/CI parity: {item}" for item in parity_problems],
        "accepted_findings": [item["finding_id"] for item in accepted],
        "review_submission": review_submission,
        "conclusion": "READY_FOR_OWNER_AUTHORIZATION" if all(outcomes.values()) else "NOT_READY",
        "execution_authority": False, "evaluation_mode": "offline_reference", "expires_at": expires.isoformat().replace("+00:00", "Z")}
    if not required_domains:
        gate["gates"]["specialist_reviews"]["result"] = "N_A"
    contracts.validate("final-gate", gate)
    return gate
