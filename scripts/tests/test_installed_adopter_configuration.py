"""AWF-305: the installed self-test must not read the adopter's own configuration.

An adopted project keeps its own PROJECT_CONFIG.yaml, routing.yaml,
OPERATING_CONFIG.yaml and project scripts. Installed tests that once treated
those live files as fixtures failed on a real 1.9.6 upgrade (78 failures, 6
errors) while a clean install passed. This regression installs the release
manifest-only, gives the install realistic non-default adopter values, and
runs every installed test that read the live configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402
from agentic.contracts import Contracts  # noqa: E402
from agentic.installer import install  # noqa: E402
from agentic.launch_surfaces import validate_repository_launch_surfaces  # noqa: E402
from agentic.model_routing import load_routing  # noqa: E402

# Every installed module or case that read ROOT's live configuration or tree.
AFFECTED = (
    "test_merge_train_controller",
    "test_merge_train_closeout",
    "test_awf243_review_regressions",
    "test_awf244_persistence",
    "test_plan_streams",
    "test_awf107_routing_config",
    "test_approved_models_awf82",
    "test_launch_surfaces",
    "test_upgrade_source_independence",
    "test_controller_switches_195",
    "test_awf154_large_file_allowances",
    "test_planning",
    "test_streams",
    "test_intake_assignment.RuleDefaults.test_multi_file_tools_change_is_not_tier_one",
    "test_intake_assignment.ConfigDefaultsAndOverrides"
    ".test_existing_project_schema_accepts_review_tiers_and_rejects_invalid_cap",
    "test_continuous_controller.ReviewBatchingCliTests"
    ".test_cycle_forwards_injected_review_ports_without_requiring_a_transport",
    "test_shipped_defaults",
)
ADOPTER_MODELS = ["gpt-6-luna", "gpt-6.1-sol"]
SWITCHES = ("dispatch_enabled", "auto_dispatch", "auto_request_critic",
            "auto_resume_amendments", "auto_transition_jira")
SUITE_TIMEOUT_SECONDS = 1800


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def adopt_non_default_configuration(project):
    """Give an install the kind of preserved values a real adopter carries."""
    config_path = project / ".agentic/PROJECT_CONFIG.yaml"
    config = _read(config_path)
    config["jira"]["status_map"].update(in_review="Code Review", done="Resolved")
    for role in config["execution"]["roles"].values():
        role["approved_model_ids"] = list(ADOPTER_MODELS)
    config["controller"].update(dict.fromkeys(SWITCHES, True))
    _write(config_path, config)

    routing_path = project / ".agentic/routing.yaml"
    routing = _read(routing_path)
    routing["catalog"] = {model: routing["catalog"][model] for model in ADOPTER_MODELS}
    _write(routing_path, routing)

    operating_path = project / "OPERATING_CONFIG.yaml"
    operating = _read(operating_path)
    operating.pop("operating_calendar")  # A legacy upgrade retains no calendar.
    _write(operating_path, operating)

    scripts = project / "scripts"
    scripts.mkdir()
    (scripts / "benchmark_project.py").write_text("print('benchmark')\n", encoding="utf-8")
    (scripts / "project_smoke.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    _write(project / "PROJECT_LAUNCH_SURFACES.json", {"version": 1, "scripts": [{
        "path": "scripts/project_smoke.sh", "mechanism": "sanitized_process_start",
        "entry_point": "scripts/benchmark_project.py"}]})
    return config


@unittest.skipUnless((ROOT / "MANIFEST.json").is_file(), "source repository only")
class InstalledAdopterConfigurationTests(unittest.TestCase):
    def test_installed_tests_pass_with_non_default_adopter_configuration(self):
        env = child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        with tempfile.TemporaryDirectory(prefix="awf-adopter-config-") as raw:
            project = Path(raw) / "project"
            project.mkdir()
            pin = hashlib.sha256((ROOT / "MANIFEST.json").read_bytes()).hexdigest()
            install(ROOT, project, pin, mode="install", discover=False)
            config = adopt_non_default_configuration(project)
            subprocess.run(["git", "-C", str(project), "init", "-q", "-b", "main"],
                           check=True, capture_output=True, env=env, timeout=180)

            # The fixture is a valid adopter, not a broken one: the schema
            # accepts it, routing loads, and the declared scripts validate.
            Contracts(project / ".agentic/schemas").validate("project-config", config)
            self.assertEqual(ADOPTER_MODELS, list(load_routing(project / ".agentic/routing.yaml")["catalog"]))
            self.assertNotIn("operating_calendar", _read(project / "OPERATING_CONFIG.yaml"))
            surfaces = validate_repository_launch_surfaces(project)
            self.assertEqual(1, surfaces["project_non_python_scripts"])

            done = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", *AFFECTED],
                cwd=project / ".agentic/tests", env=env, capture_output=True, text=True,
                timeout=SUITE_TIMEOUT_SECONDS)
            failed = [line for line in done.stderr.splitlines()
                      if line.startswith(("FAIL:", "ERROR:"))]
            self.assertEqual([], failed, done.stderr[-6000:])
            self.assertEqual(0, done.returncode, done.stderr[-6000:])
            self.assertRegex(done.stderr, r"\nOK(?: \(|\n|$)")
            # The tests passed against shipped defaults that differ from the adopter's.
            shipped = _read(project / ".agentic/defaults/PROJECT_CONFIG.yaml")
            self.assertEqual("Done", shipped["jira"]["status_map"]["done"])
            self.assertNotEqual(shipped["jira"]["status_map"], config["jira"]["status_map"])


if __name__ == "__main__":
    unittest.main()
