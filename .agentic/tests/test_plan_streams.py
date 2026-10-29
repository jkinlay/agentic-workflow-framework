import importlib.util
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_only import skip_unless_source_repo, write_awf_source_fixture  # noqa: E402,F401

from agentic.canonical import canonical, loads, sha256
from agentic.child_process import child_env
from agentic.intake_assignment import assign_intake
from routing_fixtures import fixture_policy


ROOT = Path(__file__).resolve().parents[2]


class PlanStreamsScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "awf_plan_streams_script_test", ROOT / ".agentic/scripts/plan_streams.py"
        )
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    @skip_unless_source_repo("requires fresh release source defaults; installed projects retain owner policy",
                            "scripts/build_release.py")
    def test_real_cli_synthetic_release_defaults_bind_current_policy(self):
        fixture = ROOT / ".agentic/examples/stream-input.json"
        with tempfile.TemporaryDirectory(prefix="awf-current-synthetic-") as temporary:
            project = Path(temporary).resolve()
            command = [sys.executable, "-B", str(ROOT / ".agentic/scripts/plan_streams.py"),
                       "--input", str(fixture), "--expected-input-sha256", sha256(fixture.read_bytes()),
                       "--project-root", str(project), "--allow-synthetic",
                       "--now", "2026-09-11T10:00:00Z"]
            done = subprocess.run(command, cwd=ROOT, env=child_env(), capture_output=True,
                                  text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            planned = loads(done.stdout)
            self.assertEqual("READY", planned["project_status"])
            self.assertEqual(["A", "B", "C"], planned["stream_labels"])
            self.assertEqual(3, len(planned["dispatch_packets"]))
            self.assertTrue(all(not packet["live_dispatch_eligible"]
                                for packet in planned["dispatch_packets"]))
            self.assertFalse(planned["execution_authority"])
            self.assertFalse(planned["prompt_user"])
            self.assertEqual(3, planned["capacity"]["operating_count"])
            self.assertEqual("explicit_synthetic_release_defaults",
                             planned["capacity"]["execution_provenance"]["source"])
            self.assertFalse((project / ".agentic/PROJECT_CONFIG.yaml").exists())
            self.assertFalse((project / "OPERATING_CONFIG.yaml").exists())
            refresh = subprocess.run(command + ["--expected-plan-sha256", planned["plan_sha256"],
                                               "--host-writer-capacity", "5"],
                                     cwd=ROOT, env=child_env(), capture_output=True,
                                     text=True, timeout=60)
            self.assertEqual(0, refresh.returncode, refresh.stdout + refresh.stderr)
            observed = loads(refresh.stdout)
            self.assertEqual(3, observed["capacity"]["effective_writer_capacity"])
            self.assertEqual(3, len(observed["dispatch_packets"]))
            self.assertFalse(observed["capacity"]["host_admission_confirmed"])

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
            fixture_root = Path(temporary).resolve()
            project = fixture_root / "project"
            (project / ".agentic").mkdir(parents=True)
            config = loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
            config["execution"]["model_routing"] = fixture_policy()
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

    def test_cli_applies_verifier_bound_owner_reassignment_on_intake_chain(self):
        fixture = ROOT / ".agentic/examples/stream-input.json"
        with tempfile.TemporaryDirectory(prefix="awf-plan-streams-") as temporary:
            fixture_root = Path(temporary).resolve()
            project = fixture_root / "project"
            (project / ".agentic").mkdir(parents=True)
            config = loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
            config["merge_gate"]["trusted_owner_ids"] = [1001]
            config["execution"]["model_routing"] = fixture_policy()
            config_bytes = canonical(config)
            (project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(config_bytes)
            (project / "OPERATING_CONFIG.yaml").write_bytes(
                (ROOT / ".agentic/examples/OPERATING_CONFIG.yaml").read_bytes())
            state = {}
            assignment = assign_intake(
                config,
                {"key": "DEMO-31", "summary": "Synthetic stream work",
                 "description": "", "write_paths": ["src/component.py"],
                 "risk_flags": []},
                state, None, now="2026-09-11T10:00:00Z",
                controller_actor_id="controller-1",
            )
            state_bytes = canonical(state)
            state_path = fixture_root / "controller-state.json"
            state_path.write_bytes(state_bytes)
            reason = "AWF-48 owner lane decision"
            binding = {"ticket_key": "DEMO-31",
                       "current_assignment_id": assignment["assignment_id"]}
            body = (
                f"AWF STREAM REASSIGNMENT ticket=DEMO-31 "
                f"current={assignment['assignment_id']} old=C new=B "
                f"reason_sha256={sha256(reason.encode('utf-8'))}"
            )
            source = {
                "channel": "github_pr_comment", "comment_id": 48,
                "actor_id": 1001, "actor_login": "synthetic-owner",
                "created_at": "2026-09-11T10:00:00Z",
                "updated_at": "2026-09-11T10:00:00Z",
                "raw_body": body, "raw_body_sha256": sha256(body.encode("utf-8")),
                "binding": binding,
            }
            directives = [{
                "ticket_id": "DEMO-31", "who": 1001,
                "when": "2026-09-11T10:00:00Z", "why": reason,
                "old": "C", "new": "B", "owner_source": source,
            }]
            directives_bytes = canonical(directives)
            directives_path = fixture_root / "owner-reassignments.json"
            directives_path.write_bytes(directives_bytes)
            outputs = []
            with patch.object(self.cli, "verify_installed", return_value="a" * 64), \
                    patch("builtins.print", side_effect=lambda value, **kwargs: outputs.append(value)):
                first_code = self.cli.main([
                    "--input", str(fixture),
                    "--expected-input-sha256", sha256(fixture.read_bytes()),
                    "--project-root", str(project), "--allow-synthetic",
                    "--now", "2026-09-11T10:00:00Z",
                ])
                self.assertEqual(0, first_code)
                first = loads(outputs[-1])

                def verify_owner_source(observed, expected):
                    return {
                        "status": "VERIFIED", "actor_id": observed["actor_id"],
                        "source_id": "github_pr_comment:48",
                        "created_at": observed["created_at"],
                        "observed_at": expected["now"],
                        "binding": observed["binding"],
                        "raw_body_sha256": observed["raw_body_sha256"],
                    }

                second_code = self.cli.main([
                    "--input", str(fixture),
                    "--expected-input-sha256", sha256(fixture.read_bytes()),
                    "--project-root", str(project), "--allow-synthetic",
                    "--now", "2026-09-11T10:00:00Z",
                    "--expected-plan-sha256", first["plan_sha256"],
                    "--owner-reassignments", str(directives_path),
                    "--expected-owner-reassignments-sha256", sha256(directives_bytes),
                    "--assignment-state", str(state_path),
                    "--expected-assignment-state-sha256", sha256(state_bytes),
                ], owner_source_verifier=verify_owner_source)
            self.assertEqual(0, second_code)
            report = loads(outputs[-1])
            self.assertEqual("B", report["ticket_streams"]["DEMO-31"])
            event = next(item for item in report["stream_assignment_history"]
                         if item["kind"] == "owner_reassignment")
            self.assertEqual(assignment["assignment_id"], event["intake_assignment_id"])


if __name__ == "__main__":
    unittest.main()
