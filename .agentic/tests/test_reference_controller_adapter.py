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


class FakeRunner:
    def __init__(self, *, inventory=None, partial=False, bad_repo=False, exit_code=0, error=None):
        self.inventory = inventory or [_ticket()]
        self.partial = partial
        self.bad_repo = bad_repo
        self.exit_code = exit_code
        self.error = error
        self.calls = []
        self.launches = []

    def run(self, argv, *, cwd, env, timeout, max_bytes):
        self.calls.append((list(argv), Path(cwd), dict(env)))
        if self.error:
            raise RuntimeError(self.error)
        if self.exit_code:
            return {"returncode": self.exit_code, "stdout": "{}"}
        path = argv[-1]
        if "issues?" in path:
            if self.partial:
                return {"items": self.inventory, "complete": False}
            return self.inventory
        if path == "repos/example/project":
            return {"id": 999 if self.bad_repo else 101, "full_name": "example/project"}
        if path == "user":
            return {"id": 1001, "login": "smoke-user"}
        if "/branches/" in path:
            return {"protected": True}
        if "/collaborators/" in path:
            return {"permission": "push"}
        raise AssertionError("unexpected gh path: " + path)

    def launch(self, argv, *, cwd, env, timeout):
        self.launches.append((list(argv), Path(cwd), dict(env)))
        return {"pid": 1234, "status": "LAUNCHED"}

    def observe(self, record, *, timeout):
        return {"status": "COMPLETED"}


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
        if "/rest/api/3/project/EX" in url:
            return 200, json.dumps({"id": "project-1", "key": "EX"})
        if "/rest/api/3/search?" in url:
            if "startAt=0" in url:
                issue = {"id": "100", "fields": {"issuetype": {"name": "Task"},
                    "status": {"statusCategory": {"key": "done"}}}}
                return 200, json.dumps({"issues": [issue], "total": 2, "snapshot_id": "snap-1"})
            issue = {"id": "101", "fields": {"issuetype": {"name": "Task"},
                "status": {"statusCategory": {"key": "new"}}}}
            return 200, json.dumps({"issues": [issue], "total": 2, "snapshot_id": "snap-1"})
        if "/rest/api/3/issue/" in url and method == "GET":
            self.issue_reads += 1
            status = "Ready" if self.issue_reads == 1 else ("Done" if "/issue/100?" in url else "In Progress")
            return 200, json.dumps({"id": "100", "fields": {"status": {"id": status}}})
        if method == "POST" and "/transitions" in url:
            return 204, ""
        raise AssertionError("unexpected Jira request: " + method + " " + url)


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
                 "token_env": "AWF_JIRA_TOKEN", "merged_status_id": "Done", "page_size": 1},
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
        methods = [call[0] for call in http.calls]
        self.assertEqual(methods.count("POST"), 1)
        self.assertLess(methods.index("GET"), methods.index("POST"))
        self.assertNotIn("secret-jira", repr(result))

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
