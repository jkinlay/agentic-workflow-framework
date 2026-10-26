"""Codex skill selection is pinned and recorded without reading the real profile."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from test_continuous_controller_planning import planning_resource_inputs, planning_fixture_inputs
from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.codex_skill import enforce_pinned_awf_skill, observe_codex_awf_skills
from agentic.continuous_controller import (
    ContinuousControllerStore,
    production_controller_cycle,
)


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
        self.skill(pinned, "1.9.5")
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
            {(str(pinned.resolve()), "1.9.5"), (str(stale.resolve()), "1.9.3")},
        )

    def test_matching_user_and_project_copies_are_logged(self):
        pinned = self.codex_home / "skills" / "awf"
        project_skill = self.project / ".agents" / "skills" / "awf"
        self.skill(pinned, "1.9.5")
        self.skill(project_skill, "1.9.5")

        observed = observe_codex_awf_skills(
            self.project, self.checkout, self.environment
        )

        self.assertEqual(observed["status"], "PASS")
        self.assertEqual(observed["expected_version"], "1.9.5")
        self.assertEqual(observed["pinned_skill_path"], str(pinned.resolve()))
        self.assertEqual(len(observed["visible_skills"]), 2)
        project_record = next(
            item for item in observed["visible_skills"]
            if item["path"] == str(project_skill.resolve())
        )
        self.assertIn(str(self.project.resolve()), project_record["visible_to"])

    def test_aliased_skill_is_resolved_deduplicated_and_named_in_refusal(self):
        canonical_home = self.root / "canonical-home"
        canonical_checkout = canonical_home / "checkout"
        canonical_checkout.mkdir(parents=True)
        alias_home = self.root / "aliased-home"
        try:
            alias_home.symlink_to(canonical_home, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            if os.name != "nt":
                self.skipTest(f"directory symlinks unavailable: {error}")
            junction = subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/j", alias_home, canonical_home],
                capture_output=True,
                text=True,
                check=False,
            )
            if junction.returncode:
                self.skipTest(
                    "directory aliases unavailable: "
                    f"symlink failed ({error}); junction failed ({junction.stderr.strip()})"
                )
        stale = canonical_home / ".agents" / "skills" / "awf"
        self.skill(stale, "1.9.3")
        environment = {
            "HOME": str(canonical_home),
            "USERPROFILE": str(canonical_home),
            "CODEX_HOME": str(self.codex_home),
        }
        record = self.root / "run" / "skill-selection.json"

        with self.assertRaises(ValidationError) as raised:
            enforce_pinned_awf_skill(
                self.project,
                alias_home / canonical_checkout.name,
                environment,
                record_path=record,
            )

        resolved = str(stale.resolve())
        logged = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["path"] for item in logged["visible_skills"]], [resolved]
        )
        self.assertEqual(
            logged["problems"],
            [f"AWF skill {resolved} has version 1.9.3; expected 1.9.5"],
        )
        self.assertIn(resolved, str(raised.exception))

    def test_no_discoverable_skill_passes_but_unversioned_copy_refuses(self):
        observed = enforce_pinned_awf_skill(
            self.project, self.checkout, self.environment
        )
        self.assertEqual(observed["status"], "PASS")
        self.assertEqual(observed["visible_skills"], [])
        pinned = self.codex_home / "skills" / "awf"
        pinned.mkdir(parents=True)
        (pinned / "SKILL.md").write_text("---\nname: awf\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "version could not be established"):
            enforce_pinned_awf_skill(self.project, self.checkout, self.environment)

    def test_distinct_home_and_userprofile_are_both_observed_without_codex_home(self):
        home = self.root / "posix-home"
        profile = self.root / "windows-profile"
        selected = home if os.name != "nt" else profile
        other = profile if os.name != "nt" else home
        self.skill(selected / ".codex" / "skills" / "awf", "1.9.5")
        stale = other / ".codex" / "skills" / "awf"
        self.skill(stale, "1.9.3")
        environment = {"HOME": str(home), "USERPROFILE": str(profile)}

        with self.assertRaisesRegex(ValidationError, "1[.]9[.]3") as raised:
            enforce_pinned_awf_skill(self.project, self.checkout, environment)

        self.assertIn(str(stale.resolve()), str(raised.exception))
        observed = observe_codex_awf_skills(
            self.project, self.checkout, environment
        )
        self.assertEqual(
            {item["path"] for item in observed["visible_skills"]},
            {
                str((home / ".codex" / "skills" / "awf").resolve()),
                str((profile / ".codex" / "skills" / "awf").resolve()),
            },
        )

    def test_host_driver_codex_launch_writes_skill_record(self):
        from agentic.canonical import sha256
        from agentic.providers.github_review_host import HostDriver

        pinned = self.codex_home / "skills" / "awf"
        self.skill(pinned, "1.9.5")
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

    def test_host_driver_without_explicit_log_retains_default_skill_record(self):
        from agentic.canonical import sha256
        from agentic.providers.github_review_host import HostDriver

        state = self.root / "state"
        runtime = self.root / "runtime"
        state.mkdir()
        runtime.mkdir()
        config_path = state / "config.json"
        config_path.write_text("{}", encoding="utf-8")
        executable = Path(os.__file__).resolve()
        config = {
            "worker_checkout": str(self.checkout),
            "critic_checkout": str(self.checkout),
            "state_dir": str(state),
            "repository": "fixture/project",
            "executables": {
                "codex": {
                    "path": str(executable),
                    "sha256": sha256(executable.read_bytes()),
                }
            },
            "_config_path": str(config_path),
            "config_hash": sha256(config_path.read_bytes()),
            "command_timeout_seconds": 10,
        }
        driver = HostDriver(config, runtime)
        driver._qualification_probe = True
        completed = type("Completed", (), {"returncode": 0, "stdout": ""})()

        with patch.dict(os.environ, self.environment, clear=True), patch(
            "agentic.providers.github_review_host.subprocess.run",
            return_value=completed,
        ):
            driver.run("codex", ["exec", "--cd", str(self.checkout)])

        record = json.loads(
            (state / "skill-selection.json").read_text(encoding="utf-8")
        )
        self.assertEqual(record["status"], "PASS")

    def test_controller_dispatch_enforces_and_retains_skill_selection(self):
        pinned = self.codex_home / "skills" / "awf"
        self.skill(pinned, "1.9.5")
        now = "2026-10-11T10:00:00Z"
        binding = {
            "project_id": "project",
            "repository_id": "repository",
            "scope_sha256": "a" * 64,
        }
        ticket = {"ticket": "AWF-33", "disposition": "ELIGIBLE", "paths": ["src/a.py"], "priority": 1, "dependencies_satisfied": True, "budget_available": True, "cap_available": True, "review_independent": True, "exact_tuple": "tuple"}
        observation = {
            "source": "host_observation",
            "observed_at": now,
            "binding": binding,
            "complete": True,
            "inventory_sha256": fingerprint(
                "controller-inventory", {"binding": binding, "tickets": [ticket]}
            ),
            "tickets": [ticket],
        }
        payload = {
            "dispatch_id": "dispatch",
            "stream": "A",
            "ticket": "AWF-33",
            "exact_tuple": "tuple",
            "dispatch_nonce": "nonce",
            "prepared_at": now,
            "paths": ["src/a.py"],
            "actor": "writer",
            "next_action": "amend",
        }

        for role in ("writer", "critic"):
            with self.subTest(role=role):
                store = MagicMock(spec=ContinuousControllerStore)
                store.path = self.project.parent / 'planning-controller.sqlite3'
                store.plan_production_cycle.return_value = {'lane_count': 1, 'lanes': [{'ticket_keys': ['AWF-33']}], 'ranking': {'AWF-33': 1}}
                store.worktree_roots = (self.checkout,)
                store.pending_dispatches.return_value = []
                store.schedule.return_value = [
                    {
                        "stream": "A",
                        "state": "WORKING",
                        "ticket": "AWF-33",
                        "exact_tuple": "tuple",
                    }
                ]
                store.prepare_dispatches.return_value = [
                    {"status": "PENDING", "dispatch_id": "dispatch", "payload": payload}
                ]
                retained = {}

                def retain(_dispatch_id, record, _now):
                    retained.update(record)

                store.record_dispatch_skill_selection.side_effect = retain

                def begin(_dispatch_id, _now):
                    self.assertEqual(retained["status"], "PASS")
                    return {
                        **payload,
                        "begun_at": now,
                        "codex_skill_selection": dict(retained),
                    }

                store.begin_dispatch.side_effect = begin
                store.finish_dispatch.return_value = {"status": "ACCEPTED"}
                store.digest.return_value = None
                store.snapshot.return_value = []

                def dispatch(launch):
                    self.assertEqual(launch["role"], role)
                    self.assertEqual(launch["worktree"], str(self.checkout.resolve()))
                    self.assertEqual(
                        launch["codex_skill_selection"]["visible_skills"][0]["path"],
                        str(pinned.resolve()),
                    )
                    return {"status": "ACCEPTED"}

                with patch.dict(os.environ, self.environment, clear=True), patch(
                    "agentic.continuous_controller.canonical_repository_paths",
                    return_value=([ticket], {"root": str(self.project)}),
                ), patch(
                    "agentic.publication_readiness.require_publication_readiness",
                    return_value={"status": "PASS"},
                ):
                    production_controller_cycle(
                        store,
                        controller_config={'dispatch_enabled': True, 'auto_dispatch': True, 'auto_request_critic': True, 'auto_resume_amendments': True},
                        now=now,
                        host_capacity=1,
                        **{**planning_resource_inputs(1), **planning_fixture_inputs([ticket], store, 1, binding=binding, now=now)},
                        inventory_binding=binding,
                        repository_root=self.project,
                        repository_head_sha="a" * 40,
                        repository_tree_sha="b" * 40,
                        observe_inventory=lambda: observation,
                        dispatch_ticket=dispatch,
                        observe_dispatch=lambda value: value,
                        deliver_status=lambda value: value,
                        publication_config={
                            "github": {"repository_id": "repository"}
                        },
                        observe_publication=lambda value: value,
                        dispatch_role=role,
                    )

                store.record_dispatch_skill_selection.assert_called_once()

    def test_controller_refuses_stale_skill_in_distinct_dispatch_worktree(self):
        pinned = self.codex_home / "skills" / "awf"
        stale = self.checkout / ".agents" / "skills" / "awf"
        self.skill(pinned, "1.9.5")
        self.skill(stale, "1.9.3")
        now = "2026-10-11T10:00:00Z"
        binding = {
            "project_id": "project",
            "repository_id": "repository",
            "scope_sha256": "a" * 64,
        }
        ticket = {"ticket": "AWF-33", "disposition": "ELIGIBLE", "paths": ["src/a.py"], "priority": 1, "dependencies_satisfied": True, "budget_available": True, "cap_available": True, "review_independent": True, "exact_tuple": "tuple"}
        observation = {
            "source": "host_observation",
            "observed_at": now,
            "binding": binding,
            "complete": True,
            "inventory_sha256": fingerprint(
                "controller-inventory", {"binding": binding, "tickets": [ticket]}
            ),
            "tickets": [ticket],
        }
        payload = {
            "dispatch_id": "dispatch",
            "stream": "A",
            "ticket": "AWF-33",
            "exact_tuple": "tuple",
            "dispatch_nonce": "nonce",
            "prepared_at": now,
            "paths": ["src/a.py"],
            "actor": "writer",
            "next_action": "amend",
        }
        store = MagicMock(spec=ContinuousControllerStore)
        store.path = self.project.parent / 'stale-planning-controller.sqlite3'
        store.plan_production_cycle.return_value = {'lane_count': 1, 'lanes': [{'ticket_keys': ['AWF-33']}], 'ranking': {'AWF-33': 1}}
        store.worktree_roots = (self.checkout,)
        store.pending_dispatches.return_value = []
        store.schedule.return_value = [{
            "stream": "A",
            "state": "WORKING",
            "ticket": "AWF-33",
            "exact_tuple": "tuple",
        }]
        store.prepare_dispatches.return_value = [{
            "status": "PENDING", "dispatch_id": "dispatch", "payload": payload
        }]
        retained = {}
        store.record_dispatch_skill_selection.side_effect = (
            lambda _dispatch_id, record, _now: retained.update(record)
        )
        store.digest.return_value = None
        store.snapshot.return_value = []

        with patch.dict(os.environ, self.environment, clear=True), patch(
            "agentic.continuous_controller.canonical_repository_paths",
            return_value=([ticket], {"root": str(self.project)}),
        ), patch(
            "agentic.publication_readiness.require_publication_readiness",
            return_value={"status": "PASS"},
        ):
            result = production_controller_cycle(
                store,
                controller_config={'dispatch_enabled': True, 'auto_dispatch': True, 'auto_request_critic': True, 'auto_resume_amendments': True},
                now=now,
                host_capacity=1,
                **{**planning_resource_inputs(1), **planning_fixture_inputs([ticket], store, 1, binding=binding, now=now)},
                inventory_binding=binding,
                repository_root=self.project,
                repository_head_sha="a" * 40,
                repository_tree_sha="b" * 40,
                observe_inventory=lambda: observation,
                dispatch_ticket=lambda _value: self.fail(
                    "stale dispatch-worktree skill reached Codex dispatch"
                ),
                observe_dispatch=lambda value: value,
                deliver_status=lambda value: value,
                publication_config={"github": {"repository_id": "repository"}},
                observe_publication=lambda value: value,
            )

        self.assertEqual(result["dispatch_receipts"], [])
        self.assertEqual(result["errors"][0]["state"], "REFUSED")
        self.assertIn(str(stale.resolve()), result["errors"][0]["reason"])
        self.assertEqual(retained["status"], "REFUSED")
        self.assertEqual(retained["project_root"], str(self.checkout.resolve()))
        self.assertEqual(retained["launch_directory"], str(self.checkout.resolve()))
        store.begin_dispatch.assert_not_called()

    def test_controller_store_persists_visible_skill_record_before_begin(self):
        pinned = self.codex_home / "skills" / "awf"
        self.skill(pinned, "1.9.5")
        now = "2026-10-11T10:00:00Z"
        record = observe_codex_awf_skills(
            self.project, self.checkout, self.environment
        )
        store = ContinuousControllerStore(
            self.root / "state" / "controller.sqlite3", ["A"]
        )
        payload = {
            "dispatch_id": "dispatch",
            "stream": "A",
            "ticket": "AWF-33",
            "exact_tuple": "tuple",
            "dispatch_nonce": "nonce",
            "prepared_at": now,
        }
        with store.transaction() as database:
            database.execute(
                "INSERT INTO controller_dispatch VALUES(?,?,?,?,?,?,NULL,?)",
                (
                    "dispatch",
                    "A",
                    "AWF-33",
                    "tuple",
                    "PENDING",
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    now,
                ),
            )

        store.record_dispatch_skill_selection("dispatch", record, now)

        retained = store.pending_dispatches()[0]["payload"]
        self.assertEqual(retained["codex_skill_selection"], record)
        begun = store.begin_dispatch("dispatch", now)
        self.assertEqual(begun["codex_skill_selection"], record)


if __name__ == "__main__":
    unittest.main()
