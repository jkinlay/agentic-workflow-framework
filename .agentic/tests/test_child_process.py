"""Regression coverage for the shared AWF child-process environment."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import unittest
from unittest import mock

from agentic.child_process import PROVIDER_API_KEY_ENV_VARS, child_env, scrub_process_env


ROOT = Path(__file__).resolve().parents[2]
LAUNCH_METHODS = {"run", "Popen", "check_output", "check_call"}


class ChildEnvironmentTests(unittest.TestCase):
    def test_helper_copies_filters_and_preserves_site_specific_values(self):
        base = {
            "ANTHROPIC_API_KEY": "anthropic",
            "codex_api_key": "codex",
            "OPENAI_API_KEY": "openai",
            "GH_TOKEN": "github",
            "GIT_TERMINAL_PROMPT": "0",
        }
        result = child_env(base, extra={"OPENAI_API_KEY": "replacement", "EXTRA": "kept"})
        self.assertEqual(PROVIDER_API_KEY_ENV_VARS,
                         frozenset({"ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_API_KEY"}))
        self.assertFalse(PROVIDER_API_KEY_ENV_VARS & {key.upper() for key in result})
        self.assertEqual(result["GH_TOKEN"], "github")
        self.assertEqual(result["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(result["EXTRA"], "kept")
        self.assertEqual(base["ANTHROPIC_API_KEY"], "anthropic")

    def test_scrub_process_env_removes_provider_keys_case_insensitively(self):
        environment = {
            "anthropic_api_key": "anthropic",
            "CoDeX_ApI_KeY": "codex",
            "OPENAI_API_KEY": "openai",
            "UNRELATED_VALUE": "kept",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            scrub_process_env()
            self.assertEqual(dict(os.environ), {"UNRELATED_VALUE": "kept"})

    def test_scheduled_entry_points_scrub_before_non_bootstrap_imports(self):
        for relative in (".agentic/scripts/scheduled_tick.py", ".agentic/scripts/review_loop.py"):
            with self.subTest(relative=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
                helper_import = next(node for node in tree.body
                    if isinstance(node, ast.ImportFrom) and node.module == "agentic.child_process"
                    and any(name.name == "scrub_process_env" for name in node.names))
                scrub_call = next(node for node in tree.body
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "scrub_process_env")
                other_imports = []
                for node in tree.body:
                    if not isinstance(node, (ast.Import, ast.ImportFrom)) or node is helper_import:
                        continue
                    modules = ({name.name for name in node.names} if isinstance(node, ast.Import)
                               else {node.module})
                    if modules <= {"pathlib", "sys"}:
                        continue
                    other_imports.append(node)
                self.assertLess(helper_import.lineno, scrub_call.lineno)
                self.assertTrue(other_imports)
                self.assertLess(scrub_call.lineno, min(node.lineno for node in other_imports))

    def test_powershell_preflight_uses_matching_sanitized_environment(self):
        source = (ROOT / ".agentic/scripts/register_review_loop.ps1").read_text(encoding="utf-8")
        key_list = re.search(r"\$providerApiKeyEnvVars\s*=\s*@\((.*?)\)", source, re.DOTALL)
        self.assertIsNotNone(key_list)
        powershell_keys = frozenset(re.findall(r"'([A-Z][A-Z0-9_]*)'", key_list.group(1)))
        self.assertEqual(powershell_keys, PROVIDER_API_KEY_ENV_VARS)
        removal = source.index("$preflightStartInfo.EnvironmentVariables.Remove($name)")
        invocation = source.index("$preflightProcess.Start()")
        self.assertIn("$providerApiKeyEnvVars -contains $name.ToUpperInvariant()", source)
        self.assertLess(key_list.start(), removal)
        self.assertLess(removal, invocation)

    def test_every_production_subprocess_uses_shared_environment_helper(self):
        roots = (ROOT / ".agentic/lib", ROOT / ".agentic/scripts", ROOT / "scripts")
        failures = []
        for source in (path for root in roots for path in root.rglob("*.py")
                       if "tests" not in path.relative_to(ROOT).parts):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess" and node.func.attr in LAUNCH_METHODS):
                    continue
                environment = next((item.value for item in node.keywords if item.arg == "env"), None)
                if not (isinstance(environment, ast.Call) and isinstance(environment.func, ast.Name)
                        and environment.func.id == "child_env"):
                    failures.append(f"{source.relative_to(ROOT).as_posix()}:{node.lineno}")
        self.assertEqual(failures, [], "production subprocess launch lacks env=child_env(...): " + ", ".join(failures))

    def test_generated_portable_launcher_uses_shared_environment_helper(self):
        source = (ROOT / "scripts/build_skill_distribution.py").read_text(encoding="utf-8")
        launch_lines = [line for line in source.splitlines() if "subprocess.call(" in line]
        self.assertTrue(launch_lines)
        self.assertTrue(all("env=child_env(" in line for line in launch_lines))


if __name__ == "__main__":
    unittest.main()
