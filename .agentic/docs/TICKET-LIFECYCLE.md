# Ticket lifecycle

See the canonical [ticket lifecycle guide](23-TICKET-LIFECYCLE.md).

## Deterministic ticket plans

Lane-fill planning snapshots use the generated
[`ticket-plan` schema](../schemas/ticket-plan.schema.json) and
[`agentic.planning_graph`](../lib/agentic/planning_graph.py). The planner orders
declared dependencies, overlapping write footprints and the reviewed
single-lane generator order; applies the lane cap, dependency start gap and
owner-review windows; and emits canonical JSON plus stable Mermaid. Ticket
input order never changes the bytes.

Every ticket carries its critical/serial position, lane class, independence
result, generator slot, ownership key, idle reason, phase, status and in-lane
state. Missing, unknown or inconsistent fields; unknown reason codes; invalid
risk/round caps; dangling dependencies; and cycles fail closed with named
reasons. Validation replays the plan and rejects changed derived fields. The
record is planning evidence only and grants no dispatch, adapter or merge
authority.
