"""Manifest-verified, journaled installation of managed workflow files."""
from __future__ import annotations
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
import uuid

from . import ValidationError, VERSION
from .canonical import MAX_DOCUMENT_BYTES, load_yaml, loads, now_text, sha256
from .safeio import Tree, relative_parts
from .providers.github import load_observation_report, validate_codeowner

MANIFEST = "MANIFEST.json"
LOCK = ".agentic-install/lock"
JOURNAL = ".agentic-install/journal.json"
RECOVERY_OBJECTS = ".agentic-install/objects"
MARKER = ".agentic/INSTALLING.json"
INSTALLED = ".agentic/installed-manifest.json"
CONFIG = ".agentic/PROJECT_CONFIG.yaml"
PROVENANCE = ".agentic/workflow-version.yaml"
ROUTING = ".agentic/routing.yaml"
RUNTIME = ".agentic/.venv"
CODEOWNERS = ".github/CODEOWNERS"
GITIGNORE = ".gitignore"
GITIGNORE_TEMPLATE = ".agentic/templates/operating.gitignore"
SOURCE_CONFIG_PREFIX = ".agentic/templates/source-config/"
SOURCE_CONFIG_PATHS = frozenset({
    CONFIG,
    PROVENANCE,
    "OPERATING_CONFIG.yaml",
})
OPERATOR_CONFIG_PATHS = frozenset({"OPERATING_CONFIG.yaml", ROUTING})
INSTALL_GENERATED_PATHS = frozenset({
    MARKER,
    INSTALLED,
    GITIGNORE,
})
INSTALL_OWNED_PATHS = SOURCE_CONFIG_PATHS | INSTALL_GENERATED_PATHS
INSTALL_OWNED_PREFIXES = (
    ".agentic-install/",
    ".agentic-state/",
    ".agentic-backup/",
    RUNTIME + "/",
    RUNTIME + ".staging-",
    RUNTIME + ".backup-",
    RUNTIME + ".cleanup-",
)
RELEASE_EXCLUDED_PREFIXES = (
    "docs/showcase/",
    ".tmp-tests/",
    ".agentic/tests/fixtures/upgrades/",
    SOURCE_CONFIG_PREFIX,
    *INSTALL_OWNED_PREFIXES,
)
KNOWN_VERSIONS = ".agentic/upgrade/known-versions.json"
RELEASE_EXCLUDED_PATHS = frozenset({
    "docs/AWF-1.8.9-Showcase-Presentation.html",
    "docs/AWF-1.9.1-Showcase-Presentation.html",
    "docs/AWF-Showcase-Presentation-Plan.md",
    MARKER,
    INSTALLED,
    GITIGNORE,
})


# safeio.Tree.write creates files with this mode before replacing the target.
INSTALLER_WRITE_MODE = 0o600
INSTALL_JOURNAL_HEADROOM_BYTES = 520 * 1024
INSTALL_JOURNAL_MAX_BYTES = MAX_DOCUMENT_BYTES - INSTALL_JOURNAL_HEADROOM_BYTES
PENDING_UPDATE_KINDS = frozenset({
    "activate",
    "managed_after",
    "runtime_stage",
    "commit_cleanup",
    "commit_cleanup_authenticated",
    "rollback_cleanup",
})
_UNSPECIFIED_UPDATE_KIND = object()


def json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _journal_bytes(journal):
    """Serialize a new compact journal without consuming parser headroom."""
    raw = json_bytes(journal)
    if (isinstance(journal, dict) and
            journal.get("format") == "awf-install-journal-4" and
            len(raw) > INSTALL_JOURNAL_MAX_BYTES):
        raise ValidationError(
            "Compact installation recovery journal leaves insufficient parser headroom")
    return raw


def _journal_marker(transaction_id, journal_digest, pending_digest=None,
                    pending_update_kind=_UNSPECIFIED_UPDATE_KIND):
    """Bind recovery authority to one exact journal, with one precommitted update."""
    # Preserve legacy direct callers that construct a pending v1 marker. New
    # production updates always pass an explicit kind and publish marker v2.
    if pending_digest is not None and pending_update_kind is _UNSPECIFIED_UPDATE_KIND:
        return {"format": "awf-install-marker-1", "transaction_id": transaction_id,
                "journal_sha256": journal_digest,
                "pending_journal_sha256": pending_digest}
    if pending_update_kind is _UNSPECIFIED_UPDATE_KIND:
        pending_update_kind = None
    return {"format": "awf-install-marker-2", "transaction_id": transaction_id,
            "journal_sha256": journal_digest,
            "pending_journal_sha256": pending_digest,
            "pending_update_kind": pending_update_kind}


