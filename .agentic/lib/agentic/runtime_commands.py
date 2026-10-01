"""Canonical installed runtime paths and copy/paste-safe PowerShell commands."""
from __future__ import annotations

import os
from pathlib import Path

from . import VERSION


def installed_paths(root, *, platform=None):
    root = Path(root).resolve()
    windows = (platform or os.name) == "nt"
    interpreter = (root / ".agentic" / ".venv" /
                   (Path("Scripts") / "python.exe" if windows else Path("bin") / "python"))
    entry_point = root / ".agentic" / "scripts" / "workflow.py"
    return root, interpreter, entry_point


def powershell_quote(value):
    """Quote one literal PowerShell argument without Markdown or backslash escapes."""
    value = str(value)
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError("PowerShell command arguments must fit on one line")
    return "'" + value.replace("'", "''") + "'"


def powershell_command(interpreter, entry_point, root, arguments):
    fields = ["&", powershell_quote(interpreter), "-B", "-I", powershell_quote(entry_point),
              "--root", powershell_quote(root), *[powershell_quote(item) for item in arguments]]
    return " ".join(fields)


def command_catalog(root, *, platform=None):
    root, interpreter, entry_point = installed_paths(root, platform=platform)
    commands = [
        ("adoption", ["preflight"], 0),
        ("verification", ["verify-installation"], 0),
        ("validation", ["validate-config"], 0),
        ("status", ["status"], 0),
        ("operating", ["operating", "show"], 0),
    ]
    return {
        "format": "awf-doctor-1",
        "template_version": VERSION,
        "root": str(root),
        "runtime": {
            "interpreter": str(interpreter),
            "entry_point": str(entry_point),
            "interpreter_exists": interpreter.is_file(),
            "entry_point_exists": entry_point.is_file(),
        },
        "shell": "powershell",
        "commands": [
            {"purpose": purpose,
             "command": powershell_command(interpreter, entry_point, root, arguments),
             "expected_exit_codes": [expected]}
            for purpose, arguments, expected in commands
        ],
        "long_argument_transport": {
            "method": "literal_argv",
            "detail": "Every supported value, including long hashes and paths, is passed as one literal argv element with PowerShell single-quote escaping. No generic JSON carrier or response-file transport is implemented.",
        },
        # Extension point for PR 4: doctor deliberately does not aggregate activation blockers.
        "activation_summary": {
            "status": "EXTENSION_POINT",
            "detail": "Activation-blocker aggregation is supplied by status, not this command.",
        },
        "execution_authority": False,
    }


def render_doctor(report):
    lines = [
        "AWF doctor",
        "Interpreter: " + report["runtime"]["interpreter"],
        "Entry point: " + report["runtime"]["entry_point"],
    ]
    lines.extend(item["purpose"] + ": " + item["command"] for item in report["commands"])
    lines.append("Activation summary: EXTENSION_POINT (status owns activation blockers)")
    return "\n".join(lines) + "\n"
