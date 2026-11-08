# Operating configuration

In AWF Codex chat, run `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating show` read-only and offers **keep defaults**, **review Epics and recommend**, or **custom**. No owner terminal command. CONFIGURED permits authorized work, not ACTIVE/adapter enablement.

Protected `.agentic/PROJECT_CONFIG.yaml`: ceilings/budgets/allowlists/floors/escalation/shared critic; legacy routes accepted; see [controller configuration](34-CONTINUOUS-CONTROLLER.md#controller-automation-switches).

Show/status/`operating.state` `effective_ceiling`: minimum: `execution.max_lanes` (omitted: six), `execution.max_parallel_tickets`, enabled `execution.host_broker.max_workers`.

`effective_ceiling_governance_path`: binding path; project wins ties; null: omitted default binds. `effective_ceiling_sources`: ties.

Cycles reauthenticate `produce_planning_resource_observation`; capacity 0-1000; zero: `safe_lane_count == 0`.

Schemas: `streams.count` 1–12 with A–L routes; optional project `execution.max_lanes` 1–12. Omitting `execution.max_lanes` retains six. Lower binding limits, including `execution.max_parallel_tickets` and enabled `execution.host_broker.max_workers`, still apply. Migration: raise protected ceilings only through review. Schema acceptance grants no dispatch authority.

| Contract | Fields | Range |
| --- | --- | --- |
| `operating-config` | `streams.count` | 1–12 |
| `project-config` | `execution.max_parallel_tickets`, `execution.max_parallel_tickets_per_stream`, `execution.host_broker.max_workers`, each `execution.host_broker.resources` value | 1-1000 |
| `project-config` | `execution.host_broker.max_heavy_jobs`, `execution.host_broker.max_gpu_jobs`, explicit `execution.independent_reviewers.count` | 0-1000 |
| `ticket-plan` | `lane_cap` | 1-1000 |
| `host-lease` | `worker_slots` | 1-1000 |
| `host-lease` | `heavy_slots`, `gpu_slots` | 0-1000 |
| `host-lease`, `run-attestation` | `resources_held[].slots` | 1-1000 |

Defaults: twelve `gpt-6.1-sol`/high workers, shared critic/controller/specialists; `gpt-6-luna`/high simple work. Preserve routes; invalid blocks CONFIGURED.

`independent_reviewers.allocation: shared_critic`: one slot; legacy counts bind. `--propose-operating-capacity` proposes ceilings.

Authenticated, audited `fixed` refuses above sealed safe capacity; `max` limits recommendations. Neither raises capacity; durable plans constrain admission; replan at round boundaries; preserve reservations/triggers.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

Above-cap upgrades refuse; use `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Use three streams" --set streams.count=3` to repair four under cap three; surplus drains.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "Stream B's worker on Sol/high for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-5.6-sol/high
```

Pins `epic_overrides.PROJ-123.streams.B.worker`; `epic_id` selects scoped allowlisted routes. `--epic` excludes counts/simple switches/recommendations; unpinning requires routes.

Dispatch changes; running work retains route/hash. Surplus finishes without packets/cancellation. Labels prove no availability.

Read Jira into `operating-epics.schema.json`; unknown statuses need reviewed mappings.
Operating/governance hashes bind separately; preserve running reservations.

```text
& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating recommend --epics EPICS.json --inventory INVENTORY.json
```

`data/`, `data/**`, `data/*` match prefixes; other globs refuse. Unknown/overlapping scopes group work; risk/specialists raise routes.

Await adoption/keep; apply: `& '.agentic\.venv\Scripts\python.exe' -B -I '.agentic\scripts\workflow.py' --root '.' operating set --instruction "adopt all" --recommendation ID --accept all`. Recompute hashes.



Commit `.agentic-state/operating/changes/` with settings. Bootstrap preserves outside `.gitignore` bytes; `MIGRATION-v1.9.4-to-v1.9.5.md` defines placement, deduplication, malformed boundaries and `added_lines`. Ignore locks, `pending.json`, `*.awf-operating-*.tmp`, `.awf-*.tmp`; inspect recovery artifacts; incomplete transactions block dispatch.

Publish flushed journal/append-only audit without replacement; preserve conflicts and inspected journal SHA-256.
Protected changes use Git history and a PR, never local recovery authority.

Generated `OPERATING_CONFIG.yaml`: accounting/enforcement OFF; dormant limits
4000000/ticket, 1000000000/project lifetime. Absent fields retain legacy policy; manual budget adoption. Accounting OFF disables enforcement; limits enable
neither switch. No Epic budget overrides.

Ask in AWF Codex chat to check switches, limits, source/commit and differences.
Last PR/merger optional. Prepare a validated PR
preserving comments and showing before/after. The owner merges; restricted pushes
prepare owner publication. Protected `set` refuses without writes. See the
[chat/PR procedure](39-BUDGETING.md#settings-in-chat-and-prs).

Effective protected settings come from `OPERATING_CONFIG.yaml` at the commit
named by `refs/remotes/origin/HEAD` only. `github.base_branch` cannot select policy;
`show` reports disagreement. Branch/working edits cannot change enforcement, calendar batching or reporting, including OFF and destination bindings.
Differences mean update an older checkout, otherwise propose a PR. At cycle start
the controller locks before discovery, fetch, commit checking and publication.
Git-valid UTF-8 names and hierarchical renames are supported. Failure retains
policy or unverified refusal, with a step-specific remedy. FETCH_HEAD time is
a fetch attempt, never proof of successful policy refresh.
Absent policy there means fresh OFF or legacy retained; malformed bytes refuse.
Configured origin without verified origin/HEAD: show/validation/installation
work; admission refreshes, then refuses dangling identity, tracking refs or
nondefault working protected values. The remedy is the next
controller cycle or `git remote set-head origin --auto`. Fresh OFF without refs
can continue offline. Non-repositories invoke no Git/gh. OFF needs no ledger.

Host rules and owner retain merge authority.
`show` reports rules UNOBSERVED as a warning. Adoption/publication observations
report APPLIED, MISSING or UNOBSERVED; warnings never block local adoption.
Keep Git metadata outside worker write authority. Unprotected default-branch pushes/owner rights
bypass governance: offer the shipped ruleset separately.
`merge_gate.trusted_owner_ids` remains for merge-gate use.
Calendar shares this PR route and effective source. Defaults: continuous 24/7 work;
review batching enabled weekdays 07:00-17:00 Europe/London, EoD 17:00 daily.
Absent calendar retains these defaults; malformed accepted values refuse.
See [calendar](operating-calendar.md) and [budgeting](39-BUDGETING.md).
Reporting shares this PR route: default OFF; off/teams/slack/both with complete
project/repository/scope/platform/destination/revision/secret_ref bindings.
`show` withholds unsafe values; validate proposals offline, reachability UNOBSERVED.
Unavailable policy refuses; accepted absence means defaults. Preserve corrections
and the two-send ceiling across settings changes.
