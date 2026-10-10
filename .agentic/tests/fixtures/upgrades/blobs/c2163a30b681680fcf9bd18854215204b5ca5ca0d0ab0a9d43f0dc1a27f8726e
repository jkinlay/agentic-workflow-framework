"""Fail-closed inventory and AST checks for AWF production process launches."""
from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath

from . import ValidationError


NON_PYTHON_SUFFIXES = frozenset({".ps1", ".sh", ".cmd", ".bat"})
SUBPROCESS_LAUNCHES = frozenset({
    "Popen", "call", "check_call", "check_output", "getoutput",
    "getstatusoutput", "run",
})
SCRIPT_MECHANISMS = frozenset({
    "sanitized_process_start", "scrubbed_python_entrypoint", "delegated_launcher",
})
_ROOTS = (".agentic/lib", ".agentic/scripts", "scripts")


def _relative_path(value, *, label):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValidationError(f"{label} must be a non-empty POSIX repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(f"{label} must stay within the repository")
    return path.as_posix()


def _production_paths(root, suffixes):
    found = set()
    for directory in _ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(root)
            if (path.is_file() and path.suffix.casefold() in suffixes
                    and not {"tests", "fixtures", "examples"} & set(relative.parts)):
                found.add(relative.as_posix())
    return found


def _literal_shell_true(call):
    for keyword in call.keywords:
        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is True
        if keyword.arg is None and isinstance(keyword.value, ast.Dict):
            for key, value in zip(keyword.value.keys, keyword.value.values):
                if (isinstance(key, ast.Constant) and key.value == "shell"
                        and isinstance(value, ast.Constant) and value.value is True):
                    return True
    return False


def _has_scrubbed_environment(call, child_names, child_modules):
    for keyword in call.keywords:
        if keyword.arg != "env" or not isinstance(keyword.value, ast.Call):
            continue
        function = keyword.value.func
        if isinstance(function, ast.Name) and function.id in child_names:
            return True
        if (isinstance(function, ast.Attribute) and function.attr == "child_env"
                and isinstance(function.value, ast.Name) and function.value.id in child_modules):
            return True
    return False


def _bindings(tree):
    subprocess_modules, subprocess_names = set(), set()
    os_modules, os_names = set(), set()
    pty_modules, pty_names = set(), set()
    child_names, child_modules = set(), set()
    # Include conditional and function-local imports. Launch wrappers may need
    # alternate package and script entry modes, but both bindings must still
    # resolve to the reviewed sanitizer.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                bound = name.asname or name.name.split(".", 1)[0]
                if name.name == "subprocess": subprocess_modules.add(bound)
                if name.name == "os": os_modules.add(bound)
                if name.name == "pty": pty_modules.add(bound)
                if name.name.endswith("child_process"): child_modules.add(bound)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for name in node.names:
                bound = name.asname or name.name
                if module == "subprocess" and name.name in SUBPROCESS_LAUNCHES:
                    subprocess_names.add(bound)
                if module == "os" and (name.name in {"system", "popen"}
                                            or name.name.startswith(("exec", "spawn"))):
                    os_names.add(bound)
                if module == "pty" and name.name == "spawn":
                    pty_names.add(bound)
                if module.endswith("child_process") and name.name == "child_env":
                    child_names.add(bound)
                if module == "agentic" and name.name == "child_process":
                    child_modules.add(bound)
    return (subprocess_modules, subprocess_names, os_modules, os_names,
            pty_modules, pty_names, child_names, child_modules)


def python_launch_findings(root):
    """Return deterministic findings for unsanitized or shell-backed launches."""
    findings = []
    for relative in sorted(_production_paths(root, {".py"})):
        source = root / relative
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
        (subprocess_modules, subprocess_names, os_modules, os_names,
         pty_modules, pty_names, child_names, child_modules) = _bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            subprocess_launch = (
                isinstance(function, ast.Name) and function.id in subprocess_names
                or isinstance(function, ast.Attribute) and function.attr in SUBPROCESS_LAUNCHES
                and isinstance(function.value, ast.Name) and function.value.id in subprocess_modules)
            os_launch = (
                isinstance(function, ast.Name) and function.id in os_names
                or isinstance(function, ast.Attribute)
                and (function.attr in {"system", "popen"} or function.attr.startswith(("exec", "spawn")))
                and isinstance(function.value, ast.Name) and function.value.id in os_modules)
            pty_launch = (
                isinstance(function, ast.Name) and function.id in pty_names
                or isinstance(function, ast.Attribute) and function.attr == "spawn"
                and isinstance(function.value, ast.Name) and function.value.id in pty_modules)
            kinds = []
            if os_launch: kinds.append("forbidden_os_launch")
            if pty_launch: kinds.append("forbidden_pty_spawn")
            if subprocess_launch and _literal_shell_true(node): kinds.append("shell_true")
            if subprocess_launch and not _has_scrubbed_environment(node, child_names, child_modules):
                kinds.append("unscrubbed_subprocess")
            for kind in kinds:
                findings.append({"path": relative, "line": node.lineno, "kind": kind})
    return sorted(findings, key=lambda item: (item["path"], item["line"], item["kind"]))


def validate_repository_launch_surfaces(root, inventory_path=None):
    """Validate exact non-Python inventory and all Python launch call sites."""
    root = Path(root).resolve()
    inventory_path = Path(inventory_path or root / ".agentic/launch-surfaces.json")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Launch-surface inventory is unavailable or malformed") from exc
    if not isinstance(inventory, dict) or set(inventory) != {"version", "python_allowlist", "scripts"}:
        raise ValidationError("Launch-surface inventory has an unknown or incomplete shape")
    if inventory["version"] != 1 or not isinstance(inventory["python_allowlist"], list) or not isinstance(inventory["scripts"], list):
        raise ValidationError("Launch-surface inventory fields are malformed")

    script_paths, script_entries = [], {}
    for index, entry in enumerate(inventory["scripts"]):
        if not isinstance(entry, dict):
            raise ValidationError(f"Launch-surface script entry {index} is malformed")
        path = _relative_path(entry.get("path"), label=f"Launch-surface script entry {index}")
        if path in script_entries:
            raise ValidationError("Launch-surface inventory contains a duplicate script path")
        mechanism = entry.get("mechanism")
        if mechanism not in SCRIPT_MECHANISMS:
            raise ValidationError(f"Launch-surface script {path} has an unsupported mechanism")
        expected = {"path", "mechanism", "entry_point" if mechanism != "delegated_launcher" else "delegate"}
        if set(entry) != expected:
            raise ValidationError(f"Launch-surface script {path} has stale or incomplete fields")
        target_key = "delegate" if mechanism == "delegated_launcher" else "entry_point"
        target = _relative_path(entry[target_key], label=f"Launch-surface script {path} {target_key}")
        if not (root / target).is_file():
            raise ValidationError(f"Launch-surface script {path} names an absent {target_key}")
        script_paths.append(path)
        script_entries[path] = {**entry, target_key: target}
    discovered_scripts = _production_paths(root, NON_PYTHON_SUFFIXES)
    if set(script_paths) != discovered_scripts:
        missing = sorted(discovered_scripts - set(script_paths))
        stale = sorted(set(script_paths) - discovered_scripts)
        raise ValidationError(f"Launch-surface script inventory differs: missing={missing}; stale={stale}")
    for path, entry in script_entries.items():
        if entry["mechanism"] == "delegated_launcher" and entry["delegate"] not in script_entries:
            raise ValidationError(f"Launch-surface script {path} delegates outside the approved inventory")

    allowlist, allowlist_keys = {}, set()
    for index, entry in enumerate(inventory["python_allowlist"]):
        if not isinstance(entry, dict) or set(entry) != {"path", "line", "kind", "reason"}:
            raise ValidationError(f"Python launch allowlist entry {index} is malformed")
        path = _relative_path(entry["path"], label=f"Python launch allowlist entry {index}")
        if type(entry["line"]) is not int or entry["line"] < 1 or not isinstance(entry["kind"], str):
            raise ValidationError(f"Python launch allowlist entry {index} has an invalid key")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ValidationError(f"Python launch allowlist entry {index} needs a written reviewed reason")
        key = (path, entry["line"], entry["kind"])
        if key in allowlist_keys:
            raise ValidationError("Python launch allowlist contains a duplicate entry")
        allowlist_keys.add(key)
        allowlist[key] = entry["reason"].strip()
    findings = python_launch_findings(root)
    finding_keys = {(item["path"], item["line"], item["kind"]) for item in findings}
    unapproved = [item for item in findings if (item["path"], item["line"], item["kind"]) not in allowlist_keys]
    stale = sorted(allowlist_keys - finding_keys)
    if unapproved:
        detail = ", ".join(f"{item['path']}:{item['line']}:{item['kind']}" for item in unapproved)
        raise ValidationError("Production launch surface is not approved: " + detail)
    if stale:
        raise ValidationError("Python launch allowlist contains stale entries: " + repr(stale))
    return {"python_files": len(_production_paths(root, {".py"})),
            "python_findings_allowed": len(findings), "non_python_scripts": len(script_paths)}
