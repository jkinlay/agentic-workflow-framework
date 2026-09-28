import json
import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from agentic import ValidationError
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.gates import evaluate
from agentic import publication
from agentic.publication import (_detectors, _private_ip, render_aliases,
                                 render_scan, rewrite_unpublished, scan_repository)


ROOT = Path(__file__).resolve().parents[2]


def git(repository, *args, input_bytes=None, check=True):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
                            input=input_bytes, capture_output=True, check=False)
    if check and result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8", "replace").strip()


def git_bytes(repository, *args, input_bytes=None, check=True):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
                            input=input_bytes, capture_output=True, check=False)
    if check and result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout


def private_locator():
    return "\\" * 2 + "example-host" + "\\" + "share" + "\\" + "raw"


class Repository:
    def __init__(self, parent):
        self.path = Path(parent) / "repository"
        self.path.mkdir()
        git(self.path, "init", "-b", "main")
        git(self.path, "config", "user.name", "Synthetic User")
        git(self.path, "config", "user.email", "synthetic@example.invalid")
        self.write("seed.txt", "seed\n")
        self.write(".gitignore", ".agentic-state/publication-deny.json\n")
        git(self.path, "add", "seed.txt", ".gitignore")
        git(self.path, "commit", "-m", "base")
        self.base = git(self.path, "rev-parse", "HEAD")
        git(self.path, "switch", "-c", "awf/EX-6-publication")

    def write(self, relative, text=None, data=None):
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            path.write_bytes(data)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message, *paths):
        git(self.path, "add", "--", *paths)
        git(self.path, "commit", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def mapping(self):
        path = self.path / ".agentic-state" / "publication-deny.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "aliases": {"raw_estate": [private_locator()]}}), encoding="utf-8")
        return path


class PublicationScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-publication-test-")
        self.repo = Repository(self.temp.name)
        self.mapping = self.repo.mapping()

    def tearDown(self):
        self.temp.cleanup()

    def test_ac43_removed_value_still_blocks_and_is_redacted(self):
        value = private_locator()
        self.repo.write("generated.txt", "locator=" + value + "\n")
        contaminated = self.repo.commit("add generated report", "generated.txt")
        self.repo.write("generated.txt", "locator={raw_estate}\n")
        self.repo.commit("redact generated report", "generated.txt")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item["commit"] == contaminated and item["path"] == "generated.txt"
                            for item in result["findings"]))
        self.assertNotIn(value, json.dumps(result))
        self.assertNotIn(value, render_scan(result))

    def test_ac43_commit_message_pr_body_and_comment_each_block(self):
        value = private_locator()
        self.repo.write("safe.txt", "safe\n")
        message_commit = self.repo.commit("locator " + value, "safe.txt")
        message = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["commit"] == message_commit and item["path"] == "message" for item in message["findings"]))
        git(self.repo.path, "reset", "--hard", self.repo.base)
        self.repo.write("safe.txt", "changed\n")
        self.repo.commit("safe message", "safe.txt")
        body = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping,
                               pr_body_texts=["body " + value])
        comment = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping,
                                  comment_texts=["comment " + value])
        self.assertTrue(any(item["path"] == "pr-body" for item in body["findings"]))
        self.assertTrue(any(item["path"] == "comment" for item in comment["findings"]))
        self.assertNotIn(value, json.dumps(body) + json.dumps(comment))

    def test_preexisting_complete_touched_head_content_is_reported_but_does_not_block(self):
        value = private_locator()
        git(self.repo.path, "switch", "main")
        self.repo.write("context.txt", value + "\nold\n")
        git(self.repo.path, "add", "context.txt")
        git(self.repo.path, "commit", "-m", "preexisting private context")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("context.txt", value + "\nnew\n")
        self.repo.commit("change adjacent line", "context.txt")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("PASS", result["status"])
        current = [item for item in result["findings"] if item["source"] == "current-file"]
        self.assertTrue(current)
        self.assertTrue(all(item["classification"] == "PRE_EXISTING" for item in current))
        self.assertEqual(0, result["blocking_findings_count"])
        self.assertGreater(result["pre_existing_findings_count"], 0)

    def test_deleting_a_base_value_is_preexisting_and_nonblocking(self):
        value = private_locator()
        git(self.repo.path, "switch", "main")
        self.repo.write("remove.txt", value + "\n")
        git(self.repo.path, "add", "remove.txt")
        git(self.repo.path, "commit", "-m", "base locator")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        git(self.repo.path, "rm", "remove.txt")
        git(self.repo.path, "commit", "-m", "remove base locator")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        deleted = [item for item in result["findings"] if item["change"] == "deleted"]
        self.assertEqual("PASS", result["status"])
        self.assertTrue(deleted)
        self.assertTrue(all(item["classification"] == "PRE_EXISTING" for item in deleted))

    def test_deleted_builtin_values_are_found_after_an_earlier_unscanned_base_blob(self):
        private_ip = ".".join(["10", "23", "45", "67"])
        drive_path = "".join(["Z", ":", "/", "example"])
        git(self.repo.path, "switch", "main")
        self.repo.write("a-limit.txt", "x" * (publication.MAX_SCAN_LINE_CHARS + 1))
        self.repo.write("z-source.txt", "first\n" + private_ip + "\nkept\n" + drive_path + "\n")
        git(self.repo.path, "add", "a-limit.txt", "z-source.txt")
        git(self.repo.path, "commit", "-m", "base with scan limit before detector values")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("z-source.txt", "first\nkept\n")
        self.repo.commit("remove base detector values", "z-source.txt")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        deleted = [item for item in result["findings"] if item["change"] == "deleted"]
        self.assertFalse(result["base_tree_search"]["complete"])
        self.assertEqual("line-too-long", result["base_tree_search"]["reason"])
        self.assertEqual("PASS", result["status"])
        self.assertEqual(0, result["blocking_findings_count"])
        self.assertEqual(2, result["pre_existing_findings_count"])
        self.assertEqual({(2, "builtin.private_ipv4", "PRE_EXISTING"),
                          (4, "builtin.windows_absolute", "PRE_EXISTING")},
                         {(item["line"], item["detector_id"], item["classification"])
                          for item in deleted})

    def test_incomplete_base_search_grants_no_preexisting_exemption(self):
        value = private_locator()
        git(self.repo.path, "switch", "main")
        self.repo.write("a-binary.dat", data=value.encode("utf-8") + b"\0binary")
        git(self.repo.path, "add", "a-binary.dat")
        git(self.repo.path, "commit", "-m", "base value hidden in unscannable blob")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("context.txt", value + "\n")
        self.repo.commit("add value not proven by base scan", "context.txt")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        self.assertFalse(result["base_tree_search"]["complete"])
        self.assertEqual("binary", result["base_tree_search"]["reason"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item["classification"] == "BLOCKING" for item in result["findings"]))

    def test_ac53_channels_and_builtin_cannot_be_disabled_by_tracked_config(self):
        value = private_locator()
        config = self.repo.path / ".agentic" / "PROJECT_CONFIG.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"publication": {"deny_literals": [], "deny_regexes": [],
            "internal_hostnames": []}}), encoding="utf-8")
        channels = {}
        self.repo.write("generated.txt", "report=" + value + "\n")
        channels["generated_file"] = scan_repository(self.repo.path, self.repo.base,
            self.repo.commit("safe", "generated.txt"), config_path=config)
        git(self.repo.path, "reset", "--hard", self.repo.base)
        self.repo.write("patch.txt", value + "\n")
        channels["patch"] = scan_repository(self.repo.path, self.repo.base,
            self.repo.commit("safe", "patch.txt"), config_path=config)
        git(self.repo.path, "reset", "--hard", self.repo.base)
        self.repo.write("message.txt", "safe\n")
        channels["message"] = scan_repository(self.repo.path, self.repo.base,
            self.repo.commit(value, "message.txt"), config_path=config)
        git(self.repo.path, "reset", "--hard", self.repo.base)
        self.repo.write("body.txt", "safe\n")
        self.repo.commit("safe", "body.txt")
        channels["pr_body"] = scan_repository(self.repo.path, self.repo.base, "HEAD",
            config_path=config, pr_body_texts=[value])
        for channel, result in channels.items():
            with self.subTest(channel=channel):
                self.assertEqual("BLOCKED", result["status"])
                self.assertNotIn(value, json.dumps(result))

    def test_binary_is_reported_unscanned_and_rename_is_supported(self):
        self.repo.write("binary.dat", data=b"prefix\0suffix")
        self.repo.commit("add binary", "binary.dat")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item["path"] == "binary.dat" and item["reason"] == "binary" for item in result["unscanned"]))
        git(self.repo.path, "reset", "--hard", self.repo.base)
        self.repo.write("before.txt", "safe\n")
        self.repo.commit("add text", "before.txt")
        git(self.repo.path, "mv", "before.txt", "after.txt")
        self.repo.commit("rename text", "after.txt")
        renamed = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("PASS", renamed["status"])

    def test_invalid_utf8_blob_commit_message_and_provider_file_are_unscanned(self):
        self.repo.write("invalid.txt", data=b"synthetic-\xff-value\n")
        self.repo.commit("invalid blob", "invalid.txt")
        blob = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["reason"] == "invalid-utf8" and item["path"] == "invalid.txt"
                            for item in blob["unscanned"]))
        self.assertEqual("BLOCKED", blob["status"])

        git(self.repo.path, "reset", "--hard", self.repo.base)
        tree = git(self.repo.path, "rev-parse", "HEAD^{tree}")
        identity = b"Synthetic User <synthetic@example.invalid> 0 +0000"
        raw = (b"tree " + tree.encode("ascii") + b"\nparent " + self.repo.base.encode("ascii")
               + b"\nauthor " + identity + b"\ncommitter " + identity + b"\n\nmessage-\xff\n")
        commit = git_bytes(self.repo.path, "hash-object", "-t", "commit", "-w", "--stdin",
                           input_bytes=raw).decode("ascii").strip()
        git(self.repo.path, "update-ref", "HEAD", commit, self.repo.base)
        message = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["reason"] == "invalid-utf8" and item["source"] == "message"
                            for item in message["unscanned"]))

        provider = Path(self.temp.name) / "body.bin"
        provider.write_bytes(b"body-\xff")
        body = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping,
                               pr_body_paths=[provider])
        self.assertTrue(any(item["reason"] == "invalid-utf8" and item["source"] == "pr-body"
                            for item in body["unscanned"]))

    def test_root_commit_patch_in_merged_unrelated_history_is_scanned(self):
        git(self.repo.path, "switch", "--orphan", "isolated-root")
        git(self.repo.path, "rm", "-rf", "--ignore-unmatch", ".")
        self.repo.write("root.txt", private_locator() + "\n")
        git(self.repo.path, "add", "root.txt")
        git(self.repo.path, "commit", "-m", "isolated root")
        root_commit = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "awf/EX-6-publication")
        git(self.repo.path, "merge", "--allow-unrelated-histories", "--no-ff", "isolated-root",
            "-m", "merge isolated root")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["commit"] == root_commit and item["path"] == "root.txt"
                            and item["source"] == "patch" for item in result["findings"]))

    def test_builtin_path_ip_and_declared_hostname_detectors(self):
        self.repo.write("safe.txt", "changed\n")
        self.repo.commit("safe", "safe.txt")
        mapping = self.repo.path / ".agentic-state" / "publication-deny.json"
        mapping.write_text(json.dumps({"version": 1, "internal_hostnames": ["example-internal"]}), encoding="utf-8")
        values = {
            "builtin.windows_absolute": "Z:" + "\\" + "raw" + "\\" + "input.txt",
            "builtin.home_path": "/" + "home" + "/example-user/private.txt",
            "builtin.private_ipv4": "10." + "2.3.4",
            "builtin.private_ipv6": "fd00" + "::5",
            "declared.hostname.1": "example-" + "internal",
        }
        for detector, value in values.items():
            with self.subTest(detector=detector):
                result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=mapping,
                                         pr_body_texts=[value])
                self.assertTrue(any(item["detector_id"] == detector for item in result["findings"]))
                self.assertNotIn(value, json.dumps(result))

    def test_merge_commit_resolution_is_scanned(self):
        git(self.repo.path, "switch", "-c", "side", self.repo.base)
        self.repo.write("side.txt", "side\n")
        self.repo.commit("side", "side.txt")
        git(self.repo.path, "switch", "awf/EX-6-publication")
        self.repo.write("feature.txt", "feature\n")
        self.repo.commit("feature", "feature.txt")
        git(self.repo.path, "merge", "--no-ff", "--no-commit", "side")
        self.repo.write("resolution.txt", private_locator() + "\n")
        git(self.repo.path, "add", "resolution.txt")
        git(self.repo.path, "commit", "-m", "merge resolution")
        merge = git(self.repo.path, "rev-parse", "HEAD")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["commit"] == merge and item["path"] == "resolution.txt" for item in result["findings"]))

    def test_alias_renderer_and_shipped_ignore_rule(self):
        value = private_locator()
        mapping = {"aliases": {"raw_estate": [value]}}
        self.assertEqual("use {raw_estate}", render_aliases("use " + value, mapping))
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-v", "--no-index",
                                  ".agentic-state/publication-deny.json"], capture_output=True, text=True)
        self.assertEqual(0, ignored.returncode, ignored.stderr)
        self.assertIn(".agentic-state/publication-deny.json", ignored.stdout)

    def test_force_tracked_mapping_is_refused_from_index_and_head(self):
        git(self.repo.path, "add", "-f", ".agentic-state/publication-deny.json")
        with self.assertRaisesRegex(ValidationError, "tracked in the index"):
            scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        git(self.repo.path, "commit", "-m", "force track mapping")
        git(self.repo.path, "rm", "--cached", ".agentic-state/publication-deny.json")
        with self.assertRaisesRegex(ValidationError, "tracked in HEAD"):
            scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)

    def test_catastrophic_configured_regex_and_overlong_line_fail_closed(self):
        config = self.repo.path / "publication-config.json"
        config.write_text(json.dumps({"publication": {"deny_regexes": [
            {"id": "nested", "pattern": "(a+)+$"}]}}), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "nested repetition"):
            scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping,
                            config_path=config)
        self.repo.write("long.txt", "x" * (publication.MAX_SCAN_LINE_CHARS + 1))
        self.repo.commit("long line", "long.txt")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["reason"] == "line-too-long" for item in result["unscanned"]))
        self.assertEqual("BLOCKED", result["status"])

    def test_redaction_uses_detector_id_and_digest_only(self):
        mapping = self.repo.path / ".agentic-state" / "publication-deny.json"
        mapping.write_text(json.dumps({"version": 1, "deny_literals": ["ZX"]}), encoding="utf-8")
        self.repo.write("safe.txt", "changed\n")
        self.repo.commit("safe", "safe.txt")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=mapping,
                                 pr_body_texts=["ZX"])
        finding = next(item for item in result["findings"] if item["source"] == "pr-body")
        self.assertEqual(hashlib.sha256(b"ZX").hexdigest(), finding["match_sha256"])
        self.assertNotIn("redacted_excerpt", finding)
        self.assertNotIn("ZX", render_scan(result))

    def test_shipped_agentic_source_has_no_builtin_findings_with_empty_mapping(self):
        detectors, _allows = _detectors({"aliases": {}, "deny_literals": [], "deny_regexes": [],
                                         "internal_hostnames": [], "builtin_allow": []}, {})
        findings = []
        source_paths = (git(ROOT, "ls-files", ".agentic/lib").splitlines()
                        + git(ROOT, "ls-files", ".agentic/scripts").splitlines())
        for relative in source_paths:
            raw = (ROOT / relative).read_bytes()
            if b"\0" in raw:
                continue
            try:
                text = raw.decode("utf-8", "strict")
            except UnicodeDecodeError:
                continue
            for detector in detectors:
                if not detector.detector_id.startswith("builtin."):
                    continue
                for match in detector.regex.finditer(text):
                    if not detector.private_ip or _private_ip(match.group(0)):
                        findings.append((relative, detector.detector_id))
        self.assertEqual([], findings)


class PublicationRewriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-rewrite-test-")
        self.repo = Repository(self.temp.name)
        self.mapping = self.repo.mapping()
        self.remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        git(self.repo.path, "remote", "add", "origin", str(self.remote))

    def tearDown(self):
        self.temp.cleanup()

    def contaminate_then_remove(self):
        value = private_locator()
        self.repo.write("history.txt", value + "\n")
        first = self.repo.commit("add locator", "history.txt")
        self.repo.write("history.txt", "{raw_estate}\n")
        second = self.repo.commit("remove locator", "history.txt")
        return first, second

    def object_snapshot(self):
        objects = self.repo.path / ".git" / "objects"
        return {path.relative_to(objects).as_posix(): path.read_bytes()
                for path in objects.rglob("*") if path.is_file()}

    def test_ac44_unpublished_squash_preserves_tree_and_cleans_refs(self):
        old_commits = self.contaminate_then_remove()
        old_tree = git(self.repo.path, "rev-parse", "HEAD^{tree}")
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        result = rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                     mapping_path=self.mapping)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(old_tree, git(self.repo.path, "rev-parse", "HEAD^{tree}"))
        self.assertEqual("1", git(self.repo.path, "rev-list", "--count", self.repo.base + "..HEAD"))
        self.assertEqual("PASS", scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)["status"])
        refs = git(self.repo.path, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags").splitlines()
        for commit in old_commits:
            for ref in refs:
                self.assertNotEqual(0, subprocess.run(["git", "-C", str(self.repo.path), "merge-base",
                    "--is-ancestor", commit, ref], capture_output=True).returncode)

    def test_ac44_published_branch_and_unsupported_count_refuse_without_change(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValidationError, "Only --commits 1"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 2, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        shutil.copytree(self.repo.path / ".git" / "objects", self.remote / "objects", dirs_exist_ok=True)
        subprocess.run(["git", "--git-dir", str(self.remote), "update-ref",
            "refs/heads/awf/EX-6-publication", head], check=True, capture_output=True)
        with self.assertRaisesRegex(ValidationError, "Published-history rewrite refused"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_distinct_push_url_and_remote_tracking_reachability_refuse(self):
        old_commits = self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        push_remote = Path(self.temp.name) / "push.git"
        subprocess.run(["git", "init", "--bare", str(push_remote)], check=True, capture_output=True)
        shutil.copytree(self.repo.path / ".git" / "objects", push_remote / "objects", dirs_exist_ok=True)
        subprocess.run(["git", "--git-dir", str(push_remote), "update-ref",
                        "refs/heads/awf/EX-6-publication", head], check=True, capture_output=True)
        git(self.repo.path, "remote", "set-url", "--add", "--push", "origin", str(push_remote))
        with self.assertRaisesRegex(ValidationError, "branch exists on a remote"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

        git(self.repo.path, "remote", "set-url", "--push", "origin", str(self.remote))
        git(self.repo.path, "update-ref", "refs/remotes/origin/archive", old_commits[0])
        with self.assertRaisesRegex(ValidationError, "reachable from another branch or tag"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_remote_lookup_failure_refuses_without_change(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        missing = Path(self.temp.name) / "missing.git"
        git(self.repo.path, "remote", "set-url", "--add", "--push", "origin", str(missing))
        with self.assertRaisesRegex(ValidationError, "could not be verified"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_object_install_and_ref_update_failures_restore_repository(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        objects = self.object_snapshot()
        with mock.patch.object(publication.os, "replace", side_effect=OSError("injected replace")):
            with self.assertRaisesRegex(OSError, "injected replace"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(objects, self.object_snapshot())

        real_git = publication._git
        def interrupted_update(root, *args, **kwargs):
            if args and args[0] == "update-ref":
                raise ValidationError("injected update interruption")
            return real_git(root, *args, **kwargs)
        with mock.patch.object(publication, "_git", side_effect=interrupted_update):
            with self.assertRaisesRegex(ValidationError, "object store were restored"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(objects, self.object_snapshot())

        def reject_update(root, *args, **kwargs):
            if args and args[0] == "update-ref":
                return subprocess.CompletedProcess([], 1, b"", b"injected")
            return real_git(root, *args, **kwargs)
        with mock.patch.object(publication, "_git", side_effect=reject_update):
            with self.assertRaisesRegex(ValidationError, "object store were restored"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(objects, self.object_snapshot())

    def test_ac44_failed_rollback_is_named_with_recovery(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        calls = 0
        def ambiguous_then_failed_rollback(root, *args, **kwargs):
            nonlocal calls
            if args and args[0] == "update-ref":
                calls += 1
                if calls == 1:
                    real_git(root, *args, **kwargs)
                return subprocess.CompletedProcess([], 1, b"", b"injected")
            return real_git(root, *args, **kwargs)
        with mock.patch.object(publication, "_git", side_effect=ambiguous_then_failed_rollback):
            with self.assertRaisesRegex(ValidationError, "ROLLBACK_FAILED.*git update-ref"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        current = git(self.repo.path, "rev-parse", "HEAD")
        self.assertNotEqual(old_head, current)
        git(self.repo.path, "update-ref", "HEAD", old_head, current)


class PublicationGateTests(unittest.TestCase):
    def test_receipt_is_bound_to_candidate_and_pr_body(self):
        config = load(ROOT / ".agentic" / "examples" / "PROJECT_CONFIG.yaml")
        workflow = load(ROOT / ".agentic" / "workflow.yaml")
        bundle = load(ROOT / ".agentic" / "examples" / "evidence-bundle.json")
        contracts = Contracts(ROOT / ".agentic" / "schemas")
        gate = evaluate(config, workflow, bundle, contracts, "2026-09-09T12:00:00Z")
        self.assertEqual("PASS", gate["gates"]["publication_safety"]["result"])
        changed = copy.deepcopy(bundle)
        changed["publication_scan"]["pr_body_sha256"] = "0" * 64
        gate = evaluate(config, workflow, changed, contracts, "2026-09-09T12:00:00Z")
        self.assertEqual("FAIL", gate["gates"]["publication_safety"]["result"])
        self.assertEqual("NOT_READY", gate["conclusion"])


if __name__ == "__main__":
    unittest.main()
