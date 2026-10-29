"""Genuine published 1.9.5 bootstrap and transactional upgrade regressions."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/tests"))
import source_only
sys.path.insert(0, str(ROOT / "scripts"))
if source_only.AWF_SOURCE_REPOSITORY:
    from publish_release import materialize_commit
from agentic import VERSION, ValidationError
from agentic.canonical import load
from agentic.child_process import child_env
from agentic.installer import install
from agentic.model_routing import RoutingLedger, policy_from_config, routing_context
from agentic.upgrade import load_known_versions

PUBLISHED_COMMIT = "8ad399943c8f5e4f17db72f3e321b9365ee27782"
PUBLISHED_PIN = "1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7"


@source_only.skip_unless_source_repo("requires the published historical Git source and real locked wheels", "scripts/publish_release.py")
class Published195UpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="awf-published195-upgrade-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name).resolve()
        cls.historical = cls.base / "published-source"
        # Missing historical objects are failures in full-source CI, not skips.
        materialize_commit(ROOT, PUBLISHED_COMMIT, cls.historical)
        if hashlib.sha256((cls.historical / "MANIFEST.json").read_bytes()).hexdigest() != PUBLISHED_PIN:
            raise AssertionError("independently pinned published 1.9.5 identity mismatch")
        supplied = os.environ.get("AWF_REAL_RUNTIME_WHEELHOUSE")
        cls.wheelhouse = Path(supplied).resolve() if supplied else cls.base / "wheelhouse"
        if not supplied:
            cls.wheelhouse.mkdir()
            command = [sys.executable, "-m", "pip", "download", "--require-hashes",
                       "--only-binary=:all:", "--timeout", "30", "--retries", "1",
                       "--dest", str(cls.wheelhouse), "-r",
                       str(cls.historical / ".agentic/requirements.lock")]
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", env=child_env(), timeout=180)
            if result.returncode:
                raise AssertionError("real locked-wheel provisioning failed: " + result.stderr)
        cls.target_pin = hashlib.sha256((ROOT / "MANIFEST.json").read_bytes()).hexdigest()

    def setUp(self):
        self.project = self.base / ("p-" + self._testMethodName.split("_")[1])
        self.bootstrap(self.historical, PUBLISHED_PIN, "install")

    def bootstrap(self, source, pin, mode):
        command = [sys.executable, "-B", str(source / "scripts/bootstrap_project.py"),
                   "--dest", str(self.project), "--expected-manifest-sha256", pin,
                   "--runtime-wheelhouse", str(self.wheelhouse), "--mode", mode,
                   "--github-repo", "example-org/example-repo", "--repository-id", "101",
                   "--default-branch", "main", "--test-command", "python -m unittest",
                   "--codeowner", "@fixture-owner"]
        completed = subprocess.run(command, cwd=self.base, capture_output=True,
                                   text=True, encoding="utf-8", env=child_env(), timeout=180)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "CONFIGURED", result)
        checks = result["post_install_checks"]
        self.assertEqual([item["exit_code"] for item in checks], [0, 0], checks)
        return result

    def test_upgrade_preserves_operator_choices_accounting_and_prior_archives(self):
        table = load_known_versions((ROOT / ".agentic/upgrade/known-versions.json").read_bytes())
        entry = table["versions"]["1.9.5"]
        self.assertEqual(entry["source_manifest_sha256"], PUBLISHED_PIN)
        self.assertEqual(entry["managed_file_count"], 464)
        routing = self.project / ".agentic/routing.yaml"
        route = json.loads(routing.read_bytes())
        route["role_defaults"]["worker"]["effort"] = "high"
        routing.write_text(json.dumps(route, indent=4, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        configuration = load(self.project / ".agentic/PROJECT_CONFIG.yaml")
        with routing_context(routing.read_bytes(), "synthetic published195 owner routing"):
            policy = policy_from_config(configuration)
        ledger_path = self.project / ".agentic-state/routing/ledger.sqlite"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = RoutingLedger(ledger_path)
        request = dict(ticket_id="EX-195", role="worker", agent_id="fixture-worker",
                       context_id="fixture-context", phase="implementation", task_class="unit-fix",
                       complexity="medium", risk="low", uncertainty="low", verification="strong",
                       risk_flags=[], reservation_tokens=100, reservation_cost_microusd=None)
        capabilities = {"models": {m: v["reasoning_efforts"] for m, v in policy["models"].items()}}
        run = ledger.reserve("fixture-project", policy, request, capabilities)
        ledger.settle(run["run_id"], dict(actual_model=run["model"],
            actual_reasoning_effort=run["reasoning_effort"], actual_context_id="fixture-context",
            actual_tokens=20, actual_cost_microusd=None, success=True, validation_passed=True,
            independent_review_passed=True, reviewer_context_id="fixture-review-context", escaped_defect=False))
        prior = self.project / ".agentic-state/archive/upgrade-to-1.9.5/files/fixture-note.txt"
        prior.parent.mkdir(parents=True)
        prior.write_bytes(b"synthetic retained historical archive\n")
        audits = sorted((self.project / ".agentic-state/operating/changes").glob("C-*.json"))
        self.assertTrue(audits, "genuine bootstrap must establish operating audit history")
        preserved = [routing, self.project / "OPERATING_CONFIG.yaml", ledger_path, prior, *audits]
        before = {str(p.relative_to(self.project)): p.read_bytes() for p in preserved}
        result = self.bootstrap(ROOT, self.target_pin, "upgrade")
        self.assertEqual(result["upgrade"]["detected_version"], "1.9.5")
        for p in preserved:
            self.assertEqual(p.read_bytes(), before[str(p.relative_to(self.project))], str(p))
        archive = self.project / (".agentic-state/archive/upgrade-to-" + VERSION + "/manifest.json")
        if archive.exists():
            records = json.loads(archive.read_bytes())["files"]
            self.assertFalse(any("archive/upgrade-to-1.9.5/" in row["source"] for row in records))
        self.bootstrap(ROOT, self.target_pin, "upgrade")
        for p in preserved:
            self.assertEqual(p.read_bytes(), before[str(p.relative_to(self.project))], str(p))

    def test_missing_audited_operating_refuses_without_resetting_history(self):
        (self.project / "OPERATING_CONFIG.yaml").unlink()
        changes = self.project / ".agentic-state/operating/changes"
        before = {p.name: p.read_bytes() for p in changes.iterdir() if p.is_file()}
        self.assertTrue(before)
        with self.assertRaisesRegex(ValidationError, "missing with retained audit history"):
            install(ROOT, self.project, self.target_pin, mode="upgrade", configure=True, discover=False)
        self.assertFalse((self.project / "OPERATING_CONFIG.yaml").exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in changes.iterdir() if p.is_file()})

    def test_interrupted_upgrade_restores_published_receipt_and_runtime(self):
        paths = [self.project / ".agentic/installed-manifest.json",
                 self.project / ".agentic/PROJECT_CONFIG.yaml", self.project / "OPERATING_CONFIG.yaml"]
        before = {str(p): p.read_bytes() for p in paths}
        with self.assertRaisesRegex(OSError, "Injected installation failure"):
            install(ROOT, self.project, self.target_pin, mode="upgrade", configure=True,
                    discover=False, fail_after=4)
        for p in paths:
            self.assertEqual(p.read_bytes(), before[str(p)])
        python = self.project / ".agentic/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        for command in ("verify-installation", "validate-config"):
            checked = subprocess.run([str(python), "-B", "-I", str(self.project / ".agentic/scripts/workflow.py"),
                "--root", str(self.project), command, "--json"], capture_output=True, text=True,
                encoding="utf-8", env=child_env(), timeout=60)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            observation = json.loads(checked.stdout)
            if command == "verify-installation":
                self.assertTrue(observation["integrity_valid"])
            else:
                self.assertEqual(observation["status"], "ACCEPTED")
        self.bootstrap(ROOT, self.target_pin, "upgrade")


if __name__ == "__main__":
    unittest.main()
