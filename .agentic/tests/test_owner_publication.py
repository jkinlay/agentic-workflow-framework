"""Owner-publication fixtures use local SQLite and inert provider callbacks only."""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from agentic import ValidationError
from agentic.canonical import sha256
from agentic.owner_publication import OwnerPublicationStore, resume_owner_publication
from test_publication_readiness import NOW, publication_config, publication_observation


class OwnerPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-owner-publication-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.worktree = self.root / "owner's worktree"
        self.worktree.mkdir()
        self.config = publication_config()
        self.request = {"format": "awf-owner-publication-1",
                        "owner_policy_evidence": "urn:awf:synthetic:owner-required-policy",
                        "worktree": str(self.worktree), "streams": []}
        for index, stream in enumerate("ABC", 1):
            self.request["streams"].append({"stream": stream, "ticket": "EX-" + str(index),
                "slug": "fixture", "branch": "codex/EX-" + str(index) + "-fixture", "head": str(index) * 40,
                "title": "Retained fixture " + stream, "body": "Exact retained body " + stream + "\n",
                "completed_result_sha256": str(index) * 64,
                "stream_state": {"lifecycle": "WORKER_COMPLETED", "worker_run": "fixture-" + stream,
                                 "tests": ["already completed"], "amendment_count": 2, "budget_used": 10}})
        self.store = OwnerPublicationStore(self.root / "handoff.sqlite", worktree_roots=[self.worktree])
        self.prepared = self.store.prepare(self.config, self.request, now=NOW)
        self.batch = self.prepared["batch_id"]
        self.calls = []

    def heads(self, snapshot):
        return {"source": "host_observation", "host": "https://github.com", "repository": "fixture/example",
                "repository_id": 101, "observed_at": NOW, "complete": True,
                "heads": {item["branch"]: item["head"] for item in snapshot["request"]["streams"]}}

    def receipt(self, payload):
        number = 17 + ord(payload["stream"]) - ord("A")
        return {"operation_id": payload["operation_id"], "repository_id": payload["repository_id"],
                "base_branch": payload["base_branch"], "branch": payload["branch"], "head": payload["head"],
                "body_sha256": sha256(payload["body"].encode()), "draft": True, "number": number,
                "url": "https://github.com/fixture/example/pull/" + str(number), "observed_at": NOW}

    def resume(self, **kwargs):
        adapters = {"observe_identity": lambda: publication_observation()["identity"],
                    "observe_remote_heads": self.heads,
                    "create_draft_pr": lambda payload: self.calls.append(payload),
                    "observe_draft_pr": self.receipt}
        adapters.update(kwargs)
        return resume_owner_publication(self.store, self.batch, self.config, now=NOW, **adapters)

    def test_one_bounded_command_uses_literal_paths_atomic_push_and_exact_completed_shas(self):
        command = self.prepared["command"]
        self.assertIn("owner''s worktree", command)
        self.assertIn("push --atomic", command)
        self.assertNotIn("--force", command)
        self.assertNotIn("\n", command)
        self.assertLessEqual(len(command), 8192)
        for item in self.request["streams"]:
            self.assertIn(item["head"] + ":refs/heads/" + item["branch"], command)
        self.assertFalse(self.prepared["execution_authority"])

    def test_restart_preserves_all_stream_state_and_resumes_only_draft_prs_once(self):
        original = copy.deepcopy(self.prepared["request"])
        self.store = OwnerPublicationStore(self.root / "handoff.sqlite", worktree_roots=[self.worktree])
        repeated = self.store.prepare(self.config, self.request, now=NOW)
        self.assertEqual(repeated, self.prepared)
        result = self.resume()
        self.assertTrue(result["complete"])
        self.assertEqual(result["request"], original)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([item["stream_state"] for item in self.calls],
                         [item["stream_state"] for item in self.request["streams"]])
        self.resume(create_draft_pr=lambda _: self.fail("completed publication was replayed"),
                    observe_remote_heads=lambda _: self.fail("complete snapshot needs no provider work"))

    def test_missing_foreign_stale_or_moved_remote_heads_never_create_a_pr(self):
        mutations = [lambda x: x["heads"].pop("codex/EX-1-fixture"),
                     lambda x: x["heads"].update({"codex/EX-1-fixture": "f" * 40}),
                     lambda x: x.update(repository_id=202), lambda x: x.update(repository="other/repository"),
                     lambda x: x.update(complete=False),
                     lambda x: x.update(observed_at="2026-10-02T09:59:59Z")]
        for mutate in mutations:
            observed = self.heads(self.prepared)
            mutate(observed)
            with self.subTest(observed=observed), self.assertRaises(ValidationError):
                self.resume(observe_remote_heads=lambda _: observed)
        self.assertEqual(self.calls, [])
        self.assertEqual({row["state"] for row in self.store.snapshot(self.batch, self.config)["operations"]},
                         {"WAITING_OWNER_PUSH"})

    def test_uncertain_create_is_observed_on_restart_without_retry_or_worker_rerun(self):
        def uncertain(payload):
            self.calls.append(payload)
            raise TimeoutError("synthetic uncertain provider outcome")
        first = self.resume(create_draft_pr=uncertain)
        self.assertEqual({row["state"] for row in first["operations"]}, {"PR_UNKNOWN"})
        self.store = OwnerPublicationStore(self.root / "handoff.sqlite", worktree_roots=[self.worktree])
        final = self.resume(create_draft_pr=lambda _: self.fail("uncertain PR creation was repeated"))
        self.assertTrue(final["complete"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(final["request"], self.request)

    def test_mismatched_readback_is_retained_unknown_until_exact_observation(self):
        def wrong(payload):
            return self.receipt(payload) | {"head": "f" * 40}
        first = self.resume(observe_draft_pr=wrong)
        self.assertFalse(first["complete"])
        self.assertEqual(len(first["errors"]), 3)
        second = self.resume(create_draft_pr=lambda _: self.fail("readback failure caused mutation replay"))
        self.assertTrue(second["complete"])
        self.assertEqual(len(self.calls), 3)

    def test_config_or_identity_drift_refuses_resumption(self):
        changed = copy.deepcopy(self.config)
        changed["github"]["repository_id"] = 202
        with self.assertRaisesRegex(ValidationError, "configuration changed"):
            self.store.snapshot(self.batch, changed)
        with self.assertRaises(ValidationError):
            self.resume(observe_identity=lambda: publication_observation()["identity"] | {"actor_id": 2002})
        self.assertEqual(self.calls, [])

    def test_interrupted_in_flight_operation_is_observed_without_reissuing(self):
        self.store.record_heads(self.batch, self.config, self.heads(self.prepared), now=NOW)
        self.assertTrue(self.store.begin_pr(self.batch, "A", now=NOW))
        self.assertFalse(self.store.begin_pr(self.batch, "A", now=NOW))
        self.store = OwnerPublicationStore(self.root / "handoff.sqlite", worktree_roots=[self.worktree])
        result = self.resume()
        self.assertTrue(result["complete"])
        self.assertEqual([payload["stream"] for payload in self.calls], ["B", "C"])

    def test_one_provider_pr_cannot_complete_multiple_streams(self):
        def duplicate(payload):
            return self.receipt(payload) | {"number": 17, "url": "https://github.com/fixture/example/pull/17"}
        result = self.resume(observe_draft_pr=duplicate)
        self.assertFalse(result["complete"])
        self.assertEqual([row["state"] for row in result["operations"]], ["PR_CREATED", "PR_UNKNOWN", "PR_UNKNOWN"])

    def test_prepare_and_status_cli_only_write_local_handoff_state(self):
        script = Path(__file__).resolve().parents[2] / ".agentic/scripts/continuous_controller.py"
        spec = importlib.util.spec_from_file_location("owner_publication_cli_fixture", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config_path, request_path = self.root / "config.json", self.root / "request.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        request_path.write_text(json.dumps(self.request), encoding="utf-8")
        common = ["--state", str(self.root / "cli.sqlite"), "--worktree-root", str(self.worktree),
                  "--project-config", str(config_path)]
        out = io.StringIO()
        with redirect_stdout(out):
            code = module.main([*common, "owner-publication-prepare", "--request", str(request_path), "--now", NOW])
        self.assertEqual(code, 0)
        prepared = json.loads(out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            code = module.main([*common, "owner-publication-status", "--batch", prepared["batch_id"]])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), prepared)

    def test_state_inside_worker_root_and_implicit_policy_fallback_are_refused(self):
        with self.assertRaisesRegex(ValidationError, "outside"):
            OwnerPublicationStore(self.worktree / "unsafe.sqlite", worktree_roots=[self.worktree])
        request = copy.deepcopy(self.request)
        request["owner_policy_evidence"] = None
        with self.assertRaisesRegex(ValidationError, "policy evidence"):
            self.store.prepare(self.config, request, now=NOW)

    def test_duplicate_stream_or_command_injection_fails_before_handoff(self):
        for mutation in (lambda r: r["streams"][1].update(stream="A"),
                         lambda r: r["streams"][0].update(branch="main"),
                         lambda r: r["streams"][0].update(head="HEAD; other-command"),
                         lambda r: r["streams"][0].update(body="noncanonical\r\n")):
            request = copy.deepcopy(self.request)
            mutation(request)
            with self.subTest(request=request), self.assertRaises(ValidationError):
                self.store.prepare(self.config, request, now=NOW)


if __name__ == "__main__":
    unittest.main()
