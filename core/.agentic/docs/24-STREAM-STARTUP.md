# Native coordination runbook

Version 1.7.0. This is coordinator guidance for a host with delegation tools. The planner does not launch agents or authenticate active-writer records.

See [specification](../SPECIFICATION.md). `execution.independent_reviewers` defaults to three total, one per stream, not three approvals per PR. Reviewers and any separate coordinator consume shared host slots; no six-agent concurrency is implied.

## Plan and dispatch

Inspect assigned scope, current inventory, owners and conflicting paths. Before Jira is available, use provisional local IDs. The planner supports A/B/C, at most three implementation streams. Lower project limits apply; higher limits do not expand this planner and its output reports the structural ceiling.

Read `execution.native_streams.enabled`, `dispatch_policy`, `max_parallel_tickets`, `max_parallel_tickets_per_stream` and `max_spawn_depth` from the reviewed config, together with observed host capacity. Do not infer omitted settings from a partial example.

Use `.agentic/scripts/plan_streams.py` over verified inventory for project-owned `STREAMS.md`/`STREAMS.json`. Inspect dependency and conflict boundaries. Synthetic fixtures remain demonstrations.

Through supported host tools, assign bounded non-overlapping work within those limits. An implementing coordinator is a writer; reviewers and queued proposals are not. Record returned agent IDs and assignments, reuse existing owners and account for other occupied slots.

Follow [routing](27-MODEL-ROUTING.md): reserve, launch through the host, settle observed results. Missing launch, usage or enforcement capability is a limitation, not inferred success.

Increasing any execution cap requires explicit human direction and governance review. Never classify a lower value as obsolete to justify raising it. Continue other permitted work, reduce concurrency or request the specific change. CODEOWNERS needs valid owners and enforced repository rules.

## Reconcile

On each result, reconcile ownership and candidate identity, run tests and independent review, route amendments or the next assignment. A report alone is not completion. Report blocked work with evidence, required action and owner while continuing independent authorized work.

Use supported completion events or bounded polling. Scheduling later work requires a user request and a scheduler. The separate enrolled PR loop allows one active tick and must not race a native writer on the same branch.

Native status is supplied observation, not cryptographic attestation. Deployment-grade unforgeable writer records require a separate trusted adapter.
