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

`.agentic/adapters/reference_controller_adapter.py` supplies bounded
Codex/GitHub/Jira cycle, lifecycle, and merge operations. Owner-publication
operations are omitted and fail closed.

Copy the example to operator state and replace placeholders with absolute
paths and accepted identities. Keep state outside worktrees. Pin executables
with `Get-FileHash -Algorithm SHA256`; pin the adapter with
`(Get-FileHash -Algorithm SHA256 .agentic/adapters/reference_controller_adapter.py).Hash.ToLower()`.
The Jira token uses only its configured environment name; child environments
strip `GH_TOKEN`, `GITHUB_TOKEN`, and Jira credentials.

Create the command inputs as follows. Replace provider placeholders with the
same owner-accepted bindings:

```powershell
$STATE_DIR = (Resolve-Path <ABSOLUTE_SCRATCH_DIR>).Path
New-Item -ItemType Directory -Force $STATE_DIR | Out-Null
Copy-Item .agentic/examples/reference-controller-adapter.json "$STATE_DIR\reference-controller-adapter.json"
$adapter = Get-Content "$STATE_DIR\reference-controller-adapter.json" -Raw | ConvertFrom-Json
$project = Get-Content .agentic/examples/PROJECT_CONFIG.yaml -Raw | ConvertFrom-Json
$project.jira.enabled = $true
foreach ($name in @('cloud_id','site','provider_project_id','project_key','controller_actor_id')) { $project.jira.$name = $adapter.jira.$name }
$project | ConvertTo-Json -Depth 100 | Set-Content "$STATE_DIR\PROJECT_CONFIG.yaml" -Encoding utf8
@{project_id=[string]$adapter.github.project_id; repository_id=[string]$adapter.github.repository_id; scope_sha256=$adapter.github.scope_sha256} | ConvertTo-Json -Compress | Set-Content "$STATE_DIR\inventory-binding.json" -Encoding utf8
@{run_registered=$true; worktree_verified=$true} | ConvertTo-Json -Compress | Set-Content "$STATE_DIR\facts.json" -Encoding utf8
@{issue_id='2001'} | ConvertTo-Json -Compress | Set-Content "$STATE_DIR\binding.json" -Encoding utf8
$bundle = Get-Content .agentic/examples/evidence-bundle.json -Raw | ConvertFrom-Json
$bundle.contract | ConvertTo-Json -Depth 100 | Set-Content "$STATE_DIR\CONTRACT.json" -Encoding utf8
@{merge_confirmed=$true; candidate_matched=$true} | ConvertTo-Json -Compress | Set-Content "$STATE_DIR\MERGE_FACTS.json" -Encoding utf8
@{jira_enabled=$true; merged_ticket='2001'; scope='project = EX'; observed_at='2026-10-09T08:00:00Z'; jira_binding=@{cloud_id=[string]$adapter.jira.cloud_id; project_id=[string]$adapter.jira.provider_project_id; actor_id=[string]$adapter.jira.controller_actor_id}; max_pages=20; max_items=10000} | ConvertTo-Json -Depth 10 -Compress | Set-Content "$STATE_DIR\merge-progress.json" -Encoding utf8
```

Use one controller-owned `$NOW`; compute `$HEAD` and `$TREE` immediately before
cycle. `merged_transition_id` is distinct from `merged_status_id`. Outputs have
`execution_authority: false`; merge is `COUNTED` only after complete,
same-snapshot pages. The adapter refuses partial or mismatched evidence.
Then run:

```powershell
python -B .agentic/scripts/workflow.py controller --state <STATE_DIR>\controller.sqlite3 --stream A --stream B --stream C --worktree-root <WORKTREE_ROOT> --project-config <STATE_DIR>\PROJECT_CONFIG.yaml --adapter-module (Resolve-Path .agentic\adapters\reference_controller_adapter.py) --adapter-sha256 $ADAPTER_PIN --adapter-config <STATE_DIR>\reference-controller-adapter.json cycle --inventory-binding <STATE_DIR>\inventory-binding.json --repository-root <REPOSITORY_ROOT> --repository-head-sha $HEAD --repository-tree-sha $TREE --now $NOW --host-capacity 3 --dispatch-role writer
python -B .agentic/scripts/workflow.py controller --state <STATE_DIR>\controller.sqlite3 --stream A --stream B --stream C --worktree-root <WORKTREE_ROOT> --project-config <STATE_DIR>\PROJECT_CONFIG.yaml --adapter-module (Resolve-Path .agentic\adapters\reference_controller_adapter.py) --adapter-sha256 $ADAPTER_PIN --adapter-config <STATE_DIR>\reference-controller-adapter.json jira-lifecycle --contract <STATE_DIR>\CONTRACT.json --event WORKER_STARTED --facts <STATE_DIR>\facts.json --binding <STATE_DIR>\binding.json --issue-type LEAF --lifecycle-state DISPATCHED --producer-id CONTROLLER_ACTOR --run-id 00000000-0000-0000-0000-000000000001 --now $NOW --transition-id TRANSITION_ID
python -B .agentic/scripts/workflow.py controller --state <STATE_DIR>\controller.sqlite3 --stream A --stream B --stream C --worktree-root <WORKTREE_ROOT> --project-config <STATE_DIR>\PROJECT_CONFIG.yaml --adapter-module (Resolve-Path .agentic\adapters\reference_controller_adapter.py) --adapter-sha256 $ADAPTER_PIN --adapter-config <STATE_DIR>\reference-controller-adapter.json merge-observed --lifecycle-state MERGING --lifecycle-facts <STATE_DIR>\MERGE_FACTS.json --jira-progress <STATE_DIR>\merge-progress.json
```

Cycle output includes `streams`, `dispatch_receipts`, `status_delivery`,
`publication_readiness`, `errors`, and `execution_authority: false`. Until
AWF-32 and AWF-36 ship, no project may run its own adapter. Codex may still
authenticate through `~/.codex/auth.json` after environment stripping.

Each invocation is finite. To stop, schedule no more invocations, record the
owner stop, and let the current one finish; use `Ctrl+C` only between
invocations. If interrupted, inspect `UNKNOWN` by observation; never replay.
A PREPARED launch requires an independent terminal exit proof.
Missing proof, timeout, non-zero exit, incomplete pages, or identity mismatch
remains fail-closed.
