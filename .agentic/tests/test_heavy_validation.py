"""Adversarial synthetic tests for tuple-bound heavy validation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, sha256
from agentic.heavy_validation import resolve_without_alias, run_validation

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
    return {"name": name, "argv": [executable, "-c", code],
            "executable": {"path": executable, "sha256": file_digest(executable)},
            "timeout_seconds": timeout, "accepted_exit_codes": list(exits or [0])}


def plan(parts, *, engine="python", resource_class="heavy", resources=None,
         parallelism=4, candidate=None, cwd=None):
    return canonical({"format": "awf-heavy-validation-plan-2",
        "workload_id": "qa9430-synthetic", "engine": engine,
        "resource_class": resource_class, "required_resources": list(resources or []),
        "requested_parallelism": parallelism, "candidate": candidate or CANDIDATE,
        "working_directory": str(Path(cwd or SOURCE_ROOT).resolve()), "partitions": parts})


def review(plan_raw, *, decision="APPROVE", candidate=None):
    return canonical({"format": "awf-heavy-validation-review-2",
        "plan_sha256": sha256(plan_raw), "candidate": candidate or CANDIDATE,
        "decision": decision,
        "reviewer": {"provider": "fixture", "immutable_id": "reviewer-101", "login": "critic"},
        "reviewed_at": NOW, "expires_at": "2026-10-03T09:00:00Z"})


def authenticator(value, review_digest, plan_digest, candidate):
    return {"status": "AUTHENTICATED", **value["reviewer"],
            "evidence_sha256": "d" * 64, "review_sha256": review_digest,
            "plan_sha256": plan_digest, "candidate": candidate}


def capacity(plan_raw, config_raw, *, workers=6, heavy=2, gpu=1, resources=None,
             engines=None, observed_at=NOW, candidate=None):
    return canonical({"format": "awf-heavy-validation-capacity-2",
        "observed_at": observed_at, "broker_id": "synthetic-broker",
        "workers_available": workers, "heavy_jobs_available": heavy,
        "gpu_jobs_available": gpu, "resources_available": dict(resources or {}),
        "engines": dict({"python": {"parallel_available": True, "parallel_slots": workers}}
                        if engines is None else engines),
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
        expiry = request["required_until"]
        if self.mode == "stale":
            expiry = "2026-10-02T08:59:59Z"
        return {"status": "GRANTED", "lease_id": "lease-1", "fencing_token": 7,
                "broker_id": "synthetic-broker", "acquired_at": NOW,
                "expires_at": expiry,
                "request_sha256": fingerprint("heavy-validation-lease-request", request)}

    def release(self, lease_id, fencing_token):
        self.released.append((lease_id, fencing_token))
        return {"status": "RELEASED", "lease_id": lease_id, "fencing_token": fencing_token}


def run(plan_raw, cfg, *, cap=None, broker=None, auth=authenticator,
        cancel_event=None, candidate=None, cwd=None):
    cfg_raw = canonical(cfg)
    review_raw = review(plan_raw)
    return run_validation(plan_raw=plan_raw, expected_plan_sha256=sha256(plan_raw),
        review_raw=review_raw, expected_review_sha256=sha256(review_raw),
        config_raw=cfg_raw, expected_config_sha256=sha256(cfg_raw),
        expected_candidate=candidate or CANDIDATE,
        execution_root=str(Path(cwd or SOURCE_ROOT).resolve()),
        review_authenticator=auth, capacity_raw=cap,
        expected_capacity_sha256=sha256(cap) if cap is not None else None,
        broker_client=broker, now=NOW, cancel_event=cancel_event)


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

    def test_denied_stale_failed_or_missing_lease_falls_back_serial(self):
        raw, cfg = plan([partition("a"), partition("b")], parallelism=2), config()
        cap = capacity(raw, canonical(cfg))
        for broker, reason in ((None, "lease_client_unavailable"),
                               (Broker("deny"), "lease_denied:"),
                               (Broker("stale"), "lease_stale"),
                               (Broker("raise"), "lease_acquire_failed:")):
            with self.subTest(reason=reason):
                result = run(raw, cfg, cap=cap, broker=broker)
                self.assertEqual(1, result["execution"]["effective_parallelism"])
                self.assertTrue(any(item.startswith(reason) for item in result["execution"]["fallback_reasons"]))

    def test_review_is_unavailable_without_trusted_authenticator(self):
        raw = plan([partition("only")])
        with self.assertRaisesRegex(ValidationError, "Authenticated review authority is unavailable"):
            run(raw, config(enabled=False), auth=None)

    def test_authenticator_and_candidate_movement_fail_before_execution(self):
        raw = plan([partition("only")])
        def wrong(value, review_digest, plan_digest, candidate):
            result = authenticator(value, review_digest, plan_digest, candidate)
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


if __name__ == "__main__":
    unittest.main()
