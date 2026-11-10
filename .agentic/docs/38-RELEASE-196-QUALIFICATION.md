# Release 1.9.6 qualification and preservation

AWF-238: Tier 3, owner-approved FULL rebind with one named exception: automatic
deletion deferred to [AWF-258](#awf-258-successor). Retain pre-F5 finding #1
(object-continuity deletion safety). No rollout/adapter authority.

## Baseline

`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Assets/tags/pins/receipts stay immutable; Git proves no publication.

## Preservation

Require accepted baselines/exclusive ownership/quiescence/authorization. Record repository/ID/default-branch/releases/permissions,
owner files/settings/instructions/CODEOWNERS, routes/pins/opt-outs/Jira scope/budgets,
ledgers/sidecars/reservations/operating history/archives/receipts. Defaults stay OFF.

`scripts/qualify_upgrade.py` provides:

- `snapshot --project DISPOSABLE --output BEFORE`: hash bytes/sizes/modes,
  ownership/access/streams/directories/root; separately bind canonical runtime contents.
  Exclude root `.git`/unrelated product trees; preserve root files/nested archives.
  Managed/state links, incomplete identity/unreadable enumeration refuse. Snapshot before apply.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH
  --output PLAN`: require identical inventories and matching identity.
- Retain BEFORE; resnapshot after bootstrap. `compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE
  --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON`
  derives `approved_delta` from verified identity; inspect `remaining_changes`.
  `--allowed-delta` only constrains policy. Missing baseline/release inputs: NOT_RUN.
  Owner/history loss cannot be waived.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: require clean committed source/current manifest; bind commit/tree/pin
  without regeneration. Bootstrap authentic baseline; seed instructions/ledger/published-API
  history; snapshot/derive policy/upgrade (`--on-conflict backup`)/compare after installed checks.
  Verify complete wheels against locks. Failures retain inventories/comparison,
  NOT_QUALIFIED/failed-stage. Require QUALIFIED_DISPOSABLE_UPGRADE, two CONFIGURED
  stages, zero exits/integrity true/ACCEPTED configuration/bound source-policy-receipt
  hashes/preservation. Require executed/PASS `source_managed_membership`,
  `historical_state_continuity`, `named_streams`; missing migration/SQL continuity refuses.

Reports contain digests, never contents/SIDs. Unverified permissions/streams fail.
`PRESERVATION_COMPARISON_ONLY` proves no installed qualification.

### Migration policy

`prepare_upgrade` authenticates target manifest/pin and baseline membership. `compare_upgrade` binds rules/hashes/`approved_changes`, exact `changes` and baseline/manifest.

- Exact managed hashes/sizes; removals need authenticated old membership/target
  absence. Outside-membership content stays owner-owned: collisions refuse even
  identical bytes/preinstallation removal. **AGENTS.md is managed**; preserve other owner paths.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve other values/types/permissions.
- Only recorded schema migrations change state; prederive bytes/source-target hashes/actions.
  `state_archive_plan` binds named `upgrade-to-<target>` read-only copies/manifest
  to exact contents/sources. Preserve prior archives; prefixes authorize nothing.
- Snapshot state independently before migration. Query-only in-memory SQLite calls
  no migration/initializer/upgrade validator. Preserve every historical
  table/column/rowid/typed value/duplicate count, reservations/settlements/unknown
  charges/audits. **Existing-field exceptions: none.** New columns/tables require
  exact schema/migration checks outside this projection. Non-SQL state/reservations/
  audits/sidecars/unknown records stay byte-identical. Missing continuity refuses;
  migrations cannot approve their own loss.
- Reconstruct receipt membership/hashes/embedded manifest/install-project IDs/mutable
  paths/configuration hash. Require exact provenance target/schema/manifest/retained
  ID/upgrade/manual profile/canonical cohort time; reject extras. No recovery
  objects/journal/marker/lock; only the empty named object-store directory remains.
- Authenticate complete journals, files/directories/metadata and fresh transaction
  namespaces. Backups exactly match replaced/deleted originals/metadata; no prefix waiver.
- Directories serve exact outputs. Preserve permissions/streams. Disposable probes
  prove new installer modes/inheritance without changing baseline inventories.
  Managed removals cannot discard streams.

External deltas cannot bypass preservation. Absent controls stay LEGACY_RETAINED.
Owner-requested OFF adoption preserves comments; owner merge/fetch applies it.
Non-OFF changes need another PR. Published audits remain readable; unsupported
pre-release confirmations refuse/remain retained. `import_budget_history` merges
pinned state-store/per-host history. Independent `prepare_budget_import`/
`check_budget_import` verify sources/charges/UNKNOWN/deduplication. Qualification
requires published-API history, lost-row injection, import/check/replay/interruption/
concurrency. See `MIGRATION-v1.9.5-to-v1.9.6.md`.

## AWF-258 successor

Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8).
Deletion requires object continuity; preserve residue.

## Recovery/gates

Interrupt staging/writes/receipts/provenance/swaps/cleanup; recover freshly.
Missing/tampered objects refuse; empty-directory protections remain.
Refuse pathname recursive deletion. Retain `.venv.backup-*`/`.venv.cleanup-*`,
staged/canonical runtimes requiring removal and `.agentic-backup/<transaction>`;
all consume runtime/backup space.
Rollback requiring removal refuses before managed mutation. Successful recovery retaining backups retires coordination, returning `status: COMMITTED`,
`transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`,
`retained_directories` and `installation_recovery`. Diagnostic status remains
COMMITTED_CLEANUP_PENDING; inspect its exact inventory.
`.agentic-install/retained-journal.json` exists exactly while unresolved directories
remain; bounded records/indexes preserve source history, paths/sizes (null=unknown)/reasons/remedies.
Valid retention never blocks integrity/capabilities/later transactions. Adopt only managed directories reused without creation/deletion,
recording "adopted by transaction <id>" in `directory-adoptions.json` before
resolving evidence. Report unmanaged/replaced directories; delete none.
Standalone failures retain stages/original errors without cleanup retry; portable
stages use sibling cleanup-pending.json. Archives stay nonrecursive.

Recovery to previous runtime:

1. Obtain exclusive owner control; stop writers.
2. Preserve the journal and its objects; authenticate the backup against the
   journal's `runtime.previous_sha256` tree digest.
3. Move the failed canonical runtime to retained quarantine outside the project,
   leaving the canonical path absent. Preserve/authenticate any staged runtime
   requiring removal there too; never delete evidence.
4. Run trusted external bootstrap with `--dest PROJECT --recover`: existing
   rollback renames backup back and restores managed files, receipt and provenance.
5. Resolve retained diagnostics carefully: the recorded canonical path now holds
   the restored runtime. Do not treat that path as disposable residue or just remove the marker.
6. Verify with matching restored-version tooling, using its isolated canonical runtime.

Bind candidate/artifact/review/trust/publication, one reservation/host and exactly-once
allocation/limits; record commands/platform/interpreter/timings/counts/skips/failures/receipt hashes.

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Require zero exits/integrity true/ACCEPTED configuration/bound hashes. CONFIGURED
proves consistency, not historical execution. Require independent/specialist review,
hosted checks/trust/publication scan/review barrier/owner acceptance. Authorized
rollout needs disposable proof. ACTIVE requires independent trust and observed
receipt-changing adoption merge with accepted raw default-branch bytes; enables no adapters.
