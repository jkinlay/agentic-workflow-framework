# Native coordination runbook

Version 1.9.4. Coordinator guidance for a delegation host. The planner neither launches agents nor authenticates active writers.

See [specification](../SPECIFICATION.md), [ticket lifecycle](23-TICKET-LIFECYCLE.md) and [operating configuration](29-OPERATING-CONFIGURATION.md). Three default workers share one independent critic/adversarial handler; with the controller, this uses five slots. The review barrier awaits every reviewer frozen for a candidate. Configured ceilings do not prove host availability.

## Plan and dispatch

Inspect scope, inventory, owners and path conflicts; use provisional local IDs before Jira. The planner supports streams A-F. `effective_ceiling` is the minimum of six, protected `execution.max_parallel_tickets`, and `execution.host_broker.max_workers` when its broker is enabled. Disabled broker limits do not bind. The governance-path/source fields identify configured limits; observed host slots remain separate. Root `OPERATING_CONFIG.yaml` selects the active count, initially three. Refuse invalid counts and excess streams.

Read `execution.native_streams.enabled`, `dispatch_policy`, both parallel-ticket limits and `max_spawn_depth` from reviewed config alongside observed host capacity. Never infer omitted settings from examples.

Run `.agentic/scripts/plan_streams.py` over verified inventory for project-owned `STREAMS.md`/`STREAMS.json`, then inspect dependency and conflict boundaries. Synthetic fixtures remain demonstrations.

Plans never commit host-local absolute paths: configuration provenance is repository-relative or `<external-project-configuration>`. Absolute worktrees require explicit `--redact-ownership-worktrees`. After verifying the original `--expected-input-sha256`, the planner substitutes deterministic SHA-256 placeholders and records affected tickets in `path_redaction`; the original pin remains valid and changed worktrees reject. Migrate a pinned legacy plan with that option and its current `--expected-plan-sha256`; compare-and-swap requires matching worktree, agent and stream identities. Otherwise preserve it unpublished. Never hand-edit plan files.

Through supported host tools, assign bounded, non-overlapping work. An implementing coordinator is a writer; reviewers and queued proposals are not. Record agent IDs and assignments, reuse owners and count occupied slots.

Before dispatch, require every stream capability through `agentic.activation.require_capabilities`; ACTIVE is insufficient, and any required `UNAVAILABLE`, `UNOBSERVED` or `NOT_APPLICABLE` value refuses the stream. Production also requires the [publication readiness observer](37-PUBLICATION-READINESS.md); missing authentication, remote access, branch/rule eligibility, push or draft-PR permission names a blocker. K14/L6 cover external resources and K13 Jira identity. Never infer unobserved rows.

Follow [routing](27-MODEL-ROUTING.md): reserve, launch through the host and settle observed results. Missing launch, usage or enforcement capability is a limitation, not success.

Direct chat instructions may change the operating count within its ceiling through `operating set`, without a governance PR. Echo/apply/show; recommendations require acceptance. Reductions drain surplus streams without new dispatches and preserve active/paused owners. Protected-cap changes still require governance review. Never silently raise them. Missing repository rules do not block local [adoption](20-NEW-PROJECT-SETUP.md).

## Reconcile

On COMPLETE, reconcile ownership and candidate identity. The assigned publisher pushes the scoped branch and opens a draft PR against `github.base_branch`; observe `branch_pushed` and `pr_exists`. Current validation/requirements permit mark-ready and observed `draft_cleared`; only then review the observed PR head. Worktree-only review is not READY_FOR_CRITIC. Owner-ready needs critic, applicable specialists and final gate, and still requires human merge authorization. Amend the same PR, register its new head and re-review.

Publication also requires the [history-aware scan](31-PUBLICATION-SAFETY.md) over the exact base/head and rendered PR body before first push or PR creation. Amendments rescan the complete range; scan provider comments before posting. A tip-only diff or `git diff --check` cannot establish publication safety.

Routine publication classification requires accepted repository/default/numeric-ID/ref bindings and fresh matching live APPLIED rules evidence. It grants no execution authority and bypasses no platform, scope, protected-ref, secret or adapter prerequisite. Reuse granted authorization; report the remaining action and owner. Never turn BLOCKED/FAILED into invented completion or a PR.

Only the controller mirrors Jira lifecycle: WORKER_STARTED to In Progress, PR_READY to In Review, owner changes to In Progress, and observed merge to Done. Planning writes nothing; never transition Epics. Read before and after each write. Mismatch or unknown outcome stops that ticket's Jira writes, retains observed actor/time or unknown, and reports suspected external automation without retry. Continue unaffected streams. Disabled Jira permits no writes.

New reviewer identities default to the selected `--codeowner` (`@maintainer` unless overridden); retain existing identities. Verify access, independence and eligible non-author review. Handles and CODEOWNERS do not enforce these qualities.

Use supported completion events or bounded polling. Later scheduling needs a user request and scheduler. The enrolled PR loop permits one active tick and must not race a native writer on the same branch.

While scoped backlog remains, follow the [continuous controller contract](34-CONTINUOUS-CONTROLLER.md). Every configured stream is visibly `WORKING`, `PAUSED_INPUT`, `BLOCKED` or `COMPLETE`; completion triggers an immediate refill decision, while a pause/blocker affects only its stream. Freeze the complete independent reviewer set through the [review barrier](33-REVIEW-COMPLETION-BARRIER.md). Never aggregate while a required result is missing or unacceptable.

Native status is supplied observation, not cryptographic attestation. Deployment-grade writer records require a separate trusted adapter.
