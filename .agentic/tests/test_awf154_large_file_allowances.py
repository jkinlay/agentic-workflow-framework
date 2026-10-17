"""AWF-154 red contract: configured baseline allowances and config evidence.

Negative scan cases include a positive control: rejecting every configured
allowance must not satisfy a test for selective, digest-bound refusal.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

ROOT = Path(__file__).resolve().parents[2]
TEXT_LIMIT = 16 * 1024
LARGE_CONTENT = b"a" * (TEXT_LIMIT + 1024)
ARTIFACT = "artifacts/baseline.dat"


def allowance(path=ARTIFACT, content=LARGE_CONTENT):
    return {
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "reason": "Reviewed baseline artifact",
        "approver": "fixture-owner",
    }


def project_config(entries=None):
    config = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
    config["publication"] = {
        "deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
    }
    if entries is not None:
        config["publication"]["large_file_allowances"] = entries
    return config


def git(root, *args):
    # Per-command fixture identity; never edit repository or global Git config.
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=" + os.devnull, "-c", "user.name=Fixture User",
         "-c", "user.email=fixture@example.invalid", "-C", str(root), *args],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise AssertionError("Fixture Git failed: " + result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8").strip()


class LargeFileAllowanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="awf154-")
        self.addCleanup(temporary.cleanup)
        self.external = Path(temporary.name)
        self.repo = self.external / "repository"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def commit(self, message):
        git(self.repo, "add", "--all")
        git(self.repo, "commit", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def write_config(self, entries):
        raw = (json.dumps(project_config(entries), indent=2) + "\n").encode("utf-8")
        self.write(".agentic/PROJECT_CONFIG.yaml", raw)
        return raw

    def establish(self, entries, files=None):
        self.config_bytes = self.write_config(entries)
        for path, content in (files or {}).items():
            self.write(path, content)
        self.base = self.commit("reviewed baseline")
        self.write("feature.txt", b"safe feature\n")
        self.commit("safe feature")

    def scan(self, **kwargs):
        from agentic import ValidationError, publication

        # Patch only the size threshold, leaving Git traversal, hashing,
        # configuration parsing and scan decisions real.
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEXT_LIMIT):
            try:
                return publication.scan_repository(self.repo, self.base, "HEAD", **kwargs)
            except ValidationError as exc:
                self.fail("Configured allowance scan must be evaluable: " + str(exc))

    def assert_configured_control_passes(self):
        self.establish([allowance()], {ARTIFACT: LARGE_CONTENT})
        result = self.scan()
        self.assertEqual("PASS", result["status"], result)

    def test_exact_tracked_oversize_allowance_passes_and_binds_config_digest(self):
        self.assert_configured_control_passes()
        result = self.scan()
        self.assertEqual(hashlib.sha256(self.config_bytes).hexdigest(),
                         result["project_config_sha256"])
        self.assertEqual([], result["unscanned"])
        self.assertTrue(any(item["path"] == ARTIFACT
                            and item["source"] == "large-file-allowance"
                            and item["classification"] == "PRE_EXISTING"
                            for item in result["findings"]))

    def test_exact_allowance_does_not_exempt_another_path_with_identical_bytes(self):
        other = "artifacts/other.dat"
        self.establish([allowance()], {ARTIFACT: LARGE_CONTENT, other: LARGE_CONTENT})
        result = self.scan()
        self.assertEqual("BLOCKED", result["status"])
        oversize_paths = {item["path"] for item in result["unscanned"]
                          if item["reason"] == "oversize"}
        self.assertIn(other, oversize_paths)
        self.assertNotIn(ARTIFACT, oversize_paths)
        self.assertTrue(any(item["path"] == ARTIFACT
                            and item["source"] == "large-file-allowance"
                            and item["classification"] == "PRE_EXISTING"
                            for item in result["findings"]))

    def test_missing_configured_allowance_refuses_oversize_baseline(self):
        self.assert_configured_control_passes()
        self.write_config([])
        self.commit("remove allowance")
        result = self.scan()
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item["path"] == ARTIFACT and item["reason"] == "oversize"
                            for item in result["unscanned"]))
        self.assertFalse(any(item["source"] == "large-file-allowance"
                             and item["classification"] == "PRE_EXISTING"
                             for item in result["findings"]))

    def test_stale_configured_digest_refuses_oversize_baseline(self):
        self.assert_configured_control_passes()
        stale = {**allowance(), "sha256": hashlib.sha256(b"older content").hexdigest()}
        self.write_config([stale])
        self.commit("stale allowance")
        result = self.scan()
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(any(item["source"] == "large-file-allowance"
                             and item["classification"] == "PRE_EXISTING"
                             for item in result["findings"]))

    def assert_invalid_path_refused(self, path):
        from agentic import ValidationError, publication

        self.assert_configured_control_passes()
        self.write_config([{**allowance(), "path": path}])
        self.commit("invalid allowance scope")
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEXT_LIMIT):
            with self.assertRaises(ValidationError):
                publication.scan_repository(self.repo, self.base, "HEAD")

    def test_wildcard_configured_allowance_is_refused(self):
        self.assert_invalid_path_refused("artifacts/*.dat")

    def test_directory_wide_configured_allowance_is_refused(self):
        self.assert_invalid_path_refused("artifacts/")

    def test_allowance_scan_binds_head_config_and_supplemental_config_separately(self):
        self.establish([allowance()], {ARTIFACT: LARGE_CONTENT})
        supplemental = self.external / "supplemental.json"
        # Supplemental allowances cannot replace the tracked allowance authority,
        # but their supplied bytes must still be accounted for in the receipt.
        raw = (json.dumps(project_config([allowance("artifacts/supplemental.dat")]))
               + "\n").encode("utf-8")
        supplemental.write_bytes(raw)
        result = self.scan(config_path=supplemental)
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual(hashlib.sha256(self.config_bytes).hexdigest(),
                         result["project_config_sha256"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         result["supplemental_project_config_sha256"])

    def test_result_records_raw_supplemental_project_config_sha256_and_changes_with_bytes(self):
        # Detector-only config reaches the absent evidence field on main,
        # independently of the missing allowance-config support.
        self.establish(None)
        supplemental = self.external / "supplemental.json"
        raw = (json.dumps(project_config(), indent=2) + "\n").encode("utf-8")
        supplemental.write_bytes(raw)
        first = self.scan(config_path=supplemental)
        self.assertEqual("PASS", first["status"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         first["supplemental_project_config_sha256"])
        updated = raw + b"\n"
        supplemental.write_bytes(updated)
        second = self.scan(config_path=supplemental)
        self.assertEqual("PASS", second["status"])
        self.assertEqual(hashlib.sha256(updated).hexdigest(),
                         second["supplemental_project_config_sha256"])
        self.assertNotEqual(first["supplemental_project_config_sha256"],
                            second["supplemental_project_config_sha256"])

    def test_configuration_validation_accepts_exact_allowance_and_rejects_malformed_entries(self):
        from agentic import ValidationError, configuration
        from agentic.contracts import Contracts

        # Resolve the planned member inside the test, outside assertRaises:
        # its absence is a red result, never an import failure or a refusal pass.
        validate = configuration.validate_publication_project_config
        contracts = Contracts(ROOT / ".agentic/schemas")
        validate(project_config([allowance()]), contracts)
        invalid = {
            "not_a_list": {},
            "non_object_entry": ["artifact"],
            "missing_digest": [{key: value for key, value in allowance().items()
                                if key != "sha256"}],
            "malformed_digest": [{**allowance(), "sha256": "invalid"}],
            "wildcard": [{**allowance(), "path": "artifacts/*.dat"}],
            "directory": [{**allowance(), "path": "artifacts/"}],
            "traversal": [{**allowance(), "path": "../outside.dat"}],
            "absolute": [{**allowance(), "path": "/artifacts/baseline.dat"}],
            "empty_reason": [{**allowance(), "reason": ""}],
            "empty_approver": [{**allowance(), "approver": ""}],
            "unknown_field": [{**allowance(), "extra": "unsupported"}],
            "duplicate_path": [allowance(), allowance()],
        }
        for name, entries in invalid.items():
            with self.subTest(case=name):
                config = project_config(copy.deepcopy(entries))
                with self.assertRaises(ValidationError):
                    validate(config, contracts)


if __name__ == "__main__":
    unittest.main()
