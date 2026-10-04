import json
import copy
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zlib

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


def object_exists(repository, oid):
    return subprocess.run(["git", "-C", str(repository), "cat-file", "-e", oid],
                          capture_output=True, check=False).returncode == 0


def remove_synthetic_object_tree(path):
    """Remove only an isolated test fanout after clearing Git's read-only bit."""
    path = Path(path)
    if not path.exists():
        return
    for item in path.rglob("*"):
        if item.is_file():
            item.chmod(stat.S_IWRITE)
    shutil.rmtree(path)


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

    def test_historical_synthetic_ticket_allow_is_exact_and_real_jira_still_blocks(self):
        restricted = {"deny_regexes": [{
            "id": "restricted_identifier", "pattern": r"\b" + "QA-" + r"\d{1,64}\b"}]}
        detectors, allows = _detectors(restricted, {})
        synthetic = "QA-" + "1"
        line = '            ticket("' + synthetic + '", 1, "BLOCKED", reason="dependency unavailable"),'
        path = ".agentic/tests/test_continuous_controller.py"
        accepted = _scan_text(line, commit="published", path=path, source="patch", change="deleted",
                              detectors=detectors, allows=allows)
        self.assertEqual([], accepted)
        real = "QA-" + "9310"
        adversarial = (
            (line + " ", path, synthetic),
            (line, ".agentic/tests/other.py", synthetic),
            (line.replace(synthetic, real), path, real),
            (synthetic, "pr-body", synthetic),
        )
        for text, observed_path, value in adversarial:
            with self.subTest(path=observed_path, value=value):
                findings = _scan_text(text, commit="candidate", path=observed_path, source="patch",
                                      detectors=detectors, allows=allows)
                self.assertTrue(any(item["detector_id"] == "local.regex.restricted_identifier"
                                    for item in findings))

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

    def test_ac44_failed_final_cas_retains_owned_object_with_truthful_recovery_required(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        snapshot = publication._rewrite_snapshot(self.repo.path)
        real_git = publication._git
        created = []

        def fail_update(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=fail_update):
            with self.assertRaisesRegex(ValidationError, "CAS_FAILED_RECOVERY_REQUIRED"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(1, len(created))
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertTrue(object_exists(self.repo.path, created[0]))
        current = publication._rewrite_snapshot(self.repo.path)
        for key in snapshot:
            if key not in {"objects", "fanouts"}:
                self.assertEqual(snapshot[key], current[key])

    def test_ac44_failed_final_cas_preserves_objects_claimed_by_all_ref_namespaces(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        created = []
        concurrent_refs = ["refs/heads/concurrent", "refs/tags/concurrent",
                           "refs/remotes/origin/concurrent", "refs/notes/concurrent",
                           "refs/replace/" + head]
        def lose_after_concurrent_ref(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                for concurrent_ref in concurrent_refs:
                    real_git(root, "update-ref", concurrent_ref, args[2])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)
        with mock.patch.object(publication, "_git", side_effect=lose_after_concurrent_ref):
            with self.assertRaisesRegex(ValidationError, "CAS_FAILED_RECOVERY_REQUIRED"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        for concurrent_ref in concurrent_refs:
            self.assertEqual(created[0], git(self.repo.path, "--no-replace-objects", "rev-parse",
                                             concurrent_ref + "^{commit}"))
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
            with self.assertRaisesRegex(ValidationError, "CAS_FAILED_RECOVERY_REQUIRED"):
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
            with self.assertRaisesRegex(ValidationError, "CAS_FAILED_RECOVERY_REQUIRED"):
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
            with self.assertRaisesRegex(ValidationError, "CAS_FAILED_RECOVERY_REQUIRED"):
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
            with self.assertRaisesRegex(ValidationError, "CAS_FAILED_RECOVERY_REQUIRED"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertFalse((linked_git_dir / "logs" / "HEAD").exists())
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(created[0], git(linked, "rev-parse", "HEAD^{commit}"))
        self.assertEqual("", git(self.repo.path, "cat-file", "-e", created[0] + "^{commit}"))
        checked = subprocess.run(["git", "-C", str(self.repo.path), "fsck", "--full"],
                                 capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_ac44_claimant_created_after_census_keeps_object_and_recovery_evidence(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        created = []

        def fail_update(root, *args, **kwargs):
            if args and args[0] == "update-ref" and args[1] == "refs/heads/awf/EX-6-publication":
                created.append(args[2])
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic compare-and-swap failure")
            return real_git(root, *args, **kwargs)

        real_has_claimant = publication._has_claimant
        raced = []

        def create_claimant_after_census(root, oid):
            claimed = real_has_claimant(root, oid)
            self.assertFalse(claimed)
            real_git(root, "update-ref", "refs/heads/after-census-race", oid)
            raced.append(oid)
            # This models a claimant created immediately after a complete
            # census but before the caller could have unlinked the object.
            return claimed

        with mock.patch.object(publication, "_git", side_effect=fail_update), \
                mock.patch.object(publication, "_has_claimant", side_effect=create_claimant_after_census):
            with self.assertRaisesRegex(ValidationError,
                                        r"CAS_FAILED_RECOVERY_REQUIRED:.*retained_objects=.*non_destructive_recovery="):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(created, raced)
        self.assertTrue(object_exists(self.repo.path, created[0]))
        self.assertEqual(created[0], git(self.repo.path, "rev-parse", "refs/heads/after-census-race"))

    def test_ac44_cleanup_never_touches_preexisting_packed_or_alternate_records(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        existing = git(self.repo.path, "rev-parse", "HEAD")
        loose = publication._loose_path(Path(snapshot["object_dir"]), existing,
                                        snapshot["object_format"])
        records = [
            {"oid": existing, "snapshot_present": True, "path": loose},
            {"oid": existing, "snapshot_present": True,
             "path": Path(snapshot["object_dir"]) / "missing-packed"},
            {"oid": existing, "snapshot_present": True,
             "path": Path(self.temp.name) / "alternate-object"},
        ]
        publication._cleanup_new_objects(self.repo.path, records, snapshot)
        self.assertTrue(object_exists(self.repo.path, existing))

    def test_ac44_ambiguous_partial_install_is_retained_and_fails_closed(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        snapshot = publication._rewrite_snapshot(self.repo.path)
        real_link = os.link
        installed = []

        def fail_after_link(source, target, *args, **kwargs):
            real_link(source, target, *args, **kwargs)
            target = Path(target)
            installed.append(target.parent.name + target.name)
            raise OSError("synthetic uncertainty after atomic create")

        with mock.patch.object(publication.os, "link", side_effect=fail_after_link):
            with self.assertRaisesRegex(ValidationError, "PRE_CAS_RECOVERY_REQUIRED") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(1, len(installed))
        self.assertTrue(object_exists(self.repo.path, installed[0]))
        current = publication._rewrite_snapshot(self.repo.path)
        for key in snapshot:
            if key not in {"objects", "fanouts"}:
                self.assertEqual(snapshot[key], current[key])
        prefix = installed[0][:2]
        if snapshot["fanouts"][prefix] is None:
            self.assertIsNotNone(current["fanouts"][prefix])
            self.assertIn("retained_fanouts=" + prefix + "@", str(caught.exception))
        else:
            # The replacement commit hash can share one of the repository's
            # existing fanouts.  The uncertain link is still retained and
            # must fail closed, while the snapshot-present fanout identity is
            # unchanged and must never be attributed to this operation.
            self.assertEqual(snapshot["fanouts"][prefix], current["fanouts"][prefix])
            self.assertIn("retained_objects=" + installed[0], str(caught.exception))

    def test_ac44_link_permission_failure_retains_operation_created_fanout(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        snapshot = publication._rewrite_snapshot(self.repo.path)
        object_dir = Path(snapshot["object_dir"])
        namespace = {entry.name for entry in object_dir.iterdir()}

        # Probe candidate commit IDs in a disposable object database so the
        # test always exercises creation of a new fanout. The previous fixed
        # message left the prefix to a timestamp-derived hash, which could
        # collide with a fanout already present in this repository.
        probe_objects = Path(self.temp.name) / "probe-objects"
        probe_objects.mkdir()
        fixed_dates = {
            "GIT_AUTHOR_NAME": "Synthetic User",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_NAME": "Synthetic User",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
        tree = git(self.repo.path, "rev-parse", "HEAD^{tree}")
        candidate = None
        for value in range(256):
            candidate_message = f"clean squash\ncandidate {value:03d}\n".encode("utf-8")
            probe_env = {
                **fixed_dates,
                "GIT_OBJECT_DIRECTORY": str(probe_objects),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(object_dir),
            }
            candidate_oid = publication._git(
                self.repo.path, "commit-tree", tree, "-p", self.repo.base,
                input_bytes=candidate_message, extra_env=probe_env,
            ).stdout.decode("ascii").strip()
            if candidate_oid[:2] not in namespace:
                candidate = candidate_message
                break
        self.assertIsNotNone(candidate, "could not select an absent object fanout")
        message.write_bytes(candidate)
        real_git = publication._git

        def fixed_commit_dates(root, *args, **kwargs):
            if args and args[0] == "commit-tree":
                extra_env = dict(kwargs.get("extra_env") or {})
                extra_env.update(fixed_dates)
                kwargs["extra_env"] = extra_env
            return real_git(root, *args, **kwargs)

        try:
            with mock.patch.object(publication, "_git", side_effect=fixed_commit_dates), \
                    mock.patch.object(publication.os, "link",
                                      side_effect=PermissionError("synthetic hard-link denial")):
                with self.assertRaisesRegex(ValidationError,
                                            r"PRE_CAS_RECOVERY_REQUIRED:.*retained_fanouts="):
                    rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                        mapping_path=self.mapping)
            created = [entry for entry in object_dir.iterdir() if entry.name not in namespace]
            self.assertEqual(1, len(created))
            self.assertEqual(candidate_oid[:2], created[0].name)
            self.assertTrue(created[0].is_dir())
            self.assertEqual([], list(created[0].iterdir()))
        finally:
            for entry in object_dir.iterdir():
                if entry.name not in namespace and entry.is_dir():
                    entry.rmdir()

    def test_ac44_fanout_created_after_locked_snapshot_is_never_snapshot_present(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        real_install = publication._install_quarantine_objects
        raced = []
        seen = []

        def create_after_snapshot(root, records, snapshot):
            # This isolated fixture contains fewer than 256 loose-object
            # fanouts, so an absent prefix is guaranteed independently of the
            # replacement commit hash.  Add an unreachable synthetic record;
            # the intended fanout race must reject before its bytes are used.
            occupied = {prefix for prefix, identity in snapshot["fanouts"].items()
                        if identity is not None}
            self.assertLess(len(occupied), 256)
            prefix = min(set(snapshot["fanouts"]) - occupied)
            record = {"oid": prefix + "0" * 38, "kind": "blob", "raw": b"",
                      "loose_bytes": zlib.compress(b"blob 0\0")}
            records.append(record)
            fanout = Path(snapshot["object_dir"]) / record["oid"][:2]
            fanout.mkdir()
            marker = fanout / "external-after-snapshot"
            marker.write_bytes(b"external writer")
            raced.append((fanout, marker))
            seen.extend(records)
            return real_install(root, records, snapshot)

        try:
            with mock.patch.object(publication, "_install_quarantine_objects",
                                   side_effect=create_after_snapshot):
                with self.assertRaisesRegex(ValidationError,
                                            r"PRE_CAS_RECOVERY_REQUIRED:.*ambiguous_fanouts="):
                    rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                        mapping_path=self.mapping)
            self.assertEqual(1, len(raced))
            state = next(record["fanout"] for record in seen
                         if record["fanout"]["path"] == raced[0][0])
            self.assertFalse(state["snapshot_present"])
            self.assertTrue(state["ambiguous"])
            self.assertTrue(raced[0][1].is_file())
        finally:
            for fanout, marker in raced:
                marker.unlink(missing_ok=True)
                fanout.rmdir()

    def test_ac44_oversized_rewrite_message_is_rejected_before_commit_tree(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "oversized-message.txt"
        with message.open("wb") as handle:
            handle.truncate(publication.MAX_REWRITE_PROOF_OBJECT_BYTES + 1)
        head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        commit_tree_calls = []

        def observe_git(root, *args, **kwargs):
            if args and args[0] == "commit-tree":
                commit_tree_calls.append(args)
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=observe_git):
            with self.assertRaisesRegex(ValidationError,
                                        "Rewrite message exceeds the publication proof bound"):
                rewrite_unpublished(self.repo.path, self.repo.base,
                                    "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual([], commit_tree_calls)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_oversized_quarantine_loose_object_rejects_before_content_read(self):
        objects = Path(self.temp.name) / "oversized-quarantine"
        path = objects / "aa" / ("0" * 38)
        path.parent.mkdir(parents=True)
        with path.open("wb") as handle:
            handle.truncate(publication.MAX_REWRITE_PROOF_OBJECT_BYTES * 2 + 1025)
        with mock.patch.object(publication, "_git_bounded_stdout") as bounded:
            with self.assertRaisesRegex(ValidationError,
                                        "Quarantine loose object exceeds the publication proof bound"):
                publication._quarantine_objects(
                    self.repo.path, objects, {}, "sha1")
        bounded.assert_not_called()

    def test_ac44_quarantine_inventory_stops_at_overcount_sentinel_before_reads(self):
        objects = Path(self.temp.name) / "overcount-quarantine"
        fanout = objects / "aa"
        fanout.mkdir(parents=True)
        for value in range(4):
            (fanout / f"{value:038x}").write_bytes(b"synthetic")
        with mock.patch.object(publication, "MAX_REWRITE_PROOF_OBJECTS", 3), \
                mock.patch.object(publication, "_git_bounded_stdout") as bounded, \
                mock.patch.object(publication, "_bounded_regular_file") as read_file:
            with self.assertRaisesRegex(ValidationError,
                                        "Quarantine object inventory exceeds the object-count bound"):
                publication._quarantine_objects(
                    self.repo.path, objects, {}, "sha1")
        bounded.assert_not_called()
        read_file.assert_not_called()

    def test_ac44_bounded_quarantine_path_consumer_stops_after_sentinel(self):
        consumed = []

        def oversized_names():
            while True:
                consumed.append(len(consumed))
                yield Path("aa") / f"{len(consumed):038x}"

        with self.assertRaisesRegex(ValidationError,
                                    "Quarantine object inventory exceeds the object-count bound"):
            publication._bounded_quarantine_paths(oversized_names(), 3)
        self.assertEqual(4, len(consumed))

    def test_ac44_quarantine_content_uses_bounded_git_retrieval(self):
        objects = Path(self.temp.name) / "bounded-quarantine"
        path = objects / "aa" / ("0" * 38)
        path.parent.mkdir(parents=True)
        path.write_bytes(zlib.compress(b"blob 0\0"))
        calls = []

        def bounded(root, *args, **kwargs):
            calls.append((args, kwargs.get("limit")))
            if args[:2] == ("cat-file", "-t"):
                return b"blob\n"
            raise ValidationError("git cat-file output exceeded the publication proof bound")

        with mock.patch.object(publication, "_git_bounded_stdout", side_effect=bounded):
            with self.assertRaisesRegex(ValidationError, "output exceeded"):
                publication._quarantine_objects(
                    self.repo.path, objects, {}, "sha1")
        self.assertEqual([
            (("cat-file", "-t", "aa" + "0" * 38), 64),
            (("cat-file", "blob", "aa" + "0" * 38),
             publication.MAX_REWRITE_PROOF_OBJECT_BYTES),
        ], calls)

    def test_ac44_second_quarantine_census_pathname_change_fails_before_reads(self):
        objects = Path(self.temp.name) / "changed-quarantine"
        fanout = objects / "aa"
        fanout.mkdir(parents=True)
        (fanout / ("0" * 38)).write_bytes(zlib.compress(b"blob 0\0"))
        real_fingerprint = publication._quarantine_path_fingerprint
        calls = []

        def add_after_first_census(paths):
            result = real_fingerprint(paths)
            calls.append(True)
            if len(calls) == 1:
                (fanout / ("1" * 38)).write_bytes(b"changed namespace")
            return result

        with mock.patch.object(publication, "_quarantine_path_fingerprint",
                               side_effect=add_after_first_census), \
                mock.patch.object(publication, "_git_bounded_stdout") as bounded, \
                mock.patch.object(publication, "_bounded_regular_file") as read_file:
            with self.assertRaisesRegex(
                    ValidationError, "Quarantine object inventory changed during enumeration"):
                publication._quarantine_objects(self.repo.path, objects, {}, "sha1")
        self.assertEqual(1, len(calls))
        bounded.assert_not_called()
        read_file.assert_not_called()

    def test_ac44_empty_operation_created_fanout_is_retained_without_pathname_rmdir(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        object_dir = Path(snapshot["object_dir"])
        prefix = next(f"{value:02x}" for value in range(256)
                      if not (object_dir / f"{value:02x}").exists())
        fanout = object_dir / prefix
        fanout.mkdir()
        details = fanout.lstat()
        state = {"path": fanout, "snapshot_present": False, "snapshot_identity": None,
                 "created_by_operation": True,
                 "installed_identity": (details.st_dev, details.st_ino), "ambiguous": False}
        record = {"oid": prefix + "0" * 38, "snapshot_present": False,
                  "path": fanout / ("0" * 38), "fanout": state}
        try:
            with mock.patch.object(Path, "rmdir") as remove:
                with self.assertRaisesRegex(ValidationError,
                                            "Atomic identity-bound fanout removal is unavailable"):
                    publication._cleanup_new_objects(self.repo.path, [record], snapshot)
            remove.assert_not_called()
            self.assertTrue(fanout.is_dir())
            evidence = publication._recovery_message("PRE_CAS_RECOVERY_REQUIRED", [record],
                                                     "1" * 40, "2" * 40, "synthetic")
            self.assertTrue(evidence.startswith("PRE_CAS_RECOVERY_REQUIRED:"))
            self.assertIn("retained_objects=none", evidence)
            self.assertIn("retained_fanouts=" + prefix + "@", evidence)
        finally:
            fanout.rmdir()

    def test_ac44_nonempty_operation_created_fanout_is_ambiguous_residue(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        object_dir = Path(snapshot["object_dir"])
        prefix = next(f"{value:02x}" for value in range(256)
                      if not (object_dir / f"{value:02x}").exists())
        fanout = object_dir / prefix
        fanout.mkdir()
        marker = fanout / "foreign-residue"
        marker.write_bytes(b"concurrent namespace change")
        details = fanout.lstat()
        state = {"path": fanout, "snapshot_present": False, "snapshot_identity": None,
                 "created_by_operation": True,
                 "installed_identity": (details.st_dev, details.st_ino), "ambiguous": False}
        record = {"oid": prefix + "0" * 38, "snapshot_present": False,
                  "path": fanout / ("0" * 38), "fanout": state}
        try:
            with self.assertRaisesRegex(ValidationError,
                                        "Atomic identity-bound fanout removal is unavailable"):
                publication._cleanup_new_objects(self.repo.path, [record], snapshot)
            self.assertTrue(marker.is_file())
            evidence = publication._recovery_message("PRE_CAS_RECOVERY_REQUIRED", [record],
                                                     "1" * 40, "2" * 40, "synthetic")
            self.assertIn("retained_fanouts=" + prefix + "@", evidence)
        finally:
            marker.unlink()
            fanout.rmdir()

    def test_ac44_identity_swap_before_fanout_removal_never_deletes_external_directory(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        object_dir = Path(snapshot["object_dir"])
        prefix = next(f"{value:02x}" for value in range(256)
                      if not (object_dir / f"{value:02x}").exists())
        fanout = object_dir / prefix
        fanout.mkdir()
        original = fanout.lstat()
        state = {"path": fanout, "snapshot_present": False, "snapshot_identity": None,
                 "created_by_operation": True,
                 "installed_identity": (original.st_dev, original.st_ino), "ambiguous": False}
        record = {"oid": prefix + "0" * 38, "snapshot_present": False,
                  "path": fanout / ("0" * 38), "fanout": state}
        fanout.rmdir()
        fanout.mkdir()
        marker = fanout / "external-replacement"
        marker.write_bytes(b"external writer")
        try:
            with self.assertRaisesRegex(ValidationError,
                                        "Operation-created fanout identity changed"):
                publication._cleanup_new_objects(self.repo.path, [record], snapshot)
            self.assertTrue(marker.is_file())
            evidence = publication._recovery_message("PRE_CAS_RECOVERY_REQUIRED", [record],
                                                     "1" * 40, "2" * 40, "synthetic")
            self.assertIn("retained_fanouts=" + prefix + "@", evidence)
        finally:
            marker.unlink()
            fanout.rmdir()

    def test_ac44_concurrent_ambiguous_fanout_is_reported_and_never_removed(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        object_dir = Path(snapshot["object_dir"])
        prefix = next(f"{value:02x}" for value in range(256)
                      if not (object_dir / f"{value:02x}").exists())
        fanout = object_dir / prefix
        fanout.mkdir()
        marker = fanout / "concurrent-residue"
        marker.write_bytes(b"external writer")
        state = {"path": fanout, "snapshot_present": False, "snapshot_identity": None,
                 "created_by_operation": False, "installed_identity": None, "ambiguous": True}
        record = {"oid": prefix + "0" * 38, "snapshot_present": False,
                  "path": fanout / ("0" * 38), "fanout": state}
        try:
            with self.assertRaisesRegex(ValidationError, "fanout provenance is ambiguous"):
                publication._cleanup_new_objects(self.repo.path, [record], snapshot)
            self.assertTrue(marker.is_file())
            evidence = publication._recovery_message("PRE_CAS_RECOVERY_REQUIRED", [record],
                                                     "1" * 40, "2" * 40, "synthetic")
            self.assertIn("ambiguous_fanouts=" + prefix + "@", evidence)
        finally:
            marker.unlink()
            fanout.rmdir()

    def test_ac44_reflog_restore_refuses_file_symlink_before_external_mutation(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        log = common / "logs" / "HEAD"
        external = Path(self.temp.name) / "external-head-log"
        external.write_bytes(b"external reflog bytes\n")
        original = log.read_bytes()
        log.unlink()
        try:
            try:
                log.symlink_to(external)
            except OSError as exc:
                self.skipTest("file symlinks unavailable: " + type(exc).__name__)
            with self.assertRaisesRegex(ValidationError,
                                        "identity-bound no-follow replacement and deletion"):
                publication._restore_rewrite_reflogs(
                    self.repo.path, snapshot, "refs/heads/awf/EX-6-publication")
            self.assertEqual(b"external reflog bytes\n", external.read_bytes())
            self.assertTrue(log.is_symlink())
        finally:
            log.unlink(missing_ok=True)
            log.write_bytes(original)

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_ac44_reflog_restore_refuses_windows_junction_before_external_mutation(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        logs = common / "logs"
        held = common / "logs-held-for-test"
        external = Path(self.temp.name) / "external-logs"
        external.mkdir()
        marker = external / "HEAD"
        marker.write_bytes(b"external junction bytes\n")
        logs.rename(held)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(logs), str(external)],
                              capture_output=True, check=False).returncode == 0
        if not made:
            held.rename(logs)
            self.skipTest("directory junction unavailable")
        try:
            with self.assertRaisesRegex(ValidationError,
                                        "identity-bound no-follow replacement and deletion"):
                publication._restore_rewrite_reflogs(
                    self.repo.path, snapshot, "refs/heads/awf/EX-6-publication")
            self.assertEqual(b"external junction bytes\n", marker.read_bytes())
            self.assertTrue(logs.is_junction())
        finally:
            logs.rmdir()
            held.rename(logs)

    def test_ac44_reflog_restore_refuses_parent_swap_before_namespace_mutation(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        parent = common / "logs" / "refs" / "heads" / "awf"
        held = parent.with_name("awf-held-for-test")
        parent.rename(held)
        parent.mkdir()
        marker = parent / "external-parent-swap"
        marker.write_bytes(b"external parent bytes\n")
        try:
            with self.assertRaisesRegex(ValidationError,
                                        "identity-bound no-follow replacement and deletion"):
                publication._restore_rewrite_reflogs(
                    self.repo.path, snapshot, "refs/heads/awf/EX-6-publication")
            self.assertEqual(b"external parent bytes\n", marker.read_bytes())
            self.assertEqual([marker], list(parent.iterdir()))
        finally:
            marker.unlink()
            parent.rmdir()
            held.rename(parent)

    def test_ac44_reflog_restore_refuses_absent_original_deletion(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        snapshot = copy.deepcopy(snapshot)
        ref = "refs/heads/awf/EX-6-publication"
        snapshot["logs"].pop(ref, None)
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        log = common / "logs" / ref
        before = log.read_bytes()
        with self.assertRaisesRegex(ValidationError,
                                    "identity-bound no-follow replacement and deletion"):
            publication._restore_rewrite_reflogs(self.repo.path, snapshot, ref)
        self.assertTrue(log.is_file())
        self.assertEqual(before, log.read_bytes())

    def test_ac44_post_cas_proof_rejects_snapshot_present_fanout_replacement(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        ref = "refs/heads/awf/EX-6-publication"
        head = git(self.repo.path, "rev-parse", "HEAD")
        closure = publication._replacement_closure_evidence(
            self.repo.path, head, snapshot, [], None)
        object_dir = Path(snapshot["object_dir"])
        oid = next(value for value in snapshot["objects"]
                   if snapshot["fanouts"][value[:2]] is not None and
                   (object_dir / value[:2] / value[2:]).is_file())
        fanout = object_dir / oid[:2]
        state = {"path": fanout, "snapshot_present": True,
                 "snapshot_identity": snapshot["fanouts"][oid[:2]],
                 "created_by_operation": False, "installed_identity": None,
                 "ambiguous": False}
        record = {"oid": oid, "path": fanout / oid[2:], "snapshot_present": True,
                  "created_by_operation": False, "installed_identity": None,
                  "fanout": state}
        self.assertEqual((True, "proved"), publication._post_cas_proof(
            self.repo.path, snapshot, ref, head, [], [record], closure))
        held = object_dir / (oid[:2] + "-snapshot-held")
        fanout.rename(held)
        shutil.copytree(held, fanout)
        try:
            proved, detail = publication._post_cas_proof(
                self.repo.path, snapshot, ref, head, [], [record], closure)
            self.assertFalse(proved)
            self.assertEqual("loose object fanout identity map changed", detail)
        finally:
            remove_synthetic_object_tree(fanout)
            held.rename(fanout)

    def test_ac44_post_cas_proof_rejects_created_fanout_and_object_replacement(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        ref = "refs/heads/awf/EX-6-publication"
        head = git(self.repo.path, "rev-parse", "HEAD")
        closure = publication._replacement_closure_evidence(
            self.repo.path, head, snapshot, [], None)
        payload = None
        oid = None
        for value in range(512):
            candidate = ("post-cas-created-object-" + str(value)).encode("ascii")
            candidate_oid = git(self.repo.path, "hash-object", "--stdin",
                                input_bytes=candidate)
            if snapshot["fanouts"][candidate_oid[:2]] is None:
                payload, oid = candidate, candidate_oid
                break
        self.assertIsNotNone(oid)
        self.assertEqual(oid, git(self.repo.path, "hash-object", "-w", "--stdin",
                                  input_bytes=payload))
        object_dir = Path(snapshot["object_dir"])
        fanout = object_dir / oid[:2]
        path = fanout / oid[2:]
        fanout_details = fanout.lstat()
        path_details = path.lstat()
        state = {"path": fanout, "snapshot_present": False, "snapshot_identity": None,
                 "created_by_operation": True,
                 "installed_identity": (fanout_details.st_dev, fanout_details.st_ino),
                 "ambiguous": False}
        record = {"oid": oid, "kind": "blob", "raw": payload,
                  "loose_bytes": path.read_bytes(), "path": path,
                  "snapshot_present": False, "created_by_operation": True,
                  "installed_identity": (path_details.st_dev, path_details.st_ino),
                  "fanout": state}
        self.assertEqual((True, "proved"), publication._post_cas_proof(
            self.repo.path, snapshot, ref, head, [], [record], closure))

        held_object = path.with_name(path.name + ".identity-held")
        path.rename(held_object)
        shutil.copy2(held_object, path)
        try:
            proved, detail = publication._post_cas_proof(
                self.repo.path, snapshot, ref, head, [], [record], closure)
            self.assertFalse(proved)
            self.assertEqual("installed loose object identity or content changed", detail)
        finally:
            path.chmod(stat.S_IWRITE)
            path.unlink()
            held_object.rename(path)

        held_fanout = object_dir / (oid[:2] + "-created-held")
        fanout.rename(held_fanout)
        shutil.copytree(held_fanout, fanout)
        try:
            proved, detail = publication._post_cas_proof(
                self.repo.path, snapshot, ref, head, [], [record], closure)
            self.assertFalse(proved)
            self.assertEqual("loose object fanout identity map changed", detail)
        finally:
            remove_synthetic_object_tree(fanout)
            remove_synthetic_object_tree(held_fanout)

    def test_ac44_post_cas_proof_rejects_in_place_corruption_of_snapshot_present_graph_objects(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        ref = "refs/heads/awf/EX-6-publication"
        head = git(self.repo.path, "rev-parse", "HEAD")
        closure = publication._replacement_closure_evidence(
            self.repo.path, head, snapshot, [], None)
        self.assertEqual({"blob", "tree", "commit"}, {item["kind"] for item in closure})
        with mock.patch.object(publication, "_rewrite_snapshot", return_value=snapshot):
            self.assertEqual((True, "proved"), publication._post_cas_proof(
                self.repo.path, snapshot, ref, head, [], [], closure))
            for kind in ("commit", "tree", "blob"):
                item = next(value for value in closure
                            if value["kind"] == kind and value["loose_identity"] is not None)
                path = Path(snapshot["object_dir"]) / item["oid"][:2] / item["oid"][2:]
                original = path.read_bytes()
                path.chmod(stat.S_IWRITE)
                corrupt = ("corrupt-snapshot-present-" + kind).encode("ascii")
                path.write_bytes(zlib.compress(
                    (kind + " " + str(len(corrupt))).encode("ascii") + b"\0" + corrupt))
                try:
                    proved, detail = publication._post_cas_proof(
                        self.repo.path, snapshot, ref, head, [], [], closure)
                    self.assertFalse(proved)
                    self.assertRegex(
                        detail, r"content or OID|changed|failed with exit|bad object|bogus commit|tree object")
                    self.assertEqual(item["loose_identity"],
                                     (path.lstat().st_dev, path.lstat().st_ino))
                finally:
                    path.write_bytes(original)
                    path.chmod(stat.S_IREAD)

    def test_ac44_shallow_boundary_is_rejected_before_rewrite_mutation(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        shallow = common / "shallow"
        shallow.write_text(self.repo.base + "\n", encoding="ascii")
        try:
            with self.assertRaisesRegex(
                    ValidationError, "Reachability-altering Git metadata.*shallow"):
                rewrite_unpublished(self.repo.path, self.repo.base,
                                    "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        finally:
            shallow.unlink()
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_grafts_are_rejected_before_rewrite_mutation(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        grafts = common / "info" / "grafts"
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text(self.repo.base + "\n", encoding="ascii")
        try:
            with self.assertRaisesRegex(
                    ValidationError, "Reachability-altering Git metadata.*grafts"):
                rewrite_unpublished(self.repo.path, self.repo.base,
                                    "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        finally:
            grafts.unlink()
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_post_cas_shallow_boundary_race_rolls_back_exact_ref(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        ref = "refs/heads/awf/EX-6-publication"
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        common = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
        shallow = common / "shallow"
        real_git = publication._git
        created = []

        def mutate_boundary_after_cas(root, *args, **kwargs):
            result = real_git(root, *args, **kwargs)
            if (args[:2] == ("update-ref", ref) and result.returncode == 0 and
                    args[2] != old_head and not created):
                created.append(args[2])
                shallow.write_text(args[2] + "\n", encoding="ascii")
            elif (args[:2] == ("update-ref", ref) and result.returncode == 0 and
                  args[2] == old_head and shallow.exists()):
                shallow.unlink()
            return result

        with mock.patch.object(publication, "_git", side_effect=mutate_boundary_after_cas):
            with self.assertRaisesRegex(
                    ValidationError,
                    r"POST_CAS_(?:PROOF_FAILED_RECOVERED|RECOVERY_REQUIRED):.*Reachability-altering Git metadata"):
                rewrite_unpublished(self.repo.path, self.repo.base,
                                    "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(1, len(created))
        self.assertFalse(shallow.exists())
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_snapshot_present_packed_graph_objects_get_bounded_content_oid_proof(self):
        git(self.repo.path, "gc", "--prune=now")
        snapshot = publication._rewrite_snapshot(self.repo.path)
        head = git(self.repo.path, "rev-parse", "HEAD")
        closure = publication._replacement_closure_evidence(
            self.repo.path, head, snapshot, [], None)
        self.assertEqual({"blob", "tree", "commit"}, {item["kind"] for item in closure})
        self.assertTrue(all(item["loose_identity"] is None for item in closure))
        self.assertEqual((True, "proved"),
                         publication._snapshot_closure_matches(
                             self.repo.path, snapshot, closure))

    def test_ac44_post_cas_rewrite_rolls_back_after_same_inode_graph_corruption(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        ref = "refs/heads/awf/EX-6-publication"
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        snapshot = publication._rewrite_snapshot(self.repo.path)
        closure = publication._replacement_closure_evidence(
            self.repo.path, old_head, snapshot, [], None)
        object_dir = Path(git(self.repo.path, "rev-parse", "--path-format=absolute",
                              "--git-path", "objects"))
        for kind in ("commit", "tree", "blob"):
            with self.subTest(kind=kind):
                item = next(value for value in closure
                            if value["kind"] == kind and value["loose_identity"] is not None and
                            (kind != "commit" or value["oid"] != old_head))
                path = object_dir / item["oid"][:2] / item["oid"][2:]
                original = path.read_bytes()
                real_git = publication._git
                corrupted = []

                def corrupt_after_cas(root, *args, **kwargs):
                    result = real_git(root, *args, **kwargs)
                    if (not corrupted and args[:2] == ("update-ref", ref) and
                            args[2] != old_head and result.returncode == 0):
                        before = path.lstat()
                        path.chmod(stat.S_IWRITE)
                        corrupt = ("corrupt-snapshot-present-" + kind +
                                   "-after-cas").encode("ascii")
                        path.write_bytes(zlib.compress(
                            (kind + " " + str(len(corrupt))).encode("ascii") +
                            b"\0" + corrupt))
                        after = path.lstat()
                        self.assertEqual((before.st_dev, before.st_ino),
                                         (after.st_dev, after.st_ino))
                        corrupted.append(True)
                    return result

                try:
                    with mock.patch.object(publication, "_git",
                                           side_effect=corrupt_after_cas):
                        with self.assertRaisesRegex(
                                ValidationError,
                                r"POST_CAS_RECOVERY_REQUIRED:"):
                            rewrite_unpublished(
                                self.repo.path, self.repo.base,
                                "awf/EX-6-publication", 1, message,
                                mapping_path=self.mapping)
                finally:
                    path.write_bytes(original)
                    path.chmod(stat.S_IREAD)
                self.assertEqual([True], corrupted)
                self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))

    def test_ac44_post_cas_proof_failure_rolls_back_ref_and_retains_owned_object(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        head = git(self.repo.path, "rev-parse", "HEAD")
        snapshot = publication._rewrite_snapshot(self.repo.path)
        with mock.patch.object(publication, "_post_cas_proof",
                               return_value=(False, "synthetic post-CAS race")):
            with self.assertRaisesRegex(ValidationError, "POST_CAS_RECOVERY_REQUIRED") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        created = re.search(r"new_head=([0-9a-f]+)", str(caught.exception)).group(1)
        self.assertEqual(head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertTrue(object_exists(self.repo.path, created))
        current = publication._rewrite_snapshot(self.repo.path)
        for key in snapshot:
            if key not in {"logs", "objects", "fanouts"}:
                self.assertEqual(snapshot[key], current[key])
        self.assertIn("Automatic reflog restoration is unavailable", str(caught.exception))

    def test_ac44_post_cas_snapshot_exception_uses_exact_rollback_and_recovery_evidence(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        real_snapshot = publication._rewrite_snapshot
        calls = []

        def fail_post_cas_snapshot(root):
            calls.append(root)
            if len(calls) == 2:
                raise PermissionError("synthetic post-CAS snapshot denial")
            return real_snapshot(root)

        with mock.patch.object(publication, "_rewrite_snapshot",
                               side_effect=fail_post_cas_snapshot):
            with self.assertRaisesRegex(
                    ValidationError,
                    r"POST_CAS_RECOVERY_REQUIRED:.*post-CAS proof exception PermissionError") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1,
                                    message, mapping_path=self.mapping)
        created = re.search(r"new_head=([0-9a-f]+)", str(caught.exception)).group(1)
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertTrue(object_exists(self.repo.path, created))

    def test_ac44_post_cas_ancestor_exception_uses_exact_rollback_and_recovery_evidence(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        real_is_ancestor = publication._is_ancestor

        def fail_after_cas(root, ancestor, descendant):
            current = real_git(root, "rev-parse", "refs/heads/awf/EX-6-publication",
                               check=False)
            if (current.returncode == 0 and
                    current.stdout.decode("ascii").strip() != old_head):
                raise ValidationError("synthetic post-CAS reachability denial")
            return real_is_ancestor(root, ancestor, descendant)

        with mock.patch.object(publication, "_is_ancestor", side_effect=fail_after_cas):
            with self.assertRaisesRegex(
                    ValidationError,
                    r"POST_CAS_RECOVERY_REQUIRED:.*post-CAS proof exception ValidationError") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1,
                                    message, mapping_path=self.mapping)
        created = re.search(r"new_head=([0-9a-f]+)", str(caught.exception)).group(1)
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertTrue(object_exists(self.repo.path, created))

    def test_ac44_post_cas_proof_exception_contention_never_overwrites_concurrent_ref(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        real_snapshot = publication._rewrite_snapshot
        real_git = publication._git
        calls = []

        def contend_then_fail(root):
            calls.append(root)
            if len(calls) == 2:
                real_git(root, "update-ref", "refs/heads/awf/EX-6-publication",
                         self.repo.base)
                raise PermissionError("synthetic post-CAS snapshot denial with contention")
            return real_snapshot(root)

        with mock.patch.object(publication, "_rewrite_snapshot", side_effect=contend_then_fail):
            with self.assertRaisesRegex(
                    ValidationError,
                    r"POST_CAS_RECOVERY_REQUIRED:.*exact-CAS rollback failed after post-CAS proof exception PermissionError") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1,
                                    message, mapping_path=self.mapping)
        created = re.search(r"new_head=([0-9a-f]+)", str(caught.exception)).group(1)
        self.assertEqual(self.repo.base,
                         git(self.repo.path, "rev-parse", "refs/heads/awf/EX-6-publication"))
        self.assertTrue(object_exists(self.repo.path, created))

    def test_ac44_post_cas_recovery_snapshot_exception_is_recovery_required(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        real_snapshot = publication._rewrite_snapshot
        calls = []

        def fail_proof_and_recovery_snapshots(root):
            calls.append(root)
            if len(calls) == 2:
                raise PermissionError("synthetic post-CAS snapshot denial")
            if len(calls) == 3:
                raise PermissionError("synthetic recovery snapshot denial")
            return real_snapshot(root)

        with mock.patch.object(publication, "_rewrite_snapshot",
                               side_effect=fail_proof_and_recovery_snapshots), \
                mock.patch.object(publication, "_restore_rewrite_reflogs", return_value=None):
            with self.assertRaisesRegex(
                    ValidationError,
                    r"POST_CAS_RECOVERY_REQUIRED:.*post-CAS proof exception PermissionError.*recovery exception PermissionError") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1,
                                    message, mapping_path=self.mapping)
        created = re.search(r"new_head=([0-9a-f]+)", str(caught.exception)).group(1)
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertTrue(object_exists(self.repo.path, created))

    def test_ac44_fetch_head_and_merge_head_parse_every_canonical_oid(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        first = git(self.repo.path, "rev-parse", "HEAD")
        second = git(self.repo.path, "rev-parse", self.repo.base)
        fetch = (f"{first}\t\tbranch 'one' of example\n"
                 f"{second}\tnot-for-merge\tbranch 'two' of example\n").encode("ascii")
        merge = (first + "\n" + second + "\n").encode("ascii")
        self.assertEqual((first, second),
                         publication._pseudoref_oids("FETCH_HEAD", fetch,
                                                    snapshot["object_format"]))
        self.assertEqual((first, second),
                         publication._pseudoref_oids("MERGE_HEAD", merge,
                                                    snapshot["object_format"]))

    def test_ac44_malformed_or_partially_parsed_multi_oid_pseudoref_fails_closed(self):
        object_format = publication._rewrite_snapshot(self.repo.path)["object_format"]
        first = git(self.repo.path, "rev-parse", "HEAD")
        invalid_values = [
            ("FETCH_HEAD", (first + "\tmissing-second-tab\n").encode("ascii")),
            ("FETCH_HEAD", (first + "\t\tvalid\n" + "z" * len(first) + "\t\tbad\n").encode("ascii")),
            ("MERGE_HEAD", (first + " extra\n").encode("ascii")),
            ("MERGE_HEAD", (first + "\r\n").encode("ascii")),
        ]
        for name, content in invalid_values:
            with self.subTest(name=name, content=content):
                with self.assertRaisesRegex(ValidationError, "Pseudoref"):
                    publication._pseudoref_oids(name, content, object_format)

    def test_ac44_post_cas_rollback_failure_preserves_recovery_evidence(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git

        def fail_rollback(root, *args, **kwargs):
            if (args and args[0] == "update-ref" and
                    args[1] == "refs/heads/awf/EX-6-publication" and args[2] == old_head):
                return subprocess.CompletedProcess(args, 1, b"", b"synthetic rollback failure")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_post_cas_proof",
                               return_value=(False, "synthetic post-CAS race")), \
                mock.patch.object(publication, "_git", side_effect=fail_rollback):
            with self.assertRaisesRegex(ValidationError,
                                        r"POST_CAS_RECOVERY_REQUIRED:.*old_head=.*new_head=.*retained_objects=.*non_destructive_recovery=") as caught:
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        created = re.search(r"new_head=([0-9a-f]+)", str(caught.exception)).group(1)
        self.assertEqual(created, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertTrue(object_exists(self.repo.path, created))

    def test_ac44_concurrent_ref_after_cas_is_detected_and_target_is_rolled_back(self):
        self.contaminate_then_remove()
        message = Path(self.temp.name) / "message.txt"
        message.write_text("clean squash\n", encoding="utf-8")
        old_head = git(self.repo.path, "rev-parse", "HEAD")
        real_git = publication._git
        created = []

        def add_ref_after_cas(root, *args, **kwargs):
            if (args and args[0] == "update-ref" and
                    args[1] == "refs/heads/awf/EX-6-publication" and args[2] != old_head):
                result = real_git(root, *args, **kwargs)
                created.append(args[2])
                real_git(root, "update-ref", "refs/tags/post-cas-race", old_head)
                return result
            return real_git(root, *args, **kwargs)

        with mock.patch.object(publication, "_git", side_effect=add_ref_after_cas):
            with self.assertRaisesRegex(ValidationError, "POST_CAS_RECOVERY_REQUIRED"):
                rewrite_unpublished(self.repo.path, self.repo.base, "awf/EX-6-publication", 1, message,
                                    mapping_path=self.mapping)
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "HEAD"))
        self.assertEqual(old_head, git(self.repo.path, "rev-parse", "refs/tags/post-cas-race"))
        self.assertTrue(object_exists(self.repo.path, created[0]))

    def test_ac44_fanout_alias_is_rejected_before_object_installation(self):
        snapshot = publication._rewrite_snapshot(self.repo.path)
        oid = "ab" + "0" * 38
        fanout = Path(snapshot["object_dir"]) / oid[:2]
        target = Path(self.temp.name) / "fanout-target"
        target.mkdir()
        made_alias = False
        if hasattr(fanout, "symlink_to"):
            try:
                fanout.symlink_to(target, target_is_directory=True)
                made_alias = True
            except OSError:
                pass
        if not made_alias and os.name == "nt":
            made_alias = subprocess.run(["cmd", "/c", "mklink", "/J", str(fanout), str(target)],
                                        capture_output=True, check=False).returncode == 0
        if not made_alias:
            self.skipTest("directory alias creation is unavailable on this host")
        with self.assertRaisesRegex(ValidationError, "fanout directory is aliased"):
            publication._loose_path(Path(snapshot["object_dir"]), oid, snapshot["object_format"])

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
