#!/usr/bin/env python3
"""Read-only, standard-library discovery of a pinned local AWF release.

The catalog is a local trust input, not a signature or a network release feed.
No release Python is imported or executed, and no project/global files are written.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile
import zlib

sys.dont_write_bytecode = True
FORMAT = "awf-local-discovery-1"
CONFIG = ".agentic/PROJECT_CONFIG.yaml"
VERSION_FILE = ".agentic/workflow-version.yaml"
RECEIPT = ".agentic/installed-manifest.json"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000
HEX = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
SELECTOR = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?\Z")


class DiscoveryError(ValueError):
    """Expected rejection with a machine-readable reason and retained read proof."""

    def __init__(self, code, message, proof=None):
        super().__init__(message)
        self.code = code
        self.proof = proof or {}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fail(code, message):
    raise DiscoveryError(code, message)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            fail("INVALID_JSON", f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def parse_json(raw, label):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda value: fail("INVALID_JSON", f"Invalid constant: {value}"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("INVALID_JSON", f"Cannot read strict UTF-8 JSON from {label}: {exc}") from exc


def absolute_path(value, label):
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        fail("UNSAFE_PATH", f"{label} must name an absolute local path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        fail("UNSAFE_PATH", f"{label} must be absolute without parent traversal: {path}")
    # Do not resolve away a link before inspecting it.
    return path


def _check_stat(info, path, *, directory=None):
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        fail("UNSAFE_PATH", f"Link or reparse point is not accepted: {path}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        fail("UNSAFE_PATH", f"Unsupported filesystem object: {path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        fail("UNSAFE_PATH", f"Hardlinked file is not accepted: {path}")
    if directory is True and not stat.S_ISDIR(info.st_mode):
        fail("UNSAFE_PATH", f"Expected a directory: {path}")
    if directory is False and not stat.S_ISREG(info.st_mode):
        fail("UNSAFE_PATH", f"Expected a regular file: {path}")


def inspect_path(path, *, directory=None, missing_ok=False):
    path = absolute_path(path, "Filesystem path")
    for ancestor in reversed(path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        _check_stat(info, ancestor, directory=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    _check_stat(info, path, directory=directory)
    return info


def read_file(path, *, limit=MAX_FILE_BYTES):
    info = inspect_path(path, directory=False)
    if info.st_size > limit:
        fail("RESOURCE_LIMIT", f"File exceeds the discovery size limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        _check_stat(opened, path, directory=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(info) != identity(opened):
            fail("FILES_CHANGED", f"File changed while being opened: {path}")
        data = stream.read(limit + 1)
        if len(data) > limit or identity(opened) != identity(os.fstat(stream.fileno())):
            fail("FILES_CHANGED", f"File changed while being read: {path}")
    if identity(info) != identity(inspect_path(path, directory=False)):
        fail("FILES_CHANGED", f"File changed during verification: {path}")
    return data


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        fail("UNSAFE_PATH", f"Unsafe relative release path: {value!r}")
    parts = value.split("/")
    if any(not part or part in {".", ".."} or part.casefold() == ".git" or
           part.rstrip(" .") != part or any(ord(char) < 32 or char in ':*?"<>|' for char in part)
           or re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)
           for part in parts):
        fail("UNSAFE_PATH", f"Unsafe relative release path: {value!r}")
    if PurePosixPath(value).is_absolute():
        fail("UNSAFE_PATH", f"Absolute release member: {value}")
    return value


def hash_map(value, label):
    if not isinstance(value, dict) or not value or len(value) > MAX_FILES:
        fail("INVALID_MANIFEST", f"{label} must contain a bounded nonempty file map")
    folded = set()
    for path, expected in value.items():
        relative_path(path)
        if path.casefold() in folded:
            fail("INVALID_MANIFEST", f"Case-colliding {label} path: {path}")
        folded.add(path.casefold())
        if not isinstance(expected, str) or not HEX.fullmatch(expected):
            fail("INVALID_MANIFEST", f"Invalid SHA-256 for {path}")
    return value


def inventory(root):
    inspect_path(root, directory=True)
    found = {}
    folded = set()

    pending = [root]
    while pending:
        folder = pending.pop()
        for child in sorted(folder.iterdir(), key=lambda item: item.name):
            info = child.lstat()
            _check_stat(info, child)
            if folder == root and child.name == ".git":
                continue  # Exact root metadata only, never a link or unsupported object.
            name = child.relative_to(root).as_posix()
            relative_path(name)
            if name.casefold() in folded:
                fail("UNSAFE_PATH", f"Case-colliding source path: {name}")
            folded.add(name.casefold())
            if len(folded) > MAX_FILES:
                fail("RESOURCE_LIMIT", "Source contains too many filesystem entries")
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
            else:
                found[name] = child
                if len(found) > MAX_FILES:
                    fail("RESOURCE_LIMIT", "Source contains too many files")

    return found


def _manifest(raw, version):
    value = parse_json(raw, "MANIFEST.json")
    if not isinstance(value, dict) or set(value) != {"format", "template_version", "files"} or \
            value["format"] != "awf-manifest-1" or value["template_version"] != version:
        fail("INVALID_MANIFEST", "Manifest format/version does not match the registered release")
    files = hash_map(value["files"], "manifest")
    if {"MANIFEST.json", "MANIFEST.md"} & set(files):
        fail("INVALID_MANIFEST", "Manifest cannot list itself or MANIFEST.md")
    required = {"AGENTS.md", "README.md", CONFIG, VERSION_FILE,
                ".agentic/requirements.lock", "scripts/bootstrap_project.py"}
    if not required <= set(files):
        fail("INVALID_MANIFEST", "Release is missing required entry points/configuration")
    return value


def _archive(raw, version, manifest_sha256):
    files = {}
    total = 0
    prefix = None
    folded = set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES + 2:
                fail("RESOURCE_LIMIT", "Archive contains too many entries")
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    fail("INVALID_ARCHIVE", "Archive entry contains a truncated filename")
                full_name = relative_path(entry.filename)
                parts = full_name.split("/", 1)
                if len(parts) != 2:
                    fail("INVALID_ARCHIVE", "Archive must have one enclosing source directory")
                root, name = parts
                if prefix is None:
                    prefix = root
                if root != prefix or name.casefold() in folded:
                    fail("INVALID_ARCHIVE", "Archive has multiple roots or duplicate/case-colliding files")
                folded.add(name.casefold())
                mode = entry.external_attr >> 16
                if entry.is_dir() or entry.flag_bits & 1 or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}) or \
                        entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    fail("INVALID_ARCHIVE", f"Unsupported or encrypted archive entry: {full_name}")
                total += entry.file_size
                if entry.file_size > MAX_FILE_BYTES or total > MAX_ARCHIVE_BYTES:
                    fail("RESOURCE_LIMIT", "Expanded archive exceeds discovery limits")
                files[name] = archive.read(entry)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise DiscoveryError("INVALID_ARCHIVE", f"Cannot verify ZIP: {exc}") from exc
    if "MANIFEST.json" not in files or digest(files["MANIFEST.json"]) != manifest_sha256:
        fail("MANIFEST_HASH_MISMATCH", "Archived manifest does not match the catalog pin")
    manifest = _manifest(files["MANIFEST.json"], version)
    expected = set(manifest["files"]) | {"MANIFEST.json", "MANIFEST.md"}
    if set(files) != expected:
        fail("ARCHIVE_MEMBERSHIP_MISMATCH", "Archive membership differs from its approved manifest")
    for name, expected_hash in manifest["files"].items():
        if digest(files[name]) != expected_hash:
            fail("ARCHIVE_CONTENT_MISMATCH", f"Archived file differs from approved digest: {name}")
    provenance = parse_json(files[VERSION_FILE], VERSION_FILE)
    if not isinstance(provenance, dict) or not isinstance(provenance.get("template"), dict) or \
            provenance["template"].get("version") != version:
        fail("VERSION_MISMATCH", "Archived workflow version differs from the registered release")
    return files, manifest, prefix


def locate(catalog_path, requested_version=None):
    """Return a verified local read proof. Raise DiscoveryError with partial proof."""
    proof = {"format": FORMAT, "requested_version": requested_version,
             "archive_verified": False, "source_verified": False,
             "installed_by_helper": False, "adoption_pr_created": False,
             "release_code_executed": False, "network_used": False}
    try:
        if requested_version is not None and (not isinstance(requested_version, str) or
                                              not SELECTOR.fullmatch(requested_version)):
            fail("INVALID_VERSION_SELECTOR", "Use major.minor or exact major.minor.patch, for example 1.7")
        catalog_path = absolute_path(catalog_path, "Catalog")
        raw = read_file(catalog_path)
        proof.update(catalog_path=str(catalog_path), catalog_sha256=digest(raw))
        catalog = parse_json(raw, "catalog")
        if not isinstance(catalog, dict) or set(catalog) != {"format", "latest"} or \
                catalog["format"] != "awf-release-catalog-1" or not isinstance(catalog["latest"], dict):
            fail("INVALID_CATALOG", "Expected awf-release-catalog-1 with one latest release")
        entry = catalog["latest"]
        if set(entry) != {"version", "source_directory", "archive", "archive_sha256", "manifest_sha256"}:
            fail("INVALID_CATALOG", "Catalog latest fields differ from the supported contract")
        version = entry["version"]
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            fail("INVALID_CATALOG", "Catalog latest version must be major.minor.patch")
        proof["version"] = version
        if requested_version is not None and not (version == requested_version or
                (requested_version.count(".") == 1 and version.startswith(requested_version + "."))):
            fail("REQUESTED_VERSION_UNAVAILABLE", f"Registered {version} does not match requested {requested_version}")
        for key in ("archive_sha256", "manifest_sha256"):
            if not isinstance(entry[key], str) or not HEX.fullmatch(entry[key]):
                fail("INVALID_CATALOG", f"Catalog {key} must be lowercase SHA-256")
        source = absolute_path(entry["source_directory"], "Source directory")
        archive_path = absolute_path(entry["archive"], "Archive")
        proof.update(source_directory=str(source), archive=str(archive_path),
                     manifest_sha256=entry["manifest_sha256"], archive_sha256=entry["archive_sha256"])
        archive_raw = read_file(archive_path, limit=MAX_ARCHIVE_BYTES)
        if digest(archive_raw) != entry["archive_sha256"]:
            fail("ARCHIVE_HASH_MISMATCH", "Local ZIP does not match the catalog archive pin")
        files, manifest, prefix = _archive(archive_raw, version, entry["manifest_sha256"])
        proof.update(archive_verified=True, archive_root=prefix, archive_files_verified=len(files))
        actual = inventory(source)
        if set(actual) != set(files):
            fail("SOURCE_MEMBERSHIP_MISMATCH", "Local source file membership differs from the verified archive")
        for name, path in actual.items():
            if read_file(path) != files[name]:
                fail("SOURCE_CONTENT_MISMATCH", f"Local source differs from verified archive bytes: {name}")
        if set(inventory(source)) != set(actual):
            fail("FILES_CHANGED", "Source membership changed during verification")
        proof.update(status="VERIFIED_LOCAL_RELEASE", source_verified=True,
                     source_files_verified=len(actual), manifest_files_verified=len(manifest["files"]),
                     agents_path=str(source / "AGENTS.md"), project_config_path=str(source / CONFIG),
                     readme_path=str(source / "README.md"),
                     workflow_version_path=str(source / VERSION_FILE),
                     bootstrap_script=str(source / "scripts/bootstrap_project.py"),
                     requirements_path=str(source / ".agentic/requirements.lock"),
                     python_dependencies_verified=False,
                     version_basis="latest locally registered release; no network release lookup",
                     trust_note="Catalog pins are locally supplied trust inputs, not publisher signatures. "
                                "Read proof describes bytes verified now; reverify before executing release code.",
                     next_action={"owner": "native coordinator", "action": "inspect the target repository, then prepare an isolated adoption/upgrade PR preserving configuration and history", "authorization_required": False})
        return proof
    except DiscoveryError as exc:
        exc.proof = {**proof, **exc.proof}
        raise
    except OSError as exc:
        raise DiscoveryError("LOCAL_FILES_UNAVAILABLE", str(exc), proof) from exc


def managed(path):
    return path == "AGENTS.md" or path == ".github/PULL_REQUEST_TEMPLATE.md" or path.startswith(".agentic/")


def inspect_project(proof, project_path, python_executable=None):
    """Observe target state and return a dry-run command; never run an installer."""
    if not proof.get("source_verified"):
        fail("SOURCE_NOT_VERIFIED", "Inspect requires a successful local source read proof")
    project = absolute_path(project_path, "Project")
    source = Path(proof["source_directory"])
    if project == source or project.is_relative_to(source) or source.is_relative_to(project):
        fail("PROJECT_SOURCE_OVERLAP", "Project/adoption worktree and immutable source must not overlap")
    exists = inspect_path(project, directory=True, missing_ok=True) is not None
    observations = {}
    raw_files = {}
    for name in ("AGENTS.md", CONFIG, VERSION_FILE, RECEIPT,
                 ".agentic/INSTALLING.json", ".agentic-install/journal.json"):
        path = project / name
        present = inspect_path(path, directory=False, missing_ok=True) is not None
        raw = read_file(path) if present else None
        observations[name] = {"path": str(path), "exists": present, "sha256": digest(raw) if present else None}
        raw_files[name] = raw
    installed = {"status": "NOT_INSTALLED", "version": None, "receipt_verified_against_registered_release": False,
                 "configuration_validated": False, "observed_files": observations}
    if any(raw_files[name] is not None for name in (".agentic/INSTALLING.json", ".agentic-install/journal.json")):
        installed["status"] = "INCOMPLETE_INSTALLATION"
    elif raw_files[RECEIPT] is None:
        if any(raw_files[name] is not None for name in ("AGENTS.md", CONFIG, VERSION_FILE)):
            installed["status"] = "EXISTING_UNRECEIPTED_FILES"
    else:
        try:
            receipt = parse_json(raw_files[RECEIPT], RECEIPT)
            if not isinstance(receipt, dict) or not isinstance(receipt.get("template_version"), str) or \
                    not VERSION.fullmatch(receipt["template_version"]):
                fail("INVALID_RECEIPT", "Receipt must identify an observed template version")
            installed["version"] = receipt["template_version"]
            installed["source_manifest_sha256"] = receipt.get("source_manifest_sha256")
            if receipt["template_version"] != proof["version"] or receipt.get("source_manifest_sha256") != proof["manifest_sha256"]:
                installed["status"] = "OTHER_RELEASE_RECEIPT_OBSERVED"
                installed["note"] = "Observed receipt is not authenticated by this catalog's release pin; preserve it as migration history."
            else:
                # Bind the receipt to the independently verified release, not its self-declared map.
                manifest_raw = read_file(source / "MANIFEST.json")
                if digest(manifest_raw) != proof["manifest_sha256"]:
                    fail("FILES_CHANGED", "Source manifest changed after discovery")
                manifest = _manifest(manifest_raw, proof["version"])
                expected = {name: sha for name, sha in manifest["files"].items()
                            if managed(name) and name not in {CONFIG, VERSION_FILE}}
                actual = hash_map(receipt.get("immutable_files"), "installed receipt")
                if actual != expected:
                    fail("INVALID_RECEIPT", "Receipt managed file map differs from the registered release")
                for name, sha in expected.items():
                    if digest(read_file(project / name)) != sha:
                        fail("INSTALLED_CONTENT_MISMATCH", f"Installed managed bytes differ: {name}")
                version = parse_json(raw_files[VERSION_FILE] or b"null", VERSION_FILE)
                if not isinstance(version, dict) or not isinstance(version.get("template"), dict) or \
                        not isinstance(version.get("installation"), dict) or \
                        version["template"].get("version") != proof["version"] or \
                        version["installation"].get("source_manifest_sha256") != proof["manifest_sha256"] or \
                        not isinstance(receipt.get("install_id"), str) or not receipt["install_id"] or \
                        version["installation"].get("install_id") != receipt["install_id"] or raw_files[CONFIG] is None:
                    fail("INVALID_RECEIPT", "Installed receipt/provenance/configuration is incomplete or inconsistent")
                installed.update(status="REGISTERED_RELEASE_BYTES_VERIFIED",
                                 receipt_verified_against_registered_release=True,
                                 immutable_files_verified=len(expected),
                                 note="Mutable project configuration was observed, not validated; installation does not prove activation, adoption PR or merge.")
        except (DiscoveryError, OSError) as exc:
            installed.update(status="INSTALLED_STATE_REQUIRES_RECONCILIATION", issue=str(exc))
    mode = "upgrade" if installed["status"] == "REGISTERED_RELEASE_BYTES_VERIFIED" else "install"
    command = [str(python_executable or sys.executable), "-B", proof["bootstrap_script"],
               "--dest", str(project), "--expected-manifest-sha256", proof["manifest_sha256"],
               "--mode", mode, "--on-conflict", "backup", "--dry-run"]
    result = dict(proof)
    result.update(status="PROJECT_INSPECTED", project_directory=str(project), project_exists=exists,
                  installed=installed, installer_dry_run_command=command,
                  installer_apply_command=None, adoption_pr_created=False, installed_by_helper=False,
                  adoption_plan={"destination_requirement": "Use an isolated adoption/upgrade branch and worktree before applying changes.",
                                 "preserve": ["existing project configuration and identifiers", "AGENTS.md project instructions", "Jira/Git history", "root .gitattributes product rules"],
                                 "configuration_mapping_required": raw_files[CONFIG] is not None,
                                 "agents_reconciliation_required": raw_files["AGENTS.md"] is not None,
                                 "merge_owner": "human project owner",
                                 "note": "The command is a read-only installer preview. Do not remove --dry-run in the current checkout. "
                                         "Preserve/map brownfield configuration and instructions in the isolated adoption PR using the product guides."},
                  next_action={"owner": "native coordinator", "action": "prepare an isolated adoption/upgrade PR preserving configuration and history; reconcile observed installation issues first, validate the complete candidate and report its actual PR URL", "authorization_required": False})
    return result


def rejected(exc, requested_version=None):
    proof = dict(exc.proof)
    archive_verified = proof.get("archive_verified", False)
    source_verified = proof.get("source_verified", False)
    status = "LOCAL_DISCOVERY_REJECTED"
    if source_verified:
        action = "Resolve the reported target inspection issue, then prepare an isolated adoption/upgrade PR preserving configuration and history."
    elif archive_verified:
        status = "LOCAL_ARCHIVE_VERIFIED_SOURCE_UNAVAILABLE"
        action = "Recover the source into a clean local directory from the verified local archive, update the local catalog source path, and rerun discovery before executing release code."
    else:
        action = "Check the local catalog and readable registered release first; if no verified matching local source/archive is available, obtain the requested AWF ZIP and its independently approved pins."
    proof.update(format=FORMAT, status=status, code=exc.code, error=str(exc),
                 requested_version=proof.get("requested_version", requested_version),
                 request_zip_if_no_verified_local_release=not (archive_verified or source_verified),
                 installed_by_helper=False, adoption_pr_created=False, release_code_executed=False,
                 next_action={"owner": "native coordinator", "action": action, "authorization_required": False})
    return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("locate", "inspect"):
        command = commands.add_parser(name)
        command.add_argument("--version", help="Requested major.minor family or exact patch version")
        if name == "inspect":
            command.add_argument("--project", required=True, type=Path)
    args = parser.parse_args(argv)
    proof = {}
    try:
        proof = locate(args.catalog, args.version)
        result = inspect_project(proof, args.project) if args.command == "inspect" else proof
        print(json.dumps(result, indent=2))
        return 0
    except (DiscoveryError, OSError) as exc:
        if not isinstance(exc, DiscoveryError):
            exc = DiscoveryError("LOCAL_FILES_UNAVAILABLE", str(exc), proof)
        elif proof:
            exc.proof = {**proof, **exc.proof}
        print(json.dumps(rejected(exc, args.version), indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
