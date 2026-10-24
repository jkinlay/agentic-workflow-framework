"""AWF-117 doc 45 AC4/AC5: deliberately red merge-gate regressions.

All evidence below is synthetic, inline and inert. No provider is contacted.
The two tests exercise the real schemas and evaluator; the provenance adapter
fake supplies results from the real range checker over an isolated Git repo.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
NOW = "2026-09-09T12:00:00Z"


def _repository(root):
    # Git metadata belongs only to this temporary fixture, never the checkout.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE=NOW, GIT_COMMITTER_DATE=NOW)

    def git(*args):
        return subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
             *args], cwd=root, env=env, check=True, capture_output=True,
            text=True, encoding="utf-8", timeout=30).stdout.strip()

    git("init", "--quiet", "--initial-branch=main", "--object-format=sha1")
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/test.yml").write_bytes(b"name: fixture\n")
    (root / "src").mkdir()
    (root / "src/example.py").write_bytes(b"value = 0\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "Fixture base")
    base = git("rev-parse", "HEAD")
    (root / "src/example.py").write_bytes(b"value = 1\n")
    git("add", "src/example.py")
    git("commit", "--quiet", "-m", "Untagged code change")
    return git, base, git("rev-parse", "HEAD")


def _fixture():
    from agentic.contracts import Contracts
    from agentic.lifecycle import definition

    values = json.loads(INLINE_EVIDENCE)

    class ReviewedConfig(dict):
        def get(self, key, default=None):
            if key == "_awf_runtime":
                return values["runtime"]
            return super().get(key, default)

    return (ReviewedConfig(values["config"]), definition(), values["bundle"],
            Contracts(ROOT / ".agentic/schemas"), values["registry"])


class ProvenanceGateOverrideTests(unittest.TestCase):
    def test_ac4_merge_gate_refuses_verdict_without_critic_run_carrier(self):
        from agentic import ValidationError
        from agentic import gates, provenance

        config, workflow, bundle, contracts, registry = _fixture()
        verdict = bundle["review_verdicts"][0]
        receipt = bundle["review_round_receipts"][0]
        registered = next(run for run in bundle["runs"]
                          if run["run_id"] == verdict["run_id"])
        self.assertEqual("critic", registered["role"])
        self.assertEqual(verdict["run_id"], verdict["critic_review"]["run_id"])
        self.assertEqual(verdict["run_id"],
                         receipt["critic_artifact_binding"]["critic_run_id"])
        self.assertEqual(bundle["candidate"]["head_sha"], verdict["head_sha"])
        self.assertNotIn("Critic run:", json.dumps(bundle))
        # Doc 45 open owner question 4: receipt, terminal verdict, or both?
        # Omit the carrier everywhere, without prescribing its future location.
        # The existing primitive proves that copied IDs alone are insufficient.
        compact = {
            "run_id": verdict["run_id"], "head_sha": verdict["head_sha"],
            "result_sha256": verdict["critic_artifact_binding"]["result_sha256"],
            "critic_run_carrier": f"Critic run: {verdict['run_id']}"}
        bound = provenance.validate_critic_run_binding(
            compact, reviewed_head=bundle["candidate"]["head_sha"])
        self.assertEqual(registered["run_id"], bound["critic_run_id"])
        with self.assertRaisesRegex(ValidationError, "CRITIC_LINE_INVALID"):
            provenance.validate_critic_run_binding(dict(
                compact, critic_run_carrier="Critic run: 11111111-1111-4111-8111-111111111111"))
        with self.assertRaisesRegex(ValidationError, "different reviewed head"):
            provenance.validate_critic_run_binding(compact, reviewed_head="1" * 40)
        with self.assertRaisesRegex(ValidationError, "CRITIC_LINE_INVALID"):
            provenance.validate_critic_run_binding(dict(
                verdict, result_sha256=verdict["critic_artifact_binding"]["result_sha256"]))

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, base, head = _repository(root)
            self.assertEqual((base, head), (bundle["candidate"]["merge_base_sha"],
                                           bundle["candidate"]["head_sha"]))
            try:
                contracts.validate("review-verdict", verdict)
                contracts.validate("evidence-bundle", bundle)
                result = gates.evaluate(
                    config, workflow, bundle, contracts, NOW,
                    posting_collector_registry=registry, repository=root)
            except ValidationError as exc:
                # Only a carrier-specific schema/semantic refusal satisfies AC4.
                self.assertRegex(str(exc), r"(?i)critic[_ -]run.*carrier|CRITIC_LINE_INVALID")
            else:
                self.assertEqual(
                    "NOT_READY", result["conclusion"],
                    "AC4: merge gate accepted copied critic IDs without a Critic run: carrier")
                self.assertEqual("FAIL", result["gates"]["critic_current_tuple"]["result"])

    def test_ac5_fallback_label_alone_refuses_and_sealed_owner_override_passes(self):
        from agentic import ValidationError
        from agentic import gates, provenance
        from agentic.canonical import fingerprint, sha256

        config, workflow, bundle, contracts, registry = _fixture()
        candidate = bundle["candidate"]
        owner_payload = {"config_sha256": fingerprint("project-config", dict(config)),
                         "trusted_owner_ids": config["merge_gate"]["trusted_owner_ids"]}
        owners = dict(owner_payload, binding_sha256=fingerprint(
            "provenance_owner_config", owner_payload))
        owner_id = owners["trusted_owner_ids"][0]
        policy = {"require_run_logs": False, "default_ref": "refs/heads/main",
                  "generated_paths": [], "generators": {}}
        # Doc 45 open owner question 3 leaves the final-gate adapter API and
        # authoritative config field open. Inject only the range-check adapter
        # result, using main's reviewed numeric-owner envelope and v2 chain.
        # No candidate-owned label/list is promoted to authority by this fake.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, base, head = _repository(root)
            self.assertEqual((base, head), (candidate["merge_base_sha"], candidate["head_sha"]))
            state = {}
            original = provenance.record_owner_override(
                state, who=owner_id, when=NOW, why="Accept this exact untagged commit",
                pr=candidate["pr_number"], covered_shas=[head], reviewed_owner_config=owners)
            valid = provenance.supersede_owner_override(
                state, who=owner_id, when=NOW, why="Retain exact coverage after owner review",
                pr=candidate["pr_number"], covered_shas=[head],
                supersedes=original["override_sha256"], reviewed_owner_config=owners)
            self.assertEqual(2, valid["schema_version"])
            self.assertEqual(original["override_sha256"], valid["supersedes"])
            self.assertEqual([head], valid["covered_shas"])
            observation = {
                "version": 1, "repository_id": candidate["repository_id"],
                "pr": candidate["pr_number"], "base_ref": "main", "base": base,
                "head": head, "observed_at": NOW, "labels": ["fallback: bot-written"],
                "config_sha256": owners["config_sha256"],
                "trusted_owner_ids": owners["trusted_owner_ids"], "comments": []}

            malformed = copy.deepcopy(valid)
            malformed["override_sha256"] = "0" * 64
            untrusted = copy.deepcopy(valid)
            untrusted["who"] = owner_id + 100000
            stale = copy.deepcopy(valid)
            stale["covered_shas"] = [base]
            for record in (untrusted, stale):
                record["override_sha256"] = fingerprint(
                    "provenance_override", {k: v for k, v in record.items()
                                            if k != "override_sha256"})
            real_check = provenance.check_commit_range
            refusal_template = real_check(
                root, base, head, reviewed_policy=policy,
                provider_observation=observation, reviewed_owner_config=owners)
            self.assertEqual("REFUSE", refusal_template["status"])
            self.assertEqual([head], [item["sha"] for item in refusal_template["refusals"]])
            self.assertIn("missing Writer trailer", refusal_template["refusals"][0]["reason"])
            cases = [("label-only", None), ("malformed", malformed),
                     ("untrusted", untrusted), ("stale-coverage", stale),
                     ("sealed-owner-override", valid)]
            for name, record in cases:
                with self.subTest(case=name):
                    observed = copy.deepcopy(observation)
                    if record is not None:
                        body = provenance.render_owner_override(
                            valid if name == "malformed" else record)
                        observed["comments"] = [{
                            "kind": "issue", "id": 91, "actor_id": record["who"],
                            "url": "https://example.invalid/pull/7#issuecomment-91",
                            "body": body, "body_sha256": sha256(body.encode("utf-8"))}]
                    kwargs = dict(
                        reviewed_policy=policy, provider_observation=observed,
                        reviewed_owner_config=owners, override=record,
                        override_predecessors=[original] if record is not None else [])
                    try:
                        checked = real_check(root, base, head, **kwargs)
                    except ValidationError as exc:
                        self.assertNotEqual("sealed-owner-override", name, str(exc))
                        self.assertRegex(str(exc), r"(?i)override|owner")
                        # A registered adapter's failure remains a refusal; the
                        # evaluator must never turn it into passing provenance.
                        checked = copy.deepcopy(refusal_template)
                        checked["refusals"] = [{"sha": head, "reason": str(exc)}]
                    expected = "PASS" if name == "sealed-owner-override" else "REFUSE"
                    self.assertEqual(expected, checked["status"], checked)
                    if expected == "PASS":
                        self.assertEqual(valid["override_sha256"], checked["override_sha256"])
                        self.assertEqual(91, checked["provider_override_evidence"]["provider_comment_id"])
                    # Cover module and local-import spellings without requiring
                    # a particular import style in the future gate implementation.
                    with patch.object(provenance, "check_commit_range", return_value=checked) as adapter:
                        with patch.object(gates, "check_commit_range", adapter, create=True):
                            contracts.validate("evidence-bundle", bundle)
                            result = gates.evaluate(
                                config, workflow, bundle, contracts, NOW,
                                posting_collector_registry=registry, repository=root)
                    self.assertEqual(
                        "PASS" if expected == "PASS" else "FAIL",
                        result["gates"]["provenance"]["result"],
                        f"AC5: merge gate ignored {name} provenance for fallback: bot-written")
                    if expected == "REFUSE":
                        self.assertEqual("NOT_READY", result["conclusion"])
                    else:
                        # A provenance exception cannot waive an unrelated gate.
                        blocked = copy.deepcopy(bundle)
                        blocked["pr"]["mergeable"] = False
                        with patch.object(provenance, "check_commit_range", return_value=checked) as adapter:
                            with patch.object(gates, "check_commit_range", adapter, create=True):
                                independently_blocked = gates.evaluate(
                                    config, workflow, blocked, contracts, NOW,
                                    posting_collector_registry=registry, repository=root)
                        self.assertEqual("PASS", independently_blocked["gates"]["provenance"]["result"])
                        self.assertEqual("FAIL", independently_blocked["gates"]["merge_compatibility"]["result"])
                        self.assertEqual("NOT_READY", independently_blocked["conclusion"])


# Frozen complete synthetic review/provider bytes, including digest-bound
# receipts. Kept inline so this module imports without sibling test modules.
INLINE_EVIDENCE = r'''{
  "config": {
    "version": 3,
    "template": {
      "expected_workflow_version": "1.9.4"
    },
    "project": {
      "id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
      "name": "Offline Fixture",
      "short_name": "EX"
    },
    "jira": {
      "enabled": true,
      "cloud_id": "fixture-cloud",
      "site": "https://jira.example.invalid",
      "provider_project_id": "fixture-project",
      "project_key": "EX",
      "controller_actor_id": "fixture-controller",
      "scope": {
        "allow_entire_project": false,
        "selector_mode": "all",
        "included_epics": [],
        "labels_any": [
          "awf-fixture"
        ],
        "components_any": [],
        "additional_jql": "",
        "ownership_required": true
      },
      "status_map": {
        "backlog": "Backlog",
        "ready": "Ready",
        "in_progress": "In Progress",
        "in_review": "In Review",
        "done": "Done"
      },
      "lifecycle_writes": {
        "in_progress": true,
        "in_review": true,
        "done": true
      },
      "owner_closure_keywords": [
        "release",
        "publication",
        "qualification",
        "cutover",
        "production acceptance"
      ],
      "dependency_direction": "requires",
      "mutations_owner": "controller"
    },
    "github": {
      "host": "https://github.com",
      "repository_id": 101,
      "repository": "fixture/example",
      "expected_actor_id": 1002,
      "expected_actor_login": "fixture-owner",
      "auth_profile": "fixture",
      "base_branch": "main",
      "branch_pattern": "codex/{ticket}-{slug}",
      "merge_method": "squash",
      "draft_pr_first": true,
      "one_repository_per_controller": true
    },
    "execution": {
      "profile": "manual_reference",
      "max_parallel_tickets": 6,
      "max_parallel_tickets_per_stream": 1,
      "child_env_strip_extra": [],
      "native_streams": {
        "enabled": true,
        "dispatch_policy": "ready_independent"
      },
      "independent_reviewers": {
        "allocation": "shared_critic"
      },
      "max_agent_runs_per_ticket": 16,
      "max_spawn_depth": 1,
      "max_amendment_cycles": 3,
      "max_cap_extensions": 2,
      "risk_tiers": {
        "tier1_eligible_paths": [
          "tests/**",
          "fixtures/**",
          "tools/**",
          "evidence/**",
          "docs/**",
          "*.manifest.json"
        ],
        "tier1_excluded_paths": [],
        "tier1_review": {
          "roles": [
            "critic"
          ],
          "specialist_when_touching": [
            "security"
          ],
          "findings": "advisory"
        },
        "tier2_review": {
          "roles": [
            "critic",
            "specialist"
          ],
          "findings": "blocking"
        },
        "tier3_review": {
          "roles": [
            "critic",
            "specialist"
          ],
          "findings": "blocking",
          "max_rounds": 3
        }
      },
      "transient_retry_limit": 2,
      "max_run_seconds": 3600,
      "max_tool_calls_per_run": 100,
      "max_tokens_per_ticket": 2000000,
      "max_cost_microusd_per_ticket": null,
      "daily_project_cost_microusd": null,
      "one_writer_per_ticket": true,
      "roles": {
        "controller": {
          "model": "gpt-5.6-sol",
          "reasoning_effort": "high",
          "fallback": "deny",
          "approved_model_ids": [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-6.1-sol"
          ],
          "permission_profile": "documented-reference-profile",
          "network_allowlist": []
        },
        "worker": {
          "model": "gpt-5.6-luna",
          "reasoning_effort": "medium",
          "fallback": "deny",
          "approved_model_ids": [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-6.1-sol"
          ],
          "permission_profile": "documented-reference-profile",
          "network_allowlist": []
        },
        "critic": {
          "model": "gpt-5.6-sol",
          "reasoning_effort": "high",
          "fallback": "deny",
          "approved_model_ids": [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-6.1-sol"
          ],
          "permission_profile": "documented-reference-profile",
          "network_allowlist": []
        },
        "specialist": {
          "model": "gpt-5.6-sol",
          "reasoning_effort": "high",
          "fallback": "deny",
          "approved_model_ids": [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-6.1-sol"
          ],
          "permission_profile": "documented-reference-profile",
          "network_allowlist": []
        }
      },
      "route_capabilities": {
        "observation_path": ".agentic/route-capabilities.json",
        "max_age_days": 30
      },
      "host_broker": {
        "enabled": false,
        "broker_id": "",
        "lease_before_dispatch": true,
        "max_workers": 3,
        "max_heavy_jobs": 1,
        "max_gpu_jobs": 0,
        "resources": {}
      },
      "model_routing": {
        "routing": ".agentic/routing.yaml",
        "high_risk_flags": [
          "security",
          "permissions",
          "schema_or_migration",
          "data_loss",
          "concurrency",
          "production",
          "public_api",
          "architecture"
        ],
        "agent_overrides": {},
        "ticket_overrides": {},
        "escalation": {
          "enabled": true,
          "max_escalations_per_ticket": 2,
          "max_reasoning_failures_per_phase": 3,
          "effort_ceiling": "high"
        },
        "budgets": {
          "max_runs_per_ticket": 16,
          "max_runs_per_project_day": 250,
          "max_tokens_per_ticket": 2000000,
          "max_tokens_per_project_day": 30000000,
          "max_cost_microusd_per_ticket": null,
          "max_cost_microusd_per_project_day": null
        },
        "adaptive": {
          "mode": "shadow",
          "min_reviewed_samples": 20,
          "min_success_rate_percent": 95
        },
        "reconciliation": {
          "enabled": false,
          "authorized_operator_ids": [],
          "max_reconciled_incident_retries_per_ticket": 2
        }
      }
    },
    "validation": {
      "commands": [
        "python -m unittest discover -s tests"
      ],
      "ci_candidate_policy": "synthetic_merge_required",
      "require_candidate_bound_ci_evidence": true,
      "required_ci_checks": [
        {
          "name": "unit-tests",
          "app_id": 42,
          "workflow_path": ".github/workflows/test.yml",
          "workflow_sha": "908941adcfed7b16f8ef47292e326448f1de1af2",
          "min_tests_executed": 1,
          "verifies_history": false
        }
      ],
      "max_evidence_age_seconds": 1800,
      "test_timeout_seconds": 1800
    },
    "scope": {
      "protected_paths": [
        "AGENTS.md",
        "**/AGENTS.md",
        ".agentic/**",
        ".codex/**",
        ".github/workflows/**",
        ".github/CODEOWNERS",
        "CODEOWNERS",
        "scripts/bootstrap_project.py"
      ],
      "generated_paths": [],
      "architecture_docs": [],
      "product_docs": []
    },
    "critic": {
      "required": true,
      "independent_context": true,
      "blocking_severities": [
        "BLOCKER",
        "MAJOR"
      ],
      "require_re_review_after_any_head_change": true,
      "require_re_review_after_any_target_base_change": true,
      "require_full_final_review": true
    },
    "specialist_reviews": {
      "security": {
        "required_when_triggered": true,
        "paths": [
          "auth/**",
          "security/**"
        ],
        "keywords": [
          "credential",
          "authentication",
          "secret"
        ],
        "risk_flags": [
          "security"
        ],
        "reviewer_identity": "fixture-security"
      },
      "data_migration": {
        "required_when_triggered": true,
        "paths": [
          "migrations/**",
          "schema/**"
        ],
        "keywords": [
          "migration",
          "schema"
        ],
        "risk_flags": [
          "schema_or_migration",
          "data_loss"
        ],
        "reviewer_identity": "fixture-data"
      },
      "public_api": {
        "required_when_triggered": true,
        "paths": [
          "api/**",
          "openapi/**"
        ],
        "keywords": [
          "breaking interface",
          "public API"
        ],
        "risk_flags": [
          "public_api"
        ],
        "reviewer_identity": "fixture-api"
      },
      "operations": {
        "required_when_triggered": true,
        "paths": [
          "infra/**",
          "deploy/**"
        ],
        "keywords": [
          "production",
          "race condition"
        ],
        "risk_flags": [
          "concurrency",
          "production"
        ],
        "reviewer_identity": "fixture-operations"
      }
    },
    "merge_gate": {
      "human_authorization_required": true,
      "execution_after_authorization": "owner_manual",
      "automatic_merge_enabled": false,
      "require_critic_approval_current_tuple": true,
      "require_specialist_reviews_current_tuple": true,
      "require_required_ci_green": true,
      "require_zero_unresolved_blocking_threads": true,
      "invalidate_on_head_change": true,
      "invalidate_on_target_base_change": true,
      "authorization_ttl_seconds": 900,
      "trusted_owner_ids": [
        1001
      ],
      "high_risk_owner_quorum": 1,
      "production_posting_collector_ids": [
        "github-production-posting-collector"
      ]
    },
    "controller": {
      "dispatch_enabled": false,
      "auto_dispatch": false,
      "auto_request_critic": false,
      "auto_resume_amendments": false,
      "auto_transition_jira": false,
      "status_cadence_seconds": 600
    },
    "audit": {
      "store_must_be_outside_worktrees": true,
      "retention_days": 90,
      "redact_secrets": true
    },
    "portfolio": {
      "read_only": true,
      "cross_project_dispatch": false
    }
  },
  "runtime": {
    "active_ticket_key": "EX-1",
    "intake_assignments": {
      "EX-1": {
        "assignment_id": "gate-fixture-assignment",
        "ticket_key": "EX-1",
        "tier": 2,
        "round_cap": 2,
        "owner_review_required": false,
        "assignment_sha256": "42eb10484f7e84977a52a0a1f8a00fa9627c96483040cec754e21439145a7fae"
      }
    }
  },
  "bundle": {
    "schema_version": 3,
    "candidate": {
      "host": "https://github.com",
      "repository_id": 101,
      "repository": "fixture/example",
      "pr_number": 7,
      "target_base_branch": "main",
      "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
      "target_base_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
      "merge_base_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
      "head_tree_sha": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
      "integration_tree_sha": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
      "tested_merge_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
      "diff_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "merge_method": "squash"
    },
    "snapshot": {
      "schema_version": 3,
      "issue_id": "2001",
      "project_key": "EX",
      "summary": "Validate a label",
      "description": "Offline illustrative fixture only.",
      "acceptance_criteria": [
        {
          "id": "AC1",
          "text": "Return the requested label.",
          "validation": "Assert the output label."
        },
        {
          "id": "AC2",
          "text": "Reject empty input.",
          "validation": "Assert empty input raises an error."
        }
      ],
      "dependencies": [],
      "linked_requirements": [],
      "epic_ids": [],
      "labels": [
        "awf-fixture"
      ],
      "component_ids": []
    },
    "contract": {
      "schema_version": 3,
      "contract_id": "9cd18fb5-8aee-5e37-bbda-348cfc9a7cff",
      "contract_version": 1,
      "created_at": "2026-09-09T12:00:00Z",
      "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
      "repository_id": 101,
      "issue_id": "2001",
      "ticket": "EX-1",
      "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
      "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
      "objective": "Validate one label",
      "acceptance_criteria": [
        {
          "id": "AC1",
          "text": "Return the requested label.",
          "validation": "Assert the output label."
        },
        {
          "id": "AC2",
          "text": "Reject empty input.",
          "validation": "Assert empty input raises an error."
        }
      ],
      "dependencies": [],
      "branch_origin_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
      "target_base_branch": "main",
      "scope": {
        "expected_paths": [
          "src/example.py"
        ],
        "allowed_adjacent_paths": [],
        "protected_interfaces": [],
        "forbidden_changes": [
          "unrelated changes"
        ],
        "governance_change_authorization": null,
        "evidence_paths": [
          "evidence/**",
          "docs/**"
        ]
      },
      "validation": {
        "commands": [
          "python -m unittest discover -s tests"
        ],
        "test_cases": [
          "normal label",
          "empty input"
        ],
        "tests_not_applicable_reason": null,
        "required_resources": [],
        "platform_distinction": null
      },
      "risk_flags": {
        "security": false,
        "schema_or_migration": false,
        "public_api": false,
        "data_loss": false,
        "concurrency": false,
        "production": false
      },
      "specialist_domains": [],
      "disposition": "READY",
      "risk_tier": 2,
      "tier_justification": "Product source under src/ is outside the Tier 1 eligible paths.",
      "closure_standard": {
        "kind": "FULL",
        "accepted_limitations": [],
        "evidence_required": [
          "validation command exit 0"
        ]
      },
      "owner_closure_required": false,
      "corrects": null,
      "risk_classification": {
        "tier": 2,
        "matched_tiers": [
          2
        ],
        "evidence": {
          "2": [
            "code or non-Tier-1 change uses the Tier 2 default"
          ]
        },
        "rule": "highest matching tier wins; Tier 2 permits up to 3 round(s)",
        "risk_flags": []
      }
    },
    "dispatch": {
      "schema_version": 3,
      "record_id": "ed026ea5-c0a7-567c-89ec-39dcf62c9bea",
      "created_at": "2026-09-09T12:00:00Z",
      "producer_id": "fixture-controller",
      "run_id": "0544646c-bcc8-5f67-a333-ad482bf06795",
      "binding": {
        "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
        "repository_id": 101,
        "issue_id": "2001",
        "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
        "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
        "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
        "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
      },
      "contract_id": "9cd18fb5-8aee-5e37-bbda-348cfc9a7cff",
      "contract_version": 1,
      "role": "worker",
      "branch": "codex/EX-1-label",
      "worktree_id": "c5167da4-2c93-5787-b905-ca4d2965dcde",
      "lease": {
        "required": true,
        "lease_id": "2e6e396b-2fe2-593a-93e8-c87856184143",
        "fencing_token": 1,
        "expires_at": "2026-09-09T12:15:00Z"
      },
      "collision_check": "PASS",
      "collision_evidence": [
        "urn:awf:fixture:example-evidence"
      ],
      "disposition": "PERMITTED",
      "control_generation": 1,
      "required_resources": [],
      "operating_hash": "d4176b00935badc65ec5a318decb47b2a6c6685128cd17335b72d79519297bf9"
    },
    "worker": {
      "schema_version": 3,
      "record_id": "a3448c53-6117-53c6-8cb3-7f6e5295ec67",
      "created_at": "2026-09-09T12:00:00Z",
      "producer_id": "fixture-worker",
      "run_id": "c31bb8d3-5974-5af0-9de8-706c9762e3a9",
      "binding": {
        "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
        "repository_id": 101,
        "issue_id": "2001",
        "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
        "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
        "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
        "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
      },
      "status": "COMPLETE",
      "commit_route": "WORKER",
      "dispatch_id": "ed026ea5-c0a7-567c-89ec-39dcf62c9bea",
      "files_changed": [
        "src/example.py"
      ],
      "tested_tree": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
      "changes": [
        {
          "path": "src/example.py",
          "action": "modified"
        }
      ],
      "ignored_untracked": [],
      "acceptance_criteria": [
        {
          "id": "AC1",
          "verdict": "PASS",
          "evidence": [
            "urn:awf:fixture:example-evidence"
          ]
        },
        {
          "id": "AC2",
          "verdict": "PASS",
          "evidence": [
            "urn:awf:fixture:example-evidence"
          ]
        }
      ],
      "validation": [
        {
          "command": "python -m unittest discover -s tests",
          "started_at": "2026-09-09T11:59:59Z",
          "finished_at": "2026-09-09T12:00:00Z",
          "exit_code": 0,
          "tested_tree_sha": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
          "clean_checkout": true,
          "evidence": [
            "urn:awf:fixture:example-evidence"
          ],
          "tests_discovered": 2,
          "tests_executed": 2,
          "declared_skips": [],
          "unevaluable_files": []
        }
      ],
      "self_review_complete": true,
      "findings_addressed": [],
      "blockers": [],
      "closure": {
        "result": "MET",
        "evidence": [
          "urn:awf:fixture:example-evidence"
        ]
      }
    },
    "critic": {
      "schema_version": 3,
      "record_id": "6748e420-45e5-5339-b6a7-12c92e46f20a",
      "created_at": "2026-09-09T12:00:00Z",
      "producer_id": "fixture-critic",
      "run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
      "binding": {
        "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
        "repository_id": 101,
        "issue_id": "2001",
        "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
        "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
        "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
        "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
      },
      "verdict": "APPROVE",
      "acceptance_criteria": [
        {
          "id": "AC1",
          "verdict": "PASS",
          "evidence": [
            "urn:awf:fixture:example-evidence"
          ]
        },
        {
          "id": "AC2",
          "verdict": "PASS",
          "evidence": [
            "urn:awf:fixture:example-evidence"
          ]
        }
      ],
      "findings": [],
      "prior_finding_ids": [],
      "closure": {
        "result": "MET",
        "evidence": [
          "urn:awf:fixture:example-evidence"
        ]
      },
      "coverage": {
        "complete": true,
        "file_manifest_sha256": "9aaf357e38daea260442ed0c8a584bb38633dcfad5e3114bd2241195b24e2de4",
        "reviewed_paths": [
          "src/example.py"
        ],
        "omissions": []
      },
      "evidence_checked": [
        "urn:awf:fixture:example-evidence",
        "urn:awf:critic-review:6748e420-45e5-5339-b6a7-12c92e46f20a"
      ]
    },
    "specialists": [],
    "review_submission": {
      "submission_id": "0e6143bf-d2af-5b18-8d8d-72f129b9bb7f",
      "cycle_id": "3a420659-4012-596d-a880-dd39e2931cf2",
      "completion_snapshot": {
        "cycle_id": "3a420659-4012-596d-a880-dd39e2931cf2",
        "tuple": {
          "repository": "fixture/example",
          "base_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
          "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
          "head_tree_sha": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
          "contract_sha256": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
          "review_input_sha256": "ae6ea3453f5730bd606c6ec1f3ae5f5e308c2c4746d2858f40388a240eba798c"
        },
        "tuple_sha256": "2d33ba607b779a9305e99621ebc9575efe53b44fa40085c969eaf67105182940",
        "required_reviewers": [
          "fixture-critic"
        ],
        "reviewer_set_sha256": "5a10c872428ed78e5f630b6d8c9dd880fc76eb032305b0346d71e6cc158e2582",
        "counts": {
          "required": 1,
          "completed": 1,
          "acceptable": 1,
          "failed": 0,
          "stale": 0,
          "outstanding": 0
        },
        "results": [
          {
            "reviewer_id": "fixture-critic",
            "state": "ACCEPTABLE",
            "result_sha256": "71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db",
            "terminal_at": "2026-09-09T12:00:00Z"
          }
        ]
      },
      "completion_snapshot_sha256": "933508656448960980ccd7525855ce44a244a0e2d5cea96a1ae60e1d3942887b",
      "aggregate": {
        "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37",
        "critic_record_id": "6748e420-45e5-5339-b6a7-12c92e46f20a",
        "critic_sha256": "0fc4765a5a9c8bad189703c5c07322a8e36a4a4b7a58a59bdb60ce6fc476b152",
        "specialist_record_ids": [],
        "specialist_sha256": [],
        "required_reviewers": [
          "fixture-critic"
        ]
      },
      "aggregate_sha256": "5a599fc301ba844d3aaca024ee37cafc66e0e547cf46fcbc42c108c0719079b4",
      "provider_preconditions": {
        "operation_id": "0e6143bf-d2af-5b18-8d8d-72f129b9bb7f",
        "prepared_at": "2026-09-09T12:00:00Z",
        "repository": "fixture/example",
        "base_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
        "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
        "head_tree_sha": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
        "tuple_sha256": "2d33ba607b779a9305e99621ebc9575efe53b44fa40085c969eaf67105182940",
        "reviewer_set_sha256": "5a10c872428ed78e5f630b6d8c9dd880fc76eb032305b0346d71e6cc158e2582",
        "completion_snapshot_sha256": "933508656448960980ccd7525855ce44a244a0e2d5cea96a1ae60e1d3942887b",
        "aggregate_sha256": "5a599fc301ba844d3aaca024ee37cafc66e0e547cf46fcbc42c108c0719079b4"
      },
      "execution_authority": false
    },
    "ci": {
      "schema_version": 3,
      "record_id": "b8ba18ee-31ed-5d37-8176-1351e1ca6b72",
      "created_at": "2026-09-09T12:00:00Z",
      "producer_id": "github-production-posting-collector",
      "run_id": "8e9a288f-2dd8-5231-90ff-b338139b5de7",
      "binding": {
        "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
        "repository_id": 101,
        "issue_id": "2001",
        "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
        "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
        "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
        "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
      },
      "retrieval_complete": true,
      "candidate_type": "synthetic_merge",
      "checks": [
        {
          "name": "unit-tests",
          "check_id": "check-1",
          "app_id": 42,
          "workflow_path": ".github/workflows/test.yml",
          "workflow_sha": "908941adcfed7b16f8ef47292e326448f1de1af2",
          "attempt": 1,
          "event": "pull_request",
          "conclusion": "success",
          "tested_tree_sha": "046afe3a3786df1d6fc88ab3e3813ae1a694a1c3",
          "tested_commit_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
          "tests_executed": 2,
          "completed_at": "2026-09-09T12:00:00Z",
          "evidence": [
            "urn:awf:fixture:example-evidence"
          ],
          "checkout_depth": "full"
        }
      ],
      "collector_attestation_id": "e56e2c39-2059-59ab-bd97-85d1a8f4e36f"
    },
    "pr": {
      "schema_version": 3,
      "record_id": "d19c6425-a86d-5a30-9c41-1b903b6d7120",
      "created_at": "2026-09-09T12:00:00Z",
      "producer_id": "github-production-posting-collector",
      "run_id": "8e9a288f-2dd8-5231-90ff-b338139b5de7",
      "binding": {
        "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
        "repository_id": 101,
        "issue_id": "2001",
        "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
        "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
        "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
        "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
      },
      "state": "OPEN",
      "draft": false,
      "mergeable": true,
      "retrieval_complete": true,
      "file_manifest": [
        {
          "path": "src/example.py",
          "blob_sha": "1111111111111111111111111111111111111111"
        }
      ],
      "body_sha256": "d8995f76e8637f9e062ace8442900f5f22f4d0c4ccbb0583b749a3a798a40c21",
      "blocking_threads": [],
      "scope_pass": true,
      "dependency_compatibility_pass": true,
      "ruleset_verified": true,
      "specialist_domains": [],
      "classification_complete": true,
      "collector_attestation_id": "e56e2c39-2059-59ab-bd97-85d1a8f4e36f",
      "evidence": [
        "urn:awf:fixture:example-evidence"
      ]
    },
    "runs": [
      {
        "schema_version": 3,
        "record_id": "f7863f29-5e57-5982-86cb-3fafc7b42a34",
        "created_at": "2026-09-09T12:00:00Z",
        "producer_id": "fixture-controller",
        "run_id": "0544646c-bcc8-5f67-a333-ad482bf06795",
        "binding": {
          "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
          "repository_id": 101,
          "issue_id": "2001",
          "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
          "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
          "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
          "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
        },
        "role": "controller",
        "context_id": "a9e896b6-e418-579d-91ec-3aee3e0c2e40",
        "parent_run_id": null,
        "model": "gpt-5.6-sol",
        "runtime_version": "fixture-runtime-1",
        "instruction_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "permission_profile_hash": "e060cbe84a7eae282587685e52ce7df6937973d4bbbbbbbbbbbbbbbbbbbbbbbb",
        "capabilities": [
          "propose_dispatch",
          "propose_jira_transition",
          "read_evidence",
          "write_evidence"
        ],
        "status": "COMPLETE",
        "attestation_evidence": [
          "urn:awf:fixture:example-evidence"
        ],
        "resources_held": []
      },
      {
        "schema_version": 3,
        "record_id": "cce7a526-5b8c-5b81-9e7d-4d76750a0dc3",
        "created_at": "2026-09-09T12:00:00Z",
        "producer_id": "fixture-worker",
        "run_id": "c31bb8d3-5974-5af0-9de8-706c9762e3a9",
        "binding": {
          "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
          "repository_id": 101,
          "issue_id": "2001",
          "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
          "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
          "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
          "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
        },
        "role": "worker",
        "context_id": "843010af-09c4-5910-b8f3-240047acfc4a",
        "parent_run_id": null,
        "model": "gpt-5.6-luna",
        "runtime_version": "fixture-runtime-1",
        "instruction_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "permission_profile_hash": "e060cbe84a7eae282587685e52ce7df6937973d4bbbbbbbbbbbbbbbbbbbbbbbb",
        "capabilities": [
          "read_evidence",
          "run_isolated_tests",
          "submit_worker_result",
          "write_assigned_worktree"
        ],
        "status": "COMPLETE",
        "attestation_evidence": [
          "urn:awf:fixture:example-evidence"
        ],
        "resources_held": []
      },
      {
        "schema_version": 3,
        "record_id": "e56e2c39-2059-59ab-bd97-85d1a8f4e36f",
        "created_at": "2026-09-09T12:00:00Z",
        "producer_id": "github-production-posting-collector",
        "run_id": "8e9a288f-2dd8-5231-90ff-b338139b5de7",
        "binding": {
          "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
          "repository_id": 101,
          "issue_id": "2001",
          "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
          "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
          "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
          "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
        },
        "role": "collector",
        "context_id": "1b14cdf7-2e4f-5ec7-9d9a-072cc95ab34e",
        "parent_run_id": null,
        "model": "fixture-model",
        "runtime_version": "fixture-runtime-1",
        "instruction_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "permission_profile_hash": "e060cbe84a7eae282587685e52ce7df6937973d4bbbbbbbbbbbbbbbbbbbbbbbb",
        "capabilities": [
          "read_external_state",
          "write_evidence"
        ],
        "status": "COMPLETE",
        "attestation_evidence": [
          "urn:awf:fixture:example-evidence"
        ],
        "resources_held": []
      },
      {
        "schema_version": 3,
        "record_id": "f4120b2d-e705-5234-b465-9994c90134bf",
        "created_at": "2026-09-09T12:00:00Z",
        "producer_id": "fixture-critic",
        "run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
        "binding": {
          "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
          "repository_id": 101,
          "issue_id": "2001",
          "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
          "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
          "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
          "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
        },
        "role": "critic",
        "context_id": "5d483b29-545c-5055-858d-5b6ab26b0853",
        "parent_run_id": null,
        "model": "gpt-5.6-sol",
        "runtime_version": "fixture-runtime-1",
        "instruction_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "permission_profile_hash": "e060cbe84a7eae282587685e52ce7df6937973d4bbbbbbbbbbbbbbbbbbbbbbbb",
        "capabilities": [
          "read_evidence",
          "run_isolated_tests",
          "submit_review"
        ],
        "status": "COMPLETE",
        "attestation_evidence": [
          "urn:awf:fixture:example-evidence"
        ],
        "resources_held": []
      }
    ],
    "prior_findings": [],
    "finding_dispositions": [],
    "cap_disposition": null,
    "publication_scan": {
      "schema_version": 3,
      "status": "PASS",
      "base_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
      "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
      "pr_body_sha256": "d8995f76e8637f9e062ace8442900f5f22f4d0c4ccbb0583b749a3a798a40c21",
      "additional_pr_body_sha256": [],
      "comment_sha256": [],
      "mapping_sha256": null,
      "project_config_sha256": null,
      "mapping_loaded": false,
      "mapping_location": ".agentic-state/publication-deny.json",
      "commits_scanned": [
        "e060cbe84a7eae282587685e52ce7df6937973d4"
      ],
      "findings": [],
      "total_findings": 0,
      "blocking_findings": 0,
      "pre_existing_findings": 0,
      "unscanned": [],
      "unscanned_count": 0,
      "coverage": {
        "current_files": true,
        "commit_messages": true,
        "every_patch": true,
        "generated_reports": "when committed or passed as provider text",
        "captured_command_output": "when committed or passed as provider text",
        "strict_utf8": true,
        "pr_bodies": true,
        "pr_comments": false
      },
      "execution_authority": false
    },
    "tier_classification": {
      "tier": 2,
      "matched_tiers": [
        2
      ],
      "evidence": {
        "2": [
          "code or non-Tier-1 change uses the Tier 2 default"
        ]
      },
      "rule": "highest matching tier wins; Tier 2 permits up to 3 round(s)",
      "risk_flags": []
    },
    "review_verdicts": [
      {
        "record_id": "53838cd2-48b6-5273-908a-db2c70facf0c",
        "created_at": "2026-09-09T12:00:00Z",
        "producer_id": "fixture-critic",
        "run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
        "binding": {
          "project_id": "94fdd7f1-0a6d-5cad-9573-462539ffcd4d",
          "repository_id": 101,
          "issue_id": "2001",
          "requirements_hash": "c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17",
          "contract_hash": "e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6",
          "policy_hash": "ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be",
          "candidate_id": "70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37"
        },
        "verdict": "PASS",
        "tier": 2,
        "round": 1,
        "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
        "reviewer_id": "fixture-critic",
        "pr_comment_url": "https://github.com/fixture/example/pull/7#issuecomment-1",
        "pr_body_link": "https://github.com/fixture/example/pull/7#review-verdict-1-53838cd2-48b6-5273-908a-db2c70facf0c",
        "evidence": [
          "urn:awf:fixture:example-evidence",
          "urn:awf:critic-review:6748e420-45e5-5339-b6a7-12c92e46f20a"
        ],
        "critic_artifact_binding": {
          "critic_run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
          "result_sha256": "71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db",
          "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
          "round": 1
        },
        "owner_review": false,
        "owner_id": 1001,
        "candidate_binding": {
          "repository_id": 101,
          "pr_number": 7,
          "base_sha": "446735691b3ce371877c17f85ba950511b9df7e1",
          "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4"
        },
        "critic_review": {
          "record_id": "6748e420-45e5-5339-b6a7-12c92e46f20a",
          "run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
          "round": 1,
          "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
          "verdict": "APPROVE",
          "findings_sha256": "58eb4ed82fa8baf0699487341c301586601ec6b1083ee2fbae36ecc48f760fcf"
        },
        "posting_observation": {
          "producer_id": "github-production-posting-collector",
          "run_id": "8e9a288f-2dd8-5231-90ff-b338139b5de7",
          "source": "host_observation",
          "observed_at": "2026-09-09T12:00:00Z",
          "provider_kind": "github",
          "critic_artifact_binding": {
            "critic_run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
            "result_sha256": "71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db",
            "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
            "round": 1
          },
          "review_verdict_record_id": "53838cd2-48b6-5273-908a-db2c70facf0c",
          "review_verdict_sha256": "a800e0c3b27a509387ca484949ebbbd1aeb0e14ada4fa1b96ed99e2c8b597c20",
          "repository_id": 101,
          "pr_number": 7,
          "comment_id": 1,
          "comment_url": "https://github.com/fixture/example/pull/7#issuecomment-1",
          "comment_bytes": "{\"binding\":{\"candidate_id\":\"70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37\",\"contract_hash\":\"e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6\",\"issue_id\":\"2001\",\"policy_hash\":\"ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be\",\"project_id\":\"94fdd7f1-0a6d-5cad-9573-462539ffcd4d\",\"repository_id\":101,\"requirements_hash\":\"c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17\"},\"candidate_binding\":{\"base_sha\":\"446735691b3ce371877c17f85ba950511b9df7e1\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"pr_number\":7,\"repository_id\":101},\"created_at\":\"2026-09-09T12:00:00Z\",\"critic_artifact_binding\":{\"critic_run_id\":\"7d8465fe-d08b-53de-9c80-c839f0dad8fe\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"result_sha256\":\"71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db\",\"round\":1},\"critic_review\":{\"findings_sha256\":\"58eb4ed82fa8baf0699487341c301586601ec6b1083ee2fbae36ecc48f760fcf\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"record_id\":\"6748e420-45e5-5339-b6a7-12c92e46f20a\",\"round\":1,\"run_id\":\"7d8465fe-d08b-53de-9c80-c839f0dad8fe\",\"verdict\":\"APPROVE\"},\"evidence\":[\"urn:awf:fixture:example-evidence\",\"urn:awf:critic-review:6748e420-45e5-5339-b6a7-12c92e46f20a\"],\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"owner_id\":1001,\"owner_review\":false,\"pr_body_link\":\"https://github.com/fixture/example/pull/7#review-verdict-1-53838cd2-48b6-5273-908a-db2c70facf0c\",\"pr_comment_url\":\"https://github.com/fixture/example/pull/7#issuecomment-1\",\"producer_id\":\"fixture-critic\",\"record_id\":\"53838cd2-48b6-5273-908a-db2c70facf0c\",\"reviewer_id\":\"fixture-critic\",\"round\":1,\"run_id\":\"7d8465fe-d08b-53de-9c80-c839f0dad8fe\",\"tier\":2,\"verdict\":\"PASS\"}",
          "comment_sha256": "a800e0c3b27a509387ca484949ebbbd1aeb0e14ada4fa1b96ed99e2c8b597c20",
          "body_link": "https://github.com/fixture/example/pull/7#review-verdict-1-53838cd2-48b6-5273-908a-db2c70facf0c",
          "body_bytes": "Synthetic PR body round 1.\n\nreview-verdict:1:53838cd2-48b6-5273-908a-db2c70facf0c:a800e0c3b27a509387ca484949ebbbd1aeb0e14ada4fa1b96ed99e2c8b597c20\n",
          "body_sha256": "d8995f76e8637f9e062ace8442900f5f22f4d0c4ccbb0583b749a3a798a40c21",
          "provider_response_bytes": {
            "encoding": "base64",
            "data": "eyJjb21tZW50X2JvZHkiOiJ7XCJiaW5kaW5nXCI6e1wiY2FuZGlkYXRlX2lkXCI6XCI3MGI2NThmMmY4NDUxMWMzMzVlNDhmOTNjNGRhNzg2NzhlNzRiYzU4OWQwZDZjN2Q3ZTI4MTYzZTQ3Mjg4ZjM3XCIsXCJjb250cmFjdF9oYXNoXCI6XCJlN2I0MmJjMWQzN2FiYzBiYWUxZjNmZGE5Nzk4NTg2Mjc3NTc3NTc4ZjQxZGNlZWY1ZWVmNTdmYmFkY2M5MmU2XCIsXCJpc3N1ZV9pZFwiOlwiMjAwMVwiLFwicG9saWN5X2hhc2hcIjpcImRkZjZkMWMzZDU2MGNmOTZlNTY5NDllMDEyZjY4NzZkMTdhMmE3NTVjMGQ3NGU0YzZiZGVhYmQ1MDNlNzk1YmVcIixcInByb2plY3RfaWRcIjpcIjk0ZmRkN2YxLTBhNmQtNWNhZC05NTczLTQ2MjUzOWZmY2Q0ZFwiLFwicmVwb3NpdG9yeV9pZFwiOjEwMSxcInJlcXVpcmVtZW50c19oYXNoXCI6XCJjOTY3OTg2N2I4MDczMGQ2MzNmYjc5NmVhZTI5ZmUzYmUwZjNlMDMxN2ZlNDVjYjE5YjZlNDMxNDM4NGFmZjE3XCJ9LFwiY2FuZGlkYXRlX2JpbmRpbmdcIjp7XCJiYXNlX3NoYVwiOlwiNDQ2NzM1NjkxYjNjZTM3MTg3N2MxN2Y4NWJhOTUwNTExYjlkZjdlMVwiLFwiaGVhZF9zaGFcIjpcImUwNjBjYmU4NGE3ZWFlMjgyNTg3Njg1ZTUyY2U3ZGY2OTM3OTczZDRcIixcInByX251bWJlclwiOjcsXCJyZXBvc2l0b3J5X2lkXCI6MTAxfSxcImNyZWF0ZWRfYXRcIjpcIjIwMjYtMDktMDlUMTI6MDA6MDBaXCIsXCJjcml0aWNfYXJ0aWZhY3RfYmluZGluZ1wiOntcImNyaXRpY19ydW5faWRcIjpcIjdkODQ2NWZlLWQwOGItNTNkZS05YzgwLWM4MzlmMGRhZDhmZVwiLFwiaGVhZF9zaGFcIjpcImUwNjBjYmU4NGE3ZWFlMjgyNTg3Njg1ZTUyY2U3ZGY2OTM3OTczZDRcIixcInJlc3VsdF9zaGEyNTZcIjpcIjcxYWU1ZmQ3MzIwMWQ0YTNkNTMwZmVlYmUwZWFjODI0ZWM2MzY2MTNmMTA3ZjAzMDQ1ZTRmNDVmODdkYzgzZGJcIixcInJvdW5kXCI6MX0sXCJjcml0aWNfcmV2aWV3XCI6e1wiZmluZGluZ3Nfc2hhMjU2XCI6XCI1OGViNGVkODJmYThiYWYwNjk5NDg3MzQxYzMwMTU4NjYwMWVjNmIxMDgzZWUyZmJhZTM2ZWNjNDhmNzYwZmNmXCIsXCJoZWFkX3NoYVwiOlwiZTA2MGNiZTg0YTdlYWUyODI1ODc2ODVlNTJjZTdkZjY5Mzc5NzNkNFwiLFwicmVjb3JkX2lkXCI6XCI2NzQ4ZTQyMC00NWU1LTUzMzktYjZhNy0xMmM5MmU0NmYyMGFcIixcInJvdW5kXCI6MSxcInJ1bl9pZFwiOlwiN2Q4NDY1ZmUtZDA4Yi01M2RlLTljODAtYzgzOWYwZGFkOGZlXCIsXCJ2ZXJkaWN0XCI6XCJBUFBST1ZFXCJ9LFwiZXZpZGVuY2VcIjpbXCJ1cm46YXdmOmZpeHR1cmU6ZXhhbXBsZS1ldmlkZW5jZVwiLFwidXJuOmF3Zjpjcml0aWMtcmV2aWV3OjY3NDhlNDIwLTQ1ZTUtNTMzOS1iNmE3LTEyYzkyZTQ2ZjIwYVwiXSxcImhlYWRfc2hhXCI6XCJlMDYwY2JlODRhN2VhZTI4MjU4NzY4NWU1MmNlN2RmNjkzNzk3M2Q0XCIsXCJvd25lcl9pZFwiOjEwMDEsXCJvd25lcl9yZXZpZXdcIjpmYWxzZSxcInByX2JvZHlfbGlua1wiOlwiaHR0cHM6Ly9naXRodWIuY29tL2ZpeHR1cmUvZXhhbXBsZS9wdWxsLzcjcmV2aWV3LXZlcmRpY3QtMS01MzgzOGNkMi00OGI2LTUyNzMtOTA4YS1kYjJjNzBmYWNmMGNcIixcInByX2NvbW1lbnRfdXJsXCI6XCJodHRwczovL2dpdGh1Yi5jb20vZml4dHVyZS9leGFtcGxlL3B1bGwvNyNpc3N1ZWNvbW1lbnQtMVwiLFwicHJvZHVjZXJfaWRcIjpcImZpeHR1cmUtY3JpdGljXCIsXCJyZWNvcmRfaWRcIjpcIjUzODM4Y2QyLTQ4YjYtNTI3My05MDhhLWRiMmM3MGZhY2YwY1wiLFwicmV2aWV3ZXJfaWRcIjpcImZpeHR1cmUtY3JpdGljXCIsXCJyb3VuZFwiOjEsXCJydW5faWRcIjpcIjdkODQ2NWZlLWQwOGItNTNkZS05YzgwLWM4MzlmMGRhZDhmZVwiLFwidGllclwiOjIsXCJ2ZXJkaWN0XCI6XCJQQVNTXCJ9IiwiY29tbWVudF9pZCI6MSwiY29tbWVudF91cmwiOiJodHRwczovL2dpdGh1Yi5jb20vZml4dHVyZS9leGFtcGxlL3B1bGwvNyNpc3N1ZWNvbW1lbnQtMSIsInByX2JvZHkiOiJTeW50aGV0aWMgUFIgYm9keSByb3VuZCAxLlxuXG5yZXZpZXctdmVyZGljdDoxOjUzODM4Y2QyLTQ4YjYtNTI3My05MDhhLWRiMmM3MGZhY2YwYzphODAwZTBjM2IyN2E1MDkzODdjYTQ4NDk0OWViYmJkMWFlYjBlMTRhZGE0ZmExYjk2ZWQ5OWUyYzhiNTk3YzIwXG4ifQ=="
          },
          "provider_response_sha256": "f89dcca3907fa40d5880dfc035ce1430b52b30db24ffa50f34ce913bb60c1117",
          "collector_receipt_sha256": "243b3aeddf796b695bc46674b330aeb8da81ebc18af527e011d0d83360f8c5a4"
        }
      }
    ],
    "review_round_receipts": [
      {
        "critic_artifact_binding": {
          "critic_run_id": "7d8465fe-d08b-53de-9c80-c839f0dad8fe",
          "result_sha256": "71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db",
          "head_sha": "e060cbe84a7eae282587685e52ce7df6937973d4",
          "round": 1
        },
        "result_json": "{\"findings\":[],\"reviewer\":\"fixture-critic\",\"verdict\":\"APPROVE\"}",
        "completion_cycle_id": "3a420659-4012-596d-a880-dd39e2931cf2",
        "completion_tuple_sha256": "2d33ba607b779a9305e99621ebc9575efe53b44fa40085c969eaf67105182940",
        "completion_reviewer_set_sha256": "5a10c872428ed78e5f630b6d8c9dd880fc76eb032305b0346d71e6cc158e2582",
        "completion_snapshot_json": "{\"counts\":{\"acceptable\":1,\"completed\":1,\"failed\":0,\"outstanding\":0,\"required\":1,\"stale\":0},\"cycle_id\":\"3a420659-4012-596d-a880-dd39e2931cf2\",\"required_reviewers\":[\"fixture-critic\"],\"results\":[{\"result_sha256\":\"71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db\",\"reviewer_id\":\"fixture-critic\",\"state\":\"ACCEPTABLE\",\"terminal_at\":\"2026-09-09T12:00:00Z\"}],\"reviewer_set_sha256\":\"5a10c872428ed78e5f630b6d8c9dd880fc76eb032305b0346d71e6cc158e2582\",\"tuple\":{\"base_sha\":\"446735691b3ce371877c17f85ba950511b9df7e1\",\"contract_sha256\":\"e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"head_tree_sha\":\"046afe3a3786df1d6fc88ab3e3813ae1a694a1c3\",\"repository\":\"fixture/example\",\"review_input_sha256\":\"ae6ea3453f5730bd606c6ec1f3ae5f5e308c2c4746d2858f40388a240eba798c\"},\"tuple_sha256\":\"2d33ba607b779a9305e99621ebc9575efe53b44fa40085c969eaf67105182940\"}",
        "completion_snapshot_sha256": "933508656448960980ccd7525855ce44a244a0e2d5cea96a1ae60e1d3942887b",
        "review_verdict_json": "{\"binding\":{\"candidate_id\":\"70b658f2f84511c335e48f93c4da78678e74bc589d0d6c7d7e28163e47288f37\",\"contract_hash\":\"e7b42bc1d37abc0bae1f3fda9798586277577578f41dceef5eef57fbadcc92e6\",\"issue_id\":\"2001\",\"policy_hash\":\"ddf6d1c3d560cf96e56949e012f6876d17a2a755c0d74e4c6bdeabd503e795be\",\"project_id\":\"94fdd7f1-0a6d-5cad-9573-462539ffcd4d\",\"repository_id\":101,\"requirements_hash\":\"c9679867b80730d633fb796eae29fe3be0f3e0317fe45cb19b6e4314384aff17\"},\"candidate_binding\":{\"base_sha\":\"446735691b3ce371877c17f85ba950511b9df7e1\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"pr_number\":7,\"repository_id\":101},\"created_at\":\"2026-09-09T12:00:00Z\",\"critic_artifact_binding\":{\"critic_run_id\":\"7d8465fe-d08b-53de-9c80-c839f0dad8fe\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"result_sha256\":\"71ae5fd73201d4a3d530feebe0eac824ec636613f107f03045e4f45f87dc83db\",\"round\":1},\"critic_review\":{\"findings_sha256\":\"58eb4ed82fa8baf0699487341c301586601ec6b1083ee2fbae36ecc48f760fcf\",\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"record_id\":\"6748e420-45e5-5339-b6a7-12c92e46f20a\",\"round\":1,\"run_id\":\"7d8465fe-d08b-53de-9c80-c839f0dad8fe\",\"verdict\":\"APPROVE\"},\"evidence\":[\"urn:awf:fixture:example-evidence\",\"urn:awf:critic-review:6748e420-45e5-5339-b6a7-12c92e46f20a\"],\"head_sha\":\"e060cbe84a7eae282587685e52ce7df6937973d4\",\"owner_id\":1001,\"owner_review\":false,\"pr_body_link\":\"https://github.com/fixture/example/pull/7#review-verdict-1-53838cd2-48b6-5273-908a-db2c70facf0c\",\"pr_comment_url\":\"https://github.com/fixture/example/pull/7#issuecomment-1\",\"producer_id\":\"fixture-critic\",\"record_id\":\"53838cd2-48b6-5273-908a-db2c70facf0c\",\"reviewer_id\":\"fixture-critic\",\"round\":1,\"run_id\":\"7d8465fe-d08b-53de-9c80-c839f0dad8fe\",\"tier\":2,\"verdict\":\"PASS\"}",
        "review_verdict_sha256": "a800e0c3b27a509387ca484949ebbbd1aeb0e14ada4fa1b96ed99e2c8b597c20",
        "posting_observation_json": {
          "encoding": "base64",
          "data": "eyJib2R5X2J5dGVzIjoiU3ludGhldGljIFBSIGJvZHkgcm91bmQgMS5cblxucmV2aWV3LXZlcmRpY3Q6MTo1MzgzOGNkMi00OGI2LTUyNzMtOTA4YS1kYjJjNzBmYWNmMGM6YTgwMGUwYzNiMjdhNTA5Mzg3Y2E0ODQ5NDllYmJiZDFhZWIwZTE0YWRhNGZhMWI5NmVkOTllMmM4YjU5N2MyMFxuIiwiYm9keV9saW5rIjoiaHR0cHM6Ly9naXRodWIuY29tL2ZpeHR1cmUvZXhhbXBsZS9wdWxsLzcjcmV2aWV3LXZlcmRpY3QtMS01MzgzOGNkMi00OGI2LTUyNzMtOTA4YS1kYjJjNzBmYWNmMGMiLCJib2R5X3NoYTI1NiI6ImQ4OTk1Zjc2ZTg2MzdmOWUwNjJhY2U4NDQyOTAwZjVmMjJmNGQwYzRjY2JiMDU4M2I3NDlhM2E3OThhNDBjMjEiLCJjb2xsZWN0b3JfcmVjZWlwdF9zaGEyNTYiOiIyNDNiM2FlZGRmNzk2YjY5NWJjNDY2NzRiMzMwYWViOGRhODFlYmMxOGFmNTI3ZTAxMWQwZDgzMzYwZjhjNWE0IiwiY29tbWVudF9ieXRlcyI6IntcImJpbmRpbmdcIjp7XCJjYW5kaWRhdGVfaWRcIjpcIjcwYjY1OGYyZjg0NTExYzMzNWU0OGY5M2M0ZGE3ODY3OGU3NGJjNTg5ZDBkNmM3ZDdlMjgxNjNlNDcyODhmMzdcIixcImNvbnRyYWN0X2hhc2hcIjpcImU3YjQyYmMxZDM3YWJjMGJhZTFmM2ZkYTk3OTg1ODYyNzc1Nzc1NzhmNDFkY2VlZjVlZWY1N2ZiYWRjYzkyZTZcIixcImlzc3VlX2lkXCI6XCIyMDAxXCIsXCJwb2xpY3lfaGFzaFwiOlwiZGRmNmQxYzNkNTYwY2Y5NmU1Njk0OWUwMTJmNjg3NmQxN2EyYTc1NWMwZDc0ZTRjNmJkZWFiZDUwM2U3OTViZVwiLFwicHJvamVjdF9pZFwiOlwiOTRmZGQ3ZjEtMGE2ZC01Y2FkLTk1NzMtNDYyNTM5ZmZjZDRkXCIsXCJyZXBvc2l0b3J5X2lkXCI6MTAxLFwicmVxdWlyZW1lbnRzX2hhc2hcIjpcImM5Njc5ODY3YjgwNzMwZDYzM2ZiNzk2ZWFlMjlmZTNiZTBmM2UwMzE3ZmU0NWNiMTliNmU0MzE0Mzg0YWZmMTdcIn0sXCJjYW5kaWRhdGVfYmluZGluZ1wiOntcImJhc2Vfc2hhXCI6XCI0NDY3MzU2OTFiM2NlMzcxODc3YzE3Zjg1YmE5NTA1MTFiOWRmN2UxXCIsXCJoZWFkX3NoYVwiOlwiZTA2MGNiZTg0YTdlYWUyODI1ODc2ODVlNTJjZTdkZjY5Mzc5NzNkNFwiLFwicHJfbnVtYmVyXCI6NyxcInJlcG9zaXRvcnlfaWRcIjoxMDF9LFwiY3JlYXRlZF9hdFwiOlwiMjAyNi0wOS0wOVQxMjowMDowMFpcIixcImNyaXRpY19hcnRpZmFjdF9iaW5kaW5nXCI6e1wiY3JpdGljX3J1bl9pZFwiOlwiN2Q4NDY1ZmUtZDA4Yi01M2RlLTljODAtYzgzOWYwZGFkOGZlXCIsXCJoZWFkX3NoYVwiOlwiZTA2MGNiZTg0YTdlYWUyODI1ODc2ODVlNTJjZTdkZjY5Mzc5NzNkNFwiLFwicmVzdWx0X3NoYTI1NlwiOlwiNzFhZTVmZDczMjAxZDRhM2Q1MzBmZWViZTBlYWM4MjRlYzYzNjYxM2YxMDdmMDMwNDVlNGY0NWY4N2RjODNkYlwiLFwicm91bmRcIjoxfSxcImNyaXRpY19yZXZpZXdcIjp7XCJmaW5kaW5nc19zaGEyNTZcIjpcIjU4ZWI0ZWQ4MmZhOGJhZjA2OTk0ODczNDFjMzAxNTg2NjAxZWM2YjEwODNlZTJmYmFlMzZlY2M0OGY3NjBmY2ZcIixcImhlYWRfc2hhXCI6XCJlMDYwY2JlODRhN2VhZTI4MjU4NzY4NWU1MmNlN2RmNjkzNzk3M2Q0XCIsXCJyZWNvcmRfaWRcIjpcIjY3NDhlNDIwLTQ1ZTUtNTMzOS1iNmE3LTEyYzkyZTQ2ZjIwYVwiLFwicm91bmRcIjoxLFwicnVuX2lkXCI6XCI3ZDg0NjVmZS1kMDhiLTUzZGUtOWM4MC1jODM5ZjBkYWQ4ZmVcIixcInZlcmRpY3RcIjpcIkFQUFJPVkVcIn0sXCJldmlkZW5jZVwiOltcInVybjphd2Y6Zml4dHVyZTpleGFtcGxlLWV2aWRlbmNlXCIsXCJ1cm46YXdmOmNyaXRpYy1yZXZpZXc6Njc0OGU0MjAtNDVlNS01MzM5LWI2YTctMTJjOTJlNDZmMjBhXCJdLFwiaGVhZF9zaGFcIjpcImUwNjBjYmU4NGE3ZWFlMjgyNTg3Njg1ZTUyY2U3ZGY2OTM3OTczZDRcIixcIm93bmVyX2lkXCI6MTAwMSxcIm93bmVyX3Jldmlld1wiOmZhbHNlLFwicHJfYm9keV9saW5rXCI6XCJodHRwczovL2dpdGh1Yi5jb20vZml4dHVyZS9leGFtcGxlL3B1bGwvNyNyZXZpZXctdmVyZGljdC0xLTUzODM4Y2QyLTQ4YjYtNTI3My05MDhhLWRiMmM3MGZhY2YwY1wiLFwicHJfY29tbWVudF91cmxcIjpcImh0dHBzOi8vZ2l0aHViLmNvbS9maXh0dXJlL2V4YW1wbGUvcHVsbC83I2lzc3VlY29tbWVudC0xXCIsXCJwcm9kdWNlcl9pZFwiOlwiZml4dHVyZS1jcml0aWNcIixcInJlY29yZF9pZFwiOlwiNTM4MzhjZDItNDhiNi01MjczLTkwOGEtZGIyYzcwZmFjZjBjXCIsXCJyZXZpZXdlcl9pZFwiOlwiZml4dHVyZS1jcml0aWNcIixcInJvdW5kXCI6MSxcInJ1bl9pZFwiOlwiN2Q4NDY1ZmUtZDA4Yi01M2RlLTljODAtYzgzOWYwZGFkOGZlXCIsXCJ0aWVyXCI6MixcInZlcmRpY3RcIjpcIlBBU1NcIn0iLCJjb21tZW50X2lkIjoxLCJjb21tZW50X3NoYTI1NiI6ImE4MDBlMGMzYjI3YTUwOTM4N2NhNDg0OTQ5ZWJiYmQxYWViMGUxNGFkYTRmYTFiOTZlZDk5ZTJjOGI1OTdjMjAiLCJjb21tZW50X3VybCI6Imh0dHBzOi8vZ2l0aHViLmNvbS9maXh0dXJlL2V4YW1wbGUvcHVsbC83I2lzc3VlY29tbWVudC0xIiwiY3JpdGljX2FydGlmYWN0X2JpbmRpbmciOnsiY3JpdGljX3J1bl9pZCI6IjdkODQ2NWZlLWQwOGItNTNkZS05YzgwLWM4MzlmMGRhZDhmZSIsImhlYWRfc2hhIjoiZTA2MGNiZTg0YTdlYWUyODI1ODc2ODVlNTJjZTdkZjY5Mzc5NzNkNCIsInJlc3VsdF9zaGEyNTYiOiI3MWFlNWZkNzMyMDFkNGEzZDUzMGZlZWJlMGVhYzgyNGVjNjM2NjEzZjEwN2YwMzA0NWU0ZjQ1Zjg3ZGM4M2RiIiwicm91bmQiOjF9LCJvYnNlcnZlZF9hdCI6IjIwMjYtMDktMDlUMTI6MDA6MDBaIiwicHJfbnVtYmVyIjo3LCJwcm9kdWNlcl9pZCI6ImdpdGh1Yi1wcm9kdWN0aW9uLXBvc3RpbmctY29sbGVjdG9yIiwicHJvdmlkZXJfa2luZCI6ImdpdGh1YiIsInByb3ZpZGVyX3Jlc3BvbnNlX2J5dGVzIjp7ImRhdGEiOiJleUpqYjIxdFpXNTBYMkp2WkhraU9pSjdYQ0ppYVc1a2FXNW5YQ0k2ZTF3aVkyRnVaR2xrWVhSbFgybGtYQ0k2WENJM01HSTJOVGhtTW1ZNE5EVXhNV016TXpWbE5EaG1PVE5qTkdSaE56ZzJOemhsTnpSaVl6VTRPV1F3WkRaak4yUTNaVEk0TVRZelpUUTNNamc0WmpNM1hDSXNYQ0pqYjI1MGNtRmpkRjlvWVhOb1hDSTZYQ0psTjJJME1tSmpNV1F6TjJGaVl6QmlZV1V4WmpObVpHRTVOems0TlRnMk1qYzNOVGMzTlRjNFpqUXhaR05sWldZMVpXVm1OVGRtWW1Ga1kyTTVNbVUyWENJc1hDSnBjM04xWlY5cFpGd2lPbHdpTWpBd01Wd2lMRndpY0c5c2FXTjVYMmhoYzJoY0lqcGNJbVJrWmpaa01XTXpaRFUyTUdObU9UWmxOVFk1TkRsbE1ERXlaalk0Tnpaa01UZGhNbUUzTlRWak1HUTNOR1UwWXpaaVpHVmhZbVExTURObE56azFZbVZjSWl4Y0luQnliMnBsWTNSZmFXUmNJanBjSWprMFptUmtOMll4TFRCaE5tUXROV05oWkMwNU5UY3pMVFEyTWpVek9XWm1ZMlEwWkZ3aUxGd2ljbVZ3YjNOcGRHOXllVjlwWkZ3aU9qRXdNU3hjSW5KbGNYVnBjbVZ0Wlc1MGMxOW9ZWE5vWENJNlhDSmpPVFkzT1RnMk4ySTRNRGN6TUdRMk16Tm1ZamM1Tm1WaFpUSTVabVV6WW1Vd1pqTmxNRE14TjJabE5EVmpZakU1WWpabE5ETXhORE00TkdGbVpqRTNYQ0o5TEZ3aVkyRnVaR2xrWVhSbFgySnBibVJwYm1kY0lqcDdYQ0ppWVhObFgzTm9ZVndpT2x3aU5EUTJOek0xTmpreFlqTmpaVE0zTVRnM04yTXhOMlk0TldKaE9UVXdOVEV4WWpsa1pqZGxNVndpTEZ3aWFHVmhaRjl6YUdGY0lqcGNJbVV3TmpCalltVTROR0UzWldGbE1qZ3lOVGczTmpnMVpUVXlZMlUzWkdZMk9UTTNPVGN6WkRSY0lpeGNJbkJ5WDI1MWJXSmxjbHdpT2pjc1hDSnlaWEJ2YzJsMGIzSjVYMmxrWENJNk1UQXhmU3hjSW1OeVpXRjBaV1JmWVhSY0lqcGNJakl3TWpZdE1Ea3RNRGxVTVRJNk1EQTZNREJhWENJc1hDSmpjbWwwYVdOZllYSjBhV1poWTNSZlltbHVaR2x1WjF3aU9udGNJbU55YVhScFkxOXlkVzVmYVdSY0lqcGNJamRrT0RRMk5XWmxMV1F3T0dJdE5UTmtaUzA1WXpnd0xXTTRNemxtTUdSaFpEaG1aVndpTEZ3aWFHVmhaRjl6YUdGY0lqcGNJbVV3TmpCalltVTROR0UzWldGbE1qZ3lOVGczTmpnMVpUVXlZMlUzWkdZMk9UTTNPVGN6WkRSY0lpeGNJbkpsYzNWc2RGOXphR0V5TlRaY0lqcGNJamN4WVdVMVptUTNNekl3TVdRMFlUTmtOVE13Wm1WbFltVXdaV0ZqT0RJMFpXTTJNelkyTVRObU1UQTNaakF6TURRMVpUUm1ORFZtT0Rka1l6Z3paR0pjSWl4Y0luSnZkVzVrWENJNk1YMHNYQ0pqY21sMGFXTmZjbVYyYVdWM1hDSTZlMXdpWm1sdVpHbHVaM05mYzJoaE1qVTJYQ0k2WENJMU9HVmlOR1ZrT0RKbVlUaGlZV1l3TmprNU5EZzNNelF4WXpNd01UVTROall3TVdWak5tSXhNRGd6WldVeVptSmhaVE0yWldOak5EaG1Oell3Wm1ObVhDSXNYQ0pvWldGa1gzTm9ZVndpT2x3aVpUQTJNR05pWlRnMFlUZGxZV1V5T0RJMU9EYzJPRFZsTlRKalpUZGtaalk1TXpjNU56TmtORndpTEZ3aWNtVmpiM0prWDJsa1hDSTZYQ0kyTnpRNFpUUXlNQzAwTldVMUxUVXpNemt0WWpaaE55MHhNbU01TW1VME5tWXlNR0ZjSWl4Y0luSnZkVzVrWENJNk1TeGNJbkoxYmw5cFpGd2lPbHdpTjJRNE5EWTFabVV0WkRBNFlpMDFNMlJsTFRsak9EQXRZemd6T1dZd1pHRmtPR1psWENJc1hDSjJaWEprYVdOMFhDSTZYQ0pCVUZCU1QxWkZYQ0o5TEZ3aVpYWnBaR1Z1WTJWY0lqcGJYQ0oxY200NllYZG1PbVpwZUhSMWNtVTZaWGhoYlhCc1pTMWxkbWxrWlc1alpWd2lMRndpZFhKdU9tRjNaanBqY21sMGFXTXRjbVYyYVdWM09qWTNORGhsTkRJd0xUUTFaVFV0TlRNek9TMWlObUUzTFRFeVl6a3laVFEyWmpJd1lWd2lYU3hjSW1obFlXUmZjMmhoWENJNlhDSmxNRFl3WTJKbE9EUmhOMlZoWlRJNE1qVTROelk0TldVMU1tTmxOMlJtTmprek56azNNMlEwWENJc1hDSnZkMjVsY2w5cFpGd2lPakV3TURFc1hDSnZkMjVsY2w5eVpYWnBaWGRjSWpwbVlXeHpaU3hjSW5CeVgySnZaSGxmYkdsdWExd2lPbHdpYUhSMGNITTZMeTluYVhSb2RXSXVZMjl0TDJacGVIUjFjbVV2WlhoaGJYQnNaUzl3ZFd4c0x6Y2pjbVYyYVdWM0xYWmxjbVJwWTNRdE1TMDFNemd6T0dOa01pMDBPR0kyTFRVeU56TXRPVEE0WVMxa1lqSmpOekJtWVdObU1HTmNJaXhjSW5CeVgyTnZiVzFsYm5SZmRYSnNYQ0k2WENKb2RIUndjem92TDJkcGRHaDFZaTVqYjIwdlptbDRkSFZ5WlM5bGVHRnRjR3hsTDNCMWJHd3ZOeU5wYzNOMVpXTnZiVzFsYm5RdE1Wd2lMRndpY0hKdlpIVmpaWEpmYVdSY0lqcGNJbVpwZUhSMWNtVXRZM0pwZEdsalhDSXNYQ0p5WldOdmNtUmZhV1JjSWpwY0lqVXpPRE00WTJReUxUUTRZall0TlRJM015MDVNRGhoTFdSaU1tTTNNR1poWTJZd1kxd2lMRndpY21WMmFXVjNaWEpmYVdSY0lqcGNJbVpwZUhSMWNtVXRZM0pwZEdsalhDSXNYQ0p5YjNWdVpGd2lPakVzWENKeWRXNWZhV1JjSWpwY0lqZGtPRFEyTldabExXUXdPR0l0TlROa1pTMDVZemd3TFdNNE16bG1NR1JoWkRobVpWd2lMRndpZEdsbGNsd2lPaklzWENKMlpYSmthV04wWENJNlhDSlFRVk5UWENKOUlpd2lZMjl0YldWdWRGOXBaQ0k2TVN3aVkyOXRiV1Z1ZEY5MWNtd2lPaUpvZEhSd2N6b3ZMMmRwZEdoMVlpNWpiMjB2Wm1sNGRIVnlaUzlsZUdGdGNHeGxMM0IxYkd3dk55TnBjM04xWldOdmJXMWxiblF0TVNJc0luQnlYMkp2WkhraU9pSlRlVzUwYUdWMGFXTWdVRklnWW05a2VTQnliM1Z1WkNBeExseHVYRzV5WlhacFpYY3RkbVZ5WkdsamREb3hPalV6T0RNNFkyUXlMVFE0WWpZdE5USTNNeTA1TURoaExXUmlNbU0zTUdaaFkyWXdZenBoT0RBd1pUQmpNMkl5TjJFMU1Ea3pPRGRqWVRRNE5EazBPV1ZpWW1Ka01XRmxZakJsTVRSaFpHRTBabUV4WWprMlpXUTVPV1V5WXpoaU5UazNZekl3WEc0aWZRPT0iLCJlbmNvZGluZyI6ImJhc2U2NCJ9LCJwcm92aWRlcl9yZXNwb25zZV9zaGEyNTYiOiJmODlkY2NhMzkwN2ZhNDBkNTg4MGRmYzAzNWNlMTQzMGI1MmIzMGRiMjRmZmE1MGYzNGNlOTEzYmI2MGMxMTE3IiwicmVwb3NpdG9yeV9pZCI6MTAxLCJyZXZpZXdfdmVyZGljdF9yZWNvcmRfaWQiOiI1MzgzOGNkMi00OGI2LTUyNzMtOTA4YS1kYjJjNzBmYWNmMGMiLCJyZXZpZXdfdmVyZGljdF9zaGEyNTYiOiJhODAwZTBjM2IyN2E1MDkzODdjYTQ4NDk0OWViYmJkMWFlYjBlMTRhZGE0ZmExYjk2ZWQ5OWUyYzhiNTk3YzIwIiwicnVuX2lkIjoiOGU5YTI4OGYtMmRkOC01MjMxLTkwZmYtYjMzODEzOWI1ZGU3Iiwic291cmNlIjoiaG9zdF9vYnNlcnZhdGlvbiJ9"
        },
        "posting_observation_sha256": "7d1e35405b8aa861ea129ae42a49ba86ed4b3b13bc27657022c1b2e8c4a5408b"
      }
    ],
    "ticketed_p2_records": [],
    "evidence_registry": [
      {
        "uri": "urn:awf:fixture:example-evidence",
        "sha256": "9701f32841a7f0e2a6c9496384a68061f61e01478768582917df9c7612f53e21",
        "producer_id": "github-production-posting-collector",
        "retained_until": "2030-01-01T00:00:00Z"
      },
      {
        "uri": "urn:awf:critic-review:6748e420-45e5-5339-b6a7-12c92e46f20a",
        "sha256": "9918c2f84a4c5636b13f30a856f1cbbd8f69da1907b405ad4383346bd76ba8f7",
        "producer_id": "fixture-critic",
        "retained_until": "2030-01-01T00:00:00Z"
      },
      {
        "uri": "urn:awf:fixture:example-evidence/posting-1",
        "sha256": "5418c1f03a83ef19cfc659a3a459ba2a97a01f78ff694a565900d5913ed35985",
        "producer_id": "github-production-posting-collector",
        "retained_until": "2030-01-01T00:00:00Z"
      },
      {
        "uri": "urn:awf:review-round-receipt:1",
        "sha256": "d2458747cac533595c9657ef8c6ef998b0d6a147a09ad702388dac8e3b1c4938",
        "producer_id": "fixture-critic",
        "retained_until": "2030-01-01T00:00:00Z"
      }
    ],
    "provenance_mode": "production_observation"
  },
  "registry": {
    "github-production-posting-collector": {
      "provider_kind": "github",
      "implementation_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "release_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "repository_ids": [
        101
      ],
      "receipts": {
        "8e9a288f-2dd8-5231-90ff-b338139b5de7": "243b3aeddf796b695bc46674b330aeb8da81ebc18af527e011d0d83360f8c5a4"
      }
    }
  }
}'''
