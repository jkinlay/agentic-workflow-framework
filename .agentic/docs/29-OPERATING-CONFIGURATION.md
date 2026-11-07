# Operating configuration

`& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; **keep defaults**, **review Epics and recommend**, **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings/budgets/allowlists/floors/escalation/shared critic; legacy routes accepted; [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, enabled `execution.host_broker.max_workers`.

`effective_ceiling_governance_path`: binding path; project wins ties; null: omitted default binds. `effective_ceiling_sources`: ties.

Cycles reauthenticate `produce_planning_resource_observation`; operator values refuse; capacity 0–1000; zero: `safe_lane_count == 0`.

`streams.count` 1–12 with A–L routes; optional project `execution.max_lanes` 1–12. Omitting `execution.max_lanes` retains six. Lower binding limits, including `execution.max_parallel_tickets` and enabled `execution.host_broker.max_workers`, still apply. Migration: raise protected ceilings only through review. Schema acceptance grants no dispatch authority.

| Contract |Fields|Range|
|---|---|---|
| `operating-config` | `streams.count` | 1–12 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1–1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0–1000 |
| `ticket-plan` | `lane_cap` | 1–1000 |
| `host-lease` | `worker_slots` | 1–1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0–1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1–1000 |

Defaults: twelve `gpt-6.1-sol`/high workers; shared critic/controller/specialists; `gpt-6-luna`/high simple work. Preserve routes; invalid blocks CONFIGURED.

`independent_reviewers.allocation: shared_critic`: one slot; legacy counts bind. `--propose-operating-capacity` proposes ceilings.

Authenticated, audited `fixed` refuses above sealed safe capacity; `max` limits recommendations. Neither raises capacity; durable plans constrain admission; replan at round boundaries; preserve reservations/triggers.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`; `epic_id` selects scoped allowlisted routes. `--epic` excludes counts/simple switches/recommendations; unpinning requires routes.

Running work retains route/hash.

Jira IDs/paths: `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

`data/`, `data/**`, `data/*`: equivalent prefixes; other globs refuse. Unknown/overlapping scopes group; risk/specialists raise routes. No Epics: keep/custom.

Await adoption/keep: `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute hashes.

Commit settings/`.agentic-state/operating/changes/`; retain instructions/hashes/changes/source. Bootstrap preserves outside `.gitignore` bytes; `MIGRATION-v1.9.4-to-v1.9.5.md`: placement/deduplication/malformed boundaries/`added_lines`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/audit without replacement; preserve conflicts/interrupted staging. Independently inspect/pin journal SHA-256. Require latest consumed confirmation's registered transaction across domains. Unconfirmed/superseded journals refuse without writes. Prepublication registration: published transactions recover once; otherwise fresh `set`/`confirm`. Preserve evidence. Unsupported primitives refuse.

`OPERATING_CONFIG.yaml`: accounting/enforcement OFF; dormant 4000000/ticket, 1000000000/project-lifetime. Absent: legacy policy/manual adoption. Accounting OFF disables enforcement; limits enable neither.

`budgeting.`: `accounting_enabled`, `enforcement_enabled`, `max_tokens_per_ticket`, `max_tokens_per_project`.
Budget/calendar/reporting `set`: validate/store proposal, print changes/digest, exit 3 `PENDING_OWNER_CONFIRMATION`; unchanged settings. Protected `--initialize` refuses. Owner terminal: `workflow.py operating confirm --digest D` within 15 minutes; type eight digest characters. Skills never confirm; verify persistence: `show`. OFF/unchanged requires confirmation.

GitHub User ID matches reviewed `merge_gate.trusted_owner_ids`; empty: `OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`.
Token variables/foreign GH_HOST/noninteractive streams refuse. Windows: console handles; NUL/pipes/files refuse. Resolve native absolute `gh` once; Scripts/project paths refuse; effective sandbox-group/capability modification/replacement grants refuse; owner/admin rights pass. Absent sandbox principals pass. Audit: path/SHA-256. Project/repository/governance/prior-settings/exact-change mismatches/replay refuse. Consume/journal/audit/readback under lock.

Protected OS-account storage: Windows token SID/HKLM ProfileList ProfileImagePath/AppData/Local; POSIX password-database home.
Never AWF_STATE_HOME/LOCALAPPDATA/USERPROFILE/HOME.
Bind metadata-derived Git common directory; cross-check project ID. Editable IDs cannot hide confirmations. Worktrees share; clones reconfirm.
Ignore Git environment overrides; gitfiles require registered backlinks outside sandbox write authority.
Deny worker Git-metadata writes/replacement; clone binding cannot protect against boundary replacement. Without Git: protected changes refuse.

Loads never inspect Git/gh executables. Without store/confirmation: fresh OFF/default limits/calendars/reporting/absent legacy loads; nondefaults refuse admission. Diagnose storage/Git refusals. OFF needs no usage ledger.
Older checkouts/mismatches: show/validation diagnose latest-confirmation differences; unrelated changes work; protected work refuses. Pending proposals; protected choices/confirmation repair admission preserving audit. Screen/digest bind every confirmed-value difference, including external edits/removal; no-change reconfirmation displays complete settings. Audit/journals prove nothing; transactions bind governance/prior settings.

Editable `trusted_owner_ids` requires terminal/login. Owner rights/software replacement bypass. See [budgeting](39-BUDGETING.md).

## Operating calendar

`operating_calendar` preserves legacy bytes/hashes: `continuous` work, `Europe/London`; `review_batching` enabled, weekdays `[1,2,3,4,5]`, presentation `07:00`, cutoff `17:00`; `eod` daily `17:00`.

`operating_calendar.` paths: `timezone`, `work_mode`, `review_batching.enabled`, `.weekdays`, `.presentation_time`, `.cutoff_time`, `eod.time`, `eod.weekdays`. JSON weekdays; unknown/null fields, invalid IANA zones/times/days, presentation >= cutoff refuse atomically. Show effective/persisted.

No budget/calendar/reporting Epic overrides. Budget/calendar/reporting share store binding, protected-domain/load-time digests/transaction; digest consumes once. Validate transitions before proposal/consumption. Retain historical observations; no calendar budget/enrollment state. See [calendar](operating-calendar.md).

Reporting OFF; `reporting.mode`: `off/teams/slack/both`; `reporting.bindings`: project/repository/scope/platform/destination/revision/secret_ref JSON. Missing/foreign bindings refuse before secrets. `show`: persisted; `reporting-status`: delivery evidence. Enables no chat/accounting/dispatch/Jira writes. See [reporting](REPORTING-196.md).

Readers normalize case/whitespace/hyphens: `To Do`=`ready`, `In Progress`=`in_progress`, `Done`=`closed`. Ready: `open`/`ready`/`backlog`; active: `in_flight`/`working`/`dispatched`/`in_progress`; closed: `closed`/`done`/`merged`/`cancelled`/`canceled`. Unknowns: reviewed mappings, never authority.

Dispatch/reservation/settlement retain `operating_hash`/`policy_hash`; settlement uses reservation hash. Absent/null history: unobserved. Host authenticates instructions/routes; records establish consistency.
