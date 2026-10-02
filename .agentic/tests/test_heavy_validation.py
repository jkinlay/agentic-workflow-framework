"""Synthetic bounded-parallel validation tests; no proprietary engine is required."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, sha256
from agentic.heavy_validation import run_validation


NOW = "2026-10-02T09:00:00Z"


def config(*, enabled=True, heavy=2, gpu=1, resources=None):
    return {
        "execution": {
            "host_broker": {
                "enabled": enabled,
                "broker_id": "synthetic-broker" if enabled else "",
                "lease_before_dispatch": True,
                "max_workers": 6,
                "max_heavy_jobs": heavy,
                "max_gpu_jobs": gpu,
                "resources": dict(resources or {}),
            },
            "max_tokens_per_ticket": 100000,
            "max_cost_microusd_per_ticket": 10000000,
            "daily_project_cost_microusd": 50000000,
            "native_streams": {"enabled": True, "dispatch_policy": "ready_independent"},
        }
    }


def capacity(*, workers=6, heavy=2, gpu=1, resources=None, engines=None, observed_at=NOW):
    return canonical({
        "format": "awf-heavy-validation-capacity-1",
        "observed_at": observed_at,
        "broker_id": "synthetic-broker",
        "workers_available": workers,
        "heavy_jobs_available": heavy,
        "gpu_jobs_available": gpu,
        "resources_available": dict(resources or {}),
        "engines": dict({"python": {"parallel_available": True, "parallel_slots": workers}}
                        if engines is None else engines),
    })


def plan(partitions, *, engine="python", resource_class="heavy", resources=None, parallelism=4):
    return canonical({
        "format": "awf-heavy-validation-plan-1",
        "workload_id": "qa9430-synthetic",
        "engine": engine,
        "resource_class": resource_class,
        "required_resources": list(resources or []),
        "requested_parallelism": parallelism,
        "partitions": partitions,
    })


def partition(name, code="print('ok')", *, timeout=5, exits=None):
    return {"name": name, "argv": [sys.executable, "-c", code],
            "timeout_seconds": timeout, "accepted_exit_codes": list(exits or [0])}


def review(plan_raw, *, decision="APPROVE"):
    return canonical({
        "format": "awf-heavy-validation-review-1",
        "plan_sha256": sha256(plan_raw),
        "decision": decision,
        "reviewer_identity": "synthetic-independent-reviewer",
        "reviewed_at": NOW,
        "expires_at": "2026-10-03T09:00:00Z",
    })


def run(plan_raw, cfg, *, capacity_raw=None, cancel_event=None, review_raw=None):
    review_raw = review_raw or review(plan_raw)
    return run_validation(
        plan_raw=plan_raw,
        expected_plan_sha256=sha256(plan_raw),
        review_raw=review_raw,
        expected_review_sha256=sha256(review_raw),
        config=cfg,
        capacity_raw=capacity_raw,
        expected_capacity_sha256=sha256(capacity_raw) if capacity_raw is not None else None,
        now=NOW,
        max_capacity_age_seconds=300,
        cancel_event=cancel_event,
    )


class HeavyValidationTests(unittest.TestCase):
    def test_parallel_ceiling_uses_broker_observation_engine_and_named_resource(self):
        raw = plan([partition("c"), partition("a"), partition("b")],
                   resources=["matlab_license"], parallelism=5)
        cap = capacity(workers=5, heavy=4, resources={"matlab_license": 2},
                       engines={"python": {"parallel_available": True, "parallel_slots": 3}})
        result = run(raw, config(heavy=4, resources={"matlab_license": 4}), capacity_raw=cap)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(2, result["execution"]["effective_parallelism"])
        self.assertEqual("PARALLEL", result["execution"]["mode"])
        self.assertEqual(["a", "b", "c"], [item["name"] for item in result["partitions"]])

    def test_gpu_ceiling_and_capacity_are_both_binding(self):
        raw = plan([partition("a"), partition("b")], resource_class="gpu", parallelism=6)
        cap = capacity(gpu=3, engines={"python": {"parallel_available": True, "parallel_slots": 5}})
        result = run(raw, config(gpu=1), capacity_raw=cap)
        self.assertEqual(1, result["execution"]["effective_parallelism"])
        self.assertIn("broker.max_gpu_jobs=1", result["execution"]["fallback_reasons"])

    def test_serial_fallback_for_disabled_broker_missing_capacity_engine_and_license(self):
        cases = [
            (config(enabled=False), None, "broker_disabled"),
            (config(), None, "observed_capacity_unavailable"),
            (config(), capacity(engines={}), "engine_parallel_unobserved:python"),
            (config(resources={"matlab_license": 2}),
             capacity(resources={}, engines={"python": {"parallel_available": True, "parallel_slots": 4}}),
             "resource_capacity_unavailable:matlab_license"),
        ]
        for cfg, cap, reason in cases:
            with self.subTest(reason=reason):
                raw = plan([partition("a"), partition("b")], resources=(
                    ["matlab_license"] if "resource_" in reason else []))
                result = run(raw, cfg, capacity_raw=cap)
                self.assertEqual("PASS", result["status"])
                self.assertEqual("SERIAL_FALLBACK", result["execution"]["mode"])
                self.assertEqual(1, result["execution"]["effective_parallelism"])
                self.assertIn(reason, result["execution"]["fallback_reasons"])

    def test_engine_labels_cover_python_wolfram_matlab_and_extensions(self):
        for engine in ["python", "wolfram", "matlab", "custom_solver_v2"]:
            with self.subTest(engine=engine):
                raw = plan([partition("only")], engine=engine)
                result = run(raw, config(enabled=False))
                self.assertEqual(engine, result["engine"])
                self.assertEqual("PASS", result["status"])

    def test_review_and_hash_binding_reject_unreviewed_or_changed_commands(self):
        raw = plan([partition("only")])
        approved = review(raw)
        with self.assertRaisesRegex(ValidationError, "plan SHA-256"):
            run_validation(plan_raw=raw, expected_plan_sha256="0" * 64,
                           review_raw=approved, expected_review_sha256=sha256(approved),
                           config=config(), now=NOW)
        changed = plan([partition("only", "raise SystemExit(7)")])
        with self.assertRaisesRegex(ValidationError, "review does not bind"):
            run_validation(plan_raw=changed, expected_plan_sha256=sha256(changed),
                           review_raw=approved, expected_review_sha256=sha256(approved),
                           config=config(), now=NOW)
        rejected = review(raw, decision="REJECT")
        with self.assertRaisesRegex(ValidationError, "not approved"):
            run_validation(plan_raw=raw, expected_plan_sha256=sha256(raw),
                           review_raw=rejected, expected_review_sha256=sha256(rejected),
                           config=config(), now=NOW)

    def test_shell_strings_control_characters_and_duplicate_partitions_reject(self):
        value = json.loads(plan([partition("only")]))
        value["partitions"][0]["argv"] = "python -c unsafe"
        bad = canonical(value)
        with self.assertRaisesRegex(ValidationError, "argv must be a list"):
            run(bad, config())
        for argv in [[sys.executable, "bad\x00arg"], [sys.executable, "line\nbreak"]]:
            value = json.loads(plan([partition("only")]))
            value["partitions"][0]["argv"] = argv
            bad = canonical(value)
            with self.assertRaisesRegex(ValidationError, "argv token"):
                run(bad, config())
        duplicate = plan([partition("same"), partition("same")])
        with self.assertRaisesRegex(ValidationError, "Duplicate partition name"):
            run(duplicate, config())

    def test_failure_timeout_missing_executable_and_cancel_are_complete_fail_closed_evidence(self):
        cancelled = threading.Event()
        cancelled.set()
        cases = [
            (plan([partition("ok"), partition("failed", "raise SystemExit(7)")]), None,
             {"PASS", "FAILED"}),
            (plan([partition("timeout", "import time; time.sleep(2)", timeout=1)]), None,
             {"TIMED_OUT"}),
            (canonical({"format": "awf-heavy-validation-plan-1", "workload_id": "qa9430-synthetic",
                        "engine": "custom", "resource_class": "heavy", "required_resources": [],
                        "requested_parallelism": 1, "partitions": [{"name": "missing",
                        "argv": ["definitely-missing-qa9430-executable"], "timeout_seconds": 1,
                        "accepted_exit_codes": [0]}]}), None, {"FAILED"}),
            (plan([partition("a"), partition("b")]), cancelled, {"CANCELLED"}),
        ]
        for raw, event, expected_states in cases:
            with self.subTest(states=expected_states):
                result = run(raw, config(), cancel_event=event)
                self.assertEqual("FAIL", result["status"])
                self.assertEqual(len(json.loads(raw)["partitions"]), len(result["partitions"]))
                self.assertTrue(result["all_partitions_terminal"])
                self.assertEqual(expected_states, {item["state"] for item in result["partitions"]})
                for item in result["partitions"]:
                    self.assertIsNotNone(item["started_at"])
                    self.assertIsNotNone(item["ended_at"])

    def test_waits_for_every_partition_and_never_passes_on_partial_success(self):
        raw = plan([partition("fast-fail", "raise SystemExit(4)"),
                    partition("slow-pass", "import time; time.sleep(0.2); print('done')")], parallelism=2)
        result = run(raw, config(), capacity_raw=capacity())
        self.assertEqual(2, result["execution"]["completed_terminal_count"])
        self.assertEqual(2, result["execution"]["scheduled_partition_count"])
        self.assertEqual("FAIL", result["status"])
        self.assertEqual({"FAILED", "PASS"}, {item["state"] for item in result["partitions"]})

    def test_large_output_is_drained_without_deadlock_and_retained_bounded(self):
        expected = b"x" * 400_000
        raw = plan([partition("large", "import sys; sys.stdout.write('x' * 400000)", timeout=5)])
        result = run(raw, config(enabled=False))
        item = result["partitions"][0]
        self.assertEqual("PASS", result["status"])
        self.assertEqual(len(expected), item["stdout_bytes"])
        self.assertEqual(hashlib.sha256(expected).hexdigest(), item["stdout_sha256"])
        self.assertTrue(item["stdout_truncated"])
        self.assertLess(len(item["stdout"].encode("utf-8")), item["stdout_bytes"])

    def test_provider_api_keys_are_not_forwarded_to_partitions(self):
        code = "import os; print(os.environ.get('OPENAI_API_KEY', 'absent'))"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}):
            result = run(plan([partition("env", code)]), config(enabled=False))
        self.assertEqual("PASS", result["status"])
        self.assertEqual(["absent"], result["partitions"][0]["stdout"].splitlines())

    def test_partition_ids_order_and_aggregate_are_deterministic(self):
        left = plan([partition("z"), partition("a")])
        right = plan([partition("a"), partition("z")])
        one = run(left, config(enabled=False))
        two = run(right, config(enabled=False))
        self.assertEqual([item["partition_id"] for item in one["partitions"]],
                         [item["partition_id"] for item in two["partitions"]])
        self.assertEqual(one["aggregate_sha256"], two["aggregate_sha256"])

    def test_stale_capacity_falls_back_serial_and_config_caps_are_unchanged(self):
        cfg = config(heavy=5, resources={"wolfram_kernel": 2})
        original = deepcopy(cfg)
        stale = capacity(observed_at="2026-10-02T08:00:00Z")
        result = run(plan([partition("a"), partition("b")]), cfg, capacity_raw=stale)
        self.assertEqual(1, result["execution"]["effective_parallelism"])
        self.assertIn("observed_capacity_stale", result["execution"]["fallback_reasons"])
        self.assertEqual(original, cfg)
        self.assertEqual({"max_tokens_per_ticket": 100000,
                          "max_cost_microusd_per_ticket": 10000000,
                          "daily_project_cost_microusd": 50000000}, result["preserved_caps"])
        self.assertEqual({"enabled": True, "dispatch_policy": "ready_independent"},
                         result["native_streams"])


if __name__ == "__main__":
    unittest.main()
