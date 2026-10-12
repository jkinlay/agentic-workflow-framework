#!/usr/bin/env python3
"""Verify and transactionally install managed v1.9.4 workflow files."""
from pathlib import Path
import argparse
import json
import os
import sys
import tempfile
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
import importlib.util
import yaml
_missing = [name for name in ("yaml", "jsonschema") if importlib.util.find_spec(name) is None]
if _missing:
    raise SystemExit("BOOTSTRAP REJECTED: missing locked dependencies (" + ", ".join(_missing) + "). Install them first: "
                     "python -m pip install --require-hashes --only-binary=:all: -r .agentic/requirements.lock")
from agentic.installer import complete_runtime_transaction, install, recover
from agentic import ValidationError
from agentic.canonical import now_text
from agentic.adoption_config import (ensure_installed_runtime, post_install_checks,
                                     prevalidate_runtime_wheelhouse,
                                     assign_project_start)


def assign_startup_inventory(config, inventory, state, jira_client, *, now, controller_actor_id):
    """Controller hook for assigning a complete bootstrap ticket inventory."""
    return assign_project_start(config, inventory, state, jira_client, now=now,
                                controller_actor_id=controller_actor_id)


def _load_json(path, description):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValidationError(f"{description} must be a bounded regular JSON file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{description} is not valid UTF-8 JSON") from exc
    return value


def _prepare_assignment_state_path(path, dest):
    path = Path(path).expanduser().resolve()
    dest = Path(dest).expanduser().resolve()
    if path == dest or path.is_relative_to(dest):
        raise ValidationError("Assignment state must remain outside the adopted repository/worktree")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir():
        raise ValidationError("Assignment state parent must be a writable directory")
    descriptor, probe = tempfile.mkstemp(prefix=path.name + ".", suffix=".probe", dir=path.parent)
    os.close(descriptor)
    os.unlink(probe)
    return path


def _write_assignment_state(path, state, dest):
    path = _prepare_assignment_state_path(path, dest)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _run_assignment_path(args, jira_client, owner_source_verifier):
    requested = (args.project_start_inventory is not None or args.ticket_intake is not None
                 or args.assignment_change is not None)
    if not requested:
        return None
    if args.recover or args.dry_run:
        raise ValidationError("Project-start/ticket-intake assignment requires a completed non-recovery bootstrap")
    if args.assignment_state is None or args.controller_actor_id is None:
        raise ValidationError("Assignment execution requires --assignment-state and --controller-actor-id")
    config_path = args.dest / ".agentic/PROJECT_CONFIG.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValidationError("Installed project configuration is unavailable for assignment") from exc
    if not isinstance(config, dict):
        raise ValidationError("Installed project configuration must be an object")
    # Refuse invalid or unwritable controller-state destinations before an
    # assignment can perform any Jira read-modify-write operation.
    state_path = _prepare_assignment_state_path(args.assignment_state, args.dest)
    state = _load_json(state_path, "Assignment state") if state_path.exists() else {}
    if not isinstance(state, dict):
        raise ValidationError("Assignment state must be an object")
    from agentic.intake_assignment import load_runtime_assignments
    load_runtime_assignments(config, state)
    assigned_at = args.assignment_now or now_text()
    try:
        if args.project_start_inventory is not None:
            inventory = _load_json(args.project_start_inventory, "Project-start inventory")
            records = assign_startup_inventory(config, inventory, state, jira_client,
                                               now=assigned_at,
                                               controller_actor_id=args.controller_actor_id)
        elif args.ticket_intake is not None:
            from agentic.intake_assignment import assign_intake, inherit_pr_assignment
            item = _load_json(args.ticket_intake, "Ticket intake record")
            record = assign_intake(
                config, item, state, jira_client, now=assigned_at,
                controller_actor_id=args.controller_actor_id, source="intake")
            records = {item.get("key"): record}
            pr_number, pr_body = item.get("pr_number"), item.get("pr_body")
            if (pr_number is None) != (pr_body is None):
                raise ValidationError("Ticket intake PR inheritance requires both pr_number and pr_body")
            if pr_number is not None:
                inherit_pr_assignment(state, item["key"], pr_number, pr_body)
        else:
            from agentic.intake_assignment import change_assignment
            change = _load_json(args.assignment_change, "Assignment change")
            if not callable(owner_source_verifier):
                raise ValidationError("Assignment change requires the embedded owner-source verifier")
            required = {"ticket_key", "requested_tier", "requested_cap", "reason", "owner_source"}
            if not isinstance(change, dict) or not required.issubset(change):
                raise ValidationError("Assignment change is missing required fields")
            record = change_assignment(
                config, change["ticket_key"], state, jira_client,
                requested_tier=change["requested_tier"], requested_cap=change["requested_cap"],
                owner_source=change["owner_source"], verify_owner_source=owner_source_verifier,
                reason=change["reason"], now=assigned_at,
                controller_actor_id=args.controller_actor_id)
            records = {change["ticket_key"]: record}
    finally:
        # Unknown/mismatched Jira outcomes must survive even when assignment raises.
        _write_assignment_state(state_path, state, args.dest)
    return {key: {"assignment_id": record["assignment_id"],
                  "assignment_sha256": record["assignment_sha256"],
                  "tier": record["tier"], "round_cap": record["round_cap"]}
            for key, record in records.items()}


