"""Regression coverage for the shared AWF child-process environment."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

from agentic.child_process import PROVIDER_API_KEY_ENV_VARS, child_env


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
