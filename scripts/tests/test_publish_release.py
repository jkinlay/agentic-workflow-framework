"""Release publication, reproducibility, clean-source, and local-remote proofs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("awf_publish_release", ROOT / "scripts/publish_release.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def command(args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout.strip()


class PublishReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="awf-publish-release-")
        cls.base = Path(cls.temporary.name)
        cls.repository = cls.base / "source"
        shutil.copytree(ROOT, cls.repository,
                        ignore=shutil.ignore_patterns(".git", ".tmp", ".tmp-tests", "__pycache__", "*.pyc"))
        command([sys.executable, "-B", str(cls.repository / "scripts/build_release.py"), "--manifest-only"], cls.repository)
        cls.git_env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_AUTHOR_DATE": "2026-09-24T00:00:00+00:00",
                       "GIT_COMMITTER_DATE": "2026-09-24T00:00:00+00:00"}
        command(["git", "init", "-b", "main"], cls.repository, cls.git_env)
        command(["git", "config", "user.name", "Synthetic Release Test"], cls.repository, cls.git_env)
        command(["git", "config", "user.email", "release@example.invalid"], cls.repository, cls.git_env)
        command(["git", "config", "core.autocrlf", "false"], cls.repository, cls.git_env)
        command(["git", "config", "core.filemode", "false"], cls.repository, cls.git_env)
        command(["git", "add", "."], cls.repository, cls.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic release fixture"],
                cls.repository, cls.git_env)
        cls.commit = command(["git", "rev-parse", "HEAD"], cls.repository, cls.git_env)
        cls.epoch = int(command(["git", "show", "-s", "--format=%ct", cls.commit], cls.repository, cls.git_env))
        cls.windows_check = cls.base / "windows-check.json"
        cls.windows_check.write_text(json.dumps({
            "format": "awf-clean-windows-portable-check-1", "status": "PASS", "version": "1.9.3",
            "source_commit": cls.commit,
            "checks": {"portable_build": "PASS", "host_skill_install": "PASS", "host_skill_verify": "PASS"},
        }, sort_keys=True), encoding="utf-8")
        cls.windows_pin = hashlib.sha256(cls.windows_check.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @staticmethod
    def fake_validation(source, output, env):
        return {"self_test": {"run": 1, "failures": 0, "errors": 0},
                "release_hygiene": {"status": "PASS"},
                "portable_suites": {"portable_core": 1, "portable_host": 1}}

    def build(self, name):
        source = self.base / (name + "-source")
        temporary = self.base / (name + "-tmp")
        temporary.mkdir()
        publisher.materialize_commit(self.repository, self.commit, source)
        previous = {key: os.environ.get(key) for key in ("TMP", "TEMP")}
        os.environ.update(TMP=str(temporary), TEMP=str(temporary))
        try:
            return publisher.build_assets(source, self.base / name, self.epoch)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_ac10_two_builds_are_byte_identical_and_metadata_is_normalized(self):
        first, first_proof = self.build("first-build")
        second, second_proof = self.build("second-build")
        first_hashes = {path.name: publisher.sha256(path) for path in first}
        second_hashes = {path.name: publisher.sha256(path) for path in second}
        self.assertEqual(first_hashes, second_hashes)
        print("AC10_ASSET_SHA256=" + json.dumps(first_hashes, sort_keys=True))
        self.assertEqual(first_proof["manifest_sha256"], second_proof["manifest_sha256"])
        metadata = None
        for path in first:
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                current = [(entry.date_time, entry.create_system, entry.external_attr,
                            entry.compress_type, entry.extra, entry.comment) for entry in archive.infolist()]
                self.assertTrue(current)
                self.assertTrue(all(item[3] == zipfile.ZIP_STORED for item in current))
                expected_modes = [0o100755 if archive.read(entry).startswith(b"#!") else 0o100644
                                  for entry in archive.infolist()]
                self.assertEqual([item[2] >> 16 for item in current], expected_modes)
                self.assertTrue(all(item[1] == 3 for item in current))
                self.assertIn(0o100755, expected_modes)
                self.assertIn(0o100644, expected_modes)
                self.assertTrue(all(item[4:] == (b"", b"") for item in current))
                if metadata is None:
                    metadata = current[0][0]
                self.assertTrue(all(item[0] == metadata for item in current))

    def test_dry_run_records_hashes_and_makes_no_remote_change(self):
        result = publisher.publish(self.repository, self.commit, self.base / "dry-run-output",
                                   self.windows_check, self.windows_pin, dry_run=True,
                                   validation_runner=self.fake_validation)
        self.assertEqual("DRY_RUN", result["status"])
        for name, digest in result["record"]["assets"].items():
            self.assertIn(name, result["tag_message"])
            self.assertIn(digest, result["tag_message"])
            self.assertIn(digest, result["release_body"])
        self.assertFalse(result["remote_changes"])
        self.assertEqual("", command(["git", "tag", "--list", "v1.9.3"], self.repository))

    def test_k6_reports_untracked_tracked_and_version_disagreement_together(self):
        readme = self.repository / "README.md"
        original = readme.read_bytes()
        untracked = self.repository / "synthetic-untracked.txt"
        try:
            readme.write_text(readme.read_text(encoding="utf-8").replace("Release 1.9.3", "Release 9.9.9", 1), encoding="utf-8")
            untracked.write_text("fixture", encoding="utf-8")
            report = publisher.source_report(self.repository, self.commit)
            joined = "\n".join(report["problems"])
            self.assertIn("tracked modification", joined)
            self.assertIn("untracked file", joined)
            self.assertIn("version disagreement", joined)
        finally:
            readme.write_bytes(original)
            untracked.unlink()

    def test_k6_reports_every_stale_generated_file(self):
        source = self.base / "stale-generated-source"
        publisher.materialize_commit(self.repository, self.commit, source)
        schema = source / ".agentic/schemas/run-disposition-request.schema.json"
        example = source / ".agentic/examples/project-status.json"
        schema.write_text("{}\n", encoding="utf-8")
        example.write_text("{}\n", encoding="utf-8")
        problems = publisher.generated_file_problems(source)
        self.assertIn("stale generated file: .agentic/schemas/run-disposition-request.schema.json", problems)
        self.assertIn("stale generated file: .agentic/examples/project-status.json", problems)

    def test_g001_replacement_objects_cannot_substitute_release_bytes_or_identity(self):
        repository = self.base / "replacement-object-source"
        repository.mkdir()
        command(["git", "init", "-b", "main"], repository, self.git_env)
        command(["git", "config", "user.name", "Original Publisher"], repository, self.git_env)
        command(["git", "config", "user.email", "original@example.invalid"], repository, self.git_env)
        command(["git", "config", "core.autocrlf", "false"], repository, self.git_env)
        released = repository / "released.txt"
        released.write_bytes(b"reviewed release bytes\n")
        command(["git", "add", "released.txt"], repository, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Reviewed release identity"],
                repository, self.git_env)
        reviewed = command(["git", "rev-parse", "HEAD"], repository, self.git_env)

        command(["git", "config", "user.name", "Replacement Publisher"], repository, self.git_env)
        command(["git", "config", "user.email", "replacement@example.invalid"], repository, self.git_env)
        released.write_bytes(b"substituted release bytes\n")
        command(["git", "add", "released.txt"], repository, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Replacement release identity"],
                repository, self.git_env)
        replacement = command(["git", "rev-parse", "HEAD"], repository, self.git_env)
        command(["git", "replace", reviewed, replacement], repository, self.git_env)
        command(["git", "update-ref", "refs/heads/main", reviewed], repository, self.git_env)

        self.assertEqual("Replacement release identity",
                         command(["git", "show", "-s", "--format=%s", reviewed], repository, self.git_env))
        self.assertEqual("substituted release bytes",
                         command(["git", "show", f"{reviewed}:released.txt"], repository, self.git_env))
        identity = publisher.git(
            repository, "show", "-s", "--format=%H%x00%an%x00%ae%x00%s", reviewed).strip().split("\0")
        self.assertEqual([reviewed, "Original Publisher", "original@example.invalid",
                          "Reviewed release identity"], identity)
        materialized = self.base / "replacement-object-materialized"
        publisher.materialize_commit(repository, reviewed, materialized)
        self.assertEqual(b"reviewed release bytes\n", (materialized / "released.txt").read_bytes())

    def test_b001_materialization_ignores_export_attributes_and_verifies_raw_tree(self):
        repository = self.base / "raw-tree-source"
        repository.mkdir()
        command(["git", "init", "-b", "main"], repository, self.git_env)
        command(["git", "config", "user.name", "Raw Tree Publisher"], repository, self.git_env)
        command(["git", "config", "user.email", "raw-tree@example.invalid"], repository, self.git_env)
        (repository / ".gitattributes").write_text("omitted.txt export-ignore\n", encoding="utf-8")
        (repository / "omitted.txt").write_bytes(b"must remain in the tagged tree projection\n")
        (repository / "ordinary.txt").write_bytes(b"ordinary\n")
        (repository / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
        command(["git", "add", "."], repository, self.git_env)
        command(["git", "update-index", "--chmod=+x", "run.sh"], repository, self.git_env)
        command(["git", "-c", "commit.gpgsign=false", "commit", "-m", "Raw release tree"],
                repository, self.git_env)
        commit = command(["git", "rev-parse", "HEAD"], repository, self.git_env)
        entries = publisher._tree_entries(repository, commit)
        blobs = publisher._tree_blobs(repository, entries)
        source = self.base / "raw-tree-materialized"
        publisher.materialize_commit(repository, commit, source)
        self.assertEqual({".gitattributes", "omitted.txt", "ordinary.txt", "run.sh"},
                         {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()})
        self.assertEqual("100755", next(entry.mode for entry in entries if entry.path == "run.sh"))
        self.assertEqual(b"must remain in the tagged tree projection\n", (source / "omitted.txt").read_bytes())

        (source / "extra.txt").write_bytes(b"extra\n")
        with self.assertRaisesRegex(publisher.ReleaseError, "inventory"):
            publisher._verify_materialized_tree(source, entries, blobs)
        (source / "extra.txt").unlink()
        original = (source / "ordinary.txt").read_bytes()
        (source / "ordinary.txt").write_bytes(b"changed\n")
        with self.assertRaisesRegex(publisher.ReleaseError, "bytes"):
            publisher._verify_materialized_tree(source, entries, blobs)
        (source / "ordinary.txt").write_bytes(original)
        if os.name != "nt":
            (source / "run.sh").chmod(0o644)
            with self.assertRaisesRegex(publisher.ReleaseError, "mode"):
                publisher._verify_materialized_tree(source, entries, blobs)

    def test_b001_unsafe_modes_and_aliases_fail_before_materialization(self):
        oid = "a" * 40
        cases = {
            "symlink": (f"120000 blob {oid}\tlink\0".encode(), "non-regular"),
            "case files": ((f"100644 blob {oid}\tName.txt\0"
                            f"100644 blob {oid}\tname.txt\0").encode(), "case-folding"),
            "case directories": ((f"100644 blob {oid}\tFoo/a.txt\0"
                                  f"100644 blob {oid}\tfoo/b.txt\0").encode(), "case-folding"),
            "reserved": (f"100644 blob {oid}\tdir/CON.txt\0".encode(), "nonportable"),
            "trailing dot": (f"100644 blob {oid}\talias.\0".encode(), "nonportable"),
        }
        for label, (tree, message) in cases.items():
            destination = self.base / ("unsafe-" + label.replace(" ", "-"))
            def fake_git_run(_repository, *arguments, **_kwargs):
                if arguments[0] == "rev-parse":
                    return SimpleNamespace(stdout=oid + "\n")
                self.assertEqual(arguments[0], "ls-tree")
                return SimpleNamespace(stdout=tree)
            with self.subTest(label=label), patch.object(publisher, "git_run", side_effect=fake_git_run):
                with self.assertRaisesRegex(publisher.ReleaseError, message):
                    publisher.materialize_commit(self.repository, self.commit, destination)
                self.assertFalse(destination.exists())

    def make_fake_gh(self, directory, storage, log):
        script = directory / "fake_gh.py"
        script.write_text("""import json, os, pathlib, shutil, sys
