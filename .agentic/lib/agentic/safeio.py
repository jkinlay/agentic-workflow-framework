"""Confined file operations using pinned directories and no-follow reads.

Windows: directory handles deny delete-sharing, preventing ancestor swaps while
held. POSIX: operations use open directory descriptors and O_NOFOLLOW. Existing
hardlinked regular files are rejected. Writes replace a new temporary inode.
"""
from __future__ import annotations
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

from . import ValidationError

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
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    INVALID_HANDLE = wintypes.HANDLE(-1).value

    def win_open(path, directory=False, lock=False):
        access = 0x80 if directory else (0xC0000000 if lock else 0x80000000)
        share = 3 if directory or lock else 1
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = kernel.CreateFileW(str(path), access, share, None, 4 if lock else 3, flags, None)
        if handle == INVALID_HANDLE:
            raise OSError(ctypes.get_last_error(), f"Cannot pin {path}")
        info = FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            kernel.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), f"Cannot inspect {path}")
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            kernel.CloseHandle(handle)
            raise ValidationError(f"Refusing link, reparse point, or wrong file type: {path}")
        return handle


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

    def read(self, relative, maximum=64 * 1024 * 1024):
        parent, handle, name = self.parent(relative)
        if os.name == "nt":
            raw = win_open(parent / name)
            fd = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
        else:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=handle)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
                raise ValidationError("Unsafe file type/link count/size")
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise ValidationError("File exceeds size limit")
            return data

    def write(self, relative, data):
        if not isinstance(data, bytes):
            raise TypeError("write requires bytes")
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
        if self.inspect(relative) is None:
            return
        parent, handle, name = self.parent(relative)
        if os.name == "nt":
            os.unlink(parent / name)
        else:
            os.unlink(name, dir_fd=handle)
            os.fsync(handle)

    def file_list(self, *, exclude_root_git=False, exclude_prefixes=()):
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
