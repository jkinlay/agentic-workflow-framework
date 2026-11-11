"""Manifest-verified, journaled installation of managed workflow files."""
from __future__ import annotations
import base64
from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
import uuid

from . import ValidationError, VERSION
from .canonical import MAX_DOCUMENT_BYTES, load_yaml, loads, now_text, sha256, validate_value
from .safeio import (Tree, relative_parts, validate_windows_metadata,
                     windows_directory_identity as _windows_directory_identity)
from .providers.github import load_observation_report, validate_codeowner

MANIFEST = "MANIFEST.json"
LOCK = ".agentic-install/lock"
JOURNAL = ".agentic-install/journal.json"
RETAINED_JOURNAL = ".agentic-install/retained-journal.json"
DIRECTORY_ADOPTIONS = ".agentic-install/directory-adoptions.json"
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


# New files and private temporaries start with this mode. Tree.write preserves
# existing POSIX modes before publication, including managed files and ledgers.
INSTALLER_WRITE_MODE = 0o600
INSTALL_JOURNAL_HEADROOM_BYTES = 520 * 1024
INSTALL_JOURNAL_MAX_BYTES = MAX_DOCUMENT_BYTES - INSTALL_JOURNAL_HEADROOM_BYTES
PENDING_UPDATE_KINDS = frozenset({
    "directory_prepare",
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
    _bind_transaction_temporaries(tree, journal, initial=True)
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


def _recover_legacy_pending_update(tree, journal, journal_raw, marker, *, publish=True):
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
    if publish:
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
    if update_kind == "directory_prepare":
        valid = (_compact_journal(journal) and journal["phase"] == "object_prepare" and
                 all(row["identity"] is None or
                     row["identity"] == _directory_identity(tree, row["path"])
                     for row in journal.get("created_directories", [])))
    elif update_kind == "activate":
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
    if update_kind == "directory_prepare":
        if not (_compact_journal(journal) and journal["phase"] == "object_prepare"):
            _pending_transition_mismatch()
        # Reconstruct numbers only to authenticate the pending transition. IDs
        # can be reused: a matching digest proves no object continuity and must
        # never grant deletion authority. No project payload has been written.
        for row in candidate.get("created_directories", []):
            row["identity"] = (_directory_identity(tree, row["path"])
                               if os.name == "nt" else None)
    elif update_kind == "activate":
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


def _recover_pending_journal_update(tree, journal, journal_raw, marker, *, publish=True):
    """Resolve one explicitly named, digest-bound journal replacement."""
    pending_digest = marker["pending_journal_sha256"]
    if pending_digest is None:
        return journal
    if marker["format"] == "awf-install-marker-1":
        return _recover_legacy_pending_update(tree, journal, journal_raw, marker, publish=publish)
    update_kind = marker.get("pending_update_kind")
    if update_kind not in PENDING_UPDATE_KINDS:
        raise ValidationError(
            "Unknown or ambiguous pending journal update kind; journal retained")
    observed_digest = sha256(journal_raw)
    if observed_digest == pending_digest:
        _validate_pending_new_journal(tree, journal, update_kind)
        if publish:
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

    if update_kind == "directory_prepare":
        # Keep the old unproved identities durably, including across another
        # interruption. Only the creating handle can publish ownership proof.
        if publish:
            tree.write(MARKER, json_bytes(_journal_marker(
                journal["transaction_id"], observed_digest)))
        return journal

    if update_kind in {"runtime_stage", "commit_cleanup_authenticated"}:
        if publish:
            tree.write(JOURNAL, candidate_raw)
            tree.write(MARKER, json_bytes(_journal_marker(
                candidate["transaction_id"], sha256(candidate_raw))))
        return candidate

    # Old-side activate/managed/commit/rollback transitions deliberately keep
    # the old journal. Collapse the precommit and follow its recovery rule.
    if publish:
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
        if relative in getattr(tree, '_installer_temporary_paths', ()):
            return
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
            raise ValidationError("Managed inventory root is linked or not a directory: .agentic/")

        def visit(directory):
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as exc:
                raise ValidationError("Managed inventory is unreadable: " + directory.relative_to(tree.root).as_posix()) from exc
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


def _temporary_targets(journal):
    """Every atomic-write destination, including recovery and coordination writes."""
    targets = {JOURNAL, MARKER, RETAINED_JOURNAL, *(item['path'] for item in journal['files'])}
    if journal.get('directory_adoptions'):
        targets.add(DIRECTORY_ADOPTIONS)
    targets.update(_recovery_object_path(item['old_sha256']) for item in journal['files']
                   if item['old_sha256'] is not None)
    targets.update(_recovery_object_path(row['sha256'])
                   for row in journal.get('windows_metadata', {}).values())
    if journal.get('temporary_files') == 'sibling-uuid5-v2':
        # Immutable evidence publications are sequential and share one exact
        # sibling temporary, committed before any evidence object is created.
        targets.add(EVIDENCE_OBJECTS + '/object.json')
    return targets


def _transaction_temporary_paths(journal):
    """Derive exact sibling names from the bound transaction and recorded targets.

    The versioned scheme avoids duplicating every target in the compact journal.
    A journal cannot nominate an arbitrary adopter look-alike for deletion.
    """
    # Older journals deliberately acquire no authority over unrecorded names.
    if 'temporary_files' not in journal:
        return {}
    if (not _compact_journal(journal) or not isinstance(journal['temporary_files'], str) or
            journal['temporary_files'] not in {
            'sibling-uuid5-v1', 'sibling-uuid5-v2'}):
        raise ValidationError('Invalid installation temporary-file inventory')
    namespace = uuid.UUID(journal['transaction_id'])
    return {target: (Path(target).parent / f'.awf-{uuid.uuid5(namespace, target).hex}.tmp').as_posix()
            for target in sorted(_temporary_targets(journal))}


def _bind_transaction_temporaries(tree, journal, *, initial=False):
    paths = _transaction_temporary_paths(journal)
    for path in paths.values():
        info = tree.inspect(path)
        if info is not None:
            if initial:
                raise ValidationError('Installation temporary path already exists; no payload changed')
            # Confined read rejects links, hardlinks and unsafe Windows metadata.
            tree.read(path)
    tree.write_temporary_names = {target: Path(path).name for target, path in paths.items()}
    tree._installer_temporary_paths = frozenset(paths.values())
    tree._installer_evidence_temporary = paths.get(EVIDENCE_OBJECTS + '/object.json')


def _cleanup_transaction_temporaries(tree, journal):
    """Remove only exact journal-bound sibling names, never glob look-alikes."""
    for path in _transaction_temporary_paths(journal).values():
        tree.unlink(path)


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


def _directory_identity(tree, relative):
    parent, handle, name = tree.parent(relative)
    if os.name == "nt":
        return _windows_directory_path_identity(parent / name)
    return _posix_directory_identity(handle, name, relative)


def _posix_directory_identity(handle, name, relative):
    info = os.stat(name, dir_fd=handle, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or
            getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValidationError("Unsafe created directory; journal retained: " + relative)
    return [format(info.st_dev, "032x"), format(info.st_ino, "032x")]


def _windows_directory_path_identity(path):
    """Pin a no-follow directory for the same identity API used at creation."""
    from .safeio import kernel, win_open
    handle = win_open(path, directory=True)
    try:
        return _windows_directory_identity(handle)
    finally:
        kernel.CloseHandle(handle)


def _windows_create_directory(parent_handle, name):
    """FILE_CREATE atomically creates a directory and returns its owning handle.

    RootDirectory is the pinned parent. FILE_CREATE refuses existing objects;
    denying delete sharing pins the created object through identity capture.
    """
    import ctypes
    from ctypes import wintypes
    from .safeio import kernel

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)),
                    ("Attributes", wintypes.ULONG), ("SecurityDescriptor", ctypes.c_void_p),
                    ("SecurityQualityOfService", ctypes.c_void_p)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    create = ntdll.NtCreateFile
    create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
        ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock), ctypes.c_void_p,
        wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
        ctypes.c_void_p, wintypes.ULONG]
    create.restype = wintypes.LONG
    buffer = ctypes.create_unicode_buffer(name)
    length = len(name.encode("utf-16-le"))
    unicode_name = UnicodeString(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent_handle,
                                 ctypes.pointer(unicode_name), 0x40, None, None)
    owned, result = wintypes.HANDLE(), IoStatusBlock()
    # DELETE access is deliberate: a metadata-only handle does not enforce
    # delete-sharing against rename on Windows.
    status = create(ctypes.byref(owned), 0x100000 | 0x10000 | 0x80, ctypes.byref(attributes),
                    ctypes.byref(result), None, 0x10, 3, 2,
                    0x1 | 0x20 | 0x00200000, None, 0)
    if status < 0:
        # Keep diagnostics path-free; no failed create grants deletion authority.
        raise ValidationError("Atomic directory creation failed; journal retained")
    try:
        if result.Information != 2:  # FILE_CREATED, never FILE_OPENED.
            raise ValidationError("Directory creation was not proven; journal retained")
        return _windows_directory_identity(owned)
    finally:
        kernel.CloseHandle(owned)


