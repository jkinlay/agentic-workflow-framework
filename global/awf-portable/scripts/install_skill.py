#!/usr/bin/env python3
"""Verified, rollback-capable installation of one portable AWF skill (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid

MANIFEST = "SKILL-MANIFEST.json"
RECEIPT = ".awf-install-receipt.json"
LOCAL_FILES = {"catalog-location.json", "update-channel.json", "local-config.json"}
SCAN_NAMES = {"skills", "plugins", ".agents"}


class InstallError(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InstallError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (ValueError, OSError) as error:
        raise InstallError(f"Cannot read JSON {path}: {error}") from error


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise InstallError(f"Missing or invalid semantic version: {value!r}")
    return tuple(map(int, value.split(".")))


def relative_file(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise InstallError(f"Unsafe relative file: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p in {".", ".."} for p in path.parts):
        raise InstallError(f"Unsafe relative file: {value!r}")
    for part in path.parts:
        if part.endswith((" ", ".")) or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
            raise InstallError(f"Nonportable relative file: {value!r}")
    return value


def check_node(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise InstallError(f"Links/reparse points are not allowed: {path}")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise InstallError(f"Special files are not allowed: {path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise InstallError(f"Hardlinked files are not allowed: {path}")


def safe_path(path):
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for node in reversed((path, *path.parents)):
        if os.path.lexists(node):
            check_node(node)
    return path


def inventory(root):
    root = safe_path(root)
    if not root.is_dir():
        raise InstallError(f"Not a directory: {root}")
    files, seen = {}, set()
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            node = Path(base) / name
            check_node(node)
            rel = relative_file(node.relative_to(root).as_posix())
            if rel.casefold() in seen:
                raise InstallError(f"Case-colliding package path: {rel}")
            seen.add(rel.casefold())
            if node.is_file():
                files[rel] = digest(node)
    return files


def verify_skill_name(path):
    text = path.read_text(encoding="utf-8-sig")
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not frontmatter or not re.search(r"(?m)^name:\s*['\"]?awf['\"]?\s*$", frontmatter.group(1)):
        raise InstallError(f"Expected an AWF skill with name: awf: {path}")


def verify_package(root, expected, allowed_extra=()):
    root = safe_path(root)
    files = inventory(root)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected or ""):
        raise InstallError("An external --expected-manifest-sha256 pin is required")
    if files.get(MANIFEST) != expected.lower():
        raise InstallError("Manifest hash does not match the supplied trust pin")
    manifest = read_json(root / MANIFEST)
    if manifest.get("schema_version") != 1 or manifest.get("name") != "awf":
        raise InstallError("Unsupported AWF skill manifest")
    version_tuple(manifest.get("version"))
    if not isinstance(manifest.get("files"), list):
        raise InstallError("Manifest files must be a list")
    wanted, folded = {}, set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise InstallError("Invalid manifest file entry")
        path = relative_file(entry.get("path"))
        sha = entry.get("sha256", "")
        if path.casefold() in folded or path in {MANIFEST, RECEIPT}:
            raise InstallError(f"Duplicate/reserved manifest path: {path}")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise InstallError(f"Invalid SHA-256: {path}")
        folded.add(path.casefold())
        wanted[path] = sha
    if "SKILL.md" not in wanted:
        raise InstallError("Package must contain SKILL.md")
    unexpected = set(files) - set(wanted) - {MANIFEST} - set(allowed_extra)
    if unexpected:
        raise InstallError(f"Unexpected package files: {sorted(unexpected)}")
    for path, sha in wanted.items():
        if files.get(path) != sha:
            raise InstallError(f"Missing or corrupt package file: {path}")
    verify_skill_name(root / "SKILL.md")
    return manifest, wanted


def contains(parent, child):
    return child == parent or parent in child.parents


def default_destination():
    legacy = Path.home() / ".codex" / "skills" / "awf"
    configured = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills" / "awf"
    return legacy if legacy.exists() else configured


def duplicate_locations(dest, project=None):
    candidates = {Path.home() / ".codex" / "skills" / "awf", Path.home() / ".agents" / "skills" / "awf", default_destination()}
    if os.environ.get("CODEX_HOME"):
        candidates.add(Path(os.environ["CODEX_HOME"]) / "skills" / "awf")
    if project:
        for ancestor in (Path(project).absolute(), *Path(project).absolute().parents):
            candidates.update({ancestor / ".agents" / "skills" / "awf", ancestor / ".codex" / "skills" / "awf"})
    return sorted(str(p) for p in candidates if p.absolute() != dest and p.is_dir())


def existing_version(dest):
    declarations = {}
    for filename, key in ((MANIFEST, "version"), (RECEIPT, "version"), (".awf-skill-receipt.json", "bundled_awf_version"), ("assets/release.json", "version")):
        path = dest / filename
        if path.is_file():
            data = read_json(path)
            if key in data:
                version_tuple(data[key])
                declarations[filename] = data[key]
    if not declarations:
        raise InstallError("Existing skill version cannot be established; refusing replacement")
    if len(set(declarations.values())) != 1:
        raise InstallError(f"Conflicting existing version declarations: {declarations}")
    return next(iter(declarations.values()))


def validate_local_files(paths, packaged):
    if not isinstance(paths, (list, set, tuple)):
        raise InstallError("Preserved local files must be a list of relative file paths")
    result, folded = set(), set()
    reserved = {relative_file(p).casefold() for p in packaged} | {MANIFEST.casefold(), RECEIPT.casefold(), ".awf-skill-receipt.json", "skill.md"}
    for value in paths:
        path = relative_file(value)
        key = path.casefold()
        if key in folded:
            raise InstallError(f"Duplicate preserved local file: {path}")
        if any(key == p or key.startswith(p + "/") or p.startswith(key + "/") for p in reserved):
            raise InstallError("Preserved local files must not override package or receipt files")
        folded.add(key)
        result.add(path)
    return result


def recorded_local_files(dest, wanted):
    path = dest / RECEIPT
    if not path.is_file():
        return set()
    receipt = read_json(path)
    if receipt.get("schema_version") != 1 or receipt.get("name") != "awf" or not isinstance(receipt.get("files"), dict):
        raise InstallError("Invalid existing installation receipt")
    return validate_local_files(receipt.get("preserved_local_files", []), set(wanted) | set(receipt["files"]))


def install(source, expected_manifest_sha256, dest=None, backup_root=None, dry_run=False, project=None, preserve_relative=()):
    source = safe_path(source)
    manifest, wanted = verify_package(source, expected_manifest_sha256)
    dest = safe_path(dest or default_destination())
    if dest.name.lower() != "awf" or len(dest.parts) < 3 or dest == Path.home():
        raise InstallError("Destination must be a dedicated directory named awf")
    if contains(source, dest) or contains(dest, source):
        raise InstallError("Source and destination must not overlap")
    backup_root = safe_path(backup_root or (dest.parent.parent / "skill-backups"))
    if len(backup_root.parts) < 3 or backup_root == Path.home() or any(p.lower() in SCAN_NAMES for p in backup_root.parts):
        raise InstallError("Backup root must be dedicated and outside scanned skills/plugins/.agents directories")
    if any(contains(a, b) or contains(b, a) for a, b in ((backup_root, dest), (backup_root, source))):
        raise InstallError("Backup root must not overlap source or destination")
    local = validate_local_files(LOCAL_FILES, wanted) | validate_local_files(preserve_relative, wanted)
    previous = None
    before = None
    if dest.exists():
        before = inventory(dest)
        if "SKILL.md" not in before:
            raise InstallError("Existing destination is not an AWF skill")
        verify_skill_name(dest / "SKILL.md")
        previous = existing_version(dest)
        local |= recorded_local_files(dest, wanted)
        if version_tuple(previous) > version_tuple(manifest["version"]):
            raise InstallError("Refusing to downgrade an existing newer AWF skill")
        if previous == manifest["version"]:
            verify_package(dest, expected_manifest_sha256, local | {RECEIPT})
            receipt = read_json(dest / RECEIPT)
            if receipt.get("schema_version") != 1 or receipt.get("name") != "awf" or receipt.get("version") != previous or receipt.get("manifest_sha256") != expected_manifest_sha256.lower() or receipt.get("files") != wanted:
                raise InstallError("Installed receipt does not bind this package")
            return {"status": "already-installed", "version": previous, "destination": str(dest), "duplicates": duplicate_locations(dest, project)}
    result = {"status": "would-upgrade" if previous else "would-install", "version": manifest["version"], "previous_version": previous, "destination": str(dest), "backup_root": str(backup_root), "preserved": sorted(local & set(before or {})), "duplicates": duplicate_locations(dest, project)}
    if dry_run:
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe_path(dest.parent)
    lock = dest.parent / ".awf-install.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise InstallError(f"Another installer may be active; lock exists: {lock}") from error
    stage = dest.parent / f".awf-stage-{uuid.uuid4().hex}"
    backup = backup_root / f"awf-{previous or 'new'}-{uuid.uuid4().hex}"
    moved = False
    try:
        os.close(lock_fd)
        if (inventory(dest) if dest.exists() else None) != before:
            raise InstallError("Destination changed during installation planning")
        shutil.copytree(source, stage)
        verify_package(stage, expected_manifest_sha256)
        for rel in result["preserved"]:
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest / rel, target)
        receipt = {"schema_version": 1, "name": "awf", "version": manifest["version"], "manifest_sha256": expected_manifest_sha256.lower(), "files": wanted, "preserved_local_files": result["preserved"], "previous_backup": str(backup) if previous else None}
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        verify_package(stage, expected_manifest_sha256, local | {RECEIPT})
        if (inventory(dest) if dest.exists() else None) != before:
            raise InstallError("Destination changed while staging installation")
        if previous:
            backup_root.mkdir(parents=True, exist_ok=True)
            safe_path(backup_root)
            # Rename, not copy-and-delete: cross-filesystem backups fail safely here.
            os.replace(dest, backup)
            moved = True
        try:
            os.replace(stage, dest)
        except OSError:
            if moved:
                os.replace(backup, dest)
                moved = False
            raise
        result.update(status="upgraded" if previous else "installed", backup=str(backup) if moved else None)
        return result
    finally:
        if stage.exists():
            # Only the unique stage created by this invocation is removed.
            safe_path(stage)
            shutil.rmtree(stage)
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--dest", type=Path)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--preserve-relative", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(install(**vars(args)), indent=2))
        return 0
    except (InstallError, OSError) as error:
        print(f"AWF installation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
