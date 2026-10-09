"""Synthetic regression coverage for the production launch-surface inventory."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentic import ValidationError
from agentic.launch_surfaces import (
    python_launch_findings,
    validate_repository_launch_surfaces,
)


ROOT = Path(__file__).resolve().parents[2]


class LaunchSurfaceTests(unittest.TestCase):
    def make_root(self, python_source="", *, script=True):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / ".agentic/lib/example.py"
        source.parent.mkdir(parents=True)
        source.write_text(python_source, encoding="utf-8")
        scripts = []
        if script:
            entry = root / ".agentic/scripts/entry.py"
            entry.parent.mkdir(parents=True)
            entry.write_text("pass\n", encoding="utf-8")
            launcher = root / ".agentic/scripts/launch.ps1"
            launcher.write_text("Write-Output safe\n", encoding="utf-8")
            scripts.append({"path": ".agentic/scripts/launch.ps1",
                            "mechanism": "sanitized_process_start",
                            "entry_point": ".agentic/scripts/entry.py"})
        inventory = {"version": 1, "python_allowlist": [], "scripts": scripts}
        inventory_path = root / ".agentic/launch-surfaces.json"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        self.addCleanup(temporary.cleanup)
        return root, inventory_path, inventory

    def test_repository_inventory_is_complete_and_clean(self):
        report = validate_repository_launch_surfaces(ROOT)
        self.assertGreater(report["python_files"], 0)
        self.assertGreater(report["non_python_scripts"], 0)
        self.assertEqual(report["python_findings_allowed"], 0)

    def test_alias_and_from_import_subprocess_calls_require_child_env(self):
        source = """import subprocess as sp
from subprocess import run as launch
from subprocess import Popen as start
sp.run(['one'])
launch(['two'])
start(['three'])
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual(
            [(item["line"], item["kind"]) for item in python_launch_findings(root)],
            [(4, "unscrubbed_subprocess"), (5, "unscrubbed_subprocess"),
             (6, "unscrubbed_subprocess")],
        )

    def test_forbidden_os_and_pty_launch_aliases_are_detected(self):
        source = """import os as operating
from os import spawnv as spawn
import pty as terminal
from pty import spawn as pspawn
operating.system('one')
spawn(0, 'two', [])
terminal.spawn(['three'])
pspawn(['four'])
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual(
            [item["kind"] for item in python_launch_findings(root)],
            ["forbidden_os_launch", "forbidden_os_launch",
             "forbidden_pty_spawn", "forbidden_pty_spawn"],
        )

    def test_every_os_exec_and_spawn_family_is_detected(self):
        names = [
            "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
            "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
            "popen", "system",
        ]
        source = "import os\n" + "\n".join(f"os.{name}('x')" for name in names) + "\n"
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual([item["kind"] for item in python_launch_findings(root)],
                         ["forbidden_os_launch"] * len(names))

    def test_nested_import_alias_is_resolved_and_unrelated_method_is_ignored(self):
        source = """if True:
    import subprocess as nested
class Other:
    def run(self, value):
        return value
nested.run(['unsafe'])
Other().run(['unrelated'])
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual([(item["line"], item["kind"]) for item in python_launch_findings(root)],
                         [(6, "unscrubbed_subprocess")])

    def test_shell_true_direct_and_expanded_literal_are_detected(self):
        source = """import subprocess
from agentic.child_process import child_env
subprocess.run('one', shell=True, env=child_env())
subprocess.run('two', env=child_env(), **{'shell': True})
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual([item["kind"] for item in python_launch_findings(root)],
                         ["shell_true", "shell_true"])

    def test_scrubbed_shell_false_launch_is_accepted(self):
        source = """import subprocess as sp
from agentic.child_process import child_env as clean
sp.run(['safe'], shell=False, env=clean())
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual(python_launch_findings(root), [])

    def test_shadowed_child_env_name_does_not_satisfy_gate(self):
        source = """import subprocess
def child_env():
    return {}
subprocess.run(['unsafe'], env=child_env())
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual([item["kind"] for item in python_launch_findings(root)],
                         ["unscrubbed_subprocess"])

    def test_child_process_module_alias_is_accepted(self):
        source = """import subprocess
from agentic import child_process as safety
subprocess.run(['safe'], env=safety.child_env())
"""
        root, _, _ = self.make_root(source, script=False)
        self.assertEqual(python_launch_findings(root), [])

    def test_allowlist_needs_reason_and_rejects_stale_entries(self):
        root, path, inventory = self.make_root("import subprocess\nsubprocess.run(['x'])\n", script=False)
        finding = python_launch_findings(root)[0]
        inventory["python_allowlist"] = [{**finding, "reason": ""}]
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "written reviewed reason"):
            validate_repository_launch_surfaces(root)
        inventory["python_allowlist"] = [{**finding, "line": 99, "reason": "reviewed exception"}]
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "not approved"):
            validate_repository_launch_surfaces(root)
        inventory["python_allowlist"] = [{**finding, "reason": "reviewed exception"}]
        path.write_text(json.dumps(inventory), encoding="utf-8")
        self.assertEqual(validate_repository_launch_surfaces(root)["python_findings_allowed"], 1)

    def test_script_inventory_fails_closed_for_missing_and_stale_paths(self):
        root, path, inventory = self.make_root()
        inventory["scripts"] = []
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "inventory differs"):
            validate_repository_launch_surfaces(root)
        (root / ".agentic/scripts/launch.ps1").unlink()
        inventory["scripts"] = [{"path": ".agentic/scripts/launch.ps1",
                                 "mechanism": "sanitized_process_start",
                                 "entry_point": ".agentic/scripts/entry.py"}]
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "inventory differs"):
            validate_repository_launch_surfaces(root)

    def test_script_inventory_rejects_unsafe_or_unsupported_entries(self):
        root, path, inventory = self.make_root()
        cases = [
            ({**inventory["scripts"][0], "path": "../launch.ps1"}, "stay within"),
            ({**inventory["scripts"][0], "mechanism": "unknown"}, "unsupported mechanism"),
            ({**inventory["scripts"][0], "entry_point": ".agentic/scripts/missing.py"}, "absent entry_point"),
        ]
        for entry, message in cases:
            with self.subTest(entry=entry):
                inventory["scripts"] = [entry]
                path.write_text(json.dumps(inventory), encoding="utf-8")
                with self.assertRaisesRegex(ValidationError, message):
                    validate_repository_launch_surfaces(root)

    def test_script_inventory_rejects_duplicate_and_absent_delegated_launcher(self):
        root, path, inventory = self.make_root()
        inventory["scripts"] = inventory["scripts"] * 2
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "duplicate script path"):
            validate_repository_launch_surfaces(root)
        inventory["scripts"] = [{"path": ".agentic/scripts/launch.ps1",
                                 "mechanism": "delegated_launcher",
                                 "delegate": ".agentic/scripts/missing.ps1"}]
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "absent delegate"):
            validate_repository_launch_surfaces(root)


if __name__ == "__main__":
    unittest.main()
