"""Read-only adoption discovery and conservative filling of unresolved configuration.

Commands inspect Git/GitHub metadata only. Test commands are recorded, never run.
Discovery assertions are not repository-rule, merge, or live-execution authority.
"""
from __future__ import annotations

import copy
import base64
import csv
import hashlib
import io
import json
import os
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import stat
import tempfile
import time
import unicodedata
import uuid
import venv
import zipfile

from . import ValidationError, VERSION
from .canonical import loads, sha256
from .child_process import child_env
from .providers import github as github_provider
from .safeio import Tree

MAX_METADATA_BYTES = 1024 * 1024
validate_codeowner = github_provider.validate_codeowner
repository_name = github_provider.repository_name
origin_repository = github_provider.origin_repository
_executable = github_provider._discovery_executable
_read_command = github_provider._read_discovery_command


_LOCK_REQUIREMENT = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)==([A-Za-z0-9][A-Za-z0-9._+!-]*)$")
_LOCK_HASH = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")
_DISTRIBUTION_NAME = re.compile(r"[-_.]+")
_MAX_DEPENDENCY_FILE_BYTES = 128 * 1024 * 1024
_MAX_DEPENDENCY_BYTES = 512 * 1024 * 1024
_MAX_WHEEL_MEMBERS = 10000
_RUNTIME_IMPORTS = {
    "attrs": "attrs",
    "jsonschema": "jsonschema",
    "jsonschema-specifications": "jsonschema_specifications",
    "pyyaml": "yaml",
    "referencing": "referencing",
    "rpds-py": "rpds",
    "typing-extensions": "typing_extensions",
}
_STARTUP_HOOKS = {"sitecustomize.py", "usercustomize.py"}
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *{"COM" + str(value) for value in range(1, 10)},
                     *{"LPT" + str(value) for value in range(1, 10)}}


def _distribution_name(value):
    return _DISTRIBUTION_NAME.sub("-", value).lower()


def _locked_dependencies(lock_path):
    """Read exact dependency identities and their complete-artifact hash allowlists."""
    try:
        raw = lock_path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError("Canonical runtime dependency lock is unavailable or invalid") from exc
    dependencies = {}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        continued = line.endswith("\\")
        token = line[:-1].rstrip() if continued else line
        match = _LOCK_REQUIREMENT.fullmatch(token)
        if match:
            display_name, version = match.groups()
            current = _distribution_name(display_name)
            if current in dependencies:
                raise ValidationError("Canonical runtime dependency lock contains duplicate distributions")
            dependencies[current] = {"name": display_name, "version": version, "artifact_hashes": set()}
            if not continued:
                raise ValidationError("Canonical runtime dependency lock requires artifact hashes after every pinned requirement")
            continue
        hash_match = _LOCK_HASH.fullmatch(token)
        if current is None or hash_match is None:
            raise ValidationError("Canonical runtime dependency lock has content before a pinned requirement")
        dependencies[current]["artifact_hashes"].add(hash_match.group(1))
    if not dependencies or any(not item["artifact_hashes"] for item in dependencies.values()):
        raise ValidationError("Canonical runtime dependency lock requires exact versions and SHA-256 artifact hashes")
    for item in dependencies.values():
        item["artifact_hashes"] = frozenset(item["artifact_hashes"])
    return dependencies, hashlib.sha256(raw).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                return digest.digest()
            digest.update(block)


def _record_sha256(value):
    if re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", value) is None:
        raise ValidationError("Locked wheel RECORD uses a missing or non-SHA-256 file hash")
    try:
        encoded = value.partition("=")[2]
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Locked wheel RECORD contains an invalid SHA-256 file hash") from exc
    if len(decoded) != hashlib.sha256().digest_size:
        raise ValidationError("Locked wheel RECORD contains an invalid SHA-256 file hash")
    return decoded


def _regular_directory(path, label):
    try:
        metadata = os.lstat(path)
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise ValidationError(label + " is unavailable") from exc
    reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    junction = getattr(Path(path), "is_junction", lambda: False)()
    if stat.S_ISLNK(metadata.st_mode) or reparse or junction or not stat.S_ISDIR(metadata.st_mode):
        raise ValidationError(label + " must be a real directory, not a link or reparse point")
    return resolved


def _wheel_member_path(name):
    if (not isinstance(name, str) or not name or "\x00" in name or "\\" in name or
            name.startswith("/") or re.match(r"^[A-Za-z]:", name)):
        raise ValidationError("Locked wheel contains an unsafe member path")
    directory = name.endswith("/")
    value = name[:-1] if directory else name
    parts = value.split("/")
    unsafe_component = any(
        part in {"", ".", ".."} or ":" in part or part.endswith((" ", ".")) or
        unicodedata.normalize("NFC", part) != part or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        for part in parts)
    if not value or unsafe_component or PurePosixPath(value).as_posix() != value:
        raise ValidationError("Locked wheel contains a non-canonical member path")
    return value, directory


