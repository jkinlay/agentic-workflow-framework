# Continuous controller

1.9.6.

## Cycle

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

Do not use `python -B -I .agentic/scripts/workflow.py controller …` today: it
fails under isolated mode (AWF-207). Invoke without `-I`; `-B` may be
retained. No fix release is promised.

State outside worktrees; never reissue mutations.

## Planning recovery

`controller_meta['production_planning']`: state; `.planning.sqlite3`: plans.
Format 2: `cycle_sequence`; `seen` digest/last-cycle pairs;
legacy ages zero; identical observations refresh. Above 4096 evict oldest
`(last_cycle, source_id)`, preserving pending sources; never duplicate plans.
Canonical UTF-8 saves cap at 4 MiB; ordinary reads cap at 8 MiB;
cycles at 4,000 events including repeats. Oversize staged state refuses before
events, state/history writes or dispatch, preserving prior saves/history.

Stop **all cycles using state** before recovery:

```text
python .agentic/scripts/continuous_controller.py --state ABSOLUTE_CONTROLLER_DB --worktree-root ABSOLUTE_WORKTREE planning-state prune
```

Recovery: 16 MiB input; trim only `seen`; report bytes/counts;
preserves other state/history. Unshrinkable state refuses: reduce event windows before they accumulate.
Provider-owner confirmation permits `--forget-source SOURCE_ID` for changed bytes;
pending identities cannot be forgotten. Never delete the planning key; restart only after repair.

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

Canonical trusted-owner `AWF-OVERLAP-ALLOWANCE 1`: exact pairs, 24 hours. Plan/lane Date/ETag binds repository, PRs, heads, files,
branches and case-sensitive NFC paths. Re-observe ownership per lane; sample
UTC after each provider read.

Require terminal dispatch proof/head-bound three-way merge, never blob inequality.
True conflicts need bounded union/expected-head mutation onto branch two
and sorted, hash-matched, at-most-512-KiB critic diffs.
`EXECUTED` binds fresh post-read UTC, provider Date/ETag heads and matching
inventory; `UNKNOWN` reads only its retained `COMPLETED` receipt.

Disabled Jira performs no access and requires no Jira observers. Reconcile merges through `jira-lifecycle`
`JIRA_RECONCILED`; protected transition/readback records precede `merge-observed`.
Closeout binds record/binding. Progress never calls legacy
`reconcile_merged_ticket`: an inert 1.9.5 compatibility keyword, retained to avoid breaking adapters;
provider/canonical-issue/scope/owner-hold/status observers
are mandatory. Missing/mismatched evidence refuses counts. Owner-closure keywords
hold In Review. Complete, stable, bounded pagination precedes authoritative counts;
final submission requires the [review barrier](33-REVIEW-COMPLETION-BARRIER.md).

Unknown/failed operations stop writes.
Assignment shares admission/locking; intake/project-start/adoption/reassignment require protected `store`
outside worktrees. `controller_meta['intake_assignment_state']` retains receipts/stops/identity.
Recover via `jira-lifecycle BLOCK` and shipped `read_assignment(record)`, binding original
ID/digest, issue/provider, labels, controller comment and fresh time. Never rewrite;
preserve rejected receipts; synchronize intake recovery.
`jira-lifecycle` calls issue-scoped `recover_jira_operations` under exclusive ownership.
`read_transition` validates original operation/actor/provider/target; status alone never resolves uncertainty.
Never repeat writes. Batch closeout accepts actor-bound recovery without write
receipts. Resolutions survive legacy stops.
Failed reads preserve evidence; conflicts retain success,
report suspected external automation and require transition evidence.

`jira-not-attempted:<operation_id>` atomically distinguishes batch pre-write failures.
Retry closeout after failed reads; malformed observations require `jira-lifecycle BLOCK`,
without mutation. Proven unattempted/deferred claims resume.
Legacy FAILED/no-receipt rows require uniquely copied predecessor readback
(including JSON null). Otherwise owner restores independently retained
receipts before `jira-lifecycle`; never edit status/invent evidence.

`controller_meta['jira-identity:<operation_id>']` pins provider/project/repository/issue.
Healthy legacy SUCCEEDED rows adopt accepted configuration once, with
`ADOPTED_LEGACY_SUCCEEDED`, adoption time and configuration digest; subsequent drift refuses.
This recorded baseline cannot prove original authority or detect pre-adoption drift.
UNKNOWN/FAILED/IN_FLIGHT never adopt. Shipped `observe_jira_operation_identity(record)`
reads `{operation_id,intent_sha256,identity}` from protected
`jira_transition_directory/<operation_id>.json`; absent history proposes adoption, never unresolved recovery.
Lifecycle/batch forward it. Migration preserves rows/receipts/hashes.

Transition/closeout evidence uses persisted Jira pre-write `updated`; changelog
time must follow it and precede current Jira `updated`, with matching from/target/actor.
Host skew is irrelevant. Ambiguity/equal timestamps refuse. Older attempts without
this boundary retain strict time checks; never widen their window. AWF-175 owns
changelog pagination/completeness; recovery asserts no completeness.

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
$adapter=Get-Content $ADAPTER_CONFIG
$adapter.codex.isolated_home=$AWF_CODEX_HOME;$adapter.codex.windows_sandbox='elevated'
```

Adapter: stripped credentials; `execution_authority: false`.

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

`controller` booleans ship `false`: master `dispatch_enabled`; `auto_dispatch`,
`auto_request_critic`, `auto_resume_amendments`. Each path needs master and own switch. Disabled paths record
`CONTROLLER_AUTOMATION_DISABLED` before publication observation/dispatch.
Missing/non-boolean switches refuse before recovery/provider calls; CLI preserves
reviewed `controller`. Disabling cancels unstarted pending work; uncertain launches
reconcile through observation, never relaunching.

Install/upgrade require five switches, including `auto_transition_jira`, false. Enable dispatch under project governance; upgrades preserve values without migration.
Switches grant no publication, review, merge or adapter qualification.
