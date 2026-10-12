import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, load, sha256
from agentic.closeout import require_jira_reconciliation_before_counts
from agentic.contracts import Contracts
from test_integration_queue import register_integration_observer


NOW = "2026-10-10T10:01:00Z"
PROJECT_CONFIG = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
TRANSITION_ID = "10000000-0000-0000-0000-000000000001"
MERGE_RESULT_ID = "10000000-0000-0000-0000-000000000002"


class IntegrationQueueCloseoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts(ROOT / ".agentic/schemas")
        if not shutil.which("git"):
            raise unittest.SkipTest("Git executable unavailable")
        cls.temporary = tempfile.TemporaryDirectory(prefix="awf-closeout-count-")
        cls.repo = Path(cls.temporary.name) / "repo"
        cls.repo.mkdir()
        def git(*args):
            return subprocess.run(
                ["git", "-C", str(cls.repo), *args], check=True,
                capture_output=True, text=True).stdout.strip()
        git("init", "-q", "--initial-branch=main")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "fixture")
        git("config", "commit.gpgsign", "false")
        (cls.repo / "src").mkdir()
        (cls.repo / "src/task.py").write_bytes(b"value = 1\n")
        git("add", "src/task.py")
        git("commit", "-q", "-m", "base")
        cls.base = git("rev-parse", "HEAD")
        git("checkout", "-q", "-b", "awf/task-17")
        (cls.repo / "src/task.py").write_bytes(b"value = 2\n")
        git("commit", "-q", "-am", "candidate")
        cls.head = git("rev-parse", "HEAD")
        git("checkout", "-q", "main")
        git("merge", "-q", "--no-ff", "-m", "merge", "awf/task-17")
        cls.merge = git("rev-parse", "HEAD")
        cls.tree = git("rev-parse", "HEAD^{tree}")
        cls.blob = git("rev-parse", "HEAD:src/task.py")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def binding(self, issue="AWF-17"):
        return {"project_id": "10000000-0000-0000-0000-000000000003",
                "repository_id": 17, "issue_id": issue,
                "requirements_hash": "a" * 64, "contract_hash": "b" * 64,
                "policy_hash": "c" * 64, "candidate_id": "d" * 64}

    def record(self, transition="VALID"):
        if transition == "VALID":
            transition = TRANSITION_ID
        return {"schema_version": 3,
                "record_id": "10000000-0000-0000-0000-000000000004",
                "created_at": NOW, "producer_id": "controller-1",
                "run_id": "10000000-0000-0000-0000-000000000005",
                "binding": self.binding(), "pr_number": 17,
                "reviewed_head_sha": self.head, "base_sha": self.base,
                "merge_commit_sha": self.merge, "merge_tree_sha": self.tree,
                "bound_files": [{"path": "src/task.py", "blob_sha": self.blob,
                                 "sha256": hashlib.sha256(b"value = 2\n").hexdigest()}],
                "gate_record_id": "10000000-0000-0000-0000-000000000006",
                "review_record_ids": ["10000000-0000-0000-0000-000000000007"],
                "jira_transition_record_id": transition,
                "evidence": ["urn:awf:fixture:closeout"]}

    def reconciliation(self, **changes):
        value = {"schema_version": 3, "record_id": TRANSITION_ID,
                 "created_at": NOW, "producer_id": "controller-1",
                 "run_id": "10000000-0000-0000-0000-000000000008",
                 "binding": self.binding(),
                 "operation_id": "10000000-0000-0000-0000-000000000009",
                 "merge_result_id": MERGE_RESULT_ID,
                 "jira_provider": {"cloud_id": "fixture-cloud",
                                   "site": "https://jira.example.invalid",
                                   "project_id": "fixture-project", "project_key": "EX",
                                   "controller_actor_id": "fixture-controller"},
                 "lifecycle_event": "JIRA_RECONCILED", "from_status_id": "3",
                 "to_status_id": "Done", "transition_id": "31", "status": "SUCCEEDED",
                 "evidence": ["urn:awf:fixture:jira-transition"],
                 "read_back": {"observed_status": "Done",
                                "observed_actor": "fixture-controller",
                               "observed_at": NOW}}
        value.update(changes)
        return value

    def gate(self, closeout, reconciliation=None, *, config=None,
             accepted_config=None):
        config = copy.deepcopy(PROJECT_CONFIG if config is None else config)
        accepted_config = copy.deepcopy(
            PROJECT_CONFIG if accepted_config is None else accepted_config)
        registry = {}
        config_registry = {}
        register_integration_observer(
            "fixture-project-config",
            lambda _request: {
                "project_config": copy.deepcopy(accepted_config),
                "config_sha256": sha256(canonical(accepted_config)),
            }, config_registry, observed_at=NOW)
        selector = None
        if reconciliation is not None:
            selector = {"producer_id": reconciliation["producer_id"]}
            register_integration_observer(
                reconciliation["producer_id"],
                lambda _request: {"record": copy.deepcopy(reconciliation)},
                registry, observed_at=NOW)
        return require_jira_reconciliation_before_counts(
            closeout, repository=self.repo, project_config=config, contracts=self.contracts,
            project_config_selector={"producer_id": "fixture-project-config"},
            project_config_observer_registry=config_registry,
            jira_reconciliation_selector=selector,
            jira_observer_registry=registry, now=NOW)

    def test_ac11_schema_valid_jira_reconciliation_precedes_post_merge_count(self):
        closeout, transition = self.record(), self.reconciliation()
        self.contracts.validate("closeout-record", closeout)
        self.contracts.validate("jira-transition", transition)
        result = self.gate(closeout, transition)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("AWF-17", result["ticket"])
        self.assertTrue(result["count_allowed"])
        self.assertTrue(result["jira_reconciled"])
        self.assertFalse(result["execution_authority"])
        self.assertRegex(
            result["jira_observation"]["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_r6_002_api_has_no_caller_jira_enablement_boolean(self):
        import inspect
        parameters = inspect.signature(
            require_jira_reconciliation_before_counts).parameters
        self.assertNotIn("jira_enabled", parameters)
        self.assertIn("project_config", parameters)

    def test_ac11_enabled_jira_without_reconciliation_fails_closed(self):
        with self.assertRaisesRegex(ValidationError, "jira-transition"):
            self.gate(self.record(None))

    def test_ac11_unrelated_transition_id_and_binding_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "resolve"):
            self.gate(self.record("10000000-0000-0000-0000-000000000099"),
                      self.reconciliation())
        wrong = self.reconciliation(binding=self.binding("AWF-18"))
        with self.assertRaisesRegex(ValidationError, "closeout candidate"):
            self.gate(self.record(), wrong)

    def test_ac11_transition_and_readback_must_succeed(self):
        with self.assertRaisesRegex(ValidationError, "did not succeed"):
            self.gate(self.record(), self.reconciliation(status="FAILED"))
        bad = self.reconciliation(read_back={"observed_status": "In Review",
            "observed_actor": "fixture-controller", "observed_at": NOW})
        with self.assertRaisesRegex(ValidationError, "target-status readback"):
            self.gate(self.record(), bad)
        missing_observation_time = self.reconciliation(read_back={"observed_status": "Done",
            "observed_actor": "fixture-controller", "observed_at": None})
        with self.assertRaisesRegex(ValidationError, "target-status readback"):
            self.gate(self.record(), missing_observation_time)

    def test_ac11_disabled_jira_needs_no_synthetic_transition(self):
        disabled = copy.deepcopy(PROJECT_CONFIG)
        disabled["jira"]["enabled"] = False
        for key in ("cloud_id", "site", "provider_project_id", "project_key",
                    "controller_actor_id"):
            disabled["jira"][key] = None
        result = self.gate(
            self.record(None), config=disabled, accepted_config=disabled)
        self.assertTrue(result["count_allowed"])
        self.assertFalse(result["jira_reconciled"])
        with self.assertRaisesRegex(ValidationError, "cannot claim"):
            self.gate(
                self.record(), self.reconciliation(), config=disabled,
                accepted_config=disabled)

    def test_r7_003_caller_disabled_configuration_is_not_accepted_authority(self):
        forged = copy.deepcopy(PROJECT_CONFIG)
        forged["jira"]["enabled"] = False
        for key in ("cloud_id", "site", "provider_project_id", "project_key",
                    "controller_actor_id"):
            forged["jira"][key] = None
        with self.assertRaisesRegex(ValidationError, "accepted project configuration"):
            self.gate(self.record(None), config=forged)

    def test_r6_002_configured_done_status_is_exact(self):
        in_review = self.reconciliation(
            to_status_id="In Review",
            read_back={"observed_status": "In Review",
                       "observed_actor": "fixture-controller",
                       "observed_at": NOW})
        with self.assertRaisesRegex(ValidationError, "configured Done"):
            self.gate(self.record(), in_review)

    def test_r6_002_reconciliation_must_be_freshly_observed(self):
        config_registry = {}
        register_integration_observer(
            "fixture-project-config",
            lambda _request: {
                "project_config": copy.deepcopy(PROJECT_CONFIG),
                "config_sha256": sha256(canonical(PROJECT_CONFIG)),
            }, config_registry, observed_at=NOW)
        with self.assertRaisesRegex(ValidationError, "registered Git/provider observer"):
            require_jira_reconciliation_before_counts(
                self.record(), project_config=PROJECT_CONFIG,
                repository=self.repo,
                contracts=self.contracts,
                project_config_selector={"producer_id": "fixture-project-config"},
                project_config_observer_registry=config_registry,
                jira_reconciliation_selector={"producer_id": "controller-1"},
                jira_observer_registry={}, now=NOW)

    def test_r6_002_count_gate_revalidates_closeout_git_objects(self):
        forged = self.record()
        forged["merge_commit_sha"] = "f" * 40
        with self.assertRaisesRegex(ValidationError, "absent from the object store"):
            self.gate(forged, self.reconciliation())

    def test_ac11_rejects_old_synthetic_record_shapes(self):
        synthetic = {"ticket": "AWF-17", "pr_number": 17,
                     "reviewed_head_sha": "1" * 40,
                     "merge_commit_sha": "2" * 40,
                     "jira_transition_record_id": TRANSITION_ID}
        with self.assertRaisesRegex(ValidationError, "closeout-record"):
            self.gate(synthetic, self.reconciliation())


if __name__ == "__main__":
    unittest.main()
