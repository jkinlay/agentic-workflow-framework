"""Baseline policy invariants and deterministic scope/trigger classification."""
from __future__ import annotations
import fnmatch
import json
from urllib.parse import urlsplit

from . import CapabilityUnavailable, ValidationError, VERSION
from .canonical import fingerprint, unique

PROTECTED_PATHS = ["AGENTS.md", "**/AGENTS.md", ".agentic/**", ".codex/**", ".github/workflows/**",
                   ".github/CODEOWNERS", "CODEOWNERS", "scripts/bootstrap_project.py"]
CAPABILITIES = {
    "controller": {"read_evidence", "write_evidence", "propose_dispatch", "propose_jira_transition"},
    "worker": {"read_evidence", "write_assigned_worktree", "run_isolated_tests", "submit_worker_result"},
    "critic": {"read_evidence", "run_isolated_tests", "submit_review"},
    "specialist": {"read_evidence", "run_isolated_tests", "submit_review"},
    "collector": {"read_external_state", "write_evidence"},
    "owner": {"authorize_candidate", "deny_candidate", "revoke_authorization"},
    "verifier": {"read_evidence", "verify_source_event"},
}


def require_reference_capability(action):
    if action not in {"validate", "evaluate", "parse_authorization", "simulate", "local_state"}:
        raise CapabilityUnavailable(f"Capability {action!r} is not supplied by the offline reference tools")


def safe_path(value):
    from .safeio import relative_parts
    relative_parts(value)
    return value


def validate_config(config, workflow, contracts):
    contracts.validate("project-config", config)
    unresolved = []
    def placeholders(value, path="$", key=None):
        if isinstance(value, dict):
            for name, child in value.items():
                child_path = path + ("." + name if name.isidentifier() else "[" + json.dumps(name) + "]")
                placeholders(child, child_path, name)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                placeholders(child, f"{path}[{index}]", key)
        elif isinstance(value, str):
            reason = ("unresolved placeholder" if "CHANGE_ME" in value or "SET_BY_BOOTSTRAP" in value else
                      "whitespace-only value" if value and not value.strip() else
                      "zero UUID placeholder" if path == "$.project.id" and value == "00000000-0000-0000-0000-000000000000" else
                      "zero commit SHA placeholder" if key == "workflow_sha" and value == "0" * 40 else None)
            if reason:
                unresolved.append(f"{path}: {reason}")
    placeholders(config)
    if unresolved:
        raise ValidationError("Unresolved configuration values: " + "; ".join(unresolved))
    if workflow.get("template_version") != VERSION or workflow.get("version") != 3:
        raise ValidationError(f"workflow.template_version / workflow.version: expected {VERSION} with schema revision 3")
    if workflow.get("live_side_effects_supported") is not False:
        raise ValidationError("workflow.live_side_effects_supported: this release does not provide a live side-effect adapter")
    from .lifecycle import validate_workflow
    validate_workflow(workflow)
    if not {"BLOCKER", "MAJOR"}.issubset(config["critic"]["blocking_severities"]):
        raise ValidationError("$.critic.blocking_severities: BLOCKER and MAJOR must always block")
    if not set(PROTECTED_PATHS).issubset(config["scope"]["protected_paths"]):
        raise ValidationError("$.scope.protected_paths: baseline protected paths cannot be removed")
    scope = config["jira"]["scope"]
    selectors = [scope["included_epics"], scope["labels_any"], scope["components_any"]]
    if not scope["allow_entire_project"] and not any(selectors):
        raise ValidationError("$.jira.scope: explicit limiting Jira scope is required through included_epics, labels_any or components_any")
    for selector in ["included_epics", "labels_any", "components_any"]:
        for index, value in enumerate(scope[selector]):
            if not value.strip():
                raise ValidationError(f"$.jira.scope.{selector}[{index}]: empty/whitespace Jira selector")
    execution = config["execution"]
    if "model_routing" in execution:
        from .model_routing import policy_from_config
        try:
            policy_from_config(config)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError(f"$.execution.model_routing: invalid model routing policy: {exc}") from exc
    if execution["max_parallel_tickets_per_stream"] > execution["max_parallel_tickets"]:
        raise ValidationError("$.execution.max_parallel_tickets_per_stream: per-stream limit exceeds $.execution.max_parallel_tickets")
    if execution["daily_project_cost_microusd"] < execution["max_cost_microusd_per_ticket"]:
        raise ValidationError("$.execution.max_cost_microusd_per_ticket: ticket cost cap exceeds $.execution.daily_project_cost_microusd")
    if execution["host_broker"]["enabled"] and not execution["host_broker"]["broker_id"].strip():
        raise ValidationError("$.execution.host_broker.broker_id: enabled broker needs an identity")
    if "{ticket}" not in config["github"]["branch_pattern"]:
        raise ValidationError("$.github.branch_pattern: branch pattern must include {ticket}")
    if config["merge_gate"]["high_risk_owner_quorum"] > len(config["merge_gate"]["trusted_owner_ids"]):
        raise ValidationError("$.merge_gate.high_risk_owner_quorum: owner quorum exceeds $.merge_gate.trusted_owner_ids")
    for path, value in (("$.github.host", config["github"]["host"]), ("$.jira.site", config["jira"]["site"])):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValidationError(f"{path}: service identity must be an HTTPS origin without credentials/query/fragment")
    checks = config["validation"]["required_ci_checks"]
    unique(checks, "name", "$.validation.required_ci_checks[].name")
    for index, check in enumerate(checks):
        try:
            safe_path(check["workflow_path"])
        except ValidationError as exc:
            raise ValidationError(f"$.validation.required_ci_checks[{index}].workflow_path: {exc}") from exc
    for role, policy in execution["roles"].items():
        if policy["model"] not in policy["approved_model_ids"]:
            raise ValidationError(f"$.execution.roles.{role}.model: model is absent from approved_model_ids")
    for flag in ["security", "schema_or_migration", "public_api", "data_loss", "concurrency", "production"]:
        if not any(flag in domain["risk_flags"] for domain in config["specialist_reviews"].values()):
            raise ValidationError(f"$.specialist_reviews: risk flag {flag} has no required specialist risk_flags mapping")
    return policy_hash(config, workflow)


