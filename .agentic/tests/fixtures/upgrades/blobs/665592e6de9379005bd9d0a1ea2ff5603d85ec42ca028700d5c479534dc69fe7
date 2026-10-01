"""Host preflight: name the Windows hazards before they bite. Never blocks INSTALLED.

Each row is PASS, WARN, SKIP or N_A with a remedy line. Rows are observations
of this host; they grant nothing and are recorded in the adoption PR.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import tempfile

PATH_WARN_LENGTH = 180
MANAGED_PATHS = ("/.agentic/**", "/AGENTS.md", "/.github/PULL_REQUEST_TEMPLATE.md")


def row(check, status, detail, remedy=""):
    return {"check": check, "status": status, "detail": detail, "remedy": remedy}


def run(args, cwd=None):
    """Trusted-host executables only: never a file inside the checkout or a script wrapper."""
    from . import ValidationError
    from .adoption_status import host_executable
    try:
        executable = host_executable(args[0], Path(cwd or os.getcwd()))
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
        result = subprocess.run([executable, *args[1:]], capture_output=True, text=True, timeout=30, cwd=cwd,
                                env=env, stdin=subprocess.DEVNULL)
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


def preflight(root, *, platform=None):
    root = Path(root)
    windows = (platform or os.name) == "nt"
    rows = []
    depth = len(str(root.resolve()))
    rows.append(row("checkout_path_length", "WARN" if depth > PATH_WARN_LENGTH else "PASS",
                    f"{depth} characters", "Relocate the checkout below a shorter path; nested evidence copies exceeded 260 characters on PR #11." if depth > PATH_WARN_LENGTH else ""))
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
