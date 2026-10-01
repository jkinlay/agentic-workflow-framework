#!/usr/bin/env python3
"""Generate complete inert forms and a deterministic offline evidence example."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.canonical import fingerprint, sha256
from agentic import VERSION
from agentic.lifecycle import definition
from agentic.policy import CAPABILITIES, PROTECTED_PATHS, policy_hash
from agentic.review_policy import DEFAULT_RISK_TIERS
from generate_contracts import catalog

NOW = "2026-09-09T12:00:00Z"


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "https://example.invalid/awf/" + name))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def config(example=True):
    role = {"model": "fixture-model" if example else "CHANGE_ME_APPROVED_MODEL", "reasoning_effort": "high",
            "fallback": "deny", "approved_model_ids": ["fixture-model" if example else "CHANGE_ME_APPROVED_MODEL"],
            "permission_profile": "documented-reference-profile", "network_allowlist": []}
    domains = {
        "security": {"required_when_triggered": True, "paths": ["auth/**", "security/**"], "keywords": ["credential", "authentication", "secret"], "risk_flags": ["security"], "reviewer_identity": "fixture-security" if example else "CHANGE_ME_SECURITY_REVIEWER"},
        "data_migration": {"required_when_triggered": True, "paths": ["migrations/**", "schema/**"], "keywords": ["migration", "schema"], "risk_flags": ["schema_or_migration", "data_loss"], "reviewer_identity": "fixture-data" if example else "CHANGE_ME_DATA_REVIEWER"},
        "public_api": {"required_when_triggered": True, "paths": ["api/**", "openapi/**"], "keywords": ["breaking interface", "public API"], "risk_flags": ["public_api"], "reviewer_identity": "fixture-api" if example else "CHANGE_ME_API_REVIEWER"},
        "operations": {"required_when_triggered": True, "paths": ["infra/**", "deploy/**"], "keywords": ["production", "race condition"], "risk_flags": ["concurrency", "production"], "reviewer_identity": "fixture-operations" if example else "CHANGE_ME_OPERATIONS_REVIEWER"},
    }
    result = {"version": 3, "template": {"expected_workflow_version": VERSION},
        "project": {"id": uid("project") if example else str(uuid.UUID(int=0)), "name": "Offline Fixture" if example else "CHANGE_ME_PROJECT", "short_name": "EX" if example else "CHANGE_ME"},
        "jira": {"enabled": example, "site": "https://jira.example.invalid" if example else None, "project_key": "EX" if example else None,
            "scope": {"allow_entire_project": False, "selector_mode": "all", "included_epics": [], "labels_any": ["awf-fixture"] if example else [], "components_any": [], "additional_jql": "", "ownership_required": True},
            "status_map": {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress", "in_review": "In Review", "done": "Done"},
            "lifecycle_writes": {"in_progress": True, "in_review": True, "done": True},
            "owner_closure_keywords": ["release", "publication", "qualification", "cutover", "production acceptance"],
            "dependency_direction": "requires", "mutations_owner": "controller"},
        "github": {"host": "https://github.com", "repository_id": 101 if example else None, "repository": "fixture/example" if example else "CHANGE_ME/CHANGE_ME",
            "base_branch": "main", "branch_pattern": "codex/{ticket}-{slug}", "merge_method": "squash", "draft_pr_first": True, "one_repository_per_controller": True},
        "execution": {"profile": "manual_reference", "max_parallel_tickets": 6, "max_parallel_tickets_per_stream": 1,
            "native_streams": {"enabled": True, "dispatch_policy": "ready_independent"},
            "independent_reviewers": {"allocation": "one_per_stream"},
            "max_agent_runs_per_ticket": 12, "max_spawn_depth": 1, "max_amendment_cycles": 3, "max_cap_extensions": 2,
            "risk_tiers": copy.deepcopy(DEFAULT_RISK_TIERS), "transient_retry_limit": 2,
            "max_run_seconds": 3600, "max_tool_calls_per_run": 100, "max_tokens_per_ticket": 2000000,
            "max_cost_microusd_per_ticket": None, "daily_project_cost_microusd": None, "one_writer_per_ticket": True,
            "roles": {name: copy.deepcopy(role) for name in ["controller", "worker", "critic", "specialist"]},
            "route_capabilities": {"observation_path": ".agentic/route-capabilities.json", "max_age_days": 30},
            "host_broker": {"enabled": False, "broker_id": "", "lease_before_dispatch": True, "max_workers": 3, "max_heavy_jobs": 1, "max_gpu_jobs": 0, "resources": {}}},
        "validation": {"commands": ["python -m unittest discover -s tests" if example else "CHANGE_ME_TEST_COMMAND"], "ci_candidate_policy": "synthetic_merge_required", "require_candidate_bound_ci_evidence": True,
            "required_ci_checks": [{"name": "unit-tests", "app_id": 42, "workflow_path": ".github/workflows/test.yml", "workflow_sha": "a" * 40, "min_tests_executed": 1, "verifies_history": False}] if example else [],
            "max_evidence_age_seconds": 1800, "test_timeout_seconds": 1800},
        "scope": {"protected_paths": PROTECTED_PATHS, "generated_paths": [], "architecture_docs": [], "product_docs": []},
        "critic": {"required": True, "independent_context": True, "blocking_severities": ["BLOCKER", "MAJOR"],
            "require_re_review_after_any_head_change": True, "require_re_review_after_any_target_base_change": True, "require_full_final_review": True},
        "specialist_reviews": domains,
        "merge_gate": {"human_authorization_required": True, "execution_after_authorization": "owner_manual", "automatic_merge_enabled": False,
            "require_critic_approval_current_tuple": True, "require_specialist_reviews_current_tuple": True, "require_required_ci_green": True,
            "require_zero_unresolved_blocking_threads": True, "invalidate_on_head_change": True, "invalidate_on_target_base_change": True,
            "authorization_ttl_seconds": 900, "trusted_owner_ids": [1001] if example else [], "high_risk_owner_quorum": 1},
        "controller": {key: False for key in ["dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments", "auto_transition_jira"]},
        "audit": {"store_must_be_outside_worktrees": True, "retention_days": 90, "redact_secrets": True},
        "portfolio": {"read_only": True, "cross_project_dispatch": False}}
    from agentic.model_routing import default_policy
    routing = default_policy()
    result["execution"]["model_routing"] = routing
    for name, values in routing["role_defaults"].items():
        result["execution"]["roles"][name].update(
            model=values["model"], reasoning_effort=values["reasoning_effort"],
            approved_model_ids=routing["role_allowed_models"][name])
    return result


def example_bundle(cfg):
    ac = [{"id": "AC1", "text": "Return the requested label.", "validation": "Assert the output label."},
          {"id": "AC2", "text": "Reject empty input.", "validation": "Assert empty input raises an error."}]
    snapshot = {"schema_version": 3, "issue_id": "2001", "project_key": "EX", "summary": "Validate a label", "description": "Offline illustrative fixture only.",
        "acceptance_criteria": ac, "dependencies": [], "linked_requirements": [], "epic_ids": [], "labels": ["awf-fixture"], "component_ids": []}
    candidate = {"host": "https://github.com", "repository_id": 101, "repository": "fixture/example", "pr_number": 7, "target_base_branch": "main",
        "head_sha": "b" * 40, "target_base_sha": "c" * 40, "merge_base_sha": "c" * 40, "head_tree_sha": "d" * 40,
        "integration_tree_sha": "e" * 40, "tested_merge_sha": "f" * 40, "diff_sha256": "a" * 64, "merge_method": "squash"}
    contract = {"schema_version": 3, "contract_id": uid("contract"), "contract_version": 1, "created_at": NOW,
        "project_id": cfg["project"]["id"], "repository_id": 101, "issue_id": "2001", "ticket": "EX-1",
        "requirements_hash": fingerprint("requirements", snapshot), "policy_hash": policy_hash(cfg, definition()),
        "objective": "Validate one label", "acceptance_criteria": ac, "dependencies": [], "branch_origin_sha": "c" * 40, "target_base_branch": "main",
        "scope": {"expected_paths": ["src/example.py"], "allowed_adjacent_paths": [], "protected_interfaces": [], "forbidden_changes": ["unrelated changes"], "governance_change_authorization": None,
                  "evidence_paths": ["evidence/**", "docs/**"]},
        "validation": {"commands": cfg["validation"]["commands"], "test_cases": ["normal label", "empty input"], "tests_not_applicable_reason": None,
                       "required_resources": [], "platform_distinction": None},
        "risk_flags": {key: False for key in ["security", "schema_or_migration", "public_api", "data_loss", "concurrency", "production"]}, "specialist_domains": [], "disposition": "READY",
        "risk_tier": 2, "tier_justification": "Product source under src/ is outside the Tier 1 eligible paths.",
        "closure_standard": {"kind": "FULL", "accepted_limitations": [], "evidence_required": ["validation command exit 0"]},
        "owner_closure_required": False, "corrects": None}
    binding = {"project_id": cfg["project"]["id"], "repository_id": 101, "issue_id": "2001", "requirements_hash": contract["requirements_hash"],
        "contract_hash": fingerprint("contract", contract), "policy_hash": contract["policy_hash"], "candidate_id": fingerprint("candidate", candidate)}
    def record(name, producer_role, **values):
        return {"schema_version": 3, "record_id": uid(name), "created_at": NOW, "producer_id": "fixture-" + producer_role,
                "run_id": uid("run-" + producer_role), "binding": copy.deepcopy(binding), **values}
    evidence = ["urn:awf:fixture:example-evidence"]
    runs = [record("attestation-" + role, role, role=role, context_id=uid("context-" + role), parent_run_id=None,
            model=cfg["execution"]["roles"].get(role, {}).get("model", "fixture-model"), runtime_version="fixture-runtime-1", instruction_hash="a" * 64, permission_profile_hash="b" * 64,
            capabilities=sorted(CAPABILITIES[role]), status="COMPLETE", attestation_evidence=evidence, resources_held=[])
            for role in ["controller", "worker", "critic", "collector"]]
    dispatch = record("dispatch", "controller", contract_id=contract["contract_id"], contract_version=1, role="worker", branch="codex/EX-1-label", worktree_id=uid("worktree"),
        lease={"required": True, "lease_id": uid("lease"), "fencing_token": 1, "expires_at": "2026-09-09T12:15:00Z"},
        collision_check="PASS", collision_evidence=evidence, disposition="PERMITTED", control_generation=1, required_resources=[])
    from agentic.operating import default_operating, validate_operating
    dispatch["operating_hash"] = validate_operating(default_operating(), cfg).operating_hash
    results = [{"id": item["id"], "verdict": "PASS", "evidence": evidence} for item in ac]
    worker = record("worker-result", "worker", status="COMPLETE", commit_route="WORKER", dispatch_id=dispatch["record_id"], files_changed=["src/example.py"],
        tested_tree=candidate["head_tree_sha"], changes=[{"path": "src/example.py", "action": "modified"}], ignored_untracked=[], acceptance_criteria=results,
        validation=[{"command": cfg["validation"]["commands"][0], "started_at": "2026-09-09T11:59:59Z", "finished_at": NOW,
            "exit_code": 0, "tested_tree_sha": candidate["head_tree_sha"], "clean_checkout": True, "evidence": evidence,
            "tests_discovered": 2, "tests_executed": 2, "declared_skips": [], "unevaluable_files": []}],
        self_review_complete=True, findings_addressed=[], blockers=[], closure={"result": "MET", "evidence": evidence})
    files = [{"path": "src/example.py", "blob_sha": "1" * 40}]
    critic = record("critic-review", "critic", verdict="APPROVE", acceptance_criteria=results, findings=[], prior_finding_ids=[],
        closure={"result": "MET", "evidence": evidence}, coverage={"complete": True, "file_manifest_sha256": fingerprint("file-manifest", files), "reviewed_paths": ["src/example.py"], "omissions": []}, evidence_checked=evidence)
    ci = record("ci", "collector", retrieval_complete=True, candidate_type="synthetic_merge", checks=[{"name": "unit-tests", "check_id": "check-1", "app_id": 42,
        "workflow_path": ".github/workflows/test.yml", "workflow_sha": "a" * 40, "attempt": 1, "event": "pull_request", "conclusion": "success",
        "tested_tree_sha": candidate["integration_tree_sha"], "tested_commit_sha": candidate["tested_merge_sha"], "tests_executed": 2, "completed_at": NOW, "evidence": evidence, "checkout_depth": "full"}], collector_attestation_id=uid("attestation-collector"))
    pr_body = "Synthetic draft body.\n"
    pr = record("pr", "collector", state="OPEN", draft=False, mergeable=True, retrieval_complete=True, file_manifest=files,
        body_sha256=sha256(pr_body.encode("utf-8")), blocking_threads=[], scope_pass=True,
        dependency_compatibility_pass=True, ruleset_verified=True, specialist_domains=[], classification_complete=True, collector_attestation_id=uid("attestation-collector"), evidence=evidence)
    publication_scan = {"schema_version": 3, "status": "PASS", "base_sha": candidate["target_base_sha"],
        "head_sha": candidate["head_sha"], "pr_body_sha256": pr["body_sha256"], "additional_pr_body_sha256": [],
        "comment_sha256": [], "mapping_sha256": None, "project_config_sha256": None, "mapping_loaded": False,
        "mapping_location": ".agentic-state/publication-deny.json", "commits_scanned": [candidate["head_sha"]],
        "findings": [], "unscanned": [], "coverage": {"current_files": True, "commit_messages": True,
            "every_patch": True, "generated_reports": "when committed or passed as provider text",
            "captured_command_output": "when committed or passed as provider text", "pr_bodies": True, "pr_comments": False},
        "execution_authority": False}
    return {"schema_version": 3, "candidate": candidate, "snapshot": snapshot, "contract": contract, "dispatch": dispatch, "worker": worker, "critic": critic,
            "specialists": [], "ci": ci, "pr": pr, "runs": runs, "prior_findings": [], "finding_dispositions": [], "cap_disposition": None,
            "publication_scan": publication_scan, "evidence_registry": [{"uri": evidence[0],
                "sha256": sha256(b"Illustrative evidence; no external test was executed.\n"), "producer_id": "fixture-collector", "retained_until": "2030-01-01T00:00:00Z"}], "provenance_mode": "offline_fixture"}


def sample(schema, schemas):
    if "$ref" in schema:
        return sample(schemas[schema["$ref"].split(":")[-1]], schemas)
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if "enum" in schema:
        options = schema["enum"]
        return next((v for v in ["DRAFT", "PROPOSAL", "NOT_READY", "INCOMPLETE", "UNKNOWN", "FAIL", "REQUESTED", "DENY"] if v in options), options[0])
    if "oneOf" in schema:
        return None if any(s.get("type") == "null" for s in schema["oneOf"]) else sample(schema["oneOf"][0], schemas)
    kind = schema.get("type")
    if kind == "object":
        # Draft forms expose optional evidence bindings as well as required
        # fields; nullable bindings remain explicitly unobserved until filled.
        return {key: sample(child, schemas) for key, child in schema["properties"].items()}
    if kind == "array":
        return [sample(schema["items"], schemas) for _ in range(schema.get("minItems", 0))]
    if kind == "boolean":
        return False
    if kind == "integer":
        return schema.get("minimum", 0)
    if schema.get("format") == "uuid":
        return str(uuid.UUID(int=0))
    if schema.get("format") == "date-time":
        return "1970-01-01T00:00:00Z"
    if schema.get("format") == "uri":
        return "urn:awf:UNFILLED"
    if "{40}" in schema.get("pattern", ""):
        return "0" * 40
    if "{64}" in schema.get("pattern", ""):
        return "0" * 64
    return "UNFILLED"


def main():
    schemas = catalog()
    from agentic.operating import default_operating
    write(ROOT / "OPERATING_CONFIG.yaml", default_operating())
    write(ROOT / ".agentic/examples/OPERATING_CONFIG.yaml", default_operating())
    write(ROOT / ".agentic/workflow.yaml", definition())
    write(ROOT / ".agentic/PROJECT_CONFIG.yaml", config(False))
    write(ROOT / ".agentic/examples/unconfigured-project.yaml", config(False))
    write(ROOT / ".agentic/workflow-version.yaml", {"template": {"name": "generic-agentic-development-workflow", "version": VERSION, "schema_revision": 3},
        "installation": {"install_id": None, "last_operation": None, "operation_at": None, "source_manifest_sha256": None, "profile": "manual_reference"}})
    cfg = config(True)
    write(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml", cfg)
    bundle = example_bundle(cfg)
    write(ROOT / ".agentic/examples/evidence-bundle.json", bundle)
    from agentic.contracts import Contracts
    from agentic.digest import render
    from agentic.gates import evaluate
    gate = evaluate(cfg, definition(), bundle, Contracts(ROOT / ".agentic/schemas"), NOW)
    finding = {"id": "F1", "severity": "MAJOR", "summary": "Fixture finding F1", "evidence": ["urn:awf:fixture:example-evidence"], "status": "OPEN",
               "resolution_evidence": [], "basis": {"criterion_id": "AC1"}, "supersedes_finding_id": None, "path": "src/example.py"}
    state = {"ticket": "EX-1", "awf_state": "MERGED", "pr_number": 7, "head_sha": "b" * 40, "base_sha": "c" * 40, "tree_sha": "e" * 40, "risk_tier": 2}
    (ROOT / ".agentic/examples/digest-jira.md").write_text(render(state, audience="jira", gate=gate, findings=[finding],
        validation=bundle["worker"]["validation"], ci=bundle["ci"]["checks"], reviewer={"engine": "codex", "run_id": "r-1"}), encoding="utf-8", newline="\n")
    (ROOT / ".agentic/examples/evidence.txt").write_text("Illustrative evidence; no external test was executed.\n", encoding="utf-8", newline="\n")
    for name, schema in schemas.items():
        if name in {"project-config", "evidence-bundle", "candidate"}:
            continue
        draft = {"template_for": name, "status": "UNFILLED", "instructions": "Replace all draft values, extract record, then validate shape AND semantics. This wrapper cannot satisfy a runtime record schema.", "record": sample(schema, schemas)}
        filename = "project-status" if name == "project-state" else name
        write(ROOT / ".agentic/templates" / (filename + (".json" if name == "controller-event" else ".yaml")), draft)
    print("Generated complete draft forms, configuration and offline evidence fixture")


if __name__ == "__main__":
    main()
