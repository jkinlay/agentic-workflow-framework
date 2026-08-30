# Minimum Incident Response

Each project names the human stop authority, security contact/channel, token
owners, and evidence-retention location before A2.

## Immediate response

1. Stop agent and scheduled execution; disable or suspend the affected App or
   workflow without deleting evidence.
2. Revoke affected tokens, keys, sessions, and workload-identity grants.
3. Quarantine affected branches and pull requests; block merge, release, and
   promotion.
4. Preserve immutable references to commits, Actions runs, reviews, audit
   events, task contracts, and approvals in the approved incident store.
5. Assess confidentiality-domain crossing and notify the named authorities.

## Recovery

Restore from a known trusted base, rotate credentials, verify App/ruleset
permissions, rerun protected checks and independent review, and require a human
decision before reactivation. Do not reuse a compromised worktree or silently
rewrite pushed history.

## Required incident record

Record incident ID, reporter, stop authority, discovery and containment times,
affected repositories/domains/identities, revoked credentials, quarantined
refs, evidence links, impact, recovery decision, and follow-up owner. A ruleset
bypass caused by reviewer outage is recorded here even when no compromise is
suspected.
