# Operating configuration

Run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste in adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings, budgets, allowlists, floors, escalation, critic route; legacy per-stream routes accepted. Review changes; see [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` share `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, enabled `execution.host_broker.max_workers`. Disabled brokers/heavy/GPU limits do not constrain streams. Configuration proves no availability.

`effective_ceiling_governance_path`: binding path; project wins ties; null: omitted default binds. `effective_ceiling_sources`: ties. Check slots/ownership/spawn.

Bound cycles use `produce_planning_resource_observation`; reauthenticate evidence/capacities. Capacity: 0–1000; zero selects none (`safe_lane_count == 0`).

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

`OPERATING_CONFIG.yaml`: choices. Defaults: twelve `gpt-6.1-sol`/high workers; shared critic, controller, specialists `gpt-6.1-sol`/high; `gpt-6-luna`/high simple work. Preserve routes; invalid choices block CONFIGURED.

`independent_reviewers.allocation: shared_critic` uses slot. Legacy `one_per_stream` and fixed counts bind. `--propose-operating-capacity` proposes ceiling change.

Owner overrides require authenticated `fixed`/`max` decisions and audit: `fixed` selects count or refuses unsafe; `max` limits recommendations. Neither raises sealed safe capacity. Replans await round boundaries; preserve reservations/triggers. Durable plans constrain admission.

Authenticated readers trim/casefold and collapse whitespace/ASCII-hyphen runs: `To Do`/`To-Do` → `ready`, `In Progress`/`In-Progress` → `in_progress`, `Done` → `closed`. Vocabulary: ready `open`/`ready`/`backlog`; active `in_flight`/`working`/`dispatched`/`in_progress`; closed `closed`/`done`/`merged`/`cancelled`/`canceled`. Unknown names grant neither admission nor closed dependencies; reviewed mappings required. Graph callers supply canonical vocabulary.

Translate, echo, execute:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show; retain pins/floors.

Refusals exit 2: path/value/remedy; never clamp/partially apply.

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

Epic: echo ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Matching `epic_id` selects scoped routes within allowlists/floors. Preserve Epic identity. `--epic` supports no scoped counts/simple switches/recommendations. Unpinning requires a route.

`--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; repairs atomic.

Changes affect dispatch; running work retains route/hash. Surplus streams finish without packets or cancellation. A–F proves no availability.

Read Jira; save IDs/paths in `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory: `data/`, `data/**`, `data/*` are equivalent prefixes; other globs refuse by Epic/value. Unknown/overlapping scopes group work; refusals name Epic/value. Risk/specialist triggers raise routes; no Epics: keep/custom.

Table; await adoption/keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute stale hashes.

Preserve pins/floors/replay checks.

Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`. Bootstrap creates/appends/replaces AWF `.gitignore` block, preserving outside bytes; `MIGRATION-v1.9.4-to-v1.9.5.md` defines placement, deduplication, malformed boundaries and `added_lines`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/audit without replacement. Preserve conflicts/interrupted staging within bounds. Recovery requires independently inspected journal SHA-256; budget recovery also requires
the matching consumed confirmation in protected storage. Unconfirmed journals
refuse without writes; confirmed crash recovery completes once.

Dispatch/reservation/settlement retain `operating_hash`/`policy_hash`; settlement uses reservation hashes. Absent/null history remains unobserved. Host authenticates routes.


Generated `OPERATING_CONFIG.yaml` includes `budgeting`:
accounting/enforcement `false`; dormant ticket limit `4000000`; lifetime-project
limit `1000000000`. Older missing fields retain legacy budgeting. Limits enable
neither switch; accounting OFF disables enforcement. Epic overrides cannot alter
budget settings.

Paths: `budgeting.accounting_enabled`,
`budgeting.enforcement_enabled`, `budgeting.max_tokens_per_ticket` and
`budgeting.max_tokens_per_project`. `set` validates/stores a protected proposal,
prints changes/digest, exits 3 `PENDING_OWNER_CONFIRMATION`; settings stay
unchanged. Owner runs `workflow.py operating confirm --digest D` in their
terminal within 15 minutes, typing its first eight characters. Skills never run
confirm; afterwards verify persistence with `show`.
Stored GitHub User login requires its numeric ID in reviewed `merge_gate.trusted_owner_ids`; empty refuses
`OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`. Token
environment variables, foreign GH_HOST and noninteractive stdin/stdout refuse.
Windows requires console handles; NUL, pipes and files refuse. Resolve `gh` once
to an absolute binary path; scripts and worker-writable locations refuse. Audit
retains that path and file SHA-256.
Project/repository/governance/prior-settings/exact-change mismatches and replay
refuse. Under lock: validate, consume, journal, audit, readback.
Confirmation proves stored GitHub login and terminal access; a process with the
owner's full rights can replace software on the machine and, with a terminal,
bypass it.
See [budgeting](39-BUDGETING.md) for integration and preservation.
