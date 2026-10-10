# Continuous controller contract

Version 1.9.4. Continue streams until complete, owner-stopped or individually
blocked. `ContinuousControllerStore` persists decisions without granting host,
provider, Jira or merge authority.

## Production entry point

Use only this entry point:

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

`cycle` observes authenticated inventory, schedules, persists a timed,
nonce-bound intent before dispatch, reconciles uncertainty, delivers cadence
status and accepts only fresh exact readback. The absolute, regular,
single-link adapter is at most 1 MiB, exposes `build_adapters(config)`, and is
loaded from UTF-8 bytes matching its SHA-256. Shell strings, unpinned
executables and missing operations fail closed. `jira-lifecycle` performs
read/one mapped write/readback; `merge-observed` validates merge before Jira
reconciliation/counting; `snapshot` is read-only.

Protected state is absolute and outside worktrees. Every open checks
permissions, SQLite integrity/identity/schema and rejects link, reparse and
hardlink aliases.

## No silent idle state

Streams are `WORKING`, `PAUSED_INPUT`, `BLOCKED` or `COMPLETE`; rows retain
ticket/actor/reason/action/resume, tuple/activity/gate, counts/findings/Jira.
`COMPLETE` means no scoped action remains.

Priority then ticket identity select after dependency, budget/cap,
independence and capacity checks. One writer owns each overlapping physical
path; blocked streams do not pause others. A cycle pins
one absolute Git worktree/head/tree. Index spelling is canonical; directory
claims expand over tracked descendants, case aliases collapse, and hardlinks
share an ownership key. Case collisions, uncertain identity/parent, Git or
filesystem links, nested repositories, junctions and reparse points fail
closed. Repository/worktree rebinding is refused.

Require `required = completed + outstanding` and `completed = acceptable +
failed + stale`. Contradictions fail before effects. Revalidate running work;
a completed ticket must become absent or terminal before refill.

## Dispatch and cadence

Persist intents before calls. `PENDING`, `IN_FLIGHT` and `UNKNOWN` reserve the
ticket; latter consume capacity. Preflight cancels unbegun ineligible intents.
UNKNOWN updates only its matching stream/ticket/tuple; other streams use free
capacity.

Cadence is 600 seconds; only `--migrate-status-cadence` changes stored cadence.
Mismatch and clock rollback fail. `--disable-periodic-status` suppresses only
scheduled digests; blocker/failure/input/merge-ready changes remain immediate.
Outbox readback, deadlines and delivery survive restart. Digests include every
stream/gate and unresolved UNKNOWN identity/detached flag.

## Jira lifecycle and merge progress

Disabled Jira performs no access. Otherwise cloud/site/project/key, actor and
issue bind intent/receipts. Recover in-flight work as `UNKNOWN`,
read the issue and reconcile unknown intents before planning. Deterministic IDs
survive restart; commit `IN_FLIGHT` before mutation. Never reissue a lost
response: target-state readback completes it, otherwise keep `UNKNOWN` and
stop that ticket's writes. New writes require exact pre-read, receipt and
readback; identity/time mismatch fails closed while other streams continue.

After validated merge, reconcile that ticket, then page the complete scope.
Pages share scope digest, snapshot and post-reconciliation observation time.
Bounded counts use stable IDs/categories and exclude Epics unless requested;
missing, stale, duplicate, partial or mismatched evidence returns `UNOBSERVED`.

Final submission requires the [review barrier](33-REVIEW-COMPLETION-BARRIER.md).

## Reference adapter (AWF-32)

From a clean release root, use PowerShell 7 and owner-approved disposable
GitHub/Jira issues. Owner publication is omitted. Set environmental
`AWF_JIRA_TOKEN`; it is never written.

