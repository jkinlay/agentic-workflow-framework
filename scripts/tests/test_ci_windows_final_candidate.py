"""Keep the Windows installed-runtime self-test on final candidate heads."""
from __future__ import annotations

import hashlib
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
TOKEN = re.compile(
    r"\s*(?:(\&\&|\|\||==|!=|\(|\)|,)|('(?:[^']*)')|"
    r"(true|false)|([A-Za-z_][A-Za-z0-9_.-]*))"
)


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class Expression:
    """Evaluate the small GitHub-expression subset used by the job condition."""

    def __init__(self, source, context):
        self.tokens = []
        position = 0
        while position < len(source):
            match = TOKEN.match(source, position)
            if not match:
                raise ValueError(f"unsupported expression at {source[position:]!r}")
            operator, string, boolean, identifier = match.groups()
            self.tokens.append(operator or string or boolean or identifier)
            position = match.end()
        self.position = 0
        self.context = context

    def evaluate(self):
        value = self.parse_or()
        if self.position != len(self.tokens):
            raise ValueError(f"unexpected token {self.tokens[self.position]!r}")
        return bool(value)

    def accept(self, token):
        if self.position < len(self.tokens) and self.tokens[self.position] == token:
            self.position += 1
            return True
        return False

    def require(self, token):
        if not self.accept(token):
            raise ValueError(f"expected {token!r}")

    def parse_or(self):
        value = self.parse_and()
        while self.accept("||"):
            right = self.parse_and()
            value = bool(value) or bool(right)
        return value

    def parse_and(self):
        value = self.parse_equality()
        while self.accept("&&"):
            right = self.parse_equality()
            value = bool(value) and bool(right)
        return value

    def parse_equality(self):
        value = self.parse_primary()
        if self.accept("=="):
            return value == self.parse_primary()
        if self.accept("!="):
            return value != self.parse_primary()
        return value

    def parse_primary(self):
        if self.accept("("):
            value = self.parse_or()
            self.require(")")
            return value
        token = self.tokens[self.position]
        self.position += 1
        if token.startswith("'"):
            return token[1:-1]
        if token in {"true", "false"}:
            return token == "true"
        if self.accept("("):
            arguments = []
            if not self.accept(")"):
                arguments.append(self.parse_or())
                while self.accept(","):
                    arguments.append(self.parse_or())
                self.require(")")
            if token == "always" and not arguments:
                return True
            if token == "startsWith" and len(arguments) == 2:
                return str(arguments[0]).startswith(str(arguments[1]))
            raise ValueError(f"unsupported function {token!r}")
        value = self.context
        for component in token.split("."):
            value = value[component]
        return value


def windows_job_runs(*, event_name, action=None, draft=False, changed=True,
                     detector_result="success", base_ref="main", head_ref="feature"):
    document = workflow()
    condition = document["jobs"]["installed-runtime-self-test"]["if"]
    context = {
        "github": {
            "event_name": event_name,
            "event": {"action": action, "pull_request": {"draft": draft}},
            "base_ref": base_ref,
            "head_ref": head_ref,
        },
        "needs": {
            "windows_runtime_paths": {
                "result": detector_result,
                "outputs": {"runtime_changed": "true" if changed else "false"},
            }
        },
    }
    return Expression(condition, context).evaluate()


class WindowsFinalCandidateCiTests(unittest.TestCase):
    def test_pull_request_trigger_includes_ready_for_review(self):
        document = workflow()
        triggers = document.get("on", document.get(True))
        self.assertEqual(
            ["opened", "synchronize", "reopened", "ready_for_review"],
            (triggers["pull_request"] or {}).get("types"),
        )

    def test_windows_job_runs_only_for_final_candidate_events(self):
        self.assertFalse(windows_job_runs(
            event_name="pull_request", action="synchronize", draft=True, changed=True,
        ))
        self.assertFalse(windows_job_runs(
            event_name="pull_request", action="synchronize", draft=True, changed=True,
            detector_result="failure",
        ))
        self.assertTrue(windows_job_runs(
            event_name="pull_request", action="synchronize", draft=False, changed=True,
        ))
        self.assertTrue(windows_job_runs(
            event_name="pull_request", action="synchronize", draft=False, changed=False,
        ))
        self.assertTrue(windows_job_runs(
            event_name="pull_request", action="ready_for_review", draft=False, changed=False,
        ))
        self.assertTrue(windows_job_runs(event_name="push"))
        self.assertTrue(windows_job_runs(
            event_name="pull_request", action="synchronize", draft=True, changed=False,
            base_ref="main", head_ref="release/1.9.5",
        ))

    def test_linux_checks_and_manifest_are_unconditional_and_unchanged(self):
        document = workflow()
        checks = document["jobs"]["checks"]
        self.assertNotIn("if", checks)
        manifest = [
            step for step in checks["steps"]
            if step.get("name") == "Manifest is current (regenerate and diff)"
        ]
        self.assertEqual(1, len(manifest))
        self.assertNotIn("if", manifest[0])
        self.assertEqual(["3.11", "3.12", "3.13"], checks["strategy"]["matrix"]["python-version"])
        preserved = deepcopy(checks)
        preserved["strategy"]["matrix"]["python-version"] = ["3.11", "3.13"]
        self.assertEqual(
            "94cb1791a3e2f1cb3e00976f8f9467a6da91a95a1d8b1c66330eac64abf37f49",
            digest(preserved),
        )
        self.assertFalse(windows_job_runs(
            event_name="pull_request", action="opened", draft=True, changed=True,
        ))

    def test_permissions_windows_steps_and_timeouts_are_unchanged(self):
        document = workflow()
        self.assertEqual(
            "d8d6aceb1abc41990618a503082c3badcca8897feee0976f222af5b74e30bec2",
            digest(document["permissions"]),
        )
        self.assertEqual(
            "41d6a42088d00eb45919b531da79dfb81606b9640c5f50d26547c81c14de37a0",
            digest(document["jobs"]["windows_runtime_paths"]),
        )
        windows = document["jobs"]["installed-runtime-self-test"]
        self.assertIn("github.event.pull_request.draft == false", windows["if"])
        self.assertEqual({"fail-fast": False, "matrix": {"python-version": ["3.12.10", "3.13.3"]}}, windows["strategy"])
        self.assertEqual("Windows installed-runtime self-test (Python ${{ matrix.python-version }})", windows["name"])
        preserved = deepcopy(windows)
        preserved.pop("strategy")
        preserved["name"] = "Windows installed-runtime self-test"
        setup = next(step for step in preserved["steps"] if step.get("uses") == "actions/setup-python@v5")
        self.assertEqual("${{ matrix.python-version }}", setup["with"]["python-version"])
        setup["with"]["python-version"] = "3.13.3"
        upload = next(step for step in preserved["steps"] if step.get("uses") == "actions/upload-artifact@v4")
        self.assertEqual("windows-installed-runtime-self-test-${{ matrix.python-version }}-${{ github.run_attempt }}", upload["with"]["name"])
        upload["with"]["name"] = "windows-installed-runtime-self-test-${{ github.run_attempt }}"
        self.assertEqual(
            "83409085d8ed77668c9e266b832a61f39778eb6670e8a32e7a8fddab656b695e",
            digest({key: value for key, value in preserved.items() if key != "if"}),
        )


if __name__ == "__main__":
    unittest.main()
