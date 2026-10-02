"""Synthetic atomic review admission builder for final-gate unit fixtures."""
from agentic.canonical import fingerprint
from agentic.review_completion import gate_review_aggregate, gate_review_tuple

NOW = "2026-09-09T12:00:00Z"


def bind_review_admission(bundle):
    """Refresh test-only completion evidence after a deliberate fixture edit."""
    aggregate = gate_review_aggregate(
        bundle["candidate"], bundle["critic"], bundle["specialists"])
    tuple_value = gate_review_tuple(bundle["candidate"], bundle["contract"], aggregate)
    reviewers = aggregate["required_reviewers"]
    old = bundle.get("review_submission", {})
    cycle_id = old.get("cycle_id", "11111111-1111-4111-8111-111111111111")
    results = [{"reviewer_id": reviewer, "state": "ACCEPTABLE",
                "result_sha256": fingerprint("reviewer-result", {
                    "reviewer": reviewer, "verdict": "APPROVE", "findings": []}),
                "terminal_at": NOW} for reviewer in reviewers]
    snapshot = {"cycle_id": cycle_id, "tuple": tuple_value,
                "tuple_sha256": fingerprint("review-tuple", tuple_value),
                "required_reviewers": reviewers,
                "reviewer_set_sha256": fingerprint("reviewer-set", reviewers),
                "counts": {"required": len(reviewers), "completed": len(reviewers),
                           "acceptable": len(reviewers), "failed": 0, "stale": 0,
                           "outstanding": 0}, "results": results}
    bundle["review_submission"] = {
        "submission_id": old.get("submission_id", "22222222-2222-4222-8222-222222222222"),
        "cycle_id": cycle_id, "completion_snapshot": snapshot,
        "completion_snapshot_sha256": fingerprint("review-completion", snapshot),
        "aggregate": aggregate, "aggregate_sha256": fingerprint("review-aggregate", aggregate),
        "provider_preconditions": {"repository": tuple_value["repository"],
            "base_sha": tuple_value["base_sha"], "head_sha": tuple_value["head_sha"],
            "head_tree_sha": tuple_value["head_tree_sha"],
            "tuple_sha256": snapshot["tuple_sha256"],
            "reviewer_set_sha256": snapshot["reviewer_set_sha256"]},
        "execution_authority": False}
    return bundle["review_submission"]
