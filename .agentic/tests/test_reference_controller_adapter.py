import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import fingerprint, sha256
from agentic.continuous_controller import (
    ContinuousControllerStore, production_controller_cycle,
    production_jira_lifecycle, production_merge_observed,
)


NOW = "2026-10-09T08:00:00Z"


def _ticket(path="tracked.txt"):
    return {"ticket": "AWF-32", "priority": 1, "disposition": "ELIGIBLE",
            "actor": "controller", "reason": "eligible", "next_action": "continue",
            "resume_trigger": "completion", "paths": [path],
            "dependencies_satisfied": True, "budget_available": True, "cap_available": True,
            "review_independent": True, "exact_tuple": "base:a/head:b/tree:c/contract:d/review:e",
            "activity": "implement", "verification_gate": "PENDING",
            "reviewer_completion": {"required": 1, "completed": 0, "acceptable": 0,
                                     "failed": 0, "stale": 0, "outstanding": 1},
            "open_findings": 0, "jira_status": "Ready"}


class FakeClock:
    def __init__(self, value=NOW):
        self.value = value
        self.ticks = 0

    def now(self):
        return self.value

    def monotonic(self):
        self.ticks += 1
        return self.ticks


class AdvancingClock(FakeClock):
    def now(self):
        value = self.value
        self.value = "2026-10-09T08:00:01Z" if value == NOW else NOW
        return value


