# Continuous controller contract

Version 1.9.3. While a release or assigned backlog has work, the controller
continues every configured stream until scoped completion, an explicit owner
stop, or a recorded stream-specific blocker. The durable reference is
`ContinuousControllerStore`; it records decisions and grants no dispatch,
provider, Jira, or merge authority.

The production reference entry point is:

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE cycle|finish|digest|ack|snapshot ...
```

Use `cycle` with a fresh bounded inventory after each material observation and
`finish` immediately after a run terminates. The CLI records decisions and
pending digest deliveries. A reviewed host adapter remains responsible for
launching work, publishing a digest, Jira access, or any provider mutation.
Both ledgers use the same protected-state factory: it requires an absolute
path outside every declared worktree, rejects link/reparse and hardlink
aliases, applies owner-only file permissions, and checks SQLite integrity,
application identity, and schema version on every open.

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

Repository paths are canonical repository-relative paths. Absolute paths,
drive or UNC paths, parent traversal, and aliases that could bypass overlap
checks are refused. On every cycle the controller revalidates running work
against the new inventory, including disposition, prerequisites, capacity,
and ownership. A finished ticket cannot be immediately redispatched unless
the fresh inventory records it as complete or removes it.

## Status cadence

Emit one digest covering every configured stream while any is incomplete. The
default cadence is 900 seconds and `controller.status_cadence_seconds` may set a
positive replacement. A state or gate change can emit an immediate `CHANGE`
digest. That digest does not reset or suppress the next regular deadline. Each
digest includes exact tuple, activity, verification, reviewer completion,
findings, Jira, reason, next action, and observation time.

Digest delivery is an acknowledged outbox operation. `digest` replays the
same `delivery_id` until the transport succeeds and the operator calls `ack`;
only that acknowledgement advances the regular deadline. Restarts preserve
pending delivery, and a backwards clock observation fails closed. Once all
streams are complete, only a state change emits another digest.

The cadence defaults from `controller.status_cadence_seconds`; an explicit
positive CLI override is allowed for a reviewed host invocation.

## Merge and Jira progress

After observing a merge, first apply the controller-only conflict-safe Jira
reconciliation: read before write, perform at most one mapped transition, read
after, and require a receipt bound to immutable Jira cloud, project, actor,
issue, operation, and before/after status identities. Stop that ticket after
an unknown or mismatched outcome. A disabled
site reports `JIRA_DISABLED` and performs no Jira action.

Only after reconciliation succeeds, page through the complete configured Jira
scope. Count stable ticket identities by the adapter's terminal workflow
category; do not guess from status names. Exclude Epics unless the scope
explicitly includes them. Report the exact filter scope, observation time,
`closed`, and `remaining_open`. If reconciliation, pagination, query
completeness, identity uniqueness, or category mapping is unproved, report both
counts as `UNOBSERVED` rather than publishing a partial or stale number.
Every page must share one query-scope digest, snapshot identifier, and
observation time. Page count, item count, canonical byte size, elapsed time, cursor progression,
and identity uniqueness are bounded. A complete, stable snapshot reports
`COUNTED`; reconciliation without a complete count reports `RECONCILED` with
both counts unobserved.

Final review submission separately requires the
[review completion barrier](33-REVIEW-COMPLETION-BARRIER.md).
