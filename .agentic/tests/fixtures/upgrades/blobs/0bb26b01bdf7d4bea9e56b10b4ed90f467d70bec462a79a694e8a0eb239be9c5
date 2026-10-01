"""Offline workflow admission tests; never qualify live server environments."""
from pathlib import Path
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
import tempfile
import textwrap
from unittest import mock
import urllib.parse
import urllib.request
import re
import sys
import unittest

ADAPTER = Path(__file__).resolve().parents[1]
COMPONENT = ADAPTER.parent
sys.path.insert(0, str(ADAPTER))

import awf_review_common as common
import policy
import run_model
from tests import test_claude_adapter as adapter_tests


def job(text, name):
    match = re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  [a-z][a-z_-]*:\n|\Z)", text)
    if match is None:
        raise AssertionError(f"Missing job {name}")
    return match.group(1)


def admitted(block, context):
    """Interpret only the equality/AND subset in the actual workflow guards."""
    match = re.search(r"(?m)^    if: >-\n((?:      .+\n)+)", block)
    if match is None:
        raise AssertionError("Expected an explicit folded admission guard")

    def value(token):
        token = token.strip()
        if token in ("false", "true"):
            return token == "true"
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        prefix = "format('refs/heads/{0}', "
        if token.startswith(prefix) and token.endswith(")"):
            return "refs/heads/" + context[token[len(prefix):-1]]
        return context[token]

    for term in match.group(1).split("&&"):
        operands = term.strip().split(" == ")
        if len(operands) != 2:
            raise AssertionError(f"Unsupported guard expression: {term}")
        if value(operands[0]) != value(operands[1]):
            return False
    return True


class WorkflowBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (COMPONENT / "workflows/awf-review-claude.yml").read_text(encoding="utf-8")
        cls.gate = (COMPONENT / "workflows/awf-review-gate.yml").read_text(encoding="utf-8")
        cls.example = (COMPONENT / "project.example.yaml").read_text(encoding="utf-8")

    def test_jobs_have_separate_environments_and_no_cross_job_secret(self):
        model, publisher = job(self.workflow, "model"), job(self.workflow, "publish")
        for block, environment, secret in [
            (model, "awf-review-model", "ANTHROPIC_API_KEY"),
            (publisher, "awf-review-publisher", "AWF_REVIEWER_APP_PRIVATE_KEY"),
        ]:
            self.assertIn(f"    environment: {environment}\n", block)
            self.assertEqual(set(re.findall(r"secrets\.([A-Z_]+)", block)), {secret})
        self.assertIn("permissions: {}", self.workflow.split("jobs:")[0])
        self.assertIn("      contents: read\n      pull-requests: read", model)
        self.assertNotIn("secrets.", self.gate)

    def test_guards_reject_untrusted_targets_refs_forks_and_disabled_runs(self):
        context = {
            "vars.AWF_ENABLE_CLAUDE_REVIEW": "true",
            "github.event.pull_request.draft": False,
            "github.event.pull_request.head.repo.full_name": "owner/project",
            "github.repository": "owner/project",
            "github.event.pull_request.base.ref": "main",
            "github.event.repository.default_branch": "main",
            "github.ref": "refs/heads/main", "needs.model.result": "success",
        }
        model, publisher = job(self.workflow, "model"), job(self.workflow, "publish")
        self.assertTrue(admitted(model, context))
        self.assertTrue(admitted(publisher, context))
        for key, value in [
            ("vars.AWF_ENABLE_CLAUDE_REVIEW", "false"),
            ("github.event.pull_request.draft", True),
            ("github.event.pull_request.head.repo.full_name", "fork/project"),
            ("github.event.pull_request.base.ref", "agent/unprotected"),
            ("github.ref", "refs/heads/agent/unprotected"),
        ]:
            with self.subTest(key=key):
                self.assertFalse(admitted(model, {**context, key: value}))
        for key, value in [("needs.model.result", "failure"),
                           ("github.event.pull_request.base.ref", "agent/unprotected"),
                           ("github.ref", "refs/heads/agent/unprotected")]:
            self.assertFalse(admitted(publisher, {**context, key: value}))

    def test_trusted_code_uses_workflow_commit_and_candidate_is_data_only(self):
        refs = re.findall(r"(?m)^          ref: (.+)$", self.workflow)
        self.assertEqual(refs, ["${{ github.sha }}", "${{ github.event.pull_request.head.sha }}", "${{ github.sha }}"])
        self.assertEqual(self.workflow.count("persist-credentials: false"), 3)
        self.assertNotRegex(self.workflow, r"(?m)^\s*working-directory:\s*pr")
        self.assertNotIn("pull_request.base.ref }}", self.workflow)
        self.assertIn("github.event.pull_request.base.ref == github.event.repository.default_branch", job(self.gate, "review"))

    def test_timeout_budget_fits_job_and_runtime_supported_bound(self):
        block = job(self.workflow, "model")
        timeout = int(re.search(r"--timeout (\d+)", block).group(1))
        job_seconds = 60 * int(re.search(r"timeout-minutes: (\d+)", block).group(1))
        self.assertEqual(timeout, run_model.MAX_MODEL_TIMEOUT_SECONDS)
        self.assertEqual(common.MODEL_REQUEST_ATTEMPTS, 1)
        self.assertEqual(common.MODEL_RETRY_DELAY_SECONDS, 0)
        self.assertEqual(int(re.search(r"--max-tokens (\d+)", block).group(1)), 8000)
        self.assertLessEqual(2 * (timeout + 2) + 120, job_seconds)
        self.assertIn("--max-diff-bytes 400000", block)

    def test_policy_example_is_complete_but_unqualified(self):
        document = common.load_yaml_lite(self.example)
        self.assertEqual(policy.resolve(document, "copilot", True)["reviewer_identity"], policy.COPILOT_IDENTITY)
        flags = document["review"]["qualification"]
        self.assertIn("environment_secret_boundaries_verified", flags)
        self.assertTrue(flags and all(v is False for v in flags.values()))
        with self.assertRaises(common.AdapterError):
            policy.resolve(document, "claude", False)
        substituted = self.example.replace("__AWF_REVIEWER_BOT_LOGIN__", "fixture-reviewer[bot]").replace(
            "__AWF_PINNED_CLAUDE_MODEL__", "claude-fixture-model")
        self.assertEqual(policy.resolve(common.load_yaml_lite(substituted), "claude", False)["model"], "claude-fixture-model")
        adapter_tests.GateTests.setUpClass()
        gate = adapter_tests.GateTests()
        reviews = [{"user": {"login": policy.COPILOT_IDENTITY}, "commit_id": adapter_tests.SHA_A,
                    "state": "COMMENTED", "body": ""}]
        code, output = gate.run_gate(self.example, reviews)
        self.assertEqual(code, 1)
        self.assertIn("not qualified", output)

    def test_gate_doc_reference_points_to_shipped_runbook(self):
        self.assertIn(".agentic/docs/28-EXTERNAL-REVIEW.md", self.gate)
        self.assertNotIn("docs/REVIEW_POLICY.md", self.gate)
        self.assertNotIn("docs/agent-runtime/review-policy.md", self.gate)
        self.assertTrue((COMPONENT.parent / "docs/28-EXTERNAL-REVIEW.md").is_file())

    def test_gate_has_no_candidate_context_review_trigger(self):
        triggers = self.gate.split("on:\n", 1)[1].split("permissions:", 1)[0]
        self.assertRegex(triggers, r"(?m)^  pull_request_target:")
        self.assertRegex(triggers, r"(?m)^  workflow_run:")
        self.assertIn("workflows: [AWF Claude independent review]", triggers)
        self.assertNotIn("AWF independent review gate", triggers)
        self.assertIn("      actions: write", job(self.gate, "wake"))
        self.assertNotIn("actions: write", job(self.gate, "review"))
        self.assertIn("github.event.workflow_run.pull_requests[0].number || github.event.workflow_run.id", job(self.gate, "wake"))
        self.assertNotRegex(triggers, r"(?m)^  pull_request(?:_review|_review_comment)?:")


