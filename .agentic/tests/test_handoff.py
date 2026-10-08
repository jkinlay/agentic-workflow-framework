"""K15 handoff snapshot: read-only, credential-free, compared rather than trusted."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import uuid

from agentic import ValidationError
from agentic import cli
from agentic.canonical import load
from agentic.child_process import child_env
from agentic.contracts import Contracts
from agentic.handoff import (BASES, COMPARED, FIELDS, MAX_AGE_SECONDS, build_snapshot, compare_snapshot,
                             render_markdown, safe_origin)
from agentic.operating import read_operating

ROOT = Path(__file__).resolve().parents[2]
STATUS = {"project_state": "ACTIVE", "integrity_valid": True, "adoption": "MERGED",
          "operating": {"hash": "a" * 64},
          "checks": [{"code": "INSTALLATION_INTEGRITY", "state": "PASS", "evidence": "ok"},
                     {"code": "RELEASE_TRUST", "state": "UNOBSERVED", "evidence": "no receipt"}]}
# The K15 handoff facts, listed independently of the implementation's FIELDS so a
# fact dropped from the snapshot cannot hide behind the same list.
K15_FACTS = {
    "repository.path", "repository.origin", "repository.numeric_id", "repository.branch", "repository.head",
    "awf.version", "awf.project_state", "awf.integrity_valid", "awf.trust_basis",
    "adoption.state", "adoption.pr", "adoption.merge_commit", "operating.hash", "operating.routes",
    "jira.cloud_id", "jira.provider_project_id", "jira.project_key", "jira.controller_actor_id",
    "external_resources", "continuity.completed_tickets", "blockers",
}


def git_out(root, *arguments):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True,
                          env=child_env(env)).stdout.strip()


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

    def completed_ticket(self, issue_id="2001", pr=7, repository_id=4242, merge=True):
        """Merge a ticket branch into main and return its closeout record."""
        base = git_out(self.root, "rev-parse", "HEAD")
        branch = "ticket-" + issue_id
        git(self.root, "checkout", "-q", "-b", branch)
        content = f"ticket {issue_id}\n".encode()
        (self.root / f"{branch}.txt").write_bytes(content)
        git(self.root, "add", f"{branch}.txt")
        git(self.root, "commit", "-q", "-m", branch)
        head = git_out(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "main")
        git(self.root, "merge", "-q", "--no-ff", "-m", "merge " + branch, branch)
        merge_sha = git_out(self.root, "rev-parse", "HEAD")
        tree = git_out(self.root, "rev-parse", "HEAD^{tree}")
        blob = git_out(self.root, "rev-parse", f"HEAD:{branch}.txt")
        if not merge:
            git(self.root, "reset", "-q", "--hard", base)
        binding = deepcopy(load(ROOT / ".agentic/examples/evidence-bundle.json")["critic"]["binding"])
        binding.update(issue_id=issue_id, repository_id=repository_id)
        return {"schema_version": 3, "record_id": str(uuid.uuid4()), "created_at": "2026-10-06T00:00:00Z",
                "producer_id": "fixture-controller", "run_id": str(uuid.uuid4()), "binding": binding,
                "pr_number": pr, "reviewed_head_sha": head, "base_sha": base, "merge_commit_sha": merge_sha,
                "merge_tree_sha": tree,
                "bound_files": [{"path": f"{branch}.txt", "blob_sha": blob, "sha256": hashlib.sha256(content).hexdigest()}],
                "gate_record_id": str(uuid.uuid4()), "review_record_ids": [str(uuid.uuid4())],
                "jira_transition_record_id": None, "evidence": ["urn:awf:fixture:example-evidence"]}

    def write_config(self, config):
        (self.root / ".agentic/PROJECT_CONFIG.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")

    def snapshot(self, now="2026-10-07T00:00:00Z", status=STATUS, closeouts=None):
        return build_snapshot(self.root, status=status, now=now, closeouts=closeouts)

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
        self.assertEqual(set(FIELDS), K15_FACTS)
        for key in sorted(K15_FACTS):
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
                             ("operating", "routes"), ("continuity", "completed_tickets")):
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
        # Semantically malformed values and timestamps must not be accepted, and in
        # particular must not compare equal to a current value of another type.
        self.assertIs(current["awf"]["integrity_valid"]["value"], False)
        invalid = {
            ("awf.integrity_valid", "invalid value"): lambda s: s["awf"]["integrity_valid"].update(value=0),
            ("repository.numeric_id", "invalid value"): lambda s: s["repository"]["numeric_id"].update(
                value="4242", basis="configured"),
            ("adoption.pr", "invalid value"): lambda s: s["adoption"]["pr"].update(value=True, basis="verified"),
            ("repository.head", "invalid value"): lambda s: s["repository"]["head"].update(value="HEAD"),
            ("operating.hash", "invalid value"): lambda s: s["operating"]["hash"].update(value="a" * 63, basis="verified"),
            ("external_resources", "invalid value"): lambda s: s["external_resources"].update(
                value={"readonly_data": True}, basis="configured"),
            ("blockers", "invalid value"): lambda s: s["blockers"].update(value=[{"code": "X"}]),
            ("jira.cloud_id", "invalid observed_at"): lambda s: s["jira"]["cloud_id"].update(observed_at="yesterday"),
            ("awf.version", "invalid observed_at"): lambda s: s["awf"]["version"].update(
                observed_at="2026-13-40T00:00:00Z"),
            ("generated_at", "invalid timestamp"): lambda s: s.update(generated_at="soon"),
            ("repository.extra", "unexpected"): lambda s: s["repository"].update(
                extra={"value": None, "basis": "unavailable", "observed_at": "2026-10-07T00:00:00Z"}),
            ("granted", "unexpected"): lambda s: s.update(granted=True),
        }
        for (field, problem), damage in invalid.items():
            received = deepcopy(complete)
            damage(received)
            with self.subTest(invalid=field):
                result = compare_snapshot(received, current)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn({"field": field, "problem": problem}, result["problems"])
        legacy = deepcopy(complete)
        for section, key in (("repository", "numeric_id"), ("awf", "trust_basis"), ("operating", "routes")):
            del legacy[section][key]
        legacy["awf"]["adoption"] = legacy.pop("adoption")["state"]
        result = compare_snapshot(legacy, current)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn({"field": "adoption.pr", "problem": "missing"}, result["problems"])
        self.assertEqual(compare_snapshot({k: v for k, v in complete.items() if k != "generated_at"},
                                          current)["status"], "REJECTED")

    def test_markdown_shows_every_fact_with_basis_and_observation_time(self):
        status, _operating = self.active_status()
        snapshot = self.snapshot(now="2026-10-07T01:02:03.456789Z", status=status)
        rows = {line.split(" | ")[0][2:]: line for line in render_markdown(snapshot).splitlines()
                if line.startswith("| ") and not line.startswith(("| Field", "| ---"))}
        self.assertEqual(set(rows), set(FIELDS) - {"blockers"})
        for key, line in rows.items():
            with self.subTest(field=key):
                self.assertTrue(line.endswith(" | 2026-10-07T01:02:03.456789Z |"), line)
        self.assertIn("## Blockers (verified, observed at 2026-10-07T01:02:03.456789Z)", render_markdown(snapshot))

    def test_compared_fields_are_cross_host_identities(self):
        # Completed tickets are not compared by value; they are re-verified against Git.
        self.assertEqual(set(FIELDS) - set(COMPARED),
                         {"repository.path", "blockers", "continuity.completed_tickets"})

    def test_completed_tickets_come_from_closeout_records_merged_into_head(self):
        first = self.completed_ticket("2001", pr=7)
        second = self.completed_ticket("2002", pr=9)
        snapshot = self.snapshot(closeouts=[second, first, first])
        self.assertEqual(snapshot["continuity"]["completed_tickets"], {
            "value": [{"issue_id": "2001", "pr": 7, "merge_commit": first["merge_commit_sha"]},
                      {"issue_id": "2002", "pr": 9, "merge_commit": second["merge_commit_sha"]}],
            "basis": "verified", "observed_at": "2026-10-07T00:00:00Z"})
        self.assertIn("| continuity.completed_tickets | [", render_markdown(snapshot))
        self.assertEqual(self.snapshot()["continuity"]["completed_tickets"],
                         {"value": None, "basis": "unavailable", "observed_at": "2026-10-07T00:00:00Z"})

    def test_unprovable_closeout_records_are_refused(self):
        other_repository = self.completed_ticket("2003", repository_id=101)
        with self.assertRaisesRegex(ValidationError, "bound to repository 101"):
            self.snapshot(closeouts=[other_repository])
        unmerged = self.completed_ticket("2004", merge=False)
        with self.assertRaisesRegex(ValidationError, "not in the history of HEAD"):
            self.snapshot(closeouts=[unmerged])
        tampered = self.completed_ticket("2005")
        tampered["bound_files"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            self.snapshot(closeouts=[tampered])
        with self.assertRaises(ValidationError):
            self.snapshot(closeouts=[{"schema_version": 3}])

    def test_receiving_host_reverifies_completed_tickets_against_its_history(self):
        record = self.completed_ticket("2001")
        received = self.snapshot(closeouts=[record])
        # The receiving checkout contains the merge: continuity holds without the records.
        self.assertEqual(compare_snapshot(received, self.snapshot("2026-10-07T06:00:00Z"))["status"], "MATCH")
        # A checkout whose history lacks the merge reports the ticket as drift.
        elsewhere = Path(self.temp.name) / "elsewhere"
        git(self.root, "clone", "-q", "--no-local", str(self.root), str(elsewhere))
        git(elsewhere, "reset", "-q", "--hard", "HEAD~1")
        receiving = build_snapshot(elsewhere, status=STATUS, now="2026-10-07T06:00:00Z")
        result = compare_snapshot(received, receiving)
        self.assertEqual(result["status"], "DRIFT")
        continuity = [item for item in result["drift"] if item["field"] == "continuity.completed_tickets"]
        self.assertEqual(len(continuity), 1)
        self.assertEqual(continuity[0]["received"],
                         [{"issue_id": "2001", "pr": 7, "merge_commit": record["merge_commit_sha"]}])
        damaged = deepcopy(received)
        damaged["continuity"]["completed_tickets"]["value"][0]["pr"] = 0
        result = compare_snapshot(damaged, self.snapshot("2026-10-07T06:00:00Z"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn({"field": "continuity.completed_tickets", "problem": "invalid value"}, result["problems"])

    def test_observation_times_must_be_consistent_with_generation_time(self):
        received = self.snapshot()
        current = self.snapshot("2026-10-07T06:00:00Z")
        later = deepcopy(received)
        later["repository"]["head"]["observed_at"] = "2026-10-07T00:00:01Z"
        result = compare_snapshot(later, current)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn({"field": "repository.head", "problem": "observed after generated_at"}, result["problems"])
        # An observation in the future of the receiving host, even if consistently dated.
        future = deepcopy(received)
        future["repository"]["head"]["observed_at"] = "2026-10-07T07:00:00Z"
        self.assertEqual(compare_snapshot(future, current)["status"], "REJECTED")
        future["generated_at"] = "2026-10-07T07:00:00Z"
        result = compare_snapshot(future, current)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn({"field": "generated_at", "problem": "future-dated"}, result["problems"])
        # A matching snapshot from the future of this host is still rejected, small clock skew is not.
        self.assertEqual(compare_snapshot(self.snapshot("2026-10-07T06:10:00Z"), current)["status"], "REJECTED")
        self.assertEqual(compare_snapshot(self.snapshot("2026-10-07T06:00:20Z"), current)["status"], "MATCH")

    def test_stale_snapshot_never_matches(self):
        received = self.snapshot()
        self.assertEqual(MAX_AGE_SECONDS, 86400)
        self.assertEqual(compare_snapshot(received, self.snapshot("2026-10-08T00:00:00Z"))["status"], "MATCH")
        result = compare_snapshot(received, self.snapshot("2026-10-08T00:00:01Z"))
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["drift"], [])
        self.assertIn("generated_at", result["stale"])
        self.assertIs(result["execution_authority"], False)
        # A recently generated snapshot that carries an old observation is stale too.
        old_fact = self.snapshot("2026-10-09T00:00:00Z")
        old_fact["repository"]["head"]["observed_at"] = "2026-10-07T00:00:00Z"
        result = compare_snapshot(old_fact, self.snapshot("2026-10-09T01:00:00Z"))
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["stale"], ["repository.head"])
        # A stricter limit can be requested.
        result = compare_snapshot(received, self.snapshot("2026-10-07T06:00:00Z"), max_age_seconds=3600)
        self.assertEqual(result["status"], "STALE")

    def test_snapshot_claiming_authority_or_unknown_schema_is_rejected(self):
        forged = self.snapshot()
        forged["merge_authority"] = True
        self.assertEqual(compare_snapshot(forged, self.snapshot())["status"], "REJECTED")
        self.assertEqual(compare_snapshot({"schema": "other"}, self.snapshot())["status"], "REJECTED")

    def test_safe_origin_strips_userinfo_query_and_fragment(self):
        self.assertEqual(safe_origin("ssh://git@example.invalid/o/r.git"), "ssh://example.invalid/o/r.git")
        self.assertEqual(safe_origin("git@example.invalid:o/r.git"), "git@example.invalid:o/r.git")
        self.assertIsNone(safe_origin(None))
        for url in ("https://example.invalid/o/r.git?access_token=SECRET",
                    "https://example.invalid/o/r.git#SECRET",
                    "https://x-access-token:SECRET@example.invalid/o/r.git?token=SECRET&a=b#frag",
                    "https://user:pa?ss#SECRET@example.invalid/o/r.git",
                    "https://example.invalid?private_token=SECRET"):
            with self.subTest(url=url):
                self.assertNotIn("SECRET", safe_origin(url))
                self.assertNotIn("?", safe_origin(url))
                self.assertNotIn("#", safe_origin(url))
        self.assertEqual(safe_origin("https://x-access-token:SECRET@example.invalid/o/r.git?token=SECRET#frag"),
                         "https://example.invalid/o/r.git")
        self.assertEqual(safe_origin("git@example.invalid:o/r.git?token=SECRET"), "git@example.invalid:o/r.git")

    def test_query_string_credentials_never_reach_the_snapshot_or_drift(self):
        git(self.root, "remote", "set-url", "origin",
            "https://example.invalid/o/r.git?access_token=SECRETTOKEN#SECRETFRAGMENT")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["repository"]["origin"]["value"], "https://example.invalid/o/r.git")
        self.assertNotIn("SECRET", json.dumps(snapshot) + render_markdown(snapshot))
        # A received snapshot carrying a credential-bearing origin is rejected, not echoed as drift.
        received = deepcopy(snapshot)
        received["repository"]["origin"]["value"] = "https://example.invalid/o/r.git?access_token=LEAKED"
        result = compare_snapshot(received, self.snapshot("2026-10-07T06:00:00Z"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn({"field": "repository.origin", "problem": "invalid value"}, result["problems"])
        self.assertNotIn("LEAKED", json.dumps(result))

    def test_cli_exports_completed_tickets_and_reports_stale_handoffs(self):
        record = self.completed_ticket("2001")
        record_path = Path(self.temp.name) / "closeout.json"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        exported = Path(self.temp.name) / "handoff.json"
        with mock.patch("agentic.providers.github_status.project_status", return_value=deepcopy(STATUS)), \
                mock.patch("sys.stdout"):
            self.assertEqual(cli.main(["--root", str(self.root), "handoff", "--json", "--output", str(exported),
                                       "--closeout", str(record_path)]), 0)
            snapshot = load(exported)
            self.assertEqual(snapshot["continuity"]["completed_tickets"]["value"][0]["issue_id"], "2001")
            self.assertEqual(cli.main(["--root", str(self.root), "doctor", "--handoff", str(exported)]), 0)
            stale = deepcopy(snapshot)
            for key in FIELDS:
                item = stale
                for part in key.split("."):
                    item = item[part]
                item["observed_at"] = "2020-01-01T00:00:00Z"
            stale["generated_at"] = "2020-01-01T00:00:00Z"
            stale_path = Path(self.temp.name) / "stale.json"
            stale_path.write_text(json.dumps(stale), encoding="utf-8")
            self.assertEqual(cli.main(["--root", str(self.root), "doctor", "--handoff", str(stale_path)]), 2)
            record["bound_files"][0]["sha256"] = "0" * 64
            record_path.write_text(json.dumps(record), encoding="utf-8")
            with mock.patch("sys.stderr"):
                self.assertEqual(cli.main(["--root", str(self.root), "handoff", "--json",
                                           "--closeout", str(record_path)]), 2)


if __name__ == "__main__":
    unittest.main()