def policy_hash(config, workflow):
    return fingerprint("policy", {"config": config, "workflow": workflow})


def inside_scope(snapshot, config):
    if snapshot["project_key"] != config["jira"]["project_key"]:
        return False
    scope = config["jira"]["scope"]
    if scope["allow_entire_project"]:
        return True
    tests = []
    for selector, field in [("included_epics", "epic_ids"), ("labels_any", "labels"), ("components_any", "component_ids")]:
        if scope[selector]:
            tests.append(bool(set(scope[selector]) & set(snapshot[field])))
    return bool(tests) and (all(tests) if scope["selector_mode"] == "all" else any(tests))


def specialist_domains(config, contract, paths, text, declared):
    result = set(contract["specialist_domains"]) | set(declared)
    for name, rule in config["specialist_reviews"].items():
        flags = any(contract["risk_flags"].get(flag, False) for flag in rule["risk_flags"])
        matches = any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in rule["paths"])
        keywords = any(word.casefold() in text.casefold() for word in rule["keywords"])
        if flags or matches or keywords:
            result.add(name)
    if not result.issubset(config["specialist_reviews"]):
        raise ValidationError("Required specialist domain has no configured reviewer")
    return result


def dependencies_satisfied(dependencies):
    unique(dependencies, "issue_id", "dependency issue")
    return all(item["satisfied"] and item["evidence"] for item in dependencies)


def topological_order(graph):
    visiting, visited, output = set(), set(), []
    def visit(node):
        if node in visiting:
            raise ValidationError(f"Dependency cycle involving {node}")
        if node in visited:
            return
        if node not in graph:
            raise ValidationError(f"Missing dependency node: {node}")
        visiting.add(node)
        for dependency in sorted(graph[node]):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)
        output.append(node)
    for node in sorted(graph):
        visit(node)
    return output
