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
    def test_bounded_diagnostic_caps_utf8_after_redaction_and_framing(self):
        code = "AWF_DIAGNOSTIC"
        frame = code + ": "
        marker = "<truncated>"
        credential = "tiny" + "-value"
        local_path = chr(47) + "p"
        payload = "harmless " + credential + " " + local_path + "\n"
        expected = "harmless <redacted-credential> <redacted-path>\\x0a"
        for suffix in ("", "\u00e9", "\U0001f642", "\x00"):
            # Redaction and control escaping expand this initially short input.
            padding = 4096 - len((frame + expected).encode("utf-8"))
            detail = payload + "a" * padding + suffix
            with self.subTest(suffix=repr(suffix)):
                rendered = child_process.render_bounded_diagnostic(
                    detail, code=code, credentials=(credential,), paths=(local_path,))
                raw = rendered.encode("utf-8", errors="strict")
                self.assertEqual(raw.decode("utf-8", errors="strict"), rendered)
                self.assertLessEqual(len(raw), 4096)
                self.assertTrue(rendered.startswith(frame + expected))
                self.assertNotIn(credential, rendered)
                self.assertNotIn(local_path, rendered)
                if suffix:
                    self.assertTrue(rendered.endswith(marker))
                else:
                    self.assertEqual(rendered, frame + expected + "a" * padding)
                    self.assertEqual(len(raw), 4096)
                    self.assertNotIn(marker, rendered)
        for detail, expected_detail in ((b"harmless\xff", "harmless\ufffd"),
                                        ("harmless\ud800\udfff", "harmless\ufffd\ufffd")):
            with self.subTest(detail=repr(detail)):
                rendered = child_process.render_bounded_diagnostic(detail, code=code)
                self.assertEqual(rendered, frame + expected_detail)
                self.assertEqual(rendered.encode("utf-8").decode("utf-8"), rendered)
        multibyte = child_process.render_bounded_diagnostic(
            "harmless " + "\U0001f642" * 2000, code=code)
        self.assertTrue(multibyte.startswith(frame + "harmless \U0001f642"))
        self.assertTrue(multibyte.endswith(marker))
        self.assertLessEqual(len(multibyte.encode("utf-8")), 4096)

    def test_bounded_diagnostic_preserves_code_and_escapes_controls(self):
        code = "AWF_DIAGNOSTIC"
        frame = code + ": "
        marker = "<truncated>"
        controls = "\x00\n\x1b\x85\x7f"
        escaped = r"\x00\x0a\x1b\x85\x7f"
        rendered = child_process.render_bounded_diagnostic(
            "harmless " + controls, code=code)
        self.assertEqual(rendered, frame + "harmless " + escaped)
        # Move each four-byte escape across the cutoff, including partial fits.
        for offset in range(4):
            padding = 4096 - len(frame + marker + "harmless ") - 8 - offset
            with self.subTest(offset=offset):
                rendered = child_process.render_bounded_diagnostic(
                    "harmless " + "a" * padding + controls * 1000, code=code)
                self.assertTrue(rendered.startswith(frame + "harmless "))
                self.assertTrue(rendered.endswith(marker))
                self.assertEqual(rendered[:-len(marker)],
                                 frame + "harmless " + "a" * padding + r"\x00\x0a")
                self.assertLessEqual(len(rendered.encode("utf-8")), 4096)
                self.assertFalse(any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in rendered))
        for invalid in ("", "lower", "BAD: CODE", "BAD\nCODE", "\u00c9", "A" * 65, None):
            with self.subTest(code=invalid), self.assertRaises(ValueError):
                child_process.render_bounded_diagnostic("harmless", code=invalid)

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

    def test_safe_log_write_redacts_structured_cli_credentials_and_short_path(self):
        provider_key = "OPENAI" + "_API_KEY"
        token_key = "to" + "ken"
        provider_secret = "provider" + "-secret"
        token_secret = "secret" + "-value"
        local_path = chr(47) + "secret"
        payload = "\n".join((
            json.dumps({provider_key: provider_secret}, separators=(",", ":")),
            repr({token_key: token_secret}),
            "--" + "access-token " + token_secret,
            "failed at " + local_path,
        ))
        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_log(tree, "result.log", payload, maximum=1024)
                rendered = tree.read("result.log").decode("utf-8")
        for sensitive in (provider_secret, token_secret, local_path):
            self.assertNotIn(sensitive, rendered)
        self.assertGreaterEqual(rendered.count("<redacted-credential>"), 3)
        self.assertIn("<redacted-path>", rendered)

    def test_diagnostic_redacts_quoted_multiword_credentials_and_spaced_paths(self):
        slash = chr(47)
        credential_name = "pass" + "word"
        credential_value = "alpha" + " beta"
        local_path = slash.join(("", "srv", "Alice Smith", "private.txt"))
        payload = (
            credential_name + "=\"" + credential_value + "\"\n"
            + "failed at " + local_path
        )

        rendered = child_process.render_diagnostic(payload)

        self.assertNotIn(credential_value, rendered)
        self.assertNotIn(" beta", rendered)
        self.assertNotIn(local_path, rendered)
        self.assertNotIn(" Smith", rendered)
        self.assertEqual(rendered.count("<redacted-credential>"), 1)
        self.assertEqual(rendered.count("<redacted-path>"), 1)

    def test_log_redacts_prefixed_secret_names_and_complete_spaced_network_path(self):
        backslash = chr(92)
        assignments = (
            ("AWS" + "_SECRET_ACCESS_KEY", "aws" + "-secret-value"),
            ("AZURE" + "_CLIENT_SECRET", "azure" + "-secret-value"),
        )
        network_path = (backslash * 2) + backslash.join(
            ("server", "Shared Folder", "secret.txt")
        )
        payload = "\n".join(
            [name + "=" + value for name, value in assignments]
            + ["failed at " + network_path]
        )

        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_log(tree, "result.log", payload, maximum=1024)
                rendered = tree.read("result.log").decode("utf-8")

        for _name, value in assignments:
            self.assertNotIn(value, rendered)
        self.assertNotIn(network_path, rendered)
        self.assertNotIn(" Folder" + backslash + "secret.txt", rendered)
        self.assertEqual(rendered.count("<redacted-credential>"), 2)
        self.assertEqual(rendered.count("<redacted-path>"), 1)

    def test_diagnostic_redacts_general_prefixed_secret_names_and_spaced_unc_paths(self):
        backslash = chr(92)
        assignments = (
            ("STRIPE" + "_SECRET_KEY", "stripe" + "-sensitive-value"),
            ("DJANGO" + "_SECRET_KEY", "django" + "-sensitive-value"),
            ("SERVICE" + "_SECRET_VALUE", "service" + "-sensitive-value"),
            ("Build" + "_Access_TOKEN_Value", "token" + "-sensitive-value"),
            ("database" + "_PASSWORD_FILE", "password" + "-sensitive-value"),
            ("Partner" + "_API_KEY_Backup", "api-key" + "-sensitive-value"),
        )
        network_paths = (
            (backslash * 2) + backslash.join(
                ("server", "Shared Folder", "secret (1).txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Project Phoenix (Confidential)", "secret.txt")
            ),
        )
        payload = "\n".join(
            [name + "=" + value for name, value in assignments]
            + ["failed at " + path for path in network_paths]
        )

        rendered = child_process.render_diagnostic(payload)

        for _name, value in assignments:
            self.assertNotIn(value, rendered)
        for path in network_paths:
            self.assertNotIn(path, rendered)
        self.assertNotIn(" (1).txt", rendered)
        self.assertNotIn(" (Confidential)" + backslash + "secret.txt", rendered)
        self.assertEqual(rendered.count("<redacted-credential>"), len(assignments))
        self.assertEqual(rendered.count("<redacted-path>"), len(network_paths))

    def test_log_redacts_general_prefixed_secret_names_and_spaced_unc_paths(self):
        backslash = chr(92)
        assignments = (
            ("STRIPE" + "_SECRET_KEY", "stripe" + "-sensitive-value"),
            ("DJANGO" + "_SECRET_KEY", "django" + "-sensitive-value"),
            ("SERVICE" + "_SECRET_VALUE", "service" + "-sensitive-value"),
            ("Build" + "_Access_TOKEN_Value", "token" + "-sensitive-value"),
            ("database" + "_PASSWORD_FILE", "password" + "-sensitive-value"),
            ("Partner" + "_API_KEY_Backup", "api-key" + "-sensitive-value"),
        )
        network_paths = (
            (backslash * 2) + backslash.join(
                ("server", "Shared Folder", "secret (1).txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Project Phoenix (Confidential)", "secret.txt")
            ),
        )
        payload = "\n".join(
            [name + "=" + value for name, value in assignments]
            + ["failed at " + path for path in network_paths]
        )

        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_log(tree, "result.log", payload, maximum=2048)
                rendered = tree.read("result.log").decode("utf-8")

        for _name, value in assignments:
            self.assertNotIn(value, rendered)
        for path in network_paths:
            self.assertNotIn(path, rendered)
        self.assertNotIn(" (1).txt", rendered)
        self.assertNotIn(" (Confidential)" + backslash + "secret.txt", rendered)
        self.assertEqual(rendered.count("<redacted-credential>"), len(assignments))
        self.assertEqual(rendered.count("<redacted-path>"), len(network_paths))

    def test_diagnostic_redacts_complete_json_secret_with_escaped_quote(self):
        secret_suffixes = ("omega", "sigma")
        payload = "\n".join((
            json.dumps({
                "STRIPE" + "_SECRET_KEY": "alpha" + chr(34) + secret_suffixes[0],
            }),
            "DJANGO" + "_SECRET_KEY='delta\\'" + secret_suffixes[1] + "'",
        ))

        rendered = child_process.render_diagnostic(payload)

        for secret_suffix in secret_suffixes:
            self.assertNotIn(secret_suffix, rendered)
        self.assertEqual(
            rendered,
            '{"STRIPE_SECRET_KEY": <redacted-credential>}'
            "\\x0aDJANGO_SECRET_KEY=<redacted-credential>",
        )

    def test_log_redacts_complete_json_secret_with_escaped_quote(self):
        secret_suffixes = ("omega", "sigma")
        payload = "\n".join((
            json.dumps({
                "STRIPE" + "_SECRET_KEY": "alpha" + chr(34) + secret_suffixes[0],
            }),
            "DJANGO" + "_SECRET_KEY='delta\\'" + secret_suffixes[1] + "'",
        ))

        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_log(tree, "result.log", payload, maximum=1024)
                rendered = tree.read("result.log").decode("utf-8")

        for secret_suffix in secret_suffixes:
            self.assertNotIn(secret_suffix, rendered)
        self.assertEqual(
            rendered,
            '{"STRIPE_SECRET_KEY": <redacted-credential>}'
            "\\x0aDJANGO_SECRET_KEY=<redacted-credential>\n",
        )

    def test_diagnostic_redacts_complete_unc_paths_with_valid_punctuation(self):
        backslash = chr(92)
        network_paths = (
            (backslash * 2) + backslash.join(
                ("server", "Shared Folder", "secret [1].txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Project {Phoenix}", "secret.txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Shared Folder", "secret,backup.txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Project Phoenix", "secret!#$%&'()+,-.;=@[]^_`{}~.txt")
            ),
        )
        payload = "\n".join("failed at " + path for path in network_paths)

        rendered = child_process.render_diagnostic(payload)

        self.assertEqual(
            rendered,
            "\\x0a".join(["failed at <redacted-path>"] * len(network_paths)),
        )

    def test_log_redacts_complete_unc_paths_with_valid_punctuation(self):
        backslash = chr(92)
        network_paths = (
            (backslash * 2) + backslash.join(
                ("server", "Shared Folder", "secret [1].txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Project {Phoenix}", "secret.txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Shared Folder", "secret,backup.txt")
            ),
            (backslash * 2) + backslash.join(
                ("server", "Project Phoenix", "secret!#$%&'()+,-.;=@[]^_`{}~.txt")
            ),
        )
        payload = "\n".join("failed at " + path for path in network_paths)

        with tempfile.TemporaryDirectory() as directory:
            with safeio.Tree(directory) as tree:
                safeio.write_log(tree, "result.log", payload, maximum=2048)
                rendered = tree.read("result.log").decode("utf-8")

        self.assertEqual(
            rendered,
            "\\x0a".join(["failed at <redacted-path>"] * len(network_paths)) + "\n",
        )

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
