# Continuous controller

1.9.6.

## Cycle

```text
python .agentic/scripts/workflow.py controller --help
```

Do not use `python -B -I .agentic/scripts/workflow.py controller …`:
isolated mode fails (AWF-207). Invoke without `-I`; retain `-B`. No fix
promised.

Protect state outside worktrees; never reissue mutations.

## Planning recovery

`controller_meta['production_planning']`: state; `.planning.sqlite3`: plans.
Format 2: `cycle_sequence`, `seen` digest/last-cycle pairs; legacy ages zero. Above 4096 evict oldest `(last_cycle, source_id)`; preserve pending sources.
Canonical UTF-8 saves cap at 4 MiB; ordinary reads at 8 MiB; cycles at
4,000 events including repeats. Oversize staged state refuses before events,
state/history writes, or dispatch, preserving prior saves/history.

Stop **all cycles using state** before recovery:

```text
python .agentic/scripts/continuous_controller.py --state ABSOLUTE_CONTROLLER_DB --stream A --worktree-root ABSOLUTE_WORKTREE planning-state prune
```

Recovery: 16 MiB; trim only `seen`, preserving state/history; report bytes/counts.
Unshrinkable state refuses: reduce event windows before accumulation.
`--forget-source SOURCE_ID` needs provider-owner confirmation; pending identities cannot be forgotten. Never delete planning key.

## Dispatch state and round boundaries

Dispatch payloads/receipts use closed schema v2. Opening protected state atomically
migrates only unambiguous 1.9.4 rows in `PENDING`, `IN_FLIGHT`, `UNKNOWN` with
their durable prior-inventory digest already protected. Legacy non-terminal sets
without boundary binding are refused before schema/row changes. Unsupported
states/receipts/fields, ambiguity, or initialization refusal roll back. Reference
adapters reject legacy calls/records before runner preparation/launch.

`PENDING`, `IN_FLIGHT`, `UNKNOWN`, and adapter `LAUNCHED` are non-terminal.
A successor inventory cannot refill a round while any such operation crosses the
boundary. Refill requires one fresh authenticated host trigger bound to prior/next
inventory digests and the complete durable dispatch/stream/ticket/tuple/state set.
Persist the seal before refill; atomically consume it with successor dispatch-intent
creation. Restart resumes only persisted successor intents; never lose refill or
recreate seals from replay.
`--replan-request REQUEST.json` supplies only a positive GitHub `comment_id`.
Adapter fetches immutable Date/ETag-bound bytes: a fresh, unedited configured
trusted-owner comment exactly `AWF-ROUND-REPLAN 1`, newline, then canonical JSON
binding repository, round, inventories, and complete non-terminal dispatch set.
Caller-authored identities refuse.

Provider reads never occur inside a controller-write transaction.
For overlap allowances, validate `LANE_START`; persist before `begun_at` and
adapter calls; digest binds dispatch, ticket, stream, exact tuple, allowance,
shared path, observation, and persistence time. Restart observation must carry
that digest and never relaunch the session.

Authenticated parked/worktree-diff sources independently reserve paths. Each cycle
observes complete provider-bound paused inventory and pinned-head/tree Git diff;
immediately repeat it after every inventory replacement before scheduling.
Failed refresh invalidates reservations and blocks scheduling. Reject
foreign/stale/partial, non-NFC, case/separator/traversal, symlink/non-canonical
Git paths. Reservations block overlapping starts despite owner allowances;
unrelated paths continue within capacity.

## Exact overlap allowances

Trusted-owner `AWF-OVERLAP-ALLOWANCE 1`: exact 24-hour pairs; Date/ETag binds repository, PRs, heads, files, branches and case-sensitive NFC paths. Re-observe ownership and UTC per provider read.

Require terminal dispatch proof/head-bound three-way merge, never blob inequality.
True conflicts need bounded union/expected-head mutation onto branch two
and sorted, hash-matched, at-most-512-KiB critic diffs.
`EXECUTED` binds fresh post-read UTC, provider Date/ETag heads and matching
inventory; `UNKNOWN` reads only its retained `COMPLETED` receipt.

Disabled Jira performs no access/observations. Before `merge-observed`, reconcile
through `jira-lifecycle JIRA_RECONCILED`; require protected records,
provider/canonical-issue/scope/owner-hold/status observers, and complete stable
pagination. Refuse counts on missing/mismatched evidence. Owner-closure tickets
hold In Review; `reconcile_merged_ticket` is inert. Require [review barrier](33-REVIEW-COMPLETION-BARRIER.md).

Unknown/failed attempts stop only their ticket. Assignment shares admission/locking
and protected `store` outside worktrees; `intake_assignment_state` retains
receipts/stops/identity. `jira-lifecycle BLOCK` uses `read_assignment(record)`
bound to original ID/digest/issue/provider/labels/controller comment/fresh time.
Preserve rejected receipts and synchronize intake. Issue-scoped
`recover_jira_operations` requires exclusive ownership; `read_transition` validates
operation/actor/provider/target. Never repeat writes or infer resolution from
status. Batch closeout accepts actor-bound recovery without write receipts;
preserve success/conflict evidence.

`jira-not-attempted:<operation_id>` proves batch pre-write failures; unattempted/deferred claims resume. Malformed observations require BLOCK without mutation. Legacy FAILED/no-receipt rows require uniquely copied predecessor readback (including JSON null), otherwise independent receipts.

