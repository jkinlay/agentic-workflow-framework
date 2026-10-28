"""Portable defaults and invocation contract for tools/windows/pre-push-gate.ps1."""
from __future__ import annotations

import hashlib
import re
import subprocess
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


if __name__ == "__main__":
    unittest.main()
