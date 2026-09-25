import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))

from agentic import VERSION, ValidationError  # noqa: E402
from agentic.canonical import sha256  # noqa: E402
from agentic.canonical import load_yaml  # noqa: E402
from agentic.installer import (CONFIG, INSTALLED, KNOWN_VERSIONS, PROVENANCE,  # noqa: E402
    install, managed, verify_installed)
from agentic.contracts import Contracts  # noqa: E402
from agentic.upgrade import load_known_versions, state_schema  # noqa: E402
from upgrade_fixtures import (BLOB_ROOT, FIXTURE_ROOT, NEXT, advance_one_fixture_step,  # noqa: E402
    file_tree, fixture_blob, fixture_index, fixture_manifest, managed_tree_from_receipt,
    materialize, verify_materialized)

VERSIONS = ("1.8.3", "1.8.9", "1.9.1", "1.9.2")
FIXED_UUID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def subtree(root, prefix):
    return {path: digest for path, digest in file_tree(root).items() if path.startswith(prefix)}


def optional_bytes(path):
    return path.read_bytes() if path.is_file() else None


def target_immutable():
    source = json.loads((ROOT / "MANIFEST.json").read_bytes())
    excluded = {CONFIG, PROVENANCE, ".github/CODEOWNERS"}
    return {path: digest for path, digest in source["files"].items()
            if managed(path) and path not in excluded}


@unittest.skipUnless((FIXTURE_ROOT / "versions.json").is_file(),
                     "historical upgrade fixtures are intentionally excluded from portable releases")
class UpgradeMatrixTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.gettempdir()) / ("awf-upgrade-matrix-" + uuid.uuid4().hex)
        self.base.mkdir()
        self.addCleanup(self.cleanup_base)
        self.fixture_number = 0
        self.pin = sha256((ROOT / "MANIFEST.json").read_bytes())

    def cleanup_base(self):
        if not self.base.exists():
            return
        for path in self.base.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.base)

    def fixture(self, version):
        self.fixture_number += 1
        destination = self.base / ("fixture-" + version.replace(".", "-") + "-" + str(self.fixture_number))
        evidence = materialize(version, destination)
        verify_materialized(version, destination)
        return destination, evidence

    def upgrade(self, destination, **kwargs):
        return install(ROOT, destination, self.pin, mode="upgrade", configure=True, discover=False, **kwargs)

    def deterministic_upgrade(self, destination):
        with patch("uuid.uuid4", return_value=FIXED_UUID), \
                patch("agentic.installer.now_text", return_value="2026-01-02T00:00:00Z"), \
                patch("agentic.operating.now_text", return_value="2026-01-02T00:00:00Z"):
            return self.upgrade(destination)

    def test_fixture_storage_is_exact_deduplicated_and_under_five_mb(self):
        index = fixture_index()
        self.assertEqual(tuple(index["versions"]), VERSIONS)
        fixture_bytes = sum(path.stat().st_size for path in FIXTURE_ROOT.rglob("*") if path.is_file())
        self.assertLess(fixture_bytes, 5_000_000)
        self.assertEqual(index["storage"]["kind"], "plain-sha256-blobs")
        self.assertEqual(index["storage"]["blob_count"], len(list(BLOB_ROOT.iterdir())))
        referenced = set()
        for version in VERSIONS:
            manifest = fixture_manifest(version)
            self.assertEqual(manifest["managed_file_count"], len(manifest["managed_files"]))
            receipt = json.loads(fixture_blob(manifest["receipt"]["sha256"]))
            self.assertEqual(receipt["immutable_files"],
                             {path: entry["sha256"] for path, entry in manifest["managed_files"].items()})
            for section in ("managed_files", "owner_files", "state_files"):
                for entry in manifest[section].values():
                    self.assertIn(entry["mode"], {"100644", "100755"})
                    self.assertEqual(sha256(fixture_blob(entry["sha256"])), entry["sha256"])
                    referenced.add(entry["sha256"])
            if manifest["source_manifest_blob"]:
                self.assertEqual(sha256(fixture_blob(manifest["source_manifest_blob"])),
                                 manifest["source_manifest_sha256"])
                referenced.add(manifest["source_manifest_blob"])
        self.assertEqual(referenced, {path.name for path in BLOB_ROOT.iterdir()})
        self.assertEqual(fixture_manifest("1.8.3")["managed_files"]["AGENTS.md"]["sha256"],
                         "c8e353add76d17bb6967e99f0f64540d6db47b10bfc53c058b6af60a8223fc8b")

    def test_fixture_blobs_are_stored_byte_exactly_by_git(self):
        blobs = sorted(path for path in BLOB_ROOT.iterdir() if path.is_file())
        relatives = [path.relative_to(ROOT).as_posix() for path in blobs]
        attributes = subprocess.run(
            ["git", "check-attr", "-z", "--stdin", "text"], cwd=ROOT,
            input=b"".join(relative.encode() + b"\0" for relative in relatives),
            capture_output=True, timeout=60)
        self.assertEqual(attributes.returncode, 0, attributes.stderr.decode(errors="replace"))
        fields = attributes.stdout.rstrip(b"\0").split(b"\0")
        self.assertEqual(list(zip(fields[::3], fields[1::3], fields[2::3])),
                         [(relative.encode(), b"text", b"unset") for relative in relatives])
        self.assertEqual({path.name for path in blobs},
                         {sha256(path.read_bytes()) for path in blobs})

    def test_materializer_uses_owner_values_comments_and_both_line_endings(self):
        endings = set()
        for version in VERSIONS:
            with self.subTest(version=version):
                destination, evidence = self.fixture(version)
                config = evidence["configuration"]
                self.assertIn(b"sample-owner/upgrade-fixture", config)
                self.assertIn(b"owner choices below must survive byte-for-byte", config)
                self.assertIn(b"@fixture-owner", evidence["codeowners"])
                self.assertIn(b"fixture-owner policy", evidence["instructions"])
                endings.add("CRLF" if b"\r\n" in config else "LF")
                self.assertEqual(fixture_manifest(version)["line_endings"],
                                 "CRLF" if b"\r\n" in config else "LF")
                if os.environ.get("AWF_TEST_TEMP_SHIM") == "1":
                    self.assertIn((ROOT / ".tmp-tests").resolve(), destination.resolve().parents)
                else:
                    self.assertNotIn(ROOT.resolve(), destination.resolve().parents)
        self.assertEqual(endings, {"CRLF", "LF"})

    def test_legacy_identification_ignores_installation_level_identifiers(self):
        destination, _ = self.fixture("1.8.3")
        receipt_path = destination / INSTALLED
        receipt = json.loads(receipt_path.read_bytes())
        receipt["install_id"] = "33333333-3333-4333-8333-333333333333"
        receipt["initial_config_sha256"] = "4" * 64
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        provenance_path = destination / PROVENANCE
        provenance = json.loads(provenance_path.read_bytes())
        provenance["installation"]["install_id"] = receipt["install_id"]
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

        plan = self.upgrade(destination, dry_run=True)

        self.assertEqual(plan["upgrade"]["detected_version"], "1.8.3")

    def test_direct_real_fixture_matrix_preserves_owner_bytes_and_verifies(self):
        expected_managed = target_immutable()
        for version in VERSIONS:
            with self.subTest(version=version):
                destination, before = self.fixture(version)
                result = self.upgrade(destination)
                after = (destination / CONFIG).read_bytes()
                expected_config = before["configuration"].replace(version.encode(), VERSION.encode(), 1)
                if version == "1.8.3":
                    before_value, after_value = load_yaml(before["configuration"]), load_yaml(after)
                    expected_owner = json.loads(json.dumps(before_value))
                    expected_owner["template"]["expected_workflow_version"] = VERSION
                    routing = after_value["execution"].pop("model_routing")
                    self.assertEqual(after_value, expected_owner)
                    self.assertEqual(routing["role_defaults"]["worker"]["model"],
                                     before_value["execution"]["roles"]["worker"]["model"])
                else:
                    self.assertEqual(after, expected_config)
                self.assertEqual(result["upgrade"]["detected_version"], version)
                self.assertEqual(bool(result["upgrade"]["new_required_settings"]), version == "1.8.3")
                for relative, raw in before["owner_files"].items():
                    if relative in {CONFIG, INSTALLED, PROVENANCE}:
                        continue
                    actual = (destination / Path(relative)).read_bytes()
                    if relative == ".gitignore":
                        self.assertTrue(actual.startswith(raw), relative)
                        self.assertEqual(actual[len(raw):].decode().splitlines(),
                                         result["gitignore"]["added_lines"])
                    else:
                        self.assertEqual(actual, raw, relative)
                self.assertEqual(managed_tree_from_receipt(destination), expected_managed)
                self.assertEqual(verify_installed(destination), self.pin)
                status = subprocess.run(
                    [sys.executable, "-B", "-I", str(destination / ".agentic/scripts/workflow.py"), "status", "--json"],
                    cwd=destination, capture_output=True, text=True, timeout=60)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertIn(json.loads(status.stdout)["project_state"], {"INSTALLED", "CONFIGURED"})
                for relative, raw in before["state"].items():
                    actual_state = (destination / relative).read_bytes()
                    schema = state_schema(relative, raw)
                    if schema in {"critic-review:3-pre-closure", "controller-event:3-pre-digest"}:
                        expected_schema = ("critic-review:3" if schema.startswith("critic-review")
                                           else "controller-event:3")
                        self.assertEqual(state_schema(relative, actual_state), expected_schema)
                    else:
                        self.assertEqual(actual_state, raw)
                archive = destination / ".agentic-state/archive/upgrade-to-1.9.3/files/legacy-note.txt"
                self.assertEqual(archive.read_bytes(), before["state"][".agentic-state/legacy-note.txt"])
                self.assertEqual(archive.stat().st_mode & stat.S_IWRITE, 0)
                ledger = destination / ".agentic-state/routing/ledger.sqlite"
                self.assertEqual(state_schema(".agentic-state/routing/ledger.sqlite",
                                              ledger.read_bytes()), "routing-ledger:model_runs-v1")
                connection = sqlite3.connect(ledger)
                try:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0], 1)
                finally:
                    connection.close()
                contracts = Contracts(destination / ".agentic/schemas")
                contracts.validate("critic-review", json.loads(
                    (destination / ".agentic-state/reviews/critic-review.json").read_bytes()))
                contracts.validate("controller-event", json.loads(
                    (destination / ".agentic-state/lifecycle/controller-event.json").read_bytes()))
                contracts.validate("operating-change", json.loads(
                    (destination / ".agentic-state/operating/records/operating-change.json").read_bytes()))

    def test_direct_and_chained_real_fixture_upgrades_are_byte_identical(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                direct, _ = self.fixture(version)
                chained, _ = self.fixture(version)
                self.deterministic_upgrade(direct)
                cursor = version
                while NEXT[cursor] != VERSION:
                    cursor = advance_one_fixture_step(chained, cursor)
                self.deterministic_upgrade(chained)
                self.assertEqual((direct / CONFIG).read_bytes(), (chained / CONFIG).read_bytes())
                self.assertEqual((direct / ".github/CODEOWNERS").read_bytes(),
                                 (chained / ".github/CODEOWNERS").read_bytes())
                self.assertEqual(optional_bytes(direct / "OPERATING_CONFIG.yaml"),
                                 optional_bytes(chained / "OPERATING_CONFIG.yaml"))
                self.assertEqual(managed_tree_from_receipt(direct), managed_tree_from_receipt(chained))
                self.assertEqual(subtree(direct, ".agentic-state/"), subtree(chained, ".agentic-state/"))

    def test_schema_incompatible_json_and_header_only_sqlite_are_archived(self):
        destination, _ = self.fixture("1.9.2")
        review = destination / ".agentic-state/reviews/critic-review.json"
        ledger = destination / ".agentic-state/routing/ledger.sqlite"
        review.write_bytes(b'{"valid":"json","schema_version":999}\n')
        ledger.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        result = self.upgrade(destination)
        actions = {item["path"]: item["action"] for item in result["upgrade"]["state_migrations"]}
        self.assertEqual(actions[".agentic-state/reviews/critic-review.json"], "archived_read_only_copy")
        self.assertEqual(actions[".agentic-state/routing/ledger.sqlite"], "archived_read_only_copy")
        for relative in ("reviews/critic-review.json", "routing/ledger.sqlite"):
            self.assertTrue((destination / ".agentic-state/archive/upgrade-to-1.9.3/files" / relative).is_file())

    def test_state_compatibility_is_listed_for_every_adjacent_step(self):
        destination, before = self.fixture("1.8.3")
        plan = self.upgrade(destination, dry_run=True)
        for step in plan["upgrade"]["steps"]:
            actions = {item["path"]: item for item in step["state_migrations"]}
            for relative, raw in before["state"].items():
                if relative.endswith("legacy-note.txt"):
                    self.assertEqual(actions[relative]["action"], "archive_read_only_copy")
                elif (relative.endswith(("reviews/critic-review.json", "lifecycle/controller-event.json"))
                      and step["from"] == "1.8.9"):
                    self.assertEqual(actions[relative]["action"], "migrated_schema")
                    self.assertIn(actions[relative]["target_schema"],
                                  {"critic-review:3", "controller-event:3"})
                else:
                    self.assertEqual(actions[relative]["action"], "retained_schema_compatible")
                    expected_schema = state_schema(relative, raw)
                    if (relative.endswith("reviews/critic-review.json") and
                            step["from"] in {"1.9.1", "1.9.2"}):
                        expected_schema = "critic-review:3"
                    if (relative.endswith("lifecycle/controller-event.json") and
                            step["from"] in {"1.9.1", "1.9.2"}):
                        expected_schema = "controller-event:3"
                    self.assertEqual(actions[relative]["schema"], expected_schema)

    def assert_refused_unchanged(self, destination, pattern):
        before = file_tree(destination)
        with self.assertRaisesRegex(ValidationError, pattern):
            self.upgrade(destination)
        self.assertEqual(file_tree(destination), before)

    def claim(self, destination, old, new):
        for relative in (CONFIG, INSTALLED, PROVENANCE):
            path = destination / relative
            path.write_bytes(path.read_bytes().replace(old.encode(), new.encode(), 1))

    def test_real_fixture_modified_managed_file_reports_both_hashes_and_changes_nothing(self):
        destination, _ = self.fixture("1.9.2")
        agents = destination / "AGENTS.md"
        original = agents.read_bytes()
        agents.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        expected = fixture_manifest("1.9.2")["managed_files"]["AGENTS.md"]["sha256"]
        actual = sha256(agents.read_bytes())
        self.assert_refused_unchanged(destination, rf"AGENTS\.md expected={expected} actual={actual}")

    def test_other_refusals_and_dry_run_use_real_fixture_and_write_nothing(self):
        destination, _ = self.fixture("1.9.2")
        (destination / INSTALLED).write_bytes(b"{invalid receipt\n")
        self.assert_refused_unchanged(destination, "Unrecognised.*invalid receipt.*distribution")

        destination, _ = self.fixture("1.9.2")
        self.claim(destination, "1.9.2", "1.8.5")
        self.assert_refused_unchanged(destination, "Unrecognised.*1.8.5.*distribution")

        destination, _ = self.fixture("1.9.2")
        self.claim(destination, "1.9.2", "1.4.1")
        self.assert_refused_unchanged(destination, "below.*floor 1.8.0")

        destination, _ = self.fixture("1.9.2")
        config = destination / CONFIG
        config.write_bytes(config.read_bytes() + b'\n"expected_workflow_version": "1.9.2"\n')
        self.assert_refused_unchanged(destination, "Owner question")

        destination, _ = self.fixture("1.9.2")
        collision = destination / KNOWN_VERSIONS
        collision.parent.mkdir(parents=True, exist_ok=True)
        collision.write_bytes(b"project-owned collision\n")
        self.assert_refused_unchanged(destination, "collide.*known-versions.json")

        destination, _ = self.fixture("1.9.2")
        before = file_tree(destination)
        plan = self.upgrade(destination, dry_run=True)
        self.assertEqual(plan["status"], "PLAN")
        self.assertEqual(plan["upgrade"]["detected_version"], "1.9.2")
        self.assertIn("configuration_diff_total", plan["upgrade"])
        self.assertEqual(file_tree(destination), before)

    def test_schema_invalid_operating_refuses_before_any_write(self):
        destination, _ = self.fixture("1.9.2")
        operating = destination / "OPERATING_CONFIG.yaml"
        value = load_yaml(operating.read_bytes())
        value["streams"]["count"] = 99
        operating.write_bytes(json.dumps(value, indent=2).encode() + b"\n")
        self.assert_refused_unchanged(destination, r"streams.count|maximum")

    def test_dry_run_and_real_upgrade_report_the_same_writes_for_every_version(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                dry, _ = self.fixture(version)
                actual, _ = self.fixture(version)
                with patch("uuid.uuid4", return_value=FIXED_UUID), \
                        patch("agentic.installer.now_text", return_value="2026-01-02T00:00:00Z"), \
                        patch("agentic.operating.now_text", return_value="2026-01-02T00:00:00Z"):
                    plan = self.upgrade(dry, dry_run=True)
                with patch("uuid.uuid4", return_value=FIXED_UUID), \
                        patch("agentic.installer.now_text", return_value="2026-01-02T00:00:00Z"), \
                        patch("agentic.operating.now_text", return_value="2026-01-02T00:00:00Z"):
                    result = self.upgrade(actual)
                self.assertEqual(plan["write_plan"], result["write_plan"])
                self.assertEqual(plan["upgrade"]["operating_changes"],
                                 result["upgrade"]["operating_changes"])

    def test_failure_while_removing_historical_managed_files_rolls_back(self):
        destination, _ = self.fixture("1.8.3")
        before = {path: digest for path, digest in file_tree(destination).items()
                  if path != ".agentic-install/lock"}
        plan = self.upgrade(destination, dry_run=True)
        with self.assertRaisesRegex(OSError, "Injected installation failure"):
            self.upgrade(destination, fail_after=len(plan["managed_files"]) + 1)
        after = {path: digest for path, digest in file_tree(destination).items()
                 if path != ".agentic-install/lock"}
        self.assertEqual(after, before)

    def test_shipped_table_matches_every_real_fixture(self):
        table = load_known_versions((ROOT / KNOWN_VERSIONS).read_bytes())
        self.assertEqual(tuple(table["versions"]), VERSIONS)
        self.assertEqual(table["floor"], "1.8.0")
        self.assertEqual(table["target"], VERSION)
        for version, entry in table["versions"].items():
            fixture = fixture_manifest(version)
            self.assertEqual(fixture["source_manifest_sha256"], entry["source_manifest_sha256"])
            self.assertEqual(fixture["managed_manifest_sha256"], entry["managed_manifest_sha256"])
            self.assertEqual(fixture["managed_file_count"], entry["managed_file_count"])
            self.assertEqual(fixture["receipt"]["schema"], entry["receipt_schema"])


if __name__ == "__main__":
    unittest.main()