def main(argv=None, jira_client=None, owner_source_verifier=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--mode", choices=["install", "upgrade"], default="install")
    parser.add_argument("--propose-operating-capacity", action="store_true",
        help="Stage an existing project's ceiling of at least six and derived reviewer count in local governance for an owner-reviewed adoption PR")
    parser.add_argument("--on-conflict", choices=["error", "backup"], default="error")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--runtime-wheelhouse", type=Path,
        help="Mandatory for writing install/upgrade: offline directory containing exactly one complete requirements.lock-pinned wheel per runtime dependency")
    parser.add_argument("--project-name")
    parser.add_argument("--project-short-name")
    parser.add_argument("--jira-key")
    parser.add_argument("--jira-site")
    parser.add_argument("--github-repo")
    parser.add_argument("--repository-id", type=int)
    parser.add_argument("--test-command", help="Record a project validation command; bootstrap does not execute it")
    parser.add_argument("--codeowner", default="@maintainer", help="Seed one @handle or @organization/team only when no project CODEOWNERS exists")
    parser.add_argument("--rules-observation", type=Path, help="Optional pinned read-only rules observation; missing or invalid evidence never blocks adoption")
    parser.add_argument("--expected-rules-observation-sha256")
    parser.add_argument("--default-branch", help="Optional expected branch; live observation discovers the repository's actual default")
    parser.add_argument("--review-app-id", type=int, help="Expected App ID for the awf/review rules prerequisite; not adapter qualification")
    assignment = parser.add_mutually_exclusive_group()
    assignment.add_argument("--project-start-inventory", type=Path,
        help="JSON ticket inventory assigned after a successful bootstrap")
    assignment.add_argument("--ticket-intake", type=Path,
        help="One JSON ticket record assigned after a successful bootstrap")
    assignment.add_argument("--assignment-change", type=Path,
        help="One verifier-bound owner assignment change applied after bootstrap")
    parser.add_argument("--assignment-state", type=Path,
        help="Controller-owned assignment state outside the repository/worktree")
    parser.add_argument("--assignment-now",
        help="Observed assignment time; defaults to the current UTC time")
    parser.add_argument("--controller-actor-id",
        help="Configured controller identity used for Jira assignment writes")
    args = parser.parse_args(argv)
    # Resolve platform aliases such as macOS /tmp -> /private/tmp before the
    # installer's alias-refusing path checks run.
    args.dest = args.dest.expanduser().resolve()
    try:
        if args.recover and args.propose_operating_capacity:
            raise ValidationError("Recovery cannot stage a governance proposal; recover first and rerun adoption explicitly")
        if args.recover:
            result = recover(args.dest)
        else:
            prepared_wheelhouse = None
            if not args.dry_run:
                prepared_wheelhouse = prevalidate_runtime_wheelhouse(
                    ROOT, args.expected_manifest_sha256, args.runtime_wheelhouse)
            overrides = {}
            for source, target in [("project_name", "name"), ("project_short_name", "short_name"),
                    ("jira_key", "jira_key"), ("jira_site", "jira_site"), ("github_repo", "repository"),
                    ("repository_id", "repository_id"), ("test_command", "test_command"), ("default_branch", "base_branch")]:
                if getattr(args, source) is not None:
                    overrides[target] = getattr(args, source)
            result = install(ROOT, args.dest, args.expected_manifest_sha256, args.mode, args.on_conflict, overrides, args.dry_run,
                codeowner=args.codeowner, rules_observation=args.rules_observation,
                expected_rules_observation_sha256=args.expected_rules_observation_sha256,
                default_branch=args.default_branch, review_app_id=args.review_app_id, configure=True,
                propose_operating_capacity=args.propose_operating_capacity,
                defer_runtime=not args.dry_run)
            if not args.dry_run:
                transaction_id = result.pop("_runtime_transaction_id", None)
                if transaction_id is None:
                    try:
                        recover(args.dest)
                    except (ValidationError, OSError, ValueError) as rollback_error:
                        raise ValidationError(
                            "Installer lost the bootstrap transaction identity and rollback could not be proved") from rollback_error
                    raise ValidationError("Installer did not retain the bootstrap runtime transaction; managed files were rolled back")
                def build_runtime(transaction):
                    if result.get("source_manifest_sha256") != prepared_wheelhouse.source_manifest_sha256:
                        raise ValidationError("Installed release identity changed after runtime prevalidation")
                    return ensure_installed_runtime(
                        args.dest, prepared_wheelhouse=prepared_wheelhouse,
                        transaction=transaction)
                result["runtime"] = complete_runtime_transaction(
                    args.dest, transaction_id, build_runtime)
                result.update(post_install_checks(args.dest, result))
            assignments = _run_assignment_path(args, jira_client, owner_source_verifier)
            if assignments is not None:
                result["intake_assignments"] = assignments
        print(json.dumps(result, indent=2))
        return 1 if result["status"] == "INSTALLED_UNCONFIGURED" else 2 if result["status"] == "INSTALLATION_VERIFICATION_FAILED" else 0
    except (ValidationError, OSError, ValueError) as exc:
        print(f"BOOTSTRAP REJECTED: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
