"""Smoke-run the AC42 harness off Windows; the gate itself runs on Windows."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402

FIXTURES = ROOT / ".agentic/tests/fixtures/upgrades/versions.json"


@unittest.skipUnless(FIXTURES.is_file(), "historical upgrade fixtures are source-repository only")
class AC42HarnessSmokeTests(unittest.TestCase):
    def test_smoke_run_passes_every_covered_clause_except_the_skipped_self_test(self):
        # 1.9.2 has an owner .gitignore that the upgrade appends to; under
        # core.autocrlf=true its working-tree bytes change on checkout.
        for version in ("1.9.1", "1.9.2"):
            with self.subTest(version=version):
                self._smoke(version)

    def _smoke(self, version):
        with tempfile.TemporaryDirectory(prefix="awf-ac42-test-") as raw:
            evidence = Path(raw) / "evidence.json"
            done = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/ac42_e2e.py"), "--work",
                                   str(Path(raw) / "work"), "--evidence", str(evidence), "--skip-self-test",
                                   "--from-version", version],
                                  capture_output=True, text=True, timeout=600,
                                  env=child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1")))
            report = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertNotIn("error", report, done.stderr)
            results = {name: row["result"] for name, row in report["rows"].items()}
            self.assertEqual(results.pop("self_test"), "NOT_COVERED")
            self.assertEqual(results.pop("read_only_external_resource_admission"), "NOT_COVERED")
            self.assertEqual(set(results.values()), {"PASS"}, report["rows"])
            self.assertEqual(report["rows"]["active_single_status_run"]["evidence"]["project_state"], "ACTIVE")
            self.assertEqual(report["result"], "FAIL")  # never a gate pass without the self-test
            self.assertFalse(report["gate_eligible"])
            self.assertTrue(report["rows"]["project_values_preserved"]["evidence"]["gitignore_prefix_preserved"])
            self.assertEqual(done.returncode, 1)


def _load_harness():
    import importlib.util
    spec = importlib.util.spec_from_file_location("awf_ac42_e2e", ROOT / "scripts/ac42_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OwnerBlobTests(unittest.TestCase):
    def test_owner_blob_is_isolated_bounded_and_fails_closed(self):
        harness = _load_harness()
        with tempfile.TemporaryDirectory(prefix="awf-owner-blob-") as raw:
            repo = Path(raw)
            def git(*args):
                return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                       "-C", str(repo), *args], check=True, capture_output=True,
                                      text=True).stdout.strip()
            git("init", "-q")
            (repo / ".gitignore").write_bytes(b"owner\n")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            self.assertEqual(b"owner\n", harness.owner_blob(repo, "HEAD", ".gitignore"))
            self.assertIsNone(harness.owner_blob(repo, "HEAD", "absent.txt"))
            # Replacement objects must not substitute the committed bytes.
            original = git("rev-parse", "HEAD:.gitignore")
            (repo / "other").write_bytes(b"replaced\n")
            substitute = git("hash-object", "-w", "other")
            git("replace", original, substitute)
            self.assertEqual(b"owner\n", harness.owner_blob(repo, "HEAD", ".gitignore"))
            with self.assertRaises(RuntimeError):
                harness.owner_blob(repo, "0" * 40, ".gitignore")  # unreadable commit
            with self.assertRaises(RuntimeError):
                harness.owner_blob(repo, "HEAD", ".gitignore", max_bytes=3)


if __name__ == "__main__":
    unittest.main()
