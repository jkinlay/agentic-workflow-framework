# Continuous controller contract

Version 1.9.3. While scoped work remains, the controller continues every configured stream until completion, an owner stop, or a recorded stream-specific blocker. `ContinuousControllerStore` persists decisions and grants no host, provider, Jira, or merge authority.

## Production entry point

Run production operations only through the workflow entry point:

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

`cycle` calls `production_controller_cycle`: observe authenticated inventory, schedule, persist a nonce-bound dispatch intent with prepared/begun times, invoke dispatch or uncertain-operation observation, deliver the cadence digest, and acknowledge only fresh exact delivery readback. The adapter is loaded from the exact UTF-8 bytes matching its SHA-256 pin. It must be an absolute regular single-link file of at most 1 MiB exposing `build_adapters(config)`. No shell string or unpinned executable is accepted. Missing operations fail closed.

`jira-lifecycle` uses the same pinned adapter for read-before-write, at most one mapped write, and independent readback. `merge-observed` validates the merge before reconciliation and a fresh scoped Jira count. `snapshot` is read-only.

Protected state must be absolute and outside every declared worktree. The state factory rejects links, reparse points and hardlink aliases, applies owner-only permissions, and checks SQLite integrity, application identity and schema version on every open.

## No silent idle state

Each stream is `WORKING`, `PAUSED_INPUT`, `BLOCKED`, or `COMPLETE`. Every row names its ticket, actor, reason, next action, resume trigger, exact tuple, activity, verification gate, reviewer counts, findings and Jira observation. `COMPLETE` means the current scoped backlog has no remaining action.

Selection is deterministic by priority and ticket identity. It preserves one writer per overlapping path and checks dependencies, budgets, run and amendment caps, reviewer independence and observed host capacity. The production cycle pins one absolute worktree and exact Git head/tree. Git index spelling supplies the canonical path inventory. Every directory, subtree and wildcard claim expands across all pinned tracked descendants, whose physical identities are checked. Case aliases collapse to Git spelling. Hardlinked names share one canonical ownership key, so only one writer can run. Case collisions, missing or uncertain physical identity, paths without a canonical parent, Git symlinks, nested repositories, filesystem symlinks, junctions and reparse points fail closed. The protected ledger refuses a later repository/worktree binding change. A blocked stream never pauses eligible work elsewhere.

Reviewer counts are admitted only when `required = completed + outstanding` and `completed = acceptable + failed + stale`. Contradictory inventory fails before scheduling, dispatch or status delivery. Running work is revalidated on every inventory observation. A completed ticket must be absent or terminal before refill.

## Dispatch and cadence

Dispatch intent is durable before host calls. `PENDING`, `IN_FLIGHT`, and `UNKNOWN` tickets reserve globally; the latter two also consume host capacity. Fresh preflight cancels never-begun pending intents that lose admission, so later eligibility can prepare a new intent. Unknown reconciliation updates a stream only while its ticket and tuple still match; late observations cannot overwrite reassigned work. Other streams use remaining capacity.

Cadence defaults to 600 seconds. Existing state keeps its stored value; `--migrate-status-cadence` explicitly applies the configured cadence, while mismatch otherwise fails closed. `--disable-periodic-status` suppresses scheduled digests, not immediate blocker, failure, input-required or merge-ready `CHANGE` digests. Pending outbox items replay unchanged until exact readback; newer changes follow afterward. Changes do not reset the regular deadline. Restarts preserve pending delivery; clock rollback fails closed. Digests cover each stream/gate and unresolved UNKNOWN intent (ticket, tuple, detached flag).

## Jira lifecycle and merge progress

Jira disabled means no read or write. Otherwise lifecycle production binds cloud, site, provider project/key, controller actor and issue from accepted configuration through durable intent and every receipt. It first recovers in-flight operations as `UNKNOWN`, reads the bound issue and reconciles every unknown durable intent before planning. Operation and record UUIDs derive deterministically from the immutable intent. The protected ledger persists the intent as `PENDING`, commits `IN_FLIGHT` before provider mutation, and preserves the same operation ID over restart. A lost response is never reissued: a current target-state readback completes it, while any unproven result remains `UNKNOWN` and stops writes for that ticket. No caller supplies prior-write history. A new write requires exact bound pre-read, operation receipt and readback; identity mismatch or timestamp reversal fails closed while other streams continue.

After a validated merge, reconcile the merged ticket first. Only then page through the complete configured scope. Every page must share the scope digest, snapshot ID and observation time, and that observation must be at or after reconciliation. Counts use stable ticket IDs and terminal categories, exclude Epics unless requested, and enforce page, item, byte, time and cursor bounds. Any missing, stale, duplicate, partial or mismatched evidence reports both counts as `UNOBSERVED`.

Final review submission separately requires the [review completion barrier](33-REVIEW-COMPLETION-BARRIER.md).
