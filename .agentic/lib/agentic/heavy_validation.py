"""Reviewed, resource-bounded execution of partitioned validation workloads."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re
import subprocess
import threading
import time
from typing import Any

from . import ValidationError
from .canonical import fingerprint, loads, now_text, sha256, timestamp
from .child_process import child_env


PLAN_FORMAT = "awf-heavy-validation-plan-1"
REVIEW_FORMAT = "awf-heavy-validation-review-1"
CAPACITY_FORMAT = "awf-heavy-validation-capacity-1"
TERMINAL_STATES = frozenset({"PASS", "FAILED", "TIMED_OUT", "CANCELLED"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_PARTITION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
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


def _validate_plan(raw: bytes, expected_sha256: str) -> tuple[dict, str]:
    value = _verified_load(raw, expected_sha256, "plan")
    _exact_keys(value, {"format", "workload_id", "engine", "resource_class",
                        "required_resources", "requested_parallelism", "partitions"}, "plan")
    if value["format"] != PLAN_FORMAT:
        raise ValidationError("Unsupported heavy validation plan format")
    for field in ("workload_id", "engine"):
        if not isinstance(value[field], str) or not _LABEL.fullmatch(value[field]):
            raise ValidationError(f"Invalid plan {field}")
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
    _positive_int(value["requested_parallelism"], "requested_parallelism", maximum=256)
    parts = value["partitions"]
    if not isinstance(parts, list) or not (1 <= len(parts) <= _MAX_PARTITIONS):
        raise ValidationError(f"partitions must contain 1 to {_MAX_PARTITIONS} entries")
    names: set[str] = set()
    for index, part in enumerate(parts):
        part = _mapping(part, f"partition {index}")
        _exact_keys(part, {"name", "argv", "timeout_seconds", "accepted_exit_codes"},
                    f"partition {index}")
        name = part["name"]
        if not isinstance(name, str) or not _PARTITION_NAME.fullmatch(name):
            raise ValidationError(f"Invalid partition name at index {index}")
        if name in names:
            raise ValidationError(f"Duplicate partition name: {name}")
        names.add(name)
        argv = part["argv"]
        if not isinstance(argv, list):
            raise ValidationError(f"partition {name} argv must be a list")
        if not (1 <= len(argv) <= _MAX_ARGV):
            raise ValidationError(f"partition {name} argv must contain 1 to {_MAX_ARGV} tokens")
        for token in argv:
            if (not isinstance(token, str) or not token
                    or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES or _CONTROL.search(token)):
                raise ValidationError(f"partition {name} argv token is invalid")
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


def _validate_review(raw: bytes, expected_sha256: str, plan_digest: str, now: str) -> tuple[dict, str]:
    value = _verified_load(raw, expected_sha256, "review")
    _exact_keys(value, {"format", "plan_sha256", "decision", "reviewer_identity",
                        "reviewed_at", "expires_at"}, "review")
    if value["format"] != REVIEW_FORMAT:
        raise ValidationError("Unsupported heavy validation review format")
    if value["plan_sha256"] != plan_digest:
        raise ValidationError("review does not bind the submitted plan SHA-256")
    if value["decision"] != "APPROVE":
        raise ValidationError("review is not approved")
    identity = value["reviewer_identity"]
    if not isinstance(identity, str) or not identity.strip() or len(identity.encode("utf-8")) > 256:
        raise ValidationError("Invalid review reviewer_identity")
    reviewed = timestamp(value["reviewed_at"])
    expires = timestamp(value["expires_at"])
    instant = timestamp(now)
    if reviewed > instant or expires <= instant or expires <= reviewed:
        raise ValidationError("Review validity interval does not include execution time")
    return value, sha256(raw)


def _validate_slots(value: Any, label: str) -> dict[str, int]:
    value = _mapping(value, label)
    result = {}
    for key, slots in value.items():
        if not isinstance(key, str) or not _LABEL.fullmatch(key):
            raise ValidationError(f"Invalid {label} resource name")
        result[key] = _nonnegative_int(slots, f"{label}.{key}")
    return result


def _validate_capacity(raw: bytes | None, expected_sha256: str | None, now: str,
                       max_age_seconds: int) -> tuple[dict | None, str | None, bool]:
    if raw is None:
        if expected_sha256 is not None:
            raise ValidationError("Expected capacity SHA-256 supplied without capacity evidence")
        return None, None, False
    if expected_sha256 is None:
        raise ValidationError("Capacity evidence requires an expected SHA-256")
    value = _verified_load(raw, expected_sha256, "capacity")
    _exact_keys(value, {"format", "observed_at", "broker_id", "workers_available",
                        "heavy_jobs_available", "gpu_jobs_available", "resources_available",
                        "engines"}, "capacity")
    if value["format"] != CAPACITY_FORMAT:
        raise ValidationError("Unsupported heavy validation capacity format")
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
    if not isinstance(broker.get("broker_id"), str):
        raise ValidationError("host_broker.broker_id must be a string")
    for field in ("max_workers", "max_heavy_jobs", "max_gpu_jobs"):
        _nonnegative_int(broker.get(field), f"host_broker.{field}")
    resources = _validate_slots(broker.get("resources", {}), "host_broker.resources")
    return execution, broker, resources


def _parallelism(plan: dict, config: dict, capacity: dict | None,
                 stale: bool) -> tuple[int, list[str]]:
    execution, broker, configured_resources = _broker(config)
    requested = min(plan["requested_parallelism"], len(plan["partitions"]))
    reasons: list[str] = []
    if requested <= 1:
        return 1, reasons
    if not broker["enabled"]:
        return 1, ["broker_disabled"]
    if capacity is None:
        return 1, ["observed_capacity_unavailable"]
    if stale:
        return 1, ["observed_capacity_stale"]
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
        return 1, [f"engine_parallel_unobserved:{engine}"]
    if (not engine_capacity["parallel_available"]
            or engine_capacity["parallel_slots"] < 2):
        return 1, [f"engine_parallel_unavailable:{engine}"]
    ceilings.append((f"capacity.engine.{engine}", engine_capacity["parallel_slots"]))

    observed_resources = capacity["resources_available"]
    for resource in plan["required_resources"]:
        configured = configured_resources.get(resource, 0)
        observed = observed_resources.get(resource, 0)
        if configured < 1 or observed < 1:
            return 1, [f"resource_capacity_unavailable:{resource}"]
        ceilings.extend([(f"broker.resource.{resource}", configured),
                         (f"capacity.resource.{resource}", observed)])

    effective = requested
    for label, ceiling in ceilings:
        if ceiling < requested:
            reasons.append(f"{label}={ceiling}")
        effective = min(effective, ceiling)
    if effective < 2:
        return 1, reasons or ["parallel_capacity_below_two"]
    return effective, reasons


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


def _result_shell(partition: dict, engine: str, workload_id: str) -> dict:
    identity = fingerprint("heavy-validation-partition", {
        "workload_id": workload_id,
        "name": partition["name"],
        "engine": engine,
        "argv": partition["argv"],
    })
    return {
        "partition_id": identity,
        "name": partition["name"],
        "engine": engine,
        "command_argv": list(partition["argv"]),
        "command_sha256": fingerprint("heavy-validation-command", partition["argv"]),
        "timeout_seconds": partition["timeout_seconds"],
        "accepted_exit_codes": list(partition["accepted_exit_codes"]),
    }


def _execute(partition: dict, engine: str, workload_id: str,
             cancel_event: threading.Event) -> dict:
    result = _result_shell(partition, engine, workload_id)
    result["started_at"] = now_text()
    state = "FAILED"
    exit_code = None
    stdout_evidence = _bounded_digest(b"")
    stderr_evidence = _bounded_digest(b"")
    error_type = None
    proc = None
    try:
        if cancel_event.is_set():
            state = "CANCELLED"
        else:
            proc = subprocess.Popen(
                partition["argv"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=child_env(),
            )
            stdout_capture = _Capture(proc.stdout)
            stderr_capture = _Capture(proc.stderr)
            readers = [threading.Thread(target=stdout_capture.drain, daemon=True),
                       threading.Thread(target=stderr_capture.drain, daemon=True)]
            for reader in readers:
                reader.start()
            deadline = time.monotonic() + partition["timeout_seconds"]
            while proc.poll() is None:
                if cancel_event.is_set():
                    proc.kill()
                    state = "CANCELLED"
                    break
                if time.monotonic() >= deadline:
                    proc.kill()
                    state = "TIMED_OUT"
                    break
                time.sleep(0.02)
            proc.wait()
            for reader in readers:
                reader.join()
            stdout_evidence = stdout_capture.evidence()
            stderr_evidence = stderr_capture.evidence()
            exit_code = proc.returncode
            if state not in {"CANCELLED", "TIMED_OUT"}:
                state = "PASS" if exit_code in partition["accepted_exit_codes"] else "FAILED"
    except OSError as exc:
        state = "FAILED"
        error_type = type(exc).__name__
        stderr_evidence = _bounded_digest(str(exc).encode("utf-8", errors="replace"))
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
    except Exception as exc:  # fail closed and retain one terminal record per partition
        state = "FAILED"
        error_type = type(exc).__name__
        stderr_evidence = _bounded_digest(str(exc).encode("utf-8", errors="replace"))
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
    result["ended_at"] = now_text()
    stdout_digest, stdout_bytes, stdout_truncated, stdout_text = stdout_evidence
    stderr_digest, stderr_bytes, stderr_truncated, stderr_text = stderr_evidence
    result.update({
        "state": state,
        "exit_code": exit_code,
        "timed_out": state == "TIMED_OUT",
        "cancelled": state == "CANCELLED",
        "error_type": error_type,
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


def _unexpected_result(partition: dict, engine: str, workload_id: str, exc: Exception) -> dict:
    result = _result_shell(partition, engine, workload_id)
    moment = now_text()
    stderr = str(exc).encode("utf-8", errors="replace")
    digest, length, truncated, shown = _bounded_digest(stderr)
    empty = hashlib.sha256(b"").hexdigest()
    result.update({
        "started_at": moment, "ended_at": moment, "state": "FAILED", "exit_code": None,
        "timed_out": False, "cancelled": False, "error_type": type(exc).__name__,
        "stdout_sha256": empty, "stdout_bytes": 0, "stdout_truncated": False, "stdout": "",
        "stderr_sha256": digest, "stderr_bytes": length, "stderr_truncated": truncated,
        "stderr": shown,
    })
    return result


def run_validation(*, plan_raw: bytes, expected_plan_sha256: str,
                   review_raw: bytes, expected_review_sha256: str,
                   config: dict, capacity_raw: bytes | None = None,
                   expected_capacity_sha256: str | None = None,
                   now: str | None = None, max_capacity_age_seconds: int = 300,
                   cancel_event: threading.Event | None = None) -> dict:
    """Execute a reviewed partition plan and return complete fail-closed evidence.

    Inputs are exact digest-bound byte documents. The caller or trusted host is
    responsible for authenticating who supplied the review and capacity record.
    """
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    timestamp(now)
    max_capacity_age_seconds = _positive_int(max_capacity_age_seconds,
                                             "max_capacity_age_seconds", maximum=86_400)
    plan, plan_digest = _validate_plan(plan_raw, expected_plan_sha256)
    review, review_digest = _validate_review(review_raw, expected_review_sha256, plan_digest, now)
    capacity, capacity_digest, stale = _validate_capacity(
        capacity_raw, expected_capacity_sha256, now, max_capacity_age_seconds)
    effective, reasons = _parallelism(plan, config, capacity, stale)
    execution_config, _, _ = _broker(config)
    event = cancel_event or threading.Event()
    started_at = now_text()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=effective, thread_name_prefix="awf-heavy") as executor:
        futures = {executor.submit(_execute, part, plan["engine"], plan["workload_id"], event): part
                   for part in plan["partitions"]}
        for future in as_completed(futures):
            part = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # executor failures still get terminal evidence
                results.append(_unexpected_result(part, plan["engine"], plan["workload_id"], exc))
    ended_at = now_text()
    results.sort(key=lambda item: item["name"])
    terminal = len(results) == len(plan["partitions"]) and all(
        item["state"] in TERMINAL_STATES for item in results)
    passed = terminal and all(item["state"] == "PASS" for item in results)
    stable = [{key: item[key] for key in (
        "partition_id", "name", "engine", "command_sha256", "state", "exit_code",
        "timed_out", "cancelled", "stdout_sha256", "stderr_sha256")}
        for item in results]
    cap_keys = ("max_tokens_per_ticket", "max_cost_microusd_per_ticket",
                "daily_project_cost_microusd")
    preserved_caps = {key: execution_config[key] for key in cap_keys if key in execution_config}
    return {
        "format": "awf-heavy-validation-result-1",
        "status": "PASS" if passed else "FAIL",
        "workload_id": plan["workload_id"],
        "engine": plan["engine"],
        "resource_class": plan["resource_class"],
        "required_resources": list(plan["required_resources"]),
        "plan_sha256": plan_digest,
        "review_sha256": review_digest,
        "reviewer_identity": review["reviewer_identity"],
        "capacity_sha256": capacity_digest,
        "started_at": started_at,
        "ended_at": ended_at,
        "all_partitions_terminal": terminal,
        "execution_authority": False,
        "execution": {
            "mode": "PARALLEL" if effective > 1 else "SERIAL_FALLBACK",
            "requested_parallelism": plan["requested_parallelism"],
            "effective_parallelism": effective,
            "fallback_reasons": reasons,
            "scheduled_partition_count": len(plan["partitions"]),
            "completed_terminal_count": sum(item["state"] in TERMINAL_STATES for item in results),
            "shell": False,
        },
        "partitions": results,
        "aggregate_sha256": fingerprint("heavy-validation-result", stable),
        "preserved_caps": preserved_caps,
        "native_streams": deepcopy(execution_config.get("native_streams")),
    }
