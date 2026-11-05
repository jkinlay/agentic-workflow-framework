# Continuous controller contract

Version 1.9.6. Continue until complete, blocked or owner-stopped.

## Production cycle

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

Do not use `python -B -I .agentic/scripts/workflow.py controller …` today:
this route fails under isolated mode (AWF-207). Invoke without `-I`; `-B` may be
retained. No fix release is promised.

State stays outside worktrees; pin intent and never reissue unresolved
mutations.

## Planning retention and recovery

Primary controller database holds `controller_meta['production_planning']`;
`.planning.sqlite3` holds immutable plan history. After initialization closes,
both restrict database/WAL/SHM permissions. POSIX uses owner read/write;
Windows ACLs remain unchanged.

Format 2 retains `cycle_sequence` and `seen` digest/last-cycle pairs. Legacy
digests age from zero; identical observations refresh age. Above 4096, evict
oldest `(last_cycle, source_id)`, pinning pending sources beyond target.
Evicted sources may recompute; identical plans never duplicate. Saves
check canonical UTF-8 against 4 MiB; reads cap at 8 MiB. Excess
pinned/preserved fields refuse before writing, preserving previous bytes;
prior saves/history may persist. Cycles accept at most 4,000 provider events
(including repeats); excess refuses before state/history writes or dispatch.
Staged-state 4 MiB guard takes precedence.

Stop **all cycles using state** before recovery:

```text
python .agentic/scripts/continuous_controller.py --state ABSOLUTE_CONTROLLER_DB --worktree-root ABSOLUTE_WORKTREE planning-state prune
```

Recovery needs protected primary state, bounds input to 16 MiB, trims only
`seen`, and reports bytes/counts. Pending work, overrides, observation,
candidate metadata, other rows/history remain. Unshrinkable state refuses;
reduce event windows first. After provider-owner confirmation,
`--forget-source SOURCE_ID` reconsiders changed bytes for retained identities;
pending identities cannot be forgotten. Never delete the planning key. Restart
only after successful repair.

## Dispatch state and round boundaries

Dispatch payloads/receipts use closed schema v2. Opening protected state
atomically creates schema, migrating unambiguous 1.9.4 `PENDING`, `IN_FLIGHT`,
`UNKNOWN` rows only with durable prior-inventory digest already stored. Legacy
non-terminal sets without binding refuse before schema/row change. Unsupported
states/receipts/fields, ambiguous payloads, or later initialization refusal roll
back schema/data, preserving the complete before-image.
Reference adapter refuses legacy calls/records before runner preparation/launch.

`PENDING`, `IN_FLIGHT`, `UNKNOWN`, and adapter `LAUNCHED` are non-terminal.
A successor inventory cannot refill a round while any crosses the boundary.
Refill requires one fresh authenticated host trigger bound to prior/next
inventory digests and complete durable dispatch/stream/ticket/tuple/state set.
Persist the seal before refill; consume it atomically with successor
dispatch intent creation.
Restart resumes only persisted successor intents; it neither loses refill nor
recreates seals from replay.
`--replan-request REQUEST.json` contains only a positive GitHub `comment_id`.
Adapter fetches immutable comment with provider Date/ETag. Require fresh,
unedited configured trusted-owner comment: exactly `AWF-ROUND-REPLAN 1`, newline,
then canonical JSON binding repository, round, inventories and the complete
non-terminal dispatch set. Caller-authored identities are refused.

Provider reads never occur inside controller write transaction. For overlap,
validate `LANE_START` first; persist a dispatch-bound waiver before `begun_at`
and the adapter call. Its digest binds dispatch, ticket, stream, exact tuple,
allowance, shared path, observation and persistence time. Restart observation
must carry that digest and never relaunch the session.

Authenticated parked/worktree-diff sources independently reserve paths. Each cycle
observes complete provider-bound paused inventory/Git diff at pinned head/tree;
repeat immediately after each inventory replacement before scheduling. Failed
refresh invalidates reservations and blocks scheduling. Reject foreign/stale/partial,
non-NFC, case/separator/traversal, symlink/non-canonical Git paths. Reservations
block overlapping starts despite owner allowances; unrelated paths continue within
capacity.

