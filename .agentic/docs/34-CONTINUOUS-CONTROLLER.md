# Continuous controller contract

Version 1.9.6. Continue until complete/blocked/owner-stopped.

## Cycle

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

Do not use `python -B -I .agentic/scripts/workflow.py controller …` today: it
fails under isolated mode (AWF-207). Invoke without `-I`; `-B` may be
retained. No fix release is promised.

State outside worktrees; pin intent; never reissue unresolved mutations.

## Planning recovery

`controller_meta['production_planning']` retains production state;
`.planning.sqlite3` retains immutable plans. Initialization restricts database/WAL/SHM
permissions: POSIX owner read/write; Windows ACLs unchanged.

Format 2: `cycle_sequence`; `seen` digest/last-cycle pairs. Legacy ages
start at zero; identical observations refresh. Above 4096, evict oldest
`(last_cycle, source_id)`, preserving pending sources. Evicted
observations may recompute; identical plans never duplicate. Canonical UTF-8
saves cap at 4 MiB; ordinary reads cap at 8 MiB; cycles at 4,000 events including repeats.
Staged-state 4 MiB refusal precedes event processing. Overflow refuses before
state/history writes or dispatch, retaining prior saves/history.

Stop **all cycles using state** before recovery:

```text
python .agentic/scripts/continuous_controller.py --state ABSOLUTE_CONTROLLER_DB --worktree-root ABSOLUTE_WORKTREE planning-state prune
```

Recovery caps input at 16 MiB, trims only `seen`, reports
bytes/counts, and preserves all other state/history. Unshrinkable state refuses: reduce event windows before they accumulate.
After provider-owner confirmation, `--forget-source SOURCE_ID` reconsiders changed bytes; pending identities cannot be forgotten. Never delete the planning key.
Restart only after repair.

## Dispatch state and round boundaries

Dispatch payloads/receipts use closed schema v2. Opening protected state atomically
migrates unambiguous 1.9.4 `PENDING`, `IN_FLIGHT`, `UNKNOWN` rows only with their durable prior-inventory digest already in protected state.
Legacy non-terminal sets without that boundary binding are refused before any schema or row change. Unsupported states/receipts/fields, ambiguity or initialization refusal roll back. Reference adapters reject legacy calls/records
before runner preparation/launch.

`PENDING`, `IN_FLIGHT`, `UNKNOWN`, and adapter `LAUNCHED` are non-terminal.
A successor inventory cannot refill a round while any of those operations crosses the boundary.
Refill requires one fresh authenticated host trigger bound to prior/next inventory digests and complete durable dispatch/stream/ticket/tuple/state set.
Persist the seal before refill; consume it atomically with successor dispatch intent creation.
Restart resumes only persisted successor intents without losing refill or recreating seals from replay.
`--replan-request REQUEST.json` supplies only a positive GitHub `comment_id`.
Adapter fetches immutable Date/ETag-bound bytes: a fresh, unedited configured
trusted-owner comment, exactly `AWF-ROUND-REPLAN 1`, newline, then canonical JSON
binding repository, round, inventories and the complete non-terminal dispatch set.
Caller-authored identities refuse.

Provider reads never occur inside a controller write transaction.
For an overlap allowance, validate `LANE_START`; persist the waiver before
`begun_at` and adapter calls. Its digest binds the dispatch, ticket, stream, exact tuple, allowance, shared path, observation and persistence time.
Restart observation must carry the same digest and never relaunch the session.

Authenticated parked/worktree-diff sources independently reserve paths. Each cycle
observes complete provider-bound paused inventory and Git diff at pinned head/tree; repeat the complete observation immediately after every inventory replacement before scheduling. Failed
refresh invalidates reservations and blocks scheduling. Reject foreign/stale/partial,
non-NFC, case/separator/traversal, symlink/non-canonical Git paths. Reservations
block overlapping starts despite owner allowances; unrelated paths continue within
capacity.

## Exact overlap allowances

