"""Real local Python process coverage for the routing CLI; no live model calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentic.model_routing import MODELS, default_policy


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / ".agentic/scripts/route_model.py"


class RoutingCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-routing-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.policy = default_policy()
        self.config = {"project": {"id": "cli-project"}, "execution": {"model_routing": self.policy}}
        self.request = {"ticket_id": "LOCAL-1", "role": "worker", "agent_id": "worker-1", "context_id": None,
                        "phase": "implementation", "task_class": "cli-regression", "complexity": "medium", "risk": "low",
                        "uncertainty": "low", "verification": "strong", "risk_flags": [], "reservation_tokens": 1000}
        self.capabilities = {"models": {model: data["reasoning_efforts"] for model, data in self.policy["models"].items()}}
        self.document("config.json", self.config)
        self.document("request.json", self.request)
        self.document("capabilities.json", self.capabilities)

    def document(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def invoke(self, *arguments, expected=0):
        process = subprocess.run([sys.executable, "-B", str(CLI), *map(str, arguments)], cwd=self.project,
                                 capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "", process.stderr)
        self.assertNotIn("Traceback", process.stdout)
        result = json.loads(process.stdout)
        if expected == 2:
            self.assertIn(result["status"], ("blocked", "unavailable", "quarantined"))
        return result

    def route(self, command="suggest", expected=0):
        arguments = [command, "--config", self.root / "config.json", "--request", self.root / "request.json",
                     "--capabilities", self.root / "capabilities.json"]
        if command == "reserve":
            arguments += ["--project-root", self.project, "--ledger", self.root / "ledger.sqlite"]
        return self.invoke(*arguments, expected=expected)

    def ledger_command(self, command, *arguments, expected=0):
        return self.invoke(command, "--config", self.root / "config.json", "--project-root", self.project,
                           "--ledger", self.root / "ledger.sqlite", *arguments, expected=expected)

    def test_real_cli_routing_and_defaults(self):
        self.assertEqual(self.route()["model"], MODELS[1])
        self.assertEqual(self.invoke("defaults")["reconciliation"]["enabled"], False)

    def test_invalid_argument_shapes_are_json_errors(self):
        for args in ((), ("not-a-command",), ("suggest", "--unknown"), ("suggest", "--config")):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(*args, expected=2)["error_type"], "ValidationError")

    def test_malformed_project_and_role_configs_do_not_traceback(self):
        for project in (None, [], "not-an-object", {"id": None}, {"id": []}, {"id": " "}):
            self.config["project"] = project
            self.document("config.json", self.config)
            with self.subTest(project=project):
                result = self.route(expected=2)
                self.assertIn("project", result["reason"])
                self.assertEqual(result["error_type"], "ValidationError")
        self.config["project"] = {"id": "cli-project"}
        for roles in (None, [], "wrong", {"worker": []}, {"worker": None}, {"worker": "wrong"}):
            self.config["execution"]["roles"] = roles
            self.document("config.json", self.config)
            with self.subTest(roles=roles):
                self.assertIn("execution.roles", self.route(expected=2)["reason"])

    def test_malformed_top_level_and_request_documents_are_json_errors(self):
        for document in (None, [], "wrong", 5, {"execution": []}):
            self.document("config.json", document)
            with self.subTest(config=document):
                self.assertEqual(self.route(expected=2)["error_type"], "ValidationError")
        self.document("config.json", self.config)
        for request in (None, [], "wrong", {**self.request, "previous_route": {}}, {**self.request, "last_failure_kind": []}):
            self.document("request.json", request)
            with self.subTest(request=request):
                self.assertEqual(self.route(expected=2)["error_type"], "ValidationError")
        (self.root / "request.json").write_text('{"broken":', encoding="utf-8")
        self.assertEqual(self.route(expected=2)["error_type"], "ValidationError")

    def test_escalation_ceiling_is_respected_in_real_cli(self):
        self.request.update(previous_route={"model": MODELS[1], "reasoning_effort": "xhigh"}, last_failure_kind="reasoning", risk="high")
        self.document("request.json", self.request)
        self.assertIn("ceiling", self.route(expected=2)["reason"])
        self.policy["escalation"]["effort_ceiling"] = "xhigh"
        self.document("config.json", self.config)
        result = self.route()
        self.assertEqual((result["model"], result["reasoning_effort"]), (MODELS[3], "xhigh"))

    def test_real_cli_reserve_reconcile_history_and_idempotence(self):
        self.policy["reconciliation"] = {"enabled": True, "authorized_operator_ids": ["test-operator"]}
        self.document("config.json", self.config)
        run = self.route("reserve")
        observation = {"reconciliation_id": "op-1", "operator_id": "test-operator", "authorization_ref": "grant-1",
                       "reason": "Observed process exit and repaired host", "evidence_ref": "exit-1", "remediation_ref": "check-1",
                       "host_stopped": True, "remediation_verified": True}
        path = self.document("observation.json", observation)
        result = self.ledger_command("reconcile", "--run-id", run["run_id"], "--observation", path)
        self.assertFalse(result["budgets_refunded"])
        self.assertEqual(result["charged_tokens"], 1000)
        self.assertEqual(self.ledger_command("reconcile", "--run-id", run["run_id"], "--observation", path), result)
        history = self.ledger_command("reconciliation-history")
        self.assertEqual(len(history["reconciliations"]), 1)
        self.assertEqual(history["reconciliations"][0]["observation"]["operator_id"], "test-operator")
        observation["reason"] = "Changed claim"
        self.document("observation.json", observation)
        self.assertIn("conflicts", self.ledger_command("reconcile", "--run-id", run["run_id"], "--observation", path, expected=2)["reason"])
        retry = self.route("reserve")
        self.assertEqual(retry["model"], run["model"])
        self.assertFalse(retry["escalated"])

    def test_reconciliation_cannot_be_enabled_by_an_observation(self):
        run = self.route("reserve")
        path = self.document("observation.json", {"reconciliation_id": "op-1", "operator_id": "anyone",
                              "authorization_ref": "grant", "reason": "claim", "evidence_ref": "evidence", "remediation_ref": "fix",
                              "host_stopped": True, "remediation_verified": True})
        self.assertIn("disabled", self.ledger_command("reconcile", "--run-id", run["run_id"], "--observation", path, expected=2)["reason"])
        self.assertIn("outstanding", self.route("reserve", expected=2)["reason"])


if __name__ == "__main__":
    unittest.main()
