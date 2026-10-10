"""External-resource registry, bounded admission, and evidence claims.

Tracked configuration contains logical aliases only.  Host paths, UNC locators,
and mapping fingerprints belong in an operator-local record outside Git.  The
functions in this module deliberately return aliases and digests, never those
private locator values.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import PurePath
import re
from typing import Callable

from . import ValidationError
from .canonical import sha256


REGISTRY_FORMAT = "awf-external-resource-registry-1"
MAPPING_FORMAT = "awf-external-resource-mapping-1"
RECEIPT_FORMAT = "awf-external-resource-receipt-1"
PUBLIC_EVIDENCE_FORMAT = "awf-external-resource-evidence-1"
SCAN_FORMAT = "awf-external-resource-scan-1"
CLAIM_FORMAT = "awf-external-resource-claim-1"

RESOURCE_STATES = {
    "UNOBSERVED", "SANDBOX_BLOCKED", "PROCESS_START_FAILED",
    "COMMAND_NONZERO", "MAPPING_MISSING", "PATH_NOT_FOUND",
    "ACCESS_DENIED", "INVALID_OUTPUT", "READ_VERIFIED", "STALE",
    "MAPPING_CHANGED",
}
PERMISSION_SCOPES = {"one_command", "task", "application_session", "project"}
READ_ACCESS = {"read", "read_only"}
SENSITIVITIES = {"public", "internal", "restricted", "confidential"}
MAPPING_KINDS = {"local", "mapped_drive", "unc"}
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POWERSHELL_EXIT_STATES = {
    40: ("MAPPING_MISSING", "POWERSHELL_MAPPING"),
    41: ("PATH_NOT_FOUND", "FILESYSTEM_PATH"),
    42: ("ACCESS_DENIED", "WINDOWS_ACCESS"),
    43: ("COMMAND_NONZERO", "COMMAND_EXIT"),
}


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("timestamp must be RFC3339 with an offset") from exc
    else:
        raise ValidationError("timestamp must be RFC3339 text")
    if result.tzinfo is None:
        raise ValidationError("timestamp must include an offset")
    return result.astimezone(timezone.utc)


def _timestamp(value=None):
    return _utc(value).isoformat().replace("+00:00", "Z")


def _text(value, field):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValidationError(field + " must be nonempty trimmed text without control characters")
    return value


def _integer(value, field, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{field} must be an integer from {minimum} through {maximum}")
    return value


def _relative_sample(value, field):
    value = _text(value, field).replace("\\", "/")
    path = PurePath(value)
    if path.is_absolute() or value.startswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(field + " must be a normalized relative file path")
    return value


def validate_registry(registry):
    """Validate and normalize a tracked logical-resource registry."""
    if not isinstance(registry, dict) or registry.get("format") != REGISTRY_FORMAT:
        raise ValidationError("external-resource registry has an unsupported format")
    if set(registry) != {"format", "resources"} or not isinstance(registry["resources"], dict):
        raise ValidationError("external-resource registry requires only format and a resources object")
    normalized = {"format": REGISTRY_FORMAT, "resources": {}}
    for alias, declaration in registry["resources"].items():
        if not isinstance(alias, str) or ALIAS_RE.fullmatch(alias) is None:
            raise ValidationError("resource aliases must match ^[a-z][a-z0-9_]{0,63}$")
        if not isinstance(declaration, dict):
            raise ValidationError(f"resources.{alias} must be an object")
        expected = {"access", "required_by", "host_mapping", "sensitivity", "probe"}
        if set(declaration) != expected:
            raise ValidationError(f"resources.{alias} must contain exactly " + ", ".join(sorted(expected)))
        access = declaration["access"]
        if access not in READ_ACCESS | {"write"}:
            raise ValidationError(f"resources.{alias}.access must be read_only, read, or write")
        if declaration["host_mapping"] != "operator_local":
            raise ValidationError(f"resources.{alias}.host_mapping must be operator_local")
        if declaration["sensitivity"] not in SENSITIVITIES:
            raise ValidationError(f"resources.{alias}.sensitivity is unsupported")
        required_by = declaration["required_by"]
        if not isinstance(required_by, list) or len(set(required_by)) != len(required_by):
            raise ValidationError(f"resources.{alias}.required_by must be a unique array")
        required_by = [_text(item, f"resources.{alias}.required_by") for item in required_by]
        probe = declaration["probe"]
        if not isinstance(probe, dict) or set(probe) != {
                "sample_relative_path", "listing_limit", "timeout_seconds"}:
            raise ValidationError(f"resources.{alias}.probe must define sample_relative_path, listing_limit, and timeout_seconds")
        normalized["resources"][alias] = {
            "access": "read_only" if access in READ_ACCESS else "write",
            "required_by": required_by,
            "host_mapping": "operator_local",
            "sensitivity": declaration["sensitivity"],
            "probe": {
                "sample_relative_path": _relative_sample(
                    probe["sample_relative_path"], f"resources.{alias}.probe.sample_relative_path"),
                "listing_limit": _integer(
                    probe["listing_limit"], f"resources.{alias}.probe.listing_limit", 1, 1000),
                "timeout_seconds": _integer(
                    probe["timeout_seconds"], f"resources.{alias}.probe.timeout_seconds", 1, 30),
            },
        }
    return normalized


def validate_mappings(mappings):
    """Validate private host mappings without returning locator values in errors."""
    if not isinstance(mappings, dict) or mappings.get("format") != MAPPING_FORMAT:
        raise ValidationError("external-resource mapping has an unsupported format")
    if set(mappings) != {"format", "resources"} or not isinstance(mappings["resources"], dict):
        raise ValidationError("external-resource mapping requires only format and a resources object")
    for alias, mapping in mappings["resources"].items():
        if not isinstance(alias, str) or ALIAS_RE.fullmatch(alias) is None:
            raise ValidationError("mapping aliases must use the logical resource alias syntax")
        if not isinstance(mapping, dict) or set(mapping) != {"local_path", "canonical_locator", "mapping_kind"}:
            raise ValidationError(f"mapping for {{{alias}}} must contain local_path, canonical_locator, and mapping_kind")
        _text(mapping["local_path"], f"mapping for {{{alias}}}.local_path")
        _text(mapping["canonical_locator"], f"mapping for {{{alias}}}.canonical_locator")
        if mapping["mapping_kind"] not in MAPPING_KINDS:
            raise ValidationError(f"mapping for {{{alias}}}.mapping_kind is unsupported")
    return mappings


def _mapping_identity(locator):
    normalized = locator.replace("/", "\\").rstrip("\\").casefold()
    return sha256(normalized.encode("utf-8"))


def _command_payload(declaration, mapping):
    """Build the private, exact command. Callers must never persist its argv."""
    probe = declaration["probe"]
    # Explicit exit codes keep mapping, path and access errors separate even on
    # Windows hosts whose localized error messages differ.
    script = r"""
