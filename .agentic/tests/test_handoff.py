"""K15 handoff snapshot: read-only, credential-free, compared rather than trusted."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentic.child_process import child_env
from agentic.contracts import Contracts
from agentic.handoff import (BASES, REQUIRED_FIELDS, build_snapshot, compare_snapshot,
                             render_markdown, safe_origin)
from agentic.operating import read_operating

STATUS = {"project_state": "ACTIVE", "integrity_valid": True, "adoption": "MERGED",
          "release_trust_basis": "trusted-release-fixture",
          "adoption_pr": 48, "adoption_merge_sha": "b" * 40,
          "checks": [{"code": "INSTALLATION_INTEGRITY", "state": "PASS", "evidence": "ok"},
                     {"code": "RELEASE_TRUST", "state": "UNOBSERVED", "evidence": "no receipt"}]}
ROOT = Path(__file__).resolve().parents[2]


def git(root, *arguments):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, env=child_env(env))


class HandoffSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "remote", "add", "origin", "https://user:ghp_SECRETTOKEN@example.invalid/o/r.git")
        (self.root / ".agentic").mkdir()
        self.config = json.loads((ROOT / ".agentic/PROJECT_CONFIG.yaml").read_text(encoding="utf-8"))
        self.config["github"]["repository_id"] = 101
        self.config["jira"].update(enabled=True, cloud_id="cloud-1", site="https://jira.example.invalid",
                                   provider_project_id="project-1", project_key="SYN", controller_actor_id="actor-1")
        self.config["execution"]["host_broker"]["resources"] = {"readonly_data": 1}
        Contracts(ROOT / ".agentic/schemas").validate("project-config", self.config)
        (self.root / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(self.config), encoding="utf-8")
        self.operating_config = json.loads((ROOT / "OPERATING_CONFIG.yaml").read_text(encoding="utf-8"))
        (self.root / "OPERATING_CONFIG.yaml").write_text(json.dumps(self.operating_config), encoding="utf-8")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "base")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "complete SYN-7")
        self.operating_hash = read_operating(self.root, self.config).operating_hash

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, now="2026-10-07T00:00:00Z"):
        status = json.loads(json.dumps(STATUS))
        status["operating"] = {"hash": self.operating_hash, "status": "ACCEPTED"}
        return build_snapshot(self.root, status=status, now=now)

    def test_snapshot_records_observations_without_credentials_or_authority(self):
        snapshot = self.snapshot()
        text = json.dumps(snapshot) + render_markdown(snapshot)
        self.assertNotIn("SECRETTOKEN", text)
        self.assertNotIn("user:", text)
        self.assertEqual(snapshot["repository"]["origin"]["value"], "https://example.invalid/o/r.git")
        self.assertEqual(snapshot["repository"]["id"]["value"], 101)
        self.assertEqual(snapshot["repository"]["branch"]["value"], "main")
        self.assertEqual(len(snapshot["repository"]["head"]["value"]), 40)
        self.assertEqual(snapshot["jira"]["cloud_id"], {"value": "cloud-1", "basis": "configured",
                                                        "observed_at": "2026-10-07T00:00:00Z"})
        self.assertEqual(snapshot["jira"]["provider_project_id"], {"value": "project-1", "basis": "configured",
                                                                       "observed_at": "2026-10-07T00:00:00Z"})
        self.assertEqual(snapshot["external_resources"]["value"], ["readonly_data"])
        self.assertEqual(snapshot["awf"]["trust_basis"]["value"], "trusted-release-fixture")
        self.assertEqual(snapshot["adoption"]["pr"]["value"], 48)
        self.assertEqual(snapshot["adoption"]["merge"]["value"], "b" * 40)
        self.assertEqual(snapshot["operating"]["routes"]["value"]["streams"]["A"]["worker"]["model"], "gpt-5.6-luna")
        self.assertEqual(snapshot["completed_tickets"]["value"], ["SYN-7"])
        self.assertEqual(snapshot["completed_tickets"]["basis"], "user-asserted")
        self.assertEqual([b["code"] for b in snapshot["blockers"]["value"]], ["RELEASE_TRUST"])
        for key in ("execution_authority", "merge_authority", "jira_authority"):
            self.assertIs(snapshot[key], False)
        for dotted in REQUIRED_FIELDS:
            item = snapshot
            for key in dotted.split("."):
                item = item[key]
            self.assertEqual(set(item), {"value", "basis", "observed_at"})
            self.assertIn(item["basis"], BASES)
            self.assertEqual(item["observed_at"], "2026-10-07T00:00:00Z")

    def test_receiving_host_reports_match_and_drift(self):
        received = self.snapshot()
        self.assertEqual(compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))["status"], "MATCH")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "moved")
        result = compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))
        self.assertEqual(result["status"], "DRIFT")
        self.assertEqual([item["field"] for item in result["drift"]], ["repository.head"])
        self.assertIs(result["execution_authority"], False)

    def test_snapshot_claiming_authority_or_unknown_schema_is_rejected(self):
        forged = self.snapshot()
        forged["merge_authority"] = True
        self.assertEqual(compare_snapshot(forged, self.snapshot())["status"], "REJECTED")
        self.assertEqual(compare_snapshot({"schema": "other"}, self.snapshot())["status"], "REJECTED")

    def test_incomplete_or_malformed_snapshot_is_rejected_before_null_comparison(self):
        received = self.snapshot()
        del received["completed_tickets"]
        current = self.snapshot("2026-10-08T00:00:00Z")
        current["completed_tickets"] = {"value": None, "basis": "unavailable",
                                        "observed_at": "2026-10-08T00:00:00Z"}
        result = compare_snapshot(received, current)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("completed_tickets", result["invalid_fields"])

        malformed = self.snapshot()
        malformed["repository"]["id"]["value"] = "101"
        result = compare_snapshot(malformed, self.snapshot())
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("repository.id", result["invalid_fields"])

        malformed = self.snapshot()
        malformed["external_resources"]["value"] = ["not-a-resource"]
        result = compare_snapshot(malformed, self.snapshot())
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("external_resources", result["invalid_fields"])

        malformed = self.snapshot()
        malformed["operating"]["routes"]["value"]["controller"]["token"] = "rejected"
        result = compare_snapshot(malformed, self.snapshot())
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("operating.routes", result["invalid_fields"])

    def test_routes_are_hash_bound_and_credential_free(self):
        unsafe_config = json.loads(json.dumps(self.operating_config))
        unsafe_config["controller"]["token"] = "SECRET_ROUTE_TOKEN"
        unsafe_config["specialist"]["password"] = "SECRET_PASSWORD"
        unsafe = SimpleNamespace(operating_hash=self.operating_hash, config=unsafe_config)
        with patch("agentic.handoff.read_operating", return_value=unsafe):
            snapshot = self.snapshot()
        rendered = json.dumps(snapshot) + render_markdown(snapshot)
        self.assertNotIn("SECRET_ROUTE_TOKEN", rendered)
        self.assertNotIn("SECRET_PASSWORD", rendered)
        self.assertEqual(set(snapshot["operating"]["routes"]["value"]["controller"]),
                         {"model", "reasoning_effort"})
        self.assertEqual(compare_snapshot(snapshot, self.snapshot())["status"], "MATCH")

        changed = SimpleNamespace(operating_hash="c" * 64, config=unsafe.config)
        with patch("agentic.handoff.read_operating", return_value=changed):
            snapshot = self.snapshot()
        self.assertEqual(snapshot["operating"]["hash"]["basis"], "unavailable")
        self.assertEqual(snapshot["operating"]["routes"]["basis"], "unavailable")

    def test_safe_origin_strips_userinfo_only(self):
        self.assertEqual(safe_origin("ssh://git@example.invalid/o/r.git"), "ssh://example.invalid/o/r.git")
        self.assertEqual(safe_origin("git@example.invalid:o/r.git"), "git@example.invalid:o/r.git")
        self.assertIsNone(safe_origin(None))


if __name__ == "__main__":
    unittest.main()
