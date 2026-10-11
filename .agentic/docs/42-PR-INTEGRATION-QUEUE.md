# Reviewed epic PR integration queue

The optional epic queue tests interaction without changing owner review or
merge sources. Each task keeps its branch and PR; `awf/int/<epic>` is disposable
evidence. Serial mode remains the default.

## Authority and state

Enable epic mode only through a closed, identity-checked record produced from
reviewed queue-configuration provenance or a recorded owner instruction bound
to the epic. The App-binding observation is part of that record. Enforced mode
requires `awf-integration` from the configured App integration ID. Advisory
mode still refuses pending entries. Epic mode permits merge commits only.

The deterministic steward has no model route or tokens, cannot edit task
branches, and never pushes or merges the target. Provider work stays behind
adapters. Its append-only SQLite ledger lives outside worktrees; a write
transaction holds the queue lock while admission builds and tests.

Schemas and templates remain a marked generator-turn follow-up; this pure logic
does not require contracts absent from the release head.

## Entries and builds

An entry binds a numeric PR, ticket, reviewed head, task/base branches,
dependencies, validation time, reviewed paths, its ordered commit list, and a
stable patch ID. Its identity binds the epic, PR, and head. An independent PR
targets the epic target; a dependent entry targets its last dependency branch.
Admission and head replacement require the separately observed diff-commit
list. Extra, reordered, shared, or previously used task commits are rejected,
including commits retained in history after removal.

Every admission occurs alone under the lock. The provider runner receives a
snapshot with the new entry and returns a closed observation. Its input identity
binds the target, ordered heads, and command;
its construction trace resolves every merge base/head/result commit/tree, and
each test result binds its prefix commit and tree. Identical inputs with a
different construction or test result fail closed. A green result retains the
entry. A conflict records paths and returns that entry to amendment. A red admission removes only the new
entry. A red rebuild removes the entry at the first failing prefix, so blame
follows order rather than timing. No failure path edits another branch.

A build binds its epic, target SHA, ordered `(PR, head)` pairs, observed
construction, result commit and tree, test command, prefix results, producer,
and trigger. Generation is audit metadata; repeated inputs keep one identity.
Target movement, head change, removal, reorder, skip, and post-merge processing
require a build. Older evidence cannot satisfy the current-target check.

## Ordering and merge readiness

Owner reorder and skip operations need one-use closed instructions bound to the
exact requested order or PR, then a rebuild. Dependencies stay before
dependents and cannot be skipped while needed. The stall clock starts when the
exact PR/head becomes front. After the threshold, the next independent entry
may move first; its rebuild and owner notification are recorded. A merged
dependency is removed and its dependents
are retargeted. An unchanged stable patch ID retains review evidence; a changed
one requires review.

Only position one can receive `awf-integration: success`. It needs a green
current-target build beginning with the exact PR/head, plus valid
closed review, final-gate, and owner-authorization records. Their identities,
producers, gate hash, authorization source, expiry, and exact PR/head/target
bindings are checked together. Absent, stale, mismatched, expired, or pending
evidence stays pending with a reason.

If paths merged since the review base overlap the reviewed paths, a fresh
critic delta review is required. It binds the reviewed head/base, current
target/build, ordered intervening-merge digest, and R-versus-R-prime diff
digest, so it cannot replay after either side changes. It consumes a run but no
review round when clean. A code finding is unsatisfied and consumes the normal
amendment round. No overlap needs no delta review.

After an observed merge, rebuild from the new target before admitting the next
front. Post-merge target CI failure requests a revert PR; it never authorizes a
target push. Post-merge counts are allowed only after observed Jira
reconciliation when Jira is enabled. The closed reconciliation record must
match ticket, PR, reviewed head, and merge commit, and show both a successful
transition and a `Done` readback. Disabled Jira requires no invented
transition. When no entries remain, closing the epic records deletion of the
integration branch but grants no execution authority.

## Reference entry point

Use `.agentic/scripts/integration_queue.py --help`; `--state` must be outside
the checkout. Inputs are strict JSON/YAML records and output is JSON. Admission
and head changes require observed diff commits. Build mutations require an
in-process provider-adapter runner: the CLI refuses standalone outcome
documents because it cannot authenticate or execute that adapter. It grants no
Git, CI, review, Jira, or merge authority.
