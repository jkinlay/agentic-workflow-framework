# Continuous controller

## Cycle

```text
python .agentic/scripts/workflow.py controller --help
```

Do not use `python -B -I .agentic/scripts/workflow.py controller ...` today:
fails under isolated mode (AWF-207). Invoke without `-I`; `-B` may be
retained. No fix release is promised.

## Planning recovery

`controller_meta['production_planning']`: state; `.planning.sqlite3`: plans.
Format 2: `cycle_sequence`, `seen` digest/last-cycle pairs; legacy ages zero. Above 4096 evict oldest `(last_cycle, source_id)`; preserve pending sources.
Canonical UTF-8 saves cap at 4 MiB; ordinary reads cap at 8 MiB;
cycles at 4,000 events including repeats. Oversize staged state refuses before events/writes/dispatch, preserving prior saves/history.

Before recovery, stop **all state-using cycles**:

```text
python .agentic/scripts/continuous_controller.py --state ABSOLUTE_CONTROLLER_DB --stream A --worktree-root ABSOLUTE_WORKTREE planning-state prune
```

Recovery: 16 MiB; trim only `seen`, preserving state/history; report bytes/counts. Unshrinkable state refuses: reduce event windows before they accumulate.
`--forget-source SOURCE_ID` needs provider-owner confirmation; pending identities cannot be forgotten. Never delete planning key.

## Dispatch state and round boundaries

Dispatch payloads/receipts: closed schema v2. Protected-state opening atomically migrates unambiguous 1.9.4 `PENDING`, `IN_FLIGHT`, `UNKNOWN` rows only with their durable prior-inventory digest already in protected state.
Legacy non-terminal sets without that boundary binding are refused before any schema or row change. Unsupported states/receipts/fields, ambiguity or initialization refusal roll back. Reference adapters reject legacy calls/records
before runner preparation/launch.

`PENDING`, `IN_FLIGHT`, `UNKNOWN`, and adapter `LAUNCHED` are non-terminal.
A successor inventory cannot refill a round while any of those operations crosses the boundary.
Refill requires one fresh authenticated host trigger bound to prior/next inventory digests and complete durable dispatch/stream/ticket/tuple/state set.
Persist the seal before refill; consume it atomically with successor dispatch intent creation.
Restart resumes only persisted successor intents without losing refill or recreating seals from replay.
`--replan-request REQUEST.json`: positive GitHub `comment_id` only.
Adapter fetches immutable Date/ETag-bound bytes: fresh, unedited configured
trusted-owner comment, exactly `AWF-ROUND-REPLAN 1`, newline, then canonical JSON
binding repository, round, inventories and the complete non-terminal dispatch set.
Caller-authored identities refuse.

Provider reads never occur inside a controller write transaction.
For an overlap allowance, validate `LANE_START`; persist before
`begun_at` and adapter calls. Its digest binds the dispatch, ticket, stream, exact tuple, allowance, shared path, observation and persistence time.
Restart observation must carry the same digest and never relaunch the session.

Authenticated parked/worktree-diff sources independently reserve paths. Each cycle
observes complete provider-bound paused inventory and Git diff at pinned head/tree; repeat the complete observation immediately after every inventory replacement before scheduling. Failed
refresh invalidates reservations and blocks scheduling. Reject foreign/stale/partial,
non-NFC, case/separator/traversal, symlink/non-canonical Git paths. Reservations
block overlapping starts despite owner allowances; unrelated paths continue within
capacity.

## Exact overlap allowances

Trusted-owner `AWF-OVERLAP-ALLOWANCE 1`: exact pairs, 24 hours; Date/ETag binds repository/PRs/heads/files/branches and case-sensitive NFC paths. Re-observe ownership/UTC per provider read.

Require terminal dispatch proof/head-bound three-way merge, never blob inequality.
True conflicts need bounded union/expected-head mutation onto branch two
and sorted, hash-matched, at-most-512-KiB critic diffs.
`EXECUTED` binds fresh post-read UTC, provider Date/ETag heads and matching
inventory; `UNKNOWN` reads only its retained `COMPLETED` receipt.

Disabled Jira forbids observations. Counts require protected reconciliation/identity/scope/status/pagination evidence. Owner-closure tickets wait: **owner closes in Jira**. Re-run `jira-lifecycle JIRA_RECONCILED`: `read_current_status` validates complete history/latest Done/non-controller actor/provider time; shared owner/controller accounts stay held: indistinguishable actors. Retain actor/time; recheck history. Status alone cannot close holds; controllers never write Done. `reconcile_merged_ticket` is inert. Require [review barrier](33-REVIEW-COMPLETION-BARRIER.md).

Unknown/failed attempts stop tickets; batch failures stop batches. Abandoned closeouts name ticket/operation. Assignment shares admission/locking/protected `store`; `intake_assignment_state` retains receipts/stops/identity. `jira-lifecycle BLOCK` binds `read_assignment(record)` to original ID/digest/issue/provider/labels/controller comment/fresh time. Synchronize intake. Exclusive `recover_jira_operations` uses `read_transition` operation/actor/provider/target evidence. Never replay/infer from status. Batch recovery accepts actor-bound evidence without write receipts. Preserve success/conflict evidence.

`jira-not-attempted:<operation_id>` proves batch pre-write failures; unattempted/deferred claims resume. Malformed observations require mutation-free BLOCK. Legacy FAILED/no-receipt rows require uniquely copied predecessor readback (including JSON null), otherwise independent receipts.

