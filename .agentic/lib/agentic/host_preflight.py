"""Host preflight: name the Windows hazards before they bite. Never blocks INSTALLED.

Each row is PASS, WARN, SKIP or N_A with a remedy line. Rows are observations
of this host; they grant nothing and are recorded in the adoption PR.
"""
from __future__ import annotations
import configparser
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib

from .child_process import child_env

PATH_WARN_LENGTH = 180
MANAGED_PATHS = ("/.agentic/**", "/AGENTS.md", "/.github/PULL_REQUEST_TEMPLATE.md")
ROUTE_OBSERVATION_DEFAULT_DAYS = 30


def row(check, status, detail, remedy=""):
    return {"check": check, "status": status, "detail": detail, "remedy": remedy}


def run(args, cwd=None):
    """Trusted-host executables only: never a file inside the checkout or a script wrapper."""
    from . import ValidationError
    from .providers.github_status import host_executable
    try:
        executable = host_executable(args[0], Path(cwd or os.getcwd()))
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
        result = subprocess.run([executable, *args[1:]], capture_output=True, text=True, timeout=30, cwd=cwd,
                                env=child_env(env), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValidationError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return result.returncode, (result.stdout or result.stderr).strip()


def git_config(root, key):
    code, output = run(["git", "config", "--get", key], cwd=str(root))
    if code is None:
        return "unavailable (" + output.split(":")[0] + ")"
    return output if code == 0 else None


def gitattributes_coverage(root):
    path = Path(root) / ".gitattributes"
    if not path.is_file():
        return False, "no root .gitattributes"
    body = path.read_text(encoding="utf-8", errors="replace")
    patterns = [line.split()[0] for line in body.splitlines() if line.strip() and not line.startswith("#")]
    def covers(managed):
        return any(p == managed or p == managed.lstrip("/") or p == "*" or p == "**" for p in patterns)
    missing = [m for m in MANAGED_PATHS if not covers(m)]
    return not missing, ("covered" if not missing else "uncovered managed paths: " + ", ".join(missing))


def symlink_privilege():
    try:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target.txt"
            target.write_text("x", encoding="utf-8")
            (Path(folder) / "link").symlink_to(target)
        return "PASS", "symbolic links can be created", ""
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314 or "1314" in str(exc):
            return "SKIP", "Windows symlink creation privilege unavailable (WinError 1314).", "Enable Developer Mode or grant SeCreateSymbolicLinkPrivilege; tests needing links are declared skips (PLATFORM_PRIVILEGE)."
        return "WARN", f"symlink probe failed: {type(exc).__name__}", "Inspect filesystem permissions."


def _excludes_agentic(value):
    if isinstance(value, str):
        values = [item.strip() for item in value.replace("\n", ",").split(",")]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        return False
    for item in values:
        normalized = item.strip().replace("\\", "/").removeprefix("./").rstrip("/")
        if normalized == ".agentic" or normalized.startswith(".agentic/"):
            return True
    return False


def project_lint_scope(root):
    """Report whether project-owned lint configuration excludes managed files."""
    root = Path(root)
    configured = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            tool = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {})
            ruff = tool.get("ruff") if isinstance(tool, dict) else None
            if isinstance(ruff, dict):
                excluded = _excludes_agentic(ruff.get("exclude")) or _excludes_agentic(ruff.get("extend-exclude"))
                configured.append(("ruff", excluded, None))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            if "[tool.ruff" in pyproject.read_text(encoding="utf-8", errors="replace"):
                configured.append(("ruff", False, f"unreadable configuration ({type(exc).__name__})"))
    for name in ("setup.cfg", ".flake8"):
        path = root / name
        if not path.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(path, encoding="utf-8")
            section = next((item for item in parser.sections() if item.lower() == "flake8"), None)
            if section:
                excluded = _excludes_agentic(parser.get(section, "exclude", fallback=None)) or _excludes_agentic(parser.get(section, "extend-exclude", fallback=None))
                configured.append((f"flake8 ({name})", excluded, None))
        except (OSError, UnicodeError, configparser.Error) as exc:
            if "[flake8" in path.read_text(encoding="utf-8", errors="replace").lower():
                configured.append((f"flake8 ({name})", False, f"unreadable configuration ({type(exc).__name__})"))
    if not configured:
        return row("project_lint_scope", "N_A", "no root Ruff or flake8 configuration found", "")
    missing = [name for name, excluded, _ in configured if not excluded]
    detail = "; ".join(f"{name}: {error or ('excludes .agentic' if excluded else 'does not exclude .agentic')}"
                       for name, excluded, error in configured)
    if missing:
        remedy = ('Add `extend-exclude = [".agentic"]` under `[tool.ruff]` for Ruff, '
                  'or `extend-exclude = .agentic` under `[flake8]` for flake8.')
        return row("project_lint_scope", "WARN", detail, remedy)
    return row("project_lint_scope", "PASS", detail, "")


