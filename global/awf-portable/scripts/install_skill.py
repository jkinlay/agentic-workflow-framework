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


def profile_homes():
    """Return every explicit profile root in platform precedence order."""
    names = ("USERPROFILE", "HOME") if os.name == "nt" else ("HOME", "USERPROFILE")
    homes = []
    for name in names:
        value = os.environ.get(name)
        if value:
            path = Path(value).absolute()
            if path not in homes:
                homes.append(path)
    if not homes:
        homes.append(Path.home().absolute())
    return homes


def duplicate_locations(dest, project=None):
    candidates = {default_destination()}
    for home in profile_homes():
        candidates.update({home / ".codex" / "skills" / "awf",
                           home / ".agents" / "skills" / "awf"})
    if os.environ.get("CODEX_HOME"):
        candidates.add(Path(os.environ["CODEX_HOME"]) / "skills" / "awf")
    if project:
        for ancestor in (Path(project).absolute(), *Path(project).absolute().parents):
            candidates.update({ancestor / ".agents" / "skills" / "awf", ancestor / ".codex" / "skills" / "awf"})
    return sorted(str(p) for p in candidates if p.absolute() != dest and p.is_dir())


def skill_visibility(dest, project=None):
    """Name which Codex/agent sessions can discover each installed copy."""
    dest = Path(dest).absolute()
    homes = profile_homes()
    configured_home = Path(os.environ.get("CODEX_HOME", str(homes[0] / ".codex"))).absolute()
    candidates = []

    def add(path, visible_to, include=False):
        path = Path(path).absolute()
        if include or path.is_dir():
            candidates.append((path, visible_to))

    add(dest, f"sessions configured to scan the skills directory {dest.parent}", include=True)
    add(configured_home / "skills" / "awf",
        f"Codex sessions launched with CODEX_HOME={configured_home}")
    for home in homes:
        add(home / ".codex" / "skills" / "awf",
            f"Codex sessions using the profile home {home}")
        add(home / ".agents" / "skills" / "awf",
            f"agent sessions using the profile home {home}")
    if project:
        resolved_project = Path(project).absolute()
        for ancestor in (resolved_project, *resolved_project.parents):
            add(ancestor / ".codex" / "skills" / "awf",
                f"Codex sessions launched in project {resolved_project} or its descendants")
            add(ancestor / ".agents" / "skills" / "awf",
                f"agent sessions launched in project {resolved_project} or its descendants")
    result = {}
    for path, audience in candidates:
        key = os.path.normcase(str(path))
        item = result.setdefault(key, {"path": str(path), "audiences": []})
        if audience not in item["audiences"]:
            item["audiences"].append(audience)
    return [{"path": item["path"], "visible_to": "; ".join(item["audiences"])}
            for item in sorted(result.values(), key=lambda value: os.path.normcase(value["path"]))]


def add_duplicate_warning(result, dest, project=None):
    visibility = skill_visibility(dest, project)
    result["skill_visibility"] = visibility
    if len(visibility) > 1:
        copies = "\n".join(
            f"- {item['path']}: visible to {item['visible_to']}" for item in visibility
        )
        summary = ("this installation would leave" if result["status"].startswith("would-")
                   else "multiple")
        result["duplicate_warning"] = (
            f"WARNING: {summary} discoverable AWF skill copies:\n" + copies
            + "\nAction: align every visible copy to this AWF version, or configure each "
              "affected session to discover only the intended skills directory before launch."
        )
    else:
        result["duplicate_warning"] = None
    return result


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


