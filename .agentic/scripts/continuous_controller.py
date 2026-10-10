#!/usr/bin/env python3
"""Digest-pinned production entry point for continuous controller adapters."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import argparse
import json
import types

from agentic import ValidationError
from agentic.canonical import load, sha256
from agentic.safeio import Tree
from agentic.continuous_controller import (
    ContinuousControllerStore,
    production_controller_cycle,
    production_jira_lifecycle,
    production_merge_observed,
)
from agentic.owner_publication import OwnerPublicationStore, prepare_owner_publication, resume_owner_publication


def _load_reviewed_adapters(path, expected_sha256, config_path):
    """Execute exactly one digest-pinned adapter module; never a shell string."""
    if path is None or expected_sha256 is None:
        raise ValidationError("Production commands require a reviewed adapter module and SHA-256 pin")
    if not path.is_absolute():
        raise ValidationError("Reviewed adapter path must be absolute")
    with Tree(path.parent) as tree:
        raw = tree.read(path.name, maximum=1024 * 1024)
    if sha256(raw) != expected_sha256:
        raise ValidationError("Reviewed adapter bytes differ from the configured SHA-256 pin")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Reviewed adapter must be UTF-8 Python source") from exc
    module = types.ModuleType("_awf_reviewed_controller_adapter")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    factory = getattr(module, "build_adapters", None)
    if not callable(factory):
        raise ValidationError("Reviewed adapter must expose build_adapters(config)")
    config = load(config_path) if config_path is not None else {}
    adapters = factory(config)
    if not isinstance(adapters, dict) or not all(isinstance(name, str) for name in adapters):
        raise ValidationError("Reviewed adapter factory must return a named adapter mapping")
    return adapters


def _require_adapters(adapters, names):
    missing = sorted(name for name in names if not callable(adapters.get(name)))
    if missing:
        raise ValidationError("Reviewed adapter is missing callable operations: " + ", ".join(missing))
    return {name: adapters[name] for name in names}


def _jira_progress_request(path):
    value = load(path)
    allowed = {"jira_enabled", "merged_ticket", "scope", "observed_at", "jira_binding",
               "include_epics", "max_pages", "max_items", "max_bytes", "max_seconds"}
    required = {"jira_enabled", "merged_ticket", "scope", "observed_at", "jira_binding"}
    if not isinstance(value, dict) or not required <= set(value) <= allowed:
        raise ValidationError("Post-merge Jira request has missing or unknown fields")
    return value


def main(argv=None, default_root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--stream", action="append", dest="streams")
    parser.add_argument("--worktree-root", type=Path, action="append", required=True)
    parser.add_argument("--cadence-seconds", type=int)
    parser.add_argument("--disable-periodic-status", action="store_true",
                        help="disable scheduled status digests while keeping change digests enabled")
    parser.add_argument("--migrate-status-cadence", action="store_true",
                        help="explicitly replace the protected state database's saved cadence with the configured value")
    parser.add_argument("--project-config", type=Path)
    parser.add_argument("--adapter-module", type=Path)
    parser.add_argument("--adapter-sha256")
    parser.add_argument("--adapter-config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    cycle = sub.add_parser("cycle", help="Run inventory, dispatch and status delivery through reviewed adapters")
    cycle.add_argument("--inventory-binding", type=Path, required=True)
    cycle.add_argument("--repository-root", type=Path, required=True)
    cycle.add_argument("--repository-head-sha", required=True)
    cycle.add_argument("--repository-tree-sha", required=True)
    cycle.add_argument("--now", required=True)
    cycle.add_argument("--host-capacity", type=int, required=True)
    cycle.add_argument("--dispatch-role", choices=("writer", "critic"), default="writer")


    jira = sub.add_parser("jira-lifecycle", help="Read, optionally write once, and read back Jira lifecycle state")
    jira.add_argument("--contract", type=Path, required=True)
    jira.add_argument("--event", required=True)
    jira.add_argument("--facts", type=Path, required=True)
    jira.add_argument("--binding", type=Path, required=True)
    jira.add_argument("--issue-type", default="LEAF")
    jira.add_argument("--lifecycle-state")
    jira.add_argument("--producer-id", required=True)
    jira.add_argument("--run-id", required=True)
    jira.add_argument("--now", required=True)
    jira.add_argument("--evidence", action="append", default=[])
    jira.add_argument("--transition-id", required=True)
    jira.add_argument("--merge-result-id")

    merge = sub.add_parser("merge-observed", help="Validate merge, reconcile Jira, then count a fresh scope snapshot")
    merge.add_argument("--lifecycle-state", required=True)
    merge.add_argument("--lifecycle-facts", type=Path, required=True)
    merge.add_argument("--jira-progress", type=Path, required=True)

    owner_prepare = sub.add_parser("owner-publication-prepare", help="Retain completed streams and emit one owner push command")
    owner_prepare.add_argument("--request", type=Path, required=True)
    owner_prepare.add_argument("--now", required=True)
    owner_resume = sub.add_parser("owner-publication-resume", help="Observe owner-pushed heads and resume draft PR creation")
    owner_resume.add_argument("--batch", required=True)
    owner_resume.add_argument("--now", required=True)
    owner_status = sub.add_parser("owner-publication-status", help="Read the retained handoff without a provider call")
    owner_status.add_argument("--batch", required=True)

    sub.add_parser("snapshot")
    args = parser.parse_args(argv)
    config_path = args.project_config or (default_root / ".agentic/PROJECT_CONFIG.yaml")
    config = load(config_path)
    configured_cadence = config.get("controller", {}).get("status_cadence_seconds", 600)
    cadence_seconds = args.cadence_seconds if args.cadence_seconds is not None else configured_cadence
    if not isinstance(cadence_seconds, int) or isinstance(cadence_seconds, bool) or cadence_seconds < 1:
        parser.error("controller.status_cadence_seconds must be a positive integer")
    if args.command.startswith("owner-publication-"):
        store = OwnerPublicationStore(args.state, worktree_roots=args.worktree_root)
    else:
        store = ContinuousControllerStore(args.state, args.streams, cadence_seconds,
                                          worktree_roots=args.worktree_root,
                                          periodic_status_enabled=not args.disable_periodic_status,
                                          migrate_cadence=args.migrate_status_cadence)
    adapters = None
    if args.command in {"cycle", "jira-lifecycle", "merge-observed", "owner-publication-prepare",
                        "owner-publication-resume"}:
        adapters = _load_reviewed_adapters(args.adapter_module, args.adapter_sha256,
                                           args.adapter_config)
    if args.command == "owner-publication-prepare":
        calls = _require_adapters(adapters, {"authorize_owner_publication"})
        output = prepare_owner_publication(store, config, load(args.request), now=args.now, **calls)
    elif args.command == "owner-publication-resume":
        calls = _require_adapters(adapters, {"observe_identity", "observe_remote_heads", "create_draft_pr", "observe_draft_pr"})
        output = resume_owner_publication(store, args.batch, config, now=args.now, **calls)
    elif args.command == "owner-publication-status":
        output = store.snapshot(args.batch, config)
    elif args.command == "cycle":
        calls = _require_adapters(adapters, {"observe_inventory", "dispatch_ticket",
                                             "observe_dispatch", "deliver_status", "observe_publication"})
        output = production_controller_cycle(
            store, now=args.now, host_capacity=args.host_capacity,
            inventory_binding=load(args.inventory_binding), repository_root=args.repository_root,
            repository_head_sha=args.repository_head_sha,
            repository_tree_sha=args.repository_tree_sha, publication_config=config,
            dispatch_role=args.dispatch_role, **calls)

    elif args.command == "jira-lifecycle":
        calls = _require_adapters(adapters, {"observe_provider_identity", "read_current_status", "write_transition",
                                             "read_transition"})
        output = production_jira_lifecycle(store,
            config=config, contract=load(args.contract), event=args.event,
            facts=load(args.facts), binding=load(args.binding), issue_type=args.issue_type,
            state=args.lifecycle_state, producer_id=args.producer_id, run_id=args.run_id, now=args.now,
            evidence=args.evidence, transition_id=args.transition_id,
            merge_result_id=args.merge_result_id, **calls)
    elif args.command == "merge-observed":
        calls = _require_adapters(adapters, {"reconcile_merged_ticket", "fetch_scope_page"})
        progress = _jira_progress_request(args.jira_progress)
        output = production_merge_observed(
            lifecycle_state=args.lifecycle_state,
            lifecycle_facts=load(args.lifecycle_facts),
            jira_progress={**progress, **calls})
    else:
        output = {"streams": store.snapshot(), "execution_authority": False}
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
