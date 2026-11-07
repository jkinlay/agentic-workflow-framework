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
    # Apply project exclusions only to inherited values. Mandatory isolation
    # controls below must survive even when policy names those variables.
    environment = child_env(environment)
    environment.update({
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
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
    return child_env(environment, strip_extra=[])


def _native_require(condition, reason):
    if not condition:
        raise ValidationError(reason)


def _sandbox_group_rights(path):
    """Read sandbox group/capability grants without using the current token.

    An absent Codex group contributes no rights. Capability SIDs named by the
    DACL are also sandbox principals, including on hosts without that group.
    """
    import ctypes
    from ctypes import wintypes
    class TRUSTEE(ctypes.Structure):
        _fields_ = [('multiple', ctypes.c_void_p), ('operation', ctypes.c_int),
                    ('form', ctypes.c_int), ('type', ctypes.c_int), ('name', wintypes.LPWSTR)]
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p))
    api.GetEffectiveRightsFromAclW.argtypes = (ctypes.c_void_p, ctypes.POINTER(TRUSTEE),
                                             ctypes.POINTER(wintypes.DWORD))
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    api.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    acl, descriptor, rights = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.DWORD()
    _native_require(api.GetNamedSecurityInfoW(str(path), 1, 4, None, None, ctypes.byref(acl), None,
            ctypes.byref(descriptor)) == 0, 'GIT_UNAVAILABLE: cannot inspect Git ACL; restore read access to the native installation ACL')
    try:
        text = ctypes.c_void_p()
        _native_require(api.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 4, ctypes.byref(text), None),
            'GIT_UNAVAILABLE: cannot inspect sandbox capability ACL')
        try:
            capabilities = set(re.findall(r'S-1-15-3-(?:\d+-)*\d+', ctypes.wstring_at(text)))
        finally:
            kernel.LocalFree(text)
        value = 0
        for principal in ('CodexSandboxUsers', *sorted(capabilities)):
            sid = ctypes.c_void_p()
            try:
                if principal in capabilities:
                    _native_require(api.ConvertStringSidToSidW(principal, ctypes.byref(sid)),
                            'GIT_UNAVAILABLE: cannot inspect sandbox capability SID')
                trustee = TRUSTEE(None, 0, 0 if sid.value else 1, 2,
                                  ctypes.cast(sid, wintypes.LPWSTR) if sid.value else principal)
                rights.value = 0
                error = api.GetEffectiveRightsFromAclW(acl, ctypes.byref(trustee), ctypes.byref(rights))
                _native_require(error in (0, 1332), 'GIT_UNAVAILABLE: cannot inspect sandbox principal rights; repair the native installation ACL')
                value |= rights.value if error == 0 else 0
            finally:
                if sid.value:
                    kernel.LocalFree(sid)
        if value & 0x10000000:  # GENERIC_ALL
            value |= 0x1f01ff
        if value & 0x40000000:  # GENERIC_WRITE
            value |= 0x120116
        return value
    finally:
        kernel.LocalFree(descriptor)

def _worker_writable(path):
    """Check sandbox principals only; full-rights owner processes are out of scope.

    No sandbox principal on this host means no disqualifying grant. POSIX has
    no configured worker principal here. Never probe the current token's rights.
    """
    if os.name != 'nt':
        return False
    rights = {}
    def allowed(member, mask):
        if member not in rights:
            rights[member] = _sandbox_group_rights(member)
        return bool(rights[member] & mask)
    if (allowed(path, 2 | 4 | 0x40000 | 0x80000)
            or allowed(path.parent, 0x40000 | 0x80000)
            or allowed(path.parent, 2) and (allowed(path, 0x10000) or allowed(path.parent, 0x40))):
        return True
    for directory in path.parents:
        # ACL/ownership control can grant replacement rights on descendants.
        if allowed(directory, 0x40000 | 0x80000):
            return True
        if directory.parent == directory:
            continue
        # Renaming/removing a directory can use DELETE on it OR DELETE_CHILD
        # on its parent; installing a replacement also needs ADD_SUBDIRECTORY.
        if (allowed(directory, 0x10000) or allowed(directory.parent, 0x40)) and allowed(directory.parent, 4):
            return True
    return False

