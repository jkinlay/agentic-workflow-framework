"""AWF-244 locked timezone dependency reaches isolated runtime validation."""
import importlib.metadata
import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import adoption_config as adoption


class TimezoneRuntimeTests(unittest.TestCase):
    def test_self_test_reports_the_complete_dependency_inventory(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "awf244_self_test", root / ".agentic/scripts/self_test.py")
        self_test = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self_test)
        output = io.StringIO()
        # Stop after dependency reporting, before source integrity/full-suite
        # work; the failure must remain visible in the resulting report.
        with patch("agentic.installer.verify_installed", side_effect=RuntimeError("test stop")), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(1, self_test.main([]))
        report = json.loads(output.getvalue())
        self.assertEqual("FAILED", report["status"])
        self.assertEqual("RuntimeError: test stop", report["error"])
        self.assertEqual(
            {name: importlib.metadata.version(name) for name in (
                "PyYAML", "jsonschema", "attrs", "jsonschema-specifications",
                "referencing", "rpds-py", "typing-extensions", "tzdata")},
            report["dependencies"],
        )

    def test_reviewed_import_inventory_matches_the_complete_lock(self):
        lock = Path(__file__).resolve().parents[1] / "requirements.lock"
        dependencies, _ = adoption._locked_dependencies(lock)
        self.assertEqual(set(dependencies), set(adoption._RUNTIME_IMPORTS))
        self.assertEqual("tzdata", adoption._RUNTIME_IMPORTS["tzdata"])

    def test_isolated_child_imports_and_reports_every_locked_dependency(self):
        lock = Path(__file__).resolve().parents[1] / "requirements.lock"
        dependencies, _ = adoption._locked_dependencies(lock)
        versions = {name: importlib.metadata.version(name) for name in dependencies}
        # Exercise the real -B -I child and its version/isolation assertions.
        # Inventory verification has separate bootstrap coverage; no test tree
        # is installed or represented as a qualified installed runtime here.
        with patch.object(adoption, "_runtime_site_packages"), \
                patch.object(adoption, "_runtime_tree_files", return_value={}):
            adoption._validate_runtime(
                Path(sys.executable), Path.cwd(), Path(sys.prefix), versions, {})


if __name__ == "__main__":
    unittest.main()
