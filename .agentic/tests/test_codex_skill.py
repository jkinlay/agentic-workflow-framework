"""Codex skill selection is pinned and recorded without reading the real profile."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.codex_skill import enforce_pinned_awf_skill, observe_codex_awf_skills


class CodexSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "profile"
        self.codex_home = self.root / "selected-codex-home"
        self.project = self.root / "project"
        self.checkout = self.project / "worktree"
        self.checkout.mkdir(parents=True)
        self.environment = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.codex_home),
        }

    @staticmethod
    def skill(path, version):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: awf\ndescription: Includes AWF {version}.\n---\n",
            encoding="utf-8",
        )

    def test_different_version_in_default_user_home_refuses_and_names_path(self):
        pinned = self.codex_home / "skills" / "awf"
        stale = self.home / ".codex" / "skills" / "awf"
        self.skill(pinned, "1.9.4")
        self.skill(stale, "1.9.3")
        record = self.root / "run" / "skill-selection.json"

        with self.assertRaisesRegex(ValidationError, "1[.]9[.]3") as raised:
            enforce_pinned_awf_skill(
                self.project, self.checkout, self.environment, record_path=record
            )

        self.assertIn(str(stale.resolve()), str(raised.exception))
        logged = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(logged["status"], "REFUSED")
        self.assertEqual(
            {(item["path"], item["version"]) for item in logged["visible_skills"]},
            {(str(pinned.resolve()), "1.9.4"), (str(stale.resolve()), "1.9.3")},
        )

    def test_matching_user_and_project_copies_are_logged(self):
        pinned = self.codex_home / "skills" / "awf"
        project_skill = self.project / ".agents" / "skills" / "awf"
        self.skill(pinned, "1.9.4")
        self.skill(project_skill, "1.9.4")

        observed = observe_codex_awf_skills(
            self.project, self.checkout, self.environment
        )

        self.assertEqual(observed["status"], "PASS")
        self.assertEqual(observed["expected_version"], "1.9.4")
        self.assertEqual(observed["pinned_skill_path"], str(pinned.resolve()))
        self.assertEqual(len(observed["visible_skills"]), 2)
        project_record = next(
            item for item in observed["visible_skills"]
            if item["path"] == str(project_skill.resolve())
        )
        self.assertIn(str(self.project.resolve()), project_record["visible_to"])

    def test_missing_or_unversioned_configured_skill_refuses(self):
        with self.assertRaisesRegex(ValidationError, "configured pinned AWF skill"):
            enforce_pinned_awf_skill(self.project, self.checkout, self.environment)
        pinned = self.codex_home / "skills" / "awf"
        pinned.mkdir(parents=True)
        (pinned / "SKILL.md").write_text("---\nname: awf\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "version could not be established"):
            enforce_pinned_awf_skill(self.project, self.checkout, self.environment)

    def test_host_driver_codex_launch_writes_skill_record(self):
        from agentic.canonical import sha256
        from agentic.providers.github_review_host import HostDriver

        pinned = self.codex_home / "skills" / "awf"
        self.skill(pinned, "1.9.4")
        state = self.root / "state"
        worker = self.checkout
        critic = self.root / "critic"
        runtime = self.root / "runtime"
        for path in (state, critic, runtime):
            path.mkdir()
        config_path = state / "config.json"
        config_path.write_text("{}", encoding="utf-8")
        executable = Path(os.__file__).resolve()
        config = {
            "worker_checkout": str(worker), "critic_checkout": str(critic),
            "state_dir": str(state), "repository": "fixture/project",
            "executables": {"codex": {"path": str(executable),
                                         "sha256": sha256(executable.read_bytes())}},
            "_config_path": str(config_path),
            "config_hash": sha256(config_path.read_bytes()),
            "command_timeout_seconds": 10,
        }
        driver = HostDriver(config, runtime)
        driver._qualification_probe = True
        log = state / "runs" / "fixture" / "codex.jsonl"
        log.parent.mkdir(parents=True)
        completed = type("Completed", (), {"returncode": 0, "stdout": ""})()

        with patch.dict(os.environ, self.environment, clear=True), \
                patch("agentic.providers.github_review_host.subprocess.run",
                      return_value=completed) as run:
            driver.run("codex", ["exec", "--cd", str(worker)], log=log)

        selection = json.loads(
            (log.parent / "skill-selection.json").read_text(encoding="utf-8")
        )
        self.assertEqual(selection["status"], "PASS")
        self.assertEqual(selection["visible_skills"][0]["path"], str(pinned.resolve()))
        self.assertEqual(
            run.call_args.kwargs["env"]["CODEX_HOME"], str(self.codex_home)
        )


if __name__ == "__main__":
    unittest.main()
