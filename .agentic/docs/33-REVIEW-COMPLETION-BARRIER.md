# Review completion barrier

Version 1.9.3. Final review aggregation and provider submission use
`ReviewCompletionStore` in protected controller state outside candidate and
reviewer worktrees. This ledger records coordination evidence; it grants no
merge, Jira, dispatch, or provider authority.

## Freeze before dispatch

Before launching any independent reviewer, freeze the complete required
reviewer set against the repository, base commit, head commit, head tree,
contract SHA-256, and review-input SHA-256. Persist `RUNNING` before each
dispatch and give the reviewer the returned cycle, tuple, reviewer-set, and
identity binding. Adding or removing a reviewer, or moving any candidate tuple
field, invalidates the aggregate and requires a fresh cycle.

Persist one terminal result for every required reviewer. `FAILED`,
`TIMED_OUT`, `MALFORMED`, `STALE`, and `DUPLICATE` are completed observations
but never acceptable completion. Missing and running reviewers remain
outstanding. A duplicate is audited without replacing the first terminal
result and makes that reviewer's contribution unacceptable. Status always
reports `required`, `completed`, `acceptable`, `failed`, `stale`, and
`outstanding` counts.

## Submit one immutable snapshot

`prepare_submission` takes an immediate SQLite writer transaction, rechecks the
exact tuple and reviewer set, and admits submission only when every reviewer is
terminal and acceptable. The transaction stores one immutable completion
snapshot and final aggregate before returning provider preconditions. A
provider adapter must enforce those tuple preconditions in its own conditional
mutation. No adapter call is permitted before admission.

Once submission is prepared, later or duplicate reviewer results are audited
as late and cannot change the aggregate. A concurrent second submitter is
refused. If the provider call fails, or the controller restarts before its
receipt is durably recorded, state becomes `SUBMISSION_UNKNOWN`; reconcile the
same operation and never replay it blindly. A submitted verdict remains
immutable even if a later observation finds candidate movement.

Use the generated `review-completion` and `review-submission` contracts for
portable status and provider admission records. Final-gate evaluation and
human merge authorization remain subsequent, separate requirements.
