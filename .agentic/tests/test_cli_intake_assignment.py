"""Real CLI regressions for persisted AWF-48 intake assignments."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.cli import main as workflow_main
from agentic.intake_assignment import assign_intake, change_assignment
from agentic.review_tiers import runtime_assignment
from test_intake_assignment import NOW, owner_source, verify_owner_source
from test_review_policy import sealed_runtime


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

    def _cap_plan(self, runtime, config_path, state_path, cycles):
        base = state_path.parent
        disposition_path = base / f"disposition-{cycles}.json"
        findings_path = base / f"findings-{cycles}.json"
        disposition_path.write_text(json.dumps({
            "binding": {}, "decision": "PARK", "open_finding_ids": [],
            "successor_ticket": None,
        }), encoding="utf-8")
        findings_path.write_text("[]\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("agentic.authorization.verify_owner_record", return_value={"status": "VERIFIED"}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = workflow_main([
                "--root", str(runtime), "cap-plan",
                "--disposition", str(disposition_path),
                "--findings", str(findings_path),
                "--cycles", str(cycles),
                "--head", "a" * 40,
                "--config", str(config_path),
                "--assignment-state", str(state_path),
                "--ticket", "EX-1",
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


if __name__ == "__main__":
    unittest.main()
