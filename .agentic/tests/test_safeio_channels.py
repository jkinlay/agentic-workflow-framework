"""Closed policies, safe channels, and diagnostic regressions."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic import child_process
from agentic import safeio


class ClosedPolicyTests(unittest.TestCase):
    def test_named_policies_are_closed_and_immutable(self):
        policies = child_process.RUNNER_POLICIES
        self.assertEqual(set(policies), {"git", "host-provider", "codex-log", "streaming"})
        with self.assertRaises(TypeError):
            policies["other"] = policies["git"]
        for name, policy in policies.items():
            with self.subTest(name=name):
                self.assertEqual(policy.name, name)
                self.assertGreater(policy.stdin_limit, 0)
                self.assertLessEqual(policy.aggregate_output_limit,
                                     policy.stdout_limit + policy.stderr_limit)
                with self.assertRaises(FrozenInstanceError):
                    policy.stdin_limit = 0

    def test_operation_budget_is_shared_and_non_resettable(self):
        budget = child_process.OperationBudget("provider-observation", byte_limit=5)
        budget.consume(3)
        self.assertEqual(budget.remaining_bytes, 2)
        with self.assertRaisesRegex(child_process.ChildProcessPolicyError,
                                    "OPERATION_BUDGET"):
            budget.consume(3)
        self.assertEqual(budget.consumed_bytes, 3)
        self.assertFalse(hasattr(budget, "reset"))

    def test_policy_factory_makes_a_shared_budget_and_runner_refuses_overrides(self):
        policy = child_process.RUNNER_POLICIES["streaming"]
        budget = policy.make_budget("two-runs")
        self.assertEqual(budget.byte_limit, policy.operation_budget_limit)
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_POLICY"):
            safeio.run_bounded(
                [sys.executable, "-B", "-c", "pass"],
                policy=policy, stdout_limit=1,
            )

    def test_operation_budget_is_debited_across_runner_calls(self):
        budget = child_process.OperationBudget("shared", byte_limit=5)
        command = [sys.executable, "-B", "-c", "import sys; sys.stdout.write('abc')"]
        first = safeio.run_bounded(command, operation_budget=budget)
        self.assertEqual(first.stdout, b"abc")
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_OPERATION_BUDGET"):
            safeio.run_bounded(command, operation_budget=budget)
        self.assertEqual(budget.consumed_bytes, 3)


class StrictJsonTests(unittest.TestCase):
    def test_bounded_json_rejects_duplicates_nonfinite_and_scalar_roots(self):
        cases = (
            (b'{"a":1,"a":2}', "duplicate"),
            (b'{"a":NaN}', "constant"),
            (b'"scalar"', "root type"),
            (b'{"bad":"\\ud800"}', "surrogate"),
        )
        for raw, reason in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValidationError, reason):
                safeio.loads_bounded_json(raw, maximum=128)

    def test_bounded_json_refuses_oversize_and_wrong_expected_type(self):
        with self.assertRaisesRegex(ValidationError, "JSON_SIZE"):
            safeio.loads_bounded_json(b'{"long":"value"}', maximum=4)
        with self.assertRaisesRegex(ValidationError, "root type"):
            safeio.loads_bounded_json(b'[]', maximum=16, expected_type=dict)

    def test_file_json_read_is_pinned_and_confined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.json").write_bytes(b'{"ok":true}')
            with safeio.Tree(root) as tree:
                self.assertEqual(
                    safeio.read_bounded_json(tree, "value.json", maximum=64),
                    {"ok": True},
                )
                with self.assertRaises(ValidationError):
                    safeio.read_bounded_json(tree, "../outside.json", maximum=64)

    def test_file_json_read_rejects_hardlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            linked = root / "linked.json"
            original.write_bytes(b'{"ok":true}')
            os.link(original, linked)
            with safeio.Tree(root) as tree, self.assertRaises(ValidationError):
                safeio.read_bounded_json(tree, "linked.json", maximum=64)


class SafeWriteAndDiagnosticTests(unittest.TestCase):
    def test_atomic_json_write_is_strict_bounded_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_json(tree, "nested/value.json", {"z": 1, "a": 2}, maximum=64)
                raw = tree.read("nested/value.json")
                self.assertEqual(raw, b'{"a":2,"z":1}\n')
                self.assertEqual(json.loads(raw), {"a": 2, "z": 1})
                with self.assertRaisesRegex(ValidationError, "JSON_SIZE"):
                    safeio.write_json(tree, "large.json", {"value": "x" * 30}, maximum=8)
                with self.assertRaisesRegex(ValidationError, "JSON_VALUE"):
                    safeio.write_json(tree, "nan.json", {"value": float("nan")})
                with self.assertRaisesRegex(ValidationError, "JSON_VALUE"):
                    safeio.write_json(tree, "coerced-key.json", {1: "not strict"})

    def test_safe_log_write_redacts_credentials_paths_and_terminal_controls(self):
        slash = chr(47)
        backslash = chr(92)
        drive = chr(67) + chr(58)
        home_root = slash.join(("", "home", "alice"))
        windows_root = backslash.join((drive, "Users", "Alice"))
        windows_forward_root = slash.join((drive, "Users", "Alice"))
        unc_root = (backslash * 2) + backslash.join(("server", "share"))
        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_log(
                    tree,
                    "logs/result.log",
                    "Authorization: Bearer secret-token\n"
                    "OPENAI_API_KEY=provider-secret\n"
                    + f"at {home_root}{slash}private{slash}file.txt\n"
                    + f"at {windows_root}{backslash}private.txt\n"
                    + f"at {windows_forward_root}{slash}private.txt\n"
                    + f"at {unc_root}{backslash}private.txt\n"
                    + "\x1b[31mFAILED\x1b[0m",
                    maximum=2048,
                )
                rendered = tree.read("logs/result.log").decode("utf-8")
            self.assertNotIn("secret-token", rendered)
            self.assertNotIn("provider-secret", rendered)
            self.assertNotIn(home_root, rendered)
            self.assertNotIn(windows_root, rendered)
            self.assertNotIn(windows_forward_root, rendered)
            self.assertNotIn(unc_root, rendered)
            self.assertNotIn("\x1b", rendered)
            self.assertIn("<redacted-credential>", rendered)
            self.assertIn("<redacted-path>", rendered)
            self.assertIn("\\x1b", rendered)

    def test_diagnostic_digest_binds_raw_bytes_not_redacted_text(self):
        slash = chr(47)
        left = "token=first " + slash.join(("", "home", "alice", "file"))
        right = "token=second " + slash.join(("", "home", "bob", "file"))
        self.assertEqual(
            child_process.render_diagnostic(left),
            child_process.render_diagnostic(right),
        )
        self.assertNotEqual(
            child_process.diagnostic_digest(left),
            child_process.diagnostic_digest(right),
        )
        self.assertEqual(
            child_process.render_diagnostic("see https://example.invalid/public/path"),
            "see https://example.invalid/public/path",
        )

    def test_snapshot_has_digest_and_rejects_linked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.bin").write_bytes(b"payload")
            with safeio.Tree(root) as tree:
                snapshot = safeio.snapshot_file(tree, "value.bin", maximum=64)
                self.assertEqual(snapshot.data, b"payload")
                self.assertEqual(snapshot.size, 7)
                self.assertEqual(len(snapshot.sha256), 64)
            os.link(root / "value.bin", root / "linked.bin")
            with safeio.Tree(root) as tree, self.assertRaises(ValidationError):
                safeio.snapshot_file(tree, "linked.bin", maximum=64)


if __name__ == "__main__":
    unittest.main()
