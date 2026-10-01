"""Portable cache setup regressions using isolated, relocatable synthetic bundles."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_release.py"
spec = importlib.util.spec_from_file_location("portable_awf_setup", SCRIPT)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
awf = prep.awf


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


class BundleFixture:
    def __init__(self, root):
        self.root = root
        (root / "assets").mkdir(parents=True)
        self.archive = root / "assets/workflow.zip"
        self.metadata_path = root / "assets/release.json"
        self.files = {
            "AGENTS.md": b"# Synthetic source entry point\n",
            "README.md": b"# Synthetic release\n",
            awf.CONFIG: b'{"project":{"name":"fixture"}}\n',
            awf.VERSION_FILE: b'{"template":{"version":"1.5.0"},"installation":{}}\n',
            ".agentic/requirements.lock": b"# fixture\n",
            "scripts/bootstrap_project.py": b"raise RuntimeError('Do not execute release code')\n",
        }
        manifest = {"format": "awf-manifest-1", "template_version": "1.5.0",
                    "files": {path: awf.digest(raw) for path, raw in self.files.items()}}
        self.files["MANIFEST.json"] = json_bytes(manifest)
        self.files["MANIFEST.md"] = b"# Synthetic manifest\n"
        self.write_archive()

    def write_archive(self, extra=()):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in self.files.items():
                archive.writestr("portable-source/" + name, raw)
            for name, raw in extra:
                archive.writestr(name, raw)
        self.metadata = {"format": "awf-bundled-release-1", "version": "1.5.0", "archive": "workflow.zip",
                         "archive_sha256": awf.digest(self.archive.read_bytes()),
                         "manifest_sha256": awf.digest(self.files["MANIFEST.json"])}
        self.metadata_path.write_bytes(json_bytes(self.metadata))


def snapshot(root):
    if not root.exists():
        return None
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class PortableSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-portable-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = BundleFixture(self.root / "bundle")
        self.cache = self.root / "host-cache"

    def run_setup(self, version="1.5", cache=None, bundle=None):
        return prep.prepare_release(cache or self.cache, version, bundle_root=bundle or self.bundle.root)

    def assert_rejected(self, code, callback=None):
        with self.assertRaises(awf.DiscoveryError) as raised:
            (callback or self.run_setup)()
        self.assertEqual(code, raised.exception.code)
        self.assertIn("next_action", prep.rejected(raised.exception))
        return raised.exception

    def test_setup_verifies_actual_cache_and_does_not_change_bundle(self):
        before = snapshot(self.bundle.root)
        proof = self.run_setup()
        self.assertEqual("BUNDLED_RELEASE_PREPARED", proof["status"])
        self.assertEqual("CREATED", proof["cache_status"])
        self.assertTrue(proof["source_verified"])
        self.assertTrue(proof["cache_verified"])
        self.assertTrue(proof["cache_created_by_helper"])
        self.assertFalse(proof["project_adoption_performed"])
        self.assertFalse(proof["global_skill_installed"])
        self.assertFalse(proof["release_code_executed"])
        self.assertEqual(str(self.cache / "catalog.json"), proof["catalog_path"])
        self.assertEqual({"catalog": proof["catalog_path"]}, proof["catalog_location"])
        catalog = json.loads((self.cache / "catalog.json").read_bytes())
        self.assertEqual(str(self.cache / "source"), catalog["latest"]["source_directory"])
        self.assertEqual(str(self.cache / "release.zip"), catalog["latest"]["archive"])
        self.assertEqual(self.bundle.archive.read_bytes(), (self.cache / "release.zip").read_bytes())
        for name, raw in self.bundle.files.items():
            self.assertEqual(raw, (self.cache / "source" / name).read_bytes())
        self.assertEqual(before, snapshot(self.bundle.root))

    def test_idempotence_preserves_every_cache_byte_and_modification_time(self):
        self.run_setup()
        before = snapshot(self.cache)
        second = self.run_setup("1.5.0")
        self.assertEqual("EXISTING_VERIFIED", second["cache_status"])
        self.assertFalse(second["cache_created_by_helper"])
        self.assertEqual(before, snapshot(self.cache))

    def test_relocated_bundle_and_cli_have_only_receiving_machine_paths(self):
        moved = self.root / "relocated skill"
        shutil.move(str(self.bundle.root), moved)
        scripts = moved / "scripts"
        scripts.mkdir()
        shutil.copyfile(SCRIPT, scripts / "prepare_release.py")
        shutil.copyfile(SCRIPT.with_name("awf.py"), scripts / "awf.py")
        result = subprocess.run([sys.executable, "-B", str(scripts / "prepare_release.py"),
                                 "--cache-dir", str(self.cache), "--version", "1.5"],
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(str(moved), proof["bundle_directory"])
        catalog = (self.cache / "catalog.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.bundle.root), catalog)
        self.assertNotIn(str(SCRIPT.parents[1]), catalog)
        self.assertTrue(awf.locate(self.cache / "catalog.json", "1.5")["source_verified"])
        self.assertFalse((moved / "__pycache__").exists())
        self.assertFalse((scripts / "__pycache__").exists())

    def test_wrong_version_and_bad_selector_reject_before_any_write(self):
        for selector, code in [("1.4", "REQUESTED_VERSION_UNAVAILABLE"),
                               ("1.6", "REQUESTED_VERSION_UNAVAILABLE"),
                               ("1.5.1", "REQUESTED_VERSION_UNAVAILABLE"),
                               ("latest", "INVALID_VERSION_SELECTOR"),
                               ("v1.5", "INVALID_VERSION_SELECTOR")]:
            with self.subTest(selector=selector), patch.object(prep, "_write_new", side_effect=AssertionError("No writes")):
                self.assert_rejected(code, lambda: self.run_setup(selector))
                self.assertFalse(self.cache.exists())

    def test_default_selector_is_explicit_bundled_patch(self):
        result = self.run_setup(None)
        self.assertEqual("1.5.0", result["requested_version"])
        self.assertEqual("1.5.0", result["version"])

    def test_wrong_archive_or_manifest_pin_rejects_before_cache_creation(self):
        self.bundle.archive.write_bytes(self.bundle.archive.read_bytes() + b"corrupt")
        self.assert_rejected("ARCHIVE_HASH_MISMATCH")
        self.assertFalse(self.cache.exists())
        self.bundle.write_archive()
        self.bundle.metadata["manifest_sha256"] = "0" * 64
        self.bundle.metadata_path.write_bytes(json_bytes(self.bundle.metadata))
        self.assert_rejected("MANIFEST_HASH_MISMATCH")
        self.assertFalse(self.cache.exists())

    def test_zip_traversal_and_unknown_file_never_extract(self):
        for extra, code in [([("portable-source/../escape", b"no")], "UNSAFE_PATH"),
                            ([("portable-source/unlisted.py", b"no")], "ARCHIVE_MEMBERSHIP_MISMATCH")]:
            with self.subTest(code=code):
                self.bundle.write_archive(extra)
                self.assert_rejected(code)
                self.assertFalse(self.cache.exists())
                self.assertFalse((self.root / "escape").exists())

    def test_asset_filename_traversal_and_duplicate_metadata_rejected(self):
        self.bundle.metadata["archive"] = "../workflow.zip"
        self.bundle.metadata_path.write_bytes(json_bytes(self.bundle.metadata))
        self.assert_rejected("UNSAFE_PATH")
        self.assertFalse(self.cache.exists())
        self.bundle.metadata_path.write_bytes(b'{"version":"1.5.0","version":"1.5.1"}')
        self.assert_rejected("INVALID_JSON")
        self.assertFalse(self.cache.exists())

    def test_existing_empty_or_unrelated_directory_is_not_populated(self):
        self.cache.mkdir()
        self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
        self.assertEqual([], list(self.cache.iterdir()))
        (self.cache / "keep.txt").write_bytes(b"unrelated existing content")
        before = snapshot(self.cache)
        self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
        self.assertEqual(before, snapshot(self.cache))

    def test_corrupt_cached_source_and_archive_are_preserved_without_repair(self):
        self.run_setup()
        for target in (self.cache / "source/AGENTS.md", self.cache / "release.zip"):
            with self.subTest(target=target):
                original = target.read_bytes()
                target.write_bytes(original + b"changed")
                before = snapshot(self.cache)
                self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
                self.assertEqual(before, snapshot(self.cache))
                target.write_bytes(original)

    def test_existing_catalog_for_different_cache_or_version_is_preserved(self):
        self.run_setup()
        catalog_path = self.cache / "catalog.json"
        catalog = json.loads(catalog_path.read_bytes())
        catalog["latest"]["source_directory"] = str(self.root / "different-source")
        catalog_path.write_bytes(json_bytes(catalog))
        before = snapshot(self.cache)
        self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
        self.assertEqual(before, snapshot(self.cache))

    def test_cache_bundle_overlap_and_alias_rejected_before_mutation(self):
        before = snapshot(self.bundle.root)
        for cache in (self.bundle.root, self.bundle.root / "cache", self.root):
            with self.subTest(cache=cache):
                self.assert_rejected("CACHE_BUNDLE_OVERLAP", lambda: self.run_setup(cache=cache))
        self.assert_rejected("UNSAFE_PATH", lambda: self.run_setup(cache=self.root / ".." / self.root.name / "cache"))
        self.assertEqual(before, snapshot(self.bundle.root))
        self.assertFalse((self.bundle.root / "cache").exists())

    def test_nonexistent_parent_and_relative_cache_are_not_created(self):
        parent = self.root / "absent-parent"
        self.assert_rejected("LOCAL_SETUP_UNAVAILABLE", lambda: self.run_setup(cache=parent / "cache"))
        self.assertFalse(parent.exists())
        self.assert_rejected("UNSAFE_PATH", lambda: self.run_setup(cache=Path("relative-cache")))

    def test_hardlinked_bundled_archive_rejected_before_writes(self):
        linked = self.root / "archive-link"
        os.link(self.bundle.archive, linked)
        self.assert_rejected("UNSAFE_PATH")
        self.assertFalse(self.cache.exists())

    def test_interrupted_setup_preserves_partial_cache_and_never_reuses_it(self):
        original = prep._write_new
        def interrupted(path, raw, pins):
            if path.name == "catalog.json":
                raise OSError("synthetic interruption before completion record")
            original(path, raw, pins)
        with patch.object(prep, "_write_new", side_effect=interrupted):
            exc = self.assert_rejected("LOCAL_SETUP_UNAVAILABLE")
        self.assertTrue(exc.proof["cache_created_by_helper"])
        self.assertFalse((self.cache / "catalog.json").exists())
        before = snapshot(self.cache)
        self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
        self.assertEqual(before, snapshot(self.cache))

    def test_concurrent_setup_rejects_in_progress_cache_then_reuses_finished_cache(self):
        reached = threading.Event()
        release = threading.Event()
        original = prep._write_new
        def delayed(path, raw, pins):
            if path == self.cache / "release.zip":
                reached.set()
                if not release.wait(10):
                    raise RuntimeError("Test synchronization timeout")
            original(path, raw, pins)
        with patch.object(prep, "_write_new", side_effect=delayed), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.run_setup)
            try:
                self.assertTrue(reached.wait(10))
                self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
            finally:
                release.set()
            self.assertEqual("CREATED", first.result(timeout=20)["cache_status"])
        self.assertEqual("EXISTING_VERIFIED", self.run_setup()["cache_status"])

    def test_pinned_ancestors_prevent_writes_through_directory_redirection(self):
        victim = self.root / "unrelated-project"
        victim.mkdir()
        (victim / "keep.txt").write_bytes(b"preserve unrelated project")
        before = snapshot(victim)
        original = prep._write_new
        attempted = []
        def redirect(path, raw, pins):
            if path == self.cache / "source/AGENTS.md":
                attempted.append(True)
                source = self.cache / "source"
                moved = self.cache / "moved-source"
                if os.name == "nt":
                    # Pinned ancestors deny FILE_SHARE_DELETE, so the replacement cannot begin.
                    with self.assertRaises(OSError):
                        source.rename(moved)
                else:
                    source.rename(moved)
                    source.symlink_to(victim, target_is_directory=True)
            original(path, raw, pins)
        with patch.object(prep, "_write_new", side_effect=redirect):
            if os.name == "nt":
                self.assertEqual("CREATED", self.run_setup()["cache_status"])
            else:
                self.assert_rejected("CACHE_EXISTS_UNVERIFIED")
        self.assertEqual([True], attempted)
        self.assertEqual(before, snapshot(victim))
        self.assertFalse((victim / "AGENTS.md").exists())

    def test_exclusive_file_creation_preserves_competing_leaf(self):
        leaf = self.root / "competing.txt"
        leaf.write_bytes(b"competing writer content")
        with prep._PinnedDirectories() as pins:
            with self.assertRaises(FileExistsError):
                prep._write_new(leaf, b"must not overwrite", pins)
        self.assertEqual(b"competing writer content", leaf.read_bytes())


if __name__ == "__main__":
    unittest.main()
