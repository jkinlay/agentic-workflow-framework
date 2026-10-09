# Continuous controller contract

Version 1.9.4. While scoped work remains, the controller continues every configured stream until completion, an owner stop, or a recorded stream-specific blocker. `ContinuousControllerStore` persists decisions and grants no host, provider, Jira, or merge authority.

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

Dispatch intent is durable before the host call. An interrupted call becomes `UNKNOWN` and is reconciled only by observation; it is never blindly replayed. Unrelated streams continue after one adapter failure.

The default digest cadence is 900 seconds; configured cadence must be positive. A change can produce an immediate `CHANGE` digest without resetting the next regular deadline. The acknowledged outbox replays one `delivery_id` until exact delivery readback succeeds. Restarts preserve pending delivery, and clock rollback fails closed. Every digest covers all streams and their exact gate state.

## Jira lifecycle and merge progress

Jira disabled means no read or write. Otherwise lifecycle production binds cloud, site, provider project/key, controller actor and issue from accepted configuration through durable intent and every receipt. It first recovers in-flight operations as `UNKNOWN`, reads the bound issue and reconciles every unknown durable intent before planning. Operation and record UUIDs derive deterministically from the immutable intent. The protected ledger persists the intent as `PENDING`, commits `IN_FLIGHT` before provider mutation, and preserves the same operation ID over restart. A lost response is never reissued: a current target-state readback completes it, while any unproven result remains `UNKNOWN` and stops writes for that ticket. No caller supplies prior-write history. A new write requires exact bound pre-read, operation receipt and readback; identity mismatch or timestamp reversal fails closed while other streams continue.

After a validated merge, reconcile the merged ticket first. Only then page through the complete configured scope. Every page must share the scope digest, snapshot ID and observation time, and that observation must be at or after reconciliation. Counts use stable ticket IDs and terminal categories, exclude Epics unless requested, and enforce page, item, byte, time and cursor bounds. Any missing, stale, duplicate, partial or mismatched evidence reports both counts as `UNOBSERVED`.

Final review submission separately requires the [review completion barrier](33-REVIEW-COMPLETION-BARRIER.md).

## Reference adapter (AWF-32)

The shipped `.agentic/adapters/reference_controller_adapter.py` is a bounded
Codex/GitHub/Jira reference adapter. It returns cycle, Jira lifecycle and
merge-observation operations; owner-publication operations are intentionally
omitted until a separately qualified host supplies the required owner policy,
publication scan, push and PR readback evidence. The loader therefore fails
closed for `owner-publication-*` commands.

Copy `.agentic/examples/reference-controller-adapter.json` to operator state
and replace every `CHANGE_ME` value and angle-bracket placeholder. The shipped
file is an inert template and does not satisfy the adapter's absolute-path
validation until those placeholders are replaced with real absolute paths.
In the commands below, `<STATE_DIR>` is an operator-controlled absolute state
directory outside every worktree, `<WORKTREE_ROOT>` is the absolute declared
worktree root, and `<REPOSITORY_ROOT>` is the absolute repository root. Keep
the adapter configuration, protected controller database, run records and
outbox outside all worktrees. Compute
executable pins with `Get-FileHash -Algorithm SHA256 ABSOLUTE\codex.exe` and
the equivalent command for `gh.exe`; compute the adapter pin with
`(Get-FileHash -Algorithm SHA256 .agentic/adapters/reference_controller_adapter.py).Hash.ToLower()`.
Pin the exact output bytes immediately before running the controller. The
Jira token is read only from the configured environment-variable name; no
literal credential belongs in JSON. Codex and GitHub child environments strip
`GH_TOKEN`, `GITHUB_TOKEN` and Jira credential variables.

After AWF-36 ships and the owner enables dispatch, prepare an inventory binding
whose `project_id`, numeric `repository_id` and `scope_sha256` match the
adapter config, then run (PowerShell line continuations shown):

```powershell
python -B .agentic/scripts/workflow.py controller --state <STATE_DIR>\controller.sqlite3 --stream A --stream B --stream C --worktree-root <WORKTREE_ROOT> --project-config .agentic\PROJECT_CONFIG.yaml --adapter-module (Resolve-Path .agentic\adapters\reference_controller_adapter.py) --adapter-sha256 PIN --adapter-config <STATE_DIR>\reference-controller-adapter.json cycle --inventory-binding <STATE_DIR>\inventory-binding.json --repository-root <REPOSITORY_ROOT> --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now 2026-10-09T08:00:00Z --host-capacity 3
python -B .agentic/scripts/workflow.py controller --state <STATE_DIR>\controller.sqlite3 --stream A --stream B --stream C --worktree-root <WORKTREE_ROOT> --project-config .agentic\PROJECT_CONFIG.yaml --adapter-module (Resolve-Path .agentic\adapters\reference_controller_adapter.py) --adapter-sha256 PIN --adapter-config <STATE_DIR>\reference-controller-adapter.json jira-lifecycle --contract CONTRACT.json --event WORKER_STARTED --facts FACTS.json --binding BINDING.json --producer-id CONTROLLER_ACTOR --run-id RUN_UUID --now 2026-10-09T08:00:00Z --transition-id TRANSITION_ID
python -B .agentic/scripts/workflow.py controller --state <STATE_DIR>\controller.sqlite3 --stream A --stream B --stream C --worktree-root <WORKTREE_ROOT> --project-config .agentic\PROJECT_CONFIG.yaml --adapter-module (Resolve-Path .agentic\adapters\reference_controller_adapter.py) --adapter-sha256 PIN --adapter-config <STATE_DIR>\reference-controller-adapter.json merge-observed --lifecycle-state MERGING --lifecycle-facts MERGE_FACTS.json --jira-progress JIRA_PROGRESS.json
```

Cycle output is JSON with `schema_version`, `observed_at`, `streams`, exact
`dispatch_receipts`, `status_delivery`, `publication_readiness`, `errors`,
and `execution_authority: false`. Jira output includes the exact current
observation and a readback-bound transition record; merge output includes
`state` and `jira_progress` with `jira_state: COUNTED` only after complete
pagination. Stop by recording the owner stop or stream-specific blocker and
allowing the current command to finish; do not kill and replay an uncertain
dispatch. Until both AWF-32 and AWF-36 ship, no project may run the continuous
controller with its own adapter. Codex can still authenticate through
`~/.codex/auth.json` even when token environment variables are stripped; this
is a known credential-isolation caveat and requires owner review.
