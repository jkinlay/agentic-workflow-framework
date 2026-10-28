"""Portable defaults and invocation contract for tools/windows/pre-push-gate.ps1."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "windows" / "pre-push-gate.ps1"
WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\s<>:\"|?*]+[\\/]?)+")


def _former_default_fragments():
    """Build former machine-local fragments without embedding detector matches."""
    drive = "C" + ":"
    root = "awf-review-scratch"
    return (
        root + "\\" + "awf194",
        root + "\\" + "awf195",
        drive + "\\" + root + "\\" + "awf194",
        drive + "\\" + root + "\\" + "awf195",
    )


class PrePushGatePortableTests(unittest.TestCase):
    def test_defaults_are_portable_and_scan_clean(self):
        text = GATE.read_text(encoding="utf-8")
        self.assertIn("[string]$Py = ''", text)
        self.assertIn("[string]$ControllerRoot = ''", text)
        for fragment in _former_default_fragments():
            self.assertNotIn(fragment, text)
        for number, line in enumerate(text.splitlines(), 1):
            self.assertIsNone(WINDOWS_ABSOLUTE.search(line), f"line {number}: {line!r}")
        former_line_digests = {
            "78581231c44e5c1a15d7bb629cdd20414f1513729fbf484d9221ba387c9a9cae",
            "547f782b98c9385876a1c483caecddc3f8adec35dc4708a91f98b2a075f78599",
        }
        observed = {hashlib.sha256(line.encode("utf-8")).hexdigest() for line in text.splitlines()}
        self.assertTrue(former_line_digests.isdisjoint(observed))

    def test_invocation_resolves_empty_defaults(self):
        """Empty -Py/-ControllerRoot must resolve without requiring absolute literals in source."""
        text = GATE.read_text(encoding="utf-8")
        self.assertIn("if (-not $ControllerRoot) { $ControllerRoot = $Worktree }", text)
        self.assertIn("if (-not $Py)", text)
        self.assertIn("Get-Command -Name python", text)

    def test_pwsh_param_binding_accepts_explicit_portable_args(self):
        pwsh = None
        for candidate in ("pwsh", "powershell"):
            try:
                subprocess.run([candidate, "-NoLogo", "-Command", "$PSVersionTable.PSVersion"],
                               capture_output=True, check=True, timeout=30)
                pwsh = candidate
                break
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
        if pwsh is None:
            self.skipTest("pwsh/powershell unavailable on this host")
        script = r"""
