"""Behavioral tests: all mutations stay in an isolated temporary directory."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("install_skill", Path(__file__).resolve().parents[1] / "scripts" / "install_skill.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "package" / "awf"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: awf\ndescription: AWF 1.6\n---\n", encoding="utf-8")
        (self.source / "scripts").mkdir()
        (self.source / "scripts" / "awf.py").write_text("# new verified release\n", encoding="utf-8")
        self.dest = self.root / "profile" / "skills" / "awf"
        self.backups = self.root / "backups"
        self.manifest = {"schema_version": 1, "name": "awf", "version": "1.6.0", "files": [{"path": name, "sha256": sha} for name, sha in installer.inventory(self.source).items()]}
        self.pin_manifest()

    def pin_manifest(self):
        (self.source / installer.MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")
        self.pin = installer.digest(self.source / installer.MANIFEST)

    def run_install(self, **kwargs):
        options = {"source": self.source, "expected_manifest_sha256": self.pin, "dest": self.dest, "backup_root": self.backups}
        options.update(kwargs)
        return installer.install(**options)

    def old_skill(self, version="1.5.0"):
        self.dest.mkdir(parents=True)
        (self.dest / "SKILL.md").write_text("---\nname: awf\ndescription: AWF 1.5\n---\n", encoding="utf-8")
        (self.dest / ".awf-skill-receipt.json").write_text(json.dumps({"format": "awf-portable-skill-installation-1", "bundled_awf_version": version}), encoding="utf-8")
        (self.dest / "catalog-location.json").write_text('{"catalog":"owner/catalog.json"}', encoding="utf-8")
        (self.dest / "update-channel.json").write_text('{"channel":"owner/releases.json"}', encoding="utf-8")
        (self.dest / "old-only.txt").write_text("keep in backup", encoding="utf-8")

    def test_upgrade_preserves_settings_and_complete_recoverable_backup(self):
        self.old_skill()
        before = installer.inventory(self.dest)
        result = self.run_install()
        self.assertEqual(result["status"], "upgraded")
        self.assertEqual(installer.inventory(Path(result["backup"])), before)
        self.assertEqual(installer.digest(self.dest / "catalog-location.json"), before["catalog-location.json"])
        self.assertEqual(installer.digest(self.dest / "update-channel.json"), before["update-channel.json"])
        self.assertFalse((self.dest / "old-only.txt").exists())
        self.assertEqual(installer.read_json(self.dest / installer.RECEIPT)["files"], dict((p["path"], p["sha256"]) for p in self.manifest["files"]))

    def test_new_install_and_idempotence(self):
        self.assertEqual(self.run_install()["status"], "installed")
        before = installer.inventory(self.dest)
        self.assertEqual(self.run_install()["status"], "already-installed")
        self.assertEqual(installer.inventory(self.dest), before)
        self.assertFalse(self.backups.exists())

    def test_upgrade_then_idempotence_with_local_files(self):
        self.old_skill()
        self.run_install()
        self.assertEqual(self.run_install()["status"], "already-installed")

    def test_dry_run_creates_nothing(self):
        before = installer.inventory(self.root)
        self.assertEqual(self.run_install(dry_run=True)["status"], "would-install")
        self.assertEqual(installer.inventory(self.root), before)

    def test_dry_upgrade_creates_nothing(self):
        self.old_skill()
        before = installer.inventory(self.root)
        self.assertEqual(self.run_install(dry_run=True)["status"], "would-upgrade")
        self.assertEqual(installer.inventory(self.root), before)

    def test_corrupt_missing_extra_package_files_fail(self):
        for mode in ("corrupt", "missing", "extra"):
            with self.subTest(mode=mode):
                target = self.source / "scripts" / "awf.py"
                original = target.read_bytes()
                if mode == "corrupt":
                    target.write_bytes(b"corrupt")
                elif mode == "missing":
                    target.unlink()
                else:
                    (self.source / "extra.txt").write_bytes(b"extra")
                with self.assertRaises(installer.InstallError):
                    self.run_install()
                target.write_bytes(original)
                if mode == "extra":
                    (self.source / "extra.txt").unlink()
        self.assertFalse(self.dest.exists())

    def test_modified_manifest_requires_external_pin(self):
        self.manifest["version"] = "1.8.0"
        old_pin = self.pin
        self.pin_manifest()
        with self.assertRaisesRegex(installer.InstallError, "trust pin"):
            self.run_install(expected_manifest_sha256=old_pin)

    def test_manifest_duplicate_and_unsafe_paths(self):
        entry = self.manifest["files"][0].copy()
        for path in (entry["path"], "../escape", "/absolute", "C:/escape", "a\\b", "a/./b", "AUX.txt"):
            with self.subTest(path=path):
                self.manifest["files"].append(dict(entry, path=path))
                self.pin_manifest()
                with self.assertRaises(installer.InstallError):
                    self.run_install()
                self.manifest["files"].pop()

    def test_json_duplicate_keys_fail(self):
        (self.source / installer.MANIFEST).write_text('{"name":"awf","name":"other"}', encoding="utf-8")
        self.pin = installer.digest(self.source / installer.MANIFEST)
        with self.assertRaisesRegex(installer.InstallError, "Duplicate JSON"):
            self.run_install()

    def test_downgrade_and_unknown_existing_version_fail(self):
        self.old_skill("1.8.0")
        with self.assertRaisesRegex(installer.InstallError, "downgrade"):
            self.run_install()
        (self.dest / ".awf-skill-receipt.json").unlink()
        with self.assertRaisesRegex(installer.InstallError, "cannot be established"):
            self.run_install()

    def test_same_version_corruption_or_different_payload_rejected(self):
        self.run_install()
        (self.dest / "scripts" / "awf.py").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "corrupt"):
            self.run_install()

    def test_same_version_altered_receipt_rejected(self):
        self.run_install()
        receipt = installer.read_json(self.dest / installer.RECEIPT)
        receipt["files"] = {}
        (self.dest / installer.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "receipt"):
            self.run_install()

    def test_same_version_different_release_package_rejected(self):
        self.run_install()
        target = self.source / "scripts" / "awf.py"
        target.write_text("# another package, same version", encoding="utf-8")
        for entry in self.manifest["files"]:
            if entry["path"] == "scripts/awf.py":
                entry["sha256"] = installer.digest(target)
        self.pin_manifest()
        with self.assertRaisesRegex(installer.InstallError, "trust pin"):
            self.run_install()

    def test_existing_non_awf_skill_is_not_replaced(self):
        self.old_skill()
        (self.dest / "SKILL.md").write_text("---\nname: other\ndescription: other\n---\n", encoding="utf-8")
        before = installer.inventory(self.dest)
        with self.assertRaisesRegex(installer.InstallError, "Expected an AWF"):
            self.run_install()
        self.assertEqual(installer.inventory(self.dest), before)

    def test_unsafe_destination_and_backup_paths(self):
        for destination in (self.root, self.source, self.source / "nested" / "awf"):
            with self.subTest(destination=destination), self.assertRaises(installer.InstallError):
                self.run_install(dest=destination)
        for backup in (self.dest, self.dest / "backup", self.source, self.root / "skills" / "backups", self.root):
            with self.subTest(backup=backup), self.assertRaises(installer.InstallError):
                self.run_install(backup_root=backup)

    def test_hardlink_rejected(self):
        link = self.source / "hardlink"
        try:
            os.link(self.source / "SKILL.md", link)
        except OSError:
            self.skipTest("Hardlinks unsupported")
        with self.assertRaisesRegex(installer.InstallError, "Hardlinked"):
            self.run_install()

    def test_symlink_rejected(self):
        try:
            (self.source / "link").symlink_to(self.source / "SKILL.md")
        except OSError:
            self.skipTest("Symlinks unavailable for this Windows user")
        with self.assertRaisesRegex(installer.InstallError, "Links"):
            self.run_install()

    def test_replacement_failure_restores_previous_skill(self):
        self.old_skill()
        before = installer.inventory(self.dest)
        real_replace = os.replace
        def fail_stage(source, target):
            if Path(source).name.startswith(".awf-stage-"):
                raise OSError("simulated replacement failure")
            return real_replace(source, target)
        with patch.object(installer.os, "replace", side_effect=fail_stage):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.run_install()
        self.assertEqual(installer.inventory(self.dest), before)
        self.assertFalse((self.dest.parent / ".awf-install.lock").exists())
        self.assertEqual(list(self.dest.parent.glob(".awf-stage-*")), [])

    def test_backup_failure_leaves_old_skill_intact(self):
        self.old_skill()
        before = installer.inventory(self.dest)
        with patch.object(installer.os, "replace", side_effect=OSError("cross device")):
            with self.assertRaises(OSError):
                self.run_install()
        self.assertEqual(installer.inventory(self.dest), before)

    def test_existing_lock_blocks_cooperating_installers(self):
        self.dest.parent.mkdir(parents=True)
        (self.dest.parent / ".awf-install.lock").write_text("other installer", encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "Another installer"):
            self.run_install()
        self.assertFalse(self.dest.exists())

    def test_custom_local_file_is_preserved_without_code_override(self):
        self.old_skill()
        (self.dest / "owner.json").write_text("{}", encoding="utf-8")
        self.run_install(preserve_relative=["owner.json"])
        self.assertEqual((self.dest / "owner.json").read_text(encoding="utf-8"), "{}")
        with self.assertRaisesRegex(installer.InstallError, "override"):
            self.run_install(preserve_relative=["scripts/awf.py"])

    def test_duplicate_detection_does_not_remove_other_skill(self):
        project = self.root / "project"
        other = project / ".agents" / "skills" / "awf"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("other", encoding="utf-8")
        result = self.run_install(project=project)
        self.assertIn(str(other), result["duplicates"])
        self.assertEqual((other / "SKILL.md").read_text(encoding="utf-8"), "other")

    def test_recorded_custom_settings_survive_default_rerun_and_upgrade(self):
        self.old_skill()
        (self.dest / "owner.json").write_text('{"owner":"local"}', encoding="utf-8")
        self.run_install(preserve_relative=["owner.json"])
        self.assertEqual(self.run_install()["status"], "already-installed")
        self.manifest["version"] = "1.8.0"
        self.pin_manifest()
        self.assertEqual(self.run_install()["status"], "upgraded")
        self.assertEqual((self.dest / "owner.json").read_text(encoding="utf-8"), '{"owner":"local"}')
        self.assertIn("owner.json", installer.read_json(self.dest / installer.RECEIPT)["preserved_local_files"])
        self.assertEqual(self.run_install()["status"], "already-installed")

    def test_invalid_recorded_local_paths_are_rejected(self):
        self.run_install()
        receipt = installer.read_json(self.dest / installer.RECEIPT)
        for paths in (["../escape.json"], [installer.RECEIPT], ["scripts/awf.py"], ["SCRIPTS/AWF.PY"], ["scripts"], ["owner.json", "OWNER.json"], "owner.json"):
            with self.subTest(paths=paths):
                receipt["preserved_local_files"] = paths
                (self.dest / installer.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
                with self.assertRaises(installer.InstallError):
                    self.run_install()

    def test_preserved_config_cannot_override_new_package_file(self):
        self.old_skill()
        (self.dest / "owner.json").write_text("{}", encoding="utf-8")
        self.run_install(preserve_relative=["owner.json"])
        (self.source / "owner.json").write_text('{"new":"packaged"}', encoding="utf-8")
        self.manifest["version"] = "1.8.0"
        self.manifest["files"].append({"path": "owner.json", "sha256": installer.digest(self.source / "owner.json")})
        self.pin_manifest()
        before = installer.inventory(self.dest)
        with self.assertRaisesRegex(installer.InstallError, "override"):
            self.run_install()
        self.assertEqual(installer.inventory(self.dest), before)

    def test_conflicting_existing_versions_fail_without_mutation(self):
        self.old_skill()
        (self.dest / "assets").mkdir()
        (self.dest / "assets" / "release.json").write_text('{"version":"1.8.0"}', encoding="utf-8")
        (self.dest / installer.MANIFEST).write_text('{"version":"1.5.0"}', encoding="utf-8")
        before = installer.inventory(self.dest)
        with self.assertRaisesRegex(installer.InstallError, "Conflicting existing version"):
            self.run_install()
        self.assertEqual(installer.inventory(self.dest), before)


if __name__ == "__main__":
    unittest.main()
