"""The external-resource CLI is parseable and fails closed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agentic/scripts/external_resources.py"


class ExternalResourceCliTests(unittest.TestCase):
    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def test_validate_registry_and_bounded_scan(self):
        with tempfile.TemporaryDirectory(prefix="awf-resource-cli-") as temporary:
            base = Path(temporary)
            registry = base / "registry.json"
            registry.write_text(json.dumps({
                "format": "awf-external-resource-registry-1", "resources": {
                    "raw_estate": {"access": "read_only", "required_by": [],
                                   "host_mapping": "operator_local", "sensitivity": "restricted",
                                   "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                             "timeout_seconds": 5}}}}), encoding="utf-8")
            accepted = self.run_cli("validate-registry", "--registry", registry)
            self.assertEqual(accepted["status"], "ACCEPTED")
            scan = base / "scan.json"
            scan.write_text(json.dumps({"resource_alias": "raw_estate", "recursive": True,
                                        "max_depth": 0, "file_limit": 5,
                                        "date_range": {"start": "2026-01-01", "end": "2026-10-09"},
                                        "timeout_seconds": 5}), encoding="utf-8")
            rejected = self.run_cli("validate-scan", "--request", scan, code=2)
            self.assertEqual(rejected["status"], "REJECTED")
            self.assertIn("UNBOUNDED_SCAN", rejected["reason"])

    @unittest.skipUnless(os.name == "nt", "real PowerShell admission is Windows-specific")
    def test_real_powershell_admits_temporary_read_only_resource(self):
        with tempfile.TemporaryDirectory(prefix="awf-resource-real-") as temporary:
            base = Path(temporary)
            project = base / "project"
            estate = base / "estate"
            project.mkdir()
            estate.mkdir()
            (estate / "sample.bin").write_bytes(b"x")
            registry = project / "registry.json"
            registry.write_text(json.dumps({
                "format": "awf-external-resource-registry-1", "resources": {
                    "raw_estate": {"access": "read_only", "required_by": ["AWF-8"],
                                   "host_mapping": "operator_local", "sensitivity": "restricted",
                                   "probe": {"sample_relative_path": "sample.bin", "listing_limit": 2,
                                             "timeout_seconds": 5}}}}), encoding="utf-8")
            mapping = base / "private-mapping.json"
            mapping.write_text(json.dumps({
                "format": "awf-external-resource-mapping-1", "resources": {
                    "raw_estate": {"local_path": str(estate),
                                   "canonical_locator": "urn:awf:synthetic:raw-estate",
                                   "mapping_kind": "local"}}}), encoding="utf-8")
            receipt = self.run_cli(
                "admit", "--project-root", project, "--registry", registry,
                "--mapping", mapping, "--resource", "raw_estate", "--task", "AWF-8",
                "--principal", "worker-A", "--session", "session-1", "--now",
                "2026-10-09T12:00:00Z")
            self.assertEqual(receipt["state"], "READ_VERIFIED")
            self.assertEqual(receipt["evidence"]["bytes_read"], 1)
            self.assertNotIn(str(estate), json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
