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
from agentic.canonical import load
from agentic.configuration import inspect_config
from agentic.contracts import Contracts
from agentic.handoff import (BASES, REQUIRED_FIELDS, build_snapshot, compare_snapshot,
                             render_markdown, safe_origin)
from agentic.lifecycle import definition
from agentic.operating import read_operating
from agentic.store import Store

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
        self.state_temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "remote", "add", "origin", "https://user:ghp_SECRETTOKEN@example.invalid/o/r.git")
        (self.root / ".agentic").mkdir()
        self.config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        self.config["jira"].update(enabled=True, cloud_id="cloud-1", site="https://jira.example.invalid",
                                   provider_project_id="project-1", project_key="SYN", controller_actor_id="actor-1")
        self.config["execution"]["host_broker"].update(enabled=True, broker_id="fixture-broker",
                                                          resources={"readonly_data": 1})
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("project-config", self.config)
        self.config_report = inspect_config(self.config, definition(), contracts)
        self.assertEqual(self.config_report["status"], "ACCEPTED", self.config_report)
        (self.root / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(self.config), encoding="utf-8")
        self.operating_config = json.loads((ROOT / "OPERATING_CONFIG.yaml").read_text(encoding="utf-8"))
        (self.root / "OPERATING_CONFIG.yaml").write_text(json.dumps(self.operating_config), encoding="utf-8")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "base")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "complete SYN-7")
        self.operating_hash = read_operating(self.root, self.config).operating_hash

    def tearDown(self):
        self.temp.cleanup()
        self.state_temp.cleanup()

    def snapshot(self, now="2026-10-07T00:00:00Z", *, state_path=None):
        status = json.loads(json.dumps(STATUS))
        status["operating"] = {"hash": self.operating_hash, "status": "ACCEPTED"}
        return build_snapshot(self.root, status=status, now=now, state_path=state_path)

    def coordinator_state(self, tickets):
        path = Path(self.state_temp.name) / "coordinator.sqlite3"
        store = Store(path, self.config["project"]["id"], worktree_roots=[self.root])
        with store.transaction() as database:
            for revision, ticket in enumerate(tickets, start=1):
                database.execute("INSERT INTO tickets VALUES(?,?,?)", (ticket, "DONE", revision))
                store._event(database, "JIRA_RECONCILED",
                             {"issue_id": ticket, "previous_state": "MERGED", "state": "DONE",
                              "revision": revision, "facts": {"closeout_valid": True},
                              "resume_state": None})
        return path

    def test_snapshot_records_observations_without_credentials_or_authority(self):
        snapshot = self.snapshot()
        self.assertEqual(self.config_report["status"], "ACCEPTED")
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
        self.assertEqual(snapshot["completed_tickets"],
                         {"value": None, "basis": "unavailable",
                          "observed_at": "2026-10-07T00:00:00Z"})
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

    def test_snapshot_sanitizes_scp_credentials_and_rejects_unparseable_origin(self):
        for remote, secret in (("ghp_TOKEN@example.invalid:o/r.git", "ghp_TOKEN"),
                               ("user:password@example.invalid:o/r.git", "password")):
            with self.subTest(remote=remote):
                git(self.root, "remote", "set-url", "origin", remote)
                snapshot = self.snapshot()
                rendered = json.dumps(snapshot) + render_markdown(snapshot)
                self.assertEqual(snapshot["repository"]["origin"]["value"], "example.invalid:o/r.git")
                self.assertNotIn(secret, rendered)

        git(self.root, "remote", "set-url", "origin", "not a remote")
        self.assertEqual(self.snapshot()["repository"]["origin"],
                         {"value": None, "basis": "unavailable",
                          "observed_at": "2026-10-07T00:00:00Z"})

    def test_receiving_host_reports_match_and_drift(self):
        received = self.snapshot()
        self.assertEqual(compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))["status"], "MATCH")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "moved")
        result = compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))
        self.assertEqual(result["status"], "DRIFT")
        self.assertEqual([item["field"] for item in result["drift"]], ["repository.head"])
        self.assertIs(result["execution_authority"], False)

    def test_provenance_basis_changes_are_drift(self):
        for received_basis, current_basis in (("configured", "verified"),
                                              ("verified", "user-asserted")):
            with self.subTest(received_basis=received_basis, current_basis=current_basis):
                received = self.snapshot()
                current = self.snapshot("2026-10-08T00:00:00Z")
                received["jira"]["cloud_id"]["basis"] = received_basis
                current["jira"]["cloud_id"]["basis"] = current_basis
                result = compare_snapshot(received, current)
                self.assertEqual(result["status"], "DRIFT")
                drift = next(item for item in result["drift"] if item["field"] == "jira.cloud_id")
                self.assertEqual((drift["received_basis"], drift["current_basis"]),
                                 (received_basis, current_basis))

    def test_completed_tickets_are_unavailable_without_authoritative_state(self):
        git(self.root, "commit", "-q", "--allow-empty", "-m", "AWF-21 handoff implementation")
        self.assertEqual(self.snapshot()["completed_tickets"],
                         {"value": None, "basis": "unavailable",
                          "observed_at": "2026-10-07T00:00:00Z"})

    def test_completed_tickets_use_verified_coordinator_state(self):
        state_path = self.coordinator_state(["SYN-9", "SYN-7"])
        snapshot = self.snapshot(state_path=state_path)
        self.assertEqual(snapshot["completed_tickets"],
                         {"value": ["SYN-7", "SYN-9"], "basis": "verified",
                          "observed_at": "2026-10-07T00:00:00Z"})
        self.assertIn("project-bound, hash-chained AWF coordinator state", render_markdown(snapshot))

    def test_verified_coordinator_state_can_authoritatively_list_no_completed_tickets(self):
        state_path = self.coordinator_state([])
        self.assertEqual(self.snapshot(state_path=state_path)["completed_tickets"],
                         {"value": [], "basis": "verified",
                          "observed_at": "2026-10-07T00:00:00Z"})

    def test_unreadable_configuration_marks_resources_unavailable(self):
        with patch("agentic.handoff.load", side_effect=ValueError("malformed config")):
            snapshot = self.snapshot()
        self.assertEqual(snapshot["external_resources"], {"value": None, "basis": "unavailable",
                                                           "observed_at": "2026-10-07T00:00:00Z"})

    def test_schema_invalid_configuration_marks_resources_unavailable(self):
        self.config["execution"]["host_broker"]["resources"] = {"bad-name": 1}
        (self.root / ".agentic/PROJECT_CONFIG.yaml").write_text(
            json.dumps(self.config), encoding="utf-8")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["external_resources"],
                         {"value": None, "basis": "unavailable",
                          "observed_at": "2026-10-07T00:00:00Z"})

    def test_blocker_diagnostics_are_not_exported(self):
        secret = "ghp_EXAMPLE_SECRET"
        status = json.loads(json.dumps(STATUS))
        status["operating"] = {"hash": self.operating_hash, "status": "ACCEPTED"}
        status["checks"] = [{"code": "OPERATING_INVALID", "state": "INVALID",
                             "evidence": "Rejected operating source: " + secret}]
        snapshot = build_snapshot(self.root, status=status, now="2026-10-07T00:00:00Z")
        rendered = json.dumps(snapshot) + render_markdown(snapshot)
        self.assertNotIn(secret, rendered)
        self.assertEqual(snapshot["blockers"]["value"], [{
            "code": "OPERATING_INVALID", "state": "INVALID",
            "evidence": "Diagnostic details omitted; inspect current status locally.",
        }])

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

        for field in ("external_resources", "completed_tickets"):
            with self.subTest(field=field):
                malformed = self.snapshot()
                malformed[field] = {"value": [{}], "basis": "verified",
                                    "observed_at": "2026-10-07T00:00:00Z"}
                result = compare_snapshot(malformed, self.snapshot())
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn(field, result["invalid_fields"])

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

    def test_safe_origin_strips_credential_bearing_url_components(self):
        self.assertEqual(safe_origin("ssh://git@example.invalid/o/r.git"), "ssh://example.invalid/o/r.git")
        self.assertEqual(safe_origin("https://user:token@example.invalid/o/r.git?access_token=token#fragment"),
                         "https://example.invalid/o/r.git")
        self.assertEqual(safe_origin("git@example.invalid:o/r.git"), "git@example.invalid:o/r.git")
        self.assertEqual(safe_origin("ghp_TOKEN@example.invalid:o/r.git"), "example.invalid:o/r.git")
        self.assertEqual(safe_origin("user:password@example.invalid:o/r.git"), "example.invalid:o/r.git")
        self.assertEqual(safe_origin("git@example.invalid:o/r.git?token=secret#fragment"), "git@example.invalid:o/r.git")
        self.assertIsNone(safe_origin(chr(92).join(("C:", "repositories", "private"))))
        self.assertIsNone(safe_origin("not a remote"))
        self.assertIsNone(safe_origin(None))


if __name__ == "__main__":
    unittest.main()
