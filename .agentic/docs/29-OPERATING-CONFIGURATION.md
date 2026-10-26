# Operating configuration by conversation

At CONFIGURED, run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste the table in the adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

## Two configuration layers

Protected `.agentic/PROJECT_CONFIG.yaml` holds ceilings, budgets, model allowlists, floors, escalation and one shared critic route; legacy per-stream reviewer routes remain accepted. Changes require review; [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` share `effective_ceiling`: runtime minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. Disabled brokers do not bind; heavy/GPU limits are not stream counts. Configuration cannot prove host/reviewer availability.

`effective_ceiling_governance_path` names the binding governance path, preferring project on ties; null means only the omitted six-lane default binds. `effective_ceiling_sources` lists all ties. Planning checks observed slots, ownership and spawn depth.

`produce_planning_resource_observation` accepts only trusted host callback
capacities for a bound cycle. Planning independently reauthenticates closed
evidence and matching capacities; missing authentication, self-digests and
operator values fail closed. Observed capacity is 0–1000; zero selects no lane
(`safe_lane_count == 0`).

Pending generated-schema limitation: raw schemas cap `streams.count` at six,
provide A-F routes, and reject `execution.max_lanes` until AWF-50 item 1 lands.
Above-six configuration remains schema-invalid. Migration:
keep an omitted setting to retain six; raise protected ceilings only through review
after that schema update. Schema acceptance grants no dispatch authority.

| Contract | Fields | Range |
| --- | --- | --- |
| `operating-config` | `streams.count` | 1–6 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1–1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0–1000 |
| `ticket-plan` | `lane_cap` | 1–1000 |
| `host-lease` | `worker_slots` | 1–1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0–1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1–1000 |

`OPERATING_CONFIG.yaml` holds unprotected project choices. AWF 1.9.4 defaults to three `gpt-5.6-luna` / medium workers, one shared `gpt-5.6-sol` / high critic/adversarial handler, `gpt-5.6-sol` / high controller and specialist routes, and `gpt-5.6-luna` / low simple work. Preserve existing routes; invalid choices prevent CONFIGURED.

`independent_reviewers.allocation: shared_critic` uses one reviewer slot. Legacy `one_per_stream` and fixed counts remain binding. `--propose-operating-capacity` only proposes a ceiling change. Follow [adoption](20-NEW-PROJECT-SETUP.md) mode/conflict arguments.

Owner overrides use authenticated `fixed` or `max` decisions with
audited history. They apply after sealed safe capacity: `fixed` selects
that count or refuses if unsafe; `max` limits the recommendation. Neither raises
governance or observed capacity. Replans wait for round boundaries; running
assignments retain reservations. Authenticated triggers persist; durable plans
constrain admission with lane-start rechecks.

## Apply the user's choices

Translate, echo and execute instructions without reconfirmation:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show results. Routes pin optional escalation; mandatory floors prevail.

Refusals exit 2, naming the path, binding governance value and remedy. Never clamp or partially apply mixed valid/invalid changes.

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four streams under broker cap three; surplus streams drain.

For Epic instructions, echo the observed ID:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

This pins `epic_overrides.PROJ-123.streams.B.worker`. Matching observed `epic_id` selects scoped routes after global, before agent/ticket overrides, within allowlists/floors. Never broaden Epic identity. Scoped counts, simple switches and recommendations with `--epic` are unsupported. Unpinning requires a complete route.

`--set epic_overrides.PROJ-123.streams.B.worker=...` also works without `--epic`; global/scoped repairs are atomic.

Changes affect subsequent dispatch. Running work retains route/hash; surplus streams finish without new packets or cancellation. A–F proves no availability.

## Recommend from observed Epics

Read Jira without transitions; save observed IDs/paths using `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Optional inventory: `data/`, `data/**` and `data/*` are equivalent prefixes. Other globs refuse by Epic/value. Unknown/overlapping scopes group work; refusals name Epic/value. Risk/specialist triggers raise routes. No Epics offers keep/custom.

Present the table; wait for adoption or keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Stale hashes require recomputation.

Defaults stay unpinned; unchanged pins share a footnote. Stored/named rows and replay checks remain. Adoption preserves pins/floors; custom instructions change pins.

## Audit and recovery

Commit `OPERATING_CONFIG.yaml` and `.agentic-state/operating/changes/` audits (instructions, hashes, changes/source). Replace historical AWF `.gitignore` blocks in place; outside owner rules survive byte-for-byte. Ignore operating locks/`pending.json`/`*.awf-operating-*.tmp`/`.awf-*.tmp`. Inspect recovery artifacts; incomplete transactions block dispatch.

Publish complete flushed journal/audit writes without replacement. Preserve conflicts and interrupted staging files outside the audit chain within inventory bounds. Recovery requires an independently inspected journal SHA-256. Unsupported atomic primitives fail closed.

Dispatch/reservation/settlement retain separate `operating_hash` and `policy_hash`; settlement uses its reservation's hash. Historical absent/null hashes remain unobserved. The host authenticates instructions/routes; records establish local consistency.
