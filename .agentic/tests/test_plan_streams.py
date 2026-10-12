import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentic.canonical import canonical, loads, sha256


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

    def test_cli_result_records_each_ticket_stream_in_json_and_markdown(self):
        fixture = ROOT / ".agentic/examples/stream-input.json"
        with tempfile.TemporaryDirectory(prefix="awf-plan-streams-") as temporary:
            project = Path(temporary).resolve()
            (project / ".agentic").mkdir()
            config = loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
            config_bytes = canonical(config)
            (project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(config_bytes)
            (project / "OPERATING_CONFIG.yaml").write_bytes(
                (ROOT / ".agentic/examples/OPERATING_CONFIG.yaml").read_bytes())
            output = []
            with patch.object(self.cli, "verify_installed", return_value="a" * 64), \
                    patch("builtins.print", side_effect=lambda value, **kwargs: output.append(value)):
                code = self.cli.main([
                    "--input", str(fixture),
                    "--expected-input-sha256", sha256(fixture.read_bytes()),
                    "--project-root", str(project),
                    "--allow-synthetic", "--now", "2026-09-11T10:00:00Z",
                ])
            self.assertEqual(0, code)
            report = loads(output[-1])
            self.assertEqual("C", report["ticket_streams"]["DEMO-31"])
            self.assertEqual([], report["stream_assignment_history"])
            self.assertFalse(report["write_scope"]["fail_closed_to_one_stream"])
            plan = loads((project / "STREAMS.json").read_text(encoding="utf-8"))
            self.assertEqual(report["ticket_streams"], {
                ticket_id: ticket["stream"] for ticket_id, ticket in plan["tickets"].items()
            })
            markdown = (project / "STREAMS.md").read_text(encoding="utf-8")
            self.assertIn("## Stream C", markdown)
            self.assertIn("DEMO-31", markdown)


if __name__ == "__main__":
    unittest.main()
