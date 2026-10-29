#!/usr/bin/env python3
"""Plan A–F within accepted operating limits; write STREAMS.md and STREAMS.json."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic.canonical import MAX_DOCUMENT_BYTES, load_yaml, loads, now_text, sha256
from agentic.interaction import rejected_next_step
from agentic.installer import verify_installed
from agentic.model_routing import policy_from_config
from agentic.operating import default_operating, locked_operating, validate_operating
from agentic.safeio import Tree
from agentic.streams import EXTERNAL_CONFIG_PLACEHOLDER, native_execution_policy, write_project_plan


class PlannerParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValidationError(message)


def snapshot_config_path(config_path, project_root):
    """Return only repository-relative provenance or a non-path placeholder."""
    try:
        return config_path.relative_to(project_root).as_posix()
    except ValueError:
        return EXTERNAL_CONFIG_PLACEHOLDER


def _load_pinned_json(path, expected_sha256, label):
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)):
        raise ValidationError(f"{label} requires a lowercase SHA-256 pin")
    absolute = path.absolute()
    with Tree(absolute.parent) as source:
        raw = source.read(absolute.name, maximum=MAX_DOCUMENT_BYTES)
    if sha256(raw) != expected_sha256:
        raise ValidationError(f"{label} bytes differ from the caller's trusted hash")
    return loads(raw.decode("utf-8"))


def main(argv=None, owner_source_verifier=None):
    parser = PlannerParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Complete inventory obtained and verified by the trusted host coordinator")
    parser.add_argument("--expected-input-sha256", required=True, help="Hash pinned after authoritative inventory verification; a hash alone does not authenticate Jira")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", help="Explicit compare-and-swap update of an existing plan")
    parser.add_argument("--owner-reassignments", type=Path,
                        help="Pinned JSON owner stream-reassignment directives observed by the trusted host")
    parser.add_argument("--expected-owner-reassignments-sha256",
                        help="Trusted SHA-256 of --owner-reassignments")
    parser.add_argument("--assignment-state", type=Path,
                        help="Pinned controller state containing current sealed intake assignments")
    parser.add_argument("--expected-assignment-state-sha256",
                        help="Trusted SHA-256 of --assignment-state")
    parser.add_argument("--config", type=Path, help="Reviewed configuration for the target project; defaults to PROJECT_ROOT/.agentic/PROJECT_CONFIG.yaml")
    parser.add_argument("--host-writer-capacity", type=int, help="Observed total project writer slots, including the coordinator and retained active/paused owners; not merely free child slots")
    parser.add_argument("--coordinator-spawn-depth", type=int, default=0, help="Actual native coordinator depth (root=0); exhausted spawn depth permits its own writer only")
    parser.add_argument("--coordinator-agent-id", help="Observed native coordinator identity; prevents counting an existing coordinator owner as a new free writer")
    parser.add_argument("--allow-synthetic", action="store_true", help="Permit demonstration fixtures, never eligible for live dispatch")
    parser.add_argument("--redact-ownership-worktrees", action="store_true",
                        help="Replace absolute ownership worktrees with stable hashed placeholders after verifying the pinned inventory bytes")
    parser.add_argument("--now", default=None, help="RFC3339 test clock; omit for live planning")
    try:
        args = parser.parse_args(argv)
        verify_installed(ROOT)
        with Tree(args.input.absolute().parent) as source:
            raw = source.read(args.input.name, maximum=MAX_DOCUMENT_BYTES)
        value = loads(raw.decode("utf-8"))
        synthetic = args.allow_synthetic and value.get("inventory", {}).get("source") == "synthetic_fixture"
        target_config = args.project_root.absolute() / ".agentic/PROJECT_CONFIG.yaml"
        if args.config is not None:
            config_path = args.config.absolute()
            provenance_source = "explicit_target_project_configuration"
        elif target_config.exists():
            config_path = target_config
            provenance_source = "installed_target_project_configuration"
        elif synthetic:
            config_path = ROOT / ".agentic/PROJECT_CONFIG.yaml"
            provenance_source = "explicit_synthetic_release_defaults"
        else:
            raise ValidationError("Target project configuration is missing: read or prepare its native execution policy, or supply --config for that same project; no routine user authorization is needed")
        with Tree(config_path.parent) as configuration:
            config_bytes = configuration.read(config_path.name, maximum=MAX_DOCUMENT_BYTES)
        config = loads(config_bytes.decode("utf-8")) if config_path.suffix.lower() == ".json" else load_yaml(config_bytes)
        if not isinstance(config, dict) or "execution" not in config:
            raise ValidationError("Project configuration must supply its native execution policy")
        reassignment_arguments = (
            args.owner_reassignments, args.expected_owner_reassignments_sha256,
            args.assignment_state, args.expected_assignment_state_sha256,
        )
        if any(value is not None for value in reassignment_arguments) != all(
                value is not None for value in reassignment_arguments):
            raise ValidationError(
                "Owner stream reassignment requires both pinned directives and pinned assignment state")
        owner_reassignments = None
        intake_assignments = None
        if args.owner_reassignments is not None:
            if not callable(owner_source_verifier):
                raise ValidationError(
                    "Owner stream reassignment requires the embedded owner-source verifier")
            owner_reassignments = _load_pinned_json(
                args.owner_reassignments, args.expected_owner_reassignments_sha256,
                "Owner reassignment directives")
            assignment_state = _load_pinned_json(
                args.assignment_state, args.expected_assignment_state_sha256,
                "Intake assignment state")
            if (not isinstance(assignment_state, dict)
                    or not isinstance(assignment_state.get("tier_assignments"), dict)):
                raise ValidationError(
                    "Assignment state must contain current tier_assignments")
            intake_assignments = assignment_state["tier_assignments"]
        if not synthetic:
            identity = value.get("project", {})
            jira = config.get("jira", {})
            if isinstance(jira, dict) and jira.get("enabled", True) is False:
                raise ValidationError("Jira is disabled; this planner requires a Jira inventory. Continue provisional local stream planning through the native host.")
            github = config.get("github", {})
            if (not isinstance(identity, dict) or not isinstance(jira, dict) or not isinstance(github, dict)
                    or not isinstance(identity.get("repository"), str) or not isinstance(github.get("repository"), str)
                    or jira.get("project_key") != identity.get("key")
                    or github["repository"].casefold() != identity["repository"].casefold()):
                raise ValidationError("Execution configuration belongs to a different or unidentified Jira project/repository; use the target project's current policy")
        execution = native_execution_policy(config["execution"])
        if args.now and not synthetic:
            raise ValidationError("--now is restricted to explicitly allowed synthetic fixtures; live planning uses the actual clock")
        if synthetic and provenance_source == "explicit_synthetic_release_defaults":
            fixture = default_operating(policy_from_config(config), count=3)
            operating_context = nullcontext(validate_operating(fixture, config))
        else:
            operating_context = locked_operating(args.project_root, config)
        with operating_context as operating:
            result = write_project_plan(args.project_root, raw, args.expected_input_sha256,
                                        args.now or now_text(), ROOT, args.expected_plan_sha256, args.allow_synthetic,
                                        execution=execution, host_writer_capacity=args.host_writer_capacity,
                                        coordinator_spawn_depth=args.coordinator_spawn_depth,
                                        coordinator_agent_id=args.coordinator_agent_id,
                                        operating=operating, governance=config,
                                        redact_ownership_worktrees=args.redact_ownership_worktrees,
                                        owner_reassignments=owner_reassignments,
                                        verify_owner_reassignment=owner_source_verifier,
                                        intake_assignments=intake_assignments,
                                        execution_provenance={"source": provenance_source,
                                                              "config_path": snapshot_config_path(config_path, args.project_root.absolute()),
                                                              "config_sha256": sha256(config_bytes)})
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REJECTED", "reason": f"{type(exc).__name__}: {exc}",
                          "execution_authority": False, "prompt_user": False,
                          "next_step": rejected_next_step(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
