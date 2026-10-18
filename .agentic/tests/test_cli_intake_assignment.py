"""Real CLI regressions for persisted AWF-48 intake assignments."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
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

from agentic.cli import main as workflow_main
from agentic.intake_assignment import assign_intake, change_assignment
from agentic.lifecycle import definition
from agentic.policy import policy_hash
from agentic.review_tiers import runtime_assignment
from source_only import skip_unless_source_repo
from test_intake_assignment import NOW, owner_source, verify_owner_source
from test_review_policy import NOW as REVIEW_NOW
from test_review_policy import (bind_production_posting_fixture,
                                bind_review_admission, sealed_runtime)


class CliIntakeAssignmentTests(unittest.TestCase):
    def _fixture(self, folder):
        base = Path(folder)
        runtime = sealed_runtime(folder)
        config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
        config["jira"]["enabled"] = False
        config["jira"]["controller_actor_id"] = "controller-1"
        config["merge_gate"]["trusted_owner_ids"] = [1001]
        config_path = base / "project-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        bundle = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text(encoding="utf-8"))
        bundle["contract"]["policy_hash"] = policy_hash(config, definition())
        bundle_path = base / "bundle.json"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        state = {}
        first = assign_intake(
            config,
            {"key": "EX-1", "summary": "Docs", "description": "",
             "write_paths": ["docs/guide.md"], "risk_flags": []},
            state,
            None,
            now=NOW,
            controller_actor_id="controller-1",
        )
        state_path = base / "controller-state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return runtime, config_path, bundle_path, state_path, config, state, first

    def _evaluate(self, runtime, config_path, bundle_path, state_path):
        selected = []

        def capture(config, workflow, bundle, contracts, now, **kwargs):
            assignment = runtime_assignment(config, ticket_key=bundle["contract"]["ticket"])
            selected.append(copy.deepcopy(assignment))
            return {"selected_tier": assignment["tier"], "selected_cap": assignment["round_cap"]}

        stdout = io.StringIO()
        with mock.patch("agentic.cli.evaluate", side_effect=capture), contextlib.redirect_stdout(stdout):
            code = workflow_main([
                "--root", str(runtime), "evaluate", str(bundle_path),
                "--config", str(config_path),
                "--assignment-state", str(state_path),
                "--now", NOW,
            ])
        return code, json.loads(stdout.getvalue()), selected

    def _cap_plan(self, runtime, config_path, state_path, cycles, *, extensions=0,
                  signed_cycles=None, signed_extensions=None):
        signed_cycles = cycles if signed_cycles is None else signed_cycles
        signed_extensions = extensions if signed_extensions is None else signed_extensions
        base = state_path.parent
        disposition_path = base / f"disposition-{cycles}.json"
        verdict_path = base / f"review-verdict-{cycles}.json"
        findings_path = base / f"findings-{cycles}.json"
        disposition_path.write_text(json.dumps({
            "binding": {}, "decision": "PARK", "open_finding_ids": [],
            "successor_ticket": None,
            "cycles": signed_cycles, "cap_extensions": signed_extensions,
        }), encoding="utf-8")
        verdict_path.write_text(json.dumps({"binding": {}}), encoding="utf-8")
        findings_path.write_text("[]\n", encoding="utf-8")
        bundle_path = base / "bundle.json"
        candidate_head = json.loads(
            bundle_path.read_text(encoding="utf-8"))["candidate"]["head_sha"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("agentic.authorization.verify_owner_record", return_value={"status": "VERIFIED"}), \
                mock.patch("agentic.gates.verify_disposition_review_binding", return_value={"status": "VERIFIED"}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = workflow_main([
                "--root", str(runtime), "cap-plan",
                "--disposition", str(disposition_path),
                "--findings", str(findings_path),
                "--cycles", str(cycles),
                "--extensions", str(extensions),
                "--head", candidate_head,
                "--review-verdict", str(verdict_path),
                "--bundle", str(bundle_path),
                "--config", str(config_path),
                "--assignment-state", str(state_path),
                "--ticket", "EX-1",
            ])
        stream = stdout if code == 0 else stderr
        return code, json.loads(stream.getvalue())

    def _tier_change_gate_fixture(self, folder, *, terminal_tier=3):
        from test_review_tiers_195_gates import ReviewTiers195GateTests

        ReviewTiers195GateTests.setUpClass()
        gate_fixture = ReviewTiers195GateTests(
            "test_awf16_195_r3_004_pre_escalation_round_keeps_old_tier_and_binding"
        )
        gate_fixture.setUp()
        self.addCleanup(gate_fixture.doCleanups)
        self.provenance_runtime = gate_fixture.config.get("_awf_runtime")
        gate_fixture._tier_three_candidate()
        gate_fixture._review_history(2, historical_tier=2, terminal_tier=terminal_tier)
        bind_review_admission(gate_fixture.bundle)
        collector_registry = bind_production_posting_fixture(
            gate_fixture.config, gate_fixture.bundle
        )
        gate_fixture._owner_review()
        gate_fixture._refresh_owner_posting_receipt(collector_registry)

        state_config = copy.deepcopy(gate_fixture.config0)
        state_config["jira"]["enabled"] = False
        state = {}
        first = assign_intake(
            state_config,
            {"key": "EX-1", "summary": "Moderate code", "description": "",
             "write_paths": ["src/example.py"], "risk_flags": []},
            state,
            None,
            now="2026-10-10T08:00:00Z",
            controller_actor_id="controller-1",
        )
        changed = change_assignment(
            state_config,
            "EX-1",
            state,
            None,
            requested_tier=3,
            requested_cap=3,
            owner_source=owner_source(
                "EX-1", first["assignment_id"], 3, 3, "Gate scope added"
            ),
            verify_owner_source=verify_owner_source,
            reason="Gate scope added",
            now="2026-10-10T09:00:00Z",
            controller_actor_id="controller-1",
        )

        base = Path(folder)
        runtime = sealed_runtime(folder)
        config_path = base / "project-config.json"
        bundle_path = base / "bundle.json"
        state_path = base / "controller-state.json"
        config_path.write_text(json.dumps(dict(gate_fixture.config)), encoding="utf-8")
        bundle_path.write_text(json.dumps(gate_fixture.bundle), encoding="utf-8")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return (runtime, config_path, bundle_path, state_path, collector_registry,
                first, changed)

    def _gate_evaluate(self, runtime, config_path, bundle_path, state_path,
                       collector_registry):
        from agentic.gates import evaluate

        def evaluate_with_provenance(config, *args, **kwargs):
            config.get("_awf_runtime").update(
                commit_provenance=self.provenance_runtime["commit_provenance"])
            kwargs["repository"] = self.provenance_runtime["repository"]
            return evaluate(config, *args, **kwargs)

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("agentic.cli.evaluate", side_effect=evaluate_with_provenance), \
                mock.patch(
                "agentic.posting_collector.registry_from_host_config",
                return_value=collector_registry), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = workflow_main([
                "--root", str(runtime), "evaluate", str(bundle_path),
                "--config", str(config_path),
                "--assignment-state", str(state_path),
                "--posting-collector-host-config", str(
                    state_path.parent / "posting-host.json"
                ),
                "--now", REVIEW_NOW,
            ])
        stream = stdout if code == 0 else stderr
        return code, json.loads(stream.getvalue())

    def test_evaluate_selects_recorded_tier_and_cap_instead_of_legacy_config(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, bundle_path, state_path, _, _, _ = self._fixture(folder)
            code, output, selected = self._evaluate(runtime, config_path, bundle_path, state_path)
        self.assertEqual(0, code)
        self.assertEqual({"selected_tier": 1, "selected_cap": 1}, output)
        self.assertEqual((1, 1), (selected[0]["tier"], selected[0]["round_cap"]))

    def test_cap_plan_uses_recorded_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, _, state_path, _, _, _ = self._fixture(folder)
            code, output = self._cap_plan(runtime, config_path, state_path, cycles=1)
        self.assertEqual(0, code)
        self.assertEqual("CAP_PARK", output["event"])

    def test_cap_plan_rejects_runtime_counters_different_from_signed_disposition(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, _, state_path, _, _, _ = self._fixture(folder)
            code, output = self._cap_plan(
                runtime, config_path, state_path, cycles=4, extensions=1,
                signed_cycles=3, signed_extensions=0,
            )
        self.assertEqual(2, code)
        self.assertIn("signed disposition counters", output["reason"])

    def test_owner_change_is_selected_on_next_cli_invocation(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, bundle_path, state_path, config, state, first = self._fixture(folder)
            changed = change_assignment(
                config,
                "EX-1",
                state,
                None,
                requested_tier=2,
                requested_cap=2,
                owner_source=owner_source("EX-1", first["assignment_id"], 2, 2, "Code scope added"),
                verify_owner_source=verify_owner_source,
                reason="Code scope added",
                now="2026-10-10T09:00:00Z",
                controller_actor_id="controller-1",
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            code, output, selected = self._evaluate(runtime, config_path, bundle_path, state_path)
        self.assertEqual(0, code)
        self.assertEqual(changed["assignment_id"], selected[0]["assignment_id"])
        self.assertEqual({"selected_tier": 2, "selected_cap": 2}, output)

    def test_fresh_cap_plan_reloads_current_persisted_assignment(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, _, state_path, config, state, first = self._fixture(folder)
            first_code, first_output = self._cap_plan(runtime, config_path, state_path, cycles=1)
            self.assertEqual(0, first_code)
            self.assertEqual("CAP_PARK", first_output["event"])
            change_assignment(
                config,
                "EX-1",
                state,
                None,
                requested_tier=2,
                requested_cap=2,
                owner_source=owner_source("EX-1", first["assignment_id"], 2, 2, "Code scope added"),
                verify_owner_source=verify_owner_source,
                reason="Code scope added",
                now="2026-10-10T09:00:00Z",
                controller_actor_id="controller-1",
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            rejected_code, rejected = self._cap_plan(runtime, config_path, state_path, cycles=1)
            final_code, final = self._cap_plan(runtime, config_path, state_path, cycles=2)
        self.assertEqual(2, rejected_code)
        self.assertIn("before the amendment cap was reached", rejected["reason"])
        self.assertEqual(0, final_code)
        self.assertEqual("CAP_PARK", final["event"])

    @skip_unless_source_repo(
        "scripts/validate_archive.py exists only in the AWF source repository",
        "scripts/validate_archive.py",
    )
    def test_archive_evaluate_command_supplies_assignment_state_and_reaches_evaluation(self):
        spec = importlib.util.spec_from_file_location(
            "validate_archive_assignment", ROOT / "scripts/validate_archive.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            runtime = sealed_runtime(folder)
            state_path = Path(folder) / "controller-state.json"
            config_path = ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"
            bundle_path = ROOT / ".agentic/examples/evidence-bundle.json"
            module.write_evaluation_assignment_state(ROOT, state_path, NOW)
            command = module.evaluation_command(
                ".agentic/scripts/workflow.py", bundle_path, config_path, state_path, NOW)
            reached = []

            def capture(*args, **kwargs):
                reached.append(True)
                return {"conclusion": "READY_FOR_OWNER_AUTHORIZATION",
                        "execution_authority": False}

            with mock.patch("agentic.cli.evaluate", side_effect=capture), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = workflow_main(["--root", str(runtime), *map(str, command[1:])])
        self.assertEqual(0, code)
        self.assertEqual([True], reached)
        self.assertEqual(str(state_path), str(command[command.index("--assignment-state") + 1]))

    def test_evaluate_without_assignment_state_still_fails_argument_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, config_path, bundle_path, _, _, _, _ = self._fixture(folder)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                workflow_main([
                    "--root", str(runtime), "evaluate", str(bundle_path),
                    "--config", str(config_path), "--now", NOW,
                ])
        self.assertEqual(2, raised.exception.code)

    def test_awf48_r5_044_001_cli_accepts_historical_t2_then_terminal_t3(self):
        with tempfile.TemporaryDirectory() as folder:
            (runtime, config_path, bundle_path, state_path, collector_registry,
             first, changed) = (
                self._tier_change_gate_fixture(folder)
            )
            code, output = self._gate_evaluate(
                runtime, config_path, bundle_path, state_path, collector_registry
            )
        self.assertEqual(2, first["tier"])
        self.assertEqual(3, changed["tier"])
        self.assertEqual(0, code, output)
        self.assertEqual(3, output["risk_tier"])
        self.assertEqual("PASS", output["gates"]["critic_current_tuple"]["result"])
        self.assertEqual("READY_FOR_OWNER_AUTHORIZATION", output["conclusion"])

    def test_awf48_r5_044_001_cli_refuses_terminal_tier_assignment_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            (runtime, config_path, bundle_path, state_path, collector_registry,
             first, changed) = (
                self._tier_change_gate_fixture(folder, terminal_tier=2)
            )
            code, output = self._gate_evaluate(
                runtime, config_path, bundle_path, state_path, collector_registry
            )
        self.assertEqual(2, first["tier"])
        self.assertEqual(3, changed["tier"])
        self.assertEqual(2, code)
        self.assertIn("tier", output["reason"].lower())


if __name__ == "__main__":
    unittest.main()