def _native_executable(path):
    """Admit executable binaries only, never batch files, shebangs or scripts."""
    if os.name == 'nt' and path.suffix.lower() != '.exe':
        return False
    with path.open('rb') as source:
        magic = source.read(4)
    return (magic[:2] == b'MZ' if os.name == 'nt' else magic == b'\x7fELF' or
            magic in (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
                      b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'))


def native_git_executable(root: Path) -> str:
    """Resolve Git once; refuse scripts, relative and repository-local candidates."""
    candidate = shutil.which("git")
    if not candidate:
        raise ValidationError("Git native executable is unavailable")
    path = Path(candidate)
    if not path.is_absolute():
        raise ValidationError("Git needs an absolute native executable outside worker write authority")
    path = path.resolve(strict=True)
    if path.suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise ValidationError("Git needs a native executable")
    if path == root.resolve() or root.resolve() in path.parents:
        raise ValidationError("Repository-local Git is inside worker write authority")
    if not path.is_file() or not _native_executable(path):
        raise ValidationError("Git needs a native executable binary")
    if _worker_writable(path):
        raise ValidationError("Git native installation is writable or replaceable by sandbox principals")
    return str(path)


def run_isolated_git(root: Path, args: list[str], *, maximum: int, timeout: int,
                     label: str, missing_ok: bool = False) -> bytes | None:
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
    executable = native_git_executable(root)
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
        # after discovery. Archive/status execution uses private metadata instead.
        environment = isolated_git_environment()
        filters = subprocess.run(
            [*command, "config", "--null", "--name-only", "--get-regexp",
             r"^filter\..*\.(smudge|process|required)$"],
            cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            env=child_env(environment, strip_extra=[]), timeout=timeout, check=False)
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
        if args and args[0] in {"archive", "status"}:
            def discover(arguments: list[str]) -> str:
                result = subprocess.run(
                    [*command, "rev-parse", *arguments], cwd=str(root),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, shell=False, env=child_env(environment, strip_extra=[]),
                    timeout=timeout, check=False)
                if result.returncode or len(result.stdout) > 65536:
                    raise ValidationError(f"{label} object discovery failed")
                return result.stdout.decode("utf-8").rstrip("\n")

            # Resolve names once, then read only that immutable object ID. The
            # discovery commands perform no archive/checkout conversion.
            status = args[0] == "status"
            filemode = "true"  # Git's default when core.filemode is absent.
            if status:
                # Preserve the checkout's filesystem-capability policy without
                # importing any other source configuration into private status.
                mode = subprocess.run(
                    [*command, "config", "--type=bool", "--get", "core.filemode"],
                    cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, shell=False,
                    env=child_env(environment, strip_extra=[]), timeout=timeout, check=False)
                if mode.returncode == 0 and mode.stdout in {b"true\n", b"false\n"}:
                    filemode = mode.stdout.decode("ascii").strip()
                elif mode.returncode != 1 or mode.stdout:
                    raise ValidationError(f"{label} filemode policy is unavailable")
            object_id = discover(["--verify", "--end-of-options",
                                  "HEAD^{commit}" if status else args[2]])
            object_format = discover(["--show-object-format"])
            if (object_format not in {"sha1", "sha256"} or not re.fullmatch(
                    r"[0-9a-f]{40}" if object_format == "sha1" else r"[0-9a-f]{64}",
                    object_id)):
                raise ValidationError(f"{label} object identity is invalid")
            objects = Path(discover(["--path-format=absolute", "--git-path", "objects"]))
            if not objects.is_absolute() or not objects.is_dir():
                raise ValidationError(f"{label} object directory is unavailable")
            # Never import source config/includes, refs, worktree config, hooks
            # or info/attributes. Status also copies the real index and uses
            # the independently observed HEAD, never a supplied candidate. In
            # particular, newly introduced filter names have no executable
            # definition here, even if source config changes at exec time.
            with tempfile.TemporaryDirectory(prefix="awf-git-read-") as temporary:
                metadata = Path(temporary)
                (metadata / "objects").mkdir()
                (metadata / "refs").mkdir()
                (metadata / "HEAD").write_text(
                    object_id + "\n" if status else "ref: refs/heads/unused\n", encoding="ascii")
                config = "[core]\n\tbare = " + ("false" if status else "true")
                if status:
                    config += "\n\tfilemode = " + filemode
                config += "\n\trepositoryformatversion = "
                config += "0\n" if object_format == "sha1" else "1\n[extensions]\n\tobjectformat = sha256\n"
                (metadata / "config").write_text(config, encoding="ascii")
                archive_environment = dict(environment)
                archive_environment["GIT_OBJECT_DIRECTORY"] = str(objects)
                if status:
                    index = Path(discover(["--path-format=absolute", "--git-path", "index"]))
                    if not index.is_absolute():
                        raise ValidationError(f"{label} index path is invalid")
                    # A split index refers to sharedindex.<hash> beside the
                    # index. Copy that dependency too; never use the live index
                    # or allow Git to refresh source metadata during inspection.
                    shared = discover(["--path-format=absolute", "--shared-index-path"])
                    if shared:
                        shared_index = Path(shared)
                        if (not shared_index.is_absolute() or not re.fullmatch(
                                r"sharedindex\.[0-9a-f]{40}|sharedindex\.[0-9a-f]{64}",
                                shared_index.name)):
                            raise ValidationError(f"{label} shared index path is invalid")
                        shutil.copyfile(shared_index, metadata / shared_index.name)
                    if index.exists():
                        shutil.copyfile(index, metadata / "index")
                    # Preserve repository-local ignore data without importing
                    # executable configuration or source info/attributes.
                    exclude = Path(discover(["--path-format=absolute", "--git-path", "info/exclude"]))
                    if not exclude.is_absolute():
                        raise ValidationError(f"{label} exclude path is invalid")
                    if exclude.exists():
                        (metadata / "info").mkdir()
                        shutil.copyfile(exclude, metadata / "info/exclude")
                    operation = [f"--work-tree={root.resolve()}",
                                 "-c", f"core.hooksPath={os.devnull}",
                                 "-c", "core.fsmonitor=false",
                                 *args, "--ignore-submodules=all"]
                else:
                    operation = ["archive", "--format=tar", object_id, *args[3:]]
                # Replace the source -C with explicit private Git metadata;
                # cwd also stays private, so no source repository is discovered.
                done = subprocess.run(
                    [*command[:-2], f"--git-dir={metadata}",
                     *operation],
                    cwd=str(metadata), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                    env=child_env(archive_environment, strip_extra=[]), timeout=timeout, check=False)
        else:
            done = subprocess.run(
                [*command, *args], cwd=str(root), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                env=child_env(environment, strip_extra=[]), timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValidationError(f"{label} is unavailable") from exc
    if missing_ok and done.returncode == 1 and not done.stdout:
        return None
    if done.returncode or len(done.stdout) > maximum:
        raise ValidationError(f"{label} failed or exceeded its bound")
    return done.stdout
