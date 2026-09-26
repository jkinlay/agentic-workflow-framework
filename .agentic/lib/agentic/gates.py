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


def evaluate(config, workflow, bundle, contracts, now):
    validate_config(config, workflow, contracts)
    if config["jira"].get("enabled", True) is False:
        raise ValidationError("Jira is disabled; this Jira evidence evaluator requires Jira scope. Continue provisional local planning through the native host.")
    if not config["merge_gate"]["trusted_owner_ids"]:
        raise ValidationError("$.merge_gate.trusted_owner_ids: configure actual trusted owner IDs before merge authorization")
    contracts.validate("evidence-bundle", bundle)
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
    records = [bundle[k] for k in ["dispatch", "worker", "critic", "ci", "pr"]] + bundle["specialists"] + bundle["runs"]
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
        if cap_disposition["decision"] != "MERGE_WITH_NOTES":
            raise ValidationError("Only a MERGE_WITH_NOTES cap disposition belongs in a gate bundle; PARK/RESCOPE/EXTEND leave the review states")
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
    critic_ok = no_blockers and critic["verdict"] in ({"APPROVE", "REQUEST_CHANGES"} if lenient else {"APPROVE"})
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
        "publication_safety": publication["status"] == "PASS" and not publication["findings"]
            and not publication["unscanned"] and publication["base_sha"] == candidate["target_base_sha"]
            and publication["head_sha"] == candidate["head_sha"]
            and publication["pr_body_sha256"] == pr["body_sha256"],
    }
    # These are content-addressed references to the actual evaluated inputs.
    # They prove derivation identity, not the truth of externally supplied data.
    inputs = {
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
        "conclusion": "READY_FOR_OWNER_AUTHORIZATION" if all(outcomes.values()) else "NOT_READY",
        "execution_authority": False, "evaluation_mode": "offline_reference", "expires_at": expires.isoformat().replace("+00:00", "Z")}
    if not required_domains:
        gate["gates"]["specialist_reviews"]["result"] = "N_A"
    contracts.validate("final-gate", gate)
    return gate
