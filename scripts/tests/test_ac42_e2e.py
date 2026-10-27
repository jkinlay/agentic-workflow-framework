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
from agentic.publication import scan_repository  # noqa: E402

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
            active_command = report["rows"]["active_single_status_run"]["evidence"]["command"]
            self.assertEqual(Path(active_command[2]).resolve(),
                             (Path(raw) / "work/project/.agentic/scripts/workflow.py").resolve())
            self.assertEqual(active_command[5], "status")
            self.assertEqual(report["result"], "FAIL")  # never a gate pass without the self-test
            self.assertFalse(report["gate_eligible"])
            self.assertIn({"check": "read_only_external_resource_admission", "result": "NOT_COVERED"},
                          report["required_check_failures"])
            self.assertTrue(report["rows"]["project_values_preserved"]["evidence"]["gitignore_prefix_preserved"])
            self.assertEqual(done.returncode, 1)


def _load_harness():
    import importlib.util
    spec = importlib.util.spec_from_file_location("awf_ac42_e2e", ROOT / "scripts/ac42_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.harness = _load_harness()
        self.rows = {name: {"result": "PASS"} for name in self.harness.REQUIRED}

    def test_not_covered_required_admission_fails_closed(self):
        self.rows["read_only_external_resource_admission"] = {"result": "NOT_COVERED"}
        outcome = self.harness.gate_outcome(self.rows, execution_eligible=True)
        self.assertEqual(outcome["result"], "FAIL")
        self.assertFalse(outcome["gate_eligible"])
        self.assertEqual(outcome["required_check_failures"], [
            {"check": "read_only_external_resource_admission", "result": "NOT_COVERED"}])

    def test_all_required_pass_preserves_pass_and_gate_eligibility(self):
        outcome = self.harness.gate_outcome(self.rows, execution_eligible=True)
        self.assertEqual(outcome, {"result": "PASS", "gate_eligible": True,
                                  "required_check_failures": []})


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


class ProjectValuesPreservedTests(unittest.TestCase):
    CONFIG = ".agentic/PROJECT_CONFIG.yaml"
    INSTALLED = ".agentic/installed-manifest.json"
    BEFORE = (b"# owner comment\n"
              b"{\n"
              b'  "template": {"expected_workflow_version": "1.9.2"},\n'
              b'  "jira": {\n'
              b'    "enabled": false,\n'
              b'    "site": "https://jira.example.invalid",\n'
              b'    "project_key": "KEEP"\n'
              b'  },\n'
              b'  "execution": {\n'
              b'    "risk_tiers": {\n'
              b'      "tier1_review": {"max_rounds": 1}\n'
              b'    }\n'
              b'  }\n'
              b"}\n"
              b"# retained formatting\n")
    AFTER = (b"# owner comment\n"
             b"{\n"
             b'  "template": {"expected_workflow_version": "1.9.5"},\n'
             b'  "jira": {\n'
             b'    "enabled": false,\n'
             b'    "cloud_id": null,\n'
             b'    "provider_project_id": null,\n'
             b'    "controller_actor_id": null,\n'
             b'    "site": "https://jira.example.invalid",\n'
             b'    "project_key": "KEEP"\n'
             b'  },\n'
             b'  "execution": {\n'
             b'    "risk_tiers": {tier3_review: {roles: [critic, specialist], '
             b'findings: blocking, max_rounds: 3}, \n'
             b'      "tier1_review": {"max_rounds": 1}\n'
             b'    }\n'
             b'  }\n'
             b"}\n"
             b"# retained formatting\n")
    IGNORE_TEMPLATE = (ROOT / ".agentic/templates/operating.gitignore").read_bytes()
    IGNORE_HEADER = IGNORE_TEMPLATE.splitlines()[0]
    HISTORICAL_IGNORE = IGNORE_TEMPLATE.replace(
        b".agentic/.venv/\n", b".agentic/legacy-runtime/\n")

    def decision(self, after):
        harness = _load_harness()
        return harness.project_values_preserved(
            {self.CONFIG: self.BEFORE},
            {self.CONFIG: after, ".gitignore": self.IGNORE_TEMPLATE},
            self.CONFIG, self.INSTALLED)[0]

    def gitignore_decision(self, before, after):
        harness = _load_harness()
        owner_before = {self.CONFIG: self.BEFORE}
        owner_after = {self.CONFIG: self.AFTER}
        if before is not None:
            owner_before[".gitignore"] = before
        if after is not None:
            owner_after[".gitignore"] = after
        return harness.project_values_preserved(
            owner_before, owner_after, self.CONFIG, self.INSTALLED)[0]

    def test_documented_tier_three_insertion_is_accepted(self):
        self.assertTrue(self.decision(self.AFTER))

    def test_non_null_jira_binding_mutation_is_rejected(self):
        mutated = self.AFTER.replace(b'"cloud_id": null', b'"cloud_id": "changed"')
        self.assertFalse(self.decision(mutated))

    def test_existing_non_null_jira_binding_is_preserved_and_mutation_rejected(self):
        before = self.BEFORE.replace(
            b'    "enabled": false,\n',
            b'    "enabled": false,\n'
            b'    "cloud_id": "abc123",\n')
        after = self.AFTER.replace(b'"cloud_id": null', b'"cloud_id": "abc123"')
        harness = _load_harness()
        expected = harness.expected_project_config(before)
        self.assertEqual(after, expected)
        self.assertTrue(harness.project_values_preserved(
            {self.CONFIG: before},
            {self.CONFIG: after, ".gitignore": self.IGNORE_TEMPLATE},
            self.CONFIG, self.INSTALLED)[0])
        mutated = after.replace(b'"cloud_id": "abc123"', b'"cloud_id": "changed"')
        self.assertFalse(harness.project_values_preserved(
            {self.CONFIG: before},
            {self.CONFIG: mutated, ".gitignore": self.IGNORE_TEMPLATE},
            self.CONFIG, self.INSTALLED)[0])

    def test_comments_and_formatting_outside_exact_edits_are_kept(self):
        self.assertTrue(self.decision(self.AFTER))
        self.assertFalse(self.decision(self.AFTER.replace(
            b"# retained formatting", b"# changed formatting")))

    def test_unrelated_owner_value_change_is_rejected(self):
        mutated = self.AFTER.replace(b'"project_key": "KEEP"', b'"project_key": "CHANGED"')
        self.assertFalse(self.decision(mutated))

    def test_version_replacement_is_scoped_to_template_mapping(self):
        before = (b"template:\n"
                  b"  expected_workflow_version: !!str 1.9.2\n"
                  b"owner_note: \"expected_workflow_version: 1.9.2\"\n"
                  b"jira: {cloud_id: KEEP, provider_project_id: KEEP, "
                  b"controller_actor_id: KEEP, site: https://jira.example.invalid}\n"
                  b"execution: {risk_tiers: {tier3_review: KEEP}}\n")
        expected = before.replace(
            b"expected_workflow_version: !!str 1.9.2",
            b"expected_workflow_version: !!str 1.9.5")
        harness = _load_harness()
        self.assertEqual(expected, harness.expected_project_config(before))

    def test_flow_jira_existing_identity_is_not_duplicated(self):
        before = (b"template: {expected_workflow_version: 1.9.2}\n"
                  b"jira: {cloud_id: KEEP,\n"
                  b"  provider_project_id: KEEP,\n"
                  b"  controller_actor_id: KEEP,\n"
                  b"  site: https://jira.example.invalid}\n"
                  b"execution: {risk_tiers: {tier3_review: KEEP}}\n")
        expected = before.replace(b"1.9.2", b"1.9.5", 1)
        harness = _load_harness()
        self.assertEqual(expected, harness.expected_project_config(before))
        self.assertEqual(1, expected.count(b"cloud_id:"))

    def test_tier_insertion_is_scoped_to_execution_risk_tiers(self):
        before = (b"template: {expected_workflow_version: 1.9.2}\n"
                  b"jira: {cloud_id: KEEP, provider_project_id: KEEP, "
                  b"controller_actor_id: KEEP, site: https://jira.example.invalid}\n"
                  b"owner_policy:\n"
                  b"  risk_tiers: {owner_tier: KEEP}\n"
                  b"execution: {risk_tiers: {tier1_review: KEEP}}\n")
        tier = (b"tier3_review: {roles: [critic, specialist], findings: blocking, "
                b"max_rounds: 3}")
        expected = before.replace(b"1.9.2", b"1.9.5", 1).replace(
            b"execution: {risk_tiers: {",
            b"execution: {risk_tiers: {" + tier + b", ")
        harness = _load_harness()
        self.assertEqual(expected, harness.expected_project_config(before))
        self.assertEqual(b"owner_tier: KEEP", expected.splitlines()[3].split(b"{")[1][:-1])

    def test_historical_gitignore_block_replaced_in_place_is_accepted(self):
        prefix, suffix = b"owner-prefix/\n", b"owner-suffix/\n"
        before = prefix + self.HISTORICAL_IGNORE + suffix
        self.assertTrue(self.gitignore_decision(
            before, prefix + self.IGNORE_TEMPLATE + suffix))

    def test_crlf_historical_gitignore_block_replaced_without_owner_normalization(self):
        prefix, suffix = b"# Owner \xff\r\nowner-prefix/\r\n", b"owner-\xfe/\r\n"
        historical = self.HISTORICAL_IGNORE.replace(b"\n", b"\r\n")
        self.assertTrue(self.gitignore_decision(
            prefix + historical + suffix, prefix + self.IGNORE_TEMPLATE + suffix))

    def test_historical_terminal_at_eof_without_newline_is_replaced(self):
        prefix = b"owner-prefix/\n"
        before = prefix + self.HISTORICAL_IGNORE.rstrip(b"\n")
        self.assertTrue(self.gitignore_decision(before, prefix + self.IGNORE_TEMPLATE))

    def test_multiple_historical_gitignore_blocks_are_deduplicated(self):
        prefix, between, suffix = b"prefix/\n", b"between/\n", b"suffix/\n"
        before = (prefix + self.HISTORICAL_IGNORE + between
                  + self.HISTORICAL_IGNORE + suffix)
        self.assertTrue(self.gitignore_decision(
            before, prefix + self.IGNORE_TEMPLATE + between + suffix))

    def test_owner_terminal_after_complete_block_is_preserved(self):
        prefix = b"prefix/\n"
        suffix = b".awf-*.tmp\nowner-suffix/\n"
        before = prefix + self.HISTORICAL_IGNORE + suffix
        self.assertTrue(self.gitignore_decision(
            before, prefix + self.IGNORE_TEMPLATE + suffix))

    def test_partial_gitignore_header_is_rejected(self):
        malformed = (
            self.IGNORE_HEADER[:-1] + b"\nowner/\n.awf-*.tmp\n",
            self.IGNORE_HEADER + b" extra\nowner/\n.awf-*.tmp\n",
        )
        for before in malformed:
            with self.subTest(before=before):
                self.assertFalse(self.gitignore_decision(before, before + self.IGNORE_TEMPLATE))

    def test_nested_gitignore_header_is_rejected(self):
        before = (self.IGNORE_HEADER + b"\nowner/\n" + self.HISTORICAL_IGNORE)
        self.assertFalse(self.gitignore_decision(before, before + self.IGNORE_TEMPLATE))

    def test_incomplete_gitignore_block_is_rejected(self):
        before = self.IGNORE_HEADER + b"\nowner/\n"
        self.assertFalse(self.gitignore_decision(before, before + self.IGNORE_TEMPLATE))

    def test_bare_cr_is_owner_data_and_append_must_be_exact(self):
        before = b"owner\r" + self.IGNORE_HEADER + b"\nowner-rule/\n.awf-*.tmp\n"
        expected = before + self.IGNORE_TEMPLATE
        self.assertTrue(self.gitignore_decision(before, expected))
        for corrupt in (expected + b"extra", expected + self.IGNORE_TEMPLATE,
                        self.IGNORE_TEMPLATE):
            with self.subTest(corrupt=corrupt):
                self.assertFalse(self.gitignore_decision(before, corrupt))

    def test_gitignore_append_is_accepted_only_without_a_historical_block(self):
        for before in (b"", b"owner/\n", b"owner/"):
            separator = b"\n" if before and not before.endswith(b"\n") else b""
            expected = before + separator + self.IGNORE_TEMPLATE
            with self.subTest(valid=before):
                self.assertTrue(self.gitignore_decision(before, expected))
            with self.subTest(altered=before):
                self.assertFalse(self.gitignore_decision(before, expected + b"extra"))
            with self.subTest(duplicated=before):
                self.assertFalse(self.gitignore_decision(before, expected + self.IGNORE_TEMPLATE))
        historical = b"owner/\n" + self.HISTORICAL_IGNORE
        self.assertFalse(self.gitignore_decision(
            historical, historical + self.IGNORE_TEMPLATE))

    def test_absent_gitignore_requires_exact_template_creation(self):
        self.assertTrue(self.gitignore_decision(None, self.IGNORE_TEMPLATE))
        for corrupt in (None, b"", self.IGNORE_TEMPLATE[:-1],
                        self.IGNORE_TEMPLATE + self.IGNORE_TEMPLATE,
                        self.IGNORE_TEMPLATE + b"extra"):
            with self.subTest(corrupt=corrupt):
                self.assertFalse(self.gitignore_decision(None, corrupt))

    def test_gitignore_owner_line_change_outside_block_is_rejected(self):
        prefix, between, suffix = b"prefix/\n", b"between/\n", b"suffix/\n"
        before = (prefix + self.HISTORICAL_IGNORE + between
                  + self.HISTORICAL_IGNORE + suffix)
        expected = prefix + self.IGNORE_TEMPLATE + between + suffix
        self.assertTrue(self.gitignore_decision(before, expected))
        for corrupt in (b"changed-prefix/\n" + expected[len(prefix):],
                        prefix + self.IGNORE_TEMPLATE + b"changed-between/\n" + suffix,
                        expected[:-len(suffix)] + b"changed-suffix/\n"):
            with self.subTest(corrupt=corrupt):
                self.assertFalse(self.gitignore_decision(before, corrupt))

    def test_gitignore_block_differing_from_shipped_template_is_rejected(self):
        altered = self.IGNORE_TEMPLATE.replace(
            b".agentic-backup/\n", b".agentic-backup-altered/\n")
        owner = b"owner/\n"
        self.assertFalse(self.gitignore_decision(owner, owner + altered))
        before = owner + self.HISTORICAL_IGNORE + b"suffix/\n"
        self.assertFalse(self.gitignore_decision(
            before, owner + altered + b"suffix/\n"))


class AutocrlfScopeTests(unittest.TestCase):
    def test_fresh_clone_preserves_false_and_input_without_global_writes(self):
        harness = _load_harness()
        for value in ("false", "input"):
            with self.subTest(value=value), tempfile.TemporaryDirectory(prefix="awf-ac42-autocrlf-") as raw:
                root = Path(raw)
                home = root / "home"
                home.mkdir()
                global_config = home / "global.gitconfig"
                global_config.write_text("[core]\n\tautocrlf = false\n", encoding="utf-8", newline="\n")
                global_before = global_config.read_bytes()
                overrides = {"HOME": str(home), "GIT_CONFIG_GLOBAL": str(global_config),
                             "GIT_CONFIG_NOSYSTEM": "1"}
                environment = dict(os.environ, **overrides)
                source = root / "source"
                source.mkdir()

                def command(*arguments, cwd=source, check=True):
                    return subprocess.run(["git", *arguments], cwd=cwd, env=child_env(environment),
                                          capture_output=True, check=check, text=True)

                command("init", "-q", "-b", "main")
                command("config", "--local", "core.autocrlf", value)
                (source / "plain.txt").write_bytes(b"one\ntwo\n")
                command("add", "plain.txt")
                command("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-q", "-m", "fixture")
                clone = root / "clone"
                result = harness.clone_with_autocrlf(source, clone, root, extra=overrides)
                self.assertEqual(0, result["exit_code"], result)
                self.assertEqual(b"one\r\ntwo\r\n", (clone / "plain.txt").read_bytes())
                self.assertEqual(value, command("config", "--local", "--get", "core.autocrlf").stdout.strip())
                clone_setting = command("config", "--local", "--get", "core.autocrlf",
                                        cwd=clone, check=False)
                self.assertEqual(1, clone_setting.returncode)
                self.assertEqual(global_before, global_config.read_bytes())


class WindowsLauncherPublicationTests(unittest.TestCase):
    def test_scratch_root_default_is_derived_from_system_temp(self):
        script = (ROOT / "tools/windows/Invoke-AC42.ps1").read_text(encoding="utf-8")
        param_block = script.split("param(", 1)[1].split("\n)", 1)[0]
        scratch_parameters = [line.strip() for line in param_block.splitlines()
                              if "$ScratchRoot" in line]
        self.assertEqual(scratch_parameters, [
            '[string]$ScratchRoot = (Join-Path ([System.IO.Path]::GetTempPath()) '
            '"awf-ac42-scratch"),'
        ])

    def test_merged_tree_ac42_files_have_no_publication_findings(self):
        paths = ("scripts/ac42_e2e.py", "tools/windows/Invoke-AC42.ps1")

        def git(repository, *arguments):
            return subprocess.run(
                ["git", "-c", "core.autocrlf=false", "-c", "user.name=fixture",
                 "-c", "user.email=fixture@example.invalid", "-C", str(repository), *arguments],
                check=True, capture_output=True, env=child_env(os.environ),
            ).stdout

        with tempfile.TemporaryDirectory(prefix="awf-ac42-publication-") as raw:
            repository = Path(raw) / "repository"
            repository.mkdir()
            git(repository, "init", "-q", "-b", "main")
            for relative in paths:
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("neutral fixture\n", encoding="utf-8", newline="\n")
            git(repository, "add", "--", *paths)
            git(repository, "commit", "-q", "-m", "base")
            base = git(repository, "rev-parse", "HEAD").decode("ascii").strip()

            # Scan the exact source blobs in this checkout's merged tree. Deleted
            # base-side diff lines are intentionally absent from these blobs.
            for relative in paths:
                (repository / relative).write_bytes(git(ROOT, "show", f"HEAD:{relative}"))
            git(repository, "add", "--", *paths)
            git(repository, "commit", "-q", "-m", "candidate")

            result = scan_repository(repository, base, "HEAD")
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["unscanned_count"], 0, result)
            self.assertEqual(result["findings"], [], result)


if __name__ == "__main__":
    unittest.main()
