"""Installed ownership exceptions follow independently pinned release history."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_awf import Fixture, awf, encoded


class DiscoveryCodeownersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-discovery-owners-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)
        self.owner_path = ".github/CODEOWNERS"
        self.fixture.content[self.owner_path] = b"* @maintainer\n"
        self.fixture.manifest["files"][self.owner_path] = awf.digest(self.fixture.content[self.owner_path])
        self.fixture.content["MANIFEST.json"] = encoded(self.fixture.manifest)
        for name in (self.owner_path, "MANIFEST.json"):
            path = self.fixture.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.fixture.content[name])
        self.fixture.write_archive()
        self.project = self.root / "project"
        self.receipt = self.fixture.install_fixture(self.project)
        # Mirror the new installer's explicit, closed mutable-path exception.
        self.receipt["immutable_files"].pop(self.owner_path)
        self.receipt["mutable_paths"].append(self.owner_path)
        self.save_receipt()

    def save_receipt(self):
        (self.project / awf.RECEIPT).write_bytes(encoded(self.receipt))

    def inspect(self):
        return awf.inspect_project(self.fixture.locate(), self.project)["installed"]

    def test_project_owned_codeowners_can_change(self):
        (self.project / self.owner_path).write_bytes(b"* @project-owner @second-maintainer\n")
        result = self.inspect()
        self.assertEqual(result["status"], "REGISTERED_RELEASE_BYTES_VERIFIED")
        self.assertTrue(result["receipt_verified_against_registered_release"])

    def test_mutable_list_cannot_exempt_changed_agents(self):
        self.receipt["mutable_paths"].append("AGENTS.md")
        self.save_receipt()
        (self.project / "AGENTS.md").write_bytes(b"Changed governance\n")
        self.assertEqual(self.inspect()["status"], "INSTALLED_STATE_REQUIRES_RECONCILIATION")

    def test_receipt_cannot_remove_another_immutable_path(self):
        self.receipt["immutable_files"].pop("AGENTS.md")
        self.receipt["mutable_paths"].append("AGENTS.md")
        self.save_receipt()
        self.assertEqual(self.inspect()["status"], "INSTALLED_STATE_REQUIRES_RECONCILIATION")

    def test_other_missing_governance_remains_rejected(self):
        (self.project / "AGENTS.md").unlink()
        self.assertEqual(self.inspect()["status"], "INSTALLED_STATE_REQUIRES_RECONCILIATION")


class DiscoveryRoutingOwnershipTests(unittest.TestCase):
    routing = ".agentic/routing.yaml"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-discovery-routing-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.serial = 0

    def fixture(self, version):
        self.serial += 1
        directory = self.root / str(self.serial)
        directory.mkdir()
        fixture = Fixture(directory, version)
        # Synthetic source bytes exercise the independently established ownership
        # contract; they are not an authentic historical release/install claim.
        source_routing = encoded({"source": "synthetic release routing"})
        fixture.content[self.routing] = source_routing
        fixture.manifest["files"][self.routing] = awf.digest(source_routing)
        fixture.content["MANIFEST.json"] = encoded(fixture.manifest)
        for name in (self.routing, "MANIFEST.json"):
            (fixture.source / name).write_bytes(fixture.content[name])
        fixture.write_archive()
        project = directory / "project"
        receipt = fixture.install_fixture(project)
        # Match actual published 1.9.5/current installer ownership independently
        # of the discovery predicate being tested. Earlier synthetic controls
        # retain the previous immutable-file policy.
        if tuple(map(int, version.split("."))) >= (1, 9, 5):
            receipt["immutable_files"].pop(self.routing)
            receipt["mutable_paths"].append(self.routing)
            (project / awf.RECEIPT).write_bytes(encoded(receipt))
        return fixture, project, receipt

    def inspect(self, fixture, project):
        return awf.inspect_project(fixture.locate(fixture.version), project)["installed"]

    def assert_reconciliation(self, fixture, project):
        result = self.inspect(fixture, project)
        self.assertEqual("INSTALLED_STATE_REQUIRES_RECONCILIATION", result["status"])
        self.assertFalse(result["receipt_verified_against_registered_release"])

    def test_published_195_and_current_196_allow_installed_owner_routing(self):
        for version in ("1.9.5", "1.9.6"):
            with self.subTest(version=version):
                fixture, project, _ = self.fixture(version)
                (project / self.routing).write_bytes(encoded({"owner": "custom route choice"}))
                with patch.object(subprocess, "run", side_effect=AssertionError("No execution")):
                    result = self.inspect(fixture, project)
                self.assertEqual("REGISTERED_RELEASE_BYTES_VERIFIED", result["status"])
                self.assertTrue(result["receipt_verified_against_registered_release"])
                self.assertFalse(result["configuration_validated"])

    def test_operator_routing_must_exist_as_a_file(self):
        for version in ("1.9.5", "1.9.6"):
            for boundary in ("missing", "directory"):
                with self.subTest(version=version, boundary=boundary):
                    fixture, project, _ = self.fixture(version)
                    path = project / self.routing
                    path.unlink()
                    if boundary == "directory":
                        path.mkdir()
                    self.assert_reconciliation(fixture, project)

    def test_operator_routing_hardlinks_are_rejected(self):
        for version in ("1.9.5", "1.9.6"):
            with self.subTest(version=version):
                fixture, project, _ = self.fixture(version)
                target = project / "owner-routing.txt"
                target.write_bytes(b"Owned external routing fixture\n")
                path = project / self.routing
                path.unlink()
                try:
                    os.link(target, path)
                except (OSError, NotImplementedError) as error:
                    self.skipTest("Hardlink creation unavailable: " + str(error))
                self.assert_reconciliation(fixture, project)

    def test_operator_routing_symlinks_are_rejected(self):
        for version in ("1.9.5", "1.9.6"):
            with self.subTest(version=version):
                fixture, project, _ = self.fixture(version)
                target = project / "owner-routing.txt"
                target.write_bytes(b"Owned routing alias target\n")
                path = project / self.routing
                path.unlink()
                try:
                    path.symlink_to(target)
                except (OSError, NotImplementedError) as error:
                    self.skipTest("Symlink creation unavailable: " + str(error))
                self.assert_reconciliation(fixture, project)

    def test_current_immutable_map_and_content_remain_exact(self):
        for version in ("1.9.5", "1.9.6"):
            for mutation in ("extra", "missing", "forged", "content"):
                with self.subTest(version=version, mutation=mutation):
                    fixture, project, receipt = self.fixture(version)
                    if mutation == "extra":
                        receipt["immutable_files"][self.routing] = fixture.manifest["files"][self.routing]
                    elif mutation == "missing":
                        receipt["immutable_files"].pop("AGENTS.md")
                        receipt["mutable_paths"].append("AGENTS.md")
                    elif mutation == "forged":
                        receipt["immutable_files"]["AGENTS.md"] = "0" * 64
                    else:
                        (project / "AGENTS.md").write_bytes(b"Changed immutable instructions\n")
                    (project / awf.RECEIPT).write_bytes(encoded(receipt))
                    self.assert_reconciliation(fixture, project)

    def test_source_and_archive_routing_are_always_pinned(self):
        for version in ("1.9.4", "1.9.5", "1.9.6"):
            for mutation in ("source", "archive"):
                with self.subTest(version=version, mutation=mutation):
                    fixture, _, _ = self.fixture(version)
                    if mutation == "source":
                        (fixture.source / self.routing).write_bytes(b"Changed source routing\n")
                        expected = "SOURCE_CONTENT_MISMATCH"
                    else:
                        fixture.write_archive(transform=lambda name, data:
                            (name, data + b"changed") if name == self.routing else (name, data))
                        expected = "ARCHIVE_CONTENT_MISMATCH"
                    with self.assertRaises(awf.DiscoveryError) as raised:
                        fixture.locate(version)
                    self.assertEqual(expected, raised.exception.code)

    def test_synthetic_194_legacy_routing_stays_immutable(self):
        # Authentic 1.9.4 did not declare routing. This deliberately synthetic
        # older-version control preserves the old predicate without inventing
        # an immutable-routing claim about published 1.9.5.
        fixture, project, _ = self.fixture("1.9.4")
        self.assertTrue(self.inspect(fixture, project)["receipt_verified_against_registered_release"])
        (project / self.routing).write_bytes(b"Changed legacy immutable routing\n")
        self.assert_reconciliation(fixture, project)
        self.assertTrue(awf.managed(self.routing))
        self.assertTrue(awf.managed(self.routing, "1.9.4"))
        self.assertFalse(awf.managed(self.routing, "1.9.5"))
        self.assertFalse(awf.managed(self.routing, "1.9.10"))
        self.assertFalse(awf.managed(self.routing, "1.10.0"))


if __name__ == "__main__":
    unittest.main()
