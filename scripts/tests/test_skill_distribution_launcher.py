import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("build_skill_distribution", ROOT / "scripts/build_skill_distribution.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class FreshDistributionLauncherTests(unittest.TestCase):
    def test_generated_launcher_installs_without_prior_skill(self):
        with tempfile.TemporaryDirectory(prefix="awf-launcher-") as raw:
            base = Path(raw)
            distribution = base / "distribution"
            skill = distribution / "awf"
            (skill / "scripts").mkdir(parents=True)
            (skill / "assets").mkdir()
            (skill / "SKILL.md").write_text("---\nname: awf\ndescription: synthetic fresh launcher fixture\n---\n", encoding="utf-8")
            installer = skill / "scripts/install_skill.py"
            installer.write_bytes((ROOT / "global/awf-portable/scripts/install_skill.py").read_bytes())
            archive_name = f"agentic-workflow-template-v{builder.LINE}.zip"
            archive = skill / "assets" / archive_name
            prefix = f"agentic-workflow-template-v{builder.LINE}/.agentic/lib/agentic/"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr(prefix + "__init__.py", (ROOT / ".agentic/lib/agentic/__init__.py").read_bytes())
                package.writestr(prefix + "child_process.py", (ROOT / ".agentic/lib/agentic/child_process.py").read_bytes())
            files = []
            for path in sorted(p for p in skill.rglob("*") if p.is_file()):
                files.append({"path": path.relative_to(skill).as_posix(),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            manifest = {"schema_version": 1, "name": "awf", "version": builder.VERSION, "files": files}
            manifest_path = skill / "SKILL-MANIFEST.json"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
            pin = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            launcher = builder.render_launcher(pin, hashlib.sha256(installer.read_bytes()).hexdigest(),
                                               archive_name, hashlib.sha256(archive.read_bytes()).hexdigest())
            launcher_path = distribution / "install_awf.py"
            launcher_path.write_text(launcher, encoding="utf-8", newline="\n")
            profile = base / "empty-home"
            destination = profile / ".codex/skills/awf"
            env = os.environ.copy()
            env.update(HOME=str(profile), USERPROFILE=str(profile), CODEX_HOME=str(profile / ".codex"),
                       PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
            dry = subprocess.run([sys.executable, "-B", str(launcher_path), "--dest", str(destination), "--dry-run"],
                                 capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse(destination.exists())
            actual = subprocess.run([sys.executable, "-B", str(launcher_path), "--dest", str(destination)],
                                    capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertTrue((destination / ".awf-install-receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()
