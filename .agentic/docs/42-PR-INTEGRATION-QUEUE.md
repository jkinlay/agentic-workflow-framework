# Reviewed epic PR integration queue

The optional epic queue tests interaction without changing owner review or
merge sources. Task branches remain merge sources; `awf/int/<epic>` is
disposable evidence. Serial mode is the default.

## Authority and state

Epic mode needs a closed record from reviewed queue configuration or an
epic-bound owner instruction. A fresh, receipted runtime observer first matches
the exact accepted `PROJECT_CONFIG` bytes and digest. Only then may the queue
derive repository identity or trusted owners. The binding is re-observed at
each authority use and also binds Jira policy before closeout counts.

Mode observers authenticate activation, owner and source against that accepted
configuration. Reorder, skip and discretionary removal re-observe one-use
owner instructions. Enforced mode separately observes that
`awf-integration` comes from the configured App integration ID. Advisory mode
still refuses pending entries. Activation and App binding are refreshed before
admission and readiness; stored evidence is not perpetual authority. Epic mode
permits merge commits only.

The steward has no model route or tokens, cannot edit task branches, and never
pushes or merges the target. Provider work stays behind adapters. Its SQLite
ledger lives outside worktrees; a write transaction holds the admission lock.
Schemas and templates remain a generator-turn follow-up.

## Entries and builds

An entry binds PR, ticket, head, branches, dependencies, validation, paths,
ordered commits and patch ID. Independent PRs target the epic target; stacked
entries target their last dependency. Admission and head replacement observe:

- the exact, non-shared commit range and stable patch ID; and
- successful validation bound to PR/head, requirements digest, accepted-policy
  digest, commands and results.

A caller timestamp or patch ID is insufficient. Extra, reordered, shared or
reused commits are rejected. The admission patch observation and producer
receipt remain the baseline for later retarget comparison.

Initialization and target rebuilds observe the exact target tip. Admissions run
alone under the lock. Each build input binds target, every ordered PR/head and
the accepted test command. Construction resolves every merge step; tests bind
each prefix commit and tree. Identical inputs must produce the same result tree,
although per-run merge commit IDs may differ. A divergent tree fails closed.

Conflict evidence comes from a receipted failed-merge observation. It binds the
candidate and actual conflict paths and commits. When a prefix exists it also
binds the current build ID, complete ordered prefix, and integration result
commit/tree; a failure against the target alone does not qualify. Commit
ownership remains reserved.

A red admission removes only the new entry. A red rebuild ejects the first
failing prefix entry and quarantines its dependent closure for revalidation. No
failure edits another branch. Target move, head change, removal, reorder, skip
and post-merge processing each create a new build generation. Readiness
reconstructs the complete immutable build and verifies its registered producer
receipt, ordered input, construction, prefix executions and consumed run; a
caller-authored green summary never qualifies.

## Ordering and readiness

Dependencies stay first and cannot be skipped while needed. Review-failure and
amendment removal require a registered lifecycle/review observation bound to
the exact PR/head and reason. After the authenticated mode record's stall
interval, the next independent entry may move first with owner notice.

A merged dependency is removed only after a fresh receipt binds the exact front
PR/head, old target and result tip. Dependents are retargeted. Review and
authorization survive only when the post-retarget patch equals the receipted
admission patch; readiness re-observes that equivalence. A changed patch returns
to amendment and review.

Only position one can receive success, and every dependency must remain earlier
in the queue. The framework re-evaluates the complete evidence bundle and
requires the stored final gate to match. Authorization then verifies exact raw
owner text, trusted numeric owner, expiry and candidate binding. Its producer
must be a complete verifier run independent of implementation and review.
Caller hashes or an `authenticated` flag do not qualify.

Overlapping paths since the review base require a fresh independent critic run
bound to head/base, target/build/tree, the exact reviewed contribution R and
effective contribution R-prime. Separate digests bind both contributions and
the result. A clean result consumes a run; a code finding consumes a round.

After merge, the queue observes the new target and rebuilds before admitting the
next front. Fresh observed target CI chooses rebuild or a revert PR and never
authorizes a target push. Post-merge counts revalidate Git objects and accepted
Jira enablement, provider identity and Done status. Enabled Jira also needs the
matching observed transition and readback; disabled Jira invents none.
Empty-queue closure records integration-branch deletion without authority.

## Reference entry point

Use `.agentic/scripts/integration_queue.py --help`; keep `--state` outside the
checkout. Mutations require in-process authenticated adapters. The CLI grants
no Git, CI, review, Jira or merge authority.