class GateWakeBehaviorTests(unittest.TestCase):
    """Execute the shipped wake script with fake GitHub responses, never live calls."""

    @classmethod
    def setUpClass(cls):
        gate = (COMPONENT / "workflows/awf-review-gate.yml").read_text(encoding="utf-8")
        cls.script = textwrap.dedent(re.search(r"python3 - <<'AWF_WAKE'\n(.*?)\n\s*AWF_WAKE\n", gate, re.S).group(1))

    def fixture(self):
        repo = {"id": 123, "full_name": "o/r", "default_branch": "main"}
        association = {"number": 7, "id": 17, "head": {"sha": "a" * 40, "repo": {"id": 123}},
                       "base": {"ref": "main", "repo": {"id": 123}}}
        def run(identifier, workflow_id, name, path, conclusion):
            return {"id": identifier, "workflow_id": workflow_id, "name": name,
                    "path": path + "@main", "event": "pull_request_target", "head_sha": "b" * 40,
                    "repository": deepcopy(repo), "head_repository": deepcopy(repo),
                    "run_attempt": 1, "run_number": identifier, "status": "completed", "conclusion": conclusion,
                    "pull_requests": [deepcopy(association)]}
        producer_path = ".github/workflows/awf-review-claude.yml"
        gate_path = ".github/workflows/awf-review-gate.yml"
        producer = run(30, 3, "AWF Claude independent review", producer_path, "success")
        gate = run(40, 4, "AWF independent review gate", gate_path, "failure")
        pr = {"id": 17, "number": 7, "state": "open", "draft": False,
              "head": {"sha": "a" * 40, "repo": deepcopy(repo)},
              "base": {"ref": "main", "repo": deepcopy(repo)}}
        return {"repository": repo, "producer": producer, "gate": gate, "pr": pr,
                "notice": deepcopy(producer), "action": "completed",
                "producer_meta": {"id": 3, "name": producer["name"], "path": producer_path, "state": "active"},
                "gate_meta": {"id": 4, "name": gate["name"], "path": gate_path, "state": "active"}}

    def run_wake(self, case, mutate_response=None):
        calls, posts = [], []
        pr_reads = 0
        class Response:
            def __init__(self, body, status=200):
                self.status, self.body = status, json.dumps(body).encode("utf-8")
            def read(self, size=-1): return self.body[:size] if size >= 0 else self.body
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def open_request(request, timeout):
            nonlocal pr_reads
            self.assertEqual(timeout, 20)
            url = urllib.parse.urlsplit(request.full_url)
            self.assertEqual(url.scheme + "://" + url.netloc, "https://api.github.com")
            self.assertTrue(url.path.startswith("/repos/o/r"))
            path = url.path[len("/repos/o/r"):]
            calls.append((request.method, path))
            if request.method == "POST":
                posts.append(path)
                self.assertEqual(path, "/actions/runs/40/rerun-failed-jobs")
                return Response({}, 201)
            if path == "": value = case["repository"]
            elif path == "/actions/runs/30":
                producer_reads = sum(p == path for _, p in calls)
                value = case.get("second_producer", case["producer"]) if producer_reads > 1 else case["producer"]
            elif path.startswith("/actions/runs/30/attempts/"):
                self.assertEqual(path, f"/actions/runs/30/attempts/{case['producer']['run_attempt']}/jobs")
                value = case.get("producer_jobs", {"total_count": 2, "jobs": [
                    {"id": identifier, "name": name, "run_id": 30, "head_sha": case["producer"]["head_sha"],
                     "status": "completed", "conclusion": "success"}
                    for identifier, name in [(301, "awf/review-claude-model"), (302, "awf/review-claude-publish")]]})
            elif path == "/actions/runs/40":
                gate_reads = sum(p == path for _, p in calls)
                value = case.get("second_gate", case["gate"]) if gate_reads > 1 else case["gate"]
            elif path == "/actions/runs/39": value = case["older_gate"]
            elif path.startswith("/actions/runs/40/attempts/"):
                value = case.get("jobs", {"total_count": 1, "jobs": [{"name": "awf/review", "run_id": 40,
                    "status": "completed", "conclusion": case["gate"]["conclusion"]}]})
            elif path == "/actions/workflows/awf-review-claude.yml": value = case["producer_meta"]
            elif path == "/actions/workflows/awf-review-gate.yml": value = case["gate_meta"]
            elif path == "/actions/workflows/4/runs": value = {"workflow_runs": case.get("listing", [case["gate"]])}
            elif path == "/pulls/7":
                pr_reads += 1
                value = case.get("second_pr", case["pr"]) if pr_reads > 1 else case["pr"]
            elif path.startswith("/contents/"): value = {"type": "file", "sha": "c" * 40}
            elif path.startswith("/compare/"):
                value = {"status": "ahead", "base_commit": {"sha": path.split("/")[-1].split("...")[0]}}
            else: raise AssertionError(path)
            value = deepcopy(value)
            if mutate_response: value = mutate_response(path, url.query, value)
            return Response(value)
        with tempfile.TemporaryDirectory() as temp:
            event = Path(temp) / "event.json"
            event.write_text(json.dumps({"action": case["action"], "repository": case["repository"],
                                         "workflow_run": case["notice"]}), encoding="utf-8")
            env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "fake", "GITHUB_SHA": "b" * 40,
                   "GITHUB_EVENT_NAME": case.get("event_name", "workflow_run"), "GITHUB_REF": case.get("ref", "refs/heads/main"),
                   "GITHUB_EVENT_PATH": str(event)}
            out = io.StringIO()
            opener = mock.Mock()
            opener.open.side_effect = open_request
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(urllib.request, "build_opener", return_value=opener), redirect_stdout(out):
                try:
                    exec(compile(self.script, "wake", "exec"), {"__name__": "wake"})
                    code = 0
                except SystemExit as error:
                    code = int(error.code or 0)
        return code, out.getvalue(), posts, calls

    def test_valid_producer_reruns_only_current_head_gate(self):
        code, output, posts, calls = self.run_wake(self.fixture())
        self.assertEqual(code, 0, output)
        self.assertEqual(posts, ["/actions/runs/40/rerun-failed-jobs"])
        self.assertEqual(sum(path == "/pulls/7" for method, path in calls), 2)
        self.assertIn("this request is not a passing check", output)
        self.assertNotIn("/artifacts", str(calls))
        self.assertFalse(any("/check-runs" in path or "/statuses" in path for _, path in calls))

    def test_producer_jobs_must_both_succeed_on_the_verified_attempt(self):
        base = {"total_count": 2, "jobs": [
            {"id": identifier, "name": name, "run_id": 30, "head_sha": "b" * 40,
             "status": "completed", "conclusion": "success"}
            for identifier, name in [(301, "awf/review-claude-model"), (302, "awf/review-claude-publish")]]}
        for index in (0, 1):
            for key, value in [("conclusion", "skipped"), ("conclusion", "cancelled"), ("conclusion", "failure"),
                               ("conclusion", None), ("status", "in_progress"), ("run_id", 31),
                               ("head_sha", "a" * 40), ("run_attempt", 2), ("run_attempt", True), ("name", "other")]:
                with self.subTest(job=index, key=key, value=value):
                    case = self.fixture(); case["producer_jobs"] = deepcopy(base)
                    case["producer_jobs"]["jobs"][index][key] = value
                    code, output, posts, _ = self.run_wake(case)
                    self.assertEqual(code, 1, output); self.assertEqual(posts, [])
        for mode in ("missing", "duplicate", "malformed", "truncated", "same_id"):
            with self.subTest(mode=mode):
                case = self.fixture(); case["producer_jobs"] = deepcopy(base)
                value = case["producer_jobs"]
                if mode == "missing": value["jobs"].pop(); value["total_count"] = 1
                elif mode == "duplicate":
                    value["jobs"].append({**value["jobs"][0], "id": 303}); value["total_count"] = 3
                elif mode == "malformed": value["jobs"][0] = None
                elif mode == "truncated": value["total_count"] = 3
                else: value["jobs"][1]["id"] = value["jobs"][0]["id"]
                self.assertEqual(self.run_wake(case)[0], 1); self.assertEqual(self.run_wake(case)[2], [])

    def test_producer_attempt_endpoint_and_recheck_are_not_workflow_success_only(self):
        case = self.fixture(); case["producer"]["run_attempt"] = 2; case["notice"] = deepcopy(case["producer"])
        code, output, posts, calls = self.run_wake(case)
        self.assertEqual(code, 0, output)
        self.assertEqual(sum(path == "/actions/runs/30/attempts/2/jobs" for _, path in calls), 2)
        self.assertEqual(len(posts), 1)
        case["second_producer"] = deepcopy(case["producer"]); case["second_producer"]["run_attempt"] = 3
        code, output, posts, _ = self.run_wake(case)
        self.assertEqual(code, 1); self.assertEqual(posts, [])
        self.assertIn("producer changed", output)

    def test_source_execution_sha_is_distinct_from_candidate_sha(self):
        case = self.fixture()
        self.assertNotEqual(case["producer"]["head_sha"], case["pr"]["head"]["sha"])
        self.assertEqual(self.run_wake(case)[0], 0)

    def test_wrong_producer_identity_event_repository_or_association_cannot_mutate(self):
        mutations = [
            lambda c: c["producer"].update(workflow_id=999),
            lambda c: c["producer_meta"].update(path=".github/workflows/attacker.yml"),
            lambda c: c["producer"].update(path=".github/workflows/awf-review-claude.yml@agent/branch"),
            lambda c: c["producer"].update(event="pull_request"),
            lambda c: c["producer"].update(event="workflow_dispatch"),
            lambda c: c["producer"]["repository"].update(id=999),
            lambda c: c["producer"]["head_repository"].update(full_name="attacker/fork"),
            lambda c: c["producer"].update(pull_requests=[]),
            lambda c: c["producer"]["pull_requests"].append(deepcopy(c["producer"]["pull_requests"][0])),
            lambda c: c["producer"]["pull_requests"][0]["base"].update(ref="agent/branch"),
            lambda c: c["producer"]["pull_requests"][0]["head"]["repo"].update(id=999),
            lambda c: c["producer"].update(conclusion="failure"),
            lambda c: c["notice"].update(run_attempt=2),
            lambda c: c.update(event_name="pull_request_review"),
            lambda c: c.update(ref="refs/heads/agent/branch"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                case = self.fixture(); mutate(case)
                code, output, posts, _ = self.run_wake(case)
                self.assertEqual(code, 1, output)
                self.assertEqual(posts, [])
                self.assertIn("Next: coordinator", output)

    def test_wrong_gate_source_or_candidate_cannot_be_rerun(self):
        for field, value in [("workflow_id", 999), ("event", "workflow_dispatch"),
                             ("path", ".github/workflows/other.yml"), ("id", 41)]:
            with self.subTest(field=field):
                case = self.fixture(); case["gate"][field] = value
                # Preserve list identity so the fresh-run identity check is exercised.
                case["listing"] = [self.fixture()["gate"]]
                code, output, posts, _ = self.run_wake(case)
                self.assertEqual(code, 1, output); self.assertEqual(posts, [])

    def test_stale_changed_draft_closed_or_foreign_live_pr_cannot_mutate(self):
        mutations = [lambda p: p["head"].update(sha="d" * 40), lambda p: p.update(draft=True),
                     lambda p: p.update(state="closed"), lambda p: p.update(id=999),
                     lambda p: p["base"].update(ref="other"), lambda p: p["head"]["repo"].update(id=999)]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                case = self.fixture(); mutate(case["pr"])
                code, _, posts, _ = self.run_wake(case)
                self.assertEqual(code, 1); self.assertEqual(posts, [])
        case = self.fixture(); case["second_pr"] = deepcopy(case["pr"])
        case["second_pr"]["head"]["sha"] = "d" * 40
        self.assertEqual(self.run_wake(case)[2], [])
        self.assertEqual(self.run_wake(case)[0], 1)

    def test_missing_ambiguous_or_running_gate_and_attempt_limit_do_not_rerun(self):
        for mode in ("missing", "running", "attempt", "cancelled"):
            with self.subTest(mode=mode):
                case = self.fixture()
                if mode == "missing": case["listing"] = []
                elif mode == "running": case["gate"]["status"] = "in_progress"
                elif mode == "attempt": case["gate"]["run_attempt"] = 2
                else: case["gate"]["conclusion"] = "cancelled"
                code, _, posts, _ = self.run_wake(case)
                self.assertEqual(code, 1); self.assertEqual(posts, [])
        case = self.fixture(); case["gate"]["conclusion"] = "success"
        code, output, posts, _ = self.run_wake(case)
        self.assertEqual(code, 0); self.assertEqual(posts, []); self.assertIn("already succeeded", output)

    def test_draft_then_ready_selects_latest_verified_run(self):
        case = self.fixture()
        case["older_gate"] = deepcopy(case["gate"])
        case["older_gate"].update(id=39, run_number=39, conclusion="success")
        case["listing"] = [case["older_gate"], case["gate"]]
        code, output, posts, _ = self.run_wake(case)
        self.assertEqual(code, 0, output)
        self.assertEqual(posts, ["/actions/runs/40/rerun-failed-jobs"])
        case["older_gate"]["run_number"] = 40
        self.assertEqual(self.run_wake(case)[0], 1)
        self.assertEqual(self.run_wake(case)[2], [])

    def test_skipped_review_job_does_not_count_as_success(self):
        case = self.fixture(); case["gate"]["conclusion"] = "success"
        case["jobs"] = {"total_count": 1, "jobs": [{"name": "awf/review", "run_id": 40,
            "status": "completed", "conclusion": "skipped"}]}
        code, output, posts, _ = self.run_wake(case)
        self.assertEqual(code, 1); self.assertEqual(posts, [])
        self.assertIn("skipped", output)

    def test_concurrent_attempt_change_is_reconciled_without_post(self):
        case = self.fixture(); case["second_gate"] = deepcopy(case["gate"])
        case["second_gate"]["run_attempt"] = 2
        code, output, posts, _ = self.run_wake(case)
        self.assertEqual(code, 1); self.assertEqual(posts, [])
        self.assertIn("gate changed", output)

    def test_malformed_association_remains_actionable(self):
        case = self.fixture(); case["producer"]["pull_requests"] = ["bad"]
        code, output, posts, _ = self.run_wake(case)
        self.assertEqual(code, 1); self.assertEqual(posts, [])
        self.assertIn("Next: coordinator", output)

    def test_historical_source_requires_ancestry_and_current_workflow_bytes(self):
        for mode in ("valid", "unrelated", "changed"):
            with self.subTest(mode=mode):
                case = self.fixture()
                case["producer"]["head_sha"] = "d" * 40
                case["notice"] = deepcopy(case["producer"])
                def mutate(path, query, value):
                    if mode == "unrelated" and path.startswith("/compare/"): value["status"] = "diverged"
                    if mode == "changed" and path.startswith("/contents/") and query == "ref=" + "d" * 40:
                        value["sha"] = "e" * 40
                    return value
                code, _, posts, _ = self.run_wake(case, mutate)
                self.assertEqual(code, 0 if mode == "valid" else 1)
                self.assertEqual(len(posts), 1 if mode == "valid" else 0)

    def test_source_path_ref_variants_are_explicit_and_bounded(self):
        for suffix in ("", "@main", "@refs/heads/main", "@" + "b" * 40):
            case = self.fixture()
            case["producer"]["path"] = case["producer_meta"]["path"] + suffix
            case["notice"] = deepcopy(case["producer"])
            self.assertEqual(self.run_wake(case)[0], 0)


class QualificationPromptTests(unittest.TestCase):
    def test_secret_engine_requires_environment_boundary_evidence(self):
        adapter_tests.GateTests.setUpClass()
        gate = adapter_tests.GateTests()
        good = [{"user": {"login": adapter_tests.IDENTITY}, "commit_id": adapter_tests.SHA_A,
                 "state": "COMMENTED", "body": "awf-review-status: no_findings"}]
        text = adapter_tests.POLICY_TEXT
        for value in ("missing", "false"):
            changed = re.sub(r"(?m)^    environment_secret_boundaries_verified: true\n", "", text)
            if value == "false": changed += "    environment_secret_boundaries_verified: false\n"
            code, output = gate.run_gate(changed, good)
            self.assertEqual(code, 1, output)
            self.assertIn("environment_secret_boundaries_verified", output)
        record = json.loads((COMPONENT / "qualification.json").read_text(encoding="utf-8"))
        self.assertIs(record["environment_secret_boundaries_verified"], False)

    def test_copilot_ignores_only_the_inapplicable_environment_flag(self):
        adapter_tests.GateTests.setUpClass()
        gate = adapter_tests.GateTests()
        good = [{"user": {"login": policy.COPILOT_IDENTITY}, "commit_id": adapter_tests.SHA_A,
                 "state": "COMMENTED", "body": ""}]
        text = adapter_tests.POLICY_TEXT.replace("required_external_engine: claude", "required_external_engine: copilot")
        text = text.replace("environment_secret_boundaries_verified: true", "environment_secret_boundaries_verified: false")
        self.assertEqual(gate.run_gate(text, good)[0], 0)
        self.assertEqual(gate.run_gate(text.replace("    environment_secret_boundaries_verified: false\n", ""), good)[0], 0)
        for changed in [text.replace("head_binding_verified: true", "head_binding_verified: false"),
                        text.replace("effective_permissions_verified: true", "effective_permissions_verified: \"true\""),
                        text + "    extra_required_check: false\n",
                        text.replace("    instruction_paths_protected: true\n", "")]:
            with self.subTest(policy=changed):
                self.assertEqual(gate.run_gate(changed, good)[0], 1)

    def test_prompt_manifest_and_amendment_vocabulary_matches_schemas(self):
        prompts = COMPONENT.parent / "prompts"
        critic = (prompts / "critic.md").read_text(encoding="utf-8")
        controller = (prompts / "controller.md").read_text(encoding="utf-8")
        amendment = (prompts / "amendment.md").read_text(encoding="utf-8")
        self.assertIn("file_manifest_sha256", critic)
        self.assertIn("coverage.file_manifest_sha256", controller)
        self.assertIn("`amendment-result`", amendment)
        self.assertNotIn("`worker-result`", amendment)
        schema = json.loads((COMPONENT.parent / "schemas/critic-review.schema.json").read_text(encoding="utf-8"))
        self.assertIn("file_manifest_sha256", schema["properties"]["coverage"]["required"])
        for path in prompts.glob("*.md"):
            if path.name in ("controller.md", "critic.md", "amendment.md", "worker.md", "specialist-reviewer.md"):
                text = path.read_text(encoding="utf-8")
                self.assertLessEqual(len(text.split()), 350, path.name)
                self.assertIn("state, next action, owner, resume trigger, exact user action", text)


if __name__ == "__main__":
    unittest.main()
