# Operating configuration by conversation

At CONFIGURED, run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`, paste its table in the adoption PR, and offer **keep defaults**, **review Epics and recommend**, or **custom**. Keeping defaults permits authorized work. CONFIGURED does not mean ACTIVE or enable an adapter.

## Two configuration layers

Protected `.agentic/PROJECT_CONFIG.yaml` holds ceilings, budgets, model allowlists, floors, escalation and one shared critic route; legacy per-stream reviewer routes remain accepted. Governance changes require a reviewed PR.

`effective_ceiling` is the minimum of positive-integer `execution.max_lanes`, `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. Omitted `max_lanes` preserves the six-lane default; malformed values fail closed. Disabled broker values and heavy/GPU limits do not bind. Configured capacity never proves host slots or reviewer availability.

`effective_ceiling_governance_path` names the binding project/broker path, preferring project on ties; `effective_ceiling_sources` lists ties. An omitted ceiling cites the `operating-config.schema.json` governed-maximum annotation and has no governance path unless another limit ties it. Native planning also considers observed resources and ownership.

Generated schemas use a dynamic count placeholder and finite route set. The
shipped set is A–F; `scripts/generate_contracts.py --max-lanes N` derives another
set without fixing the schema bound at six. `x-awf-governed-maximum` identifies
the governing key and default. Runtime validation projects accepted
`execution.max_lanes` into an in-memory schema, rejecting missing active routes,
unknown labels and above-ceiling counts. Change it by governance PR; omission
needs no migration. Reductions prune inactive routes through an audited change.

Project-owned `OPERATING_CONFIG.yaml` holds governed choices. Defaults are three
`gpt-5.6-luna` / medium workers, one shared `gpt-5.6-sol` / high critic,
`gpt-5.6-sol` / high controller and specialist, and `gpt-5.6-luna` / low simple
work (five slots including controller and critic). Existing files and legacy
per-stream reviewers are preserved. Missing/invalid choices prevent CONFIGURED.

New governance uses one-slot `independent_reviewers.allocation: shared_critic`; legacy fixed/per-stream allocations remain binding. Bootstrap preserves them; `--propose-operating-capacity` only proposes a ceiling change. Follow [adoption](20-NEW-PROJECT-SETUP.md).

## Apply the user's choices

Translate direct chat instructions into settings, echo the translation, then execute without another routine permission prompt:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show the result. User-chosen routes are pinned against optional escalation; mandatory risk/review floors prevail. Unpinned defaults permit enabled simple routing for qualifying low-risk, strongly verified work.

Refusals exit 2, naming the path, binding governance value and remedy. Never clamp or partially apply mixed valid/invalid changes.

An existing count above an enabled broker cap is rejected after upgrade. Repair an audited count of four under cap three with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3`; governance stays unchanged and surplus streams drain.

For “Stream B's worker on Sol/high for this Epic”, bind the exact observed Epic ID, echo it, then run:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

This pins `epic_overrides.PROJ-123.streams.B.worker` without changing global routes. Scoped worker/reviewer/controller/specialist routes obey the same allowlists/floors. Show lists them; only matching observed `epic_id` selects them, after global routes and before agent/ticket overrides. Never infer/broaden Epic identity. Scoped counts, simple switches and project recommendations with `--epic` are unsupported. Unpinning needs an existing complete route.

Fully qualified `--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; combine global/scoped paths for atomic repair after governance changes.

Changes affect subsequent dispatch. Running work retains its reserved route/hash; surplus streams finish current tickets without new packets or cancellation. A–F does not prove six available agents.

## Recommend from observed Epics

Read authorized Jira scope without transitions. Save real IDs/observed paths using `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory is optional. `data/`, `data/**` and `data/*` are equivalent prefixes. Other globs refuse by Epic/value. Unknown/overlapping scopes conservatively group work; unknown reasons name Epic/value. Risk/specialist triggers raise routes. No Epics offers keep/custom.

Present the table; wait for adoption or keep. Apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Stale input/operating/governance hashes require a fresh recommendation.

Adopted defaults stay unpinned; adoption preserves existing pins, mandatory floors, stored rows and replay checks. Custom instructions change pins.

## Audit and recovery

Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`: audits retain instructions, hashes, changes and source. Existing `.gitignore` rules survive; operating locks, `pending.json`, `*.awf-operating-*.tmp` and `.awf-*.tmp` are ignored. Inspect recovery artifacts; incomplete transactions block dispatch.

Journal/audit publication requires complete flushed writes without replacement. Preserve conflicts and interrupted staging files outside the audit chain within inventory bounds. Recovery requires an independently inspected journal SHA-256. Unsupported atomic primitives fail closed.

Dispatch/reservation/settlement retain separate `operating_hash` and `policy_hash`; settlement uses its reservation's hash. Historical absent/null hashes remain unobserved. The host authenticates instructions and dispatches observed routes; records establish local consistency.