## Exact overlap allowances

Only a trusted-owner's canonical `AWF-OVERLAP-ALLOWANCE 1` permits an exact
pair for 24 hours. Plan/lane Date/ETag binds repository, PRs, heads, files,
branches and case-sensitive NFC paths. Re-observe ownership per lane; sample
UTC after each provider read.

Wait for terminal dispatch proof. Use head-bound three-way merge, not blob
inequality; mergeable blobs need no sync writer. Resolve true conflict onto the
second branch by bounded union/expected-head mutation; carry its sorted,
hash-matched, at-most-512-KiB diff to the critic.
`EXECUTED` binds fresh post-read UTC, provider Date/ETag heads and matching
inventory; `UNKNOWN` reads only its retained `COMPLETED` receipt.

Disabled Jira performs no access. Otherwise bind reads/writes/readbacks and
reconcile after merge through `jira-lifecycle` with `JIRA_RECONCILED`. Before
`merge-observed`, retain the successful transition/readback in the protected
controller journal. Validated closeout names that exact transition record and
binding. `merge-observed` only observes reconciliation/counts; it never calls
legacy `reconcile_merged_ticket`.

Progress requires provider identity, canonical issue identity, scope/owner-hold
policy and current-status observers. Missing/mismatched observers or journal
entries refuse counting. Owner-closure keywords hold tickets In Review. Unknown
outcomes or later status/readback mismatches durably stop the ticket; report
suspected external automation and never reissue the write. Complete, stable,
bounded pagination must follow reconciliation observation before counts become
authoritative. Final submission requires the
[review barrier](33-REVIEW-COMPLETION-BARRIER.md).

## Reference adapter (AWF-32)

**Non-executable fragment:** ellipses omit steps/variables; see reference:
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

Load a reviewed SHA-pinned module exposing
`build_adapters(config)` and callable operations. None means no reviewed
operations, regardless of state/configuration/effects.
Source: `.agentic/scripts/continuous_controller.py`.

| Controller subcommand | Required adapter operations | CLI source lines |
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
the CLI checks the supplied flag, even for an empty loaded list.
`cycle --replan-request` additionally requires `observe_replan_trigger`.

CLI forwards callable callbacks outside its unconditional load-time set:

- `observe_overlap_conflict`: expired allowance conflict observation.
- `observe_overlap_sync_inventory`: post-sync provider PR-head observation.
- `sync_writer`: confirmed conflict execution.
- `reconcile_overlap_sync`: UNKNOWN receipt-only recovery.
- `observe_dispatch_quiescence`: terminal proof for accepted dispatches.

See [Jira scope](JIRA-WRITE-SCOPE.md).

Runtime overlap paths require the corresponding callback. This contract applies;
the reference adapter lacks owner-publication and batch operations.

## Controller automation switches

The `controller` mapping supplies four boolean switches, all `false` in shipped
configuration/examples.
`dispatch_enabled` is the master gate; `auto_dispatch` permits implementation
writers, `auto_request_critic` permits independent critic requests, and
`auto_resume_amendments` permits amendment writers. Each path requires master
and own switch. Disabled paths record
`CONTROLLER_AUTOMATION_DISABLED` before publication observation or dispatch.
Missing/non-boolean values refuse the cycle before recovery/provider calls; the
CLI passes exact reviewed `controller` mapping.
Disabling cancels never-started pending work; uncertain launches reconcile by
observation, never relaunching.

Install/upgrade require all five automation switches, including
`auto_transition_jira`, false. Enable dispatch switches under project
governance. Upgrades preserve values; no migration is needed;
`auto_transition_jira` is unchanged.
Switches grant no publication, review, merge or adapter qualification.
Until AWF-36 and AWF-32 ship, no project runs the continuous controller with
its own adapter (Jonathan, 8 October).
