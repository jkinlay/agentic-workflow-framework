# Upgrade 1.9.5 to 1.9.6

Verify release/pin; rehearse disposably. Quiesce/exclusively own every affected project before planning, apply or recovery.

Before managed commits/checkouts merge `.agentic/templates/installed.gitattributes` into root `.gitattributes`; preserve product rules. Inspect nested/info overrides; prevent autocrlf corruption; never renormalize unrelated files.

`PYTHON`=absolute separate-bootstrap interpreter (`python -m venv BOOTSTRAP`). Install verified dependencies: `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. Uppercase paths=absolute. POSIX TMPDIR: external same-filesystem storage; retain private 0700 inheritance replicas listed in qualify JSON. Windows preflights dispose creating handles; dry runs skip them.

Snapshot **before apply**, keeping untouched quiescent BASELINE:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Require [preservation policy](docs/runbook/19-runbook-journal-recovery.md#migration-preservation-policy): exact replaced/deleted originals/metadata/receipt/provenance, settings/routes/operating/reviewers/pins/budgets/accounting/history. Default conflicts: no backups; never reset accounting/edit receipts. Unresolved validation commands refuse. JIRA_IDENTITY_UNBOUND permits no writes before explicit identity adoption; disabled Jira stays disabled.

Post-upgrade installed checks: run `RUNTIME -B -I WORKFLOW --root PROJECT verify-installation`, then `validate-config`, then `status`; Absolute PROJECT paths: RUNTIME=`.agentic/.venv/bin/python` (Windows `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. CONFIGURED requires zero exits/integrity true/ACCEPTED configuration/matching digests. ACTIVE also requires independent trust/observed adoption acceptance; adapters/controllers stay OFF. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md). Never reinstall unknown/tampered state.

## 1.9.6 limitation

**1.9.6 limitation—quiescent, identity-checked namespace publication.** Name-based rename or single-file unlink is permitted only for an object created by AWF in this transaction, or an explicitly managed object whose recorded identity is checked immediately before mutation. Every affected parent must remain pinned and held by AWF; the operation must hold the applicable exclusive transaction lock, and the owner must establish quiescence across installers, agents, editors, schedulers and other writers. Owner files are never eligible. Unknown identity, replacement, links, unexpected occupancy or lost custody causes refusal and retained, reported evidence. These checks do not atomically bind a pathname mutation to its inspected leaf: a non-cooperating process with directory-write access can still win the final race. Object-bound publication/closeout is deferred to AWF-258, release 1.9.8. Recursive runtime deletion and deletion-dependent automatic rollback remain refused.

Owner recovery: [canonical steps 1-8](docs/runbook/19-runbook-journal-recovery.md#owner-recovery-and-retiring-resolved-diagnostics).

Recovery recognises only exact sibling temporaries derived from journal transaction identity/naming scheme/targets. Unrecorded look-alikes/managed additions refuse, retaining marker. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budget adoption and compatibility require [migration budget controls](#migration-budget-controls).

[Budgeting](.agentic/docs/39-BUDGETING.md).

## Migration preservation policy

YAML changes only version; preserve comments/format; refusals name paths/remedies. Externally preserve unexpected import/schema files; correct/move outside managed directories. Preserve linked targets externally, restore contained independent regular objects and reconcile references before replanning. Preserve history/containment; exclude unrelated product trees.

`prepare_upgrade` authenticates target manifest/pin/baseline membership; `compare_upgrade` binds rules/hashes/`approved_changes`/`changes`/baseline/manifest.

- Exact managed hashes/sizes; removals require authenticated old-membership/target-absence. Outside-membership content stays owner-owned; refuse collisions, including identical-byte/preinstallation removals. **AGENTS.md is managed**; preserve other owner paths.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve other values/types/permissions.
- Only recorded schema-migrations change state; prederive bytes/source-target-hashes/actions.
  `state_archive_plan` binds named `upgrade-to-<target>` read-only copies/manifest
  to exact contents/sources. Preserve prior archives; prefixes authorize nothing.
- Independently snapshot pre-migration state; query-only in-memory SQLite invokes no migration/initializer/upgrade validator. Preserve all historical tables/columns/rowids/typed values/duplicates/reservations/settlements/unknown charges/audits. **Existing-field exceptions: none.** Check exact new-column/table schema/migrations separately. Non-SQL state/reservations/audits/sidecars/unknown records remain byte-identical; missing continuity refuses.
  Migrations cannot authorize loss.
- Reconstruct receipt membership/hashes/embedded manifest/install-project IDs/mutable
  paths/configuration hash. Require exact provenance target/schema/manifest/retained
  ID/upgrade/manual profile/canonical cohort time; reject extras. No recovery-objects/journal/marker/lock; retain only empty named object-store.
- Authenticate complete journals/files/directories/metadata/fresh transaction namespaces. Backups match exact replaced/deleted originals/metadata; no prefix waiver.
- Exact output directories/permissions/streams; unsupported metadata refuses. Windows probes dispose pinned handles; POSIX replicas remain external.
  Managed removals preserve streams.

## Migration budget controls

External deltas never waive preservation; absent controls stay LEGACY_RETAINED.
Owner-requested OFF adoption preserves comments; owner merge/fetch applies.
Non-OFF changes need another PR. Published audits remain readable; unsupported
pre-release confirmations refuse/remain retained. `import_budget_history` merges
pinned state-store/per-host history. Independent `prepare_budget_import`/`check_budget_import` verify sources/charges/UNKNOWN/deduplication. Qualification
requires published-API history, lost-row injection, import/check/replay/interruption/
concurrency.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. Upgrade report/state records prior-hash/EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness/adoption.

Request AWF-chat adoption from fetched default: preserve OPERATING_CONFIG.yaml comments/text; add accounting/enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime in upgrade PR. `operating set --adopt-budgeting --instruction "Adopt dormant budget controls"` prepares adoption without --set choices/enabling controls. Restricted publication prepares owner branch/body. Owner review/merge/fetch applies; unmerged edits do nothing. Non-OFF changes require another settings PR. Git/PR history replaces confirmation transactions.

Before enabling, follow all [budget-history preservation prerequisites](#budget-history-preservation).

Published 1.9.5 operating records verify without confirmation; unsupported pre-release fields refuse. Preserve records/restore supported baseline before upgrade.

## Budget-history preservation

See [complete prerequisites](docs/runbook/19-runbook-journal-recovery.md#budget-history-preservation).

Rollout unauthorized.