def _configured_routes(value):
    routes = set()
    def visit(item):
        if isinstance(item, dict):
            if isinstance(item.get("model"), str) and isinstance(item.get("reasoning_effort"), str):
                routes.add((item["model"], item["reasoning_effort"]))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return routes


def _load_json_or_yaml(path):
    from .canonical import load_yaml, loads
    raw = path.read_bytes()
    return loads(raw.decode("utf-8-sig")) if raw.lstrip().startswith((b"{", b"[")) else load_yaml(raw)


def route_models_observed(root, *, config=None, capabilities=None, now=None):
    """Return the warning-only route observation row for configured routes."""
    from . import ValidationError
    from .canonical import timestamp
    root = Path(root).resolve()
    try:
        if config is None:
            config_path = root / ".agentic/PROJECT_CONFIG.yaml"
            if not config_path.is_file():
                return row("route_models_observed", "N_A", "no project routing configuration found", "")
            config = _load_json_or_yaml(config_path)
        execution = config.get("execution", {})
        routes = _configured_routes({"roles": execution.get("roles", {}),
                                     "model_routing": execution.get("model_routing", {})})
        operating = root / "OPERATING_CONFIG.yaml"
        if operating.is_file():
            routes |= _configured_routes(_load_json_or_yaml(operating))
        settings = execution.get("route_capabilities", {})
        max_age_days = settings.get("max_age_days", ROUTE_OBSERVATION_DEFAULT_DAYS)
        if type(max_age_days) is not int or max_age_days < 1:
            raise ValidationError("execution.route_capabilities.max_age_days must be an integer >= 1")
        if capabilities is None:
            relative = settings.get("observation_path", ".agentic/route-capabilities.json")
            if not isinstance(relative, str) or not relative:
                raise ValidationError("route capability observation_path must be a nonempty relative path")
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValidationError("route capability observation_path escapes the project root")
            if not path.is_file():
                names = ", ".join(sorted(model + "/" + effort for model, effort in routes)) or "none"
                return row("route_models_observed", "WARN", "observation record missing; configured routes: " + names,
                           "Record successful probes/refusals with host provenance at " + relative + ".")
            capabilities = _load_json_or_yaml(path)
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("models"), dict):
            raise ValidationError("route capability record requires a models object")
        observed, refused, stale, unproven = set(), set(), set(), set()
        reference_now = timestamp(now) if now else datetime.now(timezone.utc)
        for model, value in capabilities["models"].items():
            provenance = value if isinstance(value, dict) else capabilities
            efforts = value.get("reasoning_efforts", []) if isinstance(value, dict) else value
            status = value.get("status", "observed") if isinstance(value, dict) else "observed"
            required = ("host_id", "host_software", "host_software_version", "method", "observed_at")
            if not all(isinstance(provenance.get(field), str) and provenance[field].strip() for field in required):
                unproven.add(model)
                continue
            if provenance["method"] not in {"successful_probe", "recorded_refusal"}:
                unproven.add(model)
                continue
            observed_at = timestamp(provenance["observed_at"])
            if (reference_now - observed_at).total_seconds() > max_age_days * 86400 or observed_at > reference_now:
                stale.add(model)
            if status == "refused" or provenance["method"] == "recorded_refusal":
                refused.add(model)
            elif status == "observed" and isinstance(efforts, list) and all(isinstance(e, str) for e in efforts):
                observed.update((model, effort) for effort in efforts)
            else:
                unproven.add(model)
        missing = sorted(route for route in routes if route not in observed and route[0] not in refused)
        refused_routes = sorted(route for route in routes if route[0] in refused)
        stale_routes = sorted(route for route in routes if route[0] in stale)
        unproven_routes = sorted(route for route in routes if route[0] in unproven)
        problems = []
        for label, values in (("missing", missing), ("refused", refused_routes),
                              ("stale", stale_routes), ("unproven", unproven_routes)):
            if values:
                problems.append(label + ": " + ", ".join(model + "/" + effort for model, effort in values))
        if problems:
            return row("route_models_observed", "WARN", "; ".join(problems),
                       "Probe configured routes on the actual host and refresh the provenance record; listed capabilities are claims until observed.")
        detail = f"{len(routes)} configured model/effort route(s) freshly observed (max age {max_age_days} days)"
        return row("route_models_observed", "PASS", detail, "")
    except (OSError, UnicodeError, ValueError, KeyError, ValidationError, json.JSONDecodeError) as exc:
        return row("route_models_observed", "WARN", "observation record invalid: " + str(exc),
                   "Replace it with a current host-provenance record; this warning never blocks installation.")


