import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COPILOT_SCAFFOLD = ROOT / "scaffolds" / "providers" / "copilot"


def run_git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )


class DistributionTests(unittest.TestCase):
    def test_current_version_is_consistent(self):
        manifest = (ROOT / ".agentic-workflow" / "distribution-manifest.yaml").read_text(
            encoding="utf-8"
        )
        version = re.search(r"(?m)^  version: ([0-9]+\.[0-9]+\.[0-9]+)$", manifest)
        self.assertIsNotNone(version)
        self.assertEqual(version.group(1), "0.1.1")
        for path in (
            ROOT / "README.md",
            ROOT / "docs" / "GETTING_STARTED.md",
            ROOT / "docs" / "SYSTEM_ARCHITECTURE.md",
        ):
            with self.subTest(path=path):
                self.assertIn("0.1.1", path.read_text(encoding="utf-8"))

    def test_managed_wording_is_layout_neutral(self):
        policy = (ROOT / "docs" / "REVIEW_POLICY.md").read_text(encoding="utf-8")
        self.assertNotIn("`schemas/review-report.schema.json`", policy)
        self.assertIn("the installed review-report schema", policy)

        for gate in (
            ROOT / ".github" / "workflows" / "awf-review-gate.yml",
            COPILOT_SCAFFOLD / ".github" / "workflows" / "awf-review-gate.yml",
        ):
            text = gate.read_text(encoding="utf-8")
            self.assertNotIn("docs/REVIEW_POLICY.md", text)
            self.assertIn("the protected review", text)

    def test_gate_source_and_dogfood_target_are_identical(self):
        source = COPILOT_SCAFFOLD / ".github" / "workflows" / "awf-review-gate.yml"
        installed = ROOT / ".github" / "workflows" / "awf-review-gate.yml"
        self.assertEqual(source.read_bytes(), installed.read_bytes())

    def test_copilot_instruction_sources_have_one_terminal_newline(self):
        paths = (
            COPILOT_SCAFFOLD / ".github" / "copilot-instructions.md",
            COPILOT_SCAFFOLD / ".github" / "instructions" / "awf-review.instructions.md",
        )
        for path in paths:
            with self.subTest(path=path):
                content = path.read_bytes()
                self.assertTrue(content.endswith(b"\n"))
                self.assertFalse(content.endswith(b"\n\n"))

    def test_clean_copilot_bootstrap_passes_diff_check(self):
        mappings = (
            (
                ROOT / "docs" / "REVIEW_POLICY.md",
                Path("docs/agent-runtime/review-policy.md"),
            ),
            (
                COPILOT_SCAFFOLD / ".github" / "copilot-instructions.md",
                Path(".github/copilot-instructions.md"),
            ),
            (
                COPILOT_SCAFFOLD / ".github" / "instructions" / "awf-review.instructions.md",
                Path(".github/instructions/awf-review.instructions.md"),
            ),
            (
                COPILOT_SCAFFOLD / ".github" / "workflows" / "awf-review-gate.yml",
                Path(".github/workflows/awf-review-gate.yml"),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init = run_git(repo, "init", "--quiet")
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertEqual(run_git(repo, "config", "core.autocrlf", "false").returncode, 0)
            (repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
            for source, target in mappings:
                destination = repo / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            added = run_git(repo, "add", ".")
            self.assertEqual(added.returncode, 0, added.stderr)
            checked = run_git(repo, "diff", "--cached", "--check")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
