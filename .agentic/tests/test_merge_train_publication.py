from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint


NOW = "2026-10-17T10:05:00Z"


def merge_train():
    return importlib.import_module("agentic.merge_train")


class MergeTrainPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-train-publish-")
        self.root = Path(self.temporary.name)
        self.provider = {
            "host": "https://example.invalid",
            "repository": "owner/repository",
            "repository_id": 42,
        }
        self.target = hashlib.sha1(b"target").hexdigest()
        self.head = hashlib.sha1(b"batch-head").hexdigest()
        self.tree = hashlib.sha1(b"batch-tree").hexdigest()
        self.source_head = hashlib.sha1(b"source-head").hexdigest()
        self.source_tree = hashlib.sha1(b"source-tree").hexdigest()
        self.generation = self._generation()
        self.provider_state = self._expected_state()
        self.observer_registry = {}
        self.publisher_registry = {}
        self.provider_calls = []
        self.remote = {"ref": None, "pr": None, "ready": False}
        self._register_observer()
        self._register_publisher()

    def tearDown(self):
        self.temporary.cleanup()

    def _generation(self):
        module = merge_train()
        value = {
            "format": "awf-merge-train-generation-1",
            "batch_id": "1.9.5-20261017-1000",
            "target_branch": "main",
            "target_sha": self.target,
            "generation": 1,
            "provider_identity": copy.deepcopy(self.provider),
            "sources": [{
                "pr_number": 174,
                "head_sha": self.source_head,
                "tree_sha": self.source_tree,
                "admission_evidence_sha256": "a" * 64,
            }],
            "authorization_scope": "CONSTRUCTION_ONLY",
            "may_publish": False,
            "may_merge": False,
        }
        value["generation_id"] = fingerprint("merge_train_generation", value)
        return value

    def _expected_state(self):
        return {
            "generation_id": self.generation["generation_id"],
            "target_branch": "main",
            "target_sha": self.target,
            "sources": copy.deepcopy(self.generation["sources"]),
        }

    def _register_observer(self, observed_at=NOW):
        module = merge_train()
        receipts = {}

        def observer(request):
            if request["operation"] == "BATCH_PUBLICATION_CANDIDATE":
                result = copy.deepcopy(request["expected_candidate"])
            elif request["operation"] == "BATCH_PUBLICATION_STATE":
                result = copy.deepcopy(self.provider_state)
            else:
                raise AssertionError(request["operation"])
            request_sha = fingerprint("merge_train_provider_request", request)
            material = {
                "producer_id": "fixture-observer",
                "observed_at": observed_at,
                "request_sha256": request_sha,
                "result": result,
            }
            observation = {
                "status": "OBSERVED",
                "producer_id": "fixture-observer",
                "observation_id": fingerprint(
                    "merge_train_provider_observation", material),
                "observed_at": observed_at,
                "request_sha256": request_sha,
                "result": result,
            }
            receipts[observation["observation_id"]] = (
                module.provider_receipt_sha256(registration, request, observation))
            return observation

        registration = {
            "producer_kind": "merge_train_observer",
            "implementation_sha256": "b" * 64,
            "release_sha256": "c" * 64,
            "capabilities": ["BATCH_PUBLICATION_CANDIDATE",
                             "BATCH_PUBLICATION_STATE"],
            "provider_identity": copy.deepcopy(self.provider),
            "receipt_namespace": "awf-merge-train-observer-v1",
            "observer": observer,
            "receipts": receipts,
        }
        self.observer_registry["fixture-observer"] = registration

    def _register_publisher(self, *, lose_pr_response=False):
        def push_batch_ref(branch, head):
            self.provider_calls.append(("push_batch_ref", branch, head))
            self.remote["ref"] = {"branch": branch, "head_sha": head}
            return copy.deepcopy(self.remote["ref"])

        def create_draft_pr(branch, base, title):
            self.provider_calls.append(("create_draft_pr", branch, base, title))
            self.remote["pr"] = {
                "number": 211, "branch": branch, "base_branch": base,
                "head_sha": self.head, "title": title, "draft": True,
            }
            if lose_pr_response:
                raise TimeoutError("provider response lost")
            return copy.deepcopy(self.remote["pr"])

        def mark_draft_pr_ready(number):
            self.provider_calls.append(("mark_draft_pr_ready", number))
            self.remote["ready"] = True
            self.remote["pr"]["draft"] = False
            return copy.deepcopy(self.remote["pr"])

        registration = {
            "producer_kind": "merge_train_publisher",
            "adapter_kind": "gh",
            "implementation_sha256": "d" * 64,
            "release_sha256": "e" * 64,
            "capabilities": ["PUSH_BATCH_REF", "CREATE_DRAFT_PR",
                             "MARK_DRAFT_PR_READY"],
            "provider_identity": copy.deepcopy(self.provider),
            "receipt_namespace": "awf-merge-train-publisher-v1",
            "push_batch_ref": push_batch_ref,
            "create_draft_pr": create_draft_pr,
            "mark_draft_pr_ready": mark_draft_pr_ready,
            "observe_batch_ref": lambda branch: copy.deepcopy(self.remote["ref"]),
            "observe_pull_request": lambda branch: copy.deepcopy(self.remote["pr"]),
        }
        self.publisher_registry["fixture-publisher"] = registration

    def _candidate(self, *, now=NOW, max_batch_size=4):
        return merge_train().freeze_publication_candidate(
            self.generation, self.head, self.tree, release="1.9.5",
            scheduled_at="2026-10-17T10:00:00Z", max_batch_size=max_batch_size,
            observer_id="fixture-observer", observer_registry=self.observer_registry,
            now=now)

    def _publish(self, candidate, journal="journal.json", mark_ready=False):
        return merge_train().publish_batch(
            candidate, observer_id="fixture-observer",
            observer_registry=self.observer_registry,
            publisher_id="fixture-publisher",
            publisher_registry=self.publisher_registry,
            intent_path=self.root / journal, now=NOW, mark_ready=mark_ready)

    def test_publication_requires_authenticated_exact_batch_candidate(self):
        candidate = self._candidate()
        forged = copy.deepcopy(candidate)
        forged["batch_head_sha"] = hashlib.sha1(b"forged").hexdigest()
        with self.assertRaisesRegex(ValidationError, "candidate"):
            self._publish(forged)
        self.assertEqual([], self.provider_calls)

        stale_registry = self.observer_registry
        self.observer_registry = {}
        self._register_observer(observed_at="2026-10-17T08:00:00Z")
        with self.assertRaisesRegex(ValidationError, "fresh"):
            self._candidate()
        self.observer_registry = stale_registry

        result = self._publish(candidate)
        self.assertEqual("PUBLISHED_DRAFT", result["status"])
        self.assertEqual(["push_batch_ref", "create_draft_pr"],
                         [call[0] for call in self.provider_calls])

    def test_cli_refuses_ungated_mutations_without_provider_execution(self):
        adapter = self._gh_adapter()
        branch = "batch/unfrozen"
        raw = {"ref": "refs/heads/" + branch,
               "object": {"sha": self.head}, "number": 211,
               "head": {"ref": branch, "sha": self.head},
               "base": {"ref": "main"}, "title": "unfrozen", "draft": True}
        commands = [
            ["push-batch-ref", "--branch", branch, "--head", self.head],
            ["create-draft-pr", "--branch", branch, "--base", "main",
             "--title", "unfrozen"],
            ["mark-draft-pr-ready", "--pr", "211"],
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                with mock.patch.object(adapter.subprocess, "run", return_value=
                        mock.Mock(returncode=0, stdout=json.dumps(raw), stderr="")) as run:
                    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                        with self.assertRaises(SystemExit) as refused:
                            adapter.main(["--repository", "owner/repository", *command])
                    self.assertEqual(2, refused.exception.code)
                    run.assert_not_called()

    def test_batch_branch_title_cadence_and_maximum_size(self):
        candidate = self._candidate(max_batch_size=1)
        self.assertEqual("batch/1.9.5-20261017-1000",
                         candidate["batch_branch"])
        self.assertEqual("1.9.5-20261017-1000 (PRs #174)",
                         candidate["draft_pr_title"])
        with self.assertRaisesRegex(ValidationError, "fixed batch slot"):
            merge_train().freeze_publication_candidate(
                self.generation, self.head, self.tree, release="1.9.5",
                scheduled_at="2026-10-17T11:00:00Z", max_batch_size=1,
                observer_id="fixture-observer",
                observer_registry=self.observer_registry, now=NOW)
        too_many = copy.deepcopy(self.generation)
        too_many["sources"].append({
            "pr_number": 175, "head_sha": "1" * 40,
            "tree_sha": "2" * 40, "admission_evidence_sha256": "3" * 64})
        too_many["generation_id"] = fingerprint(
            "merge_train_generation",
            {key: value for key, value in too_many.items()
             if key != "generation_id"})
        with self.assertRaisesRegex(ValidationError, "maximum batch size"):
            merge_train().freeze_publication_candidate(
                too_many, self.head, self.tree, release="1.9.5",
                scheduled_at="2026-10-17T10:00:00Z", max_batch_size=1,
                observer_id="fixture-observer",
                observer_registry=self.observer_registry, now=NOW)

    def test_authenticated_request_must_bind_every_publication_field(self):
        original = self._candidate()
        requests = []
        partial = copy.deepcopy(original["provider_candidate"]["request"])
        partial["expected_candidate"] = {
            "batch_head_sha": self.head, "batch_tree_sha": self.tree}
        requests.append(partial)
        for field in original["provider_candidate"]["request"]["expected_candidate"]:
            request = copy.deepcopy(original["provider_candidate"]["request"])
            del request["expected_candidate"][field]
            requests.append(request)
        for index, request in enumerate(requests):
            with self.subTest(request=request):
                candidate = copy.deepcopy(original)
                registration = self.observer_registry["fixture-observer"]
                observation = registration["observer"](request)
                _, attestation = merge_train()._validate_registered_observation(
                    registration, "fixture-observer", request, observation,
                    now=NOW, max_age_seconds=1800)
                candidate["provider_candidate"] = {
                    "request": request, "observation": observation,
                    "attestation": attestation}
                candidate["publication_candidate_id"] = fingerprint(
                    "merge_train_publication_candidate",
                    {key: value for key, value in candidate.items()
                     if key != "publication_candidate_id"})
                with self.assertRaisesRegex(ValidationError, "exact"):
                    self._publish(candidate, journal=f"partial-{index}.json")
                self.assertEqual([], self.provider_calls)

    def test_mark_ready_requires_complete_original_pr_identity(self):
        candidate = self._candidate()
        changes = {"number": 999, "branch": "batch/other",
                   "base_branch": "release", "head_sha": "f" * 40,
                   "title": "unrelated", "draft": False}
        for field, value in changes.items():
            with self.subTest(field=field):
                self.provider_calls.clear()
                self._register_publisher()
                journal = f"ready-{field}.json"
                self._publish(candidate, journal=journal)
                self.remote["pr"][field] = value
                with self.assertRaisesRegex(ValidationError, "PR.*candidate"):
                    self._publish(candidate, journal=journal, mark_ready=True)
                self.assertNotIn("mark_draft_pr_ready",
                                 [call[0] for call in self.provider_calls])

    def test_ready_response_must_retain_complete_pr_identity(self):
        candidate = self._candidate()
        for field, value in {"number": 999, "branch": "batch/other",
                             "base_branch": "release", "title": "other"}.items():
            with self.subTest(field=field):
                self._register_publisher()
                registration = self.publisher_registry["fixture-publisher"]
                original = registration["mark_draft_pr_ready"]

                def changed(number):
                    result = original(number)
                    result[field] = value
                    return result

                registration["mark_draft_pr_ready"] = changed
                with self.assertRaisesRegex(ValidationError, "different batch"):
                    self._publish(candidate, journal=f"response-{field}.json",
                                  mark_ready=True)

    def _gh_adapter(self):
        spec = importlib.util.spec_from_file_location(
            "merge_train_gh", ROOT / ".agentic/scripts/merge_train.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_gh_registered_adapter_publishes_and_recovers_without_replay(self):
        adapter = self._gh_adapter()
        candidate = self._candidate()
        branch = candidate["batch_branch"]
        commands = []
        state = {"ref": None, "pr": None, "lose_response": True}

        def gh_run(command, **kwargs):
            commands.append(command)
            self.assertEqual("gh-fixture", command[0])
            if command[1:3] == ["pr", "ready"]:
                state["pr"]["draft"] = False
                return mock.Mock(returncode=0, stdout="", stderr="")
            self.assertEqual("api", command[1])
            self.assertIn("example.invalid", command)
            endpoint = command[2]
            if endpoint.endswith("/git/refs"):
                self.assertEqual(["-f", "ref=refs/heads/" + branch,
                                  "-f", "sha=" + self.head], command[5:])
                state["ref"] = {"ref": "refs/heads/" + branch,
                                "object": {"sha": self.head}}
                output = state["ref"]
            elif "/git/matching-refs/" in endpoint:
                output = [state["ref"]] if state["ref"] else []
            elif endpoint.endswith("/pulls"):
                self.assertEqual(["-f", "head=" + branch, "-f", "base=main",
                                  "-f", "title=" + candidate["draft_pr_title"],
                                  "-F", "draft=true"], command[5:])
                state["pr"] = {
                    "number": 211, "head": {"ref": branch, "sha": self.head},
                    "base": {"ref": "main"},
                    "title": candidate["draft_pr_title"], "draft": True}
                if state["lose_response"]:
                    state["lose_response"] = False
                    raise TimeoutError("lost gh response")
                output = state["pr"]
            elif "/pulls?" in endpoint:
                output = [state["pr"]] if state["pr"] else []
            elif endpoint.endswith("/pulls/211"):
                output = state["pr"]
            else:
                self.assertEqual("repos/owner/repository", endpoint)
                output = {"id": 42, "full_name": "owner/repository"}
            return mock.Mock(returncode=0, stdout=json.dumps(output), stderr="")

        with mock.patch.object(adapter.subprocess, "run", side_effect=gh_run):
            registration = adapter.register_publisher(
                gh="gh-fixture", provider_identity=self.provider,
                implementation_sha256="d" * 64, release_sha256="e" * 64)
            self.publisher_registry["fixture-publisher"] = registration
            with self.assertRaisesRegex(ValidationError, "outcome is unknown"):
                self._publish(candidate)
            # A new process reconstructs the adapter; recovery observes gh state.
            self.publisher_registry["fixture-publisher"] = adapter.register_publisher(
                gh="gh-fixture", provider_identity=self.provider,
                implementation_sha256="d" * 64, release_sha256="e" * 64)
            result = self._publish(candidate, mark_ready=True)
            self.assertEqual("PUBLISHED_READY", result["status"])
            self.assertEqual(211, result["pull_request"]["number"])
            self.assertFalse(result["pull_request"]["draft"])
            self.assertEqual(1, sum(command[2].endswith("/git/refs")
                                    for command in commands))
            self.assertEqual(1, sum(command[2].endswith("/pulls")
                                    for command in commands))
            self.assertEqual(1, sum(command[1:3] == ["pr", "ready"]
                                    for command in commands))
            with self.assertRaisesRegex(ValidationError, "batch ref"):
                registration["push_batch_ref"]("main", self.head)
            with self.assertRaisesRegex(ValidationError, "batch ref"):
                registration["create_draft_pr"]("main", "main", "other")

    def test_gh_registration_refuses_wrong_repository_and_command_failure(self):
        adapter = self._gh_adapter()
        for response in (mock.Mock(returncode=0, stdout=json.dumps(
                {"id": 99, "full_name": "owner/repository"}), stderr=""),
                         mock.Mock(returncode=1, stdout="", stderr="failure")):
            with self.subTest(response=response):
                with mock.patch.object(adapter.subprocess, "run", return_value=response):
                    with self.assertRaises(ValidationError):
                        adapter.register_publisher(
                            gh="gh-fixture", provider_identity=self.provider,
                            implementation_sha256="d" * 64,
                            release_sha256="e" * 64)

    def test_gh_adapter_normalizes_successful_creation_and_observations(self):
        adapter = self._gh_adapter()
        branch = self._candidate()["batch_branch"]
        raw_pr = {"number": 211, "head": {"ref": branch, "sha": self.head},
                  "base": {"ref": "main"}, "title": "batch title", "draft": True}
        expected_pr = {"number": 211, "branch": branch, "head_sha": self.head,
                       "base_branch": "main", "title": "batch title", "draft": True}
        with mock.patch.object(adapter.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout=json.dumps(raw_pr), stderr="")):
            self.assertEqual(expected_pr, adapter.create_draft_pr(
                "gh-fixture", "owner/repository", branch, "main", "batch title"))
        for values, expected in (([], None), ([raw_pr], expected_pr)):
            with self.subTest(values=values):
                with mock.patch.object(adapter.subprocess, "run", return_value=mock.Mock(
                        returncode=0, stdout=json.dumps(values), stderr="")):
                    self.assertEqual(expected, adapter.observe_pull_request(
                        "gh-fixture", "owner/repository", branch))
        raw_ref = {"ref": "refs/heads/" + branch, "object": {"sha": self.head}}
        with mock.patch.object(adapter.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout=json.dumps([raw_ref]), stderr="")):
            self.assertEqual({"branch": branch, "head_sha": self.head},
                             adapter.observe_batch_ref(
                                 "gh-fixture", "owner/repository", branch))
        for values in ([raw_pr, raw_pr], {}):
            with self.subTest(values=values):
                with mock.patch.object(adapter.subprocess, "run", return_value=mock.Mock(
                        returncode=0, stdout=json.dumps(values), stderr="")):
                    with self.assertRaises(ValidationError):
                        adapter.observe_pull_request("gh-fixture", "owner/repository", branch)

    def test_gh_process_timeout_becomes_unknown_publication_outcome(self):
        adapter = self._gh_adapter()
        with mock.patch.object(adapter.subprocess, "run", side_effect=
                adapter.subprocess.TimeoutExpired(["gh-fixture"], 120)):
            with self.assertRaises(TimeoutError):
                adapter.push_batch_ref("gh-fixture", "owner/repository",
                                       self._candidate()["batch_branch"], self.head)

    def test_freeze_is_rechecked_before_every_provider_mutation(self):
        for drift_after, expected_calls in [(0, []), (1, ["push_batch_ref"]),
                                            (2, ["push_batch_ref",
                                                 "create_draft_pr"])]:
            with self.subTest(drift_after=drift_after):
                self.provider_calls.clear()
                self.remote = {"ref": None, "pr": None, "ready": False}
                candidate = self._candidate()
                observations = {"count": 0}
                original = self.observer_registry["fixture-observer"]["observer"]

                def drifting(request):
                    if request["operation"] == "BATCH_PUBLICATION_STATE":
                        if observations["count"] >= drift_after:
                            self.provider_state["target_sha"] = "f" * 40
                        observations["count"] += 1
                    return original(request)

                self.observer_registry["fixture-observer"]["observer"] = drifting
                try:
                    with self.assertRaisesRegex(ValidationError, "abandoned"):
                        self._publish(candidate, journal=f"drift-{drift_after}.json",
                                      mark_ready=True)
                finally:
                    self.observer_registry["fixture-observer"]["observer"] = original
                    self.provider_state = self._expected_state()
                self.assertEqual(expected_calls,
                                 [call[0] for call in self.provider_calls])

    def test_freeze_drift_durably_abandons_generation_after_restart(self):
        # C195-R2-016-001: restored observations cannot revive a rejected freeze.
        for drift_after in range(3):
            for drift_field in ("target", "source", "evidence"):
                with self.subTest(drift_after=drift_after, drift_field=drift_field):
                    self.provider_calls.clear()
                    self.provider_state = self._expected_state()
                    self._register_observer()
                    self._register_publisher()
                    candidate = self._candidate()
                    journal = f"abandoned-{drift_after}-{drift_field}.json"
                    original = self.observer_registry["fixture-observer"]["observer"]
                    observations = 0

                    def drifting(request):
                        nonlocal observations
                        if request["operation"] == "BATCH_PUBLICATION_STATE":
                            if observations >= drift_after:
                                if drift_field == "target":
                                    self.provider_state["target_sha"] = "f" * 40
                                elif drift_field == "source":
                                    self.provider_state["sources"][0]["head_sha"] = "f" * 40
                                else:
                                    self.provider_state["sources"][0][
                                        "admission_evidence_sha256"] = "f" * 64
                            observations += 1
                        return original(request)

                    self.observer_registry["fixture-observer"]["observer"] = drifting
                    with self.assertRaisesRegex(ValidationError, "abandoned"):
                        self._publish(candidate, journal=journal, mark_ready=True)
                    calls_at_rejection = copy.deepcopy(self.provider_calls)
                    self.assertEqual(drift_after, len(calls_at_rejection))
                    rejected_bytes = (self.root / journal).read_bytes()
                    self.assertEqual("ABANDONED", json.loads(rejected_bytes).get("status"))
                    self.provider_state = self._expected_state()
                    self.observer_registry["fixture-observer"]["observer"] = original
                    for restarted in (False, True):
                        if restarted:
                            importlib.reload(merge_train())
                            receipts = copy.deepcopy(
                                self.observer_registry["fixture-observer"]["receipts"])
                            self._register_observer()
                            self.observer_registry["fixture-observer"]["receipts"].update(receipts)
                            self._register_publisher()
                            candidate = json.loads(json.dumps(candidate))
                        with self.assertRaisesRegex(ValidationError, "abandoned"):
                            self._publish(candidate, journal=journal, mark_ready=True)
                        self.assertEqual(calls_at_rejection, self.provider_calls)
                        self.assertEqual(rejected_bytes, (self.root / journal).read_bytes())

    def test_push_pr_creation_orphan_race_is_recovered_without_replay(self):
        candidate = self._candidate()
        self.publisher_registry.clear()
        self._register_publisher(lose_pr_response=True)
        with self.assertRaisesRegex(ValidationError, "outcome is unknown"):
            self._publish(candidate)
        self.assertEqual(["push_batch_ref", "create_draft_pr"],
                         [call[0] for call in self.provider_calls])

        self.publisher_registry.clear()
        self._register_publisher(lose_pr_response=False)
        result = self._publish(candidate)
        self.assertEqual("PUBLISHED_DRAFT", result["status"])
        self.assertEqual(["push_batch_ref", "create_draft_pr"],
                         [call[0] for call in self.provider_calls])
        journal = json.loads((self.root / "journal.json").read_text("utf-8"))
        self.assertEqual("RECONCILED", journal["operations"][1]["status"])

    def _expired_publish(self, candidate, *, mark_ready=False):
        return merge_train().publish_batch(
            candidate, observer_id="fixture-observer",
            observer_registry=self.observer_registry,
            publisher_id="fixture-publisher",
            publisher_registry=self.publisher_registry,
            intent_path=self.root / "journal.json",
            now="2026-10-17T10:36:00Z", mark_ready=mark_ready)

    def _orphaned_pr(self):
        candidate = self._candidate()
        self._register_publisher(lose_pr_response=True)
        with self.assertRaisesRegex(ValidationError, "outcome is unknown"):
            self._publish(candidate)
        self._register_publisher()
        return candidate

    def test_expired_candidate_recovers_orphan_without_mutation_or_new_identity(self):
        candidate = self._orphaned_pr()
        original = copy.deepcopy(candidate)
        calls = copy.deepcopy(self.provider_calls)
        importlib.reload(merge_train())
        candidate = json.loads(json.dumps(candidate))
        registration = self.publisher_registry["fixture-publisher"]
        with mock.patch.dict(registration, {
                "observe_pull_request": mock.Mock(
                    wraps=registration["observe_pull_request"])}):
            result = self._expired_publish(candidate)
            registration["observe_pull_request"].assert_called_with(
                candidate["batch_branch"])
        self.assertEqual("PUBLISHED_DRAFT", result["status"])
        self.assertEqual("RECONCILED", result["journal"]["operations"][1]["status"])
        self.assertEqual(original["publication_candidate_id"],
                         result["journal"]["publication_candidate_id"])
        self.assertEqual(original, candidate)
        self.assertEqual(calls, self.provider_calls)

    def test_expired_recovery_cannot_start_ready_mutation(self):
        candidate = self._orphaned_pr()
        calls = copy.deepcopy(self.provider_calls)
        with self.assertRaisesRegex(ValidationError, "fresh"):
            self._expired_publish(candidate, mark_ready=True)
        journal = json.loads((self.root / "journal.json").read_text("utf-8"))
        self.assertEqual("RECONCILED", journal["operations"][1]["status"])
        self.assertEqual(2, len(journal["operations"]))
        self.assertEqual(calls, self.provider_calls)

    def test_expired_push_recovery_cannot_create_pr(self):
        candidate = self._candidate()
        registration = self.publisher_registry["fixture-publisher"]
        original = registration["push_batch_ref"]

        def lost_push(branch, head):
            original(branch, head)
            raise TimeoutError("push response lost")

        registration["push_batch_ref"] = lost_push
        with self.assertRaisesRegex(ValidationError, "outcome is unknown"):
            self._publish(candidate)
        with self.assertRaisesRegex(ValidationError, "fresh"):
            self._expired_publish(candidate)
        journal = json.loads((self.root / "journal.json").read_text("utf-8"))
        self.assertEqual("RECONCILED", journal["operations"][0]["status"])
        self.assertEqual(1, len(journal["operations"]))
        self.assertEqual(["push_batch_ref"], [call[0] for call in self.provider_calls])

    def test_expired_recovery_rejects_untrusted_or_mismatched_evidence(self):
        candidate = self._orphaned_pr()
        path = self.root / "journal.json"
        original = path.read_bytes()
        calls = copy.deepcopy(self.provider_calls)
        registration = self.publisher_registry["fixture-publisher"]
        observe = registration["observe_pull_request"]
        invalid = copy.deepcopy(self.remote["pr"])
        invalid["head_sha"] = "f" * 40
        cases = [
            (registration, "observe_pull_request", lambda branch: None, "unknown"),
            (registration, "observe_pull_request", lambda branch: invalid, "unknown"),
            (registration, "provider_identity", dict(self.provider, repository_id=99),
             "provider identity"),
            (self.observer_registry["fixture-observer"], "receipts", {}, "receipt"),
        ]
        for target, key, value, message in cases:
            with self.subTest(key=key, message=message):
                with mock.patch.dict(target, {key: value}):
                    with self.assertRaisesRegex(ValidationError, message):
                        self._expired_publish(candidate)
                self.assertEqual(original, path.read_bytes())
                self.assertEqual(calls, self.provider_calls)
        # Recovery remains available once authenticated matching evidence returns.
        with mock.patch.dict(registration, {"observe_pull_request": observe}):
            self.assertEqual("PUBLISHED_DRAFT", self._expired_publish(candidate)["status"])

    def test_expired_candidate_cannot_start_push_or_create_journal(self):
        candidate = self._candidate()
        with self.assertRaisesRegex(ValidationError, "fresh"):
            self._expired_publish(candidate)
        self.assertFalse((self.root / "journal.json").exists())
        self.assertEqual([], self.provider_calls)

    def test_merge_train_exposes_no_merge_or_target_push_api(self):
        module = merge_train()
        forbidden = {"merge", "merge_batch", "push_target", "push_target_ref"}
        self.assertTrue(forbidden.isdisjoint(set(dir(module))))
        registration = self.publisher_registry["fixture-publisher"]
        self.assertNotIn("merge", registration)
        self.assertNotIn("push_target_ref", registration)
        overpowered = copy.deepcopy(registration)
        overpowered["merge"] = lambda: None
        self.publisher_registry["overpowered"] = overpowered
        with self.assertRaisesRegex(ValidationError, "non-closed shape"):
            merge_train().publish_batch(
                self._candidate(), observer_id="fixture-observer",
                observer_registry=self.observer_registry,
                publisher_id="overpowered",
                publisher_registry=self.publisher_registry,
                intent_path=self.root / "overpowered.json", now=NOW)
        self.assertEqual([], self.provider_calls)
        self._publish(self._candidate())
        pushed = [call for call in self.provider_calls
                  if call[0] == "push_batch_ref"]
        self.assertEqual("batch/1.9.5-20261017-1000", pushed[0][1])
        script = (ROOT / ".agentic/scripts/merge_train.py").read_text("utf-8")
        self.assertNotIn("push-target", script)
        self.assertNotIn("merge-batch", script)


if __name__ == "__main__":
    unittest.main()
