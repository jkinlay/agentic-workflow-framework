#!/usr/bin/env python3
"""Verify a portable AWF bundle and prepare a separate, machine-local release cache.

No project adoption, global skill registration, network access, deletion or overwrite
is performed. An interrupted cache is preserved; choose a fresh cache to recover.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("awf_bundled_locator", SCRIPT.with_name("awf.py"))
awf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(awf)

FORMAT = "awf-portable-setup-1"
CACHE_MEMBERS = {"source", "release.zip", "catalog.json"}


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class _FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    def _pin_windows_directory(path):
        # FILE_READ_ATTRIBUTES; share read/write but deny delete/rename sharing.
        handle = kernel.CreateFileW(str(path), 0x80, 3, None, 3, 0x00200000 | 0x02000000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), f"Cannot pin cache ancestor: {path}")
        info = _FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            raise OSError(error, f"Cannot inspect pinned cache ancestor: {path}")
        if info.attributes & 0x400 or not info.attributes & 0x10:
            kernel.CloseHandle(handle)
            awf.fail("UNSAFE_PATH", f"Cache ancestor is a reparse point or not a directory: {path}")
        return handle


class _PinnedDirectories:
    """Keep cache writes anchored to directories, not re-resolved path strings."""

    def __init__(self):
        self.handles = {}

    def pin(self, path):
        if path in self.handles:
            return self.handles[path]
        parent = self.pin(path.parent) if path.parent != path else None
        if os.name == "nt":
            handle = _pin_windows_directory(path)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            handle = os.open(str(path), flags) if parent is None else os.open(path.name, flags, dir_fd=parent)
        self.handles[path] = handle
        return handle

    def mkdir(self, path):
        parent = self.pin(path.parent)
        if os.name == "nt":
            path.mkdir()
        else:
            os.mkdir(path.name, dir_fd=parent)
        self.pin(path)

    def create_file(self, path):
        parent = self.pin(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags, 0o600) if os.name == "nt" else os.open(path.name, flags, 0o600, dir_fd=parent)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for handle in reversed(list(self.handles.values())):
            if os.name == "nt":
                kernel.CloseHandle(handle)
            else:
                os.close(handle)
        self.handles.clear()


def _json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _validate_selector(selector):
    if selector is not None and (not isinstance(selector, str) or not awf.SELECTOR.fullmatch(selector)):
        awf.fail("INVALID_VERSION_SELECTOR", "Use major.minor or exact major.minor.patch, for example 1.5")


def _read_bundle(bundle, requested_version):
    awf.inspect_path(bundle, directory=True)
    metadata = awf.parse_json(awf.read_file(bundle / "assets/release.json"), "bundled release metadata")
    required = {"format", "version", "archive", "archive_sha256", "manifest_sha256"}
    if not isinstance(metadata, dict) or set(metadata) != required or metadata["format"] != "awf-bundled-release-1":
        awf.fail("INVALID_BUNDLE", "Expected exact awf-bundled-release-1 metadata")
    version = metadata["version"]
    if not isinstance(version, str) or not awf.VERSION.fullmatch(version):
        awf.fail("INVALID_BUNDLE", "Bundled version must be major.minor.patch")
    if requested_version is not None and not (version == requested_version or
            (requested_version.count(".") == 1 and version.startswith(requested_version + "."))):
        awf.fail("REQUESTED_VERSION_UNAVAILABLE", f"Bundled {version} does not match requested {requested_version}")
    archive_name = awf.relative_path(metadata["archive"])
    if "/" in archive_name:
        awf.fail("INVALID_BUNDLE", "Bundled archive must be a filename within assets")
    for key in ("archive_sha256", "manifest_sha256"):
        if not isinstance(metadata[key], str) or not awf.HEX.fullmatch(metadata[key]):
            awf.fail("INVALID_BUNDLE", f"Bundled {key} must be lowercase SHA-256")
    raw = awf.read_file(bundle / "assets" / archive_name, limit=awf.MAX_ARCHIVE_BYTES)
    if awf.digest(raw) != metadata["archive_sha256"]:
        awf.fail("ARCHIVE_HASH_MISMATCH", "Bundled ZIP does not match its registered archive digest")
    files, _, archive_root = awf._archive(raw, version, metadata["manifest_sha256"])
    return metadata, raw, files, archive_root


def _catalog(cache, metadata):
    return {"format": "awf-release-catalog-1", "latest": {
        "version": metadata["version"], "source_directory": str(cache / "source"),
        "archive": str(cache / "release.zip"), "archive_sha256": metadata["archive_sha256"],
        "manifest_sha256": metadata["manifest_sha256"],
    }}


def _verify_cache(cache, catalog, selector):
    awf.inspect_path(cache, directory=True)
    if {path.name for path in cache.iterdir()} != CACHE_MEMBERS:
        awf.fail("CACHE_EXISTS_UNVERIFIED", "Cache is incomplete or has unrelated entries; no existing files were changed")
    observed = awf.parse_json(awf.read_file(cache / "catalog.json"), "cache catalog")
    if observed != catalog:
        awf.fail("CACHE_EXISTS_UNVERIFIED", "Existing cache catalog differs from this bundle or destination; no files were changed")
    try:
        return awf.locate(cache / "catalog.json", selector)
    except awf.DiscoveryError as exc:
        raise awf.DiscoveryError("CACHE_EXISTS_UNVERIFIED", f"Existing cache failed verification: {exc}") from exc


def _write_new(path, raw, pins):
    """Create one file exclusively relative to its pinned directory ancestors."""
    fd = pins.create_file(path)
    with os.fdopen(fd, "wb") as stream:
        awf._check_stat(os.fstat(stream.fileno()), path, directory=False)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare_release(cache_dir, requested_version=None, *, bundle_root=None):
    """Prepare only a newly claimed cache, or verify an existing matching cache."""
    context = {"format": FORMAT, "requested_version": requested_version,
               "project_adoption_performed": False, "global_skill_installed": False,
               "adoption_pr_created": False, "network_used": False,
               "cache_created_by_helper": False, "cache_verified": False}
    try:
        # A bad selector must not cause any filesystem mutation, including mkdir.
        _validate_selector(requested_version)
        bundle = awf.absolute_path(bundle_root or SCRIPT.parents[1], "Bundle root")
        cache = awf.absolute_path(cache_dir, "Cache directory")
        context.update(bundle_directory=str(bundle), cache_directory=str(cache))
        awf.inspect_path(bundle, directory=True)
        awf.inspect_path(cache.parent, directory=True)  # Never create arbitrary ancestor trees.
        existing = awf.inspect_path(cache, directory=True, missing_ok=True)
        canonical_cache = cache.resolve(strict=False)
        for protected in {bundle.resolve(), SCRIPT.parents[1].resolve()}:
            if canonical_cache == protected or canonical_cache.is_relative_to(protected) or protected.is_relative_to(canonical_cache):
                awf.fail("CACHE_BUNDLE_OVERLAP", "Cache and skill/source bundle must be separate, non-overlapping directories")
        metadata, archive_bytes, files, archive_root = _read_bundle(bundle, requested_version)
        selector = requested_version or metadata["version"]
        context.update(version=metadata["version"], requested_version=selector,
                       bundled_archive_verified=True, bundled_archive_root=archive_root,
                       archive_sha256=metadata["archive_sha256"], manifest_sha256=metadata["manifest_sha256"])
        catalog = _catalog(cache, metadata)
        with _PinnedDirectories() as pins:
            pins.pin(cache.parent)
            if existing is None:
                try:
                    pins.mkdir(cache)  # Atomic claim, held through all writes and final verification.
                except FileExistsError:
                    existing = True
                else:
                    context["cache_created_by_helper"] = True
                    pins.mkdir(cache / "source")
                    _write_new(cache / "release.zip", archive_bytes, pins)
                    made = {cache / "source"}
                    for name, raw in sorted(files.items()):
                        relative = awf.relative_path(name)
                        target = cache / "source" / relative
                        current = cache / "source"
                        for part in Path(relative).parts[:-1]:
                            current = current / part
                            if current not in made:
                                pins.mkdir(current)
                                made.add(current)
                        _write_new(target, raw, pins)
                    # Completion record is last; a crashed setup cannot look ready.
                    _write_new(cache / "catalog.json", _json_bytes(catalog), pins)
            pins.pin(cache)
            proof = _verify_cache(cache, catalog, selector)
        return {**proof, **context, "status": "BUNDLED_RELEASE_PREPARED", "cache_verified": True,
                "cache_status": "CREATED" if context["cache_created_by_helper"] else "EXISTING_VERIFIED",
                "catalog_path": str(cache / "catalog.json"),
                "catalog_location": {"catalog": str(cache / "catalog.json")},
                "next_action": {"owner": "native coordinator", "authorization_required": False,
                                "action": "Use this catalog path in the locally imported AWF skill settings; inspect the target project and prepare its isolated adoption/upgrade PR. Cache setup has not imported the skill or adopted any project."}}
    except awf.DiscoveryError as exc:
        exc.proof = {**context, **exc.proof}
        raise
    except OSError as exc:
        raise awf.DiscoveryError("LOCAL_SETUP_UNAVAILABLE", str(exc), context) from exc


def rejected(exc):
    result = dict(exc.proof)
    result.update(format=FORMAT, status="BUNDLED_RELEASE_SETUP_REJECTED", code=exc.code, error=str(exc),
                  project_adoption_performed=False, global_skill_installed=False, adoption_pr_created=False,
                  next_action={"owner": "native coordinator", "authorization_required": False,
                               "action": "Resolve the reported local path/version/integrity issue and retry. If another setup owns this cache, allow it to finish and retry; preserve a persistently incomplete or different cache and select a new dedicated empty cache path. No existing files are overwritten or deleted."})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True, type=Path,
                        help="Absolute dedicated cache directory outside the skill bundle; its parent must exist")
    parser.add_argument("--version", help="Requested major.minor family or exact patch; defaults to bundled version")
    args = parser.parse_args(argv)
    try:
        result = prepare_release(args.cache_dir, args.version)
    except awf.DiscoveryError as exc:
        print(json.dumps(rejected(exc), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
