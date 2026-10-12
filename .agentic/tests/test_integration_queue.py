from contextlib import redirect_stdout
import copy
from datetime import datetime, timedelta
import importlib.util
import io
import json
import hashlib
import inspect
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from agentic import ValidationError
from agentic.authorization import make_request, render_request
from agentic.canonical import fingerprint, load, sha256
from agentic.contracts import Contracts
from agentic.gates import evaluate
from agentic.integration_queue import (DEFAULTS, WORKER_GUIDANCE, IntegrationQueueStore,
    build_execution_receipt_sha256, build_input_id, build_record,
    delta_review_requirement, integration_status, post_merge_action,
    provider_observation_receipt_sha256, render_owner_instruction, resolve_mode,
    steward_policy, verify_own_commits)
from agentic.lifecycle import definition
from test_review_policy import bind_production_posting_fixture, bind_review_admission


SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40
SHA4 = "4" * 40
SHA5 = "5" * 40
NOW = "2026-10-10T10:00:00Z"
GATE_NOW = "2026-09-09T12:00:00Z"
ROOT = Path(__file__).resolve().parents[2]
PROJECT_CONFIG = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
PROVIDER_IDENTITY = {"host": PROJECT_CONFIG["github"]["host"],
                     "repository": PROJECT_CONFIG["github"]["repository"],
                     "repository_id": PROJECT_CONFIG["github"]["repository_id"]}
BUILD_REGISTRY = {}
GIT_REGISTRY = {}
MODE_REGISTRY = {}


def entry(pr, head, *, depends=(), base="main", validated=NOW, paths=None, patch=None):
    return {
        "pr_number": pr,
        "ticket": f"AWF-{pr}",
        "head_sha": head,
        "branch": f"awf/task-{pr}",
        "base_branch": base,
        "depends_on": list(depends),
        "validated_at": validated,
        "reviewed_paths": paths or [f"src/task-{pr}.py"],
        "own_commits": [head],
        "patch_id": patch or head,
    }


def observed_build(state, *, fail_at=None, variant="stable", run_id=None,
                   observed_at=NOW):
    target = state["target_sha"]
    entries = state["entries"]
    command = "python -B -m unittest"
    prior = target
    construction = []
    for index, item in enumerate(entries, 1):
        commit = hashlib.sha1(f"{prior}:{item['head_sha']}:{variant}".encode()).hexdigest()
        tree = hashlib.sha1(f"{commit}:tree:{variant}".encode()).hexdigest()
        construction.append({"length": index, "base_commit_sha": prior,
            "head_sha": item["head_sha"], "parents": [prior, item["head_sha"]],
            "result_commit_sha": commit,
            "result_tree_sha": tree})
        prior = commit
    tree = (construction[-1]["result_tree_sha"] if construction else
            hashlib.sha1(f"{target}:tree:{variant}".encode()).hexdigest())
    input_id = build_input_id("AWF-EPIC", target, entries, command)
    prefixes = [
        {"length": index, "commit_sha": step["result_commit_sha"],
         "tree_sha": step["result_tree_sha"],
         "passed": fail_at is None or index < fail_at,
         "result_digest": f"test-result-{index}"}
        for index, step in enumerate(construction, 1)
    ]
    deterministic_result = {"input_id": input_id, "result_commit_sha": prior,
                            "result_tree_sha": tree, "construction": construction,
                            "prefix_results": prefixes}
    result_id = fingerprint("integration_build_result", deterministic_result)
    observation = {"producer_id": "fixture-build-adapter",
                   "run_id": run_id or str(uuid.uuid4()), "observed_at": observed_at,
                   "input_id": input_id, "result_id": result_id}
    observation["observation_id"] = fingerprint("integration_build_observation", observation)
    return {
        "input_id": input_id,
        "result_commit_sha": prior,
        "result_tree_sha": tree,
        "test_command": "python -B -m unittest",
        "construction": construction,
        "prefix_results": prefixes,
        "observation": observation,
    }


def register_build_runner(callback, registry=BUILD_REGISTRY):
    def adapter(state):
        outcome = callback(state)
        observation = outcome["observation"]
        registration = registry[observation["producer_id"]]
        registration["receipts"][observation["run_id"]] = (
            build_execution_receipt_sha256(
                registration, outcome, state["integration_branch"]))
        return outcome

    registry["fixture-build-adapter"] = {
        "producer_kind": "integration_build",
        "implementation_sha256": "a" * 64,
        "release_sha256": "b" * 64,
        "runner": adapter,
        "receipts": {},
    }
    return adapter


def runner(*, fail_at=None, variant="stable", observed_at=NOW):
    return register_build_runner(
        lambda state: observed_build(
            state, fail_at=fail_at, variant=variant, observed_at=observed_at))


def same_tree_different_commit_runner():
    def callback(state):
        outcome = observed_build(state)
        prior = state["target_sha"]
        for index, step in enumerate(outcome["construction"], 1):
            commit = hashlib.sha1(f"alternate-merge-{index}".encode()).hexdigest()
            step["base_commit_sha"] = prior
            step["parents"] = [prior, step["head_sha"]]
            step["result_commit_sha"] = commit
            outcome["prefix_results"][index - 1]["commit_sha"] = commit
            prior = commit
        outcome["result_commit_sha"] = prior
        deterministic_result = {
            "input_id": outcome["input_id"],
            "result_commit_sha": outcome["result_commit_sha"],
            "result_tree_sha": outcome["result_tree_sha"],
            "construction": outcome["construction"],
            "prefix_results": outcome["prefix_results"],
        }
        observation = outcome["observation"]
        observation["result_id"] = fingerprint(
            "integration_build_result", deterministic_result)
        observation["observation_id"] = fingerprint(
            "integration_build_observation",
            {key: value for key, value in observation.items()
             if key != "observation_id"})
        return outcome

    return register_build_runner(callback)


def register_integration_observer(producer_id, callback, registry,
                                  *, observed_at=NOW):
    def observer(request):
        result = callback(request)
        request_sha256 = fingerprint("integration_provider_request", request)
        material = {"producer_id": producer_id, "observed_at": observed_at,
                    "request_sha256": request_sha256, "result": result}
        observation = {
            "status": "OBSERVED", "producer_id": producer_id,
            "observation_id": fingerprint("integration_provider_observation", material),
            "observed_at": observed_at, "request_sha256": request_sha256,
            "result": result,
        }
        registration = registry[producer_id]
        registration["receipts"][observation["observation_id"]] = (
            provider_observation_receipt_sha256(registration, request, observation))
        return observation

    registry[producer_id] = {
        "producer_kind": "integration_observer",
        "implementation_sha256": "c" * 64,
        "release_sha256": "d" * 64,
        "observer": observer,
        "receipts": {},
    }
    return producer_id


