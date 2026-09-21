# Disposition of operating configuration and routine-flow findings

This patch addresses the operational gaps identified in the supplied review of the previous release. Its incident narratives are supplied evidence, not independently authenticated Jira history. The existing evidence core, human merge boundary, adoption/enablement distinction and historical releases remain intact.

| Finding | Change and boundary |
| --- | --- |
| I1: Jira/Epic transitions are improvised and retried | Entry points, controller/worker prompts, skills and the restored lifecycle guide state the sole modelled Jira status write: one explicitly authorized ticket transition to Done after confirmed merge. Epics are scope/grouping only; non-done status mappings are reserved. Read before/after, retain observed actor/time or unknown, stop further Jira writes on mismatch/unknown outcome and report suspected external automation conflict without retrying. Independent streams and read-only investigation continue. No Jira writer or pre-merge transition event is added. |
| I2: The initial draft PR is missing from handoffs | COMPLETE leads to scoped feature publication and an observed draft PR, then current validation/requirements, mark-ready and critic review of the observed PR head. Review-ready is distinct from final-gate owner readiness. Conditional routine publication requires accepted repository/default/ref identity and fresh matching live raw APPLIED rules evidence; it grants no authority and bypasses no platform, scope, secret or adapter prerequisite. |
| I3 / B.3: Routine behavior lacks prospective coverage | Eight prospective cases cover routine ticket flow and accepted/refused operating changes. Codes cover publication, draft creation, mark-ready, Jira transition and operating apply/refusal. Offline contract regressions remain separate from actual model evidence; preparation is not execution. No historical grade is relabelled. |
| A.1–A.6: Operating choices need conversation support | Root operating configuration separates three default streams and route choices from protected ceilings/allowlists/floors. Show/set/recommend enforce explicit acceptance, exact refusals, verbatim change audit and separate operating hashes. Routing/planning preserve running reservations and drain reductions. Installed CONFIGURED requires the actual accepted operating snapshot. |
| S1: Missing host-skill lookup leaks an exception into the status next action | Status separates a concise remedy from structured diagnostic details. Missing/unverified trust still cannot establish ACTIVE. Runtime tests and final acceptance must establish the implemented behavior. |
| S2: Default owner identities may be mistaken for eligibility | Preserve the explicitly requested `@maintainer` default and existing project choices. Document configurable `--codeowner` mapping plus separately verified access, independence and eligible non-author review. This is no new runtime eligibility enforcement or universal required-flag policy. |

## Authority and interpretation

The lifecycle guide renders [workflow.yaml](.agentic/workflow.yaml) and its control/recovery rules. The reference machine records state, not remote effects. The scheduled adapter operates an existing PR; it does not create the initial draft, merge or write Jira. Native hosts must observe actual PR creation/head and successful mark-ready. Local tests, worker COMPLETE, critic approval and a ready gate never grant human merge authorization.

A Jira readback mismatch establishes a conflict/unknown outcome, not its cause. Preserve issue/operation/status facts and actual actor/timestamps when available. The review's proposed automation or workflow-post-function causes require read-only investigation; do not name a culprit or change Jira rules from speculation. Missing authority, disabled Jira and unavailable reads cannot be replaced with invented transitions or successful reconciliation.

An APPLIED baseline is not a guarantee that every push is safe. Publication classification remains trusted-host presentation with `execution_authority: false`; task scope, actual platform permissions and applicable protected-ref/secret/adapter prerequisites govern execution. Adoption preparation still permits missing rules with warnings. Live required-CI, reviewer provenance and qualification remain separate.

## Evidence limits

Interpretation details: new governance omits the optional fixed reviewer count, deriving one per operating stream; a retained legacy count must agree until changed through a governance PR. Mandatory risk/review floors apply after overrides and pins. Pinned choices block optional escalation; unpinned normal worker routes permit enabled simple work. Explicit Epic-scoped routes use `--epic EPIC-ID`, preserving global choices and applying only to matching dispatches. Operating history uses a recoverable local transaction, not a replacement authorization ledger. Eight pilot cases fit the existing per-batch limit as 3+3+2.

Focused offline checks, current independent review and source/installed/portable acceptance are recorded externally against final pins. Until recorded, they are pending; this document asserts no test totals or passing release. Failed attempts remain visible.

Retained **10/12 strict FAIL**, **5/12 FAIL** and **1/3 FAIL** results remain unchanged. Different cases/contracts prevent controlled causal comparisons; code selection is not observed action execution. This patch's prospective routine cases need their own frozen inputs and fresh execution authorization. No live GitHub/Jira mutation, model pilot, scheduler qualification, owner eligibility attestation or external-engine activation is claimed here.

See [migration](MIGRATION-v1.8.6-to-v1.8.7.md), [lifecycle](.agentic/docs/23-TICKET-LIFECYCLE.md), [adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md) and the preserved prior review/evidence.
