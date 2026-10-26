# Operating configuration by conversation

At CONFIGURED, run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste table in adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

## Two configuration layers

Protected `.agentic/PROJECT_CONFIG.yaml` holds ceilings, budgets, model allowlists, floors, escalation and one shared critic route; legacy per-stream reviewer routes remain accepted. Changes require review.

Show/status/`operating.state` share `effective_ceiling`: runtime minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. Disabled brokers do not bind; heavy/GPU limits are not stream counts. Configuration cannot prove host/reviewer availability.

`effective_ceiling_governance_path`: binding governance path, project preferred on ties; null: only omitted six-lane default binds. `effective_ceiling_sources` lists all ties. Planning checks observed slots/ownership/spawn depth.

For bound cycle, `produce_planning_resource_observation` accepts trusted host callback capacities only. Planning independently reauthenticates closed evidence and matching capacities; missing authentication, self-digests and operator values fail closed. Observed capacity: 0–1000; zero selects no lane (`safe_lane_count == 0`).

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

`OPERATING_CONFIG.yaml` holds unprotected project choices. Defaults: three `gpt-5.6-luna`/medium workers; one shared `gpt-5.6-sol`/high critic/adversarial handler; `gpt-5.6-sol`/high controller/specialists; `gpt-5.6-luna`/low simple work. Preserve existing routes; invalid choices prevent CONFIGURED.

`independent_reviewers.allocation: shared_critic` uses one reviewer slot. Legacy `one_per_stream` and fixed counts remain binding. `--propose-operating-capacity` only proposes ceiling change. Follow [adoption](20-NEW-PROJECT-SETUP.md) mode/conflict arguments.

Owner overrides require authenticated `fixed`/`max` decisions and audited history. After sealed safe capacity, `fixed` selects its count or refuses if unsafe; `max` limits recommendations. Neither raises governance/observed capacity. Replans await round boundaries; running assignments retain reservations. Authenticated triggers persist; durable plans constrain admission with lane-start rechecks.

Authenticated provider readers trim/casefold statuses and collapse whitespace/ASCII-hyphen runs: `To Do`/`To-Do` → `ready`, `In Progress`/`In-Progress` → `in_progress`, `Done` → `closed`. Vocabulary: ready `open`/`ready`/`backlog`; active `in_flight`/`working`/`dispatched`/`in_progress`; closed `closed`/`done`/`merged`/`cancelled`/`canceled`. Unknown custom names grant neither admission nor closed dependencies; they require reviewed adapter mappings. Direct graph callers supply canonical vocabulary.

## Apply the user's choices

Translate, echo and execute instructions without reconfirmation:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show results. Routes pin optional escalation; mandatory floors prevail.

Refusals exit 2 with path, binding governance value and remedy; never clamp/partially apply mixed valid/invalid changes.

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four streams under broker cap three; surplus streams drain.

For Epic instructions, echo observed ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Observed matching `epic_id` selects scoped routes after global and before agent/ticket overrides, within allowlists/floors. Never broaden Epic identity. `--epic` supports no scoped counts/simple switches/recommendations. Unpinning requires complete route.

`--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; global/scoped repairs are atomic.

Changes affect subsequent dispatch; running work retains route/hash. Surplus streams finish without packets or cancellation. A–F proves no availability.

## Recommend from observed Epics

Read Jira without transitions; save observed IDs/paths using `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Optional inventory: `data/`, `data/**`, `data/*` are equivalent prefixes; other globs refuse by Epic/value. Unknown/overlapping scopes group work; refusals name Epic/value. Risk/specialist triggers raise routes. No Epics offers keep/custom.

Present table; wait for adoption or keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Stale hashes require recomputation.

Defaults stay unpinned; unchanged pins share footnote. Rows and replay checks remain. Adoption preserves pins/floors; custom instructions change pins.

## Audit and recovery

Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`; audits retain instructions, hashes, changes and source. Bootstrap creates, appends, or replaces one AWF `.gitignore` block, preserving outside bytes; release guide `MIGRATION-v1.9.4-to-v1.9.5.md` defines placement, deduplication, malformed boundaries, and `added_lines`. Ignore operating locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`. Inspect recovery artifacts; incomplete transactions block dispatch.

Publish complete flushed journal/audit writes without replacement. Preserve conflicts and interrupted staging files outside audit chain within inventory bounds. Recovery requires independently inspected journal SHA-256. Unsupported atomic primitives fail closed.

Dispatch/reservation/settlement retain separate `operating_hash` and `policy_hash`; settlement uses its reservation's hash. Historical absent/null hashes remain unobserved. Host authenticates instructions/routes; records establish local consistency.