Canonical trusted-owner `AWF-OVERLAP-ALLOWANCE 1` permits exact pairs for 24 hours. Plan/lane Date/ETag binds repository, PRs, heads, files,
branches and case-sensitive NFC paths. Re-observe ownership per lane; sample
UTC after each provider read.

Require terminal dispatch proof and head-bound three-way merge; blob inequality is insufficient. Mergeable blobs need no sync writer. True conflicts require bounded union/expected-head mutation onto branch two and
sorted, hash-matched, at-most-512-KiB critic diffs.
`EXECUTED` binds fresh post-read UTC, provider Date/ETag heads and matching
inventory; `UNKNOWN` reads only its retained `COMPLETED` receipt.

Disabled Jira performs no access. Reconcile merges through `jira-lifecycle`
`JIRA_RECONCILED`; protected transition/readback records precede `merge-observed`.
Closeout names exact record/binding. Progress never calls legacy
`reconcile_merged_ticket`; provider/canonical-issue/scope/owner-hold/status observers
are mandatory. Missing/mismatched evidence refuses counts. Owner-closure keywords
hold In Review. Complete, stable, bounded pagination precedes authoritative counts;
final submission requires the [review barrier](33-REVIEW-COMPLETION-BARRIER.md).

Unknown/failed ordinary/batch operations stop ticket writes.
Recover via `jira-lifecycle`: `read_transition` validates original
operation/actor/provider/target/time; status alone never resolves uncertainty.
Never repeat writes. Batch closeout accepts actor-bound recovery without write
receipts. Original resolutions survive ambiguous legacy stops.
Failed reads preserve evidence; conflicts retain success,
report suspected external automation and require transition evidence.

`jira-not-attempted:<operation_id>` atomically distinguishes batch pre-write failures.
Retry closeout after failed reads; malformed observations require `jira-lifecycle BLOCK`,
without mutation. Proven unattempted/deferred claims resume.
Legacy FAILED/no-receipt rows require uniquely copied predecessor readback
(including JSON null). Otherwise owner restores independently retained
receipts before `jira-lifecycle`; never edit status/invent evidence.

`controller_meta['jira-identity:<operation_id>']` binds connector, cloud/site/project/key/actor,
issue, project_id and repository_id. Legacy rows fail closed.
Reviewed-adapter `observe_jira_operation_identity(record)` restores
`{operation_id,intent_sha256,identity}` from independent historical evidence,
never current configuration; CLI-forwarded. Migration preserves rows/receipts/hashes.
Restore historical configuration after drift. AWF-175 owns changelog
pagination/completeness; recovery asserts no completeness.

## Reference adapter (AWF-32)

**Non-executable fragment:** ellipses omit steps and variables; see separate reference:
[provider-identity guide](38-PROVIDER-IDENTITY-AND-CREDENTIALS.md).

```powershell
$NOW=...
$HEAD=...
$TREE=...
$ADAPTER_PIN=...
$CONTROLLER_ACTOR=...
$TRANSITION_ID=...;$AWF_CODEX_HOME=...
git -C $SOURCE_ROOT worktree add --detach $WORKTREE_ROOT $HEAD
Copy-Item (Join-Path $WORKTREE_ROOT
$adapter=Get-Content $ADAPTER_CONFIG
$adapter.codex.executable.path=$CODEX_EXE;$adapter.codex.isolated_home=$AWF_CODEX_HOME;$adapter.codex.windows_sandbox='elevated';$adapter.codex.worktree_root=$WORKTREE_ROOT;$adapter.github.repository=[string]$repo.full_name;$adapter.github.executable.sha256=...;$adapter.jira.cloud_id=$JIRA_CLOUD;$adapter.jira.controller_actor_id=$CONTROLLER_ACTOR
$adapter | ConvertTo-Json -Depth 100 | Set-Content $ADAPTER_CONFIG
@{project_id=$PROJECT_ID;repository_id=[string]$repo.id;scope_sha256=$SCOPE_SHA256};$ticket=@{
& $GH_EXE issue create
function New-ObservationTime;$COMMON=@(
function Invoke-ControllerCycle;if (Test-Path $STOP_FILE)
& $PYTHON @COMMON cycle;& $PYTHON @COMMON jira-lifecycle;observed_at=$NOW;jira_binding=;& $PYTHON @COMMON merge-observed;Move-Item -LiteralPath $temporaryStop -Destination $STOP_FILE
```

