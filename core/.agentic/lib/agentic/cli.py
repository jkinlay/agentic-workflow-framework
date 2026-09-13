"""Command-line interface for the released reference tooling."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from . import ValidationError, VERSION
from .authorization import make_request, parse_text, request_fields, verify_record
from .canonical import load, now_text, sha256, unique
from .contracts import Contracts
from .gates import evaluate
from .installer import verify_installed
from .policy import validate_config


def local_semantics(name, value):
    def recurse(item, key=""):
        if isinstance(item, dict):
            for field, child in item.items():
                recurse(child, field)
        elif isinstance(item, list):
            if key in {"acceptance_criteria", "findings", "prior_findings"} and item:
                unique(item, "id", key)
            for child in item:
                recurse(child)
    recurse(value)
    if name == "owner-authorization":
        decision, fields = parse_text(value["source"]["raw_body"])
        if decision != value["decision"] or fields != request_fields(value):
            raise ValidationError("Raw authorization text disagrees with record fields")
        if sha256(value["source"]["raw_body"].encode()) != value["source"]["raw_body_sha256"]:
            raise ValidationError("Raw authorization digest mismatch")
    for finding in value.get("findings", []):
        if finding["status"] == "RESOLVED" and not finding["resolution_evidence"]:
            raise ValidationError("Resolved finding needs evidence")
    return value


def main(argv=None, default_root=None):
    parser = argparse.ArgumentParser(description=f"Agentic Workflow {VERSION} offline evidence tools; native streams use the host's delegation tools and PR automation uses review_loop.py")
    parser.add_argument("--root", type=Path, default=default_root or Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-installation")
    config_parser = sub.add_parser("validate-config")
    config_parser.add_argument("--config", type=Path)
    record_parser = sub.add_parser("validate-record")
    record_parser.add_argument("type")
    record_parser.add_argument("file", type=Path)
    gate_parser = sub.add_parser("evaluate")
    gate_parser.add_argument("bundle", type=Path)
    gate_parser.add_argument("--config", type=Path)
    gate_parser.add_argument("--now", default=None)
    gate_parser.add_argument("--request", action="store_true")
    auth_parser = sub.add_parser("verify-authorization")
    for key in ["record", "request", "gate", "config"]:
        auth_parser.add_argument("--" + key, type=Path, required=True)
    auth_parser.add_argument("--now", default=None)
    sub.add_parser("capabilities")
    args = parser.parse_args(argv)
    try:
        root = args.root.absolute()
        if args.command == "capabilities":
            output = {"version": VERSION, "profile": "manual_reference", "live_dispatch": False, "live_jira_mutations": False,
                      "live_merge": False, "available": ["installation", "schema_validation", "offline_gate_evaluation", "authorization_consistency", "local_state_library", "simulation_tests"],
                      "separate_host_review_loop": {"entry_point": ".agentic/scripts/review_loop.py", "available": True,
                          "automatic_by_default_after_enrollment": True, "can_launch_codex_and_push_amendments": True,
                          "requires_external_host_qualification": True, "installer_activates_schedule": False,
                          "max_active_host_ticks": 1, "capacity_scope": "enrolled PR review-loop ticks only",
                          "merge_or_jira_write": False},
                      "model_routing": {"entry_point": ".agentic/scripts/route_model.py",
                          "default_profile": "balanced", "configured_automatic_escalation": True,
                          "durable_budget_reservations": True, "adaptive_default": "shadow",
                          "launches_models": False, "attests_actual_model": False,
                          "provider_enforced_usage_caps": False, "scheduled_pr_loop_integration": False},
                      "project_coordination": {"routine_authorization_prompts": False,
                          "explicit_next_step_reporting": True,"proactive_native_stream_agents": False,
                          "native_coordination_mode": "host-dependent guidance",
                          "launches_native_agents": False, "attests_active_writers": False,
                          "execution_cap_increases_require_human_direction": True,
                          "native_streams_enabled_by_default": True, "default_max_parallel_tickets": 3,
                          "default_max_parallel_tickets_per_stream": 1,
                          "writer_count_includes_coordinator_when_implementing": True,
                          "requires_separate_concurrency_activation": False,
                          "requires_review_loop_qualification_for_native_streams": False,
                          "requires_reference_controller_or_broker": False,
                          "defaults_are_not_observed_runtime_capacity": True,
                          "status_update_ends_work": False, "project_cycle_command": "project_status.py cycle",
                          "continue_all_ready_streams_and_reviews": True,
                          "input_draft_transport": "optional host compare-and-set adapter; copyable text fallback",
                          "ships_codex_composer_adapter": False, "submits_authorizations": False,
                          "stream_plan_entry_point": ".agentic/scripts/plan_streams.py",
                          "status_entry_point": ".agentic/scripts/project_status.py",
                          "native_host_delegation_required": True,"automatic_jira_mutations": False}}
        else:
            digest = verify_installed(root)
            if args.command == "verify-installation":
                output = {"integrity_valid": True, "source_manifest_sha256": digest, "execution_authority": False}
            else:
                contracts = Contracts(root / ".agentic/schemas")
                workflow = load(root / ".agentic/workflow.yaml")
                if args.command == "validate-config":
                    config = load(args.config or root / ".agentic/PROJECT_CONFIG.yaml")
                    output = {"configuration_valid_for": "manual_reference", "policy_hash": validate_config(config, workflow, contracts), "execution_authority": False}
                elif args.command == "validate-record":
                    value = load(args.file)
                    contracts.validate(args.type, value)
                    local_semantics(args.type, value)
                    output = {"record_shape_and_local_semantics_valid": True, "cross_record_validation_required": True, "live_source_verified": False, "execution_authority": False}
                elif args.command == "evaluate":
                    config = load(args.config or root / ".agentic/PROJECT_CONFIG.yaml")
                    gate = evaluate(config, workflow, load(args.bundle), contracts, args.now or now_text())
                    output = make_request(gate, contracts, args.now or now_text()) if args.request else gate
                elif args.command == "verify-authorization":
                    config = load(args.config)
                    validate_config(config, workflow, contracts)
                    output = verify_record(load(args.record), load(args.request), load(args.gate), config, contracts, args.now or now_text())
                else:
                    raise ValidationError("Unsupported command")
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except (ValidationError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "REJECTED", "reason": str(exc), "execution_authority": False}, indent=2), file=sys.stderr)
        return 2
