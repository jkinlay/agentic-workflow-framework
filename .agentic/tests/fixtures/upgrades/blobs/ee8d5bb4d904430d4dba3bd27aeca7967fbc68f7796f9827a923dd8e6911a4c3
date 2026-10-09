"""Fail-closed validation for non-disclosing private deny scan receipts.

The matcher and its mapping can remain operator-private. This module accepts
only a closed receipt and a separately pinned authorization supplied by the
trusted controller; it never receives or emits matched values.
"""
from __future__ import annotations

import re
from typing import Any, Pattern

from . import ValidationError
from .canonical import MAX_DOCUMENT_BYTES, loads, sha256

RECEIPT_FORMAT = "awf-private-deny-scan-receipt-1"
AUTHORIZATION_FORMAT = "awf-private-deny-baseline-authorization-1"
SAFE_PATH = re.compile(r"[A-Za-z0-9._ -]+(?:/[A-Za-z0-9._ -]+)*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_PRIVATE_BLOB_BYTES = 8 * 1024 * 1024


def scan_private_deny_blob(content: bytes, matcher: Pattern[str]) -> dict[str, int]:
    """Scan raw bytes and every supported decoded view without exposing hits.

    The configured expression is compiled as UTF-8 bytes and applied directly
    to the blob, then applied to strict UTF-8 (or replacement UTF-8), Latin-1,
    and both replacement-decoded UTF-16 byte orders for null-heavy content.
    Each view is scanned independently; hits are namespaced and accumulated,
    never overwritten. If any matcher or view cannot be processed, the caller
    must treat the blob as unscanned and blocking.
    """
    if not isinstance(content, bytes) or len(content) > MAX_PRIVATE_BLOB_BYTES:
        raise ValidationError("Private-deny blob is invalid or exceeds 8 MiB")

    if not isinstance(getattr(matcher, "pattern", None), str):
        raise ValidationError("Private-deny matcher must be a text regular expression")
    allowed_byte_flags = re.IGNORECASE | re.MULTILINE | re.DOTALL | re.VERBOSE
    allowed_text_flags = allowed_byte_flags | re.UNICODE | re.ASCII
    flags = getattr(matcher, "flags", 0)
    if type(flags) is not int or flags & ~allowed_text_flags:
        raise ValidationError("Private-deny matcher uses unsupported regular-expression flags")
    try:
        byte_matcher = re.compile(matcher.pattern.encode("utf-8", errors="strict"),
                                  flags & allowed_byte_flags)
    except (UnicodeError, re.error, TypeError, ValueError) as exc:
        raise ValidationError("Private-deny matcher has no supported byte representation") from exc

    def add_matches(values: dict[str, int], matches, view: str, *, is_bytes: bool) -> None:
        for match in matches:
            matched = match.group(0)
            value_bytes = (matched if is_bytes else matched.encode("utf-8", errors="strict"))
            value_sha = sha256(value_bytes)
            key = sha256(f"{view}:{value_sha}".encode("ascii"))
            values[key] = values.get(key, 0) + 1

    def matches_text(text: str, view: str, values: dict[str, int]) -> None:
        add_matches(values, matcher.finditer(text), view, is_bytes=False)

    def matches_bytes(blob: bytes, view: str, values: dict[str, int]) -> None:
        add_matches(values, byte_matcher.finditer(blob), view, is_bytes=True)

    values: dict[str, int] = {}
    try:
        # The byte view is mandatory even when one or more decoders succeed.
        matches_bytes(content, "raw-bytes", values)

        try:
            text = content.decode("utf-8", errors="strict")
            matches_text(text, "utf8-strict", values)
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
            matches_text(text, "utf8-replacement", values)

        matches_text(content.decode("latin-1", errors="strict"), "latin1", values)

        null_heavy = content.count(b"\x00") * 4 > len(content)
        if null_heavy:
            matches_text(content.decode("utf-16-le", errors="replace"),
                         "utf16-le-replacement", values)
            matches_text(content.decode("utf-16-be", errors="replace"),
                         "utf16-be-replacement", values)
    except (TimeoutError, UnicodeError, re.error, TypeError, ValueError) as exc:
        # Do not return partial hits as a completed scan. The caller records
        # this path as unscanned, which remains blocking.
        raise ValidationError("Private-deny blob scan was incomplete") from exc

    return values


def _exact(value: Any, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"{label} has missing or unsupported fields")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _count(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise ValidationError(f"{label} must be an integer from 0 to 1000000")
    return value


def _candidate(value: Any, expected: dict, label: str) -> None:
    if value != expected:
        raise ValidationError(f"{label} differs from the frozen candidate")


def validate_private_deny_receipt(
    raw: bytes,
    *,
    expected_receipt_sha256: str,
    candidate: dict,
    expected_mapping_sha256: str,
    expected_scanner_sha256: str,
    baseline_authorization_raw: bytes | None = None,
    expected_baseline_authorization_sha256: str | None = None,
) -> dict:
    """Validate a zero-match pass or an authorized unchanged-tree baseline.

    The expected authorization digest is a trust input from the controller's
    human authorization channel. Hashing an authorization document alone does
    not authenticate its author; callers must independently pin that digest.
    """
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Private-deny receipt exceeds 8 MiB")
    receipt_sha = _sha(expected_receipt_sha256, "expected receipt SHA-256")
    if sha256(raw) != receipt_sha:
        raise ValidationError("Private-deny receipt bytes differ from their expected digest")
    mapping_sha = _sha(expected_mapping_sha256, "expected mapping SHA-256")
    scanner_sha = _sha(expected_scanner_sha256, "expected scanner SHA-256")
    try:
        receipt = loads(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ValidationError("Private-deny receipt is not strict UTF-8") from exc
    receipt = _exact(receipt, {
        "format", "policy_id", "status", "classification", "candidate",
        "provider_pr_body_sha256", "mapping_sha256", "scanner_sha256", "counts",
        "paths", "unscanned", "scan_complete", "baseline_authorization_sha256",
        "private_match_values_included", "execution_authority",
    }, "Private-deny receipt")
    if (receipt["format"] != RECEIPT_FORMAT
            or receipt["policy_id"] != "exact-current-tree-baseline-v1"
            or receipt["status"] != "PASS"):
        raise ValidationError("Private-deny receipt is not an accepted PASS policy receipt")
    _candidate(receipt["candidate"], candidate, "Receipt candidate")
    body_sha = _sha(candidate.get("provider_pr_body_sha256"), "candidate body SHA-256")
    if _sha(receipt["provider_pr_body_sha256"], "receipt body SHA-256") != body_sha:
        raise ValidationError("Private-deny receipt body differs from the frozen candidate")
    if _sha(receipt["mapping_sha256"], "receipt mapping SHA-256") != mapping_sha:
        raise ValidationError("Private-deny receipt mapping differs from the trusted mapping pin")
    if _sha(receipt["scanner_sha256"], "receipt scanner SHA-256") != scanner_sha:
        raise ValidationError("Private-deny receipt scanner differs from the trusted scanner pin")
    if receipt["scan_complete"] is not True or receipt["unscanned"] != []:
        raise ValidationError("Private-deny receipt has incomplete or unscanned inputs")
    if receipt["private_match_values_included"] is not False:
        raise ValidationError("Private-deny receipt must not expose matched values")
    if receipt["execution_authority"] is not False:
        raise ValidationError("Private-deny receipt cannot claim execution authority")

    counts = _exact(receipt["counts"], {
        "history_matches", "diff_added_matches", "diff_deleted_matches",
        "body_matches", "base_matches", "head_matches",
    }, "Private-deny counts")
    for name, value in counts.items():
        _count(value, f"counts.{name}")
    paths = receipt["paths"]
    if not isinstance(paths, list):
        raise ValidationError("Private-deny paths must be an array")
    previous = None
    base_sum = head_sum = 0
    for index, row in enumerate(paths):
        row = _exact(row, {"path", "base_count", "head_count", "multiset_equal"},
                     f"paths[{index}]")
        path = row["path"]
        if (not isinstance(path, str) or len(path) > 512 or SAFE_PATH.fullmatch(path) is None
                or ".." in path.split("/") or (previous is not None and path <= previous)):
            raise ValidationError("Private-deny paths must be safe, unique, sorted repository paths")
        previous = path
        base_count = _count(row["base_count"], f"paths[{index}].base_count")
        head_count = _count(row["head_count"], f"paths[{index}].head_count")
        if (base_count == 0 or head_count == 0 or base_count != head_count
                or row["multiset_equal"] is not True):
            raise ValidationError("Private-deny path has a new, deleted, or changed match multiset")
        base_sum += base_count
        head_sum += head_count
    if base_sum != counts["base_matches"] or head_sum != counts["head_matches"]:
        raise ValidationError("Per-path private-deny counts do not equal receipt totals")
    if any(counts[key] for key in (
        "history_matches", "diff_added_matches", "diff_deleted_matches", "body_matches",
    )):
        raise ValidationError("Private-deny history, diff, and body channels must be clear")

    classification = receipt["classification"]
    if classification == "NO_MATCHES":
        if counts["base_matches"] or counts["head_matches"] or paths:
            raise ValidationError("NO_MATCHES receipt contains tree matches")
        if receipt["baseline_authorization_sha256"] is not None:
            raise ValidationError("NO_MATCHES receipt must not carry baseline authorization")
        return receipt
    if classification != "BASE_PREEXISTING_UNCHANGED":
        raise ValidationError("Unsupported private-deny classification")
    if (counts["head_matches"] == 0 or not paths
            or counts["base_matches"] != counts["head_matches"]):
        raise ValidationError("Baseline classification requires unchanged nonzero tree matches")
    if baseline_authorization_raw is None or expected_baseline_authorization_sha256 is None:
        raise ValidationError("Baseline classification requires separately pinned authorization")
    if len(baseline_authorization_raw) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Baseline authorization exceeds 8 MiB")
    auth_sha = _sha(expected_baseline_authorization_sha256,
                    "expected baseline authorization SHA-256")
    if sha256(baseline_authorization_raw) != auth_sha:
        raise ValidationError("Baseline authorization differs from its trusted digest")
    if _sha(receipt["baseline_authorization_sha256"],
            "receipt baseline authorization SHA-256") != auth_sha:
        raise ValidationError("Baseline authorization is not bound into the scan receipt")
    try:
        authorization = loads(baseline_authorization_raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ValidationError("Baseline authorization is not strict UTF-8") from exc
    authorization = _exact(authorization, {
        "format", "candidate", "provider_pr_body_sha256", "mapping_sha256",
        "authorized_by", "authorization_ref",
    }, "Baseline authorization")
    if authorization["format"] != AUTHORIZATION_FORMAT:
        raise ValidationError("Unsupported baseline authorization format")
    _candidate(authorization["candidate"], candidate, "Authorization candidate")
    if _sha(authorization["provider_pr_body_sha256"], "authorization body SHA-256") != body_sha:
        raise ValidationError("Baseline authorization body differs from the candidate")
    if _sha(authorization["mapping_sha256"], "authorization mapping SHA-256") != mapping_sha:
        raise ValidationError("Baseline authorization mapping differs from the trusted mapping pin")
    for key in ("authorized_by", "authorization_ref"):
        value = authorization[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValidationError(f"Baseline authorization {key} is required")
    return receipt
