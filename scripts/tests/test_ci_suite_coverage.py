"""Keep pull-request CI aligned with release validation coverage."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
PUBLISH_RELEASE = ROOT / "scripts/publish_release.py"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def publish_release_suites():
    tree = ast.parse(PUBLISH_RELEASE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "default_validations"
    )
    return {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("global/")
        and node.value.endswith("/tests")
    }


def discovered_suites(job):
    suites = set()
    for step in job.get("steps", []):
        command = step.get("run", "")
        suites.update(re.findall(r"-m\s+unittest\s+discover\s+-s\s+([^\s'\"]+)", command))
    return suites


class CiSuiteCoverageTests(unittest.TestCase):
    def test_pull_request_matrix_covers_every_publish_release_portable_suite(self):
        document = workflow()
        triggers = document.get("on", document.get(True))
        self.assertIn("pull_request", triggers)

        checks = document["jobs"]["checks"]
        self.assertEqual(
            {"3.11", "3.13"},
            {str(version) for version in checks["strategy"]["matrix"]["python-version"]},
        )
        expected = publish_release_suites()
        self.assertTrue(expected, "default_validations must declare at least one portable suite")
        self.assertEqual(expected, expected & discovered_suites(checks))
        for step in checks["steps"]:
            if expected & discovered_suites({"steps": [step]}):
                self.assertFalse(step.get("continue-on-error", False), step.get("name"))

    def test_windows_installed_runtime_job_is_single_and_release_gated(self):
        document = workflow()
        triggers = document.get("on", document.get(True))
        self.assertEqual(["main"], triggers["push"]["branches"])

        jobs = document["jobs"]
        harness_jobs = [
            (job_id, job)
            for job_id, job in jobs.items()
            if any(
                "scripts/tests/test_installed_self_test.py" in step.get("run", "")
                for step in job.get("steps", [])
            )
        ]
        self.assertEqual(1, len(harness_jobs))
        job_id, job = harness_jobs[0]
        self.assertEqual("installed-runtime-self-test", job_id)
        self.assertEqual("Windows installed-runtime self-test", job["name"])
        self.assertEqual("windows-latest", job["runs-on"])
        self.assertEqual(
            "github.event_name == 'push' || "
            "(github.event_name == 'pull_request' && github.base_ref == 'main' && "
            "startsWith(github.head_ref, 'release/'))",
            job["if"],
        )

        actions = [step.get("uses") for step in job["steps"] if "uses" in step]
        self.assertIn("actions/checkout@v4", actions)
        self.assertIn("actions/setup-python@v5", actions)
        harness_step = next(
            step for step in job["steps"]
            if "scripts/tests/test_installed_self_test.py" in step.get("run", "")
        )
        self.assertIn("python -B scripts/tests/test_installed_self_test.py", harness_step["run"])
        self.assertFalse(harness_step.get("continue-on-error", False))


if __name__ == "__main__":
    unittest.main()
