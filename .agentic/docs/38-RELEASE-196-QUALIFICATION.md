# 1.9.6 qualification/preservation

AWF-238: Tier 3, owner-approved FULL rebind; sole exception: automatic
deletion deferred to [AWF-258](#awf-258-successor). Retain pre-F5 finding #1
(object-continuity deletion safety). No rollout/adapter authority.

## Baseline

`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Assets/tags/pins/receipts immutable; Git proves no publication.

## Preservation

Require accepted baselines/exclusive ownership/quiescence/authorization; record repository/ID/default-branch/releases/permissions,
owner files/settings/instructions/CODEOWNERS, routes/pins/opt-outs/Jira scope/budgets,
ledgers/sidecars/reservations/operating history/archives/receipts. Defaults stay OFF.

`scripts/qualify_upgrade.py` provides:

- `snapshot --project DISPOSABLE --output BEFORE`: bind bytes/sizes/modes/ownership/access/streams/directories/root, canonical runtime and exact transaction backups.
  Exclude root `.git`/unrelated product trees; preserve root files/nested archives.
  Refuse managed/state links/incomplete identity/unreadable enumeration; snapshot pre-apply.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH
  --output PLAN`: require identical inventories/identity.
- Retain BEFORE; resnapshot. `compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE
  --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON`
  derives identity-verified `approved_delta`; inspect `remaining_changes`.
  `--allowed-delta` only constrains policy; missing baseline/release inputs: NOT_RUN.
  Owner/history loss: unwaivable.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: require clean committed source/current manifest; bind commit/tree/pin without regeneration. Bootstrap authentic baseline; seed instructions/ledger/published-API history; snapshot/policy/upgrade (`--on-conflict backup`)/installed checks/compare.
  Verify complete locked wheels. Failures retain inventories/comparison/NOT_QUALIFIED/failed-stage.
  Require QUALIFIED_DISPOSABLE_UPGRADE/two CONFIGURED stages/zero exits/integrity true/ACCEPTED
  configuration/bound source-policy-receipt hashes/preservation, plus executed/PASS
  `source_managed_membership`/`historical_state_continuity`/`named_streams`. Missing migration/SQL continuity refuses.

Digest-only reports exclude contents/SIDs; unverified permissions/streams fail.
`PRESERVATION_COMPARISON_ONLY` establishes no installed qualification.

### Migration policy

`prepare_upgrade` authenticates target manifest/pin/baseline membership; `compare_upgrade` binds rules/hashes/`approved_changes`/`changes`/baseline/manifest.

- Exact managed hashes/sizes; removals need authenticated old membership/target
  absence. Outside-membership content stays owner-owned; refuse collisions, including identical bytes/preinstallation removal. **AGENTS.md is managed**; preserve other owner paths.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve other values/types/permissions.
- Only recorded schema migrations change state; prederive bytes/source-target hashes/actions.
  `state_archive_plan` binds named `upgrade-to-<target>` read-only copies/manifest
  to exact contents/sources. Preserve prior archives; prefixes authorize nothing.
- Independently snapshot pre-migration state; query-only in-memory SQLite invokes no migration/initializer/upgrade validator. Preserve all historical tables/columns/rowids/typed values/duplicates/reservations/settlements/unknown charges/audits. **Existing-field exceptions: none.** Check exact new-column/table schema/migrations separately. Non-SQL state/reservations/audits/sidecars/unknown records remain byte-identical; missing continuity refuses.
  Migrations cannot approve their loss.
- Reconstruct receipt membership/hashes/embedded manifest/install-project IDs/mutable
  paths/configuration hash. Require exact provenance target/schema/manifest/retained
  ID/upgrade/manual profile/canonical cohort time; reject extras. No recovery objects/journal/marker/lock; only empty named object-store remains.
- Authenticate complete journals/files/directories/metadata/fresh transaction namespaces. Backups match exact replaced/deleted originals/metadata; no prefix waiver.
- Exact output directories; preserve permissions/streams. Disposable probes verify modes/inheritance without baseline changes.
  Managed removals preserve streams.

External deltas cannot waive preservation; absent controls stay LEGACY_RETAINED.
Owner-requested OFF adoption preserves comments; owner merge/fetch applies.
Non-OFF changes need another PR. Published audits remain readable; unsupported
pre-release confirmations refuse/remain retained. `import_budget_history` merges
pinned state-store/per-host history. Independent `prepare_budget_import`/
`check_budget_import` verify sources/charges/UNKNOWN/deduplication. Qualification
requires published-API history, lost-row injection, import/check/replay/interruption/
concurrency. See `MIGRATION-v1.9.5-to-v1.9.6.md`.

## AWF-258 successor

Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8).

