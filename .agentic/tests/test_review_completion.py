import copy
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from agentic import ValidationError
from agentic.authorization import make_request
from agentic.canonical import fingerprint, load
from agentic.contracts import Contracts
from agentic.cli import local_semantics
from agentic.gates import evaluate
from agentic.interaction import gate_handoff
from agentic.lifecycle import definition, transition
from agentic.review_completion import ReviewCompletionStore, review_authority_from_config
from agentic.providers.github_reviewer_removal import (approval_comment_body,
    github_reviewer_removal_observer)
from agentic.store import Store

ROOT = Path(__file__).resolve().parents[2]

def candidate(head="2" * 40, tree="3" * 40):
    return {
        "repository": "example/project",
        "base_sha": "1" * 40,
        "head_sha": head,
        "head_tree_sha": tree,
        "contract_sha256": "4" * 64,
        "review_input_sha256": fingerprint("review-input", {"verdict": "APPROVE"}),
    }


class ReviewCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-review-completion-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "review.sqlite3"
        self.store = ReviewCompletionStore(self.path)
        self.reviewers = ["reviewer-c", "reviewer-a", "reviewer-b"]
        self.current = candidate()
        config = copy.deepcopy(load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"))
        config["github"]["repository"] = self.current["repository"]
        self.authority = review_authority_from_config(config)

    @staticmethod
    def provider_review(request, *, artifact_id=None, observed_at=None):
        record = request["record"]
        return {
            "status": "APPROVED",
            "artifact_id": artifact_id or "provider-review-" + record["disposition_id"],
            "record_sha256": request["record_sha256"],
            "disposition_id": record["disposition_id"],
            "old_cycle_id": record["old_cycle_id"],
            "tuple_sha256": record["tuple_sha256"],
            "old_reviewer_set_sha256": record["old_reviewer_set_sha256"],
            "new_reviewer_set_sha256": record["new_reviewer_set_sha256"],
            "removed_reviewers": record["removed_reviewers"],
            "reason": record["reason"],
            "owner_actor_id": record["owner_actor_id"],
            "provider_identity": request["expected_provider_identity"],
            "observed_at": observed_at or record["issued_at"],
        }

    def removal_store(self, path, observer=None):
        return ReviewCompletionStore(
            path, authority=self.authority,
            provider_observer=observer or self.provider_review)

    def freeze_and_dispatch(self):
        frozen = self.store.freeze(self.current, self.reviewers)
        self.assertEqual(frozen["required_reviewers"], sorted(self.reviewers))
        return {reviewer: self.store.dispatch(reviewer, self.current, self.reviewers)
                for reviewer in self.reviewers}

    def acceptable(self, reviewer, binding):
        return self.store.record_result(reviewer, binding, "ACCEPTABLE",
                                        {"verdict": "APPROVE", "reviewer": reviewer,
                                         "findings": []})

    def removal_disposition(self, store, old, new, *, reason="Owner reviewed reviewer removal"):
        now = datetime.now(timezone.utc)
        issued = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires = (now + timedelta(minutes=4)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        old_reviewers, new_reviewers = sorted(old["required_reviewers"]), sorted(new)
        record = {
            "schema_version": 1,
            "disposition_id": str(uuid.uuid4()),
            "decision": "REMOVE_REQUIRED_REVIEWERS",
            "old_cycle_id": old["cycle_id"],
            "tuple": copy.deepcopy(self.current),
            "tuple_sha256": old["tuple_sha256"],
            "old_required_reviewers": old_reviewers,
            "old_reviewer_set_sha256": old["reviewer_set_sha256"],
            "new_required_reviewers": new_reviewers,
            "new_reviewer_set_sha256": fingerprint("reviewer-set", new_reviewers),
            "removed_reviewers": sorted(set(old_reviewers) - set(new_reviewers)),
            "reason": reason,
            "owner_actor_id": store.authority["trusted_owner_ids"][0],
            "provider_identity": store.authority["provider_identity"],
            "issued_at": issued,
            "expires_at": expires,
        }
        record_hash = fingerprint("reviewer-removal-disposition", record)
        return {"record": record, "record_sha256": record_hash}

    @staticmethod
    def receipt(admission):
        return {"status": "SUBMITTED", **admission["provider_preconditions"],
                "observed_at": admission["provider_preconditions"]["prepared_at"]}

    def test_incomplete_set_cannot_mutate_provider_and_reports_all_counts(self):
        bindings = self.freeze_and_dispatch()
        self.acceptable("reviewer-a", bindings["reviewer-a"])
        calls = []
        with self.assertRaisesRegex(ValidationError, "Every frozen independent reviewer"):
            self.store.submit(self.current, self.reviewers, {"verdict": "APPROVE"}, calls.append,
                              lambda admission, intent: self.receipt(admission))
        self.assertEqual(calls, [])
        counts = self.store.status(self.current, self.reviewers)["counts"]
        self.assertEqual(counts, {"required": 3, "completed": 1, "acceptable": 1,
                                  "failed": 0, "stale": 0, "outstanding": 2})

    def test_result_cannot_precede_persisted_dispatch(self):
        frozen = self.store.freeze(self.current, ["one"])
        forged = {"cycle_id": frozen["cycle_id"], "reviewer_id": "one",
                  "tuple_sha256": frozen["tuple_sha256"],
                  "reviewer_set_sha256": frozen["reviewer_set_sha256"]}
        with self.assertRaisesRegex(ValidationError, "persisted dispatch"):
            self.store.record_result("one", forged, "ACCEPTABLE",
                                     {"verdict": "APPROVE", "reviewer": "one", "findings": []})

    def test_failed_timeout_malformed_and_tuple_mismatch_never_complete(self):
        reviewers = ["failed", "timeout", "malformed", "stale"]
        self.store.freeze(self.current, reviewers)
        bindings = {name: self.store.dispatch(name, self.current, reviewers) for name in reviewers}
        self.store.record_result("failed", bindings["failed"], "FAILED", {"error": "review failed"})
        self.store.record_result("timeout", bindings["timeout"], "TIMED_OUT", {"seconds": 30})
        self.store.record_result("malformed", bindings["malformed"], "MALFORMED", {"reason": "invalid JSON envelope"})
        stale = dict(bindings["stale"])
        stale["tuple_sha256"] = "0" * 64
        self.store.record_result("stale", stale, "ACCEPTABLE",
                                 {"verdict": "APPROVE", "reviewer": "stale", "findings": []})
        summary = self.store.status(self.current, reviewers)
        self.assertFalse(summary["ready"])
        self.assertEqual(summary["counts"], {"required": 4, "completed": 4, "acceptable": 0,
                                             "failed": 3, "stale": 1, "outstanding": 0})

    def test_duplicate_terminal_result_invalidates_acceptance_without_replacing_first(self):
        reviewers = ["one"]
        self.store.freeze(self.current, reviewers)
        binding = self.store.dispatch("one", self.current, reviewers)
        self.store.record_result("one", binding, "ACCEPTABLE",
                                 {"verdict": "APPROVE", "reviewer": "one", "findings": [], "sequence": 1})
        duplicate = self.store.record_result("one", binding, "ACCEPTABLE",
                                             {"verdict": "APPROVE", "reviewer": "one", "findings": [], "sequence": 2})
        self.assertEqual(duplicate["reason"], "DUPLICATE")
        self.assertEqual(duplicate["summary"]["reviewers"], [{"reviewer_id": "one", "state": "DUPLICATE"}])
        self.assertEqual(duplicate["summary"]["counts"], {"required": 1, "completed": 1,
                                                            "acceptable": 0, "failed": 1,
                                                            "stale": 0, "outstanding": 0})
        with self.assertRaises(ValidationError):
            self.store.prepare_submission(self.current, reviewers, {"verdict": "APPROVE"})

    def test_candidate_or_reviewer_set_movement_invalidates_and_needs_fresh_cycle(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        moved = candidate(head="6" * 40, tree="7" * 40)
        status = self.store.status(moved, self.reviewers)
        self.assertEqual(status["state"], "INVALIDATED")
        self.assertFalse(status["current"])
        with self.assertRaisesRegex(ValidationError, "moved"):
            self.store.prepare_submission(moved, self.reviewers, {"verdict": "APPROVE"})
        fresh = self.store.freeze(moved, [*self.reviewers, "reviewer-d"])
        self.assertNotEqual(fresh["cycle_id"], status["cycle_id"])
        self.assertEqual(fresh["counts"]["outstanding"], 4)

    def test_dispatched_cycle_reviewer_cannot_be_silently_removed_in_any_state(self):
        for state in ("QUEUED", "RUNNING", "FAILED", "CANCELLED", "ACCEPTABLE"):
            with self.subTest(state=state):
                path = Path(self.temporary.name) / f"removal-{state}.sqlite3"
                store = self.removal_store(path)
                reviewers = ["drop", "keep"]
                frozen = store.freeze(self.current, reviewers)
                store.dispatch("keep", self.current, reviewers)
                if state != "QUEUED":
                    binding = store.dispatch("drop", self.current, reviewers)
                    if state not in {"RUNNING"}:
                        payload = ({"verdict": "APPROVE", "reviewer": "drop", "findings": []}
                                   if state == "ACCEPTABLE" else {"reason": state.lower()})
                        store.record_result("drop", binding, state, payload)
                with self.assertRaisesRegex(ValidationError, "reviewed owner disposition"):
                    store.freeze(self.current, ["keep"])
                self.assertEqual(store.status()["cycle_id"], frozen["cycle_id"])
                self.assertEqual(store.reviewer_set_audit(), [])

    def test_valid_reviewed_owner_disposition_allows_and_audits_exact_removal(self):
        store = self.removal_store(Path(self.temporary.name) / "authorized-removal.sqlite3")
        reviewers = ["drop", "keep"]
        old = store.freeze(self.current, reviewers)
        store.dispatch("keep", self.current, reviewers)
        drop = store.dispatch("drop", self.current, reviewers)
        store.record_result("drop", drop, "CANCELLED", {"reason": "reviewer unavailable"})
        disposition = self.removal_disposition(store, old, ["keep"])
        new = store.freeze(self.current, ["keep"], disposition)
        self.assertNotEqual(new["cycle_id"], old["cycle_id"])
        self.assertEqual(new["required_reviewers"], ["keep"])
        self.assertFalse(new["ready"])
        with self.assertRaisesRegex(ValidationError, "Every frozen independent reviewer"):
            store.prepare_submission(self.current, ["keep"], {"verdict": "APPROVE"})
        audit = store.reviewer_set_audit()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["old_cycle_id"], old["cycle_id"])
        self.assertEqual(audit[0]["new_cycle_id"], new["cycle_id"])
        self.assertEqual(audit[0]["record"]["removed_reviewers"], ["drop"])
        self.assertEqual(audit[0]["record_sha256"], disposition["record_sha256"])
        self.assertTrue(audit[0]["review"]["artifact_id"].startswith("provider-review-"))
        with store.transaction() as db:
            db.execute("UPDATE reviewer_set_dispositions SET record_json='{}' WHERE sequence=1")
        with self.assertRaisesRegex(ValidationError, "audit digest"):
            store.reviewer_set_audit()

    def test_reviewer_removal_disposition_is_exact_identity_bound_and_fresh(self):
        mutations = {
            "actor": lambda value: value["record"].__setitem__("owner_actor_id", 9999),
            "provider": lambda value: value["record"].__setitem__(
                "provider_identity", {"host": "https://attacker.invalid",
                                      "repository": "example/project", "repository_id": 101}),
            "tuple": lambda value: value["record"]["tuple"].__setitem__("head_sha", "9" * 40),
            "old cycle": lambda value: value["record"].__setitem__("old_cycle_id", str(uuid.uuid4())),
            "new set": lambda value: value["record"].__setitem__("new_required_reviewers", ["drop"]),
            "stale": lambda value: (value["record"].__setitem__("issued_at", "2000-01-01T00:00:00Z"),
                                     value["record"].__setitem__("expires_at", "2000-01-01T00:04:00Z"),
                                     None),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                path = Path(self.temporary.name) / (label.replace(" ", "-") + ".sqlite3")
                store = self.removal_store(path)
                old = store.freeze(self.current, ["drop", "keep"])
                store.dispatch("drop", self.current, ["drop", "keep"])
                disposition = self.removal_disposition(store, old, ["keep"])
                mutate(disposition)
                # Recompute the outer digest only for mutations whose threat is not an edited record.
                if label in {"actor", "tuple", "old cycle", "new set", "stale"}:
                    disposition["record_sha256"] = fingerprint(
                        "reviewer-removal-disposition", disposition["record"])
                with self.assertRaises(ValidationError):
                    store.freeze(self.current, ["keep"], disposition)
                self.assertEqual(store.status()["cycle_id"], old["cycle_id"])
                self.assertEqual(store.reviewer_set_audit(), [])

    def test_fabricated_removal_authority_observation_and_replay_fail_closed(self):
        def prepared(name, *, authority=None, observer=None):
            path = Path(self.temporary.name) / (name + ".sqlite3")
            store = ReviewCompletionStore(
                path, authority=authority or self.authority,
                provider_observer=observer or self.provider_review)
            old = store.freeze(self.current, ["drop", "keep"])
            store.dispatch("drop", self.current, ["drop", "keep"])
            return store, old, self.removal_disposition(store, old, ["keep"])

        calls = []
        store, old, disposition = prepared(
            "caller-review", observer=lambda request: calls.append(request))
        disposition["review"] = {
            "status": "APPROVED", "artifact_id": "fabricated-local-review"
        }
        with self.assertRaisesRegex(ValidationError, "exact record and digest"):
            store.freeze(self.current, ["keep"], disposition)
        self.assertEqual(calls, [])
        self.assertEqual(store.status()["cycle_id"], old["cycle_id"])
        self.assertEqual(store.reviewer_set_audit(), [])

        def fabricated_provider(request):
            review = self.provider_review(request)
            review["provider_identity"] = {
                "host": "https://attacker.invalid",
                "repository": "example/project",
                "repository_id": 101,
            }
            return review

        store, old, disposition = prepared("fabricated-provider", observer=fabricated_provider)
        with self.assertRaisesRegex(ValidationError, "exact disposition"):
            store.freeze(self.current, ["keep"], disposition)
        self.assertEqual(store.status()["cycle_id"], old["cycle_id"])
        self.assertEqual(store.reviewer_set_audit(), [])

        stale = "2000-01-01T00:00:01Z"
        store, old, disposition = prepared(
            "stale-observation", observer=lambda request: self.provider_review(
                request, observed_at=stale))
        with self.assertRaises(ValidationError):
            store.freeze(self.current, ["keep"], disposition)
        self.assertEqual(store.status()["cycle_id"], old["cycle_id"])
        self.assertEqual(store.reviewer_set_audit(), [])

        wrong_config = copy.deepcopy(load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"))
        wrong_config["github"]["repository"] = "other/project"
        wrong_authority = review_authority_from_config(wrong_config)
        store, old, disposition = prepared("config-mismatch", authority=wrong_authority)
        with self.assertRaisesRegex(ValidationError, "PROJECT_CONFIG"):
            store.freeze(self.current, ["keep"], disposition)
        self.assertEqual(store.status()["cycle_id"], old["cycle_id"])
        self.assertEqual(store.reviewer_set_audit(), [])

        reused_artifact = "provider-artifact-replay"
        observer = lambda request: self.provider_review(request, artifact_id=reused_artifact)
        path = Path(self.temporary.name) / "artifact-replay.sqlite3"
        store = self.removal_store(path, observer=observer)
        first = store.freeze(self.current, ["a", "b", "c"])
        store.dispatch("c", self.current, ["a", "b", "c"])
        second = store.freeze(
            self.current, ["a", "b"], self.removal_disposition(store, first, ["a", "b"]))
        store.dispatch("b", self.current, ["a", "b"])
        with self.assertRaisesRegex(ValidationError, "already used"):
            store.freeze(
                self.current, ["a"], self.removal_disposition(store, second, ["a"]))
        self.assertEqual(store.status()["cycle_id"], second["cycle_id"])
        self.assertEqual(len(store.reviewer_set_audit()), 1)

    def test_production_cli_has_no_caller_supplied_reviewer_removal_trust_roots(self):
        command = [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                   "--root", str(ROOT), "review-completion",
                   "--state", str(Path(self.temporary.name) / "forbidden.sqlite3"),
                   "--worktree-root", str(Path(self.temporary.name) / "candidate"),
                   "--trusted-owner-actor", "1001", "--provider-identity", "fabricated",
                   "recover"]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--state", completed.stderr)
        self.assertFalse((Path(self.temporary.name) / "forbidden.sqlite3").exists())

    def test_live_github_observer_binds_exact_owner_repository_artifact_and_time(self):
        artifact_id = 77

        def prepared(name, mutate=None):
            path = Path(self.temporary.name) / (name + ".sqlite3")
            store = ReviewCompletionStore(path, authority=self.authority)
            old = store.freeze(self.current, ["drop", "keep"])
            store.dispatch("drop", self.current, ["drop", "keep"])
            disposition = self.removal_disposition(store, old, ["keep"])
            request = {"record": copy.deepcopy(disposition["record"]),
                       "record_sha256": disposition["record_sha256"],
                       "expected_provider_identity": copy.deepcopy(
                           self.authority["provider_identity"])}
            created = disposition["record"]["issued_at"]
            metadata = {"id": 101, "full_name": "example/project"}
            comment = {
                "id": artifact_id,
                "node_id": "IC_kwDO_exact",
                "url": "https://api.github.com/repos/example/project/issues/comments/77",
                "issue_url": "https://api.github.com/repos/example/project/issues/31",
                "user": {"id": 1001},
                "body": approval_comment_body(request),
                "created_at": created,
                "updated_at": created,
            }
            if mutate:
                mutate(metadata, comment, disposition)

            def get_json(endpoint, deadline):
                self.assertGreater(deadline, 0)
                return metadata if endpoint == "repos/example/project" else comment

            observer = github_reviewer_removal_observer(
                self.authority, artifact_id, get_json=get_json,
                now=lambda: disposition["record"]["issued_at"])
            store.provider_observer = observer
            return store, old, disposition

        store, old, disposition = prepared("live-good")
        new = store.freeze(self.current, ["keep"], disposition)
        audit = store.reviewer_set_audit()
        self.assertNotEqual(new["cycle_id"], old["cycle_id"])
        self.assertEqual(len(audit), 1)
        review = audit[0]["review"]
        self.assertEqual(review["artifact_id"],
                         "github-issue-comment:101:77:IC_kwDO_exact")
        self.assertEqual(review["owner_actor_id"], 1001)
        self.assertEqual(review["provider_identity"], self.authority["provider_identity"])
        self.assertEqual(review["old_cycle_id"], old["cycle_id"])
        self.assertEqual(review["tuple_sha256"], old["tuple_sha256"])
        self.assertEqual(review["removed_reviewers"], ["drop"])
        self.assertEqual(review["reason"], disposition["record"]["reason"])

        corruptions = {
            "repository": lambda metadata, comment, value: metadata.__setitem__("id", 999),
            "actor": lambda metadata, comment, value: comment["user"].__setitem__("id", 999),
            "body": lambda metadata, comment, value: comment.__setitem__("body", "APPROVED"),
            "artifact": lambda metadata, comment, value: comment.__setitem__("id", 78),
            "edited": lambda metadata, comment, value: comment.__setitem__(
                "updated_at", value["record"]["expires_at"]),
            "stale": lambda metadata, comment, value: (
                comment.__setitem__("created_at", "2000-01-01T00:00:00Z"),
                comment.__setitem__("updated_at", "2000-01-01T00:00:00Z")),
        }
        for label, mutation in corruptions.items():
            with self.subTest(label=label):
                store, old, disposition = prepared("live-" + label, mutation)
                with self.assertRaises(ValidationError):
                    store.freeze(self.current, ["keep"], disposition)
                self.assertEqual(store.status()["cycle_id"], old["cycle_id"])
                self.assertEqual(store.reviewer_set_audit(), [])

    def test_production_review_completion_route_wires_live_observer(self):
        runtime = Path(self.temporary.name) / "runtime"
        (runtime / ".agentic").mkdir(parents=True)
        shutil.copyfile(ROOT / ".agentic/workflow.yaml", runtime / ".agentic/workflow.yaml")
        shutil.copytree(ROOT / ".agentic/schemas", runtime / ".agentic/schemas")
        config = copy.deepcopy(load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"))
        config["github"]["repository"] = self.current["repository"]
        (runtime / ".agentic/PROJECT_CONFIG.yaml").write_text(
            json.dumps(config), encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            "awf_review_completion_script", ROOT / ".agentic/scripts/review_completion.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = Path(self.temporary.name) / "production.sqlite3"
        worktree = Path(self.temporary.name) / "production-candidate"
        worktree.mkdir()
        candidate_path = Path(self.temporary.name) / "production-candidate.json"
        old_reviewers_path = Path(self.temporary.name) / "production-old-reviewers.json"
        new_reviewers_path = Path(self.temporary.name) / "production-new-reviewers.json"
        candidate_path.write_text(json.dumps(self.current), encoding="utf-8")
        old_reviewers_path.write_text(json.dumps(["drop", "keep"]), encoding="utf-8")
        new_reviewers_path.write_text(json.dumps(["keep"]), encoding="utf-8")
        prefix = ["--state", str(state), "--worktree-root", str(worktree)]

        def invoke(arguments, factory=lambda authority, artifact: None):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(module.main(arguments, default_root=runtime,
                                             observer_factory=factory), 0)
            return json.loads(output.getvalue())

        old = invoke([*prefix, "freeze", "--candidate", str(candidate_path),
                      "--reviewers", str(old_reviewers_path)])
        invoke([*prefix, "dispatch", "--candidate", str(candidate_path),
                "--reviewers", str(old_reviewers_path), "--reviewer", "drop"])
        store = ReviewCompletionStore(state, worktree_roots=[worktree],
                                      authority=self.authority)
        disposition = self.removal_disposition(store, old, ["keep"])
        disposition_path = Path(self.temporary.name) / "production-disposition.json"
        disposition_path.write_text(json.dumps(disposition), encoding="utf-8")
        observed = []

        def factory(authority, artifact):
            self.assertEqual(authority, self.authority)
            self.assertEqual(artifact, 77)
            def observer(request):
                observed.append(copy.deepcopy(request))
                review = self.provider_review(request)
                review["artifact_id"] = "github-issue-comment:101:77:IC_production"
                review["observed_at"] = request["record"]["issued_at"]
                return review
            return observer

        new = invoke([*prefix, "freeze", "--candidate", str(candidate_path),
                      "--reviewers", str(new_reviewers_path),
                      "--reviewer-removal-disposition", str(disposition_path),
                      "--reviewer-removal-artifact-id", "77"], factory)
        self.assertEqual(new["required_reviewers"], ["keep"])
        self.assertEqual(len(observed), 1)
        audit = ReviewCompletionStore(state, worktree_roots=[worktree]).reviewer_set_audit()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["review"]["artifact_id"],
                         "github-issue-comment:101:77:IC_production")
        self.assertEqual(audit[0]["record"]["old_cycle_id"], old["cycle_id"])

    def test_production_route_rejects_alternate_root_and_authority_config_drift_atomically(self):
        runtime = Path(self.temporary.name) / "authority-runtime"
        alternate = Path(self.temporary.name) / "alternate-runtime"
        for root in (runtime, alternate):
            (root / ".agentic").mkdir(parents=True)
            shutil.copyfile(ROOT / ".agentic/workflow.yaml", root / ".agentic/workflow.yaml")
            shutil.copytree(ROOT / ".agentic/schemas", root / ".agentic/schemas")
        config = copy.deepcopy(load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"))
        config["github"]["repository"] = self.current["repository"]
        for root in (runtime, alternate):
            (root / ".agentic/PROJECT_CONFIG.yaml").write_text(
                json.dumps(config), encoding="utf-8")

        spec = importlib.util.spec_from_file_location(
            "awf_review_completion_authority_script",
            ROOT / ".agentic/scripts/review_completion.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = Path(self.temporary.name) / "authority-production.sqlite3"
        worktree = Path(self.temporary.name) / "authority-candidate"
        worktree.mkdir()
        candidate_path = Path(self.temporary.name) / "authority-candidate.json"
        old_reviewers_path = Path(self.temporary.name) / "authority-old-reviewers.json"
        new_reviewers_path = Path(self.temporary.name) / "authority-new-reviewers.json"
        disposition_path = Path(self.temporary.name) / "authority-disposition.json"
        candidate_path.write_text(json.dumps(self.current), encoding="utf-8")
        old_reviewers_path.write_text(json.dumps(["drop", "keep"]), encoding="utf-8")
        new_reviewers_path.write_text(json.dumps(["keep"]), encoding="utf-8")
        prefix = ["--state", str(state), "--worktree-root", str(worktree)]

        def invoke(root, arguments, factory=lambda authority, artifact: None):
            output = io.StringIO()
            with redirect_stdout(output):
                module.main(arguments, default_root=root, observer_factory=factory)
            return json.loads(output.getvalue())

        old = invoke(runtime, [*prefix, "freeze", "--candidate", str(candidate_path),
                               "--reviewers", str(old_reviewers_path)])
        invoke(runtime, [*prefix, "dispatch", "--candidate", str(candidate_path),
                         "--reviewers", str(old_reviewers_path), "--reviewer", "drop"])
        store = ReviewCompletionStore(state, worktree_roots=[worktree],
                                      authority=self.authority)
        disposition = self.removal_disposition(store, old, ["keep"])
        disposition_path.write_text(json.dumps(disposition), encoding="utf-8")
        observed = []

        def factory(authority, artifact):
            def observer(request):
                observed.append(copy.deepcopy(request))
                return self.provider_review(request)
            return observer

        removal = [*prefix, "freeze", "--candidate", str(candidate_path),
                   "--reviewers", str(new_reviewers_path),
                   "--reviewer-removal-disposition", str(disposition_path),
                   "--reviewer-removal-artifact-id", "77"]
        with self.assertRaisesRegex(ValidationError, "root or accepted configuration changed"):
            invoke(alternate, removal, factory)
        self.assertEqual(observed, [])
        self.assertEqual(ReviewCompletionStore(state).status()["cycle_id"], old["cycle_id"])
        self.assertEqual(ReviewCompletionStore(state).reviewer_set_audit(), [])

        drifted = copy.deepcopy(config)
        drifted["merge_gate"]["trusted_owner_ids"] = [9999]
        (runtime / ".agentic/PROJECT_CONFIG.yaml").write_text(
            json.dumps(drifted), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "root or accepted configuration changed"):
            invoke(runtime, removal, factory)
        self.assertEqual(observed, [])
        self.assertEqual(ReviewCompletionStore(state).status()["cycle_id"], old["cycle_id"])
        self.assertEqual(ReviewCompletionStore(state).reviewer_set_audit(), [])

        (runtime / ".agentic/PROJECT_CONFIG.yaml").write_text(
            json.dumps(config), encoding="utf-8")
        new = invoke(runtime, removal, factory)
        self.assertEqual(new["required_reviewers"], ["keep"])
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(ReviewCompletionStore(state).reviewer_set_audit()), 1)

    def test_equal_and_superset_freezes_never_weaken_dispatched_requirements(self):
        store = self.removal_store(Path(self.temporary.name) / "monotonic.sqlite3")
        old = store.freeze(self.current, ["a", "b"])
        store.dispatch("a", self.current, ["a", "b"])
        equal = store.freeze(self.current, ["b", "a"])
        self.assertEqual(equal["cycle_id"], old["cycle_id"])
        superset = store.freeze(self.current, ["a", "b", "c"])
        self.assertNotEqual(superset["cycle_id"], old["cycle_id"])
        self.assertEqual(superset["required_reviewers"], ["a", "b", "c"])
        self.assertEqual(superset["counts"]["outstanding"], 3)
        self.assertEqual(store.reviewer_set_audit(), [])

    def test_restart_preserves_frozen_set_and_each_result_state(self):
        bindings = self.freeze_and_dispatch()
        self.acceptable("reviewer-a", bindings["reviewer-a"])
        self.store.record_result("reviewer-b", bindings["reviewer-b"], "FAILED", {"error": "finding"})
        restarted = ReviewCompletionStore(self.path)
        status = restarted.recover()
        self.assertEqual(status["required_reviewers"], sorted(self.reviewers))
        self.assertEqual(status["counts"], {"required": 3, "completed": 2, "acceptable": 1,
                                            "failed": 1, "stale": 0, "outstanding": 1})
        self.assertEqual({item["reviewer_id"]: item["state"] for item in status["reviewers"]},
                         {"reviewer-a": "ACCEPTABLE", "reviewer-b": "FAILED", "reviewer-c": "RUNNING"})

    def test_restart_during_provider_submission_is_uncertain_and_never_replayed(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        admission = self.store.prepare_submission(self.current, self.reviewers, {"verdict": "APPROVE"})
        restarted = ReviewCompletionStore(self.path)
        self.assertEqual(restarted.recover()["state"], "SUBMISSION_UNKNOWN")
        self.assertEqual(restarted.submitted()["aggregate"], {"verdict": "APPROVE"})
        with self.assertRaises(ValidationError):
            restarted.complete_submission(admission, {"provider": "late"})

    def test_late_concurrent_result_cannot_mutate_submitted_verdict(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        entered = threading.Event()
        release = threading.Event()
        outcome = {}

        def provider(admission):
            outcome["admission"] = admission
            entered.set()
            self.assertTrue(release.wait(5))
            return {"provider_status": "submitted", "verdict": admission["aggregate"]["verdict"]}

        def submit():
            outcome["summary"] = self.store.submit(
                self.current, self.reviewers, {"verdict": "APPROVE"}, provider,
                lambda admission, intent: self.receipt(admission))

        thread = threading.Thread(target=submit)
        thread.start()
        self.assertTrue(entered.wait(5))
        late = self.store.record_result("reviewer-a", bindings["reviewer-a"], "FAILED", {"error": "late"})
        self.assertEqual(late["reason"], "LATE")
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome["summary"]["state"], "SUBMITTED")
        self.assertEqual(self.store.submitted()["aggregate"], {"verdict": "APPROVE"})

    def test_two_concurrent_submitters_admit_only_one_provider_mutation(self):
        bindings = self.freeze_and_dispatch()
        for reviewer, binding in bindings.items():
            self.acceptable(reviewer, binding)
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def provider(admission):
            calls.append(admission["submission_id"])
            entered.set()
            self.assertTrue(release.wait(5))
            return {"ok": True}

        first = threading.Thread(target=lambda: self.store.submit(
            self.current, self.reviewers, {"verdict": "APPROVE"}, provider,
            lambda admission, intent: self.receipt(admission)))
        first.start()
        self.assertTrue(entered.wait(5))
        with self.assertRaisesRegex(ValidationError, "already prepared"):
            self.store.submit(self.current, self.reviewers, {"verdict": "APPROVE"}, provider,
                              lambda admission, intent: self.receipt(admission))
        release.set()
        first.join(5)
        self.assertEqual(len(calls), 1)

    def test_generated_status_and_submission_contracts_accept_runtime_records(self):
        reviewers = ["one"]
        self.store.freeze(self.current, reviewers)
        binding = self.store.dispatch("one", self.current, reviewers)
        self.acceptable("one", binding)
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("review-completion", self.store.status(self.current, reviewers))
        with self.assertRaisesRegex(ValidationError, "review-input digest"):
            self.store.prepare_submission(self.current, reviewers, {"verdict": "REQUEST_CHANGES"})
        admission = self.store.prepare_submission(self.current, reviewers, {"verdict": "APPROVE"})
        contracts.validate("review-submission", admission)
        local_semantics("review-submission", admission)
        forged = copy.deepcopy(admission)
        forged["completion_snapshot"]["counts"]["acceptable"] = 0
        with self.assertRaisesRegex(ValidationError, "counts"):
            local_semantics("review-submission", forged)

    def test_transport_cannot_assert_acceptable_against_result(self):
        for result, message in [
            ({"verdict": "REQUEST_CHANGES", "reviewer": "one", "findings": []}, "APPROVE"),
            ({"verdict": "APPROVE", "reviewer": "other", "findings": []}, "identity"),
            ({"verdict": "APPROVE", "reviewer": "one",
              "findings": [{"id": "F1", "status": "OPEN"}]}, "unresolved"),
        ]:
            with self.subTest(result=result):
                path = Path(self.temporary.name) / (str(len(message)) + message + ".sqlite3")
                store = ReviewCompletionStore(path)
                store.freeze(self.current, ["one"])
                binding = store.dispatch("one", self.current, ["one"])
                with self.assertRaisesRegex(ValidationError, message):
                    store.record_result("one", binding, "ACCEPTABLE", result)

    def test_provider_receipt_must_be_fresh_observation_bound_to_exact_tuple(self):
        self.store.freeze(self.current, ["one"])
        dispatch = self.store.dispatch("one", self.current, ["one"])
        self.acceptable("one", dispatch)
        admission = self.store.prepare_submission(self.current, ["one"], {"verdict": "APPROVE"})
        forged = self.receipt(admission)
        forged["head_sha"] = "9" * 40
        with self.assertRaisesRegex(ValidationError, "head_sha"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["operation_id"] = str(uuid.UUID(int=9))
        with self.assertRaisesRegex(ValidationError, "operation_id"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["completion_snapshot_sha256"] = "8" * 64
        with self.assertRaisesRegex(ValidationError, "completion_snapshot_sha256"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["aggregate_sha256"] = "7" * 64
        with self.assertRaisesRegex(ValidationError, "aggregate_sha256"):
            self.store.complete_submission(admission, forged)
        forged = self.receipt(admission)
        forged["observed_at"] = "1970-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValidationError, "predates"):
            self.store.complete_submission(admission, forged)
        self.store.mark_submission_unknown(admission["submission_id"], "provider observation mismatch")
        self.assertEqual(self.store.reconcile_submission(admission, self.receipt(admission))["state"],
                         "SUBMITTED")

    def test_altered_caller_admission_cannot_redirect_complete_or_reconcile(self):
        self.store.freeze(self.current, ["one"])
        dispatch = self.store.dispatch("one", self.current, ["one"])
        self.acceptable("one", dispatch)
        admission = self.store.prepare_submission(self.current, ["one"], {"verdict": "APPROVE"})

        def changed_identity(value):
            value["submission_id"] = str(uuid.UUID(int=44))
            value["provider_preconditions"]["operation_id"] = value["submission_id"]

        def changed_time(value):
            value["provider_preconditions"]["prepared_at"] = "2030-01-01T00:00:00Z"

        def changed_tuple(value):
            snapshot = value["completion_snapshot"]
            snapshot["tuple"]["repository"] = "other/project"
            snapshot["tuple_sha256"] = fingerprint("review-tuple", snapshot["tuple"])
            value["provider_preconditions"]["repository"] = "other/project"
            value["provider_preconditions"]["tuple_sha256"] = snapshot["tuple_sha256"]
            value["completion_snapshot_sha256"] = fingerprint("review-completion", snapshot)
            value["provider_preconditions"]["completion_snapshot_sha256"] = value["completion_snapshot_sha256"]

        def changed_reviewers(value):
            snapshot = value["completion_snapshot"]
            snapshot["required_reviewers"].append("reviewer-z")
            snapshot["results"].append({"reviewer_id": "reviewer-z", "state": "ACCEPTABLE",
                "result_sha256": "9" * 64, "terminal_at": snapshot["results"][0]["terminal_at"]})
            snapshot["counts"].update(required=2, completed=2, acceptable=2)
            snapshot["reviewer_set_sha256"] = fingerprint("reviewer-set", snapshot["required_reviewers"])
            value["provider_preconditions"]["reviewer_set_sha256"] = snapshot["reviewer_set_sha256"]
            value["completion_snapshot_sha256"] = fingerprint("review-completion", snapshot)
            value["provider_preconditions"]["completion_snapshot_sha256"] = value["completion_snapshot_sha256"]

        def changed_aggregate(value):
            value["aggregate"] = {"verdict": "APPROVE", "redirected": True}
            value["aggregate_sha256"] = fingerprint("review-aggregate", value["aggregate"])
            value["provider_preconditions"]["aggregate_sha256"] = value["aggregate_sha256"]

        mutations = (changed_identity, changed_time, changed_tuple, changed_reviewers, changed_aggregate)
        for mutation in mutations:
            with self.subTest(route="complete", mutation=mutation.__name__):
                forged = copy.deepcopy(admission)
                mutation(forged)
                from agentic.review_completion import validate_submission_semantics
                validate_submission_semantics(forged)
                with self.assertRaisesRegex(ValidationError, "immutable durable admission"):
                    self.store.complete_submission(forged, self.receipt(forged))
        self.store.mark_submission_unknown(admission["submission_id"], "provider completion unavailable")
        for mutation in mutations:
            with self.subTest(route="reconcile", mutation=mutation.__name__):
                forged = copy.deepcopy(admission)
                mutation(forged)
                with self.assertRaisesRegex(ValidationError, "immutable durable admission"):
                    self.store.reconcile_submission(forged, self.receipt(forged))
        self.assertEqual(self.store.reconcile_submission(admission, self.receipt(admission))["state"],
                         "SUBMITTED")

    def test_production_workflow_cli_exposes_review_completion_barrier(self):
        root = Path(self.temporary.name)
        runtime = root / "runtime"
        (runtime / ".agentic").mkdir(parents=True)
        shutil.copyfile(ROOT / ".agentic/workflow.yaml", runtime / ".agentic/workflow.yaml")
        shutil.copytree(ROOT / ".agentic/schemas", runtime / ".agentic/schemas")
        config = copy.deepcopy(load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"))
        config["github"]["repository"] = self.current["repository"]
        (runtime / ".agentic/PROJECT_CONFIG.yaml").write_text(
            json.dumps(config), encoding="utf-8")
        worktree = root / "candidate"
        worktree.mkdir()
        state_path = root / "review.sqlite3"
        candidate_path, reviewers_path = root / "candidate.json", root / "reviewers.json"
        candidate_path.write_text(json.dumps(self.current), encoding="utf-8")
        reviewers_path.write_text(json.dumps(["one"]), encoding="utf-8")
        prefix = [sys.executable, "-B", str(ROOT / ".agentic/scripts/workflow.py"),
                  "--root", str(runtime), "review-completion", "--state", str(state_path),
                  "--worktree-root", str(worktree)]
        command = [*prefix, "freeze", "--candidate", str(candidate_path),
                   "--reviewers", str(reviewers_path)]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["counts"]["outstanding"], 1)

        cli_store = ReviewCompletionStore(state_path, worktree_roots=[worktree])
        binding = cli_store.dispatch("one", self.current, ["one"])
        cli_store.record_result("one", binding, "ACCEPTABLE",
                                {"verdict": "APPROVE", "reviewer": "one", "findings": []})
        admission = cli_store.prepare_submission(self.current, ["one"], {"verdict": "APPROVE"})
        forged = copy.deepcopy(admission)
        forged["provider_preconditions"]["repository"] = "other/project"
        admission_path, receipt_path = root / "forged-admission.json", root / "forged-receipt.json"
        admission_path.write_text(json.dumps(forged), encoding="utf-8")
        receipt_path.write_text(json.dumps(self.receipt(forged)), encoding="utf-8")
        completed = subprocess.run([*prefix, "complete", "--admission", str(admission_path),
                                    "--receipt", str(receipt_path)],
                                   cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("repository", completed.stderr)
        self.assertEqual(cli_store.submitted()["state"], "SUBMITTING")
        cli_store.mark_submission_unknown(admission["submission_id"], "CLI completion rejected")
        completed = subprocess.run([*prefix, "reconcile", "--admission", str(admission_path),
                                    "--receipt", str(receipt_path)],
                                   cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("repository", completed.stderr)
        self.assertEqual(cli_store.submitted()["state"], "SUBMISSION_UNKNOWN")

    def test_review_state_is_rejected_inside_worktree_or_through_hardlink(self):
        root = Path(self.temporary.name)
        worktree = root / "reviewer-worktree"
        worktree.mkdir()
        with self.assertRaisesRegex(ValidationError, "outside"):
            ReviewCompletionStore(worktree / "state.sqlite3", worktree_roots=[worktree])
        linked = root / "linked.sqlite3"
        os.link(self.path, linked)
        with self.assertRaisesRegex(ValidationError, "hardlinks"):
            ReviewCompletionStore(linked)

    def test_old_gate_authorization_interaction_and_lifecycle_routes_cannot_bypass_barrier(self):
        contracts = Contracts(ROOT / ".agentic/schemas")
        config = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
        bundle = load(ROOT / ".agentic/examples/evidence-bundle.json")
        gate = evaluate(config, definition(), bundle, contracts, "2026-09-09T12:00:00Z")
        request = make_request(gate, contracts, "2026-09-09T12:00:00Z")
        gate_handoff(gate, request, config, contracts, "2026-09-09T12:00:00Z")
        facts = {"derived_gate_ready": True, "requirements_current": True,
                 "final_gate_current": True, "final_gate": gate}
        self.assertEqual(transition("FINAL_REVIEW", "FINAL_GATE_PASSED", facts),
                         "READY_FOR_OWNER_AUTHORIZATION")

        missing = copy.deepcopy(bundle)
        del missing["review_submission"]
        with self.assertRaises(ValidationError):
            evaluate(config, definition(), missing, contracts, "2026-09-09T12:00:00Z")
        old_gate = copy.deepcopy(gate)
        del old_gate["review_submission"]
        with self.assertRaises(ValidationError):
            make_request(old_gate, contracts, "2026-09-09T12:00:00Z")
        with self.assertRaises(ValidationError):
            gate_handoff(old_gate, request, config, contracts, "2026-09-09T12:00:00Z")
        with self.assertRaises(ValidationError):
            transition("FINAL_REVIEW", "FINAL_GATE_PASSED",
                       {"derived_gate_ready": True, "requirements_current": True,
                        "final_gate_current": True})

        state = Path(self.temporary.name) / "coordinator.sqlite3"
        store = Store(state, config["project"]["id"])
        with store.connection() as db:
            db.execute("INSERT INTO tickets VALUES('EX-1','FINAL_REVIEW',0)")
        with self.assertRaises(ValidationError):
            store.advance("EX-1", "FINAL_GATE_PASSED", 0,
                          {"derived_gate_ready": True, "requirements_current": True,
                           "final_gate_current": True})
        self.assertEqual(store.advance("EX-1", "FINAL_GATE_PASSED", 0, facts)[0],
                         "READY_FOR_OWNER_AUTHORIZATION")


if __name__ == "__main__":
    unittest.main()
