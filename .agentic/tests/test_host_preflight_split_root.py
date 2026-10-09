"""K14 split-root host preflight reports independent host capabilities."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from agentic.host_preflight import preflight


NOW = "2026-10-09T12:00:00Z"


class SplitRootPreflightTests(unittest.TestCase):
    def registry(self, access="read_only"):
        return {"format": "awf-external-resource-registry-1", "resources": {
            "raw_estate": {"access": access, "required_by": ["AWF-8"],
                           "host_mapping": "operator_local", "sensitivity": "restricted",
                           "probe": {"sample_relative_path": "sample.bin", "listing_limit": 4,
                                     "timeout_seconds": 5}}}}

    def mapping(self, local_path):
        return {"format": "awf-external-resource-mapping-1", "resources": {
            "raw_estate": {"local_path": local_path, "canonical_locator": "\\\\synthetic\\estate",
                           "mapping_kind": "mapped_drive"}}}

    @staticmethod
    def host_run(args, cwd=None):
        if args[:2] == ["git", "--version"]:
            output = "git version synthetic"
        elif args[:3] == ["git", "config", "--get"] and args[-1] == "core.longpaths":
            output = "true"
        elif args[:3] == ["git", "config", "--get"] and args[-1] == "core.autocrlf":
            output = "true"
        elif args[0] == "powershell":
            output = "MachinePolicy=Undefined\nUserPolicy=Undefined\nProcess=Bypass\nCurrentUser=RemoteSigned\nLocalMachine=Undefined"
        else:
            output = ""
        return {"executable": args[0], "exit_code": 0, "output": output,
                "diagnostic_category": "OK", "cleanup_errors": [],
                "resource_cleanup_complete": True}

    @staticmethod
    def resource_run(command, cwd=None):
        return {"diagnostic_category": "OK", "exit_code": 0, "output": "",
                "probe": {"observed_locator": "\\\\synthetic\\estate", "listing_count": 2,
                          "sample_size": 1, "sample_mtime": NOW, "bytes_read": 1}}

    def test_ac40_ac54_repository_write_and_read_only_resource_pass_separately(self):
        resource_commands = []

        def resource_run(command, cwd=None):
            resource_commands.append(command)
            return self.resource_run(command, cwd=cwd)

        with tempfile.TemporaryDirectory(prefix="awf-split-root-") as temporary:
            root = Path(temporary)
            raw_local_path = str((root / "operator-resource").resolve())
            Path(raw_local_path).mkdir()
            (root / ".gitattributes").write_text("/.agentic/** text eol=lf\n/AGENTS.md text eol=lf\n/.github/PULL_REQUEST_TEMPLATE.md text eol=lf\n", encoding="utf-8")
            with patch("agentic.host_preflight.run", side_effect=self.host_run), \
                    patch("agentic.host_preflight.symlink_privilege", return_value=("PASS", "available", "")):
                report = preflight(
                    root, platform="nt", external_registry=self.registry(),
                    external_mappings=self.mapping(raw_local_path), resource_executor=resource_run)
        rows = {item["check"]: item for item in report["rows"]}
        self.assertEqual(rows["process_launch"]["status"], "PASS")
        self.assertEqual(rows["repository_writable"]["status"], "PASS")
        self.assertEqual(rows["external_workspace_roots"]["status"], "PASS")
        self.assertEqual(rows["external_resource:{raw_estate}"]["status"], "PASS")
        self.assertEqual(len(resource_commands), 1)
        self.assertEqual(resource_commands[0][0], "powershell")
        rendered = json.dumps(report)
        self.assertNotIn("synthetic", rendered)
        self.assertNotIn(raw_local_path, rendered)
        self.assertNotIn(json.dumps(raw_local_path)[1:-1], rendered)

    def test_unsupported_split_writable_roots_get_one_precise_remedy(self):
        with tempfile.TemporaryDirectory(prefix="awf-split-write-") as temporary:
            root = Path(temporary)
            raw_local_path = str((root / "operator-resource").resolve())
            Path(raw_local_path).mkdir()
            with patch("agentic.host_preflight.run", side_effect=self.host_run), \
                    patch("agentic.host_preflight.symlink_privilege", return_value=("PASS", "available", "")):
                report = preflight(root, platform="posix", external_registry=self.registry("write"),
                                   external_mappings=self.mapping(raw_local_path))
        row = {item["check"]: item for item in report["rows"]}["external_workspace_roots"]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("single writable workspace root", row["remedy"])
        self.assertNotIn("source folder", row["remedy"])
        external_warnings = [item for item in report["rows"]
                             if item["check"].startswith("external_") and item["status"] == "WARN"]
        self.assertEqual([item["check"] for item in external_warnings], ["external_workspace_roots"])


if __name__ == "__main__":
    unittest.main()
