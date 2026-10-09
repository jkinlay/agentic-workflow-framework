"""Shared protection for controller-owned SQLite state outside worktrees."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat

from . import ValidationError


STATE_SCHEMA_VERSION = 1


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def protected_state_path(path, worktree_roots):
    """Return a normalized absolute state path after containment/link checks."""
    candidate = Path(path)
    _require(candidate.is_absolute(), "Controller state path must be absolute")
    for parent in (candidate, *candidate.parents):
        if parent.exists() and (parent.is_symlink() or
                (hasattr(parent, "is_junction") and parent.is_junction())):
            raise ValidationError("Controller state path traverses a link/reparse point")
    normalized = candidate.resolve(strict=False)
    for worktree in worktree_roots:
        root = Path(worktree).resolve(strict=False)
        if normalized.is_relative_to(root):
            raise ValidationError("Controller state must be outside worker and reviewer worktrees")
    normalized.parent.mkdir(parents=True, exist_ok=True)
    verify_state_file(normalized)
    return normalized


def verify_state_file(path):
    """Reject aliasing that would let another location mutate controller state."""
    path = Path(path)
    if path.exists() and path.stat().st_nlink > 1:
        raise ValidationError("Controller state file has multiple hardlinks")


def configure_database(db, path, application_id, *, foreign_keys=False):
    """Apply durable settings and verify ledger identity/integrity on every open."""
    verify_state_file(path)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        if foreign_keys:
            db.execute("PRAGMA foreign_keys=ON")
        integrity = db.execute("PRAGMA quick_check").fetchone()
        _require(integrity is not None and integrity[0] == "ok",
                 "Controller state integrity check failed")
        observed_app = db.execute("PRAGMA application_id").fetchone()[0]
        observed_version = db.execute("PRAGMA user_version").fetchone()[0]
        if observed_app == 0 and observed_version == 0:
            db.execute(f"PRAGMA application_id={int(application_id)}")
            db.execute(f"PRAGMA user_version={STATE_SCHEMA_VERSION}")
        else:
            _require(observed_app == application_id,
                     "Controller state application identity is wrong or tampered")
            _require(observed_version == STATE_SCHEMA_VERSION,
                     "Controller state schema version is unsupported or tampered")
    except sqlite3.DatabaseError as exc:
        raise ValidationError("Controller state is unreadable or corrupt") from exc


def restrict_state_permissions(path):
    """Limit a newly created ledger and its current SQLite sidecars to its owner."""
    path = Path(path)
    mode = stat.S_IRUSR | stat.S_IWUSR
    for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if item.exists():
            os.chmod(item, mode)