def _prepare_transaction_directories(tree, journal):
    for row in journal["created_directories"]:
        parent, handle, name = tree.parent(row["path"])
        if os.name == "nt":
            row["identity"] = _windows_create_directory(handle, name)
        else:
            os.mkdir(name, dir_fd=handle)
            os.fsync(handle)
            # mkdir has no creating handle. A subsequent open/stat could refer
            # to a replacement: leave ownership unproved and retain on rollback.
            row["identity"] = None
    # Publish creating-handle identities before objects or project payload can
    # change. Unproved directories remain recorded for retention on rollback.
    _write_bound_journal_update(tree, journal, "directory_prepare")


def _cleanup_transaction_directories(tree, journal):
    retained = []
    unresolved = set((_retained_directory_result(tree) or {}).get('retained_directories', []))
    for row in reversed(journal.get("created_directories", [])):
        if row["identity"] is None or row['path'] in unresolved:
            # Retention belongs to a declared rollback transaction. An unbound
            # cleanup request cannot classify a pre-existing owner directory as
            # transaction residue. Normal callers validate the complete journal.
            if journal.get("format") != "awf-install-journal-4" or journal.get("phase") not in {
                    "object_prepare", "active", "rollback_cleanup"}:
                raise ValidationError("Created directory changed or unbound; journal retained: " + row["path"])
            # POSIX mkdir supplies no creating handle, and interrupted ownership
            # publication supplies no continuity proof on any OS. Retain these
            # paths in every phase; never promote a later lookup or recycled ID.
            try:
                # This lookup establishes existence for reporting only. Its
                # numbers never grant ownership or deletion authority.
                _directory_identity(tree, row["path"])
            except FileNotFoundError:
                continue
            retained.append(row["path"])
            print("WARNING: Directory retained; ownership unproved: " + row["path"],
                  file=sys.stderr)
            continue
        # Release cached child handles (Windows denies deletion while pinned),
        # then pin the parent again before checking identity and empty removal.
        tree.close()
        try:
            identity = _directory_identity(tree, row["path"])
        except FileNotFoundError:
            continue
        if identity != row["identity"]:
            raise ValidationError("Created directory changed; journal retained: " + row["path"])
        parent, handle, name = tree.parent(row["path"])
        try:
            if os.name == "nt":
                # Delete through the checked handle. Omitting SHARE_DELETE
                # prevents replacement between the identity check and disposition.
                import ctypes
                from .safeio import (kernel, INVALID_HANDLE, FileDispositionInformation,
                                     _win_set_file_information, FILE_DISPOSITION_INFO)
                owned = kernel.CreateFileW(str(parent / name), 0x10000 | 0x80,
                                           3, None, 3, 0x00200000 | 0x02000000, None)
                if owned == INVALID_HANDLE:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if _windows_directory_identity(owned) != row["identity"]:
                        raise ValidationError("Created directory changed; journal retained: " + row["path"])
                    _win_set_file_information(owned, FILE_DISPOSITION_INFO,
                                              FileDispositionInformation(True))
                finally:
                    kernel.CloseHandle(owned)
            else:
                # POSIX rmdir has no expected-inode argument, including dir_fd.
                # Another actor can replace the leaf after any stat/open check.
                raise ValidationError("Created directory is not safely empty: cannot prove "
                                      "identity-bound deletion on POSIX; journal retained: " + row["path"])
        except OSError as exc:
            raise ValidationError("Created directory is not safely empty; journal retained: " + row["path"]) from exc
    return retained


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
    for reference in journal.get('windows_metadata', {}).values():
        digest, size = reference['sha256'], reference['size']
        if digest in sizes and sizes[digest] != size:
            raise ValidationError('Recovery metadata object size references disagree; journal retained')
        sizes[digest] = size
    objects = {}
    for digest, size in sizes.items():
        objects[digest] = _read_recovery_object(tree, digest, size)
    for reference in journal.get('windows_metadata', {}).values():
        validate_windows_metadata(loads(objects[reference['sha256']].decode('utf-8')))
    return objects


def _cleanup_recovery_objects(tree, journal):
    if not _compact_journal(journal):
        return
    for digest in sorted({item["old_sha256"] for item in journal["files"]
                          if item["old_sha256"] is not None} |
                         {ref['sha256'] for ref in journal.get('windows_metadata', {}).values()}):
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
            info = tree.inspect(item["path"])
            mode = stat.S_IMODE(info.st_mode)
            if mode != item["old_mode"]:
                raise ValidationError(f"Transaction old file mode differs at {item['path']}")
            owner = journal.get('posix_ownership', {}).get(item['path'])
            if owner is not None and (os.name == 'nt' or [info.st_uid, info.st_gid] != owner):
                raise ValidationError(f"Transaction old ownership differs at {item['path']}")
    for path, reference in journal.get('windows_metadata', {}).items():
        if os.name != 'nt' or sha256(json_bytes(tree.windows_metadata(path))) != reference['sha256']:
            raise ValidationError('Transaction old Windows metadata differs; journal retained: ' + path)
    _verify_runtime_state(
        tree, _validated_runtime_journal(journal), "old", require_cleanup=True,
        preserve_partial_stage=(journal["phase"] == "rollback_cleanup" and
                                journal["managed_after_sha256"] is not None))


def _finalize_compact_rollback(tree, journal, *, marker_present):
    """Retire recovery objects before the artifacts that track their ownership."""
    _verify_compact_old_endpoint(tree, journal, "at compact rollback endpoint")
    _cleanup_transaction_temporaries(tree, journal)
    _cleanup_recovery_objects(tree, journal)
    # Retaining unproved directories does not waive unrelated owner additions.
    # At the old endpoint their installer files are gone; unexpected file bytes
    # still refuse finalization, independently of any directory deletion proof.
    for row in journal.get("created_directories", []):
        if row["identity"] is None:
            try:
                members = tree.file_list(subtree=row["path"])
            except FileNotFoundError:
                continue
            if members:
                raise ValidationError("Recovery found an external directory member; journal retained: " + members[0])
    retained = _cleanup_transaction_directories(tree, journal)
    if retained:
        # Preserve the complete terminal journal and exact unresolved inventory
        # durably before either active coordination artifact can disappear.
        record = {'format': 'awf-retained-journal-1', 'journal': journal,
                  'retained_directories': retained}
        _merge_retained_evidence(tree, record)
    if marker_present:
        tree.unlink(MARKER)
    tree.unlink(JOURNAL)
    return retained


# Keep coordination documents small. Oversized sources have hash-bound parts;
# every stored document obeys the unchanged canonical parser bound.
_EVIDENCE_INLINE_MAXIMUM = 1024 * 1024
EVIDENCE_OBJECTS = '.agentic-install/evidence'


def _evidence_put(tree, value):
    raw = json_bytes(value)
    loads(raw.decode('utf-8'))  # Refuse before publication at the existing cap.
    digest = sha256(raw)
    path = EVIDENCE_OBJECTS + '/' + digest + '.json'
    previous = _exists_read(tree, path)
    if previous is not None and previous != raw:
        raise ValidationError('Retained evidence object differs from its digest')
    if previous is None:
        temporary = getattr(tree, '_installer_evidence_temporary', None)
        if temporary is not None:
            tree.write_temporary_names[path] = Path(temporary).name
            tree.write(path, raw)
        else:
            # Standalone diagnostics and legacy transactions cannot acquire
            # authority over a new sibling name. Publish only the exclusive
            # content-addressed leaf; an incomplete object fails its digest and
            # leaves the original source/journal available for owner inspection.
            tree.write_exclusive(path, raw)
    return digest


def _evidence_get(tree, digest):
    if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValidationError('Invalid retained evidence reference')
    raw = tree.read(EVIDENCE_OBJECTS + '/' + digest + '.json', maximum=8 * 1024 * 1024)
    if sha256(raw) != digest:
        raise ValidationError('Retained evidence object digest mismatch')
    return loads(raw.decode('utf-8'))


def _evidence_list_put(tree, items):
    head = None
    offset = 0
    while offset < len(items):
        page = dict(format='awf-evidence-page-1', previous=head, items=[])
        while offset < len(items) and len(page['items']) < 128:
            page['items'].append(items[offset])
            if len(json_bytes(page)) > _EVIDENCE_INLINE_MAXIMUM:
                page['items'].pop()
                break
            offset += 1
        if not page['items']:
            # A single large item is represented by a bounded source reference,
            # never by increasing either the page or document limit.
            page = dict(format='awf-evidence-page-2', previous=head,
                        items=[_evidence_source_put(tree, items[offset])])
            offset += 1
        head = _evidence_put(tree, page)
    return head