def observed_git(request):
    if request["operation"] == "TASK_COMMIT_RANGE":
        return {"commits": [request["head_sha"]]}
    if request["operation"] == "TARGET_TIP":
        return {"target_branch": request["target_branch"],
                "target_sha": request["expected_target_sha"]}
    if request["operation"] == "TARGET_CI":
        return {"target_branch": request["target_branch"],
                "target_sha": request["target_sha"], "ci_green": False}
    if request["operation"] == "RETARGET_DIFF":
        return {"target_branch": request["target_branch"],
                "target_sha": request["new_target_sha"], "patch_ids": [
            {"pr_number": item["pr_number"], "head_sha": item["head_sha"],
             "base_branch": item["new_base_branch"], "base_sha": item["new_base_sha"],
             "patch_id": "a" * 40}
            for item in request["retargets"]
        ]}
    if request["operation"] == "PULL_REQUEST_MERGE":
        return {"status": "MERGED", "pr_number": request["pr_number"],
                "reviewed_head_sha": request["reviewed_head_sha"],
                "target_branch": request["target_branch"],
                "old_target_sha": request["old_target_sha"],
                "new_target_sha": request["expected_new_target_sha"]}
    if request["operation"] == "RETARGET_RETENTION":
        return {"pr_number": request["pr_number"], "head_sha": request["head_sha"],
                "target_sha": request["target_sha"],
                "patch_id": request["reviewed_patch_id"]}
    if request["operation"] == "MERGE_CONFLICT":
        return {"status": "CONFLICT", "target_sha": request["target_sha"],
                "candidate_head_sha": request["head_sha"],
                "conflict_paths": ["src/conflict.py"],
                "commits": [request["head_sha"]]}
    raise AssertionError("unexpected Git observation operation")


def instruction(action, payload, identifier="owner-1"):
    raw = render_owner_instruction(action, "AWF-EPIC", payload, NOW)
    value = {"owner_actor_id": PROJECT_CONFIG["merge_gate"]["trusted_owner_ids"][0],
             "producer_id": "fixture-mode-provider",
             "source_event_id": f"event-{identifier}",
             "provider_identity": PROVIDER_IDENTITY,
             "action": action, "epic": "AWF-EPIC", "payload": payload,
             "issued_at": NOW, "raw_body": raw,
             "raw_body_sha256": sha256(raw.encode("utf-8"))}
    value["instruction_id"] = fingerprint("integration_owner_instruction", value)
    return value


def owner_observer(request):
    record = request["record"]
    return {"status": "AUTHENTICATED", "instruction_id": record["instruction_id"],
            "record_sha256": request["record_sha256"],
            "source_event_id": record["source_event_id"],
            "owner_actor_id": record["owner_actor_id"],
            "producer_id": record["producer_id"],
            "provider_identity": request["expected_provider_identity"],
            "observed_at": NOW}


def config_observer(request):
    provenance = request["provenance"]
    return {"status": "ACCEPTED", "config_path": provenance["config_path"],
            "config_sha256": provenance["config_sha256"],
            "settings_sha256": fingerprint(
                "integration_queue_settings", request["settings"]),
            "review_record_id": provenance["review_record_id"],
            "producer_id": provenance["producer_id"],
            "provider_identity": request["expected_provider_identity"],
            "observed_at": NOW}


def observed_mode(request):
    if request["operation"] == "OWNER_INSTRUCTION":
        return {"instruction": copy.deepcopy(request["instruction"])}
    if request["operation"] == "REVIEWED_CONFIG":
        return {"provenance": copy.deepcopy(request["provenance"]),
                "settings": copy.deepcopy(request["settings"])}
    if request["operation"] == "APP_BINDING":
        return {"producer_id": "fixture-mode-provider",
                "provider_identity": copy.deepcopy(request["provider_identity"]),
                "repository_identity": copy.deepcopy(request["repository_identity"]),
                "source_identity": "repository_branch_rules",
                "enforced": False}
    raise AssertionError("unexpected mode observation operation")


def delta_review_record(*, status, run_id, producer_id, head_sha, review_base_sha,
                        target_sha, build_id, result_tree_sha, merges,
                        reviewed_diff, effective_diff, code_change_required,
                        reviewed_at=NOW):
    record = {"status": status, "run_id": run_id, "producer_id": producer_id,
              "head_sha": head_sha, "review_base_sha": review_base_sha,
              "target_sha": target_sha, "build_id": build_id,
              "result_tree_sha": result_tree_sha,
              "intervening_digest": fingerprint("integration_intervening", merges),
              "reviewed_diff_digest": fingerprint(
                  "integration_reviewed_diff", reviewed_diff),
              "effective_diff_digest": fingerprint(
                  "integration_effective_diff", effective_diff),
              "code_change_required": code_change_required,
              "reviewed_at": reviewed_at}
    record["result_digest"] = fingerprint("integration_delta_critic_result", record)
    return record


def epic_mode_record():
    return resolve_mode({"mode": "epic", "test_command": "python -B -m unittest", "provenance": {
        "source": "reviewed_config", "config_path": ".agentic/integration-queue-config.yaml",
        "config_sha256": "a" * 64, "review_record_id": "config-review-1",
        "reviewed_at": NOW, "producer_id": "fixture-mode-provider"}}, epic="AWF-EPIC",
        project_config=PROJECT_CONFIG,
        app_binding={"producer_id": "fixture-mode-provider"},
        mode_observer_registry=MODE_REGISTRY, now=NOW)


def trusted_readiness():
    contracts = Contracts(ROOT / ".agentic/schemas")
    config = copy.deepcopy(PROJECT_CONFIG)
    mode_registry = {}
    register_integration_observer(
        "fixture-mode-provider", observed_mode, mode_registry, observed_at=GATE_NOW)
    mode_record = resolve_mode(
        {"mode": "epic", "test_command": "python -B -m unittest",
         "provenance": {"source": "reviewed_config",
             "config_path": ".agentic/integration-queue-config.yaml",
             "config_sha256": "a" * 64, "review_record_id": "config-review-1",
             "reviewed_at": NOW, "producer_id": "fixture-mode-provider"}},
        epic="AWF-EPIC", project_config=config,
        app_binding={"producer_id": "fixture-mode-provider"},
        mode_observer_registry=mode_registry, now=GATE_NOW)
    bundle = load(ROOT / ".agentic/examples/evidence-bundle.json")
    bind_review_admission(bundle)
    verifier = copy.deepcopy(bundle["runs"][0])
    verifier.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
                    producer_id="fixture-authorization-verifier", role="verifier",
                    context_id=str(uuid.uuid4()), parent_run_id=None,
                    capabilities=["read_evidence", "verify_source_event"],
                    resources_held=[])
    bundle["runs"].append(verifier)
    delta_critic = copy.deepcopy(next(
        run for run in bundle["runs"] if run["role"] == "critic"))
    delta_critic.update(record_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
                        producer_id="fixture-delta-critic",
                        context_id=str(uuid.uuid4()), parent_run_id=None)
    bundle["runs"].append(delta_critic)
    registry = bind_production_posting_fixture(config, bundle)
    gate = evaluate(config, definition(), bundle, contracts, GATE_NOW,
                    posting_collector_registry=registry)
    request = make_request(gate, contracts, GATE_NOW)
    raw = render_request(request)
    authorization = {key: request[key] for key in [
        "schema_version", "request_id", "gate_id", "gate_hash", "candidate", "binding",
        "expires_at"]}
    authorization.update(
        record_id=str(uuid.uuid4()), created_at=GATE_NOW,
        producer_id=verifier["producer_id"], run_id=verifier["run_id"],
        source={"channel": "github_pr_comment", "event_id": str(uuid.uuid4()),
                "comment_id": 12, "repository_id": gate["candidate"]["repository_id"],
                "pr_number": gate["candidate"]["pr_number"], "created_at": GATE_NOW,
                "updated_at": GATE_NOW, "raw_body": raw,
                "raw_body_sha256": sha256(raw.encode("utf-8")), "actor_id": 1001,
                "actor_login": "fixture-owner", "deleted": False,
                "authentication_evidence": ["urn:awf:fixture:webhook"]},
        decision="AUTHORIZE", parser_version="awf-auth-1", execution_authority=False)
    candidate = gate["candidate"]
    state = {"entries": [{"pr_number": candidate["pr_number"],
                           "head_sha": candidate["head_sha"], "branch": "awf/task-7",
                           "reviewed_paths": ["src/task-7.py"], "depends_on": [],
                           "patch_id": candidate["head_sha"]}],
             "epic": "AWF-EPIC", "mode_record": mode_record,
             "target_sha": candidate["target_base_sha"],
             "target_branch": candidate["target_base_branch"],
             "integration_branch": "awf/int/fixture",
             "current_build": {"target_sha": candidate["target_base_sha"],
                               "entries": [[candidate["pr_number"], candidate["head_sha"]]],
                                "green": True, "build_id": "current-build-instance",
                                "result_commit_sha": SHA3, "result_tree_sha": SHA4}}
    raw_delta = {"review_base_sha": candidate["target_base_sha"],
                 "intervening_merges": [],
                 "reviewed_diff": [{"path": "src/task-7.py",
                                    "base_blob_sha": SHA1, "head_blob_sha": SHA2}],
                  "effective_diff": [{"path": "src/task-7.py",
                                       "base_blob_sha": SHA1, "head_blob_sha": SHA2}],
                 "review": None}
    delta_registry = {}
    register_integration_observer(
        delta_critic["producer_id"], lambda _request: copy.deepcopy(raw_delta),
        delta_registry, observed_at=GATE_NOW)
    delta = {"producer_id": delta_critic["producer_id"]}
    return (state, bundle, gate, request, authorization, delta, config, contracts,
            registry, delta_registry, mode_registry)


class IntegrationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-integration-queue-")
        self.addCleanup(self.temporary.cleanup)
        BUILD_REGISTRY.clear()
        GIT_REGISTRY.clear()
        MODE_REGISTRY.clear()
        register_integration_observer(
            "fixture-git-provider", observed_git, GIT_REGISTRY)
        register_integration_observer(
            "fixture-mode-provider", observed_mode, MODE_REGISTRY)
        self.store = IntegrationQueueStore(
            Path(self.temporary.name) / "queue.sqlite3", project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY,
            build_producer_registry=BUILD_REGISTRY,
            git_observer_registry=GIT_REGISTRY)
        self.store.initialize("AWF-EPIC", "main", SHA1,
                              target_observer_id="fixture-git-provider",
                              mode_record=epic_mode_record(),
                              observed_at=NOW)

    def admit(self, item, result=None):
        return self.store.admit("AWF-EPIC", item, "fixture-git-provider",
                                result or runner(), observed_at=NOW)

    def test_ac1_deterministic_build_and_stable_entry_identity(self):
        first = self.admit(entry(11, SHA2))
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual([11], first["queue"])
        self.assertEqual([[11, SHA2]], state["current_build"]["entries"])
        rebuilt = build_record("AWF-EPIC", SHA1, state["entries"], observed_build(state),
                               "TARGET_MOVED", 99)
        self.assertNotEqual(state["current_build"]["build_id"], rebuilt["build_id"])
        self.assertEqual(state["current_build"]["input_id"], rebuilt["input_id"])
        self.assertEqual(state["current_build"]["result_tree_sha"], rebuilt["result_tree_sha"])

    def test_ac1_rejects_divergent_results_for_identical_inputs(self):
        self.admit(entry(11, SHA2))
        with self.assertRaisesRegex(ValidationError, "divergent integration tree"):
            self.store.rebuild("AWF-EPIC", SHA1, runner(variant="different"),
                               "TARGET_MOVED", target_observer_id="fixture-git-provider",
                               observed_at=NOW)

    def test_ac1_identical_tree_allows_distinct_per_run_merge_commits(self):
        first = self.admit(entry(11, SHA2))["build"]
        rebuilt = self.store.rebuild(
            "AWF-EPIC", SHA1, same_tree_different_commit_runner(),
            "TARGET_MOVED", target_observer_id="fixture-git-provider",
            observed_at=NOW)["build"]
        self.assertEqual(first["result_tree_sha"], rebuilt["result_tree_sha"])
        self.assertNotEqual(first["result_commit_sha"], rebuilt["result_commit_sha"])
        self.assertNotEqual(first["result_id"], rebuilt["result_id"])
        self.assertEqual(2, len(self.store.snapshot("AWF-EPIC")["build_run_ids"]))

    def test_ac1_store_rejects_caller_supplied_build_document(self):
        item = entry(11, SHA2)
        proposed_state = self.store.snapshot("AWF-EPIC")
        proposed_state["entries"].append(item)
        with self.assertRaisesRegex(ValidationError, "provider adapter runner"):
            self.store.admit("AWF-EPIC", item, "fixture-git-provider",
                             observed_build(proposed_state),
                             observed_at=NOW)

    def test_ac1_unregistered_callable_cannot_self_report_a_passing_build(self):
        item = entry(11, SHA2)
        with self.assertRaisesRegex(ValidationError, "registered integration-build producer"):
            self.store.admit(
                "AWF-EPIC", item, "fixture-git-provider",
                lambda state: observed_build(state), observed_at=NOW)

    def test_ac1_registered_build_receipt_binds_branch_commit_tree_and_test_run(self):
        registry = {}

        def registered_runner(state):
            outcome = observed_build(state)
            observation = outcome["observation"]
            observation["run_id"] = "registered-build-run-1"
            observation["observation_id"] = fingerprint(
                "integration_build_observation",
                {key: value for key, value in observation.items()
                 if key != "observation_id"})
            registration = registry[observation["producer_id"]]
            test_execution_sha256 = fingerprint("integration_build_test_execution", {
                "test_command": outcome["test_command"],
                "prefix_results": outcome["prefix_results"],
            })
            receipt = fingerprint("integration_build_execution_receipt", {
                "producer_id": observation["producer_id"],
                "run_id": observation["run_id"],
                "observation_id": observation["observation_id"],
                "observed_at": observation["observed_at"],
                "input_id": observation["input_id"],
                "result_id": observation["result_id"],
                "integration_branch": state["integration_branch"],
                "result_commit_sha": outcome["result_commit_sha"],
                "result_tree_sha": outcome["result_tree_sha"],
                "test_execution_sha256": test_execution_sha256,
                "implementation_sha256": registration["implementation_sha256"],
                "release_sha256": registration["release_sha256"],
            })
            registration["receipts"][observation["run_id"]] = receipt
            return outcome

        registry["fixture-build-adapter"] = {
            "producer_kind": "integration_build",
            "implementation_sha256": "a" * 64,
            "release_sha256": "b" * 64,
            "runner": registered_runner,
            "receipts": {},
        }
        self.store.build_producer_registry = registry
        admitted = self.admit(entry(11, SHA2), registered_runner)
        self.assertEqual("ADMITTED", admitted["status"])
        self.assertEqual("registered-build-run-1",
                         admitted["build"]["observation"]["run_id"])

    def test_ac1_registered_producer_without_matching_receipt_is_refused(self):
        registry = {}

        def registered_but_unobserved(state):
            return observed_build(state, run_id="unobserved-build-run")

        registry["fixture-build-adapter"] = {
            "producer_kind": "integration_build",
            "implementation_sha256": "a" * 64,
            "release_sha256": "b" * 64,
            "runner": registered_but_unobserved,
            "receipts": {},
        }
        self.store.build_producer_registry = registry
        with self.assertRaisesRegex(ValidationError, "matching observation receipt"):
            self.admit(entry(11, SHA2), registered_but_unobserved)

    def test_ac2_admission_lock_serializes_concurrent_runners(self):
        activity = {"active": 0, "maximum": 0}
        activity_lock = threading.Lock()
        results = []

        def admission_runner(snapshot):
            with activity_lock:
                activity["active"] += 1
                activity["maximum"] = max(activity["maximum"], activity["active"])
            time.sleep(0.05)
            with activity_lock:
                activity["active"] -= 1
            return observed_build(snapshot)

        admission_runner = register_build_runner(admission_runner)

        def admit_item(item):
            results.append(self.store.admit(
                "AWF-EPIC", item, "fixture-git-provider", admission_runner,
                observed_at=NOW))

        threads = [threading.Thread(target=admit_item, args=(entry(11, SHA2),)),
                   threading.Thread(target=admit_item, args=(entry(12, SHA3),))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, activity["maximum"])
        self.assertEqual(2, len(results))
        self.assertEqual({11, 12}, {item["pr_number"] for item in
                                   self.store.snapshot("AWF-EPIC")["entries"]})

    def test_ac2_failed_admission_ejects_only_new_head(self):
        self.admit(entry(11, SHA2))
        failed = self.admit(entry(12, SHA3), runner(fail_at=2))
        self.assertEqual("INTEGRATION_FAILED", failed["status"])
        self.assertEqual([11], failed["queue"])
        self.assertEqual(SHA2, self.store.snapshot("AWF-EPIC")["entries"][0]["head_sha"])

    def test_ac3_conflict_records_paths_without_admission(self):
        result = self.store.conflict("AWF-EPIC", entry(11, SHA2),
                                     "fixture-git-provider", observed_at=NOW)
        self.assertEqual("INTEGRATION_CONFLICT", result["status"])
        self.assertEqual([], result["queue"])
        self.assertEqual(["src/conflict.py"], result["event"]["detail"]["paths"])
        self.assertEqual(11, self.store.snapshot("AWF-EPIC")["observed_commits"][SHA2])
        with self.assertRaisesRegex(ValidationError, "registered Git/provider observer"):
            self.store.conflict("AWF-EPIC", entry(12, SHA3),
                                ["src/caller-claimed.py"], observed_at=NOW)

    def test_ac4_only_exact_front_tuple_can_succeed(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         registry, delta_registry, mode_registry) = trusted_readiness()
        ready = integration_status(
            state, 7, bundle, gate, request, authorization, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("success", ready["status"])

        self.assertEqual("awf/task-7", ready["merge_source_branch"])
        self.assertEqual("awf/int/fixture", ready["integration_branch_evidence_only"])
        second = {"pr_number": 8, "head_sha": SHA3, "branch": "awf/task-8"}
        state["entries"].append(second)
        state["current_build"]["entries"].append([8, SHA3])
        pending = integration_status(
            state, 8, bundle, gate, request, authorization, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("pending", pending["status"])
        self.assertIn("position 2", pending["reason"])
        stale = copy.deepcopy(state)
        stale["entries"][0]["head_sha"] = SHA3
        stale["current_build"]["entries"][0] = [7, SHA3]
        stale = integration_status(
            stale, 7, bundle, gate, request, authorization, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("pending", stale["status"])
        self.assertIn("front queue candidate tuple", stale["reason"])
        wrong_delta = {**delta, "unexpected_summary": True}
        self.assertIn("delta review", integration_status(
            state, 7, bundle, gate, request, authorization, wrong_delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"])
        replayed_delta = {"required": False, "satisfied": True,
                          "head_sha": state["entries"][0]["head_sha"],
                          "target_sha": state["target_sha"],
                          "build_id": "older-build-instance"}
        self.assertIn("delta review", integration_status(
            state, 7, bundle, gate, request, authorization, replayed_delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"])

    def test_ac4_self_declared_evidence_and_unregistered_verifier_are_rejected(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         registry, delta_registry, mode_registry) = trusted_readiness()
        minimal = {"valid": True, "pr_number": 11, "head_sha": SHA2, "target_sha": SHA1}
        self.assertEqual("pending", integration_status(
            state, 7, minimal, minimal, minimal, minimal, delta, config=config,
            contracts=contracts, mode_observer_registry=mode_registry,
            now=GATE_NOW)["status"])
        unregistered = copy.deepcopy(authorization)
        unregistered["run_id"] = str(uuid.uuid4())
        self.assertIn("registered verifier", integration_status(
            state, 7, bundle, gate, request, unregistered, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"])
        untrusted = copy.deepcopy(authorization)
        untrusted["source"]["actor_id"] = 9999
        self.assertIn("Untrusted numeric owner", integration_status(
            state, 7, bundle, gate, request, untrusted, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"])

    def test_ac8_readiness_refuses_caller_summary_and_authenticates_raw_critic_evidence(self):
        (state, bundle, gate, request, authorization, _delta, config, contracts,
         registry, delta_registry, mode_registry) = trusted_readiness()
        state["entries"][0]["reviewed_paths"] = ["src/task-7.py"]
        caller_summary = {
            "required": True, "satisfied": True,
            "head_sha": state["entries"][0]["head_sha"],
            "review_base_sha": state["target_sha"],
            "target_sha": state["target_sha"],
            "build_id": state["current_build"]["build_id"],
            "intervening_digest": "a" * 64, "diff_digest": "b" * 64,
        }
        refused = integration_status(
            state, 7, bundle, gate, request, authorization, caller_summary,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)
        self.assertEqual("pending", refused["status"])

        merges = [{"merge_commit_sha": SHA3, "paths": ["src/task-7.py"]}]
        critic_run = next(run for run in bundle["runs"]
                          if run["producer_id"] == "fixture-delta-critic")
        reviewed_diff = [{"path": "src/task-7.py",
                          "base_blob_sha": SHA1, "head_blob_sha": SHA2}]
        effective_diff = copy.deepcopy(reviewed_diff)
        review = delta_review_record(
            status="PASS", run_id=critic_run["run_id"],
            producer_id=critic_run["producer_id"],
            head_sha=state["entries"][0]["head_sha"],
            review_base_sha=state["target_sha"], target_sha=state["target_sha"],
            build_id=state["current_build"]["build_id"],
            result_tree_sha=state["current_build"]["result_tree_sha"], merges=merges,
            reviewed_diff=reviewed_diff, effective_diff=effective_diff,
            code_change_required=False, reviewed_at=GATE_NOW)
        evidence = {"review_base_sha": state["target_sha"],
                    "intervening_merges": merges, "reviewed_diff": reviewed_diff,
                    "effective_diff": effective_diff,
                    "review": review}
        evidence_registry = {}
        register_integration_observer(
            critic_run["producer_id"], lambda _request: copy.deepcopy(evidence),
            evidence_registry, observed_at=GATE_NOW)
        selector = {"producer_id": critic_run["producer_id"]}
        ready = integration_status(
            state, 7, bundle, gate, request, authorization, selector,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=evidence_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)
        self.assertEqual("success", ready["status"])

        foreign_bundle = copy.deepcopy(bundle)
        foreign_run = next(run for run in foreign_bundle["runs"]
                           if run["run_id"] == critic_run["run_id"])
        foreign_run["binding"]["candidate_id"] = "f" * 64
        self.assertIn("binding mismatch", integration_status(
            state, 7, foreign_bundle, gate, request, authorization, selector,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=evidence_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"].lower())

        forged_digest = copy.deepcopy(evidence)
        forged_digest["review"]["effective_diff_digest"] = "f" * 64
        forged_registry = {}
        register_integration_observer(
            critic_run["producer_id"], lambda _request: copy.deepcopy(forged_digest),
            forged_registry, observed_at=GATE_NOW)
        self.assertIn("not satisfied", integration_status(
            state, 7, bundle, gate, request, authorization, selector,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=forged_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"])

        unregistered = copy.deepcopy(evidence)
        unregistered["review"]["run_id"] = "unregistered-delta-run"
        unregistered["review"]["result_digest"] = fingerprint(
            "integration_delta_critic_result",
            {key: value for key, value in unregistered["review"].items()
             if key != "result_digest"})
        unregistered_registry = {}
        register_integration_observer(
            critic_run["producer_id"], lambda _request: copy.deepcopy(unregistered),
            unregistered_registry, observed_at=GATE_NOW)
        self.assertIn("registered fresh critic run", integration_status(
            state, 7, bundle, gate, request, authorization, selector,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=unregistered_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"])
        stale = copy.deepcopy(evidence)
        stale["review"]["reviewed_at"] = "2026-09-09T10:00:00Z"
        stale["review"]["result_digest"] = fingerprint(
            "integration_delta_critic_result",
            {key: value for key, value in stale["review"].items()
             if key != "result_digest"})
        stale_registry = {}
        register_integration_observer(
            critic_run["producer_id"], lambda _request: copy.deepcopy(stale),
            stale_registry, observed_at=GATE_NOW)
        self.assertIn("stale", integration_status(
            state, 7, bundle, gate, request, authorization, selector,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=stale_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)["reason"].lower())

    def test_ac8_matching_critic_identifiers_cannot_authenticate_caller_delta_evidence(self):
        (state, bundle, gate, request, authorization, _delta, config, contracts,
         registry, delta_registry, mode_registry) = trusted_readiness()
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/task-7.py"]}]
        reviewed_diff = [{"path": "src/task-7.py",
                          "base_blob_sha": SHA1, "head_blob_sha": SHA2}]
        effective_diff = copy.deepcopy(reviewed_diff)
        critic_run = next(run for run in bundle["runs"]
                          if run["producer_id"] == "fixture-delta-critic")
        borrowed = {
            "review_base_sha": state["target_sha"],
            "intervening_merges": merges,
            "reviewed_diff": reviewed_diff,
            "effective_diff": effective_diff,
            "review": {
                "status": "PASS", "run_id": critic_run["run_id"],
                "producer_id": critic_run["producer_id"],
                "head_sha": state["entries"][0]["head_sha"],
                "review_base_sha": state["target_sha"],
                "target_sha": state["target_sha"],
                "build_id": state["current_build"]["build_id"],
                "intervening_digest": fingerprint("integration_intervening", merges),
                "diff_digest": fingerprint("integration_reviewed_diff", reviewed_diff),
                "code_change_required": False, "reviewed_at": GATE_NOW,
            },
        }
        result = integration_status(
            state, 7, bundle, gate, request, authorization, borrowed,
            config=config, contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry,
            now=GATE_NOW)
        self.assertEqual("pending", result["status"])
        self.assertIn("authenticated producer", result["reason"])

    def test_ac5_target_move_invalidates_old_build_and_prefix_ejects_culprit(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        result = self.store.rebuild("AWF-EPIC", SHA4, runner(fail_at=2),
                                    "TARGET_MOVED",
                                    target_observer_id="fixture-git-provider",
                                    observed_at=NOW)
        self.assertEqual("INTEGRATION_FAILED", result["status"])
        self.assertEqual(12, result["ejected_pr"])
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual(SHA4, state["target_sha"])
        self.assertIsNone(state["current_build"])
        self.assertEqual([11], [item["pr_number"] for item in state["entries"]])

    def test_ac7_failed_dependency_quarantines_its_dependent_closure(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11"))
        result = self.store.rebuild(
            "AWF-EPIC", SHA1, runner(fail_at=1), "TARGET_MOVED",
            target_observer_id="fixture-git-provider", observed_at=NOW)
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual(11, result["ejected_pr"])
        self.assertEqual([], state["entries"])
        self.assertEqual([11, 12],
                         [item["pr_number"] for item in state["quarantined_entries"]])
        self.admit(entry(11, SHA2))
        self.assertEqual([11], [item["pr_number"] for item in
                                self.store.snapshot("AWF-EPIC")["entries"]])

    def test_ac4_readiness_refuses_missing_or_later_dependency(self):
        (state, bundle, gate, request, authorization, delta, config, contracts,
         registry, delta_registry, mode_registry) = trusted_readiness()
        state["entries"][0]["depends_on"] = [99]
        result = integration_status(
            state, 7, bundle, gate, request, authorization, delta, config=config,
            contracts=contracts, posting_collector_registry=registry,
            delta_review_observer_registry=delta_registry,
            mode_observer_registry=mode_registry, now=GATE_NOW)
        self.assertEqual("pending", result["status"])
        self.assertIn("dependency", result["reason"])

    def test_ac5_head_change_and_removal_each_rebuild(self):
        self.admit(entry(11, SHA2))
        changed = self.store.head_changed(
            "AWF-EPIC", entry(11, SHA3), "fixture-git-provider", runner(),
            observed_at=NOW)
        self.assertEqual([[11, SHA3]], changed["build"]["entries"])
        self.assertEqual("HEAD_CHANGED", changed["build"]["reason"])
        removed = self.store.remove("AWF-EPIC", 11, runner(), observed_at=NOW)
        self.assertEqual([], removed["build"]["entries"])
        self.assertEqual("REMOVAL", removed["build"]["reason"])

    def test_ac5_head_change_preserves_stable_entry_identity(self):
        self.admit(entry(11, SHA2))
        changed = entry(11, SHA3)
        changed["ticket"] = "AWF-OTHER"
        with self.assertRaisesRegex(ValidationError, "stable ticket"):
            self.store.head_changed("AWF-EPIC", changed, "fixture-git-provider",
                                    runner(), observed_at=NOW)

    def test_ac6_owner_reorder_skip_and_dependency_order(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        reordered = self.store.owner_reorder(
            "AWF-EPIC", [12, 11], instruction("REORDER", {"order": [12, 11]}),
            runner(), observed_at=NOW)
        self.assertEqual([[12, SHA3], [11, SHA2]], reordered["build"]["entries"])
        skipped = self.store.owner_skip(
            "AWF-EPIC", 11, instruction("SKIP", {"pr_number": 11}, "owner-2"),
            runner(), observed_at=NOW)
        self.assertEqual([[12, SHA3]], skipped["build"]["entries"])

        other = IntegrationQueueStore(
            Path(self.temporary.name) / "dependencies.sqlite3",
            project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY,
            build_producer_registry=BUILD_REGISTRY,
            git_observer_registry=GIT_REGISTRY)
        other.initialize("AWF-EPIC", "main", SHA1,
                         target_observer_id="fixture-git-provider",
                         mode_record=epic_mode_record(), observed_at=NOW)
        other.admit("AWF-EPIC", entry(11, SHA2), "fixture-git-provider",
                    runner(), observed_at=NOW)
        other.admit("AWF-EPIC", entry(12, SHA3, depends=(11,), base="awf/task-11"),
                    "fixture-git-provider", runner(), observed_at=NOW)
        with self.assertRaisesRegex(ValidationError, "dependency behind"):
            other.owner_reorder("AWF-EPIC", [12, 11],
                instruction("REORDER", {"order": [12, 11]}), runner(), observed_at=NOW)

    def test_ac6_stalled_front_moves_behind_next_independent_and_notifies(self):
        old = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) -
               timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        register_integration_observer(
            "fixture-git-provider", observed_git, GIT_REGISTRY, observed_at=old)
        register_integration_observer(
            "fixture-mode-provider", observed_mode, MODE_REGISTRY, observed_at=old)
        first = entry(11, SHA2, validated=old)
        self.store.admit("AWF-EPIC", first, "fixture-git-provider", runner(observed_at=old),
                         observed_at=old)
        self.store.admit("AWF-EPIC",
            entry(12, SHA3, depends=(11,), base="awf/task-11", validated=old),
            "fixture-git-provider", runner(observed_at=old), observed_at=old)
        self.store.admit("AWF-EPIC", entry(13, SHA4, validated=old),
            "fixture-git-provider", runner(observed_at=old), observed_at=old)
        register_integration_observer(
            "fixture-mode-provider", observed_mode, MODE_REGISTRY, observed_at=NOW)
        result = self.store.move_stalled_front(
            "AWF-EPIC", runner(), now=NOW)
        self.assertEqual(13, result["notice"]["new_front"])
        self.assertTrue(result["notice"]["owner_notification_required"])
        self.assertEqual([13, 11, 12], [item[0] for item in result["build"]["entries"]])

    def test_ac6_owner_instruction_payload_is_bound_and_consumed_once(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        recorded = instruction("REORDER", {"order": [12, 11]}, "one-use")
        self.store.owner_reorder("AWF-EPIC", [12, 11], recorded, runner(), observed_at=NOW)
        with self.assertRaisesRegex(ValidationError, "already been consumed"):
            self.store.owner_reorder("AWF-EPIC", [12, 11], recorded, runner(), observed_at=NOW)
        wrong = instruction("SKIP", {"pr_number": 11}, "wrong-payload")
        with self.assertRaisesRegex(ValidationError, "payload"):
            self.store.owner_skip("AWF-EPIC", 12, wrong, runner(), observed_at=NOW)

    def test_ac6_stall_clock_starts_when_entry_becomes_front(self):
        old = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) -
               timedelta(hours=25)).isoformat().replace("+00:00", "Z")
        register_integration_observer(
            "fixture-git-provider", observed_git, GIT_REGISTRY, observed_at=old)
        register_integration_observer(
            "fixture-mode-provider", observed_mode, MODE_REGISTRY, observed_at=old)
        first = entry(11, SHA2, validated=old)
        second = entry(12, SHA3, validated=old)
        self.store.admit("AWF-EPIC", first, "fixture-git-provider", runner(observed_at=old),
                         observed_at=old)
        self.store.admit("AWF-EPIC", second, "fixture-git-provider", runner(observed_at=old),
                         observed_at=old)
        register_integration_observer(
            "fixture-git-provider", observed_git, GIT_REGISTRY, observed_at=NOW)
        register_integration_observer(
            "fixture-mode-provider", observed_mode, MODE_REGISTRY, observed_at=NOW)
        self.store.remove("AWF-EPIC", 11, runner(), observed_at=NOW)
        self.admit(entry(13, SHA4))
        with self.assertRaisesRegex(ValidationError, "stall threshold"):
            self.store.move_stalled_front("AWF-EPIC", runner(), now=NOW)

    def test_ac6_stall_threshold_has_no_caller_override(self):
        self.assertNotIn("stall_after_hours",
                         inspect.signature(self.store.move_stalled_front).parameters)

    def test_ac7_stacked_retarget_retains_only_unchanged_patch_evidence(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", patch="a" * 40))
        result = self.store.retarget_after_merge(
            "AWF-EPIC", 11, SHA4, "fixture-git-provider", runner(), observed_at=NOW)
        self.assertEqual([{"pr_number": 12, "retarget_to": "main",
                           "evidence_retained": True, "review_required": False}],
                         result["decisions"])
        self.assertEqual("main", self.store.snapshot("AWF-EPIC")["entries"][0]["base_branch"])

    def test_ac7_caller_patch_id_cannot_retain_retargeted_review(self):
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", patch="a" * 40))
        with self.assertRaisesRegex(ValidationError, "authenticated retarget"):
            self.store.retarget_after_merge(
                "AWF-EPIC", 11, SHA4, {"12": "a" * 40}, runner(), observed_at=NOW)

    def test_ac7_authenticated_changed_retarget_diff_drops_review(self):
        def changed_retarget(request):
            if request["operation"] in {"TARGET_TIP", "PULL_REQUEST_MERGE"}:
                return observed_git(request)
            return {"target_branch": request["target_branch"],
                    "target_sha": request["new_target_sha"], "patch_ids": [
                {"pr_number": item["pr_number"], "head_sha": item["head_sha"],
                 "base_branch": item["new_base_branch"],
                 "base_sha": item["new_base_sha"], "patch_id": "b" * 40}
                for item in request["retargets"]
            ]}

        register_integration_observer(
            "fixture-changed-retarget", changed_retarget, GIT_REGISTRY)
        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3, depends=(11,), base="awf/task-11", patch="a" * 40))
        result = self.store.retarget_after_merge(
            "AWF-EPIC", 11, SHA4, "fixture-changed-retarget", runner(), observed_at=NOW)
        self.assertEqual([{"pr_number": 12, "retarget_to": "main",
                           "evidence_retained": False, "review_required": True}],
                         result["decisions"])

    def test_ac8_overlap_requires_run_and_code_change_uses_round(self):
        no_merges = [{"merge_commit_sha": SHA3, "paths": ["src/b.py"]}]
        reviewed_diff = [{"path": "src/a.py", "base_blob_sha": SHA1,
                          "head_blob_sha": SHA2}]
        effective_diff = copy.deepcopy(reviewed_diff)
        none = delta_review_requirement(["src/a.py"], no_merges, head_sha=SHA2,
            review_base_sha=SHA1, target_sha=SHA3, build_id="build-1",
            result_tree_sha=SHA4, reviewed_diff=reviewed_diff,
            effective_diff=effective_diff)
        self.assertFalse(none["required"])
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/a.py"]}]
        required = delta_review_requirement(
            ["src/a.py"], merges, head_sha=SHA2, review_base_sha=SHA1,
            target_sha=SHA3, build_id="build-1", result_tree_sha=SHA4,
            reviewed_diff=reviewed_diff, effective_diff=effective_diff,
            delta_review=delta_review_record(
                status="FINDING", run_id="delta-1", producer_id="critic-1",
                head_sha=SHA2, review_base_sha=SHA1, target_sha=SHA3,
                build_id="build-1", result_tree_sha=SHA4, merges=merges,
                reviewed_diff=reviewed_diff, effective_diff=effective_diff,
                code_change_required=True))
        self.assertTrue(required["consumes_run"])
        self.assertTrue(required["consumes_round"])
        self.assertFalse(required["satisfied"])
        passed = delta_review_requirement(
            ["src/a.py"], merges, head_sha=SHA2, review_base_sha=SHA1,
            target_sha=SHA3, build_id="build-1", result_tree_sha=SHA4,
            reviewed_diff=reviewed_diff, effective_diff=effective_diff,
            delta_review=delta_review_record(
                status="PASS", run_id="delta-2", producer_id="critic-1",
                head_sha=SHA2, review_base_sha=SHA1, target_sha=SHA3,
                build_id="build-1", result_tree_sha=SHA4, merges=merges,
                reviewed_diff=reviewed_diff, effective_diff=effective_diff,
                code_change_required=False))
        self.assertTrue(passed["satisfied"])
        self.assertFalse(passed["consumes_round"])

    def test_ac8_overlap_requires_authenticated_exact_r_and_r_prime(self):
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/a.py"]}]
        reviewed_diff = [{"path": "src/a.py", "base_blob_sha": SHA1,
                          "head_blob_sha": SHA2}]
        with self.assertRaisesRegex(ValidationError, "effective_diff"):
            delta_review_requirement(
                ["src/a.py"], merges, head_sha=SHA2, review_base_sha=SHA1,
                target_sha=SHA3, build_id="build-1", result_tree_sha=SHA4,
                reviewed_diff=reviewed_diff)

    def test_ac8_delta_review_cannot_replay_after_target_change(self):
        self.admit(entry(11, SHA2))
        old_state = self.store.snapshot("AWF-EPIC")
        merges = [{"merge_commit_sha": SHA3, "paths": ["src/task-11.py"]}]
        digest = fingerprint("integration_intervening", merges)
        reviewed_diff = [{"path": "src/task-11.py", "base_blob_sha": SHA1,
                          "head_blob_sha": SHA2}]
        effective_diff = copy.deepcopy(reviewed_diff)
        delta = delta_review_requirement(["src/task-11.py"], merges, head_sha=SHA2,
            review_base_sha=SHA1, target_sha=SHA1,
            build_id=old_state["current_build"]["build_id"],
            result_tree_sha=old_state["current_build"]["result_tree_sha"],
            reviewed_diff=reviewed_diff, effective_diff=effective_diff,
            delta_review=delta_review_record(
                status="PASS", run_id="delta-old", producer_id="critic-1",
                head_sha=SHA2, review_base_sha=SHA1, target_sha=SHA1,
                build_id=old_state["current_build"]["build_id"],
                result_tree_sha=old_state["current_build"]["result_tree_sha"],
                merges=merges, reviewed_diff=reviewed_diff,
                effective_diff=effective_diff, code_change_required=False))
        self.store.rebuild(
            "AWF-EPIC", SHA1, runner(), "TARGET_MOVED",
            target_observer_id="fixture-git-provider", observed_at=NOW)
        state = self.store.snapshot("AWF-EPIC")
        self.assertEqual(old_state["current_build"]["input_id"],
                         state["current_build"]["input_id"])
        self.assertNotEqual(old_state["current_build"]["build_id"],
                            state["current_build"]["build_id"])
        self.assertNotEqual(delta["build_id"], state["current_build"]["build_id"])

    def test_ac9_independent_and_stacked_diffs_are_own_commits_only(self):
        independent = entry(11, SHA2)
        stacked = entry(12, SHA3, depends=(11,), base="awf/task-11")
        self.assertEqual("PASS", verify_own_commits(independent, [SHA2], [stacked])["status"])
        self.assertEqual("PASS", verify_own_commits(stacked, [SHA3], [independent])["status"])
        with self.assertRaisesRegex(ValidationError, "exactly its own commits"):
            verify_own_commits(stacked, [SHA2, SHA3], [independent])

    def test_ac9_admission_requires_observed_diff_and_retains_removed_commits(self):
        register_integration_observer(
            "fixture-foreign-range",
            lambda request: {"commits": [SHA2, SHA3]}, GIT_REGISTRY)
        with self.assertRaisesRegex(ValidationError, "exactly its own commits"):
            self.store.admit("AWF-EPIC", entry(11, SHA2), "fixture-foreign-range",
                             runner(), observed_at=NOW)
        self.admit(entry(11, SHA2))
        self.store.remove("AWF-EPIC", 11, runner(), observed_at=NOW)
        reused = entry(12, SHA2)
        with self.assertRaisesRegex(ValidationError, "already observed"):
            self.store.admit("AWF-EPIC", reused, "fixture-git-provider",
                             runner(), observed_at=NOW)

    def test_ac9_matching_caller_commit_lists_are_not_provider_observation(self):
        item = entry(11, SHA2)
        with self.assertRaisesRegex(ValidationError, "registered Git/provider observer"):
            self.store.admit("AWF-EPIC", item, list(item["own_commits"]),
                             runner(), observed_at=NOW)

    def test_ac7_independent_entry_must_target_epic_branch(self):
        with self.assertRaisesRegex(ValidationError, "epic target branch"):
            self.store.admit("AWF-EPIC", entry(11, SHA2, base="release"),
                             "fixture-git-provider", runner(), observed_at=NOW)

    def test_ac10_steward_limits_revert_and_epic_close(self):
        policy = steward_policy()
        self.assertIsNone(policy["model_route"])
        self.assertEqual(0, policy["model_tokens"])
        self.assertFalse(policy["may_edit_task_branches"])
        self.assertFalse(policy["may_merge_target"])
        self.assertEqual("OPEN_REVERT_PR", post_merge_action(
            {"producer_id": "fixture-git-provider"},
            provider_observer_registry=GIT_REGISTRY,
            project_config=PROJECT_CONFIG, target_branch="main", target_sha=SHA1,
            now=NOW)["action"])
        closed = self.store.close("AWF-EPIC", observed_at=NOW)
        self.assertEqual("awf/int/awf-epic", closed["delete_branch"])
        self.assertFalse(closed["execution_authority"])

    def test_ac12_serial_default_and_epic_activation_binding(self):
        self.assertEqual(DEFAULTS, resolve_mode())
        owner = instruction("ENABLE_EPIC_MODE", {
            "test_command": "python -B -m unittest", "stall_after_hours": 24,
            "max_stack_depth": 2}, "enable-1")
        self.assertEqual("epic", resolve_mode(
            {"test_command": "python -B -m unittest"}, owner_instruction=owner,
            epic="AWF-EPIC", project_config=PROJECT_CONFIG,
            app_binding={"producer_id": "fixture-mode-provider"},
            mode_observer_registry=MODE_REGISTRY, now=NOW)["mode"])
        config = {"mode": "epic", "test_command": "python -B -m unittest", "provenance": {
            "source": "reviewed_config", "config_path": ".agentic/integration-queue-config.yaml",
            "config_sha256": "a" * 64, "review_record_id": "review-1",
            "reviewed_at": NOW, "producer_id": "config-validator"}}
        advisory = resolve_mode(
            config, owner, {"producer_id": "fixture-mode-provider"}, epic="AWF-EPIC",
            project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY, now=NOW)
        self.assertEqual("epic", advisory["mode"])

        def app_binding(request, required):
            if request["operation"] != "APP_BINDING":
                return observed_mode(request)
            return {"producer_id": f"fixture-app-{required}",
                    "provider_identity": copy.deepcopy(request["provider_identity"]),
                    "repository_identity": copy.deepcopy(request["repository_identity"]),
                    "source_identity": "repository_branch_rules", "enforced": True,
                    "check_name": "awf-integration", "integration_id": 7,
                    "required_from_integration_id": required}

        register_integration_observer(
            "fixture-app-False", lambda request: app_binding(request, False),
            MODE_REGISTRY)
        with self.assertRaisesRegex(ValidationError, "App integration ID"):
            resolve_mode(config, owner, {"producer_id": "fixture-app-False"},
                         epic="AWF-EPIC",
                         project_config=PROJECT_CONFIG,
                         mode_observer_registry=MODE_REGISTRY, now=NOW)
        register_integration_observer(
            "fixture-app-True", lambda request: app_binding(request, True),
            MODE_REGISTRY)
        enforced = resolve_mode(config, owner, {"producer_id": "fixture-app-True"},
            epic="AWF-EPIC",
            project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY, now=NOW)
        self.assertEqual("epic", enforced["mode"])

    def test_ac12_mode_authority_has_no_direct_callback_trust_points(self):
        parameters = inspect.signature(resolve_mode).parameters
        self.assertNotIn("owner_instruction_observer", parameters)
        self.assertNotIn("reviewed_config_observer", parameters)
        self.assertIn("mode_observer_registry", parameters)

    def test_provider_and_build_observations_are_fresh_when_consumed(self):
        stale_at = "2026-10-10T08:00:00Z"
        register_integration_observer(
            "fixture-stale-conflict", observed_git, GIT_REGISTRY,
            observed_at=stale_at)
        with self.assertRaisesRegex(ValidationError, "stale"):
            self.store.conflict(
                "AWF-EPIC", entry(11, SHA2), "fixture-stale-conflict",
                observed_at=NOW)
        later = "2026-10-10T12:00:00Z"
        with self.assertRaisesRegex(ValidationError, "stale"):
            self.store.admit(
                "AWF-EPIC", entry(11, SHA2), "fixture-git-provider", runner(),
                observed_at=later)

    def test_ac12_fabricated_mode_records_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "provenance"):
            resolve_mode({"mode": "epic", "test_command": "python -B -m unittest"},
                         epic="AWF-EPIC", project_config=PROJECT_CONFIG,
                         app_binding={"producer_id": "fixture-mode-provider"},
                         mode_observer_registry=MODE_REGISTRY, now=NOW)
        other = IntegrationQueueStore(
            Path(self.temporary.name) / "fabricated.sqlite3",
            project_config=PROJECT_CONFIG,
            mode_observer_registry=MODE_REGISTRY,
            git_observer_registry=GIT_REGISTRY)
        with self.assertRaisesRegex(ValidationError, "closed accepted"):
            other.initialize("AWF-EPIC", "main", SHA1,
                             target_observer_id="fixture-git-provider",
                             mode_record={"mode": "epic"}, observed_at=NOW)

    def test_ac12_epic_mode_and_queue_commands_require_authenticated_provenance(self):
        owner = instruction("ENABLE_EPIC_MODE", {
            "test_command": "python -B -m unittest", "stall_after_hours": 24,
            "max_stack_depth": 2}, "auth-required")
        with self.assertRaisesRegex(ValidationError, "registered Git/provider observer"):
            resolve_mode({"test_command": "python -B -m unittest"},
                         owner_instruction=owner, epic="AWF-EPIC",
                         project_config=PROJECT_CONFIG, now=NOW)
        config = {"mode": "epic", "test_command": "python -B -m unittest",
                  "provenance": {"source": "reviewed_config",
                    "config_path": ".agentic/integration-queue-config.yaml",
                    "config_sha256": "a" * 64, "review_record_id": "review-1",
                    "reviewed_at": NOW, "producer_id": "config-validator"}}
        with self.assertRaisesRegex(ValidationError, "registered Git/provider observer"):
            resolve_mode(config, epic="AWF-EPIC", project_config=PROJECT_CONFIG,
                         now=NOW)

        self.admit(entry(11, SHA2))
        self.admit(entry(12, SHA3))
        untrusted = instruction("REORDER", {"order": [12, 11]}, "untrusted")
        untrusted["owner_actor_id"] = 9999
        material = {key: item for key, item in untrusted.items() if key != "instruction_id"}
        untrusted["instruction_id"] = fingerprint("integration_owner_instruction", material)
        with self.assertRaisesRegex(ValidationError, "configured trusted owner"):
            self.store.owner_reorder(
                "AWF-EPIC", [12, 11], untrusted, runner(), observed_at=NOW)

    def test_worker_guidance_covers_untrusted_context_dependencies_and_target(self):
        self.assertIn("untrusted context", WORKER_GUIDANCE)
        self.assertIn("depends_on", WORKER_GUIDANCE)
        self.assertIn("branch from the target", WORKER_GUIDANCE)

    def test_state_must_be_outside_declared_worktree(self):
        root = Path(self.temporary.name) / "worktree"
        root.mkdir()
        with self.assertRaisesRegex(ValidationError, "outside every worktree"):
            IntegrationQueueStore(root / "state.sqlite3", worktree_roots=[root])

    def test_entry_point_exposes_worker_guidance(self):
        script = Path(__file__).resolve().parents[1] / "scripts/integration_queue.py"
        spec = importlib.util.spec_from_file_location("integration_queue_entrypoint", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["worker-guidance"])
        self.assertEqual(0, code)
        self.assertEqual(WORKER_GUIDANCE, json.loads(output.getvalue())["guidance"])

    def test_entry_point_refuses_unauthenticated_build_documents(self):
        script = Path(__file__).resolve().parents[1] / "scripts/integration_queue.py"
        spec = importlib.util.spec_from_file_location("integration_queue_entrypoint", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = Path(self.temporary.name) / "cli.sqlite3"
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["--state", str(state), "admit", "--epic", "AWF-EPIC",
                "--entry", "entry.json", "--outcome", "outcome.json",
                "--observed-diff-commits", "commits.json"])
        self.assertEqual(2, code)
        self.assertIn("standalone CLI cannot authenticate", output.getvalue())

    def test_entry_point_has_no_caller_conflict_paths_or_stall_threshold(self):
        script = Path(__file__).resolve().parents[1] / "scripts/integration_queue.py"
        source = script.read_text(encoding="utf-8")
        self.assertNotIn('sub.add_parser("conflict")', source)
        self.assertNotIn('add_argument("--hours"', source)


if __name__ == "__main__":
    unittest.main()