## Recovery/gates

Interrupt staging/writes/receipts/provenance/swaps/cleanup; recover freshly.
Missing/tampered objects refuse; retain empty-directory protections.
Refuse pathname-recursive deletion; retain `.venv.backup-*`/`.venv.cleanup-*`,
staged/canonical runtimes requiring removal and `.agentic-backup/<transaction>`,
consuming runtime/backup space.
Rollback requiring removal refuses before managed mutation. Recovery retaining backups retires coordination, returning `status: COMMITTED`,
`transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`,
`retained_directories` and `installation_recovery`. Diagnostic status:
COMMITTED_CLEANUP_PENDING; inspect exact inventory.
Unresolved directories require `.agentic-install/retained-journal.json`;
bounded records/indexes retain source history/paths/sizes(null=unknown)/reasons/remedies.
Valid retention never blocks integrity/capabilities/transactions. Adopt only existing managed directories reused without creation/deletion;
record source evidence/"adopted by transaction <id>" in `directory-adoptions.json` before
resolving rows. Report unmanaged/replaced directories; delete none.
Standalone failures retain stages/original errors without cleanup retry; portable
stages use sibling cleanup-pending.json. Archives stay nonrecursive.

1. Obtain exclusive owner control; stop writers.
2. Preserve journal/objects/endpoints/bytes/metadata; use `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`: require exit 0/`matches:true`; mismatch/error stops. PYTHON=external bootstrap Python; RELEASE=the verified journal-writing candidate release. Backup: recorded-path/`runtime.previous_sha256`.
3. If no backup exists and canonical `.agentic/.venv` matches `runtime.previous_sha256`, quarantine only the stage outside the project; leave the canonical runtime untouched. Otherwise quarantine failed canonical/staged runtimes outside the project, leaving canonical absent. Authenticate stages with the same `runtime-hash` command and `runtime.new_sha256`; use canonical/`runtime.previous_sha256` for the no-backup check.
4. Run candidate release's external bootstrap `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`; restore provable managed files/receipt/provenance/authenticated backup.
5. For legacy `.agentic-backup/<txid>` refusal, authenticate journal-recorded files/metadata; quarantine that exact directory externally; rerun `--recover`. Never edit journal/marker.
6. Reconcile rows: record authenticated restoration/quarantine or deliberate retention. The canonical RECOVERY_REFUSED row names restored runtime; it is not automatically adopted. Resolve via step 7; never discard runtime/remove marker.
7. After resolving every row, archive index/referenced evidence/path-by-path resolutions; move only the resolved `retained-journal.json` outside the project. No owner retirement CLI exists. Preserve evidence objects/adoption audit.
8. Run `RUNTIME -B -I WORKFLOW --root PROJECT` with `verify-installation`, `validate-config`, then `status`. RUNTIME=restored `.agentic/.venv/bin/python` (`.agentic/.venv/Scripts/python.exe` on Windows); WORKFLOW=restored `.agentic/scripts/workflow.py`; use absolute paths. Restored 1.9.5 tooling prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE state.

Bind candidate/artifact/review/trust/publication, one reservation/host/exactly-once allocation/limits; record commands/platform/interpreter/timings/counts/skips/failures/receipt hashes.

Require zero exits/integrity true/ACCEPTED configuration/bound hashes. CONFIGURED proves consistency, not execution history. Require independent/specialist review/hosted checks/trust/publication scan/review barrier/owner acceptance. Authorized rollout needs
disposable proof. ACTIVE requires independent trust/observed receipt-changing adoption
merge/accepted raw default-branch bytes; enables no adapters.