`jira-identity:<operation_id>` pins provider/project/repository/issue. Stop writers; run `.agentic/scripts/continuous_controller.py --state DB --stream A --worktree-root WORKTREE --project-config CONFIG jira-identity-adopt --expected-project-id PROJECT_ID --expected-repository-id REPOSITORY_ID --operator OPERATOR --now TIME`.
IDs match accepted configuration; idempotent adoption preserves rows/receipts/hashes. ADOPTED_LEGACY_SUCCEEDED records operator/time/configuration digest as OPERATOR_SUPPLIED_NOT_OBSERVED, proving no historical authority/pre-adoption drift. Adopt healthy rows; report skipped UNKNOWN/FAILED/IN_FLIGHT by ticket/operation, retaining stops. `observe_jira_operation_identity(record)` reads historical envelopes only.

Without envelopes, stop writers; reuse global arguments with `jira-operation-resolve --issue KEY --operation-id UUID --expected-intent-sha256 DIGEST --expected-project-id PROJECT_ID --expected-repository-id REPOSITORY_ID --operator OPERATOR --now TIME --disposition ABANDON_NO_REPLAY --reason REASON --provenance REFERENCE`.
Retain original row/receipts plus operator/time/reason/reference/configuration digest as OPERATOR_SUPPLIED_NOT_OBSERVED; mark HELD. Never replay that lifecycle event or claim success/closeout/provider authority. Later events retain normal admission; no auto-clear. Missing `--stream` refuses clearly.

Retain OS claim lock before IN_FLIGHT through completion; recovery refuses live owners. Recheck the live claim under issue ownership before writing. Persist pre-write identity/status/updated/history. Require exactly one new final actor/from/target transition; hide later ones. Numeric Jira order proves novelty; 300-second host tolerance; equal timestamps refuse. Historical fallback requires pre-upgrade attempt inventory. AWF-175 owns changelog pagination/completeness.
Jira observation timestamps accept milliseconds/colonless offsets; canonical records stay strict. At 100 entries refuse before POST, reserving one readback: 99+1 succeeds. Over-limit refusals name AWF-175; concurrent growth beyond 100 after POST stays UNKNOWN. Proven pre-POST failures, including lock OSError, return retryable NOT_ATTEMPTED/PENDING without a stop.

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

Adapter: 1 MiB stderr cap, credentials stripped, fail-closed, and
`execution_authority: false`.

### Required adapter operations

Load reviewed, SHA-pinned module exposing
`build_adapters(config)` and callable operations. None means no reviewed adapter operations, regardless of
state/configuration requirements or effects.

| Controller subcommand | Required adapter operations | Source |
|---|---|---|
| `cycle` | `observe_inventory`, `observe_reservations`, `dispatch_ticket`, `observe_dispatch`, `deliver_status`, `observe_publication`, `observe_host_resources`, `verify_host_observation`, `observe_planning_backlog`, `verify_planning_backlog`, `observe_planning_events`, `verify_planning_events`, `verify_planning_owner` | 225–232 |
| `jira-lifecycle` | `observe_provider_identity`, `observe_issue_identity`, `observe_issue_policy`, `read_current_status`, `write_transition`, `read_transition` | 243–244 |
| `merge-observed` | `fetch_scope_page`, `observe_accepted_project_config`, `observe_closeout_record`; `observe_provider_identity`, `observe_issue_identity`, `observe_issue_policy`, `read_current_status` only with enabled Jira; additionally `observe_jira_reconciliation` when the supplied project configuration (--project-config) has `jira.enabled is True` | CLI |
| `owner-publication-prepare` | `authorize_owner_publication` | 217–218 |
| `owner-publication-resume` | `observe_identity`, `observe_remote_heads`, `create_draft_pr`, `observe_draft_pr` | 220–221 |
| `owner-publication-status` | None | 206–207, 222–223 |
| `batch-operation` | `batch_coordinator` | 208–211 |
| `batch-closeout` | `batch_coordinator`, `observe_batch_merge`, `observe_issue_identity`, `observe_issue_policy`, `observe_provider_identity`, `read_current_status`, `write_transition`, `read_transition` | 58–60, 212–215 |
| `snapshot` | None | 188, 206–207, 254–255 |
| `planning-state prune` | None | 145–147, 190–194 |

`cycle --overlap-allowances` additionally requires `observe_overlap_allowance`:
CLI checks presence, even an empty loaded list.
`cycle --replan-request` additionally requires `observe_replan_trigger`.

CLI forwards callbacks when callable:

- `observe_overlap_conflict`: expired allowance conflict observation.
- `observe_overlap_sync_inventory`: post-sync provider PR-head observation.
- `sync_writer`: confirmed conflict execution.
- `reconcile_overlap_sync`: UNKNOWN receipt-only recovery.
- `observe_dispatch_quiescence`: terminal proof for accepted dispatches.

See [Jira scope](JIRA-WRITE-SCOPE.md).

Runtime overlap paths require the corresponding callback. This controller contract applies;
the reference adapter lacks owner-publication and batch operations.

## Automation switches

`controller` booleans ship `false`: master `dispatch_enabled`; `auto_dispatch`,
`auto_request_critic`, `auto_resume_amendments`. Each path needs both switches. Disabled paths record
`CONTROLLER_AUTOMATION_DISABLED` before publication observation/dispatch.
Invalid switches refuse before recovery/provider calls. Preserve reviewed `controller`; disabling cancels unstarted work. Never relaunch uncertain launches.

Install/upgrade require five switches, including `auto_transition_jira`, false. Enable dispatch under project governance; upgrades preserve values without migration.
Switches grant no publication, review, merge or adapter qualification.
