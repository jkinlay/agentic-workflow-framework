"""AC46/AC58 adversarial ruleset/configuration activation coverage."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError
from agentic.canonical import canonical, load, sha256
from agentic.contracts import Contracts
from agentic.providers import github
from agentic.rules_activation import activation_rules_decision


REPOSITORY = "fixture/example"
BASE = "main"
BEFORE = "2026-10-02T10:00:00Z"
AFTER = "2026-10-02T10:01:00Z"
NOW = "2026-10-02T10:01:10Z"


def accepted_config(method="squash"):
    value = load(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml")
    value["github"]["merge_method"] = method
    return value


def ruleset(methods):
    value = copy.deepcopy(github.TEMPLATE)
    value["rules"][2]["parameters"]["allowed_merge_methods"] = list(methods)
    value.update(id=7, source_type="Repository", source=REPOSITORY)
    return value


def observation(methods=(), *, observed_at=BEFORE, repository=REPOSITORY, base=BASE,
                source="github_api", repository_id=101):
    details = [ruleset(methods)] if methods else []
    effective = [] if not details else [
        {**copy.deepcopy(item), "ruleset_id": 7, "ruleset_source_type": "Repository",
         "ruleset_source": repository}
        for item in details[0]["rules"]
    ]
    value = github.synthetic_observation(repository, base, rules=effective, rulesets=details,
                                         observed_at=observed_at, repository_id=repository_id)
    value["source"] = source
    return value


class RulesActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load(ROOT / ".agentic/workflow.yaml")
        cls.contracts = Contracts(ROOT / ".agentic/schemas")

    def decide(self, method="squash", before=None, **kwargs):
        return activation_rules_decision(
            accepted_config(method), self.workflow, self.contracts,
            before_observation=observation() if before is None else before,
            now=NOW, **kwargs)

    def test_ac46_merge_squash_rebase_generate_exact_compatible_rulesets(self):
        for method in ("merge", "squash", "rebase"):
            with self.subTest(method=method):
                config = accepted_config(method)
                first = github.ruleset_for_config(config, configuration_status="ACCEPTED")
                second = github.ruleset_for_config(copy.deepcopy(config), configuration_status="ACCEPTED")
                self.assertEqual(canonical(first), canonical(second))
                self.assertEqual([method], first["rules"][2]["parameters"]["allowed_merge_methods"])
                self.assertIs(first, github.validate_ruleset_for_merge_method(first, method))

    def test_ac46_shipped_template_is_safe_for_every_supported_configuration(self):
        template = json.loads((ROOT / ".agentic/templates/awf-main-ruleset.json").read_bytes())
        self.assertIs(template, github.validate_ruleset_template(template))
        self.assertEqual({"merge", "squash", "rebase"},
                         set(template["rules"][2]["parameters"]["allowed_merge_methods"]))
        for method in ("merge", "squash", "rebase"):
            self.assertIs(template, github.validate_ruleset_for_merge_method(template, method))

    def test_ac46_missing_conflicting_and_unaccepted_configuration_reject_precisely(self):
        missing = accepted_config()
        del missing["github"]["merge_method"]
        with self.assertRaisesRegex(ValidationError, r"\$\.github\.merge_method.*missing"):
            activation_rules_decision(missing, self.workflow, self.contracts,
                                      before_observation=observation(), now=NOW)
        invalid = accepted_config()
        invalid["github"]["merge_method"] = "octopus"
        with self.assertRaisesRegex(ValidationError, r"\$\.github\.merge_method"):
            activation_rules_decision(invalid, self.workflow, self.contracts,
                                      before_observation=observation(), now=NOW)
        incompatible = ruleset(["rebase"])
        with self.assertRaisesRegex(ValidationError, "disables configured merge method merge"):
            github.validate_ruleset_for_merge_method(incompatible, "merge")
        with self.assertRaisesRegex(ValidationError, "accepted PROJECT_CONFIG"):
            github.ruleset_for_config(accepted_config(), configuration_status="REJECTED")

    def test_ac46_incompatible_observed_rules_are_missing_and_never_recommended(self):
        assessed = github.assess_repository_rules(
            observation(["squash"]), repository=REPOSITORY, default_branch=BASE,
            merge_method="merge", now=NOW)
        self.assertEqual("MISSING", assessed["repository_rules"])
        self.assertFalse(assessed["configuration_compatible"])
        self.assertIn("merge", assessed["incompatibility"])
        decision = self.decide("merge", before=observation(["squash"]))
        self.assertEqual(["merge"], decision["proposed_ruleset"]["rules"][2]["parameters"]["allowed_merge_methods"])
        self.assertNotEqual(canonical(ruleset(["squash"])),
                            canonical(decision["proposed_ruleset"]))
        self.assertEqual("BLOCKED", decision["status"])

    def test_ac46_one_incompatible_applicable_ruleset_blocks_even_with_compatible_peer(self):
        compatible = ruleset(["merge"])
        incompatible = ruleset(["squash"])
        incompatible["id"] = 8
        effective = []
        for detail in (compatible, incompatible):
            effective.extend({**copy.deepcopy(item), "ruleset_id": detail["id"],
                              "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY}
                             for item in detail["rules"])
        value = github.synthetic_observation(
            REPOSITORY, BASE, rules=effective, rulesets=[compatible, incompatible],
            observed_at=BEFORE, repository_id=101)
        value["source"] = "github_api"
        assessed = github.assess_repository_rules(
            value, repository=REPOSITORY, default_branch=BASE, merge_method="merge", now=NOW)
        self.assertEqual("MISSING", assessed["repository_rules"])
        self.assertEqual([], assessed["baseline_ruleset_ids"])
        self.assertFalse(assessed["configuration_compatible"])

    def test_ac46_partial_pull_request_ruleset_merge_method_conflict_blocks(self):
        partial = {
            "id": 7, "source_type": "Repository", "source": REPOSITORY,
            "target": "branch", "enforcement": "active",
            "rules": [{"type": "pull_request", "parameters": {
                "allowed_merge_methods": ["squash"]}}],
        }
        effective = [{**copy.deepcopy(rule), "ruleset_id": 7,
                      "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY}
                     for rule in partial["rules"]]
        before = github.synthetic_observation(
            REPOSITORY, BASE, rules=effective, rulesets=[partial],
            observed_at=BEFORE, repository_id=101)
        before["source"] = "github_api"

        assessed = github.assess_repository_rules(
            before, repository=REPOSITORY, default_branch=BASE,
            merge_method="merge", expected_repository_id=101, now=NOW)
        self.assertEqual("MISSING", assessed["repository_rules"])
        self.assertFalse(assessed["configuration_compatible"])
        self.assertIn("disables configured merge method merge", assessed["incompatibility"])
        decision = self.decide("merge", before=before)
        self.assertEqual("BLOCKED", decision["status"])
        self.assertEqual("MISSING", decision["observed_rules_state"])
        self.assertFalse(decision["observed_rules"]["configuration_compatible"])

    def test_ac58_numeric_repository_id_is_bound_before_and_after_owner_action(self):
        config = accepted_config("merge")
        wrong_initial = observation(["merge"], repository_id=202)
        initial = activation_rules_decision(
            config, self.workflow, self.contracts, before_observation=wrong_initial, now=NOW)
        self.assertEqual("BLOCKED", initial["status"])
        self.assertEqual("UNOBSERVED", initial["observed_rules_state"])
        self.assertIn("RULES_OBSERVATION_WRONG_REPOSITORY_ID", initial["blocker_codes"])

        before = observation(repository_id=101)
        wrong_post = observation(["merge"], observed_at=AFTER, repository_id=202)
        post = activation_rules_decision(
            config, self.workflow, self.contracts, before_observation=before,
            owner_outcome="APPLIED", post_observation=wrong_post, now=NOW)
        self.assertEqual("BLOCKED", post["status"])
        self.assertEqual("UNOBSERVED", post["post_action_observation"]["state"])
        self.assertIn("POST_ACTION_OBSERVATION_WRONG_REPOSITORY_ID", post["blocker_codes"])
        self.assertNotEqual("APPLIED", post["final_rules_state"])

    def test_ac46_ac58_critic_findings_have_exactly_one_permanent_regression(self):
        inventory = load(ROOT / ".agentic/validation/five-slot-adversarial-regressions.json")
        expected = {
            "PR35-C01": (
                "PR35-C01-REGRESSION",
                "RulesActivationTests.test_ac58_numeric_repository_id_is_bound_before_and_after_owner_action",
            ),
            "PR35-C02": (
                "PR35-C02-REGRESSION",
                "RulesActivationTests.test_ac46_partial_pull_request_ruleset_merge_method_conflict_blocks",
            ),
        }
        for finding_id, (regression_id, test_id) in expected.items():
            with self.subTest(finding_id=finding_id):
                rows = [row for row in inventory["regressions"]
                        if row["source_finding_id"] == finding_id]
                self.assertEqual(1, len(rows))
                self.assertEqual(regression_id, rows[0]["regression_id"])
                self.assertEqual(test_id, rows[0]["test_id"])
                self.assertTrue(rows[0]["permanent"])

    def test_ac58_missing_presents_five_parts_and_never_mutates_provider(self):
        with patch.object(github.subprocess, "Popen", side_effect=AssertionError("provider mutation attempted")):
            result = self.decide("merge")
        self.assertEqual("MISSING", result["observed_rules_state"])
        self.assertEqual("PUBLICATION_GOVERNANCE_MISSING", result["publication_consequence"]["code"])
        self.assertEqual(["merge"], result["proposed_ruleset"]["rules"][2]["parameters"]["allowed_merge_methods"])
        action = result["owner_approval_action"]
        self.assertTrue(action["required"])
        self.assertEqual("POST", action["provider_request"]["method"])
        self.assertEqual("repos/fixture/example/rulesets", action["provider_request"]["endpoint"])
        self.assertEqual(result["proposed_ruleset_sha256"], action["provider_request"]["body_sha256"])
        self.assertEqual("UNOBSERVED", result["post_action_observation"]["state"])
        self.assertIn("RULES_MISSING", result["blocker_codes"])
        self.assertTrue(result["operational_blocker"])
        self.assertFalse(result["provider_mutation_performed"])
        self.assertFalse(result["execution_authority"])

    def test_ac58_owner_pending_declined_and_applied_without_post_observation_block(self):
        cases = [
            ("PENDING", "OWNER_APPROVAL_PENDING"),
            ("DECLINED", "OWNER_DECLINED"),
            ("APPLIED", "POST_ACTION_OBSERVATION_MISSING"),
        ]
        for outcome, blocker in cases:
            with self.subTest(outcome=outcome):
                result = self.decide(owner_outcome=outcome)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn("RULES_MISSING", result["blocker_codes"])
                self.assertIn(blocker, result["blocker_codes"])

    def test_ac58_fresh_post_action_observation_is_required_and_identity_bound(self):
        good = observation(["merge"], observed_at=AFTER)
        result = self.decide("merge", owner_outcome="APPLIED", post_observation=good)
        self.assertEqual("RULES_OBSERVED", result["status"])
        self.assertFalse(result["operational_blocker"])
        self.assertEqual("APPLIED", result["post_action_observation"]["state"])
        self.assertEqual([], result["blocker_codes"])
        bad_cases = [
            (observation(["merge"], observed_at=BEFORE), "POST_ACTION_OBSERVATION_NOT_FRESH"),
            (observation(["merge"], observed_at=AFTER, source="synthetic_fixture"),
             "POST_ACTION_OBSERVATION_NOT_LIVE"),
            (observation(["merge"], observed_at=AFTER, repository="fixture/other"),
             "POST_ACTION_OBSERVATION_WRONG_REPOSITORY"),
            (observation(["merge"], observed_at=AFTER, base="develop"),
             "POST_ACTION_OBSERVATION_WRONG_DEFAULT_BRANCH"),
            (observation(["squash"], observed_at=AFTER), "RULES_MISSING_AFTER_ACTION"),
        ]
        for post, blocker in bad_cases:
            with self.subTest(blocker=blocker):
                result = self.decide("merge", owner_outcome="APPLIED", post_observation=post)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(blocker, result["blocker_codes"])

    def test_ac58_stale_or_wrong_initial_identity_stays_explicitly_unobserved(self):
        bad = [
            (observation(observed_at="2026-10-02T09:00:00Z"), "RULES_OBSERVATION_STALE_OR_FUTURE"),
            (observation(repository="fixture/other"), "RULES_OBSERVATION_WRONG_REPOSITORY"),
            (observation(base="develop"), "RULES_OBSERVATION_WRONG_DEFAULT_BRANCH"),
        ]
        for before, diagnostic in bad:
            with self.subTest(diagnostic=diagnostic):
                result = self.decide(before=before)
                self.assertEqual("UNOBSERVED", result["observed_rules_state"])
                self.assertEqual("NOT_AVAILABLE", result["owner_approval_action"]["state"])
                self.assertIn("RULES_UNOBSERVED", result["blocker_codes"])
                self.assertIn(diagnostic, result["blocker_codes"])
                self.assertTrue(result["operational_blocker"])

    def test_ac58_synthetic_applied_observation_cannot_clear_activation(self):
        result = self.decide("merge", before=observation(["merge"], source="synthetic_fixture"))
        self.assertEqual("APPLIED", result["observed_rules_state"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("PUBLICATION_GOVERNANCE_UNOBSERVED",
                         result["publication_consequence"]["code"])
        self.assertIn("RULES_OBSERVATION_NOT_LIVE", result["blocker_codes"])

    def test_ac58_live_compatible_applied_observation_needs_no_provider_action(self):
        result = self.decide("merge", before=observation(["merge"]))
        self.assertEqual("RULES_OBSERVED", result["status"])
        self.assertFalse(result["owner_approval_action"]["required"])
        self.assertEqual("NOT_REQUIRED", result["owner_approval_action"]["state"])
        self.assertEqual("NOT_APPLICABLE", result["post_action_observation"]["state"])
        self.assertFalse(result["provider_mutation_performed"])

    def test_contract_template_is_deterministic_and_valid(self):
        result = self.decide("merge")
        self.assertIs(result, self.contracts.validate("rules-activation-decision", result))
        again = self.decide("merge")
        self.assertEqual(canonical(result), canonical(again))
        template = load(ROOT / ".agentic/templates/rules-activation-decision.yaml")
        self.assertIs(template, self.contracts.validate("rules-activation-decision", template))
        spec = importlib.util.spec_from_file_location(
            "awf_generate_rules_activation_test", ROOT / "scripts/generate_rules_activation.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        expected = (ROOT / ".agentic/templates/rules-activation-decision.yaml").read_text(encoding="utf-8")
        self.assertEqual(expected, generator.render())

    def test_cli_reads_only_digest_pinned_observation_and_never_calls_provider(self):
        spec = importlib.util.spec_from_file_location(
            "awf_rules_activation_cli_test", ROOT / ".agentic/scripts/rules_activation.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        raw = canonical(observation())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observation.json"
            path.write_bytes(raw)
            args = ["--config", str(ROOT / ".agentic/examples/PROJECT_CONFIG.yaml"),
                    "--workflow", str(ROOT / ".agentic/workflow.yaml"),
                    "--observation", str(path), "--expected-observation-sha256", sha256(raw),
                    "--now", NOW]
            out = io.StringIO()
            with patch.object(cli, "verify_installed"), redirect_stdout(out), \
                    patch.object(github.subprocess, "Popen", side_effect=AssertionError("provider call")):
                self.assertEqual(2, cli.main(args))
            self.assertEqual("BLOCKED", json.loads(out.getvalue())["status"])
            err = io.StringIO()
            bad_args = list(args)
            bad_args[7] = "0" * 64
            with patch.object(cli, "verify_installed"), redirect_stderr(err):
                self.assertEqual(2, cli.main(bad_args))
            self.assertEqual("REJECTED", json.loads(err.getvalue())["status"])

            wrong_initial_raw = canonical(observation(["squash"], repository_id=202))
            path.write_bytes(wrong_initial_raw)
            wrong_initial_args = list(args)
            wrong_initial_args[7] = sha256(wrong_initial_raw)
            out = io.StringIO()
            err = io.StringIO()
            with patch.object(cli, "verify_installed"), redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(2, cli.main(wrong_initial_args))
            initial_result = json.loads(out.getvalue())
            self.assertEqual("BLOCKED", initial_result["status"])
            self.assertEqual("UNOBSERVED", initial_result["observed_rules_state"])
            self.assertIn("RULES_OBSERVATION_WRONG_REPOSITORY_ID", initial_result["blocker_codes"])
            self.assertEqual("", err.getvalue())

            before_raw = canonical(observation())
            path.write_bytes(before_raw)
            after_raw = canonical(observation(["squash"], observed_at=AFTER, repository_id=202))
            post_path = Path(directory) / "post-observation.json"
            post_path.write_bytes(after_raw)
            post_args = list(args)
            post_args[7] = sha256(before_raw)
            post_args.extend(["--owner-outcome", "APPLIED", "--post-observation", str(post_path),
                              "--expected-post-observation-sha256", sha256(after_raw)])
            out = io.StringIO()
            err = io.StringIO()
            with patch.object(cli, "verify_installed"), redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(2, cli.main(post_args))
            post_result = json.loads(out.getvalue())
            self.assertEqual("BLOCKED", post_result["status"])
            self.assertIn("POST_ACTION_OBSERVATION_WRONG_REPOSITORY_ID", post_result["blocker_codes"])
            self.assertEqual("UNOBSERVED", post_result["post_action_observation"]["state"])
            self.assertEqual("", err.getvalue())


if __name__ == "__main__":
    unittest.main()
