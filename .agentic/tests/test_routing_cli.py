"""Real local Python process coverage for the routing CLI; no live model calls."""
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentic.model_routing import MODELS, default_policy
from agentic.canonical import canonical
from agentic.operating import set_operating
from test_operating_integration import configuration, governance, outcome


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
        arguments = [command, "--legacy-without-operating", "--config", self.root / "config.json", "--request", self.root / "request.json",
                     "--capabilities", self.root / "capabilities.json"]
        if command == "reserve":
            arguments += ["--project-root", self.project, "--ledger", self.root / "ledger.sqlite"]
        return self.invoke(*arguments, expected=expected)

    def ledger_command(self, command, *arguments, expected=0):
        return self.invoke(command, "--config", self.root / "config.json", "--project-root", self.project,
                           "--ledger", self.root / "ledger.sqlite", *arguments, expected=expected)

    def test_real_cli_routing_and_defaults(self):
        self.assertEqual(self.route()["model"], MODELS[1])
        defaults = self.invoke("defaults")
        self.assertEqual(defaults["reconciliation"]["enabled"], False)
        self.assertEqual(1000000, defaults["budgets"]["max_tokens_per_ticket"])
        self.assertEqual(5000000, defaults["budgets"]["max_tokens_per_project_day"])

    def test_token_only_cli_reserves_and_settles_with_null_cost(self):
        reserved = self.route("reserve")
        self.assertIsNone(reserved["reservation_cost_microusd"])
        observed = {"actual_model": reserved["model"], "actual_reasoning_effort": reserved["reasoning_effort"],
                    "actual_context_id": "cli-context", "actual_tokens": 321, "actual_cost_microusd": None,
                    "success": True, "validation_passed": True, "independent_review_passed": True,
                    "reviewer_context_id": "reviewer-context", "escaped_defect": False}
        path = self.document("outcome.json", observed)
        settled = self.ledger_command("settle", "--run-id", reserved["run_id"], "--outcome", path)
        self.assertEqual("settled", settled["status"])
        with closing(sqlite3.connect(self.root / "ledger.sqlite")) as connection:
            row = connection.execute("SELECT reserved_cost,actual_tokens,actual_cost FROM model_runs WHERE run_id=?",
                                     (reserved["run_id"],)).fetchone()
        self.assertEqual((None, 321, None), row)

    def test_cli_monetary_cap_keeps_exact_verified_cost_refusal(self):
        self.policy["budgets"]["max_cost_microusd_per_ticket"] = 100
        self.document("config.json", self.config)
        result = self.route("reserve", expected=2)
        self.assertEqual("cost ceiling is configured but a verified hard cost reservation is unavailable", result["reason"])

    def project_route(self, command="suggest", expected=0, *extra):
        args = [command, "--config", self.project / ".agentic/PROJECT_CONFIG.yaml", "--project-root", self.project,
                "--request", self.root / "request.json", "--capabilities", self.root / "capabilities.json"]
        if command == "reserve":
            args += ["--ledger", self.root / "ledger.sqlite"]
        return self.invoke(*args, *extra, expected=expected)

    def prepare_operating_project(self):
        governing = governance()
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(governing))
        (self.project / "OPERATING_CONFIG.yaml").write_bytes(canonical(configuration()))
        self.request["stream"] = "A"
        self.document("request.json", self.request)
        return governing

    def test_actual_cli_requires_operating_file_and_refuses_full_project_legacy_bypass(self):
        self.prepare_operating_project()
        self.assertIsNotNone(self.project_route()["operating_hash"])
        (self.project / "OPERATING_CONFIG.yaml").unlink()
        missing = self.project_route(expected=2)
        self.assertIn("OPERATING_CONFIG.yaml is missing", missing["reason"])
        bypass = self.project_route("suggest", 2, "--legacy-without-operating")
        self.assertIn("cannot bypass", bypass["reason"])

    def test_actual_reservation_binds_current_controller_snapshot_and_settlement_keeps_it(self):
        governing = self.prepare_operating_project()
        first = self.project_route("reserve")
        set_operating(self.project, governing, "Synthetic test: use Sol/high for A", ["A.worker=gpt-5.6-sol/high"])
        proposal = self.project_route()
        self.assertNotEqual(first["operating_hash"], proposal["operating_hash"])
        self.assertEqual(first["policy_sha256"], proposal["policy_sha256"])
        observation = outcome(first, actual_context_id="synthetic-context")
        path = self.document("outcome.json", observation)
        settled = self.invoke("settle", "--config", self.project / ".agentic/PROJECT_CONFIG.yaml", "--project-root", self.project,
                              "--ledger", self.root / "ledger.sqlite", "--run-id", first["run_id"], "--outcome", path)
        self.assertEqual(settled["operating_hash"], first["operating_hash"])

    def test_actual_cli_rejects_pending_operating_transaction_before_reservation(self):
        self.prepare_operating_project()
        state = self.project / ".agentic-state/operating"
        state.mkdir(parents=True)
        (state / "pending.json").write_bytes(b'{}')
        report = self.project_route("reserve", expected=2)
        self.assertIn("Pending operating transaction", report["reason"])
        with closing(sqlite3.connect(self.root / "ledger.sqlite")) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM model_runs").fetchone()[0], 0)

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

    def test_default_reconciled_retry_limit_returns_structured_admission_diagnostics(self):
        # Deliberately omit the new optional field: older reviewed policy still
        # gets a finite allowance without being rewritten on disk.
        self.policy["reconciliation"] = {"enabled": True, "authorized_operator_ids": ["test-operator"]}
        config_path = self.document("config.json", self.config)
        original_bytes = config_path.read_bytes()
        for index in range(3):
            run = self.route("reserve")
            self.assertFalse(run["escalated"])
            observation = {"reconciliation_id": "hang-" + str(index), "operator_id": "test-operator",
                           "authorization_ref": "grant-1", "reason": "Recorded stopped host and remediation assertions",
                           "evidence_ref": "exit-1", "remediation_ref": "check-1", "host_stopped": True, "remediation_verified": True}
            path = self.document("observation.json", observation)
            closed = self.ledger_command("reconcile", "--run-id", run["run_id"], "--observation", path)
            self.assertEqual(closed["charged_tokens"], 1000)
        blocked = self.route("reserve", expected=2)
        self.assertEqual(blocked["code"], "RECONCILED_INCIDENT_RETRY_LIMIT")
        self.assertEqual((blocked["reconciled_incidents"], blocked["retry_limit"]), (3, 2))
        self.assertEqual(config_path.read_bytes(), original_bytes)
        self.policy["adaptive"]["min_reviewed_samples"] += 1
        self.document("config.json", self.config)
        self.assertEqual(self.route("reserve", expected=2)["code"], blocked["code"])
        self.assertEqual(self.ledger_command("reconcile", "--run-id", run["run_id"], "--observation", path), closed)

    def test_routing_cli_runs_from_installed_directory_layout(self):
        installed = self.root / "installed"
        shutil.copytree(ROOT / ".agentic/lib", installed / ".agentic/lib")
        installed_script = installed / ".agentic/scripts/route_model.py"
        installed_script.parent.mkdir()
        shutil.copy2(CLI, installed_script)
        with patch(__name__ + ".CLI", installed_script):
            policy = self.invoke("defaults")
            self.assertEqual(policy["reconciliation"]["max_reconciled_incident_retries_per_ticket"], 2)
            self.assertEqual(self.route()["model"], MODELS[1])


if __name__ == "__main__":
    unittest.main()