`jira-identity:<operation_id>` pins provider/project/repository/issue. Stop writers; run `.agentic/scripts/continuous_controller.py --state DB --stream A --worktree-root WORKTREE --project-config CONFIG jira-identity-adopt --expected-project-id PROJECT_ID --expected-repository-id REPOSITORY_ID --operator OPERATOR --now TIME`.
Match IDs; preserve rows/receipts/hashes. ADOPTED_LEGACY_SUCCEEDED: operator/time/configuration digest, OPERATOR_SUPPLIED_NOT_OBSERVED; authority unproven. Adopt healthy rows; report skipped UNKNOWN/FAILED/IN_FLIGHT/PENDING/HELD by ticket/operation/reason/digest/next-command; retain stops. Name corrupt rows. `observe_jira_operation_identity(record)` reads historical envelopes only.

Without envelopes, stop writers; globals: `jira-operation-resolve --issue KEY --operation-id UUID --expected-intent-sha256 DIGEST --expected-project-id PROJECT_ID --expected-repository-id REPOSITORY_ID --operator OPERATOR --now TIME --disposition ABANDON_NO_REPLAY --reason REASON --provenance REFERENCE`.
Also retires pinless PENDING/HELD. Retain rows/receipts/operator/time/reason/reference/digest as OPERATOR_SUPPLIED_NOT_OBSERVED; mark HELD. Never replay/claim success. Later events admit reviewed configuration edits by invariant identity; retain historical digests. Abandoned Done attempts count only via independently observed non-controller closure. Never auto-clear. Missing `--stream` refuses clearly.

Lock/recheck claims through completion; refuse live owners. Persist pre-write identity/status/updated/history. Require exactly one new final actor/from/target transition; hide none later. Numeric Jira order proves novelty; host tolerance: 300 seconds; equal timestamps refuse. Historical fallback requires pre-upgrade attempt inventory. AWF-175 owns changelog pagination/completeness.
Jira accepts milliseconds/colonless offsets; records stay strict. At 100 entries refuse before POST; reserve one readback entry: 99+1 succeeds. Over-limit refusals: AWF-175; post-POST growth beyond 100 stays UNKNOWN. Pre-POST timeouts/lock OSError return retryable NOT_ATTEMPTED/PENDING. First-read 500 reports `writes_stopped: true` but retry succeeds; re-read 500 gives SCOPE_REFUSED/durable FAILED: run `jira-lifecycle BLOCK` before retry.

## Reference adapter (AWF-32)

**Non-executable fragment:** ellipses omit steps and variables; separate reference:
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

1 MiB stderr cap, credentials stripped, fail-closed, and
`execution_authority: false`.

### Required adapter operations

Load reviewed, SHA-pinned module exposing
`build_adapters(config)` and callable operations. None means no reviewed adapter operations, regardless of
state/configuration requirements or effects.

| Controller subcommand | Required adapter operations | Source |
|---|---|---|
| `cycle` | `observe_inventory`, `observe_reservations`, `dispatch_ticket`, `observe_dispatch`, `deliver_status`, `observe_publication`, `observe_host_resources`, `verify_host_observation`, `observe_planning_backlog`, `verify_planning_backlog`, `observe_planning_events`, `verify_planning_events`, `verify_planning_owner` | CLI |
| `jira-lifecycle` | `observe_provider_identity`, `observe_issue_identity`, `observe_issue_policy`, `read_current_status`, `write_transition`, `read_transition` | CLI |
| `merge-observed` | `fetch_scope_page`, `observe_accepted_project_config`, `observe_closeout_record`; `observe_provider_identity`, `observe_issue_identity`, `observe_issue_policy`, `read_current_status` only with enabled Jira; additionally `observe_jira_reconciliation` when the supplied project configuration (--project-config) has `jira.enabled is True` | CLI |
| `owner-publication-prepare` | `authorize_owner_publication` | CLI |
| `owner-publication-resume` | `observe_identity`, `observe_remote_heads`, `create_draft_pr`, `observe_draft_pr` | CLI |
| `owner-publication-status` | None | CLI |
| `batch-operation` | `batch_coordinator` | CLI |
| `batch-closeout` | `batch_coordinator`, `observe_batch_merge`, `observe_issue_identity`, `observe_issue_policy`, `observe_provider_identity`, `read_current_status`, `write_transition`, `read_transition` | CLI |
| `snapshot` | None | CLI |
| `planning-state prune` | None | CLI |

`cycle --overlap-allowances` additionally requires `observe_overlap_allowance`:
the CLI checks whether the flag was supplied, even an empty loaded list.
`cycle --replan-request` additionally requires `observe_replan_trigger`.

CLI forwards these additional callbacks when callable:

- `observe_overlap_conflict`: expired allowance conflict observation.
- `observe_overlap_sync_inventory`: post-sync provider PR-head observation.
- `sync_writer`: confirmed conflict execution.
- `reconcile_overlap_sync`: UNKNOWN receipt-only recovery.
- `observe_dispatch_quiescence`: terminal proof for accepted dispatches.

[Jira scope](JIRA-WRITE-SCOPE.md).

Runtime overlap paths require the corresponding callback. This controller contract applies;
the reference adapter lacks owner-publication and batch operations.

## Automation switches

`controller` defaults `false`: master `dispatch_enabled`; `auto_dispatch`,
`auto_request_critic`, `auto_resume_amendments`. Each path requires both switches. Disabled paths record
`CONTROLLER_AUTOMATION_DISABLED` before publication observation/dispatch.
Invalid switches refuse before recovery/provider calls. Preserve reviewed `controller`; disabling cancels unstarted work. Never relaunch uncertain launches.

Install/upgrade: five switches false, including `auto_transition_jira`. Project governance enables dispatch; upgrades preserve unmigrated values.
Switches grant no publication/review/merge authority or adapter qualification.
