"""Each AWF-75 test module must bootstrap its imports in a fresh interpreter."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TESTS = Path(__file__).resolve().parent
MODULES = (
    "test_child_process",
    "test_continuous_controller",
    "test_review_first_draft194",
    "test_review_loop",
    "test_review_qualification",
    "test_streams",
)
IMPORT_MODULE = (
    "import importlib, sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "importlib.import_module(sys.argv[2])"
)


class ModuleImportIsolationTests(unittest.TestCase):
    def test_each_target_module_imports_in_a_clean_interpreter(self):
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper() != "PYTHONPATH"
        }
        with tempfile.TemporaryDirectory(prefix="awf-module-import-") as temporary:
            for module in MODULES:
                with self.subTest(module=module):
                    completed = subprocess.run(
                        [sys.executable, "-B", "-c", IMPORT_MODULE,
                         str(TESTS), module],
                        cwd=temporary,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(
                        0,
                        completed.returncode,
                        completed.stdout + completed.stderr,
                    )


if __name__ == "__main__":
    unittest.main()
