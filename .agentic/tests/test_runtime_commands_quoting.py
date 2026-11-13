"""PowerShell literal regressions; all executed commands call inert functions."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from agentic.owner_publication import _literal
from agentic.runtime_commands import command_catalog, powershell_quote


QUOTES = ("'", "\u2018", "\u2019", "\u201a", "\u201b")


class PowerShellQuotingTests(unittest.TestCase):
    def check_literal(self, quote):
        value = "data" + quote + "x"
        for helper in (powershell_quote, _literal):
            with self.subTest(helper=helper.__name__):
                self.assertEqual("'data" + quote * 2 + "x'", helper(value))

    def test_ascii_quote(self):
        self.check_literal("'")

    def test_left_single_quote(self):
        self.check_literal("\u2018")

    def test_right_single_quote(self):
        self.check_literal("\u2019")

    def test_single_low_quote(self):
        self.check_literal("\u201a")

    def test_single_high_reversed_quote(self):
        self.check_literal("\u201b")

    def test_controls_are_still_refused(self):
        for control in ("\x00", "\r", "\n"):
            with self.subTest(control=repr(control)):
                with self.assertRaises(ValueError):
                    powershell_quote("data" + control + "x")

    def require_engine(self, engine):
        executable = shutil.which(engine)
        if executable is None:
            self.skipTest(engine + " unavailable on PATH; real PowerShell proof requires this engine")
        return executable

    def run_shell(self, executable, script):
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=30)
        self.assertEqual(0, completed.returncode,
                         completed.stderr.decode("utf-8", errors="replace"))
        return json.loads(completed.stdout.decode("utf-8-sig"))

    def check_round_trip(self, engine):
        executable = self.require_engine(engine)
        values = ["data" + quote + "x" for quote in QUOTES]
        values += ["", "space name\\$value;`&[,]", "".join(QUOTES) * 2]
        for helper in (powershell_quote, _literal):
            accepted = values if helper is powershell_quote else values[0:5] + values[6:]
            script = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
                      "function AWF-Echo { @($args) | ConvertTo-Json -Compress }\n"
                      "AWF-Echo " + " ".join(helper(value) for value in accepted))
            with self.subTest(helper=helper.__name__):
                self.assertEqual(accepted, self.run_shell(executable, script))

    def test_round_trip_pwsh(self):
        self.check_round_trip("pwsh")

    def test_round_trip_powershell(self):
        self.check_round_trip("powershell")

    def check_printed_commands(self, engine):
        executable = self.require_engine(engine)
        # Use real temporary roots where the host permits the child name.
        # AWF-Literal only records arguments; no deletion command is invoked.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        roots = []
        for quote in QUOTES[1:]:
            for tail in (",", "; $script:injected=$true; #"):
                name = "data" + quote + tail + quote + "x"
                child = Path(temporary.name) / name
                try:
                    child.mkdir()
                except OSError:
                    # Windows forbids the colon in the injection payload.
                    # Keep that exact payload in a path-free synthetic root.
                    if sys.platform != "win32" or ":" not in name:
                        raise
                    roots.append(name)
                else:
                    roots.append(str(child))
        for root in roots:
            with self.subTest(root=root):
                with patch("agentic.runtime_commands.installed_paths", return_value=(
                        Path(root), Path("AWF-Capture"), Path("entry.py"))):
                    catalog = command_catalog(root, platform="nt")
                commands = [item["command"] for item in catalog["commands"]]
                literal_path = root + "\\.agentic\\example"
                commands.append("AWF-Literal -LiteralPath " + powershell_quote(literal_path)
                                + " -Recurse -Force")
                script = """
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:injected=$false
$script:observed=New-Object System.Collections.ArrayList
function AWF-Capture { [void]$script:observed.Add(@($args)) }
function AWF-Literal {
    param([string[]]$LiteralPath, [switch]$Recurse, [switch]$Force)
    [void]$script:observed.Add(@($LiteralPath))
}
""" + "\n".join(commands) + """
@{observed=@($script:observed.ToArray()); injected=$script:injected} | ConvertTo-Json -Depth 5 -Compress
"""
                observed = self.run_shell(executable, script)
                self.assertFalse(observed["injected"])
                expected = [["-B", "-I", "entry.py", "--root", root, *arguments]
                            for arguments in (["preflight"], ["verify-installation"],
                                              ["validate-config"], ["status"], ["operating", "show"])]
                self.assertEqual(expected + [[literal_path]], observed["observed"])

    def test_printed_commands_pwsh(self):
        self.check_printed_commands("pwsh")

    def test_printed_commands_powershell(self):
        self.check_printed_commands("powershell")


if __name__ == "__main__":
    unittest.main()
