import json
import copy
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from agentic import ValidationError
from agentic import publication
from agentic.canonical import load
from agentic.contracts import Contracts
from agentic.gates import evaluate
from agentic.publication import (_detectors, _scan_text, render_aliases, render_scan,
                                 rewrite_unpublished, scan_repository)


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

    def test_builtin_detectors_do_not_flag_their_own_shipped_source(self):
        path = ROOT / ".agentic/lib/agentic/publication.py"
        detectors, allows = _detectors({}, {})
        findings = _scan_text(path.read_text(encoding="utf-8"), commit="working-tree",
                              path=".agentic/lib/agentic/publication.py", source="current_file",
                              detectors=detectors, allows=allows)
        self.assertEqual([], [item for item in findings if item["detector_id"].startswith("builtin.")])

    def test_historical_self_reference_identities_are_exact_and_path_scoped(self):
        detectors, allows = _detectors({}, {})
        ipv4 = '        return (address in ipaddress.ip_network("' + ".".join(("10", "0", "0", "0")) + '/8") or'
        windows = '        for value in ("../escape", "", "/root", "C:' + '/root", "data//nested"):'
        cases = [
            (ipv4, ".agentic/lib/agentic/publication.py", "builtin.private_ipv4"),
            (windows, ".agentic/tests/test_operating.py", "builtin.windows_absolute"),
        ]
        for line, path, detector_id in cases:
            with self.subTest(path=path):
                accepted = _scan_text(line, commit="fixture", path=path, source="patch",
                                      change="deleted", detectors=detectors, allows=allows)
                self.assertEqual([], accepted)
                other_path = _scan_text(line, commit="fixture", path="other.py", source="patch",
                                        change="deleted", detectors=detectors, allows=allows)
                self.assertTrue(any(item["detector_id"] == detector_id for item in other_path))
                changed = _scan_text(line + " ", commit="fixture", path=path, source="patch",
                                     change="deleted", detectors=detectors, allows=allows)
                self.assertTrue(any(item["detector_id"] == detector_id for item in changed))

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

    def test_ac53_complete_touched_head_content_reports_preexisting_context(self):
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
        retained = [item for item in result["findings"]
                    if item["source"] == "current-file" and item["path"] == "context.txt"]
        self.assertTrue(retained)
        self.assertTrue(all(item["classification"] == "PRE_EXISTING" for item in retained))
        self.assertEqual(0, result["blocking_findings"])
        self.assertEqual(len(result["findings"]), result["pre_existing_findings"])

    def test_deleted_preexisting_base_line_does_not_block(self):
        value = private_locator()
        git(self.repo.path, "switch", "main")
        self.repo.write("context.txt", value + "\nretained\n")
        git(self.repo.path, "add", "context.txt")
        git(self.repo.path, "commit", "-m", "preexisting private context")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("context.txt", "retained\n")
        self.repo.commit("remove preexisting private context", "context.txt")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("PASS", result["status"])
        deleted = [item for item in result["findings"] if item["change"] == "deleted"]
        self.assertTrue(deleted)
        self.assertTrue(all(item["classification"] == "PRE_EXISTING" for item in deleted))
        self.assertEqual(0, result["blocking_findings"])

    def test_exact_raw_membership_is_case_sensitive_and_candidate_additions_always_block(self):
        lower = private_locator()
        upper = lower.upper()
        git(self.repo.path, "switch", "main")
        self.repo.write("accepted.txt", upper + "\n")
        git(self.repo.path, "add", "accepted.txt")
        git(self.repo.path, "commit", "-m", "accepted base value")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("case-variant.txt", lower + "\n")
        self.repo.write("exact-duplicate.txt", upper + "\n")
        self.repo.commit("candidate additions", "case-variant.txt", "exact-duplicate.txt")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        added = [item for item in result["findings"] if item["change"] == "added"]
        self.assertTrue(added)
        self.assertTrue(all(item["classification"] == "BLOCKING" for item in added))
        self.assertEqual("BLOCKED", result["status"])

    def test_binary_base_classification_is_lossless_and_retains_known_exact_positives(self):
        value = private_locator()
        git(self.repo.path, "switch", "main")
        self.repo.write("context.txt", value + "\nold\n")
        self.repo.write("opaque.bin", data=b"opaque\0base")
        git(self.repo.path, "add", "context.txt", "opaque.bin")
        git(self.repo.path, "commit", "-m", "accepted mixed base")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("context.txt", value + "\nnew\n")
        self.repo.commit("change adjacent line", "context.txt")
        result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("PASS", result["status"])
        self.assertFalse(result["unscanned"])
        retained = [item for item in result["findings"] if item["source"] == "current-file"]
        self.assertTrue(retained)
        self.assertTrue(all(item["classification"] == "PRE_EXISTING" for item in retained))

    def test_failed_base_classification_retains_known_positives_and_fails_closed(self):
        value = private_locator()
        git(self.repo.path, "switch", "main")
        self.repo.write("context.txt", value + "\nold\n")
        self.repo.write("oversize.txt", "opaque\n")
        git(self.repo.path, "add", "context.txt", "oversize.txt")
        git(self.repo.path, "commit", "-m", "accepted base")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "awf/EX-6-publication")
        self.repo.write("context.txt", value + "\nnew\n")
        self.repo.commit("change adjacent line", "context.txt")
        real_blob = publication._blob

        def incomplete(root, oid, extra_env=None, *, classification=False):
            tree = publication._tree(root, base, extra_env)
            if classification and oid == tree["oversize.txt"][2]:
                return None, "oversize"
            return real_blob(root, oid, extra_env, classification=classification)

        with mock.patch.object(publication, "_blob", side_effect=incomplete):
            result = scan_repository(self.repo.path, base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item["source"] == "base-classification" for item in result["unscanned"]))
        retained = [item for item in result["findings"] if item["source"] == "current-file"]
        self.assertTrue(retained)
        self.assertTrue(all(item["classification"] == "PRE_EXISTING" for item in retained))

    def test_deleted_preexisting_base_line_is_independent_of_merge_parent_order(self):
        for root_first in (False, True):
            with self.subTest(root_first=root_first):
                parent = Path(self.temp.name) / ("root-first" if root_first else "root-second")
                parent.mkdir()
                repo = Repository(parent)
                mapping = repo.mapping()
                git(repo.path, "switch", "main")
                repo.write("context.txt", private_locator() + "\nretained\n")
                git(repo.path, "add", "context.txt")
                git(repo.path, "commit", "-m", "accepted base private line")
                base = git(repo.path, "rev-parse", "HEAD")
                git(repo.path, "switch", "-C", "awf/EX-6-publication")
                repo.write("context.txt", "retained\n")
                feature = repo.commit("delete accepted-base line", "context.txt")
                safe_blob = git(repo.path, "hash-object", "-w", "--stdin", input_bytes=b"safe root\n")
                root_tree = git(repo.path, "mktree", input_bytes=(
                    f"100644 blob {safe_blob}\tcontext.txt\n").encode("ascii"))
                side_root = git(repo.path, "commit-tree", root_tree, input_bytes=b"unrelated root\n")
                feature_tree = git(repo.path, "rev-parse", feature + "^{tree}")
                ordered = (side_root, feature) if root_first else (feature, side_root)
                merge = git(repo.path, "commit-tree", feature_tree,
                            "-p", ordered[0], "-p", ordered[1], input_bytes=b"merge root\n")
                git(repo.path, "update-ref", "refs/heads/awf/EX-6-publication", merge, feature)
                result = scan_repository(repo.path, base, merge, mapping_path=mapping)
                self.assertEqual("PASS", result["status"], render_scan(result))
                self.assertTrue(any(item["change"] == "deleted" and item["classification"] == "PRE_EXISTING"
                                    for item in result["findings"]))

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

    def test_invalid_utf8_blob_message_and_provider_text_are_unscanned(self):
        self.repo.write("invalid.dat", data=b"text-\xff-not-utf8")
        blob_commit = self.repo.commit("add invalid text", "invalid.dat")
        blob = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["commit"] == blob_commit and item["reason"] == "invalid-utf8"
                            for item in blob["unscanned"]))
        Contracts(ROOT / ".agentic/schemas").validate("publication-scan", blob)
        git(self.repo.path, "reset", "--hard", self.repo.base)
        self.repo.write("safe.txt", "changed\n")
        git(self.repo.path, "add", "safe.txt")
        tree = git(self.repo.path, "write-tree")
        parent = git(self.repo.path, "rev-parse", "HEAD")
        template = git(self.repo.path, "commit-tree", tree, "-p", parent, input_bytes=b"template\n")
        raw_template = subprocess.run(["git", "-C", str(self.repo.path), "cat-file", "commit", template],
                                      capture_output=True, check=True).stdout
        forged = raw_template.split(b"\n\n", 1)[0] + b"\n\nmessage-\xff\n"
        invalid_message = git(self.repo.path, "hash-object", "-t", "commit", "-w", "--stdin", input_bytes=forged)
        git(self.repo.path, "update-ref", "refs/heads/awf/EX-6-publication", invalid_message, parent)
        message = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["commit"] == invalid_message and item["path"] == "message"
                            and item["reason"] == "invalid-utf8" for item in message["unscanned"]))
        provider = Path(self.temp.name) / "provider.txt"
        provider.write_bytes(b"provider-\xff")
        body = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping,
                               pr_body_paths=[provider])
        self.assertTrue(any(item["path"] == "pr-body" and item["reason"] == "invalid-utf8"
                            for item in body["unscanned"]))

    def test_git_blob_size_is_checked_before_content_is_requested(self):
        calls = []

        def plumbing(_root, *args, **_kwargs):
            calls.append(args)
            if args[:2] == ("cat-file", "-s"):
                return subprocess.CompletedProcess(args, 0, str(publication.MAX_TEXT_BYTES + 1).encode(), b"")
            raise AssertionError("oversize blob content must not be requested")

        with mock.patch.object(publication, "_git", side_effect=plumbing):
            text, reason = publication._blob(self.repo.path, "a" * 40)
        self.assertIsNone(text)
        self.assertEqual("oversize", reason)
        self.assertEqual([("cat-file", "-s", "a" * 40)], calls)

    def test_provider_file_is_streamed_with_bounded_memory(self):
        provider = Path(self.temp.name) / "oversize-provider.txt"
        provider.write_bytes(b"0123456789")
        with mock.patch.object(publication, "MAX_TEXT_BYTES", 8):
            text, digest, reason = publication._provider_file(provider)
        self.assertIsNone(text)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual("oversize", reason)

        with mock.patch.object(publication, "MAX_TEXT_BYTES", 8):
            text, digest, reason = publication._provider_text("0123456789")
        self.assertIsNone(text)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual("oversize", reason)

        text, digest, reason = publication._provider_text("invalid-\ud800")
        self.assertIsNone(text)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual("invalid-utf8", reason)

    def test_parentless_side_root_is_scanned(self):
        value = private_locator().encode("utf-8") + b"\n"
        blob = git(self.repo.path, "hash-object", "-w", "--stdin", input_bytes=value)
        tree = git(self.repo.path, "mktree", input_bytes=f"100644 blob {blob}\troot.txt\n".encode("ascii"))
        root_commit = git(self.repo.path, "commit-tree", tree, input_bytes=b"unrelated root\n")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        head_tree = git(self.repo.path, "rev-parse", "HEAD^{tree}")
        merge = git(self.repo.path, "commit-tree", head_tree, "-p", old_head, "-p", root_commit,
                    input_bytes=b"merge unrelated root\n")
        git(self.repo.path, "update-ref", "refs/heads/awf/EX-6-publication", merge, old_head)
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertTrue(any(item["commit"] == root_commit and item["path"] == "root.txt"
                            for item in result["findings"]))

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

    def test_force_tracked_mapping_cannot_disable_builtin(self):
        value = "10." + "2.3.4"
        self.mapping.write_text(json.dumps({"version": 1, "builtin_allow": [{
            "id": "private_ipv4", "pattern": r"10\.2\.3\.4"}]}), encoding="utf-8")
        self.repo.write("generated.txt", value + "\n")
        git(self.repo.path, "add", "generated.txt")
        git(self.repo.path, "add", "-f", ".agentic-state/publication-deny.json")
        git(self.repo.path, "commit", "-m", "attempt tracked detector override")
        with self.assertRaisesRegex(ValidationError, "tracked content|share identity"):
            scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)

    def test_force_tracked_mapping_cannot_hide_behind_repository_alias(self):
        value = "10." + "2.3.4"
        self.mapping.write_text(json.dumps({"version": 1, "builtin_allow": [{
            "id": "private_ipv4", "pattern": r"10\.2\.3\.4"}]}), encoding="utf-8")
        self.repo.write("generated.txt", value + "\n")
        git(self.repo.path, "add", "generated.txt")
        git(self.repo.path, "add", "-f", ".agentic-state/publication-deny.json")
        git(self.repo.path, "commit", "-m", "attempt aliased tracked detector override")
        alias = Path(self.temp.name) / "repository-alias"
        try:
            alias.symlink_to(self.repo.path, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {type(exc).__name__}")
        aliased_mapping = alias / ".agentic-state" / "publication-deny.json"
        with self.assertRaisesRegex(ValidationError, "symlinks|tracked content|share identity"):
            scan_repository(self.repo.path.resolve(), self.repo.base, "HEAD",
                            mapping_path=aliased_mapping)

    def test_mapping_hardlink_to_tracked_content_is_refused(self):
        tracked = self.repo.path / "tracked-mapping.json"
        tracked.write_text(json.dumps({"version": 1, "builtin_allow": []}), encoding="utf-8")
        self.repo.commit("track mapping target", "tracked-mapping.json")
        alias = Path(self.temp.name) / "operator-map.json"
        try:
            os.link(tracked, alias)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {type(exc).__name__}")
        with self.assertRaisesRegex(ValidationError, "share identity"):
            scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=alias)

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_mapping_windows_junction_to_tracked_content_is_refused(self):
        tracked_dir = self.repo.path / "tracked-mapping-dir"
        tracked_dir.mkdir()
        tracked = tracked_dir / "mapping.json"
        tracked.write_text(json.dumps({"version": 1, "builtin_allow": []}), encoding="utf-8")
        self.repo.commit("track junction target", "tracked-mapping-dir/mapping.json")
        junction = Path(self.temp.name) / "mapping-junction"
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(tracked_dir)],
                              capture_output=True, text=True)
        if made.returncode:
            self.skipTest("directory junction unavailable")
        with self.assertRaisesRegex(ValidationError, "junctions|reparse"):
            scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=junction / "mapping.json")

    def test_custom_regex_policy_is_bounded_and_safe(self):
        for pattern in (r"(a+)+$", r"a.*b", r"a?b", r"a{1,8}b{1,8}", r"a{1,65}",
                        r"a{0,8}", r"^$", "a" * 257):
            with self.subTest(pattern=pattern):
                self.mapping.write_text(json.dumps({"version": 1, "deny_regexes": [{
                    "id": "unsafe", "pattern": pattern}]}), encoding="utf-8")
                with self.assertRaisesRegex(ValidationError, "regular expression"):
                    scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.mapping.write_text(json.dumps({"version": 1, "deny_regexes": [{
            "id": "bounded", "pattern": r"private-[0-9]{1,8}"}]}), encoding="utf-8")
        self.repo.write("safe.txt", "changed\n")
        self.repo.commit("safe", "safe.txt")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping,
                                 pr_body_texts=["private-1234"])
        self.assertTrue(any(item["detector_id"] == "local.regex.bounded" for item in result["findings"]))

    def test_findings_use_digest_only_redaction(self):
        value = private_locator()
        self.repo.write("generated.txt", value + "\n")
        self.repo.commit("add generated", "generated.txt")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        excerpts = [item["redacted_excerpt"] for item in result["findings"]]
        self.assertTrue(excerpts)
        self.assertTrue(all(re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in excerpts))
        self.assertNotIn(value[:2], json.dumps(excerpts))

    def test_scanner_source_passes_its_own_builtin_detectors(self):
        source = (ROOT / ".agentic/lib/agentic/publication.py").read_text(encoding="utf-8")
        self.repo.write("publication.py", source)
        self.repo.commit("dogfood publication scanner", "publication.py")
        result = scan_repository(self.repo.path, self.repo.base, "HEAD", mapping_path=self.mapping)
        self.assertEqual("PASS", result["status"], render_scan(result))

    def test_specification_names_fourteen_gates(self):
        specification = (ROOT / ".agentic/SPECIFICATION.md").read_text(encoding="utf-8")
        self.assertIn("records/fourteen gates", specification)
        self.assertNotIn("records/thirteen gates", specification)


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

    def test_ac44_all_push_urls_and_remote_tracking_refs_block(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        published = Path(self.temp.name) / "published.git"
        subprocess.run(["git", "init", "--bare", str(published)], check=True, capture_output=True)
        shutil.copytree(self.repo.path / ".git" / "objects", published / "objects", dirs_exist_ok=True)
        subprocess.run(["git", "--git-dir", str(published), "update-ref",
                        "refs/heads/awf/EX-6-publication", head], check=True, capture_output=True)
        git(self.repo.path, "remote", "set-url", "--add", "--push", "origin", str(self.remote))
        git(self.repo.path, "remote", "set-url", "--add", "--push", "origin", str(published))
        with self.assertRaisesRegex(ValidationError, "configured remote URL"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        git(self.repo.path, "config", "--unset-all", "remote.origin.pushurl")
        git(self.repo.path, "update-ref", "refs/remotes/origin/other", head)
        with self.assertRaisesRegex(ValidationError, "remote-tracking ref"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_every_ref_namespace_is_in_the_reachability_census(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "update-ref", "refs/notes/retention-proof", head)
        with self.assertRaisesRegex(ValidationError, "reachable from another"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_reachability_lookup_error_refuses_before_mutation(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        refs = git(self.repo.path, "for-each-ref", "--format=%(refname)%00%(objectname)")
        objects = git(self.repo.path, "count-objects", "-v")
        real_git = publication._git

        def fail_lookup(root, *args, **kwargs):
            if args[:2] == ("merge-base", "--is-ancestor"):
                return subprocess.CompletedProcess(args, 2, b"", b"synthetic lookup failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=fail_lookup):
            with self.assertRaisesRegex(ValidationError, "reachability lookup failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(refs, git(self.repo.path, "for-each-ref", "--format=%(refname)%00%(objectname)"))
        self.assertEqual(objects, git(self.repo.path, "count-objects", "-v"))

    def test_ac44_distinct_fetch_and_push_urls_are_all_checked(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        published = Path(self.temp.name) / "fetch-published.git"
        subprocess.run(["git", "init", "--bare", str(published)], check=True, capture_output=True)
        shutil.copytree(self.repo.path / ".git" / "objects", published / "objects", dirs_exist_ok=True)
        subprocess.run(["git", "--git-dir", str(published), "update-ref",
                        "refs/heads/awf/EX-6-publication", head], check=True, capture_output=True)
        git(self.repo.path, "remote", "set-url", "origin", str(published))
        git(self.repo.path, "remote", "set-url", "--push", "origin", str(self.remote))
        with self.assertRaisesRegex(ValidationError, "configured remote URL"):
            rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_failed_final_cas_retains_valid_unreachable_object_without_ref_change(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        refs_before = git(self.repo.path, "for-each-ref", "--format=%(refname)%00%(objectname)")
        real_git = publication._git
        created = []

        def fail_update(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=fail_update):
            with self.assertRaisesRegex(ValidationError, "Atomic branch update failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(1, len(created))
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(refs_before, git(self.repo.path, "for-each-ref", "--format=%(refname)%00%(objectname)"))
        self.assertEqual("commit", git(self.repo.path, "cat-file", "-t", created[0]))
        self.assertEqual("", git(self.repo.path, "for-each-ref", "--contains", created[0], "--format=%(refname)"))
        reachable = git(self.repo.path, "rev-list", "--all", "--reflog").splitlines()
        self.assertNotIn(created[0], reachable)

    def test_ac44_failed_final_cas_preserves_object_claimed_by_concurrent_ref(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        created = []
        def lose_after_concurrent_ref(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                real_git(root, "update-ref", "refs/heads/concurrent", args[2])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)
        with mock.patch.object(publication, "_git", side_effect=lose_after_concurrent_ref):
            with self.assertRaisesRegex(ValidationError, "Atomic branch update failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(created[0], git(self.repo.path, "rev-parse", "refs/heads/concurrent^{commit}"))
        self.assertEqual("", git(self.repo.path, "cat-file", "-e", created[0] + "^{commit}"))

    def test_ac44_failed_final_cas_preserves_object_retained_only_by_reflog(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        created = []
        def lose_after_concurrent_ref_moves_away(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                real_git(root, "update-ref", "refs/heads/concurrent", args[2])
                real_git(root, "update-ref", "refs/heads/concurrent", head, args[2])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)
        with mock.patch.object(publication, "_git", side_effect=lose_after_concurrent_ref_moves_away):
            with self.assertRaisesRegex(ValidationError, "Atomic branch update failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(head, git(self.repo.path, "rev-parse", "refs/heads/concurrent^{commit}"))
        reflog = git(self.repo.path, "reflog", "show", "--format=%H", "refs/heads/concurrent").splitlines()
        self.assertIn(created[0], reflog)
        self.assertEqual("", git(self.repo.path, "cat-file", "-e", created[0] + "^{commit}"))
        checked = subprocess.run(["git", "-C", str(self.repo.path), "fsck", "--full"],
                                 capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_ac44_failed_final_cas_preserves_reflogless_detached_head_claim(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "config", "core.logAllRefUpdates", "false")
        (self.repo.path / ".git" / "logs" / "HEAD").unlink(missing_ok=True)
        real_git = publication._git
        created = []

        def fail_final_update(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                real_git(self.repo.path, "update-ref", "--no-deref", "HEAD", created[0])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=fail_final_update):
            with self.assertRaisesRegex(ValidationError, "Atomic branch update failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertFalse((self.repo.path / ".git" / "logs" / "HEAD").exists())
        self.assertEqual(head, git(self.repo.path, "rev-parse", "refs/heads/awf/EX-6-publication^{commit}"))
        self.assertEqual(created[0], git(self.repo.path, "rev-parse", "HEAD^{commit}"))
        self.assertEqual("", git(self.repo.path, "cat-file", "-e", created[0] + "^{commit}"))
        checked = subprocess.run(["git", "-C", str(self.repo.path), "fsck", "--full"],
                                 capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_ac44_failed_final_cas_preserves_reflogless_orig_head_claim(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "config", "core.logAllRefUpdates", "false")
        (self.repo.path / ".git" / "logs" / "ORIG_HEAD").unlink(missing_ok=True)
        real_git = publication._git
        created = []

        def fail_final_update(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                real_git(self.repo.path, "update-ref", "ORIG_HEAD", created[0])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=fail_final_update):
            with self.assertRaisesRegex(ValidationError, "Atomic branch update failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertFalse((self.repo.path / ".git" / "logs" / "ORIG_HEAD").exists())
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(created[0], git(self.repo.path, "rev-parse", "ORIG_HEAD^{commit}"))
        self.assertEqual("", git(self.repo.path, "cat-file", "-e", created[0] + "^{commit}"))
        checked = subprocess.run(["git", "-C", str(self.repo.path), "fsck", "--full"],
                                 capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_ac44_failed_final_cas_preserves_linked_worktree_detached_head_claim(self):
        linked = Path(self.temp.name) / "linked"
        git(self.repo.path, "config", "core.logAllRefUpdates", "false")
        git(self.repo.path, "worktree", "add", "--detach", str(linked), self.repo.base)
        linked_git_dir = Path(git(linked, "rev-parse", "--git-dir"))
        (linked_git_dir / "logs" / "HEAD").unlink(missing_ok=True)
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        created = []

        def fail_final_update(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                real_git(linked, "update-ref", "--no-deref", "HEAD", created[0])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=fail_final_update):
            with self.assertRaisesRegex(ValidationError, "Atomic branch update failed"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertFalse((linked_git_dir / "logs" / "HEAD").exists())
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(created[0], git(linked, "rev-parse", "HEAD^{commit}"))
        self.assertEqual("", git(self.repo.path, "cat-file", "-e", created[0] + "^{commit}"))
        checked = subprocess.run(["git", "-C", str(self.repo.path), "fsck", "--full"],
                                 capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_ac44_invalid_utf8_message_refuses_without_change(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.bin"
        message.write_bytes(b"clean-\xff")
        head = git(self.repo.path, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValidationError, "strict UTF-8"):
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

    def test_preexisting_only_pass_receipt_is_accepted_and_counts_fail_closed(self):
        config = load(ROOT / ".agentic" / "examples" / "PROJECT_CONFIG.yaml")
        workflow = load(ROOT / ".agentic" / "workflow.yaml")
        bundle = load(ROOT / ".agentic" / "examples" / "evidence-bundle.json")
        contracts = Contracts(ROOT / ".agentic" / "schemas")
        finding = {"commit": bundle["candidate"]["head_sha"], "path": "src/example.py", "line": 1,
                   "source": "current-file", "change": None, "detector_id": "declared.literal.1",
                   "classification": "PRE_EXISTING", "redacted_excerpt": "sha256:" + "1" * 64}
        publication_scan = bundle["publication_scan"]
        publication_scan["findings"] = [finding]
        publication_scan["total_findings"] = 1
        publication_scan["blocking_findings"] = 0
        publication_scan["pre_existing_findings"] = 1
        gate = evaluate(config, workflow, bundle, contracts, "2026-09-09T12:00:00Z")
        self.assertEqual("PASS", gate["gates"]["publication_safety"]["result"])

        inconsistent = copy.deepcopy(bundle)
        inconsistent["publication_scan"]["total_findings"] = 2
        gate = evaluate(config, workflow, inconsistent, contracts, "2026-09-09T12:00:00Z")
        self.assertEqual("FAIL", gate["gates"]["publication_safety"]["result"])

        blocking = copy.deepcopy(bundle)
        blocking["publication_scan"]["findings"][0]["classification"] = "BLOCKING"
        blocking["publication_scan"]["blocking_findings"] = 1
        blocking["publication_scan"]["pre_existing_findings"] = 0
        blocking["publication_scan"]["status"] = "BLOCKED"
        gate = evaluate(config, workflow, blocking, contracts, "2026-09-09T12:00:00Z")
        self.assertEqual("FAIL", gate["gates"]["publication_safety"]["result"])

        unscanned = copy.deepcopy(bundle)
        unscanned["publication_scan"]["unscanned"] = [{"commit": bundle["candidate"]["head_sha"],
            "path": "provider", "source": "pr-body", "reason": "oversize", "parent": None}]
        unscanned["publication_scan"]["unscanned_count"] = 1
        unscanned["publication_scan"]["status"] = "BLOCKED"
        gate = evaluate(config, workflow, unscanned, contracts, "2026-09-09T12:00:00Z")
        self.assertEqual("FAIL", gate["gates"]["publication_safety"]["result"])

        malformed = copy.deepcopy(bundle)
        malformed["publication_scan"]["total_findings"] = -1
        with self.assertRaises(ValidationError):
            evaluate(config, workflow, malformed, contracts, "2026-09-09T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
