# Operating configuration

Run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste in adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings, budgets, allowlists, floors, escalation, shared critic; legacy per-stream routes accepted. Review changes; see [controller](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` share `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, enabled `execution.host_broker.max_workers`. Disabled brokers/heavy/GPU limits never constrain streams. Availability remains unproven.

`effective_ceiling_governance_path`: binding path, project wins ties, null=default; `effective_ceiling_sources`: ties. Check slots/ownership/spawn.

`produce_planning_resource_observation` requires reauthenticated host capacities; operator values refuse. Capacity: 0–1000; zero selects none (`safe_lane_count == 0`).

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

`OPERATING_CONFIG.yaml` defaults: twelve `gpt-6.1-sol`/high workers, shared critic/controller/specialists; `gpt-6-luna`/high simple work. Preserve routes; invalid choices block CONFIGURED.

`independent_reviewers.allocation: shared_critic` uses slot. Legacy `one_per_stream` and fixed counts bind. `--propose-operating-capacity` proposes ceiling change.

Authenticated, audited `fixed` refuses counts above sealed safe capacity; `max` limits recommendations. Neither raises capacity. Replans await round boundaries; preserve reservations/triggers. Durable plans constrain admission.

Authenticated readers trim/casefold, collapse whitespace/ASCII-hyphens: `To Do` → `ready`, `In Progress` → `in_progress`, `Done` → `closed`. Vocabulary: ready `open`/`ready`/`backlog`; active `in_flight`/`working`/`dispatched`/`in_progress`; closed `closed`/`done`/`merged`/`cancelled`/`canceled`. Unknowns require reviewed mappings, granting no admission/closure. Graph callers supply canonical vocabulary.

Choices:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Refusals exit 2: path/value/remedy; no clamping/partial application.

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

Epic ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Exact `epic_id` selects after global, before agent/ticket overrides, within allowlists/floors. No scoped counts/simple switches/recommendations. Unpinning requires a route.

`--set epic_overrides.PROJ-123.streams.B.worker=...` also works; repairs atomic.

Changes affect dispatch; running work retains route/hash. Surplus finishes without packets/cancellation. Labels prove no availability.

Read Jira; save IDs/paths in `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory: `data/`, `data/**`, `data/*` are equivalent; other globs refuse by Epic/value. Unknown/overlapping scopes group work. Risk/specialist triggers raise routes; no Epics: keep/custom.

Table; wait for adoption/keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute stale hashes.

Defaults unpinned; preserve pins/floors/rows/replay checks.

Commit `.agentic-state/operating/changes/` with settings; audits retain instructions/hashes/changes/source. Bootstrap preserves outside `.gitignore` bytes; see `MIGRATION-v1.9.4-to-v1.9.5.md`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/audit without replacement; preserve bounded conflicts/interrupted staging. Recovery requires independently inspected journal SHA-256; unsupported primitives refuse.

Dispatch/reservation/settlement retain `operating_hash`/`policy_hash`; settlement uses reservation hash. Historical absent/null hashes remain unobserved. Host authenticates instructions/routes; records establish consistency.


Generated `OPERATING_CONFIG.yaml` `budgeting`:
accounting/enforcement `false`; dormant ticket limit `4000000`; lifetime-project
limit `1000000000`. Missing fields retain legacy budgeting. Limits enable
neither switch; accounting OFF disables enforcement. No Epic budget overrides.

Supported paths: `budgeting.accounting_enabled`,
`budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket` and
`budgeting.max_tokens_per_project`. Budgeting/calendar `set` validates/stores a protected proposal,
prints exact changes/digest, exits 3 `PENDING_OWNER_CONFIRMATION`; settings stay
unchanged. Owner runs `workflow.py operating confirm --digest D` in their
terminal within 15 minutes, typing its first eight characters. Skills never run
confirm; after owner-reported completion, run `show` for persistence.
Confirmation observes the machine's stored GitHub User login and requires its
numeric ID in reviewed `merge_gate.trusted_owner_ids`; empty refuses
`OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`. Token
environment variables, foreign GH_HOST and noninteractive stdin/stdout refuse.
Project/repository/governance/prior-settings/exact-change mismatches/replay
refuse. Under lock: validate, consume, journal, audit, readback.
Stored GitHub login/terminal cannot defend against processes with the owner's
full rights and their own terminal. OFF/unchanged assignments require confirmation.
See [budgeting](39-BUDGETING.md) for host integration, UNKNOWN attribution,
canonical totals, preserved reservations and versioned upgrade decision.

## Operating calendar

Optional `operating_calendar` defaults preserve legacy bytes/hashes: `continuous` work, `Europe/London`; `review_batching` enabled, weekdays `[1,2,3,4,5]`, presentation `07:00`, cutoff `17:00`; `eod` daily `17:00`.

Dotted paths: `timezone`, `work_mode`, `review_batching.enabled`, `.weekdays`, `.presentation_time`, `.cutoff_time`, `eod.time`, `eod.weekdays`, prefixed `operating_calendar.`. Weekdays: JSON lists. Unknown/null fields, invalid IANA zones/times/days, presentation >= cutoff refuse atomically. No Epic overrides. Show: effective/persisted settings.

Calendar uses shared `set`/`confirm` admission: one digest consumes once. Pure validators check transitions before proposal/consumption. Historical observations/recovery remain supported. Calendar creates no budget/enrollment state. See [calendar](operating-calendar.md).
