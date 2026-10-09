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

    def commit(self, root, message):
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", message)
        return self.git(root, "rev-parse", "HEAD").stdout.decode().strip()

    def make_repository(self, *, driver=False):
        root = self.base / ("driver" if driver else "server")
        root.mkdir()
        files = {
            "alpha.txt": b"alpha base\n",
            "middle.txt": b"middle base\n",
            "omega.txt": b"omega base\n",
        }
        for name, data in files.items():
            (root / name).write_bytes(data)
        shutil.copy2(ROOT / ".gitattributes", root / ".gitattributes")
        if driver:
            attributes = (root / ".gitattributes").read_text(encoding="utf-8")
            (root / ".gitattributes").write_text(
                attributes.replace("merge=union", "merge=awf-manifest"),
                encoding="utf-8",
                newline="\n",
            )
            shutil.copy2(ROOT / "scripts/manifest_merge_driver.py", root / "manifest_merge_driver.py")
        self.write_manifests(root, files)
        self.git(root, "init", "-b", "main")
        self.git(root, "config", "user.name", "Manifest Fixture")
        self.git(root, "config", "user.email", "manifest@example.invalid")
        if driver:
            command = (
                f'"{sys.executable}" -B "{root / "manifest_merge_driver.py"}" '
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

    def assert_current(self, root):
        files = {
            path.name: path.read_bytes()
            for path in root.glob("*.txt")
        }
        machine, advisory = self.manifests(files)
        build_release.check_manifest_files(root, machine, advisory)

    def test_disjoint_prs_merge_without_manifest_conflict_in_either_order(self):
        root, files, base = self.make_repository()
        left = self.branch_change(
            root, base, "pr-a", files, "aardvark-a.txt", b"added by A\n"
        )
        right = self.branch_change(
            root, base, "pr-b", files, "aardvark-b.txt", b"added by B\n"
        )

        self.git(root, "checkout", "-b", "a-then-b", left)
        self.git(root, "merge", "--no-edit", right)
        self.assert_current(root)

        self.git(root, "checkout", "-b", "b-then-a", right)
        self.git(root, "merge", "--no-edit", left)
        self.assert_current(root)

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
        root, files, base = self.make_repository(driver=True)
        left = self.branch_change(root, base, "same-a", files, "alpha.txt", b"alpha from A\n")
        right = self.branch_change(root, base, "same-b", files, "alpha.txt", b"alpha from B\n")

        self.git(root, "checkout", "-b", "same-merge", left)
        merged = self.git(root, "merge", "--no-edit", right, check=False)
        self.assertNotEqual(0, merged.returncode)
        unresolved = self.git(root, "diff", "--name-only", "--diff-filter=U").stdout.decode().splitlines()
        self.assertEqual(["alpha.txt"], unresolved)

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
