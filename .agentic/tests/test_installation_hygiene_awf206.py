"""Offline regression coverage for AWF-206 installation hygiene (PR-A)."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.installer import merge_operating_ignores

TEMPLATE = ROOT / ".agentic/templates/operating.gitignore"
SETUP = ROOT / ".agentic/docs/20-NEW-PROJECT-SETUP.md"


class InstallationHygieneTests(unittest.TestCase):
    def test_template_ignores_installer_state_and_backups(self):
        rules = TEMPLATE.read_bytes().splitlines()
        for rule in (b".agentic-install/", b".agentic-backup/"):
            with self.subTest(rule=rule):
                self.assertIn(rule, rules)

    def test_fresh_and_legacy_merges_exclude_residue_from_staging(self):
        required = TEMPLATE.read_bytes()
        legacy = (b"# Project rules\r\nproduct-output/\r\n"
                  b".agentic/.venv/\r\n!*.tmp\r\n"
                  b"!.agentic-install/\r\n!.agentic-backup/")
        residue = {
            ".agentic-install/lock",
            ".agentic-install/journal.json",
            ".agentic-install/objects/snapshot",
            ".agentic-backup/transaction/AGENTS.md",
            ".agentic/.venv/runtime.txt",
            ".agentic-state/operating/pending.json",
        }
        evidence = {
            "product.txt",
            ".agentic-installation/product.txt",
            ".agentic-backup-notes.md",
            "OPERATING_CONFIG.yaml",
            "changes/awf206.json",
            ".agentic-state/operating/changes/awf206.json",
        }
        for name, original in (("fresh", None), ("legacy", legacy)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                merged = merge_operating_ignores(original, required)
                if original is not None:
                    self.assertTrue(merged.startswith(original))
                self.assertEqual(merged, merge_operating_ignores(merged, required))
                (root / ".gitignore").write_bytes(merged)
                for relative in residue | evidence:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"fixture\n")
                env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="NUL",
                           PYTHONDONTWRITEBYTECODE="1")
                # All staging is confined to this disposable repository.
                def git(*arguments):
                    result = subprocess.run(
                        ["git", "-c", "core.autocrlf=false", "-c", "core.excludesFile=NUL",
                         *arguments], cwd=root, env=env, capture_output=True, check=True)
                    return result.stdout
                git("init", "--quiet")
                git("add", "--all")
                staged = set(git("diff", "--cached", "--name-only", "-z").decode().split("\0")) - {""}
                self.assertFalse(residue & staged, sorted(residue & staged))
                self.assertEqual(evidence | {".gitignore"}, staged)

    def test_setup_table_explains_unobserved_default_branch(self):
        rows = [line for line in SETUP.read_text(encoding="utf-8").splitlines()
                if line.startswith("|") and "`github.base_branch`" in line]
        self.assertEqual(1, len(rows), "configuration table needs a default-branch row")
        row = rows[0]
        for term in ("metadata", "--default-branch", "null", "INSTALLED_UNCONFIGURED"):
            self.assertIn(term, row)
        self.assertIn("unobserved", row.lower())
        self.assertIn("main", row)

    def test_setup_names_local_installer_recovery_directories(self):
        paragraphs = SETUP.read_text(encoding="utf-8").split("\n\n")
        recovery = [paragraph for paragraph in paragraphs
                    if "`.agentic-install/`" in paragraph and "`.agentic-backup/`" in paragraph]
        self.assertEqual(1, len(recovery), "guide must name both recovery directories")
        self.assertRegex(recovery[0], r"(?i)(retain|preserve).*through recovery")
        self.assertIn("`changes/`", recovery[0])


if __name__ == "__main__":
    unittest.main()
