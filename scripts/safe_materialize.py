"""No-follow, handle-relative creation for release tree projections."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat


class MaterializeError(OSError):
    pass


# Tests replace this callback to introduce an alias at the exact creation
# boundary.  Production leaves it as None.
RACE_HOOK = None


def _hook(kind, destination, relative):
    if RACE_HOOK is not None:
        RACE_HOOK(kind, Path(destination), relative)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise MaterializeError("release file write made no progress")
        view = view[written:]


class _PosixWriter:
    def __init__(self, destination):
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_TMPFILE"):
            raise MaterializeError("this host lacks atomic no-follow release materialization primitives")
        self.destination = Path(destination)
        os.mkdir(self.destination, 0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.directories = {"": os.open(self.destination, flags)}
        self.identities = {"": os.fstat(self.directories[""])}
        self.files = []

    def close(self):
        for fd in reversed(list(self.directories.values())):
            os.close(fd)
        self.directories.clear()

    def _assert_attached(self, relative):
        root = os.stat(self.destination, follow_symlinks=False)
        expected = self.identities[""]
        if not stat.S_ISDIR(root.st_mode) or (root.st_dev, root.st_ino) != (expected.st_dev, expected.st_ino):
            raise MaterializeError("release root was replaced during materialization")
        current = ""
        for part in relative.split("/") if relative else ():
            parent = current
            current = part if not current else current + "/" + part
            observed = os.stat(part, dir_fd=self.directories[parent], follow_symlinks=False)
            expected = self.identities[current]
            if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
                raise MaterializeError("release directory prefix was replaced during materialization: " + current)

    def directory(self, relative):
        if relative in self.directories:
            self._assert_attached(relative)
            return self.directories[relative]
        parent, _, name = relative.rpartition("/")
        parent_fd = self.directory(parent)
        _hook("directory", self.destination, relative)
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(name, flags, dir_fd=parent_fd)
        opened = os.fstat(fd)
        if (not stat.S_ISDIR(opened.st_mode) or
                (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            os.close(fd)
            raise MaterializeError("release directory changed while it was pinned: " + relative)
        self.directories[relative] = fd
        self.identities[relative] = opened
        self._assert_attached(relative)
        return fd

    def file(self, relative, data, mode):
        parent, _, name = relative.rpartition("/")
        parent_fd = self.directory(parent)
        self._assert_attached(parent)
        flags = os.O_RDWR | os.O_TMPFILE
        fd = os.open(".", flags, mode, dir_fd=parent_fd)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 0:
                raise MaterializeError("anonymous release file is not a new regular inode: " + relative)
            _write_all(fd, data)
            os.fchmod(fd, mode)
            os.fsync(fd)
            _hook("file", self.destination, relative)
            self._assert_attached(parent)
            libc = ctypes.CDLL(None, use_errno=True)
            linkat = libc.linkat
            linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            linkat.restype = ctypes.c_int
            if linkat(fd, b"", parent_fd, os.fsencode(name), 0x1000) != 0:  # AT_EMPTY_PATH
                first_error = ctypes.get_errno()
                proc_path = f"/proc/self/fd/{fd}".encode("ascii")
                if (first_error not in {1, 13} or
                        linkat(-100, proc_path, parent_fd, os.fsencode(name), 0x400) != 0):  # AT_FDCWD, AT_SYMLINK_FOLLOW
                    error = ctypes.get_errno() if first_error in {1, 13} else first_error
                    raise OSError(error, os.strerror(error), relative)
            linked = os.fstat(fd)
            if linked.st_nlink != 1:
                raise MaterializeError("release file acquired an unexpected hardlink: " + relative)
            self.files.append(relative)
        finally:
            os.close(fd)


if os.name == "nt":
    from ctypes import wintypes

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", wintypes.LPWSTR)]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(_UNICODE_STRING)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", wintypes.LPVOID), ("SecurityQualityOfService", wintypes.LPVOID)]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
                    ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
                    ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                    ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                    ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _FILE_READ_DATA = 0x0001
    _FILE_WRITE_DATA = 0x0002
    _FILE_APPEND_DATA = 0x0004
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_WRITE_ATTRIBUTES = 0x0100
    _FILE_ADD_FILE = 0x0002
    _FILE_ADD_SUBDIRECTORY = 0x0004
    _FILE_TRAVERSE = 0x0020
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _SHARE_ALL = 0x7
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_DIRECTORY_FILE = 0x1
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x20
    _FILE_NON_DIRECTORY_FILE = 0x40
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x40
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400

    _ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES), ctypes.POINTER(_IO_STATUS_BLOCK), ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.ULONG]
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel32.FlushFileBuffers.restype = wintypes.BOOL
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG


    def _win_info(handle):
        value = _BY_HANDLE_FILE_INFORMATION()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value


    def _win_create(parent, name, *, directory, disposition, exclusive, writable=True):
        buffer = ctypes.create_unicode_buffer(name)
        string = _UNICODE_STRING(len(name.encode("utf-16-le")), len(name.encode("utf-16-le")) + 2,
                                 ctypes.cast(buffer, wintypes.LPWSTR))
        attributes = _OBJECT_ATTRIBUTES(ctypes.sizeof(_OBJECT_ATTRIBUTES), parent,
            ctypes.pointer(string), _OBJ_CASE_INSENSITIVE, None, None)
        status_block = _IO_STATUS_BLOCK()
        handle = wintypes.HANDLE()
        access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
        if not directory:
            access |= _FILE_READ_DATA | _FILE_WRITE_DATA | _FILE_APPEND_DATA | _FILE_WRITE_ATTRIBUTES
        else:
            access |= _FILE_TRAVERSE
            if writable:
                access |= _FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY
        options = _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
        options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        status = _ntdll.NtCreateFile(ctypes.byref(handle), access, ctypes.byref(attributes),
            ctypes.byref(status_block), None, 0, 0 if exclusive else _SHARE_ALL,
            disposition, options, None, 0)
        if status < 0:
            error = _ntdll.RtlNtStatusToDosError(status)
            raise ctypes.WinError(error)
        info = _win_info(handle)
        if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            _kernel32.CloseHandle(handle)
            raise MaterializeError("release path is a reparse point: " + name)
        return handle


    def _win_open_anchor(anchor):
        handle = _kernel32.CreateFileW(str(anchor), _FILE_READ_ATTRIBUTES | _FILE_TRAVERSE | _SYNCHRONIZE,
            _SHARE_ALL, None, 3, 0x02000000 | 0x00200000, None)  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        if handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        if _win_info(handle).dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            _kernel32.CloseHandle(handle)
            raise MaterializeError("release destination anchor is a reparse point")
        return handle


    class _WindowsWriter:
        def __init__(self, destination):
            self.destination = Path(destination).absolute()
            if self.destination.exists() or self.destination.is_symlink():
                raise FileExistsError(str(self.destination))
            parent = self.destination.parent
            anchor = Path(self.destination.anchor)
            current = _win_open_anchor(anchor)
            try:
                relative_parts = parent.parts[1:]
                for index, part in enumerate(relative_parts):
                    following = _win_create(current, part, directory=True,
                                            disposition=_FILE_OPEN, exclusive=False,
                                            writable=index == len(relative_parts) - 1)
                    _kernel32.CloseHandle(current)
                    current = following
                self.root = _win_create(current, self.destination.name, directory=True,
                                        disposition=_FILE_CREATE, exclusive=True)
            finally:
                _kernel32.CloseHandle(current)
            self.directories = {"": self.root}
            self.files = []

        def close(self):
            for handle in reversed(list(self.files)):
                _kernel32.CloseHandle(handle)
            self.files.clear()
            for handle in reversed(list(self.directories.values())):
                _kernel32.CloseHandle(handle)
            self.directories.clear()

        def directory(self, relative):
            if relative in self.directories:
                return self.directories[relative]
            parent, _, name = relative.rpartition("/")
            parent_handle = self.directory(parent)
            _hook("directory", self.destination, relative)
            handle = _win_create(parent_handle, name, directory=True,
                                 disposition=_FILE_CREATE, exclusive=True)
            self.directories[relative] = handle
            return handle

        def file(self, relative, data, mode):
            del mode  # Windows has no executable bit; archive metadata uses the Git mode map.
            parent, _, name = relative.rpartition("/")
            parent_handle = self.directory(parent)
            _hook("file", self.destination, relative)
            handle = _win_create(parent_handle, name, directory=False,
                                 disposition=_FILE_CREATE, exclusive=True)
            self.files.append(handle)
            view = memoryview(data)
            while view:
                chunk = view[:0x7ffff000]
                copied = ctypes.create_string_buffer(chunk.tobytes())
                written = wintypes.DWORD()
                if not _kernel32.WriteFile(handle, copied, len(chunk), ctypes.byref(written), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if written.value <= 0:
                    raise MaterializeError("release file write made no progress")
                view = view[written.value:]
            if not _kernel32.FlushFileBuffers(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            if _win_info(handle).nNumberOfLinks != 1:
                raise MaterializeError("release file acquired an unexpected hardlink: " + relative)


def materialize(destination, entries, blobs):
    """Create validated ``(path, mode, oid)`` entries without following aliases."""
    writer_type = _WindowsWriter if os.name == "nt" else _PosixWriter
    writer = writer_type(destination)
    try:
        for entry in entries:
            writer.file(entry.path, blobs[entry.oid], int(entry.mode[-3:], 8))
    finally:
        writer.close()
