"""Authoritative raw-Git mode maps for deterministic release archives."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

from agentic.child_process import child_env, isolated_git_env


FORMAT = "awf-raw-git-modes-1"
REGULAR_MODES = {"100644", "100755"}


def _validate(files):
    if not isinstance(files, dict) or not files:
        raise ValueError("raw Git mode manifest has no files")
    result = {}
    for path, mode in files.items():
        if (not isinstance(path, str) or not path or "\\" in path or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise ValueError("raw Git mode manifest contains an unsafe path")
        if mode not in REGULAR_MODES:
            raise ValueError("raw Git mode manifest contains a non-regular mode: " + path)
        result[path] = mode
    return result


def write_mode_manifest(path, entries):
    files = _validate({entry.path: entry.mode for entry in entries})
    raw = (json.dumps({"format": FORMAT, "files": files}, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)
    return Path(path)


def _git_modes(root):
    executable = shutil.which("git")
    if not executable:
        raise ValueError("Git is required when no raw mode manifest is supplied")
    command = [executable, "--no-replace-objects", "-c", "core.useReplaceRefs=false",
               "-C", str(Path(root).resolve()), "ls-tree", "-rz", "--full-tree", "HEAD"]
    result = subprocess.run(command, capture_output=True, timeout=120, check=False,
                            env=child_env(isolated_git_env()))
    if result.returncode:
        raise ValueError("cannot read authoritative modes from the raw HEAD tree")
    files = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Git returned malformed raw-tree mode data") from exc
        if kind != "blob" or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
            raise ValueError("raw Git tree contains a non-blob release entry: " + path)
        files[path] = mode
    return _validate(files)


def load_modes(root, manifest=None):
    if manifest is None:
        return _git_modes(root)
    value = json.loads(Path(manifest).read_bytes().decode("utf-8"))
    if not isinstance(value, dict) or set(value) != {"format", "files"} or value["format"] != FORMAT:
        raise ValueError("unsupported raw Git mode manifest")
    return _validate(value["files"])


def archive_mode(modes, repository_path):
    try:
        return int(modes[repository_path], 8)
    except KeyError as exc:
        raise ValueError("archive member has no authoritative raw Git mode: " + repository_path) from exc
