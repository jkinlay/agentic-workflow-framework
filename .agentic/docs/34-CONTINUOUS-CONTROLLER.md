# Continuous controller contract

Version 1.9.3. While a release or assigned backlog has work, the controller
continues every configured stream until scoped completion, an explicit owner
stop, or a recorded stream-specific blocker. The durable reference is
`ContinuousControllerStore`; it records decisions and grants no dispatch,
provider, Jira, or merge authority.

## No silent idle state

Each stream is exactly one of `WORKING`, `PAUSED_INPUT`, `BLOCKED`, or
`COMPLETE`. Every record names the ticket, actor, reason, next safe action, and
resume trigger, plus its exact tuple, activity, verification gate, frozen
reviewer counts, findings, and Jira observation. `COMPLETE` means no action
remains in the current scoped backlog; it does not invent replacement work.

After a worker or reviewer finishes, refill that stream in the same scheduling
transaction with the next eligible action. Otherwise record the concrete input
wait, blocker, or scoped completion. Schedule other streams independently: a
blocked or input-dependent stream never pauses eligible work elsewhere.

Selection is deterministic by configured priority and stable ticket identity.
It preserves one writer per overlapping path and checks dependencies, budgets,
run and amendment caps, reviewer independence, and observed host capacity.
Unavailable prerequisites produce a named `BLOCKED` record and resume trigger;
they never become inferred permission or capacity. Restart opens the same
SQLite ledger and preserves all stream states.

## Status cadence

Emit one digest covering every configured stream while any is incomplete. The
default cadence is 900 seconds and `controller.status_cadence_seconds` may set a
positive replacement. A state or gate change can emit an immediate `CHANGE`
digest. That digest does not reset or suppress the next regular deadline. Each
digest includes exact tuple, activity, verification, reviewer completion,
findings, Jira, reason, next action, and observation time.

## Merge and Jira progress

After observing a merge, first apply the controller-only conflict-safe Jira
reconciliation: read before write, perform at most one mapped transition, read
after, and stop that ticket after an unknown or mismatched outcome. A disabled
site reports `JIRA_DISABLED` and performs no Jira action.

Only after reconciliation succeeds, page through the complete configured Jira
scope. Count stable ticket identities by the adapter's terminal workflow
category; do not guess from status names. Exclude Epics unless the scope
explicitly includes them. Report the exact filter scope, observation time,
`closed`, and `remaining_open`. If reconciliation, pagination, query
completeness, identity uniqueness, or category mapping is unproved, report both
counts as `UNOBSERVED` rather than publishing a partial or stale number.

Final review submission separately requires the
[review completion barrier](33-REVIEW-COMPLETION-BARRIER.md).