def _validate_journal_marker(marker, journal_raw):
    if not isinstance(marker, dict):
        raise ValidationError("Invalid installation recovery marker")
    if "format" not in marker or not isinstance(marker["format"], str):
        raise ValidationError("Invalid installation recovery marker")
    marker_format = marker["format"]
    base_keys = {"format", "transaction_id", "journal_sha256",
                 "pending_journal_sha256"}
    marker_keys = set(marker)
    if marker_keys not in (base_keys, base_keys | {"pending_update_kind"}):
        raise ValidationError("Invalid installation recovery marker")
    if marker_format == "awf-install-marker-1":
        expected_keys = base_keys
    elif marker_format == "awf-install-marker-2":
        expected_keys = base_keys | {"pending_update_kind"}
    else:
        raise ValidationError("Unsupported installation recovery marker")
    if marker_keys != expected_keys:
        raise ValidationError("Invalid installation recovery marker")
    try:
        transaction_id = str(uuid.UUID(marker["transaction_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("Invalid installation recovery marker identity") from exc
    digest_pattern = r"[0-9a-f]{64}"
    if (transaction_id != marker["transaction_id"] or
            not isinstance(marker["journal_sha256"], str) or
            re.fullmatch(digest_pattern, marker["journal_sha256"]) is None or
            (marker["pending_journal_sha256"] is not None and (
                not isinstance(marker["pending_journal_sha256"], str) or
                re.fullmatch(digest_pattern, marker["pending_journal_sha256"]) is None))):
        raise ValidationError("Invalid installation recovery marker digest")
    if marker_format == "awf-install-marker-2":
        update_kind = marker["pending_update_kind"]
        if update_kind is not None and not isinstance(update_kind, str):
            raise ValidationError("Invalid installation recovery marker update kind")
        if marker["pending_journal_sha256"] is None and update_kind is not None:
            raise ValidationError("Invalid installation recovery marker update kind")
    observed = sha256(journal_raw)
    if observed not in {marker["journal_sha256"], marker["pending_journal_sha256"]}:
        raise ValidationError("Installation recovery journal differs from its bound marker")
    return transaction_id


def _publish_initial_intent(tree, journal):
    """Create recoverable intent before any destination payload can change."""
    _validate_transaction_journal(journal)
    journal_raw = _journal_bytes(journal)
    tree.write(JOURNAL, journal_raw)
    tree.write(MARKER, json_bytes(_journal_marker(journal["transaction_id"], sha256(journal_raw))))


def _read_bound_journal(tree):
    journal_raw = _exists_read(tree, JOURNAL)
    marker_raw = _exists_read(tree, MARKER)
    if journal_raw is None or marker_raw is None:
        raise ValidationError("Installation recovery marker and journal must both be present")
    marker = loads(marker_raw.decode("utf-8"))
    transaction_id = _validate_journal_marker(marker, journal_raw)
    journal = loads(journal_raw.decode("utf-8"))
    if not isinstance(journal, dict) or journal.get("transaction_id") != transaction_id:
        raise ValidationError("Installation recovery marker and journal identities differ")
    _validate_transaction_journal(journal)
    return journal, journal_raw, marker


def _write_bound_journal_update(tree, journal, update_kind=None):
    """Publish a journal update without an unauthenticated cross-file crash window."""
    if update_kind is not None and update_kind not in PENDING_UPDATE_KINDS:
        raise ValidationError("Invalid installation journal update kind")
    current, current_raw, _marker = _read_bound_journal(tree)
    if current["transaction_id"] != journal["transaction_id"]:
        raise ValidationError("Installation recovery journal identity changed during update")
    updated_raw = _journal_bytes(journal)
    updated_digest = sha256(updated_raw)
    current_digest = sha256(current_raw)
    if updated_digest == current_digest:
        return
    transaction_id = journal["transaction_id"]
    # Precommit the exact next digest. A crash before the journal write leaves
    # the old digest valid; a crash after it leaves only the precommitted digest
    # valid. The final marker collapses the state back to one accepted digest.
    tree.write(MARKER, json_bytes(_journal_marker(
        transaction_id, current_digest, updated_digest, update_kind)))
    tree.write(JOURNAL, updated_raw)
    tree.write(MARKER, json_bytes(_journal_marker(transaction_id, updated_digest)))


def _recover_legacy_pending_update(tree, journal, journal_raw, marker):
    """Retain recovery compatibility for pending markers emitted before marker v2."""
    if (marker["pending_journal_sha256"] is None or
            sha256(journal_raw) != marker["journal_sha256"]):
        return journal
    if _compact_journal(journal) and journal["phase"] == "active":
        # Rollback publishes its cleanup phase through the same bound-update
        # protocol. If its journal replace was interrupted, keep the active
        # journal and let idempotent rollback re-prove the restored endpoint.
        rollback_candidate = loads(json_bytes(journal).decode("utf-8"))
        rollback_candidate["phase"] = "rollback_cleanup"
        if sha256(json_bytes(rollback_candidate)) == marker["pending_journal_sha256"]:
            return journal
    runtime = _validated_runtime_journal(journal)
    if (journal["format"] not in {"awf-install-journal-3", "awf-install-journal-4"} or
            journal["phase"] != "active" or
            runtime is None or runtime["new_sha256"] is not None):
        return journal
    stage = tree.root / runtime["stage"]
    if not (stage.exists() or stage.is_symlink()):
        raise ValidationError("Pending runtime journal update has no staged runtime; journal retained")
    candidate = loads(json_bytes(journal).decode("utf-8"))
    candidate["runtime"]["new_sha256"] = _runtime_tree_sha256(stage)
    candidate_raw = json_bytes(candidate)
    if sha256(candidate_raw) != marker["pending_journal_sha256"]:
        raise ValidationError("Staged runtime does not prove the pending journal update; journal retained")
    tree.write(JOURNAL, candidate_raw)
    tree.write(MARKER, json_bytes(_journal_marker(
        candidate["transaction_id"], sha256(candidate_raw))))
    return candidate


def _pending_transition_mismatch():
    raise ValidationError(
        "Pending journal update kind does not match the bound transition; journal retained")


def _pending_digest_mismatch(update_kind):
    display = update_kind.replace("_", "-")
    raise ValidationError(
        f"Pending {display} journal update digest mismatch; journal retained")


def _copy_journal(journal):
    return loads(json_bytes(journal).decode("utf-8"))


def _validate_pending_new_journal(tree, journal, update_kind):
    runtime = _validated_runtime_journal(journal)
    if update_kind == "activate":
        valid = (journal["format"] == "awf-install-journal-4" and
                 journal["phase"] == "active" and
                 journal["managed_after_sha256"] is None)
    elif update_kind == "managed_after":
        valid = (journal["format"] == "awf-install-journal-4" and
                 journal["phase"] == "active" and
                 journal["managed_after_sha256"] is not None)
    elif update_kind == "runtime_stage":
        managed_after = (journal.get("managed_after_sha256") if _compact_journal(journal)
                         else journal.get("managed_after"))
        valid = (journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"} and
                 journal["phase"] == "active" and managed_after is not None and
                 runtime is not None and runtime["new_sha256"] is not None)
    elif update_kind == "commit_cleanup":
        valid = journal["phase"] == "commit_cleanup"
    elif update_kind == "commit_cleanup_authenticated":
        valid = journal["phase"] == "commit_cleanup_authenticated"
    else:
        valid = journal["phase"] == "rollback_cleanup"
    if not valid:
        _pending_transition_mismatch()
    if update_kind == "activate":
        _load_recovery_objects(tree, journal)
    elif update_kind == "managed_after":
        candidate = _copy_journal(journal)
        _capture_managed_after(tree, candidate)
        if json_bytes(candidate) != json_bytes(journal):
            _pending_digest_mismatch(update_kind)
    elif update_kind == "runtime_stage":
        stage = tree.root / runtime["stage"]
        if not (stage.exists() or stage.is_symlink()):
            raise ValidationError(
                "Pending runtime-stage journal update has no staged runtime; journal retained")
        if _runtime_tree_sha256(stage) != runtime["new_sha256"]:
            raise ValidationError(
                "Staged runtime does not prove the pending runtime-stage update; journal retained")
    elif update_kind == "commit_cleanup_authenticated" and runtime is not None:
        cleanup_path = _runtime_cleanup_path(tree.root, runtime)
        if runtime["previous_sha256"] is None:
            if cleanup_path.exists() or cleanup_path.is_symlink():
                _pending_transition_mismatch()
        elif ("cleanup_identity" not in runtime or runtime["cleanup_identity"] is None or
              runtime["cleanup_members"] is None):
            _pending_transition_mismatch()
        else:
            _verify_runtime_cleanup_proof(cleanup_path, {
                "identity": runtime["cleanup_identity"],
                "members": runtime["cleanup_members"],
            })
    if update_kind in {"managed_after", "runtime_stage"}:
        _load_recovery_objects(tree, journal)


def _reconstruct_pending_old_journal(tree, journal, update_kind):
    """Authenticate the exact named transition without mutating project state."""
    candidate = _copy_journal(journal)
    runtime = _validated_runtime_journal(candidate)
    if update_kind == "activate":
        if not (_compact_journal(journal) and journal["phase"] == "object_prepare"):
            _pending_transition_mismatch()
        candidate["phase"] = "active"
    elif update_kind == "managed_after":
        if not (_compact_journal(journal) and journal["phase"] == "active" and
                journal["managed_after_sha256"] is None):
            _pending_transition_mismatch()
        _capture_managed_after(tree, candidate)
    elif update_kind == "runtime_stage":
        managed_after = (journal.get("managed_after_sha256") if _compact_journal(journal)
                         else journal.get("managed_after"))
        if not (journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"} and
                journal["phase"] == "active" and managed_after is not None and
                runtime is not None and runtime["new_sha256"] is None):
            _pending_transition_mismatch()
        stage = tree.root / runtime["stage"]
        if not (stage.exists() or stage.is_symlink()):
            raise ValidationError(
                "Pending runtime-stage journal update has no staged runtime; journal retained")
        candidate["runtime"]["new_sha256"] = _runtime_tree_sha256(stage)
    elif update_kind in {"commit_cleanup", "rollback_cleanup"}:
        if journal["phase"] != "active":
            _pending_transition_mismatch()
        candidate["phase"] = update_kind
    else:
        if journal["phase"] != "commit_cleanup":
            _pending_transition_mismatch()
        candidate["phase"] = "commit_cleanup_authenticated"
        if runtime is not None and runtime["previous_sha256"] is not None and \
                "cleanup_identity" in runtime:
            cleanup_path = _runtime_cleanup_path(tree.root, runtime)
            if not (cleanup_path.exists() or cleanup_path.is_symlink()) or \
                    _runtime_tree_sha256(cleanup_path) != runtime["previous_sha256"]:
                raise ValidationError(
                    "Pending runtime cleanup cannot be authenticated; journal retained")
            proof = _runtime_cleanup_proof(cleanup_path)
            candidate["runtime"]["cleanup_identity"] = proof["identity"]
            candidate["runtime"]["cleanup_members"] = proof["members"]
    return candidate


def _recover_pending_journal_update(tree, journal, journal_raw, marker):
    """Resolve one explicitly named, digest-bound journal replacement."""
    pending_digest = marker["pending_journal_sha256"]
    if pending_digest is None:
        return journal
    if marker["format"] == "awf-install-marker-1":
        return _recover_legacy_pending_update(tree, journal, journal_raw, marker)
    update_kind = marker.get("pending_update_kind")
    if update_kind not in PENDING_UPDATE_KINDS:
        raise ValidationError(
            "Unknown or ambiguous pending journal update kind; journal retained")
    observed_digest = sha256(journal_raw)
    if observed_digest == pending_digest:
        _validate_pending_new_journal(tree, journal, update_kind)
        tree.write(MARKER, json_bytes(_journal_marker(
            journal["transaction_id"], observed_digest)))
        return journal
    if observed_digest != marker["journal_sha256"]:
        raise ValidationError(
            "Installation recovery journal differs from its bound marker")

    candidate = _reconstruct_pending_old_journal(tree, journal, update_kind)
    candidate_raw = json_bytes(candidate)
    if sha256(candidate_raw) != pending_digest:
        if update_kind == "runtime_stage":
            raise ValidationError(
                "Staged runtime does not prove the pending runtime-stage update; journal retained")
        _pending_digest_mismatch(update_kind)

    if update_kind in {"managed_after", "runtime_stage", "commit_cleanup",
                       "rollback_cleanup"}:
        _load_recovery_objects(tree, journal)

    if update_kind in {"runtime_stage", "commit_cleanup_authenticated"}:
        tree.write(JOURNAL, candidate_raw)
        tree.write(MARKER, json_bytes(_journal_marker(
            candidate["transaction_id"], sha256(candidate_raw))))
        return candidate

    # Old-side activate/managed/commit/rollback transitions deliberately keep
    # the old journal. Collapse the precommit and follow its recovery rule.
    tree.write(MARKER, json_bytes(_journal_marker(
        journal["transaction_id"], observed_digest)))
    return journal


def managed(path):
    managed_path = (path in {"AGENTS.md", ".github/PULL_REQUEST_TEMPLATE.md", CODEOWNERS}
                    or path.startswith(".agentic/"))
    return managed_path and path not in OPERATOR_CONFIG_PATHS


def install_owned_path(path):
    """Return source-checkout paths whose bytes belong to one installation."""
    return path in INSTALL_OWNED_PATHS or any(path.startswith(prefix) for prefix in INSTALL_OWNED_PREFIXES)


def _inventory_managed(path):
    """The managed-file proof excludes only intent and runtime transaction trees."""
    if path == MARKER or path == RUNTIME or path.startswith(RUNTIME + "/"):
        return False
    if re.fullmatch(r"\.agentic/\.venv\.(?:staging|backup|cleanup)-[0-9a-f-]+(?:/.*)?", path):
        return False
    return managed(path)


def _managed_file_inventory(tree):
    """Return the complete regular-file inventory governed by the installer."""
    entries = []

    def add(relative, info):
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValidationError("Managed inventory contains a non-regular or linked file: " + relative)
        entries.append({"path": relative, "mode": stat.S_IMODE(info.st_mode),
                        "sha256": sha256(tree.read(relative))})

    for relative in sorted({"AGENTS.md", ".github/PULL_REQUEST_TEMPLATE.md", CODEOWNERS}):
        info = tree.inspect(relative)
        if info is not None:
            add(relative, info)

    root = tree.root / ".agentic"
    if root.exists() or root.is_symlink():
        root_info = os.lstat(root)
        root_reparse = getattr(root_info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(root_info.st_mode) or root_reparse or not stat.S_ISDIR(root_info.st_mode):
            raise ValidationError("Managed inventory root is linked or not a directory")

        def visit(directory):
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as exc:
                raise ValidationError("Managed inventory is unreadable") from exc
            for child in children:
                relative = Path(child.path).relative_to(tree.root).as_posix()
                if not _inventory_managed(relative):
                    continue
                try:
                    # Windows DirEntry metadata can report st_nlink == 0 from
                    # FindFirstFile; request full metadata before enforcing it.
                    info = os.lstat(child.path) if os.name == "nt" else child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ValidationError("Managed inventory entry is unreadable: " + relative) from exc
                reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                if stat.S_ISLNK(info.st_mode) or reparse:
                    raise ValidationError("Managed inventory contains a link or reparse point: " + relative)
                if stat.S_ISDIR(info.st_mode):
                    visit(Path(child.path))
                else:
                    add(relative, info)

        visit(root)
    return sorted(entries, key=lambda item: item["path"].casefold())


def _validate_inventory(value, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, list) or len(value) > 10000:
        raise ValidationError("Invalid managed recovery inventory")
    previous = None
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "mode", "sha256"}:
            raise ValidationError("Invalid managed recovery inventory entry")
        relative_parts(item["path"])
        if (not _inventory_managed(item["path"]) or item["path"] == MARKER or
                not isinstance(item["mode"], int) or item["mode"] < 0 or item["mode"] > 0o7777 or
                not isinstance(item["sha256"], str) or
                re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None or
                (previous is not None and item["path"].casefold() <= previous)):
            raise ValidationError("Invalid or duplicate managed recovery inventory entry")
        previous = item["path"].casefold()


def _verify_managed_inventory(tree, expected, context):
    _validate_inventory(expected)
    observed = _managed_file_inventory(tree)
    if observed != expected:
        expected_map = {item["path"]: (item["mode"], item["sha256"]) for item in expected}
        observed_map = {item["path"]: (item["mode"], item["sha256"]) for item in observed}
        added = sorted(set(observed_map) - set(expected_map))
        removed = sorted(set(expected_map) - set(observed_map))
        changed = sorted(path for path in set(expected_map) & set(observed_map)
                         if expected_map[path] != observed_map[path])
        detail = "; ".join(filter(None, [
            "added=" + ",".join(added) if added else "",
            "removed=" + ",".join(removed) if removed else "",
            "mode_or_bytes=" + ",".join(changed) if changed else "",
        ]))
        raise ValidationError(f"Managed-file inventory changed {context}: {detail}")


def _managed_inventory_sha256(inventory):
    """Bind one complete inventory without retaining every entry in the journal."""
    _validate_inventory(inventory)
    return sha256(json_bytes(inventory))


def _compact_journal(journal):
    return journal["format"] == "awf-install-journal-4"


def _recovery_object_path(digest):
    return f"{RECOVERY_OBJECTS}/{digest}"


def _read_recovery_object(tree, digest, size):
    path = _recovery_object_path(digest)
    try:
        info = tree.inspect(path)
    except OSError as exc:
        raise ValidationError(
            f"Recovery object is missing; journal retained: {digest} (recovery object)") from exc
    if info is None:
        raise ValidationError(
            f"Recovery object is missing; journal retained: {digest} (recovery object)")
    if info.st_size != size:
        raise ValidationError(
            f"Recovery object size mismatch; journal retained: {digest} (recovery object)")
    try:
        data = tree.read(path, maximum=size)
    except (OSError, ValidationError) as exc:
        raise ValidationError(
            f"Recovery object size mismatch; journal retained: {digest} (recovery object)") from exc
    if len(data) != size:
        raise ValidationError(
            f"Recovery object size mismatch; journal retained: {digest} (recovery object)")
    if sha256(data) != digest:
        raise ValidationError(
            f"Recovery object hash mismatch; journal retained: {digest} (recovery object)")
    return data


def _write_recovery_objects(tree, objects):
    for digest, data in sorted(objects.items()):
        if sha256(data) != digest:
            raise ValidationError("Recovery object digest differs from its bytes")
        tree.write(_recovery_object_path(digest), data)
        _read_recovery_object(tree, digest, len(data))


def _load_recovery_objects(tree, journal):
    if not _compact_journal(journal):
        return {}
    sizes = {}
    for item in journal["files"]:
        digest = item["old_sha256"]
        if digest is None:
            continue
        if digest in sizes and sizes[digest] != item["old_size"]:
            raise ValidationError(
                f"Recovery object size references disagree; journal retained: {digest}")
        sizes[digest] = item["old_size"]
    objects = {}
    for digest, size in sizes.items():
        objects[digest] = _read_recovery_object(tree, digest, size)
    return objects


def _cleanup_recovery_objects(tree, journal):
    if not _compact_journal(journal):
        return
    for digest in sorted({item["old_sha256"] for item in journal["files"]
                          if item["old_sha256"] is not None}):
        tree.unlink(_recovery_object_path(digest))


def _verify_compact_old_endpoint(tree, journal, context):
    """Prove the old state from journal digests without requiring object bytes."""
    if not _compact_journal(journal):
        raise ValidationError("Compact recovery endpoint requires a compact journal")
    _verify_journal_managed_state(tree, journal, "before", context)
    for item in journal["files"]:
        current = _exists_read(tree, item["path"])
        expected_digest = item["old_sha256"]
        matches = (current is None if expected_digest is None else
                   current is not None and len(current) == item["old_size"] and
                   sha256(current) == expected_digest)
        if not matches:
            raise ValidationError(f"Transaction old file state differs at {item['path']}")
        if current is not None:
            mode = stat.S_IMODE(tree.inspect(item["path"]).st_mode)
            if mode != item["old_mode"]:
                raise ValidationError(f"Transaction old file mode differs at {item['path']}")
    _verify_runtime_state(
        tree, _validated_runtime_journal(journal), "old", require_cleanup=True,
        preserve_partial_stage=(journal["phase"] == "rollback_cleanup" and
                                journal["managed_after_sha256"] is not None))


def _finalize_compact_rollback(tree, journal, *, marker_present):
    """Retire recovery objects before the artifacts that track their ownership."""
    _verify_compact_old_endpoint(tree, journal, "at compact rollback endpoint")
    _cleanup_recovery_objects(tree, journal)
    if marker_present:
        tree.unlink(MARKER)
    tree.unlink(JOURNAL)


def _old_file_bytes(journal, item, objects):
    if _compact_journal(journal):
        digest = item["old_sha256"]
        return None if digest is None else objects[digest]
    return base64.b64decode(item["old"], validate=True) if item["old"] is not None else None


def _managed_inventory_at_old_state(journal, observed):
    """Normalize journalled paths to old state, leaving all other entries exact."""
    normalized = {item["path"]: item for item in observed}
    for item in journal["files"]:
        path = item["path"]
        if not _inventory_managed(path):
            continue
        old_digest = item["old_sha256"] if _compact_journal(journal) else (
            sha256(base64.b64decode(item["old"], validate=True))
            if item["old"] is not None else None)
        if old_digest is None:
            normalized.pop(path, None)
        else:
            normalized[path] = {"path": path, "mode": item["old_mode"],
                                "sha256": old_digest}
    return sorted(normalized.values(), key=lambda item: item["path"].casefold())


def _verify_compact_unchanged_inventory(journal, observed, context):
    normalized = _managed_inventory_at_old_state(journal, observed)
    if _managed_inventory_sha256(normalized) != journal["managed_before_sha256"]:
        raise ValidationError(f"Managed-file inventory changed {context}")


def _verify_journal_managed_state(tree, journal, which, context):
    if not _compact_journal(journal):
        _verify_managed_inventory(tree, journal[f"managed_{which}"], context)
        return
    expected = journal[f"managed_{which}_sha256"]
    if expected is None or _managed_inventory_sha256(_managed_file_inventory(tree)) != expected:
        raise ValidationError(f"Managed-file inventory changed {context}")


def _capture_managed_after(tree, journal):
    """Validate planned membership/bytes and bind the resulting file modes."""
    if _compact_journal(journal):
        observed = _managed_file_inventory(tree)
        observed_map = {item["path"]: item for item in observed}
        for item in journal["files"]:
            if item["new_sha256"] is None:
                if item["path"] in observed_map:
                    raise ValidationError("Managed-file membership differs from the authenticated transaction plan")
                continue
            info = tree.inspect(item["path"])
            if info is None or not stat.S_ISREG(info.st_mode):
                raise ValidationError("Transaction output is not a regular file: " + item["path"])
            if sha256(tree.read(item["path"])) != item["new_sha256"]:
                raise ValidationError("Managed-file bytes differ from the authenticated transaction plan: " + item["path"])
            item["new_mode"] = stat.S_IMODE(info.st_mode)
        _verify_compact_unchanged_inventory(journal, observed, "while capturing the installed state")
        journal["managed_after_sha256"] = _managed_inventory_sha256(observed)
        return
    before = {item["path"]: item for item in journal["managed_before"]}
    expected = dict(before)
    changed = {}
    for item in journal["files"]:
        path = item["path"]
        if not _inventory_managed(path):
            continue
        changed[path] = item
        if item["new_sha256"] is None:
            expected.pop(path, None)
        else:
            expected[path] = {"path": path, "mode": None, "sha256": item["new_sha256"]}
    observed = _managed_file_inventory(tree)
    observed_map = {item["path"]: item for item in observed}
    if set(observed_map) != set(expected):
        raise ValidationError("Managed-file membership differs from the authenticated transaction plan")
    for path, item in expected.items():
        actual = observed_map[path]
        if actual["sha256"] != item["sha256"]:
            raise ValidationError("Managed-file bytes differ from the authenticated transaction plan: " + path)
        if path not in changed and actual != item:
            raise ValidationError("Unchanged managed-file mode or bytes drifted during the transaction: " + path)
    for item in journal["files"]:
        if item["new_sha256"] is not None:
            info = tree.inspect(item["path"])
            if info is None or not stat.S_ISREG(info.st_mode):
                raise ValidationError("Transaction output is not a regular file: " + item["path"])
            item["new_mode"] = stat.S_IMODE(info.st_mode)
    journal["managed_after"] = observed


def release_member(path):
    """Repository-only showcase material is not portable release content."""
    return path not in RELEASE_EXCLUDED_PATHS and not any(path.startswith(prefix) for prefix in RELEASE_EXCLUDED_PREFIXES)


def merge_operating_ignores(existing, required):
    """Replace complete AWF blocks while preserving every outside project byte."""
    if not required.endswith(b"\n") or b"\x00" in required:
        raise ValidationError("Invalid operating ignore template")
    if existing is None:
        return required
    header = b"# AWF operating recovery files are local; changes/ is versioned audit evidence."
    terminal = b".awf-*.tmp"
    spans = []
    start = None
    # Match only LF/CRLF lines, without decoding or normalizing owner bytes.
    for match in re.finditer(rb"[^\n]*\n|[^\n]+$", existing):
        raw = match.group()
        line = raw[:-1].removesuffix(b"\r") if raw.endswith(b"\n") else raw
        if line == header:
            if start is not None:
                raise ValidationError("Nested AWF operating ignore block; preserve project bytes and repair boundaries")
            start = match.start()
        elif line.startswith(header) or (line.startswith(b"# AWF ") and header.startswith(line)):
            raise ValidationError("Partial AWF operating ignore header; preserve project bytes and repair boundaries")
        elif line == terminal and start is not None:
            spans.append((start, match.end()))
            start = None
    if start is not None:
        raise ValidationError("Incomplete AWF operating ignore block; preserve project bytes and repair boundaries")
    if spans:
        parts = []
        previous = 0
        for index, (start, end) in enumerate(spans):
            parts.append(existing[previous:start])
            if index == 0:
                parts.append(required)
            previous = end
        parts.append(existing[previous:])
        return b"".join(parts)
    # Without a recognized block, append after existing project negations.
    return existing + (b"\n" if existing and not existing.endswith(b"\n") else b"") + required


def operating_ignore_plan(existing, required):
    """Return the byte-exact project-owned ignore merge and its noninstalling plan."""
    proposed = merge_operating_ignores(existing, required)
    payload = required if proposed != existing else b""
    report = {
        "status": "SEEDED" if existing is None else "UNCHANGED" if proposed == existing else "MERGED",
        "previous_sha256": None if existing is None else sha256(existing),
        "proposed_sha256": sha256(proposed),
        "added_lines": payload.decode("utf-8").splitlines(),
    }
    return proposed, report


def verify_release(tree, expected_digest=None, *, allow_source_checkout=False):
    raw = tree.read(MANIFEST)
    if expected_digest is not None and sha256(raw) != expected_digest:
        raise ValidationError("Release manifest does not match the approved digest")
    manifest = loads(raw.decode("utf-8"))
    if set(manifest) != {"format", "template_version", "files"} or manifest["format"] != "awf-manifest-1" or manifest["template_version"] != VERSION:
        raise ValidationError("Unsupported release manifest")
    actual = {path for path in tree.file_list(exclude_root_git=allow_source_checkout,
                                              reject_root_git=not allow_source_checkout,
                                              exclude_prefixes=RELEASE_EXCLUDED_PREFIXES)
              if path not in {MANIFEST, "MANIFEST.md"} and release_member(path)}
    if actual != set(manifest["files"]):
        raise ValidationError("Release manifest file membership mismatch")
    folded = [path.casefold() for path in actual]
    if len(set(folded)) != len(folded):
        raise ValidationError("Case-colliding paths in release")
    content = {}
    for path, expected in manifest["files"].items():
        relative_parts(path)
        data = tree.read(path)
        if (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or (path not in OPERATOR_CONFIG_PATHS and expected != sha256(data))):
            raise ValidationError(f"Source digest mismatch: {path}")
        content[path] = data
    return sha256(raw), content


@contextmanager
def install_lock(tree, lock_path=LOCK):
    parent, handle, name = tree.parent(lock_path, create=True)
    tree.inspect(lock_path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if os.name == "nt":
        import msvcrt
        from .safeio import win_open
        fd = msvcrt.open_osfhandle(win_open(parent / name, lock=True), os.O_RDWR | os.O_BINARY)
    else:
        fd = os.open(name, flags, 0o600, dir_fd=handle)
    locked = False
    try:
        if os.fstat(fd).st_nlink != 1:
            raise ValidationError("Installer lock has multiple links")
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except (BlockingIOError, PermissionError) as exc:
        raise ValidationError("Another installer owns the destination lock") from exc
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _exists_read(tree, path):
    return tree.read(path) if tree.inspect(path) is not None else None


def assert_quiescent(config_bytes):
    if config_bytes is None:
        return
    import yaml
    from .canonical import validate_value
    try:
        value = load_yaml(config_bytes)
        validate_value(value)
        if not isinstance(value, dict):
            raise ValidationError("Existing configuration is not an object")
        controller = value.get("controller", {})
        if not isinstance(controller, dict):
            raise ValidationError("$.controller must be an object with every automation switch false; correct the existing configuration before installing")
        if any(controller.get(key) is not False for key in ["dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments", "auto_transition_jira"]):
            raise ValidationError("Pause all automation switches before installing/upgrading")
    except yaml.YAMLError as exc:
        raise ValidationError("Cannot verify existing configuration quiescence") from exc


def _runtime_tree_sha256(path):
    """Bind a runtime tree without following links or reparse-point directories."""
    path = Path(path)
    metadata = os.lstat(path)
    reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    junction = getattr(path, "is_junction", lambda: False)()
    if stat.S_ISLNK(metadata.st_mode) or reparse or junction or not stat.S_ISDIR(metadata.st_mode):
        raise ValidationError("Canonical runtime transaction root is linked or not a directory")
    digest = hashlib.sha256()

    def add(value):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)

    def visit(directory, prefix=""):
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise ValidationError("Canonical runtime transaction inventory is unreadable") from exc
        for entry in entries:
            relative = entry.name if not prefix else prefix + "/" + entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValidationError("Canonical runtime transaction entry is unreadable: " + relative) from exc
            entry_reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                try:
                    target = os.readlink(entry.path)
                except OSError as exc:
                    raise ValidationError("Canonical runtime link is unreadable: " + relative) from exc
                add([relative, "link", mode, target])
            elif entry_reparse:
                raise ValidationError("Canonical runtime contains a reparse point: " + relative)
            elif stat.S_ISDIR(info.st_mode):
                add([relative, "directory", mode])
                visit(entry.path, relative)
            elif stat.S_ISREG(info.st_mode):
                file_digest = hashlib.sha256()
                try:
                    with open(entry.path, "rb") as stream:
                        while True:
                            block = stream.read(1024 * 1024)
                            if not block:
                                break
                            file_digest.update(block)
                except OSError as exc:
                    raise ValidationError("Canonical runtime file is unreadable: " + relative) from exc
                add([relative, "file", mode, info.st_size, file_digest.hexdigest()])
            else:
                raise ValidationError("Canonical runtime contains a special file: " + relative)

    add([".", "directory", stat.S_IMODE(metadata.st_mode)])
    visit(path)
    return digest.hexdigest()


def _contained_cleanup_link(relative, target):
    """True for a relative POSIX link target that stays inside the cleanup root."""
    if not isinstance(target, str) or not target or "\\" in target or target.startswith("/") or ":" in target:
        return False
    depth = len(relative.split("/")) - 1
    for part in target.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        elif part not in ("", "."):
            depth += 1
    return True


def _runtime_cleanup_proof(path):
    """Return stable root identity and the exact surviving member identities/content."""
    def identity(info):
        values = (info.st_dev, info.st_ino)
        if any(type(value) is not int or value < 0 or value > 0xffffffffffffffff for value in values):
            raise ValidationError("Runtime cleanup object identity is outside the supported range")
        return [format(value, "016x") for value in values]

    path = Path(path)
    root = os.lstat(path)
    proof = {"identity": identity(root), "members": []}
    def visit(directory, prefix=""):
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            relative = entry.name if not prefix else prefix + "/" + entry.name
            info = entry.stat(follow_symlinks=False)
            reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            mode = stat.S_IMODE(info.st_mode)
            object_identity = identity(info)
            if stat.S_ISDIR(info.st_mode) and not reparse:
                proof["members"].append([relative, "directory", mode, object_identity, None])
                visit(entry.path, relative)
            elif stat.S_ISREG(info.st_mode) and not reparse:
                h = hashlib.sha256()
                with open(entry.path, "rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(block)
                proof["members"].append([relative, "file", mode, object_identity, h.hexdigest()])
            elif stat.S_ISLNK(info.st_mode) and not reparse:
                # Venvs on 64-bit POSIX create ``lib64 -> lib``. Accept only a
                # relative link whose lexical target stays inside the cleanup root;
                # rmtree removes the link itself and never follows it.
                target = os.readlink(entry.path)
                if not _contained_cleanup_link(relative, target):
                    raise ValidationError("Authenticated runtime cleanup contains an unsafe member")
                proof["members"].append([relative, "link", mode, object_identity, target])
            else:
                raise ValidationError("Authenticated runtime cleanup contains an unsafe member")
    if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode):
        raise ValidationError("Authenticated runtime cleanup root is unsafe")
    visit(path)
    return proof


def _verify_runtime_cleanup_proof(path, expected):
    if not (Path(path).exists() or Path(path).is_symlink()):
        return
    actual = _runtime_cleanup_proof(path)
    if actual["identity"] != expected["identity"]:
        raise ValidationError("Authenticated runtime cleanup directory identity changed")
    by_path = {row[0]: row for row in expected["members"]}
    for row in actual["members"]:
        if by_path.get(row[0]) != row:
            raise ValidationError("Authenticated runtime cleanup member changed or was added")


def _remove_runtime_transaction_path(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    metadata = os.lstat(path)
    reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    junction = getattr(path, "is_junction", lambda: False)()
    if stat.S_ISLNK(metadata.st_mode) or reparse or junction or not stat.S_ISDIR(metadata.st_mode):
        raise ValidationError("Refusing to clean an unsafe runtime transaction path")
    shutil.rmtree(path)


def _runtime_journal(root, transaction_id):
    runtime = root / RUNTIME
    previous = _runtime_tree_sha256(runtime) if runtime.exists() or runtime.is_symlink() else None
    stage = f".agentic/.venv.staging-{transaction_id}"
    backup = f".agentic/.venv.backup-{transaction_id}"
    cleanup = f".agentic/.venv.cleanup-{transaction_id}"
    for relative in (stage, backup, cleanup):
        target = root / relative
        if target.exists() or target.is_symlink():
            raise ValidationError("Runtime transaction path already exists: " + relative)
    return {"path": RUNTIME, "previous_sha256": previous, "new_sha256": None,
            "stage": stage, "backup": backup, "cleanup_identity": None,
            "cleanup_members": None}


def _runtime_cleanup_path(root, runtime):
    """Return the v3-derived cleanup path without changing its journal schema."""
    return Path(root) / runtime["backup"].replace(".venv.backup-", ".venv.cleanup-", 1)


def ensure_usable(root):
    with Tree(root) as tree:
        if tree.inspect(MARKER) is not None or tree.inspect(JOURNAL) is not None:
            raise ValidationError("Installation is incomplete; use bootstrap --recover before running tools")


def _verify_installed(root, expected_version):
    ensure_usable(root)
    with Tree(root) as tree:
        if tree.inspect(INSTALLED) is None:
            # An uninstalled source template is normally a Git checkout.
            return verify_release(tree, allow_source_checkout=True)[0]
        manifest = loads(tree.read(INSTALLED).decode())
        if manifest.get("template_version") != expected_version:
            raise ValidationError("Installed template version mismatch")
        if "source_manifest_json" not in manifest:
            raise ValidationError("Verified installation receipt requires source_manifest_json")
        source_raw = manifest["source_manifest_json"]
        if not isinstance(source_raw, str) or sha256(source_raw.encode("utf-8")) != manifest["source_manifest_sha256"]:
            raise ValidationError("Installed embedded source manifest digest mismatch")
        source_manifest = loads(source_raw)
        if source_manifest.get("format") != "awf-manifest-1" or source_manifest.get("template_version") != expected_version:
            raise ValidationError("Installed embedded source manifest is invalid")
        if ROUTING in source_manifest.get("files", {}):
            tree.read(ROUTING)  # Operator bytes may change, but the file must remain present.
        expected = {path: digest for path, digest in source_manifest["files"].items()
                    if managed(path) and path not in {CONFIG, PROVENANCE, CODEOWNERS}}
        if manifest["immutable_files"] != expected:
            raise ValidationError("Installed immutable membership differs from its source manifest")
        for path, digest in manifest["immutable_files"].items():
            if sha256(tree.read(path)) != digest:
                raise ValidationError(f"Installed managed file changed: {path}")
        version = loads(tree.read(PROVENANCE).decode())
        if version["template"]["version"] != expected_version or version["installation"]["source_manifest_sha256"] != manifest["source_manifest_sha256"]:
            raise ValidationError("Installed provenance is inconsistent")
        return manifest["source_manifest_sha256"]


def verify_installed(root):
    return _verify_installed(root, VERSION)


def migrate_config_version(raw, previous="1.9.2", current=VERSION):
    """Change only the unique, line-oriented template version scalar."""
    config = load_yaml(raw)
    template = config.get("template") if isinstance(config, dict) else None
    if not isinstance(template, dict) or template.get("expected_workflow_version") != previous:
        raise ValidationError(f"Upgrade requires template.expected_workflow_version {previous}")
    key = rb'(?:"expected_workflow_version"|\'expected_workflow_version\'|expected_workflow_version)'
    pattern = re.compile(
        rb'(?m)^(?P<prefix>[ \t]*' + key + rb'[ \t]*:[ \t]*)(?P<quote>["\']?)' +
        re.escape(previous.encode("ascii")) +
        rb'(?P=quote)(?P<suffix>[ \t]*(?:,[ \t]*)?(?:#[^\r\n]*)?(?:\r\n|\n|\r|$))')
    matches = list(pattern.finditer(raw))
    if len(matches) != 1:
        raise ValidationError("Upgrade requires one line-oriented template.expected_workflow_version scalar")
    match = matches[0]
    start = match.start() + len(match.group("prefix")) + len(match.group("quote"))
    migrated = raw[:start] + current.encode("ascii") + raw[start + len(previous):]
    expected = json.loads(json.dumps(config))
    expected["template"]["expected_workflow_version"] = current
    if load_yaml(migrated) != expected:
        raise ValidationError("Configuration version migration changed owner policy")
    return migrated


def _validated_runtime_journal(journal):
    if journal["format"] == "awf-install-journal-1":
        if set(journal) != {"format", "transaction_id", "files"}:
            raise ValidationError("Invalid recovery journal")
        return None
    if journal["format"] == "awf-install-journal-2":
        if set(journal) != {"format", "transaction_id", "files", "runtime"}:
            raise ValidationError("Invalid recovery journal")
    elif journal["format"] == "awf-install-journal-3":
        if set(journal) != {"format", "transaction_id", "phase", "files", "runtime",
                            "managed_before", "managed_after"}:
            raise ValidationError("Invalid recovery journal")
    elif journal["format"] == "awf-install-journal-4":
        if set(journal) != {"format", "transaction_id", "phase", "files", "runtime",
                            "managed_before_sha256", "managed_after_sha256"}:
            raise ValidationError("Invalid recovery journal")
    else:
        raise ValidationError("Invalid recovery journal")
    transaction_id = str(uuid.UUID(journal["transaction_id"]))
    runtime = journal["runtime"]
    if journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"} and runtime is None:
        return None
    base_runtime_keys = {"path", "previous_sha256", "new_sha256", "stage", "backup"}
    proof_keys = {"cleanup_identity", "cleanup_members"}
    if not isinstance(runtime, dict) or frozenset(runtime) not in {frozenset(base_runtime_keys),
                                                                   frozenset(base_runtime_keys | proof_keys)}:
        raise ValidationError("Invalid runtime recovery journal")
    expected_stage = f".agentic/.venv.staging-{transaction_id}"
    expected_backup = f".agentic/.venv.backup-{transaction_id}"
    valid_digest = lambda value: value is None or (
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    if (runtime["path"] != RUNTIME or runtime["stage"] != expected_stage or
            runtime["backup"] != expected_backup or
            not valid_digest(runtime["previous_sha256"]) or
            not valid_digest(runtime["new_sha256"])):
        raise ValidationError("Invalid runtime recovery paths or digests")
    if proof_keys <= set(runtime):
        identity, members = runtime["cleanup_identity"], runtime["cleanup_members"]
        valid_identity = lambda value: (isinstance(value, list) and len(value) == 2 and
                                        all(isinstance(part, str) and
                                            re.fullmatch(r"[0-9a-f]{16}", part) is not None
                                            for part in value))
        if identity is not None and not valid_identity(identity):
            raise ValidationError("Invalid authenticated cleanup identity")
        if members is not None:
            if not isinstance(members, list) or len(members) > 100000:
                raise ValidationError("Invalid authenticated cleanup inventory")
            seen_members = set()
            for row in members:
                if (not isinstance(row, list) or len(row) != 5 or not isinstance(row[0], str) or
                        row[1] not in {"directory", "file", "link"} or type(row[2]) is not int or
                        not valid_identity(row[3]) or
                        (row[1] == "file" and (not isinstance(row[4], str) or
                         re.fullmatch(r"[0-9a-f]{64}", row[4]) is None)) or
                        (row[1] == "directory" and row[4] is not None) or
                        (row[1] == "link" and not _contained_cleanup_link(row[0], row[4]))):
                    raise ValidationError("Invalid authenticated cleanup member")
                relative_parts(row[0])
                if row[0].casefold() in seen_members:
                    raise ValidationError("Duplicate authenticated cleanup member")
                seen_members.add(row[0].casefold())
    return runtime


def _validate_transaction_journal(journal):
    if not isinstance(journal, dict) or "format" not in journal or "transaction_id" not in journal:
        raise ValidationError("Invalid recovery journal")
    try:
        transaction_id = str(uuid.UUID(journal["transaction_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("Invalid recovery transaction identity") from exc
    if transaction_id != journal["transaction_id"]:
        raise ValidationError("Non-canonical recovery transaction identity")
    _validated_runtime_journal(journal)
    if journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"}:
        if journal["phase"] not in {"object_prepare", "active", "rollback_cleanup",
                                    "commit_cleanup", "commit_cleanup_authenticated"}:
            raise ValidationError("Invalid recovery journal phase")
        if journal["phase"] in {"object_prepare", "rollback_cleanup"} and not _compact_journal(journal):
            raise ValidationError("Compact recovery phase requires a compact journal")
        if _compact_journal(journal):
            digest_pattern = r"[0-9a-f]{64}"
            if (not isinstance(journal["managed_before_sha256"], str) or
                    re.fullmatch(digest_pattern, journal["managed_before_sha256"]) is None or
                    (journal["managed_after_sha256"] is not None and (
                        not isinstance(journal["managed_after_sha256"], str) or
                        re.fullmatch(digest_pattern, journal["managed_after_sha256"]) is None))):
                raise ValidationError("Invalid managed recovery inventory digest")
        else:
            _validate_inventory(journal["managed_before"])
            _validate_inventory(journal["managed_after"], optional=True)
    if not isinstance(journal["files"], list) or len(journal["files"]) > 10000:
        raise ValidationError("Invalid recovery file inventory")
    seen = set()
    backup_prefix = f".agentic-backup/{transaction_id}/"
    mode_aware = journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"}
    for item in journal["files"]:
        if _compact_journal(journal):
            expected_keys = {"path", "old_sha256", "old_size", "new_sha256",
                             "old_mode", "new_mode"}
        elif mode_aware:
            expected_keys = {"path", "old", "new_sha256", "old_mode", "new_mode"}
        else:
            expected_keys = {"path", "old", "new_sha256"}
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValidationError("Invalid recovery file entry")
        relative_parts(item["path"])
        state_path = item["path"].startswith(".agentic-state/")
        valid_new = item["new_sha256"] is None or (
            isinstance(item["new_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", item["new_sha256"]))
        transaction_backup = item["path"].startswith(backup_prefix)
        safe_path = (managed(item["path"]) or item["path"] in OPERATOR_CONFIG_PATHS | {GITIGNORE}
                     or state_path or transaction_backup)
        has_old = (item["old_sha256"] is not None if _compact_journal(journal)
                   else item["old"] is not None)
        if (not safe_path or item["path"] == MARKER or item["path"].casefold() in seen or not valid_new or
                (transaction_backup and (has_old or item["new_sha256"] is None))):
            raise ValidationError("Unsafe or duplicate recovery path/digest")
        seen.add(item["path"].casefold())
        if _compact_journal(journal):
            valid_old_digest = item["old_sha256"] is None or (
                isinstance(item["old_sha256"], str) and
                re.fullmatch(r"[0-9a-f]{64}", item["old_sha256"]) is not None)
            valid_old_size = item["old_size"] is None or (
                type(item["old_size"]) is int and item["old_size"] >= 0)
            if (not valid_old_digest or not valid_old_size or
                    (item["old_sha256"] is None) != (item["old_size"] is None)):
                raise ValidationError("Invalid recovery object reference")
        elif item["old"] is not None:
            try:
                base64.b64decode(item["old"], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValidationError("Invalid recovery file bytes") from exc
        if mode_aware:
            valid_mode = lambda value: value is None or (
                isinstance(value, int) and 0 <= value <= 0o7777)
            if (not valid_mode(item["old_mode"]) or not valid_mode(item["new_mode"]) or
                    (not has_old) != (item["old_mode"] is None) or
                    (item["new_sha256"] is None and item["new_mode"] is not None)):
                raise ValidationError("Invalid recovery file mode")
    return journal


def _rollback_runtime(tree, runtime, *, preserve_partial_stage=False):
    if runtime is None:
        return
    root = tree.root
    canonical = root / runtime["path"]
    stage = root / runtime["stage"]
    backup = root / runtime["backup"]
    cleanup = _runtime_cleanup_path(root, runtime)
    previous = runtime["previous_sha256"]
    proposed = runtime["new_sha256"]

    if cleanup.exists() or cleanup.is_symlink():
        raise ValidationError("Runtime recovery found an unauthenticated cleanup path; journal retained")
    if stage.exists() or stage.is_symlink():
        if proposed is None:
            if preserve_partial_stage:
                _verify_runtime_state(
                    tree, runtime, "old", require_cleanup=True,
                    preserve_partial_stage=True)
                return
            raise ValidationError("Runtime recovery cannot prove ownership of the staged runtime; journal retained")
        if _runtime_tree_sha256(stage) != proposed:
            raise ValidationError("Runtime recovery found an external edit in the staged runtime; journal retained")
        _remove_runtime_transaction_path(stage)

    if previous is None:
        if backup.exists() or backup.is_symlink():
            raise ValidationError("Fresh runtime recovery found an unexpected backup; journal retained")
        if canonical.exists() or canonical.is_symlink():
            if proposed is None or _runtime_tree_sha256(canonical) != proposed:
                raise ValidationError("Fresh runtime recovery cannot prove the canonical runtime is transaction-owned; journal retained")
            _remove_runtime_transaction_path(canonical)
        return

    if backup.exists() or backup.is_symlink():
        if _runtime_tree_sha256(backup) != previous:
            raise ValidationError("Runtime recovery backup differs from the exact prior runtime; journal retained")
        if canonical.exists() or canonical.is_symlink():
            if proposed is None or _runtime_tree_sha256(canonical) != proposed:
                raise ValidationError("Runtime recovery found an external canonical-runtime edit; journal retained")
            _remove_runtime_transaction_path(canonical)
        os.replace(backup, canonical)
    elif not (canonical.exists() or canonical.is_symlink()) or _runtime_tree_sha256(canonical) != previous:
        raise ValidationError("Runtime recovery cannot prove or restore the exact prior runtime; journal retained")
    if _runtime_tree_sha256(canonical) != previous:
        raise ValidationError("Runtime recovery verification failed; journal retained")


def rollback(tree, journal):
    _validate_transaction_journal(journal)
    if _compact_journal(journal) and journal["phase"] in {"object_prepare", "rollback_cleanup"}:
        _finalize_compact_rollback(tree, journal, marker_present=True)
        return
    # Resolve and authenticate every recovery object before any project or
    # runtime mutation, so one missing/tampered object fails the whole rollback.
    recovery_objects = _load_recovery_objects(tree, journal)
    runtime = _validated_runtime_journal(journal)
    backup_prefix = f".agentic-backup/{journal['transaction_id']}/"
    mode_aware = journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"}
    # Do not clobber edits that were neither the old nor proposed installer bytes.
    for item in journal["files"]:
        current = _exists_read(tree, item["path"])
        old = _old_file_bytes(journal, item, recovery_objects)
        if current != old:
            if item["new_sha256"] is None:
                if current is not None:
                    raise ValidationError(f"Recovery found an external edit at {item['path']}; marker retained")
            elif current is None or sha256(current) != item["new_sha256"]:
                raise ValidationError(f"Recovery found an external edit at {item['path']}; marker retained")
        if mode_aware and current is not None:
            mode = stat.S_IMODE(tree.inspect(item["path"]).st_mode)
            expected_mode = item["old_mode"] if current == old else item["new_mode"]
            # An identical-byte rewrite interrupted before new_mode was recorded
            # carries the installer's own private write mode; rollback restores old_mode.
            own_write = (current == old and item["new_mode"] is None and
                         item["new_sha256"] == sha256(current) and mode == INSTALLER_WRITE_MODE)
            if expected_mode is not None and mode != expected_mode and not own_write:
                raise ValidationError(f"Recovery found an external mode edit at {item['path']}; marker retained")
    if journal["format"] == "awf-install-journal-3":
        before = {item["path"]: item for item in journal["managed_before"]}
        after = ({item["path"]: item for item in journal["managed_after"]}
                 if journal["managed_after"] is not None else {})
        allowed = set(before) | set(after) | {
            item["path"] for item in journal["files"] if _inventory_managed(item["path"])
        }
        observed = {item["path"]: item for item in _managed_file_inventory(tree)}
        unexpected = sorted(set(observed) - allowed)
        if unexpected:
            raise ValidationError("Recovery found an external managed-file addition; marker retained: " +
                                  ", ".join(unexpected))
        changed = {item["path"] for item in journal["files"]}
        for path, expected in before.items():
            if path not in changed and observed.get(path) != expected:
                raise ValidationError("Recovery found external drift in an unchanged managed file; marker retained: " + path)
    elif _compact_journal(journal):
        observed = _managed_file_inventory(tree)
        _verify_compact_unchanged_inventory(journal, observed,
                                            "during rollback; marker retained")
    expected_backups = {item["path"] for item in journal["files"]
                        if item["path"].startswith(backup_prefix)}
    backup_root = tree.root / backup_prefix.rstrip("/")
    if backup_root.exists() or backup_root.is_symlink():
        metadata = os.lstat(backup_root)
        reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISDIR(metadata.st_mode):
            raise ValidationError("Recovery backup root is unsafe; marker retained")
        actual_backups = set()
        for directory, directories, files in os.walk(backup_root, followlinks=False):
            for name in [*directories, *files]:
                path = Path(directory) / name
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValidationError("Recovery backup contains a link or reparse point; marker retained")
            for name in files:
                actual_backups.add((Path(directory) / name).relative_to(tree.root).as_posix())
        if not actual_backups.issubset(expected_backups):
            raise ValidationError("Recovery backup contains an external file; marker retained")
    # Restore or remove the canonical runtime before changing the managed files
    # that it executes. A runtime proof failure retains the whole transaction.
    _rollback_runtime(
        tree, runtime,
        preserve_partial_stage=(_compact_journal(journal) and
                                journal["phase"] == "active" and
                                journal["managed_after_sha256"] is not None))
    for item in reversed(journal["files"]):
        old = _old_file_bytes(journal, item, recovery_objects)
        if old is None:
            tree.unlink(item["path"])
        else:
            tree.write(item["path"], old)
            if mode_aware:
                os.chmod(tree.root / item["path"], item["old_mode"])
    if backup_root.exists():
        shutil.rmtree(backup_root)
    if mode_aware:
        _verify_journal_managed_state(tree, journal, "before", "after rollback")
    if _compact_journal(journal):
        # Publish the restored endpoint before deleting any object. Recovery can
        # then prove the old state from hashes even after partial object cleanup.
        journal["phase"] = "rollback_cleanup"
        _write_bound_journal_update(tree, journal, "rollback_cleanup")
        _finalize_compact_rollback(tree, journal, marker_present=True)
    else:
        tree.unlink(MARKER)
        tree.unlink(JOURNAL)


class _RuntimeInstallTransaction:
    def __init__(self, tree, journal):
        self.tree = tree
        self.journal = journal
        self.runtime = journal["runtime"]
        self.root = tree.root
        self.runtime_root = self.root / self.runtime["path"]
        self.stage = self.root / self.runtime["stage"]
        self.backup = self.root / self.runtime["backup"]
        self.cleanup = _runtime_cleanup_path(self.root, self.runtime)

    @property
    def had_previous(self):
        return self.runtime["previous_sha256"] is not None

    def verify_initial(self):
        if (self.stage.exists() or self.stage.is_symlink() or
                self.backup.exists() or self.backup.is_symlink() or
                self.cleanup.exists() or self.cleanup.is_symlink()):
            raise ValidationError("Runtime transaction paths changed before canonical runtime creation")
        present = self.runtime_root.exists() or self.runtime_root.is_symlink()
        if self.had_previous:
            if not present or _runtime_tree_sha256(self.runtime_root) != self.runtime["previous_sha256"]:
                raise ValidationError("Canonical runtime changed after installation planning")
        elif present:
            raise ValidationError("A canonical runtime appeared after fresh-install planning")

    def record_staged_runtime(self):
        proposed = _runtime_tree_sha256(self.stage)
        if self.runtime["new_sha256"] not in {None, proposed}:
            raise ValidationError("Staged canonical runtime identity changed")
        self.runtime["new_sha256"] = proposed
        _write_bound_journal_update(self.tree, self.journal, "runtime_stage")
        return proposed

    def verify_committed(self):
        proposed = self.runtime["new_sha256"]
        if proposed is None or not self.runtime_root.exists() or _runtime_tree_sha256(self.runtime_root) != proposed:
            raise ValidationError("Canonical runtime commit cannot be proved")
        if self.stage.exists() or self.stage.is_symlink():
            raise ValidationError("Staged runtime remains after canonical runtime commit")
        if self.had_previous:
            if not self.backup.exists() or _runtime_tree_sha256(self.backup) != self.runtime["previous_sha256"]:
                raise ValidationError("Exact prior canonical runtime backup cannot be proved")
        elif self.backup.exists() or self.backup.is_symlink():
            raise ValidationError("Fresh runtime transaction created an unexpected backup")
        if self.cleanup.exists() or self.cleanup.is_symlink():
            raise ValidationError("Runtime cleanup path appeared before durable commit")


def _verify_file_state(tree, journal, which):
    mode_aware = journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"}
    recovery_objects = _load_recovery_objects(tree, journal) if which == "old" else {}
    for item in journal["files"]:
        if which == "old":
            expected = _old_file_bytes(journal, item, recovery_objects)
            expected_mode = item.get("old_mode")
        else:
            expected = None if item["new_sha256"] is None else item["new_sha256"]
            expected_mode = item.get("new_mode")
        current = _exists_read(tree, item["path"])
        matches = current == expected if which == "old" else (
            current is None if expected is None else current is not None and sha256(current) == expected)
        if not matches:
            raise ValidationError(f"Transaction {which} file state differs at {item['path']}")
        if mode_aware and current is not None:
            if expected_mode is None or stat.S_IMODE(tree.inspect(item["path"]).st_mode) != expected_mode:
                raise ValidationError(f"Transaction {which} file mode differs at {item['path']}")


def _verify_runtime_state(tree, runtime, which, *, require_cleanup=False,
                          preserve_partial_stage=False):
    if runtime is None:
        return
    canonical = tree.root / runtime["path"]
    stage = tree.root / runtime["stage"]
    backup = tree.root / runtime["backup"]
    cleanup = _runtime_cleanup_path(tree.root, runtime)
    expected = runtime["previous_sha256"] if which == "old" else runtime["new_sha256"]
    present = canonical.exists() or canonical.is_symlink()
    if expected is None:
        if present:
            raise ValidationError(f"Canonical runtime differs from the exact {which} state")
    elif not present or _runtime_tree_sha256(canonical) != expected:
        raise ValidationError(f"Canonical runtime differs from the exact {which} state")
    stage_present = stage.exists() or stage.is_symlink()
    if stage_present:
        if not (preserve_partial_stage and require_cleanup and which == "old" and
                runtime["previous_sha256"] is not None and
                runtime["new_sha256"] is None and
                runtime.get("cleanup_identity") is None and
                runtime.get("cleanup_members") is None):
            raise ValidationError("Runtime staging path remains after transaction finalization")
        info = os.lstat(stage)
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        junction = getattr(stage, "is_junction", lambda: False)()
        if stat.S_ISLNK(info.st_mode) or reparse or junction or not stat.S_ISDIR(info.st_mode):
            raise ValidationError("Unproved partial runtime stage is unsafe; journal retained")
    if require_cleanup and (backup.exists() or backup.is_symlink() or
                            cleanup.exists() or cleanup.is_symlink()):
        raise ValidationError("Prior runtime backup or authenticated cleanup path remains after transaction cleanup")
    if stage_present:
        # This proves only the old endpoint, never ownership of the stage bytes.
        # Preserve the entire directory without traversing or deleting its contents.
        import logging

        timestamp = now_text().replace("-", "").replace(":", "")
        relative = f"_to_delete/{stage.name}-{timestamp}-{uuid.uuid4()}"
        parent, handle, name = tree.parent(relative, create=True)
        if tree.inspect(relative) is not None:
            raise ValidationError("Partial runtime preservation path already exists; journal retained")
        retained = parent / name
        _stage_parent, stage_handle, stage_name = tree.parent(runtime["stage"])
        if os.name == "nt":
            os.rename(stage, retained)
        else:
            os.rename(stage_name, name, src_dir_fd=stage_handle, dst_dir_fd=handle)
            os.fsync(stage_handle)
            os.fsync(handle)
        logging.warning("Unproved partial runtime stage moved to %s; bytes preserved for inspection",
                        retained)


def _cleanup_committed_runtime(tree, journal):
    """Authenticate by atomic rename, then resume deletion under a durable phase."""
    runtime = _validated_runtime_journal(journal)
    if journal["phase"] not in {"commit_cleanup", "commit_cleanup_authenticated"}:
        raise ValidationError("Runtime cleanup requires authenticated commit intent")
    if runtime is None:
        if journal["phase"] == "commit_cleanup":
            journal["phase"] = "commit_cleanup_authenticated"
            _write_bound_journal_update(
                tree, journal, "commit_cleanup_authenticated")
        return []
    cleanup = []
    backup = tree.root / runtime["backup"]
    cleanup_path = _runtime_cleanup_path(tree.root, runtime)
    if journal["phase"] == "commit_cleanup":
        if runtime["previous_sha256"] is None:
            if (backup.exists() or backup.is_symlink() or
                    cleanup_path.exists() or cleanup_path.is_symlink()):
                raise ValidationError("Fresh runtime commit found an unexpected backup or cleanup path")
        elif backup.exists() or backup.is_symlink():
            if cleanup_path.exists() or cleanup_path.is_symlink():
                raise ValidationError("Runtime backup and cleanup paths both exist; journal retained")
            if _runtime_tree_sha256(backup) != runtime["previous_sha256"]:
                raise ValidationError("Exact prior canonical runtime backup cannot be authenticated for cleanup")
            os.replace(backup, cleanup_path)
            if _runtime_tree_sha256(cleanup_path) != runtime["previous_sha256"]:
                raise ValidationError("Prior runtime backup changed while being isolated for cleanup; journal retained")
        elif cleanup_path.exists() or cleanup_path.is_symlink():
            # A crash after the atomic rename but before the journal update is
            # recoverable only while the complete cleanup tree still proves the
            # exact prior-runtime digest.
            if _runtime_tree_sha256(cleanup_path) != runtime["previous_sha256"]:
                raise ValidationError("Pending runtime cleanup cannot be authenticated; journal retained")
        else:
            raise ValidationError("Exact prior canonical runtime backup is missing before cleanup")
        if runtime["previous_sha256"] is not None and "cleanup_identity" in runtime:
            proof = _runtime_cleanup_proof(cleanup_path)
            runtime["cleanup_identity"] = proof["identity"]
            runtime["cleanup_members"] = proof["members"]
        journal["phase"] = "commit_cleanup_authenticated"
        _write_bound_journal_update(
            tree, journal, "commit_cleanup_authenticated")
    if backup.exists() or backup.is_symlink():
        raise ValidationError("Prior runtime backup remains after authenticated cleanup transition")
    if runtime["previous_sha256"] is None:
        if cleanup_path.exists() or cleanup_path.is_symlink():
            raise ValidationError("Fresh runtime commit found an unauthenticated cleanup path")
        return []
    if "cleanup_identity" not in runtime or runtime["cleanup_identity"] is None or runtime["cleanup_members"] is None:
        raise ValidationError("Authenticated cleanup has no persisted ownership proof")
    _verify_runtime_cleanup_proof(cleanup_path, {
        "identity": runtime["cleanup_identity"], "members": runtime["cleanup_members"]})
    try:
        _remove_runtime_transaction_path(cleanup_path)
    except (OSError, ValidationError) as exc:
        cleanup.append({"path": str(cleanup_path), "error": type(exc).__name__})
    return cleanup


def _verify_committed_payload(tree, journal, context):
    _verify_journal_managed_state(tree, journal, "after", context)
    _verify_file_state(tree, journal, "new")
    _verify_runtime_state(tree, _validated_runtime_journal(journal), "new")


def _verify_outer_commit_state(tree, journal, transaction):
    """Jointly re-prove every governed surface before durable commit intent."""
    _verify_journal_managed_state(tree, journal, "after", "immediately before outer commit")
    _verify_file_state(tree, journal, "new")
    transaction.verify_committed()


def _finalize_committed(tree, journal):
    _verify_committed_payload(tree, journal, "during authenticated commit cleanup")
    _verify_runtime_state(tree, _validated_runtime_journal(journal), "new", require_cleanup=True)
    # A durable commit can no longer roll back, so its recovery objects may be
    # removed before retiring the bound journal and marker.
    _cleanup_recovery_objects(tree, journal)
    tree.unlink(MARKER)
    tree.unlink(JOURNAL)


def _finalize_journal_only(tree, journal):
    """Resolve a one-artifact endpoint without trusting it to rewrite state."""
    _validate_transaction_journal(journal)
    if journal["format"] not in {"awf-install-journal-3", "awf-install-journal-4"}:
        raise ValidationError("Legacy journal-only recovery failed closed")
    if journal["phase"] in {"object_prepare", "rollback_cleanup"}:
        _finalize_compact_rollback(tree, journal, marker_present=False)
        return "ROLLED_BACK"
    if journal["phase"] == "active":
        _verify_journal_managed_state(tree, journal, "before", "at journal-only rollback endpoint")
        _verify_file_state(tree, journal, "old")
        _verify_runtime_state(tree, _validated_runtime_journal(journal), "old", require_cleanup=True)
        status = "ROLLED_BACK"
    else:
        _verify_journal_managed_state(tree, journal, "after", "at journal-only commit endpoint")
        _verify_file_state(tree, journal, "new")
        _verify_runtime_state(tree, _validated_runtime_journal(journal), "new", require_cleanup=True)
        status = "COMMITTED"
    _cleanup_recovery_objects(tree, journal)
    tree.unlink(JOURNAL)
    return status


def complete_runtime_transaction(destination, transaction_id, builder):
    """Build the canonical runtime, retaining authenticated recovery through cleanup."""
    destination = Path(destination).absolute()
    cleanup = []
    with Tree(destination) as tree, install_lock(tree):
        journal, _raw, _marker = _read_bound_journal(tree)
        runtime = _validated_runtime_journal(journal)
        if (journal["format"] not in {"awf-install-journal-3", "awf-install-journal-4"} or
                runtime is None or
                journal["transaction_id"] != str(transaction_id) or journal["phase"] != "active" or
                (journal["managed_after_sha256"] if _compact_journal(journal)
                 else journal["managed_after"]) is None):
            raise ValidationError("Bootstrap runtime transaction identity or state changed")
        transaction = _RuntimeInstallTransaction(tree, journal)
        try:
            _verify_journal_managed_state(tree, journal, "after", "before canonical runtime creation")
            transaction.verify_initial()
            result = builder(transaction)
            transaction.verify_committed()
            _verify_outer_commit_state(tree, journal, transaction)
        except BaseException as original:
            try:
                rollback(tree, journal)
            except BaseException as rollback_error:
                raise ValidationError(
                    "Bootstrap runtime failed and exact managed/runtime rollback cannot be proved; "
                    f"transaction {journal['transaction_id']} and backup "
                    f"{journal['runtime']['backup'] if journal['runtime'] else '<none>'} were retained") from rollback_error
            raise original

        journal["phase"] = "commit_cleanup"
        try:
            _write_bound_journal_update(tree, journal, "commit_cleanup")
            committed, _raw, _marker = _read_bound_journal(tree)
            if committed["phase"] != "commit_cleanup":
                raise ValidationError("Authenticated runtime commit phase was not published")
        except BaseException as exc:
            # The marker may authenticate either side of the phase transition.
            # Recovery will roll back the old phase or finish the new phase.
            raise ValidationError("Runtime commit intent publication was interrupted; recover the retained transaction") from exc

        _verify_committed_payload(tree, journal, "before authenticated runtime backup cleanup")
        cleanup = _cleanup_committed_runtime(tree, journal)
        if not cleanup:
            _finalize_committed(tree, journal)
    if isinstance(result, dict):
        result = dict(result)
        result["transaction_cleanup"] = "COMPLETE" if not cleanup else "COMMITTED_CLEANUP_PENDING"
        result["transaction_cleanup_failures"] = cleanup
    return result


def recover(destination):
    with Tree(destination) as tree, install_lock(tree):
        journal_raw = _exists_read(tree, JOURNAL)
        marker_raw = _exists_read(tree, MARKER)
        if journal_raw is None and marker_raw is None:
            return {"status": "NO_PENDING_INSTALL"}
        if journal_raw is None:
            raise ValidationError("Installation recovery marker has no journal; recovery failed closed")
        if marker_raw is None:
            # A journal-only state is legitimate only before marker creation or
            # after marker deletion. Never use this unauthenticated artifact to
            # write project/runtime bytes; prove a completed endpoint and only
            # remove the orphan journal.
            try:
                journal = loads(journal_raw.decode("utf-8"))
                _validate_transaction_journal(journal)
            except (UnicodeDecodeError, ValueError, ValidationError) as exc:
                raise ValidationError("Unauthenticated journal-only recovery failed closed") from exc
            return {"status": _finalize_journal_only(tree, journal)}
        journal, bound_raw, bound_marker = _read_bound_journal(tree)
        journal = _recover_pending_journal_update(
            tree, journal, bound_raw, bound_marker)
        if journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"} and journal["phase"] in {
                "commit_cleanup", "commit_cleanup_authenticated"}:
            _verify_committed_payload(tree, journal, "before resumed runtime backup cleanup")
            cleanup = _cleanup_committed_runtime(tree, journal)
            if cleanup:
                return {"status": "COMMITTED_CLEANUP_PENDING", "transaction_cleanup_failures": cleanup}
            _finalize_committed(tree, journal)
            return {"status": "COMMITTED"}
        rollback(tree, journal)
        return {"status": "ROLLED_BACK"}


def install(source, destination, expected_digest, mode="install", conflict="error", overrides=None,
            dry_run=False, fail_after=None, *, codeowner="@maintainer", rules_observation=None,
            expected_rules_observation_sha256=None, default_branch=None, review_app_id=None,
            configure=False, discover=True, propose_operating_capacity=False,
            defer_runtime=False):
    validate_codeowner(codeowner)
    if mode not in {"install", "upgrade"} or conflict not in {"error", "backup"}:
        raise ValidationError("Only install/upgrade and error/backup are supported; skip was removed")
    if type(propose_operating_capacity) is not bool or (propose_operating_capacity and not configure):
        raise ValidationError("A capacity governance proposal requires explicit configured adoption")
    if type(defer_runtime) is not bool or (defer_runtime and dry_run):
        raise ValidationError("Deferred runtime commit is available only for a writing bootstrap transaction")
    if not expected_digest or len(expected_digest) != 64:
        raise ValidationError("Provide the externally approved manifest SHA-256")
    source, destination = Path(source).absolute(), Path(destination).absolute()
    overlap = source == destination or source.is_relative_to(destination) or destination.is_relative_to(source)
    test_scratch = source / ".tmp-tests"
    if overlap and not destination.is_relative_to(test_scratch):
        raise ValidationError("Source and destination trees must not overlap")
    with Tree(source) as src:
        # Installing from a pinned source checkout: root .git is excluded, never copied.
        digest, content = verify_release(src, expected_digest, allow_source_checkout=True)
        source_manifest_json = src.read(MANIFEST).decode("utf-8")
    from .upgrade import (OPERATING, apply_chain, config_diff,
                          identify_installation, immutable_from_source_manifest,
                          load_known_versions, state_archive_plan)
    known_versions = load_known_versions(content[KNOWN_VERSIONS]) if KNOWN_VERSIONS in content else None
    if known_versions is not None and known_versions["target"] != VERSION:
        raise ValidationError("Known-versions table target differs from the release")
    planned = {path: data for path, data in content.items() if managed(path)}
    if ROUTING in content:
        planned[ROUTING] = content.get(".agentic/defaults/routing.yaml", content[ROUTING])
    from .model_routing import routing_context
    release_immutable = {path: data for path, data in planned.items()
                         if path not in {CONFIG, PROVENANCE, CODEOWNERS} | OPERATOR_CONFIG_PATHS}
    if CODEOWNERS in planned:
        planned[CODEOWNERS] = planned[CODEOWNERS].replace(b"@maintainer", codeowner.encode("ascii"))
    owner_report = {"status": "SEEDED" if CODEOWNERS in planned else "NOT_IN_SOURCE",
                    "path": CODEOWNERS, "requested_owner": codeowner, "project_owned": True,
                    "requested_owner_applied": CODEOWNERS in planned}
    def rules_report():
        config = load_yaml(planned[CONFIG])
        github = config.get("github") if isinstance(config, dict) else None
        repository = github.get("repository") if isinstance(github, dict) else None
        return load_observation_report(rules_observation, expected_rules_observation_sha256,
            repository=repository, default_branch=default_branch, review_app_id=review_app_id)
    configuration_report = None
    governance_proposal = None
    upgrade_report = None
    deletions = []
    archive_paths = []
    ignore_report = {"status": "NOT_IN_SOURCE", "path": GITIGNORE, "project_owned": True,
                     "audit_policy": "Version .agentic-state/operating/changes/; review existing project ignore rules if they hide it."}
    def ignore_plan(existing_ignore=None):
        if GITIGNORE_TEMPLATE in content:
            planned[GITIGNORE], report = operating_ignore_plan(existing_ignore, content[GITIGNORE_TEMPLATE])
            ignore_report.update(report)
    def configure_plan(existing_config=None, existing_receipt=None):
        nonlocal configuration_report, governance_proposal
        from .adoption_config import prepare_config, operating_capacity_proposal
        from .contracts import Contracts
        from .policy import inspect_config
        existing = load_yaml(existing_config) if existing_config is not None else None
        result = prepare_config(load_yaml(planned[CONFIG]), existing=existing,
            receipt_project_id=(existing_receipt or {}).get("project_id"), project_root=destination,
            overrides=overrides, codeowner=codeowner, discover=discover)
        cfg = result.pop("config")
        cfg, governance_proposal = operating_capacity_proposal(cfg, existing=existing_config is not None,
            requested=propose_operating_capacity, dry_run=dry_run)
        planned[CONFIG] = existing_config if existing == cfg else json_bytes(cfg)
        inspection = inspect_config(cfg, load_yaml(content[".agentic/workflow.yaml"]), Contracts(source / ".agentic/schemas"))
        configuration_report = {**inspection, "discovery": result}
    if mode == "install" and overrides and not configure:
        cfg = load_yaml(planned[CONFIG])
        cfg["project"].update({key: value for key, value in overrides.items() if key in {"name", "short_name"}})
        if "repository" in overrides:
            cfg["github"]["repository"] = overrides["repository"]
        if "jira_key" in overrides:
            cfg["jira"]["project_key"] = overrides["jira_key"]
        planned[CONFIG] = json_bytes(cfg)
    if dry_run and not destination.exists():
        # Pin the existing parent without creating the destination.
        with Tree(destination.parent), routing_context(planned[ROUTING], ROUTING):
            if configure:
                configure_plan()
            ignore_plan()
            from .operating import operating_applicability
            operating_report = operating_applicability(load_yaml(planned[CONFIG]))
            return {"status": "PLAN", "managed_files": sorted(planned), "source_manifest_sha256": digest,
                    "installed": False, "configuration": configuration_report, "governance_proposal": governance_proposal,
                    "gitignore": ignore_report, "codeowners": owner_report,
                    "operating": operating_report, **rules_report()}
    with Tree(destination, create=not dry_run) as dst, ExitStack() as planning_context:
        if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
            raise ValidationError("An interrupted installation requires --recover")
        existing_config = _exists_read(dst, CONFIG)
        existing_routing = _exists_read(dst, ROUTING)
        if existing_routing is not None and ROUTING in content:
            planned[ROUTING] = existing_routing
        planning_context.enter_context(routing_context(planned[ROUTING], ROUTING))
        receipt_raw = _exists_read(dst, INSTALLED)
        try:
            existing_receipt = loads(receipt_raw.decode()) if receipt_raw is not None else None
        except (UnicodeDecodeError, ValueError):
            if mode != "upgrade":
                raise
            existing_receipt = {}
        if mode != "upgrade":
            assert_quiescent(existing_config)
        planning_inputs = {CONFIG: existing_config, INSTALLED: receipt_raw}
        if GITIGNORE_TEMPLATE in content:
            planning_inputs[GITIGNORE] = _exists_read(dst, GITIGNORE)
            ignore_plan(planning_inputs[GITIGNORE])
        owner_inputs = {}
        if CODEOWNERS in planned:
            for candidate in (CODEOWNERS, "CODEOWNERS", "docs/CODEOWNERS"):
                existing_owners = _exists_read(dst, candidate)
                # Retain the examined precedence prefix, including absent higher
                # locations. A new root/docs policy must not be silently shadowed.
                owner_inputs[candidate] = existing_owners
                if existing_owners is not None:
                    if candidate == CODEOWNERS:
                        planned[CODEOWNERS] = existing_owners
                    else:
                        # A new .github file would shadow GitHub's lower-priority
                        # root/docs ownership file; preserve that effective policy.
                        del planned[CODEOWNERS]
                    owner_report.update(status="PRESERVED", path=candidate, requested_owner_applied=False,
                                        existing_sha256=sha256(existing_owners))
                    break
        if mode == "upgrade":
            if _exists_read(dst, INSTALLED) is None or existing_config is None:
                raise ValidationError("Upgrade requires a verified AWF installation receipt and project configuration")
            installed_version = existing_receipt.get("template_version") if isinstance(existing_receipt, dict) else None
            # The managed runtime may legitimately contain venv-internal links
            # (POSIX ``lib64 -> lib``); it is not project state, so never walk it.
            state = {path: dst.read(path) for path in dst.file_list(exclude_root_git=True,
                                                                     exclude_prefixes=(RUNTIME + "/",))
                     if path.startswith(".agentic-state/")}
            operating = _exists_read(dst, OPERATING)
            provenance_raw = _exists_read(dst, PROVENANCE)
            if provenance_raw is None:
                raise ValidationError("Unrecognised AWF installation: workflow provenance is missing; no files changed")
            existing_codeowners = _exists_read(dst, CODEOWNERS)
            if installed_version == VERSION:
                _verify_installed(destination, VERSION)
                source_manifest = loads(existing_receipt["source_manifest_json"])
                identity = {"version": VERSION, "immutable_files": immutable_from_source_manifest(source_manifest),
                            "install_id": existing_receipt.get("install_id")}
                migrated_config, step_reports = existing_config, []
                total_diff = ""
            else:
                if known_versions is None:
                    raise ValidationError("Release is missing the known-versions table required for a historical upgrade")
                identity = identify_installation(dst, known_versions, existing_config, receipt_raw)
                target_entry = {"source_manifest_sha256": digest,
                                "_source_manifest_json": source_manifest_json,
                                "_immutable_files": {path: sha256(data) for path, data in release_immutable.items()}}
                bundle, step_reports = apply_chain(known_versions, identity["version"], existing_config,
                                                   operating, receipt_raw, provenance_raw, state, target_entry)
                migrated_config = bundle.project_config
                total_diff = config_diff(existing_config, migrated_config, identity["version"], VERSION)
            assert_quiescent(existing_config)
            planned[CONFIG] = migrated_config
            if existing_codeowners is not None:
                planned[CODEOWNERS] = existing_codeowners
                owner_report = {"status": "PRESERVED", "path": CODEOWNERS,
                                "project_owned": True, "requested_owner": None}
            old_files = identity["immutable_files"]
            old_paths, new_paths = set(old_files), set(release_immutable)
            collisions = []
            for path in sorted(new_paths - old_paths):
                if dst.inspect(path) is not None:
                    collisions.append(path)
            if collisions:
                raise ValidationError("Project-owned files collide with new managed paths; no files changed: " + ", ".join(collisions))
            deletions = sorted(old_paths - new_paths)
            changed = sorted(path for path in old_paths & new_paths if old_files[path] != sha256(release_immutable[path]))
            additions = sorted(new_paths - old_paths)
            migrated_state = state if identity["version"] == VERSION else dict(bundle.state)
            migrated_actions = []
            for path, data in sorted(migrated_state.items()):
                if state.get(path) != data:
                    planned[path] = data
                    migrated_actions.append({"path": path, "action": "migrated_schema",
                                             "source_sha256": sha256(state[path]),
                                             "target_sha256": sha256(data)})
            archives, archive_actions = (({}, []) if identity["version"] == VERSION
                                         else state_archive_plan(migrated_state))
            state_actions = migrated_actions + archive_actions
            for path, data in archives.items():
                if dst.inspect(path) is not None and dst.read(path) != data:
                    raise ValidationError("State archive path already exists with different bytes; no files changed: " + path)
                planned[path] = data
                archive_paths.append(path)
            upgrade_report = {"detected_version": identity["version"], "target_version": VERSION,
                              "steps": step_reports, "configuration_diff_total": total_diff,
                              "managed_files": {"added": additions, "changed": changed, "removed": deletions},
                              "state_migrations": state_actions,
                              "new_required_settings": [setting for step in step_reports for setting in step["new_required_settings"]],
                              "single_reviewed_change_set": True}
        if configure:
            configure_plan(planned[CONFIG] if mode == "upgrade" else existing_config, existing_receipt)
        from .operating import inspect_operating, operating_applicability, plan_initialize_operating
        governance = load_yaml(planned[CONFIG])
        applicability = operating_applicability(governance)
        if applicability["status"] == "NOT_APPLICABLE":
            operating_plan = {"files": {}, "actions": [], "inspection": applicability}
        else:
            fresh_defaults = ".agentic/defaults/OPERATING_CONFIG.yaml"
            initial_operating = (load_yaml(content[fresh_defaults])
                                 if mode == "install" and existing_config is None and fresh_defaults in content else None)
            operating_plan = plan_initialize_operating(destination, governance, initial_operating=initial_operating)
        for path, data in operating_plan["files"].items():
            planned[path] = data
        if upgrade_report is not None:
            upgrade_report["operating_changes"] = operating_plan["actions"]
            upgrade_report["operating"] = operating_plan["inspection"]
        install_id = str(uuid.uuid4())
        if mode == "upgrade":
            install_id = identity["install_id"]
            try:
                install_id = str(uuid.UUID(install_id))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError("Installed receipt/provenance has no valid install_id; no files changed") from exc
        elif configure and existing_receipt:
            install_id = str(uuid.UUID(existing_receipt["install_id"]))
        version = {"template": {"name": "generic-agentic-development-workflow", "version": VERSION, "schema_revision": 3},
                   "installation": {"install_id": install_id, "last_operation": mode, "operation_at": now_text(),
                                    "source_manifest_sha256": digest, "profile": "manual_reference"}}
        planned[PROVENANCE] = json_bytes(version)
        immutable = {path: sha256(data) for path, data in planned.items()
                     if managed(path) and path not in {CONFIG, PROVENANCE, CODEOWNERS, GITIGNORE}}
        installed = {"template_version": VERSION, "source_manifest_sha256": digest, "immutable_files": immutable,
                     "source_manifest_json": source_manifest_json,
                     "initial_config_sha256": sha256(planned[CONFIG]), "mutable_paths": [CONFIG, CODEOWNERS], "install_id": install_id}
        if GITIGNORE in planned:
            installed["mutable_paths"].append(GITIGNORE)
        if configure:
            installed["project_id"] = configuration_report["discovery"]["project_id"]
        elif existing_receipt and "project_id" in existing_receipt:
            installed["project_id"] = existing_receipt["project_id"]
        planned[INSTALLED] = json_bytes(installed)
        originals, conflicts = {}, []
        for path, data in planned.items():
            old = _exists_read(dst, path)  # Pins and validates every existing parent.
            if path in planning_inputs and old != planning_inputs[path]:
                raise ValidationError("Destination changed while preparing adoption: " + path)
            originals[path] = old
            state_migration = mode == "upgrade" and path.startswith(".agentic-state/")
            if old is not None and old != data and path != GITIGNORE and not (
                    mode == "upgrade" and managed(path)) and not state_migration:
                conflicts.append(path)
        for path in deletions:
            if path in originals:
                raise ValidationError("Upgrade plan both writes and removes a managed path: " + path)
            originals[path] = _exists_read(dst, path)
            if originals[path] is None:
                raise ValidationError("Managed file disappeared while preparing upgrade: " + path)
        write_plan = [{"path": path, "action": "create" if originals[path] is None else
                       "write_same" if originals[path] == data else "replace"}
                      for path, data in sorted(planned.items())]
        write_plan.extend({"path": path, "action": "delete"} for path in deletions)
        if conflicts and conflict == "error":
            raise ValidationError("Conflicting files; no managed files written: " + ", ".join(sorted(conflicts)))
        if dry_run:
            return {"status": "PLAN", "managed_files": sorted(planned), "conflicts": conflicts, "source_manifest_sha256": digest,
                    "installed": False, "configuration": configuration_report, "governance_proposal": governance_proposal,
                    "gitignore": ignore_report, "codeowners": owner_report, "upgrade": upgrade_report,
                    "write_plan": write_plan, "operating": operating_plan["inspection"], **rules_report()}
        adoption_rules = rules_report()
        with install_lock(dst):
            # Re-read while locked: another cooperating installer or editor may
            # have changed a leaf between preflight and lock acquisition.
            if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
                raise ValidationError("Another installation changed destination state")
            for path, old in owner_inputs.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("CODEOWNERS selection changed after preflight; preserve current project ownership and retry planning")
            for path, old in originals.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("Destination changed after preflight")
            managed_before = _managed_file_inventory(dst)
            transaction_id = str(uuid.uuid4())
            backup_plan = {}
            if conflict == "backup":
                for path, old in originals.items():
                    proposed = planned.get(path)
                    if old is not None and old != proposed:
                        backup_path = f".agentic-backup/{transaction_id}/{path}"
                        if dst.inspect(backup_path) is not None:
                            raise ValidationError("Transaction backup path appeared after preflight")
                        backup_plan[backup_path] = old
            recovery_objects = {}

            def recovery_entry(path, old, new_digest):
                info = dst.inspect(path)
                old_digest = sha256(old) if old is not None else None
                if old is not None:
                    recovery_objects[old_digest] = old
                return {"path": path,
                        "old_sha256": old_digest,
                        "old_size": len(old) if old is not None else None,
                        "old_mode": stat.S_IMODE(info.st_mode) if info is not None else None,
                        "new_sha256": new_digest, "new_mode": None}

            changed_planned = [(path, data) for path, data in sorted(planned.items())
                               if originals[path] != data]
            journal = {"format": "awf-install-journal-4",
                "transaction_id": transaction_id,
                "phase": "object_prepare",
                "files": [recovery_entry(path, originals[path], sha256(data))
                          for path, data in changed_planned]
                          + [recovery_entry(path, originals[path], None) for path in deletions]
                          + [recovery_entry(path, None, sha256(data))
                             for path, data in sorted(backup_plan.items())],
                "managed_before_sha256": _managed_inventory_sha256(managed_before),
                "managed_after_sha256": None,
                "runtime": None}
            if defer_runtime:
                journal["runtime"] = _runtime_journal(destination, transaction_id)
            # Bind the complete object inventory before creating it. An abrupt
            # stop during side-store creation therefore leaves recoverable intent,
            # while no destination payload changes before every object validates.
            _publish_initial_intent(dst, journal)
            _write_recovery_objects(dst, recovery_objects)
            journal["phase"] = "active"
            _write_bound_journal_update(dst, journal, "activate")
            try:
                for path, data in sorted(backup_plan.items()):
                    dst.write(path, data)
                for count, (path, data) in enumerate(sorted(planned.items()), 1):
                    dst.write(path, data)
                    if fail_after == count:
                        raise OSError("Injected installation failure")
                for offset, path in enumerate(deletions, len(planned) + 1):
                    dst.unlink(path)
                    if fail_after == offset:
                        raise OSError("Injected installation failure")
                for path, data in planned.items():
                    if dst.read(path) != data:
                        raise ValidationError(f"Installed byte verification failed: {path}")
                for path in deletions:
                    if dst.inspect(path) is not None:
                        raise ValidationError(f"Removed managed file remains after upgrade: {path}")
                operating_report = (operating_plan["inspection"] if applicability["status"] == "NOT_APPLICABLE"
                                    else inspect_operating(destination, governance))
                if applicability["status"] != "NOT_APPLICABLE" and operating_report["status"] != "ACCEPTED":
                    raise ValidationError("Operating initialization failed after staged writes: " +
                                          "; ".join(item["path"] + ": " + item["reason"]
                                                    for item in operating_report["refusals"]))
                for path in archive_paths:
                    if originals[path] is None:
                        (destination / Path(path)).chmod(stat.S_IREAD)
                _capture_managed_after(dst, journal)
                _write_bound_journal_update(dst, journal, "managed_after")
                _verify_journal_managed_state(dst, journal, "after", "after managed writes")
                # Bootstrap keeps the same journal/marker until the canonical
                # runtime has been staged, swapped and validated.
                if not defer_runtime:
                    journal["phase"] = "commit_cleanup"
                    _write_bound_journal_update(dst, journal, "commit_cleanup")
                    _finalize_committed(dst, journal)
            except Exception:
                rollback(dst, journal)
                raise
        result = {"status": "INSTALLED" if mode == "install" else "UPGRADED", "template_version": VERSION,
                "install_id": install_id, "source_manifest_sha256": digest, "managed_files": len(planned),
                "profile": "manual_reference", "live_automation_enabled": False,
                "live_automation_enabled_scope": "reference controller and installed background services",
                "native_streams_dispatch_owner": "native host coordinator using the project's execution.native_streams policy",
                "installer_launches_agents": False, "configuration": configuration_report,
                "governance_proposal": governance_proposal, "gitignore": ignore_report,
                "operating": operating_report, "write_plan": write_plan,
                "codeowners": owner_report, "upgrade": upgrade_report, **adoption_rules}
        if defer_runtime:
            result["_runtime_transaction_id"] = transaction_id
        return result
