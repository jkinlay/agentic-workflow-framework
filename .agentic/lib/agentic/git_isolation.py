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
    # Archive format names can select arbitrary repo-local tar.<format>.command
    # programs. Only the explicit built-in tar invocation used by our readers
    # is safe; reject output/remote/format switches rather than running a filter.
    if args and args[0] == "archive" and (
            len(args) < 3 or args[1] != "--format=tar"
            or any(value.startswith("-") for value in args[2:])):
        raise ValidationError(f"{label} needs a built-in tar archive")
    # Raw cat-file does not apply smudge/process drivers. Do not let a caller
    # opt into conversion, which would execute repo-local filter code.
    if args and args[0] == "cat-file" and any(
            value in {"--filters", "--textconv"} for value in args[1:]):
        raise ValidationError(f"{label} needs unfiltered object bytes")
    executable = shutil.which("git")
    if not executable:
        raise ValidationError(f"{label} is unavailable")
    executable = str(Path(executable).resolve())
    if Path(executable).suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise ValidationError(f"{label} needs a native executable")
    safe_directory = root.resolve().as_posix()
    # A literal '*' directory name can become Git's parent/prefix trust rule.
    # Git cannot express that name as exact trust, so refuse it before launch.
    if "*" in safe_directory:
        raise ValidationError(f"{label} cannot use wildcard repository trust")
    command = [
        executable, "--no-replace-objects",
        # Protected, command-only trust for exactly this resolved repository;
        # never inherit safe.directory or trust a parent/prefix/wildcard.
        "-c", f"safe.directory={safe_directory}",
        "-c", f"core.attributesFile={os.devnull}",
        "-c", "core.fsmonitor=false",
        "-c", "core.preloadIndex=false",
        "-c", "core.untrackedCache=false",
        "-c", "submodule.recurse=false",
        # Stable archive modes, independent of repository or process umask.
        "-c", "tar.umask=0002",
        # Disable a repo-local replacement of the built-in tar archiver.
        "-c", "tar.tar.command=",
        # Keep byte reads independent of checkout line-ending preferences.
        "-c", "core.autocrlf=false",
        "-c", "core.eol=lf",
        "-C", str(root),
    ]
    try:
        # Archive applies .gitattributes conversion, including named filters.
        # Git has no wildcard filter-disable switch: enumerate the configured
        # driver keys without invoking them, then override every smudge/process
        # driver and its required flag in command scope. Bound this discovery
        # just like the object read; malformed/unreadable config fails closed.
        environment = isolated_git_environment()
        filters = subprocess.run(
            [*command, "config", "--null", "--name-only", "--get-regexp",
             r"^filter\..*\.(smudge|process|required)$"],
            cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            env=child_env(environment), timeout=timeout, check=False)
        if filters.returncode not in {0, 1} or len(filters.stdout) > 65536:
            raise ValidationError(f"{label} filter configuration is unavailable")
        for raw_key in filters.stdout.split(b"\0"):
            if not raw_key:
                continue
            key = raw_key.decode("utf-8")
            driver, setting = key.rsplit(".", 1)
            if not driver.startswith("filter.") or setting not in {"smudge", "process", "required"}:
                raise ValidationError(f"{label} filter configuration is malformed")
            # -c splits at the first '='; such a subsection cannot be pinned
            # faithfully and could otherwise leave its executable enabled.
            if "=" in driver:
                raise ValidationError(f"{label} filter configuration cannot be pinned")
            # Pin both execution routes even if only one was configured.
            command[2:2] = ["-c", f"{driver}.smudge=", "-c", f"{driver}.process=",
                            "-c", f"{driver}.required=false"]
        done = subprocess.run(
            [*command, *args], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            env=child_env(environment), timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValidationError(f"{label} is unavailable") from exc
    if done.returncode or len(done.stdout) > maximum:
        raise ValidationError(f"{label} failed or exceeded its bound")
    return done.stdout
