"""Offline metadata/configuration and installed-CLI bootstrap regressions."""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
AWF_SOURCE_REPOSITORY = (ROOT / "MANIFEST.json").is_file()  # source-only scripts/fixtures/inventory
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))
from agentic import ValidationError, VERSION
from agentic import adoption_config as adoption
from agentic import installer
from agentic.canonical import load, load_yaml, sha256
from agentic.installer import (CONFIG, INSTALLED, GITIGNORE, GITIGNORE_TEMPLATE, install, json_bytes,
                               merge_operating_ignores, operating_ignore_plan, verify_installed)
from agentic.contracts import Contracts
from agentic.policy import inspect_config


def template():
    # Installed PROJECT_CONFIG.yaml is project-owned and may already be mapped.
    return load(ROOT / ".agentic/examples/unconfigured-project.yaml")


def explicit():
    return {"repository": "fixture/widget", "repository_id": 54321, "base_branch": "trunk", "test_command": "pytest"}


def _record_hash(raw):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")


def synthetic_wheel(name="fixture-dep", version="1.0", *, additions=None,
                    metadata_name=None, metadata_version=None, corrupt_record=None,
                    special_member=None):
    """Build a small deterministic wheel byte fixture, including a complete RECORD."""
    normalized = name.replace("-", "_").replace(".", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    files = {
        f"{normalized}.py": b"VALUE = 1\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {metadata_name or name}\n"
            f"Version: {metadata_version or version}\n\n").encode("utf-8"),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: awf-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files.update(additions or {})
    record_path = f"{dist_info}/RECORD"
    rows = [[path, _record_hash(raw), str(len(raw))] for path, raw in sorted(files.items())]
    rows.append([record_path, "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    record = output.getvalue().encode("utf-8")
    if corrupt_record == "hash":
        position = record.index(b"sha256=") + len(b"sha256=")
        replacement = b"A" if record[position:position + 1] != b"A" else b"B"
        record = record[:position] + replacement + record[position + 1:]
    elif corrupt_record == "inventory":
        record += b"unlisted.py,,\n"
    files[record_path] = record
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, raw in sorted(files.items()):
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            if special_member == path:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, raw)
    return buffer.getvalue()


def wheel_lock(entries):
    lines = ["# Test runtime lock."]
    for name, version, raw in entries:
        lines.extend([f"{name}=={version} \\", f"    --hash=sha256:{hashlib.sha256(raw).hexdigest()}"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def installed_wheel(distribution_name):
    """Repack the test interpreter's installed dependency as a complete local wheel fixture."""
    distribution = importlib.metadata.distribution(distribution_name)
    root = Path(distribution.locate_file("")).resolve(strict=True)
    files = {}
    for member in distribution.files or ():
        source = Path(distribution.locate_file(member))
        try:
            metadata = os.lstat(source)
            resolved = source.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if (not stat.S_ISREG(metadata.st_mode) or relative.endswith(".dist-info/RECORD") or
                "/__pycache__/" in "/" + relative or relative.endswith((".pyc", ".pyo", ".pth")) or
                Path(relative).name.lower() in {"sitecustomize.py", "usercustomize.py"}):
            continue
        files[relative] = resolved.read_bytes()
    dist_infos = {path.split("/", 1)[0] for path in files if path.endswith(".dist-info/METADATA")}
    if len(dist_infos) != 1:
        raise AssertionError(f"test dependency {distribution_name} has no unique metadata")
    dist_info = dist_infos.pop()
    if f"{dist_info}/WHEEL" not in files:
        files[f"{dist_info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: awf-test\nRoot-Is-Purelib: false\nTag: py3-none-any\n"
    record_path = f"{dist_info}/RECORD"
    rows = [[path, _record_hash(raw), str(len(raw))] for path, raw in sorted(files.items())]
    rows.append([record_path, "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    files[record_path] = output.getvalue().encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, raw in sorted(files.items()):
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, raw)
    return distribution.version, buffer.getvalue()


class ConfigurationDerivationTests(unittest.TestCase):
    def test_complete_new_configuration_has_real_uuid_no_invented_owners(self):
        value = adoption.prepare_config(template(), overrides=explicit(), discover=False, codeowner="@custom")
        config = value["config"]
        self.assertEqual(uuid.UUID(config["project"]["id"]).version, 4)
        self.assertEqual(config["project"]["name"], "widget")
        self.assertEqual(config["project"]["short_name"], "widget")
        self.assertEqual(config["jira"]["enabled"], False)
        self.assertIsNone(config["jira"]["site"])
        self.assertEqual(config["validation"]["required_ci_checks"], [])
        self.assertEqual(config["merge_gate"]["trusted_owner_ids"], [])
        self.assertEqual(config["execution"]["max_tokens_per_ticket"], 2000000)
        self.assertIsNone(config["execution"]["max_cost_microusd_per_ticket"])
        self.assertIsNone(config["execution"]["daily_project_cost_microusd"])
        self.assertEqual(config["execution"]["model_routing"]["budgets"]["max_tokens_per_ticket"], 2000000)
        self.assertEqual(config["execution"]["model_routing"]["budgets"]["max_tokens_per_project_day"], 30000000)
        self.assertEqual({v["reviewer_identity"] for v in config["specialist_reviews"].values()}, {"@custom"})
        report = inspect_config(config, load(ROOT / ".agentic/workflow.yaml"), Contracts(ROOT / ".agentic/schemas"))
        self.assertEqual(report["status"], "ACCEPTED", report)
        self.assertEqual(report["ci_gate"], "NOT_CONFIGURED")

    def test_new_adoption_budget_defaults(self):
        config = adoption.prepare_config(template(), overrides=explicit(), discover=False)["config"]
        self.assertEqual(2000000, config["execution"]["max_tokens_per_ticket"])
        self.assertEqual(16, config["execution"]["max_agent_runs_per_ticket"])
        self.assertIsNone(config["execution"]["max_cost_microusd_per_ticket"])
        self.assertIsNone(config["execution"]["daily_project_cost_microusd"])
        self.assertEqual({
            "max_runs_per_ticket": 16,
            "max_runs_per_project_day": 250,
            "max_tokens_per_ticket": 2000000,
            "max_tokens_per_project_day": 30000000,
            "max_cost_microusd_per_ticket": None,
            "max_cost_microusd_per_project_day": None,
        }, config["execution"]["model_routing"]["budgets"])

    def test_new_projects_never_reuse_a_source_template_uuid(self):
        source = template()
        source_id = str(uuid.uuid4())
        source["project"]["id"] = source_id
        first = adoption.prepare_config(source, overrides=explicit(), discover=False)["project_id"]
        second = adoption.prepare_config(source, overrides=explicit(), discover=False)["project_id"]
        self.assertEqual(len({source_id, first, second}), 3)
        self.assertEqual(uuid.UUID(first).version, 4)
        self.assertEqual(uuid.UUID(second).version, 4)
        self.assertEqual(source["project"]["id"], source_id)

    def test_no_gh_leaves_only_repository_id_when_origin_head_and_tests_exist(self):
        metadata = {"repository": "fixture/widget", "repository_id": None, "base_branch": "trunk"}
        with patch.object(adoption, "discover_repository", return_value=metadata), patch.object(adoption, "detect_test_command", return_value="pytest"):
            config = adoption.prepare_config(template(), project_root=ROOT)["config"]
        report = inspect_config(config, load(ROOT / ".agentic/workflow.yaml"), Contracts(ROOT / ".agentic/schemas"))
        self.assertEqual({item["path"] for item in report["unresolved"]}, {"$.github.repository_id"})
        self.assertEqual(report["unresolved"][0]["flag"], "--repository-id")

    def test_unknown_branch_is_not_assumed_main(self):
        config = adoption.prepare_config(template(), overrides={"repository": "fixture/widget"}, discover=False)["config"]
        self.assertIsNone(config["github"]["repository_id"])
        self.assertIsNone(config["github"]["base_branch"])
        self.assertEqual(config["validation"]["commands"], [])

    def test_malformed_existing_policy_is_preserved_with_precise_residue(self):
        original = template()
        original["validation"] = None
        value = adoption.prepare_config(template(), existing=original, discover=False)
        self.assertEqual(value["config"], original)
        report = inspect_config(value["config"], load(ROOT / ".agentic/workflow.yaml"), Contracts(ROOT / ".agentic/schemas"))
        self.assertEqual(report["status"], "REJECTED")
        self.assertIn("$.validation", {item["path"] for item in report["unresolved"]})

    def test_unbound_legacy_source_example_repository_id_is_not_preserved(self):
        original = template()
        original["github"].update(repository="CHANGE_ME/CHANGE_ME", repository_id=101)
        value = adoption.prepare_config(template(), existing=original, overrides={"repository": "fixture/widget"}, discover=False)
        self.assertIsNone(value["config"]["github"]["repository_id"])

    def test_existing_choices_scope_and_uuid_are_preserved(self):
        original = adoption.prepare_config(template(), overrides=explicit(), discover=False)["config"]
        original["jira"].update(enabled=True, site="https://fixture.atlassian.net", project_key="FIX")
        original["jira"]["scope"]["labels_any"] = ["approved-subset"]
        original["execution"]["max_parallel_tickets"] = 2
        original["project"]["name"] = "Reviewed name"
        with patch.object(adoption, "discover_repository", side_effect=AssertionError("accepted identities need no lookup")):
            value = adoption.prepare_config(template(), existing=original, receipt_project_id=original["project"]["id"],
                overrides={**explicit(), "name": "Other", "jira_key": "OTHER"}, project_root=ROOT)
        self.assertEqual(value["config"], original)
        self.assertTrue(value["warnings"])

    def test_receipt_uuid_mismatch_rejected(self):
        original = adoption.prepare_config(template(), overrides=explicit(), discover=False)["config"]
        with self.assertRaisesRegex(ValidationError, "UUID disagrees"):
            adoption.prepare_config(template(), existing=original, receipt_project_id=str(uuid.uuid4()))

    def test_jira_partial_pair_reports_exact_missing_path_without_scope_broadening(self):
        config = adoption.prepare_config(template(), overrides={**explicit(), "jira_site": "https://fixture.atlassian.net"}, discover=False)["config"]
        self.assertTrue(config["jira"]["enabled"])
        self.assertFalse(config["jira"]["scope"]["allow_entire_project"])
        report = inspect_config(config, load(ROOT / ".agentic/workflow.yaml"), Contracts(ROOT / ".agentic/schemas"))
        self.assertIn("$.jira.project_key", {item["path"] for item in report["unresolved"]})

    def test_explicit_id_disagrees_with_observation(self):
        with patch.object(adoption, "discover_repository", return_value={"repository_id": 99999, "base_branch": "trunk"}):
            with self.assertRaisesRegex(ValidationError, "conflicts with observed"):
                adoption.prepare_config(template(), project_root=ROOT, overrides={key: value for key, value in explicit().items() if key != "base_branch"})

    def test_complete_explicit_metadata_does_not_query_network(self):
        with patch.object(adoption, "discover_repository", side_effect=AssertionError("no discovery needed")):
            value = adoption.prepare_config(template(), project_root=ROOT, overrides=explicit())
        self.assertEqual(value["config"]["github"]["repository_id"], 54321)

    def test_preserved_id_disagrees_with_later_metadata(self):
        original = adoption.prepare_config(template(), overrides=explicit(), discover=False)["config"]
        original["github"]["base_branch"] = None
        with patch.object(adoption, "discover_repository", return_value={"repository_id": 99999, "base_branch": "trunk"}):
            with self.assertRaisesRegex(ValidationError, "Preserved repository_id conflicts"):
                adoption.prepare_config(template(), existing=original, project_root=ROOT)

    def test_origin_url_guards(self):
        for value in ("https://github.com/fixture/widget.git", "git@github.com:fixture/widget.git", "ssh://git@github.com/fixture/widget.git"):
            self.assertEqual(adoption.origin_repository(value), "fixture/widget")
        for value in ("https://secret@github.com/fixture/widget", "https://github.com/fixture/widget?token=secret", "https://evil.invalid/fixture/widget", "git@github.com:../widget"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                adoption.origin_repository(value)

    def test_metadata_commands_are_read_only_and_identity_bound(self):
        calls = []
        def command(argv, root, deadline):
            calls.append(argv)
            if "rev-parse" in argv:
                return str(root)
            if "get-url" in argv:
                return "git@github.com:fixture/widget.git"
            if "symbolic-ref" in argv:
                return "refs/remotes/origin/trunk"
            return '{"id":54321,"full_name":"fixture/widget","default_branch":"trunk"}'
        with patch.object(adoption, "_executable", side_effect=lambda name, root: name), patch.object(adoption, "_read_command", side_effect=command):
            observed = adoption.discover_repository(ROOT)
        self.assertEqual(observed["repository_id"], 54321)
        self.assertEqual(len(calls), 4)
        self.assertIn("GET", calls[-1])
        self.assertFalse(any("test" in call for call in calls))

    def test_ancestor_repository_metadata_never_becomes_target_identity(self):
        with patch.object(adoption, "_executable", side_effect=lambda name, root: name), patch.object(adoption, "_read_command", return_value=str(ROOT.parent)) as read:
            observed = adoption.discover_repository(ROOT)
        self.assertIsNone(observed["repository"])
        self.assertIsNone(observed["repository_id"])
        self.assertIsNone(observed["base_branch"])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args[0][-2:], ["rev-parse", "--show-toplevel"])

    def test_wrong_gh_identity_is_not_assigned(self):
        with patch.object(adoption, "_executable", side_effect=lambda name, root: "gh" if name == "gh" else None), patch.object(adoption, "_read_command", return_value='{"id":1,"full_name":"other/repo","default_branch":"main"}'):
            observed = adoption.discover_repository(ROOT, "fixture/widget")
        self.assertIsNone(observed["repository_id"])
        self.assertTrue(observed["warnings"])

    def test_static_test_detection_prefers_explicit_package_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "package.json").write_text('{"scripts":{"test":"would-run-a-side-effect"}}')
            with patch.object(subprocess, "Popen", side_effect=AssertionError("tests must not execute")):
                self.assertEqual(adoption.detect_test_command(root), "npm test")
                (root / "package.json").unlink()
                self.assertEqual(adoption.detect_test_command(root), "pytest")


class OperatingAdoptionProposalTests(unittest.TestCase):
    def legacy(self):
        config = adoption.prepare_config(template(), overrides=explicit(), discover=False)["config"]
        config["execution"]["max_parallel_tickets"] = 3
        config["execution"]["independent_reviewers"] = {"allocation": "one_per_stream", "count": 3}
        return config

    def test_ordinary_adoption_offers_exact_changes_without_applying(self):
        original = self.legacy()
        proposed, report = adoption.operating_capacity_proposal(original, existing=True)
        self.assertEqual(proposed, original)
        self.assertEqual(report["status"], "OFFERED")
        self.assertEqual([(row["current"], row["proposed"]) for row in report["values"]],
                         [(3, 6), (3, "derived: one per operating stream")])
        self.assertFalse(report["staged_locally"])
        self.assertFalse(report["execution_authority"])
        self.assertIn("--propose-operating-capacity", report["adoption_pr_section"])

    def test_explicit_choice_changes_only_two_governance_fields(self):
        original = self.legacy()
        proposed, report = adoption.operating_capacity_proposal(original, existing=True, requested=True)
        self.assertEqual(report["status"], "STAGED_FOR_REVIEW")
        self.assertEqual(proposed["execution"]["max_parallel_tickets"], 6)
        self.assertNotIn("count", proposed["execution"]["independent_reviewers"])
        proposed["execution"]["max_parallel_tickets"] = 3
        proposed["execution"]["independent_reviewers"]["count"] = 3
        self.assertEqual(proposed, original)

    def test_higher_accepted_ceiling_and_other_policy_are_preserved(self):
        original = self.legacy()
        original["execution"]["max_parallel_tickets"] = 9
        proposed, report = adoption.operating_capacity_proposal(original, existing=True, requested=True)
        self.assertEqual(proposed["execution"]["max_parallel_tickets"], 9)
        self.assertEqual(len(report["changes"]), 1)
        self.assertEqual(report["changes"][0]["action"], "remove")

    def test_current_default_needs_no_change_and_new_project_flag_refuses(self):
        original = template()
        proposed, report = adoption.operating_capacity_proposal(original, existing=True, requested=True)
        self.assertEqual(proposed, original)
        self.assertEqual(report["status"], "NOT_NEEDED")
        self.assertFalse(report["staged_locally"])
        with self.assertRaisesRegex(ValidationError, "existing project governance"):
            adoption.operating_capacity_proposal(original, existing=False, requested=True)

    def test_invalid_governance_is_not_repaired_by_proposal(self):
        for changes in ({"max_parallel_tickets": True}, {"independent_reviewers": {"count": "3"}}):
            original = self.legacy()
            original["execution"].update(changes)
            proposed, report = adoption.operating_capacity_proposal(original, existing=True)
            self.assertEqual(proposed, original)
            self.assertEqual(report["status"], "UNAVAILABLE")
            with self.assertRaisesRegex(ValidationError, r"\$\.execution"):
                adoption.operating_capacity_proposal(original, existing=True, requested=True)

    def test_ignore_merge_preserves_raw_bytes_and_appends_after_negations(self):
        block = (ROOT / GITIGNORE_TEMPLATE).read_bytes()
        for original in (b"project-output/\r\n!*.tmp", b"# Owner policy\n", b""):
            merged = merge_operating_ignores(original, block)
            self.assertTrue(merged.startswith(original))
            self.assertTrue(merged.endswith(block))
            self.assertEqual(merge_operating_ignores(merged, block), merged)
        self.assertEqual(merge_operating_ignores(None, block), block)
        self.assertNotIn(b".agentic-state/\n", block)
        self.assertNotIn(b".agentic-state/operating/changes/\n", block)

    def test_fresh_and_upgrade_ignore_plans_include_canonical_runtime_without_installing(self):
        block = (ROOT / GITIGNORE_TEMPLATE).read_bytes()
        runtime_rule = b".agentic/.venv/"
        legacy_block = block.replace(runtime_rule + b"\n", b"")
        for name, existing, status in (("fresh", None, "SEEDED"),
                                       ("upgrade", legacy_block, "MERGED")):
            with self.subTest(name=name):
                proposed, report = operating_ignore_plan(existing, block)
                self.assertEqual(status, report["status"])
                self.assertIn(runtime_rule, proposed.splitlines())
                self.assertIn(runtime_rule.decode("ascii"), report["added_lines"])


class PostInstallCheckTests(unittest.TestCase):
    def setUp(self):
        self.installation = {"status": "INSTALLED", "source_manifest_sha256": "a" * 64,
                             "configuration": {"status": "ACCEPTED", "policy_sha256": "b" * 64},
                             "operating": {"status": "ACCEPTED", "hash": "d" * 64}}
        preflight = patch.object(adoption, "_verify_import_surface")
        preflight.start()
        self.addCleanup(preflight.stop)

    def response(self, output, code=0):
        return {"command": [], "exit_code": code, "output": output, "diagnostic": None}

    def test_both_actual_command_vectors_and_policy_binding(self):
        responses = [self.response({"integrity_valid": True, "source_manifest_sha256": "a" * 64}),
                     self.response({"status": "ACCEPTED", "policy_sha256": "b" * 64, "unresolved": [],
                                    "operating": {"status": "ACCEPTED", "hash": "d" * 64}})]
        with patch.object(adoption, "_post_command", side_effect=responses) as run:
            result = adoption.post_install_checks(ROOT, self.installation)
        self.assertEqual(result["status"], "CONFIGURED")
        self.assertEqual([item.args[0][-1] for item in run.call_args_list], ["verify-installation", "validate-config"])
        from agentic.runtime_commands import installed_paths
        _root, interpreter, entry_point = installed_paths(ROOT)
        for item in run.call_args_list:
            self.assertEqual(item.args[0][:4], [str(interpreter), "-B", "-I", str(entry_point)])
            self.assertEqual(item.args[0][4:6], ["--root", str(ROOT.resolve())])
        self.assertFalse(result["active"])

    def test_missing_or_mismatched_operating_check_cannot_report_configured(self):
        for operating in ({}, {"status": "REJECTED", "hash": "d" * 64},
                          {"status": "ACCEPTED", "hash": "e" * 64}):
            with self.subTest(operating=operating), patch.object(adoption, "_post_command", side_effect=[
                    self.response({"integrity_valid": True, "source_manifest_sha256": "a" * 64}),
                    self.response({"status": "ACCEPTED", "policy_sha256": "b" * 64, "unresolved": [], "operating": operating})]):
                self.assertEqual(adoption.post_install_checks(ROOT, self.installation)["status"], "INSTALLED_UNCONFIGURED")

    def test_exit_zero_wrong_integrity_and_hash_never_configured(self):
        for output in ({"integrity_valid": "true"}, {"integrity_valid": True}, {"integrity_valid": True, "source_manifest_sha256": "c" * 64}, {}):
            with self.subTest(output=output), patch.object(adoption, "_post_command", side_effect=[self.response(output), self.response({"status": "ACCEPTED", "policy_sha256": "b" * 64})]) as run:
                result = adoption.post_install_checks(ROOT, self.installation)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(result["status"], "INSTALLATION_VERIFICATION_FAILED")
            self.assertFalse(result["installed"])

    def test_rejected_or_mismatched_policy_remains_installed_unconfigured(self):
        for response in (self.response({"status": "REJECTED"}, 2), self.response({"status": "ACCEPTED", "policy_sha256": "c" * 64}), self.response({"status": "ACCEPTED"}, 1)):
            with patch.object(adoption, "_post_command", side_effect=[self.response({"integrity_valid": True, "source_manifest_sha256": "a" * 64}), response]):
                self.assertEqual(adoption.post_install_checks(ROOT, self.installation)["status"], "INSTALLED_UNCONFIGURED")

    def test_actual_rejected_configuration_residue_replaces_stale_inspection(self):
        rejected = {"status": "REJECTED", "unresolved": [{"path": "$.validation.commands", "reason": "missing", "flag": "--test-command"}], "policy_sha256": None}
        with patch.object(adoption, "_post_command", side_effect=[self.response({"integrity_valid": True, "source_manifest_sha256": "a" * 64}), self.response(rejected, 2)]):
            result = adoption.post_install_checks(ROOT, self.installation)
        self.assertEqual(result["configuration"]["status"], "REJECTED")
        self.assertEqual(result["configuration"]["unresolved"], rejected["unresolved"])
        self.assertIn("$.validation.commands", result["next_action"])
        self.assertEqual(result["pre_install_configuration"]["status"], "ACCEPTED")

    def test_missing_child_output_names_runtime_correction_not_empty_paths(self):
        with patch.object(adoption, "_post_command", side_effect=[self.response({"integrity_valid": True, "source_manifest_sha256": "a" * 64}), self.response(None, 3)]):
            result = adoption.post_install_checks(ROOT, self.installation)
        self.assertEqual(result["configuration"]["status"], "UNOBSERVED")
        self.assertIn("runtime/dependencies", result["next_action"])

    def test_real_child_json_and_rejection_stderr_are_parsed(self):
        for text, code, stream in (("{\"status\":\"ACCEPTED\"}", 0, "stdout"), ("{\"status\":\"REJECTED\"}", 2, "stderr")):
            script = "import sys; print(" + repr(text) + ",file=sys." + stream + ");sys.exit(" + str(code) + ")"
            result = adoption._post_command([sys.executable, "-B", "-c", script], ROOT)
            self.assertEqual(result["exit_code"], code)
            self.assertEqual(result["output"]["status"], "ACCEPTED" if code == 0 else "REJECTED")
            self.assertEqual(result["output_stream"], stream)

    def test_malformed_duplicate_oversized_and_timeout_responses_are_bounded(self):
        for script in ("print('not JSON')", "print('{\"a\":1,\"a\":2}')", "print('x'*1048578)", "import time;time.sleep(5)"):
            result = adoption._post_command([sys.executable, "-B", "-c", script], ROOT, timeout=.2)
            self.assertIsNone(result["output"])
            self.assertIsNotNone(result["diagnostic"])


class RuntimeWheelArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-wheel-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wheelhouse = self.root / "wheelhouse"
        self.wheelhouse.mkdir()
        self.lock = self.root / "requirements.lock"

    def inspect(self, raw, *, name="fixture-dep", version="1.0", lock_raw=None):
        (self.wheelhouse / f"{name}-{version}-py3-none-any.whl").write_bytes(raw)
        self.lock.write_bytes(lock_raw or wheel_lock([(name, version, raw)]))
        requirements, _digest = adoption._locked_dependencies(self.lock)
        return adoption._load_locked_wheels(self.wheelhouse, requirements)

    def test_complete_wheel_bytes_are_bound_to_an_allowed_lock_hash(self):
        raw = synthetic_wheel()
        artifacts = self.inspect(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), artifacts["fixture-dep"]["artifact_sha256"])
        arbitrary = b"fixture-dep==1.0 \\\n    --hash=sha256:" + b"0" * 64 + b"\n"
        with self.assertRaisesRegex(ValidationError, "artifact SHA-256 is absent"):
            self.inspect(raw, lock_raw=arbitrary)

    def test_forged_metadata_record_and_exact_version_are_rejected(self):
        cases = [
            (synthetic_wheel(metadata_name="different-dep"), "absent from requirements.lock"),
            (synthetic_wheel(metadata_version="2.0"), "exact version identity"),
            (synthetic_wheel(corrupt_record="hash"), "RECORD hash or size mismatch"),
            (synthetic_wheel(corrupt_record="inventory"), "RECORD inventory"),
        ]
        for index, (raw, message) in enumerate(cases):
            with self.subTest(message=message):
                (self.wheelhouse / f"case-{index}.whl").write_bytes(raw)
                self.lock.write_bytes(wheel_lock([("fixture-dep", "1.0", raw)]))
                requirements, _digest = adoption._locked_dependencies(self.lock)
                with self.assertRaisesRegex(ValidationError, message):
                    adoption._load_locked_wheels(self.wheelhouse, requirements)
                (self.wheelhouse / f"case-{index}.whl").unlink()

    def test_startup_hooks_are_rejected_without_execution(self):
        marker = self.root / "executed"
        hook = ("open(" + repr(str(marker)) + ", 'w').write('executed')\n").encode("utf-8")
        for path in ("fixture_hook.pth", "sitecustomize.py", "pkg/usercustomize.py"):
            with self.subTest(path=path):
                raw = synthetic_wheel(additions={path: hook})
                with self.assertRaisesRegex(ValidationError, "prohibited Python startup hook"):
                    self.inspect(raw)
                self.assertFalse(marker.exists())
                for artifact in self.wheelhouse.iterdir():
                    artifact.unlink()

    def test_unsafe_archive_paths_and_link_members_are_rejected(self):
        cases = [
            (synthetic_wheel(additions={"../escape.py": b"escape\n"}), "unsafe|non-canonical"),
            (synthetic_wheel(additions={"linked.py": b"target"}, special_member="linked.py"), "link or special"),
        ]
        for index, (raw, message) in enumerate(cases):
            with self.subTest(message=message):
                (self.wheelhouse / f"case-{index}.whl").write_bytes(raw)
                self.lock.write_bytes(wheel_lock([("fixture-dep", "1.0", raw)]))
                requirements, _digest = adoption._locked_dependencies(self.lock)
                with self.assertRaisesRegex(ValidationError, message):
                    adoption._load_locked_wheels(self.wheelhouse, requirements)
                (self.wheelhouse / f"case-{index}.whl").unlink()

    def test_linked_wheelhouse_and_artifact_are_rejected(self):
        raw = synthetic_wheel()
        real = self.wheelhouse / "real.whl"
        real.write_bytes(raw)
        linked_artifact = self.wheelhouse / "linked.whl"
        try:
            linked_artifact.symlink_to(real)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.lock.write_bytes(wheel_lock([("fixture-dep", "1.0", raw)]))
        requirements, _digest = adoption._locked_dependencies(self.lock)
        with self.assertRaisesRegex(ValidationError, "only regular .whl"):
            adoption._load_locked_wheels(self.wheelhouse, requirements)
        linked_artifact.unlink()
        hardlinked_artifact = self.wheelhouse / "hardlinked.whl"
        try:
            os.link(real, hardlinked_artifact)
        except OSError:
            pass
        else:
            with self.assertRaisesRegex(ValidationError, "only regular .whl"):
                adoption._load_locked_wheels(self.wheelhouse, requirements)
            hardlinked_artifact.unlink()
        real.unlink()
        real_house = self.root / "real-house"
        real_house.mkdir()
        (real_house / "fixture.whl").write_bytes(raw)
        linked_house = self.root / "linked-house"
        linked_house.symlink_to(real_house, target_is_directory=True)
        with self.assertRaisesRegex(ValidationError, "real directory"):
            adoption._load_locked_wheels(linked_house, requirements)


class ConfiguredInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-config-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source, self.dest = self.base / "source", self.base / "project"
        self.source.mkdir()
        paths = [ROOT / "AGENTS.md", ROOT / ".agentic/workflow.yaml",
                 ROOT / ".agentic/scripts/workflow.py", ROOT / ".agentic/requirements.lock"]
        # Include package modules recursively: provider adapters are part of the
        # installed runtime, not optional test-only dependencies.
        paths += list((ROOT / ".agentic/lib/agentic").rglob("*.py"))
        paths += list((ROOT / ".agentic/schemas").glob("*.json"))
        self.files = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in paths}
        self.files[CONFIG] = (ROOT / ".agentic/examples/unconfigured-project.yaml").read_bytes()
        self.files[".github/CODEOWNERS"] = b"# Synthetic source ownership\n/.agentic/ @maintainer\n"
        self.files[GITIGNORE_TEMPLATE] = (ROOT / GITIGNORE_TEMPLATE).read_bytes()
        self.files[installer.KNOWN_VERSIONS] = (ROOT / installer.KNOWN_VERSIONS).read_bytes()
        for name, raw in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        manifest = json_bytes({"format": "awf-manifest-1", "template_version": VERSION,
                              "files": {name: sha256(raw) for name, raw in self.files.items()}})
        (self.source / "MANIFEST.json").write_bytes(manifest)
        self.pin = sha256(manifest)

    def prepare_runtime_source(self):
        wheelhouse = self.base / "wheelhouse"
        wheelhouse.mkdir()
        entries = []
        for name in ("PyYAML", "jsonschema", "attrs", "jsonschema-specifications",
                     "referencing", "rpds-py", "typing-extensions"):
            version, raw = installed_wheel(name)
            filename = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
            (wheelhouse / filename).write_bytes(raw)
            entries.append((name, version, raw))
        lock = wheel_lock(entries)
        self.files[".agentic/requirements.lock"] = lock
        (self.source / ".agentic/requirements.lock").write_bytes(lock)
        manifest = json_bytes({"format": "awf-manifest-1", "template_version": VERSION,
                              "files": {name: sha256(raw) for name, raw in self.files.items()}})
        (self.source / "MANIFEST.json").write_bytes(manifest)
        self.pin = sha256(manifest)
        return wheelhouse

    def perform(self, **kwargs):
        return install(self.source, self.dest, self.pin, configure=True, discover=False, overrides=explicit(), **kwargs)

    def test_same_version_receipt_project_uuid_and_raw_config_survive_upgrade(self):
        first = self.perform()
        config_path = self.dest / CONFIG
        raw = config_path.read_bytes()
        config_path.write_bytes(b"\n" + raw)
        second = self.perform(mode="upgrade")
        self.assertEqual(config_path.read_bytes(), b"\n" + raw)
        receipt = json.loads((self.dest / INSTALLED).read_bytes())
        self.assertEqual(receipt["project_id"], json.loads(raw)["project"]["id"])
        self.assertEqual(first["install_id"], second["install_id"])
        self.assertEqual(sha256(receipt["source_manifest_json"].encode()), self.pin)
        self.assertEqual(verify_installed(self.dest), self.pin)

    @unittest.skipUnless(AWF_SOURCE_REPOSITORY, "needs AWF source-repository files absent from installed projects")
    def test_192_upgrade_changes_only_version_line_in_owner_config(self):
        from upgrade_fixtures import materialize
        materialize("1.9.2", self.dest)
        receipt_path = self.dest / INSTALLED
        receipt = json.loads(receipt_path.read_bytes())
        old_install_id = receipt["install_id"]
        legacy_agents = (self.dest / "AGENTS.md").read_bytes()
        self.assertNotEqual(legacy_agents, self.files["AGENTS.md"])
        config_path = self.dest / CONFIG
        config = load_yaml(config_path.read_bytes())
        config["template"]["expected_workflow_version"] = "1.9.2"
        config["execution"].update(max_tokens_per_ticket=100000,
                                   max_cost_microusd_per_ticket=2500000,
                                   daily_project_cost_microusd=9000000)
        config["execution"]["model_routing"]["budgets"].update(
            max_tokens_per_ticket=100000,
            max_tokens_per_project_day=400000,
            max_cost_microusd_per_ticket=2400000,
            max_cost_microusd_per_project_day=8500000)
        text = json.dumps(config, indent=2, ensure_ascii=False)
        text = text.replace('  "execution": {', '  # owner budget policy\n  "execution": {')
        before = (text + "\n").replace("\n", "\r\n").encode("utf-8")
        config_path.write_bytes(before)
        # MIGRATION-to-v1.9.3.md: "The migration inserts null immutable Jira
        # bindings where absent, preserving owner values." Every other owner
        # byte, including comments and CRLF endings, must be unchanged.
        jira_anchor = b'  "jira": {\r\n    "enabled": true,\r\n'
        self.assertEqual(before.count(jira_anchor), 1)
        null_bindings = (b'    "cloud_id": null,\r\n    "provider_project_id": null,\r\n'
                         b'    "controller_actor_id": null,\r\n')
        expected = before.replace(b'"expected_workflow_version": "1.9.2"',
                                  b'"expected_workflow_version": "1.9.3"'
                                  ).replace(jira_anchor, jira_anchor + null_bindings)

        second = self.perform(mode="upgrade")

        after = config_path.read_bytes()
        self.assertEqual(after, expected)
        removed = [line for line in before.splitlines(keepends=True) if line not in after.splitlines(keepends=True)]
        self.assertEqual(removed, [b'    "expected_workflow_version": "1.9.2"\r\n'])
        current = json.loads(after.replace(b'  # owner budget policy\r\n', b''))
        self.assertEqual(current["execution"]["max_tokens_per_ticket"], 100000)
        self.assertEqual(current["execution"]["max_cost_microusd_per_ticket"], 2500000)
        self.assertEqual(current["execution"]["model_routing"]["budgets"]["max_tokens_per_ticket"], 100000)
        self.assertEqual((self.dest / "AGENTS.md").read_bytes(), self.files["AGENTS.md"])
        self.assertEqual(old_install_id, second["install_id"])
        self.assertEqual(verify_installed(self.dest), self.pin)

    @unittest.skipUnless(AWF_SOURCE_REPOSITORY, "needs AWF source-repository files absent from installed projects")
    def test_192_pure_migration_changes_only_version_line(self):
        from types import MappingProxyType
        from agentic.upgrade import MigrationBundle, load_known_versions, migrate_1_9_2_to_1_9_3
        from upgrade_fixtures import fixture_blob, fixture_manifest
        config = json.loads((ROOT / ".agentic/examples/unconfigured-project.yaml").read_bytes())
        config["template"]["expected_workflow_version"] = "1.9.2"
        config["execution"].update(max_tokens_per_ticket=100000,
                                   max_cost_microusd_per_ticket=2500000,
                                   daily_project_cost_microusd=9000000)
        config["execution"]["model_routing"]["budgets"].update(
            max_tokens_per_ticket=100000,
            max_tokens_per_project_day=400000,
            max_cost_microusd_per_ticket=2400000,
            max_cost_microusd_per_project_day=8500000)
        text = json.dumps(config, indent=2, ensure_ascii=False)
        text = text.replace('  "execution": {', '  # owner budget policy\n  "execution": {')
        before = (text + "\n").replace("\n", "\r\n").encode("utf-8")
        expected = before.replace(b'"expected_workflow_version": "1.9.2"',
                                  b'"expected_workflow_version": "1.9.3"')
        fixture = fixture_manifest("1.9.2")
        receipt = fixture_blob(fixture["receipt"]["sha256"])
        provenance = fixture_blob(fixture["owner_files"][installer.PROVENANCE]["sha256"])
        source_manifest = json.loads((self.source / "MANIFEST.json").read_bytes())
        target = {"source_manifest_sha256": self.pin,
                  "source_manifest_json": (self.source / "MANIFEST.json").read_text(encoding="utf-8"),
                  "_immutable_files": {path: digest for path, digest in source_manifest["files"].items()
                                       if installer.managed(path) and path not in {CONFIG, installer.PROVENANCE,
                                                                                ".github/CODEOWNERS"}}}
        after = migrate_1_9_2_to_1_9_3(
            MigrationBundle(before, None, receipt, provenance, MappingProxyType({})), target).project_config
        self.assertEqual(after, expected)
        changed_lines = [(old, new) for old, new in zip(before.splitlines(keepends=True), after.splitlines(keepends=True))
                         if old != new]
        self.assertEqual(changed_lines, [
            (b'    "expected_workflow_version": "1.9.2"\r\n',
             b'    "expected_workflow_version": "1.9.3"\r\n')])
        current = json.loads(after.replace(b'  # owner budget policy\r\n', b''))
        self.assertEqual(current["execution"]["max_tokens_per_ticket"], 100000)
        self.assertEqual(current["execution"]["max_cost_microusd_per_ticket"], 2500000)
        self.assertEqual(current["execution"]["model_routing"]["budgets"]["max_tokens_per_ticket"], 100000)

    def test_legacy_upgrade_offer_dryrun_and_explicit_local_proposal(self):
        self.perform()
        path = self.dest / CONFIG
        legacy = json.loads(path.read_bytes())
        legacy["execution"]["max_parallel_tickets"] = 3
        legacy["execution"]["independent_reviewers"]["count"] = 3
        raw = b"\n" + json_bytes(legacy)
        path.write_bytes(raw)
        operating = (self.dest / "OPERATING_CONFIG.yaml").read_bytes()
        ordinary = self.perform(mode="upgrade")
        self.assertEqual(ordinary["governance_proposal"]["status"], "OFFERED")
        self.assertEqual(path.read_bytes(), raw)
        planned = self.perform(mode="upgrade", dry_run=True, propose_operating_capacity=True)
        self.assertEqual(planned["governance_proposal"]["status"], "PLANNED_FOR_REVIEW")
        self.assertFalse(planned["governance_proposal"]["staged_locally"])
        self.assertEqual(path.read_bytes(), raw)
        actual = self.perform(mode="upgrade", propose_operating_capacity=True)
        self.assertEqual(actual["governance_proposal"]["status"], "STAGED_FOR_REVIEW")
        self.assertEqual(actual["configuration"]["status"], "ACCEPTED")
        current = json.loads(path.read_bytes())
        self.assertEqual(current["execution"]["max_parallel_tickets"], 6)
        self.assertNotIn("count", current["execution"]["independent_reviewers"])
        current["execution"]["max_parallel_tickets"] = 3
        current["execution"]["independent_reviewers"]["count"] = 3
        self.assertEqual(current, legacy)
        self.assertEqual((self.dest / "OPERATING_CONFIG.yaml").read_bytes(), operating)
        self.assertEqual(verify_installed(self.dest), self.pin)

    def test_ignore_install_merge_upgrade_and_mutable_receipt(self):
        self.dest.mkdir()
        path = self.dest / GITIGNORE
        original = b"# Project rules\r\noutput/\r\n!*.tmp"
        path.write_bytes(original)
        result = self.perform()
        merged = path.read_bytes()
        self.assertTrue(merged.startswith(original))
        self.assertTrue(merged.endswith(self.files[GITIGNORE_TEMPLATE]))
        self.assertEqual(result["gitignore"]["status"], "MERGED")
        receipt = json.loads((self.dest / INSTALLED).read_bytes())
        self.assertNotIn(GITIGNORE, receipt["immutable_files"])
        self.assertIn(GITIGNORE, receipt["mutable_paths"])
        self.perform(mode="upgrade")
        self.assertEqual(path.read_bytes(), merged)
        path.write_bytes(merged + b"owner-added-output/\n")
        self.assertEqual(verify_installed(self.dest), self.pin)

    def test_ignore_rollback_restores_project_bytes(self):
        self.dest.mkdir()
        path = self.dest / GITIGNORE
        original = b"owner-output/\r\n"
        path.write_bytes(original)
        planned = self.perform(dry_run=True)
        failure_position = planned["managed_files"].index(GITIGNORE) + 1
        with self.assertRaisesRegex(OSError, "Injected installation failure"):
            self.perform(fail_after=failure_position)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.dest / "AGENTS.md").exists())

    def test_ignore_edit_during_planning_is_preserved(self):
        self.dest.mkdir()
        path = self.dest / GITIGNORE
        path.write_bytes(b"owner-output/\n")
        concurrent = b"owner-added-during-plan/\n"
        def merge_and_edit(existing, required):
            path.write_bytes(concurrent)
            return merge_operating_ignores(existing, required)
        with patch.object(installer, "merge_operating_ignores", side_effect=merge_and_edit):
            with self.assertRaisesRegex(ValidationError, "Destination changed while preparing adoption"):
                self.perform()
        self.assertEqual(path.read_bytes(), concurrent)
        self.assertFalse((self.dest / "AGENTS.md").exists())

    def test_governance_edit_during_proposal_is_preserved(self):
        self.perform()
        path = self.dest / CONFIG
        original = path.read_bytes()
        concurrent = original + b"\n"
        proposal = adoption.operating_capacity_proposal
        def propose_and_edit(config, **kwargs):
            path.write_bytes(concurrent)
            return proposal(config, **kwargs)
        with patch.object(adoption, "operating_capacity_proposal", side_effect=propose_and_edit):
            with self.assertRaisesRegex(ValidationError, "Destination changed while preparing adoption"):
                self.perform(mode="upgrade", propose_operating_capacity=True)
        self.assertEqual(path.read_bytes(), concurrent)

    def test_receipt_cannot_remove_immutable_member(self):
        self.perform()
        path = self.dest / INSTALLED
        receipt = json.loads(path.read_bytes())
        del receipt["immutable_files"]["AGENTS.md"]
        path.write_bytes(json_bytes(receipt))
        with self.assertRaisesRegex(ValidationError, "membership"):
            verify_installed(self.dest)

    def test_current_receipt_cannot_downgrade_to_missing_source_manifest(self):
        self.perform()
        path = self.dest / INSTALLED
        receipt = json.loads(path.read_bytes())
        del receipt["source_manifest_json"]
        del receipt["immutable_files"]["AGENTS.md"]
        path.write_bytes(json_bytes(receipt))
        (self.dest / "AGENTS.md").write_bytes(b"Unverified replacement governance\n")
        with self.assertRaisesRegex(ValidationError, "requires source_manifest_json"):
            verify_installed(self.dest)

    def test_malformed_controller_rejects_before_managed_writes_with_path(self):
        self.dest.mkdir()
        path = self.dest / CONFIG
        path.parent.mkdir()
        original = template()
        original["controller"] = None
        raw = json_bytes(original)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValidationError, r"\$\.controller must be an object"):
            self.perform()
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.dest / "AGENTS.md").exists())

    def test_dry_run_missing_destination_writes_nothing(self):
        result = self.perform(dry_run=True)
        self.assertEqual(result["status"], "PLAN")
        self.assertFalse(result["installed"])
        self.assertFalse(self.dest.exists())

    def test_backed_up_reinstall_reuses_receipt_identity(self):
        self.perform()
        before = json.loads((self.dest / INSTALLED).read_bytes())
        self.perform(conflict="backup")
        after = json.loads((self.dest / INSTALLED).read_bytes())
        self.assertEqual(before["project_id"], after["project_id"])
        self.assertEqual(before["install_id"], after["install_id"])

    def test_real_installed_commands_confirm_accepted_configuration(self):
        wheelhouse = self.prepare_runtime_source()
        result = self.perform()
        runtime = adoption.ensure_installed_runtime(self.dest, wheelhouse)
        pyvenv = (self.dest / ".agentic/.venv/pyvenv.cfg").read_text(encoding="utf-8").lower()
        self.assertIn("include-system-site-packages = false", pyvenv)
        self.assertEqual(sha256((self.dest / ".agentic/requirements.lock").read_bytes()), runtime["requirements_lock_sha256"])
        self.assertEqual(7, len(runtime["artifact_sha256"]))
        self.assertIn("complete wheel SHA-256", runtime["dependency_source"])
        sentinel = self.base / "unlisted" / "awf_unlisted_sentinel.py"
        sentinel.parent.mkdir()
        sentinel.write_text("raise RuntimeError('executed')\n", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(sentinel.parent)
        isolated = subprocess.run([runtime["interpreter"], "-B", "-I", "-c", "import awf_unlisted_sentinel"],
                                  cwd=self.dest, env=env, capture_output=True, text=True)
        self.assertNotEqual(0, isolated.returncode)
        checked = adoption.post_install_checks(self.dest, result)
        self.assertEqual(checked["status"], "CONFIGURED", checked)
        self.assertEqual([item["exit_code"] for item in checked["post_install_checks"]], [0, 0])

    def test_prevalidated_wheel_bytes_survive_source_removal_and_bind_installed_lock(self):
        wheelhouse = self.prepare_runtime_source()
        prepared = adoption.prevalidate_runtime_wheelhouse(self.source, self.pin, wheelhouse)
        result = self.perform()
        for artifact in wheelhouse.iterdir():
            artifact.unlink()
        wheelhouse.rmdir()
        runtime = adoption.ensure_installed_runtime(self.dest, prepared_wheelhouse=prepared)
        self.assertTrue(Path(runtime["interpreter"]).is_file())
        self.assertEqual(sha256((self.dest / ".agentic/requirements.lock").read_bytes()),
                         runtime["requirements_lock_sha256"])
        lock_path = self.dest / ".agentic/requirements.lock"
        changed = lock_path.read_text(encoding="utf-8")
        offset = changed.index("--hash=sha256:") + len("--hash=sha256:")
        changed = changed[:offset] + ("0" if changed[offset] != "0" else "1") + changed[offset + 1:]
        lock_path.write_text(changed, encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "changed after wheelhouse prevalidation"):
            adoption.ensure_installed_runtime(self.dest, prepared_wheelhouse=prepared)

    def test_runtime_rejects_unhashed_or_version_mismatched_dependency_lock(self):
        wheelhouse = self.prepare_runtime_source()
        self.perform()
        lock = self.dest / ".agentic/requirements.lock"
        original = lock.read_text(encoding="utf-8")
        locked_version = adoption._locked_dependencies(lock)[0]["pyyaml"]["version"]
        lock.write_text(f"PyYAML=={locked_version}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "requires artifact hashes"):
            adoption.ensure_installed_runtime(self.dest, wheelhouse)
        lock.write_text(original.replace(f"PyYAML=={locked_version}", "PyYAML==0.0.0"), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "exact version identity"):
            adoption.ensure_installed_runtime(self.dest, wheelhouse)

    def test_runtime_failure_after_swap_restores_previous_good_runtime_and_cleans_transactions(self):
        wheelhouse = self.prepare_runtime_source()
        self.perform()
        adoption.ensure_installed_runtime(self.dest, wheelhouse)
        marker = self.dest / ".agentic/.venv/previous-good-runtime"
        marker.write_bytes(b"preserve me\n")
        validate = adoption._validate_runtime
        calls = 0
        def fail_final(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("injected final runtime validation failure")
            return validate(*args, **kwargs)
        with patch.object(adoption, "_validate_runtime", side_effect=fail_final):
            with self.assertRaisesRegex(ValidationError, "injected final"):
                adoption.ensure_installed_runtime(self.dest, wheelhouse)
        self.assertEqual(marker.read_bytes(), b"preserve me\n")
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.staging-*")))
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.backup-*")))

    def test_runtime_keyboard_interrupt_after_swap_restores_previous_good_runtime(self):
        wheelhouse = self.prepare_runtime_source()
        self.perform()
        adoption.ensure_installed_runtime(self.dest, wheelhouse)
        marker = self.dest / ".agentic/.venv/previous-good-runtime"
        marker.write_bytes(b"preserve on interrupt\n")
        validate = adoption._validate_runtime
        calls = 0
        def interrupt_final(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return validate(*args, **kwargs)
        with patch.object(adoption, "_validate_runtime", side_effect=interrupt_final):
            with self.assertRaises(KeyboardInterrupt):
                adoption.ensure_installed_runtime(self.dest, wheelhouse)
        self.assertEqual(marker.read_bytes(), b"preserve on interrupt\n")
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.staging-*")))
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.backup-*")))

    def test_runtime_stage_failure_leaves_no_partial_runtime_or_transaction_residue(self):
        wheelhouse = self.prepare_runtime_source()
        self.perform()
        with patch.object(adoption, "_validate_runtime",
                          side_effect=ValidationError("injected staged runtime validation failure")):
            with self.assertRaisesRegex(ValidationError, "injected staged"):
                adoption.ensure_installed_runtime(self.dest, wheelhouse)
        self.assertFalse((self.dest / ".agentic/.venv").exists())
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.staging-*")))
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.backup-*")))

    def test_runtime_success_atomically_replaces_previous_runtime_and_cleans_transactions(self):
        wheelhouse = self.prepare_runtime_source()
        self.perform()
        adoption.ensure_installed_runtime(self.dest, wheelhouse)
        marker = self.dest / ".agentic/.venv/replace-me"
        marker.write_bytes(b"old\n")
        runtime = adoption.ensure_installed_runtime(self.dest, wheelhouse)
        self.assertFalse(marker.exists())
        self.assertTrue(Path(runtime["interpreter"]).is_file())
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.staging-*")))
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.backup-*")))

    def test_runtime_cleanup_failure_after_commit_keeps_new_good_runtime_and_retries_cleanup(self):
        wheelhouse = self.prepare_runtime_source()
        self.perform()
        adoption.ensure_installed_runtime(self.dest, wheelhouse)
        marker = self.dest / ".agentic/.venv/old-runtime-marker"
        marker.write_bytes(b"old\n")
        remove = adoption._remove_runtime_tree
        failed = False
        def fail_first_backup_cleanup(path):
            nonlocal failed
            if ".venv.backup-" in Path(path).name and not failed:
                failed = True
                raise ValidationError("injected backup cleanup failure")
            return remove(path)
        with patch.object(adoption, "_remove_runtime_tree", side_effect=fail_first_backup_cleanup):
            with self.assertRaisesRegex(ValidationError, "injected backup cleanup"):
                adoption.ensure_installed_runtime(self.dest, wheelhouse)
        self.assertFalse(marker.exists())
        self.assertTrue((self.dest / ".agentic/.venv/pyvenv.cfg").is_file())
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.staging-*")))
        self.assertEqual([], list((self.dest / ".agentic").glob(".venv.backup-*")))

    def test_real_installed_commands_report_unconfigured_residue(self):
        wheelhouse = self.prepare_runtime_source()
        result = install(self.source, self.dest, self.pin, configure=True, discover=False,
                         overrides={key: value for key, value in explicit().items() if key != "repository_id"})
        adoption.ensure_installed_runtime(self.dest, wheelhouse)
        checked = adoption.post_install_checks(self.dest, result)
        self.assertEqual(checked["status"], "INSTALLED_UNCONFIGURED", checked)
        self.assertEqual([item["exit_code"] for item in checked["post_install_checks"]], [0, 2])
        self.assertEqual({item["path"] for item in result["configuration"]["unresolved"]}, {"$.github.repository_id"})

    def test_target_shadow_modules_and_caches_never_execute(self):
        result = self.perform()
        marker = self.base / "shadow-executed"
        for relative in (".agentic/scripts/pathlib.py", ".agentic/lib/json.py", ".agentic/lib/__pycache__/json.cpython-312.pyc"):
            with self.subTest(relative=relative):
                path = self.dest / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = ("open(" + repr(str(marker)) + ",'w').write('executed')\n").encode()
                path.write_bytes(raw)
                with patch.object(adoption, "_post_command", side_effect=AssertionError("untrusted imports must prevent any child")):
                    checked = adoption.post_install_checks(self.dest, result)
                self.assertEqual(checked["status"], "INSTALLATION_VERIFICATION_FAILED")
                self.assertFalse(checked["installed"])
                self.assertEqual([check["execution_status"] for check in checked["post_install_checks"]], ["NOT_RUN", "NOT_RUN"])
                self.assertFalse(marker.exists())
                self.assertEqual(path.read_bytes(), raw)
                path.unlink()


class RuntimeTransactionSelectorTests(unittest.TestCase):
    def test_outer_transaction_exclusively_owns_prior_runtime_backup_cleanup(self):
        self.assertTrue(adoption._builder_owns_backup_cleanup(None))
        self.assertFalse(adoption._builder_owns_backup_cleanup(object()))

    def test_recovery_marker_binds_exact_journal_and_one_precommitted_update(self):
        transaction_id = str(uuid.uuid4())
        original = installer.json_bytes({"transaction_id": transaction_id, "old": "trusted"})
        updated = installer.json_bytes({"transaction_id": transaction_id, "old": "trusted",
                                        "runtime": "staged"})
        marker = installer._journal_marker(transaction_id, sha256(original), sha256(updated))
        self.assertEqual(transaction_id, installer._validate_journal_marker(marker, original))
        self.assertEqual(transaction_id, installer._validate_journal_marker(marker, updated))
        altered = installer.json_bytes({"transaction_id": transaction_id, "old": "injected"})
        with self.assertRaisesRegex(ValidationError, "differs from its bound marker"):
            installer._validate_journal_marker(marker, altered)


class InstallerTransactionPureRegressionTests(unittest.TestCase):
    """Pure state-machine checks; no install/bootstrap/upgrade entry point runs."""

    class MemoryTree:
        def __init__(self, values=None):
            self.values = dict(values or {})
            self.root = Path("awf-pure-transaction-model")
            self.snapshots = []
            self.unlinks = []

        def inspect(self, path):
            return object() if path in self.values else None

        def read(self, path):
            return self.values[path]

        def write(self, path, data):
            self.values[path] = data
            self.snapshots.append(dict(self.values))

        def unlink(self, path):
            self.values.pop(path, None)
            self.unlinks.append(path)
            self.snapshots.append(dict(self.values))

    @staticmethod
    def journal(transaction_id, *, phase="active"):
        return {"format": "awf-install-journal-3", "transaction_id": transaction_id,
                "phase": phase, "files": [], "runtime": None,
                "managed_before": [], "managed_after": [] if phase != "active" else None}

    def test_v3_validator_accepts_old_and_authenticated_cleanup_phases_only(self):
        transaction_id = str(uuid.uuid4())
        for phase in ("active", "commit_cleanup", "commit_cleanup_authenticated"):
            with self.subTest(phase=phase):
                self.assertIsNotNone(installer._validate_transaction_journal(
                    self.journal(transaction_id, phase=phase)))
        invalid = self.journal(transaction_id, phase="commit_cleanup_authenticated")
        invalid["phase"] = "cleanup_unproved"
        with self.assertRaisesRegex(ValidationError, "Invalid recovery journal phase"):
            installer._validate_transaction_journal(invalid)

    def test_complete_inventory_proof_rejects_add_remove_rename_mode_bytes_and_type(self):
        expected = [{"path": ".agentic/a.txt", "mode": 0o600, "sha256": "1" * 64}]
        variants = {
            "addition": expected + [{"path": ".agentic/b.txt", "mode": 0o600, "sha256": "2" * 64}],
            "removal": [],
            "rename": [{"path": ".agentic/c.txt", "mode": 0o600, "sha256": "1" * 64}],
            "mode": [{"path": ".agentic/a.txt", "mode": 0o400, "sha256": "1" * 64}],
            "bytes": [{"path": ".agentic/a.txt", "mode": 0o600, "sha256": "2" * 64}],
        }
        for name, observed in variants.items():
            with self.subTest(name=name), patch.object(installer, "_managed_file_inventory", return_value=observed):
                with self.assertRaisesRegex(ValidationError, "Managed-file inventory changed"):
                    installer._verify_managed_inventory(object(), expected, "in pure proof")
        with patch.object(installer, "_managed_file_inventory",
                          side_effect=ValidationError("Managed inventory contains a non-regular file")):
            with self.assertRaisesRegex(ValidationError, "non-regular"):
                installer._verify_managed_inventory(object(), expected, "in pure proof")

    def test_every_operating_guide_command_uses_the_target_isolated_runtime(self):
        guide = (ROOT / ".agentic/docs/29-OPERATING-CONFIGURATION.md").read_text(encoding="utf-8")
        separator = chr(92)
        canonical = ("& '.agentic{0}.venv{0}Scripts{0}python.exe' -B -I "
                     "'.agentic{0}scripts{0}workflow.py' --root '.'").format(separator)
        command_lines = [line for line in guide.splitlines()
                         if any(f"operating {action}" in line for action in ("show", "set", "recommend"))]
        self.assertEqual(6, len(command_lines))
        for line in command_lines:
            self.assertIn(canonical, line)

    def test_create_update_and_delete_single_artifact_boundaries(self):
        transaction_id = str(uuid.uuid4())
        active = self.journal(transaction_id)
        tree = self.MemoryTree()
        installer._publish_initial_intent(tree, active)
        create_states = tree.snapshots[:2]
        self.assertEqual({installer.JOURNAL}, set(create_states[0]))
        self.assertEqual({installer.JOURNAL, installer.MARKER}, set(create_states[1]))
        journal_only = self.MemoryTree(create_states[0])
        with self.assertRaisesRegex(ValidationError, "must both be present"):
            installer._read_bound_journal(journal_only)
        marker_only = self.MemoryTree({installer.MARKER: create_states[1][installer.MARKER]})
        with self.assertRaisesRegex(ValidationError, "must both be present"):
            installer._read_bound_journal(marker_only)
        self.assertEqual("active", installer._read_bound_journal(tree)[0]["phase"])

        committed = self.journal(transaction_id, phase="commit_cleanup")
        installer._write_bound_journal_update(tree, committed)
        update_states = tree.snapshots[-3:]
        for index, state in enumerate(update_states):
            with self.subTest(update_boundary=index):
                self.assertIn(installer.JOURNAL, state)
                self.assertIn(installer.MARKER, state)
                candidate = self.MemoryTree(state)
                observed = installer._read_bound_journal(candidate)[0]
                self.assertEqual("active" if index == 0 else "commit_cleanup", observed["phase"])

        with patch.object(installer, "_managed_file_inventory", return_value=[]):
            installer._finalize_committed(tree, committed)
        self.assertEqual([installer.MARKER, installer.JOURNAL], tree.unlinks)
        self.assertIn(installer.JOURNAL, tree.snapshots[-2])
        self.assertNotIn(installer.MARKER, tree.snapshots[-2])
        self.assertEqual({}, tree.snapshots[-1])

    def test_journal_only_create_and_delete_endpoints_are_non_destructive(self):
        transaction_id = str(uuid.uuid4())
        for phase, expected in (("active", "ROLLED_BACK"),
                                ("commit_cleanup", "COMMITTED"),
                                ("commit_cleanup_authenticated", "COMMITTED")):
            journal = self.journal(transaction_id, phase=phase)
            tree = self.MemoryTree({installer.JOURNAL: installer.json_bytes(journal)})
            with self.subTest(phase=phase), patch.object(installer, "_managed_file_inventory", return_value=[]):
                self.assertEqual(expected, installer._finalize_journal_only(tree, journal))
                self.assertEqual({}, tree.values)
                self.assertEqual([installer.JOURNAL], tree.unlinks)

    def test_structurally_valid_journal_substitution_and_identity_change_are_rejected(self):
        transaction_id = str(uuid.uuid4())
        journal = self.journal(transaction_id)
        raw = installer.json_bytes(journal)
        marker = installer.json_bytes(installer._journal_marker(transaction_id, sha256(raw)))
        substituted = dict(journal, phase="commit_cleanup", managed_after=[])
        tree = self.MemoryTree({installer.JOURNAL: installer.json_bytes(substituted), installer.MARKER: marker})
        with self.assertRaisesRegex(ValidationError, "differs from its bound marker"):
            installer._read_bound_journal(tree)

        valid_tree = self.MemoryTree({installer.JOURNAL: raw, installer.MARKER: marker})
        changed_identity = self.journal(str(uuid.uuid4()), phase="commit_cleanup")
        with self.assertRaisesRegex(ValidationError, "identity changed"):
            installer._write_bound_journal_update(valid_tree, changed_identity)

    def test_authenticated_cleanup_failure_remains_pending_and_stable_success_finishes(self):
        transaction_id = str(uuid.uuid4())
        journal = self.journal(transaction_id, phase="commit_cleanup_authenticated")
        journal["runtime"] = {"path": installer.RUNTIME, "previous_sha256": "1" * 64,
                              "new_sha256": "2" * 64,
                              "stage": f".agentic/.venv.staging-{transaction_id}",
                              "backup": f".agentic/.venv.backup-{transaction_id}",
                              "cleanup_identity": ["0000000000000000", "0000000000000000"],
                              "cleanup_members": []}
        raw = installer.json_bytes(journal)
        tree = self.MemoryTree({installer.JOURNAL: raw, installer.MARKER: installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw)))})
        with patch.object(installer, "_remove_runtime_transaction_path",
                          side_effect=OSError("injected cleanup failure")):
            failures = installer._cleanup_committed_runtime(tree, journal)
        self.assertEqual(1, len(failures))
        self.assertEqual("OSError", failures[0]["error"])
        self.assertEqual({installer.JOURNAL, installer.MARKER}, set(tree.values))
        with patch.object(installer, "_remove_runtime_transaction_path") as remove:
            self.assertEqual([], installer._cleanup_committed_runtime(tree, journal))
        remove.assert_called_once_with(installer._runtime_cleanup_path(tree.root, journal["runtime"]))


class BootstrapRuntimeTransactionRegressionTests(unittest.TestCase):
    """Synthetic journal tests; no installer/bootstrap/upgrade entry point runs."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-bootstrap-transaction-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".agentic-install").mkdir()
        (self.root / ".agentic").mkdir()

    def journal(self, *, transaction_id, managed, old, new, previous_runtime):
        stage = f".agentic/.venv.staging-{transaction_id}"
        backup = f".agentic/.venv.backup-{transaction_id}"
        value = {
            "format": "awf-install-journal-3",
            "transaction_id": transaction_id,
            "phase": "active",
            "files": [{"path": managed,
                        "old": None if old is None else base64.b64encode(old).decode("ascii"),
                        "old_mode": None if old is None else stat.S_IMODE((self.root / managed).stat().st_mode),
                        "new_sha256": sha256(new),
                        "new_mode": stat.S_IMODE((self.root / managed).stat().st_mode)}],
            "runtime": {"path": ".agentic/.venv",
                        "previous_sha256": previous_runtime,
                        "new_sha256": None, "stage": stage, "backup": backup,
                        "cleanup_identity": None, "cleanup_members": None},
            "managed_before": ([] if old is None else [{"path": managed,
                "mode": stat.S_IMODE((self.root / managed).stat().st_mode), "sha256": sha256(old)}]),
            "managed_after": [{"path": managed,
                "mode": stat.S_IMODE((self.root / managed).stat().st_mode), "sha256": sha256(new)}],
        }
        journal_raw = installer.json_bytes(value)
        (self.root / installer.JOURNAL).write_bytes(journal_raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(journal_raw))))
        return value

    @staticmethod
    def failing_runtime(transaction):
        transaction.stage.mkdir()
        (transaction.stage / "runtime.txt").write_bytes(b"new canonical runtime\n")
        transaction.record_staged_runtime()
        if transaction.had_previous:
            os.replace(transaction.runtime_root, transaction.backup)
        os.replace(transaction.stage, transaction.runtime_root)
        raise ValidationError("injected canonical runtime failure")

    def test_fresh_install_runtime_failure_removes_new_receipt_managed_files_and_runtime(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"new receipt\n"
        (self.root / managed).write_bytes(new)
        self.journal(transaction_id=transaction_id, managed=managed, old=None, new=new,
                     previous_runtime=None)
        with self.assertRaisesRegex(ValidationError, "injected canonical runtime failure"):
            installer.complete_runtime_transaction(
                self.root, transaction_id, self.failing_runtime)
        self.assertFalse((self.root / managed).exists())
        self.assertFalse((self.root / ".agentic/.venv").exists())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())
        self.assertEqual([], list((self.root / ".agentic").glob(".venv.*-*")))

    def test_upgrade_runtime_failure_restores_exact_receipt_managed_and_runtime_bytes(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        old, new = b"old receipt\n", b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"exact previous runtime\n")
        previous_runtime = installer._runtime_tree_sha256(runtime)
        self.journal(transaction_id=transaction_id, managed=managed, old=old, new=new,
                     previous_runtime=previous_runtime)
        with self.assertRaisesRegex(ValidationError, "injected canonical runtime failure"):
            installer.complete_runtime_transaction(
                self.root, transaction_id, self.failing_runtime)
        self.assertEqual(old, (self.root / managed).read_bytes())
        self.assertEqual(b"exact previous runtime\n", (runtime / "runtime.txt").read_bytes())
        self.assertEqual(previous_runtime, installer._runtime_tree_sha256(runtime))
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())
        self.assertEqual([], list((self.root / ".agentic").glob(".venv.*-*")))

    def test_upgrade_success_proves_old_backup_before_durable_intent_cleanup(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        old, new = b"old receipt\n", b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"exact previous runtime\n")
        previous_runtime = installer._runtime_tree_sha256(runtime)
        self.journal(transaction_id=transaction_id, managed=managed, old=old, new=new,
                     previous_runtime=previous_runtime)
        observed = {}

        def successful_runtime(transaction):
            transaction.stage.mkdir()
            (transaction.stage / "runtime.txt").write_bytes(b"new canonical runtime\n")
            transaction.record_staged_runtime()
            os.replace(transaction.runtime_root, transaction.backup)
            os.replace(transaction.stage, transaction.runtime_root)
            observed["backup_during_builder"] = installer._runtime_tree_sha256(transaction.backup)
            return {"status": "BUILT"}

        result = installer.complete_runtime_transaction(self.root, transaction_id, successful_runtime)
        self.assertEqual(previous_runtime, observed["backup_during_builder"])
        self.assertEqual(b"upgraded receipt\n", (self.root / managed).read_bytes())
        self.assertEqual(b"new canonical runtime\n", (runtime / "runtime.txt").read_bytes())
        self.assertEqual("COMPLETE", result["transaction_cleanup"])
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())
        self.assertEqual([], list((self.root / ".agentic").glob(".venv.*-*")))

    def test_final_outer_commit_proof_detects_backup_edit_between_prior_proofs(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        old, new = b"old receipt\n", b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"exact previous runtime\n")
        previous_runtime = installer._runtime_tree_sha256(runtime)
        journal = self.journal(transaction_id=transaction_id, managed=managed, old=old, new=new,
                               previous_runtime=previous_runtime)

        def successful_runtime(transaction):
            transaction.stage.mkdir()
            (transaction.stage / "runtime.txt").write_bytes(b"new canonical runtime\n")
            transaction.record_staged_runtime()
            os.replace(transaction.runtime_root, transaction.backup)
            os.replace(transaction.stage, transaction.runtime_root)
            return {"status": "BUILT"}

        original_verify = installer._verify_file_state
        def tamper_after_file_proof(tree, observed_journal, which):
            original_verify(tree, observed_journal, which)
            backup = self.root / observed_journal["runtime"]["backup"]
            (backup / "runtime.txt").write_bytes(b"external edit between proofs\n")

        with patch.object(installer, "_verify_file_state", side_effect=tamper_after_file_proof):
            with self.assertRaises(ValidationError) as raised:
                installer.complete_runtime_transaction(
                    self.root, transaction_id, successful_runtime)
        self.assertIn(f"transaction {transaction_id}", str(raised.exception))
        self.assertIn(journal["runtime"]["backup"], str(raised.exception))
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_authenticated_cleanup_rejects_replaced_directory_without_deleting_it(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        original = self.root / f".agentic/.venv.cleanup-{transaction_id}"
        original.mkdir()
        (original / "old.txt").write_bytes(b"authenticated old member\n")
        journal = self.journal(transaction_id=transaction_id, managed=managed, old=b"old receipt\n",
                               new=new, previous_runtime="1" * 64)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        proof = installer._runtime_cleanup_proof(original)
        journal["runtime"]["cleanup_identity"] = proof["identity"]
        journal["runtime"]["cleanup_members"] = proof["members"]
        journal["phase"] = "commit_cleanup_authenticated"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        displaced = self.root / "displaced-cleanup"
        os.replace(original, displaced)
        cleanup = installer._runtime_cleanup_path(self.root, journal["runtime"])
        cleanup.mkdir()
        sentinel = cleanup / "external.txt"
        sentinel.write_bytes(b"preserve external data\n")

        with self.assertRaisesRegex(ValidationError, "directory identity changed"):
            installer.recover(self.root)
        self.assertEqual(b"preserve external data\n", sentinel.read_bytes())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_authenticated_cleanup_rejects_unknown_member_without_deleting_it(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        cleanup = self.root / f".agentic/.venv.cleanup-{transaction_id}"
        cleanup.mkdir()
        (cleanup / "old.txt").write_bytes(b"authenticated old member\n")
        journal = self.journal(transaction_id=transaction_id, managed=managed, old=b"old receipt\n",
                               new=new, previous_runtime="1" * 64)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        proof = installer._runtime_cleanup_proof(cleanup)
        journal["runtime"]["cleanup_identity"] = proof["identity"]
        journal["runtime"]["cleanup_members"] = proof["members"]
        journal["phase"] = "commit_cleanup_authenticated"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        sentinel = cleanup / "external.txt"
        sentinel.write_bytes(b"preserve external data\n")

        with self.assertRaisesRegex(ValidationError, "member changed or was added"):
            installer.recover(self.root)
        self.assertEqual(b"preserve external data\n", sentinel.read_bytes())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_structurally_valid_journal_tamper_cannot_supply_different_old_bytes(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        old, new = b"trusted old receipt\n", b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        journal = self.journal(transaction_id=transaction_id, managed=managed, old=old, new=new,
                               previous_runtime=None)
        journal["files"][0]["old"] = base64.b64encode(b"attacker-selected bytes\n").decode("ascii")
        (self.root / installer.JOURNAL).write_bytes(installer.json_bytes(journal))
        called = False

        def builder(_transaction):
            nonlocal called
            called = True

        with self.assertRaisesRegex(ValidationError, "differs from its bound marker"):
            installer.complete_runtime_transaction(self.root, transaction_id, builder)
        self.assertFalse(called)
        self.assertEqual(new, (self.root / managed).read_bytes())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_staged_journal_pending_marker_accepts_only_old_or_precommitted_bytes(self):
        transaction_id = str(uuid.uuid4())
        (self.root / ".agentic/installed-manifest.json").write_bytes(b"new receipt\n")
        journal = self.journal(transaction_id=transaction_id,
                               managed=".agentic/installed-manifest.json",
                               old=None, new=b"new receipt\n", previous_runtime=None)
        old_raw = (self.root / installer.JOURNAL).read_bytes()
        stage = self.root / journal["runtime"]["stage"]
        stage.mkdir()
        (stage / "runtime.txt").write_bytes(b"staged canonical runtime\n")
        proposed = installer._runtime_tree_sha256(stage)
        journal["runtime"]["new_sha256"] = proposed
        new_raw = installer.json_bytes(journal)
        pending = installer._journal_marker(transaction_id, sha256(old_raw), sha256(new_raw))
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(pending))
        with installer.Tree(self.root) as tree:
            self.assertIsNone(installer._read_bound_journal(tree)[0]["runtime"]["new_sha256"])
        (self.root / installer.JOURNAL).write_bytes(new_raw)
        with installer.Tree(self.root) as tree:
            self.assertEqual(proposed, installer._read_bound_journal(tree)[0]["runtime"]["new_sha256"])
        journal["runtime"]["new_sha256"] = "2" * 64
        (self.root / installer.JOURNAL).write_bytes(installer.json_bytes(journal))
        with installer.Tree(self.root) as tree:
            with self.assertRaisesRegex(ValidationError, "differs from its bound marker"):
                installer._read_bound_journal(tree)

    def test_pending_marker_old_journal_reconstructs_stage_digest_before_rollback(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        (self.root / managed).write_bytes(b"new receipt\n")
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=None, new=b"new receipt\n", previous_runtime=None)
        old_raw = (self.root / installer.JOURNAL).read_bytes()
        stage = self.root / journal["runtime"]["stage"]
        stage.mkdir()
        (stage / "runtime.txt").write_bytes(b"staged canonical runtime\n")
        candidate = json.loads(json.dumps(journal))
        candidate["runtime"]["new_sha256"] = installer._runtime_tree_sha256(stage)
        candidate_raw = installer.json_bytes(candidate)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(old_raw), sha256(candidate_raw))))

        self.assertEqual("ROLLED_BACK", installer.recover(self.root)["status"])
        self.assertFalse(stage.exists())
        self.assertFalse((self.root / managed).exists())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())

    def test_pending_marker_stage_tamper_fails_closed_without_deleting_stage(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        (self.root / managed).write_bytes(b"new receipt\n")
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=None, new=b"new receipt\n", previous_runtime=None)
        old_raw = (self.root / installer.JOURNAL).read_bytes()
        stage = self.root / journal["runtime"]["stage"]
        stage.mkdir()
        runtime_file = stage / "runtime.txt"
        runtime_file.write_bytes(b"trusted staged runtime\n")
        candidate = json.loads(json.dumps(journal))
        candidate["runtime"]["new_sha256"] = installer._runtime_tree_sha256(stage)
        candidate_raw = installer.json_bytes(candidate)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(old_raw), sha256(candidate_raw))))
        runtime_file.write_bytes(b"externally changed staged runtime\n")

        with self.assertRaisesRegex(ValidationError, "does not prove the pending journal"):
            installer.recover(self.root)
        self.assertTrue(stage.exists())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_backup_cleanup_authenticates_before_delete_and_resumes_partial_removal(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        backup_bytes = b"exact previous runtime\n"
        backup = self.root / f".agentic/.venv.backup-{transaction_id}"
        backup.mkdir()
        (backup / "runtime.txt").write_bytes(backup_bytes)
        previous = installer._runtime_tree_sha256(backup)
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=b"old receipt\n", new=new, previous_runtime=previous)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        journal["phase"] = "commit_cleanup"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        cleanup = self.root / f".agentic/.venv.cleanup-{transaction_id}"

        def partial_remove(path):
            self.assertEqual(cleanup, path)
            (path / "runtime.txt").unlink()
            raise OSError("injected partial cleanup")

        with installer.Tree(self.root) as tree, patch.object(
                installer, "_remove_runtime_transaction_path", side_effect=partial_remove):
            failures = installer._cleanup_committed_runtime(tree, journal)
        self.assertEqual("commit_cleanup_authenticated", journal["phase"])
        self.assertEqual(1, len(failures))
        self.assertFalse(backup.exists())
        self.assertTrue(cleanup.exists())

        self.assertEqual("COMMITTED", installer.recover(self.root)["status"])
        self.assertFalse(cleanup.exists())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())

    def test_backup_rename_crash_before_authenticated_phase_recovers_by_exact_digest(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        backup = self.root / f".agentic/.venv.backup-{transaction_id}"
        backup.mkdir()
        (backup / "runtime.txt").write_bytes(b"exact previous runtime\n")
        previous = installer._runtime_tree_sha256(backup)
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=b"old receipt\n", new=new, previous_runtime=previous)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        journal["phase"] = "commit_cleanup"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        cleanup = installer._runtime_cleanup_path(self.root, journal["runtime"])
        os.replace(backup, cleanup)

        self.assertEqual("COMMITTED", installer.recover(self.root)["status"])
        self.assertFalse(cleanup.exists())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())

    def test_backup_edit_during_authentication_rename_fails_closed(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        backup = self.root / f".agentic/.venv.backup-{transaction_id}"
        backup.mkdir()
        backup_file = backup / "runtime.txt"
        backup_file.write_bytes(b"exact previous runtime\n")
        previous = installer._runtime_tree_sha256(backup)
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=b"old receipt\n", new=new, previous_runtime=previous)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        journal["phase"] = "commit_cleanup"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        cleanup = installer._runtime_cleanup_path(self.root, journal["runtime"])
        replace = os.replace

        def tamper_then_rename(source, destination):
            backup_file.write_bytes(b"changed between proof and rename\n")
            replace(source, destination)

        with installer.Tree(self.root) as tree, patch.object(
                installer.os, "replace", side_effect=tamper_then_rename):
            with self.assertRaisesRegex(ValidationError, "changed while being isolated"):
                installer._cleanup_committed_runtime(tree, journal)
        self.assertFalse(backup.exists())
        self.assertTrue(cleanup.exists())
        self.assertEqual("commit_cleanup", installer.loads(
            (self.root / installer.JOURNAL).read_text(encoding="utf-8"))["phase"])
        with self.assertRaisesRegex(ValidationError, "cannot be authenticated"):
            installer.recover(self.root)
        self.assertTrue(cleanup.exists())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_pending_authenticated_phase_after_backup_rename_recovers(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        backup = self.root / f".agentic/.venv.backup-{transaction_id}"
        backup.mkdir()
        (backup / "runtime.txt").write_bytes(b"exact previous runtime\n")
        previous = installer._runtime_tree_sha256(backup)
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=b"old receipt\n", new=new, previous_runtime=previous)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        journal["phase"] = "commit_cleanup"
        raw = installer.json_bytes(journal)
        authenticated = json.loads(json.dumps(journal))
        authenticated["phase"] = "commit_cleanup_authenticated"
        authenticated_raw = installer.json_bytes(authenticated)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(
                transaction_id, sha256(raw), sha256(authenticated_raw))))
        cleanup = installer._runtime_cleanup_path(self.root, journal["runtime"])
        os.replace(backup, cleanup)

        self.assertEqual("COMMITTED", installer.recover(self.root)["status"])
        self.assertFalse(cleanup.exists())
        self.assertFalse((self.root / installer.JOURNAL).exists())
        self.assertFalse((self.root / installer.MARKER).exists())

    def test_fresh_authenticated_commit_refuses_unowned_cleanup_path(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"new receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=None, new=new, previous_runtime=None)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        journal["phase"] = "commit_cleanup_authenticated"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        cleanup = installer._runtime_cleanup_path(self.root, journal["runtime"])
        cleanup.mkdir()
        sentinel = cleanup / "not-transaction-owned.txt"
        sentinel.write_bytes(b"preserve\n")

        with self.assertRaisesRegex(ValidationError, "unauthenticated cleanup path"):
            installer.recover(self.root)
        self.assertEqual(b"preserve\n", sentinel.read_bytes())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())

    def test_backup_tamper_before_cleanup_fails_closed(self):
        transaction_id = str(uuid.uuid4())
        managed = ".agentic/installed-manifest.json"
        new = b"upgraded receipt\n"
        (self.root / managed).write_bytes(new)
        runtime = self.root / ".agentic/.venv"
        runtime.mkdir()
        (runtime / "runtime.txt").write_bytes(b"new canonical runtime\n")
        backup = self.root / f".agentic/.venv.backup-{transaction_id}"
        backup.mkdir()
        (backup / "runtime.txt").write_bytes(b"exact previous runtime\n")
        previous = installer._runtime_tree_sha256(backup)
        journal = self.journal(transaction_id=transaction_id, managed=managed,
                               old=b"old receipt\n", new=new, previous_runtime=previous)
        journal["runtime"]["new_sha256"] = installer._runtime_tree_sha256(runtime)
        journal["phase"] = "commit_cleanup"
        raw = installer.json_bytes(journal)
        (self.root / installer.JOURNAL).write_bytes(raw)
        (self.root / installer.MARKER).write_bytes(installer.json_bytes(
            installer._journal_marker(transaction_id, sha256(raw))))
        (backup / "runtime.txt").write_bytes(b"externally changed prior runtime\n")

        with self.assertRaisesRegex(ValidationError, "cannot be authenticated"):
            installer.recover(self.root)
        self.assertTrue(backup.exists())
        self.assertTrue((self.root / installer.JOURNAL).exists())
        self.assertTrue((self.root / installer.MARKER).exists())


class BootstrapMainTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "scripts/bootstrap_project.py").is_file(),
                         "Source bootstrap entry point is not shipped in installed runtimes")
    def test_normal_exit_states_and_dryrun_skip_postcheck(self):
        spec = importlib.util.spec_from_file_location("test_bootstrap_entry", ROOT / "scripts/bootstrap_project.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        pin = "a" * 64
        wheelhouse = ROOT / ".tmp-tests" / "fixture-wheelhouse"
        for state, expected in (("CONFIGURED", 0), ("INSTALLED_UNCONFIGURED", 1), ("INSTALLATION_VERIFICATION_FAILED", 2)):
            prepared = type("Prepared", (), {"source_manifest_sha256": pin})()
            events = []
            def prevalidate(*args):
                events.append("prevalidate")
                return prepared
            def install_result(*args, **kwargs):
                events.append("install")
                return {"status": "INSTALLED", "source_manifest_sha256": pin,
                        "_runtime_transaction_id": "fixture-transaction"}
            def runtime_result(*args, **kwargs):
                events.append("runtime")
                self.assertEqual(args, (ROOT,))
                self.assertIs(kwargs["prepared_wheelhouse"], prepared)
                self.assertIs(kwargs["transaction"], transaction)
                return {"interpreter": "fixture", "entry_point": "fixture"}
            transaction = object()
            def complete(destination, transaction_id, builder):
                events.append("complete")
                self.assertEqual(destination, ROOT)
                self.assertEqual(transaction_id, "fixture-transaction")
                return builder(transaction)
            def checked(*args):
                events.append("postcheck")
                return {"status": state}
            argv = ["bootstrap", "--dest", str(ROOT), "--expected-manifest-sha256", pin,
                    "--runtime-wheelhouse", str(wheelhouse), "--github-repo", "fixture/repo"]
            with patch.object(sys, "argv", argv), patch.object(
                    module, "prevalidate_runtime_wheelhouse", side_effect=prevalidate) as prevalidate_call, patch.object(
                    module, "install", side_effect=install_result) as install_call, patch.object(
                    module, "complete_runtime_transaction", side_effect=complete), patch.object(
                    module, "ensure_installed_runtime", side_effect=runtime_result), patch.object(
                    module, "post_install_checks", side_effect=checked), patch(
                    "sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(module.main(), expected)
                self.assertEqual(json.loads(out.getvalue())["status"], state)
                self.assertTrue(install_call.call_args.kwargs["configure"])
                self.assertTrue(install_call.call_args.kwargs["defer_runtime"])
                self.assertEqual(prevalidate_call.call_args.args, (ROOT, pin, wheelhouse))
                self.assertEqual(events, ["prevalidate", "install", "complete", "runtime", "postcheck"])
        with patch.object(sys, "argv", ["bootstrap", "--dest", str(ROOT), "--dry-run"]), patch.object(module, "prevalidate_runtime_wheelhouse", side_effect=AssertionError("dryrun must not prevalidate runtime")), patch.object(module, "install", return_value={"status": "PLAN"}), patch.object(module, "ensure_installed_runtime", side_effect=AssertionError("dryrun must not build runtime")), patch.object(module, "post_install_checks", side_effect=AssertionError("dryrun must not execute")), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(module.main(), 0)
        with patch.object(sys, "argv", ["bootstrap", "--dest", str(ROOT), "--mode", "upgrade", "--propose-operating-capacity", "--dry-run"]), patch.object(module, "prevalidate_runtime_wheelhouse", side_effect=AssertionError("dryrun must not prevalidate runtime")), patch.object(module, "install", return_value={"status": "PLAN"}) as install_call, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(module.main(), 0)
            self.assertTrue(install_call.call_args.kwargs["propose_operating_capacity"])
        with patch.object(sys, "argv", ["bootstrap", "--dest", str(ROOT), "--recover"]), patch.object(module, "prevalidate_runtime_wheelhouse", side_effect=AssertionError("recovery must not prevalidate runtime")), patch.object(module, "recover", return_value={"status": "NO_PENDING_INSTALL"}), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(module.main(), 0)
            self.assertEqual("NO_PENDING_INSTALL", json.loads(out.getvalue())["status"])
        with patch.object(sys, "argv", ["bootstrap", "--dest", str(ROOT), "--recover", "--propose-operating-capacity"]), patch.object(module, "recover", side_effect=AssertionError("invalid combined flags")), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(module.main(), 2)


if __name__ == "__main__":
    unittest.main()
