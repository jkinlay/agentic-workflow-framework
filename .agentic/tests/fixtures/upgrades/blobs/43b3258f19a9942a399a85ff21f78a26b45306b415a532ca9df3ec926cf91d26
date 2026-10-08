"""Fail-closed Git reads isolated from ambient config and replacement objects."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from . import ValidationError
from .child_process import child_env


def isolated_git_environment() -> dict[str, str]:
    permitted = {"COMSPEC", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in permitted}
    environment.update({
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    })
    return child_env(environment)


def run_isolated_git(root: Path, args: list[str], *, maximum: int, timeout: int,
                     label: str) -> bytes:
    executable = shutil.which("git")
    if not executable:
        raise ValidationError(f"{label} is unavailable")
    executable = str(Path(executable).resolve())
    if Path(executable).suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise ValidationError(f"{label} needs a native executable")
    command = [
        executable, "--no-replace-objects",
        "-c", f"core.attributesFile={os.devnull}",
        "-c", "core.fsmonitor=false",
        "-c", "core.preloadIndex=false",
        "-c", "core.untrackedCache=false",
        "-c", "submodule.recurse=false",
        "-C", str(root), *args,
    ]
    try:
        done = subprocess.run(
            command, cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            env=child_env(isolated_git_environment()), timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError(f"{label} is unavailable") from exc
    if done.returncode or len(done.stdout) > maximum:
        raise ValidationError(f"{label} failed or exceeded its bound")
    return done.stdout
