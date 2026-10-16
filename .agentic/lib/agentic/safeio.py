"""Confined file operations using pinned directories and no-follow reads.

Windows: directory handles deny delete-sharing, preventing ancestor swaps while
held. POSIX: operations use open directory descriptors and O_NOFOLLOW. Existing
hardlinked regular files are rejected. Writes replace a new temporary inode.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import subprocess
import threading
import time
import uuid

from . import ValidationError
from .child_process import (
    ChildProcessPolicyError,
    OperationBudget,
    RunnerPolicy,
    child_env,
    render_diagnostic,
)


_LEGACY_STDOUT_LIMIT = 8 * 1024 * 1024
_LEGACY_STDERR_LIMIT = 8 * 1024 * 1024
_LEGACY_WATCHED_LIMIT = 1024 * 1024
_DEFAULT_STDIN_LIMIT = 1024 * 1024


def run_bounded(argv, *, stdin=None, cwd=None, env=None, timeout=60,
                stdout_limit=_LEGACY_STDOUT_LIMIT,
                stderr_limit=_LEGACY_STDERR_LIMIT,
                watched_files=(), watched_file_limit=_LEGACY_WATCHED_LIMIT,
                stdin_limit=_DEFAULT_STDIN_LIMIT, aggregate_output_limit=None,
                cancel_event=None, stream_sink=None, policy=None,
                operation_budget=None):
    """Run one owned process tree with bounded input, output, and artifacts.

    Existing callers may keep supplying the historical channel caps. New
    operations select an immutable ``RunnerPolicy``. A selected policy is
    closed: caller-selected cap or timeout overrides are refused. The optional
    ``OperationBudget`` is shared and non-resettable across related launches.
    """
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(value, str) for value in argv)):
        raise ValidationError("SUBPROCESS_ARGV: command arguments are invalid")
    if policy is not None:
        if not isinstance(policy, RunnerPolicy):
            raise ValidationError("SUBPROCESS_POLICY: runner policy is invalid")
        if (timeout != 60
                or stdout_limit != _LEGACY_STDOUT_LIMIT
                or stderr_limit != _LEGACY_STDERR_LIMIT
                or watched_file_limit != _LEGACY_WATCHED_LIMIT
                or stdin_limit != _DEFAULT_STDIN_LIMIT
                or aggregate_output_limit is not None):
            raise ValidationError("SUBPROCESS_POLICY: closed policy cannot be overridden")
        timeout = policy.timeout
        stdin_limit = policy.stdin_limit
        stdout_limit = policy.stdout_limit
        stderr_limit = policy.stderr_limit
        aggregate_output_limit = policy.aggregate_output_limit
        watched_file_limit = policy.watched_file_limit
    if aggregate_output_limit is None:
        aggregate_output_limit = stdout_limit + stderr_limit
    if not all(type(value) is int and value >= 0 for value in (
            stdin_limit, stdout_limit, stderr_limit,
            aggregate_output_limit, watched_file_limit)):
        raise ValidationError("SUBPROCESS_CAP: stream limits are invalid")
    if aggregate_output_limit > stdout_limit + stderr_limit:
        raise ValidationError("SUBPROCESS_CAP: aggregate limit exceeds channel limits")
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or timeout <= 0):
        raise ValidationError("SUBPROCESS_TIMEOUT: timeout is invalid")
    if (not isinstance(watched_files, (list, tuple))
            or any(not isinstance(value, (str, os.PathLike))
                   for value in watched_files)):
        raise ValidationError("SUBPROCESS_WATCH: watched files are invalid")
    if operation_budget is not None and not isinstance(operation_budget, OperationBudget):
        raise ValidationError("SUBPROCESS_OPERATION_BUDGET: budget is invalid")
    if cancel_event is not None and not callable(getattr(cancel_event, "is_set", None)):
        raise ValidationError("SUBPROCESS_CANCEL: cancellation event is invalid")
    if stream_sink is not None and not callable(stream_sink):
        raise ValidationError("SUBPROCESS_STREAM_SINK: sink must be callable")
    input_bytes = None
    if stdin is not None:
        input_bytes = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        if not isinstance(input_bytes, bytes):
            raise ValidationError("SUBPROCESS_STDIN: stdin must be bytes or text")
        if len(input_bytes) > stdin_limit:
            raise ValidationError("SUBPROCESS_STDIN_LIMIT: stdin exceeded its cap")
    if cancel_event is not None and cancel_event.is_set():
        raise ValidationError("SUBPROCESS_CANCELLED: operation was cancelled before launch")

    watchers = []
    try:
        for value in watched_files:
            watchers.append(_PinnedWatchedOutput(value, watched_file_limit))
    except BaseException:
        for watcher in watchers:
            watcher.close()
        raise

    if operation_budget is not None:
        try:
            operation_budget.start_operation()
        except ChildProcessPolicyError as exc:
            for watcher in watchers:
                watcher.close()
            raise ValidationError("SUBPROCESS_OPERATION_BUDGET: operation cap exceeded") from exc

    job = _WindowsJob() if os.name == "nt" else None
    popen_arguments = {}
    if os.name == "nt":
        popen_arguments["creationflags"] = job.creationflags
    else:
        popen_arguments["start_new_session"] = True
    try:
        process = None
        process = subprocess.Popen(
            list(argv), cwd=cwd, env=child_env(env), shell=False, close_fds=True,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_arguments,
        )
        if job is not None:
            job.attach_and_resume(process)
    except BaseException:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait()
            except OSError:
                pass
        if job is not None:
            job.close()
        for watcher in watchers:
            watcher.close()
        raise

    captured = {"stdout": bytearray(), "stderr": bytearray()}
    state_lock = threading.Lock()
    sink_lock = threading.Lock()
    issues = []
    aggregate_size = 0
    stop = threading.Event()

    def drain(name, stream, limit):
        nonlocal aggregate_size
        try:
            while True:
                # BufferedReader.read(size) may wait for the entire requested
                # size. read1 keeps streaming latency bounded to one pipe read.
                chunk = stream.read1(65536)
                if not chunk:
                    return
                with state_lock:
                    issue = None
                    if len(captured[name]) + len(chunk) > limit:
                        issue = name
                    elif aggregate_size + len(chunk) > aggregate_output_limit:
                        issue = "aggregate"
                    elif operation_budget is not None:
                        try:
                            operation_budget.consume(len(chunk))
                        except ChildProcessPolicyError:
                            issue = "operation_budget"
                    if issue is None:
                        captured[name].extend(chunk)
                        aggregate_size += len(chunk)
                    elif not issues:
                        issues.append(issue)
                        stop.set()
                if issue is not None:
                    return
                if stream_sink is not None:
                    try:
                        with sink_lock:
                            stream_sink(name, bytes(chunk))
                    except BaseException as exc:
                        with state_lock:
                            if not issues:
                                issues.append("stream_sink")
                                issues.append(exc)
                                stop.set()
                        return
        finally:
            stream.close()

    def write_stdin():
        try:
            if input_bytes:
                process.stdin.write(input_bytes)
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    threads = [
        threading.Thread(target=drain, args=("stdout", process.stdout, stdout_limit), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr, stderr_limit), daemon=True),
    ]
    input_thread = None
    if input_bytes is not None:
        input_thread = threading.Thread(target=write_stdin, daemon=True)
    for thread in threads:
        thread.start()
    if input_thread is not None:
        input_thread.start()

    deadline = time.monotonic() + timeout
    reason = None
    try:
        while process.poll() is None:
            if stop.wait(0.01):
                reason = issues[0]
                break
            if cancel_event is not None and cancel_event.is_set():
                reason = "cancelled"
                break
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            for watcher in watchers:
                try:
                    watcher.check()
                except _WatchedFileLimit:
                    reason = "result_file"
                    break
                except (OSError, ValidationError):
                    reason = "watch"
                    break
            if reason is not None:
                break
        # A completed leader does not authorize detached descendants. Reap the
        # owned group/job before joining inherited pipes.
        _terminate_owned_tree(process, job)
        process.wait()
        for watcher in watchers:
            try:
                watcher.finalize()
            except _WatchedFileLimit:
                if reason is None:
                    reason = "result_file"
            except (OSError, ValidationError):
                if reason is None:
                    reason = "watch"
    finally:
        if process.poll() is None:
            _terminate_owned_tree(process, job)
            process.wait()
        if input_thread is not None:
            input_thread.join(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
        if job is not None:
            job.close()
        for watcher in watchers:
            watcher.close()

    if reason is None and issues:
        reason = issues[0]
    messages = {
        "stdout": "SUBPROCESS_STDOUT_LIMIT: owned child exceeded its stdout cap",
        "stderr": "SUBPROCESS_STDERR_LIMIT: owned child exceeded its stderr cap",
        "aggregate": "SUBPROCESS_AGGREGATE_LIMIT: owned child exceeded its aggregate output cap",
        "operation_budget": "SUBPROCESS_OPERATION_BUDGET: shared operation budget exceeded",
        "stream_sink": "SUBPROCESS_STREAM_SINK: streaming sink refused output",
        "result_file": "SUBPROCESS_RESULT_FILE_LIMIT: watched output exceeded its cap",
        "watch": "SUBPROCESS_WATCH: watched output was unsafe or exceeded its cap",
        "cancelled": "SUBPROCESS_CANCELLED: owned process tree was cancelled",
        "timeout": "SUBPROCESS_TIMEOUT: owned process tree exceeded its timeout",
    }
    if reason is not None:
        raise ValidationError(messages[reason])
    result = subprocess.CompletedProcess(
        list(argv), process.returncode,
        bytes(captured["stdout"]), bytes(captured["stderr"]),
    )
    result.watched_outputs = tuple(watcher.snapshot for watcher in watchers)
    return result


def _terminate_owned_tree(process, job):
    """Terminate only the process tree created by ``run_bounded``."""
    if os.name == "nt":
        if job is not None:
            job.terminate()
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME), ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]
    class FileDispositionInformation(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]
    class FileDispositionInformationEx(ctypes.Structure):
        _fields_ = [("flags", wintypes.DWORD)]
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                   wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    INVALID_HANDLE = wintypes.HANDLE(-1).value
    FILE_DISPOSITION_INFO = 4
    FILE_DISPOSITION_INFO_EX = 21
    FILE_DISPOSITION_FLAG_DELETE = 0x1
    FILE_DISPOSITION_FLAG_POSIX_SEMANTICS = 0x2
    FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE = 0x10
    FILE_ATTRIBUTE_READONLY = 0x1
    _CREATE_SUSPENDED = 0x00000004

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD),
            ("owner_process_id", wintypes.DWORD),
            ("base_priority", wintypes.LONG),
            ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    class JobBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("per_process_user_time_limit", ctypes.c_longlong),
            ("per_job_user_time_limit", ctypes.c_longlong),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class JobIoCounters(ctypes.Structure):
        _fields_ = [
            ("read_operation_count", ctypes.c_ulonglong),
            ("write_operation_count", ctypes.c_ulonglong),
            ("other_operation_count", ctypes.c_ulonglong),
            ("read_transfer_count", ctypes.c_ulonglong),
            ("write_transfer_count", ctypes.c_ulonglong),
            ("other_transfer_count", ctypes.c_ulonglong),
        ]

    class JobExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("basic_limit_information", JobBasicLimitInformation),
            ("io_info", JobIoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        ]

    class _WindowsJob:
        """Kill-on-close Job that owns every descendant of one runner launch."""

        def __init__(self):
            self._handle = kernel.CreateJobObjectW(None, None)
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = JobExtendedLimitInformation()
            limits.basic_limit_information.limit_flags = 0x00002000
            if not kernel.SetInformationJobObject(
                    self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.get_last_error()
                kernel.CloseHandle(self._handle)
                self._handle = None
                raise ctypes.WinError(error)

        @property
        def creationflags(self):
            return _CREATE_SUSPENDED

        def attach_and_resume(self, process):
            """Attach a newly created suspended child, then permit execution."""
            if not kernel.AssignProcessToJobObject(self._handle, process._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)
            if snapshot == INVALID_HANDLE:
                raise ctypes.WinError(ctypes.get_last_error())
            thread_handle = None
            try:
                entry = ThreadEntry32()
                entry.size = ctypes.sizeof(entry)
                present = kernel.Thread32First(snapshot, ctypes.byref(entry))
                while present:
                    if entry.owner_process_id == process.pid:
                        thread_handle = kernel.OpenThread(0x0002, False, entry.thread_id)
                        if not thread_handle:
                            raise ctypes.WinError(ctypes.get_last_error())
                        if kernel.ResumeThread(thread_handle) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
                raise OSError("new child has no resumable primary thread")
            finally:
                if thread_handle:
                    kernel.CloseHandle(thread_handle)
                kernel.CloseHandle(snapshot)

        def terminate(self):
            if self._handle and not kernel.TerminateJobObject(self._handle, 1):
                error = ctypes.get_last_error()
                if error not in {5, 6}:
                    raise ctypes.WinError(error)

        def close(self):
            if self._handle:
                kernel.CloseHandle(self._handle)
                self._handle = None

    def win_open(path, directory=False, lock=False, allow_hardlinks=False,
                 shared_write=False):
        access = 0x80 if directory else (0xC0000000 if lock else 0x80000000)
        share = 3 if directory or lock or shared_write else 1
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = kernel.CreateFileW(str(path), access, share, None, 4 if lock else 3, flags, None)
        if handle == INVALID_HANDLE:
            raise OSError(ctypes.get_last_error(), f"Cannot pin {path}")
        info = FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            kernel.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), f"Cannot inspect {path}")
        if (info.attributes & 0x400 or bool(info.attributes & 0x10) != directory
                or (not directory and not allow_hardlinks and info.links != 1)):
            kernel.CloseHandle(handle)
            raise ValidationError(f"Refusing link, reparse point, or wrong file type: {path}")
        return handle

    def _win_set_file_information(handle, information_class, information):
        if not kernel.SetFileInformationByHandle(
                handle, information_class, ctypes.byref(information), ctypes.sizeof(information)):
            raise ctypes.WinError(ctypes.get_last_error())

    def win_unlink(path):
        """Validate and atomically mark one leaf for deletion through one handle."""
        delete = 0x00010000
        read_attributes = 0x80
        # Omitting FILE_SHARE_DELETE prevents a concurrent rename or replacement
        # from changing which object is mutated while this handle is held.
        handle = kernel.CreateFileW(str(path), delete | read_attributes,
                                    0x1 | 0x2, None, 3, 0x00200000, None)
        if handle == INVALID_HANDLE:
            error = ctypes.get_last_error()
            if error in {2, 3}:
                return False
            raise ctypes.WinError(error)
        try:
            info = FileInformation()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & 0x400 or info.attributes & 0x10 or info.links != 1:
                raise ValidationError(f"Refusing link, reparse point, or wrong file type: {path}")
            if info.attributes & FILE_ATTRIBUTE_READONLY:
                # FileDispositionInfoEx applies the read-only override and delete
                # disposition in one kernel operation. If the host does not support
                # it the call fails without first persisting a writable mode.
                flags = (FILE_DISPOSITION_FLAG_DELETE |
                         FILE_DISPOSITION_FLAG_POSIX_SEMANTICS |
                         FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE)
                _win_set_file_information(
                    handle, FILE_DISPOSITION_INFO_EX, FileDispositionInformationEx(flags))
            else:
                _win_set_file_information(
                    handle, FILE_DISPOSITION_INFO, FileDispositionInformation(True))
            return True
        finally:
            kernel.CloseHandle(handle)

else:
    class _WindowsJob:
        def __init__(self):  # pragma: no cover - platform guard
            raise RuntimeError("Windows Job objects are unavailable")


@dataclass(frozen=True)
class FileSnapshot:
    """Bytes and digest captured from one descriptor-pinned regular file."""

    data: bytes
    size: int
    sha256: str
    identity: tuple


class _WatchedFileLimit(ValidationError):
    pass


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


class _PinnedWatchedOutput:
    """Pin a watched leaf and refuse replacement, links, or special files."""

    def __init__(self, path, maximum):
        self.path = Path(os.path.abspath(path))
        self.maximum = maximum
        self._tree = Tree(self.path.parent)
        self._fd = None
        self._identity = None
        self.snapshot = None
        try:
            self._pin_if_present()
        except BaseException:
            self.close()
            raise

    def _pin_if_present(self):
        if self._fd is not None:
            return
        parent, handle, name = self._tree.parent(self.path.name)
        try:
            if os.name == "nt":
                raw = win_open(parent / name, shared_write=True)
                self._fd = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
            else:
                self._fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=handle,
                )
        except FileNotFoundError:
            return
        except ValidationError as exc:
            raise ValidationError(
                "SUBPROCESS_WATCH: watched output is linked or non-regular") from exc
        info = os.fstat(self._fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            self.close()
            raise ValidationError("SUBPROCESS_WATCH: watched output is linked or non-regular")
        self._identity = _file_identity(info)

    def check(self):
        self._pin_if_present()
        if self._fd is None:
            return
        info = os.fstat(self._fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValidationError("SUBPROCESS_WATCH: watched output is unsafe or oversized")
        if info.st_size > self.maximum:
            raise _WatchedFileLimit("watched output exceeded its cap")
        parent, handle, name = self._tree.parent(self.path.name)
        current = (os.stat(parent / name, follow_symlinks=False) if os.name == "nt"
                   else os.stat(name, dir_fd=handle, follow_symlinks=False))
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValidationError("SUBPROCESS_WATCH: watched output was replaced")

    def finalize(self):
        self.check()
        if self._fd is None:
            self.snapshot = None
            return
        before = os.fstat(self._fd)
        os.lseek(self._fd, 0, os.SEEK_SET)
        data = bytearray()
        while len(data) <= self.maximum:
            chunk = os.read(self._fd, min(65536, self.maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(self._fd)
        if (len(data) > self.maximum or _file_identity(before) != _file_identity(after)
                or after.st_size != len(data)):
            if len(data) > self.maximum or after.st_size > self.maximum:
                raise _WatchedFileLimit("watched output exceeded its cap")
            raise ValidationError("SUBPROCESS_WATCH: watched output changed or exceeded its cap")
        raw = bytes(data)
        self.snapshot = FileSnapshot(
            raw, len(raw), hashlib.sha256(raw).hexdigest(), _file_identity(after))

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._tree is not None:
            self._tree.close()
            self._tree = None


def relative_parts(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value:
        raise ValidationError("Unsafe relative file path")
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or any(p in {"", ".", ".."} for p in parts):
        raise ValidationError("Unsafe relative path component")
    # Avoid Windows aliasing even when building a package on POSIX.
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{n}" for prefix in ["COM", "LPT"] for n in range(1, 10)}
    if any(p.endswith((".", " ")) or p.split(".")[0].upper() in reserved or any(c in '<>"|?*' or ord(c) < 32 for c in p) for p in parts):
        raise ValidationError("Unsupported or ambiguous filename")
    return parts


class Tree:
    def __init__(self, root, create=False):
        self.root = Path(os.path.abspath(root))
        self._dirs = {}
        try:
            self._pin_absolute(self.root, create)
        except BaseException:
            self.close()
            raise

    def _pin_absolute(self, path, create=False):
        if path in self._dirs:
            return self._dirs[path]
        parent_handle = None
        if path.parent != path:
            parent_handle = self._pin_absolute(path.parent, create)
        if os.name == "nt":
            if create and not path.exists():
                path.mkdir()
            handle = win_open(path, directory=True)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            if parent_handle is None:
                handle = os.open(str(path), flags)
            else:
                if create:
                    try:
                        os.mkdir(path.name, dir_fd=parent_handle)
                    except FileExistsError:
                        pass
                handle = os.open(path.name, flags, dir_fd=parent_handle)
        self._dirs[path] = handle
        return handle

    def parent(self, relative, create=False):
        parts = relative_parts(relative)
        parent_path = self.root.joinpath(*parts[:-1])
        handle = self._pin_absolute(parent_path, create)
        return parent_path, handle, parts[-1]

    def inspect(self, relative):
        try:
            parent, handle, name = self.parent(relative)
            info = os.lstat(parent / name) if os.name == "nt" else os.stat(name, dir_fd=handle, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValidationError(f"Refusing non-regular or linked file: {relative}")
        return info

    def read(self, relative, maximum=64 * 1024 * 1024, *, allow_hardlinks=False):
        return self._snapshot(relative, maximum, allow_hardlinks=allow_hardlinks).data

    def _snapshot(self, relative, maximum=64 * 1024 * 1024, *, allow_hardlinks=False):
        _validate_maximum(maximum, "FILE_SIZE")
        parent, handle, name = self.parent(relative)
        if os.name == "nt":
            raw = win_open(parent / name, allow_hardlinks=allow_hardlinks)
            fd = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
        else:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=handle)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode)
                    or (not allow_hardlinks and before.st_nlink != 1)
                    or before.st_size > maximum):
                raise ValidationError("Unsafe file type/link count/size")
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise ValidationError("File exceeds size limit")
            after = os.fstat(stream.fileno())
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                      value.st_mtime_ns, value.st_nlink)
            if identity(before) != identity(after) or after.st_size != len(data):
                raise ValidationError("File changed during bounded read")
            return FileSnapshot(
                data, len(data), hashlib.sha256(data).hexdigest(), identity(after))

    def write(self, relative, data):
        if not isinstance(data, bytes):
            raise TypeError("write requires bytes")
        # Replacing identical bytes would create a mode-only intermediate state:
        # the existing mode becomes the private temporary-file mode even though
        # the content did not change. Keep an already-durable identical leaf so
        # an interrupted journaled operation has no ambiguous mode transition.
        if self.inspect(relative) is not None and self.read(relative) == data:
            return
        parent, handle, name = self.parent(relative, create=True)
        self.inspect(relative)
        temp = f".awf-{uuid.uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(parent / temp, flags, 0o600) if os.name == "nt" else os.open(temp, flags, 0o600, dir_fd=handle)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # os.replace replaces a leaf entry rather than following it; pinned
            # directory parents prevent path redirection on Windows.
            if os.name == "nt":
                os.replace(parent / temp, parent / name)
            else:
                os.replace(temp, name, src_dir_fd=handle, dst_dir_fd=handle)
                os.fsync(handle)
        finally:
            try:
                if os.name == "nt":
                    os.unlink(parent / temp)
                else:
                    os.unlink(temp, dir_fd=handle)
            except FileNotFoundError:
                pass

    def unlink(self, relative):
        try:
            parent, handle, name = self.parent(relative)
        except FileNotFoundError:
            return
        if os.name == "nt":
            # Upgrade archives are made read-only before the managed-after
            # journal update. Validate and delete through one handle which denies
            # replacement and atomically ignores the read-only attribute.
            win_unlink(parent / name)
        else:
            if self.inspect(relative) is None:
                return
            os.unlink(name, dir_fd=handle)
            os.fsync(handle)

    def file_list(self, *, exclude_root_git=False, reject_root_git=False, exclude_prefixes=()):
        if exclude_root_git and reject_root_git:
            raise ValueError("Root Git metadata cannot be both excluded and rejected")
        result = []
        excluded_roots = {prefix.rstrip('/') for prefix in exclude_prefixes}
        def walk(directory):
            handle = self._pin_absolute(directory)
            entries = list(os.scandir(directory if os.name == "nt" else handle))
            for entry in entries:
                relative = (directory / entry.name).relative_to(self.root).as_posix()
                relative_parts(relative)
                # Windows scandir caches FindFirstFile metadata, whose link count
                # can be zero. Request full metadata before enforcing one link.
                info = os.lstat(directory / entry.name) if os.name == "nt" else entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValidationError(f"Linked source entry: {relative}")
                # A source checkout may have root Git metadata. Never traverse
                # it or include it in release bytes; keep rejecting links,
                # special entries, nested Git directories and unrelated extras.
                if reject_root_git and relative == '.git':
                    raise ValidationError('Root Git metadata is not permitted')
                if exclude_root_git and relative == '.git':
                    if not (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
                        raise ValidationError('Unsupported root Git metadata')
                    continue
                if relative in excluded_roots and stat.S_ISDIR(info.st_mode):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    walk(directory / entry.name)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    result.append(relative)
                else:
                    raise ValidationError(f"Unsupported source file: {relative}")
        walk(self.root)
        return sorted(result)

    def close(self):
        for handle in reversed(list(self._dirs.values())):
            if os.name == "nt":
                kernel.CloseHandle(handle)
            else:
                os.close(handle)
        self._dirs.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _validate_maximum(maximum, code):
    if type(maximum) is not int or maximum < 0:
        raise ValidationError(f"{code}: maximum must be a non-negative integer")


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"JSON_DUPLICATE: duplicate object key {key!r}")
        result[key] = value
    return result


def _json_constant(value):
    raise ValidationError(f"JSON_CONSTANT: non-finite constant {value!r} is forbidden")


def _json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValidationError("JSON_CONSTANT: non-finite floating point value is forbidden")
    return parsed


def _validate_json_value(value, code="JSON_VALUE", active=None):
    value_type = type(value)
    if value_type is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValidationError(f"{code}: unpaired Unicode surrogate is forbidden")
        return
    if value is None or value_type in {bool, int}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ValidationError(f"{code}: non-finite floating point value is forbidden")
        return
    if value_type not in {list, dict}:
        raise ValidationError(f"{code}: unsupported JSON value type")
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        raise ValidationError(f"{code}: circular JSON value is forbidden")
    active.add(identity)
    try:
        if value_type is list:
            for item in value:
                _validate_json_value(item, code, active)
        else:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValidationError(f"{code}: JSON object keys must be text")
                _validate_json_value(key, code, active)
                _validate_json_value(item, code, active)
    finally:
        active.remove(identity)


def loads_bounded_json(raw, *, maximum=1024 * 1024,
                       expected_type=(dict, list)):
    """Decode bounded UTF-8 JSON with duplicate and non-finite values refused."""
    _validate_maximum(maximum, "JSON_SIZE")
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        raise ValidationError("JSON_VALUE: JSON input must be bytes or text")
    if len(raw) > maximum:
        raise ValidationError("JSON_SIZE: JSON input exceeds its byte cap")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("JSON_ENCODING: JSON input is not strict UTF-8") from exc
    if text.startswith("\ufeff"):
        raise ValidationError("JSON_ENCODING: a UTF-8 BOM is not permitted")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_json_pairs,
            parse_constant=_json_constant,
            parse_float=_json_float,
        )
    except ValidationError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValidationError("JSON_SYNTAX: JSON input is invalid") from exc
    _validate_json_value(value, "JSON_VALUE")
    if expected_type is not None:
        if (not isinstance(expected_type, type)
                and not (isinstance(expected_type, tuple)
                         and expected_type
                         and all(isinstance(item, type) for item in expected_type))):
            raise ValidationError("JSON_TYPE: expected type declaration is invalid")
        accepted_types = ((expected_type,) if isinstance(expected_type, type)
                          else expected_type)
        if type(value) not in accepted_types:
            raise ValidationError("JSON_TYPE: JSON root type is not permitted")
    return value


def read_bounded_json(source, relative=None, *, maximum=1024 * 1024,
                      expected_type=(dict, list)):
    """Read strict JSON through a pinned ``Tree`` or a newly pinned path."""
    _validate_maximum(maximum, "JSON_SIZE")
    if isinstance(source, Tree):
        if not isinstance(relative, str):
            raise ValidationError("JSON_PATH: a confined relative path is required")
        raw = source.read(relative, maximum)
    else:
        if relative is not None:
            raise ValidationError("JSON_PATH: relative path is invalid for a path source")
        path = Path(os.path.abspath(source))
        with Tree(path.parent) as tree:
            raw = tree.read(path.name, maximum)
    return loads_bounded_json(raw, maximum=maximum, expected_type=expected_type)


def snapshot_file(source, relative=None, *, maximum=64 * 1024 * 1024):
    """Return bytes and SHA-256 from one stable, descriptor-pinned file read."""
    _validate_maximum(maximum, "FILE_SIZE")
    if isinstance(source, Tree):
        if not isinstance(relative, str):
            raise ValidationError("FILE_PATH: a confined relative path is required")
        return source._snapshot(relative, maximum)
    else:
        if relative is not None:
            raise ValidationError("FILE_PATH: relative path is invalid for a path source")
        path = Path(os.path.abspath(source))
        with Tree(path.parent) as tree:
            return tree._snapshot(path.name, maximum)


def write_json(tree, relative, value, *, maximum=1024 * 1024):
    """Atomically replace one confined leaf with canonical strict JSON."""
    if not isinstance(tree, Tree):
        raise ValidationError("JSON_PATH: writes require a pinned Tree")
    _validate_maximum(maximum, "JSON_SIZE")
    try:
        _validate_json_value(value, "JSON_VALUE")
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        chunks = []
        size = 0
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size + 1 > maximum:
                raise ValidationError("JSON_SIZE: encoded JSON exceeds its byte cap")
            chunks.append(encoded)
        raw = b"".join(chunks) + b"\n"
    except ValidationError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValidationError("JSON_VALUE: value is not strict JSON") from exc
    tree.write(relative, raw)


def write_log(tree, relative, value, *, maximum=8 * 1024 * 1024,
              credentials=(), paths=()):
    """Redact and atomically replace one bounded diagnostic log leaf."""
    if not isinstance(tree, Tree):
        raise ValidationError("LOG_PATH: writes require a pinned Tree")
    _validate_maximum(maximum, "LOG_SIZE")
    if isinstance(value, bytes):
        if len(value) > maximum:
            raise ValidationError("LOG_SIZE: raw log exceeds its byte cap")
    else:
        try:
            if len(str(value).encode("utf-8", errors="replace")) > maximum:
                raise ValidationError("LOG_SIZE: raw log exceeds its byte cap")
        except (TypeError, ValueError) as exc:
            raise ValidationError("LOG_ENCODING: log input is invalid") from exc
    rendered = render_diagnostic(value, credentials=credentials, paths=paths)
    try:
        raw = (rendered + ("" if rendered.endswith("\n") else "\n")).encode("utf-8")
    except UnicodeError as exc:
        raise ValidationError("LOG_ENCODING: log is not encodable as UTF-8") from exc
    if len(raw) > maximum:
        raise ValidationError("LOG_SIZE: rendered log exceeds its byte cap")
    tree.write(relative, raw)


# Descriptive compatibility aliases for callers adopting the primitives in
# later slices. They share the implementations above rather than widening the
# accepted input grammar.
bounded_json_loads = loads_bounded_json
read_strict_json = read_bounded_json
atomic_write_json = write_json
atomic_write_log = write_log