def preflight(root, *, platform=None):
    root = Path(root)
    windows = (platform or os.name) == "nt"
    rows = []
    depth = len(str(root.resolve()))
    rows.append(row("checkout_path_length", "WARN" if depth > PATH_WARN_LENGTH else "PASS",
                    f"{depth} characters", "Relocate the checkout below a shorter path; nested evidence copies exceeded 260 characters on PR #11." if depth > PATH_WARN_LENGTH else ""))
    rows.append(project_lint_scope(root))
    rows.append(route_models_observed(root))
    if windows:
        longpaths = git_config(root, "core.longpaths")
        rows.append(row("core.longpaths", "PASS" if longpaths == "true" else "WARN", f"core.longpaths={longpaths or 'unset'}",
                        "" if longpaths == "true" else "git config --system core.longpaths true (also add the CI step)."))
        code, output = run(["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy -List | ForEach-Object { $_.Scope.ToString() + '=' + $_.ExecutionPolicy.ToString() }"])
        if code is None:
            rows.append(row("powershell_execution_policy", "SKIP", output, "PowerShell not available; fixture launchers using .ps1 may fail."))
        else:
            restricted = any(s in output for s in ("=Restricted", "=AllSigned", "=Undefined"))
            effective = next((line for line in output.splitlines() if not line.endswith("=Undefined")), output)
            rows.append(row("powershell_execution_policy", "WARN" if restricted else "PASS", output.replace("\n", "; "),
                            "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned, or launch fixtures with -ExecutionPolicy Bypass." if restricted else ""))
        status, detail, remedy = symlink_privilege()
        rows.append(row("symlink_privilege", status, detail, remedy))
        autocrlf = git_config(root, "core.autocrlf")
        covered, detail = gitattributes_coverage(root)
        rows.append(row("line_endings", "PASS" if covered else "WARN", f"core.autocrlf={autocrlf or 'unset'}; .gitattributes {detail}",
                        "" if covered else "Merge .agentic/templates/installed.gitattributes into the root .gitattributes so manifest-bound bytes survive checkout."))
    else:
        for name in ("core.longpaths", "powershell_execution_policy", "symlink_privilege", "line_endings"):
            rows.append(row(name, "N_A", "not a Windows host", ""))
    attributes = Path(root) / ".gitattributes"
    if attributes.is_file() and "filter=lfs" in attributes.read_text(encoding="utf-8", errors="replace"):
        code, output = run(["git", "lfs", "version"])
        rows.append(row("git_lfs", "PASS" if code == 0 else "WARN", output if code == 0 else "git lfs not found",
                        "" if code == 0 else "Install Git LFS; .gitattributes names an lfs filter."))
    else:
        rows.append(row("git_lfs", "N_A", ".gitattributes names no lfs filter", ""))
    warnings = [r for r in rows if r["status"] == "WARN"]
    return {"format": "awf-host-preflight-1", "platform": "windows" if windows else "posix", "rows": rows,
            "warnings": len(warnings), "blocks_installation": False,
            "next_action": ("; ".join(f"{r['check']}: {r['remedy']}" for r in warnings) if warnings else None)}


def render_markdown(report):
    lines = ["## Host preflight", "", f"Platform: {report['platform']}. Rows never block INSTALLED; WARN rows are the next action.", "",
             "| Check | Status | Observed | Remedy |", "| --- | --- | --- | --- |"]
    for r in report["rows"]:
        lines.append(f"| {r['check']} | {r['status']} | {r['detail']} | {r['remedy'] or '—'} |")
    return "\n".join(lines) + "\n"
