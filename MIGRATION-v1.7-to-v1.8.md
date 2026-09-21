# Upgrade an AWF project to 1.8

Installing the user-level skill and adopting a project release are separate operations. The skill installer verifies the selected package, backs up the existing `awf` directory outside skill discovery and preserves catalog/update-channel settings. Do not uninstall first. A preserved channel may advertise an older release; use the bundled verified source explicitly until the owner updates it.

## Project migration

1. Record the accepted installation version and pins, outstanding changes, active writers and scheduled jobs. Pause affected writers; preserve project configuration, product instructions, ledgers and evidence. Work on an isolated adoption branch.
2. Prepare the verified source in a new external cache. Follow the [adoption commands](.agentic/docs/20-NEW-PROJECT-SETUP.md), beginning with `--dry-run`. Use backed-up `install` mode for cross-version changes, never same-version `upgrade`. Preserve the complete backup and transaction journal.
3. Set `template.expected_workflow_version` to `1.8.0` in the reviewed configuration. Preserve actual identities, budgets, model choices, scheduler enrollment and ownership. Do not replace the whole project configuration with an example.
4. Existing reconciliation policies may omit `max_reconciled_incident_retries_per_ticket`; omission behaves as two retries without rewriting bytes or the policy hash. New defaults explicitly include the field. Counts span policy, phase, role and agent changes. After exhaustion, safe closure remains possible but new ticket admission stops. Raising the cap or enabling reconciliation needs human direction. Do not clear the ledger.
5. Keep prior policy cohorts queryable under their original hashes. Changing defaults creates a new cohort; it does not retroactively qualify historical runs or erase their charges. Do not pool changed-policy evidence to meet adaptive thresholds.
6. Review the substantive native prompts and the [external-review boundary](.agentic/docs/28-EXTERNAL-REVIEW.md). The optional component installs as inert files, not a scheduled service. Existing external gate/policy stays project-owned; do not weaken required engines or copy qualification flags. No live external calls are enabled by upgrading the skill.
7. Run configuration and installation verification, project tests, source/installed acceptance checks and an independent governance review. Preserve current-head acceptance evidence before activating the new baseline. A failed preflight leaves the project unchanged; an interrupted install requires journal-guided recovery.

For rollback, stop affected execution and restore the recorded complete installation/configuration backup under human direction. Verify its original pins and expected version before resuming. Retain the newer ledger/audit evidence separately; rollback never erases spent budget or external side effects. Do not perform an automatic downgrade over unreviewed local changes.

Projects beginning at older versions should also read the [routing migration](MIGRATION-v1.5-to-v1.6.md) and [reviewer/reconciliation migration](MIGRATION-v1.6-to-v1.7.md). Earlier scaffolds require an explicit mapping of their actual contracts to the current specification; historical defaults are not safe current policy.
