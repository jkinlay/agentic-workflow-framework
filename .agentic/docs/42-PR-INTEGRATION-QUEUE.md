# Reviewed epic PR integration queue

The optional epic queue makes interaction testing explicit without changing
what the owner reviews or merges. Each task keeps its own branch and PR. The
disposable `awf/int/<epic>` branch is evidence only; it never becomes a merge
source. Serial mode remains the default.

## Authority and state

Enable epic mode only through reviewed queue configuration or a recorded owner
instruction. Enforced mode also requires an observed `awf-integration` required
check from the configured App integration ID. Advisory mode still refuses to
make a pending entry merge-ready. Epic mode permits merge commits only.

The steward is deterministic infrastructure: it has no model route, consumes
no model tokens, does not edit task branches, and never pushes or merges the
target. Provider operations remain behind adapters. Store its SQLite ledger on
controller-owned storage outside every worktree. A SQLite write transaction is
the queue lock and remains held while an admission runner builds and tests the
candidate. The audit is append-only.

The current narrowing ships pure logic and the entry point before generated
contracts. Schema and template validation is a marked generator-turn follow-up;
the code does not depend on schemas absent from this release head.

## Entries and builds

An entry binds a numeric PR, ticket, reviewed head, task/base branches,
dependencies, validation time, reviewed paths, its ordered commit list, and a
stable patch ID. Its identity binds the epic, PR, and head. A dependent entry
is admitted after its dependencies and targets its last dependency's branch.
The own-commit check rejects extra, reordered, or shared task commits.

Every admission occurs alone under the lock. The provider runner receives a
snapshot containing the newly appended entry and returns one result for every
ordered prefix. A green result retains the entry. A conflict records paths and
returns only that entry to amendment. A red admission removes only the new
entry. A red rebuild removes the entry at the first failing prefix, so blame
follows order rather than timing. No failure path edits another branch.

A build binds its epic, generation, target SHA, ordered `(PR, head)` pairs,
result commit and tree, test command, prefix results, and trigger. Repeated
inputs produce the same build identity. Target movement, head change, removal,
reorder, skip, and post-merge processing require a new build. Older evidence
cannot satisfy the current-target check.

## Ordering and merge readiness

Owner reorder and skip operations need closed owner-instruction records, then
a rebuild. Dependencies may not move behind their dependents, and a dependency
with an admitted dependent cannot be skipped. After the stall threshold, the
next independent entry may move to the front; its rebuild and required owner
notification are recorded. A merged dependency is removed and its dependents
are retargeted. An unchanged stable patch ID retains review evidence; a changed
one requires review.

Only queue position one can receive `awf-integration: success`. It also needs a
green build on the current target beginning with the exact PR/head, plus valid
review, final-gate, and owner-authorization evidence bound to the exact
PR/head/target tuple. Any absent, stale, mismatched, or pending evidence keeps
the status pending with a reason.

If paths merged since the review base overlap the reviewed paths, a fresh
critic delta review is required. It consumes a run but no review round when
clean. A finding that requires code is unsatisfied and consumes the normal
amendment round. No overlap needs no delta review.

After an observed merge, rebuild from the new target before admitting the next
front. Post-merge target CI failure requests a revert PR; it never authorizes a
target push. Post-merge counts are allowed only after observed Jira
reconciliation when Jira is enabled. Disabled Jira requires no invented
transition. When no entries remain, closing the epic records deletion of the
integration branch but grants no execution authority.

## Reference entry point

Use `.agentic/scripts/integration_queue.py --help`. JSON/YAML inputs are strict
records; successful output is JSON. Queue commands require `--state` outside
the checkout. `initialize`, `admit`, `conflict`, `rebuild`, `reorder`, `skip`,
`stall`, `retarget`, `check`, and `close` mutate or inspect the protected
ledger; `head-change` and `remove` invalidate and rebuild changed membership.
`mode`, `delta`, `steward-policy`, and `worker-guidance` expose pure
validation results. The CLI validates evidence; it is not a Git, CI, review,
Jira, or merge adapter.
