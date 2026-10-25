"""Current-tree AC1 integration tripwire; historical red/green runs stay delivery evidence.

Inline inert fixtures exercise evaluate, not another AWF-117 test module. The
range-check fake isolates delegation/result consumption from AC2/AC3/AC5's
separately owned Git/parser tests. It never replaces evaluate or a gate result.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
NOW = '2026-09-09T12:00:00Z'

# Frozen, neutral policy data; no repository configuration is changed or loaded.
CONFIG = {'version': 3,
 'template': {'expected_workflow_version': '1.9.4'},
 'project': {'id': '94fdd7f1-0a6d-5cad-9573-462539ffcd4d',
             'name': 'Offline Fixture',
             'short_name': 'EX'},
 'jira': {'enabled': True,
          'cloud_id': 'fixture-cloud',
          'site': 'https://jira.example.invalid',
          'provider_project_id': 'fixture-project',
          'project_key': 'EX',
          'controller_actor_id': 'fixture-controller',
          'scope': {'allow_entire_project': False,
                    'selector_mode': 'all',
                    'included_epics': [],
                    'labels_any': ['awf-fixture'],
                    'components_any': [],
                    'additional_jql': '',
                    'ownership_required': True},
          'status_map': {'backlog': 'Backlog',
                         'ready': 'Ready',
                         'in_progress': 'In Progress',
                         'in_review': 'In Review',
                         'done': 'Done'},
          'lifecycle_writes': {'in_progress': True, 'in_review': True, 'done': True},
          'owner_closure_keywords': ['release',
                                     'publication',
                                     'qualification',
                                     'cutover',
                                     'production acceptance'],
          'dependency_direction': 'requires',
          'mutations_owner': 'controller'},
 'github': {'host': 'https://github.com',
            'repository_id': 101,
            'repository': 'fixture/example',
            'expected_actor_id': 1002,
            'expected_actor_login': 'fixture-owner',
            'auth_profile': 'fixture',
            'base_branch': 'main',
            'branch_pattern': 'codex/{ticket}-{slug}',
            'merge_method': 'squash',
            'draft_pr_first': True,
            'one_repository_per_controller': True},
 'execution': {'profile': 'manual_reference',
               'max_parallel_tickets': 6,
               'max_parallel_tickets_per_stream': 1,
               'child_env_strip_extra': [],
               'native_streams': {'enabled': True, 'dispatch_policy': 'ready_independent'},
               'independent_reviewers': {'allocation': 'shared_critic'},
               'max_agent_runs_per_ticket': 16,
               'max_spawn_depth': 1,
               'max_amendment_cycles': 3,
               'max_cap_extensions': 2,
               'risk_tiers': {'tier1_eligible_paths': ['tests/**',
                                                       'fixtures/**',
                                                       'tools/**',
                                                       'evidence/**',
                                                       'docs/**',
                                                       '*.manifest.json'],
                              'tier1_excluded_paths': [],
                              'tier1_review': {'roles': ['critic'],
                                               'specialist_when_touching': ['security'],
                                               'findings': 'advisory'},
                              'tier2_review': {'roles': ['critic', 'specialist'],
                                               'findings': 'blocking'},
                              'tier3_review': {'roles': ['critic', 'specialist'],
                                               'findings': 'blocking',
                                               'max_rounds': 3}},
               'transient_retry_limit': 2,
               'max_run_seconds': 3600,
               'max_tool_calls_per_run': 100,
               'max_tokens_per_ticket': 2000000,
               'max_cost_microusd_per_ticket': None,
               'daily_project_cost_microusd': None,
               'one_writer_per_ticket': True,
               'roles': {'controller': {'model': 'gpt-5.6-sol',
                                        'reasoning_effort': 'high',
                                        'fallback': 'deny',
                                        'approved_model_ids': ['gpt-5.6-luna',
                                                               'gpt-5.6-sol',
                                                               'gpt-6.1-sol'],
                                        'permission_profile': 'documented-reference-profile',
                                        'network_allowlist': []},
                         'worker': {'model': 'gpt-5.6-luna',
                                    'reasoning_effort': 'medium',
                                    'fallback': 'deny',
                                    'approved_model_ids': ['gpt-5.6-luna',
                                                           'gpt-5.6-sol',
                                                           'gpt-6.1-sol'],
                                    'permission_profile': 'documented-reference-profile',
                                    'network_allowlist': []},
                         'critic': {'model': 'gpt-5.6-sol',
                                    'reasoning_effort': 'high',
                                    'fallback': 'deny',
                                    'approved_model_ids': ['gpt-5.6-luna',
                                                           'gpt-5.6-sol',
                                                           'gpt-6.1-sol'],
                                    'permission_profile': 'documented-reference-profile',
                                    'network_allowlist': []},
                         'specialist': {'model': 'gpt-5.6-sol',
                                        'reasoning_effort': 'high',
                                        'fallback': 'deny',
                                        'approved_model_ids': ['gpt-5.6-luna',
                                                               'gpt-5.6-sol',
                                                               'gpt-6.1-sol'],
                                        'permission_profile': 'documented-reference-profile',
                                        'network_allowlist': []}},
               'route_capabilities': {'observation_path': '.agentic/route-capabilities.json',
                                      'max_age_days': 30},
               'host_broker': {'enabled': False,
                               'broker_id': '',
                               'lease_before_dispatch': True,
                               'max_workers': 3,
                               'max_heavy_jobs': 1,
                               'max_gpu_jobs': 0,
                               'resources': {}},
               'model_routing': {'routing': '.agentic/routing.yaml',
                                 'high_risk_flags': ['security',
                                                     'permissions',
                                                     'schema_or_migration',
                                                     'data_loss',
                                                     'concurrency',
                                                     'production',
                                                     'public_api',
                                                     'architecture'],
                                 'agent_overrides': {},
                                 'ticket_overrides': {},
                                 'escalation': {'enabled': True,
                                                'max_escalations_per_ticket': 2,
                                                'max_reasoning_failures_per_phase': 3,
                                                'effort_ceiling': 'high'},
                                 'budgets': {'max_runs_per_ticket': 16,
                                             'max_runs_per_project_day': 250,
                                             'max_tokens_per_ticket': 2000000,
                                             'max_tokens_per_project_day': 30000000,
                                             'max_cost_microusd_per_ticket': None,
                                             'max_cost_microusd_per_project_day': None},
                                 'adaptive': {'mode': 'shadow',
                                              'min_reviewed_samples': 20,
                                              'min_success_rate_percent': 95},
                                 'reconciliation': {'enabled': False,
                                                    'authorized_operator_ids': [],
                                                    'max_reconciled_incident_retries_per_ticket': 2}}},
 'validation': {'commands': ['python -m unittest discover -s tests'],
                'ci_candidate_policy': 'synthetic_merge_required',
                'require_candidate_bound_ci_evidence': True,
                'required_ci_checks': [{'name': 'unit-tests',
                                        'app_id': 42,
                                        'workflow_path': '.github/workflows/test.yml',
                                        'workflow_sha': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                                        'min_tests_executed': 1,
                                        'verifies_history': False}],
                'max_evidence_age_seconds': 1800,
                'test_timeout_seconds': 1800},
 'scope': {'protected_paths': ['AGENTS.md',
                               '**/AGENTS.md',
                               '.agentic/**',
                               '.codex/**',
                               '.github/workflows/**',
                               '.github/CODEOWNERS',
                               'CODEOWNERS',
                               'scripts/bootstrap_project.py'],
           'generated_paths': [],
           'architecture_docs': [],
           'product_docs': []},
 'critic': {'required': True,
            'independent_context': True,
            'blocking_severities': ['BLOCKER', 'MAJOR'],
            'require_re_review_after_any_head_change': True,
            'require_re_review_after_any_target_base_change': True,
            'require_full_final_review': True},
 'specialist_reviews': {'security': {'required_when_triggered': True,
                                     'paths': ['auth/**', 'security/**'],
                                     'keywords': ['credential', 'authentication', 'secret'],
                                     'risk_flags': ['security'],
                                     'reviewer_identity': 'fixture-security'},
                        'data_migration': {'required_when_triggered': True,
                                           'paths': ['migrations/**', 'schema/**'],
                                           'keywords': ['migration', 'schema'],
                                           'risk_flags': ['schema_or_migration', 'data_loss'],
                                           'reviewer_identity': 'fixture-data'},
                        'public_api': {'required_when_triggered': True,
                                       'paths': ['api/**', 'openapi/**'],
                                       'keywords': ['breaking interface', 'public API'],
                                       'risk_flags': ['public_api'],
                                       'reviewer_identity': 'fixture-api'},
                        'operations': {'required_when_triggered': True,
                                       'paths': ['infra/**', 'deploy/**'],
                                       'keywords': ['production', 'race condition'],
                                       'risk_flags': ['concurrency', 'production'],
                                       'reviewer_identity': 'fixture-operations'}},
 'merge_gate': {'human_authorization_required': True,
                'execution_after_authorization': 'owner_manual',
                'automatic_merge_enabled': False,
                'require_critic_approval_current_tuple': True,
                'require_specialist_reviews_current_tuple': True,
                'require_required_ci_green': True,
                'require_zero_unresolved_blocking_threads': True,
                'invalidate_on_head_change': True,
                'invalidate_on_target_base_change': True,
                'authorization_ttl_seconds': 900,
                'trusted_owner_ids': [1001],
                'high_risk_owner_quorum': 1,
                'production_posting_collector_ids': ['github-production-posting-collector']},
 'controller': {'dispatch_enabled': False,
                'auto_dispatch': False,
                'auto_request_critic': False,
                'auto_resume_amendments': False,
                'auto_transition_jira': False,
                'status_cadence_seconds': 600},
 'audit': {'store_must_be_outside_worktrees': True, 'retention_days': 90, 'redact_secrets': True},
 'portfolio': {'read_only': True, 'cross_project_dispatch': False}}


def _bundle(cfg, operating=None):
    import base64
    import copy
    import uuid
    from agentic.canonical import canonical, fingerprint, sha256
    from agentic.lifecycle import definition
    from agentic.policy import CAPABILITIES, policy_hash
    from agentic.review_tiers import classify
    from agentic.review_completion import gate_review_aggregate, gate_review_tuple
    from agentic.gates import (critic_artifact_receipt_sha256,
                               posting_collector_receipt_sha256,
                               review_round_receipt_sha256, review_verdict_json)

    def uid(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://example.invalid/ac1/' + name))

    def base64_bytes(raw):
        return {'encoding': 'base64', 'data': base64.b64encode(raw).decode('ascii')}

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
    classification = classify(cfg, ["src/example.py"], risk_flags=[])
    contract["risk_classification"] = classification
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
    operating = default_operating() if operating is None else copy.deepcopy(operating)
    dispatch["operating_hash"] = validate_operating(operating, cfg).operating_hash
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
    aggregate = gate_review_aggregate(candidate, critic, [])
    review_tuple = gate_review_tuple(candidate, contract, aggregate)
    reviewers = aggregate["required_reviewers"]
    cycle_id = uid("review-cycle")
    critic_result_json = canonical({
        "reviewer": critic["producer_id"], "verdict": critic["verdict"],
        "findings": critic["findings"],
    }).decode("utf-8")
    critic_result_sha256 = sha256(critic_result_json.encode("utf-8"))
    completion_snapshot = {"cycle_id": cycle_id, "tuple": review_tuple,
        "tuple_sha256": fingerprint("review-tuple", review_tuple),
        "required_reviewers": reviewers,
        "reviewer_set_sha256": fingerprint("reviewer-set", reviewers),
        "counts": {"required": 1, "completed": 1, "acceptable": 1,
                   "failed": 0, "stale": 0, "outstanding": 0},
        "results": [{"reviewer_id": reviewers[0], "state": "ACCEPTABLE",
            "result_sha256": critic_result_sha256,
            "terminal_at": NOW}]}
    submission_id = uid("review-submission")
    review_submission = {"submission_id": submission_id, "cycle_id": cycle_id,
        "completion_snapshot": completion_snapshot,
        "completion_snapshot_sha256": fingerprint("review-completion", completion_snapshot),
        "aggregate": aggregate, "aggregate_sha256": fingerprint("review-aggregate", aggregate),
        "provider_preconditions": {"operation_id": submission_id, "prepared_at": NOW,
            "repository": review_tuple["repository"],
            "base_sha": review_tuple["base_sha"], "head_sha": review_tuple["head_sha"],
            "head_tree_sha": review_tuple["head_tree_sha"],
            "tuple_sha256": completion_snapshot["tuple_sha256"],
            "reviewer_set_sha256": completion_snapshot["reviewer_set_sha256"],
            "completion_snapshot_sha256": fingerprint("review-completion", completion_snapshot),
            "aggregate_sha256": fingerprint("review-aggregate", aggregate)},
        "execution_authority": False}
    ci = record("ci", "collector", retrieval_complete=True, candidate_type="synthetic_merge", checks=[{"name": "unit-tests", "check_id": "check-1", "app_id": 42,
        "workflow_path": ".github/workflows/test.yml", "workflow_sha": "a" * 40, "attempt": 1, "event": "pull_request", "conclusion": "success",
        "tested_tree_sha": candidate["integration_tree_sha"], "tested_commit_sha": candidate["tested_merge_sha"], "tests_executed": 2, "completed_at": NOW, "evidence": evidence, "checkout_depth": "full"}], collector_attestation_id=uid("attestation-collector"))
    pr = record("pr", "collector", state="OPEN", draft=False, mergeable=True, retrieval_complete=True, file_manifest=files,
        body_sha256="0" * 64, blocking_threads=[], scope_pass=True,
        dependency_compatibility_pass=True, ruleset_verified=True, specialist_domains=[], classification_complete=True, collector_attestation_id=uid("attestation-collector"), evidence=evidence)
    publication_scan = {"schema_version": 3, "status": "PASS", "base_sha": candidate["target_base_sha"],
        "head_sha": candidate["head_sha"], "pr_body_sha256": pr["body_sha256"], "additional_pr_body_sha256": [],
        "comment_sha256": [], "mapping_sha256": None, "project_config_sha256": None, "mapping_loaded": False,
        "mapping_location": ".agentic-state/publication-deny.json", "commits_scanned": [candidate["head_sha"]],
        "findings": [], "total_findings": 0, "blocking_findings": 0, "pre_existing_findings": 0,
        "unscanned": [], "unscanned_count": 0, "coverage": {"current_files": True, "commit_messages": True,
            "every_patch": True, "generated_reports": "when committed or passed as provider text",
            "captured_command_output": "when committed or passed as provider text", "strict_utf8": True,
            "pr_bodies": True, "pr_comments": False},
        "execution_authority": False}
    critic_artifact_uri = f"urn:awf:critic-review:{critic['record_id']}"
    artifact_binding = {"critic_run_id": critic["run_id"],
                        "result_sha256": critic_result_sha256,
                        "head_sha": candidate["head_sha"], "round": 1}
    verdict = record("review-verdict-1", "critic", verdict="PASS", tier=classification["tier"], round=1,
        head_sha=candidate["head_sha"], reviewer_id="fixture-critic", pr_comment_url="https://github.com/fixture/example/pull/7#issuecomment-1",
        pr_body_link=f"https://github.com/fixture/example/pull/7#review-verdict-1-{uid('review-verdict-1')}", evidence=evidence + [critic_artifact_uri],
        critic_artifact_binding=artifact_binding,
        owner_review=False, owner_id=1001,
        candidate_binding={"repository_id": candidate["repository_id"], "pr_number": candidate["pr_number"],
                           "base_sha": candidate["target_base_sha"], "head_sha": candidate["head_sha"]},
        critic_review={"record_id": critic["record_id"], "run_id": critic["run_id"], "round": 1, "head_sha": candidate["head_sha"],
                       "verdict": "APPROVE", "findings_sha256": fingerprint("critic-findings", critic["findings"])},
        )
    verdict.pop("schema_version", None)
    verdict_json = review_verdict_json(verdict)
    verdict_sha256 = sha256(verdict_json.encode("utf-8"))
    pr_body = ("Synthetic draft body.\n\n"
               f"review-verdict:1:{verdict['record_id']}:{verdict_sha256}\n")
    pr["body_sha256"] = sha256(pr_body.encode("utf-8"))
    publication_scan["pr_body_sha256"] = pr["body_sha256"]
    provider_response = canonical({
        "comment_id": 1, "comment_url": verdict["pr_comment_url"],
        "comment_body": verdict_json, "pr_body": pr_body,
    })
    posting = {"source": "host_observation", "observed_at": NOW,
        "producer_id": "fixture-collector",
        "run_id": next(run["run_id"] for run in runs if run["role"] == "collector"),
        "provider_kind": "github", "critic_artifact_binding": artifact_binding,
        "review_verdict_record_id": verdict["record_id"],
        "review_verdict_sha256": verdict_sha256,
        "repository_id": candidate["repository_id"], "pr_number": candidate["pr_number"],
        "comment_id": 1, "comment_url": verdict["pr_comment_url"],
        "comment_bytes": verdict_json, "comment_sha256": sha256(verdict_json.encode("utf-8")),
        "body_link": verdict["pr_body_link"], "body_bytes": pr_body,
        "body_sha256": pr["body_sha256"],
        "provider_response_bytes": base64_bytes(provider_response),
        "provider_response_sha256": sha256(provider_response),
        "collector_receipt_sha256": "0" * 64}
    test_registration = {"provider_kind": "github", "implementation_sha256": "a" * 64,
        "release_sha256": "b" * 64, "repository_ids": [candidate["repository_id"]], "receipts": {}}
    # The receipt binds the final observation digest.  Its own field is blanked
    # only for that digest calculation, avoiding a self-referential hash.
    posting_digest_basis = copy.deepcopy(posting)
    posting_digest_basis["collector_receipt_sha256"] = "0" * 64
    posting_digest = sha256(canonical(posting_digest_basis))
    posting["collector_receipt_sha256"] = posting_collector_receipt_sha256(
        posting, posting_digest, test_registration)
    verdict["posting_observation"] = posting
    posting_bytes = canonical(posting)
    completion_snapshot_json = canonical(completion_snapshot).decode("utf-8")
    round_receipt = {"critic_artifact_binding": artifact_binding,
        "result_json": critic_result_json, "completion_cycle_id": cycle_id,
        "completion_tuple_sha256": completion_snapshot["tuple_sha256"],
        "completion_reviewer_set_sha256": completion_snapshot["reviewer_set_sha256"],
        "completion_snapshot_json": completion_snapshot_json,
        "completion_snapshot_sha256": review_submission["completion_snapshot_sha256"],
        "review_verdict_json": verdict_json, "review_verdict_sha256": verdict_sha256,
        "posting_observation_json": base64_bytes(posting_bytes),
        "posting_observation_sha256": sha256(posting_bytes)}
    return {"schema_version": 3, "candidate": candidate, "snapshot": snapshot, "contract": contract, "dispatch": dispatch, "worker": worker, "critic": critic,
            "specialists": [], "review_submission": review_submission, "ci": ci, "pr": pr, "runs": runs, "prior_findings": [], "finding_dispositions": [], "cap_disposition": None,
            "publication_scan": publication_scan, "tier_classification": classification, "review_verdicts": [verdict],
            "review_round_receipts": [round_receipt],
            "ticketed_p2_records": [],
            "evidence_registry": [{"uri": evidence[0],
                "sha256": sha256(b"Illustrative evidence; no external test was executed.\n"), "producer_id": "fixture-collector", "retained_until": "2030-01-01T00:00:00Z"},
               {"uri": "urn:awf:review-round-receipt:1", "sha256": review_round_receipt_sha256(round_receipt),
                "producer_id": verdict["producer_id"], "retained_until": "2030-01-01T00:00:00Z"},
               {"uri": critic_artifact_uri, "sha256": critic_artifact_receipt_sha256(verdict),
                "producer_id": verdict["producer_id"], "retained_until": "2030-01-01T00:00:00Z"}],
            "provenance_mode": "offline_fixture"}


class Ac1RedSuiteTests(unittest.TestCase):
    def test_ac1_red_suite_precedes_gate_implementation(self):
        # Planned production imports are deliberately inside the test: discovery
        # must work even before the commit-provenance integration is shipped.
        import base64
        from agentic import gates, provenance
        from agentic.canonical import canonical, fingerprint, sha256
        from agentic.contracts import Contracts
        from agentic.lifecycle import definition

        class RuntimeConfig(dict):
            def get(self, key, default=None):
                if key != '_awf_runtime':
                    return super().get(key, default)
                assignment = {
                    'assignment_id': 'ac1-inline-assignment', 'ticket_key': 'EX-1',
                    'tier': 2, 'round_cap': 2, 'owner_review_required': False,
                }
                assignment['assignment_sha256'] = fingerprint(
                    'intake_assignment_record', assignment)
                return {'active_ticket_key': 'EX-1',
                        'intake_assignments': {'EX-1': assignment}}

        config = RuntimeConfig(deepcopy(CONFIG))
        config['merge_gate']['production_posting_collector_ids'] = ['fixture-collector']
        contracts = Contracts(ROOT / '.agentic/schemas')

        def fixture(*, carrier=True):
            bundle = _bundle(config)
            verdict = bundle['review_verdicts'][0]
            observation = verdict['posting_observation']
            receipt = bundle['review_round_receipts'][0]
            # Keep all caller-supplied IDs and digest bindings valid in both
            # variants. Only the explicit raw carrier changes for AC4.
            comment = gates.review_verdict_json(verdict)
            if carrier:
                comment += '\nCritic run: ' + verdict['run_id'] + '\n'
            observation['comment_bytes'] = comment
            observation['comment_sha256'] = sha256(comment.encode('utf-8'))
            response = canonical({
                'comment_id': observation['comment_id'],
                'comment_url': observation['comment_url'],
                'comment_body': comment, 'pr_body': observation['body_bytes'],
            })
            observation['provider_response_bytes'] = {
                'encoding': 'base64', 'data': base64.b64encode(response).decode('ascii')}
            observation['provider_response_sha256'] = sha256(response)
            registration = {
                'provider_kind': 'github', 'implementation_sha256': 'a' * 64,
                'release_sha256': 'b' * 64,
                'repository_ids': [bundle['candidate']['repository_id']], 'receipts': {},
            }
            basis = deepcopy(observation)
            basis['collector_receipt_sha256'] = '0' * 64
            digest = gates.posting_collector_receipt_sha256(
                observation, sha256(canonical(basis)), registration)
            observation['collector_receipt_sha256'] = digest
            registration['receipts'][observation['run_id']] = digest
            raw = canonical(observation)
            receipt['posting_observation_json'] = {
                'encoding': 'base64', 'data': base64.b64encode(raw).decode('ascii')}
            receipt['posting_observation_sha256'] = sha256(raw)
            entry = next(item for item in bundle['evidence_registry']
                         if item['uri'] == 'urn:awf:review-round-receipt:1')
            entry['sha256'] = gates.review_round_receipt_sha256(receipt)
            bundle['provenance_mode'] = 'production_observation'
            return bundle, {observation['producer_id']: registration}

        # These are the existing check_commit_range result contract, not a fake
        # final gate. The control proves unrelated gate evidence can pass.
        candidate = _bundle(config)['candidate']
        passed = {
            'status': 'PASS', 'base': candidate['merge_base_sha'],
            'head': candidate['head_sha'], 'origin_main': candidate['target_base_sha'],
            'object_format': 'sha1', 'commits': [], 'refusals': [],
            'override_sha256': None, 'provider_override_evidence': None,
            'run_logs_required': True,
        }
        cases = (
            ('AC2 untagged code commit', 'Implement feature', (),
             'missing Writer trailer', True),
            ('AC3 non-mechanical script commit',
             'Implement feature\n\nWriter: script/manifest-regen', (),
             'script-tagged commit is not reproducibly mechanical: src/example.py', True),
            ('AC4 verdict without critic-run carrier',
             'Implement feature\n\nWriter: codex/11111111-1111-4111-8111-111111111111',
             (), None, False),
            ('AC5 label-only fallback', 'Implement feature', ('fallback: bot-written',),
             'missing Writer trailer', True),
        )
        with tempfile.TemporaryDirectory(prefix='awf117-ac1-') as temporary:
            repository = Path(temporary)

            def evaluate_with(bundle, registry, range_result):
                calls = []

                def check_range(*args, **kwargs):
                    calls.append((args, kwargs))
                    return deepcopy(range_result)

                # Cover both module-qualified lookup and the planned imported
                # alias. create=True introduces no production implementation;
                # evaluate must actually call the checker and consume REFUSE.
                with patch.object(provenance, 'check_commit_range', side_effect=check_range), \
                     patch.object(gates, 'check_commit_range', side_effect=check_range,
                                  create=True):
                    result = gates.evaluate(
                        config, definition(), bundle, contracts, NOW,
                        posting_collector_registry=registry, repository=repository)
                return result, calls

            control, registry = fixture()
            result, _ = evaluate_with(control, registry, passed)
            self.assertEqual('READY_FOR_OWNER_AUTHORIZATION', result['conclusion'],
                             'AC1 positive control must pass unrelated gates: ' + repr({
                                 key: value['result'] for key, value in result['gates'].items()}))
            for name, message, labels, reason, carrier in cases:
                with self.subTest(case=name):
                    bundle, registry = fixture(carrier=carrier)
                    range_result = deepcopy(passed)
                    commit = {
                        'sha': candidate['head_sha'], 'writer': (
                            'script/manifest-regen' if 'Writer: script/' in message else None),
                        'changed_files': ['src/example.py'], 'changed_entries': [],
                        'mechanical': False, 'mechanical_reason': reason,
                        'run_log': None, 'status': 'REFUSE' if reason else 'PASS',
                        'reasons': [reason] if reason else [],
                    }
                    range_result['commits'] = [commit]
                    if reason:
                        range_result.update(status='REFUSE', refusals=[{
                            'sha': candidate['head_sha'], 'reason': reason}])
                    # Provider labels alone never constitute an owner override.
                    self.assertIsNone(range_result['override_sha256'])
                    if labels:
                        self.assertEqual(('fallback: bot-written',), labels)
                    result, calls = evaluate_with(bundle, registry, range_result)
                    self.assertEqual(
                        'NOT_READY', result['conclusion'],
                        name + ': evaluate ignored commit provenance or the raw critic carrier')
                    if reason:
                        self.assertEqual('FAIL', result['gates']['provenance']['result'], name)
                    self.assertTrue(calls, name + ': evaluate must call check_commit_range')
                    args, kwargs = calls[0]
                    self.assertEqual(repository, Path(args[0] if args else kwargs['root']))
                    self.assertEqual(candidate['merge_base_sha'],
                                     args[1] if len(args) > 1 else kwargs['base'])
                    self.assertEqual(candidate['head_sha'],
                                     args[2] if len(args) > 2 else kwargs['head'])


if __name__ == '__main__':
    unittest.main()
