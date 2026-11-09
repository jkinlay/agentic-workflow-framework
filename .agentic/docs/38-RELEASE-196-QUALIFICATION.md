# Release 1.9.6 qualification and preservation

AWF-238: Tier 3, owner-approved FULL rebind with one named exception: automatic
deletion deferred to [AWF-258](#awf-258-successor). Retain pre-F5 finding #1
(object-continuity deletion safety). No rollout/adapter authority.

## Baseline and scope

`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Assets/tags/pins/receipts stay immutable; Git proves no publication.

## Disposable preservation

Require accepted baselines/exclusive ownership/quiescence/authorization. Record repository/ID/default-branch/releases/permissions,
owner files/settings/instructions/CODEOWNERS, routes/pins/opt-outs/Jira scope/budgets,
ledgers/sidecars/reservations/operating history/archives/receipts. Defaults stay OFF.

`scripts/qualify_upgrade.py` provides:

- `snapshot --project DISPOSABLE --output BEFORE`: hash bytes/sizes/modes,
  ownership/access control, streams and directories/root; separately bind canonical runtime contents. Exclude root `.git` and unrelated product trees.
  Preserve root files and nested archives; managed/state links, incomplete identity
  and unreadable enumeration refuse. Snapshot before apply.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH
  --output PLAN`: require identical inventories and matching identity.
- Retain BEFORE; resnapshot after bootstrap. `compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE
  --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON`
  derives `approved_delta` from verified release identity; inspect `remaining_changes`.
  `--allowed-delta` only constrains policy. Missing baseline/release inputs fail
  preservation (NOT_RUN). Owner/history loss cannot be waived.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: require clean committed source and a current manifest; bind
  commit/tree/pin without regeneration. Bootstrap authentic baseline,
  seed instructions, populated legacy ledger and published-API accounting history;
  snapshot/derive policy/upgrade with `--on-conflict backup`/compare after installed
  checks. Verify downloads against each lock; require complete wheels.
  Failures retain inventories/comparison with NOT_QUALIFIED/failed-stage. Require
  QUALIFIED_DISPOSABLE_UPGRADE, two CONFIGURED stages, canonical exits zero,
  integrity true, ACCEPTED configuration, bound source/policy/receipt hashes and
  preservation. All `checks` must execute/PASS: `source_managed_membership`,
  `historical_state_continuity`, `named_streams`.
  Missing migration/SQL continuity refuses qualification.

Reports contain digests, never owner contents/SIDs. Unverified permissions/streams fail. `PRESERVATION_COMPARISON_ONLY` proves no installed qualification.

### Derived migration policy

`prepare_upgrade` authenticates target manifest/pin and baseline membership. `compare_upgrade` binds rules/hashes/`approved_changes`, exact `changes` and baseline/manifest.

- Require exact managed target hashes/sizes. Removals require authenticated old
  membership/target absence. Outside-baseline-membership files are owner
  content: collisions refuse, even for identical bytes or
  removal before installation. **AGENTS.md is managed**; other owner paths stay identical.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve all
  other values/types/permissions.
- Only recorded schema migrations change state; prederive exact bytes,
  source/target hashes/actions. `state_archive_plan` derives named `upgrade-to-<target>`
  copies/manifest with exact content/source bindings/read-only mode. Prior archives
  stay identical; prefixes authorize nothing.
- Independently snapshot state before migrations. Query-only in-memory SQLite
  reads call no migration/initializer/upgrade validator. Preserve every historical
  table/column/rowid/typed value/duplicate count: reservation identities, settlements,
  unknown charges, audits. **Existing-field exceptions: none.** New SQL columns/tables
  remain outside this projection but require exact migration/schema checks. Non-SQL
  state/reservations/audits/sidecars/unknown records stay byte-identical. Missing continuity
  evidence refuses; migrations cannot approve their own loss.
- Reconstruct receipt membership/hashes/embedded manifest/install/project IDs/mutable
  paths/configuration hash. Provenance requires exact target/schema/manifest/retained
  ID/upgrade/manual profile/canonical cohort time. Extra fields fail. No recovery
  objects/journal/marker/lock remain; permit only the empty named object-store directory.
- Backups must exactly match this upgrade's replaced/deleted originals and expected
  metadata under one fresh transaction UUID; no prefix waiver.
- Directories serve exact outputs. Preserve existing permissions/streams; new
  paths match installer modes/inheritance through disposable probes leaving baseline
  inventories identical. Managed removals cannot discard streams.

External deltas cannot bypass preservation. Disposition
keeps absent controls LEGACY_RETAINED without new gates. Owner-requested adoption
adds only dormant OFF controls on the upgrade/settings PR branch, preserving
text/comments; owner merge and fetch apply it. Non-OFF values require a later
settings PR. Supported 1.9.5 audits remain valid;
unsupported pre-release confirmation records refuse plainly and remain retained. Explicit `import_budget_history` merges pinned
state-store/per-host history into one shared ledger. Independently capture
`prepare_budget_import`; `check_budget_import` verifies sources, canonical
charges/UNKNOWN values and deduplication proofs. Test published API-generated
history, lost-row injection, interruption/replay and concurrency. Installed
qualification requires this import/check/replay. See `MIGRATION-v1.9.5-to-v1.9.6.md`.

## AWF-258 successor

Automatic deletion remains deferred to AWF-258: prove object continuity before
removal. Until then, preserve residue for owner inspection/recovery.

## Recovery and final gates

Interrupt staging/writes/receipts/provenance/swaps/cleanup; recover freshly.
Missing/tampered objects refuse; empty-directory protections remain.
Refuse pathname recursive deletion. Retain `.venv.backup-*`/`.venv.cleanup-*`,
staged/canonical runtimes requiring removal and `.agentic-backup/<transaction>`;
all consume runtime/backup space.
Rollback requiring removal refuses before managed mutation. Forward commits
retire coordination, reporting COMMITTED_CLEANUP_PENDING backups.
`.agentic-install/retained-journal.json` exists exactly while unresolved directories
remain; bounded records/indexes preserve source history, paths/sizes (null=unknown)/reasons/remedies.
Valid retention never blocks integrity checks/capability admission or later transactions. Transactions adopt only managed directories reused without creation/deletion,
recording "adopted by transaction <id>" in `directory-adoptions.json` before
resolving evidence. Unmanaged/replaced directories remain reported. Unadopted residue needs owner inspection/recovery; preserve needed bytes. No retained directory is deleted.
Standalone failures retain stages/original errors without cleanup retry; portable
stages use sibling cleanup-pending.json. Archives stay nonrecursive.

Freeze/rebind candidate/artifact/review/trust/publication bindings. Retain one reservation/host, exactly-once allocation/approved limits; record
commands/platform/interpreter/timings/counts/skips/failures/receipt hashes.

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Require exits zero/integrity true/ACCEPTED configuration/bound hashes. CONFIGURED
proves consistency, never historical execution. Require independent/
specialist review, hosted checks/trust/publication scan/review barrier/owner acceptance.
Rollout requires authorization/disposable proof. ACTIVE needs
independent trust and observed receipt-changing adoption merge with accepted raw
default-branch bytes; enables no adapters.
