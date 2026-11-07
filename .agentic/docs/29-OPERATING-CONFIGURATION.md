# Operating configuration

In AWF Codex chat, the skill runs `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show` read-only and offers **keep defaults**, **review Epics and recommend**, or **custom**. No owner terminal command is required. CONFIGURED permits authorized work, is not ACTIVE, and enables no adapter.

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
Unprotected recovery retains the inspected journal pin and append-only audit.
Protected changes use Git history and a PR, never local recovery authority.

Generated `OPERATING_CONFIG.yaml`: accounting/enforcement OFF; dormant limits
4000000/ticket, 1000000000/project lifetime. Missing fields retain legacy policy;
budget adoption stays manual. Accounting OFF disables enforcement; limits enable
neither switch. No Epic budget overrides.

Ask in AWF Codex chat to check settings. The skill presents both switches,
both limits, source (fresh defaults,
default branch at a commit, or legacy retained), and working-file differences.
Last PR/merger is optional information; unavailable observations never fail.

Ask for a change in chat. The skill branches from the configured default branch,
changes only requested `budgeting.*` values preserving file text/comments, validates
the proposed file with `operating validate --file PROPOSED` and presents exact before/after in a PR. Invalid values refuse;
enforcement requires accounting. The owner reviews and merges on GitHub; agents
never merge. Restricted agent pushes prepare the branch/body for owner publication.
No terminal command is required of the owner. Protected `set` refuses
without writing the live file and explains the PR route; unprotected set is unchanged.

Effective protected settings come from `OPERATING_CONFIG.yaml` at
`refs/remotes/origin/<github.base_branch>`, as locally known. Fetch after merge;
the next controller load reads that commit, including older worktrees. Branch or
working-file edits cannot enable or disable enforcement. `show` and
`validate-config` report differences with the next step: open a PR.
Unreadable/malformed default-branch bytes refuse budget work, never silently OFF.
Fresh defaults without remote metadata and absent legacy blocks load without
Git commands, gh or network; nondefault settings without a fetched ref refuse.
Existing remote metadata must be inspected even when working bytes say OFF.
OFF requires no usage ledger, telemetry or reservations.

Merge authority rests with the host's repository rules and the owner.
`show` reports rules APPLIED, MISSING or UNOBSERVED as warnings, never a blocker.
Git metadata must be outside worker write authority. A process able to push
directly to an unprotected default branch, or one with the owner's full rights,
can get around this: offer the shipped ruleset separately.
`merge_gate.trusted_owner_ids` remains for merge-gate use.
See [budgeting](39-BUDGETING.md).