def _evidence_list_get(tree, head):
    pages, seen = [], set()
    while head is not None:
        if head in seen:
            raise ValidationError('Cyclic retained evidence index')
        seen.add(head)
        page = _evidence_get(tree, head)
        if (not isinstance(page, dict) or set(page) != {'format', 'previous', 'items'} or
                page['format'] not in {'awf-evidence-page-1', 'awf-evidence-page-2'} or
                not isinstance(page['items'], list) or
                not 1 <= len(page['items']) <= 128 or len(json_bytes(page)) > _EVIDENCE_INLINE_MAXIMUM):
            raise ValidationError('Invalid retained evidence page')
        if page['format'] == 'awf-evidence-page-2':
            if len(page['items']) != 1:
                raise ValidationError('Invalid retained evidence reference page')
            pages.append([_evidence_source_get(tree, page['items'][0])])
        else:
            pages.append(page['items'])
        head = page['previous']
    return [item for page in reversed(pages) for item in page]


def _evidence_value_put(tree, value):
    """Split JSON values structurally; no oversized document is parsed."""
    def encode(item):
        if len(json_bytes(item)) <= _EVIDENCE_INLINE_MAXIMUM // 4:
            return dict(value=item)
        return dict(reference=_evidence_value_put(tree, item))

    if isinstance(value, dict):
        kind = 'mapping'
        items = [[encode(key), encode(item)] for key, item in value.items()]
    elif isinstance(value, list):
        kind = 'sequence'
        items = [encode(item) for item in value]
    elif isinstance(value, str):
        kind = 'string'
        # Six bytes per escaped control character is the largest JSON encoding;
        # this also leaves room for multibyte scalars and page framing.
        width = _EVIDENCE_INLINE_MAXIMUM // 12
        items = [value[offset:offset + width] for offset in range(0, len(value), width)]
    else:
        raise ValidationError('Invalid oversized evidence value')
    return _evidence_put(tree, dict(format='awf-evidence-value-1', kind=kind,
                                   items=_evidence_list_put(tree, items)))


def _evidence_value_get(tree, reference, active=None):
    if not isinstance(reference, str) or re.fullmatch('[0-9a-f]{64}', reference) is None:
        raise ValidationError('Invalid retained evidence reference')
    active = set() if active is None else active
    if reference in active or len(active) > 64:
        raise ValidationError('Cyclic or overnested retained evidence value')
    active.add(reference)
    try:
        node = _evidence_get(tree, reference)
        if (not isinstance(node, dict) or set(node) != {'format', 'kind', 'items'} or
                node['format'] != 'awf-evidence-value-1'):
            raise ValidationError('Invalid retained evidence value')
        items = _evidence_list_get(tree, node['items'])

        def decode(item):
            if not isinstance(item, dict):
                raise ValidationError('Invalid retained evidence part')
            if set(item) == {'value'}:
                return item['value']
            if set(item) == {'reference'}:
                return _evidence_value_get(tree, item['reference'], active)
            raise ValidationError('Invalid retained evidence part')

        if node['kind'] == 'sequence':
            return [decode(item) for item in items]
        if node['kind'] == 'mapping':
            result = {}
            for pair in items:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValidationError('Invalid retained evidence mapping part')
                key = decode(pair[0])
                if not isinstance(key, str) or key in result:
                    raise ValidationError('Duplicate or invalid retained evidence mapping key')
                result[key] = decode(pair[1])
            return result
        if node['kind'] == 'string' and all(isinstance(item, str) for item in items):
            return ''.join(items)
        raise ValidationError('Invalid retained evidence value kind')
    finally:
        active.remove(reference)


def _evidence_source_put(tree, source):
    raw = json_bytes(source)
    if len(raw) <= MAX_DOCUMENT_BYTES:
        return _evidence_put(tree, source)
    validate_value(source)
    return _evidence_put(tree, dict(format='awf-evidence-source-1',
        source_sha256=sha256(raw), source_size=len(raw), value=_evidence_value_put(tree, source)))


def _evidence_source_get(tree, reference):
    source = _evidence_get(tree, reference)
    if isinstance(source, dict) and source.get('format') == 'awf-evidence-source-1':
        if (set(source) != {'format', 'source_sha256', 'source_size', 'value'} or
                type(source['source_size']) is not int or source['source_size'] <= MAX_DOCUMENT_BYTES):
            raise ValidationError('Invalid split retained evidence source')
        value = _evidence_value_get(tree, source['value'])
        validate_value(value)
        raw = json_bytes(value)
        if len(raw) != source['source_size'] or sha256(raw) != source['source_sha256']:
            raise ValidationError('Retained evidence source binding mismatch')
        return value
    return source


def _pack_retained_record(tree, record):
    _retained_record_result(record)
    if len(json_bytes(record)) <= _EVIDENCE_INLINE_MAXIMUM:
        return record
    return dict(format='awf-retained-directories-3',
        records=_evidence_list_put(tree, [_evidence_source_put(tree, source) for source in _retained_sources(record)]),
        retained_directories=_evidence_list_put(tree, record['retained_directories']))


def _unpack_retained_record(tree, record):
    if isinstance(record, dict) and record.get('format') == 'awf-retained-directories-3':
        if set(record) != {'format', 'records', 'retained_directories'} or tree is None:
            raise ValidationError('Invalid paged retained evidence')
        record = dict(format='awf-retained-directories-2',
            records=[_evidence_source_get(tree, digest) for digest in _evidence_list_get(tree, record['records'])],
            retained_directories=_evidence_list_get(tree, record['retained_directories']))
    _retained_record_result(record)
    return record


def _read_retained_record(tree):
    raw = _exists_read(tree, RETAINED_JOURNAL)
    return None if raw is None else _unpack_retained_record(tree, loads(raw.decode('utf-8')))


def _retained_directory_result(tree):
    record = _read_retained_record(tree)
    return None if record is None else _retained_record_result(record)


def _retained_record_result(record):
    if isinstance(record, dict) and record.get('format') == 'awf-retained-directories-2':
        if set(record) != {'format', 'records', 'retained_directories'}:
            raise ValidationError('Invalid merged retained-directory evidence')
        records, retained = record['records'], record['retained_directories']
        if (not isinstance(records, list) or not records or
                any(not isinstance(item, dict) or item.get('format') not in {
                    'awf-retained-journal-1', 'awf-retained-cleanup-1', 'awf-retained-cleanup-2'} for item in records)):
            raise ValidationError('Invalid retained evidence sources')
        results = [_retained_record_result(item) for item in records]
        allowed = {path for result in results for path in result['retained_directories']}
        if (not isinstance(retained, list) or not retained or
                any(not isinstance(path, str) or path not in allowed for path in retained) or
                len(set(retained)) != len(retained)):
            raise ValidationError('Invalid merged retained-directory inventory')
        rows = {row['path']: row for result in results for row in result.get('cleanup_pending', [])
                if row['path'] in retained}
        result = dict(status='RETAINED_DIRECTORIES', retained_directories=retained)
        if rows:
            result['cleanup_pending'] = list(rows.values())
        return result
    if isinstance(record, dict) and record.get('format') in {'awf-retained-cleanup-1', 'awf-retained-cleanup-2'}:
        keys = {'format', 'status', 'retained_directories', 'cleanup_pending'}
        if record['format'] == 'awf-retained-cleanup-2':
            keys.add('journal')
            journal = _validate_transaction_journal(record['journal'])
            runtime = _validated_runtime_journal(journal)
            allowed = ({runtime['backup'], _runtime_cleanup_path(Path('.'), runtime).as_posix()}
                       if runtime is not None else set())
            if (record['status'] != 'COMMITTED_CLEANUP_PENDING' or journal['phase'] not in {
                    'commit_cleanup', 'commit_cleanup_authenticated'} or
                    not set(record['retained_directories']) <= allowed):
                raise ValidationError('Retained cleanup is not bound to its committed transaction')
        if set(record) != keys:
            raise ValidationError('Invalid retained cleanup record')
        if record['status'] not in {'COMMITTED_CLEANUP_PENDING', 'RECOVERY_REFUSED'}:
            raise ValidationError('Invalid retained cleanup status')
        rows = record['cleanup_pending']
        if not isinstance(rows, list) or not rows:
            raise ValidationError('Invalid retained cleanup inventory')
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'path', 'size_bytes', 'reason', 'owner_action'}:
                raise ValidationError('Invalid retained cleanup entry')
            relative_parts(row['path'])
            if (row['size_bytes'] is not None and
                    (type(row['size_bytes']) is not int or row['size_bytes'] < 0)):
                raise ValidationError('Invalid retained cleanup size')
            if row['reason'] != _CLEANUP_REASON or row['owner_action'] not in {
                    _CLEANUP_OWNER_ACTION, _LEGACY_CLEANUP_OWNER_ACTION}:
                raise ValidationError('Invalid retained cleanup remedy')
        if record['retained_directories'] != [row['path'] for row in rows]:
            raise ValidationError('Invalid retained cleanup paths')
        return {key: record[key] for key in ('status', 'retained_directories', 'cleanup_pending')}
    if (not isinstance(record, dict) or set(record) != {
            'format', 'journal', 'retained_directories'} or
            record['format'] != 'awf-retained-journal-1'):
        raise ValidationError('Invalid retained installation journal')
    journal = _validate_transaction_journal(record['journal'])
    retained = record['retained_directories']
    allowed = {row['path'] for row in journal.get('created_directories', [])}
    if (not isinstance(retained, list) or not retained or
            any(not isinstance(path, str) or path not in allowed for path in retained) or
            len(set(retained)) != len(retained) or
            journal.get('phase') not in {'object_prepare', 'rollback_cleanup'}):
        raise ValidationError('Invalid retained-directory inventory')
    # Diagnostics never grant deletion authority. Successful transactions may
    # resolve only directories they actually reuse, without creation/deletion.
    return {'status': 'ROLLED_BACK', 'retained_directories': retained}


