# Native coordination runbook

Version 1.9.1. This is coordinator guidance for a host with delegation tools. The planner does not launch agents or authenticate active-writer records.

See [specification](../SPECIFICATION.md), [ticket lifecycle](23-TICKET-LIFECYCLE.md) and [operating configuration](29-OPERATING-CONFIGURATION.md). Default three streams each have one independent reviewer; their count is derived. Reviewers and any separate coordinator consume shared host slots; configured ceilings do not prove available agents.

## Plan and dispatch

Inspect assigned scope, inventory, owners and conflicting paths. Before Jira is available, use provisional local IDs. The planner supports A–F, up to six implementation streams. The shared `effective_ceiling` is the minimum of six, protected `execution.max_parallel_tickets`, and `execution.host_broker.max_workers` only when `host_broker.enabled` is true. Disabled broker limits do not bind. `effective_ceiling_governance_path` and `effective_ceiling_sources` identify binding configured limits; observed host slots remain separate. Root `OPERATING_CONFIG.yaml` selects the active count, initially three. Invalid operating counts are refused, not clamped. Do not create more streams than independent work warrants.

Read `execution.native_streams.enabled`, `dispatch_policy`, `max_parallel_tickets`, `max_parallel_tickets_per_stream` and `max_spawn_depth` from the reviewed config, together with observed host capacity. Do not infer omitted settings from a partial example.

Use `.agentic/scripts/plan_streams.py` over verified inventory for project-owned `STREAMS.md`/`STREAMS.json`. Inspect dependency and conflict boundaries. Synthetic fixtures remain demonstrations.

Through supported host tools, assign bounded non-overlapping work within those limits. An implementing coordinator is a writer; reviewers and queued proposals are not. Record returned agent IDs and assignments, reuse existing owners and account for other occupied slots.

Follow [routing](27-MODEL-ROUTING.md): reserve, launch through the host, settle observed results. Missing launch, usage or enforcement capability is a limitation, not inferred success.

Direct chat instructions may change the operating count within its ceiling through `operating set`, without a governance PR. Echo/apply/show; recommendations require acceptance. Reducing count drains surplus streams' existing tickets without new dispatches. Preserve active/paused owners. Changes to protected caps still require governance review; never silently raise them. Missing repository rules do not block local [adoption](20-NEW-PROJECT-SETUP.md).

## Reconcile

On COMPLETE, reconcile ownership and candidate identity, then have the assigned publisher push the scoped feature branch/open a draft PR against `github.base_branch`. Observe `branch_pushed` and `pr_exists`. Current validation/requirements permit mark-ready and observed `draft_cleared`; only then dispatch the critic against the observed PR head. A worktree-only review is not READY_FOR_CRITIC. Owner-ready requires critic, applicable specialists and final gate; it still needs human merge authorization. Amend the same PR, register the observed new head and re-review.

Routine publication classification requires accepted repository/default/numeric-ID/ref bindings and fresh matching live APPLIED rules evidence. It grants no execution authority and bypasses no platform, scope, protected-ref, secret or adapter prerequisites. Reuse authorization already granted; report any remaining concrete action/owner. BLOCKED/FAILED never becomes an invented PR or completion.

Jira mirrors the lifecycle through the controller only (WORKER_STARTED → In Progress, PR_READY → In Review, owner changes → In Progress, observed merge → Done); stream planning itself writes nothing, and Epics are scope/grouping, never transition targets. Read before/after each write. Mismatch/unknown outcome stops that ticket's Jira writes, retains observed actor/time or unknown, and reports a suspected external automation conflict without retrying. Continue unaffected streams; disabled Jira permits no writes.

New reviewer identities default to selected `--codeowner` (`@maintainer` unless overridden); existing identities remain. Verify actual access, independence and eligible non-author review. Configured handles and CODEOWNERS files do not enforce these qualities.

Use supported completion events or bounded polling. Scheduling later work requires a user request and a scheduler. The separate enrolled PR loop allows one active tick and must not race a native writer on the same branch.

Native status is supplied observation, not cryptographic attestation. Deployment-grade unforgeable writer records require a separate trusted adapter.
