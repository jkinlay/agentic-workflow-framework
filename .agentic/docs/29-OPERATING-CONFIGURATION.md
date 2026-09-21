# Operating configuration by conversation

At CONFIGURED, run `python -B .agentic/scripts/workflow.py operating show`, paste its table in the adoption PR, and offer **keep defaults**, **review Epics and recommend**, or **custom**. Keeping defaults permits authorized work. CONFIGURED does not mean ACTIVE or enable an adapter.

## Two configuration layers

Protected `.agentic/PROJECT_CONFIG.yaml` holds ceilings, budgets, model allowlists, floors, escalation and one reviewer per stream. Governance changes require a reviewed PR.

Show JSON/text, status and newly prepared `operating.state` share `effective_ceiling`: the minimum of six, `execution.max_parallel_tickets`, and enabled `execution.host_broker.max_workers`. `host_broker.*` limits bind only when `host_broker.enabled` is true. Disabled values cannot lower the ceiling; heavy/GPU limits are not stream counts. Configured capacity never proves observed host slots or reviewer availability.

`effective_ceiling_governance_path` names the binding project/broker path, preferring project on ties. `effective_ceiling_sources` lists every tied path, including the schema's six-stream maximum. When only that structural limit binds, the governance path is null. Native planning separately considers observed host slots, ownership and spawn depth. Malformed inputs are refused.

Project-owned `OPERATING_CONFIG.yaml` holds choices within governance. Bootstrap seeds three streams: Terra/medium workers, Sol/high reviewers, Sol/medium controller, Astra/high specialist and Luna/low simple work. Existing files are preserved; this file is mutable and unprotected. Missing/invalid choices prevent CONFIGURED; preserve evidence and follow the remedy.

Reviewer count derives from streams; legacy fixed counts still bind. Bootstrap's `--propose-operating-capacity` stages a ceiling of at least six and removes fixed count for the adoption PR. Merge before use; ordinary upgrades preserve both. Follow [adoption](20-NEW-PROJECT-SETUP.md) mode/conflict arguments.

## Apply the user's choices

Translate direct chat instructions into settings, echo the translation, then execute without another routine permission prompt:

```text
python -B .agentic/scripts/workflow.py operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Show the result. User-chosen routes are pinned against optional escalation; mandatory risk/review floors prevail. Unpinned defaults permit enabled simple routing for qualifying low-risk, strongly verified work.

Refusals exit 2, naming the path, binding governance value and remedy. Never clamp or partially apply mixed valid/invalid changes.

An existing count above an enabled broker cap is rejected after upgrade. Repair an audited count of four under cap three with `operating set --instruction "Use three streams" --set streams.count=3`; governance stays unchanged and surplus streams drain.

For “Stream B's worker on Astra/xhigh for this Epic”, bind the exact observed Epic ID, echo it, then run:

```text
python -B .agentic/scripts/workflow.py operating set --instruction "Stream B's worker on Astra/xhigh for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-6-astra/xhigh
```

This pins `epic_overrides.PROJ-123.streams.B.worker` without changing global routes. Scoped worker/reviewer/controller/specialist routes obey the same allowlists/floors. Show lists them; only matching observed `epic_id` selects them, after global routes and before agent/ticket overrides. Never infer/broaden Epic identity. Scoped counts, simple switches and project recommendations with `--epic` are unsupported. Unpinning needs an existing complete route.

Fully qualified `--set epic_overrides.PROJ-123.streams.B.worker=...` works without `--epic`; combine global/scoped paths for atomic repair after governance changes.

Changes affect subsequent dispatch. Running work retains its reserved route/hash; surplus streams finish current tickets without new packets or cancellation. A–F does not prove six available agents.

## Recommend from observed Epics

Read authorized Jira scope without transitions. Save real IDs/observed paths using `operating-epics.schema.json`:

```text
python -B .agentic/scripts/workflow.py operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory is optional. `data/`, `data/**` and `data/*` are equivalent prefixes. Other globs refuse by Epic/value. Unknown/overlapping scopes conservatively group work; unknown reasons name Epic/value. Risk/specialist triggers raise routes. No Epics offers keep/custom.

Present the table; wait for adoption or keep. Apply with `operating set --instruction "adopt all" --recommendation ID --accept all`. Stale input/operating/governance hashes require a fresh recommendation.

Adopted defaults stay unpinned. Unchanged pins appear in one footnote so actual changes remain visible; stored rows, named-row adoption and replay checks stay intact. Adoption preserves pins and mandatory floors. Custom instructions change pins; non-default recommendations display their pin state.

## Audit and recovery

Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`: audits retain instructions, hashes, changes and source. Existing `.gitignore` rules survive; operating locks, `pending.json`, `*.awf-operating-*.tmp` and `.awf-*.tmp` are ignored. Inspect recovery artifacts; incomplete transactions block dispatch.

Journal/audit publication requires complete flushed writes without replacement. Preserve conflicts and interrupted staging files outside the audit chain within inventory bounds. Recovery requires an independently inspected journal SHA-256. Unsupported atomic primitives fail closed.

Dispatch/reservation/settlement retain separate `operating_hash` and `policy_hash`; settlement uses its reservation's hash. Historical absent/null hashes remain unobserved. The host authenticates instructions and dispatches observed routes; records establish local consistency.
