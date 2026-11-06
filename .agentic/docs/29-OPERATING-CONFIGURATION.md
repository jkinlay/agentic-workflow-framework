# Operating configuration

`& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; adoption PR: **keep defaults**, **review Epics and recommend**, **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings/budgets/allowlists/floors/escalation/shared critic; legacy routes accepted; see [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, enabled `execution.host_broker.max_workers`.

`effective_ceiling_governance_path`: binding path; project wins ties; null: omitted default binds. `effective_ceiling_sources`: ties.

Cycles reauthenticate `produce_planning_resource_observation`; capacity 0–1000; zero: `safe_lane_count == 0`.

Schemas: `streams.count` 1–12 with A–L routes; optional project `execution.max_lanes` 1–12. Omitting `execution.max_lanes` retains six. Lower binding limits, including `execution.max_parallel_tickets` and enabled `execution.host_broker.max_workers`, still apply. Migration: raise protected ceilings only through review. Schema acceptance grants no dispatch authority.

| Contract | Fields | Range |
| --- | --- | --- |
| `operating-config` | `streams.count` | 1–12 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1–1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0–1000 |
| `ticket-plan` | `lane_cap` | 1–1000 |
| `host-lease` | `worker_slots` | 1–1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0–1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1–1000 |

Defaults: twelve `gpt-6.1-sol`/high workers, shared critic/controller/specialists; `gpt-6-luna`/high simple work. Preserve routes; invalid blocks CONFIGURED.

`independent_reviewers.allocation: shared_critic`: one slot; legacy counts bind. `--propose-operating-capacity` proposes ceilings.

Audited authenticated `fixed` selects safe counts; `max` limits recommendations. Neither raises sealed safe capacity. Replan at round boundaries; preserve reservations/triggers.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Matching `epic_id` selects allowed scoped routes. `--epic` excludes counts/simple switches/recommendations. Unpinning requires a route.

Dispatch changes; running work retains route/hash. Surplus finishes without packets/cancellation. Labels prove no availability.

Read Jira; save IDs/paths in `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

`data/`, `data/**`, `data/*` match prefixes; other globs refuse. Unknown/overlapping scopes group work; risk/specialists raise routes.

Await adoption/keep; apply: `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute hashes.



Commit `.agentic-state/operating/changes/` with settings. Bootstrap preserves outside `.gitignore` bytes; `MIGRATION-v1.9.4-to-v1.9.5.md` defines placement, deduplication, malformed boundaries and `added_lines`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/audit without replacement; preserve conflicts. Inspect/pin journal SHA-256.
Both domains require latest consumed confirmation's registered transaction. Unconfirmed/superseded journals
refuse without writes. Register before publication: published transactions recover once;
otherwise `set`/`confirm` afresh. Preserve evidence.


Generated `OPERATING_CONFIG.yaml`: accounting/enforcement OFF; dormant limits
4000000/ticket, 1000000000/project lifetime. Absent fields retain legacy policy; manual budget adoption. Accounting OFF disables enforcement; limits enable
neither switch. No Epic budget overrides.

`budgeting.accounting_enabled`, `budgeting.enforcement_enabled`,
`budgeting.max_tokens_per_ticket`, `budgeting.max_tokens_per_project`.
`set` validates/stores proposal, prints changes/digest, exits 3
`PENDING_OWNER_CONFIRMATION`; settings stay unchanged. Protected `--initialize` refuses. Owner runs `workflow.py operating confirm
--digest D` within 15 minutes in their terminal; type eight digest characters.
Skills never confirm; verify with `show`.

GitHub User ID matches `merge_gate.trusted_owner_ids`;
empty refuses `OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`.
Token variables, foreign GH_HOST, noninteractive streams refuse. Windows requires
console handles; NUL/pipes/files refuse. Resolve `gh`; scripts and effective
sandbox-group/capability modification/replacement grants refuse; owner/admin
rights do not. Absent sandbox principals pass. Audit retains
path/SHA-256. Repository/governance/prior-settings/exact-change mismatches
and replay refuse. Consume/journal/audit/readback under lock.

Protected OS-account storage: Windows token SID/HKLM ProfileList
ProfileImagePath/AppData/Local; POSIX password-database home.
Never AWF_STATE_HOME/LOCALAPPDATA/USERPROFILE/HOME.
Bind metadata-derived Git common directory; project ID cross-checks. Editable IDs cannot hide confirmations.
Worktrees share; other clones reconfirm.
Ignore Git environment overrides; gitfiles require registered backlinks outside sandbox write authority.
Deny worker Git-metadata writes/replacement; binding protects clone identity,
not against processes able to replace that boundary. Without Git, protected changes refuse.

Loads never inspect Git/gh executables. Without a store/confirmation, fresh
OFF/default dormant limits/default calendars/absent legacy blocks load; nondefaults refuse protected admission. Changes report storage/Git refusals. OFF needs no usage ledger.
Older checkouts/mismatches: show/validation diagnose latest-confirmation differences; unrelated changes work; protected work refuses.
Proposals: pending. Protected choices/confirmation repair admission
preserving audit. Screen/digest bind every confirmed-value difference,
including external edits/removal; display complete settings for no-change reconfirmation.
Audit/journals prove nothing; transactions bind governance/prior settings.

`trusted_owner_ids` is editable, but still requires terminal/login. Full owner rights can bypass. See [budgeting](39-BUDGETING.md).

## Operating calendar

`operating_calendar` preserves legacy bytes/hashes: `continuous` work, `Europe/London`; `review_batching` enabled, weekdays `[1,2,3,4,5]`, presentation `07:00`, cutoff `17:00`; `eod` daily `17:00`.

Paths: `timezone`, `work_mode`, `review_batching.enabled`, `.weekdays`, `.presentation_time`, `.cutoff_time`, `eod.time`, `eod.weekdays`, prefixed `operating_calendar.`. JSON weekdays; unknown/null fields, invalid IANA zones/times/days, presentation >= cutoff refuse atomically. No Epic overrides. Show effective/persisted.

Calendar shares budgeting's store binding, protected-domain/load-time digest checks and transaction; one digest consumes once. Validate transitions before proposal/consumption. Retain historical observations. No calendar budget/enrollment state. See [calendar](operating-calendar.md).