Adapter: 1 MiB stderr cap, stripped credentials, fail-closed,
`execution_authority: false`.

### Required adapter operations

Load reviewed, SHA-pinned module exposing
`build_adapters(config)` and callable operations. None means no reviewed adapter operations, regardless of
state/configuration requirements or effects.

| Controller subcommand | Required adapter operations | Source |
|---|---|---|
| `cycle` | `observe_inventory`, `observe_reservations`, `dispatch_ticket`, `observe_dispatch`, `deliver_status`, `observe_publication`, `observe_host_resources`, `verify_host_observation`, `observe_planning_backlog`, `verify_planning_backlog`, `observe_planning_events`, `verify_planning_events`, `verify_planning_owner` | 225–232 |
| `jira-lifecycle` | `observe_provider_identity`, `observe_issue_identity`, `observe_issue_policy`, `read_current_status`, `write_transition`, `read_transition` | 243–244 |
| `merge-observed` | `observe_provider_identity`, `observe_issue_identity`, `observe_issue_policy`, `read_current_status`, `fetch_scope_page`, `observe_accepted_project_config`, `observe_closeout_record`; additionally `observe_jira_reconciliation` when the supplied project configuration (--project-config) has `jira.enabled is True` | 246–252 |
| `owner-publication-prepare` | `authorize_owner_publication` | 217–218 |
| `owner-publication-resume` | `observe_identity`, `observe_remote_heads`, `create_draft_pr`, `observe_draft_pr` | 220–221 |
| `owner-publication-status` | None | 206–207, 222–223 |
| `batch-operation` | `batch_coordinator` | 208–211 |
| `batch-closeout` | `batch_coordinator`, `observe_batch_merge`, `observe_issue_identity`, `observe_issue_policy`, `observe_provider_identity`, `read_current_status`, `write_transition`, `read_transition` | 58–60, 212–215 |
| `snapshot` | None | 188, 206–207, 254–255 |
| `planning-state prune` | None | 145–147, 190–194 |

`cycle --overlap-allowances` additionally requires `observe_overlap_allowance`:
the CLI checks whether the flag was supplied, even an empty loaded list.
`cycle --replan-request` additionally requires `observe_replan_trigger`.

CLI forwards these additional callbacks when callable:

- `observe_overlap_conflict`: expired allowance conflict observation.
- `observe_overlap_sync_inventory`: post-sync provider PR-head observation.
- `sync_writer`: confirmed conflict execution.
- `reconcile_overlap_sync`: UNKNOWN receipt-only recovery.
- `observe_dispatch_quiescence`: terminal proof for accepted dispatches.

See [Jira scope](JIRA-WRITE-SCOPE.md).

Runtime overlap paths require the corresponding callback. This controller contract applies;
the reference adapter lacks owner-publication and batch operations.

## Automation switches

`controller` booleans ship `false`: master `dispatch_enabled`; `auto_dispatch`
(implementation), `auto_request_critic` (independent critics), `auto_resume_amendments`
(amendments). Each path needs master and own switch. Disabled paths record
`CONTROLLER_AUTOMATION_DISABLED` before publication observation/dispatch.
Missing/non-boolean switches refuse before recovery/provider calls; CLI preserves
reviewed `controller`. Disabling cancels unstarted pending work; uncertain launches
reconcile through observation, never relaunching.

Install/upgrade require five switches, including `auto_transition_jira`, false. Enable dispatch under project governance; upgrades preserve values without migration.
Switches grant no publication, review, merge or adapter qualification.
Until AWF-36 and AWF-32 ship, no project runs the continuous controller with
its own adapter (Jonathan, 8 October).
