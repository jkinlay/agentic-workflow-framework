#!/usr/bin/env python3
"""Run the named publication-safety adversarial set before controller verification."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic" / "lib"))
sys.path.insert(0, str(ROOT / ".agentic" / "tests"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import test_publication  # noqa: E402
import test_continuous_controller  # noqa: E402
import test_bootstrap_configuration  # noqa: E402
import test_five_slot_acceleration  # noqa: E402
import test_heavy_validation  # noqa: E402
import test_operating  # noqa: E402
import test_operating_integration  # noqa: E402
import test_review_completion  # noqa: E402
import test_review_loop  # noqa: E402
import test_streams  # noqa: E402
import test_rules_activation  # noqa: E402
import test_pre_controller_registry  # noqa: E402


INVENTORY_MODULES = {
    ".agentic/tests/test_bootstrap_configuration.py": test_bootstrap_configuration,
    ".agentic/tests/test_continuous_controller.py": test_continuous_controller,
    ".agentic/tests/test_five_slot_acceleration.py": test_five_slot_acceleration,
    ".agentic/tests/test_heavy_validation.py": test_heavy_validation,
    ".agentic/tests/test_operating.py": test_operating,
    ".agentic/tests/test_operating_integration.py": test_operating_integration,
    ".agentic/tests/test_publication.py": test_publication,
    ".agentic/tests/test_review_completion.py": test_review_completion,
    ".agentic/tests/test_review_loop.py": test_review_loop,
    ".agentic/tests/test_streams.py": test_streams,
    ".agentic/tests/test_rules_activation.py": test_rules_activation,
    "scripts/tests/test_pre_controller_registry.py": test_pre_controller_registry,
}


# This inventory is intentionally explicit.  Adding a publication adversarial
# regression requires naming it here so the short pre-controller gate cannot
# silently lose coverage while the complete publication suite remains the
# subsequent authority.
CASE_GROUPS = {
    "classification": (
        "PublicationScanTests.test_deleted_preexisting_base_line_does_not_block",
        "PublicationScanTests.test_exact_raw_membership_is_case_sensitive_and_candidate_additions_always_block",
        "PublicationScanTests.test_binary_base_classification_is_lossless_and_retains_known_exact_positives",
        "PublicationScanTests.test_failed_base_classification_retains_known_positives_and_fails_closed",
    ),
    "mapping_and_bounded_reads": (
        "PublicationScanTests.test_force_tracked_mapping_cannot_hide_behind_repository_alias",
        "PublicationScanTests.test_mapping_hardlink_to_tracked_content_is_refused",
        "PublicationScanTests.test_mapping_windows_junction_to_tracked_content_is_refused",
        "PublicationScanTests.test_git_blob_size_is_checked_before_content_is_requested",
        "PublicationScanTests.test_provider_file_is_streamed_with_bounded_memory",
    ),
    "rewrite_reachability_and_recovery": (
        "PublicationRewriteTests.test_ac44_unpublished_squash_preserves_tree_and_cleans_refs",
        "PublicationRewriteTests.test_ac44_every_ref_namespace_is_in_the_reachability_census",
        "PublicationRewriteTests.test_ac44_grafts_are_rejected_before_rewrite_mutation",
        "PublicationRewriteTests.test_ac44_reachability_lookup_error_refuses_before_mutation",
        "PublicationRewriteTests.test_ac44_failed_final_cas_preserves_objects_claimed_by_all_ref_namespaces",
        "PublicationRewriteTests.test_ac44_failed_final_cas_preserves_object_retained_only_by_reflog",
        "PublicationRewriteTests.test_ac44_failed_final_cas_preserves_reflogless_detached_head_claim",
        "PublicationRewriteTests.test_ac44_failed_final_cas_preserves_reflogless_orig_head_claim",
        "PublicationRewriteTests.test_ac44_failed_final_cas_preserves_linked_worktree_detached_head_claim",
        "PublicationRewriteTests.test_ac44_claimant_created_after_census_keeps_object_and_recovery_evidence",
        "PublicationRewriteTests.test_ac44_ambiguous_partial_install_is_retained_and_fails_closed",
        "PublicationRewriteTests.test_ac44_fanout_created_after_locked_snapshot_is_never_snapshot_present",
        "PublicationRewriteTests.test_ac44_oversized_rewrite_message_is_rejected_before_commit_tree",
        "PublicationRewriteTests.test_ac44_oversized_quarantine_loose_object_rejects_before_content_read",
        "PublicationRewriteTests.test_ac44_quarantine_content_uses_bounded_git_retrieval",
        "PublicationRewriteTests.test_ac44_second_quarantine_census_pathname_change_fails_before_reads",
        "PublicationRewriteTests.test_ac44_quarantine_inventory_stops_at_overcount_sentinel_before_reads",
        "PublicationRewriteTests.test_ac44_bounded_quarantine_path_consumer_stops_after_sentinel",
        "PublicationRewriteTests.test_ac44_link_permission_failure_retains_operation_created_fanout",
        "PublicationRewriteTests.test_ac44_identity_swap_before_fanout_removal_never_deletes_external_directory",
        "PublicationRewriteTests.test_ac44_reflog_restore_refuses_file_symlink_before_external_mutation",
        "PublicationRewriteTests.test_ac44_reflog_restore_refuses_windows_junction_before_external_mutation",
        "PublicationRewriteTests.test_ac44_reflog_restore_refuses_parent_swap_before_namespace_mutation",
        "PublicationRewriteTests.test_ac44_reflog_restore_refuses_absent_original_deletion",
        "PublicationRewriteTests.test_ac44_post_cas_proof_rejects_snapshot_present_fanout_replacement",
        "PublicationRewriteTests.test_ac44_post_cas_proof_rejects_created_fanout_and_object_replacement",
        "PublicationRewriteTests.test_ac44_post_cas_proof_rejects_in_place_corruption_of_snapshot_present_graph_objects",
        "PublicationRewriteTests.test_ac44_shallow_boundary_is_rejected_before_rewrite_mutation",
        "PublicationRewriteTests.test_ac44_post_cas_shallow_boundary_race_rolls_back_exact_ref",
        "PublicationRewriteTests.test_ac44_post_cas_rewrite_rolls_back_after_same_inode_graph_corruption",
        "PublicationRewriteTests.test_ac44_post_cas_snapshot_exception_uses_exact_rollback_and_recovery_evidence",
        "PublicationRewriteTests.test_ac44_post_cas_ancestor_exception_uses_exact_rollback_and_recovery_evidence",
        "PublicationRewriteTests.test_ac44_post_cas_proof_exception_contention_never_overwrites_concurrent_ref",
        "PublicationRewriteTests.test_ac44_post_cas_recovery_snapshot_exception_is_recovery_required",
        "PublicationRewriteTests.test_ac44_fetch_head_and_merge_head_parse_every_canonical_oid",
        "PublicationRewriteTests.test_ac44_malformed_or_partially_parsed_multi_oid_pseudoref_fails_closed",
        "PublicationRewriteTests.test_ac44_post_cas_rollback_failure_preserves_recovery_evidence",
        "PublicationRewriteTests.test_ac44_concurrent_ref_after_cas_is_detected_and_target_is_rolled_back",
    ),
    "receipt_gate": (
        "PublicationGateTests.test_receipt_is_bound_to_candidate_and_pr_body",
        "PublicationGateTests.test_preexisting_only_pass_receipt_is_accepted_and_counts_fail_closed",
    ),
}


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    case_ids = []
    for group, names in CASE_GROUPS.items():
        for name in names:
            case_id = "test_publication." + name
            loaded = loader.loadTestsFromName(name, test_publication)
            if loaded.countTestCases() != 1:
                raise RuntimeError(f"Pre-controller case is missing or ambiguous: {group}:{case_id}")
            suite.addTests(loaded)
            case_ids.append(case_id)

    inventory_path = ROOT / ".agentic" / "validation" / "five-slot-adversarial-regressions.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory_rows = inventory["regressions"]
    inventory_suite = unittest.TestSuite()
    inventory_cases = []
    for index, row in enumerate(inventory_rows):
        module = INVENTORY_MODULES.get(row["evidence_path"])
        if module is None:
            raise RuntimeError(
                f"No pre-controller test module is registered for inventory[{index}]: "
                f"{row['evidence_path']}"
            )
        test_id = row["test_id"].replace("::", ".")
        loaded = loader.loadTestsFromName(test_id, module)
        if loaded.countTestCases() != 1:
            raise RuntimeError(
                f"Pre-controller inventory case is missing or ambiguous at index {index}: "
                f"{row['regression_id']}:{test_id}"
            )
        case = next(iter(loaded))
        inventory_suite.addTests(loaded)
        inventory_cases.append((row["regression_id"], test_id, case))

    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    inventory_result = unittest.TextTestRunner(verbosity=2).run(inventory_suite)
    elapsed = time.monotonic() - started
    failed_cases = {id(test) for test, _ in inventory_result.failures + inventory_result.errors}
    skipped_cases = {id(test) for test, _ in inventory_result.skipped}
    inventory_statuses = [
        "FAIL" if id(case) in failed_cases else
        "UNOBSERVED" if id(case) in skipped_cases else "PASS"
        for _, _, case in inventory_cases
    ]
    inventory_passed = (
        inventory_result.testsRun == len(inventory_rows)
        and all(status == "PASS" for status in inventory_statuses)
    )
    passed = result.wasSuccessful() and inventory_result.wasSuccessful() and inventory_passed
    print(json.dumps({
        "gate": "publication_pre_controller_adversarial",
        "status": "PASS" if passed else "FAIL",
        "groups": {key: len(value) for key, value in CASE_GROUPS.items()},
        "cases": len(case_ids),
        "run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "inventory": {
            "members": len(inventory_rows),
            "regression_ids": [regression_id for regression_id, _, _ in inventory_cases],
            "test_ids": [test_id for _, test_id, _ in inventory_cases],
            "statuses": inventory_statuses,
            "run": inventory_result.testsRun,
            "failures": len(inventory_result.failures),
            "errors": len(inventory_result.errors),
            "skipped": len(inventory_result.skipped),
        },
        "elapsed_seconds": round(elapsed, 3),
    }, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
