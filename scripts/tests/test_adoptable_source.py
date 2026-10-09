import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import build_release  # noqa: E402
from agentic.canonical import sha256  # noqa: E402
from agentic.installer import (INSTALL_OWNED_PATHS, SOURCE_CONFIG_PREFIX, install,  # noqa: E402
                               install_owned_path)


WORKTREE_OVERLAY = (
    ".agentic/lib/agentic/installer.py",
    ".agentic/lib/agentic/operating.py",
    ".agentic/templates/source-config/PROJECT_CONFIG.yaml",
    ".agentic/templates/source-config/workflow-version.yaml",
    ".agentic/templates/source-config/OPERATING_CONFIG.yaml",
    "scripts/build_release.py",
    "scripts/release_hygiene.py",
    "scripts/tests/test_adoptable_source.py",
    "scripts/tests/test_build_release_adoptable_source.py",
    "docs/showcase/AWF-PROOF-PACK.md",
)


class AdoptableSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-adoptable-source-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def prepare_clone(self):
        self.clone = self.base / "clone"
        cloned = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(self.clone)],
            text=True, capture_output=True, timeout=120)
        self.assertEqual(0, cloned.returncode, cloned.stderr)
        # Local worker validation runs before publication. Overlay only this
        # ticket's declared files so the temporary Git clone exercises the
        # exact working candidate; in CI these bytes already match HEAD.
        for relative in WORKTREE_OVERLAY:
            source = ROOT / relative
            if not source.exists():
                continue
            target = self.clone / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def command(self, *args, cwd=None):
        result = subprocess.run(
            [sys.executable, "-B", *map(str, args)], cwd=cwd or self.clone,
            text=True, capture_output=True, timeout=180)
        self.assertEqual(0, result.returncode,
                         f"command failed: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def materialize_release(self):
        manifest = json.loads((self.clone / "MANIFEST.json").read_text(encoding="utf-8"))
        release = self.base / "release"
        for relative in [*manifest["files"], "MANIFEST.json", "MANIFEST.md"]:
            source_relative = build_release.SOURCE_CONFIGS.get(relative, relative)
            source = self.clone / source_relative
            target = release / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return release, sha256((release / "MANIFEST.json").read_bytes())

    def test_installer_owned_source_inventory_is_authoritative(self):
        self.assertEqual({
            ".agentic/INSTALLING.json",
            ".agentic/installed-manifest.json",
            ".agentic/PROJECT_CONFIG.yaml",
            ".agentic/workflow-version.yaml",
            "OPERATING_CONFIG.yaml",
            ".gitignore",
        }, set(INSTALL_OWNED_PATHS))
        for relative in (
                ".agentic-install/lock",
                ".agentic-state/operating/changes/change.json",
                ".agentic-backup/transaction/.agentic/PROJECT_CONFIG.yaml",
                ".agentic/.venv/pyvenv.cfg",
                ".agentic/.venv.staging-123/runtime.txt"):
            self.assertTrue(install_owned_path(relative), relative)
        self.assertFalse(install_owned_path(".github/CODEOWNERS"))

    def test_adopted_clone_keeps_release_manifest_and_hygiene_green(self):
        self.prepare_clone()
        self.command("scripts/build_release.py", "--manifest-only")
        baseline_manifest = (self.clone / "MANIFEST.json").read_bytes()
        baseline_advisory = (self.clone / "MANIFEST.md").read_bytes()
        baseline_templates = {
            target: (self.clone / target).read_bytes()
            for target in build_release.SOURCE_CONFIGS
        }
        release, pin = self.materialize_release()

        uninstalled = subprocess.run([
            sys.executable, "-B", ".agentic/scripts/workflow.py",
            "--root", str(self.clone), "operating", "set",
            "--instruction", "This uninstalled release source must remain immutable",
            "--set", "streams.count=2", "--json",
        ], cwd=self.clone, text=True, capture_output=True, timeout=180)
        self.assertEqual(2, uninstalled.returncode, uninstalled.stderr)
        refusal = json.loads(uninstalled.stdout)
        self.assertEqual("REJECTED", refusal["status"])
        self.assertIn("immutable release source", refusal["refusals"][0]["reason"])

        result = install(
            release, self.clone, pin, conflict="backup", configure=True,
            discover=False, overrides={
                "repository": "agentic-workflow-framework/agentic-workflow-framework",
                "repository_id": 194,
                "base_branch": "main",
                "test_command": "python -B -m unittest discover",
            })
        self.assertEqual("INSTALLED", result["status"])
        self.assertEqual("ACCEPTED", result["configuration"]["status"])
        self.assertTrue((self.clone / "MANIFEST.json").is_file())
        self.assertTrue((self.clone / "MANIFEST.md").is_file())
        operating = self.command(
            ".agentic/scripts/workflow.py", "--root", self.clone,
            "operating", "set",
            "--instruction", "Use two worker streams for the source-repository adoption test",
            "--set", "streams.count=2", "--json")
        self.assertEqual("ACCEPTED", json.loads(operating.stdout)["status"])

        self.assertTrue((self.clone / ".agentic/installed-manifest.json").is_file())
        self.assertTrue((self.clone / ".agentic-backup").is_dir())
        self.assertTrue((self.clone / ".agentic-state/operating/changes").is_dir())
        for target, template in build_release.SOURCE_CONFIGS.items():
            self.assertEqual(baseline_templates[target], (self.clone / template).read_bytes())
            self.assertNotEqual((self.clone / target).read_bytes(), (self.clone / template).read_bytes())

        self.command("scripts/build_release.py", "--manifest-only")
        self.command("scripts/release_hygiene.py")
        self.assertEqual(baseline_manifest, (self.clone / "MANIFEST.json").read_bytes())
        self.assertEqual(baseline_advisory, (self.clone / "MANIFEST.md").read_bytes())
        adopted = json.loads(baseline_manifest)
        self.assertNotIn(".gitignore", adopted["files"])
        self.assertFalse(any(path.startswith(SOURCE_CONFIG_PREFIX)
                             for path in adopted["files"]))
        for target, expected in baseline_templates.items():
            self.assertEqual(sha256(expected), adopted["files"][target])
        for relative in adopted["files"]:
            if relative in build_release.SOURCE_CONFIGS:
                continue
            self.assertFalse(install_owned_path(relative), relative)


if __name__ == "__main__":
    unittest.main()