```powershell
$SOURCE_ROOT=(Resolve-Path (git rev-parse --show-toplevel).Trim()).Path
$STATE_DIR=Join-Path $env:TEMP ("awf-controller-"+[guid]::NewGuid().ToString("N"))
$WORKTREE_ROOT=Join-Path $STATE_DIR "worktree"
$ADAPTER_CONFIG=Join-Path $STATE_DIR "reference-controller-adapter.json"
$STOP_FILE=Join-Path $STATE_DIR "OWNER-STOP.json"
New-Item -ItemType Directory -Force $STATE_DIR | Out-Null
Write-Host "STATE_DIR=$STATE_DIR"
$HEAD=(git -C $SOURCE_ROOT rev-parse HEAD).Trim()
git -C $SOURCE_ROOT worktree add --detach $WORKTREE_ROOT $HEAD
if ($LASTEXITCODE) { throw "git worktree add failed" }
$TREE=(git -C $WORKTREE_ROOT rev-parse "HEAD^{tree}").Trim()
$ADAPTER_MODULE=Join-Path $WORKTREE_ROOT ".agentic/adapters/reference_controller_adapter.py"
$WORKFLOW=Join-Path $WORKTREE_ROOT ".agentic/scripts/workflow.py"
Copy-Item (Join-Path $WORKTREE_ROOT ".agentic/examples/reference-controller-adapter.json") $ADAPTER_CONFIG
$adapter=Get-Content $ADAPTER_CONFIG -Raw | ConvertFrom-Json
$project=Get-Content (Join-Path $WORKTREE_ROOT ".agentic/examples/PROJECT_CONFIG.yaml") -Raw | ConvertFrom-Json
$PYTHON=(Get-Command python.exe -CommandType Application -ErrorAction Stop).Source
$CODEX_EXE=(Get-Command codex.exe -CommandType Application -ErrorAction Stop).Source
$GH_EXE=(Get-Command gh.exe -CommandType Application -ErrorAction Stop).Source
$REPOSITORY=Read-Host "GitHub owner/name"
$PROJECT_ID=Read-Host "Accepted AWF project UUID"
$repo=(& $GH_EXE api "repos/$REPOSITORY" | ConvertFrom-Json)
$actor=(& $GH_EXE api user | ConvertFrom-Json)
$auth=(& $GH_EXE auth status --hostname github.com --json hosts | ConvertFrom-Json)
$active=@($auth.hosts.'github.com' | Where-Object active)
if ($active.Count -ne 1) { throw "one active gh profile is required" }
$AUTH_PROFILE=[string]$(if ($active[0].profile) { $active[0].profile } else { $active[0].login })
$INVENTORY_ENDPOINT="repos/$REPOSITORY/issues?state=open&labels=awf-controller"
$INVENTORY_ENDPOINT | Set-Content (Join-Path $STATE_DIR "inventory-scope.txt") -NoNewline -Encoding utf8
$SCOPE_SHA256=(Get-FileHash (Join-Path $STATE_DIR "inventory-scope.txt") -Algorithm SHA256).Hash.ToLower()
$JIRA_SITE=(Read-Host "Jira site URL").TrimEnd('/')
$JIRA_CLOUD=Read-Host "Jira cloud ID"
$JIRA_PROJECT=Read-Host "Jira numeric project ID"
$JIRA_KEY=Read-Host "Jira project key"
$CONTROLLER_ACTOR=Read-Host "Jira controller account ID"
$JIRA_ISSUE=Read-Host "Disposable Jira issue ID"
$IN_PROGRESS_STATUS_ID=Read-Host "In Progress status ID"
$TRANSITION_ID=Read-Host "Owner-approved In Progress transition ID"
$DONE_STATUS_ID=Read-Host "Done status ID"
$DONE_TRANSITION_ID=Read-Host "Owner-approved Done transition ID"
if (-not $env:AWF_JIRA_TOKEN) { throw "AWF_JIRA_TOKEN is not set" }

$adapter.codex.executable.path=$CODEX_EXE
$adapter.codex.executable.sha256=(Get-FileHash $CODEX_EXE -Algorithm SHA256).Hash.ToLower()
$adapter.codex.roles.writer.model=$project.execution.roles.worker.model
$adapter.codex.roles.critic.model=$project.execution.roles.critic.model
$adapter.codex.worktree_root=$WORKTREE_ROOT
$adapter.codex.run_record_directory=Join-Path $STATE_DIR "controller-runs"
$adapter.github.repository=[string]$repo.full_name
$adapter.github.repository_id=[int64]$repo.id
$adapter.github.project_id=$PROJECT_ID
$adapter.github.scope_sha256=$SCOPE_SHA256
$adapter.github.base_branch=[string]$repo.default_branch
$adapter.github.expected_actor_id=[int64]$actor.id
$adapter.github.auth_profile=$AUTH_PROFILE
$adapter.github.executable.path=$GH_EXE
$adapter.github.executable.sha256=(Get-FileHash $GH_EXE -Algorithm SHA256).Hash.ToLower()
$adapter.github.inventory_endpoint=$INVENTORY_ENDPOINT
$adapter.jira.cloud_id=$JIRA_CLOUD
$adapter.jira.site=$JIRA_SITE
$adapter.jira.provider_project_id=$JIRA_PROJECT
$adapter.jira.project_key=$JIRA_KEY
$adapter.jira.controller_actor_id=$CONTROLLER_ACTOR
$adapter.jira.merged_status_id=$DONE_STATUS_ID
$adapter.jira.merged_transition_id=$DONE_TRANSITION_ID
$adapter.outbox.directory=Join-Path $STATE_DIR "controller-outbox"
$adapter | ConvertTo-Json -Depth 100 | Set-Content $ADAPTER_CONFIG -Encoding utf8

$project.project.id=$PROJECT_ID
$project.jira.enabled=$true
foreach ($name in @('cloud_id','site','provider_project_id','project_key','controller_actor_id')) { $project.jira.$name = $adapter.jira.$name }
$project.jira.status_map.in_progress=$IN_PROGRESS_STATUS_ID
$project.github.repository=[string]$repo.full_name
$project.github.repository_id=[int64]$repo.id
$project.github.expected_actor_id=[int64]$actor.id
$project.github.expected_actor_login=[string]$actor.login
$project.github.auth_profile=$AUTH_PROFILE
$project.github.base_branch=[string]$repo.default_branch
$project | ConvertTo-Json -Depth 100 | Set-Content (Join-Path $STATE_DIR "PROJECT_CONFIG.yaml") -Encoding utf8
@{project_id=$PROJECT_ID;repository_id=[string]$repo.id;scope_sha256=$SCOPE_SHA256} | ConvertTo-Json -Compress | Set-Content (Join-Path $STATE_DIR "inventory-binding.json") -Encoding utf8
@{owner_closure_required=$false} | ConvertTo-Json -Compress | Set-Content (Join-Path $STATE_DIR "contract.json") -Encoding utf8
@{issue_id=$JIRA_ISSUE} | ConvertTo-Json -Compress | Set-Content (Join-Path $STATE_DIR "binding.json") -Encoding utf8

$ticket=@{ticket="$JIRA_KEY-$JIRA_ISSUE";priority=1;disposition="ELIGIBLE";actor="owner";reason="disposable controller example";next_action="implement the disposable ticket";resume_trigger="owner input";paths=@("README.md");dependencies_satisfied=$true;budget_available=$true;cap_available=$true;review_independent=$true;exact_tuple="base:$HEAD/head:$HEAD/tree:$TREE/contract:example/review:pending";activity="example implementation";verification_gate="PENDING";reviewer_completion=@{required=0;completed=0;acceptable=0;failed=0;stale=0;outstanding=0};open_findings=0;jira_status="Ready"}
& $GH_EXE label create awf-controller --repo $REPOSITORY --color 5319e7 --force
& $GH_EXE issue create --repo $REPOSITORY --title "Controller example $($ticket.ticket)" --label awf-controller --body ($ticket | ConvertTo-Json -Depth 10 -Compress)
if ($LASTEXITCODE) { throw "disposable inventory issue creation failed" }
```

