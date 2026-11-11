# 1.9.6 qualification/preservation

AWF-238: Tier 3, owner-approved FULL rebind except automatic
deletion deferred to [AWF-258](#awf-258-successor). Retain pre-F5 #1
(object-continuity deletion). Rollout/adapters unauthorized.

## Baseline

`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Assets/tags/pins/receipts immutable; Git proves no publication.

## Preservation

Require accepted baselines/exclusive ownership/quiescence/authorization; record repository/ID/default-branch/releases/permissions,
owner-files/settings/instructions/CODEOWNERS, routes/pins/opt-outs/Jira scope/budgets,
ledgers/sidecars/reservations/operating history/archives/receipts. Defaults stay OFF.

`scripts/qualify_upgrade.py` provides:

- `snapshot --project DISPOSABLE --output BEFORE`: bind bytes/sizes/modes/ownership/access/streams/directories/root, canonical runtime and exact transaction backups.
  Exclude root `.git`/unrelated product trees; retain root files/nested archives.
  Refuse managed/state links/incomplete identity/unreadable enumeration; snapshot pre-apply.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH
  --output PLAN`: require identical inventories/identity.
- Retain BEFORE/resnapshot. `compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE
  --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON`
  derives identity-verified `approved_delta`; inspect `remaining_changes`.
  `--allowed-delta` only constrains policy; missing baseline/release inputs: NOT_RUN.
  Owner/history loss: unwaivable.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: require clean committed source/current manifest; bind commit/tree/pin; never regenerate. Bootstrap authentic baseline; seed instructions/ledger/published-API history; snapshot/policy/upgrade (`--on-conflict backup`)/installed checks/compare.
  Verify complete locked wheels. Failures retain inventories/comparison/NOT_QUALIFIED/failed-stage.
  Require QUALIFIED_DISPOSABLE_UPGRADE/two CONFIGURED stages/zero exits/integrity true/ACCEPTED
  configuration/bound source-policy-receipt hashes/preservation, plus executed/PASS
  `source_managed_membership`/`historical_state_continuity`/`named_streams`. Missing migration/SQL continuity refuses.

Digest-only reports exclude contents/SIDs; unverified permissions/streams fail.
`PRESERVATION_COMPARISON_ONLY` establishes no installed qualification.

### Migration policy

`prepare_upgrade` authenticates target manifest/pin/baseline membership; `compare_upgrade` binds rules/hashes/`approved_changes`/`changes`/baseline/manifest.

- Exact managed hashes/sizes; removals require authenticated old-membership/target-absence. Outside-membership content stays owner-owned; refuse collisions, including identical-byte/preinstallation removals. **AGENTS.md is managed**; preserve other owner paths.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve other values/types/permissions.
- Only recorded schema migrations change state; prederive bytes/source-target-hashes/actions.
  `state_archive_plan` binds named `upgrade-to-<target>` read-only copies/manifest
  to exact contents/sources. Preserve prior archives; prefixes authorize nothing.
- Independently snapshot pre-migration state; query-only in-memory SQLite invokes no migration/initializer/upgrade validator. Preserve all historical tables/columns/rowids/typed values/duplicates/reservations/settlements/unknown charges/audits. **Existing-field exceptions: none.** Check exact new-column/table schema/migrations separately. Non-SQL state/reservations/audits/sidecars/unknown records remain byte-identical; missing continuity refuses.
  Migrations cannot authorize loss.
- Reconstruct receipt membership/hashes/embedded manifest/install-project IDs/mutable
  paths/configuration hash. Require exact provenance target/schema/manifest/retained
  ID/upgrade/manual profile/canonical cohort time; reject extras. No recovery objects/journal/marker/lock; retain only empty named object-store.
- Authenticate complete journals/files/directories/metadata/fresh transaction namespaces. Backups match exact replaced/deleted originals/metadata; no prefix waiver.
- Exact output directories; preserve permissions/streams. Windows probes dispose pinned handles; POSIX retains external inheritance replicas; unsupported metadata refuses. Windows metadata preflights retain external probes.
  Managed removals preserve streams.

External deltas never waive preservation; absent controls stay LEGACY_RETAINED.
Owner-requested OFF adoption preserves comments; owner merge/fetch applies.
Non-OFF changes need another PR. Published audits remain readable; unsupported
pre-release confirmations refuse/remain retained. `import_budget_history` merges
pinned state-store/per-host history. Independent `prepare_budget_import`/`check_budget_import` verify sources/charges/UNKNOWN/deduplication. Qualification
requires published-API history, lost-row injection, import/check/replay/interruption/
concurrency. See `MIGRATION-v1.9.5-to-v1.9.6.md`.

## AWF-258 successor

Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8).

## Recovery/gates

Interrupt staging/writes/receipts/provenance/swaps/cleanup; recover freshly.
Missing/tampered objects refuse; preserve empty-directory protections.
Refuse pathname-recursive deletion; retain `.venv.backup-*`/`.venv.cleanup-*`,
staged/canonical runtimes requiring removal and `.agentic-backup/<transaction>`,
consuming runtime/backup space.
Rollback requiring removal refuses before managed mutation. Recovery retaining backups retires coordination, returning `status: COMMITTED`,
`transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`,
`retained_directories` and `installation_recovery`. Diagnostic status:
COMMITTED_CLEANUP_PENDING; inspect exact inventory.
Unresolved directories require `.agentic-install/retained-journal.json`;
bounded records/indexes retain history/paths/sizes(null=unknown)/reasons/remedies.
Valid retention never blocks integrity/capabilities/transactions. Adopt only existing managed directories reused without creation/deletion;
first record source evidence/"adopted by transaction <id>" in `directory-adoptions.json`. Report unmanaged/replaced directories; delete none.
Standalone failures retain stages/original-errors without cleanup-retry; portable
stages use sibling cleanup-pending.json. Archives stay nonrecursive.

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Lock presence proves nothing.
2. Preserve journal/objects/endpoints/metadata. `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST` requires 0/`matches:true`; stop for 1=mismatch or 2=malformed/refused. PYTHON=external bootstrap interpreter; RELEASE=verified journal-writing release; PROJECT=absolute project. Backup=recorded-path/`runtime.previous_sha256`.
3. No backup/canonical `.agentic/.venv`=previous hash: quarantine stage only. Otherwise authenticate canonical/stage=`runtime.new_sha256`; quarantine authenticated runtimes, leaving canonical absent. Absent stage: canonical=new when swapped, previous when unswapped. Existing stage/null new_sha256: unauthenticated; retain diagnostics. Quarantine needs independent owner-verified creation evidence; otherwise stop.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover` restores provable managed files/receipt/provenance/backup.
5. Legacy `.agentic-backup/<txid>`: authenticate existing journal-listed FILEs: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST` (new_sha256). Require reported modes/permissions/streams=journal/accepted baseline; unrecorded/unprovable metadata stops. Reject extras; quarantine exact directory/rerun recovery. Never edit journal/marker.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted; use step 7. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` externally. No retirement CLI; preserve evidence objects/adoption audit.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute restored paths: RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

Bind candidate/artifact/review/trust/publication, one-reservation/host/exactly-once allocation/limits; record commands/platform/interpreter/timings/counts/skips/failures/receipt hashes.

Require zero exits/integrity true/ACCEPTED configuration/bound hashes. CONFIGURED proves consistency, not execution history. Require independent/specialist review, hosted checks/trust/publication-scan/review-barrier/owner-acceptance. Authorized rollout needs
disposable proof. ACTIVE requires independent trust/observed receipt-changing adoption
merge/accepted raw default-branch bytes; enables no adapters.
