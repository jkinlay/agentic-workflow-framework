"""Reviewed, resource-bounded execution of partitioned validation workloads."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any

from . import ValidationError
from .canonical import fingerprint, load_yaml, loads, now_text, sha256, timestamp
from .child_process import child_env


PLAN_FORMAT = "awf-heavy-validation-plan-3"
REVIEW_FORMAT = "awf-heavy-validation-review-3"
CAPACITY_FORMAT = "awf-heavy-validation-capacity-3"
RESULT_FORMAT = "awf-heavy-validation-result-3"
TERMINAL_STATES = frozenset({"PASS", "FAILED", "TIMED_OUT", "CANCELLED"})
FROZEN_H_PROVIDER_API_KEY_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY",
    "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
})
PROTECTED_GITHUB_ENV_VARS = frozenset({"GH_TOKEN", "GITHUB_TOKEN"})
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_PARTITION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_PARTITIONS = 256
_MAX_ARGV = 64
_MAX_TOKEN_BYTES = 8192
_MAX_OUTPUT_BYTES = 256 * 1024


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a mapping")
    return value


def _exact_keys(value: dict, expected: set[str], label: str):
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValidationError(f"{label} fields mismatch: missing={missing}, extra={extra}")


def _positive_int(value: Any, label: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= maximum):
        raise ValidationError(f"{label} must be an integer from 1 to {maximum}")
    return value


def _nonnegative_int(value: Any, label: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= maximum):
        raise ValidationError(f"{label} must be an integer from 0 to {maximum}")
    return value


def _expected_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _verified_load(raw: bytes, expected_sha256: str, label: str) -> dict:
    if not isinstance(raw, bytes):
        raise ValidationError(f"{label} must be supplied as bytes")
    expected = _expected_digest(expected_sha256, f"expected {label} SHA-256")
    actual = sha256(raw)
    if actual != expected:
        raise ValidationError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    try:
        value = loads(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValidationError(f"{label} must be UTF-8") from exc
    return _mapping(value, label)


def _validate_candidate(value: Any, label: str = "candidate") -> dict:
    value = _mapping(value, label)
    _exact_keys(value, {"repository_id", "base_sha", "head_sha", "tree_sha"}, label)
    _positive_int(value["repository_id"], f"{label}.repository_id")
    for key in ("base_sha", "head_sha", "tree_sha"):
        if not isinstance(value[key], str) or not _SHA.fullmatch(value[key]):
            raise ValidationError(f"{label}.{key} must be a lowercase SHA-1 object ID")
    return deepcopy(value)


def _validate_config(raw: bytes, expected_sha256: str) -> tuple[dict, str]:
    if not isinstance(raw, bytes):
        raise ValidationError("config must be supplied as bytes")
    expected = _expected_digest(expected_sha256, "expected config SHA-256")
    actual = sha256(raw)
    if actual != expected:
        raise ValidationError(f"config SHA-256 mismatch: expected {expected}, got {actual}")
    try:
        value = loads(raw.decode("utf-8")) if raw.lstrip().startswith(b"{") else load_yaml(raw)
    except UnicodeError as exc:
        raise ValidationError("config must be UTF-8") from exc
    return _mapping(value, "config"), actual


def _is_alias(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def resolve_without_alias(path_value: str | os.PathLike[str], label: str, *, directory: bool) -> Path:
    """Reject lexical symlink/reparse components before resolving an absolute path."""
    lexical = Path(path_value)
    if not lexical.is_absolute():
        raise ValidationError(f"{label} must be absolute")
    lexical = Path(os.path.abspath(os.fspath(lexical)))
    current = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        current /= component
        try:
            if _is_alias(current):
                raise ValidationError(f"{label} uses a symlink or reparse alias: {current}")
        except FileNotFoundError as exc:
            raise ValidationError(f"{label} does not exist: {current}") from exc
    resolved = lexical.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(lexical)):
        raise ValidationError(f"{label} lexical and resolved paths differ")
    if directory and not resolved.is_dir():
        raise ValidationError(f"{label} must be a directory")
    if not directory and not resolved.is_file():
        raise ValidationError(f"{label} must be a regular file")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_plan(raw: bytes, expected_sha256: str) -> tuple[dict, str]:
    value = _verified_load(raw, expected_sha256, "plan")
    _exact_keys(value, {"format", "workload_id", "engine", "resource_class",
                        "required_resources", "requested_parallelism", "candidate",
                        "working_directory", "determinism", "isolation", "partitions"}, "plan")
    if value["format"] != PLAN_FORMAT:
        raise ValidationError("Unsupported heavy validation plan format")
    for field in ("workload_id", "engine"):
        if not isinstance(value[field], str) or not _LABEL.fullmatch(value[field]):
            raise ValidationError(f"Invalid plan {field}")
    value["candidate"] = _validate_candidate(value["candidate"], "plan.candidate")
    if (not isinstance(value["working_directory"], str)
            or not Path(value["working_directory"]).is_absolute()
            or _CONTROL.search(value["working_directory"])):
        raise ValidationError("plan.working_directory must be an absolute path string")
    if value["resource_class"] not in {"standard", "heavy", "gpu"}:
        raise ValidationError("resource_class must be standard, heavy, or gpu")
    resources = value["required_resources"]
    if not isinstance(resources, list):
        raise ValidationError("required_resources must be a list")
    if len(resources) != len(set(resources)):
        raise ValidationError("Duplicate required resource")
    for resource in resources:
        if not isinstance(resource, str) or not _LABEL.fullmatch(resource):
            raise ValidationError("Invalid required resource")
    determinism = _mapping(value["determinism"], "plan.determinism")
    _exact_keys(determinism, {"seed", "retry_limit"}, "plan.determinism")
    _nonnegative_int(determinism["seed"], "plan.determinism.seed", maximum=2**63 - 1)
    _nonnegative_int(determinism["retry_limit"], "plan.determinism.retry_limit", maximum=5)
    isolation = _mapping(value["isolation"], "plan.isolation")
    _exact_keys(isolation, {"process_tree", "network", "filesystem"}, "plan.isolation")
    if isolation != {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                      "filesystem": "WORKTREE"}:
        raise ValidationError("plan.isolation must require process-tree, host-network, and worktree controls")
    _positive_int(value["requested_parallelism"], "requested_parallelism", maximum=256)
    parts = value["partitions"]
    if not isinstance(parts, list) or not (1 <= len(parts) <= _MAX_PARTITIONS):
        raise ValidationError(f"partitions must contain 1 to {_MAX_PARTITIONS} entries")
    names: set[str] = set()
    for index, part in enumerate(parts):
        part = _mapping(part, f"partition {index}")
        _exact_keys(part, {"name", "framework", "resources", "argv", "executable",
                           "timeout_seconds", "accepted_exit_codes"},
                    f"partition {index}")
        name = part["name"]
        if not isinstance(name, str) or not _PARTITION_NAME.fullmatch(name):
            raise ValidationError(f"Invalid partition name at index {index}")
        if name in names:
            raise ValidationError(f"Duplicate partition name: {name}")
        names.add(name)
        if part["framework"] not in {"command", "python-unittest", "pytest", "matlab", "wolfram"}:
            raise ValidationError(f"partition {name} framework is unsupported")
        claims = _validate_slots(part["resources"], f"partition {name} resources")
        if any(amount < 1 for amount in claims.values()):
            raise ValidationError(f"partition {name} resource claims must be positive")
        if not set(claims).issubset(resources):
            raise ValidationError(f"partition {name} claims an undeclared resource")
        argv = part["argv"]
        if not isinstance(argv, list):
            raise ValidationError(f"partition {name} argv must be a list")
        if not (1 <= len(argv) <= _MAX_ARGV):
            raise ValidationError(f"partition {name} argv must contain 1 to {_MAX_ARGV} tokens")
        for token in argv:
            if (not isinstance(token, str) or not token
                    or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES or _CONTROL.search(token)):
                raise ValidationError(f"partition {name} argv token is invalid")
        executable = _mapping(part["executable"], f"partition {name} executable")
        _exact_keys(executable, {"path", "sha256"}, f"partition {name} executable")
        if not isinstance(executable["path"], str) or not Path(executable["path"]).is_absolute():
            raise ValidationError(f"partition {name} executable.path must be absolute")
        _expected_digest(executable["sha256"], f"partition {name} executable.sha256")
        if os.path.normcase(argv[0]) != os.path.normcase(executable["path"]):
            raise ValidationError(f"partition {name} argv[0] must equal executable.path")
        program = Path(executable["path"]).name.casefold().removesuffix(".exe")
        if part["framework"] == "python-unittest" and argv[1:3] != ["-m", "unittest"]:
            raise ValidationError(f"partition {name} is not a python unittest adapter command")
        if part["framework"] == "pytest" and argv[1:3] != ["-m", "pytest"]:
            raise ValidationError(f"partition {name} is not a pytest adapter command")
        if part["framework"] == "matlab" and (program != "matlab" or "-batch" not in argv[1:]):
            raise ValidationError(f"partition {name} is not a MATLAB batch adapter command")
        if part["framework"] == "wolfram" and (program != "wolframscript"
                                                  or not ({"-code", "-file"} & set(argv[1:]))):
            raise ValidationError(f"partition {name} is not a WolframScript adapter command")
        _positive_int(part["timeout_seconds"], f"partition {name} timeout_seconds",
                      maximum=86_400)
        exits = part["accepted_exit_codes"]
        if not isinstance(exits, list) or not exits or len(exits) > 32:
            raise ValidationError(f"partition {name} accepted_exit_codes must be a non-empty list")
        if len(exits) != len(set(exits)):
            raise ValidationError(f"partition {name} accepted_exit_codes contains duplicates")
        for code in exits:
            if isinstance(code, bool) or not isinstance(code, int) or not (-255 <= code <= 255):
                raise ValidationError(f"partition {name} accepted exit code is invalid")
    return value, sha256(raw)


def _validate_review(raw: bytes, expected_sha256: str, plan_digest: str,
                     candidate: dict, now: str) -> tuple[dict, str]:
    value = _verified_load(raw, expected_sha256, "review")
    _exact_keys(value, {"format", "plan_sha256", "candidate", "decision", "reviewer",
                        "reviewed_at", "expires_at"}, "review")
    if value["format"] != REVIEW_FORMAT:
        raise ValidationError("Unsupported heavy validation review format")
    if value["plan_sha256"] != plan_digest:
        raise ValidationError("review does not bind the submitted plan SHA-256")
    if _validate_candidate(value["candidate"], "review.candidate") != candidate:
        raise ValidationError("review candidate tuple does not match the plan")
    if value["decision"] != "APPROVE":
        raise ValidationError("review is not approved")
    reviewer = _mapping(value["reviewer"], "review.reviewer")
    _exact_keys(reviewer, {"provider", "immutable_id", "login"}, "review.reviewer")
    for field in ("provider", "immutable_id", "login"):
        if not isinstance(reviewer[field], str) or not reviewer[field].strip() or len(reviewer[field]) > 256:
            raise ValidationError(f"Invalid review reviewer.{field}")
    reviewed = timestamp(value["reviewed_at"])
    expires = timestamp(value["expires_at"])
    instant = timestamp(now)
    if reviewed > instant or expires <= instant or expires <= reviewed:
        raise ValidationError("Review validity interval does not include execution time")
    return value, sha256(raw)


def _authenticate_review(review: dict, review_digest: str, plan_digest: str,
                         candidate: dict, authenticator) -> dict:
    if authenticator is None:
        raise ValidationError("Authenticated review authority is unavailable")
    try:
        value = authenticator(deepcopy(review), review_digest, plan_digest, deepcopy(candidate))
    except Exception as exc:
        raise ValidationError(f"Authenticated review lookup failed: {type(exc).__name__}") from exc
    value = _mapping(value, "authenticated review")
    _exact_keys(value, {"status", "provider", "immutable_id", "login", "evidence_sha256",
                        "review_sha256", "plan_sha256", "candidate"}, "authenticated review")
    if value["status"] != "AUTHENTICATED":
        raise ValidationError("Review authority did not authenticate the approval")
    for field in ("provider", "immutable_id", "login"):
        if value[field] != review["reviewer"][field]:
            raise ValidationError(f"Authenticated reviewer {field} does not match review")
    _expected_digest(value["evidence_sha256"], "authenticated review evidence_sha256")
    if value["review_sha256"] != review_digest or value["plan_sha256"] != plan_digest:
        raise ValidationError("Authenticated review digest binding mismatch")
    if _validate_candidate(value["candidate"], "authenticated review candidate") != candidate:
        raise ValidationError("Authenticated review candidate tuple mismatch")
    return deepcopy(value)


def _validate_slots(value: Any, label: str) -> dict[str, int]:
    value = _mapping(value, label)
    result = {}
    for key, slots in value.items():
        if not isinstance(key, str) or not _LABEL.fullmatch(key):
            raise ValidationError(f"Invalid {label} resource name")
        result[key] = _nonnegative_int(slots, f"{label}.{key}")
    return result


def _validate_capacity(raw: bytes | None, expected_sha256: str | None, now: str,
                       max_age_seconds: int, plan_digest: str, config_digest: str,
                       candidate: dict) -> tuple[dict | None, str | None, bool]:
    if raw is None:
        if expected_sha256 is not None:
            raise ValidationError("Expected capacity SHA-256 supplied without capacity evidence")
        return None, None, False
    if expected_sha256 is None:
        raise ValidationError("Capacity evidence requires an expected SHA-256")
    value = _verified_load(raw, expected_sha256, "capacity")
    _exact_keys(value, {"format", "observed_at", "broker_id", "workers_available",
                         "heavy_jobs_available", "gpu_jobs_available", "resources_available",
                         "engines", "plan_sha256", "config_sha256", "candidate"}, "capacity")
    if value["format"] != CAPACITY_FORMAT:
        raise ValidationError("Unsupported heavy validation capacity format")
    if value["plan_sha256"] != plan_digest or value["config_sha256"] != config_digest:
        raise ValidationError("capacity does not bind the current plan and config")
    if _validate_candidate(value["candidate"], "capacity.candidate") != candidate:
        raise ValidationError("capacity candidate tuple mismatch")
    timestamp(value["observed_at"])
    if not isinstance(value["broker_id"], str) or len(value["broker_id"].encode("utf-8")) > 256:
        raise ValidationError("Invalid capacity broker_id")
    for field in ("workers_available", "heavy_jobs_available", "gpu_jobs_available"):
        _nonnegative_int(value[field], f"capacity.{field}")
    value["resources_available"] = _validate_slots(value["resources_available"],
                                                    "resources_available")
    engines = _mapping(value["engines"], "capacity.engines")
    for engine, evidence in engines.items():
        if not isinstance(engine, str) or not _LABEL.fullmatch(engine):
            raise ValidationError("Invalid capacity engine label")
        evidence = _mapping(evidence, f"capacity engine {engine}")
        _exact_keys(evidence, {"parallel_available", "parallel_slots"},
                    f"capacity engine {engine}")
        if not isinstance(evidence["parallel_available"], bool):
            raise ValidationError(f"capacity engine {engine} parallel_available must be boolean")
        _nonnegative_int(evidence["parallel_slots"],
                         f"capacity engine {engine} parallel_slots")
    age = (timestamp(now) - timestamp(value["observed_at"])).total_seconds()
    stale = age < -30 or age > max_age_seconds
    return value, sha256(raw), stale


def _broker(config: dict) -> tuple[dict, dict, dict]:
    config = _mapping(config, "config")
    execution = _mapping(config.get("execution"), "config.execution")
    broker = _mapping(execution.get("host_broker"), "config.execution.host_broker")
    if not isinstance(broker.get("enabled"), bool):
        raise ValidationError("host_broker.enabled must be boolean")
    if broker.get("lease_before_dispatch") is not True:
        raise ValidationError("host_broker.lease_before_dispatch must be true")
    if not isinstance(broker.get("broker_id"), str):
        raise ValidationError("host_broker.broker_id must be a string")
    for field in ("max_workers", "max_heavy_jobs", "max_gpu_jobs"):
        _nonnegative_int(broker.get(field), f"host_broker.{field}")
    resources = _validate_slots(broker.get("resources", {}), "host_broker.resources")
    return execution, broker, resources


def _strip_names(config: dict) -> frozenset[str]:
    values = _mapping(config.get("execution"), "config.execution").get("child_env_strip_extra", [])
    if not isinstance(values, list):
        raise ValidationError("execution.child_env_strip_extra must be a list")
    folded: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not _ENV_NAME.fullmatch(value):
            raise ValidationError("execution.child_env_strip_extra contains an invalid environment name")
        name = value.upper()
        if name in PROTECTED_GITHUB_ENV_VARS:
            raise ValidationError(f"execution.child_env_strip_extra cannot name {name}")
        if name in folded:
            raise ValidationError("execution.child_env_strip_extra contains a case-insensitive duplicate")
        folded.add(name)
    return frozenset(folded)


def _validation_child_env(config: dict, base=None, *, seed: int | None = None,
                          attempt: int | None = None) -> dict:
    environment = child_env(base)
    denied = FROZEN_H_PROVIDER_API_KEY_ENV_VARS | _strip_names(config)
    for key in list(environment):
        if key.upper() in denied:
            environment.pop(key)
    if seed is not None:
        environment["AWF_VALIDATION_SEED"] = str(seed)
    if attempt is not None:
        environment["AWF_VALIDATION_ATTEMPT"] = str(attempt)
    return environment


def _resource_claims(plan: dict, parallelism: int) -> dict[str, int]:
    """Return a conservative bounded claim for the concurrently scheduled partitions."""
    claims: dict[str, list[int]] = {name: [] for name in plan["required_resources"]}
    for part in plan["partitions"]:
        for name in claims:
            claims[name].append(part["resources"].get(name, 0))
    return {name: sum(sorted(values, reverse=True)[:parallelism])
            for name, values in claims.items() if any(values)}


def _lease_request(plan: dict, plan_digest: str, config_digest: str,
                   capacity_digest: str, parallelism: int, now: str) -> dict:
    required_until = timestamp(now) + timedelta(
        seconds=sum(part["timeout_seconds"] for part in plan["partitions"]) + 60)
    return {"format": "awf-heavy-validation-lease-request-1",
            "candidate": deepcopy(plan["candidate"]), "plan_sha256": plan_digest,
            "config_sha256": config_digest, "capacity_sha256": capacity_digest,
            "parallelism": parallelism, "resource_class": plan["resource_class"],
            "engine": plan["engine"], "required_resources": list(plan["required_resources"]),
            "resource_claims": _resource_claims(plan, parallelism),
            "determinism": deepcopy(plan["determinism"]),
            "isolation": deepcopy(plan["isolation"]),
            "required_until": required_until.isoformat(timespec="seconds").replace("+00:00", "Z")}


def _validate_lease(value: Any, request: dict, broker_id: str, now: str) -> dict:
    value = _mapping(value, "broker lease")
    if value.get("status") == "DENIED":
        _exact_keys(value, {"status", "reason"}, "denied broker lease")
        if not isinstance(value["reason"], str) or not value["reason"]:
            raise ValidationError("Denied broker lease needs a reason")
        return deepcopy(value)
    _exact_keys(value, {"status", "lease_id", "fencing_token", "broker_id", "acquired_at",
                        "expires_at", "request_sha256"}, "broker lease")
    if value["status"] != "GRANTED" or not isinstance(value["lease_id"], str) or not value["lease_id"]:
        raise ValidationError("Broker lease is not a valid grant")
    _positive_int(value["fencing_token"], "broker lease fencing_token")
    if value["broker_id"] != broker_id or value["request_sha256"] != fingerprint("heavy-validation-lease-request", request):
        raise ValidationError("Broker lease binding mismatch")
    acquired, expires, instant = timestamp(value["acquired_at"]), timestamp(value["expires_at"]), timestamp(now)
    if acquired > instant + timedelta(seconds=30) or expires <= instant or expires < timestamp(request["required_until"]):
        return {**deepcopy(value), "status": "STALE"}
    return deepcopy(value)


def _release_lease(client, lease: dict | None) -> dict:
    if lease is None or lease.get("status") not in {"GRANTED", "STALE"}:
        return {"status": "NOT_ACQUIRED"}
    try:
        result = client.release(lease["lease_id"], lease["fencing_token"])
    except Exception as exc:
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: release failed"}
    if (not isinstance(result, dict) or result.get("status") != "RELEASED"
            or result.get("lease_id") != lease["lease_id"]
            or result.get("fencing_token") != lease["fencing_token"]):
        return {"status": "FAILED", "reason": "broker did not confirm the exact fence release"}
    return deepcopy(result)


def _parallelism(plan: dict, config: dict, capacity: dict | None,
                 stale: bool) -> tuple[int, list[str]]:
    execution, broker, configured_resources = _broker(config)
    requested = min(plan["requested_parallelism"], len(plan["partitions"]))
    reasons: list[str] = []
    if not broker["enabled"]:
        return 1, (["broker_disabled"] if requested > 1 else [])
    if capacity is None:
        return 0, ["observed_capacity_unavailable"]
    if stale:
        return 0, ["observed_capacity_stale"]
    if capacity["broker_id"] != broker["broker_id"]:
        raise ValidationError("Capacity broker_id does not match configured host broker")

    ceilings = [("broker.max_workers", broker["max_workers"]),
                ("capacity.workers_available", capacity["workers_available"])]
    resource_class = plan["resource_class"]
    if resource_class in {"heavy", "gpu"}:
        ceilings.extend([("broker.max_heavy_jobs", broker["max_heavy_jobs"]),
                         ("capacity.heavy_jobs_available", capacity["heavy_jobs_available"])])
    if resource_class == "gpu":
        ceilings.extend([("broker.max_gpu_jobs", broker["max_gpu_jobs"]),
                         ("capacity.gpu_jobs_available", capacity["gpu_jobs_available"])])

    engine = plan["engine"]
    engine_capacity = capacity["engines"].get(engine)
    if engine_capacity is None:
        return 0, [f"engine_capacity_unobserved:{engine}"]
    if engine_capacity["parallel_slots"] < 1:
        return 0, [f"engine_capacity_unavailable:{engine}"]
    if not engine_capacity["parallel_available"] or engine_capacity["parallel_slots"] < 2:
        reasons.append(f"engine_parallel_unavailable:{engine}")
    ceilings.append((f"capacity.engine.{engine}", engine_capacity["parallel_slots"]))

    observed_resources = capacity["resources_available"]
    for resource in plan["required_resources"]:
        configured = configured_resources.get(resource, 0)
        observed = observed_resources.get(resource, 0)
        required = max((part["resources"].get(resource, 0) for part in plan["partitions"]), default=0)
        if required > configured or required > observed:
            return 0, [f"resource_capacity_unavailable:{resource}"]

    effective = requested
    for label, ceiling in ceilings:
        if ceiling < requested:
            reasons.append(f"{label}={ceiling}")
        effective = min(effective, ceiling)
    while effective > 1:
        claim = _resource_claims(plan, effective)
        if all(claim.get(name, 0) <= configured_resources.get(name, 0)
               and claim.get(name, 0) <= observed_resources.get(name, 0)
               for name in plan["required_resources"]):
            break
        effective -= 1
        reasons.append("named_resource_capacity_reduced_parallelism")
    if effective < 1:
        return 0, reasons or ["capacity_zero"]
    if effective < 2:
        return 1, reasons or (["parallel_capacity_below_two"] if requested > 1 else [])
    return effective, reasons


def _admit(plan: dict, plan_digest: str, config_digest: str,
           capacity_digest: str | None, desired: int, reasons: list[str],
           broker: dict, client, now: str) -> tuple[int, list[str], dict | None, dict, list[dict], bool]:
    """Acquire an exact lease for governed parallel or serial execution."""
    events: list[dict] = []
    if not broker["enabled"]:
        return desired, reasons, None, {"status": "NOT_REQUIRED"}, events, desired > 0
    if desired < 1:
        return 0, reasons, None, {"status": "NOT_ACQUIRED"}, events, False
    if client is None:
        return 0, [*reasons, "lease_client_unavailable"], None, {"status": "NOT_ACQUIRED"}, events, False
    parallelism = desired
    while parallelism >= 1:
        request = _lease_request(plan, plan_digest, config_digest, capacity_digest,
                                 parallelism, now)
        event = {"parallelism": parallelism,
                 "request_sha256": fingerprint("heavy-validation-lease-request", request)}
        try:
            lease = _validate_lease(client.acquire(deepcopy(request)), request,
                                    broker["broker_id"], now)
        except ValidationError:
            raise
        except Exception as exc:
            event.update(status="FAILED", reason=type(exc).__name__)
            events.append(event)
            if parallelism == 1:
                return 0, [*reasons, f"lease_acquire_failed:{type(exc).__name__}"], None, {"status": "NOT_ACQUIRED"}, events, False
            reasons = [*reasons, f"parallel_lease_acquire_failed:{type(exc).__name__}"]
            parallelism = 1
            continue
        event["status"] = lease["status"]
        if lease["status"] == "DENIED":
            event["reason"] = lease["reason"]
            events.append(event)
            if parallelism == 1:
                return 0, [*reasons, "serial_lease_denied:" + lease["reason"]], None, {"status": "NOT_ACQUIRED"}, events, False
            reasons = [*reasons, "parallel_lease_denied:" + lease["reason"]]
            parallelism = 1
            continue
        if lease["status"] == "STALE":
            released = _release_lease(client, lease)
            event["release"] = released
            events.append(event)
            if released.get("status") != "RELEASED":
                return 0, [*reasons, "stale_fence_release_failed"], lease, released, events, False
            if parallelism == 1:
                return 0, [*reasons, "serial_lease_stale"], None, released, events, False
            reasons = [*reasons, "parallel_lease_stale"]
            parallelism = 1
            continue
        events.append(event)
        return parallelism, reasons, lease, {"status": "PENDING"}, events, True
    return 0, reasons, None, {"status": "NOT_ACQUIRED"}, events, False


def _bounded_digest(data: bytes) -> tuple[str, int, bool, str]:
    digest = hashlib.sha256(data).hexdigest()
    total = len(data)
    truncated = total > _MAX_OUTPUT_BYTES
    shown = data[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return digest, total, truncated, shown


class _Capture:
    """Drain a pipe continuously while retaining only bounded display bytes."""

    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.total = 0
        self.shown = bytearray()
        self.error: Exception | None = None

    def drain(self):
        try:
            while True:
                chunk = self.stream.read(65_536)
                if not chunk:
                    break
                self.digest.update(chunk)
                self.total += len(chunk)
                remaining = _MAX_OUTPUT_BYTES - len(self.shown)
                if remaining > 0:
                    self.shown.extend(chunk[:remaining])
        except Exception as exc:
            self.error = exc
        finally:
            self.stream.close()

    def evidence(self) -> tuple[str, int, bool, str]:
        if self.error is not None:
            raise self.error
        return (self.digest.hexdigest(), self.total, self.total > _MAX_OUTPUT_BYTES,
                bytes(self.shown).decode("utf-8", errors="replace"))


def _terminate_process_tree(proc: subprocess.Popen, config: dict) -> dict:
    job = getattr(proc, "_awf_job_handle", None)
    if os.name == "nt" and job:
        try:
            import ctypes
            from ctypes import wintypes
            terminate = ctypes.windll.kernel32.TerminateJobObject
            terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
            terminate.restype = wintypes.BOOL
            if terminate(job, 1):
                return {"outcome": "COMPLETE", "mechanism": "windows_job_object"}
        except (AttributeError, OSError):
            pass
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot")
        taskkill = (Path(system_root) / "System32" / "taskkill.exe") if system_root else None
        try:
            if taskkill is None:
                raise OSError("SystemRoot is unavailable")
            done = subprocess.run([str(taskkill), "/PID", str(proc.pid), "/T", "/F"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, shell=False,
                                  env=_validation_child_env(config), timeout=10)
            if done.returncode == 0:
                return {"outcome": "COMPLETE", "mechanism": "windows_taskkill_tree"}
        except (OSError, subprocess.SubprocessError, ValidationError):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return {"outcome": "COMPLETE", "mechanism": "posix_process_group"}
        except ProcessLookupError:
            return {"outcome": "COMPLETE", "mechanism": "posix_process_group_absent"}
        except OSError:
            pass
    try:
        if proc.poll() is None:
            proc.kill()
        return {"outcome": "PARTIAL", "mechanism": "direct_process_only"}
    except OSError:
        return {"outcome": "FAILED", "mechanism": "termination_failed"}


def _attach_windows_job(proc: subprocess.Popen):
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.windll.kernel32
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ValidationError("Windows containment job creation failed")
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel.CloseHandle(handle)
            raise ValidationError("Windows containment policy setup failed")
        if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(proc._handle)):
            kernel.CloseHandle(handle)
            raise ValidationError("Windows containment assignment failed")
        proc._awf_job_handle = handle
        return handle
    except ValidationError:
        raise
    except (AttributeError, OSError, ValueError) as exc:
        raise ValidationError("Windows containment setup failed") from exc


def _release_windows_launcher(proc: subprocess.Popen, argv: list[str]):
    if os.name != "nt":
        return
    try:
        payload = json.dumps({"argv": argv}, ensure_ascii=True,
                             separators=(",", ":")).encode("ascii") + b"\n"
        proc.stdin.write(payload)
        proc.stdin.flush()
        proc.stdin.close()
    except (AttributeError, OSError, ValueError) as exc:
        raise ValidationError("Windows contained launcher could not be released") from exc


def _close_windows_job(proc: subprocess.Popen):
    handle = getattr(proc, "_awf_job_handle", None)
    if not handle:
        return
    try:
        import ctypes
        from ctypes import wintypes
        close = ctypes.windll.kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)
    finally:
        proc._awf_job_handle = None


def _result_shell(partition: dict, plan: dict, executable: dict) -> dict:
    identity = fingerprint("heavy-validation-partition", {
        "candidate": plan["candidate"],
        "workload_id": plan["workload_id"],
        "working_directory": plan["working_directory"],
        "name": partition["name"],
        "engine": plan["engine"],
        "argv": partition["argv"],
        "executable": executable,
    })
    return {
        "partition_id": identity,
        "name": partition["name"],
        "engine": plan["engine"],
        "framework": partition["framework"],
        "resources": deepcopy(partition["resources"]),
        "isolation": deepcopy(plan["isolation"]),
        "determinism": deepcopy(plan["determinism"]),
        "command_argv": list(partition["argv"]),
        "command_sha256": fingerprint("heavy-validation-command", partition["argv"]),
        "timeout_seconds": partition["timeout_seconds"],
        "accepted_exit_codes": list(partition["accepted_exit_codes"]),
        "executable": deepcopy(executable),
    }


def _execute_attempt(partition: dict, plan: dict, executable: dict, cwd: Path, config: dict,
                     cancel_event: threading.Event, attempt: int) -> dict:
    result = _result_shell(partition, plan, executable)
    result["started_at"] = now_text()
    state = "FAILED"
    exit_code = None
    stdout_evidence = _bounded_digest(b"")
    stderr_evidence = _bounded_digest(b"")
    error_type = None
    cleanup = {"outcome": "NOT_REQUIRED", "mechanism": "none"}
    proc = None
    released = os.name != "nt"
    try:
        if cancel_event.is_set():
            state = "CANCELLED"
            cleanup = {"outcome": "COMPLETE", "mechanism": "not_started"}
        else:
            target_argv = [executable["resolved_path"], *partition["argv"][1:]]
            if os.name == "nt":
                launcher = str(Path(__file__).with_name("heavy_validation_child.py"))
                command = [sys.executable, "-B", launcher]
                options = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
                child_stdin = subprocess.PIPE
            else:
                command = target_argv
                options = {"start_new_session": True}
                child_stdin = subprocess.DEVNULL
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=child_stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=_validation_child_env(config, seed=plan["determinism"]["seed"],
                                          attempt=attempt),
                **options,
            )
            if os.name == "nt":
                _attach_windows_job(proc)
                _release_windows_launcher(proc, target_argv)
                released = True
            stdout_capture = _Capture(proc.stdout)
            stderr_capture = _Capture(proc.stderr)
            readers = [threading.Thread(target=stdout_capture.drain, daemon=True),
                       threading.Thread(target=stderr_capture.drain, daemon=True)]
            for reader in readers:
                reader.start()
            deadline = time.monotonic() + partition["timeout_seconds"]
            while proc.poll() is None:
                if cancel_event.is_set():
                    cleanup = _terminate_process_tree(proc, config)
                    state = "CANCELLED"
                    break
                if time.monotonic() >= deadline:
                    cleanup = _terminate_process_tree(proc, config)
                    state = "TIMED_OUT"
                    break
                time.sleep(0.02)
            proc.wait()
            for reader in readers:
                reader.join(timeout=15)
            if any(reader.is_alive() for reader in readers):
                cleanup = _terminate_process_tree(proc, config)
                state, error_type = "FAILED", "DescendantPipeTimeout"
            stdout_evidence = stdout_capture.evidence()
            stderr_evidence = stderr_capture.evidence()
            exit_code = proc.returncode
            if state not in {"CANCELLED", "TIMED_OUT"} and error_type is None:
                state = "PASS" if exit_code in partition["accepted_exit_codes"] else "FAILED"
    except OSError as exc:
        state = "FAILED"
        error_type = type(exc).__name__
        stderr_evidence = _bounded_digest(str(exc).encode("utf-8", errors="replace"))
        if proc is not None and proc.poll() is None:
            if os.name == "nt" and not released:
                proc.kill()
                cleanup = {"outcome": "COMPLETE", "mechanism": "contained_launcher_terminated"}
            else:
                cleanup = _terminate_process_tree(proc, config)
            proc.wait()
    except Exception as exc:  # fail closed and retain one terminal record per partition
        state = "FAILED"
        error_type = type(exc).__name__
        stderr_evidence = _bounded_digest(str(exc).encode("utf-8", errors="replace"))
        if proc is not None and proc.poll() is None:
            if os.name == "nt" and not released:
                proc.kill()
                cleanup = {"outcome": "COMPLETE", "mechanism": "contained_launcher_terminated"}
            else:
                cleanup = _terminate_process_tree(proc, config)
            proc.wait()
    finally:
        if proc is not None:
            _close_windows_job(proc)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
    result["ended_at"] = now_text()
    stdout_digest, stdout_bytes, stdout_truncated, stdout_text = stdout_evidence
    stderr_digest, stderr_bytes, stderr_truncated, stderr_text = stderr_evidence
    result.update({
        "attempt": attempt,
        "state": state,
        "exit_code": exit_code,
        "timed_out": state == "TIMED_OUT",
        "cancelled": state == "CANCELLED",
        "error_type": error_type,
        "process_tree_cleanup": cleanup,
        "stdout_sha256": stdout_digest,
        "stdout_bytes": stdout_bytes,
        "stdout_truncated": stdout_truncated,
        "stdout": stdout_text,
        "stderr_sha256": stderr_digest,
        "stderr_bytes": stderr_bytes,
        "stderr_truncated": stderr_truncated,
        "stderr": stderr_text,
    })
    return result


def _execute(partition: dict, plan: dict, executable: dict, cwd: Path, config: dict,
             cancel_event: threading.Event) -> dict:
    attempts = []
    maximum = plan["determinism"]["retry_limit"] + 1
    final = None
    for attempt in range(1, maximum + 1):
        final = _execute_attempt(partition, plan, executable, cwd, config, cancel_event, attempt)
        attempts.append({key: final[key] for key in (
            "attempt", "state", "exit_code", "timed_out", "cancelled", "error_type",
            "process_tree_cleanup", "stdout_sha256", "stderr_sha256")})
        if (final["state"] in {"PASS", "CANCELLED"}
                or final["process_tree_cleanup"]["outcome"] in {"PARTIAL", "FAILED"}
                or cancel_event.is_set()):
            break
    final["attempts"] = attempts
    final["retry_count"] = len(attempts) - 1
    return final


def _unexpected_result(partition: dict, plan: dict, executable: dict, exc: Exception) -> dict:
    result = _result_shell(partition, plan, executable)
    moment = now_text()
    stderr = str(exc).encode("utf-8", errors="replace")
    digest, length, truncated, shown = _bounded_digest(stderr)
    empty = hashlib.sha256(b"").hexdigest()
    result.update({
        "started_at": moment, "ended_at": moment, "attempt": 0,
        "state": "FAILED", "exit_code": None,
        "timed_out": False, "cancelled": False, "error_type": type(exc).__name__,
        "process_tree_cleanup": {"outcome": "FAILED", "mechanism": "executor_failure"},
        "stdout_sha256": empty, "stdout_bytes": 0, "stdout_truncated": False, "stdout": "",
        "stderr_sha256": digest, "stderr_bytes": length, "stderr_truncated": truncated,
        "stderr": shown,
    })
    result["attempts"] = [{key: result[key] for key in (
        "attempt", "state", "exit_code", "timed_out", "cancelled", "error_type",
        "process_tree_cleanup", "stdout_sha256", "stderr_sha256")}]
    result["retry_count"] = 0
    return result


def _execution_context(plan: dict, expected_candidate: dict,
                       execution_root: str | os.PathLike[str]):
    if _validate_candidate(expected_candidate, "expected candidate") != plan["candidate"]:
        raise ValidationError("Expected candidate tuple does not match plan")
    root = resolve_without_alias(execution_root, "execution root", directory=True)
    planned = resolve_without_alias(plan["working_directory"], "plan working_directory", directory=True)
    if os.path.normcase(str(root)) != os.path.normcase(str(planned)):
        raise ValidationError("Execution cwd does not match reviewed plan")
    executables = []
    for part in plan["partitions"]:
        path = resolve_without_alias(part["executable"]["path"],
                                     f"partition {part['name']} executable", directory=False)
        observed = _file_sha256(path)
        if observed != part["executable"]["sha256"]:
            raise ValidationError(f"partition {part['name']} executable digest mismatch")
        executables.append({"path": part["executable"]["path"],
                            "resolved_path": str(path), "sha256": observed})
    return root, executables


def run_validation(*, plan_raw: bytes, expected_plan_sha256: str,
                   review_raw: bytes, expected_review_sha256: str,
                   config_raw: bytes, expected_config_sha256: str,
                   expected_candidate: dict, execution_root: str | os.PathLike[str],
                   review_authenticator=None, capacity_raw: bytes | None = None,
                   expected_capacity_sha256: str | None = None, broker_client=None,
                   now: str | None = None, max_capacity_age_seconds: int = 300,
                   cancel_event: threading.Event | None = None) -> dict:
    """Execute a reviewed partition plan and return complete fail-closed evidence.

    Inputs are exact digest-bound documents. A trusted host authenticator must
    independently authenticate the immutable reviewer and candidate tuple.
    """
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    timestamp(now)
    max_capacity_age_seconds = _positive_int(max_capacity_age_seconds,
                                             "max_capacity_age_seconds", maximum=86_400)
    plan, plan_digest = _validate_plan(plan_raw, expected_plan_sha256)
    config, config_digest = _validate_config(config_raw, expected_config_sha256)
    root, executables = _execution_context(plan, expected_candidate, execution_root)
    review, review_digest = _validate_review(
        review_raw, expected_review_sha256, plan_digest, plan["candidate"], now)
    review_authority = _authenticate_review(
        review, review_digest, plan_digest, plan["candidate"], review_authenticator)
    capacity, capacity_digest, stale = _validate_capacity(
        capacity_raw, expected_capacity_sha256, now, max_capacity_age_seconds,
        plan_digest, config_digest, plan["candidate"])
    effective, reasons = _parallelism(plan, config, capacity, stale)
    execution_config, broker, _ = _broker(config)
    effective, reasons, lease, lease_release, admission_events, admitted = _admit(
        plan, plan_digest, config_digest, capacity_digest, effective, reasons,
        broker, broker_client, now)
    event = cancel_event or threading.Event()
    started_at = now_text()
    results: list[dict] = []
    try:
        if admitted:
            with ThreadPoolExecutor(max_workers=effective, thread_name_prefix="awf-heavy") as executor:
                futures = {executor.submit(_execute, part, plan, executable, root, config, event): (part, executable)
                           for part, executable in zip(plan["partitions"], executables)}
                for future in as_completed(futures):
                    part, executable = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:  # executor failures still get terminal evidence
                        results.append(_unexpected_result(part, plan, executable, exc))
    finally:
        if lease is not None and lease.get("status") == "GRANTED":
            lease_release = _release_lease(broker_client, lease)
    ended_at = now_text()
    results.sort(key=lambda item: item["name"])
    terminal = len(results) == len(plan["partitions"]) and all(
        item["state"] in TERMINAL_STATES for item in results)
    cleanup_complete = all(item["process_tree_cleanup"]["outcome"] not in {"PARTIAL", "FAILED"}
                           for item in results)
    release_complete = (not broker["enabled"] or
                        (admitted and lease is not None and lease.get("status") == "GRANTED"
                         and lease_release["status"] == "RELEASED"))
    passed = admitted and terminal and cleanup_complete and release_complete and all(
        item["state"] == "PASS" for item in results)
    stable = [{key: item[key] for key in (
        "partition_id", "name", "engine", "command_sha256", "state", "exit_code",
        "timed_out", "cancelled", "retry_count", "attempts", "process_tree_cleanup",
        "stdout_sha256", "stderr_sha256")}
        for item in results]
    serial_equivalence_sha256 = fingerprint("heavy-validation-serial-equivalence", {
        "candidate": plan["candidate"], "plan_sha256": plan_digest,
        "partition_set": stable})
    cap_keys = ("max_tokens_per_ticket", "max_cost_microusd_per_ticket",
                "daily_project_cost_microusd")
    preserved_caps = {key: execution_config[key] for key in cap_keys if key in execution_config}
    return {
        "format": RESULT_FORMAT,
        "status": "PASS" if passed else "FAIL",
        "candidate": deepcopy(plan["candidate"]),
        "working_directory": str(root),
        "workload_id": plan["workload_id"],
        "engine": plan["engine"],
        "resource_class": plan["resource_class"],
        "required_resources": list(plan["required_resources"]),
        "determinism": deepcopy(plan["determinism"]),
        "isolation": deepcopy(plan["isolation"]),
        "plan_sha256": plan_digest,
        "review_sha256": review_digest,
        "review_authority": review_authority,
        "config_sha256": config_digest,
        "capacity_sha256": capacity_digest,
        "lease": deepcopy(lease),
        "lease_release": lease_release,
        "admission": {"granted": admitted, "events": admission_events,
                      "release_complete": release_complete},
        "started_at": started_at,
        "ended_at": ended_at,
        "all_partitions_terminal": terminal,
        "execution_authority": False,
        "execution": {
            "mode": ("PARALLEL" if effective > 1 else
                     "SERIAL" if effective == 1 else "NOT_STARTED"),
            "requested_parallelism": plan["requested_parallelism"],
            "effective_parallelism": effective,
            "fallback_reasons": reasons,
            "scheduled_partition_count": len(plan["partitions"]),
            "completed_terminal_count": sum(item["state"] in TERMINAL_STATES for item in results),
            "shell": False,
            "process_tree_cleanup_complete": cleanup_complete,
        },
        "partitions": results,
        "aggregate_sha256": fingerprint("heavy-validation-result", {
            "candidate": plan["candidate"], "working_directory": str(root),
            "plan_sha256": plan_digest, "review_sha256": review_digest,
            "config_sha256": config_digest, "capacity_sha256": capacity_digest,
            "lease": lease, "lease_release": lease_release,
            "admission_events": admission_events, "partitions": stable}),
        "serial_equivalence_sha256": serial_equivalence_sha256,
        "preserved_caps": preserved_caps,
        "native_streams": deepcopy(execution_config.get("native_streams")),
        "credential_strip_names": sorted(FROZEN_H_PROVIDER_API_KEY_ENV_VARS | _strip_names(config)),
    }