Each operation reuses one UTC time in its record and command. A non-advancing
clock fails before provider access.

```powershell
$script:LAST_OBSERVATION=$null
function New-ObservationTime {
  $value=[DateTimeOffset]::UtcNow
  if ($script:LAST_OBSERVATION -and $value -le $script:LAST_OBSERVATION) { throw "UTC clock did not advance" }
  $script:LAST_OBSERVATION=$value
  $value.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
}
$ADAPTER_PIN=(Get-FileHash $ADAPTER_MODULE -Algorithm SHA256).Hash.ToLower()
$COMMON=@("-B",$WORKFLOW,"controller","--state",(Join-Path $STATE_DIR "controller.sqlite3"),"--stream","A","--stream","B","--stream","C","--worktree-root",$WORKTREE_ROOT,"--project-config",(Join-Path $STATE_DIR "PROJECT_CONFIG.yaml"),"--adapter-module",$ADAPTER_MODULE,"--adapter-sha256",$ADAPTER_PIN,"--adapter-config",$ADAPTER_CONFIG)
function Invoke-ControllerCycle {
  if (Test-Path $STOP_FILE) { throw "owner stop is recorded" }
  $NOW=New-ObservationTime
  $head=(git -C $WORKTREE_ROOT rev-parse HEAD).Trim()
  $tree=(git -C $WORKTREE_ROOT rev-parse "HEAD^{tree}").Trim()
  $text=& $PYTHON @COMMON cycle --inventory-binding (Join-Path $STATE_DIR "inventory-binding.json") --repository-root $WORKTREE_ROOT --repository-head-sha $head --repository-tree-sha $tree --now $NOW --host-capacity 3 --dispatch-role writer | Out-String
  if ($LASTEXITCODE) { throw "controller cycle failed" }
  $cycle=$text | ConvertFrom-Json
  if (@($cycle.dispatch_receipts).Count -eq 0 -or @($cycle.errors).Count -ne 0) { throw "cycle did not prove dispatch" }
  $cycle
}
$cycle=Invoke-ControllerCycle
@{run_registered=$true;worktree_verified=$true} | ConvertTo-Json -Compress | Set-Content (Join-Path $STATE_DIR "facts.json") -Encoding utf8

$NOW=New-ObservationTime
& $PYTHON @COMMON jira-lifecycle --contract (Join-Path $STATE_DIR "contract.json") --event WORKER_STARTED --facts (Join-Path $STATE_DIR "facts.json") --binding (Join-Path $STATE_DIR "binding.json") --issue-type LEAF --lifecycle-state DISPATCHED --producer-id $CONTROLLER_ACTOR --run-id 00000000-0000-0000-0000-000000000001 --now $NOW --transition-id $TRANSITION_ID
if ($LASTEXITCODE) { throw "Jira lifecycle failed" }

$NOW=New-ObservationTime
if ((Read-Host "Type MERGE-OBSERVED after independent confirmation") -cne "MERGE-OBSERVED") { throw "merge unconfirmed" }
@{merge_confirmed=$true;candidate_matched=$true} | ConvertTo-Json -Compress | Set-Content (Join-Path $STATE_DIR "merge-facts.json") -Encoding utf8
@{jira_enabled=$true;merged_ticket=$JIRA_ISSUE;scope="project = $JIRA_KEY";observed_at=$NOW;jira_binding=@{cloud_id=$JIRA_CLOUD;project_id=$JIRA_PROJECT;actor_id=$CONTROLLER_ACTOR};max_pages=20;max_items=10000} | ConvertTo-Json -Depth 10 -Compress | Set-Content (Join-Path $STATE_DIR "merge-progress.json") -Encoding utf8
& $PYTHON @COMMON merge-observed --lifecycle-state MERGING --lifecycle-facts (Join-Path $STATE_DIR "merge-facts.json") --jira-progress (Join-Path $STATE_DIR "merge-progress.json")
if ($LASTEXITCODE) { throw "merge observation failed" }
```

Run `Invoke-ControllerCycle` at the approved cadence. To stop, atomically write
the marker; later calls refuse. Let a running cycle finish; observe, never
replay, any `UNKNOWN`.

```powershell
$STATE_DIR=Read-Host "STATE_DIR printed by setup"
$STOP_FILE=Join-Path $STATE_DIR "OWNER-STOP.json"
$NOW=[DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
$temporaryStop="$STOP_FILE.tmp"
@{format="awf-owner-stop-1";owner=(Read-Host "Owner identity");observed_at=$NOW;reason=(Read-Host "Stop reason")} | ConvertTo-Json -Compress | Set-Content $temporaryStop -Encoding utf8
Move-Item -LiteralPath $temporaryStop -Destination $STOP_FILE
```

Output includes streams, receipts, delivery, readiness, errors and
`execution_authority: false`. Merge observation needs independent
confirmation. Missing proof, incomplete pages, identity mismatch or non-zero
exit fails closed.
