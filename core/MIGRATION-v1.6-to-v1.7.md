# Reviewer and recovery migration: projects originating at AWF 1.6

The 1.7 transition introduced conservative operator reconciliation, monotone routing checks and a default pool of three independent reviewers total, one per workstream. These are native host-coordination roles, not three approvals per PR and not proof of available parallel capacity. Workers and reviewers share the actual host limit.

When adopting the current release, preserve deliberate concurrency and reviewer settings. Map the reviewer pool explicitly; retain one writer per ticket/path and separate critic contexts. A reviewer unavailable under its approved model/effort floor pauses that review rather than silently downgrading it. Existing model pins, lower execution caps and protected ownership survive migration.

Reconciliation starts disabled. Enabling it requires human-approved operator identities and host evidence of termination/remediation. The local helper records supplied assertions; it cannot authenticate the operator or prove that a process stopped. Preserve full orphan reservations, known overruns and append-only audit. Never delete ledger state to regain capacity. The current release additionally limits repeated reconciled-incident retries; preserve old policy hashes and inspect historical cohorts separately.

Use the current [migration procedure](MIGRATION-v1.7-to-v1.8.md) for isolated preflight, backed-up install, expected-version update, verification and rollback. Product instructions belong in reviewed project-owned files rather than edits to byte-pinned managed instructions. User-level skill replacement preserves its own backup/settings but leaves project versions untouched.

The public scaffold and template core were consolidated, not merged into a new grant of authority. Preserve existing external-engine gate/policy and validate its separate [qualification requirements](.agentic/docs/28-EXTERNAL-REVIEW.md); a native critic or offline pass cannot substitute for it.
