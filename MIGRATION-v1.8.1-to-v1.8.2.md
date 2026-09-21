# Migrate the 1.8.1 baseline to 1.8.2

Use the existing-project adoption guide for any project, including partially completed development. This is a reviewed, backed-up cross-version install; same-version `--mode upgrade` is not a cross-version migration. Preserve actual configuration, Jira mappings, accepted evidence and prior approval identities. Installing the portable skill only updates discovery; it does not migrate projects.

Verify the independently supplied ZIP and manifest pins. Record existing installation, configuration, protected external state and active work. Prepare an adoption PR from the actual project baseline, map project-owned configuration into the new template and retain historical evidence without relabelling approvals. Run source and installed validation, including the documented `verify.py unit` command, before activation.

## Retry ledger

Pause admission and back up the trusted routing database consistently. Opening it with the new router transactionally imports each existing retry ceiling into an append-only event and seals the old table. Existing authorizations, incident history and charges remain. Unknown historical reduction provenance stays unknown. Reopening does not repeat migration. SQL guards resist update/delete/REPLACE, but workers must still lack direct database writes and privileged API access.

All subsequent seeds, reductions and increases carry project/revision/value/time/provenance. A lower configured cap still persists. To increase it, the trusted host authenticates human direction for a staged proposed policy, reads the current ceiling/revision, and calls `authorize_retry_limit` with the exact binding and `expires_at` within 24 hours. Initialize a new project's ceiling through admission first. Examples: [retry-authority tests](.agentic/tests/test_retry_authority.py).

Keep admissions paused through approval, the host operation and installation of that identical proposed policy, then resume. The proposed configuration need not already be active. If installed first, admission blocks until the grant is recorded; if old configuration remains active during admission after a raise, it can lower the cap again. Expiry limits first application, not the duration of an applied ceiling. Replaying an already recorded request returns history and never restores later-lowered capacity. Old requests remain replayable but cannot grant new increases. A ledger revision prevents stale approval after a lower/raise cycle returns to the same number.

Safe settlement/reconciliation remains available with exhausted admission limits. Never reset state or switch to an old router to recover capacity. Rollback requires a host-reviewed plan preserving all newer events; old routers may bypass admission rules or conflict with sealed tables.

## External review and operation

Review [external-review setup](.agentic/docs/28-EXTERNAL-REVIEW.md) for protected environments, trusted workflow wakeups, qualification and timeout semantics. Keep all qualification claims false until observed. Offline passing tests do not establish actual provider permissions, secret isolation or a disposable live canary result. The adapter remains separately configured and cannot merge or write Jira.

The complete distribution contains the current portable skill and source. Preserve old release archives as historical identities; use the current release folder and published digest for installation.
