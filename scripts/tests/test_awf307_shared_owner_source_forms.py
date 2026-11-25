"""AWF-307 source configuration forms are checked outside installed tests."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

import test_awf307_o1c_regressions as regressions
from agentic import ValidationError
from agentic.canonical import load

intake_fixture = regressions.intake_fixture

KEY = "shared_owner_automation_actor_id"


class SharedOwnerSourceFormsTests(unittest.TestCase):
    def test_shipped_and_generated_forms_default_to_null(self):
        for relative in ("PROJECT_CONFIG.yaml", "templates/source-config/PROJECT_CONFIG.yaml"):
            with self.subTest(path=relative):
                gate = load(ROOT / ".agentic" / relative)["merge_gate"]
                self.assertIn(KEY, gate)
                self.assertIsNone(gate[KEY])


class BootstrapAssignmentChangeTests(unittest.TestCase):
    """The bootstrap entry point ships only in the source repository."""
    setUp = regressions.DistinctOwnerCompatibility.setUp
    hidden_policy_case = regressions.DistinctOwnerCompatibility.hidden_policy_case
    intake_inputs = regressions.DistinctOwnerCompatibility.intake_inputs

    def run_bootstrap(self, config, state, folder, bootstrap, **requested):
        import importlib.util
        specification = importlib.util.spec_from_file_location(
            "bootstrap_project_hidden_policy", ROOT / "scripts/bootstrap_project.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        (bootstrap / ".agentic").mkdir(exist_ok=True)
        config.pop("_awf_runtime", None)
        (bootstrap / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(config), encoding="utf-8")
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return module._run_assignment_path(SimpleNamespace(**{"project_start_inventory": None,
            "ticket_intake": None, "assignment_change": None, **requested},
            assignment_state=folder / "state.json", controller_actor_id="fixture-controller",
            assignment_now=intake_fixture.NOW, dest=bootstrap, recover=False, dry_run=False),
            None, intake_fixture.verify_owner_source)

    def intake_change(self, setting, *, bootstrap, identity=1002):
        config, state, reason, source = self.intake_inputs(setting)
        config["github"]["expected_actor_id"] = identity
        folder = self.root / ("bootstrap-" + setting + str(identity) + bootstrap.name)
        folder.mkdir()
        (folder / "change.json").write_text(json.dumps({"ticket_key": "AWF-100",
            "requested_tier": 3, "requested_cap": 3, "reason": reason, "owner_source": source}),
            encoding="utf-8")
        return self.run_bootstrap(config, state, folder, bootstrap,
                                  assignment_change=folder / "change.json")

    def test_bootstrap_assignment_change_hidden_opt_out_refuses(self):
        case = self.hidden_policy_case()
        ordinary = self.root / "ordinary"
        ordinary.mkdir()
        for setting in ("null", "absent"):
            with self.subTest(setting=setting), case.policy_reads():
                with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                    self.intake_change(setting, bootstrap=case.project)
            with self.subTest(setting=setting, hidden=False):
                self.assertEqual(3, self.intake_change(setting, bootstrap=ordinary)["AWF-100"]["tier"])

    def test_bootstrap_assignment_change_changed_identity_cannot_hide_the_opt_out(self):
        case = self.hidden_policy_case()
        ordinary = self.root / "ordinary"
        ordinary.mkdir()
        for setting in ("null", "absent"):
            for identity in (1003, None):
                with self.subTest(setting=setting, identity=identity), case.policy_reads():
                    with self.assertRaisesRegex(ValidationError, "accepted policy digest changed"):
                        self.intake_change(setting, bootstrap=case.project, identity=identity)
                with self.subTest(setting=setting, identity=identity, hidden=False):
                    self.assertEqual(3, self.intake_change(
                        setting, bootstrap=ordinary, identity=identity)["AWF-100"]["tier"])

    def test_bootstrap_retained_owner_decision_cannot_hide_the_opt_out(self):
        case = self.hidden_policy_case()
        ordinary = self.root / "ordinary"
        ordinary.mkdir()
        for setting in ("null", "absent"):
            for audited in (True, False):
                config, state, reason, source = self.intake_inputs(setting)
                config["github"]["expected_actor_id"] = 1003
                if audited:
                    regressions.intake_assignment.change_assignment(config, "AWF-100", state, None,
                        requested_tier=3, requested_cap=3, owner_source=source,
                        verify_owner_source=intake_fixture.verify_owner_source, reason=reason,
                        now=intake_fixture.NOW, controller_actor_id="fixture-controller")
                for root, hidden in ((case.project, True), (ordinary, False)):
                    folder = self.root / f"retained-{setting}-{audited}-{hidden}"
                    folder.mkdir()
                    (folder / "intake.json").write_text(
                        json.dumps({**intake_fixture.ticket(), "key": "AWF-101"}), encoding="utf-8")

                    def intake():
                        return self.run_bootstrap(config, state, folder, root,
                                                  ticket_intake=folder / "intake.json")
                    with self.subTest(setting=setting, audited=audited, hidden=hidden):
                        if hidden and audited:
                            with case.policy_reads(), self.assertRaisesRegex(
                                    ValidationError, "accepted policy digest changed"):
                                intake()
                        else:
                            # Intake without a retained owner decision uses no owner authority.
                            self.assertIn("AWF-101", intake())


if __name__ == "__main__":
    unittest.main()
