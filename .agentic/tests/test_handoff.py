"""K15 handoff snapshot: read-only, credential-free, compared rather than trusted."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agentic.canonical import load
from agentic.child_process import child_env
from agentic.contracts import Contracts
from agentic.handoff import (BASES, COMPARED, FIELDS, build_snapshot, compare_snapshot, render_markdown,
                             safe_origin)
from agentic.operating import read_operating

ROOT = Path(__file__).resolve().parents[2]
STATUS = {"project_state": "ACTIVE", "integrity_valid": True, "adoption": "MERGED",
          "operating": {"hash": "a" * 64},
          "checks": [{"code": "INSTALLATION_INTEGRITY", "state": "PASS", "evidence": "ok"},
                     {"code": "RELEASE_TRUST", "state": "UNOBSERVED", "evidence": "no receipt"}]}


def git(root, *arguments):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, env=child_env(env))


def project_config():
    """A schema-valid project configuration that declares two broker resources."""
    config = load(ROOT / ".agentic/PROJECT_CONFIG.yaml")
    config["github"].update(repository_id=4242, repository="synthetic/project")
    config["jira"].update(enabled=True, cloud_id="11111111-2222-3333-4444-555555555555",
                          site="https://synthetic.atlassian.net", project_key="SYN",
                          provider_project_id=None, controller_actor_id="712020:actor-1")
    config["execution"]["host_broker"].update(enabled=True, broker_id="host-a",
                                              resources={"readonly_data": 1, "gpu_slot": 2})
    return config


class HandoffSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "remote", "add", "origin", "https://user:ghp_SECRETTOKEN@example.invalid/o/r.git")
        (self.root / ".agentic").mkdir()
        self.write_config(project_config())
        git(self.root, "commit", "-q", "--allow-empty", "-m", "base")

    def write_config(self, config):
        (self.root / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")

    def snapshot(self, now="2026-10-07T00:00:00Z", status=STATUS):
        return build_snapshot(self.root, status=status, now=now)

    def active_status(self):
        """A status as project_status reports a verified ACTIVE project, with real operating routes."""
        shutil.copyfile(ROOT / "OPERATING_CONFIG.yaml", self.root / "OPERATING_CONFIG.yaml")
        operating = read_operating(self.root, load(self.root / ".agentic/PROJECT_CONFIG.yaml"))
        status = deepcopy(STATUS)
        status.update(adoption="MERGED_VERIFIED", repository_id=4242, release_trust_basis="independent_release_source",
                      adoption_pr=7, adoption_merge_sha="c" * 40,
                      operating={"status": "ACCEPTED", "hash": operating.operating_hash})
        status["checks"][1] = {"code": "RELEASE_TRUST", "state": "PASS", "evidence": "matched"}
        return status, operating

    def test_fixture_configuration_is_schema_valid(self):
        Contracts(ROOT / ".agentic/schemas").validate("project-config", project_config())

    def test_snapshot_records_observations_without_credentials_or_authority(self):
        snapshot = self.snapshot()
        text = json.dumps(snapshot) + render_markdown(snapshot)
        self.assertNotIn("SECRETTOKEN", text)
        self.assertNotIn("user:", text)
        self.assertEqual(snapshot["repository"]["origin"]["value"], "https://example.invalid/o/r.git")
        self.assertEqual(snapshot["repository"]["branch"]["value"], "main")
        self.assertEqual(len(snapshot["repository"]["head"]["value"]), 40)
        self.assertEqual(snapshot["jira"]["cloud_id"], {"value": "11111111-2222-3333-4444-555555555555",
                                                        "basis": "configured", "observed_at": "2026-10-07T00:00:00Z"})
        self.assertEqual(snapshot["jira"]["provider_project_id"]["basis"], "unavailable")
        self.assertEqual([b["code"] for b in snapshot["blockers"]["value"]], ["RELEASE_TRUST"])
        for key in ("execution_authority", "merge_authority", "jira_authority"):
            self.assertIs(snapshot[key], False)

    def test_declared_broker_resources_come_from_execution_host_broker(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["external_resources"],
                         {"value": {"gpu_slot": 2, "readonly_data": 1}, "basis": "configured",
                          "observed_at": "2026-10-07T00:00:00Z"})
        # A stray top-level host_broker is not a valid declaration location.
        config = project_config()
        config["host_broker"] = {"resources": {"decoy": 1}}
        config["execution"]["host_broker"]["resources"] = {}
        self.write_config(config)
        self.assertEqual(self.snapshot()["external_resources"]["value"], {})
        self.assertEqual(self.snapshot()["external_resources"]["basis"], "configured")
        (self.root / ".agentic/PROJECT_CONFIG.yaml").unlink()
        self.assertEqual(self.snapshot()["external_resources"]["basis"], "unavailable")

    def test_snapshot_carries_every_k15_fact_with_basis_and_observation_time(self):
        status, operating = self.active_status()
        snapshot = self.snapshot(status=status)
        for key in FIELDS:
            item = snapshot
            for part in key.split("."):
                item = item[part]
            with self.subTest(field=key):
                self.assertEqual(set(item), {"value", "basis", "observed_at"})
                self.assertIn(item["basis"], BASES)
                self.assertEqual(item["observed_at"], "2026-10-07T00:00:00Z")
        self.assertEqual(snapshot["repository"]["path"]["value"], str(self.root.absolute()))
        self.assertEqual(snapshot["repository"]["numeric_id"], {"value": 4242, "basis": "verified",
                                                                "observed_at": "2026-10-07T00:00:00Z"})
        self.assertEqual(snapshot["awf"]["trust_basis"]["value"], "independent_release_source")
        self.assertEqual(snapshot["adoption"]["state"]["value"], "MERGED_VERIFIED")
        self.assertEqual(snapshot["adoption"]["pr"]["value"], 7)
        self.assertEqual(snapshot["adoption"]["merge_commit"]["value"], "c" * 40)
        self.assertEqual(snapshot["operating"]["hash"]["value"], operating.operating_hash)
        routes = snapshot["operating"]["routes"]
        self.assertEqual(routes["basis"], "verified")
        self.assertEqual(routes["value"]["critic"], operating.config["critic"])
        self.assertEqual(routes["value"]["streams"], operating.config["streams"])
        self.assertNotIn("source", routes["value"])
        self.assertIn("| operating.routes | {", render_markdown(snapshot))

    def test_unobserved_k15_facts_are_recorded_unavailable_not_omitted(self):
        snapshot = self.snapshot()
        # Without provider observation the configured repository ID is used and labelled as such.
        self.assertEqual(snapshot["repository"]["numeric_id"]["basis"], "configured")
        self.assertEqual(snapshot["repository"]["numeric_id"]["value"], 4242)
        for section, key in (("awf", "trust_basis"), ("adoption", "pr"), ("adoption", "merge_commit"),
                             ("operating", "routes")):
            with self.subTest(field=section + "." + key):
                self.assertEqual(snapshot[section][key]["value"], None)
                self.assertEqual(snapshot[section][key]["basis"], "unavailable")
        # Routes are only reported for the operating snapshot that status accepted.
        status, _operating = self.active_status()
        status["operating"]["hash"] = "f" * 64
        self.assertEqual(self.snapshot(status=status)["operating"]["routes"]["basis"], "unavailable")

    def test_receiving_host_reports_match_and_drift(self):
        received = self.snapshot()
        self.assertEqual(compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))["status"], "MATCH")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "moved")
        result = compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))
        self.assertEqual(result["status"], "DRIFT")
        self.assertEqual([item["field"] for item in result["drift"]], ["repository.head"])
        self.assertIs(result["execution_authority"], False)
        status, _operating = self.active_status()
        result = compare_snapshot(self.snapshot(status=status), self.snapshot("2026-10-08T00:00:00Z"))
        self.assertEqual(result["status"], "DRIFT")
        fields = {item["field"] for item in result["drift"]}
        self.assertTrue({"awf.trust_basis", "adoption.pr", "adoption.merge_commit", "operating.routes"} <= fields)
        # The same repository ID, verified on one host and configured on the other, is not drift.
        self.assertNotIn("repository.numeric_id", fields)

    def test_incomplete_or_malformed_snapshot_never_matches(self):
        # A host with nothing observable: most current values are null.
        (self.root / ".agentic/PROJECT_CONFIG.yaml").unlink()
        bare = {"checks": []}
        current = self.snapshot("2026-10-08T00:00:00Z", status=bare)
        complete = self.snapshot(status=bare)
        self.assertEqual(compare_snapshot(complete, current)["status"], "MATCH")
        for key in FIELDS:
            received = deepcopy(complete)
            *parents, leaf = key.split(".")
            container = received
            for part in parents:
                container = container[part]
            del container[leaf]
            with self.subTest(missing=key):
                result = compare_snapshot(received, current)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn({"field": key, "problem": "missing"}, result["problems"])
                self.assertIs(result["execution_authority"], False)
        malformed = {
            "bare value": lambda s: s["repository"].__setitem__("head", None),
            "unknown basis": lambda s: s["jira"]["cloud_id"].__setitem__("basis", "rumoured"),
            "value marked unavailable": lambda s: s["jira"]["cloud_id"].update(value="cloud-x"),
            "null value not marked unavailable": lambda s: s["adoption"]["pr"].update(basis="verified"),
            "no observation time": lambda s: s["operating"]["hash"].pop("observed_at"),
            "section replaced": lambda s: s.__setitem__("adoption", None),
        }
        for label, damage in malformed.items():
            received = deepcopy(complete)
            damage(received)
            with self.subTest(malformed=label):
                self.assertEqual(compare_snapshot(received, current)["status"], "REJECTED")
        legacy = deepcopy(complete)
        for section, key in (("repository", "numeric_id"), ("awf", "trust_basis"), ("operating", "routes")):
            del legacy[section][key]
        legacy["awf"]["adoption"] = legacy.pop("adoption")["state"]
        result = compare_snapshot(legacy, current)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn({"field": "adoption.pr", "problem": "missing"}, result["problems"])
        self.assertEqual(compare_snapshot({k: v for k, v in complete.items() if k != "generated_at"},
                                          current)["status"], "REJECTED")

    def test_compared_fields_are_cross_host_identities(self):
        self.assertEqual(set(FIELDS) - set(COMPARED), {"repository.path", "blockers"})

    def test_snapshot_claiming_authority_or_unknown_schema_is_rejected(self):
        forged = self.snapshot()
        forged["merge_authority"] = True
        self.assertEqual(compare_snapshot(forged, self.snapshot())["status"], "REJECTED")
        self.assertEqual(compare_snapshot({"schema": "other"}, self.snapshot())["status"], "REJECTED")

    def test_safe_origin_strips_userinfo_only(self):
        self.assertEqual(safe_origin("ssh://git@example.invalid/o/r.git"), "ssh://example.invalid/o/r.git")
        self.assertEqual(safe_origin("git@example.invalid:o/r.git"), "git@example.invalid:o/r.git")
        self.assertIsNone(safe_origin(None))


if __name__ == "__main__":
    unittest.main()
