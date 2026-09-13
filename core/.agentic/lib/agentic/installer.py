"""Manifest-verified, journaled installation of managed workflow files."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import uuid

from . import ValidationError, VERSION
from .canonical import canonical, load, load_yaml, loads, now_text, sha256
from .safeio import Tree, relative_parts

MANIFEST = "MANIFEST.json"
LOCK = ".agentic-install/lock"
JOURNAL = ".agentic-install/journal.json"
MARKER = ".agentic/INSTALLING.json"
INSTALLED = ".agentic/installed-manifest.json"
CONFIG = ".agentic/PROJECT_CONFIG.yaml"
PROVENANCE = ".agentic/workflow-version.yaml"


def json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def managed(path):
    return path == "AGENTS.md" or path == ".github/PULL_REQUEST_TEMPLATE.md" or path.startswith(".agentic/")


def verify_release(tree, expected_digest=None):
    raw = tree.read(MANIFEST)
    if expected_digest is not None and sha256(raw) != expected_digest:
        raise ValidationError("Release manifest does not match the approved digest")
    manifest = loads(raw.decode("utf-8"))
    if set(manifest) != {"format", "template_version", "files"} or manifest["format"] != "awf-manifest-1" or manifest["template_version"] != VERSION:
        raise ValidationError("Unsupported release manifest")
    actual = set(tree.file_list(exclude_root_git=True)) - {MANIFEST, "MANIFEST.md"}
    if actual != set(manifest["files"]):
        raise ValidationError("Release manifest file membership mismatch")
    folded = [path.casefold() for path in actual]
    if len(set(folded)) != len(folded):
        raise ValidationError("Case-colliding paths in release")
    content = {}
    for path, expected in manifest["files"].items():
        relative_parts(path)
        data = tree.read(path)
        if expected != sha256(data):
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
        if any(controller.get(key) is not False for key in ["dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments", "auto_transition_jira"]):
            raise ValidationError("Pause all automation switches before installing/upgrading")
    except yaml.YAMLError as exc:
        raise ValidationError("Cannot verify existing configuration quiescence") from exc


def ensure_usable(root):
    with Tree(root) as tree:
        if tree.inspect(MARKER) is not None or tree.inspect(JOURNAL) is not None:
            raise ValidationError("Installation is incomplete; use bootstrap --recover before running tools")


def verify_installed(root):
    ensure_usable(root)
    with Tree(root) as tree:
        if tree.inspect(INSTALLED) is None:
            return verify_release(tree)[0]
        manifest = loads(tree.read(INSTALLED).decode())
        if manifest.get("template_version") != VERSION:
            raise ValidationError("Installed template version mismatch")
        for path, digest in manifest["immutable_files"].items():
            if sha256(tree.read(path)) != digest:
                raise ValidationError(f"Installed managed file changed: {path}")
        version = loads(tree.read(PROVENANCE).decode())
        if version["template"]["version"] != VERSION or version["installation"]["source_manifest_sha256"] != manifest["source_manifest_sha256"]:
            raise ValidationError("Installed provenance is inconsistent")
        return manifest["source_manifest_sha256"]


def rollback(tree, journal):
    if not isinstance(journal, dict) or set(journal) != {"format", "transaction_id", "files"} or journal["format"] != "awf-install-journal-1":
        raise ValidationError("Invalid recovery journal")
    uuid.UUID(journal["transaction_id"])
    if not isinstance(journal["files"], list) or len(journal["files"]) > 10000:
        raise ValidationError("Invalid recovery file inventory")
    seen = set()
    for item in journal["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "old", "new_sha256"}:
            raise ValidationError("Invalid recovery file entry")
        relative_parts(item["path"])
        if not managed(item["path"]) or item["path"] == MARKER or item["path"].casefold() in seen or not re.fullmatch(r"[0-9a-f]{64}", item["new_sha256"]):
            raise ValidationError("Unsafe or duplicate recovery path/digest")
        seen.add(item["path"].casefold())
        if item["old"] is not None:
            base64.b64decode(item["old"], validate=True)
    # Do not clobber edits that were neither the old nor proposed installer bytes.
    for item in journal["files"]:
        current = _exists_read(tree, item["path"])
        old = base64.b64decode(item["old"], validate=True) if item["old"] is not None else None
        if current != old and (current is None or sha256(current) != item["new_sha256"]):
            raise ValidationError(f"Recovery found an external edit at {item['path']}; marker retained")
    for item in reversed(journal["files"]):
        if item["old"] is None:
            tree.unlink(item["path"])
        else:
            tree.write(item["path"], base64.b64decode(item["old"], validate=True))
    tree.unlink(MARKER)
    tree.unlink(JOURNAL)


def recover(destination):
    with Tree(destination) as tree, install_lock(tree):
        journal = _exists_read(tree, JOURNAL)
        if journal is None:
            if tree.inspect(MARKER) is not None:
                raise ValidationError("Marker has no journal; restore from verified backup")
            return {"status": "NO_PENDING_INSTALL"}
        rollback(tree, loads(journal.decode()))
        return {"status": "ROLLED_BACK"}


def install(source, destination, expected_digest, mode="install", conflict="error", overrides=None,
            dry_run=False, fail_after=None):
    if mode not in {"install", "upgrade"} or conflict not in {"error", "backup"}:
        raise ValidationError("Only install/upgrade and error/backup are supported; skip was removed")
    if not expected_digest or len(expected_digest) != 64:
        raise ValidationError("Provide the externally approved manifest SHA-256")
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValidationError("Source and destination trees must not overlap")
    with Tree(source) as src:
        digest, content = verify_release(src, expected_digest)
    planned = {path: data for path, data in content.items() if managed(path)}
    if dry_run and not destination.exists():
        # Pin the existing parent without creating the destination.
        with Tree(destination.parent):
            return {"status": "PLAN", "managed_files": sorted(planned), "source_manifest_sha256": digest}
    with Tree(destination, create=not dry_run) as dst:
        if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
            raise ValidationError("An interrupted installation requires --recover")
        existing_config = _exists_read(dst, CONFIG)
        assert_quiescent(existing_config)
        if mode == "upgrade":
            if _exists_read(dst, INSTALLED) is None or existing_config is None:
                raise ValidationError("Upgrade requires a same-version v1.7.0 installation; migrate older versions through a reviewed install")
            verify_installed(destination)
            planned[CONFIG] = existing_config
        elif overrides:
            import yaml
            cfg = yaml.safe_load(planned[CONFIG])
            cfg["project"].update({key: value for key, value in overrides.items() if key in {"name", "short_name"}})
            if "repository" in overrides:
                cfg["github"]["repository"] = overrides["repository"]
            if "jira_key" in overrides:
                cfg["jira"]["project_key"] = overrides["jira_key"]
            planned[CONFIG] = json_bytes(cfg)
        original_version = _exists_read(dst, PROVENANCE)
        install_id = str(uuid.uuid4())
        if mode == "upgrade":
            install_id = loads(original_version.decode())["installation"]["install_id"]
        version = {"template": {"name": "generic-agentic-development-workflow", "version": VERSION, "schema_revision": 3},
                   "installation": {"install_id": install_id, "last_operation": mode, "operation_at": now_text(),
                                    "source_manifest_sha256": digest, "profile": "manual_reference"}}
        planned[PROVENANCE] = json_bytes(version)
        immutable = {path: sha256(data) for path, data in planned.items() if path not in {CONFIG, PROVENANCE}}
        installed = {"template_version": VERSION, "source_manifest_sha256": digest, "immutable_files": immutable,
                     "initial_config_sha256": sha256(planned[CONFIG]), "mutable_paths": [CONFIG], "install_id": install_id}
        planned[INSTALLED] = json_bytes(installed)
        originals, conflicts = {}, []
        for path, data in planned.items():
            old = _exists_read(dst, path)  # Pins and validates every existing parent.
            originals[path] = old
            if old is not None and old != data and not (mode == "upgrade" and path in {CONFIG, PROVENANCE, INSTALLED}):
                conflicts.append(path)
        if conflicts and conflict == "error":
            raise ValidationError("Conflicting files; no managed files written: " + ", ".join(sorted(conflicts)))
        if dry_run:
            return {"status": "PLAN", "managed_files": sorted(planned), "conflicts": conflicts, "source_manifest_sha256": digest}
        with install_lock(dst):
            # Re-read while locked: another cooperating installer or editor may
            # have changed a leaf between preflight and lock acquisition.
            if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
                raise ValidationError("Another installation changed destination state")
            for path, old in originals.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("Destination changed after preflight")
            transaction_id = str(uuid.uuid4())
            journal = {"format": "awf-install-journal-1", "transaction_id": transaction_id,
                "files": [{"path": path, "old": base64.b64encode(originals[path]).decode() if originals[path] is not None else None,
                           "new_sha256": sha256(data)} for path, data in sorted(planned.items())]}
            if conflict == "backup":
                for path, old in originals.items():
                    if old is not None and old != planned[path]:
                        dst.write(f".agentic-backup/{transaction_id}/{path}", old)
            dst.write(JOURNAL, json_bytes(journal))
            dst.write(MARKER, json_bytes({"transaction_id": transaction_id}))
            try:
                for count, (path, data) in enumerate(sorted(planned.items()), 1):
                    dst.write(path, data)
                    if fail_after == count:
                        raise OSError("Injected installation failure")
                for path, data in planned.items():
                    if dst.read(path) != data:
                        raise ValidationError(f"Installed byte verification failed: {path}")
                # Journal remains a fail-closed marker until its final removal.
                dst.unlink(MARKER)
                dst.unlink(JOURNAL)
            except Exception:
                rollback(dst, journal)
                raise
        return {"status": "INSTALLED" if mode == "install" else "UPGRADED", "template_version": VERSION,
                "install_id": install_id, "source_manifest_sha256": digest, "managed_files": len(planned),
                "profile": "manual_reference", "live_automation_enabled": False,
                "live_automation_enabled_scope": "reference controller and installed background services",
                "native_streams_dispatch_owner": "native host coordinator using the project's execution.native_streams policy",
                "installer_launches_agents": False}
