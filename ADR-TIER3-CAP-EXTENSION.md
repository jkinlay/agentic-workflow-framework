# One owner extension for Tier 3 review caps

**Status:** accepted by owner ruling of 13 October 2026; the policy binds once this PR merges. A follow-up ticket implements it in configuration, schema and gates.

## Context

[30-REVIEW-TIERS-AND-CLOSEOUT](.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md) gives Tier 3 an unextendable three-round cap, and the configuration sets `tier3_review.max_rounds: 3`. Only Tier 2 may extend, through an owner-signed `review-cap-disposition` of `EXTEND_ONE_CYCLE`.

During 1.9.5 delivery the owner nevertheless approved Tier 3 rounds beyond three:

* AWF-76 (#130) round 4, approved 21:30 on 12 October 2026 under the owner's 09:50 targeted-P1 extra-round ruling. Both are recorded in the PR description.
* AWF-10 (#118), which passed at round 8.

Practice and policy had diverged. The owner chose to ratify an extension rather than park or rescope at the cap.

## Decision

1. **One extension.** A Tier 3 ticket may receive **at most one** extension round beyond its three-round cap.
2. **Owner-signed.** The extension needs an owner-signed `review-cap-disposition` of `EXTEND_ONE_CYCLE`, bound to the exact head and recorded like a Tier 2 extension. A relayed approval is not enough.
3. **Targeted.** The disposition lists the open P1 finding ids the round may address. Findings outside that list become successor tickets, not new scope.
4. **Same safeguards.** The extension round keeps the independent critic, exact-head CI and the Tier 3 owner review. It runs at the top escalation step in [ADR-LANE-MODEL-ROUTING](ADR-LANE-MODEL-ROUTING.md).
5. **No second extension.** After the extension round, the owner chooses one of:
   * merge with notes (non-boundary findings only);
   * park;
   * rescope.

   Boundary findings always block.
6. **Earlier rounds.** Rounds approved before this decision (AWF-76 round 4; AWF-10 rounds 4–8) are recorded as owner-approved exceptions. They are not precedents under this rule. Owner approval records for AWF-10's extra rounds are still to be cited.

## Consequences

**Follow-up Tier 3 governance ticket:**

* Allow Tier 3 in `EXTEND_ONE_CYCLE` with a per-tier limit of one (for example `execution.max_cap_extensions_by_tier`, Tier 3 = 1).
* Require the finding-id scope on Tier 3 extensions.
* Update doc 30, the disposition schema, `cap-plan`, the gates and their tests.

Until that ships, Tier 3 extensions are recorded as owner rulings carrying the same fields:

* ticket and PR;
* head;
* round;
* finding ids;
* approver and time.

**Effects:** review policy becomes slightly weaker for Tier 3, by explicit owner direction. The extension is bounded, targeted and auditable. Tier 1 and Tier 2 are unchanged.