def retained_directory_status(destination):
    """Read unresolved restoration evidence without changing it."""
    with Tree(destination) as tree:
        return _retained_directory_result(tree)


_CLEANUP_REASON = 'Pathname recursive deletion refused; object continuity cannot be guaranteed'
_LEGACY_CLEANUP_OWNER_ACTION = ('Keep the project quiescent; inspect retained paths and journal, preserve '
                         'needed bytes, then recover or remove residue under exclusive owner control '
                         'and retire retained-journal.json only after resolving every entry')
_CLEANUP_OWNER_ACTION = ('A later transaction may adopt directories it reuses; inspect unadopted '
                         'paths and journal under exclusive owner control, preserve needed bytes '
                         'and perform owner recovery for residue nobody adopts')


def _retained_sources(record):
    _retained_record_result(record)
    return record['records'] if record['format'] == 'awf-retained-directories-2' else [record]


def _merge_retained_evidence(tree, record):
    """Preserve every prior source; no retention operation erases older residue."""
    raw = _exists_read(tree, RETAINED_JOURNAL)
    if raw is None:
        _retained_record_result(record)
        tree.write(RETAINED_JOURNAL, json_bytes(_pack_retained_record(tree, record)))
        return
    previous = _unpack_retained_record(tree, loads(raw.decode('utf-8')))
    paths = list(dict.fromkeys(_retained_record_result(previous)['retained_directories'] +
                              _retained_record_result(record)['retained_directories']))
    sources = {sha256(json_bytes(item)): item
               for item in _retained_sources(previous) + _retained_sources(record)}
    if len(sources) == 1 and paths == next(iter(sources.values()))['retained_directories']:
        merged = next(iter(sources.values()))
    else:
        merged = dict(format='awf-retained-directories-2', records=list(sources.values()),
                      retained_directories=paths)
    tree.write(RETAINED_JOURNAL, json_bytes(_pack_retained_record(tree, merged)))


def _plan_directory_adoptions(tree, parents):
    raw = _exists_read(tree, RETAINED_JOURNAL)
    if raw is None:
        return []
    record = _unpack_retained_record(tree, loads(raw.decode('utf-8')))
    retained = _retained_record_result(record)['retained_directories']
    rows = []
    for path in retained:
        if path not in parents:
            continue
        try:
            identity = _directory_identity(tree, path)
        except FileNotFoundError:
            continue  # Creating a new directory is not adopting an existing one.
        source = next(item for item in _retained_sources(record)
                      if path in item['retained_directories'])
        rows.append(dict(path=path, identity=identity, evidence_sha256=sha256(json_bytes(source))))
    return rows


def _directory_adoption_audit(raw, *, tree=None):
    audit = (loads(raw.decode('utf-8')) if raw is not None else
             dict(format='awf-directory-adoptions-1', transactions=[]))
    if isinstance(audit, dict) and audit.get('format') == 'awf-directory-adoptions-2':
        if set(audit) != {'format', 'transactions'} or tree is None:
            raise ValidationError('Invalid paged directory adoption audit')
        events = [_evidence_source_get(tree, ref) for ref in _evidence_list_get(tree, audit['transactions'])]
        audit = dict(format='awf-directory-adoptions-1', transactions=[
            dict(event, evidence=_unpack_retained_record(tree, event['evidence'])) for event in events])
    if (not isinstance(audit, dict) or set(audit) != {'format', 'transactions'} or
            audit['format'] != 'awf-directory-adoptions-1' or not isinstance(audit['transactions'], list)):
        raise ValidationError('Invalid retained-directory adoption audit')
    seen = set()
    for event in audit['transactions']:
        if not isinstance(event, dict) or set(event) != {'transaction_id', 'directories', 'resolution', 'evidence'}:
            raise ValidationError('Invalid directory adoption event')
        identity = event['transaction_id']
        try:
            canonical = str(uuid.UUID(identity))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError('Invalid adoption transaction identity') from exc
        allowed = _retained_record_result(event['evidence'])['retained_directories']
        paths = event['directories']
        if (identity != canonical or identity in seen or not isinstance(paths, list) or not paths or
                any(not isinstance(path, str) or path not in allowed for path in paths) or
                len(set(paths)) != len(paths) or event['resolution'] != 'adopted by transaction ' + identity):
            raise ValidationError('Invalid directory adoption resolution')
        seen.add(identity)
    return audit


def _write_directory_adoption_audit(tree, audit):
    if len(json_bytes(audit)) <= _EVIDENCE_INLINE_MAXIMUM:
        tree.write(DIRECTORY_ADOPTIONS, json_bytes(audit))
        return
    events = [_evidence_source_put(tree, dict(event, evidence=_pack_retained_record(tree, event['evidence'])))
              for event in audit['transactions']]
    tree.write(DIRECTORY_ADOPTIONS, json_bytes(dict(format='awf-directory-adoptions-2',
        transactions=_evidence_list_put(tree, events))))


def _adopt_transaction_directories(tree, journal):
    """Resolve reuse at the verified commit endpoint; replay never duplicates it."""
    raw = _exists_read(tree, RETAINED_JOURNAL)
    if raw is None or not journal.get('directory_adoptions'):
        return
    record = _unpack_retained_record(tree, loads(raw.decode('utf-8')))
    retained = _retained_record_result(record)['retained_directories']
    sources = _retained_sources(record)
    adopted = []
    for row in journal['directory_adoptions']:
        if row['path'] not in retained or not any(
                row['path'] in source['retained_directories'] and
                sha256(json_bytes(source)) == row['evidence_sha256'] for source in sources):
            continue
        try:
            if _directory_identity(tree, row['path']) == row['identity']:
                adopted.append(row['path'])
        except FileNotFoundError:
            pass
    if not adopted:
        return
    old_audit = _exists_read(tree, DIRECTORY_ADOPTIONS)
    audit = _directory_adoption_audit(old_audit, tree=tree)
    event = dict(transaction_id=journal['transaction_id'], directories=adopted,
                 resolution='adopted by transaction ' + journal['transaction_id'], evidence=record)
    previous = [item for item in audit['transactions'] if item.get('transaction_id') == journal['transaction_id']]
    if previous and previous != [event]:
        raise ValidationError('Retained-directory adoption replay differs')
    if not previous:
        audit['transactions'].append(event)
        _write_directory_adoption_audit(tree, audit)
    remaining = [path for path in retained if path not in adopted]
    if remaining:
        tree.write(RETAINED_JOURNAL, json_bytes(_pack_retained_record(tree, dict(
            format='awf-retained-directories-2', records=sources, retained_directories=remaining))))
    else:
        tree.unlink(RETAINED_JOURNAL)  # Evidence file only; directories are never deleted.


