"""Only the exact seeded CODEOWNERS path is exempt from installed byte pins."""
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
