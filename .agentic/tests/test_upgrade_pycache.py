import hashlib
import json
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic import adoption_config as adoption  # noqa: E402
from agentic.installer import install  # noqa: E402
from source_only import skip_unless_source_repo  # noqa: E402
from test_bootstrap_configuration import installed_wheel, wheel_lock  # noqa: E402
from upgrade_fixtures import materialize, verify_materialized  # noqa: E402


DEPENDENCIES = (
    "PyYAML",
    "jsonschema",
    "attrs",
    "jsonschema-specifications",
    "referencing",
    "rpds-py",
    "typing-extensions",
)
OBSERVED_MODULES = ("__init__", "adoption_config", "installer", "upgrade")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare_release(base, *, runtime=False):
    """Copy manifest members and repin them after test-only lock generation."""
    source = base / "release"
    source.mkdir()
    manifest = json.loads((ROOT / "MANIFEST.json").read_bytes())
    for relative in manifest["files"]:
        target = source / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / Path(relative), target)

    wheelhouse = None
    if runtime:
        wheelhouse = base / "wheelhouse"
        wheelhouse.mkdir()
        entries = []
        for name in DEPENDENCIES:
            version, raw = installed_wheel(name)
            filename = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
            (wheelhouse / filename).write_bytes(raw)
            entries.append((name, version, raw))
        (source / ".agentic/requirements.lock").write_bytes(wheel_lock(entries))

    manifest["files"] = {
        relative: sha256((source / Path(relative)).read_bytes())
        for relative in manifest["files"]
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (source / "MANIFEST.json").write_bytes(raw)
    return source, sha256(raw), wheelhouse


def plant_cache(project, source_relative, *, tag="cpython-313"):
    source = project / Path(source_relative)
    cache = source.parent / "__pycache__" / (source.stem + "." + tag + ".pyc")
    cache.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(source), cfile=str(cache), doraise=True)
    return cache


def disable_jira(project):
    path = project / ".agentic/PROJECT_CONFIG.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["jira"].update(
        enabled=False,
        cloud_id=None,
        site=None,
        provider_project_id=None,
        project_key=None,
        controller_actor_id=None,
    )
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def verify_previous_runtime(project):
    completed = subprocess.run([
        sys.executable,
        "-B",
        "-I",
        str(project / ".agentic/scripts/workflow.py"),
        "--root",
        str(project),
        "verify-installation",
    ], cwd=project, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr + completed.stdout)
    result = json.loads(completed.stdout)
    if result.get("integrity_valid") is not True:
        raise AssertionError(result)
    return result


@skip_unless_source_repo(
    "historical upgrade fixtures are intentionally excluded from portable releases",
    ".agentic/tests/fixtures/upgrades/versions.json")
class UpgradePycacheTests(unittest.TestCase):
    def test_only_pinned_sibling_cpython_caches_are_tolerated(self):
        with tempfile.TemporaryDirectory(prefix="awf-upgrade-pycache-surface-") as folder:
            base = Path(folder)
            project = base / "project"
            materialize("1.9.3", project)
            verify_materialized("1.9.3", project)
            source, pin, _wheelhouse = prepare_release(base)
            installation = install(source, project, pin, mode="upgrade", configure=True,
                                   discover=False)

            accepted = [
                plant_cache(project, ".agentic/lib/agentic/adoption_config.py"),
                plant_cache(project, ".agentic/scripts/workflow.py", tag="cpython-313.opt-1"),
            ]
            adoption._verify_import_surface(project, pin)
            self.assertTrue(all(path.is_file() for path in accepted))

            rejected = {
                "stray source": ".agentic/lib/agentic/shadow.py",
                "legacy bytecode": ".agentic/lib/agentic/adoption_config.pyc",
                "cache without pinned source": ".agentic/lib/agentic/__pycache__/shadow.cpython-313.pyc",
                "path injection": ".agentic/lib/shadow.pth",
                "other unexpected file": ".agentic/scripts/__pycache__/workflow.cpython-313.pyc.txt",
            }
            for label, relative in rejected.items():
                with self.subTest(label=label):
                    path = project / Path(relative)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"preserve unexpected bytes\n")
                    with patch.object(
                            adoption, "_post_command",
                            side_effect=AssertionError("rejected import files must prevent child execution")):
                        checked = adoption.post_install_checks(project, installation)
                    self.assertEqual("INSTALLATION_VERIFICATION_FAILED", checked["status"])
                    self.assertFalse(checked["installed"])
                    self.assertEqual(["NOT_RUN", "NOT_RUN"], [
                        item["execution_status"] for item in checked["post_install_checks"]])
                    self.assertEqual(b"preserve unexpected bytes\n", path.read_bytes())
                    path.unlink()

            adoption._verify_import_surface(project, pin)

    def test_193_upgrade_with_four_stale_caches_is_configured_end_to_end(self):
        with tempfile.TemporaryDirectory(prefix="awf-upgrade-pycache-e2e-") as folder:
            base = Path(folder)
            project = base / "project"
            materialize("1.9.3", project)
            verify_materialized("1.9.3", project)
            disable_jira(project)
            previous = verify_previous_runtime(project)
            self.assertIs(True, previous["integrity_valid"])
            caches = [
                plant_cache(project, ".agentic/lib/agentic/" + module + ".py")
                for module in OBSERVED_MODULES
            ]
            before = {path: path.read_bytes() for path in caches}
            source, pin, wheelhouse = prepare_release(base, runtime=True)

            command = [
                sys.executable,
                "-B",
                str(source / "scripts/bootstrap_project.py"),
                "--dest",
                str(project),
                "--expected-manifest-sha256",
                pin,
                "--mode",
                "upgrade",
            ]
            planned = subprocess.run(command + ["--dry-run"], cwd=base,
                                     capture_output=True, text=True, timeout=120)
            self.assertEqual(0, planned.returncode, planned.stderr + planned.stdout)
            plan = json.loads(planned.stdout)
            self.assertEqual("PLAN", plan["status"])
            self.assertEqual([], plan["conflicts"])
            self.assertFalse(plan["installed"])
            self.assertEqual(before, {path: path.read_bytes() for path in caches})

            upgraded = subprocess.run(command + ["--runtime-wheelhouse", str(wheelhouse)],
                                      cwd=base, capture_output=True, text=True, timeout=300)
            self.assertEqual(0, upgraded.returncode, upgraded.stderr + upgraded.stdout)
            result = json.loads(upgraded.stdout)
            self.assertEqual("CONFIGURED", result["status"])
            self.assertTrue(result["installed"])
            verification, validation = result["post_install_checks"]
            self.assertEqual(0, verification["exit_code"])
            self.assertIs(True, verification["output"]["integrity_valid"])
            self.assertEqual(pin, verification["output"]["source_manifest_sha256"])
            self.assertEqual(0, validation["exit_code"])
            self.assertEqual("ACCEPTED", validation["output"]["status"])
            self.assertEqual(before, {path: path.read_bytes() for path in caches})


if __name__ == "__main__":
    unittest.main()
