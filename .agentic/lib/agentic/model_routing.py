"""Deterministic model proposals and trusted-host reservation accounting.

This module never dispatches models. Callers must authenticate facts, protect the
policy/ledger and enforce the reserved token/cost ceilings at their host boundary.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
import uuid

from agentic import ValidationError
from agentic.canonical import validate_value

ROLES = ("controller", "worker", "critic", "specialist")
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
RISK_FLAGS = ("security", "permissions", "schema_or_migration", "data_loss",
              "concurrency", "production", "public_api", "architecture")
REASONING_FAILURES = ("reasoning", "implementation", "validation")
NON_REASONING_FAILURES = ("credentials", "infrastructure", "rate_limit", "cancelled", "unknown")


def _pair(model, effort):
    return {"model": model, "reasoning_effort": effort}


def default_policy():
    """Return an independent editable Balanced policy, without invented prices."""
    return {
        "schema_version": 1,
        "profile": "balanced",
        "model_order": list(MODELS),
        "models": {m: {"reasoning_efforts": list(EFFORTS if m != MODELS[0] else EFFORTS[:-1])}
                   for m in MODELS},
        "role_defaults": {"controller": _pair(MODELS[2], "medium"),
                          "worker": _pair(MODELS[1], "medium"),
                          "critic": _pair(MODELS[2], "high"),
                          "specialist": _pair(MODELS[3], "high")},
        "role_allowed_models": {role: list(MODELS) for role in ROLES},
        "simple_worker": _pair(MODELS[0], "low"),
        "review_floor": _pair(MODELS[2], "high"),
        "risk_route": _pair(MODELS[3], "high"),
        "high_risk_flags": list(RISK_FLAGS),
        "agent_overrides": {},
        "ticket_overrides": {},
        "escalation": {"enabled": True, "max_escalations_per_ticket": 2,
                       "max_reasoning_failures_per_phase": 3,
                       "effort_ceiling": "high"},
        "budgets": {"max_runs_per_ticket": 8, "max_runs_per_project_day": 40,
                    "max_tokens_per_ticket": 1000000, "max_tokens_per_project_day": 5000000,
                    "max_cost_microusd_per_ticket": None,
                    "max_cost_microusd_per_project_day": None},
        "adaptive": {"mode": "shadow", "min_reviewed_samples": 20,
                     "min_success_rate_percent": 95},
        "reconciliation": {"enabled": False, "authorized_operator_ids": [],
                           "max_reconciled_incident_retries_per_ticket": 2},
    }


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _integer(value, label, minimum=0, nullable=False):
    if value is None and nullable:
        return
    _require(type(value) is int and minimum <= value <= 2**53 - 1,
             label + " must be an interoperable integer >= " + str(minimum))


def _object(value, label):
    _require(isinstance(value, dict), label + " must be an object")
    validate_value(value)


def _name(value, label):
    _require(isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value,
             label + " must be a nonempty bounded identifier")


def _approval_reference(value, *, historical=False):
    """Canonical opaque host identity; this does not resolve arbitrary aliases."""
    _name(value, "approval/evidence reference")
    if not historical:
        _require(not any(unicodedata.category(char) in {"Cc", "Cf"} for char in value),
                 "approval/evidence reference must not contain control characters")
    return unicodedata.normalize("NFC", value)


def _valid_pair(policy, pair, label, allow_pin=False):
    _require(isinstance(pair, dict) and {"model", "reasoning_effort"} <= set(pair)
             and set(pair) <= {"model", "reasoning_effort"} | ({"pinned"} if allow_pin else set()),
             label + " requires model and reasoning_effort (overrides may also specify pinned)")
    if "pinned" in pair:
        _require(type(pair["pinned"]) is bool, label + ".pinned must be boolean")
    _require(isinstance(pair["model"], str) and pair["model"] in policy["models"], label + " uses an unknown model")
    _require(isinstance(pair["reasoning_effort"], str)
             and pair["reasoning_effort"] in policy["models"][pair["model"]]["reasoning_efforts"],
             label + " uses an unsupported policy model/effort combination")


def validate_policy(policy):
    """Validate complete policies; malformed or unsupported settings fail closed."""
    _object(policy, "model_routing")
    required = set(default_policy()) - {"reconciliation"}
    _require(required <= set(policy) <= required | {"reconciliation"},
             "model_routing requires " + ", ".join(sorted(required)) + "; only reconciliation is optional")
    _require(type(policy["schema_version"]) is int and policy["schema_version"] == 1, "unsupported routing schema_version")
    _require(policy["profile"] == "balanced", "supported routing profile is balanced")
    models, order = policy["models"], policy["model_order"]
    _require(isinstance(models, dict) and bool(models), "models must be a nonempty object")
    _require(isinstance(order, list) and all(isinstance(m, str) for m in order)
             and len(order) == len(set(order)) and set(order) == set(models),
             "model_order must include every configured model exactly once")
    for model, info in models.items():
        _name(model, "model")
        _require(isinstance(info, dict) and set(info) == {"reasoning_efforts"}, "model metadata requires reasoning_efforts")
        efforts = info["reasoning_efforts"]
        _require(isinstance(efforts, list) and bool(efforts) and all(e in EFFORTS for e in efforts)
                 and len(efforts) == len(set(efforts)), "model reasoning_efforts must be unique supported levels")
    for field in ("role_defaults", "role_allowed_models"):
        _require(isinstance(policy[field], dict) and set(policy[field]) == set(ROLES), field + " must define all roles")
    for role in ROLES:
        allowed = policy["role_allowed_models"][role]
        _require(isinstance(allowed, list) and all(isinstance(m, str) and m in models for m in allowed)
                 and len(allowed) == len(set(allowed)), "invalid role allowlist: " + role)
        _valid_pair(policy, policy["role_defaults"][role], role)
    for field in ("simple_worker", "review_floor", "risk_route"):
        _valid_pair(policy, policy[field], field)
    _require(_at_least(policy, policy["risk_route"], policy["review_floor"]),
             "risk_route must meet both model and effort review_floor")
    _require(isinstance(policy["high_risk_flags"], list)
             and all(isinstance(x, str) and x for x in policy["high_risk_flags"]), "invalid high_risk_flags")
    for field in ("agent_overrides", "ticket_overrides"):
        _require(isinstance(policy[field], dict), field + " must be an object")
        for identifier, overrides in policy[field].items():
            _name(identifier, field)
            _require(isinstance(overrides, dict) and bool(overrides) and set(overrides) <= set(ROLES), field + " entries map roles to choices")
            for role, pair in overrides.items():
                _valid_pair(policy, pair, field + "." + identifier + "." + role, allow_pin=True)
    escalation = policy["escalation"]
    _require(isinstance(escalation, dict) and set(escalation) == {"enabled", "max_escalations_per_ticket", "max_reasoning_failures_per_phase", "effort_ceiling"}, "invalid escalation settings")
    _require(type(escalation["enabled"]) is bool, "escalation.enabled must be boolean")
    _integer(escalation["max_escalations_per_ticket"], "max_escalations_per_ticket")
    _integer(escalation["max_reasoning_failures_per_phase"], "max_reasoning_failures_per_phase", 1)
    _require(escalation["effort_ceiling"] in EFFORTS, "invalid effort_ceiling")
    if escalation["enabled"]:
        for role, allowed in policy["role_allowed_models"].items():
            for model in allowed:
                _require(escalation["effort_ceiling"] in models[model]["reasoning_efforts"],
                         "escalation.effort_ceiling is unsupported by approved " + role + " model " + model)
    budgets = policy["budgets"]
    _require(isinstance(budgets, dict) and set(budgets) == set(default_policy()["budgets"]), "invalid budget fields")
    for field, value in budgets.items():
        _integer(value, field, 1, nullable="cost" in field)
    adaptive = policy["adaptive"]
    _require(isinstance(adaptive, dict) and set(adaptive) == {"mode", "min_reviewed_samples", "min_success_rate_percent"}, "invalid adaptive settings")
    _require(adaptive["mode"] in ("off", "shadow"), "adaptive mode must be off or shadow; autonomous learned routing is not enabled")
    _integer(adaptive["min_reviewed_samples"], "min_reviewed_samples", 1)
    _integer(adaptive["min_success_rate_percent"], "min_success_rate_percent", 1)
    _require(adaptive["min_success_rate_percent"] <= 100, "invalid min_success_rate_percent")
    reconciliation = policy.get("reconciliation", default_policy()["reconciliation"])
    reconciliation_required = {"enabled", "authorized_operator_ids"}
    reconciliation_optional = {"max_reconciled_incident_retries_per_ticket"}
    _require(isinstance(reconciliation, dict) and reconciliation_required <= set(reconciliation)
             <= reconciliation_required | reconciliation_optional,
             "invalid reconciliation settings")
    _require(type(reconciliation["enabled"]) is bool, "reconciliation.enabled must be boolean")
    operators = reconciliation["authorized_operator_ids"]
    _require(isinstance(operators, list), "reconciliation.authorized_operator_ids must be a list")
    for operator in operators:
        _name(operator, "reconciliation operator ID")
    _require(len(operators) == len(set(operators)), "reconciliation operator IDs must be unique")
    _require(not reconciliation["enabled"] or bool(operators), "enabled reconciliation requires an authorized operator")
    _integer(reconciliation.get("max_reconciled_incident_retries_per_ticket", 2),
             "reconciliation.max_reconciled_incident_retries_per_ticket")
    return policy


def policy_from_config(config):
    """Intersect routing limits/allowlists with existing execution constraints."""
    _object(config, "configuration")
    _require(isinstance(config.get("execution"), dict), "execution configuration must be an object")
    execution = config["execution"]
    policy = deepcopy(execution.get("model_routing"))
    validate_policy(policy)
    caps = {"max_agent_runs_per_ticket": "max_runs_per_ticket",
            "max_tokens_per_ticket": "max_tokens_per_ticket",
            "max_cost_microusd_per_ticket": "max_cost_microusd_per_ticket",
            "daily_project_cost_microusd": "max_cost_microusd_per_project_day"}
    for existing, routing in caps.items():
        if existing in execution:
            value = execution[existing]
            monetary = "cost" in existing
            _integer(value, existing, 1, nullable=monetary)
            configured = policy["budgets"][routing]
            if value is not None:
                policy["budgets"][routing] = value if configured is None else min(value, configured)
    roles = execution.get("roles", {})
    _require(isinstance(roles, dict), "execution.roles must be an object")
    for role in ROLES:
        role_config = roles.get(role, {})
        _require(isinstance(role_config, dict), "execution.roles." + role + " must be an object")
        configured = role_config.get("approved_model_ids")
        if "approved_model_ids" in role_config:
            _require(isinstance(configured, list) and all(isinstance(m, str) for m in configured), "invalid existing role allowlist")
            policy["role_allowed_models"][role] = [m for m in policy["role_allowed_models"][role] if m in configured]
    validate_policy(policy)
    return policy


def _raw_fingerprint(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _effective_policy(policy):
    result = deepcopy(policy)
    settings = result.setdefault("reconciliation", {"enabled": False, "authorized_operator_ids": []})
    settings.setdefault("max_reconciled_incident_retries_per_ticket", 2)
    return result


def _fingerprint(policy):
    """Hash effective defaults without modifying the caller or historical rows."""
    return _raw_fingerprint(_effective_policy(policy))


def _equivalent_policy_hashes(policy):
    """Only the three valid legacy encodings of these exact defaults qualify."""
    effective = _effective_policy(policy)
    hashes = {_raw_fingerprint(effective)}
    settings = effective["reconciliation"]
    if settings["max_reconciled_incident_retries_per_ticket"] == 2:
        omitted_cap = deepcopy(effective)
        del omitted_cap["reconciliation"]["max_reconciled_incident_retries_per_ticket"]
        hashes.add(_raw_fingerprint(omitted_cap))
        if settings == default_policy()["reconciliation"]:
            omitted_block = deepcopy(effective)
            del omitted_block["reconciliation"]
            hashes.add(_raw_fingerprint(omitted_block))
    return sorted(hashes)


def _at_least(policy, pair, floor):
    return (policy["model_order"].index(pair["model"]) >= policy["model_order"].index(floor["model"])
            and EFFORTS.index(pair["reasoning_effort"]) >= EFFORTS.index(floor["reasoning_effort"]))


def _request(request):
    _object(request, "request")
    for field in ("ticket_id", "agent_id", "phase", "task_class"):
        _name(request.get(field), field)
    if request.get("context_id") is not None:
        _name(request["context_id"], "context_id")
    _require(request.get("role") in ROLES, "unknown routing role")
    for field in ("complexity", "risk", "uncertainty"):
        _require(request.get(field) in ("low", "medium", "high"), field + " must be low, medium, or high")
    _require(request.get("verification") in ("strong", "limited", "none"), "verification must be strong, limited, or none")
    _require(isinstance(request.get("risk_flags"), list) and all(isinstance(x, str) for x in request["risk_flags"]), "risk_flags must be a string list")
    if "stream" in request:
        _require(request["stream"] in tuple("ABCDEF"), "stream must be A through F")
    if request.get("epic_id") is not None:
        _name(request["epic_id"], "epic_id")
        _require("epic_risk_flags" in request, "Epic-scoped work requires host-observed inherited epic_risk_flags, including an explicit empty list")
    if "epic_risk_flags" in request:
        _require(isinstance(request["epic_risk_flags"], list) and all(isinstance(x, str) for x in request["epic_risk_flags"]),
                 "epic_risk_flags must be a host-observed string list")
    if request["role"] in ("critic", "specialist"):
        _name(request.get("worker_context_id"), "worker_context_id")
        _require(request.get("context_id") != request["worker_context_id"], "review requires an independent context")


def _operating_binding(operating, governance, policy):
    if operating is None:
        return None, {"operating_hash": None, "operating_state": "UNOBSERVED_LEGACY"}
    from .operating import validate_snapshot
    _require(governance is not None, "An operating snapshot requires its accepted governance configuration")
    validate_snapshot(operating, governance)
    _require(policy_from_config(governance) == policy, "Operating snapshot governance differs from the effective routing policy")
    return operating.config, {"operating_hash": operating.operating_hash,
                              "operating_state": "VALIDATED_SNAPSHOT",
                              "operating_provenance": deepcopy(operating.provenance)}


def select_route(policy, request, capabilities, *, operating=None, governance=None):
    """Read-only proposal with explicit legacy attribution or a validated snapshot."""
    config, binding = _operating_binding(operating, governance, policy)
    result = _select_route(policy, request, capabilities, config)
    draining = bool(operating is not None and request.get("stream") in tuple("ABCDEF")
                    and tuple("ABCDEF").index(request["stream"]) >= operating.count)
    return {**result, **binding, "draining": draining,
            "admission_requirement": "Only a previously admitted ticket in this surplus stream may finish; no new ticket admission" if draining else None}


def _select_route(policy, request, capabilities, operating_config):
    """Read-only proposal. History fields are advisory; reserve derives its own."""
    validate_policy(policy)
    _request(request)
    _object(capabilities, "capabilities")
    _require(isinstance(capabilities.get("models"), dict), "observed host capabilities.models must be an object")
    for model, efforts in capabilities["models"].items():
        _name(model, "capability model")
        _require(isinstance(efforts, list) and all(isinstance(e, str) and e in EFFORTS for e in efforts),
                 "host model capabilities must be effort lists")
    role = request["role"]
    risk_flags = set(request["risk_flags"]) | set(request.get("epic_risk_flags", []))
    high_risk = request["risk"] == "high" or bool(risk_flags & set(policy["high_risk_flags"]))
    demanding = high_risk or request["complexity"] == "high" or request["uncertainty"] == "high"
    simple = all(request[x] == "low" for x in ("risk", "complexity", "uncertainty")) and request["verification"] == "strong" and not risk_flags
    selected = deepcopy(policy["role_defaults"][role])
    reasons = ["balanced role default"]
    role_route = operating_config.get(role) if operating_config and role in ("controller", "specialist") else None
    if role_route:
        selected = {k: role_route[k] for k in ("model", "reasoning_effort")}
        reasons = ["operating role default"]
    simple_route = operating_config["simple_worker"] if operating_config else policy["simple_worker"]
    simple_enabled = simple_route.get("enabled", True)
    if role == "worker" and simple and simple_enabled:
        selected = {k: simple_route[k] for k in ("model", "reasoning_effort")}
        reasons = ["simple, low-risk, low-uncertainty work with strong verification"]
    if demanding:
        selected = deepcopy(policy["risk_route"])
        reasons.append("high risk, complexity, or uncertainty")
    floor = policy["risk_route"] if demanding else policy["review_floor"] if role in ("critic", "specialist") else None
    pinned = bool(role_route and role_route.get("pinned", False))
    if role == "worker" and simple and simple_enabled:
        pinned = simple_route.get("pinned", False)
    if operating_config and role in ("worker", "critic"):
        _require("stream" in request, "Operating worker/reviewer routing requires an observed stream A–F")
        stream_route = operating_config["streams"].get(request["stream"], {}).get("reviewer" if role == "critic" else "worker")
        _require(stream_route is not None, "Requested stream has no retained operating route; reconcile stream ownership")
        # An ordinary unpinned stream route is the normal-work default. Keeping
        # the simple option effective avoids overriding Luna with default Terra.
        ordinary_default = all(stream_route[k] == policy["role_defaults"][role][k]
                               for k in ("model", "reasoning_effort"))
        if not (role == "worker" and simple and simple_enabled and ordinary_default and not stream_route.get("pinned", False)):
            selected = {k: stream_route[k] for k in ("model", "reasoning_effort")}
            pinned = stream_route.get("pinned", False)
            reasons.append("operating stream route" + (" pinned by user" if pinned else " for ordinary work"))
        else:
            reasons.append("enabled simple-worker selection takes precedence over an unpinned ordinary stream default")
    # An explicit Epic identity selects only that Epic's recorded route. The
    # global route stays intact for unrelated work and future Epics.
    epic_routes = operating_config.get("epic_overrides", {}).get(request.get("epic_id"), {}) if operating_config else {}
    epic_route = (epic_routes.get("streams", {}).get(request.get("stream"), {}).get("reviewer" if role == "critic" else "worker")
                  if role in ("worker", "critic") else epic_routes.get(role))
    if epic_route is not None:
        selected = {k: epic_route[k] for k in ("model", "reasoning_effort")}
        pinned = epic_route.get("pinned", False)
        reasons.append("operating Epic-scoped route" + (" pinned by user" if pinned else ""))
    for field, key in (("agent_overrides", request["agent_id"]), ("ticket_overrides", request["ticket_id"])):
        override = policy[field].get(key, {}).get(role)
        if override is not None:
            selected = {k: override[k] for k in ("model", "reasoning_effort")}
            pinned = override.get("pinned", False)
            reasons.append(field + " override")
    if floor:
        protected = _pair(max((selected["model"], floor["model"]), key=policy["model_order"].index),
                          max((selected["reasoning_effort"], floor["reasoning_effort"]), key=EFFORTS.index))
        if protected != selected:
            reasons.append("mandatory risk/review floor applied after overrides and pins; stronger dimensions retained")
        selected = protected
    retry_route = request.get("resolved_failure_route")
    if retry_route is not None:
        _valid_pair(policy, retry_route, "resolved_failure_route")
        if (floor and not _at_least(policy, retry_route, floor)) or (pinned and retry_route != selected):
            return {"status": "blocked", "reason": "resolved failure route conflicts with current risk floor or pin", "policy_sha256": _fingerprint(policy)}
        selected = deepcopy(retry_route)
        reasons.append("same-model retry after verified non-reasoning failure resolution")
    failures = request.get("reasoning_failures", 0)
    _integer(failures, "reasoning_failures")
    escalated = False
    previous = request.get("previous_route")
    failure_kind = request.get("last_failure_kind")
    _require(failure_kind is None or isinstance(failure_kind, str)
             and failure_kind in REASONING_FAILURES + NON_REASONING_FAILURES, "invalid last_failure_kind")
    if previous is not None:
        _valid_pair(policy, previous, "previous_route")
    if failure_kind in NON_REASONING_FAILURES:
        return {"status": "blocked", "reason": "resolve non-reasoning failure before retry: " + failure_kind, "policy_sha256": _fingerprint(policy)}
    if failures >= policy["escalation"]["max_reasoning_failures_per_phase"]:
        return {"status": "blocked", "reason": "reasoning failure limit reached", "policy_sha256": _fingerprint(policy)}
    if failure_kind in REASONING_FAILURES:
        _require(previous is not None, "reasoning escalation requires previous_route")
        mandatory_raise = floor is not None and not _at_least(policy, previous, floor)
        if pinned and not mandatory_raise:
            return {"status": "blocked", "reason": "explicit model/effort pin prevents automatic escalation", "policy_sha256": _fingerprint(policy)}
        if not policy["escalation"]["enabled"] and not mandatory_raise:
            return {"status": "blocked", "reason": "automatic escalation disabled", "policy_sha256": _fingerprint(policy)}
        # Preserve BOTH dimensions: a stronger model must not reduce effort,
        # and a higher effort must not undo a newly required stronger model.
        selected = _pair(max((selected["model"], previous["model"]), key=policy["model_order"].index),
                         max((selected["reasoning_effort"], previous["reasoning_effort"]), key=EFFORTS.index))
        already_stronger = selected != previous and _at_least(policy, selected, previous)
        rank = policy["model_order"].index(selected["model"])
        ceiling = policy["escalation"]["effort_ceiling"]
        if EFFORTS.index(selected["reasoning_effort"]) > EFFORTS.index(ceiling):
            return {"status": "blocked", "reason": "monotone escalation would exceed configured effort ceiling", "policy_sha256": _fingerprint(policy)}
        stronger_allowed = [m for m in policy["model_order"][rank + 1:] if m in policy["role_allowed_models"][role]]
        if already_stronger:
            # A revised risk assessment may already require a stronger model.
            # Do not unnecessarily step beyond that newly required route.
            pass
        elif stronger_allowed:
            selected = _pair(stronger_allowed[0], ceiling)
        elif EFFORTS.index(selected["reasoning_effort"]) < EFFORTS.index(ceiling):
            selected["reasoning_effort"] = ceiling
        else:
            return {"status": "blocked", "reason": "configured escalation ceiling reached", "policy_sha256": _fingerprint(policy)}
        escalated = True
        reasons.append("mandatory risk/review protection after changed risk evidence" if mandatory_raise
                       else "automatic escalation after " + failure_kind + " failure")
        _require(_at_least(policy, selected, previous), "escalation must not reduce model or reasoning effort")
    _valid_pair(policy, selected, "selected route")
    if floor and not _at_least(policy, selected, floor):
        return {"status": "blocked", "reason": "escalation violates risk/review floor", "policy_sha256": _fingerprint(policy)}
    if selected["model"] not in policy["role_allowed_models"][role]:
        return {"status": "unavailable", "reason": "selected model is outside effective role allowlist", "requested": selected, "policy_sha256": _fingerprint(policy)}
    host_efforts = capabilities["models"].get(selected["model"], [])
    _require(isinstance(host_efforts, list) and all(isinstance(e, str) for e in host_efforts), "host model capabilities must be effort lists")
    if selected["reasoning_effort"] not in host_efforts:
        return {"status": "unavailable", "reason": "host does not support requested model/effort; no fallback", "requested": selected, "policy_sha256": _fingerprint(policy)}
    return {"status": "ready", **selected, "role": role, "reasons": reasons,
            "escalated": escalated, "pinned": pinned, "high_risk": demanding, "simple": simple,
            "policy_sha256": _fingerprint(policy), "dispatch_performed": False}


class RoutingLedger:
    """One durable SQLite ledger per trusted controller, outside worker access.

    BEGIN IMMEDIATE serializes admission across connections. Outstanding runs stay
    charged indefinitely until observed completion; timeout never refunds a run.
    """
    def __init__(self, path):
        self.path = Path(path).absolute()
        _require(str(path) != ":memory:", "routing ledger must be durable")
        _require(self.path.parent.is_dir(), "create a trusted ledger directory before use")
        with self._transaction() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS model_runs (
              run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
              role TEXT NOT NULL, agent_id TEXT NOT NULL, phase TEXT NOT NULL,
              created_at TEXT NOT NULL, day TEXT NOT NULL, policy_hash TEXT NOT NULL,
              status TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
              reserved_cost INTEGER, actual_tokens INTEGER, actual_cost INTEGER,
              escalated INTEGER NOT NULL, request TEXT NOT NULL,
              route TEXT NOT NULL, outcome TEXT)""")
            connection.execute("CREATE INDEX IF NOT EXISTS model_runs_project ON model_runs(project_id,ticket_id,day)")
            connection.execute("CREATE TABLE IF NOT EXISTS model_failure_resolutions (run_id TEXT PRIMARY KEY, observation TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS model_defect_observations (observation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, observation TEXT NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_reconciliations (
              reconciliation_id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL,
              project_id TEXT NOT NULL, request_hash TEXT NOT NULL,
              observation TEXT NOT NULL, result TEXT NOT NULL)""")
            connection.execute("CREATE TRIGGER IF NOT EXISTS reconciliation_no_update BEFORE UPDATE ON model_reconciliations BEGIN SELECT RAISE(ABORT, 'reconciliation audit is append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS reconciliation_no_delete BEFORE DELETE ON model_reconciliations BEGIN SELECT RAISE(ABORT, 'reconciliation audit is append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS reconciliation_no_replace BEFORE INSERT ON model_reconciliations WHEN EXISTS(SELECT 1 FROM model_reconciliations WHERE reconciliation_id=NEW.reconciliation_id OR run_id=NEW.run_id OR rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT, 'reconciliation audit is append-only'); END")
            connection.execute("CREATE TABLE IF NOT EXISTS model_retry_ceilings (project_id TEXT PRIMARY KEY, retry_limit INTEGER NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_retry_limit_authorizations (
              operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              request_hash TEXT NOT NULL, observation TEXT NOT NULL, result TEXT NOT NULL)""")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_authorization_no_update BEFORE UPDATE ON model_retry_limit_authorizations BEGIN SELECT RAISE(ABORT, 'retry-limit authorization audit is append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_authorization_no_delete BEFORE DELETE ON model_retry_limit_authorizations BEGIN SELECT RAISE(ABORT, 'retry-limit authorization audit is append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_authorization_no_replace BEFORE INSERT ON model_retry_limit_authorizations WHEN EXISTS(SELECT 1 FROM model_retry_limit_authorizations WHERE operation_id=NEW.operation_id OR rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT, 'retry-limit authorization audit is append-only'); END")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_retry_approval_consumptions (
              reference_id TEXT PRIMARY KEY, first_operation_id TEXT NOT NULL,
              first_project_id TEXT NOT NULL, provenance TEXT NOT NULL)""")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_consumption_no_update BEFORE UPDATE ON model_retry_approval_consumptions BEGIN SELECT RAISE(ABORT, 'approval consumption is append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_consumption_no_delete BEFORE DELETE ON model_retry_approval_consumptions BEGIN SELECT RAISE(ABORT, 'approval consumption is append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_consumption_no_replace BEFORE INSERT ON model_retry_approval_consumptions WHEN EXISTS(SELECT 1 FROM model_retry_approval_consumptions WHERE reference_id=NEW.reference_id OR rowid=NEW.rowid) BEGIN SELECT RAISE(ABORT, 'approval consumption is append-only'); END")
            # Index existing grants without rewriting audit rows or rejecting old
            # duplicate references. First historical use consumes the identity
            # for every project and both reference fields in this database.
            for row in connection.execute("SELECT * FROM model_retry_limit_authorizations ORDER BY rowid").fetchall():
                audit = json.loads(row["observation"])
                references = {_approval_reference(audit[field], historical=True)
                              for field in ("authorization_ref", "evidence_ref")}
                for reference in sorted(references):
                    if connection.execute("SELECT 1 FROM model_retry_approval_consumptions WHERE reference_id=?", (reference,)).fetchone() is None:
                        connection.execute("INSERT INTO model_retry_approval_consumptions VALUES (?,?,?,?)",
                                           (reference, row["operation_id"], row["project_id"],
                                            "historical grant; original records and duplicate references preserved"))
            connection.execute("""CREATE TABLE IF NOT EXISTS model_retry_ceiling_events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision > 0), previous_limit INTEGER,
              retry_limit INTEGER NOT NULL CHECK(retry_limit >= 0),
              kind TEXT NOT NULL, observation TEXT NOT NULL, UNIQUE(project_id,revision))""")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_ceiling_event_no_update BEFORE UPDATE ON model_retry_ceiling_events BEGIN SELECT RAISE(ABORT, 'retry ceiling events are append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_ceiling_event_no_delete BEFORE DELETE ON model_retry_ceiling_events BEGIN SELECT RAISE(ABORT, 'retry ceiling events are append-only'); END")
            connection.execute("CREATE TRIGGER IF NOT EXISTS retry_ceiling_event_no_replace BEFORE INSERT ON model_retry_ceiling_events WHEN EXISTS(SELECT 1 FROM model_retry_ceiling_events WHERE event_id=NEW.event_id OR (project_id=NEW.project_id AND revision=NEW.revision)) BEGIN SELECT RAISE(ABORT, 'retry ceiling events are append-only'); END")
            # Preserve the last legacy ceiling, not a guessed historical policy.
            for row in connection.execute("SELECT * FROM model_retry_ceilings").fetchall():
                if self._current_retry_ceiling(connection, row["project_id"]) is None:
                    self._append_retry_ceiling(connection, row["project_id"], None,
                                               row["retry_limit"], "legacy_migration", {
                        "policy_sha256": None, "provenance": "v1.8.1 current ceiling; historical reductions were not audited"})
            for operation in ("INSERT", "UPDATE", "DELETE"):
                connection.execute(f"CREATE TRIGGER IF NOT EXISTS retry_legacy_no_{operation.lower()} BEFORE {operation} ON model_retry_ceilings BEGIN SELECT RAISE(ABORT, 'legacy ceiling table is sealed; use append-only events'); END")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(model_runs)")}
            if "closed_day" not in columns:
                connection.execute("ALTER TABLE model_runs ADD COLUMN closed_day TEXT")
                for row in connection.execute("SELECT run_id,outcome FROM model_runs WHERE outcome IS NOT NULL").fetchall():
                    closed_at = json.loads(row["outcome"]).get("settled_at")
                    _require(isinstance(closed_at, str) and len(closed_at) >= 10,
                             "legacy ledger outcome lacks settlement timestamp; inspect protected ledger")
                    connection.execute("UPDATE model_runs SET closed_day=? WHERE run_id=?", (closed_at[:10], row["run_id"]))
            if "operating_hash" not in columns:
                # Attribution is additive. Never invent a current operating
                # configuration for pre-operating reservations or settlements.
                connection.execute("ALTER TABLE model_runs ADD COLUMN operating_hash TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS model_runs_status ON model_runs(project_id,status)")
            connection.execute("CREATE INDEX IF NOT EXISTS model_runs_day ON model_runs(project_id,day)")
            connection.execute("CREATE INDEX IF NOT EXISTS model_runs_closed ON model_runs(project_id,closed_day)")

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve(self, project_id, policy, request, capabilities, *, operating=None, governance=None):
        """Persist a validated policy observation, then atomically admit a run.

        The savepoint rolls back failed admission work while the enclosing write
        lock retains its seed/reduction. Failed attempts are not model runs.
        """
        _name(project_id, "project_id")
        validate_policy(policy)
        _, operating_binding = _operating_binding(operating, governance, policy)
        _request(request)
        _integer(request.get("reservation_tokens"), "reservation_tokens", 1)
        _integer(request.get("reservation_cost_microusd"), "reservation_cost_microusd", nullable=True)
        validate_value(capabilities)
        run_id = str(uuid.uuid4())
        request_hash = _raw_fingerprint({"project_id": project_id, "policy_sha256": _fingerprint(policy),
                                         "request": request, "capabilities": capabilities,
                                         "operating_hash": operating_binding["operating_hash"]})
        attempt = {"attempted_run_id": run_id, "request_sha256": request_hash,
                   "admitted": False, "dispatch_performed": False, **operating_binding}
        now = datetime.now(timezone.utc).isoformat()
        day = now[:10]
        error = None
        with self._transaction() as connection:
            retry_limit = policy.get("reconciliation", {}).get("max_reconciled_incident_retries_per_ticket", 2)
            ceiling_state = self._retry_ceiling(connection, project_id, retry_limit, policy)
            ceiling = ceiling_state["retry_limit"]
            if retry_limit > ceiling:
                return {"status": "blocked", "code": "RETRY_LIMIT_INCREASE_REQUIRES_AUTHORIZATION",
                        "reason": "configured retry cap exceeds the protected ledger ceiling; a trusted host must record explicit human direction",
                        "project_id": project_id, "configured_retry_limit": retry_limit,
                        "authorized_retry_ceiling": ceiling, "policy_sha256": _fingerprint(policy),
                        "retry_ceiling_revision": ceiling_state["revision"], **attempt}
            if retry_limit < ceiling:
                self._append_retry_ceiling(connection, project_id, ceiling_state, retry_limit,
                                           "configuration_reduction", {
                    "policy_sha256": _fingerprint(policy), "configured_retry_limit": retry_limit,
                    "ticket_id": request["ticket_id"], "agent_id": request["agent_id"],
                    "role": request["role"], "phase": request["phase"],
                    "attempted_run_id": run_id, "request_sha256": request_hash,
                    "run_binding": "admission attempt only; a model_runs row with this ID establishes reservation",
                    "provenance": "validated configuration observed before admission; caller identity is a host assertion"})
            connection.execute("SAVEPOINT model_admission")
            try:
                result = self._admit(connection, project_id, policy, request, capabilities, retry_limit, now, day, run_id,
                                     operating=operating, governance=governance)
            except Exception as exc:
                connection.execute("ROLLBACK TO model_admission")
                error = exc
            finally:
                connection.execute("RELEASE model_admission")
        # Re-raise only after a successful commit; transaction/commit errors must
        # never be mistaken for a durable configuration observation.
        if error is not None:
            error.admission_attempt = {**attempt, "configuration_observation_committed": True}
            raise error
        return {**result, **attempt, "admitted": result["status"] == "reserved"}

    def _admit(self, connection, project_id, policy, request, capabilities, retry_limit, now, day, run_id, *, operating=None, governance=None):
        costs_required = any(value is not None for key, value in policy["budgets"].items() if "cost" in key)
        _require(not costs_required or request.get("reservation_cost_microusd") is not None,
                 "cost ceiling is configured but a verified hard cost reservation is unavailable")
        _require(connection.execute("SELECT 1 FROM model_runs WHERE project_id=? AND status='quarantined' LIMIT 1", (project_id,)).fetchone() is None,
                 "project has a quarantined host mismatch or budget overrun; reconcile with authorized operator evidence")
        ticket_rows = list(connection.execute("SELECT * FROM model_runs WHERE project_id=? AND ticket_id=? ORDER BY rowid", (project_id, request["ticket_id"])))
        if operating is not None and request.get("stream") in tuple("ABCDEF") and tuple("ABCDEF").index(request["stream"]) >= operating.count:
            retained = [row for row in ticket_rows if row["operating_hash"] is not None
                        and json.loads(row["request"]).get("stream") == request["stream"]]
            _require(retained, "Operating stream is draining: no previously admitted ticket exists in this stream; no new ticket admission")
            workers = [row for row in retained if row["role"] == "worker"]
            _require(request["role"] != "worker" or workers and workers[-1]["agent_id"] == request["agent_id"],
                     "Draining work must retain its previously admitted worker identity; reconcile ownership before replacement")
        # This allowance gates admission, never safe incident closure.
        # Keep the count ticket-wide and independent of policy hash, role or
        # phase so renaming a retry cannot reset a repeated-hang budget.
        reconciled_incidents = sum(row["status"] == "reconciled" for row in ticket_rows)
        if reconciled_incidents > retry_limit:
            return {"status": "blocked", "code": "RECONCILED_INCIDENT_RETRY_LIMIT",
                    "reason": "ticket reconciled-incident retry allowance exhausted; closure is permitted but further runs require explicit human direction",
                    "project_id": project_id, "ticket_id": request["ticket_id"],
                    "reconciled_incidents": reconciled_incidents, "retry_limit": retry_limit,
                    "policy_sha256": _fingerprint(policy), "dispatch_performed": False}
        phase_rows = [row for row in ticket_rows if row["role"] == request["role"] and row["phase"] == request["phase"]]
        _require(not any(row["status"] == "reserved" for row in phase_rows), "a run is still outstanding for this ticket/role/phase")
        enriched = deepcopy(request)
        for key in ("previous_route", "last_failure_kind", "reasoning_failures", "resolved_failure_route"):
            enriched.pop(key, None)
        enriched["reasoning_failures"] = sum(json.loads(row["outcome"] or "{}").get("failure_kind") in REASONING_FAILURES for row in phase_rows)
        if phase_rows:
            previous = phase_rows[-1]
            outcome = json.loads(previous["outcome"] or "{}")
            if not outcome.get("success", False):
                enriched["previous_route"] = {k: json.loads(previous["route"])[k] for k in ("model", "reasoning_effort")}
                enriched["last_failure_kind"] = outcome.get("failure_kind", "unknown")
                resolution = connection.execute("SELECT run_id FROM model_failure_resolutions WHERE run_id=?", (previous["run_id"],)).fetchone()
                if resolution and enriched["last_failure_kind"] in NON_REASONING_FAILURES:
                    enriched["resolved_failure_route"] = enriched.pop("previous_route")
                    enriched.pop("last_failure_kind")
        route = select_route(policy, enriched, capabilities, operating=operating, governance=governance)
        if route["status"] != "ready":
            return route
        if route["escalated"]:
            _require(sum(row["escalated"] for row in ticket_rows) < policy["escalation"]["max_escalations_per_ticket"], "ticket automatic escalation limit reached")
        # Outstanding reservations from previous days count on each admission
        # day as well: crossing midnight must not make running work disappear.
        # UNION avoids double counting and uses the indexed day/status lookups.
        daily_rows = list(connection.execute("""SELECT * FROM model_runs WHERE rowid IN (
          SELECT rowid FROM model_runs WHERE project_id=? AND day=? UNION
          SELECT rowid FROM model_runs WHERE project_id=? AND status='reserved' UNION
          SELECT rowid FROM model_runs WHERE project_id=? AND closed_day=?)""",
          (project_id, day, project_id, project_id, day)))
        for scope, history in (("ticket", ticket_rows), ("project_day", daily_rows)):
            budgets = policy["budgets"]
            _require(len(history) + 1 <= budgets["max_runs_per_" + scope], scope + " run budget exhausted")
            used_tokens = sum(row["actual_tokens"] if row["actual_tokens"] is not None else row["reserved_tokens"] for row in history)
            _require(used_tokens + request["reservation_tokens"] <= budgets["max_tokens_per_" + scope], scope + " token budget exhausted")
            cost_cap = budgets["max_cost_microusd_per_" + scope]
            if cost_cap is not None:
                values = [row["actual_cost"] if row["actual_cost"] is not None else row["reserved_cost"] for row in history]
                _require(all(value is not None for value in values), "historical cost is unknown; capped admission unavailable")
                _require(sum(values) + request["reservation_cost_microusd"] <= cost_cap, scope + " cost budget exhausted")
        connection.execute("""INSERT INTO model_runs
                           (run_id,project_id,ticket_id,role,agent_id,phase,created_at,day,policy_hash,status,
                            reserved_tokens,reserved_cost,actual_tokens,actual_cost,escalated,request,route,outcome,operating_hash)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (run_id, project_id, request["ticket_id"], request["role"], request["agent_id"],
                            request["phase"], now, day, route["policy_sha256"], "reserved",
                            request["reservation_tokens"], request.get("reservation_cost_microusd"),
                            None, None, int(route["escalated"]), json.dumps(enriched), json.dumps(route), None, route["operating_hash"]))
        return {**route, "status": "reserved", "run_id": run_id,
                "reservation_tokens": request["reservation_tokens"],
                "reservation_cost_microusd": request.get("reservation_cost_microusd"),
                "requires_host_limit_enforcement": True}

    @staticmethod
    def _current_retry_ceiling(connection, project_id):
        return connection.execute("SELECT * FROM model_retry_ceiling_events WHERE project_id=? ORDER BY revision DESC LIMIT 1", (project_id,)).fetchone()

    @staticmethod
    def _append_retry_ceiling(connection, project_id, previous, retry_limit, kind, observation):
        revision = 1 if previous is None else previous["revision"] + 1
        old_limit = None if previous is None else previous["retry_limit"]
        audit = {**observation, "project_id": project_id, "revision": revision,
                 "previous_limit": old_limit, "new_limit": retry_limit, "kind": kind,
                 "recorded_at": datetime.now(timezone.utc).isoformat()}
        connection.execute("INSERT INTO model_retry_ceiling_events (project_id,revision,previous_limit,retry_limit,kind,observation) VALUES (?,?,?,?,?,?)",
                           (project_id, revision, old_limit, retry_limit, kind, json.dumps(audit)))
        return RoutingLedger._current_retry_ceiling(connection, project_id)

    @staticmethod
    def _retry_ceiling(connection, project_id, configured_limit, policy):
        row = RoutingLedger._current_retry_ceiling(connection, project_id)
        if row is None:
            # Neither an edited policy nor an old hash can bootstrap a raised cap.
            row = RoutingLedger._append_retry_ceiling(connection, project_id, None,
                                                       min(2, configured_limit), "seed", {
                "policy_sha256": _fingerprint(policy), "configured_retry_limit": configured_limit,
                "provenance": "conservative initial admission ceiling"})
        return row

    def authorize_retry_limit(self, project_id, policy, observation):
        """Trusted-host-only CAS operation; assertions do not authenticate humans.

        Do not expose this operation or direct ledger writes to workers. The host
        must authenticate explicit human direction before submitting this record.
        Ordinary reserve/configuration changes cannot invoke it implicitly.
        """
        _name(project_id, "project_id")
        validate_policy(policy)
        _object(observation, "retry-limit authorization")
        legacy_fields = {"operation_id", "expected_limit", "new_limit", "policy_sha256",
                    "operator_id", "authorization_ref", "evidence_ref", "reason"}
        v182_fields = legacy_fields | {"expected_revision", "expires_at"}
        required = v182_fields | {"approved_at"}
        _require(set(observation) in (legacy_fields, v182_fields, required), "retry-limit authorization has missing or unexpected fields")
        for field in set(observation) - {"expected_limit", "new_limit", "expected_revision"}:
            _name(observation[field], field)
        for field in ("expected_limit", "new_limit"):
            _integer(observation[field], field)
        _require(observation["new_limit"] > observation["expected_limit"], "authorization must increase the expected ceiling")
        settings = policy.get("reconciliation", default_policy()["reconciliation"])
        _require(settings["enabled"] and observation["operator_id"] in settings["authorized_operator_ids"],
                 "retry-limit authorization requires an operator in the host-reviewed reconciliation policy")
        _require(settings.get("max_reconciled_incident_retries_per_ticket", 2) == observation["new_limit"],
                 "authorization new limit differs from the proposed policy")
        _require(observation["policy_sha256"] == _fingerprint(policy), "authorization policy fingerprint mismatch")
        binding = {"project_id": project_id, "observation": observation}
        request_hash = _raw_fingerprint(binding)
        with self._transaction() as connection:
            prior = connection.execute("SELECT * FROM model_retry_limit_authorizations WHERE operation_id=?", (observation["operation_id"],)).fetchone()
            if prior:
                _require(prior["request_hash"] == request_hash, "retry-limit operation ID conflicts with recorded authorization")
                return json.loads(prior["result"])
            _require(set(observation) == required, "new retry-limit authorization requires revision, approved_at and expiry")
            _integer(observation["expected_revision"], "expected_revision", 1)
            try:
                expires = datetime.fromisoformat(observation["expires_at"].replace("Z", "+00:00"))
                approved = datetime.fromisoformat(observation["approved_at"].replace("Z", "+00:00"))
            except ValueError as error:
                raise ValidationError("retry-limit approval time and expiry must be aware ISO timestamps") from error
            now = datetime.now(timezone.utc)
            _require(approved.tzinfo is not None and expires.tzinfo is not None and
                     approved <= now < expires and 0 < (expires - approved).total_seconds() <= 86400,
                     "retry-limit approval time must not be future; expiry must be current and within 24 hours of approved_at")
            references = sorted({_approval_reference(observation[field]) for field in ("authorization_ref", "evidence_ref")})
            ceiling_state = self._current_retry_ceiling(connection, project_id)
            _require(ceiling_state is not None, "initialize the ceiling through admission before authorizing an increase")
            ceiling = ceiling_state["retry_limit"]
            _require(ceiling == observation["expected_limit"] and ceiling_state["revision"] == observation["expected_revision"],
                     "retry-limit ceiling changed; obtain a current authorization")
            for reference in references:
                _require(connection.execute("SELECT 1 FROM model_retry_approval_consumptions WHERE reference_id=?", (reference,)).fetchone() is None,
                         "approval/evidence reference was already consumed; obtain a new human approval and canonical evidence identity")
            _require(datetime.now(timezone.utc) < expires, "retry-limit authorization expiry passed before consumption")
            result = {"status": "retry_limit_authorization_recorded", "project_id": project_id,
                      "operation_id": observation["operation_id"], "previous_limit": ceiling,
                      "new_limit": observation["new_limit"], "policy_sha256": _fingerprint(policy),
                      "previous_revision": ceiling_state["revision"], "new_revision": ceiling_state["revision"] + 1,
                      "approval_expires_at": observation["expires_at"],
                      "approved_at": observation["approved_at"], "consumed_references": references,
                      "approval_time_authenticated_by_helper": False,
                      "human_authenticated_by_helper": False, "budgets_refunded": False}
            audit = {**observation, "recorded_at": datetime.now(timezone.utc).isoformat(),
                     "authentication": "trusted host must authenticate human direction; this record is an assertion"}
            connection.execute("INSERT INTO model_retry_limit_authorizations VALUES (?,?,?,?,?)",
                               (observation["operation_id"], project_id, request_hash, json.dumps(audit), json.dumps(result)))
            for reference in references:
                connection.execute("INSERT INTO model_retry_approval_consumptions VALUES (?,?,?,?)",
                                   (reference, observation["operation_id"], project_id, "first use of host-asserted canonical approval/evidence identity"))
            self._append_retry_ceiling(connection, project_id, ceiling_state, observation["new_limit"],
                                       "authorized_increase", audit)
            return result

    def retry_limit_history(self, project_id):
        """Read the protected ceiling and append-only host assertion audit."""
        _name(project_id, "project_id")
        with closing(self._connect()) as connection:
            ceiling = self._current_retry_ceiling(connection, project_id)
            events = connection.execute("SELECT observation FROM model_retry_ceiling_events WHERE project_id=? ORDER BY revision", (project_id,)).fetchall()
            rows = connection.execute("SELECT observation,result FROM model_retry_limit_authorizations WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return {"project_id": project_id, "retry_ceiling": None if ceiling is None else ceiling["retry_limit"],
                "retry_ceiling_revision": None if ceiling is None else ceiling["revision"],
                "events": [json.loads(row["observation"]) for row in events],
                "authorizations": [{"observation": json.loads(row["observation"]), "result": json.loads(row["result"])} for row in rows]}

    def settle(self, run_id, outcome):
        """Record observed final usage once. Mismatch/overrun quarantines project."""
        _name(run_id, "run_id")
        _object(outcome, "outcome")
        for field in ("actual_model", "actual_reasoning_effort", "actual_context_id"):
            _name(outcome.get(field), field)
        for field in ("success", "validation_passed", "independent_review_passed", "escaped_defect"):
            _require(type(outcome.get(field)) is bool, field + " must be boolean")
        _integer(outcome.get("actual_tokens"), "actual_tokens")
        _integer(outcome.get("actual_cost_microusd"), "actual_cost_microusd", nullable=True)
        if not outcome["success"]:
            _require(outcome.get("failure_kind") in REASONING_FAILURES + NON_REASONING_FAILURES, "failed run requires a classified failure_kind")
        else:
            _require(outcome.get("failure_kind") is None, "successful run cannot have failure_kind")
        if outcome["independent_review_passed"]:
            _name(outcome.get("reviewer_context_id"), "reviewer_context_id")
            _require(outcome["reviewer_context_id"] != outcome["actual_context_id"], "review outcome cannot attest its own independence")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM model_runs WHERE run_id=?", (run_id,)).fetchone()
            _require(row is not None, "unknown reservation")
            _require(row["status"] == "reserved", "reservation already settled; duplicate settlement denied")
            route, request = json.loads(row["route"]), json.loads(row["request"])
            _require(row["reserved_cost"] is None or outcome.get("actual_cost_microusd") is not None,
                     "observed actual cost required to settle a cost reservation")
            violations = []
            if "operating_hash" in outcome and outcome["operating_hash"] != row["operating_hash"]:
                violations.append("host operating snapshot mismatch")
            if outcome["actual_model"] != route["model"] or outcome["actual_reasoning_effort"] != route["reasoning_effort"]:
                violations.append("host model/effort mismatch")
            if request.get("context_id") is not None and outcome["actual_context_id"] != request["context_id"]:
                violations.append("host context mismatch")
            if request["role"] in ("critic", "specialist") and outcome["actual_context_id"] == request["worker_context_id"]:
                violations.append("host review context is not independent")
            if outcome["actual_tokens"] > row["reserved_tokens"]:
                violations.append("host token ceiling overrun")
            if row["reserved_cost"] is not None and outcome["actual_cost_microusd"] > row["reserved_cost"]:
                violations.append("host cost ceiling overrun")
            status = "quarantined" if violations else "settled"
            recorded = {**outcome, "operating_hash": row["operating_hash"],
                        "operating_state": "UNOBSERVED_LEGACY" if row["operating_hash"] is None else "RESERVATION_SNAPSHOT",
                        "violations": violations, "settled_at": datetime.now(timezone.utc).isoformat()}
            connection.execute("UPDATE model_runs SET status=?,actual_tokens=?,actual_cost=?,outcome=?,closed_day=? WHERE run_id=?",
                               (status, outcome["actual_tokens"], outcome.get("actual_cost_microusd"), json.dumps(recorded), recorded["settled_at"][:10], run_id))
            return {"status": status, "run_id": run_id, "accepted": not violations, "violations": violations,
                    "operating_hash": row["operating_hash"], "policy_sha256": row["policy_hash"],
                    "actual_model": outcome["actual_model"], "actual_reasoning_effort": outcome["actual_reasoning_effort"]}

    def resolve_failure(self, run_id, observation):
        """Trusted coordinator records a verified external recovery, never a refund."""
        _name(run_id, "run_id")
        _object(observation, "observation")
        _require(observation.get("verified") is True,
                 "failure resolution requires verified=true from the trusted coordinator")
        for field in ("evidence_ref", "reason"):
            _name(observation.get(field), field)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM model_runs WHERE run_id=?", (run_id,)).fetchone()
            _require(row is not None and row["status"] == "settled", "resolution requires a settled run")
            previous = json.loads(row["outcome"])
            _require(not previous["success"] and previous.get("failure_kind") in NON_REASONING_FAILURES,
                     "only non-reasoning failures can be externally resolved")
            _require(connection.execute("SELECT run_id FROM model_failure_resolutions WHERE run_id=?", (run_id,)).fetchone() is None,
                     "failure resolution already recorded")
            recorded = {**observation, "recorded_at": datetime.now(timezone.utc).isoformat()}
            connection.execute("INSERT INTO model_failure_resolutions VALUES (?,?)", (run_id, json.dumps(recorded)))
            return {"status": "resolved", "run_id": run_id, "budgets_refunded": False}

    def record_defect(self, run_id, observation):
        """Append a late escaped-defect observation without rewriting settlement."""
        _name(run_id, "run_id")
        _object(observation, "observation")
        for field in ("evidence_ref", "reason"):
            _name(observation.get(field), field)
        with self._transaction() as connection:
            row = connection.execute("SELECT status FROM model_runs WHERE run_id=?", (run_id,)).fetchone()
            _require(row is not None and row["status"] in ("settled", "quarantined", "reconciled"),
                     "defect observation requires a closed settled, quarantined or reconciled run")
            observation_id = str(uuid.uuid4())
            recorded = {**observation, "recorded_at": datetime.now(timezone.utc).isoformat()}
            connection.execute("INSERT INTO model_defect_observations VALUES (?,?,?)", (observation_id, run_id, json.dumps(recorded)))
            return {"status": "recorded", "run_id": run_id, "observation_id": observation_id}

    def reconcile(self, project_id, policy, run_id, observation):
        """Close one stopped orphan/quarantine with authorized operator evidence.

        This trusted-caller API records authorization assertions, not authentication.
        It never replaces actual usage or discounts unknown reserved usage.
        """
        _name(project_id, "project_id")
        _name(run_id, "run_id")
        validate_policy(policy)
        _object(observation, "reconciliation observation")
        required = {"reconciliation_id", "operator_id", "authorization_ref", "reason", "evidence_ref",
                    "remediation_ref", "host_stopped", "remediation_verified"}
        _require(set(observation) == required, "reconciliation observation requires exactly " + ", ".join(sorted(required)))
        for field in required - {"host_stopped", "remediation_verified"}:
            _name(observation[field], field)
        _require(observation["host_stopped"] is True, "reconciliation requires verified host termination")
        _require(observation["remediation_verified"] is True, "reconciliation requires verified remediation")
        settings = policy.get("reconciliation", default_policy()["reconciliation"])
        _require(settings["enabled"], "operator reconciliation is disabled by project policy")
        _require(observation["operator_id"] in settings["authorized_operator_ids"], "reconciliation operator is not authorized by project policy")
        binding = {"project_id": project_id, "run_id": run_id, "observation": observation}
        request_hash = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._transaction() as connection:
            prior = connection.execute("SELECT * FROM model_reconciliations WHERE reconciliation_id=?", (observation["reconciliation_id"],)).fetchone()
            if prior:
                _require(prior["request_hash"] == request_hash, "reconciliation idempotency key conflicts with recorded operation")
                return json.loads(prior["result"])
            row = connection.execute("SELECT * FROM model_runs WHERE run_id=? AND project_id=?", (run_id, project_id)).fetchone()
            _require(row is not None, "unknown reservation in this project")
            _require(row["status"] in ("reserved", "quarantined"), "only outstanding reservations or quarantine incidents can be reconciled")
            now = datetime.now(timezone.utc).isoformat()
            before = dict(row)
            if row["status"] == "reserved":
                # Unknown telemetry stays NULL: ordinary accounting keeps charging
                # the full reservation. No success/review attestation is invented.
                closed = {"success": False, "failure_kind": "unknown", "reconciled": True,
                          "settled_at": now, "actual_usage_known": False, "operating_hash": row["operating_hash"]}
                connection.execute("UPDATE model_runs SET status='reconciled',outcome=?,closed_day=? WHERE run_id=?",
                                   (json.dumps(closed), now[:10], run_id))
                connection.execute("INSERT INTO model_failure_resolutions VALUES (?,?)",
                                   (run_id, json.dumps({**observation, "verified": True, "recorded_at": now})))
            else:
                # Preserve the original quarantine outcome, defects and every
                # observed overrun byte; reconciliation is a separate audit event.
                connection.execute("UPDATE model_runs SET status='reconciled' WHERE run_id=?", (run_id,))
                previous_outcome = json.loads(row["outcome"])
                if not previous_outcome["success"] and previous_outcome.get("failure_kind") in NON_REASONING_FAILURES:
                    connection.execute("INSERT INTO model_failure_resolutions VALUES (?,?)",
                                       (run_id, json.dumps({**observation, "verified": True, "recorded_at": now})))
            result = {"status": "reconciled", "run_id": run_id, "project_id": project_id,
                      "operating_hash": row["operating_hash"],
                      "reconciliation_id": observation["reconciliation_id"], "previous_status": row["status"],
                      "budgets_refunded": False, "positive_evidence_eligible": False,
                      "charged_tokens": row["actual_tokens"] if row["actual_tokens"] is not None else row["reserved_tokens"],
                      "charged_cost_microusd": row["actual_cost"] if row["actual_cost"] is not None else row["reserved_cost"]}
            audit = {**observation, "recorded_at": now, "policy_sha256": _fingerprint(policy),
                     "before": before, "authentication": "operator identity and evidence must be authenticated by the external host"}
            connection.execute("INSERT INTO model_reconciliations VALUES (?,?,?,?,?,?)",
                               (observation["reconciliation_id"], run_id, project_id, request_hash, json.dumps(audit), json.dumps(result)))
            return result

    def reconciliation_history(self, project_id):
        """Read project-scoped operator audit events without changing state."""
        _name(project_id, "project_id")
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT observation,result FROM model_reconciliations WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
        return {"project_id": project_id, "reconciliations": [{"observation": json.loads(row["observation"]),
                                                              "result": json.loads(row["result"])} for row in rows]}

    def evidence(self, project_id, policy):
        """Shadow-only summaries; policy never changes itself from this ledger."""
        validate_policy(policy)
        _name(project_id, "project_id")
        groups = {}
        equivalent_hashes = _equivalent_policy_hashes(policy)
        with closing(self._connect()) as connection:
            placeholders = ",".join("?" for _ in equivalent_hashes)
            rows = connection.execute("SELECT * FROM model_runs WHERE project_id=? AND policy_hash IN (" + placeholders + ") AND status IN ('settled','quarantined','reconciled') ORDER BY rowid", (project_id, *equivalent_hashes)).fetchall()
            late_defects = {row[0] for row in connection.execute("SELECT DISTINCT run_id FROM model_defect_observations")}
        # A ticket can contribute at most once to a model/class cohort. Use its
        # latest outcome and remember defects/incidents from earlier attempts.
        # Reconciliation closes accounting incidents; it cannot erase negative
        # evidence or turn unknown reservations into measured actual usage.
        ticket_cohorts = {}
        for row in rows:
            request, outcome, route = json.loads(row["request"]), json.loads(row["outcome"]), json.loads(row["route"])
            key = (request["task_class"], request["role"], route["model"], route["reasoning_effort"], route["simple"], route["high_risk"], row["operating_hash"])
            ticket_key = (key, row["ticket_id"])
            prior = ticket_cohorts.get(ticket_key)
            outcome["escaped_defect"] = outcome.get("escaped_defect", False) or row["run_id"] in late_defects or bool(prior and prior[1]["escaped_defect"])
            outcome["incident"] = row["status"] != "settled" or bool(prior and prior[1]["incident"])
            outcome["independent_review_passed"] = outcome.get("independent_review_passed", False) and row["status"] == "settled"
            outcome["validation_passed"] = outcome.get("validation_passed", False)
            outcome["actual_tokens"] = row["actual_tokens"]
            outcome["actual_cost_microusd"] = row["actual_cost"]
            outcome["charged_tokens"] = row["actual_tokens"] if row["actual_tokens"] is not None else row["reserved_tokens"]
            outcome["charged_cost_microusd"] = row["actual_cost"] if row["actual_cost"] is not None else row["reserved_cost"]
            if prior:
                prior_tokens, new_tokens = prior[1]["actual_tokens"], outcome["actual_tokens"]
                outcome["actual_tokens"] = None if prior_tokens is None or new_tokens is None else prior_tokens + new_tokens
                prior_cost, new_cost = prior[1].get("actual_cost_microusd"), outcome.get("actual_cost_microusd")
                outcome["actual_cost_microusd"] = None if prior_cost is None or new_cost is None else prior_cost + new_cost
                outcome["charged_tokens"] += prior[1]["charged_tokens"]
                prior_charge, new_charge = prior[1]["charged_cost_microusd"], outcome["charged_cost_microusd"]
                outcome["charged_cost_microusd"] = None if prior_charge is None or new_charge is None else prior_charge + new_charge
            ticket_cohorts[ticket_key] = (key, outcome)
        for key, outcome in ticket_cohorts.values():
            group = groups.setdefault(key, {"task_class": key[0], "role": key[1], "model": key[2],
                                           "reasoning_effort": key[3], "simple": key[4], "high_risk": key[5],
                                           "operating_hash": key[6], "operating_state": "UNOBSERVED_LEGACY" if key[6] is None else "RESERVATION_SNAPSHOT",
                                           "sample_unit": "distinct_ticket", "samples": 0, "reviewed_samples": 0, "accepted_samples": 0,
                                           "escaped_defects": 0, "incident_tickets": 0, "actual_tokens": 0,
                                           "actual_cost_microusd": 0, "unknown_token_samples": 0, "unknown_cost_samples": 0,
                                           "charged_tokens": 0, "charged_cost_microusd": 0, "unknown_charge_samples": 0})
            group["samples"] += 1
            group["reviewed_samples"] += int(outcome["independent_review_passed"])
            group["accepted_samples"] += int(outcome["success"] and outcome["validation_passed"] and outcome["independent_review_passed"]
                                              and not outcome["escaped_defect"] and not outcome["incident"])
            group["escaped_defects"] += int(outcome["escaped_defect"])
            group["incident_tickets"] += int(outcome["incident"])
            if outcome["actual_tokens"] is None:
                group["unknown_token_samples"] += 1
            else:
                group["actual_tokens"] += outcome["actual_tokens"]
            group["charged_tokens"] += outcome["charged_tokens"]
            if outcome["charged_cost_microusd"] is None:
                group["unknown_charge_samples"] += 1
            else:
                group["charged_cost_microusd"] += outcome["charged_cost_microusd"]
            if outcome.get("actual_cost_microusd") is None:
                group["unknown_cost_samples"] += 1
            else:
                group["actual_cost_microusd"] += outcome["actual_cost_microusd"]
        for group in groups.values():
            rate = group["accepted_samples"] / group["samples"]
            qualified = (policy["adaptive"]["mode"] == "shadow" and group["simple"] and not group["high_risk"]
                         and group["role"] == "worker" and group["reviewed_samples"] >= policy["adaptive"]["min_reviewed_samples"]
                         and rate * 100 >= policy["adaptive"]["min_success_rate_percent"] and group["escaped_defects"] == 0)
            group["acceptance_rate_percent"] = group["accepted_samples"] * 100 // group["samples"]
            group["recommendation"] = "eligible for a reviewed routing experiment using this observed model" if qualified else "retain explicit routing; insufficient or ineligible evidence"
            if group["unknown_cost_samples"]:
                group["actual_cost_microusd"] = None
            if group["unknown_token_samples"]:
                group["actual_tokens"] = None
            if group["unknown_charge_samples"]:
                group["charged_cost_microusd"] = None
        return {"mode": policy["adaptive"]["mode"], "policy_sha256": _fingerprint(policy),
                "included_stored_policy_sha256": sorted({row["policy_hash"] for row in rows}),
                "compatible_policy_sha256": equivalent_hashes,
                "policy_hash_basis": "effective reconciliation defaults; historical row hashes are preserved",
                "automatic_policy_changes": False, "groups": list(groups.values()),
                "limitations": "Host-supplied outcomes; no causal cost comparison, unseen-model recommendation, or automatic downgrade. Independent context assertions require host authentication."}
