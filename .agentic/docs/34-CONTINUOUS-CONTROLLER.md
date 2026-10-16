# Continuous controller contract

Version 1.9.4. Continue until complete, blocked or owner-stopped.

## Production cycle

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

State stays outside worktrees; pin intent and never reissue unresolved
mutations.

## Dispatch state and round boundaries

Dispatch payloads and receipts use closed schema version 2. Opening protected
state atomically creates any new schema and migrates unambiguous 1.9.4
`PENDING`, `IN_FLIGHT`, and `UNKNOWN` rows only when protected state already
contains their durable prior-inventory digest. A legacy non-terminal set without
that boundary binding is explicitly refused before any schema or row change.
An unsupported state, receipt, field, mixed ambiguous payload, or later
initialization refusal rolls back the schema and data transaction and preserves
the complete 1.9.4 before-image. The reference adapter refuses legacy calls and
records before preparing or launching a runner.

`PENDING`, `IN_FLIGHT`, `UNKNOWN`, and adapter `LAUNCHED` are non-terminal.
A successor inventory cannot refill a round while any of those operations
crosses the boundary. Refill requires one fresh authenticated host trigger
bound to the prior and next inventory digests and the complete durable
dispatch/stream/ticket/tuple/state set. The controller persists that seal before
refill and atomically consumes it with creation of the successor dispatch
intent. A restart at that boundary resumes only those persisted successor
intents; it neither loses the refill nor recreates a seal from replay. Use
`--replan-request REQUEST.json` containing only a positive GitHub `comment_id`.
The reviewed adapter fetches the immutable comment with provider Date/ETag;
the controller requires a fresh, unedited comment by a configured trusted owner
whose body is exactly `AWF-ROUND-REPLAN 1`, a newline, and canonical JSON
binding repository, round, prior/next inventory digests, and the complete
non-terminal dispatch set. Caller-authored trigger or round identities are not
accepted.

Provider reads never occur inside a controller write transaction. For an
overlap allowance, the `LANE_START` provider observation is validated first;
then a dispatch-bound waiver record is persisted before `begun_at` and before
the adapter call. Its digest binds the dispatch, ticket, stream, exact tuple,
allowance, shared path, observation, and persistence time. Restart observation
must carry the same digest and never relaunch the session.

Authenticated parked and worktree-diff sources are independent reservations,
not overlap allowances. Every production cycle asks the reviewed adapter for
the complete provider-bound paused inventory and the complete Git diff at the
pinned repository head/tree, and repeats that complete observation immediately
after every inventory replacement before scheduling. A failed refresh
invalidates the earlier reservation set and blocks scheduling. The controller
rejects foreign, stale, partial,
non-NFC, case/separator/traversal, symlink, and non-canonical Git paths. A valid
reservation blocks every overlapping start even when an exact owner allowance
exists; unrelated paths continue when capacity permits.

## Exact overlap allowances

Only a trusted owner's canonical `AWF-OVERLAP-ALLOWANCE 1` permits an exact
pair for 24 hours. Plan/lane Date/ETag binds repository, PRs, heads, files,
branches and case-sensitive NFC paths. Re-observe ownership per lane and
sample UTC after each provider read.

Wait for terminal dispatch proof. Use head-bound three-way merge, not blob
inequality. Mergeable blobs need no sync writer. Resolve true conflict onto the
second branch by bounded union/expected-head mutation; carry its sorted,
hash-matched, at-most-512-KiB diff to the critic.
`EXECUTED` binds fresh post-read UTC, provider Date/ETag heads and matching
inventory. `UNKNOWN` reads only its retained `COMPLETED` receipt.

Disabled Jira performs no access. Otherwise bind reads/writes/readbacks and
reconcile after merge. Final submission requires the
[review barrier](33-REVIEW-COMPLETION-BARRIER.md).

## Reference adapter (AWF-32)

**Non-executable fragment:** ellipses omit steps and variables. See the
[provider-identity guide](38-PROVIDER-IDENTITY-AND-CREDENTIALS.md).

```powershell
$NOW=...
$HEAD=...
$TREE=...
$ADAPTER_PIN=...
$CONTROLLER_ACTOR=...
$TRANSITION_ID=...
$AWF_CODEX_HOME=...
git -C $SOURCE_ROOT worktree add --detach $WORKTREE_ROOT $HEAD
Copy-Item (Join-Path $WORKTREE_ROOT ".agentic/examples/reference-controller-adapter.json") $ADAPTER_CONFIG
$adapter=Get-Content $ADAPTER_CONFIG
$adapter.codex.executable.path=$CODEX_EXE
$adapter.codex.isolated_home=$AWF_CODEX_HOME
$adapter.codex.windows_sandbox='elevated'
$adapter.codex.worktree_root=$WORKTREE_ROOT
$adapter.github.repository=[string]$repo.full_name
$adapter.github.executable.sha256=...
$adapter.jira.cloud_id=$JIRA_CLOUD
$adapter.jira.controller_actor_id=$CONTROLLER_ACTOR
$adapter | ConvertTo-Json -Depth 100 | Set-Content $ADAPTER_CONFIG
@{project_id=$PROJECT_ID;repository_id=[string]$repo.id;scope_sha256=$SCOPE_SHA256} | ...
$ticket=@{ ... }
& $GH_EXE issue create ...
function New-ObservationTime { ... }
$COMMON=@(...)
function Invoke-ControllerCycle {
  if (Test-Path $STOP_FILE) { ... }
  & $PYTHON @COMMON cycle ...
}
& $PYTHON @COMMON jira-lifecycle ...
@{observed_at=$NOW;jira_binding=...} | ...
& $PYTHON @COMMON merge-observed ...
Move-Item -LiteralPath $temporaryStop -Destination $STOP_FILE
```

Adapter: 1 MiB stderr cap, credentials stripped, fail-closed, and
`execution_authority: false`.
