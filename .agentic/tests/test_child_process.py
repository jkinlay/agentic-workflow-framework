"""Regression coverage for the shared AWF child-process environment."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from agentic.child_process import (
    ISOLATED_GIT_ENV,
    ChildEnvironmentError,
    PROVIDER_API_KEY_ENV_VARS,
    child_env,
    configured_child_env_strip_extra,
    isolated_git_env,
    scrub_process_env,
    validate_child_env_strip_extra,
)


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
        self.assertEqual(PROVIDER_API_KEY_ENV_VARS, frozenset({
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY",
            "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
        }))
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

    def test_isolated_git_environment_removes_all_inherited_git_redirection(self):
        inherited = {
            "Path": "fixture-bin", "GIT_DIR": "other.git", "git_work_tree": "elsewhere",
            "GiT_ObJeCt_DiReCtOrY": "objects", "GIT_ALTERNATE_OBJECT_DIRECTORIES": "alternates",
            "GIT_CONFIG": "redirect.cfg", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "include.path",
            "GIT_CONFIG_VALUE_0": "attacker.cfg", "PYTHONPATH": "candidate-code", "UNRELATED": "kept",
        }
        result = isolated_git_env(inherited)
        self.assertEqual(result["UNRELATED"], "kept")
        self.assertEqual(result["Path"], "fixture-bin")
        self.assertNotIn("PYTHONPATH", result)
        self.assertFalse({key for key in result if key.upper().startswith("GIT_")}
                         - set(ISOLATED_GIT_ENV))
        self.assertEqual({key: result[key] for key in ISOLATED_GIT_ENV}, ISOLATED_GIT_ENV)

    def test_all_builtin_provider_names_are_removed_case_insensitively(self):
        base = {name.swapcase(): "secret" for name in PROVIDER_API_KEY_ENV_VARS}
        self.assertEqual(child_env(base), {})

    def test_configured_extra_is_removed_and_cannot_be_restored(self):
        result = child_env(
            {"PRIVATE_TOKEN": "original", "SAFE": "kept"},
            extra={"private_token": "replacement"},
            strip_extra=["PRIVATE_TOKEN"],
        )
        self.assertEqual(result, {"SAFE": "kept"})

    def test_configured_extra_loader_reads_bounded_json_project_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "PROJECT_CONFIG.yaml"
            path.write_text('{"execution":{"child_env_strip_extra":["PRIVATE_TOKEN"]}}', encoding="utf-8")
            self.assertEqual(configured_child_env_strip_extra(path), frozenset({"PRIVATE_TOKEN"}))

    def test_configured_extra_validation_fails_closed(self):
        invalid = [
            "PRIVATE_TOKEN", "private_token",
        ]
        with self.assertRaisesRegex(ChildEnvironmentError, "case-insensitive duplicate"):
            validate_child_env_strip_extra(invalid)
        for value in ([""], [" PADDED"], ["LINE\nBREAK"], ["9INVALID"], ["GH_TOKEN"], ["github_token"]):
            with self.subTest(value=value), self.assertRaises(ChildEnvironmentError):
                validate_child_env_strip_extra(value)
        with self.assertRaisesRegex(ChildEnvironmentError, "must be an array"):
            validate_child_env_strip_extra("PRIVATE_TOKEN")

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
        self.assertIn("$childEnvStripNames -contains $name.ToUpperInvariant()", source)
        self.assertIn("$projectConfig.execution.child_env_strip_extra", source)
        self.assertIn("case-insensitive duplicate", source)
        self.assertIn("may not remove GitHub host authorization", source)
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
