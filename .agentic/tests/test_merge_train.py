from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint


NOW = "2026-10-16T18:00:00Z"


def merge_train():
    return importlib.import_module("agentic.merge_train")


class MergeTrainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-train-test-")
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self._git("init", "--initial-branch=main")
        self._git("config", "user.name", "AWF Fixture")
        self._git("config", "user.email", "fixture@example.invalid")
        self.target = self._commit("src/base.txt", "base\n", "base")
        self.head1 = self._commit("src/one.txt", "one\n", "one")
        self.head2 = self._commit("src/two.txt", "two\n", "two")
        self.tree1 = self._git("rev-parse", f"{self.head1}^{{tree}}")
        self.tree2 = self._git("rev-parse", f"{self.head2}^{{tree}}")
        self.observer_registry = {}
        self.observer_count = 0
        self.provider_identity = {
            "host": "https://example.invalid",
            "repository": "owner/repository",
            "repository_id": 42,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ["git", *arguments], cwd=self.repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def _commit(self, path, content, message):
        destination = self.repo / Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
        self._git("add", "--", path)
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _source(self, pr=11, *, head=None, tree=None, depends=(), commits=None,
                owner_required=True):
        return {
            "pr_number": pr,
            "ticket": f"AWF-{pr}",
            "head_sha": head or self.head1,
            "tree_sha": tree or self.tree1,
            "base_branch": "main",
            "base_sha": self.target,
            "depends_on": list(depends),
            "commit_range": list(commits or [head or self.head1]),
            "risk_tier": 3 if owner_required else 1,
            "normal_final_gate_requires_owner_review": owner_required,
        }

    def _evidence(self, source, *, observed_at=NOW, draft=False,
                  head=None, base_branch=None, owner=True,
                  reviewer_id="owner-1"):
        head = head or source["head_sha"]
        base_branch = base_branch or source["base_branch"]
        digest = lambda name: hashlib.sha256(
            f"{name}:{source['pr_number']}:{head}".encode("ascii")
        ).hexdigest()
        final_gate = {
            "status": "PASS",
            "pr_number": source["pr_number"],
            "head_sha": head,
            "tree_sha": source["tree_sha"],
            "base_branch": base_branch,
            "risk_tier": source["risk_tier"],
            "owner_review_required": source[
                "normal_final_gate_requires_owner_review"],
            "completed_at": observed_at,
            "evidence_sha256": digest("gate"),
        }
        evidence = {
            "observed_at": observed_at,
            "source_state": {
                "pr_number": source["pr_number"],
                "head_sha": head,
                "tree_sha": source["tree_sha"],
                "base_branch": base_branch,
                "draft": draft,
            },
            "final_gate": final_gate,
            "critic": {
                "status": "PASS", "head_sha": head,
                "evidence_sha256": digest("critic"),
            },
            "ci": {
                "status": "PASS", "head_sha": head,
                "evidence_sha256": digest("ci"),
            },
            "provenance": {
                "status": "PASS", "base_sha": source["base_sha"],
                "head_sha": head, "evidence_sha256": digest("provenance"),
            },
            "owner_review": None,
        }
        if owner:
            evidence["owner_review"] = {
                "status": "APPROVED",
                "pr_number": source["pr_number"],
                "head_sha": head,
                "reviewer_id": reviewer_id,
                "submitted_at": observed_at,
                "observed_at": observed_at,
                "provider_artifact_id": f"review-{source['pr_number']}",
                "evidence_sha256": digest("owner-review"),
            }
        return evidence

    def _registration(self, source, evidence,
                      observer_id="fixture-observer", target_branch="main",
                      provider_identity=None,
                      expected_provider_identity=None):
        module = merge_train()
        provider_identity = copy.deepcopy(
            provider_identity or self.provider_identity)
        expected_provider_identity = copy.deepcopy(
            expected_provider_identity or provider_identity)
        request = module.source_admission_request(
            source, target_branch, expected_provider_identity)
        receipts = {}

        def observer(value):
            result = copy.deepcopy(evidence)
            request_sha = fingerprint("merge_train_provider_request", value)
            material = {
                "producer_id": observer_id,
                "observed_at": result["observed_at"],
                "request_sha256": request_sha,
                "result": result,
            }
            observation = {
                "status": "OBSERVED",
                "producer_id": observer_id,
                "observation_id": fingerprint(
                    "merge_train_provider_observation", material),
                "observed_at": result["observed_at"],
                "request_sha256": request_sha,
                "result": result,
            }
            receipt = module.provider_receipt_sha256(
                registration, value, observation)
            receipts[observation["observation_id"]] = receipt
            return observation

        registration = {
            "producer_kind": "merge_train_observer",
            "implementation_sha256": "a" * 64,
            "release_sha256": "b" * 64,
            "capabilities": ["SOURCE_ADMISSION", "BATCH_PARENT_PROVENANCE"],
            "provider_identity": provider_identity,
            "receipt_namespace": "awf-merge-train-observer-v1",
            "observer": observer,
            "receipts": receipts,
        }
        return request, registration

    def _admit(self, source=None, evidence=None, *, target_branch="main",
               provider_identity=None, expected_provider_identity=None,
               trusted_owner_ids=None):
        module = merge_train()
        source = source or self._source()
        evidence = evidence or self._evidence(source)
        expected_provider_identity = copy.deepcopy(
            expected_provider_identity or provider_identity
            or self.provider_identity)
        self.observer_count += 1
        observer_id = f"fixture-observer-{self.observer_count}"
        request, registration = self._registration(
            source, evidence, observer_id=observer_id,
            target_branch=target_branch,
            provider_identity=provider_identity,
            expected_provider_identity=expected_provider_identity)
        self.observer_registry[observer_id] = registration
        return module.admit_source(
            source, target_branch, expected_provider_identity, observer_id,
            self.observer_registry, now=NOW,
            trusted_owner_ids=(trusted_owner_ids
                               if trusted_owner_ids is not None
                               else ["owner-1"]),
        )

    def test_default_is_on_for_adopters_and_off_for_awf_repository(self):
        module = merge_train()
        self.assertTrue(module.default_enabled(is_awf_repository=False))
        self.assertFalse(module.default_enabled(is_awf_repository=True))

    def test_fresh_exact_head_owner_review_admits_required_source_only(self):
        admitted = self._admit()
        self.assertEqual("ADMITTED", admitted["status"])
        self.assertEqual("SOURCE_ADMISSION_ONLY", admitted["authorization_scope"])
        self.assertFalse(admitted["batch_merge_authorized"])
        self.assertEqual(
            self.provider_identity,
            admitted["provider_admission"]["request"]["provider_identity"])

    def test_numeric_provider_owner_id_admits_required_source(self):
        module = merge_train()
        source = self._source()
        admitted = self._admit(
            source, self._evidence(source, reviewer_id=54314148),
            trusted_owner_ids=[54314148])
        self.assertEqual("ADMITTED", admitted["status"])
        self.assertEqual("SOURCE_ADMISSION_ONLY",
                         admitted["authorization_scope"])
        self.assertEqual([admitted], module.order_sources(
            [admitted], self.provider_identity, self.observer_registry,
            now=NOW, trusted_owner_ids=[54314148]))

    def test_numeric_provider_owner_identity_boundary(self):
        source = self._source()
        evidence = self._evidence(source, reviewer_id=54314148)
        self.assertEqual("ADMITTED", self._admit(
            source, evidence, trusted_owner_ids=[54314148])["status"])

        for invalid_id in (0, -1, True):
            with self.subTest(trusted_owner_id=invalid_id):
                with self.assertRaisesRegex(
                        ValidationError, "identity collection"):
                    self._admit(
                        source, evidence, trusted_owner_ids=[invalid_id])

        for invalid_id in (0, -1, True):
            with self.subTest(reviewer_id=invalid_id):
                invalid_evidence = self._evidence(
                    source, reviewer_id=invalid_id)
                with self.assertRaisesRegex(
                        ValidationError, "owner review actor"):
                    self._admit(
                        source, invalid_evidence,
                        trusted_owner_ids=[54314148])

        with self.assertRaisesRegex(ValidationError, "owner review actor"):
            self._admit(
                source, self._evidence(source, reviewer_id=54314149),
                trusted_owner_ids=[54314148])

    def test_admission_rejects_observer_from_different_repository(self):
        module = merge_train()
        source = self._source()
        observer_id = "wrong-repository-observer"
        _, registration = self._registration(
            source, self._evidence(source), observer_id=observer_id,
            provider_identity={
                "host": "https://example.invalid",
                "repository": "owner/other-repository",
                "repository_id": 84,
            },
            expected_provider_identity=self.provider_identity)
        registry = {observer_id: registration}
        with self.assertRaisesRegex(ValidationError, "provider identity"):
            module.admit_source(
                source, "main", self.provider_identity, observer_id, registry,
                now=NOW, trusted_owner_ids=["owner-1"])

    def test_missing_required_owner_review_is_refused(self):
        source = self._source()
        with self.assertRaisesRegex(ValidationError, "owner review"):
            self._admit(source, self._evidence(source, owner=False))

    def test_tier_three_cannot_disable_owner_review_requirement(self):
        source = self._source(owner_required=False)
        source["risk_tier"] = 3
        with self.assertRaisesRegex(ValidationError, "Tier 3.*owner review"):
            self._admit(source, self._evidence(source, owner=False))

    def test_stale_owner_review_is_refused(self):
        source = self._source()
        stale = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) -
                 timedelta(hours=2)).astimezone(timezone.utc).isoformat().replace(
                     "+00:00", "Z")
        with self.assertRaisesRegex(ValidationError, "fresh"):
            self._admit(source, self._evidence(source, observed_at=stale))

    def test_wrong_head_owner_review_is_refused(self):
        source = self._source()
        evidence = self._evidence(source)
        evidence["owner_review"]["head_sha"] = self.head2
        with self.assertRaisesRegex(ValidationError, "owner review.*head"):
            self._admit(source, evidence)

    def test_lower_tier_without_normal_owner_gate_needs_no_owner_review(self):
        source = self._source(owner_required=False)
        admitted = self._admit(source, self._evidence(source, owner=False))
        self.assertEqual("ADMITTED", admitted["status"])
        self.assertIsNone(admitted["owner_review_evidence_sha256"])

    def test_failed_critic_ci_or_provenance_is_refused(self):
        source = self._source()
        for field in ("critic", "ci", "provenance"):
            with self.subTest(field=field):
                evidence = self._evidence(source)
                evidence[field]["status"] = "FAIL"
                with self.assertRaisesRegex(ValidationError, field):
                    self._admit(source, evidence)

    def test_draft_base_and_head_drift_are_refused(self):
        source = self._source()
        cases = [
            (self._evidence(source, draft=True), "draft"),
            (self._evidence(source, base_branch="release"), "base"),
            (self._evidence(source, head=self.head2), "head"),
        ]
        for evidence, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValidationError, reason):
                    self._admit(source, evidence)

    def test_order_is_dependency_then_approval_time_then_pr_number(self):
        module = merge_train()
        independent_late = self._admit(
            self._source(pr=12, head=self.head2, tree=self.tree2,
                         commits=[self.head2]),
            self._evidence(
                self._source(pr=12, head=self.head2, tree=self.tree2,
                             commits=[self.head2])),
        )
        early_source = self._source()
        independent_early = self._admit(
            early_source,
            self._evidence(early_source, observed_at="2026-10-16T17:55:00Z"),
        )
        dependent_source = self._source(
            pr=13, head=hashlib.sha1(b"dependent").hexdigest(),
            tree=hashlib.sha1(b"dependent-tree").hexdigest(),
            depends=[12], commits=[hashlib.sha1(b"dependent").hexdigest()],
        )
        dependent_source["base_branch"] = "awf/task-12"
        dependent_source["base_sha"] = self.head2
        dependent = self._admit(
            dependent_source,
            self._evidence(dependent_source,
                           observed_at="2026-10-16T17:50:00Z"),
        )
        ordered = module.order_sources(
            [dependent, independent_late, independent_early],
            self.provider_identity,
            self.observer_registry, now=NOW,
            trusted_owner_ids=["owner-1"])
        self.assertEqual([11, 12, 13],
                         [item["pr_number"] for item in ordered])

    def test_duplicate_pr_head_and_shared_commit_range_are_refused(self):
        module = merge_train()
        first = self._admit()
        duplicate_head_source = self._source(
            pr=12, commits=[self.head1])
        duplicate_head = self._admit(
            duplicate_head_source, self._evidence(duplicate_head_source))
        shared_source = self._source(
            pr=13, head=self.head2, tree=self.tree2,
            commits=[self.head1, self.head2])
        shared = self._admit(shared_source, self._evidence(shared_source))
        for sources, reason in [
            ([first, first], "duplicate PR"),
            ([first, duplicate_head], "duplicate head"),
            ([first, shared], "shared commit"),
        ]:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValidationError, reason):
                    module.order_sources(
                        sources, self.provider_identity,
                        self.observer_registry, now=NOW,
                        trusted_owner_ids=["owner-1"])

    def test_source_review_cannot_authorize_batch(self):
        module = merge_train()
        with self.assertRaisesRegex(ValidationError, "batch authorization"):
            module.require_batch_authorization(self._admit())

    def test_forged_admission_with_recomputed_id_is_refused_at_freeze(self):
        module = merge_train()
        admitted = self._admit()
        forged = copy.deepcopy(admitted)
        forged_source = self._source(
            head=self.head2, tree=self.tree2, commits=[self.head2])
        forged.update(forged_source)
        evidence = self._evidence(forged_source)
        request = module.source_admission_request(
            forged_source, "main", self.provider_identity)
        producer_id = admitted["provider_observation"]["producer_id"]
        registration = self.observer_registry[producer_id]
        request_sha = fingerprint("merge_train_provider_request", request)
        observation_material = {
            "producer_id": producer_id,
            "observed_at": evidence["observed_at"],
            "request_sha256": request_sha,
            "result": evidence,
        }
        raw_observation = {
            "status": "OBSERVED",
            "producer_id": producer_id,
            "observation_id": fingerprint(
                "merge_train_provider_observation", observation_material),
            "observed_at": evidence["observed_at"],
            "request_sha256": request_sha,
            "result": evidence,
        }
        forged_receipt = module.provider_receipt_sha256(
            registration, request, raw_observation)
        forged["provider_admission"]["request"] = request
        forged["provider_admission"]["observation"] = raw_observation
        forged["provider_observation"] = {
            "producer_id": producer_id,
            "observation_id": raw_observation["observation_id"],
            "observed_at": raw_observation["observed_at"],
            "request_sha256": request_sha,
            "receipt_sha256": forged_receipt,
            "implementation_sha256": registration["implementation_sha256"],
            "release_sha256": registration["release_sha256"],
        }
        forged["evidence_digests"] = {
            field: evidence[field]["evidence_sha256"]
            for field in ("final_gate", "critic", "ci", "provenance")
        }
        forged["owner_review_evidence_sha256"] = evidence[
            "owner_review"]["evidence_sha256"]
        forged["approval_time"] = evidence["owner_review"]["submitted_at"]
        forged["admission_evidence_sha256"] = fingerprint(
            "merge_train_source_admission_evidence", {
                "source": forged_source,
                "provider_observation": forged["provider_observation"],
                "evidence_digests": forged["evidence_digests"],
                "owner_review_evidence_sha256": forged[
                    "owner_review_evidence_sha256"],
            })
        forged["admission_id"] = fingerprint(
            "merge_train_source_admission",
            {key: value for key, value in forged.items()
             if key != "admission_id"})

        with self.assertRaisesRegex(ValidationError, "registered receipt"):
            module.freeze_generation(
                "AWF-54-batch", "main", self.target, [forged], 1,
                self.provider_identity, self.observer_registry, now=NOW,
                trusted_owner_ids=["owner-1"])

    def test_generation_rejects_wrong_or_mixed_admission_targets(self):
        module = merge_train()
        admitted_main = self._admit()
        release_source = self._source(
            pr=12, head=self.head2, tree=self.tree2, commits=[self.head2])
        release_source["base_branch"] = "release"
        admitted_release = self._admit(
            release_source, self._evidence(release_source),
            target_branch="release")

        cases = [
            ("release", [admitted_main]),
            ("main", [admitted_main, admitted_release]),
        ]
        for target_branch, sources in cases:
            with self.subTest(target_branch=target_branch,
                              source_count=len(sources)):
                with self.assertRaisesRegex(
                        ValidationError, "admission target branch"):
                    module.freeze_generation(
                        "AWF-54-batch", target_branch, self.target, sources, 1,
                        self.provider_identity, self.observer_registry, now=NOW,
                        trusted_owner_ids=["owner-1"])

    def test_generation_rejects_admissions_from_different_repositories(self):
        module = merge_train()
        first = self._admit()
        second_source = self._source(
            pr=12, head=self.head2, tree=self.tree2, commits=[self.head2])
        second_evidence = self._evidence(second_source)
        observer_id = "other-repository-observer"
        other_provider_identity = {
            "host": "https://example.invalid",
            "repository": "owner/other-repository",
            "repository_id": 84,
        }
        _, registration = self._registration(
            second_source, second_evidence, observer_id=observer_id,
            provider_identity=other_provider_identity)
        self.observer_registry[observer_id] = registration
        second = module.admit_source(
            second_source, "main", other_provider_identity, observer_id,
            self.observer_registry, now=NOW,
            trusted_owner_ids=["owner-1"])

        with self.assertRaisesRegex(ValidationError, "provider identity"):
            module.freeze_generation(
                "AWF-54-batch", "main", self.target, [first, second], 1,
                self.provider_identity, self.observer_registry, now=NOW,
                trusted_owner_ids=["owner-1"])

    def test_generation_invalidates_on_target_head_or_evidence_drift(self):
        module = merge_train()
        admitted = self._admit()
        generation = module.freeze_generation(
            "AWF-54-batch", "main", self.target, [admitted], 1,
            self.provider_identity, self.observer_registry, now=NOW,
            trusted_owner_ids=["owner-1"])
        self.assertEqual(self.provider_identity,
                         generation["provider_identity"])
        self.assertEqual("CURRENT", module.generation_status(
            generation, self.target, [admitted], self.provider_identity,
            self.observer_registry,
            now=NOW, trusted_owner_ids=["owner-1"])["status"])
        changed_head_source = self._source(
            head=self.head2, tree=self.tree2, commits=[self.head2])
        changed_head = self._admit(
            changed_head_source, self._evidence(changed_head_source))
        changed_evidence = self._admit(
            self._source(), self._evidence(
                self._source(), observed_at="2026-10-16T17:59:00Z"))
        for target, sources, reason in [
            (self.head2, [admitted], "TARGET_DRIFT"),
            (self.target, [changed_head], "SOURCE_HEAD_DRIFT"),
            (self.target, [changed_evidence], "SOURCE_EVIDENCE_DRIFT"),
        ]:
            with self.subTest(reason=reason):
                status = module.generation_status(
                    generation, target, sources, self.provider_identity,
                    self.observer_registry,
                    now=NOW, trusted_owner_ids=["owner-1"])
                self.assertEqual("ABANDONED", status["status"])
                self.assertIn(reason, status["reasons"])

    def test_conflict_policy_is_bounded_and_fail_closed(self):
        module = merge_train()
        self.assertEqual("REGENERATE_ONCE_FROM_COMBINED_TREE",
            module.conflict_action(
                conflict_paths=["MANIFEST.json"],
                generated_paths=["MANIFEST.json"], reproduced=True,
                unexpected_generated_bytes=False, repair_attempts=0,
                failure_kind="MERGE_CONFLICT"))
        self.assertEqual("EJECT_SOURCE_REBUILD_FROM_TARGET",
            module.conflict_action(
                conflict_paths=["src/app.py"],
                generated_paths=["MANIFEST.json"], reproduced=False,
                unexpected_generated_bytes=False, repair_attempts=0,
                failure_kind="MERGE_CONFLICT"))
        self.assertEqual("EJECT_SOURCE_REBUILD_FROM_TARGET",
            module.conflict_action(
                conflict_paths=["MANIFEST.json"],
                generated_paths=["MANIFEST.json"], reproduced=True,
                unexpected_generated_bytes=True, repair_attempts=0,
                failure_kind="MERGE_CONFLICT"))
        self.assertEqual("EJECT_SOURCE_REBUILD_FROM_TARGET",
            module.conflict_action(
                conflict_paths=[], generated_paths=[], reproduced=False,
                unexpected_generated_bytes=False, repair_attempts=0,
                failure_kind="LOCAL_PREFIX_FAILURE"))
        self.assertEqual("ABANDON_GENERATION",
            module.conflict_action(
                conflict_paths=[], generated_paths=[], reproduced=False,
                unexpected_generated_bytes=False, repair_attempts=0,
                failure_kind="REMOTE_ONLY_CI_FAILURE"))

    def test_failed_source_is_ejected_and_generation_rebuilt_from_frozen_target(self):
        module = merge_train()
        second_head = hashlib.sha1(b"second-source").hexdigest()
        third_head = hashlib.sha1(b"third-source").hexdigest()
        sources = [
            {"pr_number": 11, "head_sha": self.head1, "depends_on": [],
             "admission_evidence_sha256": "a" * 64},
            {"pr_number": 12, "head_sha": second_head, "depends_on": []},
            {"pr_number": 13, "head_sha": third_head, "depends_on": [12]},
        ]
        built = []

        def build_prefix(target_sha, included):
            built.append((target_sha, [item["pr_number"] for item in included]))
            return hashlib.sha1(
                (target_sha + ":" + ",".join(str(item["pr_number"])
                                               for item in included)).encode("ascii")
            ).hexdigest()

        result = module.rebuild_after_failed_source(
            frozen_target_sha=self.target, ordered_sources=sources,
            failed_pr_number=12, build_prefix=build_prefix)
        self.assertEqual([12, 13], result["ejected_pr_numbers"])
        self.assertEqual([11], result["retained_pr_numbers"])
        self.assertEqual([(self.target, [11])], built)
        self.assertTrue(all(item["started_from_sha"] == self.target
                            for item in result["rebuilt_prefixes"]))

    def test_merge_train_fixtures_are_portable_on_windows_and_linux(self):
        module = merge_train()
        self.assertEqual(("src/app.py", "tests/test_app.py"),
                         module._portable_paths(
                             ["tests/test_app.py", "src/app.py"], "fixture"))
        separator = chr(92)
        drive_path = "C" + ":" + separator + separator.join(
            ("fixture", "member.py"))
        unc_path = separator * 2 + separator.join(
            ("server", "share", "member.py"))
        for path in (drive_path, unc_path,
                     "/absolute/member.py"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValidationError, "non-portable"):
                    module._portable_paths([path], "fixture")

        # T14 also exercises the shipped 54B publication boundary with portable
        # batch identities; path normalization alone is not a red regression.
        adapter = runpy.run_path(str(ROOT / ".agentic/scripts/merge_train.py"))
        branch = "batch/portable-fixture"
        raw = {"ref": "refs/heads/" + branch,
               "object": {"sha": self.head1}, "number": 211,
               "head": {"ref": branch, "sha": self.head1},
               "base": {"ref": "main"}, "title": "portable fixture", "draft": True}
        commands = [
            ["push-batch-ref", "--branch", branch, "--head", self.head1],
            ["create-draft-pr", "--branch", branch, "--base", "main",
             "--title", "portable fixture"],
            ["mark-draft-pr-ready", "--pr", "211"],
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                with mock.patch.object(subprocess, "run", return_value=mock.Mock(
                        returncode=0, stdout=json.dumps(raw), stderr="")) as run:
                    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                        with self.assertRaises(SystemExit) as refused:
                            adapter["main"](["--repository", "owner/repository", *command])
                    self.assertEqual(2, refused.exception.code)
                    run.assert_not_called()

    def test_strict_registered_batch_parent_provenance(self):
        module = merge_train()
        admitted = self._admit()
        generation = module.freeze_generation(
            "AWF-54-batch", "main", self.target, [admitted], 1,
            self.provider_identity, self.observer_registry, now=NOW,
            trusted_owner_ids=["owner-1"])
        commit_sha = hashlib.sha1(b"batch-commit").hexdigest()
        result = {
            "generation_id": generation["generation_id"],
            "commits": [{
                "length": 1,
                "commit_sha": commit_sha,
                "parents": [self.target, admitted["head_sha"]],
            }],
        }
        request, registration = self._registration(
            self._source(), self._evidence(self._source()))

        def parent_observer(value):
            request_sha = fingerprint("merge_train_provider_request", value)
            material = {
                "producer_id": "fixture-observer", "observed_at": NOW,
                "request_sha256": request_sha, "result": copy.deepcopy(result),
            }
            observation = {
                "status": "OBSERVED", "producer_id": "fixture-observer",
                "observation_id": fingerprint(
                    "merge_train_provider_observation", material),
                "observed_at": NOW, "request_sha256": request_sha,
                "result": copy.deepcopy(result),
            }
            receipt = module.provider_receipt_sha256(
                registration, value, observation)
            registration["receipts"][observation["observation_id"]] = receipt
            return observation

        registration["observer"] = parent_observer
        provenance = module.verify_batch_parent_provenance(
            generation, [commit_sha], "fixture-observer",
            self.provider_identity, {"fixture-observer": registration},
            now=NOW)
        self.assertEqual("PASS", provenance["status"])
        self.assertEqual([self.target, admitted["head_sha"]],
                         provenance["commits"][0]["parents"])

        registration["provider_identity"] = {
            "host": "https://example.invalid",
            "repository": "owner/other-repository",
            "repository_id": 84,
        }
        with self.assertRaisesRegex(ValidationError, "provider identity"):
            module.verify_batch_parent_provenance(
                generation, [commit_sha], "fixture-observer",
                self.provider_identity, {"fixture-observer": registration},
                now=NOW)
        registration["provider_identity"] = copy.deepcopy(
            self.provider_identity)

        result["commits"][0]["parents"].reverse()
        with self.assertRaisesRegex(ValidationError, "batch parents"):
            module.verify_batch_parent_provenance(
                generation, [commit_sha], "fixture-observer",
                self.provider_identity, {"fixture-observer": registration},
                now=NOW)


if __name__ == "__main__":
    unittest.main()
