"""Fail-closed Git reads isolated from ambient config and replacement objects."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

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
        # Missing promisor objects must fail locally, never trigger lazy fetch.
        "GIT_NO_LAZY_FETCH": "1",
        # Deny every transport, including repo-selected remote helpers with
        # protocol.<name>.allow=always. This fixed empty allowlist also protects
        # older Git versions that do not implement GIT_NO_LAZY_FETCH.
        "GIT_ALLOW_PROTOCOL": "",
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
        # Retain fail-closed validation of existing filter keys. These overrides
        # alone are NOT an isolation boundary: source configuration can change
        # after discovery. Archive execution below uses private metadata instead.
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
        if args and args[0] == "archive":
            def discover(arguments: list[str]) -> str:
                result = subprocess.run(
                    [*command, "rev-parse", *arguments], cwd=str(root),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, shell=False, env=child_env(environment),
                    timeout=timeout, check=False)
                if result.returncode or len(result.stdout) > 65536:
                    raise ValidationError(f"{label} object discovery failed")
                return result.stdout.decode("utf-8").rstrip("\n")

            # Resolve names once, then read only that immutable object ID. The
            # discovery commands perform no archive/checkout conversion.
            object_id = discover(["--verify", "--end-of-options", args[2]])
            object_format = discover(["--show-object-format"])
            if (object_format not in {"sha1", "sha256"} or not re.fullmatch(
                    r"[0-9a-f]{40}" if object_format == "sha1" else r"[0-9a-f]{64}",
                    object_id)):
                raise ValidationError(f"{label} object identity is invalid")
            objects = Path(discover(["--path-format=absolute", "--git-path", "objects"]))
            if not objects.is_absolute() or not objects.is_dir():
                raise ValidationError(f"{label} object directory is unavailable")
            # Never import source config/includes, refs, worktree config, hooks
            # or info/attributes. Only the object database is shared. In
            # particular, newly introduced filter names have no executable
            # definition here, even if source config changes at exec time.
            with tempfile.TemporaryDirectory(prefix="awf-git-read-") as temporary:
                metadata = Path(temporary)
                (metadata / "objects").mkdir()
                (metadata / "refs").mkdir()
                (metadata / "HEAD").write_text("ref: refs/heads/unused\n", encoding="ascii")
                config = "[core]\n\tbare = true\n\trepositoryformatversion = "
                config += "0\n" if object_format == "sha1" else "1\n[extensions]\n\tobjectformat = sha256\n"
                (metadata / "config").write_text(config, encoding="ascii")
                archive_environment = dict(environment)
                archive_environment["GIT_OBJECT_DIRECTORY"] = str(objects)
                # Replace the source -C with explicit private Git metadata;
                # cwd also stays private, so no source repository is discovered.
                done = subprocess.run(
                    [*command[:-2], f"--git-dir={metadata}",
                     "archive", "--format=tar", object_id, *args[3:]],
                    cwd=str(metadata), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                    env=child_env(archive_environment), timeout=timeout, check=False)
        else:
            done = subprocess.run(
                [*command, *args], cwd=str(root), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                env=child_env(environment), timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValidationError(f"{label} is unavailable") from exc
    if done.returncode or len(done.stdout) > maximum:
        raise ValidationError(f"{label} failed or exceeded its bound")
    return done.stdout
