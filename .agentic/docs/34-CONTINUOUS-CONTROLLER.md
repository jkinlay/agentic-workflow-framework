# Continuous controller contract

Version 1.9.4. Continue until complete, blocked or owner-stopped.

## Production cycle

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

State stays outside worktrees; pin intent and never reissue unresolved
mutations.

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
