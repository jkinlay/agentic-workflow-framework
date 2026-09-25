"""Offline rule observation/enablement-prerequisite checks; no GitHub calls."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic import ValidationError
from agentic import repository_rules as rules

NOW = "2026-09-14T12:00:00Z"
REPOSITORY = "fixture/project"


def fixture(*, app=None, live=False):
    detail = copy.deepcopy(rules.TEMPLATE)
    detail.update(id=7, source_type="Repository", source=REPOSITORY)
    if app is not None:
        detail["rules"][-1]["parameters"]["required_status_checks"] = [{"context": "awf/review", "integration_id": app}]
    effective = [{**copy.deepcopy(item), "ruleset_id": 7, "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY} for item in detail["rules"]]
    value = rules.synthetic_observation(REPOSITORY, "trunk", rules=effective, rulesets=[detail], observed_at=NOW)
    if live:
        value["source"] = "github_api"  # Mocked transport assertion for this offline unit test only.
    return value


class RepositoryRulesTests(unittest.TestCase):
    def assess(self, value=None, **kwargs):
        return rules.assess_repository_rules(value, repository=REPOSITORY, now=NOW, **kwargs)

    def test_shipped_template_is_exact_default_selector_and_solo_compatible(self):
        value = json.loads((ROOT / ".agentic/templates/awf-main-ruleset.json").read_bytes())
        self.assertIs(rules.validate_ruleset_template(value), value)
        self.assertEqual(value["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        self.assertEqual(value["rules"][2]["parameters"]["required_approving_review_count"], 0)
        self.assertFalse(value["rules"][2]["parameters"]["require_code_owner_review"])
        for mutate in (lambda x: x["conditions"]["ref_name"].update(include=["refs/heads/main"]),
                       lambda x: x.update(bypass_actors=[{"actor_id": 1}]),
                       lambda x: x["rules"][2]["parameters"].update(required_approving_review_count=False)):
            altered = copy.deepcopy(value)
            mutate(altered)
            with self.assertRaises(ValidationError):
                rules.validate_ruleset_template(altered)

    def test_unprotected_repository_proceeds_with_exact_adoption_decisions(self):
        value = rules.synthetic_observation(REPOSITORY, "trunk", observed_at=NOW)
        result = self.assess(value)
        self.assertEqual(result["repository_rules"], "MISSING")
        self.assertEqual(set(result["decision_codes"]), {"prepare_adoption_pr", "report_missing_ruleset"})
        self.assertNotIn("block_adoption", result["decision_codes"])
        self.assertTrue(result["adoption_allowed"])
        self.assertFalse(result["rules_enablement_ready"])
        self.assertFalse(result["execution_authority"])

    def test_no_observation_is_not_claimed_missing(self):
        result = self.assess()
        self.assertEqual(result["repository_rules"], "UNOBSERVED")
        self.assertTrue(result["adoption_allowed"])

    def test_applied_empty_checks_is_not_live_ready(self):
        result = self.assess(fixture(live=True), review_app_id=12)
        self.assertEqual(result["repository_rules"], "APPLIED")
        self.assertFalse(result["rules_enablement_ready"])

    def test_live_app_bound_check_satisfies_only_rules_prerequisite(self):
        result = self.assess(fixture(app=12, live=True), review_app_id=12)
        self.assertTrue(result["rules_enablement_ready"])
        self.assertEqual(result["enablement_preflight"], "RULES_READY_QUALIFICATION_STILL_REQUIRED")
        self.assertEqual(result["expected_review_app_id"], 12)
        self.assertFalse(result["execution_authority"])
        self.assertTrue(result["additional_live_qualification_required"])
        for app in (None, 13):
            self.assertFalse(self.assess(fixture(app=12, live=True), review_app_id=app)["rules_enablement_ready"])

    def test_synthetic_applied_never_grants_live_readiness(self):
        result = self.assess(fixture(app=12), review_app_id=12)
        self.assertEqual(result["repository_rules"], "APPLIED")
        self.assertEqual(result["observation_source"], "synthetic_fixture")
        self.assertFalse(result["rules_enablement_ready"])

    def test_stale_future_wrong_host_repo_or_default_is_unobserved(self):
        for change in ({"observed_at": "2026-09-14T11:00:00Z"}, {"observed_at": "2026-09-14T12:00:01Z"},
                       {"host": "other.example"}, {"repository": "other/project"}, {"complete": False}):
            with self.subTest(change=change):
                result = self.assess({**fixture(app=12, live=True), **change}, review_app_id=12)
                self.assertEqual(result["repository_rules"], "UNOBSERVED")
                self.assertTrue(result["adoption_allowed"])
                self.assertFalse(result["rules_enablement_ready"])
        self.assertEqual(self.assess(fixture(), default_branch="main")["repository_rules"], "UNOBSERVED")

    def test_baseline_missing_and_unknown_bypass_are_distinct(self):
        value = fixture()
        value["rulesets"][0].pop("bypass_actors")
        self.assertEqual(self.assess(value)["repository_rules"], "UNOBSERVED")
        value = fixture()
        value["rulesets"][0]["bypass_actors"] = [{"actor_id": 12, "actor_type": "Integration", "bypass_mode": "always"}]
        self.assertEqual(self.assess(value)["repository_rules"], "MISSING")
        value = fixture()
        value["rulesets"][0]["rules"].pop(0)
        value["branch_pages"][0]["rules"].pop(0)
        self.assertEqual(self.assess(value)["repository_rules"], "MISSING")

    def test_malformed_or_incomplete_detail_does_not_establish_applied(self):
        mutations = [lambda x: x.update(rulesets=[]), lambda x: x["rulesets"][0].update(enforcement="evaluate"),
                     lambda x: x["repository_response"].update(default_branch="main"),
                     lambda x: x["rulesets"][0]["rules"][2]["parameters"].pop("dismiss_stale_reviews_on_push"),
                     lambda x: x["branch_pages"][0].update(page=2),
                     lambda x: x["rulesets"][0].update(source="other/project")]
        for mutate in mutations:
            value = fixture(app=12, live=True)
            mutate(value)
            with self.subTest(mutate=mutate):
                result = self.assess(value, review_app_id=12)
                self.assertEqual(result["repository_rules"], "UNOBSERVED")
                self.assertFalse(result["rules_enablement_ready"])

    def test_effective_detail_rule_order_is_not_identity(self):
        value = fixture()
        value["rulesets"][0]["rules"].reverse()
        self.assertEqual(self.assess(value)["repository_rules"], "APPLIED")

    def test_live_collector_discovers_default_and_only_gets_empty_rules(self):
        calls = []
        def get(endpoint, deadline):
            calls.append(endpoint)
            return ({"id": 1, "full_name": REPOSITORY, "default_branch": "release/current"} if endpoint.endswith(REPOSITORY) else []), 100
        observed = rules.observe_repository_rules(REPOSITORY, get=get)
        self.assertEqual(observed["default_branch"], "release/current")
        self.assertEqual(calls, ["repos/fixture/project", "repos/fixture/project/rules/branches/release%2Fcurrent?per_page=100&page=1", "repos/fixture/project"])
        result = rules.assess_repository_rules(observed, repository=REPOSITORY)
        self.assertEqual(result["repository_rules"], "MISSING")

    def test_live_default_override_mismatch_stops_before_branch_get(self):
        get = Mock(return_value=({"id": 1, "full_name": REPOSITORY, "default_branch": "trunk"}, 100))
        self.assertIsNone(rules.observe_repository_rules(REPOSITORY, default_branch="main", get=get))
        get.assert_called_once()

    def test_live_collector_does_not_assume_first_page_complete(self):
        value = fixture()
        extras = [{"type": "synthetic_rule_" + str(n), "ruleset_id": 7, "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY} for n in range(100)]
        detail = value["rulesets"][0]
        detail["rules"] += [{"type": item["type"]} for item in extras]
        calls = []
        def get(endpoint, deadline):
            calls.append(endpoint)
            if endpoint.endswith(REPOSITORY):
                body = value["repository_response"]
            elif "&page=1" in endpoint:
                body = extras
            elif "&page=2" in endpoint:
                body = value["branch_pages"][0]["rules"]
            else:
                body = detail
            return body, 100
        observed = rules.observe_repository_rules(REPOSITORY, get=get)
        self.assertEqual(len(observed["branch_pages"]), 2)
        self.assertEqual(len(calls), 5)
        self.assertTrue(calls[-2].endswith("rulesets/7?includes_parents=true"))

    def test_default_or_repository_identity_change_during_collection_is_unobserved(self):
        for changed in ({"default_branch": "develop"}, {"id": 2}, {"full_name": "other/project"}):
            initial = {"id": 1, "full_name": REPOSITORY, "default_branch": "trunk"}
            get = Mock(side_effect=[(initial, 100), ([], 10), ({**initial, **changed}, 100)])
            with self.subTest(changed=changed):
                self.assertIsNone(rules.observe_repository_rules(REPOSITORY, get=get))
                self.assertEqual(get.call_count, 3)

    def test_access_failure_truncation_and_aggregate_limit_are_unobserved(self):
        self.assertIsNone(rules.observe_repository_rules(REPOSITORY, get=Mock(side_effect=OSError("secret provider body"))))
        self.assertIsNone(rules.observe_repository_rules(REPOSITORY, get=Mock(return_value=({}, rules.MAX_TOTAL_BYTES + 1))))
        def endless(endpoint, deadline):
            if endpoint.endswith(REPOSITORY):
                return {"id": 1, "full_name": REPOSITORY, "default_branch": "trunk"}, 100
            return [{"type": "synthetic", "ruleset_id": 7}] * 100, 100
        self.assertIsNone(rules.observe_repository_rules(REPOSITORY, get=endless))

    def test_gh_command_is_get_only_and_malformed_body_is_sanitized(self):
        commands = []
        def launch(command, **kwargs):
            commands.append(command)
            kwargs["stdout"].write(b'not-json secret-provider-value')
            return Mock(poll=Mock(return_value=0), returncode=0)
        with patch.object(rules.subprocess, "Popen", side_effect=launch):
            value = rules.observe_repository_rules(REPOSITORY)
        self.assertIsNone(value)
        self.assertEqual(commands[0][commands[0].index("--method") + 1], "GET")
        self.assertEqual(commands[0][commands[0].index("--hostname") + 1], "github.com")
        self.assertNotIn("secret-provider-value", json.dumps(self.assess(value)))

    def test_transport_deadline_cleanup_failure_remains_unobserved(self):
        child = Mock(poll=Mock(return_value=None), kill=Mock(side_effect=OSError("synthetic")))
        with patch.object(rules.subprocess, "Popen", return_value=child), patch.object(rules.time, "monotonic", side_effect=[0, 0, 61]):
            self.assertIsNone(rules.observe_repository_rules(REPOSITORY))
        child.kill.assert_called_once()

    def test_quick_exit_oversized_stderr_is_not_accepted(self):
        def launch(command, **kwargs):
            kwargs["stdout"].write(b'{}')
            kwargs["stderr"].write(b'x' * (rules.MAX_BYTES + 1))
            return Mock(poll=Mock(return_value=0), returncode=0)
        with patch.object(rules.subprocess, "Popen", side_effect=launch):
            self.assertIsNone(rules.observe_repository_rules(REPOSITORY))

    def test_imported_observation_read_is_bounded_before_parsing(self):
        raw = json.dumps(rules.synthetic_observation(REPOSITORY, "trunk")).encode()
        tree = Mock()
        tree.read.return_value = raw
        context = Mock(__enter__=Mock(return_value=tree), __exit__=Mock(return_value=False))
        with patch.object(rules, "Tree", return_value=context):
            result = rules.load_observation_report(Path("synthetic.json"), rules.sha256(raw), repository=REPOSITORY)
        tree.read.assert_called_once_with("synthetic.json", maximum=rules.MAX_TOTAL_BYTES)
        self.assertEqual(result["repository_rules"], "MISSING")

    def test_cli_empty_rules_warns_but_only_enablement_flag_refuses(self):
        spec = importlib.util.spec_from_file_location("awf_repository_rules_cli_test", ROOT / ".agentic/scripts/repository_rules.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        args = ["--repository", REPOSITORY, "--synthetic-none", "--default-branch", "trunk"]
        for flag, expected in (([], 0), (["--require-enablement"], 2)):
            out = io.StringIO()
            with patch.object(cli, "verify_installed"), patch.object(subprocess, "Popen", side_effect=AssertionError("No network child")), redirect_stdout(out):
                self.assertEqual(cli.main(args + flag), expected)
            result = json.loads(out.getvalue())
            self.assertEqual(result["repository_rules"], "MISSING")
            self.assertTrue(result["adoption_allowed"])
            self.assertFalse(result["execution_authority"])


if __name__ == "__main__":
    unittest.main()
