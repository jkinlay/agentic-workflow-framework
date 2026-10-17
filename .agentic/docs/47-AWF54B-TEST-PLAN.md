# AWF-54B merge-train tests-first plan

`main` basis: `0cf694097853ea8311bf3c6746a974e25a35af14`.
This Tier 3/`FULL` design adds no runtime, schema, or tests.
54B requires accepted 54A/PR #174 and grants no merge authority.

## Decisions and acceptance mapping

Fixed rulings: Q1=A, so a fresh provider-observed exact-head
owner review admits a source only and is never batch authorization; Q2=B keeps
adopters on and this AWF repository off; Q3=A delivers sequential 54A then 54B
PRs; Q4=B requires source owner review only when its tier's normal final gate
does.

| AWF-54 outcome | Owner | Planned proof |
| --- | --- | --- |
| Authenticated admission, deterministic prefix construction/receipts, dependency/approval/PR ordering, generation invalidation, conflict classification, and strict batch-parent provenance | 54A | PR #174 tests; 54B does not repeat them. |
| Eject the failing source and rebuild from the frozen target | 54B orchestration over 54A policy | T7 |
| Authenticated publication with freeze-race recovery | 54B | T1-T3 |
| Batch lifecycle, exact-head CI, final gate, mark-ready, and batch-only authorization | 54B | T4-T6 |
| One immutable audit record per batch | 54B | T8 |
| Constituent closeout only as `merged-via-batch` after the batch commit is on `main`; controller-only reconciliation | 54B | T9-T11 |
| AWF never merges a batch or pushes the target | 54B | T12 |
| Windows and Linux support | 54B | T14 |
| Adopter-on/AWF-off configuration projections | 54B | T13 |

Fixtures use temporary repositories and POSIX-style member paths, without
network. Targets: `P=.agentic/tests/test_merge_train_publication.py`,
`L=.agentic/tests/test_merge_train_lifecycle.py`,
`C=.agentic/tests/test_merge_train_closeout.py`,
`J=.agentic/tests/test_merge_train_controller.py`,
`M=.agentic/tests/test_merge_train.py`, and
`G=scripts/tests/test_generate_contracts_merge_train.py`.

## Named tests

| ID / test | Fixture | Expected failure on main | Pass condition | Target |
| --- | --- | --- | --- | --- |
| T1 `test_publication_requires_authenticated_exact_batch_candidate` | Frozen batch; forged/stale provider observations. | No train publisher. | Only fresh registered exact-head/tree/base publication mutates provider state. | P |
| T2 `test_freeze_is_rechecked_before_every_provider_mutation` | Target/source/evidence drift injected before push, PR creation, and mark-ready. | No mutation path. | Each drift refuses before that call and abandons the generation. | P |
| T3 `test_push_pr_creation_orphan_race_is_recovered_without_replay` | Push succeeds; PR response is lost; restart observes provider. | No recovery state. | Durable intent is reconciled once; no duplicate push/PR. | P |
| T4 `test_batch_final_gate_requires_full_exact_head_ci_and_frozen_reviews` | Draft CI, stale/full CI, changed head, reviewer set, and gate. | No batch gate. | Only full CI and complete frozen reviews for the exact batch pass. | L |
| T5 `test_batch_mark_ready_requires_authenticated_exact_head_authorization` | Draft batch PR and forged/stale/mismatched approval. | No batch lifecycle. | Logged provider-observed approval precedes mark-ready; refusal keeps draft. | L |
| T6 `test_source_review_cannot_authorize_batch_merge` | Valid source reviews plus batch authorization variants. | 54A only rejects a direct record. | Exact-head final gate precedes fresh batch-bound owner authorization permitting human-merge handoff; gate consumes neither authorization nor merge-observation. | L |
| T7 `test_failed_source_is_ejected_and_generation_rebuilt_from_frozen_target` | Three sources; middle local prefix fails. | 54A returns policy only. | Middle/dependents are ejected as specified; rebuilt prefixes start at the same frozen target. | M |
| T8 `test_each_batch_audit_record_is_immutable` | External store; retry, collision, attempted rewrite/delete. | No batch ledger. | One candidate-bound record appends per batch; retry is stable and mutation refuses. | L |
| T9 `test_ordinary_closeout_refuses_batch_constituent` | Constituent and ordinary merge evidence. | No batch closeout. | Ordinary closeout refuses before Jira access. | C |
| T10 `test_batch_closeout_waits_for_commit_on_main_then_marks_merged_via_batch` | Missing/wrong/right observed target commit. | No constituent contract. | Only the exact batch commit on `main` closes every bound constituent with batch identity. | C |
| T11 `test_only_controller_reconciles_constituent_jira` | Disabled/enabled Jira, worker/steward/controller, mismatch/readback spies. | No batch reconciliation. | Non-controller paths make zero calls; controller uses read/write/read once and stops a mismatched ticket. | J |
| T12 `test_merge_train_exposes_no_merge_or_target_push_api` | Publisher/CLI plus adapter spies. | Publisher surfaces absent. | Publication may push only the batch ref; no merge/target-push verb exists. | P |
| T13 `test_merge_train_config_defaults_adopter_on_awf_off` | All generated config projections. | Contract absent. | Q2 default is reproduced and generator check is clean. | G |
| T14 `test_merge_train_fixtures_are_portable_on_windows_and_linux` | Temporary roots and POSIX members. | 54B suite absent. | Identical assertions pass both installed-runtime CI jobs; no drive/UNC fixture. | M |

## Planned files, order, and open questions

54B plans `.agentic/lib/agentic/{merge_train,gates,closeout,continuous_controller}.py`,
`.agentic/scripts/merge_train.py`, `.agentic/workflow.yaml`, generated batch and
constituent-closeout schemas/templates plus every `PROJECT_CONFIG.yaml`
projection through `scripts/generate_contracts.py`, targets P/L/C/J/M/G, docs
23/29/30/34/41/42/43, and manifests regenerated last. Any other path needs an
owner-approved scope amendment.

The generator slot is 54B after 54A; AWF-54 remains before AWF-34 in
`115 -> 36 -> 88 -> 117 -> 50 -> 54 -> 34`.

Open owner questions for 54B: exact batch branch/PR naming and cadence; the
authenticated publisher adapter; orphan-state recovery contract; batch
lifecycle/final-gate fields; audit schema, location, retention, and purge;
constituent closeout/reconciliation ordering and partial-failure recovery; and
the required Windows/Linux CI job names.
