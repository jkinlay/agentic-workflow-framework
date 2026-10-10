"""Cross-platform path normalization for strict containment checks."""
from __future__ import annotations

import os
from pathlib import Path


def _identity_long_path(path):
    return Path(path)


def _get_windows_long_path(path):
    """Return the Win32 long spelling of one existing path, if available."""
    import ctypes
    from ctypes import wintypes

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_long = kernel.GetLongPathNameW
        get_long.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_long.restype = wintypes.DWORD
        needed = get_long(str(path), None, 0)
        if not needed:
            return Path(path)
        buffer = ctypes.create_unicode_buffer(needed)
        written = get_long(str(path), buffer, needed)
        if not written or written >= needed:
            return Path(path)
        return Path(buffer.value)
    except (AttributeError, OSError, ValueError):
        return Path(path)


def _expand_windows_long_path(path):
    """Expand the longest existing prefix and preserve a nonexistent tail."""
    candidate = Path(path)
    prefix = candidate
    tail = []
    while True:
        try:
            exists = prefix.exists()
        except OSError:
            exists = False
        if exists:
            break
        parent = prefix.parent
        if parent == prefix:
            return candidate
        tail.append(prefix.name)
        prefix = parent
    expanded = _get_windows_long_path(prefix)
    return expanded.joinpath(*reversed(tail))


_long_path_expander = (_expand_windows_long_path if os.name == "nt"
                       else _identity_long_path)


def normalize_path(path):
    """Return an absolute, physical path with Windows short names expanded."""
    resolved = Path(path).resolve(strict=False)
    return Path(_long_path_expander(resolved))


def _comparison_path(path):
    normalized = normalize_path(path)
    if os.name == "nt":
        return Path(os.path.normcase(str(normalized)))
    return normalized


def relative_within(child, root):
    """Return child's normalized relative path, or raise ValueError if outside."""
    return _comparison_path(child).relative_to(_comparison_path(root))


def is_within(child, root):
    """Return whether the normalized child is within the normalized root."""
    try:
        relative_within(child, root)
    except ValueError:
        return False
    return True