def publish_exclusive(path, raw, mode=0o600, metadata=None):
    """Create once through pinned, non-following parents; never replace a leaf."""
    from contextlib import ExitStack
    path = Path(os.path.abspath(path))
    with ExitStack() as stack:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                ctypes.c_void_p, wintypes.DWORD]
            for parent in reversed(path.parents):
                handle = kernel.CreateFileW(str(parent), 0x80, 3, None, 3, 0x02200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                stack.callback(kernel.CloseHandle, handle)
                attributes = (wintypes.DWORD * 2)()
                if not kernel.GetFileInformationByHandleEx(handle, 9, attributes, ctypes.sizeof(attributes)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not attributes[0] & 0x10 or attributes[0] & 0x400:
                    raise InstallError('Cleanup diagnostic parent is not an ordinary directory')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        else:
            parent_fd = None
            for parent in reversed(path.parents):
                parent_fd = os.open(str(parent) if parent_fd is None else parent.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    **({} if parent_fd is None else {'dir_fd': parent_fd}))
                stack.callback(os.close, parent_fd)
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent_fd)
        with os.fdopen(fd, 'wb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InstallError('Unsafe cleanup diagnostic file')
            if isinstance(raw, bytes):
                stream.write(raw)
            else:
                while block := raw.read(1024 * 1024):
                    stream.write(block)
            stream.flush()
            if os.name == 'nt':
                import msvcrt
                class BasicInformation(ctypes.Structure):
                    _fields_ = [('created', ctypes.c_longlong), ('accessed', ctypes.c_longlong),
                                ('written', ctypes.c_longlong), ('changed', ctypes.c_longlong),
                                ('attributes', wintypes.DWORD)]
                kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                    ctypes.c_void_p, wintypes.DWORD]
                accessed = written = 0
                flags = 0x80 if mode & stat.S_IWRITE else 1
                if metadata is not None:
                    accessed = metadata['atime_ns'] // 100 + 116444736000000000
                    written = metadata['mtime_ns'] // 100 + 116444736000000000
                    flags = metadata['attributes'] & (1 | 2 | 4 | 0x20) or flags
                attributes = BasicInformation(0, accessed, written, 0, flags)
                if not kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(stream.fileno()),
                        0, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                if metadata is not None:
                    os.utime(stream.fileno(), ns=(metadata['atime_ns'], metadata['mtime_ns']))
                    for name, value in metadata['xattrs'].items():
                        os.setxattr(stream.fileno(), name, value)
                os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())


def publish_cleanup_diagnostic(path, record):
    publish_exclusive(path, (json.dumps(record, indent=2) + '\n').encode('utf-8'))


def copy_exclusive(source, target):
    """Never copy onto a name that already exists, including an owner hard link."""
    source, target = Path(source), Path(target)
    safe_path(source)
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InstallError('Stage source must be an independent regular file')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(source, flags)
    with os.fdopen(fd, 'rb') as stream:
        current = os.fstat(stream.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_nlink)
        if identity(info) != identity(current) or not stat.S_ISREG(current.st_mode):
            raise InstallError('Stage source changed before opening; no destination published')
        metadata = dict(atime_ns=current.st_atime_ns, mtime_ns=current.st_mtime_ns,
                        attributes=getattr(current, 'st_file_attributes', 0), xattrs={})
        if os.name != 'nt':
            metadata['xattrs'] = {name: os.getxattr(stream.fileno(), name)
                                  for name in os.listxattr(stream.fileno())}
        publish_exclusive(target, stream, stat.S_IMODE(current.st_mode), metadata)
        if identity(current) != identity(os.fstat(stream.fileno())):
            raise InstallError('Stage source changed while copying; no destination published')


def create_stage_directory(source, target):
    """Request source permissions atomically; never chmod a reopened directory."""
    safe_path(source)
    mode = stat.S_IMODE(source.lstat().st_mode)
    target.mkdir(mode=mode)
    safe_path(target)
    if stat.S_IMODE(target.lstat().st_mode) != mode:
        raise InstallError('Stage directory creation mode differs; no destination published')
    if os.name != 'nt':
        descriptors = []
        try:
            for path in (source, target):
                descriptors.append(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            attributes = [{name: os.getxattr(fd, name) for name in os.listxattr(fd)} for fd in descriptors]
            if attributes[0] != attributes[1]:
                raise InstallError('Stage directory inherited attributes differ; no destination published')
        finally:
            for fd in descriptors:
                os.close(fd)


def copy_stage(source, stage):
    """Build a new tree without copytree's pathname copystat or copy2 writers."""
    create_stage_directory(source, stage)
    for parent, directories, files in os.walk(source, followlinks=False):
        relative = Path(parent).relative_to(source)
        for name in directories:
            safe_path(Path(parent) / name)
            create_stage_directory(Path(parent) / name, stage / relative / name)
        for name in files:
            copy_exclusive(Path(parent) / name, stage / relative / name)


def create_install_lock(path):
    if os.name != 'nt':
        import fcntl
        # Lease the existing parent inode instead of creating a named lock
        # which POSIX cannot safely dispose. Legacy/unknown locks still refuse.
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise FileExistsError('Another installer holds the directory lease') from error
            try:
                os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return fd
            raise FileExistsError('Legacy or unknown installer lock exists')
        except BaseException:
            os.close(fd)
            raise
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x10000 | 0xC0000000, 3, None, 1,
                                0x00200000 | 0x04000000, None)  # CREATE_NEW, DELETE_ON_CLOSE
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (80, 183):
            raise FileExistsError('Installer lock exists')
        raise ctypes.WinError(error)
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise


