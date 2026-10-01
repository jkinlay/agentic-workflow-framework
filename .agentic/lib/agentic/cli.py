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
from .policy import inspect_config, validate_config


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
    config_parser.add_argument("--require-enablement", action="store_true",
                               help="Also require pinned CI and actual owner identities; does not qualify an adapter")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--json", action="store_true", help="Print the state, proofs and single next action as JSON")
    status_parser.add_argument("--adoption-pr", type=int, help="Optional adoption PR number; otherwise discover its receipt-changing commit's PR")
    status_parser.add_argument("--gh", help="Trusted host GitHub CLI executable for read-only acceptance observation")
    status_parser.add_argument("--release-source", type=Path, help="Verified release source outside this project; requires an independently approved manifest pin")
    status_parser.add_argument("--expected-manifest-sha256", help="Independent approved source manifest pin; otherwise use the trusted host installed AWF skill")
    record_parser = sub.add_parser("validate-record")
    record_parser.add_argument("type")
    record_parser.add_argument("file", type=Path)
    record_parser.add_argument("--head", default=None, help="Candidate head SHA an owner record was signed against (cap dispositions); finding dispositions carry their own head_sha; tier/closure records sign '-'")
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
    closeout_parser = sub.add_parser("validate-closeout", help="Bind a closeout record to recorded Git objects; never reads the working tree")
    closeout_parser.add_argument("record", type=Path)
    closeout_parser.add_argument("--repository", type=Path, help="Git repository whose object store holds the recorded commits (default: project root)")
    closeout_parser.add_argument("--render", action="store_true", help="Also print the derived Markdown rendering (output only)")
    digest_parser = sub.add_parser("digest", help="Render a fixed-shape evidence digest from validated records")
    digest_parser.add_argument("--ticket", required=True)
    digest_parser.add_argument("--for", dest="audience", choices=["jira", "pr", "owner"], default="owner")
    digest_parser.add_argument("--input", type=Path, required=True, help="JSON with state, gate, findings, validation, ci, reviewer")
    digest_parser.add_argument("--prose", default=None, help="Optional free prose to check against the 80-word cap")
    cap_parser = sub.add_parser("cap-plan", help="Translate an owner cap disposition into the lifecycle event, or refuse it by name")
    cap_parser.add_argument("--disposition", type=Path, required=True)
    cap_parser.add_argument("--findings", type=Path, required=True, help="JSON list of current findings")
    cap_parser.add_argument("--cycles", type=int, required=True)
    cap_parser.add_argument("--extensions", type=int, default=0)
    cap_parser.add_argument("--head", required=True, help="Candidate head SHA the owner signed against")
    cap_parser.add_argument("--config", type=Path)
    sub.add_parser("preflight", help="Host preflight rows (PASS/WARN/SKIP/N_A); never blocks INSTALLED")
    scan_parser = sub.add_parser("publication-scan", help="Scan every commit, patch, changed head file and supplied provider text")
    scan_parser.add_argument("--base", required=True)
    scan_parser.add_argument("--head", required=True)
    scan_parser.add_argument("--pr-body", type=Path, action="append", default=[])
    scan_parser.add_argument("--comment", type=Path, action="append", default=[])
    scan_parser.add_argument("--mapping", type=Path)
    scan_parser.add_argument("--config", type=Path)
    scan_parser.add_argument("--json", action="store_true")
    rewrite_parser = sub.add_parser("publication-rewrite", help="Squash an unpublished branch without changing its final tree")
    rewrite_parser.add_argument("--base", required=True)
    rewrite_parser.add_argument("--branch", required=True)
    rewrite_parser.add_argument("--commits", type=int, required=True)
    rewrite_parser.add_argument("--message-file", type=Path, required=True)
    rewrite_parser.add_argument("--mapping", type=Path)
    rewrite_parser.add_argument("--config", type=Path)
    rewrite_parser.add_argument("--json", action="store_true")
    render_parser = sub.add_parser("publication-render", help="Replace private mapping values in text with logical aliases")
    render_parser.add_argument("--input", type=Path, required=True)
    render_parser.add_argument("--mapping", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.absolute()
        if args.command == "publication-scan":
            from .publication import render_scan, scan_repository
            output = scan_repository(root, args.base, args.head, pr_body_paths=args.pr_body,
                                     comment_paths=args.comment, mapping_path=args.mapping, config_path=args.config)
            print(json.dumps(output, indent=2, ensure_ascii=False) if args.json else render_scan(output), end="")
            return 0 if output["status"] == "PASS" else 2
        elif args.command == "publication-rewrite":
            from .publication import rewrite_unpublished
            output = rewrite_unpublished(root, args.base, args.branch, args.commits, args.message_file,
                                         mapping_path=args.mapping, config_path=args.config)
            if args.json:
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                print(f"Publication rewrite: {output['status']}\nOld head: {output['old_head']}\nNew head: {output['head_sha']}\nTree: {output['head_tree']}\n{output['reflog_notice']}")
            return 0
        elif args.command == "publication-render":
            from .publication import load_mapping, render_aliases
            mapping, _digest, _path = load_mapping(root, args.mapping)
            print(render_aliases(args.input.read_text(encoding="utf-8", errors="replace"), mapping), end="")
            return 0
        elif args.command == "capabilities":
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
                          "native_streams_enabled_by_default": True, "default_max_parallel_tickets": 6,
                          "default_operating_streams": 3, "operating_ceiling_is_not_active_count": True,
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
        elif args.command == "preflight":
            from .host_preflight import preflight
            output = preflight(root)
        elif args.command == "digest":
            from .digest import check_prose, digest_sha256, render
            value = load(args.input)
            state = dict(value.get("state", {}))
            state.setdefault("ticket", args.ticket)
            if args.prose is not None:
                check_prose(args.prose)
            contracts = Contracts(root / ".agentic/schemas")
            if value.get("gate") is not None:
                contracts.validate("final-gate", value["gate"])
            from jsonschema import Draft202012Validator
            finding_schema = contracts.schemas["critic-review"]["properties"]["findings"]["items"]
            for finding in value.get("findings", []):
                errors = list(Draft202012Validator(finding_schema, registry=contracts.registry, format_checker=contracts.formats).iter_errors(finding))
                if errors:
                    raise ValidationError("digest finding rejected: " + errors[0].message)
            body = render(state, audience=args.audience, gate=value.get("gate"), findings=value.get("findings", []),
                          validation=value.get("validation"), reviewer=value.get("reviewer"), ci=value.get("ci"))
            print(body, end="")
            print(json.dumps({"digest_sha256": digest_sha256(body), "event_type": "evidence_comment", "execution_authority": False}, indent=2), file=sys.stderr)
            return 0
        elif args.command == "status":
            from .providers.github_status import project_status, render_status
            output = project_status(root, adoption_pr=args.adoption_pr, gh=args.gh,
                                    release_source=args.release_source, expected_manifest_sha256=args.expected_manifest_sha256)
            print(json.dumps(output, indent=2, ensure_ascii=False) if args.json else render_status(output))
            return 0 if output['project_state'] is not None else 2
        else:
            digest = verify_installed(root)
            if args.command == "verify-installation":
                output = {"integrity_valid": True, "source_manifest_sha256": digest, "execution_authority": False}
            else:
                contracts = Contracts(root / ".agentic/schemas")
                workflow = load(root / ".agentic/workflow.yaml")
                if args.command == "validate-config":
                    config = load(args.config or root / ".agentic/PROJECT_CONFIG.yaml")
                    instructions = root / "PROJECT_INSTRUCTIONS.md"
                    output = inspect_config(config, workflow, contracts,
                                            project_instructions=instructions.read_text(encoding="utf-8") if instructions.is_file() else None)
                    from .operating_status import with_operating
                    output = with_operating(root, config, output)
                    output.update(configuration_valid_for="adoption", policy_hash=output['policy_sha256'])
                    if output['status'] == 'REJECTED':
                        print(json.dumps(output, indent=2, ensure_ascii=False))
                        return 2
                    if args.require_enablement:
                        from .configuration import require_enablement_config
                        require_enablement_config(config, workflow, contracts)
                        output['enablement_configuration_valid'] = True
                        output['adapter_qualification_still_required'] = True
                elif args.command == "validate-record":
                    value = load(args.file)
                    contracts.validate(args.type, value)
                    local_semantics(args.type, value)
                    output = {"record_shape_and_local_semantics_valid": True, "cross_record_validation_required": True, "live_source_verified": False, "execution_authority": False}
                    if args.type == "ticket-contract":
                        from .review_policy import check_tier_declaration
                        config = load(root / ".agentic/PROJECT_CONFIG.yaml")
                        paths = value["scope"]["expected_paths"] + value["scope"]["allowed_adjacent_paths"]
                        output["risk_tier"] = check_tier_declaration(config, value, paths)
                        output["tier_paths_checked"] = sorted(paths)
                    if args.type in {"critic-review", "specialist-review"}:
                        from .review_policy import lineage_errors
                        errors = lineage_errors(value["findings"], [])
                        if errors:
                            raise ValidationError("; ".join(errors))
                    if args.type in {"finding-disposition", "review-cap-disposition", "tier-reassignment", "owner-closure"}:
                        from .authorization import verify_owner_record
                        config = load(root / ".agentic/PROJECT_CONFIG.yaml")
                        head = value.get("head_sha") or args.head or "-"
                        output["owner_record"] = verify_owner_record(value, args.type, config, contracts, now_text(), head_sha=head,
                                                                     binding=value["binding"])
                        output["producer_run_verified"] = False
                        output["binding_cross_checked"] = False
                elif args.command == "validate-closeout":
                    from .closeout import render_markdown, validate_closeout
                    record = load(args.record)
                    output = validate_closeout(record, args.repository or root, contracts)
                    if args.render:
                        output["markdown"] = render_markdown(record)
                elif args.command == "cap-plan":
                    from .review_policy import cap_disposition_plan
                    config = load(args.config or root / ".agentic/PROJECT_CONFIG.yaml")
                    disposition = load(args.disposition)
                    from .authorization import verify_owner_record
                    verify_owner_record(disposition, "review-cap-disposition", config, contracts, now_text(), head_sha=args.head,
                                        binding=disposition["binding"])
                    findings = [f for f in load(args.findings) if f["status"] != "RESOLVED" and f["severity"] in set(config["critic"]["blocking_severities"])]
                    output = cap_disposition_plan(config, disposition, args.cycles, args.extensions, findings)
                    output["execution_authority"] = False
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
