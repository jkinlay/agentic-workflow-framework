"""AWF-158: load the installed pinned adapter with an isolated installed runtime."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.canonical import load_yaml, sha256
from agentic.installer import install, verify_installed
from source_only import skip_unless_source_repo
from test_reference_controller_adapter import _base_config, NOW, cycle_requirements

SWITCHES = ("dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments")

@skip_unless_source_repo()
class InstalledReferenceAdapterLoadTests(unittest.TestCase):
    def setUp(self):
        # No historical trust is manufactured: this is the current candidate's
        # ordinary logical release projection, also used before its adapter fix.
        self.temp = tempfile.TemporaryDirectory(prefix="awf205-installed-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        sys.path.insert(0, str(ROOT / "scripts"))
        import build_release
        from agentic.safeio import Tree
        self.source = self.base / "candidate-source"
        self.source.mkdir()
        files = {}
        with Tree(ROOT) as tree:
            for relative in build_release.release_paths(tree):
                if relative in {"MANIFEST.json", "MANIFEST.md"}:
                    continue
                raw = build_release.release_bytes(tree, relative)
                path = self.source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                files[relative] = sha256(raw)
        machine, advisory = build_release.render_manifests(files)
        build_release.write_manifest_files(self.source, machine, advisory)
        self.pin = sha256(machine)

    def installed_runtime(self, project):
        runtime = project / ".agentic/.venv"
        # Reuse installed local dependencies, without pip/network installation.
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(runtime)
        import jsonschema
        dependencies = Path(jsonschema.__file__).resolve().parents[1]
        site = runtime / ("Lib/site-packages" if os.name == "nt"
                          else "lib/python%d.%d/site-packages" % sys.version_info[:2])
        site.mkdir(parents=True, exist_ok=True)
        (site / "awf-test-dependencies.pth").write_text(str(dependencies) + "\n", encoding="utf-8")
        return runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def load_for_cycle(self, project):
        self.assertEqual(self.pin, verify_installed(project))
        cfg = load_yaml((project / ".agentic/PROJECT_CONFIG.yaml").read_bytes())
        self.assertTrue(all(cfg["controller"][name] is False for name in SWITCHES))
        executable = self.installed_runtime(project)
        harness = self.base / "adapter-host"
        harness.mkdir()
        adapter_config = _base_config(harness)
        adapter_config["jira"]["enabled"] = False
        adapter_config["github"]["project_id"] = cfg["project"]["id"]
        adapter_config["github"]["executable"] = {
            "path": str(executable), "sha256": sha256(executable.read_bytes())}
        config_path = self.base / "adapter-config.json"
        config_path.write_text(json.dumps(adapter_config), encoding="utf-8")
        controller = project / ".agentic/scripts/continuous_controller.py"
        adapter = project / ".agentic/adapters/reference_controller_adapter.py"
        adapter_pin = sha256(adapter.read_bytes())
        required = cycle_requirements(controller)
        unrelated = self.base / "unrelated"
        unrelated.mkdir()
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        program = (
            "import importlib.util,json,pathlib,sys;"
            "p=pathlib.Path(sys.argv[1]);"
            "s=importlib.util.spec_from_file_location('installed_controller',p);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "a=m._load_reviewed_adapters(pathlib.Path(sys.argv[2]),sys.argv[3],pathlib.Path(sys.argv[4]));"
            "required=set(json.loads(sys.argv[5]));m._require_adapters(a,required);"
            "print(json.dumps({'interpreter':sys.executable,'prefix':sys.prefix,"
            "'controller':m.__file__,'adapter':a['observe_host_resources'].__code__.co_filename,"
            "'pin':sys.argv[3],'callbacks':sorted(required)}))")
        command = [str(executable), "-B", "-I", "-c", program,
                   str(controller), str(adapter), adapter_pin, str(config_path),
                   json.dumps(sorted(required))]
        result = subprocess.run(command, cwd=unrelated, env=env, capture_output=True, text=True, timeout=60)
        print(json.dumps({"command": command, "exit_code": result.returncode,
                          "stdout": result.stdout, "stderr": result.stderr}))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        loaded = json.loads(result.stdout)
        self.assertEqual(Path(loaded["interpreter"]).resolve(), executable.resolve())
        self.assertEqual(Path(loaded["prefix"]).resolve(), executable.parents[1].resolve())
        self.assertEqual(Path(loaded["controller"]).resolve(), controller.resolve())
        self.assertEqual(Path(loaded["adapter"]).resolve(), adapter.resolve())
        self.assertEqual(loaded["pin"], adapter_pin)
        self.assertEqual(set(loaded["callbacks"]), required)
        binding = self.base / "binding.json"
        binding.write_text(json.dumps({"project_id": cfg["project"]["id"],
            "repository_id": "101", "scope_sha256": "a" * 64}), encoding="utf-8")
        # Actual isolated script entrypoint. The pinned local Python executable
        # substitutes only the external provider boundary and cannot contact GitHub.
        direct = [str(executable), "-B", "-I", str(controller),
            "--state", str(self.base / "state.sqlite3"), "--stream", "A",
            "--worktree-root", str(harness / "worktree"),
            "--adapter-module", str(adapter), "--adapter-sha256", adapter_pin,
            "--adapter-config", str(config_path), "cycle",
            "--inventory-binding", str(binding), "--repository-root", str(project),
            "--repository-head-sha", "a" * 40, "--repository-tree-sha", "b" * 40,
            "--now", NOW]
        result = subprocess.run(direct, cwd=unrelated, env=env, capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        print(json.dumps({"command": direct, "exit_code": result.returncode,
                          "stdout": result.stdout, "stderr": result.stderr}))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("External command returned a non-zero exit", output)
        self.assertNotIn("missing callable operations", output)
        self.assertNotIn("Codex adapter configuration is incomplete", output)
        self.assertTrue(list((harness / "runs/planning-evidence").glob("host-*.json")), output)
        self.assertEqual(self.pin, verify_installed(project))
        cfg = load_yaml((project / ".agentic/PROJECT_CONFIG.yaml").read_bytes())
        self.assertTrue(all(cfg["controller"][name] is False for name in SWITCHES))

    def test_fresh_install_reference_adapter_loads_for_cycle(self):
        project = self.base / "fresh"
        project.mkdir()
        result = install(self.source, project, self.pin, configure=True, discover=False,
            overrides={"repository": "example/project", "repository_id": 101,
                       "base_branch": "main", "test_command": "python -m unittest"})
        self.assertFalse(result["live_automation_enabled"])
        self.assertFalse(result["installer_launches_agents"])
        self.load_for_cycle(project)

    def test_upgraded_194_reference_adapter_loads_for_cycle(self):
        from upgrade_fixtures import fixture_index, materialize, verify_materialized
        if "1.9.4" not in fixture_index()["versions"]:
            self.skipTest("SETUP_FAILURE: authentic 1.9.4 predecessor fixture is unavailable; no fixture fabricated")
        project = self.base / "upgraded"
        materialize("1.9.4", project)
        verify_materialized("1.9.4", project)
        result = install(self.source, project, self.pin, mode="upgrade",
                         configure=True, discover=False)
        self.assertFalse(result["live_automation_enabled"])
        self.assertFalse(result["installer_launches_agents"])
        self.load_for_cycle(project)
