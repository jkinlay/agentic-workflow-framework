"""Safe, synthetic regressions for the five-slot acceleration evidence contract."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic import ValidationError  # noqa: E402
from agentic.canonical import canonical, sha256  # noqa: E402
from agentic.contracts import Contracts  # noqa: E402
from agentic.five_slot_acceleration import (  # noqa: E402
    GATE_NAME,
    aggregate_shard_receipts,
    build_regression_receipt,
    canonicalize_pr_body,
    deterministic_shards,
    encode_plan,
    provider_body_receipt,
    validate_plan,
)


class FiveSlotAccelerationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.inventory_raw = (
            ROOT / ".agentic/validation/five-slot-adversarial-regressions.json"
        ).read_bytes()
        self.inventory = json.loads(self.inventory_raw)
        self.inventory_sha = sha256(self.inventory_raw)
        self.local_body = b"## Visibility only\r\n\r\nOWNER_READY=NO\r\n\r\n"
        self.provider_body = canonicalize_pr_body(self.local_body)
        self.candidate = {
            "repository_id": 123456,
            "pr_number": 36,
            "base_sha": "1" * 40,
            "head_sha": "2" * 40,
            "tree_sha": "3" * 40,
            "manifest_sha256": "4" * 64,
            "provider_pr_body_sha256": sha256(self.provider_body),
        }
        self.mapping_sha = "f" * 64
        self.scanner_sha = "e" * 64
        self.authorization_raw = self._authorization(self.candidate)
        self.plan = self._plan()

    def _authorization(self, candidate):
        return canonical({
            "format": "awf-private-deny-baseline-authorization-1",
            "candidate": deepcopy(candidate),
            "provider_pr_body_sha256": candidate["provider_pr_body_sha256"],
            "mapping_sha256": self.mapping_sha,
            "authorized_by": "synthetic-controller",
            "authorization_ref": "synthetic-explicit-authorization",
        })

    def _deny_receipt(self, candidate, *, classification="NO_MATCHES", counts=None,
                      paths=None, authorization_sha256=None, overrides=None):
        counts = counts or {
            "history_matches": 0, "diff_added_matches": 0,
            "diff_deleted_matches": 0, "body_matches": 0,
            "base_matches": 0, "head_matches": 0,
        }
        receipt = {
            "format": "awf-private-deny-scan-receipt-1",
            "policy_id": "exact-current-tree-baseline-v1",
            "status": "PASS",
            "classification": classification,
            "candidate": deepcopy(candidate),
            "provider_pr_body_sha256": candidate["provider_pr_body_sha256"],
            "mapping_sha256": self.mapping_sha,
            "scanner_sha256": self.scanner_sha,
            "counts": deepcopy(counts),
            "paths": deepcopy(paths or []),
            "unscanned": [],
            "scan_complete": True,
            "baseline_authorization_sha256": authorization_sha256,
            "private_match_values_included": False,
            "execution_authority": False,
        }
        receipt.update(overrides or {})
        return canonical(receipt)

    def _plan(self):
        candidate = deepcopy(self.candidate)
        tests = ["tests::delta", "tests::alpha", "tests::charlie", "tests::bravo"]
        assignments = deterministic_shards(tests, 2)
        shard_receipts = [{
            "shard_id": row["shard_id"],
            "test_ids": row["test_ids"],
            "status": "PASS",
            "result_sha256": sha256(row["shard_id"].encode()),
        } for row in assignments]
        aggregate = aggregate_shard_receipts(candidate, assignments, shard_receipts)
        def exact_gate(sequence):
            return {
                "sequence": sequence,
                "status": "PASS",
                "candidate": deepcopy(candidate),
                "receipt_sha256": f"{sequence:x}" * 64,
            }
        return {
            "format": "awf-five-slot-acceleration-plan-1",
            "candidate": candidate,
            "topology": {
                "default_slots": 5,
                "observed_slots": 5,
                "controller": {
                    "role": "sole-controller", "agent_id": "controller-1",
                    "execution": self._execution("RUNNING"),
                },
                "adversarial_handler": {
                    "role": "adversarial-case-handler",
                    "agent_id": "adversarial-1",
                    "dedicated": True,
                    "gate_name": GATE_NAME,
                    "execution": self._execution("RUNNING"),
                },
                "workers": [
                    {
                        "stream": "A",
                        "agent_id": "worker-a",
                        "execution": self._execution("BLOCKED"),
                        "active": {"deliverable_id": "EXAMPLE-1001", "dependency_order": 1,
                                   "work_state": "BLOCKED", "jira_status": "In Progress"},
                        "inactive": [{"deliverable_id": "EXAMPLE-1002", "dependency_order": 2,
                                      "work_state": "ON_HOLD", "jira_status": "On Hold"}],
                    },
                    {
                        "stream": "B",
                        "agent_id": "worker-b",
                        "execution": self._execution("RUNNING"),
                        "active": {"deliverable_id": "EXAMPLE-2001", "dependency_order": 1,
                                   "work_state": "ACTIVE", "jira_status": "In Progress"},
                        "inactive": [{"deliverable_id": "EXAMPLE-2002", "dependency_order": 2,
                                      "work_state": "OPEN", "jira_status": "Open"}],
                    },
                    {
                        "stream": "C",
                        "agent_id": "worker-c",
                        "execution": self._execution("RUNNING"),
                        "active": {"deliverable_id": "EXAMPLE-3001", "dependency_order": 1,
                                   "work_state": "ACTIVE", "jira_status": "In Progress"},
                        "inactive": [],
                    },
                ],
                "integration_steward": {
                    "kind": "deterministic-non-model-infrastructure",
                    "consumes_agent_slot": False,
                },
                "degraded_mode": None,
            },
            "freeze": {"sequence": 1, "status": "FROZEN", "candidate": deepcopy(candidate)},
            "accepted_critic_findings": [],
            "adversarial_gate": {
                "sequence": 2,
                "name": GATE_NAME,
                "status": "PASS",
                "candidate": deepcopy(candidate),
                "inventory_sha256": self.inventory_sha,
                "regression_receipts": [build_regression_receipt(
                    candidate, row, sha256(row["test_id"].encode())
                ) for row in self.inventory["regressions"]],
            },
            "sharded_validation": {
                "sequence": 3,
                "resource_capacity": 2,
                "test_ids": tests,
                "assignments": assignments,
                "receipts": shard_receipts,
                "aggregate_sha256": aggregate,
            },
            "full_suite": {"sequence": 4, "runs": [{
                "status": "PASS", "candidate": deepcopy(candidate),
                "receipt_sha256": "b" * 64,
            }]},
            "controller_verification": {
                "sequence": 5, "status": "PASS", "exact_tuple": deepcopy(candidate),
                "receipt_sha256": "c" * 64,
            },
            "review_completion": {
                "sequence": 6, "status": "PASS", "frozen": True,
                "candidate": deepcopy(candidate), "receipt_sha256": "d" * 64,
            },
            "publication_scan": exact_gate(7),
            "deny_scan": exact_gate(8),
            "final_independent_review": {
                "sequence": 9, "status": "APPROVE", "independent": True,
                "reviewer_id": "final-reviewer-1", "candidate": deepcopy(candidate),
                "receipt_sha256": "e" * 64,
            },
            "owner_ready": "NO",
        }

    @staticmethod
    def _execution(status):
        if status == "RUNNING":
            return {"status": status, "reason": None, "resume_trigger": None,
                    "evidence_sha256": None}
        return {"status": status, "reason": "waiting-for-reviewed-dependency",
                "resume_trigger": "dependency-is-merged", "evidence_sha256": "a" * 64}

    def _validate(self, plan=None, inventory_raw=None, *, deny_receipt=None,
                  authorization=None, mapping_sha=None, scanner_sha=None,
                  authorization_sha=None):
        chosen_plan = deepcopy(plan or self.plan)
        inventory_raw = self.inventory_raw if inventory_raw is None else inventory_raw
        deny_receipt = deny_receipt or self._deny_receipt(chosen_plan["candidate"])
        chosen_plan["deny_scan"]["receipt_sha256"] = sha256(deny_receipt)
        raw = encode_plan(chosen_plan)
        authorization = (None if authorization is False else
                         self.authorization_raw if authorization is None else authorization)
        return validate_plan(
            raw, sha256(raw), local_pr_body=self.local_body,
            provider_readback=self.provider_body, inventory_raw=inventory_raw,
            expected_inventory_sha256=sha256(inventory_raw),
            deny_scan_receipt_raw=deny_receipt,
            expected_private_mapping_sha256=(mapping_sha or self.mapping_sha),
            expected_private_scanner_sha256=(scanner_sha or self.scanner_sha),
            baseline_authorization_raw=authorization,
            expected_baseline_authorization_sha256=(
                authorization_sha if authorization_sha is not None else sha256(authorization)
            ) if authorization is not None else None,
        )

    def assertRejected(self, plan, pattern, inventory_raw=None):
        with self.assertRaisesRegex(ValidationError, pattern):
            self._validate(plan, inventory_raw)

    def test_default_topology_and_complete_gate_chain_pass(self):
        result = self._validate()
        self.assertEqual("DEFAULT_FIVE_SLOT", result["mode"])
        self.assertEqual("NO", result["owner_ready"])
        self.assertFalse(result["execution_authority"])
        self.assertEqual(["A"], result["blocked_streams"])
        self.assertTrue(result["other_streams_continue"])
        duplicate = deepcopy(self.plan)
        duplicate["topology"]["workers"][0]["agent_id"] = "controller-1"
        self.assertRejected(duplicate, "distinct agent identity")

    def test_capacity_shortfall_requires_explicit_authorized_degraded_mode(self):
        plan = deepcopy(self.plan)
        plan["topology"]["observed_slots"] = 4
        self.assertRejected(plan, "explicit degraded-mode authorization")
        plan["topology"]["workers"][1]["execution"] = self._execution("PAUSED")
        plan["topology"]["workers"][1]["active"].update(
            work_state="PAUSED", jira_status="In Progress")
        plan["topology"]["degraded_mode"] = {
            "authorized": True,
            "authorized_by": "release-owner",
            "authorization_evidence_sha256": "f" * 64,
            "reason": "host-capacity-shortfall",
            "paused_roles": ["worker-b"],
        }
        self.assertEqual("AUTHORIZED_DEGRADED", self._validate(plan)["mode"])

    def test_gate_inventory_and_sequence_fail_closed(self):
        plan = deepcopy(self.plan)
        plan["adversarial_gate"]["regression_receipts"].pop()
        self.assertRejected(plan, "exactly one receipt per frozen inventory member")
        plan = deepcopy(self.plan)
        plan["adversarial_gate"]["sequence"] = 4
        self.assertRejected(plan, "immediately after freeze")

    def test_inventory_requires_all_accumulated_categories_and_ordered_receipts(self):
        inventory = deepcopy(self.inventory)
        inventory["regressions"] = [row for row in inventory["regressions"]
                                    if row["category"] != "alias"]
        inventory_raw = (json.dumps(inventory, sort_keys=True) + "\n").encode()
        plan = deepcopy(self.plan)
        plan["adversarial_gate"]["inventory_sha256"] = sha256(inventory_raw)
        self.assertRejected(plan, "omits mandatory categories.*alias", inventory_raw)

        plan = deepcopy(self.plan)
        receipts = plan["adversarial_gate"]["regression_receipts"]
        receipts[0], receipts[1] = receipts[1], receipts[0]
        self.assertRejected(plan, "ordered permanent inventory")

    def test_inventory_paths_tests_and_execution_receipts_are_exact(self):
        for field, value, pattern in (
            ("evidence_path", ".agentic/tests/does-not-exist.py", "path does not exist"),
            ("test_id", "FiveSlotAccelerationTests.test_does_not_exist", "test ID does not exist"),
        ):
            with self.subTest(field=field):
                inventory = deepcopy(self.inventory)
                inventory["regressions"][0][field] = value
                inventory_raw = (json.dumps(inventory, sort_keys=True) + "\n").encode()
                plan = deepcopy(self.plan)
                plan["adversarial_gate"]["inventory_sha256"] = sha256(inventory_raw)
                self.assertRejected(plan, pattern, inventory_raw)

        plan = deepcopy(self.plan)
        plan["adversarial_gate"]["regression_receipts"][0]["candidate"]["head_sha"] = "7" * 40
        self.assertRejected(plan, "frozen exact tuple")
        plan = deepcopy(self.plan)
        plan["adversarial_gate"]["regression_receipts"][0]["result_sha256"] = "7" * 64
        self.assertRejected(plan, "fabricated or mismatched")
        plan = deepcopy(self.plan)
        plan["adversarial_gate"]["regression_receipts"][0]["artifact_sha256"] = "7" * 64
        self.assertRejected(plan, "frozen test artifact")

    def test_receipt_cardinality_rejects_duplicate_unknown_and_extra_receipts(self):
        receipts = self.plan["adversarial_gate"]["regression_receipts"]
        trailing_receipts = {
            "duplicate": deepcopy(receipts[-1]),
            "unknown": {
                **deepcopy(receipts[-1]),
                "regression_id": "UNKNOWN-TRAILING-REGRESSION",
            },
            "extra": deepcopy(receipts[0]),
        }
        for case, trailing in trailing_receipts.items():
            with self.subTest(case=case):
                plan = deepcopy(self.plan)
                plan["adversarial_gate"]["regression_receipts"].append(trailing)
                self.assertRejected(
                    plan,
                    "exactly one receipt per frozen inventory member",
                )

    def test_provider_body_canonicalization_and_exact_readback(self):
        receipt = provider_body_receipt("alpha\r\n\r\n", b"alpha\n")
        self.assertEqual("BYTE_EXACT", receipt["provider_readback"])
        with self.assertRaisesRegex(ValidationError, "byte-for-byte"):
            provider_body_receipt("alpha\r\n", b"alpha")
        with self.assertRaisesRegex(ValidationError, "strict UTF-8"):
            provider_body_receipt(b"\xff", b"\xff")

    def test_accepted_finding_requires_permanent_inventory_member(self):
        plan = deepcopy(self.plan)
        plan["accepted_critic_findings"] = [{
            "finding_id": "CRITIC-17", "acceptance_evidence_sha256": "9" * 64,
        }]
        self.assertRejected(plan, "permanent named regression")
        inventory = deepcopy(self.inventory)
        inventory["regressions"].append({
            "regression_id": "CRITIC-17-REGRESSION",
            "category": "receipt-replay",
            "name": "Accepted critic finding CRITIC-17 remains covered",
            "test_id": "FiveSlotAccelerationTests.test_accepted_finding_requires_permanent_inventory_member",
            "evidence_path": ".agentic/tests/test_five_slot_acceleration.py",
            "permanent": True,
            "source_finding_id": "CRITIC-17",
        })
        inventory_raw = (json.dumps(inventory, sort_keys=True) + "\n").encode()
        plan["adversarial_gate"]["inventory_sha256"] = sha256(inventory_raw)
        plan["adversarial_gate"]["regression_receipts"].append(
            build_regression_receipt(self.candidate, inventory["regressions"][-1], "8" * 64))
        self.assertEqual("PASS", self._validate(plan, inventory_raw)["status"])

    def test_canonical_retained_findings_have_exactly_one_permanent_mapping(self):
        """C26-F08: guard the complete retained set with owner-supplied F03/F05/F06 text."""
        canonical_ids = (
            *(f"L-F{number:02d}" for number in range(1, 11)),
            "PMF-21-2", "PMF-21-3", "PMF-21-4",
            *(f"C25-F{number:02d}" for number in range(1, 6)),
            *(f"FRESH-F{number:02d}" for number in range(1, 4)),
            *(f"C26-F{number:02d}" for number in range(1, 7)),
            "C26-F07",
            "C26-F08",
            "C26-IC-F01",
            "C22-CV-001",
            "C-TRX-001",
            "C-TRX-002",
            "C-TRX-003",
        )
        mapped_ids = [row["source_finding_id"] for row in self.inventory["regressions"]]
        for finding_id in canonical_ids:
            with self.subTest(finding_id=finding_id):
                self.assertEqual(1, mapped_ids.count(finding_id))
        b001 = [row for row in self.inventory["regressions"]
                if row["source_finding_id"] == "B-001"]
        self.assertEqual(1, len(b001))
        self.assertEqual("scripts/tests/test_publish_release.py", b001[0]["evidence_path"])
        self.assertEqual(
            "PublishReleaseTests.test_b001_materialize_commit_preserves_concurrent_destination_winner",
            b001[0]["test_id"],
        )
        c22_cv_001 = [row for row in self.inventory["regressions"]
                      if row["source_finding_id"] == "C22-CV-001"]
        self.assertEqual(1, len(c22_cv_001))
        self.assertEqual(".agentic/tests/test_review_loop.py", c22_cv_001[0]["evidence_path"])
        self.assertEqual("HostTests.test_ci_workflow_pin_and_failed_result", c22_cv_001[0]["test_id"])

    def test_owner_supplied_c26_acceptance_text_matches_permanent_inventory(self):
        provenance = json.loads((
            ROOT / ".agentic/validation/pr26-owner-accepted-finding-provenance.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual("owner_supplied_decision", provenance["authority"]["kind"])
        self.assertFalse(provenance["authority"]["historical_source_claimed"])
        for record in provenance["findings"]:
            with self.subTest(finding_id=record["finding_id"]):
                rows = [row for row in self.inventory["regressions"]
                        if row["source_finding_id"] == record["finding_id"]]
                self.assertEqual(1, len(rows))
                self.assertEqual(record["inventory_regression_id"], rows[0]["regression_id"])
                self.assertEqual(record["inventory_test_id"], rows[0]["test_id"])
                self.assertEqual(record["acceptance_text"], rows[0]["name"])

    def test_shards_are_deterministic_bounded_and_exact(self):
        first = deterministic_shards(["z", "b", "a", "c"], 99)
        second = deterministic_shards(["c", "a", "z", "b"], 3)
        self.assertEqual(first, second)
        self.assertEqual(3, len(first))
        plan = deepcopy(self.plan)
        plan["sharded_validation"]["assignments"][0]["test_ids"].reverse()
        self.assertRejected(plan, "not deterministic and bounded")
        plan = deepcopy(self.plan)
        plan["sharded_validation"]["receipts"][0]["result_sha256"] = "0" * 64
        self.assertRejected(plan, "aggregate receipt")

    def test_worker_delivery_invariants_fail_closed(self):
        plan = deepcopy(self.plan)
        plan["topology"]["workers"][0]["inactive"][0]["dependency_order"] = 1
        self.assertRejected(plan, "follow dependency order")
        plan = deepcopy(self.plan)
        plan["topology"]["workers"][1]["active"]["jira_status"] = "Open"
        self.assertRejected(plan, "Jira status is inconsistent")
        plan = deepcopy(self.plan)
        plan["topology"]["workers"][2]["inactive"] = [{
            "deliverable_id": "EXAMPLE-1001", "dependency_order": 2,
            "work_state": "ON_HOLD", "jira_status": "On Hold",
        }]
        self.assertRejected(plan, "more than one stream")

    def test_jira_lifecycle_matches_role_execution_and_started_work_never_reopens(self):
        plan = deepcopy(self.plan)
        plan["topology"]["workers"][1]["active"]["jira_status"] = "Open"
        self.assertRejected(plan, "Jira status is inconsistent")

        plan = deepcopy(self.plan)
        plan["topology"]["workers"][1]["execution"] = self._execution("PAUSED")
        plan["topology"]["workers"][1]["active"].update(
            work_state="PR_READY", jira_status="In Review")
        result = self._validate(plan)
        self.assertIn("B", result["paused_streams"])

        plan["topology"]["workers"][1]["active"].update(
            work_state="ACTIVE", jira_status="In Progress")
        self.assertRejected(plan, "Paused workers must have PAUSED or PR_READY")

    def test_degraded_capacity_names_paused_roles_and_continuation_is_derived(self):
        plan = deepcopy(self.plan)
        plan["topology"]["observed_slots"] = 4
        plan["topology"]["workers"][1]["execution"] = self._execution("PAUSED")
        plan["topology"]["workers"][1]["active"].update(
            work_state="PAUSED", jira_status="In Progress")
        plan["topology"]["degraded_mode"] = {
            "authorized": True, "authorized_by": "release-owner",
            "authorization_evidence_sha256": "f" * 64,
            "reason": "host-capacity-shortfall", "paused_roles": [],
        }
        self.assertRejected(plan, "name every explicitly paused role")
        plan["topology"]["degraded_mode"]["paused_roles"] = ["worker-b"]
        self.assertTrue(self._validate(plan)["other_streams_continue"])

        one = deepcopy(self.plan)
        one["topology"]["observed_slots"] = 1
        one["topology"]["degraded_mode"] = {
            "authorized": True, "authorized_by": "release-owner",
            "authorization_evidence_sha256": "f" * 64,
            "reason": "single-slot-host", "paused_roles": [],
        }
        self.assertRejected(one, "exceed.*observed slot capacity")

        for role in [one["topology"]["adversarial_handler"],
                     *one["topology"]["workers"]]:
            role["execution"] = self._execution("PAUSED")
        for worker in one["topology"]["workers"]:
            worker["active"].update(work_state="PAUSED", jira_status="In Progress")
        one["topology"]["degraded_mode"]["paused_roles"] = [
            "adversarial-1", "worker-a", "worker-b", "worker-c",
        ]
        result = self._validate(one)
        self.assertEqual(["controller-1"], result["running_roles"])
        self.assertFalse(result["other_streams_continue"])

    def test_candidate_freeze_and_single_full_suite_are_exact(self):
        plan = deepcopy(self.plan)
        plan["freeze"]["candidate"]["tree_sha"] = "7" * 40
        self.assertRejected(plan, "frozen exact tuple")
        plan = deepcopy(self.plan)
        plan["full_suite"]["runs"].append(deepcopy(plan["full_suite"]["runs"][0]))
        self.assertRejected(plan, "Exactly one full suite")
        plan = deepcopy(self.plan)
        plan["full_suite"]["sequence"] = 2
        self.assertRejected(plan, "after freeze")

    def test_mandatory_preservation_gates_fail_closed(self):
        cases = [
            ("controller_verification", "status", "FAIL", "Controller verification"),
            ("review_completion", "frozen", False, "review completion"),
            ("publication_scan", "status", "FAIL", "publication_scan"),
            ("deny_scan", "status", "FAIL", "deny_scan"),
            ("final_independent_review", "independent", False, "Final independent review"),
        ]
        for section, field, value, pattern in cases:
            with self.subTest(section=section):
                plan = deepcopy(self.plan)
                plan[section][field] = value
                self.assertRejected(plan, pattern)
        plan = deepcopy(self.plan)
        plan["final_independent_review"]["reviewer_id"] = "worker-b"
        self.assertRejected(plan, "independent from all five")
        plan = deepcopy(self.plan)
        plan["owner_ready"] = "YES"
        self.assertRejected(plan, "OWNER_READY=NO")

    def test_private_deny_baseline_pass_requires_exact_bound_authorization(self):
        counts = {
            "history_matches": 0, "diff_added_matches": 0,
            "diff_deleted_matches": 0, "body_matches": 0,
            "base_matches": 2, "head_matches": 2,
        }
        receipt = self._deny_receipt(
            self.candidate, classification="BASE_PREEXISTING_UNCHANGED",
            counts=counts, paths=[{
                "path": "docs/example.md", "base_count": 2,
                "head_count": 2, "multiset_equal": True,
            }], authorization_sha256=sha256(self.authorization_raw),
        )
        result = self._validate(deny_receipt=receipt)
        self.assertEqual("NO", result["owner_ready"])
        self.assertFalse(result["execution_authority"])
        contracts = Contracts(ROOT / ".agentic/schemas")
        contracts.validate("private-deny-scan", json.loads(receipt))
        contracts.validate("private-deny-baseline-authorization",
                           json.loads(self.authorization_raw))

        with self.assertRaisesRegex(ValidationError, "separately pinned authorization"):
            self._validate(deny_receipt=receipt, authorization=False)

    def test_private_deny_history_diff_and_body_findings_block_baseline(self):
        for channel in ("history_matches", "diff_added_matches",
                        "diff_deleted_matches", "body_matches"):
            with self.subTest(channel=channel):
                counts = {
                    "history_matches": 0, "diff_added_matches": 0,
                    "diff_deleted_matches": 0, "body_matches": 0,
                    "base_matches": 1, "head_matches": 1,
                }
                counts[channel] = 1
                receipt = self._deny_receipt(
                    self.candidate, classification="BASE_PREEXISTING_UNCHANGED",
                    counts=counts, paths=[{
                        "path": "docs/example.md", "base_count": 1,
                        "head_count": 1, "multiset_equal": True,
                    }], authorization_sha256=sha256(self.authorization_raw),
                )
                with self.assertRaisesRegex(ValidationError, "channels must be clear"):
                    self._validate(deny_receipt=receipt)

    def test_private_deny_new_moved_deleted_or_changed_paths_block(self):
        cases = [
            ([{"path": "docs/new.md", "base_count": 0, "head_count": 1,
               "multiset_equal": False}], "new, deleted, or changed"),
            ([{"path": "docs/old.md", "base_count": 1, "head_count": 0,
               "multiset_equal": False}], "new, deleted, or changed"),
            ([{"path": "docs/example.md", "base_count": 1, "head_count": 1,
               "multiset_equal": False}], "new, deleted, or changed"),
            ([{"path": "docs/example.md", "base_count": 1, "head_count": 2,
               "multiset_equal": True}], "new, deleted, or changed"),
        ]
        for paths, message in cases:
            with self.subTest(paths=paths):
                counts = {
                    "history_matches": 0, "diff_added_matches": 0,
                    "diff_deleted_matches": 0, "body_matches": 0,
                    "base_matches": 1, "head_matches": 1,
                }
                receipt = self._deny_receipt(
                    self.candidate, classification="BASE_PREEXISTING_UNCHANGED",
                    counts=counts, paths=paths,
                    authorization_sha256=sha256(self.authorization_raw),
                )
                with self.assertRaisesRegex(ValidationError, message):
                    self._validate(deny_receipt=receipt)

    def test_private_deny_receipt_rejects_mapping_body_auth_and_tuple_drift(self):
        counts = {
            "history_matches": 0, "diff_added_matches": 0,
            "diff_deleted_matches": 0, "body_matches": 0,
            "base_matches": 1, "head_matches": 1,
        }
        good = self._deny_receipt(
            self.candidate, classification="BASE_PREEXISTING_UNCHANGED",
            counts=counts, paths=[{
                "path": "docs/example.md", "base_count": 1,
                "head_count": 1, "multiset_equal": True,
            }], authorization_sha256=sha256(self.authorization_raw),
        )
        with self.assertRaisesRegex(ValidationError, "trusted mapping pin"):
            self._validate(deny_receipt=good, mapping_sha="a" * 64)
        with self.assertRaisesRegex(ValidationError, "trusted scanner pin"):
            self._validate(deny_receipt=good, scanner_sha="a" * 64)

        bad_auth = canonical({
            "format": "awf-private-deny-baseline-authorization-1",
            "candidate": deepcopy(self.candidate),
            "provider_pr_body_sha256": "0" * 64,
            "mapping_sha256": self.mapping_sha,
            "authorized_by": "synthetic-controller",
            "authorization_ref": "wrong-body",
        })
        bad_auth_receipt = self._deny_receipt(
            self.candidate, classification="BASE_PREEXISTING_UNCHANGED",
            counts=counts, paths=[{
                "path": "docs/example.md", "base_count": 1,
                "head_count": 1, "multiset_equal": True,
            }], authorization_sha256=sha256(bad_auth),
        )
        with self.assertRaisesRegex(ValidationError, "authorization body differs"):
            self._validate(deny_receipt=bad_auth_receipt, authorization=bad_auth,
                           authorization_sha=sha256(bad_auth))

        wrong_candidate = deepcopy(self.candidate)
        wrong_candidate["head_sha"] = "9" * 40
        wrong_receipt = self._deny_receipt(
            wrong_candidate, classification="BASE_PREEXISTING_UNCHANGED",
            counts=counts, paths=[{
                "path": "docs/example.md", "base_count": 1,
                "head_count": 1, "multiset_equal": True,
            }], authorization_sha256=sha256(self.authorization_raw),
        )
        with self.assertRaisesRegex(ValidationError, "Receipt candidate differs"):
            self._validate(deny_receipt=wrong_receipt)

    def test_private_deny_receipt_rejects_unscanned_or_disclosing_output(self):
        receipt = self._deny_receipt(
            self.candidate, overrides={"unscanned": ["docs/unreadable.md"]},
        )
        with self.assertRaisesRegex(ValidationError, "incomplete or unscanned"):
            self._validate(deny_receipt=receipt)
        receipt = self._deny_receipt(
            self.candidate, overrides={"private_match_values_included": True},
        )
        with self.assertRaisesRegex(ValidationError, "must not expose matched values"):
            self._validate(deny_receipt=receipt)


if __name__ == "__main__":
    unittest.main()