args=sys.argv[1:]
with pathlib.Path(os.environ['AWF_FAKE_GH_LOG']).open('a', encoding='utf-8') as out:
    out.write(json.dumps(args)+'\\n')
store=pathlib.Path(os.environ['AWF_FAKE_GH_STORE'])
store.mkdir(exist_ok=True)
if args[:2] == ['release','create']:
    for value in args[3:]:
        path=pathlib.Path(value)
        if path.suffix == '.zip' and path.is_file(): shutil.copyfile(path, store/path.name)
elif args[:2] == ['release','download']:
    target=pathlib.Path(args[args.index('--dir')+1]); target.mkdir(exist_ok=True)
    for path in store.glob('*.zip'): shutil.copyfile(path, target/path.name)
else: raise SystemExit(2)
""", encoding="utf-8")
        if os.name == "nt":
            executable = directory / "gh.cmd"
            executable.write_text('@"' + sys.executable + '" "%~dp0fake_gh.py" %*\n', encoding="utf-8")
        else:
            executable = directory / "gh"
            executable.write_text("#!/bin/sh\nexec \"" + sys.executable + "\" \"$(dirname \"$0\")/fake_gh.py\" \"$@\"\n", encoding="utf-8")
            executable.chmod(0o755)
        return executable

    def test_local_bare_remote_fake_gh_and_verify_changed_byte(self):
        bare = self.base / "remote.git"
        command(["git", "init", "--bare", str(bare)], self.base)
        command(["git", "remote", "add", "origin", str(bare)], self.repository)
        command(["git", "push", "-u", "origin", "main"], self.repository)
        fake_bin = self.base / "fake-bin"; fake_bin.mkdir()
        store = self.base / "fake-release"; store.mkdir()
        log = self.base / "fake-gh.jsonl"
        executable = self.make_fake_gh(fake_bin, store, log)
        env = os.environ.copy()
        env.update(PATH=str(fake_bin) + os.pathsep + env.get("PATH", ""),
                   AWF_FAKE_GH_LOG=str(log), AWF_FAKE_GH_STORE=str(store))
        old = os.environ.copy()
        os.environ.update(env)
        try:
            result = publisher.publish(self.repository, self.commit, self.base / "published-output",
                                       self.windows_check, self.windows_pin,
                                       validation_runner=self.fake_validation, gh=str(executable))
            self.assertEqual("DRAFT_CREATED", result["status"])
            verified = publisher.verify_tag(self.repository, "v1.9.3", self.base / "verify-output",
                                            gh=str(executable))
            self.assertEqual("PASS", verified["status"])
            extra = store / "unexpected-extra.zip"
            extra.write_bytes(b"unrecorded release asset")
            with self.assertRaisesRegex(publisher.ReleaseError, "published release assets differ"):
                publisher.verify_tag(self.repository, "v1.9.3", self.base / "verify-extra-output",
                                     gh=str(executable))
            extra.unlink()
            changed = next(store.glob("*.zip"))
            changed.write_bytes(changed.read_bytes() + b"changed")
            with self.assertRaisesRegex(publisher.ReleaseError, "published release assets differ"):
                publisher.verify_tag(self.repository, "v1.9.3", self.base / "verify-fail-output",
                                     gh=str(executable))
        finally:
            os.environ.clear(); os.environ.update(old)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(call[:2] == ["release", "create"] and "--draft" in call for call in calls))
        self.assertTrue(any(call[:2] == ["release", "download"] for call in calls))


if __name__ == "__main__":
    unittest.main()
