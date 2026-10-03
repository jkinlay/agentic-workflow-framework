# Review completion barrier

Version 1.9.3. Final review aggregation and provider submission use
`ReviewCompletionStore` in protected controller state outside candidate and
reviewer worktrees. This ledger records coordination evidence; it grants no
merge, Jira, dispatch, or provider authority.

The production reference entry point is:

```text
python .agentic/scripts/workflow.py review-completion --state ABSOLUTE_PROTECTED_DB --worktree-root ABSOLUTE_WORKTREE freeze|dispatch|record|status|prepare|complete|reconcile|recover ...
```

Keep the SQLite path on controller-owned storage outside every candidate and
reviewer worktree. The CLI performs state transitions only; a reviewed host
adapter is still responsible for launching reviewers or mutating a provider.

## Freeze before dispatch

Before launching any independent reviewer, freeze the complete required
reviewer set against the repository, base commit, head commit, head tree,
contract SHA-256, and review-input SHA-256. Persist `RUNNING` before each
dispatch and give the reviewer the returned cycle, tuple, reviewer-set, and
identity binding. Adding a reviewer or moving any candidate tuple field
invalidates the aggregate and requires a fresh cycle. After the first dispatch,
removing or replacing a required reviewer also requires a fresh, provider-observed
owner disposition. That disposition is bound to the unchanged exact tuple, old
cycle, old and new reviewer sets, removed identities, reason, configured owner
actor, provider identity, and a five-minute issue/review/expiry window. The
protected ledger retains the record and review digests in an append-only audit.
Equal freezes remain idempotent; supersets never need a weakening disposition.

Reviewer-removal authority is derived from the accepted
`.agentic/PROJECT_CONFIG.yaml`: `merge_gate.trusted_owner_ids` and the immutable
GitHub host, repository name, and numeric repository ID. The production CLI has
no flags that can replace those trust roots. A weakening request supplies only
the exact disposition record and its digest; a trusted host adapter must make a
fresh live provider observation. Its immutable artifact ID, approval, actor,
provider identity, old cycle, tuple, old/new sets, removed IDs, and reason must
all match. Provider artifacts are single-use. Caller-authored approval payloads,
stale observations, configuration mismatches, and artifact replay fail without
changing the active reviewer set or its audit.

Persist one terminal result for every required reviewer. `FAILED`,
`TIMED_OUT`, `MALFORMED`, `CANCELLED`, `STALE`, and `DUPLICATE` are completed observations
but never acceptable completion. Missing and running reviewers remain
outstanding. A duplicate is audited without replacing the first terminal
result and makes that reviewer's contribution unacceptable. Status always
reports `required`, `completed`, `acceptable`, `failed`, `stale`, and
`outstanding` counts.

`ACCEPTABLE` is derived from the captured result, not asserted independently:
the result must name the frozen reviewer, have verdict `APPROVE`, contain a
findings list with no unresolved item, and fit within the bounded result size.
Malformed, contradictory, or oversized results are refused. `CANCELLED` is a
terminal failed observation and cannot satisfy completion.

## Submit one immutable snapshot

`prepare_submission` takes an immediate SQLite writer transaction, rechecks the
exact tuple and reviewer set, and admits submission only when every reviewer is
terminal and acceptable. The transaction stores one immutable completion
snapshot and final aggregate before returning provider preconditions. A
provider adapter must enforce the durable submission operation UUID,
preparation time, repository, base, head, head-tree, tuple, reviewer-set,
completion-snapshot, and aggregate digest preconditions in its own conditional mutation. No adapter call is permitted
before admission. Completion and reconciliation reconstruct the admission from
protected state, fully revalidate it, reject any caller discrepancy, and accept
only an exact provider receipt observed at or after preparation. The mutation
callback's assertion alone is insufficient.

Once submission is prepared, later or duplicate reviewer results are audited
as late and cannot change the aggregate. A concurrent second submitter is
refused. If the provider call fails, or the controller restarts before its
receipt is durably recorded, state becomes `SUBMISSION_UNKNOWN`; use
`reconcile` with a matching observed receipt for the same operation and never
replay it blindly. A submitted verdict remains
immutable even if a later observation finds candidate movement.

Use the generated `review-completion` and `review-submission` contracts for
portable status and provider admission records. The final evidence bundle must
carry the exact `review-submission`. Gate evaluation binds its candidate,
contract, critic and specialist record digests to that admission and embeds it
in the final gate. Authorization request creation, authorization verification,
the user-facing gate handoff, and `FINAL_GATE_PASSED` lifecycle transition all
revalidate the embedded admission. Removing, replacing, or rebinding it fails
closed before readiness. `ReviewCompletionStore.submit` remains the sole
provider-submission route; its mutation callback cannot run before the atomic
admission exists.
