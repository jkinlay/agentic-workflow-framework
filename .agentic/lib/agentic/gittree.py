"""Compute and verify Git trees without writing objects or other Git metadata."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess

from . import ValidationError
from .child_process import child_env


@dataclass(frozen=True)
class CandidateTree:
    tested_tree: str
    ignored_untracked: tuple[str, ...]
    object_format: str


def _git(root, *args, input_bytes=None):
    executable = shutil.which("git")
    if not executable:
        raise ValidationError("Git is required to compute a tested tree")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    safe_root = str(Path(root).resolve())
    try:
        result = subprocess.run(
            [executable, "-c", "safe.directory=" + safe_root, *args], cwd=root,
            input=input_bytes, capture_output=True,
            timeout=60, env=child_env(env), check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"Git tree command failed: {type(exc).__name__}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ValidationError("Git tree command failed: " + (detail or "exit " + str(result.returncode)))
    return result.stdout


def _path(value):
    if not isinstance(value, str) or not value:
        raise ValidationError("change path must be a nonempty string")
    if "\0" in value or "\n" in value or "\r" in value:
        raise ValidationError("Git tree paths containing newline or NUL are unsupported")
    if "\\" in value:
        raise ValidationError("change paths must use Git '/' separators")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or value.endswith("/") or any(part in ("", ".", "..") for part in parsed.parts):
        raise ValidationError("change path must be a normalized repository-relative path: " + value)
    return value


def _decode_path(value):
    path = value.decode("utf-8", "surrogateescape")
    return _path(path)


def _base_entries(root, base, oid_bytes):
    raw = _git(root, "ls-tree", "-rz", "--full-tree", base)
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
        except (ValueError, UnicodeError) as exc:
            raise ValidationError("Git returned a malformed base tree entry") from exc
        path = _decode_path(raw_path)
        if kind not in {"blob", "commit"} or len(oid) != oid_bytes * 2:
            raise ValidationError("Git returned an unsupported base tree entry: " + path)
        entries[path] = (mode, oid)
    return entries


def _status(root):
    raw = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")
    records = raw.split(b"\0")
    tracked, untracked = set(), set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise ValidationError("Git returned malformed porcelain status")
        xy = record[:2].decode("ascii", "strict")
        path = _decode_path(record[3:])
        if xy == "??":
            untracked.add(path)
            continue
        if xy == "!!":
            continue
        tracked.add(path)
        if "R" in xy or "C" in xy:
            if index >= len(records) or not records[index]:
                raise ValidationError("Git returned malformed rename/copy status")
            tracked.add(_decode_path(records[index]))
            index += 1
    return tracked, untracked


def _file_mode(root, path, base_mode):
    target = Path(root, *PurePosixPath(path).parts)
    if target.is_symlink():
        return "120000"
    if not target.is_file():
        raise ValidationError("declared added/modified path is not a regular file or symbolic link: " + path)
    filemode = _git(root, "config", "--bool", "core.filemode").decode("ascii", "replace").strip()
    if filemode == "true":
        return "100755" if stat.S_IMODE(target.stat().st_mode) & 0o111 else "100644"
    staged = _git(root, "ls-files", "--stage", "-z", "--", path)
    if staged:
        try:
            index_mode = staged.split(b" ", 1)[0].decode("ascii")
        except UnicodeError as exc:
            raise ValidationError("Git returned a malformed index mode for " + path) from exc
        if index_mode in {"100644", "100755"}:
            return index_mode
    return base_mode if base_mode in {"100644", "100755"} else "100644"


def _blob(root, path, mode):
    if mode == "120000":
        target = Path(root, *PurePosixPath(path).parts)
        data = os.fsencode(os.readlink(target))
        return _git(root, "hash-object", "--path=" + path, "--stdin", input_bytes=data).decode("ascii").strip()
    return _git(root, "hash-object", "--path=" + path, "--", path).decode("ascii").strip()


def _tree_id(root, entries, oid_bytes):
    tree = {}
    for path, entry in entries.items():
        node = tree
        parts = path.split("/")
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValidationError("file/directory collision in candidate tree: " + path)
            node = child
        if parts[-1] in node:
            raise ValidationError("duplicate candidate tree path: " + path)
        node[parts[-1]] = entry

    def emit(node):
        records = []
        for name, value in node.items():
            raw_name = name.encode("utf-8", "surrogateescape")
            if isinstance(value, dict):
                oid = emit(value)
                records.append((raw_name + b"/", b"40000 " + raw_name + b"\0" + bytes.fromhex(oid)))
            else:
                mode, oid = value
                records.append((raw_name, mode.encode("ascii") + b" " + raw_name + b"\0" + bytes.fromhex(oid)))
        body = b"".join(record for _, record in sorted(records, key=lambda item: item[0]))
        oid = _git(root, "hash-object", "-t", "tree", "--stdin", input_bytes=body).decode("ascii").strip()
        if len(oid) != oid_bytes * 2:
            raise ValidationError("Git returned a tree ID inconsistent with the repository object format")
        return oid

    return emit(tree)


def candidate_tree(root, base, changes):
    """Return the base tree plus declared changes, with no Git metadata writes."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValidationError("repository root is not a directory")
    object_format = _git(root, "rev-parse", "--show-object-format").decode("ascii").strip()
    if object_format not in {"sha1", "sha256"}:
        raise ValidationError("unsupported Git object format: " + object_format)
    oid_bytes = 20 if object_format == "sha1" else 32
    if not isinstance(base, str) or not base.strip():
        raise ValidationError("base must name a Git commit or tree")
    if not isinstance(changes, list):
        raise ValidationError("changes must be a list")
    declared = {}
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "action"}:
            raise ValidationError("each change requires exactly path and action")
        path = _path(change["path"])
        if path in declared:
            raise ValidationError("duplicate declared change path: " + path)
        if change["action"] not in {"added", "modified", "deleted"}:
            raise ValidationError("unsupported change action for " + path)
        declared[path] = change["action"]

    entries = _base_entries(root, base, oid_bytes)
    tracked_status, untracked = _status(root)
    undeclared = sorted(tracked_status - set(declared))
    if undeclared:
        raise ValidationError("scope violation: undeclared tracked change(s): " + ", ".join(undeclared))

    for path, action in declared.items():
        prior = entries.get(path)
        target = Path(root, *PurePosixPath(path).parts)
        exists = target.exists() or target.is_symlink()
        if prior and prior[0] == "160000":
            raise ValidationError("declared changes to gitlinks are unsupported: " + path)
        if action == "added":
            if prior is not None:
                raise ValidationError("declared added path already exists in the base tree: " + path)
            if not exists:
                raise ValidationError("declared added path is absent from the worktree: " + path)
        elif action == "modified":
            if prior is None:
                raise ValidationError("declared modified path is absent from the base tree: " + path)
            if not exists:
                raise ValidationError("declared modified path is absent from the worktree: " + path)
        else:
            if prior is None:
                raise ValidationError("declared deleted path is absent from the base tree: " + path)
            if exists:
                raise ValidationError("declared deleted path still exists in the worktree: " + path)
            del entries[path]
            continue
        mode = _file_mode(root, path, prior[0] if prior else None)
        oid = _blob(root, path, mode)
        if len(oid) != oid_bytes * 2:
            raise ValidationError("Git returned a blob ID inconsistent with the repository object format")
        current = (mode, oid)
        if current == prior:
            raise ValidationError("declared path is unchanged from the base tree: " + path)
        entries[path] = current

    ignored = tuple(sorted(untracked - {path for path, action in declared.items() if action == "added"}))
    return CandidateTree(_tree_id(root, entries, oid_bytes), ignored, object_format)


def tested_tree(root, base, changes):
    """Return the candidate tree ID for base plus exactly changes."""
    return candidate_tree(root, base, changes).tested_tree


def verify_publisher_tree(root, expected):
    """Require the publisher's committed HEAD tree to equal the worker-tested tree."""
    if not isinstance(expected, str) or len(expected) not in {40, 64}:
        raise ValidationError("tested_tree must be a SHA-1 or SHA-256 object ID")
    actual = _git(Path(root).resolve(), "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    if actual != expected:
        raise ValidationError(
            "scope violation: publisher HEAD tree differs from tested_tree; return to the worker without publisher repair"
        )
    return actual
