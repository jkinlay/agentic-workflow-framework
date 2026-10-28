"""Exact registered SQLite fixture scanning for publication safety.

Only the closed registered identity (.agentic-state/routing/ledger.sqlite at the
pinned SHA-256) may leave the binary/unscanned path. Validation is fail-closed:
encoding, schema, types, size, integrity, virtual tables and BLOB values all
refuse. Logical TEXT (overflow joined by SQLite) and the full raw Latin-1 /
UTF-8-surrogateescape views are returned so slack bytes cannot hide deny
tokens. Unknown or changed binaries stay unscanned.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from . import ValidationError

REGISTERED_PATH = ".agentic-state/routing/ledger.sqlite"
REGISTERED_SHA256 = "c0b83f21f0e18cea859ff62ec769f2f48849ad50efbf67ffef9d6cb6ae5f98dd"
REGISTERED_SIZE = 24576
FIXTURE_BLOB_PREFIX = ".agentic/tests/fixtures/upgrades/blobs/"
SQLITE_HEADER = b"SQLite format 3\x00"
_ALLOWED_TABLES = frozenset({
    "model_runs",
    "model_failure_resolutions",
    "model_defect_observations",
    "model_disposition_requests",
    "model_reconciliations",
    "model_retry_ceilings",
    "model_retry_limit_authorizations",
    "model_retry_approval_consumptions",
    "model_retry_ceiling_events",
    "sqlite_sequence",
})
_TEXT_AFFINITY = re.compile(r"(CHAR|CLOB|TEXT)", re.IGNORECASE)
_BLOB_AFFINITY = re.compile(r"BLOB", re.IGNORECASE)
_INT_AFFINITY = re.compile(r"(INT|BOOL)", re.IGNORECASE)


def content_addressed_blob_path(sha256: str) -> str:
    return FIXTURE_BLOB_PREFIX + sha256


def registered_fixture_identity(path: str, sha256: str):
    """Return the registered state path when path+digest match the closed identity."""
    if not isinstance(path, str) or not isinstance(sha256, str):
        return None
    if sha256 != REGISTERED_SHA256:
        return None
    if path == REGISTERED_PATH:
        return REGISTERED_PATH
    if path == content_addressed_blob_path(REGISTERED_SHA256):
        return REGISTERED_PATH
    return None


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _validate_schema_and_types(connection: sqlite3.Connection) -> None:
    encoding = connection.execute("PRAGMA encoding").fetchone()
    if encoding is None or encoding[0] != "UTF-8":
        raise ValidationError("registered SQLite fixture encoding must be strict UTF-8")
    quick = connection.execute("PRAGMA quick_check").fetchone()
    if quick is None or quick[0] != "ok":
        raise ValidationError("registered SQLite fixture failed integrity check")
    master = list(connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"))
    for kind, name, table, sql in master:
        if kind == "table" and isinstance(sql, str) and sql.lstrip().upper().startswith("CREATE VIRTUAL"):
            raise ValidationError("registered SQLite fixture refuses virtual tables")
        if kind == "table" and name not in _ALLOWED_TABLES:
            raise ValidationError("registered SQLite fixture has unexpected table")
    tables = [name for kind, name, _table, _sql in master if kind == "table"]
    if set(tables) - _ALLOWED_TABLES:
        raise ValidationError("registered SQLite fixture has unexpected table")
    if "model_runs" not in tables:
        raise ValidationError("registered SQLite fixture missing model_runs")
    for name in tables:
        if name.startswith("sqlite_"):
            continue
        for _cid, column, declared, _notnull, _default, _pk in connection.execute(
                f"PRAGMA table_info({_quote(name)})"):
            declared = declared or ""
            if _BLOB_AFFINITY.search(declared) and not _TEXT_AFFINITY.search(declared):
                raise ValidationError("registered SQLite fixture refuses BLOB columns")
            if declared and not (_TEXT_AFFINITY.search(declared) or _INT_AFFINITY.search(declared)
                                 or declared.upper() in {"NUMERIC", "REAL", "FLOAT", "DOUBLE"}):
                # Fail closed on unfamiliar declared types.
                raise ValidationError("registered SQLite fixture has unsupported column type")
    # No BLOB-typed cell values anywhere.
    for name in tables:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(name)})")]
        if not columns:
            continue
        selects = ", ".join(f"typeof({_quote(column)})" for column in columns)
        for row in connection.execute(f"SELECT {selects} FROM {_quote(name)}"):
            if any(value == "blob" for value in row):
                raise ValidationError("registered SQLite fixture refuses BLOB values")


def _logical_text_views(connection: sqlite3.Connection) -> list[str]:
    views = []
    for kind, name, table, sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"):
        for part in (kind, name, table, sql or ""):
            if isinstance(part, str) and part:
                views.append(part)
    for kind, name, _table, _sql in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        info = list(connection.execute(f"PRAGMA table_info({_quote(name)})"))
        text_columns = [column for _cid, column, declared, *_rest in info
                        if _TEXT_AFFINITY.search(declared or "TEXT")]
        if not text_columns:
            continue
        quoted = ", ".join(_quote(column) for column in text_columns)
        for row in connection.execute(f"SELECT {quoted} FROM {_quote(name)}"):
            for value in row:
                if isinstance(value, str):
                    views.append(value)
                elif value is not None:
                    raise ValidationError("registered SQLite fixture TEXT column returned non-text")
    return views


def validate_registered_sqlite_fixture(raw: bytes, *, path: str, sha256: str | None = None):
    """Validate the closed fixture and return scannable text views.

    Returns (views, None) on success where views is a list of strings to scan.
    Returns (None, reason) when the bytes must remain blocked/unscanned.
    """
    if not isinstance(raw, (bytes, bytearray)):
        return None, "malformed"
    raw = bytes(raw)
    digest = sha256 or hashlib.sha256(raw).hexdigest()
    if registered_fixture_identity(path, digest) is None:
        return None, "unregistered-binary"
    if len(raw) != REGISTERED_SIZE:
        return None, "budget-overrun" if len(raw) > REGISTERED_SIZE else "size-mismatch"
    if digest != REGISTERED_SHA256:
        return None, "changed-binary"
    if not raw.startswith(SQLITE_HEADER):
        return None, "malformed"
    # UTF-16 database files use a different header encoding marker; refuse BOM-like
    # page-size patterns that indicate UTF-16 before deserialize.
    page_size = int.from_bytes(raw[16:18], "big")
    if page_size in {0xFEFF, 0xFFFE} or raw[16:18] in {b"\xfe\xff", b"\xff\xfe"}:
        return None, "utf16"
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        _validate_schema_and_types(connection)
        logical = _logical_text_views(connection)
    except (sqlite3.DatabaseError, ValidationError, TypeError, ValueError, AttributeError):
        return None, "malformed"
    finally:
        connection.close()
    latin1 = raw.decode("latin-1")
    surrogate = raw.decode("utf-8", "surrogateescape")
    views = list(logical)
    views.append(latin1)
    views.append(surrogate)
    return views, None


def load_pinned_specimen_bytes(root: Path | None = None) -> bytes:
    """Load the pinned registered specimen from the content-addressed fixture path."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    path = base / "tests" / "fixtures" / "upgrades" / "blobs" / REGISTERED_SHA256
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTERED_SHA256 or len(raw) != REGISTERED_SIZE:
        raise ValidationError("pinned SQLite specimen digest or size mismatch")
    return raw
