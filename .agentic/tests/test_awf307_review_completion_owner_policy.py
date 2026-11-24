"""Rooted reviewer-removal authority, pins and retained owner evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from test_awf307_provider_owner_policy import ProviderPolicyFixture, KEY
import test_review_completion as completion_fixture
from agentic import ValidationError, review_completion as rc
from agentic.canonical import canonical, fingerprint
from agentic.shared_owner_policy import accepted_owner_evidence, OWNER_EVIDENCE_FIELDS


class ReviewPolicyFixture(ProviderPolicyFixture):
    consumer = None

    def setUp(self):
        super().setUp()
        self.current = completion_fixture.candidate()
        self.current["repository"] = self.config["github"]["repository"]
        self.worktree = self.project / "candidate"
        self.worktree.mkdir()
        self.dbpath = self.project / "state.sqlite3"
        self.calls = []
        self.after_observe = None
        with self.policy_reads():
            self.annotation = accepted_owner_evidence(self.project, self.config, 1002)

    def authority(self, *, rooted=True):
        return rc.review_authority_from_config(self.config, **self.options(rooted))

    def options(self, rooted=True):
        return {"accepted_policy_root": self.project} if rooted else {}

    def binding(self, *, rooted=True):
        return rc.review_authority_binding(self.config, self.project, [self.worktree], self.current,
                                           **self.options(rooted))

    def observer(self, request):
        self.calls.append(deepcopy(request))
        result = completion_fixture.ReviewCompletionTests.provider_review(request)
        if self.after_observe:
            self.after_observe(result)
        return result

    def store(self, *, rooted=True):
        return rc.ReviewCompletionStore(self.dbpath, [self.worktree], authority=self.authority(rooted=rooted),
            authority_binding=self.binding(rooted=rooted), provider_observer=self.observer,
            accepted_policy_config=self.config, **self.options(rooted))

    def prepare(self):
        self.ledger = self.store()
        self.old = self.ledger.freeze(self.current, ["a", "b", "c"])
        self.ledger.dispatch("c", self.current, ["a", "b", "c"])
        now = datetime.now(timezone.utc)
        record = {"schema_version": 1, "disposition_id": str(uuid.uuid4()),
            "decision": "REMOVE_REQUIRED_REVIEWERS", "old_cycle_id": self.old["cycle_id"],
            "tuple": deepcopy(self.current), "tuple_sha256": self.old["tuple_sha256"],
            "old_required_reviewers": ["a", "b", "c"], "old_reviewer_set_sha256": self.old["reviewer_set_sha256"],
            "new_required_reviewers": ["a", "b"], "new_reviewer_set_sha256": fingerprint("reviewer-set", ["a", "b"]),
            "removed_reviewers": ["c"], "reason": "Owner approved the revised set.", "owner_actor_id": 1002,
            "provider_identity": deepcopy(self.ledger.authority["provider_identity"]),
            "issued_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
            **deepcopy(self.annotation)}
        self.disposition = {"record": record, "record_sha256": fingerprint("reviewer-removal-disposition", record)}

    def call(self, *, rooted=True):
        if self.consumer == "authority":
            return self.authority(rooted=rooted)
        if self.consumer == "binding":
            return self.binding(rooted=rooted)
        return self.ledger.freeze(self.current, ["a", "b"], self.disposition)

    def refuses(self, *, rooted=True, message=None):
        with self.assertRaisesRegex(ValidationError, message or "."):
            self.call(rooted=rooted)


class SourceCases:
    def test_unset_profile_only_policy_keeps_ordinary_authority(self):
        self.config["merge_gate"].update(trusted_owner_ids=[1001], **{KEY: None})
        self.config["github"]["expected_actor_id"] = None
        result = self.call(rooted=False)
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(result))
        if self.consumer == "authority":
            self.assertEqual(result["trusted_owner_ids"], (1001,))
        else:
            authority = self.authority(rooted=False)
            self.assertEqual(result["review_authority_sha256"], fingerprint("review-authority", {
                "trusted_owner_ids": [1001], "provider_identity": authority["provider_identity"]}))

    def test_accepted_default_policy_retains_complete_annotation(self):
        with self.policy_reads():
            result = self.call()
        self.assert_annotation(dict(result, execution_authority=False))

    def test_rootless_refuses_and_unset_keeps_parent_result(self):
        self.refuses(rooted=False, message="^The configured automation actor must not be a trusted owner$")
        self.config["merge_gate"][KEY] = None
        authority = {"trusted_owner_ids": (1001, 1002), "provider_identity": {
            "host": "https://github.com", "repository": "fixture/example", "repository_id": 101}}
        expected = authority if self.consumer == "authority" else {
            "schema_version": 1, "project_root": os.path.normcase(str(self.project.resolve())),
            "worktree_roots": [os.path.normcase(str(self.worktree.resolve()))],
            "project_config_sha256": fingerprint("accepted-project-config", self.config),
            "tuple": self.current, "tuple_sha256": fingerprint("review-tuple", self.current),
            "review_authority_sha256": fingerprint("review-authority", {
                "trusted_owner_ids": [1001, 1002],
                "provider_identity": authority["provider_identity"]})}
        self.assertEqual(expected, self.call(rooted=False))

    def test_supplied_working_feature_and_missing_policy_refuse(self):
        original = self.blobs[".agentic/PROJECT_CONFIG.yaml"]
        self.accepted["merge_gate"].update(trusted_owner_ids=[1001], **{KEY: None})
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = canonical(self.accepted)
        (self.project / ".agentic").mkdir()
        (self.project / ".agentic/PROJECT_CONFIG.yaml").write_bytes(canonical(self.config))
        with self.policy_reads():
            self.refuses()
        self.blobs[".agentic/PROJECT_CONFIG.yaml"] = original
        self.ref = "refs/remotes/origin/feature"
        with self.policy_reads():
            self.refuses()
        self.ref = "refs/remotes/origin/main"
        self.blobs.pop(".agentic/PROJECT_CONFIG.yaml")
        with self.policy_reads():
            self.refuses()

    def test_account_changes_profile_only_invalid_and_hidden_setting_refuse(self):
        original = deepcopy(self.config)
        for section, key, value in [("github", "expected_actor_id", 1003), ("github", "expected_actor_id", None),
                ("merge_gate", "trusted_owner_ids", [1001]),
                *[("merge_gate", KEY, v) for v in (True, 0, -1, "1002", 1001, None)]]:
            self.config[section][key] = value
            with self.subTest(key=key, value=value), self.policy_reads():
                self.refuses()
            self.config[section][key] = deepcopy(original[section][key])

    def test_drift_between_observation_and_use_refuses(self):
        with self.policy_reads(drift=True):
            self.refuses(message="policy changed")


class AuthorityPolicyTests(SourceCases, ReviewPolicyFixture):
    consumer = "authority"


class AuthorityBindingPolicyTests(SourceCases, ReviewPolicyFixture):
    consumer = "binding"

    def test_host_root_cannot_be_replaced_by_project_root(self):
        with self.policy_reads(), self.assertRaises(ValidationError):
            rc.review_authority_binding(self.config, self.worktree, [self.worktree], self.current,
                                        accepted_policy_root=self.project)


class RemovalPolicyTests(ReviewPolicyFixture):
    consumer = "removal"

    def setUp(self):
        super().setUp()
        with self.policy_reads():
            self.prepare()

    def test_shared_removal_is_hash_bound_without_provider_body_mutation(self):
        original = deepcopy(self.disposition)
        with self.policy_reads():
            result = self.call()
            audit = self.ledger.reviewer_set_audit()
        self.assertEqual(result["required_reviewers"], ["a", "b"])
        self.assertEqual(self.disposition, original)
        self.assert_annotation(dict(audit[0]["record"], execution_authority=False))
        self.assertEqual(audit[0]["record_sha256"], fingerprint("reviewer-removal-disposition", audit[0]["record"]))
        self.assertFalse(OWNER_EVIDENCE_FIELDS.intersection(audit[0]["review"]))

    def test_incomplete_altered_and_absent_annotations_refuse(self):
        original = deepcopy(self.disposition["record"])
        for key, value in [(k, None) for k in OWNER_EVIDENCE_FIELDS] + [(None, None),
                ("shared_owner_automation_account", 1), ("owner_policy_sha256", "e" * 64),
                ("accepted_owner_policy", self.annotation["accepted_owner_policy"] | {"extra": True}),
                ("accepted_owner_policy", self.annotation["accepted_owner_policy"] | {"default_branch_commit": "e" * 40})]:
            record = deepcopy(original)
            if key is None:
                for field in OWNER_EVIDENCE_FIELDS:
                    record.pop(field)
            elif value is None:
                record.pop(key)
            else:
                record[key] = value
            self.disposition = {"record": record, "record_sha256": fingerprint("reviewer-removal-disposition", record)}
            with self.subTest(key=key, value=value), self.policy_reads():
                self.refuses()
            self.assertEqual(self.ledger.status()["cycle_id"], self.old["cycle_id"])
            self.assertEqual(self.ledger.reviewer_set_audit(), [])

    def test_different_account_provider_tuple_set_and_expiry_controls_preserved(self):
        original = deepcopy(self.disposition["record"])
        for key, value in (("owner_actor_id", 1001), ("owner_actor_id", 9999), ("tuple_sha256", "e" * 64),
                ("old_reviewer_set_sha256", "e" * 64), ("new_reviewer_set_sha256", "e" * 64),
                ("removed_reviewers", ["b"]), ("expires_at", "2000-01-01T00:00:00Z")):
            record = deepcopy(original)
            record[key] = value
            self.disposition = {"record": record, "record_sha256": fingerprint("reviewer-removal-disposition", record)}
            with self.subTest(key=key), self.policy_reads():
                self.refuses()
            self.assertEqual(self.ledger.reviewer_set_audit(), [])
        self.disposition = {"record": original, "record_sha256": fingerprint("reviewer-removal-disposition", original)}
        self.after_observe = lambda result: result.update(owner_actor_id=9999)
        with self.policy_reads():
            self.refuses(message="exact disposition")

    def test_policy_drift_in_provider_callback_rolls_back(self):
        self.after_observe = lambda result: setattr(self, "commit", "d" * 40)
        with self.policy_reads():
            self.refuses(message="policy changed")
        self.assertEqual(self.ledger.status()["cycle_id"], self.old["cycle_id"])
        self.assertEqual(self.ledger.reviewer_set_audit(), [])

    def test_expiry_after_provider_observation_refuses_before_application(self):
        self.clock = self.disposition["record"]["issued_at"]
        expires = datetime.fromisoformat(self.disposition["record"]["expires_at"].replace("Z", "+00:00"))
        self.after_observe = lambda result: setattr(self, "clock",
            (expires + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"))
        with self.policy_reads(), patch.object(rc, "now_text", side_effect=lambda: self.clock):
            self.refuses(message="expired")
        self.assertEqual(self.ledger.status()["cycle_id"], self.old["cycle_id"])
        self.assertEqual(self.ledger.reviewer_set_audit(), [])

    def test_freeze_reobserves_policy_and_refuses_rootless_restart(self):
        self.commit = "d" * 40
        with self.policy_reads():
            self.refuses(message="policy changed")
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValidationError):
            rc.ReviewCompletionStore(self.dbpath, [self.worktree], authority=self.ledger.authority,
                authority_binding=self.ledger.authority_binding, provider_observer=self.observer).freeze(
                    self.current, ["a", "b", "c"])

    def test_pin_and_authority_annotation_tampering_refuse(self):
        for target in (self.ledger.authority, self.ledger.authority_binding):
            original = deepcopy(target)
            for key in OWNER_EVIDENCE_FIELDS:
                target.pop(key, None)
                with self.subTest(target=target, missing=key), self.policy_reads():
                    self.refuses()
                target.clear()
                target.update(deepcopy(original))

    def test_reviewer_floor_frozen_set_replay_and_all_results_barrier_preserved(self):
        with self.policy_reads(), self.assertRaises(ValidationError):
            self.ledger.freeze(self.current, [], self.disposition)
        with self.policy_reads(), self.assertRaisesRegex(ValidationError, "Every frozen independent reviewer"):
            self.ledger.prepare_submission(self.current, ["a", "b", "c"], {"verdict": "APPROVE"})
        with self.policy_reads():
            self.call()
            self.refuses()
        binding = self.ledger.dispatch("a", self.current, ["a", "b"])
        with self.assertRaisesRegex(ValidationError, "frozen reviewer"):
            self.ledger.record_result("a", binding, "ACCEPTABLE",
                {"verdict": "APPROVE", "reviewer": "c", "findings": []})
        self.assertEqual(len(self.ledger.reviewer_set_audit()), 1)


if __name__ == "__main__":
    unittest.main()
