import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import build_release  # noqa: E402
from agentic import ValidationError  # noqa: E402


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ManifestConflictTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-manifest-")
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def manifests(self, files):
        return build_release.render_manifests(
            {name: digest(data) for name, data in sorted(files.items())},
            version="1.9.4",
        )

    def write_manifests(self, root, files):
        machine, advisory = self.manifests(files)
        (root / "MANIFEST.json").write_bytes(machine)
        (root / "MANIFEST.md").write_bytes(advisory)

    def git(self, root, *arguments, check=True):
        environment = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if check and completed.returncode:
            self.fail(
                f"git {' '.join(arguments)} failed ({completed.returncode}):\n"
                + completed.stdout.decode(errors="replace")
                + completed.stderr.decode(errors="replace")
            )
        return completed

    def unconfigured_git_environment(self):
        home = self.base / "isolated-home"
        home.mkdir(exist_ok=True)
        environment = {
            **os.environ,
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.base / "isolated-gitconfig"),
        }
        for name in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            environment.pop(name, None)
        return environment

    def remove_repository_identity(self, root):
        self.git(root, "config", "--unset-all", "user.name")
        self.git(root, "config", "--unset-all", "user.email")

    def run_driver(self, root, *arguments):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts/manifest_merge_driver.py"),
                *arguments,
            ],
            cwd=root,
            env=self.unconfigured_git_environment(),
            capture_output=True,
            check=False,
            timeout=30,
        )

    def run_checkout_python(self, root, relative, *arguments):
        environment = self.unconfigured_git_environment()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-B", str(root / relative), *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            check=False,
            timeout=60,
        )

    def commit(self, root, message):
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", message)
        return self.git(root, "rev-parse", "HEAD").stdout.decode().strip()

    def make_repository(self, name, configure_driver=True):
        root = self.base / name
        root.mkdir()
        files = {
            "alpha.txt": b"alpha base\n",
            "middle.txt": b"middle base\n",
            "omega.txt": b"omega base\n",
        }
        for name, data in files.items():
            (root / name).write_bytes(data)
        shutil.copy2(ROOT / ".gitattributes", root / ".gitattributes")
        self.write_manifests(root, files)
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Manifest Fixture")
        self.git(root, "config", "user.email", "manifest@example.invalid")
        if configure_driver:
            command = (
                f'"{sys.executable}" -B "{ROOT / "scripts/manifest_merge_driver.py"}" '
                "%O %A %B %P"
            )
            self.git(root, "config", "merge.awf-manifest.driver", command)
        base = self.commit(root, "base")
        return root, files, base

    def branch_change(self, root, base, branch, files, name, data):
        self.git(root, "checkout", "-b", branch, base)
        files = dict(files)
        files[name] = data
        (root / name).write_bytes(data)
        self.write_manifests(root, files)
        return self.commit(root, branch)

    def branch_delete(self, root, base, branch, files, name):
        self.git(root, "checkout", "-b", branch, base)
        files = dict(files)
        del files[name]
        (root / name).unlink()
        self.write_manifests(root, files)
        return self.commit(root, branch)

    def assert_current(self, root):
        files = {
            path.name: path.read_bytes()
            for path in root.glob("*.txt")
        }
        machine, advisory = self.manifests(files)
        build_release.check_manifest_files(root, machine, advisory)

    def assert_merges_in_either_order(self, root, left, right):
        for branch, first, second in (
            ("a-then-b", left, right),
            ("b-then-a", right, left),
        ):
            with self.subTest(branch=branch):
                self.git(root, "checkout", "-b", branch, first)
                self.git(root, "merge", "--no-edit", second)
                unresolved = self.git(
                    root, "diff", "--name-only", "--diff-filter=U"
                ).stdout.decode().splitlines()
                self.assertEqual([], unresolved)
                self.assert_current(root)

    def test_adjacent_disjoint_additions_merge_in_either_order(self):
        root, files, base = self.make_repository("additions")
        left = self.branch_change(
            root, base, "pr-a", files, "aardvark-a.txt", b"added by A\n"
        )
        right = self.branch_change(
            root, base, "pr-b", files, "aardvark-b.txt", b"added by B\n"
        )
        self.assert_merges_in_either_order(root, left, right)

    def test_adjacent_disjoint_modifications_merge_in_either_order(self):
        root, files, base = self.make_repository("modifications")
        left = self.branch_change(
            root, base, "pr-a", files, "alpha.txt", b"alpha from A\n"
        )
        right = self.branch_change(
            root, base, "pr-b", files, "middle.txt", b"middle from B\n"
        )
        self.assert_merges_in_either_order(root, left, right)

    def test_adjacent_disjoint_deletions_merge_in_either_order(self):
        root, files, base = self.make_repository("deletions")
        left = self.branch_delete(root, base, "pr-a", files, "alpha.txt")
        right = self.branch_delete(root, base, "pr-b", files, "middle.txt")
        self.assert_merges_in_either_order(root, left, right)

    def test_merge_ref_uses_invocation_identity_when_pr_is_behind_main(self):
        root, files, base = self.make_repository(
            "behind-main", configure_driver=False
        )
        self.branch_change(
            root, base, "pr", files, "aardvark.txt", b"added by PR\n"
        )
        self.git(root, "checkout", "main")
        main_files = dict(files)
        main_files["zebra.txt"] = b"added by main\n"
        (root / "zebra.txt").write_bytes(main_files["zebra.txt"])
        self.write_manifests(root, main_files)
        main = self.commit(root, "advance main")
        self.git(root, "checkout", "pr")
        self.remove_repository_identity(root)

        merged = self.run_driver(root, "--merge-ref", main)

        self.assertEqual(
            0,
            merged.returncode,
            merged.stdout.decode(errors="replace")
            + merged.stderr.decode(errors="replace"),
        )
        unresolved = self.git(
            root, "diff", "--name-only", "--diff-filter=U"
        ).stdout.decode().splitlines()
        self.assertEqual([], unresolved)
        self.assert_current(root)

    def test_merge_ref_leaves_up_to_date_pr_unchanged_without_identity(self):
        root, files, base = self.make_repository(
            "up-to-date", configure_driver=False
        )
        pr = self.branch_change(
            root, base, "pr", files, "aardvark.txt", b"added by PR\n"
        )
        self.remove_repository_identity(root)

        merged = self.run_driver(root, "--merge-ref", base)

        self.assertEqual(
            0,
            merged.returncode,
            merged.stdout.decode(errors="replace")
            + merged.stderr.decode(errors="replace"),
        )
        head = self.git(root, "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(pr, head)
        status = self.git(root, "status", "--porcelain").stdout.decode()
        self.assertEqual("", status)
        self.assert_current(root)

    def test_merge_ref_still_fails_on_non_manifest_conflict(self):
        root, files, base = self.make_repository(
            "content-conflict", configure_driver=False
        )
        self.branch_change(
            root, base, "pr", files, "alpha.txt", b"alpha from PR\n"
        )
        self.git(root, "checkout", "main")
        main_files = dict(files)
        main_files["alpha.txt"] = b"alpha from main\n"
        (root / "alpha.txt").write_bytes(main_files["alpha.txt"])
        self.write_manifests(root, main_files)
        main = self.commit(root, "conflicting main change")
        self.git(root, "checkout", "pr")
        self.remove_repository_identity(root)

        merged = self.run_driver(root, "--merge-ref", main)

        self.assertNotEqual(0, merged.returncode)
        unresolved = self.git(
            root, "diff", "--name-only", "--diff-filter=U"
        ).stdout.decode().splitlines()
        self.assertEqual(["alpha.txt"], unresolved)

    def test_manifest_check_sequence_leaves_no_bytecode_and_rejects_ignored_residue(self):
        root = self.base / "manifest-check"
        shutil.copytree(
            ROOT,
            root,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".tmp", "tmp", ".tmp-tests"
            ),
        )
        # The fixture deliberately tests ignored bytecode residue. A closed
        # source ZIP omits repository-only ignore policy, so declare this
        # synthetic fixture's policy without changing the shipped source.
        if not (root / ".gitignore").exists():
            shipped = (root / ".agentic/templates/operating.gitignore").read_bytes()
            (root / ".gitignore").write_bytes(
                shipped + b"\n__pycache__/\n*.pyc\n.tmp-tests/\n")
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Manifest Fixture")
        self.git(root, "config", "user.email", "manifest@example.invalid")
        generated = self.run_checkout_python(
            root, "scripts/build_release.py", "--manifest-only"
        )
        self.assertEqual(
            0,
            generated.returncode,
            generated.stdout.decode(errors="replace")
            + generated.stderr.decode(errors="replace"),
        )
        base = self.commit(root, "base")

        self.git(root, "checkout", "-b", "pr", base)
        (root / "pr-marker.txt").write_text(
            "added by PR\n", encoding="utf-8", newline="\n"
        )
        generated = self.run_checkout_python(
            root, "scripts/build_release.py", "--manifest-only"
        )
        self.assertEqual(
            0, generated.returncode, generated.stderr.decode(errors="replace")
        )
        self.commit(root, "advance PR")

        self.git(root, "checkout", "main")
        (root / "main-marker.txt").write_text(
            "added by main\n", encoding="utf-8", newline="\n"
        )
        generated = self.run_checkout_python(
            root, "scripts/build_release.py", "--manifest-only"
        )
        self.assertEqual(
            0, generated.returncode, generated.stderr.decode(errors="replace")
        )
        main = self.commit(root, "advance main")
        self.git(root, "checkout", "pr")
        self.remove_repository_identity(root)

        merged = self.run_checkout_python(
            root, "scripts/manifest_merge_driver.py", "--merge-ref", main
        )
        self.assertEqual(
            0,
            merged.returncode,
            merged.stdout.decode(errors="replace")
            + merged.stderr.decode(errors="replace"),
        )
        checked = self.run_checkout_python(
            root, "scripts/build_release.py", "--check-manifest"
        )
        self.assertEqual(
            0,
            checked.returncode,
            checked.stdout.decode(errors="replace")
            + checked.stderr.decode(errors="replace"),
        )
        residue = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.name == "__pycache__" or path.suffix == ".pyc"
        ]
        self.assertEqual([], residue)

        ignored = root / "scripts/__pycache__/ignored.pyc"
        ignored.parent.mkdir()
        ignored.write_bytes(b"ignored bytecode residue\n")
        self.assertEqual(
            0,
            self.git(
                root, "check-ignore", "-q", ignored.relative_to(root).as_posix()
            ).returncode,
        )
        rejected = self.run_checkout_python(
            root, "scripts/build_release.py", "--check-manifest"
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn(
            "Source contains runtime/build residue",
            rejected.stderr.decode(errors="replace"),
        )

    def test_production_sync_merges_and_pushes_without_repository_config(self):
        root, files, base = self.make_repository(
            "production-sync", configure_driver=False
        )
        left = self.branch_change(
            root, base, "pr-a", files, "aardvark-a.txt", b"added by A\n"
        )
        right = self.branch_change(
            root, base, "pr-b", files, "aardvark-b.txt", b"added by B\n"
        )
        remote = self.base / "provider.git"
        self.git(self.base, "init", "--bare", str(remote))
        self.git(root, "remote", "add", "origin", str(remote))
        self.git(root, "push", "origin", f"{left}:refs/heads/pr-a")
        self.git(root, "checkout", "-B", "pr-a", left)
        self.remove_repository_identity(root)

        configured = self.git(
            root, "config", "--get", "merge.awf-manifest.driver", check=False
        )
        self.assertNotEqual(0, configured.returncode)
        merged = self.run_driver(
            root,
            "--sync-ref",
            right,
            "--push-branch",
            "pr-a",
        )
        self.assertEqual(
            0,
            merged.returncode,
            merged.stdout.decode(errors="replace")
            + merged.stderr.decode(errors="replace"),
        )
        self.assertNotEqual(
            0,
            self.git(
                root,
                "config",
                "--get",
                "merge.awf-manifest.driver",
                check=False,
            ).returncode,
        )
        unresolved = self.git(
            root, "diff", "--name-only", "--diff-filter=U"
        ).stdout.decode().splitlines()
        self.assertEqual([], unresolved)
        self.assert_current(root)
        pushed = self.git(
            root, "ls-remote", "--heads", "origin", "refs/heads/pr-a"
        ).stdout.decode().split()[0]
        self.assertEqual(self.git(root, "rev-parse", "HEAD").stdout.decode().strip(), pushed)
        self.assertNotEqual(left, pushed)
        parents = self.git(root, "show", "-s", "--format=%P", pushed).stdout.decode().split()
        self.assertEqual([left, right], parents)

    def test_workflow_dispatches_authorized_production_manifest_sync(self):
        workflow = (ROOT / ".github/workflows/manifest-check.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("pull-request:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("pull-requests: read", workflow)
        self.assertIn("pull.head.repo.full_name", workflow)
        self.assertIn("pull.base.ref !== context.payload.repository.default_branch", workflow)
        self.assertIn("path: trusted", workflow)
        self.assertIn("path: candidate", workflow)
        self.assertIn("working-directory: candidate", workflow)
        self.assertIn(
            "python -B ../trusted/scripts/manifest_merge_driver.py --sync-ref",
            workflow,
        )
        self.assertIn("--push-branch", workflow)

    def test_workflow_disables_bytecode_for_every_python_step(self):
        workflow = (ROOT / ".github/workflows/manifest-check.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            workflow,
            r'(?m)^env:\n  PYTHONDONTWRITEBYTECODE: ["\']1["\']$',
        )

    def test_workflow_scopes_matrix_concurrency_to_manifest_job(self):
        workflow = (ROOT / ".github/workflows/manifest-check.yml").read_text(
            encoding="utf-8"
        )
        self.assertIsNone(re.search(r"(?m)^concurrency:$", workflow))
        self.assertRegex(
            workflow,
            r"(?ms)^jobs:\n  manifest:\n"
            r"(?:    [^\n]*\n)*?    concurrency:\n"
            r"      group: manifest-check-\$\{\{ github\.ref \}\}-"
            r"\$\{\{ matrix\.os \}\}-\$\{\{ matrix\.python-version \}\}\n",
        )

    def test_local_driver_keeps_same_file_conflict_out_of_manifests(self):
        root, files, base = self.make_repository("same-file")
        left = self.branch_change(root, base, "same-a", files, "alpha.txt", b"alpha from A\n")
        right = self.branch_change(root, base, "same-b", files, "alpha.txt", b"alpha from B\n")

        self.git(root, "checkout", "-b", "same-merge", left)
        merged = self.git(root, "merge", "--no-edit", right, check=False)
        self.assertNotEqual(0, merged.returncode)
        unresolved = self.git(root, "diff", "--name-only", "--diff-filter=U").stdout.decode().splitlines()
        self.assertEqual(["alpha.txt"], unresolved)
        self.git(root, "checkout", "--ours", "alpha.txt")
        self.git(root, "add", "alpha.txt")
        self.assert_current(root)

    def test_check_rejects_missing_stale_and_hand_edited_manifests(self):
        root = self.base / "check"
        root.mkdir()
        files = {"alpha.txt": b"alpha\n", "omega.txt": b"omega\n"}
        machine, advisory = self.manifests(files)

        with self.assertRaisesRegex(ValidationError, "missing"):
            build_release.check_manifest_files(root, machine, advisory)

        (root / "MANIFEST.json").write_bytes(machine)
        (root / "MANIFEST.md").write_bytes(advisory)
        build_release.check_manifest_files(root, machine, advisory)

        alpha_json_row = (
            f'    "alpha.txt": "{digest(files["alpha.txt"])}",\n'.encode()
        )
        (root / "MANIFEST.json").write_bytes(
            machine.replace(alpha_json_row, alpha_json_row + alpha_json_row)
        )
        with self.assertRaisesRegex(ValidationError, "MANIFEST.json"):
            build_release.check_manifest_files(root, machine, advisory)

        (root / "MANIFEST.json").write_bytes(machine)
        alpha_advisory_row = (
            f'| `alpha.txt` | `{digest(files["alpha.txt"])}` |\n'.encode()
        )
        (root / "MANIFEST.md").write_bytes(
            advisory.replace(
                alpha_advisory_row, alpha_advisory_row + alpha_advisory_row
            )
        )
        with self.assertRaisesRegex(ValidationError, "MANIFEST.md"):
            build_release.check_manifest_files(root, machine, advisory)

        missing_entry = json.loads(machine)
        del missing_entry["files"]["alpha.txt"]
        (root / "MANIFEST.json").write_text(
            json.dumps(missing_entry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaisesRegex(ValidationError, "MANIFEST.json"):
            build_release.check_manifest_files(root, machine, advisory)

        stale_entry = json.loads(machine)
        stale_entry["files"]["alpha.txt"] = "0" * 64
        (root / "MANIFEST.json").write_text(
            json.dumps(stale_entry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaisesRegex(ValidationError, "MANIFEST.json"):
            build_release.check_manifest_files(root, machine, advisory)

        (root / "MANIFEST.json").write_bytes(machine)
        (root / "MANIFEST.md").write_bytes(advisory + b"hand edit\n")
        with self.assertRaisesRegex(ValidationError, "MANIFEST.md"):
            build_release.check_manifest_files(root, machine, advisory)

    def test_render_is_deterministic_conflict_free_and_migrates_old_advisory(self):
        files = {"omega.txt": b"omega\n", "alpha.txt": b"alpha\n"}
        first = self.manifests(files)
        second = self.manifests(dict(reversed(list(files.items()))))
        self.assertEqual(first, second)
        self.assertNotIn(b"content files are", first[1])
        self.assertNotIn(b"MANIFEST.json SHA-256", first[1])
        self.assertNotIn(b"\r", first[0] + first[1])

        root = self.base / "migration"
        root.mkdir()
        (root / "MANIFEST.json").write_text("{}\n", encoding="utf-8")
        (root / "MANIFEST.md").write_text(
            "2 content files are SHA-256 listed in MANIFEST.json.\n"
            "MANIFEST.json SHA-256: `old`\n",
            encoding="utf-8",
        )
        build_release.write_manifest_files(root, *first)
        self.assertEqual(first[0], (root / "MANIFEST.json").read_bytes())
        self.assertEqual(first[1], (root / "MANIFEST.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