def _wheel_metadata(raw, name):
    try:
        message = BytesParser(policy=email_policy.default).parsebytes(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Locked wheel contains invalid " + name) from exc
    values = message.get_all(name, [])
    if message.defects or len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
        raise ValidationError("Locked wheel must contain one exact " + name + " metadata field")
    return values[0].strip()


def _wheel_install_path(member, dist_info):
    parts = member.split("/")
    if parts[0].endswith(".data"):
        expected_data = dist_info[:-len(".dist-info")] + ".data"
        if parts[0] != expected_data or len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
            raise ValidationError("Locked wheel uses an unsupported data or script installation scheme")
        parts = parts[2:]
    relative = PurePosixPath(*parts)
    if relative.name.lower().endswith(".pth") or relative.name.lower() in _STARTUP_HOOKS:
        raise ValidationError("Locked wheel contains a prohibited Python startup hook: " + relative.as_posix())
    return relative


def _inspect_locked_wheel(filename, raw, requirements):
    """Validate a complete hash-bound wheel and return immutable extraction bytes."""
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    allowed_hashes = set().union(*(item["artifact_hashes"] for item in requirements.values()))
    if artifact_sha256 not in allowed_hashes:
        raise ValidationError("Offline wheel artifact SHA-256 is absent from requirements.lock")
    if len(raw) > _MAX_DEPENDENCY_FILE_BYTES:
        raise ValidationError("Locked wheel artifact exceeds the runtime copy limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError("Locked runtime artifact is not a valid wheel: " + filename) from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_WHEEL_MEMBERS:
            raise ValidationError("Locked wheel has an invalid member count")
        members, folded_members, total = {}, set(), 0
        for info in infos:
            member, directory = _wheel_member_path(info.filename)
            folded = member.casefold()
            if member in members or folded in folded_members:
                raise ValidationError("Locked wheel contains a duplicate member path: " + member)
            folded_members.add(folded)
            mode = (info.external_attr >> 16) & 0xffff
            kind = stat.S_IFMT(mode)
            if info.flag_bits & 1:
                raise ValidationError("Locked wheel contains encrypted content")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValidationError("Locked wheel uses an unsupported compression type")
            if directory:
                if kind not in {0, stat.S_IFDIR}:
                    raise ValidationError("Locked wheel directory has an unsafe file type")
                members[member] = None
                continue
            if kind not in {0, stat.S_IFREG}:
                raise ValidationError("Locked wheel contains a link or special file: " + member)
            if info.file_size > _MAX_DEPENDENCY_FILE_BYTES:
                raise ValidationError("Locked wheel member exceeds the runtime copy limit")
            total += info.file_size
            if total > _MAX_DEPENDENCY_BYTES:
                raise ValidationError("Locked wheel exceeds the runtime extraction limit")
            try:
                data = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ValidationError("Locked wheel member cannot be read safely: " + member) from exc
            if len(data) != info.file_size:
                raise ValidationError("Locked wheel member size changed while reading: " + member)
            members[member] = data

    files = {path: value for path, value in members.items() if value is not None}
    metadata_paths = [path for path in files if path.endswith(".dist-info/METADATA")]
    wheel_paths = [path for path in files if path.endswith(".dist-info/WHEEL")]
    record_paths = [path for path in files if path.endswith(".dist-info/RECORD")]
    if len(metadata_paths) != 1 or len(wheel_paths) != 1 or len(record_paths) != 1:
        raise ValidationError("Locked wheel must contain exactly one METADATA, WHEEL and RECORD")
    dist_info = metadata_paths[0].split("/", 1)[0]
    expected_prefix = dist_info + "/"
    if wheel_paths[0] != expected_prefix + "WHEEL" or record_paths[0] != expected_prefix + "RECORD":
        raise ValidationError("Locked wheel metadata directories disagree")
    dist_info_roots = {path.split("/", 1)[0] for path in files if path.split("/", 1)[0].endswith(".dist-info")}
    if dist_info_roots != {dist_info}:
        raise ValidationError("Locked wheel contains an unexpected distribution metadata directory")
    observed_name = _wheel_metadata(files[metadata_paths[0]], "Name")
    observed_version = _wheel_metadata(files[metadata_paths[0]], "Version")
    wheel_message = BytesParser(policy=email_policy.default).parsebytes(files[wheel_paths[0]])
    wheel_versions = wheel_message.get_all("Wheel-Version", [])
    purelib_values = wheel_message.get_all("Root-Is-Purelib", [])
    wheel_tags = wheel_message.get_all("Tag", [])
    if (wheel_message.defects or [str(value) for value in wheel_versions] != ["1.0"] or len(purelib_values) != 1 or
            str(purelib_values[0]).lower() not in {"true", "false"} or not wheel_tags or
            any(not isinstance(value, str) or not value.strip() for value in wheel_tags)):
        raise ValidationError("Locked wheel contains invalid or incomplete WHEEL metadata")
    canonical_name = _distribution_name(observed_name)
    requirement = requirements.get(canonical_name)
    if requirement is None:
        raise ValidationError("Offline wheelhouse contains an artifact absent from requirements.lock: " + observed_name)
    if observed_version != requirement["version"]:
        raise ValidationError("Locked wheel does not match the exact version identity: " + requirement["name"])
    stem = dist_info[:-len(".dist-info")]
    if "-" not in stem:
        raise ValidationError("Locked wheel has an invalid distribution metadata directory")
    stem_name, stem_version = stem.rsplit("-", 1)
    if _distribution_name(stem_name) != canonical_name or stem_version.replace("_", "-") != observed_version.replace("_", "-"):
        raise ValidationError("Locked wheel metadata path disagrees with its exact identity")
    if artifact_sha256 not in requirement["artifact_hashes"]:
        raise ValidationError("Offline wheel artifact SHA-256 is absent from requirements.lock: " + requirement["name"])

    try:
        record_text = files[record_paths[0]].decode("utf-8")
        rows = list(csv.reader(io.StringIO(record_text, newline="")))
    except (UnicodeError, csv.Error) as exc:
        raise ValidationError("Locked wheel RECORD is invalid") from exc
    records = {}
    for row in rows:
        if len(row) != 3:
            raise ValidationError("Locked wheel RECORD has an invalid row")
        record_path, hash_value, size_value = row
        normalized, directory = _wheel_member_path(record_path)
        if directory or normalized in records:
            raise ValidationError("Locked wheel RECORD contains a duplicate or directory row")
        records[normalized] = (hash_value, size_value)
    if set(records) != set(files):
        raise ValidationError("Locked wheel RECORD inventory does not exactly match the archive")
    for member, data in files.items():
        hash_value, size_value = records[member]
        if member == record_paths[0]:
            if hash_value or size_value:
                raise ValidationError("Locked wheel RECORD self-entry must omit hash and size")
            continue
        if re.fullmatch(r"0|[1-9][0-9]*", size_value) is None:
            raise ValidationError("Locked wheel RECORD contains an invalid member size")
        expected_size = int(size_value)
        if expected_size != len(data) or _record_sha256(hash_value) != hashlib.sha256(data).digest():
            raise ValidationError("Locked wheel RECORD hash or size mismatch: " + member)

    installed = {}
    for member, data in files.items():
        relative = _wheel_install_path(member, dist_info)
        key = relative.as_posix()
        if not key or key in installed:
            raise ValidationError("Locked wheel contains a conflicting installed path: " + key)
        installed[key] = data
    return {"name": canonical_name, "display_name": requirement["name"],
            "version": observed_version, "artifact_sha256": artifact_sha256,
            "filename": filename, "files": installed}


def _load_locked_wheels(wheelhouse, requirements):
    """Read an operator-prepared offline wheelhouse into verified immutable bytes."""
    root = _regular_directory(wheelhouse, "Offline runtime wheelhouse")
    artifacts, total = {}, 0
    try:
        entries = sorted(root.iterdir(), key=lambda value: value.name)
    except OSError as exc:
        raise ValidationError("Offline runtime wheelhouse cannot be enumerated") from exc
    if not entries:
        raise ValidationError("Offline runtime wheelhouse is empty")
    for path in entries:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise ValidationError("Offline runtime wheel artifact is unavailable: " + path.name) from exc
        reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (not path.name.endswith(".whl") or stat.S_ISLNK(metadata.st_mode) or reparse or
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise ValidationError("Offline runtime wheelhouse may contain only regular .whl files: " + path.name)
        if metadata.st_size > _MAX_DEPENDENCY_FILE_BYTES:
            raise ValidationError("Offline runtime wheel artifact exceeds the runtime copy limit")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValidationError("Offline runtime wheel artifact cannot be read: " + path.name) from exc
        if len(raw) != metadata.st_size:
            raise ValidationError("Offline runtime wheel artifact changed while reading: " + path.name)
        total += len(raw)
        if total > _MAX_DEPENDENCY_BYTES:
            raise ValidationError("Offline runtime wheelhouse exceeds the runtime copy limit")
        artifact = _inspect_locked_wheel(path.name, raw, requirements)
        if artifact["name"] in artifacts:
            raise ValidationError("Offline runtime wheelhouse contains multiple artifacts for: " + artifact["display_name"])
        artifacts[artifact["name"]] = artifact
    missing = sorted(set(requirements) - set(artifacts))
    if missing:
        raise ValidationError("Offline runtime wheelhouse is missing locked dependencies: " + ", ".join(missing))
    return artifacts


def _extract_locked_wheels(artifacts, target):
    copied, folded, total = {}, {}, 0
    for name in sorted(artifacts):
        artifact = artifacts[name]
        for relative_text, raw in sorted(artifact["files"].items()):
            relative = PurePosixPath(relative_text)
            previous = copied.get(relative_text)
            digest = hashlib.sha256(raw).digest()
            previous_folded = folded.get(relative_text.casefold())
            if previous is not None or previous_folded is not None:
                raise ValidationError("Locked wheels contain a conflicting runtime file: " + relative_text)
            total += len(raw)
            if total > _MAX_DEPENDENCY_BYTES:
                raise ValidationError("Locked dependencies exceed the runtime extraction limit")
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open("xb") as stream:
                    stream.write(raw)
                os.chmod(destination, 0o600)
            except OSError as exc:
                raise ValidationError("Canonical runtime dependency extraction failed: " + relative_text) from exc
            if _file_sha256(destination) != digest:
                raise ValidationError("Canonical runtime dependency extraction failed verification: " + relative_text)
            copied[relative_text] = digest
            folded[relative_text.casefold()] = relative_text
    return copied


def _runtime_site_packages(interpreter, root, runtime_root):
    command = [str(interpreter), "-B", "-I", "-c",
               "import json,sysconfig; print(json.dumps([sysconfig.get_path('purelib'),sysconfig.get_path('platlib')]))"]
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        result = subprocess.run(command, cwd=root, env=child_env(env), stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=30, check=False)
        paths = json.loads(result.stdout.decode("utf-8")) if result.returncode == 0 else None
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValidationError("Canonical runtime could not report its isolated site-packages path") from exc
    if (not isinstance(paths, list) or len(paths) != 2 or
            any(not isinstance(value, str) or not value for value in paths)):
        raise ValidationError("Canonical runtime returned invalid site-packages paths")
    resolved = [Path(value).resolve() for value in paths]
    runtime_root = Path(runtime_root).resolve()
    if any(not value.is_relative_to(runtime_root) for value in resolved):
        raise ValidationError("Canonical runtime site-packages escapes the isolated runtime")
    return resolved[0]


def _runtime_interpreter(runtime_root):
    return Path(runtime_root) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _runtime_tree_files(site_packages):
    files = {}
    for base, directories, names in os.walk(site_packages, topdown=True, followlinks=False):
        base_path = Path(base)
        for name in list(directories) + list(names):
            path = base_path / name
            metadata = os.lstat(path)
            reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(metadata.st_mode) or reparse:
                raise ValidationError("Canonical runtime contains a linked dependency path")
            if name in names and not stat.S_ISREG(metadata.st_mode):
                raise ValidationError("Canonical runtime contains a special dependency file")
        for name in names:
            relative = (base_path / name).relative_to(site_packages).as_posix()
            if name.lower().endswith(".pth") or name.lower() in _STARTUP_HOOKS:
                raise ValidationError("Canonical runtime contains a prohibited Python startup hook: " + relative)
            files[relative] = _file_sha256(base_path / name)
    return files


def _validate_runtime(interpreter, root, runtime_root, expected_versions, expected_files):
    site_packages = _runtime_site_packages(interpreter, root, runtime_root)
    if _runtime_tree_files(site_packages) != expected_files:
        raise ValidationError("Canonical runtime dependency inventory changed during validation")
    script = (
        "import importlib,importlib.metadata as m,json,sys,sysconfig;"
        "mods={'attrs':'attrs','jsonschema':'jsonschema','jsonschema-specifications':'jsonschema_specifications',"
        "'pyyaml':'yaml','referencing':'referencing','rpds-py':'rpds','typing-extensions':'typing_extensions'};"
        "[importlib.import_module(v) for v in mods.values()];"
        "print(json.dumps({'versions':{k:m.version(k) for k in mods},'prefix':sys.prefix,"
        "'purelib':sysconfig.get_path('purelib'),'platlib':sysconfig.get_path('platlib')},sort_keys=True))"
    )
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PIP_NO_INDEX="1")
    try:
        result = subprocess.run([str(interpreter), "-B", "-I", "-c", script], cwd=root,
                                env=child_env(env), stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=60, check=False)
        output = json.loads(result.stdout.decode("utf-8")) if result.returncode == 0 else None
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValidationError("Canonical runtime dependency imports could not be validated") from exc
    if (not isinstance(output, dict) or output.get("versions") != expected_versions or
            Path(output.get("prefix", "")).resolve() != Path(runtime_root).resolve() or
            any(not Path(output.get(key, "")).resolve().is_relative_to(Path(runtime_root).resolve())
                for key in ("purelib", "platlib"))):
        raise ValidationError("Canonical runtime dependency versions or isolation do not match the exact lock")
    if _runtime_tree_files(site_packages) != expected_files:
        raise ValidationError("Canonical runtime dependency inventory changed after isolated imports")


def _remove_runtime_tree(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    metadata = os.lstat(path)
    reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISDIR(metadata.st_mode):
        raise ValidationError("Refusing to clean a linked or non-directory runtime transaction path")
    shutil.rmtree(path)


def _transaction_sibling(runtime_root, kind):
    for _attempt in range(10):
        candidate = runtime_root.with_name(runtime_root.name + "." + kind + "-" + uuid.uuid4().hex)
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise ValidationError("Could not allocate a unique runtime transaction path")


def ensure_installed_runtime(destination, wheelhouse=None):
    """Build a hash-bound offline runtime and atomically replace the canonical runtime."""
    from .runtime_commands import installed_paths
    root, interpreter, entry_point = installed_paths(destination)
    runtime_root = root / ".agentic" / ".venv"
    if runtime_root.exists() or runtime_root.is_symlink():
        metadata = os.lstat(runtime_root)
        reparse = getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        junction = getattr(runtime_root, "is_junction", lambda: False)()
        if (stat.S_ISLNK(metadata.st_mode) or reparse or junction or not stat.S_ISDIR(metadata.st_mode) or
                runtime_root.resolve() != runtime_root):
            raise ValidationError("Refusing linked or reparse-point canonical runtime")
    lock_path = root / ".agentic" / "requirements.lock"
    dependencies, lock_sha256 = _locked_dependencies(lock_path)
    if set(dependencies) != set(_RUNTIME_IMPORTS):
        raise ValidationError("Canonical runtime dependency lock and reviewed import invariant disagree")
    source = wheelhouse if wheelhouse is not None else root / ".agentic" / "wheelhouse"
    artifacts = _load_locked_wheels(source, dependencies)
    expected_versions = {name: dependencies[name]["version"] for name in sorted(dependencies)}
    stage = _transaction_sibling(runtime_root, "staging")
    backup = _transaction_sibling(runtime_root, "backup")
    had_previous = runtime_root.exists()
    committed = False
    try:
        venv.EnvBuilder(with_pip=False, system_site_packages=False, clear=False).create(stage)
        stage_interpreter = _runtime_interpreter(stage)
        if not stage_interpreter.is_file() or not entry_point.is_file():
            raise ValidationError("Canonical installed runtime or workflow entry point is unavailable")
        site_packages = _runtime_site_packages(stage_interpreter, root, stage)
        site_packages.mkdir(parents=True, exist_ok=True)
        expected_files = _extract_locked_wheels(artifacts, site_packages)
        _validate_runtime(stage_interpreter, root, stage, expected_versions, expected_files)
        if had_previous:
            os.replace(runtime_root, backup)
        os.replace(stage, runtime_root)
        _validate_runtime(interpreter, root, runtime_root, expected_versions, expected_files)
        # Final-path validation is the commit point.  From here onward the new
        # canonical runtime is the known-good copy; a backup-cleanup error must
        # never roll it back to a possibly partially removed backup.
        committed = True
        if backup.exists():
            _remove_runtime_tree(backup)
    except BaseException as original:
        if not committed:
            try:
                # Filesystem state, rather than flags set after os.replace(),
                # closes the asynchronous-exception windows on both renames.
                if backup.exists() or backup.is_symlink():
                    if runtime_root.exists() or runtime_root.is_symlink():
                        _remove_runtime_tree(runtime_root)
                    os.replace(backup, runtime_root)
                elif (not had_previous and not stage.exists() and
                      (runtime_root.exists() or runtime_root.is_symlink())):
                    _remove_runtime_tree(runtime_root)
            except BaseException as rollback_error:
                raise ValidationError(
                    "Canonical runtime replacement failed and automatic rollback could not complete; "
                    "the uniquely named backup was retained for recovery: " + str(backup)) from rollback_error
        raise
    finally:
        if stage.exists() or stage.is_symlink():
            _remove_runtime_tree(stage)
        if committed and (backup.exists() or backup.is_symlink()):
            _remove_runtime_tree(backup)
    return {"interpreter": str(interpreter), "entry_point": str(entry_point),
            "dependency_source": "operator-supplied offline wheelhouse; complete wheel SHA-256 pinned by requirements.lock; RECORD and extracted inventory verified",
            "requirements_lock_sha256": lock_sha256,
            "locked_dependencies": {item["name"]: item["version"] for item in dependencies.values()},
            "artifact_sha256": {artifacts[name]["display_name"]: artifacts[name]["artifact_sha256"] for name in sorted(artifacts)},
            "verified_dependency_files": len(expected_files)}


def unresolved(value):
    return value is None or value == "" or (isinstance(value, str) and
        ("CHANGE_ME" in value or "SET_BY_BOOTSTRAP" in value or not value.strip() or
         value == "00000000-0000-0000-0000-000000000000" or value == "0" * 40))


def project_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return str(parsed) if parsed.int and str(parsed) == value else None
    except (ValueError, TypeError, AttributeError):
        return None


def discover_repository(root, repository=None):
    """Compatibility entry point for the explicit GitHub discovery adapter."""
    return github_provider.discover_repository(root, repository, executable=_executable,
                                               read_command=_read_command)


def detect_test_command(root):
    """Inspect a bounded package manifest or Python test markers; execute nothing."""
    if root is None or not Path(root).is_dir():
        return None
    with Tree(root) as tree:
        if tree.inspect("package.json") is not None:
            try:
                value = loads(tree.read("package.json", maximum=MAX_METADATA_BYTES).decode("utf-8"))
                script = value.get("scripts", {}).get("test") if isinstance(value, dict) and isinstance(value.get("scripts", {}), dict) else None
                if isinstance(script, str) and script.strip():
                    return "npm test"
            except (ValidationError, UnicodeError):
                pass
        if any(tree.inspect(name) is not None for name in ("pyproject.toml", "pytest.ini")):
            return "pytest"
        try:
            with Tree(Path(root) / "tests"):
                return "pytest"
        except FileNotFoundError:
            pass
    return None


def operating_capacity_proposal(config, *, existing, requested=False, dry_run=False):
    """Offer a bounded local PR change; ordinary adoption never applies it."""
    proposed = copy.deepcopy(config)
    report = {"status": "NOT_APPLICABLE", "flag": "--propose-operating-capacity",
              "requested": requested, "staged_locally": False, "owner_review_required": True,
              "execution_authority": False, "changes": [], "values": [],
              "next_action": "New projects already use the shipped operating-capacity policy."}
    if not existing:
        if requested:
            raise ValidationError("--propose-operating-capacity requires existing project governance to review")
        return proposed, report
    execution = config.get("execution") if isinstance(config, dict) else None
    ceiling = execution.get("max_parallel_tickets") if isinstance(execution, dict) else None
    reviewers = execution.get("independent_reviewers", {}) if isinstance(execution, dict) else None
    if (type(ceiling) is not int or ceiling < 1 or not isinstance(reviewers, dict)
            or (reviewers and reviewers.get("allocation") != "one_per_stream")
            or ("count" in reviewers and (type(reviewers["count"]) is not int or reviewers["count"] < 1))):
        report.update(status="UNAVAILABLE", next_action="Resolve $.execution.max_parallel_tickets and $.execution.independent_reviewers before proposing a capacity migration.")
        if requested:
            raise ValidationError(report["next_action"])
        return proposed, report
    target = max(6, ceiling)
    count = reviewers.get("count", "derived: one per operating stream")
    report["values"] = [
        {"path": "$.execution.max_parallel_tickets", "current": ceiling, "proposed": target},
        {"path": "$.execution.independent_reviewers.count", "current": count,
         "proposed": "derived: one per operating stream"}]
    if target != ceiling:
        report["changes"].append({**report["values"][0], "action": "replace"})
    if "count" in reviewers:
        report["changes"].append({**report["values"][1], "action": "remove"})
    if requested and report["changes"]:
        proposed["execution"]["max_parallel_tickets"] = target
        if "count" in reviewers:
            del proposed["execution"]["independent_reviewers"]["count"]
        report.update(status="PLANNED_FOR_REVIEW" if dry_run else "STAGED_FOR_REVIEW", staged_locally=not dry_run,
            next_action="Include these exact local governance changes in the adoption PR. Keep adoption quiescent until owner review and merge; operating choices and all other limits are preserved.")
    elif report["changes"]:
        report.update(status="OFFERED", next_action="Offer these changes in the adoption PR. With explicit user direction, rerun bootstrap with --propose-operating-capacity on the isolated adoption branch; otherwise keep current governance.")
    else:
        report.update(status="NOT_NEEDED", next_action="The existing ceiling already permits six and reviewer count is derived; no capacity governance change is needed.")
    report["adoption_pr_section"] = "\n".join([
        "### Operating-capacity governance proposal", "",
        "| Governance path | Current | Proposed |", "| --- | --- | --- |",
        *[f"| `{row['path']}` | {row['current']} | {row['proposed']} |" for row in report["values"]],
        "", "Status: " + report["status"] + ". " + report["next_action"],
        "This local proposal neither launches agents nor enables automation or authorizes merge."])
    return proposed, report


def prepare_config(template, *, existing=None, receipt_project_id=None, project_root=None,
                   overrides=None, codeowner="@maintainer", discover=True):
    validate_codeowner(codeowner)
    overrides = overrides or {}
    config = copy.deepcopy(existing if existing is not None else template)
    sections = ("project", "github", "jira", "template", "validation", "merge_gate", "specialist_reviews")
    shape_ok = (isinstance(config, dict) and all(isinstance(config.get(key), dict) for key in sections) and
                all(isinstance(rule, dict) for rule in config["specialist_reviews"].values()) and
                isinstance(config["validation"].get("commands", []), list) and
                isinstance(config["validation"].get("required_ci_checks", []), list) and
                all(isinstance(check, dict) for check in config["validation"].get("required_ci_checks", [])))
    if not shape_ok:
        # Preserve malformed project policy for the shared validator's complete
        # residue; guessing missing policy sections could broaden authority.
        current_id = config.get("project", {}).get("id") if isinstance(config, dict) and isinstance(config.get("project"), dict) else None
        saved_id = project_uuid(current_id)
        if receipt_project_id is not None and (not project_uuid(receipt_project_id) or (saved_id and receipt_project_id != saved_id)):
            raise ValidationError("Configuration project UUID disagrees with the persisted installation receipt")
        return {"config": config, "project_id": saved_id or receipt_project_id or str(uuid.uuid4()),
                "warnings": ["Preserved incomplete or malformed existing policy; resolve the shared validator's exact paths before configuration can be accepted"],
                "observations": [], "test_commands_executed": False, "execution_authority": False}
    project, github, jira = config["project"], config["github"], config["jira"]
    warnings, observations = [], []
    def fill(section, key, value, flag):
        if value is None:
            return
        if unresolved(section.get(key)):
            section[key] = value
        elif section[key] != value and flag in overrides:
            warnings.append(f"Preserved existing {flag}; edit reviewed PROJECT_CONFIG.yaml to change an established choice")
    if existing is None:
        # Source examples never assert a target's numeric identity/default branch.
        github.update(repository_id=None, base_branch=None)
        project["id"] = None
        config["merge_gate"]["trusted_owner_ids"] = []
    elif unresolved(github.get("repository")):
        # An unbound source/example numeric ID cannot identify a target repo.
        github.update(repository_id=None, base_branch=None)
    saved_id = project_uuid(project.get("id"))
    if receipt_project_id is not None:
        if not project_uuid(receipt_project_id) or (saved_id and saved_id != receipt_project_id):
            raise ValidationError("Configuration project UUID disagrees with the persisted installation receipt")
        saved_id = receipt_project_id
    if not saved_id and not unresolved(project.get("id")):
        raise ValidationError("Existing project UUID is invalid; reconcile it explicitly before installation")
    project["id"] = saved_id or str(uuid.uuid4())
    config["template"]["expected_workflow_version"] = VERSION
    if "repository" in overrides:
        fill(github, "repository", repository_name(overrides["repository"]), "repository")
    repository = None if unresolved(github.get("repository")) else repository_name(github["repository"])
    needs_metadata = any(unresolved(github.get(key)) and key not in overrides
                         for key in ("repository", "repository_id", "base_branch"))
    metadata = discover_repository(project_root, repository) if discover and needs_metadata and project_root is not None else {}
    warnings.extend(metadata.get("warnings", []))
    observations.extend(metadata.get("observations", []))
    fill(github, "repository", metadata.get("repository"), "repository")
    if not unresolved(github.get("repository_id")) and metadata.get("repository_id") is not None and github["repository_id"] != metadata["repository_id"]:
        raise ValidationError("Preserved repository_id conflicts with observed repository metadata; reconcile repository identity before installation")
    if not unresolved(github.get("base_branch")) and metadata.get("base_branch") is not None and github["base_branch"] != metadata["base_branch"]:
        warnings.append("Preserved configured base_branch differs from observed default branch; review the target choice before adoption merge")
    for key in ("repository_id", "base_branch"):
        supplied = overrides.get(key)
        if supplied is not None and metadata.get(key) is not None and supplied != metadata[key]:
            raise ValidationError(f"Explicit {key} conflicts with observed repository metadata; reconcile before installation")
        fill(github, key, overrides.get(key, metadata.get(key)), key)
    repository = None if unresolved(github.get("repository")) else github["repository"]
    name = repository.split("/")[1] if repository else None
    for key in ("name", "short_name"):
        fill(project, key, overrides.get(key, name), key)
    for rule in config["specialist_reviews"].values():
        fill(rule, "reviewer_identity", codeowner, "codeowner")
    if existing is None or (unresolved(jira.get("site")) and unresolved(jira.get("project_key"))):
        if "jira_site" not in overrides and "jira_key" not in overrides:
            jira.update(enabled=False, site=None, project_key=None)
        else:
            jira.update(enabled=True, site=overrides.get("jira_site"), project_key=overrides.get("jira_key"))
    else:
        for key, flag in (("site", "jira_site"), ("project_key", "jira_key")):
            fill(jira, key, overrides.get(flag), flag)
    validation = config["validation"]
    if existing is None or any(unresolved(item.get("workflow_sha")) for item in validation.get("required_ci_checks", [])):
        if existing is None:
            validation["required_ci_checks"] = []
        else:
            warnings.append("Preserved unresolved existing CI check definitions; configure or explicitly remove them in PROJECT_CONFIG.yaml")
    if not validation.get("commands") or any(unresolved(command) for command in validation["commands"]):
        command = overrides["test_command"] if "test_command" in overrides else detect_test_command(project_root)
        validation["commands"] = [command] if command else []
    elif "test_command" in overrides and validation["commands"] != [overrides["test_command"]]:
        warnings.append("Preserved existing validation commands; edit reviewed PROJECT_CONFIG.yaml to change them")
    return {"config": config, "project_id": project["id"], "warnings": warnings,
            "observations": observations, "test_commands_executed": False, "execution_authority": False}


def _post_command(command, root, timeout=60):
    """Execute a verified installed CLI once, with bounded output and child time."""
    result = {"command": command, "exit_code": None, "output": None, "diagnostic": None}
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    process = None
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            deadline = time.monotonic() + timeout
            process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=err, env=child_env(env), shell=False)
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > MAX_METADATA_BYTES:
                        raise ValidationError("Post-install check exceeded its time or byte limit")
                    try:
                        process.wait(timeout=min(.05, max(.001, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                result["exit_code"] = process.returncode
                if time.monotonic() > deadline or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > MAX_METADATA_BYTES:
                    raise ValidationError("Post-install check exceeded its time or byte limit")
                out.seek(0)
                raw = out.read(MAX_METADATA_BYTES + 1)
                result["output_stream"] = "stdout"
                if not raw.strip() and process.returncode:
                    err.seek(0)
                    raw = err.read(MAX_METADATA_BYTES + 1)
                    result["output_stream"] = "stderr"
                value = loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValidationError("Post-install CLI output is not an object")
                result["output"] = value
                if process.returncode:
                    result["diagnostic"] = "Installed CLI returned a nonzero exit status"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
    except (ValidationError, OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        result["diagnostic"] = "Post-install CLI did not return bounded valid JSON; inspect the installed runtime/dependencies and rerun the recorded command"
    return result


def post_install_checks(destination, installation):
    root = Path(destination).resolve()
    from .runtime_commands import installed_paths
    _root, interpreter, entry_point = installed_paths(root)
    commands = [[str(interpreter), "-B", "-I", str(entry_point), "--root", str(root), action]
                for action in ("verify-installation", "validate-config")]
    try:
        _verify_import_surface(root, installation["source_manifest_sha256"])
    except (ValidationError, OSError, ValueError, KeyError, TypeError):
        checks = [{"command": command, "exit_code": None, "output": None, "execution_status": "NOT_RUN",
                   "diagnostic": "Trusted preflight rejected installed runtime bytes/import surfaces; preserve unexpected files and reconcile before executing installed code"}
                  for command in commands]
    else:
        checks = [_post_command(command, root) for command in commands]
    verification, validation = checks
    output = verification["output"] or {}
    integrity = (verification["exit_code"] == 0 and verification["diagnostic"] is None and
                 output.get("integrity_valid") is True and
                 output.get("source_manifest_sha256") == installation["source_manifest_sha256"])
    inspection = installation["configuration"]
    config_output = validation["output"] or {}
    configured = (integrity and validation["exit_code"] == 0 and validation["diagnostic"] is None and
                  config_output.get("status") == "ACCEPTED" and inspection["status"] == "ACCEPTED" and
                  config_output.get("policy_sha256") == inspection["policy_sha256"])
    operating = config_output.get("operating") or {}
    initialized_operating = installation.get("operating") or {}
    configured = (configured and operating.get("status") == "ACCEPTED" and
                  initialized_operating.get("status") == "ACCEPTED" and
                  isinstance(operating.get("hash"), str) and
                  operating["hash"] == initialized_operating.get("hash"))
    actual_residue = config_output.get("unresolved")
    observed_configuration = (config_output.get("status") in {"ACCEPTED", "REJECTED"} and
                              isinstance(actual_residue, list) and all(isinstance(item, dict) and
                                  isinstance(item.get("path"), str) and isinstance(item.get("reason"), str)
                                  for item in actual_residue))
    configured = configured and observed_configuration and not actual_residue
    status = "CONFIGURED" if configured else "INSTALLED_UNCONFIGURED" if integrity else "INSTALLATION_VERIFICATION_FAILED"
    if observed_configuration:
        configuration = {**config_output, "observation_source": "installed validate-config subprocess",
                         "pre_install_inspection_status": inspection["status"]}
    else:
        configuration = {"status": "UNOBSERVED", "policy_sha256": None, "unresolved": [],
                         "ci_gate": "NOT_CONFIGURED", "warnings": [],
                         "observation_source": "installed validate-config output unavailable or incomplete"}
    if configured:
        next_action = "Show workflow.py operating show and offer keep defaults, review Epics and recommend, or custom; include the table in the adoption PR, then verify its accepted default checkout after merge"
    elif not integrity:
        next_action = "Resolve the failed installation verification and rerun both recorded checks; do not claim installation or activation"
    elif observed_configuration and config_output["status"] == "REJECTED" and actual_residue:
        next_action = "Resolve the actual installed configuration paths: " + "; ".join(
            item["path"] + " (" + str(item.get("flag", "edit PROJECT_CONFIG.yaml")) + ")" for item in actual_residue) + "; rerun both recorded checks"
    elif observed_configuration and config_output["status"] == "ACCEPTED":
        next_action = "Installed configuration differs from the inspected installation plan; replan against the current configuration and rerun both recorded checks"
    else:
        next_action = "Correct the installed runtime/dependencies or unreadable configuration using the recorded command diagnostics, then rerun both checks"
    from .host_preflight import preflight, render_markdown
    host = preflight(destination)
    if host["next_action"]:
        next_action += "; host preflight WARN: " + host["next_action"]
    return {"status": status, "transaction_status": installation["status"], "installed": integrity,
            "post_install_checks": checks, "active": False, "configuration": configuration,
            "pre_install_configuration": inspection, "host_preflight": host,
            "adoption_pr_host_preflight_section": render_markdown(host), "next_action": next_action}


def _verify_import_surface(root, expected_digest):
    """Use trusted release code to reject target import shadows before execution.

    Isolated Python excludes the target script/current directory and PYTHONPATH;
    the workflow intentionally inserts .agentic/lib, whose complete inventory is
    therefore pinned here. Unexpected caches/packages/extensions are not deleted.
    """
    from .installer import INSTALLED, verify_installed
    if verify_installed(root) != expected_digest:
        raise ValidationError("Installed source identity differs from bootstrap")
    with Tree(root) as tree:
        receipt = loads(tree.read(INSTALLED).decode("utf-8"))
        source_raw = receipt.get("source_manifest_json")
        if not isinstance(source_raw, str) or sha256(source_raw.encode("utf-8")) != expected_digest:
            raise ValidationError("Installed import inventory requires the pinned source manifest")
        manifest = loads(source_raw)
        for prefix in (".agentic/lib/", ".agentic/scripts/"):
            expected = {path.removeprefix(prefix): digest for path, digest in manifest["files"].items() if path.startswith(prefix)}
            with Tree(Path(root) / prefix) as runtime:
                if set(runtime.file_list()) != set(expected):
                    raise ValidationError("Unexpected installed runtime import files")
                for path, digest in expected.items():
                    if sha256(runtime.read(path)) != digest:
                        raise ValidationError("Installed runtime import file differs from source")
