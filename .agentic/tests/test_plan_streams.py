import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PlanStreamsScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "awf_plan_streams_script_test", ROOT / ".agentic/scripts/plan_streams.py"
        )
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def test_snapshot_config_path_is_relative_or_placeholder(self):
        with tempfile.TemporaryDirectory(prefix="awf-plan-streams-") as temporary:
            root = Path(temporary).resolve()
            local = root / ".agentic" / "PROJECT_CONFIG.yaml"
            external = root.parent / "external-project" / "PROJECT_CONFIG.yaml"

            self.assertEqual(
                self.cli.snapshot_config_path(local, root),
                ".agentic/PROJECT_CONFIG.yaml",
            )
            self.assertEqual(
                self.cli.snapshot_config_path(external, root),
                "<external-project-configuration>",
            )


if __name__ == "__main__":
    unittest.main()
