# Supplied report layout checklist

Use reporting.bodies; these are field instructions, not observed project data.
Every report retains observation/timezone, scope/candidate, unknowns, source
freshness, baseline/forecast revision and verified detailed-report link if hosted.
Section labels below are the exact ordered `sections` keys. No silent truncation.

Hourly:

1. Summary (explicitly state if implementation has not started).
2. Current work (planned Start/End, actual stage, actor, elapsed, activity, gate).
3. PRs and Jira tickets (all verified associations, observed status/head).
4. Waiting and blockers (reason, responsible owner, age, resume).
5. Next tickets (planned Start/End, eligibility/dependencies, conditional label).
6. Capacity and review queue (observed capacity, queues and file conflicts).
7. Changes since the previous report.
8. Next action.
9. Owner action (accountable owner, requested response, oldest review queue).
10. Sources and freshness.

End of Day (header shows overlapping Today and Since previous daily report):

1. Summary.
2. PRs merged and Jira delivery (distinct PR count, time/head/merge commit,
   separate reconciled ticket count and merged/pending work).
3. Remaining work (required leaf counts/states; dispositions separately).
4. Critical paths (milestones, changes, external/owner/reviewer waits).
5. Time and estimate comparison (mean/median, n/coverage, original compatible
   estimates and measured execution/review/wait components).
6. Token usage (complete-ticket mean/n/coverage, consumed, WIP/failures/retries,
   shared overhead once, remaining/expected range, unknowns/model mix).
7. Epic and project forecast (planned Start/End, milestone scenarios, token
   reconciliation, assumptions/drift).
8. Blockers and risks (owner, age, consequence, oldest actions/final reviews).
9. Next action (proposed dependency-ordered work and gate).
10. Owner action.
11. Sources and freshness.

Jira review:

1. Summary (business outcome/boundaries, evidenced approval stage/revision).
2. Scope and progress (distinct Epic/leaf counts, priority/size matrix with
   Unknown/Unestimated/Unassigned, states/dispositions).
3. PRs and Jira tickets.
4. Critical path (duration/milestone, external gate/owner/delay).
5. Epic outlook (owner, priority/size, remaining, planned Start/End, ETA scenarios,
   consumed + remaining = expected tokens; shared overhead separately).
6. Time and review outcome (cohort mean/median/IQR, n/coverage, rounds/gates,
   risk/findings/dispositions and separate owner approval).
7. Blockers.
8. Next action.
9. Owner action.
10. Sources and freshness.

Immediate owner-action and final-human-review begin:

1. Decision / readiness (request/reason or gate-qualified readiness; stage,
   candidate, next gate and verified PR/Jira associations).
2. Owner action (owner, requested time, supported options, recommendation).
3. Impact and independent work (delay consequence, continuing work, resume).
4. Evidence (candidate-bound validation/reviews, limitations, freshness/unknowns).

Owner-action then includes Summary, PRs and Jira tickets, Next action, Sources and
freshness. The combined final-human-review adds Jira Summary, Scope and progress,
PRs and Jira tickets, Critical path, Epic outlook, Validation and review completion,
Findings and accepted limitations, Time and review outcome, Blockers, Next action,
Sources and freshness. It is ONE event/message with implementation Jira review.

Complete matrices, per-ticket/Epic planned dates, cohorts, schedule and evidence
belong in the versioned detail artifact. Unknown stays Unknown; None requires
observed absence. Links require existence/access verification. Approval is not
merge, a merged PR is not reconciled Done, and no report grants authority.
