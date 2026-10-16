import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


from agentic import ValidationError
from agentic import publication
from agentic.publication import render_scan, scan_repository


ROOT = Path(__file__).resolve().parents[2]
TEST_TEXT_LIMIT = 16 * 1024
TEST_LARGE_BYTES = 20 * 1024


def git(repository, *args, input_bytes=None):
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
        input=input_bytes, capture_output=True, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8", "replace").strip()


def allowance(path, content, **updates):
    value = {
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "reason": "Reviewed historical artifact",
        "approver": "fixture-owner",
    }
    value.update(updates)
    return value


class Repository:
    def __init__(self, parent):
        self.path = Path(parent) / "repository"
        self.path.mkdir()
        git(self.path, "init", "-b", "main")
        git(self.path, "config", "user.name", "Synthetic User")
        git(self.path, "config", "user.email", "synthetic@example.invalid")
        self.write("seed.txt", b"seed\n")
        self.commit("seed", "seed.txt")

    def write(self, relative, data):
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, message, *paths):
        git(self.path, "add", "--", *paths)
        git(self.path, "commit", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def config(self, allowances=(), **publication_updates):
        value = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
        publication_value = {
            "deny_literals": [], "deny_regexes": [], "internal_hostnames": [],
            "large_file_allowances": list(allowances),
        }
        publication_value.update(publication_updates)
        value["publication"] = publication_value
        self.write(".agentic/PROJECT_CONFIG.yaml", json.dumps(value, indent=2).encode("utf-8"))

    def establish_base(self, allowances, files):
        self.config(allowances)
        paths = [".agentic/PROJECT_CONFIG.yaml"]
        for path, content in files.items():
            self.write(path, content)
            paths.append(path)
        self.base = self.commit("establish reviewed base", *paths)
        git(self.path, "switch", "-c", "awf/EX-34-large-files")
        self.write("feature.txt", b"feature\n")
        self.commit("feature", "feature.txt")


class PublicationConfigIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-publication-config-")
        self.repo = Repository(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_bounded_head_config_load_rejects_malformed_or_invalid_whole_config(self):
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-c", "awf/EX-34-large-files")
        cases = (b"publication: [unterminated", b"{}\n")
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                self.repo.write(".agentic/PROJECT_CONFIG.yaml", payload)
                self.repo.commit(f"invalid config {index}", ".agentic/PROJECT_CONFIG.yaml")
                with self.assertRaises(ValidationError):
                    scan_repository(self.repo.path, base, "HEAD")
        self.repo.config([])
        self.repo.commit("oversize config fixture", ".agentic/PROJECT_CONFIG.yaml")
        with mock.patch.object(publication, "MAX_DOCUMENT_BYTES", 64), \
                self.assertRaises(ValidationError):
            scan_repository(self.repo.path, base, "HEAD")

    def test_head_config_is_only_allowance_authority_and_real_scan_uses_it(self):
        content = b"x" * TEST_LARGE_BYTES
        entry = allowance("artifacts/existing.dat", content)
        self.repo.establish_base([entry], {entry["path"]: content})
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
            result = scan_repository(self.repo.path, self.repo.base, "HEAD")
        self.assertEqual("PASS", result["status"], render_scan(result))
        raw_config = subprocess.run(
            ["git", "-C", str(self.repo.path), "show", "HEAD:.agentic/PROJECT_CONFIG.yaml"],
            capture_output=True, check=True).stdout
        self.assertEqual(hashlib.sha256(raw_config).hexdigest(), result["project_config_sha256"])
        self.assertRegex(result["project_config_binding_sha256"], r"^[0-9a-f]{64}$")

    def test_supplemental_config_adds_detectors_but_never_allowances(self):
        content = b"x" * TEST_LARGE_BYTES
        entry = allowance("artifacts/existing.dat", content)
        self.repo.establish_base([], {entry["path"]: content})
        supplemental = Path(self.temp.name) / "supplemental.json"
        value = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
        value["publication"] = {
            "deny_literals": ["supplemental-only-token"], "deny_regexes": [],
            "internal_hostnames": [], "large_file_allowances": [entry],
        }
        supplemental.write_text(json.dumps(value), encoding="utf-8")
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
            result = scan_repository(
                self.repo.path, self.repo.base, "HEAD", config_path=supplemental,
                pr_body_texts=["supplemental-only-token"],
            )
        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(any(item["detector_id"] == "declared.literal.1"
                            for item in result["findings"]))
        self.assertRegex(result["supplemental_project_config_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(any(item["source"] == "large-file-allowance"
                             and item["classification"] == "PRE_EXISTING"
                             for item in result["findings"]))

    def test_direct_argument_omission_empty_equal_reordered_and_mutated(self):
        first_content = b"a" * TEST_LARGE_BYTES
        second_content = b"b" * TEST_LARGE_BYTES
        first = allowance("artifacts/first.dat", first_content)
        second = allowance("artifacts/second.dat", second_content)
        self.repo.establish_base(
            [first, second], {first["path"]: first_content, second["path"]: second_content}
        )
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
            self.assertEqual("PASS", scan_repository(
                self.repo.path, self.repo.base, "HEAD", large_file_allowances=None
            )["status"])
            self.assertEqual("PASS", scan_repository(
                self.repo.path, self.repo.base, "HEAD", large_file_allowances=[first, second]
            )["status"])
            for supplied in ([], [second, first], [{**first, "reason": "mutated"}, second]):
                with self.subTest(supplied=supplied), self.assertRaises(ValidationError):
                    scan_repository(self.repo.path, self.repo.base, "HEAD",
                                    large_file_allowances=supplied)

    def test_allowed_base_blob_passes_with_redacted_complete_accounting(self):
        content = b"x" * TEST_LARGE_BYTES
        entry = allowance("artifacts/existing.dat", content)
        self.repo.establish_base([entry], {entry["path"]: content})
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
            result = scan_repository(self.repo.path, self.repo.base, "HEAD")
        self.assertEqual("PASS", result["status"], render_scan(result))
        self.assertNotIn(entry["reason"], json.dumps(result))
        self.assertNotIn(entry["approver"], json.dumps(result))
        accounting = result["large_file_allowance_accounting"]
        self.assertEqual(1, len(accounting))
        self.assertEqual("MATCHED", accounting[0]["base_disposition"])
        self.assertEqual([], accounting[0]["candidate_events"])
        self.assertEqual(entry["sha256"], accounting[0]["configured_sha256"])
        self.assertEqual(hashlib.sha256(entry["path"].encode()).hexdigest(),
                         accounting[0]["path_sha256"])

    def test_wrong_hash_new_move_change_small_delete_and_revert_block_with_accounting(self):
        content = b"x" * TEST_LARGE_BYTES
        entry = allowance("artifacts/existing.dat", content)
        self.repo.establish_base([entry], {entry["path"]: content})
        self.repo.write(entry["path"], b"y" * TEST_LARGE_BYTES)
        self.repo.commit("change allowed artifact", entry["path"])
        self.repo.write(entry["path"], content)
        self.repo.commit("revert allowed artifact", entry["path"])
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
            result = scan_repository(self.repo.path, self.repo.base, "HEAD")
        self.assertEqual("BLOCKED", result["status"])
        events = result["large_file_allowance_accounting"][0]["candidate_events"]
        self.assertEqual(["CHANGED", "REVERTED_AFTER_CHANGE"],
                         [event["event"] for event in events])

        wrong = copy.deepcopy(entry)
        wrong["sha256"] = "0" * 64
        git(self.repo.path, "switch", "main")
        self.repo.config([wrong])
        self.repo.commit("change reviewed allowance", ".agentic/PROJECT_CONFIG.yaml")
        base = git(self.repo.path, "rev-parse", "HEAD")
        git(self.repo.path, "switch", "-C", "wrong-hash")
        self.repo.write("feature-2.txt", b"feature\n")
        self.repo.commit("feature 2", "feature-2.txt")
        with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
            result = scan_repository(self.repo.path, base, "HEAD")
        self.assertEqual("HASH_MISMATCH",
                         result["large_file_allowance_accounting"][0]["base_disposition"])

        scenarios = {
            "new": ("PATH_MISSING", "CANDIDATE_ONLY"),
            "move": ("MATCHED", "MOVED"),
            "small": ("NOT_OVERSIZE", None),
            "delete": ("MATCHED", "DELETED"),
        }
        for name, (disposition, event) in scenarios.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                    prefix=f"awf-publication-{name}-", dir=self.temp.name) as parent:
                repo = Repository(parent)
                scenario_content = b"s" * 10 if name == "small" else content
                scenario_entry = allowance("artifacts/scenario.dat", scenario_content)
                files = {} if name == "new" else {scenario_entry["path"]: scenario_content}
                repo.establish_base([scenario_entry], files)
                if name == "new":
                    repo.write(scenario_entry["path"], scenario_content)
                    repo.commit("add candidate-only artifact", scenario_entry["path"])
                elif name == "move":
                    git(repo.path, "mv", scenario_entry["path"], "artifacts/moved.dat")
                    repo.commit("move allowed artifact", "artifacts/moved.dat")
                elif name == "delete":
                    git(repo.path, "rm", "--", scenario_entry["path"])
                    git(repo.path, "commit", "-m", "delete allowed artifact")
                with mock.patch.object(publication, "MAX_TEXT_BYTES", TEST_TEXT_LIMIT):
                    scenario_result = scan_repository(repo.path, repo.base, "HEAD")
                scenario_accounting = scenario_result["large_file_allowance_accounting"][0]
                self.assertEqual("BLOCKED", scenario_result["status"])
                self.assertEqual(disposition, scenario_accounting["base_disposition"])
                self.assertEqual(
                    [] if event is None else [event],
                    [item["event"] for item in scenario_accounting["candidate_events"]],
                )

    def test_oid_cache_and_file_total_deadline_failure_cleanup(self):
        budget = publication._AllowanceHashBudget(
            per_file_limit=100, total_limit=100, deadline_seconds=60,
            clock=lambda: 0,
        )
        with mock.patch.object(publication, "_blob_size", return_value=80), \
                mock.patch.object(publication, "_blob_sha256", return_value="1" * 64) as hasher:
            self.assertEqual(("1" * 64, "MATCHED"), budget.hash_blob(".", "a" * 40))
            self.assertEqual(("1" * 64, "MATCHED"), budget.hash_blob(".", "a" * 40))
            self.assertEqual(1, hasher.call_count)
            self.assertEqual((None, "TOTAL_LIMIT"), budget.hash_blob(".", "b" * 40))

        expired = publication._AllowanceHashBudget(
            per_file_limit=100, total_limit=200, deadline_seconds=1,
            clock=iter((0, 2)).__next__,
        )
        with mock.patch.object(publication, "_blob_size", return_value=80):
            self.assertEqual((None, "DEADLINE"), expired.hash_blob(".", "c" * 40))

        per_file = publication._AllowanceHashBudget(
            per_file_limit=79, total_limit=200, deadline_seconds=60, clock=lambda: 0)
        with mock.patch.object(publication, "_blob_size", return_value=80):
            self.assertEqual((None, "PER_FILE_LIMIT"), per_file.hash_blob(".", "d" * 40))

        mismatch = publication._AllowanceHashBudget(
            per_file_limit=100, total_limit=200, deadline_seconds=60, clock=lambda: 0)
        with mock.patch.object(publication, "_blob_size", side_effect=(80, 81)), \
                mock.patch.object(publication, "_blob_sha256", return_value="2" * 64):
            self.assertEqual(("2" * 64, "MATCHED"), mismatch.hash_blob(".", "e" * 40))
            self.assertEqual((None, "OID_SIZE_MISMATCH"), mismatch.hash_blob(".", "e" * 40))

        failed = publication._AllowanceHashBudget(
            per_file_limit=100, total_limit=200, deadline_seconds=60, clock=lambda: 0)
        with mock.patch.object(publication, "_blob_size", return_value=80), \
                mock.patch.object(publication, "_blob_sha256",
                                  side_effect=ValidationError("synthetic child failure")):
            self.assertEqual((None, "HASH_FAILED"), failed.hash_blob(".", "f" * 40))

        class TimedOutProcess:
            def __init__(self):
                self.stdout = io.BytesIO(b"z" * 80)
                self.stderr = io.BytesIO()
                self.returncode = None
                self.killed = False

            def wait(self, timeout=None):
                if not self.killed:
                    raise subprocess.TimeoutExpired("git", timeout)
                self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True

        child = TimedOutProcess()
        with mock.patch.object(publication, "_blob_size", return_value=80), \
                mock.patch.object(publication.shutil, "which", return_value="git"), \
                mock.patch.object(publication.subprocess, "Popen", return_value=child), \
                self.assertRaisesRegex(ValidationError, "did not complete"):
            publication._blob_sha256(".", "a" * 40, expected_size=80, timeout=0.001)
        self.assertTrue(child.killed)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)


if __name__ == "__main__":
    unittest.main()
