"""Adversarial synthetic tests for tuple-bound heavy validation."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
import tarfile
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
from agentic.heavy_validation import (_engine_identity, _run_validation_at,
                                      resolve_without_alias, run_validation,
                                      workload_authorization)
from agentic.heavy_validation_controller import (
    FileLeaseBroker, GitCheckoutAttestor, GitCheckoutSnapshotter,
    GitHubReviewAuthenticator, read_result_log, write_result_log)

NOW = "2026-10-02T09:00:00Z"
SOURCE_HEAD = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"],
                             check=True, stdout=subprocess.PIPE,
                             text=True).stdout.strip()
SOURCE_TREE = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD^{tree}"],
                             check=True, stdout=subprocess.PIPE,
                             text=True).stdout.strip()
CANDIDATE = {"repository_id": 101, "base_sha": "a" * 40,
             "head_sha": SOURCE_HEAD, "tree_sha": SOURCE_TREE}


def git_archive_file(root, head, relative_path):
    raw = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", head, relative_path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        member = archive.getmember(relative_path)
        source = archive.extractfile(member)
        if source is None:
            raise AssertionError("archive fixture entry is not a file")
        return source.read()


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
         parallelism=4, candidate=None, cwd=None, seed=17, retries=0,
         environment=None, strip_extra=None):
    return canonical({"format": "awf-heavy-validation-plan-4",
        "workload_id": "qa9430-synthetic", "engine": engine,
        "resource_class": resource_class, "required_resources": list(resources or []),
        "requested_parallelism": parallelism, "candidate": candidate or CANDIDATE,
        "working_directory": str(Path(cwd or SOURCE_ROOT).resolve()),
        "determinism": {"seed": seed, "retry_limit": retries},
        "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                      "filesystem": "WORKTREE"},
        "target_environment": {"values": dict(environment or {}),
                               "strip_extra": list(strip_extra or [])},
        "partitions": parts})


def review(plan_raw, *, decision="APPROVE", candidate=None):
    plan_value = json.loads(plan_raw)
    authorization = workload_authorization(plan_value, sha256(plan_raw))
    return canonical({"format": "awf-heavy-validation-review-6",
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
        launcher.write_bytes(git_archive_file(
            SOURCE_ROOT, candidate["head_sha"],
            ".agentic/lib/agentic/heavy_validation_child.py"))
        sanitizer = launcher.with_name("child_process.py")
        sanitizer.write_bytes(git_archive_file(
            SOURCE_ROOT, candidate["head_sha"],
            ".agentic/lib/agentic/child_process.py"))
        record = {"candidate": deepcopy(candidate),
                  "source_working_directory": working_directory,
                  "snapshot_working_directory": snapshot,
                  "tree_sha": candidate["tree_sha"],
                  "archive_source_object": candidate["tree_sha"],
                  "archive_sha256": "f" * 64, "file_count": 2,
                  "reviewed_tree_inventory_sha256": "d" * 64,
                  "content_inventory_sha256": "e" * 64,
                  "mutation_guard": "windows-file-handles-and-sealed-directories",
                  "guarded_paths": 5, "sealed_directories": 4}
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
            seconds=request["required_duration_seconds"]
            + heavy_controller.LEASE_VALIDATION_TRANSPORT_MARGIN_SECONDS)).isoformat(
                timespec="microseconds").replace("+00:00", "Z")
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
    return _run_validation_at(plan_raw=plan_raw, expected_plan_sha256=sha256(plan_raw),
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
    def test_parallel_unavailable_with_multiple_slots_caps_effective_and_lease_limits(self):
        raw = plan([partition("a"), partition("b")], parallelism=2)
        cfg, broker = config(), Broker()
        observed = json.loads(capacity(raw, canonical(cfg)))
        observed["engines"]["python"]["parallel_available"] = False
        observed["engines"]["python"]["parallel_slots"] = 4
        cap = canonical(observed)
        result = run(raw, cfg, cap=cap, broker=broker)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("SERIAL", result["execution"]["mode"])
        self.assertEqual(1, result["execution"]["effective_parallelism"])
        self.assertEqual([1], [request["parallelism"] for request in broker.acquired])
        self.assertEqual([1], [request["engine_slots"] for request in broker.acquired])

    def test_unavailable_unrelated_engine_also_caps_host_dispatch(self):
        raw = plan([partition("a"), partition("b")], parallelism=2)
        cfg, broker = config(), Broker()
        observed = json.loads(capacity(raw, canonical(cfg)))
        observed["engines"]["matlab"] = {
            "identity_sha256": "2" * 64,
            "parallel_available": False,
            "parallel_slots": 5,
        }
        cap = canonical(observed)
        result = run(raw, cfg, cap=cap, broker=broker)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, result["execution"]["effective_parallelism"])
        self.assertEqual(1, broker.acquired[0]["engine_slots"])

    def test_result_receipt_binds_windows_sanitizer_artifact(self):
        raw = plan([partition("receipt")], parallelism=1)
        outputs = []
        for sanitizer_sha in ("a" * 64, "b" * 64):
            def execute(part, candidate_plan, executable, *args, **kwargs):
                result = heavy._unexpected_result(
                    part, candidate_plan, executable, RuntimeError("synthetic result"))
                result.update(state="PASS", error_type=None,
                              process_tree_cleanup={"outcome": "NOT_REQUIRED",
                                                    "mechanism": "synthetic"},
                              windows_launch_chain_artifacts=[{
                                  "method": "sealed_reviewed_snapshot",
                                  "source_path": ".agentic/lib/agentic/child_process.py",
                                  "launch_path": ".agentic/lib/agentic/child_process.py",
                                  "sha256": sanitizer_sha}])
                result["attempts"][0].update(
                    state="PASS", error_type=None,
                    process_tree_cleanup=result["process_tree_cleanup"],
                    windows_launch_chain_artifacts=result["windows_launch_chain_artifacts"])
                return result
            with mock.patch.object(heavy, "_execute", side_effect=execute):
                outputs.append(run(raw, config(enabled=False)))
        self.assertNotEqual(outputs[0]["aggregate_sha256"],
                            outputs[1]["aggregate_sha256"])
        self.assertNotEqual(outputs[0]["serial_equivalence_sha256"],
                            outputs[1]["serial_equivalence_sha256"])
        with tempfile.TemporaryDirectory() as folder:
            key = b"s" * 32
            authenticated = []
            for index, output in enumerate(outputs):
                log, receipt = (Path(folder) / f"result-{index}.json",
                                Path(folder) / f"receipt-{index}.json")
                write_result_log(log, receipt, output, key)
                authenticated.append(read_result_log(log, receipt, key))
            self.assertEqual("a" * 64, authenticated[0]["result"]["partitions"][0]
                             ["windows_launch_chain_artifacts"][0]["sha256"])
            self.assertEqual("b" * 64, authenticated[1]["result"]["partitions"][0]
                             ["windows_launch_chain_artifacts"][0]["sha256"])

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
            result = run(plan([partition("env", code)],
                              strip_extra=["SYNTHETIC_EXTRA_SECRET"]),
                         config(enabled=False, extras=["SYNTHETIC_EXTRA_SECRET"]))
        self.assertEqual("|".join(["absent"] * len(names)), result["partitions"][0]["stdout"].strip())
        for protected in ("GH_TOKEN", "GITHUB_TOKEN"):
            with self.assertRaisesRegex(ValidationError, "cannot name"):
                run(plan([partition("x")]), config(enabled=False, extras=[protected]))

    def test_target_environment_is_reviewed_deterministic_and_does_not_load_host_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            hostile = Path(folder)
            sentinels = []
            for module in ("json", "sitecustomize", "usercustomize"):
                sentinel = hostile / f"{module}.executed"
                sentinels.append(sentinel)
                (hostile / f"{module}.py").write_text(
                    f"open({str(sentinel)!r}, 'w').write('bad')\n", encoding="utf-8")
            names = ["PYTHONPATH", "PYTHONUSERBASE", "HOST_ONLY"]
            code = ("import json,os; print(os.environ['SAFE_VALUE']); "
                    "print('|'.join(os.environ.get(k, 'absent') for k in "
                    + repr(names) + "))")
            plan_raw = plan([partition("target-env", code)], parallelism=1,
                            environment={"SAFE_VALUE": "reviewed caf\u00e9"})
            with mock.patch.dict(os.environ, {
                    "PYTHONPATH": str(hostile), "PYTHONUSERBASE": str(hostile),
                    "HOST_ONLY": "unreviewed"}):
                result = run(plan_raw, config(enabled=False))
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertEqual(["reviewed caf\u00e9", "absent|absent|absent"],
                             result["partitions"][0]["stdout"].splitlines())
            self.assertFalse(any(path.exists() for path in sentinels))
            target = result["workload_authorization"]["record"]["target_environment"]
            self.assertEqual({"PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1",
                              "PYTHONUTF8": "1", "SAFE_VALUE": "reviewed caf\u00e9"},
                             target["values"])
            self.assertEqual(target["sha256"], result["target_environment_sha256"])

    def test_target_environment_policy_must_match_config_and_rejects_reserved_names(self):
        raw = plan([partition("env")], strip_extra=["SYNTHETIC_SECRET"])
        with self.assertRaisesRegex(ValidationError, "does not match reviewed"):
            run(raw, config(enabled=False))
        for name in ("PYTHONPATH", "OPENAI_API_KEY", "PYTHONNOUSERSITE",
                     "PYTHONIOENCODING", "PYTHONUTF8"):
            bad = plan([partition("env")], environment={name: "unreviewed"})
            with self.assertRaisesRegex(ValidationError, "reserved name"):
                run(bad, config(enabled=False))

    def test_windows_launcher_payload_is_ascii_exact_and_bounded(self):
        environment = {"SAFE": "caf\u00e9", "PYTHONNOUSERSITE": "1"}
        argv = [str(Path(sys.executable).resolve()), "space value", "snowman \u2603"]
        payload = heavy._windows_launcher_payload(argv, environment)
        self.assertEqual(payload, payload.decode("ascii").encode("ascii"))
        self.assertEqual({"argv": argv, "environment": environment},
                         json.loads(payload.decode("ascii")))
        near = ["x", *(["a" * heavy._MAX_TOKEN_BYTES] * 7), ""]
        missing = heavy._MAX_WINDOWS_LAUNCHER_PAYLOAD_BYTES - len(
            heavy._windows_launcher_payload(near, environment))
        self.assertGreaterEqual(missing, 0)
        self.assertLessEqual(missing, heavy._MAX_TOKEN_BYTES)
        near[-1] = "b" * missing
        self.assertEqual(heavy._MAX_WINDOWS_LAUNCHER_PAYLOAD_BYTES,
                         len(heavy._windows_launcher_payload(near, environment)))
        near[-1] += "b"
        with self.assertRaisesRegex(ValidationError, "65536-byte"):
            heavy._windows_launcher_payload(near, environment)
        exact_value = json.loads(plan([partition("exact-payload")], parallelism=1))
        reviewed_environment = heavy._target_environment_authorization(exact_value)
        runtime_environment = heavy._target_child_env(
            reviewed_environment, seed=exact_value["determinism"]["seed"], attempt=1)
        exact_argv = [exact_value["partitions"][0]["argv"][0],
                      *(["a" * heavy._MAX_TOKEN_BYTES] * 7), ""]
        exact_missing = heavy._MAX_WINDOWS_LAUNCHER_PAYLOAD_BYTES - len(
            heavy._windows_launcher_payload(exact_argv, runtime_environment))
        self.assertLessEqual(exact_missing, heavy._MAX_TOKEN_BYTES)
        exact_argv[-1] = "b" * exact_missing
        exact_value["partitions"][0]["argv"] = exact_argv
        exact_raw = canonical(exact_value)
        heavy._validate_plan(exact_raw, sha256(exact_raw))
        exact_value["partitions"][0]["argv"][-1] += "b"
        oversized_raw = canonical(exact_value)
        with self.assertRaisesRegex(ValidationError, "65536-byte"):
            heavy._validate_plan(oversized_raw, sha256(oversized_raw))
        escaped = plan([partition("escaped")], environment={
            f"UNICODE_{index}": "\u2603" * 2730 for index in range(4)})
        with self.assertRaisesRegex(ValidationError, "65536-byte"):
            run(escaped, config(enabled=False))

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
                                    heavy._windows_launch_chain(value), lambda: None)
        self.assertEqual(1, execute.call_count)
        self.assertEqual(0, result["retry_count"])

    def test_incomplete_cleanup_cancels_queued_sibling_under_launch_gate(self):
        value = json.loads(plan([partition("failed"), partition("queued")],
                                parallelism=2, retries=1))
        parts = value["partitions"]
        executable = {"path": parts[0]["executable"]["path"],
                      "resolved_path": parts[0]["executable"]["path"],
                      "sha256": parts[0]["executable"]["sha256"]}
        event = threading.Event()
        sibling_queued = threading.Event()
        gate = heavy._ChildLaunchAuthorization(
            review={}, review_digest="0" * 64, plan_digest="1" * 64,
            candidate=value["candidate"], authorization={}, authenticator=None,
            capacity=None, max_capacity_age_seconds=300, capacity_required=False,
            lease=None, lease_guard=lambda: None, clock=lambda: NOW,
            cancel_event=event)
        attempts = []
        partial = {"attempt": 1, "state": "FAILED", "exit_code": None,
                   "timed_out": False, "cancelled": False, "error_type": "CleanupError",
                   "process_tree_cleanup": {"outcome": "PARTIAL",
                                            "mechanism": "direct_process_only"},
                   "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64}

        def attempt(partition, *args):
            attempts.append(partition["name"])
            if partition["name"] == "failed":
                with gate.authorize(partition, 1, "failed-id"):
                    pass
                return dict(partial)
            sibling_queued.set()
            event.wait(timeout=5)
            with gate.authorize(partition, 1, "queued-id"):
                self.fail("queued sibling passed authorization after cleanup failure")

        with mock.patch.object(heavy, "_authenticate_review", return_value={"status": "PASS"}), \
                mock.patch.object(heavy, "_dispatch_freshness", return_value={"status": "PASS", "reasons": []}), \
                mock.patch.object(heavy, "_execute_attempt", side_effect=attempt):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(heavy._execute, parts[0], value, executable,
                                        SOURCE_ROOT, config(enabled=False), event,
                                        heavy._windows_launch_chain(value), lambda: None,
                                        gate.authorize, gate.abort)
                second = executor.submit(heavy._execute, parts[1], value, executable,
                                         SOURCE_ROOT, config(enabled=False), event,
                                         heavy._windows_launch_chain(value), lambda: None,
                                         gate.authorize, gate.abort)
                self.assertTrue(sibling_queued.wait(timeout=5))
                first.result()
                with self.assertRaises(heavy.ValidationError):
                    second.result()
        self.assertTrue(event.is_set())
        self.assertEqual(["failed", "queued"], attempts)

    def test_incomplete_cleanup_keeps_broker_lease_held(self):
        raw = plan([partition("uncertain")], parallelism=1)
        cfg, broker = config(), Broker()
        cap = capacity(raw, canonical(cfg), workers=1, heavy=1)

        def uncertain(*args):
            part, candidate_plan, executable = args[:3]
            return heavy._unexpected_result(
                part, candidate_plan, executable, RuntimeError("cleanup uncertain"))

        with mock.patch.object(heavy, "_execute", side_effect=uncertain):
            result = run(raw, cfg, cap=cap, broker=broker)
        self.assertEqual("FAIL", result["status"])
        self.assertFalse(result["execution"]["process_tree_cleanup_complete"])
        self.assertEqual([], broker.released)
        self.assertNotEqual("RELEASED", result["lease_release"]["status"])

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
        launch_chain = heavy._windows_launch_chain(value)
        launch_chain["target_environment"] = heavy._target_environment_authorization(value)
        fake = FakeProcess()
        @contextmanager
        def artifact_context(executable):
            path = executable.get("resolved_path", executable.get("path"))
            yield {"method": "fixture", "source_path": path,
                   "launch_path": path, "sha256": executable["sha256"],
                   "pass_fds": ()}
        with mock.patch.object(heavy.os, "name", "nt"), \
             mock.patch.object(heavy.subprocess, "Popen", return_value=fake) as popen, \
             mock.patch.object(heavy, "_immutable_executable", side_effect=artifact_context), \
             mock.patch.object(heavy, "_attach_windows_job",
                               side_effect=ValidationError("containment unavailable")), \
             mock.patch.object(heavy, "_release_windows_launcher") as release:
            result = heavy._execute_attempt(part, value, executable, SOURCE_ROOT,
                                            config(enabled=False), threading.Event(), 1,
                                            launch_chain, lambda: None)
        self.assertTrue(fake.killed)
        self.assertEqual(["-I", "-S", "-B"], popen.call_args.args[0][1:4])
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

    def test_production_cli_rejects_arbitrary_clock_override(self):
        command = [sys.executable, str(SOURCE_ROOT / ".agentic/scripts/run_heavy_validation.py"),
                   "--config", "missing-config", "--expected-config-sha256", "a" * 64,
                   "--plan", "missing-plan", "--expected-plan-sha256", "b" * 64,
                   "--review", "missing-review", "--expected-review-sha256", "c" * 64,
                   "--repository-id", "101", "--base-sha", "d" * 40,
                   "--head-sha", "e" * 40, "--tree-sha", "f" * 40,
                   "--result-log", "missing-result", "--result-receipt", "missing-receipt",
                   "--receipt-key-file", "missing-key", "--github-repository", "example/project",
                   "--github-pr", "7", "--github-review-id", "9", "--now", NOW]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        self.assertEqual(2, completed.returncode)
        self.assertIn("unrecognized arguments: --now", completed.stderr)

    def test_production_host_clock_rejects_expired_review(self):
        raw = plan([partition("expired-review")], parallelism=1)
        cfg_raw = canonical(config(enabled=False))
        value = json.loads(review(raw))
        value["reviewed_at"] = "2026-10-01T08:00:00Z"
        value["expires_at"] = "2026-10-02T08:00:00Z"
        review_raw = canonical(value)

        class HostClock:
            @classmethod
            def now(cls, tz):
                return datetime(2026, 10, 3, 8, 0, 0, tzinfo=tz)

        with mock.patch.object(heavy, "datetime", HostClock):
            with self.assertRaisesRegex(ValidationError,
                                        "validity interval does not include execution time"):
                run_validation(
                    plan_raw=raw, expected_plan_sha256=sha256(raw),
                    review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                    config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                    expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                    review_authenticator=authenticator,
                    checkout_attestor=checkout_attestor)

    def test_production_host_clock_keeps_stale_capacity_stale_and_authenticates_review(self):
        raw = plan([partition("stale-capacity")], parallelism=1)
        cfg = config()
        cfg_raw = canonical(cfg)
        cap = capacity(raw, cfg_raw, observed_at="2026-10-02T08:00:00Z")
        review_raw = review(raw)
        authenticated = mock.Mock(wraps=authenticator)

        class HostClock:
            @classmethod
            def now(cls, tz):
                return datetime(2026, 10, 3, 8, 0, 0, tzinfo=tz)

        with mock.patch.object(heavy, "datetime", HostClock):
            result = run_validation(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticated,
                checkout_attestor=checkout_attestor,
                capacity_raw=cap, expected_capacity_sha256=sha256(cap))
        self.assertEqual("FAIL", result["status"])
        self.assertEqual("NOT_STARTED", result["execution"]["mode"])
        self.assertIn("observed_capacity_stale", result["execution"]["fallback_reasons"])
        self.assertEqual("AUTHENTICATED", result["review_authority"]["status"])
        authenticated.assert_called_once()

    def test_production_authenticator_and_durable_broker_run_parallel(self):
        raw = plan([partition("a"), partition("b")], parallelism=2)
        cfg_raw = canonical(config())
        cap = capacity(raw, cfg_raw, workers=2, heavy=2)
        authorization = workload_authorization(json.loads(raw), sha256(raw))
        review_raw = canonical({"format": "awf-heavy-validation-review-6",
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
            result = _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
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

        review_reads = 0

        def revoked_after_admission(endpoint):
            nonlocal review_reads
            value = deepcopy(endpoints[endpoint])
            if endpoint.endswith("/reviews/9"):
                review_reads += 1
                if review_reads == 2:
                    value["state"] = "DISMISSED"
            return value

        revoked_auth = GitHubReviewAuthenticator(
            "example/project", 7, 9, read_api=revoked_after_admission)
        broker = Broker()
        with mock.patch.object(heavy, "_execute_attempt",
                               side_effect=AssertionError("target scheduled")):
            with self.assertRaisesRegex(ValidationError, "lookup failed"):
                _run_validation_at(
                    plan_raw=raw, expected_plan_sha256=sha256(raw),
                    review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                    config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                    expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                    review_authenticator=revoked_auth,
                    checkout_attestor=checkout_attestor,
                    checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                    expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                    lease_clock=lambda: NOW)
        self.assertEqual(2, review_reads)
        self.assertEqual([("lease-1", 7)], broker.released)

    def test_post_admission_reauthentication_releases_lease_and_starts_no_target(self):
        mutations = {
            "revoked approval": lambda evidence: evidence.update(status="REVOKED"),
            "moved tuple": lambda evidence: evidence["candidate"].update(
                head_sha="f" * 40),
            "moved reviewer": lambda evidence: evidence.update(
                immutable_id="other-reviewer"),
            "moved authorization": lambda evidence: evidence.update(
                workload_authorization_sha256="0" * 64),
        }
        with tempfile.TemporaryDirectory() as folder:
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    marker = Path(folder) / (label.replace(" ", "-") + ".txt")
                    raw = plan([partition(
                        "guarded", f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')")],
                        parallelism=1)
                    cfg, broker = config(), Broker()
                    calls = 0

                    def reauthenticate(*args):
                        nonlocal calls
                        calls += 1
                        evidence = authenticator(*args)
                        if calls == 2:
                            mutate(evidence)
                        return evidence

                    with self.assertRaises(ValidationError):
                        run(raw, cfg, cap=capacity(raw, canonical(cfg)), broker=broker,
                            auth=reauthenticate)
                    self.assertEqual(2, calls)
                    self.assertEqual([("lease-1", 7)], broker.released)
                    self.assertFalse(marker.exists())

    def test_final_dispatch_gate_rejects_review_expired_during_provider_delay(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "provider-delay-started.txt"
            raw = plan([partition(
                "guarded", f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')")],
                parallelism=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            reviewed = json.loads(review(raw))
            reviewed["expires_at"] = "2026-10-02T09:00:01Z"
            review_raw = canonical(reviewed)
            clock = {"now": NOW}
            calls = 0

            def delayed_provider(*args):
                nonlocal calls
                calls += 1
                evidence = authenticator(*args)
                if calls == 3:
                    clock["now"] = "2026-10-02T09:00:02Z"
                return evidence

            with mock.patch.object(heavy, "_execute_attempt",
                                   side_effect=AssertionError("target scheduled")):
                result = _run_validation_at(
                    plan_raw=raw, expected_plan_sha256=sha256(raw),
                    review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                    config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                    expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                    review_authenticator=delayed_provider,
                    checkout_attestor=checkout_attestor,
                    checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                    expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                    lease_clock=lambda: clock["now"],
                    dispatch_clock=lambda: clock["now"])
            self.assertEqual(3, calls)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual("NOT_STARTED", result["execution"]["mode"])
            self.assertEqual(0, result["execution"]["scheduled_partition_count"])
            self.assertEqual("REJECTED", result["dispatch_freshness"]["status"])
            self.assertEqual(["review_validity_interval_elapsed"],
                             result["dispatch_freshness"]["reasons"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])
            self.assertFalse(marker.exists())

    def test_final_dispatch_gate_rejects_capacity_stale_after_admission_and_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "capacity-delay-started.txt"
            raw = plan([partition(
                "guarded", f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')")],
                parallelism=1)
            cfg = config()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            clock = {"now": timestamp(NOW)}

            class DelayedBroker(Broker):
                def acquire(self, request):
                    self.acquired.append(deepcopy(request))
                    clock["now"] += timedelta(seconds=150)
                    acquired = clock["now"]
                    expiry = acquired + timedelta(
                        seconds=request["required_duration_seconds"]
                        + heavy_controller.LEASE_VALIDATION_TRANSPORT_MARGIN_SECONDS)
                    return {"status": "GRANTED", "lease_id": "lease-1",
                            "fencing_token": 7, "broker_id": "synthetic-broker",
                            "acquired_at": acquired.isoformat(timespec="microseconds").replace(
                                "+00:00", "Z"),
                            "expires_at": expiry.isoformat(timespec="microseconds").replace(
                                "+00:00", "Z"),
                            "request_sha256": fingerprint(
                                "heavy-validation-lease-request", request)}

            @contextmanager
            def delayed_snapshot(candidate, working_directory):
                clock["now"] += timedelta(seconds=151)
                with checkout_snapshotter(candidate, working_directory) as value:
                    yield value

            broker = DelayedBroker()
            text_clock = lambda: clock["now"].isoformat(timespec="microseconds").replace(
                "+00:00", "Z")
            with mock.patch.object(heavy, "_execute_attempt",
                                   side_effect=AssertionError("target scheduled")):
                result = _run_validation_at(
                    plan_raw=raw, expected_plan_sha256=sha256(raw),
                    review_raw=review(raw), expected_review_sha256=sha256(review(raw)),
                    config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                    expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                    review_authenticator=authenticator,
                    checkout_attestor=checkout_attestor,
                    checkout_snapshotter=delayed_snapshot, capacity_raw=cap,
                    expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                    lease_clock=text_clock, dispatch_clock=text_clock)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual("NOT_STARTED", result["execution"]["mode"])
            self.assertEqual(0, result["execution"]["scheduled_partition_count"])
            self.assertEqual("REJECTED", result["dispatch_freshness"]["status"])
            self.assertEqual(301_000_000,
                             result["dispatch_freshness"]["capacity_age_microseconds"])
            self.assertEqual(["observed_capacity_stale_at_dispatch"],
                             result["dispatch_freshness"]["reasons"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])
            self.assertFalse(marker.exists())

    def test_each_queued_serial_partition_rechecks_review_before_child_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first-started.txt"
            forbidden = Path(folder) / "expired-partition-started.txt"
            raw = plan([
                partition("first", f"from pathlib import Path; Path({str(first)!r}).write_text('ok')"),
                partition("expired", f"from pathlib import Path; Path({str(forbidden)!r}).write_text('bad')"),
            ], parallelism=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            reviewed = json.loads(review(raw))
            reviewed["expires_at"] = "2026-10-02T09:00:01Z"
            review_raw = canonical(reviewed)
            instants = iter([NOW, NOW, "2026-10-02T09:00:02Z"])
            provider_calls = 0

            def counted_provider(*args):
                nonlocal provider_calls
                provider_calls += 1
                return authenticator(*args)

            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=counted_provider,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=lambda: NOW, dispatch_clock=lambda: next(instants))
            self.assertTrue(first.exists())
            self.assertFalse(forbidden.exists())
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(5, provider_calls)
            gates = result["child_launch_authorization"]
            self.assertEqual("REJECTED", gates["status"])
            self.assertEqual(["PASS", "REJECTED"],
                             [item["status"] for item in gates["checks"]])
            self.assertEqual(["review_validity_interval_elapsed"],
                             gates["checks"][1]["reasons"])
            self.assertRegex(gates["evidence_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

    def test_each_queued_serial_partition_rechecks_capacity_freshness(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first-capacity-started.txt"
            forbidden = Path(folder) / "stale-capacity-started.txt"
            raw = plan([
                partition("first", f"from pathlib import Path; Path({str(first)!r}).write_text('ok')"),
                partition("stale", f"from pathlib import Path; Path({str(forbidden)!r}).write_text('bad')"),
            ], parallelism=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            instants = iter([NOW, NOW, "2026-10-02T09:05:01Z"])
            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review(raw), expected_review_sha256=sha256(review(raw)),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=lambda: NOW, dispatch_clock=lambda: next(instants))
            self.assertTrue(first.exists())
            self.assertFalse(forbidden.exists())
            gates = result["child_launch_authorization"]
            self.assertEqual("FAIL", result["status"])
            self.assertEqual("REJECTED", gates["status"])
            self.assertEqual(["observed_capacity_stale_at_dispatch"],
                             gates["checks"][1]["reasons"])
            self.assertEqual(301_000_000,
                             gates["checks"][1]["freshness"]["capacity_age_microseconds"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

    def test_retry_rechecks_review_and_starts_no_expired_retry_child(self):
        with tempfile.TemporaryDirectory() as folder:
            forbidden = Path(folder) / "expired-retry-started.txt"
            code = ("import os; from pathlib import Path; "
                    f"Path({str(forbidden)!r}).write_text('bad') if "
                    "os.environ['AWF_VALIDATION_ATTEMPT']=='2' else None; "
                    "raise SystemExit(9)")
            raw = plan([partition("retry", code)], parallelism=1, retries=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            reviewed = json.loads(review(raw))
            reviewed["expires_at"] = "2026-10-02T09:00:01Z"
            review_raw = canonical(reviewed)
            instants = iter([NOW, NOW, "2026-10-02T09:00:02Z"])
            provider_calls = 0

            def counted_provider(*args):
                nonlocal provider_calls
                provider_calls += 1
                return authenticator(*args)

            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=counted_provider,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=lambda: NOW, dispatch_clock=lambda: next(instants))
            self.assertFalse(forbidden.exists())
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(5, provider_calls)
            gates = result["child_launch_authorization"]
            self.assertEqual([1, 2], [item["attempt"] for item in gates["checks"]])
            self.assertEqual(["PASS", "REJECTED"],
                             [item["status"] for item in gates["checks"]])
            self.assertEqual(["review_validity_interval_elapsed"],
                             gates["checks"][1]["reasons"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

    def test_provider_delay_cannot_outlive_review_and_launch_child(self):
        with tempfile.TemporaryDirectory() as folder:
            forbidden = Path(folder) / "provider-delayed-review-child.txt"
            raw = plan([partition(
                "delayed-review",
                f"from pathlib import Path; Path({str(forbidden)!r}).write_text('bad')",
            )], parallelism=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            reviewed = json.loads(review(raw))
            reviewed["expires_at"] = "2026-10-02T09:00:01Z"
            review_raw = canonical(reviewed)
            clock = {"now": timestamp(NOW)}
            provider_calls = 0
            cancelled = threading.Event()

            def delayed_provider(*args):
                nonlocal provider_calls
                provider_calls += 1
                value = authenticator(*args)
                if provider_calls == 4:
                    clock["now"] += timedelta(seconds=2)
                return value

            def text_clock():
                return clock["now"].isoformat(
                    timespec="microseconds").replace("+00:00", "Z")
            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=delayed_provider,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=text_clock, dispatch_clock=text_clock,
                cancel_event=cancelled)
            self.assertEqual(4, provider_calls)
            self.assertFalse(forbidden.exists())
            self.assertTrue(cancelled.is_set())
            self.assertEqual("FAIL", result["status"])
            self.assertTrue(result["all_partitions_terminal"])
            self.assertEqual(1, result["execution"]["completed_terminal_count"])
            self.assertEqual("REJECTED", result["child_launch_authorization"]["status"])
            gate = result["child_launch_authorization"]["checks"][0]
            self.assertEqual("2026-10-02T09:00:02.000000Z", gate["sampled_at"])
            self.assertEqual(["review_validity_interval_elapsed"], gate["reasons"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

    def test_provider_delay_cannot_age_capacity_and_launch_child(self):
        with tempfile.TemporaryDirectory() as folder:
            forbidden = Path(folder) / "provider-delayed-capacity-child.txt"
            raw = plan([partition(
                "delayed-capacity",
                f"from pathlib import Path; Path({str(forbidden)!r}).write_text('bad')",
            )], parallelism=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            clock = {"now": timestamp(NOW)}
            provider_calls = 0
            cancelled = threading.Event()

            def delayed_provider(*args):
                nonlocal provider_calls
                provider_calls += 1
                value = authenticator(*args)
                if provider_calls == 4:
                    clock["now"] += timedelta(seconds=301)
                return value

            def text_clock():
                return clock["now"].isoformat(
                    timespec="microseconds").replace("+00:00", "Z")
            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review(raw), expected_review_sha256=sha256(review(raw)),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=delayed_provider,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=text_clock, dispatch_clock=text_clock,
                cancel_event=cancelled)
            self.assertEqual(4, provider_calls)
            self.assertFalse(forbidden.exists())
            self.assertTrue(cancelled.is_set())
            self.assertEqual("FAIL", result["status"])
            self.assertTrue(result["all_partitions_terminal"])
            self.assertEqual(1, result["execution"]["completed_terminal_count"])
            self.assertEqual("REJECTED", result["child_launch_authorization"]["status"])
            gate = result["child_launch_authorization"]["checks"][0]
            self.assertEqual("2026-10-02T09:05:01.000000Z", gate["sampled_at"])
            self.assertEqual(["observed_capacity_stale_at_dispatch"], gate["reasons"])
            self.assertEqual(301_000_000,
                             gate["freshness"]["capacity_age_microseconds"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

    def test_launch_clock_exception_rejects_all_queued_children_and_releases_once(self):
        with tempfile.TemporaryDirectory() as folder:
            markers = [Path(folder) / f"clock-exception-{index}.txt" for index in range(2)]
            raw = plan([
                partition(f"clock-exception-{index}",
                          f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')")
                for index, marker in enumerate(markers)
            ], parallelism=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            cancelled = threading.Event()
            calls = 0

            def failing_clock():
                nonlocal calls
                calls += 1
                if calls == 1:
                    return NOW
                raise RuntimeError("trusted clock unavailable")

            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review(raw), expected_review_sha256=sha256(review(raw)),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=lambda: NOW, dispatch_clock=failing_clock,
                cancel_event=cancelled)
            self.assertEqual(2, calls)
            self.assertTrue(cancelled.is_set())
            self.assertTrue(result["all_partitions_terminal"])
            self.assertEqual(2, result["execution"]["completed_terminal_count"])
            self.assertTrue(all(not marker.exists() for marker in markers))
            gates = result["child_launch_authorization"]
            self.assertEqual("REJECTED", gates["status"])
            self.assertEqual(1, len(gates["checks"]))
            self.assertIsNone(gates["checks"][0]["sampled_at"])
            self.assertEqual(["trusted_clock_rejected"], gates["checks"][0]["reasons"])
            self.assertEqual("RuntimeError", gates["checks"][0]["freshness"]["error_type"])
            self.assertRegex(gates["checks"][0]["evidence_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(gates["evidence_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

    def test_malformed_launch_clock_rejects_all_retries_and_releases_once(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "malformed-clock-retry.txt"
            raw = plan([partition(
                "malformed-clock-retry",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('bad'); raise SystemExit(9)",
            )], parallelism=1, retries=1)
            cfg, broker = config(), Broker()
            cfg_raw = canonical(cfg)
            cap = capacity(raw, cfg_raw, workers=1, heavy=1)
            cancelled = threading.Event()
            calls = 0

            def malformed_clock():
                nonlocal calls
                calls += 1
                return NOW if calls == 1 else {"not": "trusted UTC"}

            result = _run_validation_at(
                plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review(raw), expected_review_sha256=sha256(review(raw)),
                config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator,
                checkout_attestor=checkout_attestor,
                checkout_snapshotter=checkout_snapshotter, capacity_raw=cap,
                expected_capacity_sha256=sha256(cap), broker_client=broker, now=NOW,
                lease_clock=lambda: NOW, dispatch_clock=malformed_clock,
                cancel_event=cancelled)
            self.assertEqual(2, calls)
            self.assertTrue(cancelled.is_set())
            self.assertTrue(result["all_partitions_terminal"])
            self.assertEqual(1, result["execution"]["completed_terminal_count"])
            self.assertFalse(marker.exists())
            self.assertEqual(0, result["partitions"][0]["retry_count"])
            gates = result["child_launch_authorization"]
            self.assertEqual("REJECTED", gates["status"])
            self.assertEqual(1, len(gates["checks"]))
            self.assertIsNone(gates["checks"][0]["sampled_at"])
            self.assertEqual(["trusted_clock_rejected"], gates["checks"][0]["reasons"])
            self.assertEqual("ValidationError", gates["checks"][0]["freshness"]["error_type"])
            self.assertEqual([("lease-1", 7)], broker.released)
            self.assertEqual("RELEASED", result["lease_release"]["status"])

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

    def test_expired_quarantined_file_lease_survives_restart_until_exact_recovery(self):
        now = {"value": datetime.fromisoformat(NOW.replace("Z", "+00:00"))}
        clock = lambda: now["value"]
        limits = {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 1}}}
        request = {"format": "awf-heavy-validation-lease-request-2",
                   "candidate": CANDIDATE, "plan_sha256": "a" * 64,
                   "config_sha256": "b" * 64, "capacity_sha256": "c" * 64,
                   "parallelism": 1, "resource_class": "heavy", "engine": "python",
                   "engine_identity_sha256": "d" * 64, "engine_slots": 1,
                   "required_resources": [], "resource_claims": {},
                   "determinism": {"seed": 1, "retry_limit": 0},
                   "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                                 "filesystem": "WORKTREE"},
                   "required_duration_seconds": 10}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broker.json"
            broker = FileLeaseBroker(path, "fixture", limits, clock=clock)
            lease = broker.acquire(request)
            self.assertEqual("GRANTED", lease["status"])
            self.assertEqual("QUARANTINED", broker.quarantine(
                lease["lease_id"], lease["fencing_token"],
                {"outcome": "PARTIAL", "mechanism": "direct_process_only"})["status"])
            now["value"] += timedelta(days=2)
            restarted = FileLeaseBroker(path, "fixture", limits, clock=clock)
            denied = restarted.acquire(request)
            self.assertEqual("DENIED", denied["status"])
            self.assertIn("worker capacity", denied["reason"])
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("awf-heavy-validation-broker-state-3", state["format"])
            self.assertEqual("PARTIAL", state["leases"][0]["quarantine"]["outcome"])
            self.assertEqual("QUARANTINED", restarted.release(
                lease["lease_id"], lease["fencing_token"])["status"])
            evidence = {"format": "awf-heavy-validation-termination-evidence-1",
                        "lease_id": lease["lease_id"],
                        "fencing_token": lease["fencing_token"], "status": "TERMINATED",
                        "source": "operator_recovery_record", "observed_at": NOW,
                        "proof_sha256": "9" * 64}
            evidence["evidence_sha256"] = fingerprint(
                "heavy-validation-termination-evidence", evidence)
            wrong_fence = deepcopy(evidence)
            wrong_fence["fencing_token"] += 1
            wrong_fence_core = {key: value for key, value in wrong_fence.items()
                                if key != "evidence_sha256"}
            wrong_fence["evidence_sha256"] = fingerprint(
                "heavy-validation-termination-evidence", wrong_fence_core)
            with self.assertRaisesRegex(ValidationError, "does not bind"):
                restarted.release_quarantined(
                    lease["lease_id"], lease["fencing_token"], wrong_fence)
            released = restarted.release_quarantined(
                lease["lease_id"], lease["fencing_token"], evidence)
            self.assertEqual("RELEASED", released["status"])
            self.assertEqual("GRANTED", restarted.acquire(request)["status"])

    def test_existing_empty_broker_state_fails_closed_and_initialization_is_durable(self):
        limits = {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 1}}}
        clock = lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broker.json"
            broker = FileLeaseBroker(path, "fixture", limits, clock=clock)
            for index, damaged in enumerate((b"", b" \r\n\t")):
                path.write_bytes(damaged)
                with self.subTest(index=index), self.assertRaises(ValidationError):
                    broker._transaction(lambda state: None)
                self.assertEqual(damaged, path.read_bytes())
                path.unlink()
            broker._transaction(lambda state: None)
            marker = path.with_name(path.name + ".initialized")
            self.assertTrue(marker.is_file())
            path.unlink()
            with self.assertRaisesRegex(ValidationError, "missing after initialization"):
                broker._transaction(lambda state: None)

    def test_interrupted_broker_replacement_preserves_quarantine_lease_and_fence(self):
        limits = {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 1}}}
        clock = lambda: datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        request = {"format": "awf-heavy-validation-lease-request-2",
                   "candidate": CANDIDATE, "plan_sha256": "a" * 64,
                   "config_sha256": "b" * 64, "capacity_sha256": "c" * 64,
                   "parallelism": 1, "resource_class": "heavy", "engine": "python",
                   "engine_identity_sha256": "d" * 64, "engine_slots": 1,
                   "required_resources": [], "resource_claims": {},
                   "determinism": {"seed": 1, "retry_limit": 0},
                   "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                                 "filesystem": "WORKTREE"},
                   "required_duration_seconds": 10}
        for interruption in ("before_replace", "torn_staging_write"):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "broker.json"
                broker = FileLeaseBroker(path, "fixture", limits, clock=clock)
                lease = broker.acquire(request)
                self.assertEqual("GRANTED", lease["status"])
                broker.quarantine(lease["lease_id"], lease["fencing_token"],
                                  {"outcome": "PARTIAL",
                                   "mechanism": "direct_process_only"})
                before = path.read_bytes()
                with self.subTest(interruption=interruption):
                    if interruption == "before_replace":
                        with mock.patch.object(heavy_controller.os, "replace",
                                               side_effect=OSError("simulated interruption")):
                            with self.assertRaisesRegex(OSError, "simulated interruption"):
                                broker.release(lease["lease_id"], lease["fencing_token"])
                    else:
                        def tear_staged_file(descriptor):
                            os.ftruncate(descriptor, 1)
                            raise OSError("simulated torn staging write")
                        with mock.patch.object(heavy_controller.os, "fsync",
                                               side_effect=tear_staged_file):
                            with self.assertRaisesRegex(OSError, "torn staging write"):
                                broker.release(lease["lease_id"], lease["fencing_token"])
                self.assertEqual(before, path.read_bytes())
                persisted = json.loads(path.read_bytes())
                self.assertEqual(2, persisted["next_fence"])
                self.assertEqual(1, persisted["leases"][0]["fencing_token"])
                self.assertEqual("PARTIAL",
                                 persisted["leases"][0]["quarantine"]["outcome"])
                restarted = FileLeaseBroker(path, "fixture", limits, clock=clock)
                self.assertEqual("QUARANTINED", restarted.release(
                    lease["lease_id"], lease["fencing_token"])["status"])
                self.assertEqual("DENIED", restarted.acquire(request)["status"])

    def test_C33_IC_F02_failed_first_quarantine_commit_retains_pending_lease_after_restart(self):
        now = {"value": datetime.fromisoformat(NOW.replace("Z", "+00:00"))}
        clock = lambda: now["value"]
        limits = {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 1}}}
        request = {"format": "awf-heavy-validation-lease-request-2",
                   "candidate": CANDIDATE, "plan_sha256": "a" * 64,
                   "config_sha256": "b" * 64, "capacity_sha256": "c" * 64,
                   "parallelism": 1, "resource_class": "heavy", "engine": "python",
                   "engine_identity_sha256": "d" * 64, "engine_slots": 1,
                   "required_resources": [], "resource_claims": {},
                   "determinism": {"seed": 1, "retry_limit": 0},
                   "isolation": {"process_tree": "REQUIRED", "network": "HOST_POLICY",
                                 "filesystem": "WORKTREE"},
                   "required_duration_seconds": 10}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broker.json"
            broker = FileLeaseBroker(path, "fixture", limits, clock=clock)
            lease = broker.acquire(request)
            persisted = json.loads(path.read_bytes())
            self.assertEqual("PENDING", persisted["leases"][0]["quarantine"]["outcome"])
            # Simulate the first failure-path quarantine write failing before its
            # atomic replacement, followed by controller death before any retry.
            with mock.patch.object(heavy_controller.os, "replace",
                                   side_effect=OSError("injected first quarantine commit failure")):
                with self.assertRaisesRegex(OSError, "injected first quarantine commit failure"):
                    broker.quarantine(lease["lease_id"], lease["fencing_token"],
                                      {"outcome": "FAILED", "mechanism": "cleanup_unknown"})
            self.assertEqual("PENDING", json.loads(path.read_bytes())[
                "leases"][0]["quarantine"]["outcome"])
            now["value"] += timedelta(days=2)
            restarted = FileLeaseBroker(path, "fixture", limits, clock=clock)
            self.assertEqual("DENIED", restarted.acquire(request)["status"])
            evidence = {"format": "awf-heavy-validation-termination-evidence-1",
                        "lease_id": lease["lease_id"],
                        "fencing_token": lease["fencing_token"], "status": "TERMINATED",
                        "source": "operator_recovery_record", "observed_at": NOW,
                        "proof_sha256": "9" * 64}
            evidence["evidence_sha256"] = fingerprint(
                "heavy-validation-termination-evidence", evidence)
            self.assertEqual("RELEASED", restarted.release_quarantined(
                lease["lease_id"], lease["fencing_token"], evidence)["status"])
            self.assertEqual("GRANTED", restarted.acquire(request)["status"])

    def test_workload_authorization_binds_every_dispatch_input(self):
        raw = plan([partition("bound")], parallelism=1)
        reviewed = json.loads(review(raw))
        reviewed["workload_authorization"]["record"]["partitions"][0]["argv"].append("moved")
        changed = canonical(reviewed)
        cfg = canonical(config(enabled=False))
        with self.assertRaisesRegex(ValidationError, "exact workload"):
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=changed, expected_review_sha256=sha256(changed),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, checkout_attestor=checkout_attestor,
                now=NOW)
        reviewed = json.loads(review(raw))
        reviewed["workload_authorization"]["record"]["windows_launch_chain"][
            "sanitizer"]["sha256"] = "0" * 64
        changed = canonical(reviewed)
        with self.assertRaisesRegex(ValidationError, "exact workload"):
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
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
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=changed, expected_review_sha256=sha256(changed),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, checkout_attestor=checkout_attestor,
                now=NOW)
        reviewed = json.loads(review(raw))
        reviewed["workload_authorization"]["record"]["windows_launch_chain"][
            "interpreter_flags"] = ["-B"]
        changed = canonical(reviewed)
        with self.assertRaisesRegex(ValidationError, "exact workload"):
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
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
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=cfg, expected_config_sha256=sha256(cfg),
                expected_candidate=CANDIDATE, execution_root=SOURCE_ROOT,
                review_authenticator=authenticator, now=NOW)
        outputs = {
            ("rev-parse", "--show-toplevel"): str(SOURCE_ROOT),
            ("rev-parse", "--verify", "HEAD^{commit}"): CANDIDATE["head_sha"],
            ("rev-parse", "--verify", "HEAD^{tree}"): CANDIDATE["tree_sha"],
            ("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"):
                "dirty.txt",
        }
        attestor = GitCheckoutAttestor(SOURCE_ROOT, run_git=lambda args: outputs[tuple(args)])
        with self.assertRaisesRegex(ValidationError, "dirty"):
            attestor(CANDIDATE, str(SOURCE_ROOT))
        outputs[("status", "--porcelain=v1", "--untracked-files=all",
                 "--ignored=matching")] = ""
        outputs[("rev-parse", "--verify", "HEAD^{commit}")] = "0" * 40
        with self.assertRaisesRegex(ValidationError, "HEAD/tree"):
            attestor(CANDIDATE, str(SOURCE_ROOT))

    def test_execution_requires_separate_immutable_checkout_snapshot(self):
        raw = plan([partition("checkout")], parallelism=1)
        cfg = canonical(config(enabled=False))
        review_raw = review(raw)
        with self.assertRaisesRegex(ValidationError, "snapshot provider is unavailable"):
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
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
            _run_validation_at(plan_raw=raw, expected_plan_sha256=sha256(raw),
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

    @unittest.skipUnless(os.name == "nt", "Immutable snapshot lock (_lock_snapshot) is implemented only on Windows")
    def test_post_attestation_replace_and_ambient_git_config_cannot_redirect_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            outer = Path(folder)
            root, other = outer / "reviewed", outer / "other"
            root.mkdir()
            other.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            tracked = root / "tracked.txt"
            tracked.write_text("reviewed\n", encoding="utf-8", newline="\n")
            launcher = root / ".agentic/lib/agentic/heavy_validation_child.py"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("# reviewed launcher\n", encoding="utf-8", newline="\n")
            shutil.copy2(SOURCE_ROOT / ".agentic/lib/agentic/child_process.py",
                         launcher.with_name("child_process.py"))
            reviewed_launcher_sha256 = file_digest(launcher)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            commit = ["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                      "user.email=fixture@example.invalid", "commit", "-q", "-m"]
            subprocess.run([*commit, "reviewed"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            reviewed_head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                stdout=subprocess.PIPE, text=True).stdout.strip()
            reviewed_tree = subprocess.run(
                ["git", "-C", str(root), "show", "-s", "--format=%T", "HEAD"],
                check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
            candidate = {**CANDIDATE, "head_sha": reviewed_head,
                         "tree_sha": reviewed_tree}
            attestor = GitCheckoutAttestor(root)
            subprocess.run(["git", "init", "-q", str(other)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            attributes = outer / "ambient-attributes"
            attributes.write_text("tracked.txt export-ignore\n", encoding="utf-8",
                                  newline="\n")
            hostile = {
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.attributesFile",
                "GIT_CONFIG_VALUE_0": str(attributes), "GIT_DIR": str(other / ".git"),
                "GIT_REPLACE_REF_BASE": "refs/replace",
            }
            with mock.patch.dict(os.environ, hostile):
                self.assertEqual("CLEAN", attestor(candidate, str(root))["status"])

            tracked.write_text("replacement\n", encoding="utf-8", newline="\n")
            launcher.write_text("# replacement launcher\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run([*commit, "replacement"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            replacement_head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                stdout=subprocess.PIPE, text=True).stdout.strip()
            replacement_tree = subprocess.run(
                ["git", "-C", str(root), "show", "-s", "--format=%T", "HEAD"],
                check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "--detach",
                            reviewed_head], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "replace", reviewed_head,
                            replacement_head], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
            ambient_tree = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True,
                stdout=subprocess.PIPE, text=True).stdout.strip()
            self.assertEqual(replacement_tree, ambient_tree)
            self.assertEqual("reviewed\n", tracked.read_text(encoding="utf-8"))
            with mock.patch.dict(os.environ, hostile):
                with GitCheckoutSnapshotter(root)(candidate, str(root)) as evidence:
                    snapshot = Path(evidence["snapshot_working_directory"])
                    self.assertEqual("reviewed\n",
                                     (snapshot / "tracked.txt").read_text(encoding="utf-8"))
                    self.assertEqual(reviewed_tree, evidence["archive_source_object"])
                    self.assertRegex(evidence["reviewed_tree_inventory_sha256"], r"^[0-9a-f]{64}$")
                chain_plan = json.loads(plan(
                    [partition("reviewed-launcher")], parallelism=1,
                    candidate=candidate, cwd=root))
                chain = heavy._windows_launch_chain(chain_plan)
                self.assertEqual(reviewed_launcher_sha256,
                                 chain["launcher"]["sha256"])

    def test_snapshot_rejects_archive_inventory_or_content_not_bound_to_reviewed_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            tracked = root / "tracked.txt"
            tracked.write_text("reviewed\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            commit = ["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                      "user.email=fixture@example.invalid", "commit", "-q", "-m"]
            subprocess.run([*commit, "reviewed"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                  check=True, stdout=subprocess.PIPE,
                                  text=True).stdout.strip()
            tree = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%T",
                                   "HEAD"], check=True, stdout=subprocess.PIPE,
                                  text=True).stdout.strip()
            candidate = {**CANDIDATE, "head_sha": head, "tree_sha": tree}
            tracked.write_text("unreviewed\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run([*commit, "unreviewed"], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            other_tree = subprocess.run(
                ["git", "-C", str(root), "show", "-s", "--format=%T", "HEAD"],
                check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
            production = GitCheckoutSnapshotter(root)
            wrong_archive = production._archive(other_tree)
            snapshotter = GitCheckoutSnapshotter(
                root, run_archive=lambda observed_tree: wrong_archive)
            with self.assertRaisesRegex(ValidationError, "not bound to the reviewed tree"):
                with snapshotter(candidate, str(root)):
                    self.fail("tree-mismatched archive was dispatched")

    @unittest.skipUnless(os.name == "nt", "Immutable snapshot lock (_lock_snapshot) is implemented only on Windows")
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
                injected = Path(evidence["snapshot_working_directory"]) / "unreviewed.py"
                with self.assertRaises(OSError):
                    injected.write_text("INJECTED = True\n", encoding="utf-8", newline="\n")
                self.assertFalse(injected.exists())
                self.assertEqual(tree, evidence["tree_sha"])
                self.assertEqual("IMMUTABLE", evidence["status"])
            real_lock = snapshotter._lock_snapshot

            def inject_before_seal(destination):
                (destination / "unreviewed.py").write_text(
                    "INJECTED = True\n", encoding="utf-8", newline="\n")
                return real_lock(destination)

            with mock.patch.object(snapshotter, "_lock_snapshot",
                                   side_effect=inject_before_seal):
                with self.assertRaisesRegex(ValidationError,
                                            "content changed before namespace seal"):
                    with snapshotter(candidate, str(root)):
                        self.fail("snapshot with an injected sibling was dispatched")

    @unittest.skipUnless(os.name == "nt", "Immutable snapshot lock (_lock_snapshot) is implemented only on Windows")
    def test_production_snapshot_blocks_injection_during_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            launcher = root / ".agentic/lib/agentic/heavy_validation_child.py"
            launcher.parent.mkdir(parents=True)
            shutil.copy2(SOURCE_ROOT / ".agentic/lib/agentic/heavy_validation_child.py",
                         launcher)
            shutil.copy2(SOURCE_ROOT / ".agentic/lib/agentic/child_process.py",
                         launcher.with_name("child_process.py"))
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-q", "-m",
                            "fixture"], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
            head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                  check=True, stdout=subprocess.PIPE,
                                  text=True).stdout.strip()
            tree = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%T",
                                   "HEAD"], check=True, stdout=subprocess.PIPE,
                                  text=True).stdout.strip()
            candidate = {**CANDIDATE, "head_sha": head, "tree_sha": tree}
            attempt = ("exec(\"from pathlib import Path\\ntry:\\n "
                       "Path('unreviewed.py').write_text('bad')\\nexcept OSError:\\n "
                       "raise SystemExit(0)\\nraise SystemExit(19)\\n\")")
            plan_raw = plan([partition("namespace-injection", attempt)], parallelism=1,
                            candidate=candidate, cwd=root)
            config_raw = canonical(config(enabled=False))
            review_raw = review(plan_raw, candidate=candidate)
            result = _run_validation_at(
                plan_raw=plan_raw, expected_plan_sha256=sha256(plan_raw),
                review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                config_raw=config_raw, expected_config_sha256=sha256(config_raw),
                expected_candidate=candidate, execution_root=str(root.resolve()),
                review_authenticator=authenticator,
                checkout_attestor=GitCheckoutAttestor(root),
                checkout_snapshotter=GitCheckoutSnapshotter(root), now=NOW)
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertEqual("PASS", result["partitions"][0]["state"])
            self.assertFalse((root / "unreviewed.py").exists())
            self.assertGreater(result["checkout_snapshot"]["sealed_directories"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows containment launcher only")
    def test_production_launcher_ignores_hostile_python_module_search(self):
        with tempfile.TemporaryDirectory() as folder:
            outer = Path(folder)
            root, hostile = outer / "repository", outer / "hostile"
            launcher = root / ".agentic/lib/agentic/heavy_validation_child.py"
            launcher.parent.mkdir(parents=True)
            hostile.mkdir()
            shutil.copy2(SOURCE_ROOT / ".agentic/lib/agentic/heavy_validation_child.py",
                         launcher)
            shutil.copy2(SOURCE_ROOT / ".agentic/lib/agentic/child_process.py",
                         launcher.with_name("child_process.py"))
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-q", "-m",
                            "fixture"], check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
            head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                  check=True, stdout=subprocess.PIPE,
                                  text=True).stdout.strip()
            tree = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%T",
                                   "HEAD"], check=True, stdout=subprocess.PIPE,
                                  text=True).stdout.strip()
            candidate = {**CANDIDATE, "head_sha": head, "tree_sha": tree}
            sentinels = []
            for module in ("json", "sitecustomize", "usercustomize"):
                sentinel = hostile / f"{module}.executed"
                sentinels.append(sentinel)
                source = (f"open({str(sentinel)!r}, 'w', encoding='utf-8').write('bad')\n"
                          "raise RuntimeError('unreviewed module executed')\n")
                (hostile / f"{module}.py").write_text(source, encoding="utf-8")
            target = Path(os.environ["COMSPEC"]).resolve()
            part = partition("module-search")
            part["executable"] = {"path": str(target), "sha256": file_digest(target)}
            part["argv"] = [str(target), "/d", "/c", "exit", "0"]
            plan_raw = plan([part], parallelism=1, candidate=candidate, cwd=root)
            config_raw = canonical(config(enabled=False))
            review_raw = review(plan_raw, candidate=candidate)
            with mock.patch.dict(os.environ, {
                    "PYTHONPATH": str(hostile), "PYTHONUSERBASE": str(hostile)}):
                result = _run_validation_at(
                    plan_raw=plan_raw, expected_plan_sha256=sha256(plan_raw),
                    review_raw=review_raw, expected_review_sha256=sha256(review_raw),
                    config_raw=config_raw, expected_config_sha256=sha256(config_raw),
                    expected_candidate=candidate, execution_root=str(root.resolve()),
                    review_authenticator=authenticator,
                    checkout_attestor=GitCheckoutAttestor(root),
                    checkout_snapshotter=GitCheckoutSnapshotter(root), now=NOW)
            self.assertEqual("PASS", result["status"], json.dumps(result, indent=2))
            self.assertEqual(["-I", "-S", "-B"], result["workload_authorization"][
                "record"]["windows_launch_chain"]["interpreter_flags"])
            self.assertFalse(any(path.exists() for path in sentinels))

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
        value = json.loads(plan([partition("launcher")], parallelism=1))
        chain = heavy._windows_launch_chain(value)
        self.assertEqual(["-I", "-S", "-B"], chain["interpreter_flags"])
        launcher = chain["launcher"]
        reviewed = git_archive_file(
            SOURCE_ROOT, value["candidate"]["head_sha"],
            launcher["repository_relative_path"])
        self.assertEqual(hashlib.sha256(reviewed).hexdigest(), launcher["sha256"])
        sanitizer = chain["sanitizer"]
        sanitizer_bytes = git_archive_file(
            SOURCE_ROOT, value["candidate"]["head_sha"],
            sanitizer["repository_relative_path"])
        self.assertEqual(hashlib.sha256(sanitizer_bytes).hexdigest(), sanitizer["sha256"])
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / "heavy_validation_child.py"
            copied.write_bytes(reviewed)
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
        acquired = timestamp(NOW) + timedelta(minutes=11, microseconds=375_000)
        current = {"time": timestamp(NOW), "timeline": None}

        def clock():
            if current["timeline"] is None:
                return current["time"]
            return next(current["timeline"])
        limits = {"max_workers": 1, "max_heavy_jobs": 1, "max_gpu_jobs": 0,
                  "resources": {}, "engines": {"python": {
                      "identity_sha256": "d" * 64, "slots": 1}}}
        with tempfile.TemporaryDirectory() as folder:
            broker = FileLeaseBroker(Path(folder) / "broker.json", "fixture", limits,
                                     clock=clock)
            transaction = broker._transaction

            def delayed_transaction(operation):
                current["timeline"] = iter([acquired - timedelta(seconds=2), acquired])
                return transaction(operation)

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
            with mock.patch.object(broker, "_transaction",
                                   side_effect=delayed_transaction):
                lease = broker.acquire(request)
        self.assertTrue(lease["acquired_at"].endswith(".375000Z"))
        self.assertTrue(lease["expires_at"].endswith(".375000Z"))
        self.assertEqual(acquired, timestamp(lease["acquired_at"]))
        self.assertEqual(755, (timestamp(lease["expires_at"])
                              - timestamp(lease["acquired_at"])).total_seconds())
        almost_margin = (acquired + timedelta(seconds=4, microseconds=999_999)
                         ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.assertEqual("GRANTED", heavy._validate_lease(
            lease, request, "fixture", almost_margin)["status"])
        beyond_margin = (acquired + timedelta(seconds=5, microseconds=1)
                         ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        self.assertEqual("STALE", heavy._validate_lease(
            lease, request, "fixture", beyond_margin)["status"])

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
