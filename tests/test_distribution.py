import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COPILOT_SCAFFOLD = ROOT / "scaffolds" / "providers" / "copilot"
GIT = shutil.which("git")
SOURCE_POLICY_PATH = "docs/REVIEW_POLICY.md"
INSTALLED_POLICY_PATH = "docs/agent-runtime/review-policy.md"

EXPECTED_DOGFOOD_TARGETS = {
    ".agentic/project.yaml",
    ".agentic/review/claude/awf_review_common.py",
    ".agentic/review/claude/build_input.py",
    ".agentic/review/claude/policy.py",
    ".agentic/review/claude/publish.py",
    ".agentic/review/claude/run_model.py",
    ".agentic/review/claude/tests/__init__.py",
    ".agentic/review/claude/tests/test_claude_adapter.py",
    ".agentic/review/claude/verify.py",
    ".agentic/schemas/review-report.schema.json",
    ".github/workflows/awf-review-claude.yml",
    ".github/workflows/awf-review-claude-demonstrate.yml",
    ".github/workflows/awf-review-gate.yml",
}
EXPECTED_COPILOT_MAPPINGS = {
    ("scaffolds/project/AGENTS.md.template", "AGENTS.md"),
    ("scaffolds/project/ARCHITECTURE.md.template", "ARCHITECTURE.md"),
    ("scaffolds/project/.agentic/project.yaml.template", ".agentic/project.yaml"),
    ("docs/providers/codex.md", "docs/agent-runtime/codex.md"),
    ("docs/REVIEW_POLICY.md", "docs/agent-runtime/review-policy.md"),
    ("docs/THREAT_MODEL.md", "docs/agent-runtime/threat-model.md"),
    ("docs/IDENTITY_AND_AUDIT.md", "docs/agent-runtime/identity-and-audit.md"),
    ("docs/INCIDENT_RESPONSE.md", "docs/agent-runtime/incident-response.md"),
    ("schemas/review-report.schema.json", ".agentic/schemas/review-report.schema.json"),
    ("docs/providers/copilot.md", "docs/agent-runtime/copilot-review.md"),
    (
        "scaffolds/providers/copilot/.github/copilot-instructions.md",
        ".github/copilot-instructions.md",
    ),
    (
        "scaffolds/providers/copilot/.github/instructions/awf-review.instructions.md",
        ".github/instructions/awf-review.instructions.md",
    ),
    (
        "scaffolds/providers/copilot/.github/workflows/awf-review-gate.yml",
        ".github/workflows/awf-review-gate.yml",
    ),
    ("scaffolds/project/CODEOWNERS.fragment", ".github/CODEOWNERS"),
}


def isolated_git_env(index_file=None):
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    return env


def run_git(repo, *args, index_file=None, text=True):
    if GIT is None:
        raise FileNotFoundError("git is not installed")
    return subprocess.run(
        [
            GIT,
            "-c",
            "init.templateDir=",
            "-c",
            f"safe.directory={repo}",
            "-C",
            str(repo),
            *args,
        ],
        check=False,
        capture_output=True,
        text=text,
        env=isolated_git_env(index_file),
    )


def has_one_terminal_newline(content):
    return content.endswith(b"\n") and not content.endswith(b"\n\n")


def blobs_match(left, right):
    return left == right


def executable_yaml(content):
    return b"\n".join(
        line for line in content.splitlines() if line.strip() and not line.lstrip().startswith(b"#")
    )


def manifest_artifacts(text):
    artifacts = []
    for block in re.split(r"(?m)^  - source: ", text)[1:]:
        source = block.splitlines()[0]
        target = re.search(r"(?m)^    target: (.+)$", block)
        policy = re.search(r"(?m)^    policy: (.+)$", block)
        requires = re.search(r"(?m)^    requires: (.+)$", block)
        if target is None or policy is None:
            raise AssertionError(f"incomplete manifest artifact: {source}")
        artifacts.append(
            {
                "source": source,
                "target": target.group(1),
                "policy": policy.group(1),
                "requires": requires.group(1) if requires else "",
            }
        )
    return artifacts


def selected_copilot_mappings(text):
    mappings = []
    for artifact in manifest_artifacts(text):
        if artifact["source"] == "generated" or "claude" in artifact["requires"]:
            continue
        mappings.append((ROOT / artifact["source"], Path(artifact["target"])))
    return mappings


def lock_value(text, pattern):
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise AssertionError(f"lock field not found: {pattern}")
    return match.group(1)


