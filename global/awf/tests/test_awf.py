"""Isolated discovery regressions; no released code, network or installer is run."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/awf.py"
spec = importlib.util.spec_from_file_location("awf_discovery", SCRIPT)
awf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(awf)


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


class Fixture:
    def __init__(self, root, version="1.5.0"):
        self.root = root
        self.source = root / "source"
        self.source.mkdir()
        self.archive = root / "release.zip"
        self.catalog = root / "catalog.json"
        self.version = version
        self.content = {
            "AGENTS.md": b"# Synthetic test project instructions\n",
            "README.md": b"# Synthetic AWF release fixture\n",
            awf.CONFIG: encoded({"project": {"name": "synthetic fixture"}}),
            awf.VERSION_FILE: encoded({"template": {"version": version}, "installation": {}}),
            ".agentic/requirements.lock": b"# No external test dependencies\n",
            ".agentic/lib/fixture.py": b"raise RuntimeError('fixture code must never execute')\n",
            "scripts/bootstrap_project.py": b"raise RuntimeError('installer must never execute')\n",
        }
        self.manifest = {"format": "awf-manifest-1", "template_version": version,
                         "files": {name: awf.digest(data) for name, data in self.content.items()}}
        self.content["MANIFEST.json"] = encoded(self.manifest)
        self.content["MANIFEST.md"] = b"Synthetic human manifest\n"
        for name, data in self.content.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.write_archive()

    def write_archive(self, extra=(), transform=None):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in self.content.items():
                    if transform:
                        name, data = transform(name, data)
                    archive.writestr("release/" + name, data)
                for name, data in extra:
                    archive.writestr(name, data)
        self.write_catalog()

    def write_catalog(self):
        self.catalog.write_bytes(encoded({"format": "awf-release-catalog-1", "latest": {
            "version": self.version, "source_directory": str(self.source), "archive": str(self.archive),
            "archive_sha256": awf.digest(self.archive.read_bytes()),
            "manifest_sha256": awf.digest(self.content["MANIFEST.json"]),
        }}))

    def locate(self, version="1.5"):
        return awf.locate(self.catalog, version)

    def install_fixture(self, project):
        project.mkdir()
        immutable = {}
        for name, data in self.content.items():
            if not awf.managed(name):
                continue
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if name not in {awf.CONFIG, awf.VERSION_FILE}:
                immutable[name] = awf.digest(data)
        receipt = {"template_version": self.version,
                   "source_manifest_sha256": awf.digest(self.content["MANIFEST.json"]),
                   "immutable_files": immutable, "initial_config_sha256": awf.digest(self.content[awf.CONFIG]),
                   "mutable_paths": [awf.CONFIG], "install_id": "synthetic-install-identity"}
        (project / awf.RECEIPT).write_bytes(encoded(receipt))
        (project / awf.VERSION_FILE).write_bytes(encoded({"template": {"version": self.version},
            "installation": {"source_manifest_sha256": receipt["source_manifest_sha256"], "install_id": receipt["install_id"]}}))
        return receipt


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-discovery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)

    def assert_rejected(self, code, callback=None):
        with self.assertRaises(awf.DiscoveryError) as raised:
            (callback or self.fixture.locate)()
        self.assertEqual(code, raised.exception.code)
        self.assertIn("action", awf.rejected(raised.exception)["next_action"])
        return raised.exception

    def test_actual_source_and_archive_read_proof_without_execution(self):
        before = {name: path.read_bytes() for name, path in awf.inventory(self.fixture.source).items()}
        with patch.object(subprocess, "run", side_effect=AssertionError("No execution")):
            proof = self.fixture.locate()
        self.assertEqual("VERIFIED_LOCAL_RELEASE", proof["status"])
        self.assertTrue(proof["source_verified"])
        self.assertTrue(proof["archive_verified"])
        self.assertEqual(len(before), proof["source_files_verified"])
        self.assertEqual(awf.digest(self.fixture.catalog.read_bytes()), proof["catalog_sha256"])
        self.assertFalse(proof["release_code_executed"])
        self.assertFalse(proof["adoption_pr_created"])
        self.assertFalse(proof["python_dependencies_verified"])
        self.assertEqual(before, {name: path.read_bytes() for name, path in awf.inventory(self.fixture.source).items()})

    def test_version_family_exact_and_latest_local_only(self):
        self.assertEqual("1.5.0", self.fixture.locate("1.5.0")["version"])
        proof = self.fixture.locate(None)
        self.assertIn("locally registered", proof["version_basis"])
        for selector in ("1.4", "1.6", "1.5.1"):
            with self.subTest(selector=selector):
                self.assert_rejected("REQUESTED_VERSION_UNAVAILABLE", lambda: self.fixture.locate(selector))
        for selector in ("1", "v1.5", "1.05", "latest", "1.5.*"):
            with self.subTest(selector=selector):
                self.assert_rejected("INVALID_VERSION_SELECTOR", lambda: self.fixture.locate(selector))

    def test_future_registered_release_does_not_substitute_for_requested_family(self):
        other = self.root / "future"
        other.mkdir()
        future = Fixture(other, "1.6.0")
        exc = self.assert_rejected("REQUESTED_VERSION_UNAVAILABLE", future.locate)
        self.assertEqual("1.6.0", exc.proof["version"])
        self.assertEqual("1.5", exc.proof["requested_version"])

    def test_catalog_pin_and_strict_duplicate_json(self):
        value = json.loads(self.fixture.catalog.read_bytes())
        value["latest"]["archive_sha256"] = "0" * 64
        self.fixture.catalog.write_bytes(encoded(value))
        self.assert_rejected("ARCHIVE_HASH_MISMATCH")
        self.fixture.catalog.write_bytes(b'{"format":"one","format":"two","latest":{}}')
        self.assert_rejected("INVALID_JSON")

    def test_manifest_pin_and_archive_member_bytes_are_independently_checked(self):
        value = json.loads(self.fixture.catalog.read_bytes())
        value["latest"]["manifest_sha256"] = "0" * 64
        self.fixture.catalog.write_bytes(encoded(value))
        self.assert_rejected("MANIFEST_HASH_MISMATCH")
        self.fixture.write_archive(transform=lambda name, data: (name, data + b"changed") if name == "AGENTS.md" else (name, data))
        self.assert_rejected("ARCHIVE_CONTENT_MISMATCH")

    def test_extra_and_missing_archive_members_rejected(self):
        self.fixture.write_archive(extra=[("release/unexpected.txt", b"extra")])
        self.assert_rejected("ARCHIVE_MEMBERSHIP_MISMATCH")
        original = self.fixture.content.pop("MANIFEST.md")
        self.fixture.write_archive()
        self.assert_rejected("ARCHIVE_MEMBERSHIP_MISMATCH")
        self.fixture.content["MANIFEST.md"] = original

    def test_zip_traversal_duplicates_multiple_roots_and_case_collisions(self):
        for extra, code in [
            ([("release/../escaped", b"no")], "UNSAFE_PATH"),
            ([("release/AGENTS.md", b"duplicate")], "INVALID_ARCHIVE"),
            ([("release/agents.md", b"collision")], "INVALID_ARCHIVE"),
            ([("other/unexpected", b"no")], "INVALID_ARCHIVE"),
            ([("release/.git/config", b"no")], "UNSAFE_PATH"),
        ]:
            with self.subTest(extra=extra):
                self.fixture.write_archive(extra=extra)
                self.assert_rejected(code)
        self.assertFalse((self.root / "escaped").exists())

    def test_symlink_zip_member_rejected(self):
        link = zipfile.ZipInfo("release/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.fixture.write_archive(extra=[(link, b"../target")])
        self.assert_rejected("INVALID_ARCHIVE")

    def test_source_extra_nested_git_and_changed_managed_bytes_rejected(self):
        extra = self.fixture.source / "extra.txt"
        extra.write_bytes(b"extra")
        self.assert_rejected("SOURCE_MEMBERSHIP_MISMATCH")
        extra.unlink()
        nested = self.fixture.source / ".agentic/.git"
        nested.mkdir()
        self.assert_rejected("UNSAFE_PATH")
        nested.rmdir()
        (self.fixture.source / "AGENTS.md").write_bytes(b"different\r\n")
        exc = self.assert_rejected("SOURCE_CONTENT_MISMATCH")
        self.assertTrue(exc.proof["archive_verified"])
        recovery = awf.rejected(exc)
        self.assertEqual("LOCAL_ARCHIVE_VERIFIED_SOURCE_UNAVAILABLE", recovery["status"])
        self.assertFalse(recovery["request_zip_if_no_verified_local_release"])

    def test_manifest_markdown_is_bound_to_archive_even_though_not_self_listed(self):
        (self.fixture.source / "MANIFEST.md").write_bytes(b"modified")
        self.assert_rejected("SOURCE_CONTENT_MISMATCH")

    def test_root_git_directory_and_regular_worktree_pointer_supported(self):
        metadata = self.fixture.source / ".git"
        metadata.mkdir()
        (metadata / "config").write_bytes(b"local Git metadata\r\n")
        self.assertTrue(self.fixture.locate()["source_verified"])
        (metadata / "config").unlink()
        metadata.rmdir()
        metadata.write_bytes(b"gitdir: ../parent.git/worktrees/source\n")
        self.assertTrue(self.fixture.locate()["source_verified"])

    def test_hardlinked_source_member_and_git_metadata_rejected(self):
        for name in ("AGENTS.md", ".git"):
            with self.subTest(name=name):
                path = self.fixture.source / name
                data = path.read_bytes() if path.exists() else None
                if path.exists():
                    path.unlink()
                target = self.root / "hardlink-target"
                target.write_bytes(data or b"metadata")
                os.link(target, path)
                self.assert_rejected("UNSAFE_PATH")
                path.unlink()
                target.unlink()
                if data is not None:
                    path.write_bytes(data)

    def test_parent_traversal_and_relative_catalog_paths_rejected(self):
        for path in (Path("catalog.json"), self.root / ".." / self.root.name / "catalog.json"):
            self.assert_rejected("UNSAFE_PATH", lambda: awf.locate(path, "1.5"))

    def test_missing_source_keeps_verified_local_archive_recovery(self):
        value = json.loads(self.fixture.catalog.read_bytes())
        value["latest"]["source_directory"] = str(self.root / "missing-source")
        self.fixture.catalog.write_bytes(encoded(value))
        exc = self.assert_rejected("LOCAL_FILES_UNAVAILABLE")
        result = awf.rejected(exc)
        self.assertEqual("LOCAL_ARCHIVE_VERIFIED_SOURCE_UNAVAILABLE", result["status"])
        self.assertIn("Recover", result["next_action"]["action"])
        self.assertFalse(result["request_zip_if_no_verified_local_release"])
        self.assertFalse((self.root / "missing-source").exists())

    def test_new_project_inspection_emits_read_only_command_without_creating_project(self):
        project = self.root / "new-project"
        proof = self.fixture.locate()
        result = awf.inspect_project(proof, project)
        self.assertEqual("NOT_INSTALLED", result["installed"]["status"])
        self.assertFalse(project.exists())
        command = result["installer_dry_run_command"]
        self.assertEqual(["--mode", "install", "--on-conflict", "backup", "--dry-run"], command[-5:])
        self.assertIn(proof["manifest_sha256"], command)
        self.assertIsNone(result["installer_apply_command"])
        self.assertFalse(result["adoption_pr_created"])
        self.assertIn("isolated adoption/upgrade PR", result["next_action"]["action"])

    def test_existing_project_config_and_agents_are_observed_and_preserved(self):
        project = self.root / "brownfield"
        (project / ".agentic").mkdir(parents=True)
        config = b"project:\n  name: Existing real project\n"
        instructions = b"Preserve project instructions\n"
        (project / awf.CONFIG).write_bytes(config)
        (project / "AGENTS.md").write_bytes(instructions)
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("EXISTING_UNRECEIPTED_FILES", result["installed"]["status"])
        self.assertTrue(result["adoption_plan"]["configuration_mapping_required"])
        self.assertTrue(result["adoption_plan"]["agents_reconciliation_required"])
        self.assertEqual(config, (project / awf.CONFIG).read_bytes())
        self.assertEqual(instructions, (project / "AGENTS.md").read_bytes())

    def test_matching_installed_receipt_is_verified_against_catalog_not_self_trusted(self):
        project = self.root / "installed"
        receipt = self.fixture.install_fixture(project)
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("REGISTERED_RELEASE_BYTES_VERIFIED", result["installed"]["status"])
        self.assertFalse(result["installed"]["configuration_validated"])
        command = result["installer_dry_run_command"]
        self.assertEqual("upgrade", command[command.index("--mode") + 1])
        (project / "AGENTS.md").write_bytes(b"unauthorized changed content\n")
        receipt["immutable_files"]["AGENTS.md"] = awf.digest((project / "AGENTS.md").read_bytes())
        (project / awf.RECEIPT).write_bytes(encoded(receipt))
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("INSTALLED_STATE_REQUIRES_RECONCILIATION", result["installed"]["status"])
        self.assertIn("map differs", result["installed"]["issue"])

    def test_changed_installed_file_and_inconsistent_provenance_need_reconciliation(self):
        project = self.root / "installed"
        self.fixture.install_fixture(project)
        (project / "AGENTS.md").write_bytes(b"changed")
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("INSTALLED_STATE_REQUIRES_RECONCILIATION", result["installed"]["status"])
        (project / "AGENTS.md").write_bytes(self.fixture.content["AGENTS.md"])
        version = json.loads((project / awf.VERSION_FILE).read_bytes())
        version["installation"]["install_id"] = "different"
        (project / awf.VERSION_FILE).write_bytes(encoded(version))
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("INSTALLED_STATE_REQUIRES_RECONCILIATION", result["installed"]["status"])

    def test_old_receipt_is_only_observed_and_incomplete_install_is_explicit(self):
        project = self.root / "installed"
        receipt = self.fixture.install_fixture(project)
        receipt["template_version"] = "1.4.1"
        (project / awf.RECEIPT).write_bytes(encoded(receipt))
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("OTHER_RELEASE_RECEIPT_OBSERVED", result["installed"]["status"])
        self.assertEqual("1.4.1", result["installed"]["version"])
        self.assertFalse(result["installed"]["receipt_verified_against_registered_release"])
        (project / ".agentic/INSTALLING.json").write_bytes(b"{}")
        result = awf.inspect_project(self.fixture.locate(), project)
        self.assertEqual("INCOMPLETE_INSTALLATION", result["installed"]["status"])

    def test_immutable_source_overlap_and_traversal_rejected_before_any_command(self):
        proof = self.fixture.locate()
        for project in (self.fixture.source, self.fixture.source / "target", self.root):
            self.assert_rejected("PROJECT_SOURCE_OVERLAP", lambda: awf.inspect_project(proof, project))
        self.assert_rejected("UNSAFE_PATH", lambda: awf.inspect_project(proof, self.root / ".." / self.root.name / "target"))

    def test_cli_success_and_structured_failure_include_next_action(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = awf.main(["--catalog", str(self.fixture.catalog), "locate", "--version", "1.5"])
        self.assertEqual(0, status)
        self.assertEqual("VERIFIED_LOCAL_RELEASE", json.loads(output.getvalue())["status"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = awf.main(["--catalog", str(self.fixture.catalog), "inspect", "--project", str(self.fixture.source), "--version", "1.5"])
        value = json.loads(output.getvalue())
        self.assertEqual(2, status)
        self.assertEqual("PROJECT_SOURCE_OVERLAP", value["code"])
        self.assertFalse(value["request_zip_if_no_verified_local_release"])
        self.assertIn("target inspection", value["next_action"]["action"])


if __name__ == "__main__":
    unittest.main()
