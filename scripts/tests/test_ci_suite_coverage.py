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


def steps_with(job, fragment):
    return [
        step
        for step in job.get("steps", [])
        if fragment in step.get("run", "").replace("\\", "/")
    ]


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
            if steps_with(job, ".agentic/scripts/self_test.py")
        ]
        self.assertEqual(1, len(harness_jobs))
        job_id, job = harness_jobs[0]
        self.assertEqual("installed-runtime-self-test", job_id)
        self.assertEqual("Windows installed-runtime self-test", job["name"])
        self.assertEqual("windows-latest", job["runs-on"])
        self.assertEqual("3.13.3", str(next(
            step["with"]["python-version"]
            for step in job["steps"]
            if step.get("uses") == "actions/setup-python@v5"
        )))
        self.assertEqual(
            "github.event_name == 'push' || "
            "(github.event_name == 'pull_request' && github.base_ref == 'main' && "
            "startsWith(github.head_ref, 'release/'))",
            job["if"],
        )

        actions = [step.get("uses") for step in job["steps"] if "uses" in step]
        self.assertIn("actions/checkout@v4", actions)
        self.assertIn("actions/setup-python@v5", actions)
        self.assertEqual("Initialize installed-runtime evidence paths", job["steps"][0]["name"])
        self.assertNotIn("self-test.json", job["steps"][0]["run"])
        self.assertIn("installer.log", job["steps"][0]["run"])
        self.assertEqual(1, len(steps_with(job, "scripts/build_release.py --output")))
        self.assertEqual(1, len(steps_with(job, "git -C $project init -q -b main")))
        self.assertEqual(1, len(steps_with(job, "git -C $project rev-parse --verify --quiet HEAD")))
        self.assertEqual(1, len(steps_with(job, "scripts/bootstrap_project.py")))
        self.assertEqual(1, len(steps_with(job, ".agentic/scripts/self_test.py")))
        self.assertIn(
            "& $installedPython -B $selfTest --checks-only --report $reportPath",
            steps_with(job, ".agentic/scripts/self_test.py")[0]["run"],
        )

        uploads = [step for step in job["steps"] if step.get("uses") == "actions/upload-artifact@v4"]
        self.assertEqual(1, len(uploads))
        self.assertEqual("always()", uploads[0].get("if"))
        artifact_paths = uploads[0]["with"]["path"]
        self.assertIn("self-test.json", artifact_paths)
        self.assertIn("installer.log", artifact_paths)

    def test_windows_self_test_report_is_fresh_and_fallback_never_overwrites(self):
        steps = workflow()["jobs"]["installed-runtime-self-test"]["steps"]
        run_index, run_step = next(
            (index, step)
            for index, step in enumerate(steps)
            if ".agentic/scripts/self_test.py" in step.get("run", "").replace("\\", "/")
        )
        fallback_index, fallback_step = next(
            (index, step)
            for index, step in enumerate(steps)
            if step.get("name") == "Preserve self-test evidence after an earlier failure"
        )
        upload_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("uses") == "actions/upload-artifact@v4"
        )

        before_self_test = "\n".join(step.get("run", "") for step in steps[:run_index])
        self.assertNotIn("self-test.json", before_self_test)
        invocation = "& $installedPython -B $selfTest --checks-only --report $reportPath"
        invocation_offset = run_step["run"].index(invocation)
        failed_exit_offset = run_step["run"].index("if ($selfTestExit -ne 0)")
        fallback_write_offset = run_step["run"].index("Set-Content -Encoding utf8 -LiteralPath $reportPath")
        self.assertLess(invocation_offset, failed_exit_offset)
        self.assertLess(failed_exit_offset, fallback_write_offset)
        self.assertIn("if (-not (Test-Path -LiteralPath $reportPath))", run_step["run"])

        self.assertLess(run_index, fallback_index)
        self.assertLess(fallback_index, upload_index)
        self.assertEqual("always()", fallback_step.get("if"))
        self.assertIn("if (-not (Test-Path -LiteralPath $reportPath))", fallback_step["run"])
        self.assertIn("Set-Content -Encoding utf8 -LiteralPath $reportPath", fallback_step["run"])

    def test_windows_installed_runtime_trigger_excludes_feature_pull_requests(self):
        job = workflow()["jobs"]["installed-runtime-self-test"]
        condition = job["if"]
        self.assertIn("github.event_name == 'push'", condition)
        self.assertIn("github.base_ref == 'main'", condition)
        self.assertIn("startsWith(github.head_ref, 'release/')", condition)
        self.assertNotIn("github.event_name == 'pull_request' ||", condition)


if __name__ == "__main__":
    unittest.main()
