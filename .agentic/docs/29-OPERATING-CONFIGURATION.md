# Operating configuration by conversation

Run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste table in adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

## Two configuration layers

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings, budgets, model allowlists, floors, escalation and one shared critic route; legacy per-stream reviewer routes remain accepted. Changes require review; [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` share `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. Disabled brokers do not bind. Availability remains unproven.

`effective_ceiling_governance_path` names the binding path; `effective_ceiling_sources` lists ties. Planning checks observed slots/ownership/spawn depth.

`produce_planning_resource_observation` requires trusted host capacities, reauthenticated. Missing authentication refuses; zero capacity selects no lanes.

Generated schemas accept `streams.count` 1–12 with A–L routes and optional project `execution.max_lanes` 1–12. Omitting `execution.max_lanes` retains six. Lower binding limits, including `execution.max_parallel_tickets` and enabled `execution.host_broker.max_workers`, still apply. Migration: raise protected ceilings only through review. Schema acceptance grants no dispatch authority.

| Contract | Fields | Range |
| --- | --- | --- |
| `operating-config` | `streams.count` | 1–12 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1–1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0–1000 |
| `ticket-plan` | `lane_cap` | 1–1000 |
| `host-lease` | `worker_slots` | 1–1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0–1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1–1000 |

`OPERATING_CONFIG.yaml`: project choices. Fresh defaults: twelve `gpt-6.1-sol`/high workers, shared critic/controller/specialists; `gpt-6-luna`/high simple work. Preserve existing routes; invalid choices prevent CONFIGURED.

`independent_reviewers.allocation: shared_critic` uses one slot; legacy fixed counts remain binding. Capacity proposals require reviewed governance changes.

Authenticated `fixed`/`max` decisions retain audited history. After sealed safe capacity, `fixed` refuses unsafe counts; `max` limits recommendations. Replans await round boundaries; running reservations remain. Neither raises governance/observed capacity.

Authenticated provider readers normalize known statuses. Unknown custom names require reviewed adapter mappings; they grant neither admission nor closed dependencies.

## Apply the user's choices

Echo and execute instructions:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show results; mandatory floors prevail.

Refusals exit 2 with path, binding governance value and remedy; never clamp/partially apply mixed valid/invalid changes.

Above-cap upgrades refuse; `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` repairs an above-cap count; surplus streams drain.

For Epic instructions, echo observed ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Only matching observed Epic IDs select scoped routes, within floors. No scoped counts, simple switches or recommendations; unpinning requires a complete route.

`--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; global/scoped repairs are atomic.

Changes affect subsequent dispatch. Surplus streams drain; running work retains route/hash.

## Recommend from observed Epics

Read Jira without transitions; save observed IDs/paths using `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory prefixes `data/`, `data/**`, `data/*` are equivalent; other globs refuse. Unknown/overlapping scopes group work; risk/specialist triggers raise routes.

Present table; wait for adoption or keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Stale hashes require recomputation.

Defaults stay unpinned; adoption preserves pins/floors. Replay checks remain.

## Audit and recovery

Commit `.agentic-state/operating/changes/` with settings; audits retain instructions/hashes/source. Bootstrap preserves outside `.gitignore` bytes; see `MIGRATION-v1.9.4-to-v1.9.5.md`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`. Incomplete transactions block dispatch.

Publish flushed journal/audit without replacement. Preserve conflicts/staging evidence. Recovery requires independently inspected journal SHA-256; unsupported atomic primitives refuse.

Reservations/settlement retain operating/policy hashes. Historical absent/null hashes remain unobserved. Host authenticates instructions/routes; records establish consistency.


## Budget choices

Fresh `budgeting`: accounting/enforcement `false`, dormant ticket/project limits `4000000`/`1000000000`. Omitted blocks retain legacy budgeting. Limits enable neither switch; accounting OFF disables enforcement. No Epic overrides.

Supported paths: `budgeting.accounting_enabled`, `budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket`, `budgeting.max_tokens_per_project`. Mutations require the injected trusted-host registry; standalone CLI refuses. Admission follows complete validation under the lock, using the existing journal/audit/recovery. Show/status display effective settings. See [budgeting](39-BUDGETING.md) for host integration and upgrade disposition.

## Operating calendar

Optional `operating_calendar` resolves omitted defaults on read without changing legacy bytes/hashes. Work remains `continuous`; timezone defaults `Europe/London`. `review_batching` defaults enabled, weekdays `[1,2,3,4,5]`, presentation `07:00`, cutoff `17:00`. `eod` defaults every day at `17:00`.

Dotted set paths cover `timezone`, `work_mode`, `review_batching.enabled`, `.weekdays`, `.presentation_time`, `.cutoff_time`, `eod.time`, `eod.weekdays`, prefixed `operating_calendar.`. Weekdays use JSON lists. Unknown/null fields, invalid IANA zones/times/days and presentation >= cutoff refuse atomically. No Epic overrides. Show exposes effective settings separately from persisted configuration.

Trusted infrastructure injects `CalendarSettingsRegistry` through `calendar_registry`, using budgeting's host-observer pattern; instruction text authenticates nobody. Observation consumption follows validation under the operating lock; existing journal/audit/pinned recovery applies. Calendar changes enable no reporting, budgeting, chat or adapters. See [calendar](operating-calendar.md) for DST and controller wiring.
