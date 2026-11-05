# Operating configuration

Run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste in adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings, budgets, allowlists, floors, escalation and shared critic route; legacy per-stream routes accepted. Review changes; see [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` share `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. Disabled brokers do not bind; heavy/GPU limits are not stream counts; configuration cannot prove host/reviewer availability.

`effective_ceiling_governance_path`: binding path, project wins ties; null means only omitted six-lane default binds. `effective_ceiling_sources`: ties. Planning checks slots/ownership/spawn.

Bound cycles accept trusted host callback capacities via `produce_planning_resource_observation`. Planning reauthenticates closed evidence/capacities; missing authentication, self-digests and operator values fail closed. Observed capacity: 0–1000; zero selects none (`safe_lane_count == 0`).

Schemas accept `streams.count` 1–12 with A–L routes and optional project `execution.max_lanes` 1–12. Omitting `execution.max_lanes` retains six. Lower binding limits, including `execution.max_parallel_tickets` and enabled `execution.host_broker.max_workers`, still apply. Migration: raise protected ceilings only through review. Schema acceptance grants no dispatch authority.

| Contract | Fields | Range |
| --- | --- | --- |
| `operating-config` | `streams.count` | 1–12 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1–1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0–1000 |
| `ticket-plan` | `lane_cap` | 1–1000 |
| `host-lease` | `worker_slots` | 1–1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0–1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1–1000 |

`OPERATING_CONFIG.yaml`: unprotected choices. Defaults: twelve `gpt-6.1-sol`/high workers; shared critic, controller, specialists `gpt-6.1-sol`/high; `gpt-6-luna`/high simple work. Preserve routes; invalid choices block CONFIGURED.

`independent_reviewers.allocation: shared_critic` uses slot. Legacy `one_per_stream` and fixed counts bind. `--propose-operating-capacity` proposes ceiling change.

Owner overrides require authenticated `fixed`/`max` decisions and audited history. sealed safe capacity: `fixed` selects count or refuses unsafe; `max` limits recommendations. Neither raises governance/observed capacity. Replans await round boundaries; assignments retain reservations. Triggers persist; durable plans constrain admission at start.

Authenticated readers trim/casefold and collapse whitespace/ASCII-hyphen runs: `To Do`/`To-Do` → `ready`, `In Progress`/`In-Progress` → `in_progress`, `Done` → `closed`. Vocabulary: ready `open`/`ready`/`backlog`; active `in_flight`/`working`/`dispatched`/`in_progress`; closed `closed`/`done`/`merged`/`cancelled`/`canceled`. Unknown names grant neither admission nor closed dependencies; reviewed mappings required. Graph callers supply canonical vocabulary.

Translate, echo and execute without reconfirmation:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show results; escalation pins; floors prevail.

Refusals exit 2 with path, value, remedy; never clamp or partially apply mixed changes.

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

Epic: echo ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Matching `epic_id` selects scoped routes after global and before agent/ticket overrides, within allowlists/floors. Never broaden Epic identity. `--epic` supports no scoped counts/simple switches/recommendations. Unpinning requires a route.

`--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; repairs atomic.

Changes affect dispatch; running work retains route/hash. Surplus streams finish without packets or cancellation. A–F proves no availability.

Read Jira; save IDs/paths in `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory: `data/`, `data/**`, `data/*` are equivalent prefixes; other globs refuse by Epic/value. Unknown/overlapping scopes group work; refusals name Epic/value. Risk/specialist triggers raise routes; no Epics: keep/custom.

Table; wait for adoption/keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute stale hashes.

Defaults unpinned; unchanged pins share footnote. Stored/named rows and replay checks remain. Adoption preserves pins/floors; custom instructions change them.

Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`; audits retain instructions, hashes, changes and source. Bootstrap creates/appends/replaces AWF `.gitignore` block, preserving outside bytes; `MIGRATION-v1.9.4-to-v1.9.5.md` defines placement, deduplication, malformed boundaries and `added_lines`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/audit writes without replacement. Preserve conflicts/interrupted staging files outside audit chain within bounds. Recovery requires independently inspected journal SHA-256; unsupported primitives fail closed.

Dispatch/reservation/settlement retain separate `operating_hash`/`policy_hash`; settlement uses its reservation hash. Absent/null historical hashes remain unobserved. Host authenticates instructions/routes; records establish consistency.


Generated `OPERATING_CONFIG.yaml` includes `budgeting`:
accounting/enforcement `false`; dormant ticket limit `4000000`; lifetime-project
limit `1000000000`. Older missing fields retain legacy budgeting. Limits enable
neither switch; accounting OFF disables enforcement. Epic overrides cannot alter
budget settings.

Supported paths: `budgeting.accounting_enabled`,
`budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket` and
`budgeting.max_tokens_per_project`. Mutations require trusted host registry
injected into `set_operating` or `operating.main`; the standalone CLI lacks an
authenticated owner observer and refuses them. Consume it under the operating
lock after validation. Settings and owner observation share a journal and
append-only audit, including pinned recovery after interruption. Show/status
display persisted effective settings.
See [budgeting](39-BUDGETING.md) for host integration, UNKNOWN attribution,
canonical totals, preserved reservations and versioned upgrade decision.