param([string]$root,[string]$sample,[int]$limit,[string]$mappingKind,[string]$canonical)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=(New-Object System.Text.UTF8Encoding($false))
try {
  $observed=$null
  if ($mappingKind -eq 'mapped_drive') {
    $name=[System.IO.Path]::GetPathRoot($root).TrimEnd('\').TrimEnd(':')
    $drive=Get-PSDrive -Name $name -ErrorAction Stop
    if ($null -ne $drive.DisplayRoot -and $drive.DisplayRoot.Length -gt 0) { $observed=$drive.DisplayRoot }
    else { $observed=$drive.Root }
  }
  else { $observed=$canonical }
  $entries=@(Get-ChildItem -LiteralPath $root -Force -ErrorAction Stop | Select-Object -First $limit)
  $target=Join-Path -Path $root -ChildPath $sample
  $item=Get-Item -LiteralPath $target -ErrorAction Stop
  $stream=[System.IO.File]::Open($item.FullName,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite)
  try { $value=$stream.ReadByte() } finally { $stream.Dispose() }
  @{observed_locator=$observed;listing_count=$entries.Count;sample_size=$item.Length;sample_mtime=$item.LastWriteTimeUtc.ToString('o');bytes_read=$(if($value -eq -1){0}else{1})} | ConvertTo-Json -Compress
  exit 0
} catch {
  $code=43; $kindName=$_.Exception.GetType().FullName; $fid=$_.FullyQualifiedErrorId
  if ($kindName -match 'UnauthorizedAccess|SecurityException') { $code=42 }
  elseif ($kindName -match 'DriveNotFound' -or $fid -match 'DriveNotFound|Get-PsDrive') { $code=40 }
  elseif ($kindName -match 'DirectoryNotFound|FileNotFound' -or $fid -match 'PathNotFound|ItemNotFound') { $code=41 }
  $failure=@{error_code=$code}
  if ($null -ne $observed) { $failure.observed_locator=$observed }
  $failure | ConvertTo-Json -Compress
  exit $code
}
""".strip()
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    arguments = (mapping["local_path"], probe["sample_relative_path"],
                 probe["listing_limit"], mapping["mapping_kind"],
                 mapping["canonical_locator"])
    invocation = "& {\n" + script + "\n} " + " ".join(literal(value) for value in arguments)
    return ("powershell", "-NoProfile", "-NonInteractive", "-Command", invocation)


def command_sha256(command):
    encoded = json.dumps(list(command), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded)


def _default_execute(command, *, cwd=None, timeout_seconds=None):
    from .host_preflight import run
    return run(list(command), cwd=cwd, timeout_seconds=timeout_seconds)


def classify_probe_result(result):
    """Classify a trusted-host execution observation at its actual failure layer."""
    if not isinstance(result, dict):
        raise ValidationError("resource probe executor returned no structured observation")
    diagnostic = result.get("diagnostic_category")
    exit_code = result.get("exit_code")
    if diagnostic == "SANDBOX_BLOCKED":
        return "SANDBOX_BLOCKED", "SANDBOX", None
    if exit_code is None:
        return "PROCESS_START_FAILED", "PROCESS_START", None
    observation = result.get("probe")
    if observation is None:
        try:
            observation = json.loads(result.get("output", ""))
        except (json.JSONDecodeError, TypeError):
            observation = None
    mapping_observation = None
    if (isinstance(observation, dict) and
            isinstance(observation.get("observed_locator"), str) and
            observation["observed_locator"]):
        mapping_observation = {"observed_locator": observation["observed_locator"]}
    if exit_code in POWERSHELL_EXIT_STATES:
        state, layer = POWERSHELL_EXIT_STATES[exit_code]
        return state, layer, mapping_observation
    if exit_code != 0:
        if diagnostic == "ACCESS_DENIED":
            return "ACCESS_DENIED", "WINDOWS_ACCESS", mapping_observation
        return "COMMAND_NONZERO", "COMMAND_EXIT", mapping_observation
    required = {"observed_locator", "listing_count", "sample_size", "sample_mtime", "bytes_read"}
    if not isinstance(observation, dict) or not required.issubset(observation):
        return "INVALID_OUTPUT", "COMMAND_OUTPUT", None
    if (type(observation["listing_count"]) is not int or observation["listing_count"] < 0 or
            type(observation["sample_size"]) is not int or observation["sample_size"] < 0 or
            observation["bytes_read"] not in {0, 1} or
            not isinstance(observation["observed_locator"], str)):
        return "INVALID_OUTPUT", "COMMAND_OUTPUT", None
    return "READ_VERIFIED", "READ_PROBE", observation


def _attempt(result, digest, attempted_at):
    state, layer, observation = classify_probe_result(result)
    return {
        "attempted_at": _timestamp(attempted_at),
        "command_sha256": digest,
        "state": state,
        "error_layer": layer,
        "exit_code": result.get("exit_code"),
    }, observation


def _permission(scope, expires_at, source):
    if scope not in PERMISSION_SCOPES:
        raise ValidationError("permission scope is unsupported")
    return {
        "access": "read_only",
        "scope": scope,
        "expires_at": _timestamp(expires_at) if expires_at is not None else None,
        "source": source,
    }


def admit_resource(registry, mappings, resource_alias, *, task_id, principal, session_id,
                   execute: Callable | None = None, approval: Callable | None = None,
                   permission_scope="task", permission_expires_at=None, now=None, cwd=None):
    """Run a bounded per-task read admission and return an operator-local receipt.

    If a host reports SANDBOX_BLOCKED, ``approval`` is invoked at most once with
    a narrow read-only request.  An approval causes the identical command tuple
    to be retried; both attempts carry the same command digest.
    """
    registry = validate_registry(registry)
    mappings = validate_mappings(mappings)
    alias = _text(resource_alias, "resource_alias")
    task_id = _text(task_id, "task_id")
    principal = _text(principal, "principal")
    session_id = _text(session_id, "session_id")
    declaration = registry["resources"].get(alias)
    if declaration is None:
        raise ValidationError(f"resource {{{alias}}} is not declared")
    if declaration["access"] != "read_only":
        raise ValidationError(f"resource {{{alias}}} requests write access; admission probes are read-only")
    mapping = mappings["resources"].get(alias)
    if mapping is None:
        raise ValidationError(f"operator-local mapping for {{{alias}}} is missing")
    observed_at = _utc(now)
    command = _command_payload(declaration, mapping)
    digest = command_sha256(command)
    if execute is None:
        runner = lambda value, cwd=None: _default_execute(
            value, cwd=cwd, timeout_seconds=declaration["probe"]["timeout_seconds"])
    else:
        runner = execute
    first_result = runner(command, cwd=cwd)
    first, observation = _attempt(first_result, digest, observed_at)
    attempts = [first]
    permission = _permission(permission_scope, permission_expires_at, "existing_grant")
    if first["state"] == "SANDBOX_BLOCKED" and approval is not None:
        request = {
            "resource_alias": alias,
            "access": "read_only",
            "command_sha256": digest,
            "scope_requested": "one_command",
        }
        decision = approval(command, request)
        if not isinstance(decision, dict) or decision.get("approved") is not True:
            decision = None
        if decision is not None:
            if decision.get("access") != "read_only" or decision.get("command_sha256") != digest:
                raise ValidationError("approval must bind read_only access to the exact command digest")
            retry_command = _command_payload(declaration, mapping)
            if retry_command != command or command_sha256(retry_command) != digest:
                raise ValidationError("exact-command retry changed after approval")
            retry_time = _utc(decision.get("approved_at", observed_at))
            retry_result = runner(retry_command, cwd=cwd)
            retried, observation = _attempt(retry_result, digest, retry_time)
            attempts.append(retried)
            permission = _permission(decision.get("scope", "one_command"),
                                     decision.get("expires_at"), "narrow_approval")
    final = attempts[-1]
    read_verified = final["state"] == "READ_VERIFIED"
    expected_mapping = _mapping_identity(mapping["canonical_locator"])
    actual_mapping = None
    if observation is not None and isinstance(observation.get("observed_locator"), str):
        actual_mapping = _mapping_identity(observation["observed_locator"])
        if actual_mapping != expected_mapping:
            final = dict(final, state="MAPPING_CHANGED", error_layer="MAPPING_IDENTITY")
            attempts[-1] = final
    if read_verified:
        evidence = {
            "listing_limit": declaration["probe"]["listing_limit"],
            "listing_count": observation["listing_count"],
            "sample_metadata_observed": True,
            "bytes_read": observation["bytes_read"],
            "accessibility": "CONFIRMED" if observation["bytes_read"] == 1 else "UNCONFIRMED",
            "data_validity": "UNCONFIRMED",
        }
    else:
        evidence = {
            "listing_limit": declaration["probe"]["listing_limit"],
            "listing_count": None,
            "sample_metadata_observed": False,
            "bytes_read": 0,
            "accessibility": "UNCONFIRMED",
            "data_validity": "UNCONFIRMED",
        }
    receipt_core = {
        "resource_alias": alias,
        "task_id": task_id,
        "principal": principal,
        "session_id": session_id,
        "observed_at": final["attempted_at"],
        "permission": permission,
        "state": final["state"],
        "error_layer": final["error_layer"],
        "command_sha256": digest,
    }
    receipt = {
        "format": RECEIPT_FORMAT,
        "visibility": "operator_local",
        **receipt_core,
        "attempts": attempts,
        "evidence": evidence,
        "private_mapping_fingerprint": actual_mapping or expected_mapping,
        "execution_authority": False,
    }
    receipt["receipt_id"] = _receipt_identity(receipt)
    return receipt


def public_evidence(receipt):
    """Return the deliberately small tracked view of a private receipt."""
    _validate_receipt_shape(receipt)
    return {
        "format": PUBLIC_EVIDENCE_FORMAT,
        "resource_alias": receipt["resource_alias"],
        "observed_at": receipt["observed_at"],
        "result": receipt["state"],
        "execution_authority": False,
    }


def _receipt_identity(receipt):
    bound = {key: value for key, value in receipt.items() if key != "receipt_id"}
    return sha256(json.dumps(bound, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _validate_receipt_shape(receipt):
    if not isinstance(receipt, dict) or receipt.get("format") != RECEIPT_FORMAT:
        raise ValidationError("external-resource receipt has an unsupported format")
    required = {
        "format", "visibility", "receipt_id", "resource_alias", "task_id", "principal",
        "session_id", "observed_at", "permission", "state", "error_layer",
        "command_sha256", "attempts", "evidence", "private_mapping_fingerprint",
        "execution_authority",
    }
    if set(receipt) != required:
        raise ValidationError("external-resource receipt is incomplete or has unknown fields")
    if receipt["visibility"] != "operator_local" or receipt["execution_authority"] is not False:
        raise ValidationError("external-resource receipt has an invalid trust boundary")
    alias = receipt["resource_alias"]
    if not isinstance(alias, str) or ALIAS_RE.fullmatch(alias) is None:
        raise ValidationError("external-resource receipt has an invalid resource alias")
    for field in ("task_id", "principal", "session_id", "error_layer"):
        _text(receipt[field], "receipt." + field)
    _utc(receipt["observed_at"])
    if receipt.get("state") not in RESOURCE_STATES:
        raise ValidationError("external-resource receipt has an unsupported state")
    for field in ("receipt_id", "command_sha256", "private_mapping_fingerprint"):
        if not isinstance(receipt[field], str) or SHA256_RE.fullmatch(receipt[field]) is None:
            raise ValidationError("external-resource receipt has an invalid " + field)
    permission = receipt.get("permission")
    if (not isinstance(permission, dict) or
            set(permission) != {"access", "scope", "expires_at", "source"} or
            permission.get("access") != "read_only" or
            permission.get("scope") not in PERMISSION_SCOPES or
            permission.get("source") not in {"existing_grant", "narrow_approval"}):
        raise ValidationError("external-resource receipt has no valid permission scope")
    if permission["expires_at"] is not None:
        _utc(permission["expires_at"])
    attempts = receipt.get("attempts")
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
        raise ValidationError("external-resource receipt must contain one or two attempts")
    for attempt in attempts:
        if (not isinstance(attempt, dict) or
                set(attempt) != {"attempted_at", "command_sha256", "state", "error_layer", "exit_code"}):
            raise ValidationError("external-resource receipt has a malformed attempt")
        _utc(attempt["attempted_at"])
        _text(attempt["error_layer"], "receipt.attempt.error_layer")
        if (attempt["command_sha256"] != receipt["command_sha256"] or
                attempt["state"] not in RESOURCE_STATES or
                (attempt["exit_code"] is not None and type(attempt["exit_code"]) is not int)):
            raise ValidationError("external-resource receipt attempt is not bound to the probe")
    if len(attempts) == 2 and (
            attempts[0]["state"] != "SANDBOX_BLOCKED" or
            permission["source"] != "narrow_approval"):
        raise ValidationError("external-resource receipt retry is not bound to a narrow approval")
    if permission["source"] == "narrow_approval" and len(attempts) != 2:
        raise ValidationError("external-resource narrow approval has no exact-command retry")
    final = attempts[-1]
    if (receipt["observed_at"] != final["attempted_at"] or
            receipt["state"] != final["state"] or
            receipt["error_layer"] != final["error_layer"]):
        raise ValidationError("external-resource receipt does not match its final attempt")
    evidence = receipt.get("evidence")
    evidence_fields = {"listing_limit", "listing_count", "sample_metadata_observed",
                       "bytes_read", "accessibility", "data_validity"}
    if not isinstance(evidence, dict) or set(evidence) != evidence_fields:
        raise ValidationError("external-resource receipt has malformed probe evidence")
    _integer(evidence["listing_limit"], "receipt.evidence.listing_limit", 1, 1000)
    if (evidence["listing_count"] is not None and
            (type(evidence["listing_count"]) is not int or evidence["listing_count"] < 0)):
        raise ValidationError("external-resource receipt has invalid listing evidence")
    if (type(evidence["sample_metadata_observed"]) is not bool or
            type(evidence["bytes_read"]) is not int or evidence["bytes_read"] not in {0, 1} or
            evidence["accessibility"] not in {"CONFIRMED", "UNCONFIRMED"} or
            evidence["data_validity"] != "UNCONFIRMED"):
        raise ValidationError("external-resource receipt has inconsistent probe evidence")
    expected_accessibility = "CONFIRMED" if evidence["bytes_read"] == 1 else "UNCONFIRMED"
    if evidence["accessibility"] != expected_accessibility:
        raise ValidationError("external-resource receipt accessibility exceeds its read evidence")
    successful_read = final["exit_code"] == 0 and receipt["state"] in {
        "READ_VERIFIED", "MAPPING_CHANGED"}
    if successful_read:
        if evidence["listing_count"] is None or evidence["sample_metadata_observed"] is not True:
            raise ValidationError("external-resource successful read lacks bounded probe evidence")
    elif (evidence["listing_count"] is not None or
          evidence["sample_metadata_observed"] is not False or
          evidence["bytes_read"] != 0):
        raise ValidationError("external-resource failed read claims probe evidence it did not collect")
    if receipt["receipt_id"] != _receipt_identity(receipt):
        raise ValidationError("external-resource receipt identity does not match its complete contents")
    return receipt


def _validate_admission_binding(receipt, *, expected_receipt_sha256, registry, mappings):
    """Bind a receipt to a separately trusted host pin and the exact probe."""
    receipt = _validate_receipt_shape(receipt)
    if (not isinstance(expected_receipt_sha256, str) or
            SHA256_RE.fullmatch(expected_receipt_sha256) is None):
        raise ValidationError("a separately trusted receipt digest is required for admission")
    if receipt["receipt_id"] != expected_receipt_sha256:
        raise ValidationError("external-resource receipt does not match the trusted receipt digest")
    registry = validate_registry(registry)
    mappings = validate_mappings(mappings)
    alias = receipt["resource_alias"]
    declaration = registry["resources"].get(alias)
    if declaration is None:
        raise ValidationError(f"resource {{{alias}}} is not declared by the trusted registry")
    if declaration["access"] != "read_only":
        raise ValidationError(f"resource {{{alias}}} is not declared read-only")
    mapping = mappings["resources"].get(alias)
    if mapping is None:
        raise ValidationError(f"operator-local mapping for {{{alias}}} is missing")
    expected_mapping = _mapping_identity(mapping["canonical_locator"])
    mapping_changed = receipt["private_mapping_fingerprint"] != expected_mapping
    expected_command = command_sha256(_command_payload(declaration, mapping))
    if receipt["command_sha256"] != expected_command:
        if mapping_changed:
            return receipt, "MAPPING_CHANGED"
        raise ValidationError("external-resource receipt is not bound to the declared probe and mapping")
    if mapping_changed:
        return receipt, "MAPPING_CHANGED"
    if receipt["evidence"]["listing_limit"] != declaration["probe"]["listing_limit"]:
        raise ValidationError("external-resource receipt is not bound to the declared probe bounds")
    return receipt, None


def admission_decision(receipt, *, resource_alias, task_id, principal, session_id,
                       expected_receipt_sha256=None, registry=None, mappings=None,
                       now=None):
    """Decide dispatch admission from host-pinned evidence before work starts."""
    receipt, reason = _validate_admission_binding(
        receipt, expected_receipt_sha256=expected_receipt_sha256,
        registry=registry, mappings=mappings)
    reference = _utc(now)
    state = receipt["state"]
    if reason is None and state != "READ_VERIFIED":
        reason = state
    elif reason is None and receipt["resource_alias"] != resource_alias:
        reason = "RESOURCE_MISMATCH"
    elif reason is None and receipt["task_id"] != task_id:
        reason = "STALE"
    elif reason is None and receipt["principal"] != principal:
        reason = "STALE"
    elif reason is None and receipt["session_id"] != session_id:
        reason = "STALE"
    elif reason is None and receipt["permission"]["scope"] == "one_command":
        reason = "STALE"
    elif reason is None:
        expires_at = receipt["permission"].get("expires_at")
        if expires_at is not None and reference >= _utc(expires_at):
            reason = "STALE"
    return {
        "status": "ADMITTED" if reason is None else "REFUSED",
        "resource_alias": resource_alias,
        "state": "READ_VERIFIED" if reason is None else reason,
        "receipt_id": receipt.get("receipt_id"),
        "before_work_started": True,
        "execution_authority": False,
    }


def require_admissions(required_aliases, receipts, *, task_id, principal, session_id,
                       expected_receipt_digests, registry, mappings, now=None):
    """Return one fail-closed dispatch decision for all required resources."""
    if not isinstance(required_aliases, list) or not all(isinstance(x, str) for x in required_aliases):
        raise ValidationError("required resources must be an array of aliases")
    if not isinstance(expected_receipt_digests, dict):
        raise ValidationError("trusted receipt digests must be supplied by resource alias")
    by_alias = {item.get("resource_alias"): item for item in receipts if isinstance(item, dict)}
    decisions = []
    for alias in required_aliases:
        receipt = by_alias.get(alias)
        if receipt is None:
            decisions.append({"status": "REFUSED", "resource_alias": alias,
                              "state": "UNOBSERVED", "receipt_id": None,
                              "before_work_started": True, "execution_authority": False})
        else:
            decisions.append(admission_decision(
                receipt, resource_alias=alias, task_id=task_id, principal=principal,
                session_id=session_id, expected_receipt_sha256=expected_receipt_digests.get(alias),
                registry=registry, mappings=mappings, now=now))
    return {
        "status": "ADMITTED" if all(item["status"] == "ADMITTED" for item in decisions) else "REFUSED",
        "before_work_started": True,
        "resources": decisions,
        "execution_authority": False,
    }


def validate_scan_request(request):
    """Refuse unbounded estate scans and return the exact accepted bounds."""
    if not isinstance(request, dict):
        raise ValidationError("external-resource scan request must be an object")
    expected = {"resource_alias", "recursive", "max_depth", "file_limit", "date_range", "timeout_seconds"}
    if set(request) != expected:
        raise ValidationError("external-resource scans require recursive, max_depth, file_limit, date_range, and timeout_seconds bounds")
    alias = _text(request["resource_alias"], "resource_alias")
    if type(request["recursive"]) is not bool:
        raise ValidationError("recursive must be boolean")
    depth = _integer(request["max_depth"], "max_depth", 0, 32)
    if request["recursive"] and depth == 0:
        raise ValidationError("UNBOUNDED_SCAN: a recursive scan requires an explicit positive max_depth")
    limit = _integer(request["file_limit"], "file_limit", 1, 100000)
    timeout = _integer(request["timeout_seconds"], "timeout_seconds", 1, 3600)
    dates = request["date_range"]
    if not isinstance(dates, dict) or set(dates) != {"start", "end"}:
        raise ValidationError("date_range requires explicit start and end dates")
    try:
        start = datetime.strptime(dates["start"], "%Y-%m-%d").date()
        end = datetime.strptime(dates["end"], "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValidationError("date_range start and end must be YYYY-MM-DD") from exc
    if end < start:
        raise ValidationError("date_range end must not precede start")
    return {
        "format": SCAN_FORMAT,
        "status": "BOUNDED_SCAN_ACCEPTED",
        "resource_alias": alias,
        "bounds": {"recursive": request["recursive"], "max_depth": depth,
                   "file_limit": limit, "date_range": request["date_range"],
                   "timeout_seconds": timeout},
        "execution_authority": False,
    }


def validate_claim(receipt, claim):
    """Accept only claims supported by the stated observation."""
    _validate_receipt_shape(receipt)
    if not isinstance(claim, dict) or set(claim) != {"kind", "value", "supporting_evidence"}:
        raise ValidationError("claim requires kind, value, and supporting_evidence")
    kind = claim["kind"]
    value = claim["value"]
    supporting = claim["supporting_evidence"]
    if kind not in {"accessibility", "data_validity", "completeness", "absence"} or value not in {"CONFIRMED", "UNCONFIRMED"}:
        raise ValidationError("claim kind or value is unsupported")
    if not isinstance(supporting, list) or not all(isinstance(item, str) for item in supporting):
        raise ValidationError("supporting_evidence must be an array of evidence labels")
    accepted = False
    reason = "CLAIM_EXCEEDS_OBSERVATION"
    if value == "UNCONFIRMED":
        accepted, reason = True, None
    elif kind == "accessibility":
        accepted = receipt["state"] == "READ_VERIFIED" and receipt.get("evidence", {}).get("bytes_read") == 1
        reason = None if accepted else "READ_NOT_VERIFIED"
    elif kind == "data_validity":
        # Labels supplied beside an access receipt are assertions, not the
        # separately validated schema/provenance/manifest/hash/quality records.
        # This admission layer therefore never promotes validity to confirmed.
        reason = "SEPARATE_VALIDATION_REQUIRED"
    # A bounded listing or read never proves estate-wide completeness/absence.
    result = {
        "format": CLAIM_FORMAT,
        "status": "CLAIM_ACCEPTED" if accepted else "CLAIM_REJECTED",
        "resource_alias": receipt["resource_alias"],
        "kind": kind,
        "value": value,
        "reason": reason,
        "execution_authority": False,
    }
    return result


def split_root_policy(registry):
    """Describe workspace roles without treating read-only data as writable."""
    registry = validate_registry(registry)
    writable = sorted(alias for alias, value in registry["resources"].items()
                      if value["access"] == "write")
    return {
        "status": "UNSUPPORTED_SPLIT_WRITABLE_ROOTS" if writable else "SUPPORTED",
        "repository_role": "writable",
        "external_read_only": sorted(alias for alias, value in registry["resources"].items()
                                     if value["access"] == "read_only"),
        "external_write_requested": writable,
        "remedy": ("Keep the repository as the single writable workspace root; declare the external data estate read_only and use a separately governed output root."
                   if writable else None),
    }