class DistributionTests(unittest.TestCase):
    def require_git(self):
        self.assertIsNotNone(GIT, "Git is required for distribution provenance tests")

    def test_current_version_is_consistent(self):
        manifest = (ROOT / ".agentic-workflow" / "distribution-manifest.yaml").read_text(
            encoding="utf-8"
        )
        version = re.search(r"(?m)^  version: ([0-9]+\.[0-9]+\.[0-9]+)$", manifest)
        self.assertIsNotNone(version)
        expected = version.group(1)
        statements = (
            (ROOT / "README.md", r"Version `([0-9]+\.[0-9]+\.[0-9]+)`"),
            (ROOT / "docs" / "GETTING_STARTED.md", r"AWF v([0-9]+\.[0-9]+\.[0-9]+) uses"),
            (ROOT / "docs" / "SYSTEM_ARCHITECTURE.md", r"AWF v([0-9]+\.[0-9]+\.[0-9]+) remains"),
        )
        for path, pattern in statements:
            with self.subTest(path=path):
                match = re.search(pattern, path.read_text(encoding="utf-8"))
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), expected)

    def test_managed_references_use_installed_layout(self):
        policy = (ROOT / "docs" / "REVIEW_POLICY.md").read_text(encoding="utf-8")
        self.assertNotIn("`schemas/review-report.schema.json`", policy)
        self.assertIn("`.agentic/schemas/review-report.schema.json`", policy)

        for gate in (
            ROOT / ".github" / "workflows" / "awf-review-gate.yml",
            COPILOT_SCAFFOLD / ".github" / "workflows" / "awf-review-gate.yml",
        ):
            with self.subTest(path=gate):
                text = gate.read_text(encoding="utf-8")
                self.assertIn(SOURCE_POLICY_PATH, text)
                self.assertIn(INSTALLED_POLICY_PATH, text)

    def test_gate_source_and_dogfood_target_are_identical(self):
        source = COPILOT_SCAFFOLD / ".github" / "workflows" / "awf-review-gate.yml"
        installed = ROOT / ".github" / "workflows" / "awf-review-gate.yml"
        self.assertTrue(blobs_match(source.read_bytes(), installed.read_bytes()))

    def test_gate_behavior_unchanged_from_task_base(self):
        self.require_git()
        contract = (ROOT / ".agentic" / "task-contract.yaml").read_text(encoding="utf-8")
        base_ref = re.search(r"(?m)^base_ref: ([0-9a-f]+)$", contract)
        self.assertIsNotNone(base_ref)
        current = (ROOT / ".github" / "workflows" / "awf-review-gate.yml").read_bytes()
        base = run_git(
            ROOT,
            "cat-file",
            "blob",
            f"{base_ref.group(1)}:.github/workflows/awf-review-gate.yml",
            text=False,
        )
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertEqual(executable_yaml(current), executable_yaml(base.stdout))

    def test_copilot_instruction_changes_have_one_terminal_newline(self):
        paths = (
            COPILOT_SCAFFOLD / ".github" / "copilot-instructions.md",
            COPILOT_SCAFFOLD / ".github" / "instructions" / "awf-review.instructions.md",
            ROOT / ".github" / "copilot-instructions.md",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(has_one_terminal_newline(path.read_bytes()))

    def test_guard_helpers_reject_regressions(self):
        self.assertFalse(has_one_terminal_newline(b"text\n\n"))
        self.assertFalse(blobs_match(b"left\n", b"right\n"))
        malformed_manifest = "  - source: missing.txt\n    policy: managed\n"
        with self.assertRaises(AssertionError):
            manifest_artifacts(malformed_manifest)

    def test_clean_copilot_bootstrap_passes_diff_check(self):
        self.require_git()
        manifest = (ROOT / ".agentic-workflow" / "distribution-manifest.yaml").read_text(
            encoding="utf-8"
        )
        mappings = selected_copilot_mappings(manifest)
        actual_mappings = {
            (source.relative_to(ROOT).as_posix(), target.as_posix())
            for source, target in mappings
        }
        self.assertEqual(actual_mappings, EXPECTED_COPILOT_MAPPINGS)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            init = run_git(repo, "init", "--quiet")
            self.assertEqual(init.returncode, 0, init.stderr)
            for source, target in mappings:
                self.assertTrue(source.is_file(), f"manifest source is missing: {source}")
                self.assertNotIn(b"\r", source.read_bytes(), f"non-LF source: {source}")
                destination = repo / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            added = run_git(repo, "add", ".")
            self.assertEqual(added.returncode, 0, added.stderr)
            checked = run_git(repo, "diff", "--cached", "--check")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_dogfood_lock_reproduces(self):
        self.require_git()
        lock = (ROOT / ".agentic" / "workflow.lock.yaml").read_text(encoding="utf-8")
        source_commit = lock_value(lock, r"^  source_base_commit: ([0-9a-f]+)$")
        expected_tree = lock_value(lock, r"^  source_tree_without_lock: ([0-9a-f]+)$")
        bootstrap_commit = lock_value(lock, r"^  base_commit: ([0-9a-f]+)$")
        bootstrap_ancestor = run_git(
            ROOT, "merge-base", "--is-ancestor", bootstrap_commit, source_commit
        )
        self.assertEqual(
            bootstrap_ancestor.returncode,
            0,
            "bootstrap.base_commit must remain the original ancestor of the current source: "
            + bootstrap_ancestor.stderr,
        )
        ancestor = run_git(ROOT, "merge-base", "--is-ancestor", source_commit, "HEAD")
        self.assertEqual(
            ancestor.returncode,
            0,
            "the lock source commit must exist in a full clone and be an ancestor of HEAD: "
            + ancestor.stderr,
        )

        manifest = re.search(
            r"(?ms)^manifest:\n  path: (?P<path>[^\n]+)\n"
            r"  git_blob_oid: (?P<blob>[0-9a-f]+)\n  sha256: (?P<sha>[0-9a-f]+)$",
            lock,
        )
        self.assertIsNotNone(manifest)
        manifest_path = manifest.group("path")
        source_manifest = run_git(
            ROOT, "cat-file", "blob", f"{source_commit}:{manifest_path}", text=False
        )
        self.assertEqual(source_manifest.returncode, 0, source_manifest.stderr)
        manifest_blob = run_git(ROOT, "rev-parse", f"{source_commit}:{manifest_path}")
        self.assertEqual(manifest_blob.returncode, 0, manifest_blob.stderr)
        self.assertEqual(manifest_blob.stdout.strip(), manifest.group("blob"))
        self.assertEqual(hashlib.sha256(source_manifest.stdout).hexdigest(), manifest.group("sha"))

        with tempfile.TemporaryDirectory() as directory:
            index_file = Path(directory) / "index"
            read_tree = run_git(ROOT, "read-tree", source_commit, index_file=index_file)
            self.assertEqual(read_tree.returncode, 0, read_tree.stderr)
            removed = run_git(
                ROOT,
                "update-index",
                "--force-remove",
                "--",
                ".agentic/workflow.lock.yaml",
                index_file=index_file,
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            actual_tree = run_git(ROOT, "write-tree", index_file=index_file)
            self.assertEqual(actual_tree.returncode, 0, actual_tree.stderr)
            self.assertEqual(actual_tree.stdout.strip(), expected_tree)

        blocks = list(
            re.finditer(
                r"(?ms)^  - target: (?P<target>[^\n]+)\n"
                r"    source: (?P<source>[^\n]+)\n"
                r"    policy: (?P<policy>[^\n]+)\n"
                r"    source_git_blob_oid: (?P<source_blob>[0-9a-f]+)\n"
                r"    installed_git_blob_oid: (?P<installed_blob>[0-9a-f]+)\n"
                r"    source_sha256: (?P<source_sha>[0-9a-f]+)\n"
                r"    installed_sha256: (?P<installed_sha>[0-9a-f]+)$",
                lock,
            )
        )
        self.assertEqual({block.group("target") for block in blocks}, EXPECTED_DOGFOOD_TARGETS)
        for block in blocks:
            source_spec = f"{source_commit}:{block.group('source')}"
            target_spec = f"{source_commit}:{block.group('target')}"
            source_blob = run_git(ROOT, "rev-parse", source_spec)
            installed_blob = run_git(ROOT, "rev-parse", target_spec)
            self.assertEqual(source_blob.returncode, 0, source_blob.stderr)
            self.assertEqual(installed_blob.returncode, 0, installed_blob.stderr)
            self.assertEqual(source_blob.stdout.strip(), block.group("source_blob"))
            self.assertEqual(installed_blob.stdout.strip(), block.group("installed_blob"))
            source_content = run_git(ROOT, "cat-file", "blob", source_spec, text=False)
            installed_content = run_git(ROOT, "cat-file", "blob", target_spec, text=False)
            self.assertEqual(source_content.returncode, 0, source_content.stderr)
            self.assertEqual(installed_content.returncode, 0, installed_content.stderr)
            source_bytes = source_content.stdout
            installed_bytes = installed_content.stdout
            self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), block.group("source_sha"))
            self.assertEqual(
                hashlib.sha256(installed_bytes).hexdigest(), block.group("installed_sha")
            )
            if block.group("policy") == "managed":
                self.assertTrue(blobs_match(source_bytes, installed_bytes))


if __name__ == "__main__":
    unittest.main()