param([string]$GatePath)
$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($GatePath, [ref]$tokens, [ref]$errors)
if ($errors -and $errors.Count) { $errors | ForEach-Object { $_.ToString() }; exit 2 }
$params = $ast.ParamBlock.Parameters
$map = @{}
foreach ($p in $params) { $map[$p.Name.VariablePath.UserPath] = $p.DefaultValue }
if ($null -eq $map['Py']) { 'Py default missing'; exit 3 }
if ($null -eq $map['ControllerRoot']) { 'ControllerRoot default missing'; exit 3 }
$pyDefault = $map['Py'].Extent.Text
$rootDefault = $map['ControllerRoot'].Extent.Text
if ($pyDefault -ne "''" -and $pyDefault -ne '""') { "Py default not empty: $pyDefault"; exit 4 }
if ($rootDefault -ne "''" -and $rootDefault -ne '""') { "ControllerRoot default not empty: $rootDefault"; exit 5 }
'OK'
"""
        result = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-Command", script, "-GatePath", str(GATE)],
            capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)


def _find_powershell():
    for candidate in ("pwsh", "powershell"):
        try:
            subprocess.run(
                [candidate, "-NoLogo", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion"],
                capture_output=True, check=True, timeout=30)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return None


def _init_disposable_git_worktree(parent):
    root = Path(parent) / "worktree"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Synthetic User"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "synthetic@example.invalid"],
                   check=True, capture_output=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "base"], check=True, capture_output=True)
    return root


class PrePushGateWindowsInvocationTests(unittest.TestCase):
    """Real gate invocations on Windows. -Worktree stays mandatory."""

    @classmethod
    def setUpClass(cls):
        if sys.platform != "win32":
            raise unittest.SkipTest("Windows-only invocation checks")
        cls.pwsh = _find_powershell()
        if cls.pwsh is None:
            raise unittest.SkipTest("pwsh/powershell unavailable")

    def _run_gate(self, args, *, env=None, timeout=120):
        command = [
            self.pwsh, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(GATE), *args,
        ]
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            env=env, check=False)

    def test_explicit_args_tracked_dirty_exits_3_with_gate_log(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worktree = _init_disposable_git_worktree(temp.name)
        controller = Path(temp.name) / "controller"
        controller.mkdir()
        # Deliberate tracked-dirty precondition.
        (worktree / "seed.txt").write_text("dirty\n", encoding="utf-8")
        label = "awf270-portable-dirty"
        py = sys.executable
        result = self._run_gate([
            "-Worktree", str(worktree),
            "-Py", py,
            "-ControllerRoot", str(controller),
            "-Label", label,
        ])
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(3, result.returncode, combined)
        self.assertNotRegex(combined, r"(?i)missing.*parameter|parameter.*binding|cannot bind")
        self.assertIn("tracked modifications present", combined)
        gate_logs = list((controller / "gate-logs").glob("gate-*.log"))
        self.assertTrue(gate_logs, combined)
        log_text = gate_logs[0].read_text(encoding="utf-8", errors="replace")
        self.assertIn(label, log_text)
        self.assertIn(str(worktree), log_text)
        runs = controller / "gate-logs" / "GATE-RUNS.tsv"
        self.assertTrue(runs.is_file(), log_text)
        runs_text = runs.read_text(encoding="utf-8", errors="replace")
        self.assertIn(label, runs_text)
        self.assertIn(str(worktree), runs_text)
        self.assertIn(str(controller), combined + log_text + runs_text)
        self.assertNotIn("awf194", combined + log_text)
        self.assertNotIn("awf195", combined + log_text)

    def test_portable_defaults_resolve_from_worktree_and_path_python(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worktree = _init_disposable_git_worktree(temp.name)
        (worktree / "seed.txt").write_text("dirty-for-exit3\n", encoding="utf-8")
        label = "awf270-portable-defaults"
        # Omit only -Py and -ControllerRoot; -Worktree remains mandatory.
        result = self._run_gate([
            "-Worktree", str(worktree),
            "-Label", label,
        ])
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(3, result.returncode, combined)
        self.assertNotIn("awf194", combined)
        self.assertNotIn("awf195", combined)
        # ControllerRoot falls back to Worktree, so gate-logs live there.
        gate_logs = list((worktree / "gate-logs").glob("gate-*.log"))
        self.assertTrue(gate_logs, combined)
        log_text = gate_logs[0].read_text(encoding="utf-8", errors="replace")
        self.assertIn(str(worktree), log_text)
        self.assertIn(label, log_text)
        # PATH python should have been selected (no binding error).
        self.assertNotRegex(combined, r"(?i)missing.*parameter|parameter.*binding|cannot bind")
        self.assertNotRegex(combined, r"-Py is required when python is not on PATH")

    def test_missing_python_on_child_path_exits_3(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worktree = _init_disposable_git_worktree(temp.name)
        env = os.environ.copy()
        # Change only the child process PATH; never mutate the global process PATH.
        env["PATH"] = str(Path(temp.name) / "empty-bin")
        Path(env["PATH"]).mkdir()
        result = self._run_gate([
            "-Worktree", str(worktree),
            "-Label", "awf270-missing-python",
        ], env=env)
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(3, result.returncode, combined)
        self.assertRegex(combined, r"-Py is required when python is not on PATH")


if __name__ == "__main__":
    unittest.main()
