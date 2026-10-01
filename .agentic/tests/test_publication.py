import json
import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agentic import ValidationError
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.gates import evaluate
from agentic.publication import render_aliases, render_scan, rewrite_unpublished, scan_repository


ROOT = Path(__file__).resolve().parents[2]


def git(repository, *args, input_bytes=None, check=True):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
                            input=input_bytes, capture_output=True, check=False)
    if check and result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8", "replace").strip()


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

    def test_ac43_preexisting_context_line_does_not_trigger(self):
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
