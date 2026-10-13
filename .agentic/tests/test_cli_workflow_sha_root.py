"""CLI regression for binding content lookup to the selected project root."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic.cli import main as workflow_main  # noqa: E402
import test_cli_intake_assignment as intake_fixture  # noqa: E402


class CliWorkflowShaRootTests(unittest.TestCase):
    def test_evaluate_receives_the_trusted_selected_root(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, bundle_path, state_path, *_ = (
                intake_fixture.CliIntakeAssignmentTests()._fixture(folder)
            )
            observed = []

            def capture(*args, **kwargs):
                observed.append(kwargs.get("repository"))
                return {"status": "captured"}

            stdout = io.StringIO()
            with mock.patch("agentic.cli.evaluate", side_effect=capture), \
                    contextlib.redirect_stdout(stdout):
                code = workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path),
                    "--assignment-state", str(state_path),
                    "--now", intake_fixture.NOW,
                ])

        self.assertEqual(0, code)
        self.assertEqual({"status": "captured"}, json.loads(stdout.getvalue()))
        self.assertEqual([runtime.absolute()], observed)


if __name__ == "__main__":
    unittest.main()
