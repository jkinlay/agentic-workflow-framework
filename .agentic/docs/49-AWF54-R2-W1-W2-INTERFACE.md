# AWF-54 R2 W1/W2 frozen interface (1.9.5)

Status: FROZEN by devhead-1521 and devhead-1524 (19 Oct 2026). This file is committed, byte-identical, as the first commit on both R2 branches before either writer starts. Writers must not edit it. A change needs a DevHead ruling and lands identically on both branches.

Base: #215 head `078149203b856a8d592956d260f33948d3241b38` (T11), stacked. Merge order: W1 first; W2 then syncs main by merge only (no rebase) and regenerates the manifests.

## Acceptance (unchanged)

All nine R2 behaviour rows and doc 47 Q4/T8 stay in scope; no acceptance criterion is amended or dropped (`47-AWF54B-TEST-PLAN.md` T1-T14 and Q1-Q5; `43-MERGE-TRAIN.md`). Minimum-scope choices:

- The AWF-91 audit uses typed events over the existing AWF-91 `Store` (fixed namespace) rather than a dedicated table. Uniqueness, immutability, validation and release-lifetime retention are identical to Q4/T8.
- Jira alias input is REJECTED before dispatch. Only the provider-observed canonical issue key is accepted, and ordinary and batch paths enforce the same identity (D-1217-1). A test proves the rejection.
- Out of 1.9.5: standalone merge-train CLI (AWF-161; the existing refusal stays), legacy audit migration (AWF-160), hosted-CI culprit diagnosis (AWF-162).

## Ownership (exact; a writer needing any other path STOPS and asks)

W1 - candidate, publication and audit:
`.agentic/lib/agentic/merge_train.py`, `.agentic/lib/agentic/store.py`, `.agentic/scripts/merge_train.py` (only if unavoidable; the refusal stays), `.agentic/tests/test_merge_train_publication.py`, `.agentic/tests/test_store_regressions.py`, `.agentic/tests/test_merge_train_operational.py` (new), `MANIFEST.json`, `MANIFEST.md`.

W2 - controller entrypoint, closeout, Jira and counting:
`.agentic/lib/agentic/continuous_controller.py`, `.agentic/lib/agentic/closeout.py`, `.agentic/scripts/continuous_controller.py`, `.agentic/tests/test_merge_train_controller.py`, `.agentic/tests/test_merge_train_closeout.py`, `MANIFEST.json`, `MANIFEST.md`.

Shared, read-only for both: gates, provenance, authorization, integration_build, configuration, installer, generators, every other test, and this file.

## Contract

1. W1 owns: accepted-config scheduling with slots and max size; durable slot and candidate identity; draft-first publication through registered, authenticated publishers (no merge or target-push verb, T12); orphan reconciliation without replay; the ready step bound to the exact published batch; full exact-head CI and frozen-review binding; durable REMOTE_ONLY_CI_FAILURE abandonment with no culprit guess; operator eject with a recorded decision and rebuild from the frozen target; the AWF-91 audit append before the first provider mutation (an append failure blocks the mutation).
2. W1 exposes, in `merge_train.py`, a coordinator that takes reviewed registrations and explicit operation requests and returns or loads a **retained candidate**. Callers never supply evidence as "authenticated"; W1 observes it through the registered adapters.
3. Retained-candidate fields (validated on read): `repository_id`, `batch_id`, `generation`, `pr`, `head`, `tree`, `base`, `state`, `audit_sha256`, and ordered `sources`. Each source carries `pr`, `head`, canonical `issue_key` and `paths`. States: `DRAFT`, `READY_AUTHORIZED`, `CI_GREEN`, `ABANDONED`, `MERGED_OBSERVED`. A candidate in `ABANDONED` is terminal; it can never be published, made ready or closed out.
4. W1's audit API in `store.py` is append/read over AWF-91 events: key = repository_id + batch_id; same canonical bytes on retry is a no-op returning the prior record; conflicting bytes are refused; reads validate namespace, key, candidate identity, bytes and digest; duplicate-insert/REPLACE is blocked as strictly as the controller ledger's no-replace trigger. There is no purge path. The controller-local T8 ledger stays readable and unchanged.
5. W2 owns: controller batch commands and reviewed-adapter loading; Jira alias rejection before dispatch; authenticated exact-main observation of the batch commit; mapping the retained candidate to ordered constituent closeout; Jira ONLY through T11 `production_batch_jira_reconciliation` (closeout.py's independent writer is not used for batch constituents); restart with remaining-only recovery and UNKNOWN preserved; count refusal for held, unknown or unresolved constituents.
6. W2 consumes W1 only through items 2-4. It creates no publication or audit journal of its own. W1 never calls a Jira writer.
7. Until W1 merges, W2's tests use a fixture that implements items 2-4 exactly. After W1 merges, W2 merge-syncs and replaces the fixture with the real coordinator; every assertion stays.
8. Tests first and red on this base; Tier 3, cap 3; no test deleted, skipped or weakened; #215's safety regressions preserved; manifests regenerated last.
