"""AWF-34 combined coverage for committed, digest-bound baseline allowances."""
import hashlib
import json
import unittest

import test_awf154_large_file_allowances as fixtures


class ConfiguredAllowanceClosureTests(unittest.TestCase):
    # Reuse fixture operations without inheriting and rediscovering its tests.
    setUp = fixtures.LargeFileAllowanceTests.setUp
    write = fixtures.LargeFileAllowanceTests.write
    commit = fixtures.LargeFileAllowanceTests.commit
    write_config = fixtures.LargeFileAllowanceTests.write_config
    establish = fixtures.LargeFileAllowanceTests.establish
    scan = fixtures.LargeFileAllowanceTests.scan

    def assert_receipt_binding(self, result):
        self.assertEqual(self.base, result["base_sha"])
        self.assertEqual(fixtures.git(self.repo, "rev-parse", "HEAD"), result["head_sha"])
        self.assertEqual(hashlib.sha256(self.config_bytes).hexdigest(),
                         result["project_config_sha256"])
        self.assertFalse(result["execution_authority"])

    def assert_baseline_evidence(self, result, entries):
        observed = [item for item in result["findings"]
                    if item["source"] == "large-file-allowance"
                    and item["classification"] == "PRE_EXISTING"]
        expected = []
        for entry in entries:
            canonical = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected.append({
                "commit": self.base, "path": entry["path"], "line": None,
                "source": "large-file-allowance", "change": None,
                "detector_id": "allowance.large_file.sha256." + entry["sha256"],
                "classification": "PRE_EXISTING",
                "redacted_excerpt": "sha256:" + hashlib.sha256(canonical).hexdigest(),
            })
        self.assertEqual(sorted(expected, key=lambda item: item["path"]),
                         sorted(observed, key=lambda item: item["path"]))

    def configured_control(self):
        entry = fixtures.allowance()
        self.establish([entry], {fixtures.ARTIFACT: fixtures.LARGE_CONTENT})
        result = self.scan()
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual([], result["unscanned"])
        self.assertEqual(0, result["blocking_findings"])
        self.assert_receipt_binding(result)
        self.assert_baseline_evidence(result, [entry])
        return entry

    def test_multiple_committed_allowances_each_have_digest_bound_pre_existing_evidence(self):
        files = {
            fixtures.ARTIFACT: fixtures.LARGE_CONTENT,
            "artifacts/second.dat": b"b" * (fixtures.TEXT_LIMIT + 2048),
        }
        entries = [fixtures.allowance(path, content) for path, content in files.items()]
        self.establish(entries, files)

        result = self.scan()

        self.assertEqual("PASS", result["status"], result)
        self.assertEqual([], result["unscanned"])
        self.assertEqual(0, result["blocking_findings"])
        self.assertEqual(2, result["pre_existing_findings"])
        self.assert_receipt_binding(result)
        self.assert_baseline_evidence(result, entries)

    def test_configured_pass_then_new_oversize_file_blocks_even_with_identical_bytes(self):
        entry = self.configured_control()
        new_path = "artifacts/new.dat"
        self.write(new_path, fixtures.LARGE_CONTENT)
        added = self.commit("add another artifact")

        result = self.scan()

        self.assertEqual("BLOCKED", result["status"], result)
        self.assert_receipt_binding(result)
        self.assert_baseline_evidence(result, [entry])
        self.assertIn(added, result["commits_scanned"])
        self.assertEqual({(added, new_path, "patch", "oversize"),
                          (added, new_path, "current-file", "oversize")},
                         {(item["commit"], item["path"], item["source"], item["reason"])
                          for item in result["unscanned"]})
        self.assertFalse(any(item["path"] == new_path
                             and item["source"] == "large-file-allowance"
                             for item in result["findings"]))

    def test_restored_allowed_file_retains_intermediate_digest_mismatch_and_blocks(self):
        entry = self.configured_control()
        changed_bytes = b"c" * len(fixtures.LARGE_CONTENT)
        changed_digest = hashlib.sha256(changed_bytes).hexdigest()
        self.assertNotEqual(entry["sha256"], changed_digest)
        self.write(fixtures.ARTIFACT, changed_bytes)
        changed = self.commit("change baseline artifact")
        self.write(fixtures.ARTIFACT, fixtures.LARGE_CONTENT)
        restored = self.commit("restore baseline artifact")
        restored_bytes = (self.repo / fixtures.ARTIFACT).read_bytes()
        self.assertEqual(entry["sha256"], hashlib.sha256(restored_bytes).hexdigest())

        result = self.scan()

        self.assertEqual("BLOCKED", result["status"], result)
        self.assert_receipt_binding(result)
        self.assert_baseline_evidence(result, [entry])
        self.assertEqual([changed, restored], result["commits_scanned"][-2:])
        blocking = [item for item in result["findings"]
                    if item["classification"] == "BLOCKING"]
        self.assertEqual([{
            "commit": changed, "path": fixtures.ARTIFACT, "line": None,
            "source": "large-file-allowance", "change": None,
            "detector_id": "allowance.large_file.sha256." + entry["sha256"],
            "classification": "BLOCKING", "redacted_excerpt": "sha256:" + changed_digest,
        }], blocking)
        self.assertEqual({(changed, fixtures.ARTIFACT, "patch", "oversize"),
                          (restored, fixtures.ARTIFACT, "patch", "oversize")},
                         {(item["commit"], item["path"], item["source"], item["reason"])
                          for item in result["unscanned"]})


if __name__ == "__main__":
    unittest.main()
