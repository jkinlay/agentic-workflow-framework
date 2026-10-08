"""Encoding-stable command-line output helpers."""
from __future__ import annotations

import json
import sys


def configure_streams():
    """Use deterministic UTF-8 bytes where Python supports stream reconfiguration."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def json_text(value, *, indent=2):
    """Serialize JSON using ASCII escapes, independent of the console code page."""
    return json.dumps(value, indent=indent, ensure_ascii=True)


def emit_json(value, *, stream=None, indent=2):
    print(json_text(value, indent=indent), file=stream or sys.stdout)


def require_ascii_line(value):
    """Return a stable one-line status only when every byte is ASCII."""
    if "\n" in value or "\r" in value:
        raise ValueError("Stable status line must contain one line")
    value.encode("ascii")
    return value
