# AWF-54B merge-train tests-first plan

`main` basis: `3519719af5dbb58032802532e599164c439a61d4`.
This Tier 3/`FULL` 54B-1 slice adds publication, orphan recovery, ejection,
batch-closeout runtime, and the named tests without adding a schema.
54B builds on accepted 54A/PR #174 and grants no merge authority.

54A/PR #174 owns admission, ordering, invalidation, conflict policy, and parent
provenance. 54B fixtures use temporary roots and POSIX member paths. Targets:
`P=test_merge_train_publication.py`, `C=test_merge_train_closeout.py`,
`M=test_merge_train.py`; `L`, `J`, and `G` remain separately owned.

## Named tests

| ID / test | Pass condition | Target |
| --- | --- | --- |
| T1 `test_publication_requires_authenticated_exact_batch_candidate` | Only fresh registered exact-head/tree/base publication mutates provider state. | P |
| T2 `test_freeze_is_rechecked_before_every_provider_mutation` | Drift before push, PR creation, or ready refuses that call and abandons the generation. | P |
| T3 `test_push_pr_creation_orphan_race_is_recovered_without_replay` | A lost response is reconciled once; push/PR creation is not replayed. | P |
| T4 `test_batch_final_gate_requires_full_exact_head_ci_and_frozen_reviews` | Only full CI and complete frozen exact-batch reviews pass. | L |
| T5 `test_batch_mark_ready_requires_authenticated_exact_head_authorization` | Observed approval precedes ready; refusal keeps draft. | L |
| T6 `test_source_review_cannot_authorize_batch_merge` | Source review never supplies batch merge authorization. | L |
| T7 `test_failed_source_is_ejected_and_generation_rebuilt_from_frozen_target` | The failure/dependents are ejected; rebuild starts at the frozen target. | M |
| T8 `test_each_batch_audit_record_is_immutable` | One candidate-bound immutable record exists per batch. | L |
| T9 `test_ordinary_closeout_refuses_batch_constituent` | Refusal precedes Jira access. | C |
| T10 `test_batch_closeout_waits_for_commit_on_main_then_marks_merged_via_batch` | Exact batch commit on `main` closes bound constituents with batch identity. | C |
| T11 `test_only_controller_reconciles_constituent_jira` | Controller read/write/read stops on mismatch; other roles make no calls. | J |
| T12 `test_merge_train_exposes_no_merge_or_target_push_api` | Only the batch ref may be pushed; no merge/target-push verb exists. | P |
| T13 `test_merge_train_config_defaults_adopter_on_awf_off` | Generated projections retain the accepted default. | G |
| T14 `test_merge_train_fixtures_are_portable_on_windows_and_linux` | Fixtures contain no drive/UNC path and pass both platforms. | M |

## Owner decisions for 54B-1

Ruling `devhead-1016-54b-answers` accepts the following answers in full:

1. Q1 batch naming and cadence (doc 47: "exact batch branch/PR naming and cadence"). ACCEPTED ANSWER: branch `batch/<release>-<yyyymmdd-hhmm>` (AWF-54 scope item 2) with a draft PR to main titled with the batch id and its constituent PR numbers; cadence at fixed batch slots (10:00 / 15:00) plus a configurable max batch size.
2. Q2 publisher adapter (doc 47: "the authenticated publisher adapter"). ACCEPTED ANSWER: a registered publisher modelled on 54A's observer registration (provider identity + receipt namespace, as in merge_train.py `_validate_registration`), whose capability is limited to pushing the batch ref and creating / marking ready the draft PR; it exposes no merge or target-push verb (T12); gh-backed.
3. Q3 orphan recovery (doc 47: "orphan-state recovery contract"). ACCEPTED ANSWER: write a durable intent record (batch id, generation, expected head) before every provider mutation; after a restart, observe the provider by branch name and head sha and reconcile exactly once; never replay a push or PR creation (T3).
4. Q4 audit schema (doc 47: "audit schema, location, retention, and purge"). ACCEPTED ANSWER: one append-only, candidate-bound sha256 record per batch in the AWF-91 store (#178); retained for the release lifetime; no purge in 1.9.5 except by owner ruling (T8). NOT implemented in 54B-1 (record the answer in doc 47 only).
5. Q5 closeout ordering (doc 47: "constituent closeout/reconciliation ordering and partial-failure recovery"). ACCEPTED ANSWER: closeout runs only after the exact batch commit is observed on main; constituents are closed in batch merge order as merged-via-batch; only the controller writes Jira (read / write / read back); on a mismatch, stop at that ticket, record progress, and resume idempotently from the remaining constituents.

The remaining open 54B questions are the batch lifecycle/final-gate fields and
the required Windows/Linux CI job names. Q4/T8 is reserved to AWF-91/#178;
T4-T6, T11, and T13 remain in their separately owned implementation slots.
