"""Canonical installed runtime paths and copy/paste-safe shell commands."""
from __future__ import annotations

import os
import re
import shlex
import stat
import sys
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


def provision_command(root):
    root, _, entry_point = installed_paths(root)
    runtime = root / '.agentic' / '.venv'
    executable = Path(sys.executable).resolve()
    base = getattr(sys, '_base_executable', None)
    host = sys.executable
    # Before Python 3.14, -S can hide a venv in sys.prefix; the base
    # executable still identifies its host interpreter.
    if (sys.prefix != sys.base_prefix or executable.is_relative_to(runtime) or
            (base and Path(base).resolve() != executable)):
        host = 'HOST_PYTHON'
        if base:
            candidate = Path(base).resolve()
            if (candidate.is_file() and not candidate.is_relative_to(runtime) and
                    (sys.prefix == sys.base_prefix or not candidate.is_relative_to(Path(sys.prefix).resolve()))):
                host = str(candidate)
    arguments = ['provision-runtime', '--wheelhouse', 'ABS_WHEELHOUSE']
    posix = [str(host), '-B', '-I', str(entry_point), '--root', str(root), *arguments]
    return ('PowerShell: ' + powershell_command(host, entry_point, root, arguments) +
            '\nPOSIX sh: ' + shlex.join(posix))


def _runtime_anomaly(path):
    return ('WARNING: Runtime recovery anomaly: ' + repr(str(path)) +
            '. Inspect under owner control; no cleanup command is safe to recommend. '
            'This entry is not ignored by Git and must not be committed.')


def runtime_backup_warning(path):
    """Render owner-only cleanup advice; never execute a cleanup operation."""
    path = Path(path)
    try:
        metadata = os.lstat(path)
        reparse = getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
        if (re.fullmatch(r'\.venv\.(?:backup|staging)-[0-9a-f]{32}', path.name) is None or
                not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or reparse):
            return _runtime_anomaly(path)
        quoted = powershell_quote(path)
    except (OSError, ValueError):
        return _runtime_anomaly(path)
    path = str(path)
    return ("WARNING: Retained runtime directory: " + path +
            ". Once satisfied with recovery, remove this retained directory yourself. "
            "This directory is not ignored by Git and must not be committed.\n"
            "PowerShell: Remove-Item -LiteralPath " + quoted + " -Recurse -Force\n"
            "POSIX sh: rm -rf -- " + shlex.quote(path))


def runtime_backup_warnings(root):
    """Observe runtime siblings without traversing or changing retained bytes."""
    parent = Path(root).resolve() / '.agentic'
    try:
        paths = sorted(path for path in parent.iterdir()
                       if path.name.startswith(('.venv.backup-', '.venv.staging-')))
    except FileNotFoundError:
        return []
    except OSError:
        return ['WARNING: Runtime recovery inventory unavailable; inspect .agentic/.venv.backup-* and .venv.staging-* under owner control.']
    warnings = []
    for path in paths:
        try:
            warnings.append(runtime_backup_warning(path))
        except (OSError, ValueError):
            warnings.append(_runtime_anomaly(path))
    return warnings


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
        "warnings": runtime_backup_warnings(root),
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
        "next_action": provision_command(root) if not interpreter.is_file() else None,
    }


def render_doctor(report):
    lines = [
        "AWF doctor",
        "Interpreter: " + report["runtime"]["interpreter"],
        "Entry point: " + report["runtime"]["entry_point"],
    ]
    lines.extend(item["purpose"] + ": " + item["command"] for item in report["commands"])
    lines.extend(report.get("warnings", []))
    lines.append("Activation summary: EXTENSION_POINT (status owns activation blockers)")
    if report.get("next_action"):
        lines.append("Next: " + report["next_action"])
    return "\n".join(lines) + "\n"
