# Operating configuration

Run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show`; paste in adoption PR; offer **keep defaults**, **review Epics and recommend**, or **custom**. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings/allowlists/floors/routes; see [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` share `effective_ceiling`: minimum of `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, enabled `execution.host_broker.max_workers`.

`effective_ceiling_governance_path`: binding path; project wins ties; null: omitted default binds. `effective_ceiling_sources`: ties.

Bound cycles reauthenticate `produce_planning_resource_observation`; capacity 0–1000; zero: `safe_lane_count == 0`.

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

`independent_reviewers.allocation: shared_critic` uses one slot; legacy counts bind. `--propose-operating-capacity` proposes ceilings.

Audited authenticated `fixed` selects safe counts; `max` limits recommendations. Neither raises sealed safe capacity. Replan at round boundaries; preserve reservations/triggers.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`. Matching `epic_id` selects allowed scoped routes. `--epic` excludes counts/simple switches/recommendations. Unpinning requires a route.

Changes affect dispatch; running work retains route/hash. Surplus streams finish without packets or cancellation. A–F proves no availability.

Read Jira; save IDs/paths in `operating-epics.schema.json`:

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Inventory: `data/`, `data/**`, `data/*` match prefixes; other globs refuse. Unknown/overlapping scopes group work; risk/specialists raise routes.

Await adoption/keep; apply with `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute stale hashes.



Commit `.agentic-state/operating/changes/` with `OPERATING_CONFIG.yaml`. Bootstrap preserves outside `.gitignore` bytes; `MIGRATION-v1.9.4-to-v1.9.5.md` defines placement, deduplication, malformed boundaries and `added_lines`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/audit without replacement; preserve conflicts. Inspect/pin journal SHA-256.
Budgets require latest consumed confirmation's registered transaction. Unconfirmed/superseded journals
refuse without writes. Register before publication: published transactions recover once;
otherwise `set`/`confirm` afresh. Preserve evidence.


Generated `OPERATING_CONFIG.yaml`: accounting/enforcement OFF; dormant limits
4000000/ticket, 1000000000/project lifetime. Missing fields retain legacy policy;
budget adoption stays manual. Accounting OFF disables enforcement; limits enable
neither switch. No Epic budget overrides.

Paths: `budgeting.accounting_enabled`, `budgeting.enforcement_enabled`,
`budgeting.max_tokens_per_ticket`, `budgeting.max_tokens_per_project`.
`set` validates/stores a protected proposal, prints changes/digest, exits 3
`PENDING_OWNER_CONFIRMATION`; settings stay unchanged. Initialize;
protected `--initialize` refuses. Owner runs `workflow.py operating confirm
--digest D` within 15 minutes in their terminal; type eight digest characters.
Skills never confirm; verify with `show`.

Stored GitHub User login requires its numeric ID in `merge_gate.trusted_owner_ids`;
empty refuses `OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`.
Token variables, foreign GH_HOST, noninteractive streams refuse. Windows requires
console handles; NUL/pipes/files refuse. Resolve `gh` once; scripts and effective
worker modification/replacement rights refuse with their reason. Audit retains
path/SHA-256. Repository/governance/prior-settings/exact-change mismatches
and replay refuse. Consume/journal/audit/readback under lock.

Confirmations use protected OS-account storage: Windows token SID/HKLM ProfileList
ProfileImagePath plus AppData/Local; POSIX password-database home.
AWF_STATE_HOME, LOCALAPPDATA, USERPROFILE and HOME cannot select it.
Binding hashes the resolved Git common directory from `git rev-parse --git-common-dir`;
project ID only cross-checks. Editable repository/project IDs cannot hide confirmations.
Worktrees share confirmations; moved/fresh clones and copies confirm afresh.
Ignore Git environment overrides; gitfiles require registered metadata readonly to the caller.
Deny worker Git-metadata writes/replacement; binding protects clone identity,
not against processes able to replace that boundary. Without Git, protected changes refuse.

With no resolvable store/confirmation, fresh OFF/default dormant limits and absent
legacy blocks load; nondefaults refuse budget admission. Protected changes
refuse with storage/Git reasons. OFF needs no usage ledger.
Older checkouts/mismatches remain readable: show/validation report settings differing
from this clone's latest confirmation; unrelated changes work, budget work refuses.
Proposals report pending. Budgeting choices/confirmation repair admission
without deleting audit. The screen/digest bind every difference from confirmed values,
including external edits; display complete settings for no-change reconfirmation.
Audit/journals prove nothing; transactions bind governance/prior settings.

`trusted_owner_ids` comes from the working tree: a process that can edit it still
needs a terminal and that login. A process with the owner's full rights can still
get around this. See [budgeting](39-BUDGETING.md).