def _record_cleanup_pending(tree, paths, *, status, journal=None):
    """Diagnostic inventory only; size observations grant no deletion authority."""
    rows = {}
    for path in paths:
        relative = Path(path).relative_to(tree.root).as_posix()
        relative_parts(relative)
        size = 0
        try:
            root_info = os.lstat(path)
            if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode) or
                    getattr(root_info, 'st_file_attributes', 0) & 0x400):
                raise OSError('Size observation requires an ordinary directory')
            def incomplete(error):
                raise error
            for directory, directories, files in os.walk(path, followlinks=False, onerror=incomplete):
                for name in [*directories, *files]:
                    info = os.lstat(Path(directory) / name)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise OSError('Size observation cannot follow links')
                    if stat.S_ISREG(info.st_mode):
                        size += info.st_size
        except OSError:
            size = None
        rows[relative] = dict(path=relative, size_bytes=size, reason=_CLEANUP_REASON,
                              owner_action=_CLEANUP_OWNER_ACTION)
    record = dict(format='awf-retained-cleanup-1', status=status,
                  retained_directories=list(rows), cleanup_pending=list(rows.values()))
    if journal is not None:
        record.update(format='awf-retained-cleanup-2', journal=journal)
    _merge_retained_evidence(tree, record)
    return record['cleanup_pending']


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
    """Use the same streaming endpoint authentication as the recovery command."""
    from .preservation_metadata import runtime_tree_sha256
    return runtime_tree_sha256(path)


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
    proof = {"identity": (_windows_directory_path_identity(path) if os.name == "nt"
                          else identity(root)), "members": []}
    def visit(directory, prefix=""):
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            relative = entry.name if not prefix else prefix + "/" + entry.name
            info = entry.stat(follow_symlinks=False)
            reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode) and not reparse:
                object_identity = (_windows_directory_path_identity(entry.path)
                                   if os.name == "nt" else identity(info))
                proof["members"].append([relative, "directory", mode, object_identity, None])
                visit(entry.path, relative)
            elif stat.S_ISREG(info.st_mode) and not reparse:
                object_identity = identity(info)
                h = hashlib.sha256()
                with open(entry.path, "rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(block)
                proof["members"].append([relative, "file", mode, object_identity, h.hexdigest()])
            elif stat.S_ISLNK(info.st_mode) and not reparse:
                object_identity = identity(info)
                # Venvs on 64-bit POSIX create ``lib64 -> lib``. Accept only a
                # relative link whose lexical target stays inside the cleanup root;
                # This is an inventory observation, never deletion authority.
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
    raise ValidationError('Runtime cleanup pending: ' + _CLEANUP_REASON)


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
        _retained_directory_result(tree)  # Validate diagnostics without blocking reuse/recovery.


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
        receipt_id = manifest.get("install_id")
        provenance_id = version.get("installation", {}).get("install_id")
        if not isinstance(receipt_id, str) or not receipt_id or receipt_id != provenance_id:
            raise ValidationError("Installed receipt/provenance install_id values differ or are missing; no files changed")
        from .adoption_config import verify_import_membership
        verify_import_membership(root, source_manifest)
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
        keys = set(journal) - {'windows_metadata', 'posix_ownership', 'temporary_files', 'directory_adoptions'}
        if keys not in ({"format", "transaction_id", "phase", "files", "runtime",
                            "managed_before_sha256", "managed_after_sha256"},
                           {"format", "transaction_id", "phase", "files", "runtime",
                            "managed_before_sha256", "managed_after_sha256", "created_directories"}):
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
        def valid_identity(value, *, directory=False):
            # Legacy stat proofs have two 16-digit parts. Windows directory
            # proofs now use the same 32-digit FILE_ID_INFO parts as creation.
            # Keep legacy bytes readable, but never pad/convert them: exact
            # verification refuses them against a new handle proof before delete.
            return (isinstance(value, list) and len(value) == 2 and
                    any(all(isinstance(part, str) and
                            re.fullmatch(r"[0-9a-f]{%d}" % width, part) is not None
                            for part in value)
                        for width in ((16, 32) if directory else (16,))))
        if identity is not None and not valid_identity(identity, directory=True):
            raise ValidationError("Invalid authenticated cleanup identity")
        if members is not None:
            if not isinstance(members, list) or len(members) > 100000:
                raise ValidationError("Invalid authenticated cleanup inventory")
            seen_members = set()
            for row in members:
                if (not isinstance(row, list) or len(row) != 5 or not isinstance(row[0], str) or
                        row[1] not in {"directory", "file", "link"} or type(row[2]) is not int or
                        not valid_identity(row[3], directory=row[1] == "directory") or
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
    adoptions = journal.get('directory_adoptions', [])
    if not isinstance(adoptions, list) or len(adoptions) > 10000:
        raise ValidationError('Invalid directory adoption inventory')
    allowed_parents = {parent.as_posix() for item in journal['files'] for parent in Path(item['path']).parents
                       if parent.as_posix() != '.'} | {RECOVERY_OBJECTS}
    seen_adoptions = set()
    for row in adoptions:
        if not isinstance(row, dict) or set(row) != {'path', 'identity', 'evidence_sha256'}:
            raise ValidationError('Invalid directory adoption entry')
        relative_parts(row['path'])
        scoped_parent = (row['path'] in allowed_parents | {'.agentic', '.github', '.agentic-install'} or
                         _inventory_managed(row['path'] + '/retained-directory') or
                         row['path'].startswith('.agentic-state/'))
        if (not scoped_parent or row['path'] in seen_adoptions or
                not isinstance(row['identity'], list) or len(row['identity']) != 2 or
                any(not isinstance(part, str) or re.fullmatch('[0-9a-f]{32}', part) is None
                    for part in row['identity']) or not isinstance(row['evidence_sha256'], str) or
                re.fullmatch('[0-9a-f]{64}', row['evidence_sha256']) is None):
            raise ValidationError('Unsafe directory adoption identity/path')
        seen_adoptions.add(row['path'])
    directories = journal.get("created_directories", [])
    if not isinstance(directories, list) or len(directories) > 10000:
        raise ValidationError("Invalid created-directory inventory")
    seen_directories = set()
    for row in directories:
        if not isinstance(row, dict) or set(row) != {"path", "identity"}:
            raise ValidationError("Invalid created-directory entry")
        relative_parts(row["path"])
        allowed = {str(parent).replace("\\", "/") for item in journal["files"]
                   for parent in Path(item["path"]).parents if str(parent) != "."}
        allowed.add(RECOVERY_OBJECTS)
        if row["path"] not in allowed or row["path"].casefold() in seen_directories:
            raise ValidationError("Unsafe or duplicate created directory")
        seen_directories.add(row["path"].casefold())
        if row['path'] in seen_adoptions:
            raise ValidationError('Directory cannot be both created and adopted')
        identity = row["identity"]
        if identity is not None and (not isinstance(identity, list) or len(identity) != 2 or
                any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None
                    for value in identity)):
            raise ValidationError("Invalid created-directory identity")
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
    metadata = journal.get('windows_metadata', {})
    if not isinstance(metadata, dict):
        raise ValidationError('Invalid Windows recovery metadata inventory')
    for path, reference in metadata.items():
        relative_parts(path)
        if (not isinstance(reference, dict) or set(reference) != {'sha256', 'size'} or
                not isinstance(reference['sha256'], str) or
                not re.fullmatch('[0-9a-f]{64}', reference['sha256']) or
                type(reference['size']) is not int or not 0 < reference['size'] <= MAX_DOCUMENT_BYTES):
            raise ValidationError('Invalid Windows recovery metadata reference')
    ownership = journal.get('posix_ownership', {})
    if not isinstance(ownership, dict):
        raise ValidationError('Invalid POSIX recovery ownership inventory')
    for path, owner in ownership.items():
        relative_parts(path)
        if (not isinstance(owner, list) or len(owner) != 2 or
                any(type(value) is not int or value < 0 for value in owner)):
            raise ValidationError('Invalid POSIX recovery ownership')
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
        if item['path'] in metadata and not has_old:
            raise ValidationError('Windows recovery metadata has no original file')
        if item['path'] in ownership and not has_old:
            raise ValidationError('POSIX recovery ownership has no original file')
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
    if set(metadata) - {item['path'] for item in journal['files']}:
        raise ValidationError('Windows recovery metadata has no journalled path')
    if set(ownership) - {item['path'] for item in journal['files']}:
        raise ValidationError('POSIX recovery ownership has no journalled path')
    _transaction_temporary_paths(journal)
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
        _record_cleanup_pending(tree, [stage], status='RECOVERY_REFUSED')
        _remove_runtime_transaction_path(stage)

    if previous is None:
        if backup.exists() or backup.is_symlink():
            raise ValidationError("Fresh runtime recovery found an unexpected backup; journal retained")
        if canonical.exists() or canonical.is_symlink():
            if proposed is None or _runtime_tree_sha256(canonical) != proposed:
                raise ValidationError("Fresh runtime recovery cannot prove the canonical runtime is transaction-owned; journal retained")
            _record_cleanup_pending(tree, [canonical], status='RECOVERY_REFUSED')
            _remove_runtime_transaction_path(canonical)
        return

    if backup.exists() or backup.is_symlink():
        if _runtime_tree_sha256(backup) != previous:
            raise ValidationError("Runtime recovery backup differs from the exact prior runtime; journal retained")
        if canonical.exists() or canonical.is_symlink():
            if proposed is None or _runtime_tree_sha256(canonical) != proposed:
                raise ValidationError("Runtime recovery found an external canonical-runtime edit; journal retained")
            _record_cleanup_pending(tree, [canonical], status='RECOVERY_REFUSED')
            _remove_runtime_transaction_path(canonical)
        os.replace(backup, canonical)
    elif not (canonical.exists() or canonical.is_symlink()) or _runtime_tree_sha256(canonical) != previous:
        raise ValidationError("Runtime recovery cannot prove or restore the exact prior runtime; journal retained")
    if _runtime_tree_sha256(canonical) != previous:
        raise ValidationError("Runtime recovery verification failed; journal retained")


def rollback(tree, journal):
    _validate_transaction_journal(journal)
    # Refuse before any managed-file or temporary mutation. The stopgap cannot
    # restore an endpoint that requires deleting an existing runtime/backup.
    runtime = _validated_runtime_journal(journal)
    blocked = []
    if runtime is not None:
        stage = tree.root / runtime['stage']
        canonical = tree.root / runtime['path']
        backup = tree.root / runtime['backup']
        if (stage.exists() or stage.is_symlink()) and runtime['new_sha256'] is not None:
            if _runtime_tree_sha256(stage) != runtime['new_sha256']:
                raise ValidationError('Runtime recovery found an external edit in the staged runtime; journal retained')
            blocked.append(stage)
        if ((canonical.exists() or canonical.is_symlink()) and
                (runtime['previous_sha256'] is None or backup.exists() or backup.is_symlink())):
            blocked.append(canonical)
    legacy_backup = tree.root / '.agentic-backup' / journal['transaction_id']
    if 'created_directories' not in journal and (legacy_backup.exists() or legacy_backup.is_symlink()):
        blocked.append(legacy_backup)
    if blocked:
        _record_cleanup_pending(tree, blocked, status='RECOVERY_REFUSED')
        raise ValidationError('Rollback refused before managed-file mutation; cleanup pending')
    if _compact_journal(journal) and journal["phase"] in {"object_prepare", "rollback_cleanup"}:
        return _finalize_compact_rollback(tree, journal, marker_present=True)
    # Resolve and authenticate every recovery object before any project or
    # runtime mutation, so one missing/tampered object fails the whole rollback.
    recovery_objects = _load_recovery_objects(tree, journal)
    if journal.get('windows_metadata') and os.name != 'nt':
        raise ValidationError('Windows metadata recovery requires Windows; journal retained')
    if journal.get('posix_ownership') and os.name == 'nt':
        raise ValidationError('POSIX ownership recovery requires POSIX; journal retained')
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
        metadata_reference = journal.get('windows_metadata', {}).get(item['path'])
        if (metadata_reference is not None and current is not None and
                sha256(json_bytes(tree.windows_metadata(item['path']))) != metadata_reference['sha256']):
            raise ValidationError(f"Recovery found an external metadata edit at {item['path']}; marker retained")
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
    _cleanup_transaction_temporaries(tree, journal)
    _rollback_runtime(
        tree, runtime,
        preserve_partial_stage=(_compact_journal(journal) and
                                journal["phase"] == "active" and
                                journal["managed_after_sha256"] is not None))
    if mode_aware:
        tree.write_modes = {item['path']: item['old_mode'] for item in journal['files']
                            if item['old_mode'] is not None}
    tree.write_ownership = journal.get('posix_ownership', {})
    for item in reversed(journal["files"]):
        old = _old_file_bytes(journal, item, recovery_objects)
        if old is None:
            tree.unlink(item["path"])
        else:
            reference = journal.get('windows_metadata', {}).get(item['path'])
            if reference is None:
                tree.write(item['path'], old)
            else:
                tree.write(item['path'], old, windows_metadata=loads(recovery_objects[reference['sha256']].decode('utf-8')))
    if backup_root.exists() and "created_directories" not in journal:
        _record_cleanup_pending(tree, [backup_root], status='RECOVERY_REFUSED')
        raise ValidationError('Legacy backup cleanup pending; backup retained')
    if mode_aware:
        _verify_journal_managed_state(tree, journal, "before", "after rollback")
    if _compact_journal(journal):
        # Publish the restored endpoint before deleting any object. Recovery can
        # then prove the old state from hashes even after partial object cleanup.
        journal["phase"] = "rollback_cleanup"
        _write_bound_journal_update(tree, journal, "rollback_cleanup")
        return _finalize_compact_rollback(tree, journal, marker_present=True)
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
    if require_cleanup:
        remaining = [path.relative_to(tree.root).as_posix() for path in (backup, cleanup)
                     if path.exists() or path.is_symlink()]
        pending = {row['path'] for row in (_retained_directory_result(tree) or {}).get('cleanup_pending', [])}
        if remaining and not (which == 'new' and set(remaining) <= pending):
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
    """Retain committed backups; pathname cleanup cannot guarantee continuity."""
    runtime = _validated_runtime_journal(journal)
    if journal["phase"] not in {"commit_cleanup", "commit_cleanup_authenticated"}:
        raise ValidationError("Runtime cleanup requires authenticated commit intent")
    if runtime is None:
        if journal["phase"] == "commit_cleanup":
            journal["phase"] = "commit_cleanup_authenticated"
            _write_bound_journal_update(
                tree, journal, "commit_cleanup_authenticated")
        return []
    backup = tree.root / runtime["backup"]
    cleanup_path = _runtime_cleanup_path(tree.root, runtime)
    if runtime['previous_sha256'] is not None:
        retained = [path for path in (backup, cleanup_path)
                    if path.exists() or path.is_symlink()]
        if not retained:
            if journal['phase'] == 'commit_cleanup_authenticated':
                if (runtime.get('cleanup_identity') is None or
                        runtime.get('cleanup_members') is None):
                    raise ValidationError('Authenticated cleanup has no persisted ownership proof')
                # A pre-stopgap process may already have finished deletion.
                # The helper is a no-op for absence and refuses any new object.
                try:
                    _remove_runtime_transaction_path(cleanup_path)
                except (OSError, ValidationError) as exc:
                    if cleanup_path.exists() or cleanup_path.is_symlink():
                        return _record_cleanup_pending(tree, [cleanup_path], status='COMMITTED_CLEANUP_PENDING')
                    return [{'path': cleanup_path.relative_to(tree.root).as_posix(),
                             'error': type(exc).__name__}]
                return []
            raise ValidationError('Exact prior canonical runtime backup is missing before cleanup')
        if len(retained) != 1:
            raise ValidationError('Runtime backup/cleanup membership cannot be authenticated')
        path = retained[0]
        if journal['phase'] == 'commit_cleanup_authenticated' and path == cleanup_path:
            if runtime.get('cleanup_identity') is None or runtime.get('cleanup_members') is None:
                raise ValidationError('Authenticated cleanup has no persisted ownership proof')
            _verify_runtime_cleanup_proof(path, dict(identity=runtime['cleanup_identity'],
                                                    members=runtime['cleanup_members']))
        elif _runtime_tree_sha256(path) != runtime['previous_sha256']:
            raise ValidationError('Runtime backup cannot be authenticated; journal retained')
        rows = _record_cleanup_pending(tree, retained, status='COMMITTED_CLEANUP_PENDING', journal=journal)
        # Re-prove observations before retiring coordination, without deleting
        # or renaming the object they describe.
        if journal['phase'] == 'commit_cleanup_authenticated' and path == cleanup_path:
            _verify_runtime_cleanup_proof(path, dict(identity=runtime['cleanup_identity'],
                                                    members=runtime['cleanup_members']))
        elif _runtime_tree_sha256(path) != runtime['previous_sha256']:
            raise ValidationError('Runtime backup changed while being retained; journal retained')
        return rows
    if backup.exists() or backup.is_symlink() or cleanup_path.exists() or cleanup_path.is_symlink():
        raise ValidationError("Fresh runtime commit found an unauthenticated cleanup path or unexpected backup")
    if journal['phase'] == 'commit_cleanup':
        journal['phase'] = 'commit_cleanup_authenticated'
        _write_bound_journal_update(tree, journal, 'commit_cleanup_authenticated')
    return []


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
    _cleanup_transaction_temporaries(tree, journal)
    # A durable commit can no longer roll back, so its recovery objects may be
    # removed before retiring the bound journal and marker.
    _cleanup_recovery_objects(tree, journal)
    _adopt_transaction_directories(tree, journal)
    tree.unlink(MARKER)
    tree.unlink(JOURNAL)


def _finalize_journal_only(tree, journal, *, retained_directories=None):
    """Prove an endpoint before retiring its journal and coordination residue."""
    _validate_transaction_journal(journal)
    _bind_transaction_temporaries(tree, journal)
    if journal["format"] not in {"awf-install-journal-3", "awf-install-journal-4"}:
        raise ValidationError("Legacy journal-only recovery failed closed")
    if journal["phase"] in {"object_prepare", "rollback_cleanup"}:
        retained = _finalize_compact_rollback(tree, journal, marker_present=False)
        if retained_directories is not None:
            retained_directories.extend(retained)
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
    _cleanup_transaction_temporaries(tree, journal)
    _cleanup_recovery_objects(tree, journal)
    if status == 'COMMITTED':
        _adopt_transaction_directories(tree, journal)
    tree.unlink(JOURNAL)
    return status


def complete_runtime_transaction(destination, transaction_id, builder):
    """Build the canonical runtime, retaining authenticated recovery through cleanup."""
    destination = Path(destination).absolute()
    cleanup = []
    with Tree(destination) as tree, install_lock(tree):
        journal, _raw, _marker = _read_bound_journal(tree)
        _bind_transaction_temporaries(tree, journal)
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
        if not cleanup or all('reason' in row for row in cleanup):
            _finalize_committed(tree, journal)
        retained_result = _retained_directory_result(tree)
    if isinstance(result, dict):
        result = dict(result)
        result["transaction_cleanup"] = "COMPLETE" if not cleanup else "COMMITTED_CLEANUP_PENDING"
        result["transaction_cleanup_failures"] = cleanup
        if retained_result:
            result['installation_recovery'] = retained_result
    return result


def _with_retained_inventory(tree, result):
    retained = _retained_directory_result(tree)
    if retained:
        result['retained_directories'] = retained['retained_directories']
        if result['status'] != 'ROLLED_BACK':
            result['installation_recovery'] = retained
    return result


def recover(destination):
    with Tree(destination) as tree, install_lock(tree):
        journal_raw = _exists_read(tree, JOURNAL)
        marker_raw = _exists_read(tree, MARKER)
        if journal_raw is None and marker_raw is None:
            return _retained_directory_result(tree) or {"status": "NO_PENDING_INSTALL"}
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
            retained = []
            result = {"status": _finalize_journal_only(tree, journal, retained_directories=retained)}
            if retained:
                result["retained_directories"] = retained
            return _with_retained_inventory(tree, result)
        journal, bound_raw, bound_marker = _read_bound_journal(tree)
        _bind_transaction_temporaries(tree, journal)
        if _compact_journal(journal):
            # Authenticate specific recovery evidence before a broader inventory
            # refusal can mask it. Endpoint cleanup legitimately permits objects
            # already retired; active rollback still requires every exact object.
            if journal["phase"] == "active":
                _load_recovery_objects(tree, journal)
            _recover_pending_journal_update(
                tree, journal, bound_raw, bound_marker, publish=False)
            # Refuse unrelated additions before cleaning any recorded residue or
            # resuming a pending journal/marker write with a reused sibling name.
            _verify_compact_unchanged_inventory(
                journal, _managed_file_inventory(tree), 'during rollback; marker retained')
        _cleanup_transaction_temporaries(tree, journal)
        journal = _recover_pending_journal_update(
            tree, journal, bound_raw, bound_marker)
        if journal["format"] in {"awf-install-journal-3", "awf-install-journal-4"} and journal["phase"] in {
                "commit_cleanup", "commit_cleanup_authenticated"}:
            _verify_committed_payload(tree, journal, "before resumed runtime backup cleanup")
            cleanup = _cleanup_committed_runtime(tree, journal)
            if cleanup and not all('reason' in row for row in cleanup):
                return _with_retained_inventory(tree, {"status": "COMMITTED_CLEANUP_PENDING", "transaction_cleanup_failures": cleanup})
            _finalize_committed(tree, journal)
            result = {'status': 'COMMITTED'}
            if cleanup:
                result.update(transaction_cleanup='COMMITTED_CLEANUP_PENDING',
                              transaction_cleanup_failures=cleanup)
            return _with_retained_inventory(tree, result)
        retained = rollback(tree, journal)
        result = {"status": "ROLLED_BACK"}
        if retained:
            result["retained_directories"] = retained
        return _with_retained_inventory(tree, result)


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
                          BUDGET_DISPOSITION, budget_upgrade_disposition,
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
        from .adoption_config import (prepare_config, operating_capacity_proposal,
                                      project_uuid, repository_name, unresolved)
        from .contracts import Contracts
        from .policy import inspect_config
        existing = load_yaml(existing_config) if existing_config is not None else None
        if mode == "upgrade" and isinstance(existing, dict):
            project_section = existing.get('project')
            saved_id = project_uuid(project_section.get('id')) if isinstance(project_section, dict) else None
            receipt_id = (existing_receipt or {}).get('project_id')
            if receipt_id is not None and (not project_uuid(receipt_id) or receipt_id != saved_id):
                raise ValidationError("Configuration project UUID disagrees with the persisted installation receipt")
            github = existing.get("github", {})
            for key in ("repository", "repository_id", "base_branch"):
                if key not in (overrides or {}) or not isinstance(github, dict):
                    continue
                established = github.get(key)
                supplied = overrides[key]
                if key == "repository":
                    supplied = repository_name(supplied)
                if not unresolved(established) and supplied != established:
                    raise ValidationError(
                        "Explicit " + key + " conflicts with the established upgrade identity; "
                        "reconcile reviewed PROJECT_CONFIG.yaml before upgrading; no files changed")
            # Upgrade is a migration, not adoption/discovery. Keep the migrated
            # text verbatim, including comments, formatting and rejected choices.
            inspection = inspect_config(existing, load_yaml(content[".agentic/workflow.yaml"]), Contracts(source / ".agentic/schemas"))
            # Older policy can lack newly required settings. Retain its REJECTED
            # result rather than filling those settings. Missing validation
            # commands or project identity require owner repair before upgrading.
            if not saved_id or any(item['path'].startswith('$.validation.commands')
                                   for item in inspection.get('unresolved', [])):
                residue = "; ".join(item['path'] + " (" + item['reason'] + ")"
                                    for item in inspection.get('unresolved', []))
                raise ValidationError("Upgrade configuration rejected: " + CONFIG + ": " + residue +
                    "; resolve these choices in the reviewed project configuration before retrying; no files changed")
            cfg, governance_proposal = operating_capacity_proposal(existing, existing=True,
                requested=propose_operating_capacity, dry_run=dry_run)
            planned[CONFIG] = existing_config if cfg == existing else json_bytes(cfg)
            if cfg != existing:
                inspection = inspect_config(cfg, load_yaml(content['.agentic/workflow.yaml']), Contracts(source / '.agentic/schemas'))
            configuration_report = {**inspection, 'discovery': {
                'project_id': existing['project']['id'], 'warnings': [], 'observations': [],
                'test_commands_executed': False, 'execution_authority': False}}
            return
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
        with Tree(destination.parent), (routing_context(planned[ROUTING], ROUTING)
                                       if ROUTING in planned else nullcontext()):
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
        retained_before = _exists_read(dst, RETAINED_JOURNAL)
        _retained_directory_result(dst)
        adoption_audit_before = _exists_read(dst, DIRECTORY_ADOPTIONS)
        _directory_adoption_audit(adoption_audit_before, tree=dst)
        if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
            raise ValidationError("An interrupted installation requires --recover")
        existing_config = _exists_read(dst, CONFIG)
        existing_routing = _exists_read(dst, ROUTING)
        if existing_routing is not None and ROUTING in content:
            planned[ROUTING] = existing_routing
        if ROUTING in planned:
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
            try:
                _managed_file_inventory(dst)
            except (ValidationError, OSError) as exc:
                raise ValidationError(str(exc) + '; preserve affected managed content externally, restore contained '
                    'regular unlinked release files, then rerun the non-writing plan; no files changed') from exc
            for relative in (RUNTIME, '.agentic-backup'):
                try:
                    path = destination / relative
                    try:
                        info = os.lstat(path)
                    except FileNotFoundError:
                        info = None
                    if info is not None and (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                            or getattr(info, 'st_file_attributes', 0) & 0x400):
                        raise ValidationError('Expected a contained directory')
                except (ValidationError, OSError) as exc:
                    raise ValidationError('Upgrade path refused: ' + relative + '; preserve its target externally, '
                        'replace linked/reparse content with a contained directory, then rerun the plan; no files changed') from exc
            # Retained owner schemas are loaded by installed validate-config.
            # Parse them before any transaction, using the same loader as runtime.
            from .contracts import Contracts
            Contracts(destination / '.agentic/schemas')
            if _exists_read(dst, INSTALLED) is None or existing_config is None:
                raise ValidationError("Upgrade requires a verified AWF installation receipt and project configuration")
            installed_version = existing_receipt.get("template_version") if isinstance(existing_receipt, dict) else None
            # The managed runtime may legitimately contain venv-internal links
            # (POSIX ``lib64 -> lib``); it is not project state, so never walk it.
            state = {}
            # Only retained state is migrated/archived. Product trees are not
            # inputs and must not be traversed merely to select this prefix.
            state_root = destination / '.agentic-state'
            if state_root.exists() or state_root.is_symlink():
                try:
                    state_paths = dst.file_list(subtree='.agentic-state')
                except (ValidationError, OSError) as exc:
                    raise ValidationError("Upgrade state refusal: .agentic-state/ (" + str(exc) +
                        "); preserve and move unsafe state outside the project, reconcile its references, then rerun the plan; no files changed") from exc
                for path in state_paths:
                    try:
                        state[path] = dst.read(path)
                    except (ValidationError, OSError) as exc:
                        raise ValidationError("Upgrade state refusal: " + path +
                            "; bounded regular unlinked state is required (64 MiB per file); preserve and move this file "
                            "outside the project, reconcile its references, then rerun the plan; no files changed") from exc
            operating = _exists_read(dst, OPERATING)
            provenance_raw = _exists_read(dst, PROVENANCE)
            if provenance_raw is None:
                raise ValidationError("Unrecognised AWF installation: workflow provenance is missing; no files changed")
            planning_inputs[PROVENANCE] = provenance_raw
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
                from .adoption_config import verify_import_membership
                # Registered legacy receipts lack an embedded source manifest;
                # identify_installation already authenticated this exact set.
                verify_import_membership(destination, {'files': identity['immutable_files']})
                target_entry = {"source_manifest_sha256": digest,
                                "_source_manifest_json": source_manifest_json,
                                "_immutable_files": {path: sha256(data) for path, data in release_immutable.items()}}
                bundle, step_reports = apply_chain(known_versions, identity["version"], existing_config,
                                                   operating, receipt_raw, provenance_raw, state, target_entry,
                                                   install_id=identity["install_id"])
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
                                         else state_archive_plan(migrated_state, target_version=VERSION))
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
            if identity['version'] != VERSION and VERSION == '1.9.6':
                disposition = budget_upgrade_disposition(existing_config, operating, identity['version'])
                prior_disposition = _exists_read(dst, BUDGET_DISPOSITION)
                if prior_disposition is not None and prior_disposition != json_bytes(disposition):
                    raise ValidationError('Budget upgrade disposition already exists with different bytes; reconcile before upgrade')
                planned[BUDGET_DISPOSITION] = json_bytes(disposition)
                upgrade_report['budgeting'] = disposition
        if configure:
            configure_plan(planned[CONFIG] if mode == "upgrade" else existing_config, existing_receipt)
        if upgrade_report is not None:
            upgrade_report['configuration_diff_total'] = config_diff(
                existing_config, planned[CONFIG], identity['version'], VERSION)
            prior_jira = load_yaml(existing_config).get('jira', {})
            if (isinstance(prior_jira, dict) and prior_jira.get('enabled') is True
                    and (not prior_jira.get('site') or not prior_jira.get('project_key'))):
                upgrade_report['configuration_status_changes'] = [{
                    'code': 'JIRA_IDENTITY_UNBOUND', 'prior_validation': 'REJECTED',
                    'target_validation': (configuration_report or {}).get('status', 'NOT_CHECKED'),
                    'reason': '1.9.6 permits incomplete Jira binding as a configuration warning; '
                              'Jira writes still require explicit identity adoption',
                    'settings_changed': False, 'jira_write_authority': False}]
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
        # Capture and prove metadata restoration for the entire change set
        # before creating the lock, journal, objects or any target payload.
        original_metadata = {}
        if os.name == 'nt':
            for path, old in originals.items():
                if old is not None and (path in deletions or old != planned.get(path)):
                    if path not in deletions and dst.inspect(path).st_file_attributes & 1:
                        raise ValidationError('Read-only replacement cannot preserve metadata safely; no files changed: ' + path)
                    original_metadata[path] = dst.windows_metadata(path, preflight=True)
        if dry_run:
            return {"status": "PLAN", "managed_files": sorted(planned), "conflicts": conflicts, "source_manifest_sha256": digest,
                    "installed": False, "configuration": configuration_report, "governance_proposal": governance_proposal,
                    "gitignore": ignore_report, "codeowners": owner_report, "upgrade": upgrade_report,
                    "write_plan": write_plan, "operating": operating_plan["inspection"], **rules_report()}
        adoption_rules = rules_report()
        with install_lock(dst):
            # Re-read while locked: another cooperating installer or editor may
            # have changed a leaf between preflight and lock acquisition.
            if (any(dst.inspect(path) is not None for path in (JOURNAL, MARKER)) or
                    _exists_read(dst, RETAINED_JOURNAL) != retained_before or
                    _exists_read(dst, DIRECTORY_ADOPTIONS) != adoption_audit_before):
                raise ValidationError("Another installation changed destination state")
            for path, old in owner_inputs.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("CODEOWNERS selection changed after preflight; preserve current project ownership and retry planning")
            for path, old in originals.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("Destination changed after preflight")
                if path in original_metadata and dst.windows_metadata(path) != original_metadata[path]:
                    raise ValidationError('Destination metadata changed after preflight; no payload changed')
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
            metadata_references = {}
            posix_ownership = {}

            def recovery_entry(path, old, new_digest):
                info = dst.inspect(path)
                old_digest = sha256(old) if old is not None else None
                if old is not None:
                    recovery_objects[old_digest] = old
                    if os.name != 'nt':
                        posix_ownership[path] = [info.st_uid, info.st_gid]
                entry = {"path": path,
                        "old_sha256": old_digest,
                        "old_size": len(old) if old is not None else None,
                        "old_mode": stat.S_IMODE(info.st_mode) if info is not None else None,
                        "new_sha256": new_digest, "new_mode": None}
                if path in original_metadata:
                    raw_metadata = json_bytes(original_metadata[path])
                    metadata_digest = sha256(raw_metadata)
                    recovery_objects[metadata_digest] = raw_metadata
                    metadata_references[path] = {'sha256': metadata_digest, 'size': len(raw_metadata)}
                return entry

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
            if metadata_references:
                journal['windows_metadata'] = metadata_references
            if posix_ownership:
                journal['posix_ownership'] = posix_ownership
            if defer_runtime:
                journal["runtime"] = _runtime_journal(destination, transaction_id)
            # Commit each exact sibling name before it can be created. Reusing
            # the same bound names also covers interrupted recovery writes.
            journal['temporary_files'] = 'sibling-uuid5-v2'
            # Bind the complete object inventory before creating it. An abrupt
            # stop during side-store creation therefore leaves recoverable intent,
            # while no destination payload changes before every object validates.
            # The marker/lock coordination roots retain their existing lifecycle;
            # track payload/object/archive parents before any payload mkdir.
            parents = {parent.as_posix() for path in [*planned, *deletions, *backup_plan,
                       _recovery_object_path("0" * 64)] for parent in Path(path).parents
                       if parent.as_posix() != "."}
            journal["created_directories"] = [
                {"path": path, "identity": None}
                for path in sorted(parents, key=lambda value: (value.count("/"), value))
                if path != ".agentic" and not (destination / path).exists()]
            adoptions = _plan_directory_adoptions(dst, parents)
            if adoptions:
                journal['directory_adoptions'] = adoptions
            _publish_initial_intent(dst, journal)
            _prepare_transaction_directories(dst, journal)
            _write_recovery_objects(dst, recovery_objects)
            journal["phase"] = "active"
            _write_bound_journal_update(dst, journal, "activate")
            dst.write_ownership = posix_ownership
            dst.write_modes = {path: stat.S_IREAD for path in archive_paths
                               if originals[path] is None}
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
                _capture_managed_after(dst, journal)
                _write_bound_journal_update(dst, journal, "managed_after")
                _verify_journal_managed_state(dst, journal, "after", "after managed writes")
                # Bootstrap keeps the same journal/marker until the canonical
                # runtime has been staged, swapped and validated.
                if not defer_runtime:
                    journal["phase"] = "commit_cleanup"
                    _write_bound_journal_update(dst, journal, "commit_cleanup")
                    _finalize_committed(dst, journal)
            except Exception as exc:
                if journal['phase'] in {'commit_cleanup', 'commit_cleanup_authenticated'}:
                    raise ValidationError('Committed installation finalization is incomplete; recover the retained transaction') from exc
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
        retained_result = _retained_directory_result(dst)
        if retained_result:
            result['installation_recovery'] = retained_result
        return result
