"""Offline tests for the AWF Claude review adapter.

No network: GitHub and Anthropic calls are replaced with fakes. Run with
`python3 verify.py unit` or `python3 -m unittest discover -s tests` from the
adapter directory.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ADAPTER = os.path.dirname(HERE)
sys.path.insert(0, ADAPTER)

import awf_review_common as common  # noqa: E402
import build_input  # noqa: E402
import policy  # noqa: E402
import publish  # noqa: E402
import run_model  # noqa: E402
import verify  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40
IDENTITY = "awf-reviewer[bot]"


def find_upwards(relative: str) -> str:
    """Locate a file in the project layout or the framework scaffold layout."""
    here = ADAPTER
    for _ in range(8):
        for candidate in (os.path.join(here, relative),
                          os.path.join(here, "scaffolds", "providers", "copilot", relative),
                          os.path.join(here, "scaffolds", "providers", "claude", relative)):
            if os.path.isfile(candidate):
                return candidate
        here = os.path.dirname(here)
    raise FileNotFoundError(relative)


SCHEMA_PATH = None
for candidate in (".agentic/schemas/review-report.schema.json", "schemas/review-report.schema.json"):
    try:
        SCHEMA_PATH = find_upwards(candidate)
        break
    except FileNotFoundError:
        continue
if SCHEMA_PATH is None:
    raise FileNotFoundError("review-report.schema.json")
with open(SCHEMA_PATH, encoding="utf-8") as _handle:
    SCHEMA = json.load(_handle)

POLICY_TEXT = textwrap.dedent(f"""\
    schema_version: 1
    review:
      required_external_engine: claude
      optional_engines: [copilot]
      reviewer_identities:
        copilot: copilot-pull-request-reviewer[bot]
        claude: "{IDENTITY}"
      claude:
        model: "claude-example-model-2026-01-01"
      qualification:
        effective_permissions_verified: true
        instruction_paths_protected: true
        head_binding_verified: true
      required_check: awf/review
      fail_closed: true
    """)


def report(status="findings", **overrides):
    base = {
        "schema_version": 1, "engine": "claude", "model": "m", "vendor": "anthropic",
        "reviewer_identity": IDENTITY, "repository": "o/r", "head_sha": SHA_A, "status": status,
        "findings": [] if status != "findings" else [{
            "id": "F1", "severity": "high", "title": "Skipped test", "description": "test marked skip",
            "file": "src/a.py", "line": 12, "side": "RIGHT", "phase": "blind", "blocking": True}],
        "claim_assessments": [], "limitations": [],
    }
    if status == "error":
        base["error"] = "boom"
    base.update(overrides)
    return base


class CommonTests(unittest.TestCase):
    def test_validator_accepts_valid_and_rejects_invalid(self):
        self.assertEqual(common.validate_report(report(), SCHEMA), [])
        self.assertEqual(common.validate_report(report("no_findings"), SCHEMA), [])
        self.assertTrue(common.validate_report(report(head_sha="nope"), SCHEMA))
        self.assertTrue(common.validate_report(report(extra=1), SCHEMA))
        bad = report("no_findings"); bad["findings"] = report()["findings"]
        self.assertTrue(common.validate_report(bad, SCHEMA))
        bad = report("error"); del bad["error"]
        self.assertTrue(common.validate_report(bad, SCHEMA))
        bad = report(); bad["findings"][0]["severity"] = "urgent"
        self.assertTrue(common.validate_report(bad, SCHEMA))

    def test_yaml_lite_reads_policy_and_fails_closed(self):
        doc = common.load_yaml_lite(POLICY_TEXT)
        self.assertEqual(common.get_nested(doc, "review.reviewer_identities.claude"), IDENTITY)
        self.assertEqual(doc["review"]["optional_engines"], ["copilot"])
        self.assertTrue(doc["review"]["fail_closed"])
        with self.assertRaises(common.AdapterError):
            common.load_yaml_lite("a:\n\t- x\n")
        with self.assertRaises(common.AdapterError):
            common.load_yaml_lite("a:\n  - k: v\n")

    def test_bounds_and_binary_detection(self):
        text, truncated = common.bounded_text("x" * 10, 4)
        self.assertTrue(truncated and text.startswith("xxxx"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.bin")
            with open(path, "wb") as handle:
                handle.write(b"\x00\x01\x02")
            self.assertEqual(common.read_text_file(path, 100)[2], True)


class PolicyTests(unittest.TestCase):
    def test_resolve_claude_outputs(self):
        outputs = policy.resolve(common.load_yaml_lite(POLICY_TEXT), "claude", require_required=True)
        self.assertEqual(outputs["reviewer_identity"], IDENTITY)
        self.assertEqual(outputs["model"], "claude-example-model-2026-01-01")
        self.assertEqual(outputs["task_contract_path"], ".agentic/task-contract.yaml")

    def test_copilot_identity_is_adapter_constant_and_placeholders_fail(self):
        doc = common.load_yaml_lite(POLICY_TEXT.replace(f'claude: "{IDENTITY}"', 'claude: "__AWF_CLAUDE_REVIEWER_LOGIN__"'))
        with self.assertRaises(common.AdapterError):
            policy.resolve(doc, "claude", False)
        doc = common.load_yaml_lite(POLICY_TEXT.replace('copilot: copilot-pull-request-reviewer[bot]', 'copilot: someone-else[bot]'))
        self.assertEqual(policy.resolve(doc, "copilot", False)["reviewer_identity"], policy.COPILOT_IDENTITY)
        with self.assertRaises(common.AdapterError):
            policy.resolve(common.load_yaml_lite(POLICY_TEXT), "codex-review", False)
        with self.assertRaises(common.AdapterError):
            policy.resolve(common.load_yaml_lite(POLICY_TEXT), "copilot", require_required=True)


class BuildInputTests(unittest.TestCase):
    def make_checkout(self, tmp: str, head: str = SHA_A) -> str:
        pr_dir = os.path.join(tmp, "pr")
        os.makedirs(os.path.join(pr_dir, ".git"))
        os.makedirs(os.path.join(pr_dir, "src"))
        with open(os.path.join(pr_dir, ".git", "HEAD"), "w") as handle:
            handle.write(head + "\n")
        with open(os.path.join(pr_dir, "src", "a.py"), "w") as handle:
            handle.write("print('hello')\n" * 50)
        # A real file OUTSIDE the checkout that a traversal path would reach if the guard failed.
        with open(os.path.join(tmp, "outside.txt"), "w") as handle:
            handle.write("SECRET-OUTSIDE-CHECKOUT\n")
        return pr_dir

    def build_with(self, tmp, files, extra_args=()):
        with mock.patch.object(build_input, "github_paginate", return_value=files):
            pr_dir = self.make_checkout(tmp)
            args = build_input.parse_args(["--repo", "o/r", "--pr", "7", "--base-sha", SHA_B, "--head-sha", SHA_A,
                                           "--pr-dir", pr_dir, "--out", "x", *extra_args])
            return build_input.build(args, "token")

    def test_build_excludes_pr_text_and_records_limitations(self):
        files = [{"filename": "src/a.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ -1 +1 @@\n-x\n+y"},
                 {"filename": "gone.py", "status": "removed", "additions": 0, "deletions": 3, "patch": "@@ -1,3 +0,0 @@\n-a\n-b\n-c"},
                 {"filename": "missing.txt", "status": "added", "additions": 1, "deletions": 0, "patch": None}]
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.build_with(tmp, files, ("--max-file-bytes", "100"))
        self.assertNotIn("title", payload)
        self.assertIn("pull request body", payload["excluded_by_design"])
        self.assertEqual([f["path"] for f in payload["files"]], ["src/a.py"])
        self.assertTrue(payload["files"][0]["truncated"])
        joined = " ".join(payload["limitations"])
        self.assertIn("missing.txt", joined)
        self.assertIn("No task contract", joined)
        self.assertIn("claims phase was skipped", joined)
        self.assertIn("No textual patch", joined)

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        """Platform-independent: '../outside.txt' and an absolute path must never be read."""
        with tempfile.TemporaryDirectory() as tmp:
            absolute_outside = os.path.join(tmp, "outside.txt")
            files = [{"filename": "../outside.txt", "status": "added", "additions": 1, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+x"},
                     {"filename": absolute_outside, "status": "added", "additions": 1, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+x"},
                     {"filename": "src/a.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ -1 +1 @@\n-x\n+y"}]
            payload = self.build_with(tmp, files)
        self.assertEqual([f["path"] for f in payload["files"]], ["src/a.py"])
        self.assertNotIn("SECRET-OUTSIDE-CHECKOUT", json.dumps(payload))
        joined = " ".join(payload["limitations"])
        self.assertIn("../outside.txt", joined)
        self.assertIn("not readable from the checkout", joined)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported on this platform")
    def test_symlink_escape_is_rejected(self):
        """Symlink coverage, separate from traversal: skipped where symlinks need privileges."""
        with tempfile.TemporaryDirectory() as tmp:
            pr_dir = self.make_checkout(tmp)
            try:
                os.symlink(os.path.join(tmp, "outside.txt"), os.path.join(pr_dir, "escape.txt"))
            except (OSError, NotImplementedError) as err:
                self.skipTest(f"cannot create symlinks here: {err}")
            files = [{"filename": "escape.txt", "status": "added", "additions": 1, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+x"}]
            with mock.patch.object(build_input, "github_paginate", return_value=files):
                args = build_input.parse_args(["--repo", "o/r", "--pr", "7", "--base-sha", SHA_B, "--head-sha", SHA_A,
                                               "--pr-dir", pr_dir, "--out", "x"])
                payload = build_input.build(args, "token")
        self.assertEqual(payload["files"], [])
        self.assertNotIn("SECRET-OUTSIDE-CHECKOUT", json.dumps(payload))
        self.assertIn("escape.txt", " ".join(payload["limitations"]))

    def test_head_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build_input, "github_paginate", return_value=[]):
            pr_dir = self.make_checkout(tmp, head=SHA_B)
            args = build_input.parse_args(["--repo", "o/r", "--pr", "7", "--base-sha", SHA_B, "--head-sha", SHA_A,
                                           "--pr-dir", pr_dir, "--out", "x"])
            with self.assertRaises(common.AdapterError):
                build_input.build(args, "token")


class RunModelTests(unittest.TestCase):
    def make_input(self, tmp: str, with_report: bool) -> str:
        payload = {"schema_version": 1, "repository": "o/r", "pr_number": 7, "base_sha": SHA_B, "head_sha": SHA_A,
                   "changed_files": [{"path": "src/a.py", "status": "modified", "additions": 1, "deletions": 0, "patch_included": True}],
                   "diff": "@@ -1 +1 @@\n-x\n+y", "files": [], "task_contract": "objective: x",
                   "execution_report": '{"tests": "all pass"}' if with_report else None, "limitations": ["L0"], "bounds": {}}
        path = os.path.join(tmp, "input.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    @staticmethod
    def fake_api(responses):
        calls = []

        def _fake(base_url, body, timeout):
            calls.append(body)
            tool = body["tool_choice"]["name"]
            return {"content": [{"type": "tool_use", "id": f"toolu_{tool}", "name": tool, "input": responses[tool]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "tool_use"}
        return _fake, calls

    def test_two_phases_add_but_never_withdraw(self):
        responses = {
            "submit_review": {"status": "findings", "limitations": ["did not see tests"],
                              "findings": [{"severity": "medium", "title": "T1", "description": "D1", "file": "src/a.py",
                                            "line": 1, "side": "RIGHT", "blocking": False}]},
            "assess_claims": {"claim_assessments": [{"claim_id": "C1", "status": "contradicted", "evidence": "no tests in diff"}],
                              "additional_findings": [{"severity": "high", "title": "T2", "description": "D2", "file": "src/a.py",
                                                       "blocking": False}]},
        }
        fake, calls = self.fake_api(responses)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", fake):
            args = run_model.parse_args(["--in", self.make_input(tmp, True), "--schema", SCHEMA_PATH, "--out", "r",
                                         "--model", "m", "--reviewer-identity", IDENTITY])
            rep, meta, code = run_model.run(args)
        self.assertEqual(code, 0)
        self.assertEqual(common.validate_report(rep, SCHEMA), [])
        self.assertEqual([f["phase"] for f in rep["findings"]], ["blind", "claims"])
        self.assertEqual([f["id"] for f in rep["findings"]], ["F1", "F2"])
        self.assertTrue(rep["findings"][1]["blocking"], "high severity is forced blocking")
        self.assertEqual(rep["claim_assessments"][0]["status"], "contradicted")
        self.assertIn("did not see tests", rep["limitations"])
        self.assertEqual(len(calls), 2)
        blind_messages, claims_messages = calls[0]["messages"], calls[1]["messages"]
        self.assertEqual(len(blind_messages), 1)
        self.assertNotIn("UNTRUSTED CLAIMS", blind_messages[0]["content"])
        # The claims phase continues the blind conversation: same diff/contract turn, the model's
        # own blind answer, then a tool_result plus the untrusted claims.
        self.assertEqual([m["role"] for m in claims_messages], ["user", "assistant", "user"])
        self.assertEqual(claims_messages[0]["content"], blind_messages[0]["content"])
        self.assertIn("@@ -1 +1 @@", claims_messages[0]["content"])
        self.assertEqual(claims_messages[1]["content"][0]["name"], "submit_review")
        final_turn = claims_messages[2]["content"]
        self.assertEqual(final_turn[0]["type"], "tool_result")
        self.assertEqual(final_turn[0]["tool_use_id"], "toolu_submit_review")
        self.assertIn("UNTRUSTED CLAIMS", final_turn[1]["text"])
        self.assertEqual({t["name"] for t in calls[1]["tools"]}, {"submit_review", "assess_claims"})
        self.assertEqual(calls[1]["tool_choice"]["name"], "assess_claims")
        for call in calls:
            self.assertNotIn("temperature", call, "Claude Opus 5 rejects non-default sampling parameters")
            self.assertNotIn("top_p", call)
            self.assertNotIn("top_k", call)

    def test_active_workflow_sets_opus5_runtime_bounds(self):
        with open(find_upwards(".github/workflows/awf-review-claude.yml"), encoding="utf-8") as handle:
            model_job = handle.read().split("  publish:", 1)[0]
        self.assertRegex(model_job, r"(?m)^    timeout-minutes: 30$")
        self.assertRegex(model_job, r"(?m)^          --max-tokens 20000$")
        self.assertRegex(model_job, r"(?m)^          --timeout 540$")

    def test_no_report_means_single_blind_phase(self):
        fake, calls = self.fake_api({"submit_review": {"status": "no_findings", "findings": [], "limitations": []}})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", fake):
            args = run_model.parse_args(["--in", self.make_input(tmp, False), "--schema", SCHEMA_PATH, "--out", "r",
                                         "--model", "m", "--reviewer-identity", IDENTITY])
            rep, _, code = run_model.run(args)
        self.assertEqual((code, rep["status"], len(calls)), (0, "no_findings", 1))

    def test_api_failure_yields_error_report_and_nonzero_exit(self):
        def failing(base_url, body, timeout):
            raise common.AdapterError("HTTP 529: overloaded")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", failing):
            args = run_model.parse_args(["--in", self.make_input(tmp, True), "--schema", SCHEMA_PATH, "--out", "r",
                                         "--model", "m", "--reviewer-identity", IDENTITY])
            rep, _, code = run_model.run(args)
        self.assertEqual((code, rep["status"]), (1, "error"))
        self.assertEqual(common.validate_report(rep, SCHEMA), [])

    def test_malformed_tool_input_is_an_error_not_no_findings(self):
        cases = {
            "empty object": {},
            "missing findings": {"status": "no_findings", "limitations": []},
            "unknown field": {"status": "no_findings", "findings": [], "limitations": [], "verdict": "approve"},
            "bad severity": {"status": "findings", "limitations": [],
                             "findings": [{"severity": "urgent", "title": "t", "description": "d", "file": "f", "blocking": True}]},
            "status contradicts findings": {"status": "no_findings", "limitations": [],
                                            "findings": [{"severity": "high", "title": "t", "description": "d", "file": "f", "blocking": True}]},
            "findings status with none": {"status": "findings", "findings": [], "limitations": []},
        }
        for label, answer in cases.items():
            fake, _ = self.fake_api({"submit_review": answer})
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", fake):
                args = run_model.parse_args(["--in", self.make_input(tmp, False), "--schema", SCHEMA_PATH, "--out", "r",
                                             "--model", "m", "--reviewer-identity", IDENTITY])
                rep, _, code = run_model.run(args)
            self.assertEqual((code, rep["status"]), (1, "error"), label)
            self.assertEqual(common.validate_report(rep, SCHEMA), [], label)

    def test_malformed_claims_answer_is_an_error(self):
        responses = {"submit_review": {"status": "no_findings", "findings": [], "limitations": []},
                     "assess_claims": {"claim_assessments": [{"claim_id": "C1", "status": "maybe", "evidence": "?"}],
                                       "additional_findings": []}}
        fake, _ = self.fake_api(responses)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", fake):
            args = run_model.parse_args(["--in", self.make_input(tmp, True), "--schema", SCHEMA_PATH, "--out", "r",
                                         "--model", "m", "--reviewer-identity", IDENTITY])
            rep, _, code = run_model.run(args)
        self.assertEqual((code, rep["status"]), (1, "error"))

    def test_wrong_tool_is_an_error(self):
        def wrong(base_url, body, timeout):
            return {"content": [{"type": "text", "text": "I approve this PR"}], "stop_reason": "end_turn"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", wrong):
            args = run_model.parse_args(["--in", self.make_input(tmp, False), "--schema", SCHEMA_PATH, "--out", "r",
                                         "--model", "m", "--reviewer-identity", IDENTITY])
            rep, _, code = run_model.run(args)
        self.assertEqual((code, rep["status"]), (1, "error"))

    def test_max_tokens_without_tool_use_is_an_error_not_no_findings(self):
        def exhausted(base_url, body, timeout):
            return {
                "content": [{"type": "thinking", "thinking": "Review still in progress."}],
                "usage": {"input_tokens": 1, "output_tokens": body["max_tokens"]},
                "stop_reason": "max_tokens",
            }

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_model, "anthropic_request", exhausted):
            args = run_model.parse_args(["--in", self.make_input(tmp, False), "--schema", SCHEMA_PATH, "--out", "r",
                                         "--model", "m", "--reviewer-identity", IDENTITY])
            rep, _, code = run_model.run(args)
        self.assertEqual((code, rep["status"]), (1, "error"))
        self.assertIn("stop_reason=max_tokens", rep["error"])
        self.assertEqual(common.validate_report(rep, SCHEMA), [])


class PublishTests(unittest.TestCase):
    PATCH = "@@ -10,3 +10,4 @@\n context\n-old\n+new\n+added\n context2"

    def test_payload_consolidates_all_findings_and_is_comment_only(self):
        rep = report()
        rep["findings"].append({"id": "F2", "severity": "low", "title": "Elsewhere", "description": "not in diff",
                                "file": "src/a.py", "line": 99, "side": "RIGHT", "phase": "blind", "blocking": False})
        payload = publish.build_payload(rep)
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], SHA_A)
        self.assertEqual(payload["comments"], [])
        self.assertIn("### Findings", payload["body"])
        self.assertIn("src/a.py:12", payload["body"])
        self.assertIn("src/a.py:99", payload["body"])
        self.assertIn("All findings are consolidated", payload["body"])
        self.assertIn("awf-review-status: findings", payload["body"])
        self.assertNotIn("APPROVE", payload["event"])

    def write_report(self, tmp: str, rep: dict) -> str:
        path = os.path.join(tmp, "report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rep, handle)
        return path

    def run_main(self, tmp, rep, live=SHA_A, existing=None, post_status=201, argv_extra=()):
        posted = []

        def fake_http(method, url, token=None, body=None, extra_headers=None, timeout=60):
            if method == "GET" and url.endswith("/pulls/7"):
                return 200, {"head": {"sha": live}}, {}
            if method == "GET" and "/pulls/7/reviews" in url:
                return 200, existing or [], {}
            if method == "GET" and "/pulls/7/files" in url:
                return 200, [{"filename": "src/a.py", "patch": self.PATCH}], {}
            if method == "POST" and url.endswith("/pulls/7/reviews"):
                posted.append(body)
                if post_status == 422:
                    return 422, {"message": "Unprocessable"}, {}
                return 201, {"id": 1, "state": "COMMENTED", "html_url": "u"}, {}
            raise AssertionError(f"unexpected call {method} {url}")

        argv = ["--report", self.write_report(tmp, rep), "--schema", SCHEMA_PATH, "--repo", "o/r", "--pr", "7",
                "--expected-head", SHA_A, "--reviewer-identity", IDENTITY, *argv_extra]
        with mock.patch.object(publish, "http_json", fake_http), mock.patch.object(common, "http_json", fake_http), \
                mock.patch.dict(os.environ, {"GH_TOKEN": "t"}), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = publish.main(argv)
        return code, posted

    def test_happy_path_posts_one_comment_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, posted = self.run_main(tmp, report())
        self.assertEqual(code, publish.EXIT_OK)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["event"], "COMMENT")
        self.assertEqual(posted[0]["commit_id"], SHA_A)

    def test_stale_head_posts_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, posted = self.run_main(tmp, report(), live=SHA_B)
        self.assertEqual((code, posted), (publish.EXIT_STALE, []))

    def test_error_report_posts_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, posted = self.run_main(tmp, report("error"))
        self.assertEqual((code, posted), (publish.EXIT_ERROR_REPORT, []))

    def test_rejects_wrong_repo_head_identity_engine(self):
        for bad in (report(repository="x/y"), report(head_sha=SHA_B), report(reviewer_identity="other[bot]"),
                    report(engine="copilot"), report(extra="field")):
            with tempfile.TemporaryDirectory() as tmp:
                code, posted = self.run_main(tmp, bad)
            self.assertEqual((code, posted), (publish.EXIT_INVALID, []), bad)

    def test_idempotent_when_head_already_reviewed(self):
        existing = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "COMMENTED",
                     "body": "## AWF independent review\nawf-review-status: findings"}]
        with tempfile.TemporaryDirectory() as tmp:
            code, posted = self.run_main(tmp, report(), existing=existing)
        self.assertEqual((code, posted), (publish.EXIT_OK, []))

    def test_github_rejection_fails_closed_without_second_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, posted = self.run_main(tmp, report(), post_status=422)
        self.assertEqual(code, publish.EXIT_INVALID)
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["comments"], [])

    def test_policy_identity_cross_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            pol = os.path.join(tmp, "project.yaml")
            with open(pol, "w", encoding="utf-8") as handle:
                handle.write(POLICY_TEXT.replace(IDENTITY, "someone-else[bot]"))
            code, posted = self.run_main(tmp, report(), argv_extra=("--policy", pol))
        self.assertEqual((code, posted), (publish.EXIT_INVALID, []))

    def test_post_review_refuses_non_comment_event(self):
        with self.assertRaises(common.AdapterError):
            publish.post_review("https://api", "o/r", 7, "t", {"event": "APPROVE", "commit_id": SHA_A, "body": "", "comments": []})


class GateTests(unittest.TestCase):
    """Exercise the inline gate script from the workflow file against fake API responses."""

    @classmethod
    def setUpClass(cls):
        workflow = find_upwards(".github/workflows/awf-review-gate.yml")
        with open(workflow, encoding="utf-8") as handle:
            text = handle.read()
        match = re.search(r"python3 - <<'AWF_GATE'\n(.*?)\n\s*AWF_GATE\n", text, re.S)
        cls.script = textwrap.dedent(match.group(1))

    def run_gate(self, policy_text, reviews, head=SHA_A, policy_status=200):
        class FakeResponse:
            def __init__(self, status, payload):
                self.status, self._payload, self.headers = status, payload, {}

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=30):
            url = req.full_url
            if "/contents/" in url:
                if policy_status != 200:
                    raise urllib.error.HTTPError(url, policy_status, "nf", {}, io.BytesIO(b'{"message":"Not Found"}'))
                import base64
                return FakeResponse(200, {"encoding": "base64", "content": base64.b64encode(policy_text.encode()).decode()})
            if url.endswith("/pulls/7"):
                return FakeResponse(200, {"head": {"sha": head}})
            if "/pulls/7/reviews" in url:
                return FakeResponse(200, reviews)
            raise AssertionError(url)

        env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "PR_NUMBER": "7", "BASE_REF": "main",
               "POLICY_PATH": ".agentic/project.yaml", "COPILOT_IDENTITY": "copilot-pull-request-reviewer[bot]"}
        out = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen), mock.patch.dict(os.environ, env), redirect_stdout(out):
            try:
                exec(compile(self.script, "gate", "exec"), {"__name__": "gate"})
                return 0, out.getvalue()
            except SystemExit as err:
                return int(err.code or 0), out.getvalue()

    def test_claude_requires_identity_marker_and_current_head(self):
        good = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "COMMENTED", "body": "awf-review-status: no_findings"}]
        self.assertEqual(self.run_gate(POLICY_TEXT, good)[0], 0)
        stale = [{"user": {"login": IDENTITY}, "commit_id": SHA_B, "state": "COMMENTED", "body": "awf-review-status: findings"}]
        self.assertEqual(self.run_gate(POLICY_TEXT, stale)[0], 1)
        no_marker = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "COMMENTED", "body": "hello"}]
        self.assertEqual(self.run_gate(POLICY_TEXT, no_marker)[0], 1)
        dismissed = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "DISMISSED", "body": "awf-review-status: findings"}]
        self.assertEqual(self.run_gate(POLICY_TEXT, dismissed)[0], 1)
        wrong_identity = [{"user": {"login": "copilot-pull-request-reviewer[bot]"}, "commit_id": SHA_A, "state": "COMMENTED",
                           "body": "awf-review-status: findings"}]
        self.assertEqual(self.run_gate(POLICY_TEXT, wrong_identity)[0], 1, "optional engine must not satisfy the gate")

    def test_copilot_engine_and_missing_policy_fails_closed(self):
        copilot_policy = POLICY_TEXT.replace("required_external_engine: claude", "required_external_engine: copilot")
        native = [{"user": {"login": "copilot-pull-request-reviewer[bot]"}, "commit_id": SHA_A, "state": "COMMENTED", "body": ""}]
        self.assertEqual(self.run_gate(copilot_policy, native)[0], 0)
        code, out = self.run_gate("", native, policy_status=404)
        self.assertEqual(code, 1, "no policy file must never default to an engine")
        self.assertIn("fail closed", out)

    def test_every_qualification_flag_must_be_true(self):
        extra_false = POLICY_TEXT.replace("head_binding_verified: true",
                                          "head_binding_verified: true\n    benchmark_passed: false")
        good = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "COMMENTED", "body": "awf-review-status: findings"}]
        code, out = self.run_gate(extra_false, good)
        self.assertEqual(code, 1)
        self.assertIn("benchmark_passed", out)
        no_baseline = POLICY_TEXT.replace("    instruction_paths_protected: true\n", "")
        code, out = self.run_gate(no_baseline, good)
        self.assertEqual(code, 1)
        self.assertIn("baseline", out)

    def test_unqualified_engine_fails_closed(self):
        unqualified = POLICY_TEXT.replace("head_binding_verified: true", "head_binding_verified: false")
        good = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "COMMENTED", "body": "awf-review-status: findings"}]
        code, out = self.run_gate(unqualified, good)
        self.assertEqual(code, 1)
        self.assertIn("not qualified", out)

    def test_placeholder_identity_fails_closed(self):
        bad = POLICY_TEXT.replace(f'claude: "{IDENTITY}"', 'claude: "__AWF_CLAUDE_REVIEWER_LOGIN__"')
        good = [{"user": {"login": IDENTITY}, "commit_id": SHA_A, "state": "COMMENTED", "body": "awf-review-status: findings"}]
        self.assertEqual(self.run_gate(bad, good)[0], 1)


class VerifyRecordTests(unittest.TestCase):
    def test_record_requires_every_permission_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, ok in (("model-negative", True), ("publisher-negative", True), ("app-permissions", False),
                             ("unit", True), ("hashes", True)):
                path = os.path.join(tmp, name + ".json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"step": name, "pass": ok}, handle)
                paths.append(path)
            out = os.path.join(tmp, "demo.json")
            with redirect_stdout(io.StringIO()):
                code = verify.main(["record", "--inputs", *paths, "--out", out])
            with open(out, encoding="utf-8") as handle:
                record = json.load(handle)
        self.assertEqual(code, 1)
        self.assertFalse(record["suggested_qualification"]["effective_permissions_verified"])
        self.assertFalse(record["suggested_qualification"]["head_binding_verified"])

    def test_jwt_shape_with_generated_key(self):
        import subprocess
        try:
            key = subprocess.run(["openssl", "genrsa", "2048"], capture_output=True, check=True).stdout.decode()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("openssl not available")
        token = verify.make_app_jwt("12345", key)
        header, payload, signature = token.split(".")
        self.assertTrue(header and payload and signature)
        import base64
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.assertEqual(decoded["iss"], "12345")


if __name__ == "__main__":
    unittest.main()
