"""Source guard for Python 3.11-safe junction checks in shipped code."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
# These helpers already reject FILE_ATTRIBUTE_REPARSE_POINT independently.
SAFE_GETATTR = {
    (".agentic/lib/agentic/adoption_config.py", "_regular_directory"),
    (".agentic/lib/agentic/adoption_config.py", "_ensure_installed_runtime"),
    (".agentic/lib/agentic/continuous_controller.py", "_reparse"),
    (".agentic/lib/agentic/installer.py", "_verify_runtime_state"),
    (".agentic/scripts/self_test.py", "checked_scratch_root"),
}


def unsafe_checks(source, path):
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    failures = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in {"hasattr", "getattr"} and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and node.args[1].value == "is_junction"):
            continue
        owner = parents.get(node)
        while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents.get(owner)
        allowed = node.func.id == "getattr" and owner is not None and (path, owner.name) in SAFE_GETATTR
        if allowed:
            constants = {child.value for child in ast.walk(owner)
                         if isinstance(child, ast.Constant) and isinstance(child.value, str)}
            bit_checks = [child for child in ast.walk(owner)
                          if isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitAnd)]
            allowed = {"st_file_attributes", "FILE_ATTRIBUTE_REPARSE_POINT"} <= constants and bool(bit_checks)
        if not allowed:
            failures.append(f"{path}:{node.lineno}: {node.func.id} is_junction link check")
    return failures


class JunctionLinkGuardTests(unittest.TestCase):
    def test_shipped_link_checks_have_no_unsafe_junction_fallback(self):
        failures = []
        for directory in (".agentic/lib", ".agentic/scripts"):
            for path in sorted((ROOT / directory).rglob("*.py")):
                failures.extend(unsafe_checks(path.read_text(encoding="utf-8"),
                                              path.relative_to(ROOT).as_posix()))
        self.assertEqual([], failures, "\n".join(failures))

    def test_guard_catches_both_legacy_spellings(self):
        for expression in ('hasattr(p, "is_junction") and p.is_junction()',
                           'getattr(p, "is_junction", lambda: False)()'):
            with self.subTest(expression=expression):
                self.assertEqual(1, len(unsafe_checks("if " + expression + ": pass", "new.py")))


if __name__ == "__main__":
    unittest.main()
