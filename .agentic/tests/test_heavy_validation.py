"""Adversarial synthetic tests for tuple-bound heavy validation."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, sha256, timestamp
from agentic import heavy_validation as heavy
from agentic import heavy_validation_controller as heavy_controller
from agentic.heavy_validation import (_engine_identity, resolve_without_alias, run_validation,
                                      workload_authorization)
from agentic.heavy_validation_controller import (
    FileLeaseBroker, GitCheckoutAttestor, GitCheckoutSnapshotter,
    GitHubReviewAuthenticator, read_result_log, write_result_log)

NOW = "2026-10-02T09:00:00Z"
CANDIDATE = {"repository_id": 101, "base_sha": "a" * 40,
             "head_sha": "b" * 40, "tree_sha": "c" * 40}


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def config(*, enabled=True, heavy=2, gpu=1, resources=None, extras=None):
    return {"execution": {"host_broker": {
        "enabled": enabled, "broker_id": "synthetic-broker" if enabled else "",
        "lease_before_dispatch": True, "max_workers": 6,
        "max_heavy_jobs": heavy, "max_gpu_jobs": gpu,
        "resources": dict(resources or {})},
        "child_env_strip_extra": list(extras or []),
        "max_tokens_per_ticket": 100000,
        "max_cost_microusd_per_ticket": 10000000,
        "daily_project_cost_microusd": 50000000,
        "native_streams": {"enabled": True, "dispatch_policy": "ready_independent"}}}


def partition(name, code="print('ok')", *, timeout=5, exits=None):
    executable = str(Path(sys.executable).resolve())
    return {"name": name, "framework": "command", "resources": {},
            "argv": [executable, "-c", code],
            "executable": {"path": executable, "sha256": file_digest(executable)},
            "timeout_seconds": timeout, "accepted_exit_codes": list(exits or [0])}


def plan(parts, *, engine="python", resource_class="heavy", resources=None,
         parallelism=4, candidate=None, cwd=None, seed=17, retries=0):
    return canonical({"format": "awf-heavy-validation-plan-3",
        "workload_id": "qa9430-synthetic", "engine": engine,
        "resource_class": resource_class, "required_resources": list(resources or []),
        "requested_parallelism": parallelism, "candidate": candidate or CANDIDATE,
        "working_directory": str(Path(cwd or SOURCE_ROOT).resolve()),
        "determinism": {"seed": seed, "retry_limit": retries},
        "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                      "filesystem": "WORKTREE"}, "partitions": parts})


def review(plan_raw, *, decision="APPROVE", candidate=None):
    plan_value = json.loads(plan_raw)
    authorization = workload_authorization(plan_value, sha256(plan_raw))
    return canonical({"format": "awf-heavy-validation-review-5",
        "plan_sha256": sha256(plan_raw), "candidate": candidate or CANDIDATE,
        "decision": decision, "workload_authorization": authorization,
        "reviewer": {"provider": "fixture", "immutable_id": "reviewer-101", "login": "critic"},
        "reviewed_at": NOW, "expires_at": "2026-10-03T09:00:00Z"})


def authenticator(value, review_digest, plan_digest, candidate, authorization):
    return {"status": "AUTHENTICATED", **value["reviewer"],
            "evidence_sha256": "d" * 64, "review_sha256": review_digest,
            "plan_sha256": plan_digest, "candidate": candidate,
            "workload_authorization_sha256": authorization["sha256"]}


def checkout_attestor(candidate, working_directory):
    return {"status": "CLEAN", "candidate": candidate,
            "working_directory": working_directory, "evidence_sha256": "e" * 64}


@contextmanager
def checkout_snapshotter(candidate, working_directory):
    with tempfile.TemporaryDirectory(prefix="awf-heavy-test-snapshot-") as folder:
        snapshot = str(Path(folder).resolve())
        launcher = Path(snapshot) / ".agentic/lib/agentic/heavy_validation_child.py"
        launcher.parent.mkdir(parents=True)
        shutil.copy2(SOURCE_ROOT / ".agentic/lib/agentic/heavy_validation_child.py", launcher)
        record = {"candidate": deepcopy(candidate),
                  "source_working_directory": working_directory,
                  "snapshot_working_directory": snapshot,
                  "tree_sha": candidate["tree_sha"],
                  "archive_sha256": "f" * 64, "file_count": 1,
                  "mutation_guard": "windows-deny-write-delete-handles",
                  "guarded_paths": 5}
        yield {"status": "IMMUTABLE", **record,
               "evidence_sha256": fingerprint("heavy-validation-checkout-snapshot", record)}


def capacity(plan_raw, config_raw, *, workers=6, heavy=2, gpu=1, resources=None,
             engines=None, observed_at=NOW, candidate=None):
    identity = _engine_identity(json.loads(plan_raw))
    engine_values = ({"python": {"identity_sha256": identity,
                                  "parallel_available": True, "parallel_slots": workers}}
                     if engines is None else deepcopy(engines))
    for evidence in engine_values.values():
        evidence.setdefault("identity_sha256", identity)
    return canonical({"format": "awf-heavy-validation-capacity-4",
        "observed_at": observed_at, "broker_id": "synthetic-broker",
        "workers_available": workers, "heavy_jobs_available": heavy,
        "gpu_jobs_available": gpu, "resources_available": dict(resources or {}),
        "engines": engine_values,
        "plan_sha256": sha256(plan_raw), "config_sha256": sha256(config_raw),
        "candidate": candidate or CANDIDATE})


class Broker:
    def __init__(self, mode="grant"):
        self.mode, self.acquired, self.released = mode, [], []

    def acquire(self, request):
        self.acquired.append(deepcopy(request))
        if self.mode == "raise":
            raise RuntimeError("unavailable")
        if self.mode == "deny":
            return {"status": "DENIED", "reason": "capacity raced"}
        if self.mode == "deny_parallel" and request["parallelism"] > 1:
            return {"status": "DENIED", "reason": "parallel capacity raced"}
        expiry = (timestamp(NOW) + timedelta(
            seconds=request["required_duration_seconds"])).isoformat(
                timespec="seconds").replace("+00:00", "Z")
        if self.mode in {"stale", "stale_release_fail"}:
            expiry = "2026-10-02T08:59:59Z"
        return {"status": "GRANTED", "lease_id": "lease-1", "fencing_token": 7,
                "broker_id": "synthetic-broker", "acquired_at": NOW,
                "expires_at": expiry,
                "request_sha256": fingerprint("heavy-validation-lease-request", request)}

    def release(self, lease_id, fencing_token):
        self.released.append((lease_id, fencing_token))
        if self.mode in {"release_fail", "stale_release_fail"}:
            return {"status": "STALE", "lease_id": lease_id, "fencing_token": fencing_token}
        return {"status": "RELEASED", "lease_id": lease_id, "fencing_token": fencing_token}


def run(plan_raw, cfg, *, cap=None, broker=None, auth=authenticator,
        cancel_event=None, candidate=None, cwd=None, lease_clock=None):
    cfg_raw = canonical(cfg)
    review_raw = review(plan_raw)
    return run_validation(plan_raw=plan_raw, expected_plan_sha256=sha256(plan_raw),
        review_raw=review_raw, expected_review_sha256=sha256(review_raw),
        config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
        expected_candidate=candidate or CANDIDATE,
        execution_root=str(Path(cwd or SOURCE_ROOT).resolve()),
        review_authenticator=auth, checkout_attestor=checkout_attestor,
        checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
        expected_capacity_sha256=sha256(cap) if cap is not None else None,
        broker_client=broker, now=NOW, cancel_event=cancel_event,
        lease_clock=lease_clock or (lambda: NOW))


class HeavyValidationTests(unittest.TestCase):
    def test_parallel_requires_exact_lease_and_releases_fence_after_barrier(self):
        raw = plan([partition("a"), partition("b")], parallelism=2)
        cfg, broker = config(), Broker()
        cap = capacity(raw, canonical(cfg))
        result = run(raw, cfg, cap=cap, broker=broker)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("PARALLEL", result["execution"]["mode"])
        self.assertEqual([("lease-1", 7)], broker.released)
        self.assertEqual("RELEASED", result["lease_release"]["status"])
        self.assertEqual(2, result["execution"]["completed_terminal_count"])

    def test_parallel_denial_requires_a_separate_governed_serial_lease(self):
        raw, cfg = plan([partition("a"), partition("b")], parallelism=2), config()
        cap = capacity(raw, canonical(cfg))
        broker = Broker("deny_parallel")
        result = run(raw, cfg, cap=cap, broker=broker)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("SERIAL", result["execution"]["mode"])
        self.assertEqual([2, 1], [item["parallelism"] for item in broker.acquired])
        self.assertEqual([("lease-1", 7)], broker.released)

    def test_governed_serial_requires_admission_and_zero_capacity_starts_no_child(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "started.txt"
            raw = plan([partition("only", "import pathlib; pathlib.Path(" +
                       repr(str(marker)) + ").write_text('started')")], parallelism=1)
            cfg, broker = config(), Broker()
            cap = capacity(raw, canonical(cfg), workers=0, heavy=0)
            result = run(raw, cfg, cap=cap, broker=broker)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual("NOT_STARTED", result["execution"]["mode"])
            self.assertFalse(result["admission"]["granted"])
            self.assertEqual([], broker.acquired)
            self.assertFalse(marker.exists())
            good = capacity(raw, canonical(cfg), workers=1, heavy=1)
            admitted = run(raw, cfg, cap=good, broker=broker)
            self.assertEqual("PASS", admitted["status"])
            self.assertEqual(1, broker.acquired[-1]["parallelism"])

    def test_unavailable_or_denied_serial_admission_starts_no_child(self):
        raw, cfg = plan([partition("only")], parallelism=1), config()
        cap = capacity(raw, canonical(cfg), workers=1, heavy=1)
        for broker in (None, Broker("deny"), Broker("raise")):
            with self.subTest(broker=broker):
                result = run(raw, cfg, cap=cap, broker=broker)
                self.assertEqual("NOT_STARTED", result["execution"]["mode"])
                self.assertEqual([], result["partitions"])

    def test_stale_or_granted_fence_release_failure_blocks(self):
        raw, cfg = plan([partition("a"), partition("b")], parallelism=2), config()
        cap = capacity(raw, canonical(cfg))
        stale = run(raw, cfg, cap=cap, broker=Broker("stale_release_fail"))
        self.assertEqual("NOT_STARTED", stale["execution"]["mode"])
        self.assertEqual("FAIL", stale["status"])
        self.assertFalse(stale["admission"]["release_complete"])
        granted = run(raw, cfg, cap=cap, broker=Broker("release_fail"))
        self.assertEqual("FAIL", granted["status"])
        self.assertEqual("FAILED", granted["lease_release"]["status"])

    def test_review_is_unavailable_without_trusted_authenticator(self):
        raw = plan([partition("only")])
        with self.assertRaisesRegex(ValidationError, "Authenticated review authority is unavailable"):
            run(raw, config(enabled=False), auth=None)

    def test_authenticator_and_candidate_movement_fail_before_execution(self):
        raw = plan([partition("only")])
        def wrong(value, review_digest, plan_digest, candidate, authorization):
            result = authenticator(value, review_digest, plan_digest, candidate, authorization)
            result["immutable_id"] = "attacker"
            return result
        with self.assertRaisesRegex(ValidationError, "immutable_id"):
            run(raw, config(enabled=False), auth=wrong)
        moved = {**CANDIDATE, "head_sha": "e" * 40}
        with self.assertRaisesRegex(ValidationError, "Expected candidate tuple"):
            run(raw, config(enabled=False), candidate=moved)

    def test_cwd_and_executable_digest_are_reviewed_and_enforced(self):
        raw = plan([partition("only")])
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaisesRegex(ValidationError, "cwd"):
                run(raw, config(enabled=False), cwd=other)
        value = json.loads(raw)
        value["partitions"][0]["executable"]["sha256"] = "0" * 64
        changed = canonical(value)
        with self.assertRaisesRegex(ValidationError, "executable digest mismatch"):
            run(changed, config(enabled=False))

    def test_capacity_and_config_are_digest_and_tuple_bound(self):
        raw, cfg = plan([partition("a"), partition("b")]), config()
        cfg_raw, cap = canonical(cfg), capacity(raw, canonical(cfg))
        changed_cfg = config(heavy=3)
        with self.assertRaisesRegex(ValidationError, "capacity does not bind"):
            run(raw, changed_cfg, cap=cap, broker=Broker())
        moved = capacity(raw, cfg_raw, candidate={**CANDIDATE, "tree_sha": "f" * 40})
        with self.assertRaisesRegex(ValidationError, "capacity candidate"):
            run(raw, cfg, cap=moved, broker=Broker())

    def test_widened_and_configured_credentials_are_stripped(self):
        names = ["ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY", "GEMINI_API_KEY",
                 "GOOGLE_API_KEY", "SYNTHETIC_EXTRA_SECRET"]
        code = "import os; print('|'.join(os.environ.get(k, 'absent') for k in " + repr(names) + "))"
        with mock.patch.dict(os.environ, {name: "secret" for name in names}):
            result = run(plan([partition("env", code)]),
                         config(enabled=False, extras=["SYNTHETIC_EXTRA_SECRET"]))
        self.assertEqual("|".join(["absent"] * len(names)), result["partitions"][0]["stdout"].strip())
        for protected in ("GH_TOKEN", "GITHUB_TOKEN"):
            with self.assertRaisesRegex(ValidationError, "cannot name"):
                run(plan([partition("x")]), config(enabled=False, extras=[protected]))

    def test_timeout_terminates_descendant_tree_and_records_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "survived.txt"
            child = "import time,pathlib; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('bad')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(10)"
            result = run(plan([partition("tree", parent, timeout=1)]), config(enabled=False))
            time.sleep(2.2)
            self.assertFalse(marker.exists())
            item = result["partitions"][0]
            self.assertEqual("TIMED_OUT", item["state"])
            self.assertEqual("COMPLETE", item["process_tree_cleanup"]["outcome"])

    def test_lexical_symlink_or_reparse_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target, link = Path(folder) / "target", Path(folder) / "alias"
            target.mkdir()
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symlink privilege unavailable")
            with self.assertRaisesRegex(ValidationError, "symlink or reparse"):
                resolve_without_alias(link, "fixture", directory=True)

    def test_all_partition_barrier_failure_and_deterministic_order(self):
        raw = plan([partition("z", "print('z')"),
                    partition("a", "raise SystemExit(7)")], parallelism=2)
        cfg, broker = config(), Broker()
        result = run(raw, cfg, cap=capacity(raw, canonical(cfg)), broker=broker)
        self.assertEqual("FAIL", result["status"])
        self.assertTrue(result["all_partitions_terminal"])
        self.assertEqual(["a", "z"], [item["name"] for item in result["partitions"]])
        self.assertEqual(2, result["execution"]["completed_terminal_count"])

    def test_structured_argv_shell_false_and_caps_are_preserved(self):
        raw = plan([partition("only")])
        cfg, original = config(enabled=False), config(enabled=False)
        result = run(raw, cfg)
        self.assertEqual("PASS", result["status"])
        self.assertFalse(result["execution"]["shell"])
        self.assertEqual(original, cfg)
        self.assertEqual({"max_tokens_per_ticket": 100000,
                          "max_cost_microusd_per_ticket": 10000000,
                          "daily_project_cost_microusd": 50000000}, result["preserved_caps"])

    def test_missing_failed_cancelled_and_large_output_are_fail_closed_or_bounded(self):
        event = threading.Event(); event.set()
        cancelled = run(plan([partition("a"), partition("b")]), config(enabled=False), cancel_event=event)
        self.assertEqual({"CANCELLED"}, {item["state"] for item in cancelled["partitions"]})
        failed = run(plan([partition("bad", "raise SystemExit(9)")]), config(enabled=False))
        self.assertEqual("FAIL", failed["status"])
        large = run(plan([partition("large", "import sys; sys.stdout.write('x'*400000)")]), config(enabled=False))
        self.assertTrue(large["partitions"][0]["stdout_truncated"])
        self.assertEqual(400000, large["partitions"][0]["stdout_bytes"])

    def test_seed_and_retry_are_bound_and_record_every_attempt(self):
        code = ("import os; print(os.environ['AWF_VALIDATION_SEED']); "
                "raise SystemExit(0 if os.environ['AWF_VALIDATION_ATTEMPT']=='2' else 9)")
        result = run(plan([partition("retry", code)], parallelism=1, seed=314, retries=1),
                     config(enabled=False))
        item = result["partitions"][0]
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, item["retry_count"])
        self.assertEqual(["FAILED", "PASS"], [entry["state"] for entry in item["attempts"]])
        self.assertEqual("314", item["stdout"].strip())

    def test_incomplete_cleanup_never_retries_or_launches_another_child(self):
        value = json.loads(plan([partition("cleanup")], parallelism=1, retries=2))
        part = value["partitions"][0]
        executable = {"path": part["executable"]["path"],
                      "resolved_path": part["executable"]["path"],
                      "sha256": part["executable"]["sha256"]}
        failed = {"attempt": 1, "state": "FAILED", "exit_code": None,
                  "timed_out": False, "cancelled": False, "error_type": "CleanupError",
                  "process_tree_cleanup": {"outcome": "PARTIAL",
                                           "mechanism": "direct_process_only"},
                  "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64}
        with mock.patch.object(heavy, "_execute_attempt", return_value=failed) as execute:
            result = heavy._execute(part, value, executable, SOURCE_ROOT,
                                    config(enabled=False), threading.Event(),
                                    heavy._windows_launch_chain(), lambda: None)
        self.assertEqual(1, execute.call_count)
        self.assertEqual(0, result["retry_count"])

    def test_parallel_and_serial_results_have_same_equivalence_proof(self):
        raw = plan([partition("b", "print('b')"), partition("a", "print('a')")],
                   parallelism=2)
        parallel_cfg = config()
        parallel = run(raw, parallel_cfg, cap=capacity(raw, canonical(parallel_cfg)),
                       broker=Broker())
        serial = run(raw, config(enabled=False))
        self.assertEqual("PASS", parallel["status"])
        self.assertEqual("PASS", serial["status"])
        self.assertEqual(parallel["serial_equivalence_sha256"],
                         serial["serial_equivalence_sha256"])

    def test_named_resource_exhaustion_starts_no_child(self):
        part = partition("licensed")
        part["resources"] = {"licensed_engine": 2}
        raw = plan([part], parallelism=1, resources=["licensed_engine"])
        cfg = config(resources={"licensed_engine": 1})
        cap = capacity(raw, canonical(cfg), workers=1, heavy=1,
                       resources={"licensed_engine": 1})
        broker = Broker()
        result = run(raw, cfg, cap=cap, broker=broker)
        self.assertEqual("NOT_STARTED", result["execution"]["mode"])
        self.assertEqual([], broker.acquired)

    def test_gpu_and_engine_zero_capacity_start_no_child(self):
        for cap_kwargs in (
            {"gpu": 0},
            {"gpu": 1, "engines": {"python": {"parallel_available": True,
                                                "parallel_slots": 0}}},
        ):
            with self.subTest(capacity=cap_kwargs):
                raw = plan([partition("gpu")], parallelism=1, resource_class="gpu")
                cfg = config(gpu=1)
                cap = capacity(raw, canonical(cfg), workers=1, heavy=1, **cap_kwargs)
                broker = Broker()
                result = run(raw, cfg, cap=cap, broker=broker)
                self.assertEqual("NOT_STARTED", result["execution"]["mode"])
                self.assertEqual([], broker.acquired)

    def test_cancellation_during_work_reaches_complete_terminal_barrier(self):
        event = threading.Event()
        timer = threading.Timer(.15, event.set)
        timer.start()
        try:
            raw = plan([partition("a", "import time; time.sleep(3)"),
                        partition("b", "import time; time.sleep(3)")], parallelism=2)
            result = run(raw, config(enabled=False), cancel_event=event)
        finally:
            timer.cancel()
        self.assertEqual("FAIL", result["status"])
        self.assertTrue(result["all_partitions_terminal"])
        self.assertEqual({"CANCELLED"}, {item["state"] for item in result["partitions"]})

    def test_framework_adapter_shapes_are_bound(self):
        unit = partition("unit")
        unit["framework"] = "python-unittest"
        unit["argv"] = [unit["argv"][0], "-m", "unittest", "-h"]
        result = run(plan([unit], parallelism=1), config(enabled=False))
        self.assertEqual("PASS", result["status"])
        invalid = deepcopy(unit)
        invalid["framework"] = "pytest"
        with self.assertRaisesRegex(ValidationError, "pytest adapter"):
            run(plan([invalid], parallelism=1), config(enabled=False))
        for framework, program, tail in (
            ("pytest", Path(sys.executable), ["-m", "pytest", "tests"]),
            ("matlab", Path(sys.executable).parent / "matlab.exe", ["-batch", "runTests"]),
            ("wolfram", Path(sys.executable).parent / "wolframscript.exe", ["-code", "TestReport[]"]),
        ):
            value = json.loads(plan([partition("shape")], parallelism=1))
            value["partitions"][0]["framework"] = framework
            value["partitions"][0]["executable"]["path"] = str(program)
            value["partitions"][0]["argv"] = [str(program), *tail]
            encoded = canonical(value)
            heavy._validate_plan(encoded, sha256(encoded))

    def test_windows_containment_failure_never_resumes_suspended_child(self):
        class FakeProcess:
            def __init__(self):
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
                self.returncode, self.killed, self._handle, self._thread = None, False, 1, 2
            def poll(self):
                return self.returncode
            def kill(self):
                self.killed, self.returncode = True, 1
            def wait(self, timeout=None):
                return self.returncode

        raw = plan([partition("contained")], parallelism=1)
        value = json.loads(raw)
        part = value["partitions"][0]
        executable = {"path": part["executable"]["path"],
                      "resolved_path": part["executable"]["path"],
                      "sha256": part["executable"]["sha256"]}
        fake = FakeProcess()
        with mock.patch.object(heavy.os, "name", "nt"), \
             mock.patch.object(heavy.subprocess, "Popen", return_value=fake), \
             mock.patch.object(heavy, "_attach_windows_job",
                               side_effect=ValidationError("containment unavailable")), \
             mock.patch.object(heavy, "_release_windows_launcher") as release:
            result = heavy._execute_attempt(part, value, executable, SOURCE_ROOT,
                                            config(enabled=False), threading.Event(), 1,
                                            heavy._windows_launch_chain(), lambda: None)
        self.assertTrue(fake.killed)
        release.assert_not_called()
        self.assertEqual("FAILED", result["state"])
        self.assertEqual({"outcome": "COMPLETE", "mechanism": "contained_launcher_terminated"},
                         result["process_tree_cleanup"])

    def test_github_authenticator_binds_provider_repository_pr_review_and_tree(self):
        authorization = {"record": {"fixture": True}, "sha256": "f" * 64}
        endpoints = {
            "repos/example/project": {"id": 101},
            "repos/example/project/pulls/7": {"number": 7,
                "head": {"sha": CANDIDATE["head_sha"]},
                "base": {"sha": CANDIDATE["base_sha"]}},
            f"repos/example/project/git/commits/{CANDIDATE['head_sha']}": {
                "sha": CANDIDATE["head_sha"], "tree": {"sha": CANDIDATE["tree_sha"]}},
            "repos/example/project/pulls/7/reviews/9": {"id": 9, "state": "APPROVED",
                "commit_id": CANDIDATE["head_sha"],
                "body": "AWF-HEAVY-VALIDATION-AUTHORIZATION-SHA256: " + authorization["sha256"],
                "user": {"id": 42, "login": "reviewer"}},
        }
        auth = GitHubReviewAuthenticator("example/project", 7, 9,
                                         read_api=lambda endpoint: deepcopy(endpoints[endpoint]))
        review_value = {"reviewer": {"provider": "github", "immutable_id": "42",
                                     "login": "reviewer"}}
        result = auth(review_value, "d" * 64, "e" * 64, CANDIDATE, authorization)
        self.assertEqual("AUTHENTICATED", result["status"])
        moved = deepcopy(endpoints)
        moved["repos/example/project/pulls/7"]["head"]["sha"] = "f" * 40
        bad = GitHubReviewAuthenticator("example/project", 7, 9,
                                        read_api=lambda endpoint: deepcopy(moved[endpoint]))
        with self.assertRaisesRegex(ValidationError, "PR tuple"):
            bad(review_value, "d" * 64, "e" * 64, CANDIDATE, authorization)
        unauthorized = deepcopy(endpoints)
        unauthorized["repos/example/project/pulls/7/reviews/9"]["body"] = (
            "AWF-HEAVY-VALIDATION-AUTHORIZATION-SHA256: " + "0" * 64)
        bad = GitHubReviewAuthenticator(
            "example/project", 7, 9,
            read_api=lambda endpoint: deepcopy(unauthorized[endpoint]))
        with self.assertRaisesRegex(ValidationError, "exact workload"):
            bad(review_value, "d" * 64, "e" * 64, CANDIDATE, authorization)

    def test_production_authenticator_and_durable_broker_run_parallel(self):
        raw = plan([partition("a"), partition("b")], parallelism=2)
        cfg_raw = canonical(config())
        cap = capacity(raw, cfg_raw, workers=2, heavy=2)
        authorization = workload_authorization(json.loads(raw), sha256(raw))
        review_raw = canonical({"format": "awf-heavy-validation-review-5",
            "plan_sha256": sha256(raw), "candidate": CANDIDATE, "decision": "APPROVE",
            "workload_authorization": authorization,
            "reviewer": {"provider": "github", "immutable_id": "42", "login": "reviewer"},
            "reviewed_at": NOW, "expires_at": "2026-10-03T09:00:00Z"})
        endpoints = {
            "repos/example/project": {"id": 101},
            "repos/example/project/pulls/7": {"number": 7,
                "head": {"sha": CANDIDATE["head_sha"]}, "base": {"sha": CANDIDATE["base_sha"]}},
            f"repos/example/project/git/commits/{CANDIDATE['head_sha']}": {
                "sha": CANDIDATE["head_sha"], "tree": {"sha": CANDIDATE["tree_sha"]}},
            "repos/example/project/pulls/7/reviews/9": {"id": 9, "state": "APPROVED",
                "commit_id": CANDIDATE["head_sha"],
                "body": "AWF-HEAVY-VALIDATION-AUTHORIZATION-SHA256: " + authorization["sha256"],
                "user": {"id": 42, "login": "reviewer"}},
        }
        auth = GitHubReviewAuthenticator("example/project", 7, 9,
                                         read_api=lambda endpoint: deepcopy(endpoints[endpoint]))
        clock = lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        with tempfile.TemporaryDirectory() as folder:
            broker = FileLeaseBroker(Path(folder) / "broker.json", "synthetic-broker",
                {"max_workers": 2, "max_heavy_jobs": 2, "max_gpu_jobs": 0,
                 "resources": {}, "engines": {"python": {
                     "identity_sha256": heavy._engine_identity(json.loads(raw)), "slots": 2}}},
                clock=clock)
            result = run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=auth, checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=lambda: NOW)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("PARALLEL", result["execution"]["mode"])
        self.assertTrue(result["admission"]["release_complete"])

    def test_durable_file_broker_fences_capacity_and_result_log_is_immutable(self):
        clock = lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        with tempfile.TemporaryDirectory() as folder:
            broker = FileLeaseBroker(Path(folder) / "broker.json", "fixture",
                {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                 "resources": {}, "engines": {"python": {
                     "identity_sha256": "d" * 64, "slots": 1}}}, clock=clock)
            request = {"format": "awf-heavy-validation-lease-request-2",
                       "candidate": CANDIDATE, "plan_sha256": "a" * 64,
                       "config_sha256": "b" * 64, "capacity_sha256": "c" * 64,
                       "parallelism": 1, "resource_class": "heavy", "engine": "python",
                       "engine_identity_sha256": "d" * 64, "engine_slots": 1,
                       "required_resources": [], "resource_claims": {},
                       "determinism": {"seed": 1, "retry_limit": 0},
                       "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                                     "filesystem": "WORKTREE"},
                       "required_duration_seconds": 3600}
            first = broker.acquire(request)
            self.assertEqual("GRANTED", first["status"])
            self.assertEqual("DENIED", broker.acquire(request)["status"])
            self.assertEqual("RELEASED", broker.release(first["lease_id"],
                                                         first["fencing_token"])["status"])
            second = broker.acquire(request)
            self.assertGreater(second["fencing_token"], first["fencing_token"])
            log, receipt_path, key = (Path(folder) / "result.json",
                                      Path(folder) / "result.receipt.json", b"k" * 32)
            receipt = write_result_log(log, receipt_path, {"status": "PASS", "value": 1}, key)
            envelope = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(receipt["result_sha256"], envelope["result_sha256"])
            self.assertEqual("PASS", read_result_log(log, receipt_path, key)["result"]["status"])
            with self.assertRaises(FileExistsError):
                write_result_log(log, Path(folder) / "other.receipt.json",
                                 {"status": "PASS", "value": 2}, key)

    def test_workload_authorization_binds_every_dispatch_input(self):
        raw = plan([partition("bound")], parallelism=1)
        reviewed = json.loads(review(raw))
        reviewed["workload_authorization"]["record"]["partitions"][0]["argv"].append("moved")
        changed = canonical(reviewed)
        cfg = canonical(config(enabled=False))
        with self.assertRaisesRegex(ValidationError, "exact workload"):
            run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=changed, expected_review_sha256=sha256(changed),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, checkout_attestor=checkout_attestor,
                now=NOW)
        reviewed = json.loads(review(raw))
        reviewed["workload_authorization"]["record"]["windows_launch_chain"][
            "launcher"]["sha256"] = "0" * 64
        changed = canonical(reviewed)
        with self.assertRaisesRegex(ValidationError, "exact workload"):
            run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=changed, expected_review_sha256=sha256(changed),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, checkout_attestor=checkout_attestor,
                now=NOW)

    def test_checkout_attestation_is_required_clean_and_exact(self):
        raw = plan([partition("checkout")], parallelism=1)
        cfg = canonical(config(enabled=False))
        review_raw = review(raw)
        with self.assertRaisesRegex(ValidationError, "attestation is unavailable"):
            run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, now=NOW)
        outputs = {
            ("rev-parse", "--show-toplevel"): str(SOURCE_ROOT),
            ("rev-parse", "--verify", "HEAD"): CANDIDATE["head_sha"],
            ("rev-parse", "--verify", "HEAD^{tree}"): CANDIDATE["tree_sha"],
            ("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"):
                "dirty.txt",
        }
        attestor = GitCheckoutAttestor(SOURCE_ROOT, run_git=lambda args: outputs[tuple(args)])
        with self.assertRaisesRegex(ValidationError, "dirty"):
            attestor(CANDIDATE, str(SOURCE_ROOT))
        outputs[("status", "--porcelain=v1", "--untracked-files=all",
                 "--ignored=matching")] = ""
        outputs[("rev-parse", "--verify", "HEAD")] = "0" * 40
        with self.assertRaisesRegex(ValidationError, "HEAD/tree"):
            attestor(CANDIDATE, str(SOURCE_ROOT))

    def test_execution_requires_separate_immutable_checkout_snapshot(self):
        raw = plan([partition("checkout")], parallelism=1)
        cfg = canonical(config(enabled=False))
        review_raw = review(raw)
        with self.assertRaisesRegex(ValidationError, "snapshot provider is unavailable"):
            run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, checkout_attestor=checkout_attestor,
                now=NOW)

    def test_dispatch_rechecks_checkout_and_releases_lease_on_movement(self):
        raw, cfg = plan([partition("checkout")], parallelism=1), config()
        cfg_raw = canonical(cfg)
        cap, broker, calls = capacity(raw, cfg_raw, workers=1, heavy=1), Broker(), []
        def moves(candidate, working_directory):
            calls.append(working_directory)
            if len(calls) == 2:
                raise ValidationError("checkout moved before dispatch")
            return checkout_attestor(candidate, working_directory)
        review_raw = review(raw)
        with self.assertRaisesRegex(ValidationError, "Checkout attestation failed"):
            run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, checkout_attestor=moves,
                capacity_raw=cap, expected_capacity_sha256=sha256(cap),
                broker_client=broker, now=NOW, lease_clock=lambda: NOW)
        self.assertEqual(2, len(calls))
        self.assertEqual([("lease-1", 7)], broker.released)

    def test_production_checkout_attestor_reads_clean_head_and_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (root / "tracked.txt").write_text("clean\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-q", "-m", "fixture"],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                  stdout=subprocess.PIPE, text=True).stdout.strip()
            tree = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%T", "HEAD"],
                                  check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
            candidate = {**CANDIDATE, "head_sha": head, "tree_sha": tree}
            attestor = GitCheckoutAttestor(root)
            self.assertEqual("CLEAN", attestor(candidate, str(root))["status"])
            (root / "untracked.txt").write_text("dirty\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValidationError, "dirty"):
                attestor(candidate, str(root))

    def test_git_object_snapshot_isolated_from_checkout_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            tracked = root / "tracked.txt"
            tracked.write_text("reviewed\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-q", "-m", "fixture"],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                  stdout=subprocess.PIPE, text=True).stdout.strip()
            tree = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%T", "HEAD"],
                                  check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
            candidate = {**CANDIDATE, "head_sha": head, "tree_sha": tree}
            snapshotter = GitCheckoutSnapshotter(root)
            with snapshotter(candidate, str(root)) as evidence:
                snapshot_file = Path(evidence["snapshot_working_directory"]) / "tracked.txt"
                self.assertEqual("reviewed\n", snapshot_file.read_text(encoding="utf-8"))
                tracked.write_text("raced\n", encoding="utf-8", newline="\n")
                self.assertEqual("reviewed\n", snapshot_file.read_text(encoding="utf-8"))
                try:
                    snapshot_file.chmod(0o600)
                except OSError:
                    pass
                with self.assertRaises(OSError):
                    snapshot_file.write_text("bad\n", encoding="utf-8", newline="\n")
                self.assertEqual("reviewed\n", snapshot_file.read_text(encoding="utf-8"))
                self.assertEqual(tree, evidence["tree_sha"])
                self.assertEqual("IMMUTABLE", evidence["status"])

    def test_executable_fence_blocks_or_isolates_attestation_launch_race(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / Path(sys.executable).name
            shutil.copy2(sys.executable, target)
            expected = file_digest(target)
            executable = {"resolved_path": str(target), "sha256": expected}
            with heavy._immutable_executable(executable) as artifact:
                self.assertEqual(expected, artifact["sha256"])
                if os.name == "nt":
                    with self.assertRaises(OSError):
                        target.write_bytes(b"raced")
                    self.assertEqual(expected, file_digest(target))
                else:
                    target.write_bytes(b"raced")
                    self.assertEqual(expected, file_digest(artifact["launch_path"]))
                    self.assertNotEqual(str(target), artifact["launch_path"])

    def test_windows_launcher_bytes_are_reviewed_and_mutation_fenced(self):
        chain = heavy._windows_launch_chain()
        launcher = chain["launcher"]
        self.assertEqual(file_digest(SOURCE_ROOT / launcher["repository_relative_path"]),
                         launcher["sha256"])
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / "heavy_validation_child.py"
            shutil.copy2(SOURCE_ROOT / launcher["repository_relative_path"], copied)
            expected = file_digest(copied)
            with heavy._immutable_executable({"resolved_path": str(copied),
                                              "sha256": expected}) as artifact:
                if os.name == "nt":
                    try:
                        copied.chmod(0o600)
                    except OSError:
                        pass
                    with self.assertRaises(OSError):
                        copied.write_text("mutated", encoding="utf-8")
                else:
                    copied.write_text("mutated", encoding="utf-8")
                    self.assertEqual(expected, file_digest(artifact["launch_path"]))

    def test_engine_identity_and_slots_are_fenced(self):
        raw = plan([partition("a"), partition("b")], parallelism=2)
        cfg, cfg_raw = config(), canonical(config())
        moved = capacity(raw, cfg_raw, engines={"python": {
            "identity_sha256": "0" * 64, "parallel_available": True, "parallel_slots": 2}})
        with self.assertRaisesRegex(ValidationError, "engine identity"):
            run(raw, cfg, cap=moved, broker=Broker())
        cap = capacity(raw, cfg_raw, workers=2, heavy=2)
        broker_id, limits = heavy_controller.broker_limits(cfg, json.loads(cap))
        clock = lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        with tempfile.TemporaryDirectory() as folder:
            broker = FileLeaseBroker(Path(folder) / "broker.json", broker_id, limits, clock=clock)
            request = heavy._lease_request(json.loads(raw), sha256(raw), sha256(cfg_raw),
                                           sha256(cap), 2)
            first = broker.acquire(request)
            self.assertEqual("GRANTED", first["status"])
            self.assertEqual("DENIED", broker.acquire(request)["status"])
            state = json.loads((Path(folder) / "broker.json").read_text(encoding="utf-8"))
            self.assertEqual("python", state["leases"][0]["engine"])
            self.assertEqual(2, state["leases"][0]["engine_slots"])

    def test_lease_duration_covers_every_retry_and_all_coded_waits(self):
        raw = plan([partition("a", timeout=5), partition("b", timeout=5)],
                   parallelism=2, retries=2)
        request = heavy._lease_request(json.loads(raw), sha256(raw), "a" * 64,
                                       "b" * 64, 2)
        duration = request["required_duration_seconds"]
        per_attempt = (heavy.PROCESS_STARTUP_BUDGET_SECONDS
                       + (2 * heavy.PROCESS_TREE_TERMINATION_TIMEOUT_SECONDS)
                       + (2 * heavy.PROCESS_EXIT_WAIT_SECONDS)
                       + (2 * heavy.PIPE_READER_JOIN_SECONDS))
        expected = (heavy.GIT_ATTESTATION_COMMAND_TIMEOUT_SECONDS
                    * heavy.DISPATCH_ATTESTATION_COMMAND_COUNT
                    + heavy.CHECKOUT_SNAPSHOT_TIMEOUT_SECONDS
                    + 2 * 3 * (5 + per_attempt)
                    + heavy.TERMINAL_BARRIER_OVERHEAD_SECONDS)
        self.assertEqual(750, expected)
        self.assertEqual(expected, duration)

    def test_broker_anchors_full_duration_at_delayed_acquisition(self):
        acquired = timestamp(NOW) + timedelta(minutes=11)
        clock = lambda: acquired
        limits = {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 1}}}
        with tempfile.TemporaryDirectory() as folder:
            broker = FileLeaseBroker(Path(folder) / "broker.json", "fixture", limits,
                                     clock=clock)
            request = {"format": "awf-heavy-validation-lease-request-2",
                       "candidate": CANDIDATE, "plan_sha256": "a" * 64,
                       "config_sha256": "b" * 64, "capacity_sha256": "c" * 64,
                       "parallelism": 1, "resource_class": "heavy", "engine": "python",
                       "engine_identity_sha256": "d" * 64, "engine_slots": 1,
                       "required_resources": [], "resource_claims": {},
                       "determinism": {"seed": 1, "retry_limit": 0},
                       "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                                     "filesystem": "WORKTREE"},
                       "required_duration_seconds": 750}
            lease = broker.acquire(request)
        self.assertEqual(750, (timestamp(lease["expires_at"])
                              - timestamp(lease["acquired_at"])).total_seconds())

    def test_runtime_lease_expiry_terminates_and_blocks_pass(self):
        calls = {"count": 0}
        def clock():
            calls["count"] += 1
            return NOW if calls["count"] < 4 else "2026-10-03T09:00:00Z"
        raw = plan([partition("expires", "import time; time.sleep(1)")], parallelism=1)
        cfg, broker = config(), Broker()
        result = run(raw, cfg, cap=capacity(raw, canonical(cfg), workers=1, heavy=1),
                     broker=broker, lease_clock=clock)
        self.assertEqual("FAIL", result["status"])
        self.assertFalse(result["admission"]["runtime_lease_valid"])
        self.assertEqual("ValidationError", result["partitions"][0]["error_type"])

    def test_malformed_durable_broker_state_fails_closed(self):
        limits = {"max_workers": 2, "max_heavy_jobs": 2, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 2}}}
        clock = lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        lease = {"lease_id": "a" * 64, "fencing_token": 1,
                 "expires_at": "2026-10-02T10:00:00Z", "parallelism": 1,
                 "resource_class": "heavy", "resource_claims": {}, "engine": "python",
                 "engine_identity_sha256": "d" * 64, "engine_slots": 1,
                 "request_sha256": "b" * 64}
        states = [
            {"format": "awf-heavy-validation-broker-state-2", "broker_id": "fixture",
             "next_fence": 1, "leases": [lease]},
            {"format": "awf-heavy-validation-broker-state-2", "broker_id": "fixture",
             "next_fence": 3, "leases": [lease, {**lease, "lease_id": "c" * 64}]},
            {"format": "awf-heavy-validation-broker-state-2", "broker_id": "fixture",
             "next_fence": 3, "leases": [{**lease, "parallelism": -1}]},
            {"format": "awf-heavy-validation-broker-state-2", "broker_id": "fixture",
             "next_fence": 3, "leases": [{**lease, "resource_claims": {"unknown": 1}}]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            for index, state in enumerate(states):
                path = Path(folder) / f"broker-{index}.json"
                path.write_bytes(canonical(state))
                broker = FileLeaseBroker(path, "fixture", limits, clock=clock)
                with self.subTest(index=index), self.assertRaises(ValidationError):
                    broker.release("missing", 1)

    def test_result_receipt_detects_log_receipt_and_key_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            log, receipt, key = (Path(folder) / "result.json",
                                 Path(folder) / "receipt.json", b"r" * 32)
            write_result_log(log, receipt, {"status": "PASS", "value": 7}, key)
            original = log.read_bytes()
            log.write_bytes(original.replace(b'"value":7', b'"value":8'))
            with self.assertRaisesRegex(ValidationError, "digest mismatch"):
                read_result_log(log, receipt, key)
            log.write_bytes(original)
            with self.assertRaisesRegex(ValidationError, "authentication failed"):
                read_result_log(log, receipt, b"x" * 32)
            receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_value["hmac_sha256"] = "0" * 64
            receipt.write_bytes(canonical(receipt_value))
            with self.assertRaisesRegex(ValidationError, "authentication failed"):
                read_result_log(log, receipt, key)


if __name__ == "__main__":
    unittest.main()