def dispose_install_lock(fd):
    """Dispose the Windows creating handle or close the POSIX directory lease."""
    try:
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                ctypes.c_void_p, wintypes.DWORD]
            disposition = wintypes.BOOL(True)
            if not kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), 4,
                    ctypes.byref(disposition), ctypes.sizeof(disposition)):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        os.close(fd)


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
            if (receipt.get("format") != "awf-host-skill-trust-1"
                    or receipt.get("schema_version") != 1 or receipt.get("inventory_complete") is not True
                    or receipt.get("name") != "awf" or receipt.get("version") != previous
                    or receipt.get("manifest_sha256") != expected_manifest_sha256.lower()
                    or receipt.get("files") != wanted):
                raise InstallError("Installed receipt does not bind this package")
            preserved = validate_local_files(receipt.get("preserved_local_files", []), wanted)
            local_inventory = receipt.get("preserved_local_inventory")
            if (not isinstance(local_inventory, dict) or set(local_inventory) != preserved
                    or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                           or digest(dest / name) != value
                           for name, value in local_inventory.items())):
                raise InstallError("Installed receipt does not verify every preserved local file")
            return add_duplicate_warning(
                {"status": "already-installed", "version": previous,
                 "destination": str(dest), "duplicates": duplicate_locations(dest, project)},
                dest, project)
    result = add_duplicate_warning(
        {"status": "would-upgrade" if previous else "would-install",
         "version": manifest["version"], "previous_version": previous,
         "destination": str(dest), "backup_root": str(backup_root),
         "preserved": sorted(local & set(before or {})),
         "duplicates": duplicate_locations(dest, project)},
        dest, project)
    if dry_run:
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe_path(dest.parent)
    lock = dest.parent / ".awf-install.lock"
    try:
        lock_fd = create_install_lock(lock)
    except FileExistsError as error:
        raise InstallError(f"Another installer may be active; lock exists: {lock}") from error
    stage = dest.parent / f".awf-stage-{uuid.uuid4().hex}"
    backup = backup_root / f"awf-{previous or 'new'}-{uuid.uuid4().hex}"
    moved = False
    try:
        if (inventory(dest) if dest.exists() else None) != before:
            raise InstallError("Destination changed during installation planning")
        copy_stage(source, stage)
        verify_package(stage, expected_manifest_sha256)
        for rel in result["preserved"]:
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_exclusive(dest / rel, target)
        preserved_inventory = {relative: digest(stage / relative) for relative in result["preserved"]}
        receipt = {"format": "awf-host-skill-trust-1", "schema_version": 1, "name": "awf",
                   "version": manifest["version"], "manifest_sha256": expected_manifest_sha256.lower(),
                   "files": wanted, "inventory_complete": True,
                   "preserved_local_files": result["preserved"],
                   "preserved_local_inventory": preserved_inventory,
                   "previous_backup": str(backup) if previous else None}
        publish_exclusive(stage / RECEIPT, (json.dumps(receipt, indent=2) + "\n").encode('utf-8'))
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
        return add_duplicate_warning(result, dest, project)
    finally:
        if stage.exists():
            # Verification cannot bind subsequent pathname recursive deletion.
            # Retain the stage, and never hide the original installation error.
            primary = sys.exc_info()[1]
            try:
                record = {"status": "CLEANUP_PENDING", "path": stage.name,
                          "size_bytes": None,
                          "reason": "Pathname recursive deletion refused",
                          "owner_action": "Inspect retained stage under exclusive owner control; preserve needed bytes before manual recovery"}
                publish_cleanup_diagnostic(stage.with_name(stage.name + '.cleanup-pending.json'), record)
            except (OSError, InstallError) as error:
                if primary is None:
                    raise
                primary.add_note('Stage retained; cleanup diagnostic unavailable: ' + type(error).__name__)
        dispose_install_lock(lock_fd)


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
