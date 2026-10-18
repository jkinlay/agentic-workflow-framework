# Operating configuration by conversation

At CONFIGURED, run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste the table in the adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. Defaults permit authorized work; CONFIGURED is not ACTIVE and enables no adapter.

## Two configuration layers

Protected `.agentic/PROJECT_CONFIG.yaml` holds ceilings, budgets, model allowlists, floors, escalation and one shared critic route; legacy per-stream reviewer routes remain accepted. Governance changes require review; [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show JSON/text, status and new `operating.state` share `effective_ceiling`: minimum of six, `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. Disabled brokers do not bind; heavy/GPU limits are not stream counts. Configuration cannot prove host/reviewer availability.

`effective_ceiling_governance_path` names the binding project/broker path, preferring project on ties; null means only the six-stream maximum binds. `effective_ceiling_sources` lists all ties. Planning also checks observed slots, ownership and spawn depth; malformed inputs refuse.

`produce_planning_resource_observation` accepts only trusted host callback
capacities for a bound cycle. Planning independently reauthenticates closed
evidence and matching capacities; missing authentication, self-digests and
operator values fail closed. Observed capacity is 0–1000; zero selects no lane
(`safe_lane_count == 0`).

Generated schemas enforce inclusive integer ranges below. The 1000-slot safety
maximum matches observed capacities, accommodates shared inventories and rejects
exhaustion-level declarations, including disabled brokers. Lower governance and
observed limits still bind; schema acceptance grants no dispatch authority.

| Contract | Fields | Range |
| --- | --- | --- |
| `operating-config` | `streams.count` | 1–6 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1–1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0–1000 |
| `ticket-plan` | `lane_cap` | 1–1000 |
| `host-lease` | `worker_slots` | 1–1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0–1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1–1000 |

`OPERATING_CONFIG.yaml` holds unprotected project choices. AWF 1.9.4 defaults to three `gpt-5.6-luna` / medium workers, one shared `gpt-5.6-sol` / high critic/adversarial handler, `gpt-5.6-sol` / high controller and specialist routes, and `gpt-5.6-luna` / low simple work. Existing bytes and legacy per-stream routes are preserved. Missing/invalid choices prevent CONFIGURED; follow the remedy.

New governance uses `independent_reviewers.allocation: shared_critic` with one reviewer slot. Legacy `one_per_stream` allocation and fixed counts remain binding and are preserved. `--propose-operating-capacity` only proposes a ceiling change. Follow [adoption](20-NEW-PROJECT-SETUP.md) mode/conflict arguments.

## Apply the user's choices

Translate, echo and execute direct instructions without reconfirmation:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show results. User routes pin optional escalation; mandatory risk/review floors prevail. Unpinned defaults permit enabled simple routing for low-risk, strongly verified work.

Refusals exit 2, naming the path, binding governance value and remedy. Never clamp or partially apply mixed valid/invalid changes.

Above-cap upgrades refuse; use `operating set --instruction "Use three streams" --set streams.count=3` through the interpreter/CLI above to repair four streams under broker cap three; surplus streams drain.

For Epic-scoped instructions, bind and echo the observed Epic ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

This pins `epic_overrides.PROJ-123.streams.B.worker`. Matching observed `epic_id` selects scoped routes after global, before agent/ticket overrides, within allowlists/floors. Never broaden Epic identity. Scoped counts, simple switches and recommendations with `--epic` are unsupported. Unpinning requires a complete route.

Fully qualified `--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; combine global/scoped paths for atomic repair after governance changes.

Changes affect subsequent dispatch. Running work retains route/hash; surplus streams finish without new packets or cancellation. A–F proves no availability.

## Recommend from observed Epics

Read authorized Jira scope without transitions. Save real IDs/observed paths using `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory is optional. `data/`, `data/**` and `data/*` are equivalent prefixes. Other globs refuse by Epic/value. Unknown/overlapping scopes conservatively group work; unknown reasons name Epic/value. Risk/specialist triggers raise routes. No Epics offers keep/custom.

Present the table; wait for adoption or keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Stale input/operating/governance hashes require a fresh recommendation.

Adopted defaults stay unpinned; unchanged pins share a footnote. Stored rows, named-row adoption and replay checks remain. Adoption preserves pins/floors; custom instructions change pins; non-default recommendations show pin state.

## Audit and recovery

Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`: audits retain instructions, hashes, changes and source. Existing `.gitignore` rules survive; operating locks, `pending.json`, `*.awf-operating-*.tmp` and `.awf-*.tmp` are ignored. Inspect recovery artifacts; incomplete transactions block dispatch.

Journal/audit publication requires complete flushed writes without replacement. Preserve conflicts and interrupted staging files outside the audit chain within inventory bounds. Recovery requires an independently inspected journal SHA-256. Unsupported atomic primitives fail closed.

Dispatch/reservation/settlement retain separate `operating_hash` and `policy_hash`; settlement uses its reservation's hash. Historical absent/null hashes remain unobserved. The host authenticates instructions and dispatches observed routes; records establish local consistency.
