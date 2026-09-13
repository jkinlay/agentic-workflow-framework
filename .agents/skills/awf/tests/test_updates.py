"""Local identity and mocked-HTTPS update checks; never use an external network."""
from __future__ import annotations

import contextlib
import importlib.util
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_updates.py"
spec = importlib.util.spec_from_file_location("portable_update_check", SCRIPT)
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)
awf = updates.awf


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def announcement(version, manifest="a" * 64, archive=None):
    return {"version": version, "archive": archive or f"agentic-workflow-{version}.zip",
            "archive_sha256": "b" * 64, "manifest_sha256": manifest}


class Response:
    def __init__(self, url, raw):
        self.url = url
        self.raw = raw
        self.read_limits = []
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_limits.append(limit)
        data = self.raw[self.offset:self.offset + limit]
        self.offset += len(data)
        return data


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-updates-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.channel = self.root / "updates.json"
        self.write_channel([announcement("1.5.0")])

    def write_channel(self, releases):
        self.channel.write_bytes(encoded({"format": "awf-update-channel-1", "releases": releases}))

    def installed(self, version="1.4.1", manifest="c" * 64, install_id="fixture-install-id"):
        folder = self.project / ".agentic"
        folder.mkdir(exist_ok=True)
        receipt = {"template_version": version, "source_manifest_sha256": manifest, "install_id": install_id}
        provenance = {"template": {"version": version},
                      "installation": {"source_manifest_sha256": manifest, "install_id": install_id}}
        (self.project / awf.RECEIPT).write_bytes(encoded(receipt))
        (self.project / awf.VERSION_FILE).write_bytes(encoded(provenance))
        return receipt, provenance

    def run_check(self, version=None, channel=None, project=None):
        return updates.check_updates(project or self.project, channel or self.channel, version)

    def assert_rejected(self, code, callback=None):
        with self.assertRaises(awf.DiscoveryError) as raised:
            (callback or self.run_check)()
        self.assertEqual(code, raised.exception.code)
        value = updates.rejected(raised.exception)
        self.assertEqual("UPDATE_CHECK_REJECTED", value["status"])
        self.assertFalse(value["archive_verified"])
        self.assertIn("next_action", value)
        return raised.exception

    def test_no_channel_does_not_claim_latest_or_use_bundle_implicitly(self):
        self.installed("1.5.0", "a" * 64)
        with patch.object(updates, "_read_channel", side_effect=AssertionError("No fallback channel")):
            result = updates.check_updates(self.project)
        self.assertEqual("UPDATE_CHECK_NOT_CONFIGURED", result["status"])
        self.assertIsNone(result["channel"])
        self.assertIsNone(result["candidate"])
        self.assertIsNone(result["network_used"])
        self.assertFalse(result["direct_https_used"])
        self.assertIn("without claiming it is latest", result["next_action"]["action"])

    def test_missing_installation_reports_adoption_without_fetching_or_writing(self):
        absent = self.root / "new-project"
        before = snapshot(self.root)
        result = self.run_check(project=absent)
        self.assertEqual("ADOPTION_AVAILABLE", result["status"])
        self.assertEqual("NOT_INSTALLED", result["installed"]["status"])
        self.assertFalse(result["archive_fetched"])
        self.assertFalse(result["active_adoption"])
        self.assertFalse(Path(result["candidate"]["archive"]).exists())
        self.assertFalse(absent.exists())
        self.assertEqual(before, snapshot(self.root))

    def test_coherent_older_installation_reports_update_candidate_only(self):
        self.installed()
        before = snapshot(self.root)
        result = self.run_check()
        self.assertEqual("UPDATE_AVAILABLE", result["status"])
        self.assertEqual("OBSERVED_CONSISTENT", result["installed"]["status"])
        self.assertEqual("1.4.1", result["installed"]["version"])
        self.assertEqual("1.5.0", result["candidate"]["version"])
        for flag in ("archive_verified", "installed_authenticated", "active_adoption", "global_skill_installed", "adoption_pr_created", "release_code_executed"):
            self.assertFalse(result[flag])
        self.assertFalse(result["installed"]["immutable_files_verified"])
        self.assertIn("verify archive and manifest", result["next_action"]["action"])
        self.assertEqual(before, snapshot(self.root))

    def test_matching_version_and_manifest_requires_actual_verification(self):
        self.installed("1.5.0", "a" * 64)
        result = self.run_check()
        self.assertEqual("VERSION_MATCH_REQUIRES_VERIFICATION", result["status"])
        self.assertFalse(result["installed"]["authenticated"])
        self.assertFalse(result["archive_verified"])

    def test_same_version_different_manifest_requires_reconciliation(self):
        self.installed("1.5.0", "c" * 64)
        result = self.run_check()
        self.assertEqual("RECONCILIATION_REQUIRED", result["status"])
        self.assertIn("differing manifests", result["next_action"]["action"])

    def test_installed_ahead_never_downgrades(self):
        self.installed("1.6.0")
        result = self.run_check("1.5")
        self.assertEqual("NO_DOWNGRADE", result["status"])
        self.assertEqual("1.6.0", result["installed"]["version"])

    def test_highest_numeric_version_and_explicit_family_or_patch(self):
        self.write_channel([announcement("1.5.9"), announcement("1.6.0"), announcement("1.5.10"), announcement("1.5.0")])
        self.assertEqual("1.6.0", self.run_check()["candidate"]["version"])
        self.assertEqual("1.5.10", self.run_check("1.5")["candidate"]["version"])
        self.assertEqual("1.5.0", self.run_check("1.5.0")["candidate"]["version"])
        result = self.run_check("1.8")
        self.assertEqual("REQUESTED_VERSION_UNAVAILABLE", result["status"])
        self.assertIsNone(result["candidate"])
        self.assertEqual(["1.5.0", "1.5.9", "1.5.10", "1.6.0"], result["channel"]["available_versions"])

    def test_inconsistent_install_ids_versions_and_hashes_are_not_trusted(self):
        for field, value in (("install_id", "different-id"), ("source_manifest_sha256", "d" * 64), ("version", "1.3.0")):
            with self.subTest(field=field):
                _, provenance = self.installed()
                container = provenance["template"] if field == "version" else provenance["installation"]
                container[field] = value
                (self.project / awf.VERSION_FILE).write_bytes(encoded(provenance))
                self.assertEqual("RECONCILIATION_REQUIRED", self.run_check()["status"])

    def test_partial_unreceipted_or_interrupted_installation_requires_reconciliation(self):
        folder = self.project / ".agentic"
        folder.mkdir()
        (self.project / awf.CONFIG).write_bytes(b"{}")
        self.assertEqual("RECONCILIATION_REQUIRED", self.run_check()["status"])
        self.installed()
        (folder / "INSTALLING.json").write_bytes(b"{}")
        self.assertEqual("RECONCILIATION_REQUIRED", self.run_check()["status"])

    def test_malformed_and_duplicate_installed_json_is_inconsistent(self):
        self.installed()
        for raw in (b"not-json", b'{"template_version":"1.5.0","template_version":"1.4.1"}', b'{"x":NaN}'):
            (self.project / awf.RECEIPT).write_bytes(raw)
            self.assertEqual("RECONCILIATION_REQUIRED", self.run_check()["status"])

    def test_strict_channel_duplicates_nan_types_and_unknown_fields(self):
        cases = [b'{"format":"awf-update-channel-1","format":"duplicate","releases":[]}',
                 b'{"format":"awf-update-channel-1","releases":NaN}',
                 encoded({"format": "awf-update-channel-1", "releases": [announcement("1.5.0")], "extra": True})]
        for raw in cases:
            with self.subTest(raw=raw):
                self.channel.write_bytes(raw)
                with self.assertRaises(awf.DiscoveryError):
                    self.run_check()
        self.write_channel([announcement("1.5.0"), announcement("1.5.0", "d" * 64)])
        self.assert_rejected("INVALID_CHANNEL")

    def test_invalid_versions_selectors_and_digests_rejected(self):
        for version in ("1.05.0", "1.5", "1.5.0-beta", True, 1.5):
            self.write_channel([announcement(version)])
            self.assert_rejected("INVALID_VERSION")
        self.write_channel([announcement("1.5.0")])
        for selector in ("latest", "v1.5", "1", "1.05", "1.5.*"):
            self.assert_rejected("INVALID_VERSION_SELECTOR", lambda: self.run_check(selector))
        entry = announcement("1.5.0")
        for value in ("G" * 64, "a" * 63, True):
            entry["archive_sha256"] = value
            self.write_channel([entry])
            self.assert_rejected("INVALID_CHANNEL")

    def test_archive_traversal_urls_query_percent_and_windows_names_rejected(self):
        for reference in ("../release.zip", "/release.zip", "https://other.invalid/release.zip", "folder\\release.zip", "release.zip?q=x", "release.zip#x", "%2e%2e/release.zip", "CON.zip", "folder./release.zip"):
            with self.subTest(reference=reference):
                self.write_channel([announcement("1.5.0", archive=reference)])
                with self.assertRaises(awf.DiscoveryError):
                    self.run_check()

    def test_local_relative_archive_resolves_inside_channel_directory(self):
        self.write_channel([announcement("1.5.0", archive="releases/awf 1.5.zip")])
        result = self.run_check()
        self.assertEqual(str(self.root / "releases/awf 1.5.zip"), result["candidate"]["archive"])
        self.assertEqual(str(self.channel), result["channel"]["source"])
        self.assertEqual(awf.digest(self.channel.read_bytes()), result["channel"]["sha256"])

    def test_mock_https_fetches_only_channel_with_limit_timeout_and_no_auth(self):
        url = "https://example.invalid/awf/updates.json"
        raw = encoded({"format": "awf-update-channel-1", "releases": [announcement("1.5.0", archive="releases/awf 1.5.zip")]})
        response = Response(url, raw)
        opener = Opener(response)
        with patch.object(updates.urllib.request, "build_opener", return_value=opener) as build:
            result = self.run_check(channel=url)
        self.assertEqual("ADOPTION_AVAILABLE", result["status"])
        self.assertEqual("https://example.invalid/awf/releases/awf%201.5.zip", result["candidate"]["archive"])
        self.assertEqual(url, result["channel"]["source"])
        self.assertEqual(1, len(opener.calls))
        request, timeout = opener.calls[0]
        self.assertEqual(url, request.full_url)
        self.assertEqual(10, timeout)
        self.assertTrue(all(0 < limit <= updates.READ_CHUNK_BYTES for limit in response.read_limits))
        self.assertNotIn("Authorization", dict(request.header_items()))
        self.assertNotIn("Cookie", dict(request.header_items()))
        handlers = build.call_args.args
        self.assertEqual({}, next(item for item in handlers if isinstance(item, urllib.request.ProxyHandler)).proxies)

    def test_unsafe_https_or_nonhttps_source_rejected_before_network(self):
        sources = ["http://example.invalid/updates.json", "file:///tmp/updates.json", "https://user:secret@example.invalid/u.json", "https://example.invalid/u.json?token=secret", "https://example.invalid/u.json#x", "https://example.invalid/../u.json"]
        with patch.object(updates.urllib.request, "build_opener", side_effect=AssertionError("No network")):
            for source in sources:
                with self.subTest(source=source):
                    self.assert_rejected("UNSAFE_CHANNEL_URL", lambda: self.run_check(channel=source))

    def test_rejected_credential_or_query_url_is_redacted_from_error_proof(self):
        source = "https://user:SECRET@example.invalid/updates.json?token=SECRET#SECRET"
        exc = self.assert_rejected("UNSAFE_CHANNEL_URL", lambda: self.run_check(channel=source))
        rendered = json.dumps(updates.rejected(exc))
        self.assertNotIn("SECRET", rendered)
        self.assertEqual("https://example.invalid/updates.json", exc.proof["configured_channel_source"])

    def test_redirects_rejected_and_changed_response_source_never_accepted(self):
        url = "https://example.invalid/updates.json"
        request = urllib.request.Request(url)
        with self.assertRaises(urllib.error.HTTPError):
            updates._NoRedirect().redirect_request(request, None, 302, "redirect", {}, "http://other.invalid/update.json")
        opener = Opener(Response("https://other.invalid/updates.json", self.channel.read_bytes()))
        with patch.object(updates.urllib.request, "build_opener", return_value=opener):
            self.assert_rejected("CHANNEL_SOURCE_CHANGED", lambda: self.run_check(channel=url))

    def test_https_size_limit_and_timeout_fail_closed_without_writes(self):
        url = "https://example.invalid/updates.json"
        before = snapshot(self.root)
        for opener, code in [(Opener(Response(url, b"x" * (updates.MAX_CHANNEL_BYTES + 1))), "CHANNEL_TOO_LARGE"),
                             (Opener(error=TimeoutError("synthetic network timeout")), "UPDATE_CHANNEL_UNAVAILABLE")]:
            with self.subTest(code=code), patch.object(updates.urllib.request, "build_opener", return_value=opener):
                exc = self.assert_rejected(code, lambda: self.run_check(channel=url))
                self.assertEqual(url, exc.proof["configured_channel_source"])
                self.assertEqual(1, len(opener.calls))
                self.assertEqual(10, opener.calls[0][1])
                self.assertEqual(before, snapshot(self.root))

    def test_relative_project_path_and_hardlinked_channel_rejected(self):
        self.assert_rejected("UNSAFE_PATH", lambda: self.run_check(project=Path("relative-project")))
        os.link(self.channel, self.root / "channel-hardlink.json")
        self.assert_rejected("UNSAFE_PATH")

    def test_incomplete_http_reply_produces_structured_rejection(self):
        url = "https://example.invalid/updates.json"
        response = Response(url, b"")
        with patch.object(response, "read", side_effect=http.client.IncompleteRead(b"{", 10)), \
                patch.object(updates.urllib.request, "build_opener", return_value=Opener(response)):
            exc = self.assert_rejected("UPDATE_CHANNEL_UNAVAILABLE", lambda: self.run_check(channel=url))
        self.assertEqual(url, exc.proof["configured_channel_source"])
        self.assertIn("IncompleteRead", str(exc))

    def test_total_caller_deadline_rejects_late_worker_without_changing_result(self):
        url = "https://example.invalid/updates.json"
        response = Response(url, self.channel.read_bytes())
        release = threading.Event()
        completed = threading.Event()
        reads = []
        def blocked_read(limit):
            reads.append(limit)
            try:
                if not release.wait(3):
                    raise TimeoutError("test worker cleanup timeout")
                return response.raw
            finally:
                completed.set()
        start = time.monotonic()
        with patch.object(response, "read", side_effect=blocked_read), \
                patch.object(updates, "HTTPS_TIMEOUT_SECONDS", 0.05), \
                patch.object(updates.urllib.request, "build_opener", return_value=Opener(response)):
            try:
                exc = self.assert_rejected("CHANNEL_TIMEOUT", lambda: self.run_check(channel=url))
                result = updates.rejected(exc)
                before = json.dumps(result, sort_keys=True)
                self.assertLess(time.monotonic() - start, 1)
            finally:
                release.set()
            self.assertTrue(completed.wait(1))
        self.assertEqual(before, json.dumps(result, sort_keys=True))
        self.assertEqual(1, len(reads))
        self.assertIsNone(result["candidate"])
        self.assertFalse(result["archive_verified"])

    def test_cli_business_statuses_are_success_and_errors_structured(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = updates.main(["--project", str(self.project)])
        self.assertEqual(0, code)
        self.assertEqual("UPDATE_CHECK_NOT_CONFIGURED", json.loads(output.getvalue())["status"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = updates.main(["--project", str(self.project), "--channel", str(self.channel), "--version", "v1.5"])
        self.assertEqual(2, code)
        self.assertEqual("UPDATE_CHECK_REJECTED", json.loads(output.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