class FakeRunner:
    def __init__(self, *, inventory=None, partial=False, bad_repo=False, exit_code=0, error=None):
        self.inventory = inventory or [_ticket()]
        self.partial = partial
        self.bad_repo = bad_repo
        self.exit_code = exit_code
        self.error = error
        self.calls = []
        self.launches = []
        self.prompts = []

    def run(self, argv, *, cwd, env, timeout, max_bytes):
        self.calls.append((list(argv), Path(cwd), dict(env)))
        if self.error:
            raise RuntimeError(self.error)
        if self.exit_code:
            return {"returncode": self.exit_code, "stdout": "{}"}
        path = argv[-1]
        if "issues?" in path:
            if self.partial:
                return {"items": self.inventory, "complete": False, "next_page": 2}
            return {"items": self.inventory, "complete": True, "next_page": None}
        if path == "repos/example/project":
            return {"id": 999 if self.bad_repo else 101, "full_name": "example/project"}
        if path == "user":
            return {"id": 1001, "login": "smoke-user"}
        if path == "hosts":
            return {"hosts": {"github.com": [{"active": True, "profile": "smoke"}]}}
        if "rulesets" in path:
            return [[{"id": 1, "enforcement": "active",
                       "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}}}]]
        if "/collaborators/" in path:
            return {"permission": "push"}
        raise AssertionError("unexpected gh path: " + path)

    def launch(self, argv, *, cwd, env, timeout, stdin, handoff_path, terminal_proof_path,
               launch_nonce):
        # The production contract requires the runner to persist the launch
        # handoff before returning.  This fake models that boundary so restart
        # tests cannot accidentally accept a PREPARED record without a PID.
        self.launches.append((list(argv), Path(cwd), dict(env)))
        self.prompts.append(stdin)
        Path(handoff_path).write_text(json.dumps({"pid": 1234, "launch_nonce": launch_nonce,
                                                   "status": "LAUNCHED"}), encoding="utf-8")
        return {"pid": 1234, "launch_nonce": launch_nonce,
                "handoff_path": str(handoff_path),
                "terminal_proof_path": str(terminal_proof_path), "status": "LAUNCHED"}

    def observe(self, record, *, timeout):
        return {"status": "COMPLETED", "returncode": 0, "terminal": True}


class FakeHttp:
    def __init__(self, *, mismatch=False):
        self.calls = []
        self.issue_reads = 0
        self.mismatch = mismatch

    def request(self, method, url, *, headers, body, timeout, max_bytes):
        self.calls.append((method, url, dict(headers), body))
        if self.mismatch:
            return 200, json.dumps({"accountId": "foreign"})
        if url.endswith("/rest/api/3/myself"):
            return 200, json.dumps({"accountId": "controller"})
        if url.endswith("/_edge/tenant_info"):
            return 200, json.dumps({"cloudId": "cloud-1", "siteUrl": "https://jira.example.invalid"})
        if "/rest/api/3/project/EX" in url:
            return 200, json.dumps({"id": "project-1", "key": "EX"})
        if "/rest/api/3/search?" in url:
            if "startAt=0" in url:
                issue = {"id": "100", "fields": {"issuetype": {"name": "Task"},
                    "status": {"statusCategory": {"key": "done"}}}}
                return 200, json.dumps({"issues": [issue], "total": 2})
            issue = {"id": "101", "fields": {"issuetype": {"name": "Task"},
                "status": {"statusCategory": {"key": "new"}}}}
            return 200, json.dumps({"issues": [issue], "total": 2})
        if "/rest/api/3/issue/" in url and method == "GET":
            self.issue_reads += 1
            if "expand=changelog" in url:
                status = "Done" if "/issue/100?" in url else "In Progress"
                return 200, json.dumps({"id": "100", "fields": {"status": {"id": status}},
                    "changelog": {"histories": [{"created": NOW,
                        "author": {"accountId": "controller"},
                        "items": [{"field": "status", "to": status, "toString": status}]}]}})
            status = "Ready" if self.issue_reads == 1 else "In Progress"
            return 200, json.dumps({"id": "100", "fields": {"status": {"id": status}}})
        if method == "POST" and "/transitions" in url:
            self.transition_bodies = getattr(self, "transition_bodies", [])
            self.transition_bodies.append(json.loads(body))
            self.asserted_transition_id = self.transition_bodies[-1]["transition"]["id"]
            return 204, ""
        raise AssertionError("unexpected Jira request: " + method + " " + url)


class ForeignActorHttp(FakeHttp):
    def request(self, method, url, **kwargs):
        status, body = super().request(method, url, **kwargs)
        if "expand=changelog" in url:
            value = json.loads(body)
            value["changelog"]["histories"][0]["author"]["accountId"] = "foreign-actor"
            body = json.dumps(value)
        return status, body


class AdvancingPageHttp(FakeHttp):
    def request(self, method, url, **kwargs):
        status, body = super().request(method, url, **kwargs)
        if "/rest/api/3/search?" in url:
            value = json.loads(body)
            value["total"] = 2 if "startAt=0" in url else 3
            body = json.dumps(value)
        return status, body


def _base_config(root):
    codex = root / "codex.exe"
    gh = root / "gh.exe"
    codex.write_bytes(b"codex fixture")
    gh.write_bytes(b"gh fixture")
    worktree = root / "worktree"
    worktree.mkdir()
    return {
        "codex": {"executable": {"path": str(codex), "sha256": sha256(codex.read_bytes())},
                  "roles": {"writer": {"model": "writer-model", "reasoning_effort": "high", "sandbox": "workspace-write"},
                             "critic": {"model": "critic-model", "reasoning_effort": "medium", "sandbox": "read-only"}},
                  "worktree_root": str(worktree), "run_record_directory": str(root / "runs"),
                  "timeout_seconds": 20},
        "github": {"host": "https://github.com", "repository": "example/project", "repository_id": 101,
                   "project_id": "project-1", "scope_sha256": "a" * 64, "base_branch": "main",
                   "branch_pattern": "codex/{ticket}-{slug}", "expected_actor_id": 1001,
                   "auth_profile": "smoke",
                   "executable": {"path": str(gh), "sha256": sha256(gh.read_bytes())},
                   "page_size": 100},
        "jira": {"enabled": True, "cloud_id": "cloud-1", "site": "https://jira.example.invalid",
                 "provider_project_id": "project-1", "project_key": "EX", "controller_actor_id": "controller",
                 "token_env": "AWF_JIRA_TOKEN", "merged_status_id": "Done",
                 "merged_transition_id": "done-transition", "page_size": 1},
        "outbox": {"directory": str(root / "outbox")},
    }


def _load_real(path, config_path):
    # The production script is deliberately loaded without importing the
    # adapter as a package, matching the reviewed-module execution path.
    import importlib.util
    spec = importlib.util.spec_from_file_location("test_controller_loader", ROOT / ".agentic/scripts/continuous_controller.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._load_reviewed_adapters(path, sha256(path.read_bytes()), config_path), module


class ReferenceControllerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf32-adapter-")
        self.root = Path(self.temp.name)
        self.config = _base_config(self.root)
        self.adapter = ROOT / ".agentic/adapters/reference_controller_adapter.py"

    def tearDown(self):
        self.temp.cleanup()

    def test_loader_executes_real_module_and_wrong_pin_refuses(self):
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        adapters, loader = _load_real(self.adapter, config_path)
        self.assertEqual(set(adapters), {"observe_inventory", "dispatch_ticket", "observe_dispatch",
                                         "deliver_status", "observe_publication", "observe_provider_identity",
                                         "read_current_status", "write_transition", "read_transition",
                                         "reconcile_merged_ticket", "fetch_scope_page"})
        with self.assertRaisesRegex(ValidationError, "SHA-256 pin"):
            loader._load_reviewed_adapters(self.adapter, "0" * 64, config_path)

    def test_cycle_runs_end_to_end_through_real_adapter_and_strips_credentials(self):
        repository = self.root / "repository"
        repository.mkdir()
        (repository / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
        runner = FakeRunner(inventory=[_ticket("tracked.txt")])
        clock = FakeClock()
        cfg = {**self.config, "_runner": runner, "_clock": clock}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        inventory_binding = {"project_id": "project-1", "repository_id": "101", "scope_sha256": "a" * 64}
        head = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"], text=True).strip()
        state = ContinuousControllerStore(self.root / "state.sqlite3", ["A"], worktree_roots=[self.root / "worktree"])
        with patch.dict(os.environ, {"GH_TOKEN": "secret-gh", "GITHUB_TOKEN": "secret-github", "AWF_JIRA_TOKEN": "secret-jira"}, clear=False):
            result = production_controller_cycle(state, now=NOW, host_capacity=1,
                inventory_binding=inventory_binding, repository_root=repository,
                repository_head_sha=head, repository_tree_sha=tree,
                publication_config=cfg, **{name: adapters[name] for name in
                    ("observe_inventory", "dispatch_ticket", "observe_dispatch", "deliver_status", "observe_publication")})
        self.assertEqual(result["status_delivery"]["status"], "DELIVERED")
        self.assertEqual(len(result["dispatch_receipts"]), 1, result)
        self.assertTrue(runner.launches)
        child_env = runner.launches[0][2]
        self.assertNotIn("GH_TOKEN", child_env)
        self.assertNotIn("GITHUB_TOKEN", child_env)
        self.assertNotIn("AWF_JIRA_TOKEN", child_env)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", runner.launches[0][0])
        self.assertIn("Ticket: AWF-32", runner.prompts[0])
        outbox = self.root / "outbox" / "controller-status.jsonl"
        self.assertEqual(len(outbox.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(json.loads(outbox.read_text(encoding="utf-8").splitlines()[0])["delivery_id"],
                         result["status_delivery"]["delivery_id"])

    def test_inventory_uses_controller_time_and_critic_route_is_reachable(self):
        runner = FakeRunner()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": AdvancingClock()})
        observation = adapters["observe_inventory"](NOW)
        self.assertEqual(observation["observed_at"], NOW)
        payload = {"dispatch_id": "critic-1", "stream": "A", "ticket": "AWF-32",
                   "exact_tuple": "tuple", "dispatch_nonce": "critic-nonce",
                   "prepared_at": NOW, "begun_at": NOW, "paths": ["a.py"],
                   "actor": "critic", "next_action": "review", "role": "critic"}
        adapters["dispatch_ticket"](payload)
        argv = runner.launches[-1][0]
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn("critic-model", argv)

    def test_prepared_launch_crash_is_observed_without_replay(self):
        class CrashAfterLaunch(FakeRunner):
            def launch(self, argv, *, cwd, env, timeout, stdin, handoff_path, terminal_proof_path,
                       launch_nonce):
                super().launch(argv, cwd=cwd, env=env, timeout=timeout, stdin=stdin,
                               handoff_path=handoff_path, terminal_proof_path=terminal_proof_path,
                               launch_nonce=launch_nonce)
                Path(terminal_proof_path).write_text(json.dumps({
                    "pid": 1234, "launch_nonce": launch_nonce,
                    "status": "COMPLETED", "returncode": 0, "terminal": True}), encoding="utf-8")
                raise RuntimeError("simulated persistence interruption")
        runner = CrashAfterLaunch()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": FakeClock()})
        payload = {"dispatch_id": "crash-1", "stream": "A", "ticket": "AWF-32",
                   "exact_tuple": "tuple", "dispatch_nonce": "crash-nonce",
                   "prepared_at": NOW, "begun_at": NOW, "paths": ["a.py"],
                   "actor": "writer", "next_action": "continue"}
        with self.assertRaises(ValidationError):
            adapters["dispatch_ticket"](payload)
        class RestartObserver(FakeRunner):
            def observe(self, record, *, timeout):
                self.observed_record = dict(record)
                return json.loads(Path(record["terminal_proof_path"]).read_text(encoding="utf-8"))
        restarted = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": RestartObserver(), "_clock": FakeClock()})
        receipt = restarted["observe_dispatch"]({**payload, "reconcile_nonce": "r",
                                                   "reconcile_at": NOW})
        self.assertEqual(receipt["status"], "ACCEPTED")
        self.assertEqual(len(runner.launches), 1)

    def test_nonzero_or_unproven_detached_outcome_fails_closed(self):
        class BadOutcome(FakeRunner):
            def observe(self, record, *, timeout):
                return {"status": "COMPLETED", "returncode": 7, "terminal": True}
        runner = BadOutcome()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": FakeClock()})
        payload = {"dispatch_id": "bad-1", "stream": "A", "ticket": "AWF-32",
                   "exact_tuple": "tuple", "dispatch_nonce": "bad-nonce",
                   "prepared_at": NOW, "begun_at": NOW, "paths": ["a.py"],
                   "actor": "writer", "next_action": "continue"}
        adapters["dispatch_ticket"](payload)
        with self.assertRaisesRegex(ValidationError, "terminal"):
            adapters["observe_dispatch"]({**payload, "reconcile_nonce": "r",
                                            "reconcile_at": NOW})

    def test_restart_observer_reads_durable_terminal_proof_without_replay(self):
        runner = FakeRunner()
        cfg = {**self.config, "_runner": runner, "_clock": FakeClock()}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        payload = {"dispatch_id": "restart-proof", "stream": "A", "ticket": "AWF-32",
                   "exact_tuple": "tuple", "dispatch_nonce": "restart-nonce",
                   "prepared_at": NOW, "begun_at": NOW, "paths": ["a.py"],
                   "actor": "writer", "next_action": "continue"}
        adapters["dispatch_ticket"](payload)
        record_path = self.root / "runs" / "restart-proof.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        Path(record["terminal_proof_path"]).write_text(json.dumps({
            "pid": 1234, "launch_nonce": record["launch_nonce"],
            "status": "COMPLETED", "returncode": 0, "terminal": True}), encoding="utf-8")

        class RestartRunner(FakeRunner):
            def observe(self, record, *, timeout):
                proof = json.loads(Path(record["terminal_proof_path"]).read_text(encoding="utf-8"))
                self.observed_record = dict(record)
                return proof

        restarted_runner = RestartRunner()
        restarted = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": restarted_runner, "_clock": FakeClock()})
        receipt = restarted["observe_dispatch"]({**payload, "reconcile_nonce": "r2", "reconcile_at": NOW})
        self.assertEqual(receipt["status"], "ACCEPTED")
        self.assertEqual(len(runner.launches), 1)
        self.assertEqual(len(restarted_runner.launches), 0)

    def test_detached_runner_persists_nonce_identity_before_process_launch(self):
        namespace = __import__("runpy").run_path(str(self.adapter))
        runner_class = namespace["_SubprocessRunner"]
        handoff = self.root / "runner.handoff.json"
        proof = self.root / "runner.terminal.json"
        observed = []

        class StdinProbe:
            def write(self, value):
                self.value = value

            def close(self):
                pass

        class Process:
            pid = 4321
            stdin = StdinProbe()

            def wait(self, timeout=None):
                return 0

        def fake_popen(argv, **kwargs):
            observed.append(handoff.exists())
            return Process()

        with patch("subprocess.Popen", side_effect=fake_popen):
            result = runner_class().launch(["fake-bin/codex", "exec"], cwd=self.root,
                env={}, timeout=5, stdin="task", handoff_path=str(handoff),
                terminal_proof_path=str(proof), launch_nonce="nonce")
        self.assertEqual(observed, [True])
        self.assertEqual(result["pid"], 4321)

    def test_jira_old_history_entry_cannot_prove_current_transition(self):
        class OldHistory(FakeHttp):
            def request(self, method, url, **kwargs):
                status, body = super().request(method, url, **kwargs)
                if "expand=changelog" in url:
                    value = json.loads(body)
                    value["changelog"]["histories"][0]["created"] = "2020-01-01T00:00:00Z"
                    body = json.dumps(value)
                return status, body
        http = OldHistory()
        cfg = {**self.config, "_http_transport": http, "_clock": FakeClock()}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "secret-jira"}, clear=False):
            with self.assertRaisesRegex(ValidationError, "current transition|operation"):
                adapters["read_transition"]({"binding": {"issue_id": "10001"},
                    "to_status_id": "In Progress", "jira_provider": {
                        "cloud_id": "cloud-1", "project_id": "project-1", "controller_actor_id": "controller"},
                    "operation_id": "op"}, {"observed_at": NOW})

    def test_github_slurped_pages_are_bounded(self):
        class TooManyPages(FakeRunner):
            def run(self, argv, *, cwd, env, timeout, max_bytes):
                if "issues?" in argv[-1]:
                    return [[] for _ in range(21)]
                return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
        runner = TooManyPages()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": FakeClock()})
        with self.assertRaisesRegex(ValidationError, "page"):
            adapters["observe_inventory"](NOW)

    def test_github_pagination_time_bound_uses_adapter_bound(self):
        class SlowClock(FakeClock):
            def monotonic(self):
                self.ticks += 121
                return self.ticks
        runner = FakeRunner()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": SlowClock()})
        with self.assertRaisesRegex(ValidationError, "time bound"):
            adapters["observe_inventory"](NOW)

    def test_foreign_inventory_endpoint_is_rejected_before_consumption(self):
        runner = FakeRunner()
        cfg = json.loads(json.dumps(self.config))
        cfg["github"]["inventory_endpoint"] = "repos/foreign/repository/issues?state=open"
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**cfg, "_runner": runner, "_clock": FakeClock()})
        with self.assertRaisesRegex(ValidationError, "inventory endpoint"):
            adapters["observe_inventory"](NOW)

    def test_doc_example_defines_all_runtime_values_and_no_raw_placeholders(self):
        doc = (ROOT / ".agentic/docs/34-CONTINUOUS-CONTROLLER.md").read_text(encoding="utf-8")
        section = doc[doc.index("## Reference adapter (AWF-32)"):]
        for name in ("$NOW", "$HEAD", "$TREE", "$ADAPTER_PIN", "$CONTROLLER_ACTOR", "$TRANSITION_ID"):
            self.assertRegex(section, r"(?m)^\s*\$" + name[1:] + r"\s*=")
        self.assertNotIn("<STATE_DIR>", section)
        self.assertNotIn("<WORKTREE_ROOT>", section)
        self.assertNotIn("--producer-id CONTROLLER_ACTOR", section)
        self.assertNotIn("--transition-id TRANSITION_ID", section)

    def test_github_pagination_requires_provider_evidence_and_binds_page_size(self):
        class Paged(FakeRunner):
            def run(self, argv, *, cwd, env, timeout, max_bytes):
                self.calls.append((list(argv), Path(cwd), dict(env)))
                path = argv[-1]
                if "issues?" in path:
                    return [[self.inventory[0]], []]
                return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
        runner = Paged()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": FakeClock()})
        adapters["observe_inventory"](NOW)
        issue_call = next(call[0] for call in runner.calls if "issues?" in call[0][-1])
        self.assertIn("--paginate", issue_call)
        self.assertIn("per_page=100", issue_call[-1])

    def test_default_branch_only_ruleset_does_not_authorize_a_feature_ref(self):
        class DefaultOnly(FakeRunner):
            def run(self, argv, *, cwd, env, timeout, max_bytes):
                if "rulesets" in argv[-1]:
                    return [[{"id": 1, "enforcement": "active",
                               "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}}]]
                return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
        runner = DefaultOnly()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": FakeClock()})
        observation = adapters["observe_publication"]({"ticket": "AWF-32"})
        self.assertEqual(observation["rules"]["state"], "UNOBSERVED")

    def test_generated_adapter_example_is_idempotent_and_complete(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            generated = __import__("runpy").run_path(str(ROOT / "scripts/generate_examples.py"),
                                                       run_name="awf32_generator")
        finally:
            sys.path.pop(0)
        expected = generated["controller_adapter_config"]()
        stored = json.loads((ROOT / ".agentic/examples/reference-controller-adapter.json").read_text())
        self.assertEqual(stored, expected)
        self.assertIn("expected_actor_id", stored["github"])
        self.assertIn("auth_profile", stored["github"])
        self.assertIn("merged_transition_id", stored["jira"])

    def test_jira_lifecycle_is_read_before_one_write_and_readback(self):
        http = FakeHttp()
        clock = FakeClock()
        cfg = {**self.config, "_http_transport": http, "_clock": clock}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        project_config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        project_config["jira"].update(cloud_id="cloud-1", site="https://jira.example.invalid",
                                       provider_project_id="project-1", project_key="EX",
                                       controller_actor_id="controller")
        contract = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())["contract"]
        binding = {"issue_id": "10001"}
        store = ContinuousControllerStore(self.root / "jira.sqlite3", ["A"], worktree_roots=[self.root / "worktree"])
        with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "secret-jira"}, clear=False):
            result = production_jira_lifecycle(store, config=project_config, contract=contract,
                event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
                binding=binding, issue_type="LEAF", state="DISPATCHED", producer_id="controller",
                run_id="00000000-0000-0000-0000-000000000001", now=NOW, evidence=[], transition_id="3",
                read_current_status=adapters["read_current_status"], write_transition=adapters["write_transition"],
                read_transition=adapters["read_transition"], observe_provider_identity=adapters["observe_provider_identity"])
        self.assertTrue(result["planned"])
        self.assertEqual(result["record"]["status"], "SUCCEEDED")
        self.assertEqual(http.asserted_transition_id, "3")
        methods = [call[0] for call in http.calls]
        self.assertEqual(methods.count("POST"), 1)
        self.assertLess(methods.index("GET"), methods.index("POST"))
        self.assertNotIn("secret-jira", repr(result))

    def test_jira_readback_uses_provider_history_actor(self):
        http = ForeignActorHttp()
        cfg = {**self.config, "_http_transport": http, "_clock": FakeClock()}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        project_config = json.loads((ROOT / ".agentic/examples/PROJECT_CONFIG.yaml").read_text())
        project_config["jira"].update(cloud_id="cloud-1", site="https://jira.example.invalid",
                                       provider_project_id="project-1", project_key="EX",
                                       controller_actor_id="controller")
        contract = json.loads((ROOT / ".agentic/examples/evidence-bundle.json").read_text())["contract"]
        store = ContinuousControllerStore(self.root / "foreign.sqlite3", ["A"], worktree_roots=[self.root / "worktree"])
        with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "secret-jira"}, clear=False):
            result = production_jira_lifecycle(store, config=project_config, contract=contract,
                event="WORKER_STARTED", facts={"run_registered": True, "worktree_verified": True},
                binding={"issue_id": "10001"}, issue_type="LEAF", state="DISPATCHED",
                producer_id="controller", run_id="00000000-0000-0000-0000-000000000001", now=NOW,
                evidence=[], transition_id="3", read_current_status=adapters["read_current_status"],
                write_transition=adapters["write_transition"], read_transition=adapters["read_transition"],
                observe_provider_identity=adapters["observe_provider_identity"])
        self.assertNotEqual(result.get("record", {}).get("status"), "SUCCEEDED")
        self.assertIn("writes_stopped", result)

    def test_merge_observed_reconciles_then_pages(self):
        http = FakeHttp()
        clock = FakeClock()
        cfg = {**self.config, "_http_transport": http, "_clock": clock}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        progress = {"jira_enabled": True, "merged_ticket": "100", "scope": "project = EX",
                    "observed_at": NOW, "jira_binding": {"cloud_id": "cloud-1", "project_id": "project-1", "actor_id": "controller"},
                    "max_pages": 5, "max_items": 10}
        with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "secret-jira"}, clear=False):
            result = production_merge_observed(lifecycle_state="MERGING",
                lifecycle_facts={"merge_confirmed": True, "candidate_matched": True},
                jira_progress={**progress, "reconcile_merged_ticket": adapters["reconcile_merged_ticket"],
                               "fetch_scope_page": adapters["fetch_scope_page"]})
        self.assertEqual(result["state"], "MERGED")
        self.assertEqual(result["jira_progress"]["jira_state"], "COUNTED")
        self.assertEqual(result["jira_progress"]["closed"], 1)

    def test_jira_advancing_page_time_is_not_counted(self):
        http = AdvancingPageHttp()
        cfg = {**self.config, "_http_transport": http, "_clock": FakeClock()}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        progress = {"jira_enabled": True, "merged_ticket": "100", "scope": "project = EX",
                    "observed_at": NOW,
                    "jira_binding": {"cloud_id": "cloud-1", "project_id": "project-1", "actor_id": "controller"},
                    "max_pages": 5, "max_items": 10}
        with patch.dict(os.environ, {"AWF_JIRA_TOKEN": "secret-jira"}, clear=False):
            result = production_merge_observed(lifecycle_state="MERGING",
                lifecycle_facts={"merge_confirmed": True, "candidate_matched": True},
                jira_progress={**progress, "reconcile_merged_ticket": adapters["reconcile_merged_ticket"],
                               "fetch_scope_page": adapters["fetch_scope_page"]})
        self.assertEqual(result["jira_progress"]["jira_state"], "RECONCILED")

    def test_interrupted_dispatch_is_observed_without_relaunch(self):
        runner = FakeRunner()
        cfg = {**self.config, "_runner": runner, "_clock": FakeClock()}
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"](cfg)
        payload = {"dispatch_id": "d1", "stream": "A", "ticket": "AWF-32", "exact_tuple": "tuple",
                   "dispatch_nonce": "nonce", "prepared_at": NOW, "begun_at": NOW,
                   "paths": ["a.py"], "actor": "writer", "next_action": "continue"}
        adapters["dispatch_ticket"](payload)
        receipt = adapters["observe_dispatch"]({**payload, "reconcile_nonce": "reconcile",
                                                  "reconcile_at": NOW})
        self.assertEqual(receipt["status"], "ACCEPTED")
        self.assertEqual(len(runner.launches), 1)

    def test_fail_closed_config_hash_timeout_partial_page_identity_and_secret(self):
        with self.assertRaises(ValidationError):
            __import__("runpy").run_path(str(self.adapter))["build_adapters"]({})
        bad = json.loads(json.dumps(self.config))
        bad["codex"]["roles"]["writer"]["sandbox"] = "danger-full-access"
        with self.assertRaisesRegex(ValidationError, "sandbox"):
            __import__("runpy").run_path(str(self.adapter))["build_adapters"](bad)
        runner = FakeRunner(partial=True)
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"]({**self.config, "_runner": runner, "_clock": FakeClock()})
        with self.assertRaises(ValidationError):
            adapters["observe_inventory"]()
        bad_repo = FakeRunner(bad_repo=True)
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"]({**self.config, "_runner": bad_repo, "_clock": FakeClock()})
        with self.assertRaisesRegex(ValidationError, "identity"):
            adapters["observe_publication"]({"ticket": "AWF-32"})
        self.config["github"]["executable"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "hash"):
            __import__("runpy").run_path(str(self.adapter))["build_adapters"]({**self.config, "_runner": FakeRunner()})["observe_inventory"]()

    def test_nonzero_timeout_and_missing_token_have_sanitized_errors(self):
        runner = FakeRunner(exit_code=2)
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"]({**self.config, "_runner": runner, "_clock": FakeClock()})
        with self.assertRaises(ValidationError):
            adapters["observe_inventory"]()
        runner = FakeRunner(error="TOP_SECRET_VALUE")
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"]({**self.config, "_runner": runner, "_clock": FakeClock()})
        with self.assertRaises(ValidationError) as raised:
            adapters["observe_inventory"]()
        self.assertNotIn("TOP_SECRET_VALUE", str(raised.exception))
        http = FakeHttp()
        adapters = __import__("runpy").run_path(str(self.adapter))["build_adapters"]({**self.config, "_http_transport": http, "_clock": FakeClock()})
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValidationError, "credential"):
            adapters["observe_provider_identity"]()


if __name__ == "__main__":
    unittest.main()
