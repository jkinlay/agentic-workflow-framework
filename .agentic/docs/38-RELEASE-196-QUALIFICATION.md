# Release 1.9.6 qualification and preservation

AWF-238, Tier 3, FULL; consolidates AWF-237/AWF-239. No release/rollout/controller/adapter
authority. Failed/missing/stale/unexecuted evidence remains outstanding.

## Baseline and scope

Retain PR #271 recognition/migration and Windows Python 3.12 coverage. Authentic
`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Git proves bytes, not hosted provenance. Published assets/tags/pins/receipts stay
immutable; fixtures use published-installer receipts.

Qualify AWF-236/238/241/242/243/244/247. Inventory implementation/repairs/generated
artifacts/deferrals; retain failures/causes/dispositions/affected reruns. Changed
bytes invalidate passes. Deferred work requires demonstrated failure/owner decision.

## Disposable preservation

Synthetic customization is not CMC evidence. Keep CMC untouched; cancelled personal
Quant Foundry/forbidden HFT targets stay excluded. Adopter copies require accepted
baselines, exclusive ownership, quiescence, approved external storage and separate
authorization. Record repository/ID/default-branch, releases/permissions,
owner files/settings/instructions/CODEOWNERS, routes/pins/opt-outs/Jira scope/budgets,
ledgers/sidecars/reservations/operating history/archives/receipts. Preserve default-OFF choices.

`scripts/qualify_upgrade.py` provides:

- `snapshot --project DISPOSABLE --output BEFORE`: hash bytes/sizes/modes,
  ownership/access control, named streams and directories/root. Exclude only root
  `.git`/`.agentic/.venv`; nested archive Git/runtime/cache names remain evidence.
  Refuse links, incomplete identity and unreadable enumeration.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --output PLAN`: require identical inventories; test main/master/trunk and wrong
  repository/ID/branch refusals.
- After bootstrap, snapshot again. `compare --project DISPOSABLE --before BEFORE
  --after AFTER --allowed-delta DELTA --output COMPARISON` requires exact reviewed
  records, including modes/sizes. Directories end `/`; reject wildcards, unused
  exceptions and identity changes.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: project source/disposable manifests; bootstrap authentic baseline,
  seed synthetic instructions, snapshot/derive policy/upgrade/compare after installed
  checks. Verify downloads against each lock; require network/complete wheel seeds.
  Retain external stdout/stderr/hashes. Optional `--allowed-delta` cannot widen policy.
  Cohort receipts/times cannot reuse approvals. Failure retains inventories/comparison
  and NOT_QUALIFIED/failed-stage; never infer later execution. Success requires
  QUALIFIED_DISPOSABLE_UPGRADE, two CONFIGURED stages, canonical exits zero,
  integrity true, ACCEPTED configuration, bound source/policy/receipt hashes and
  preservation. All `checks` must execute/PASS: `source_managed_membership`,
  `historical_state_continuity`, `named_streams`.

Exclusively create external reports. Emit permission digests only (Windows
owner/group/DACL SDDL; POSIX owner/group/mode), never SIDs/descriptors/owner contents.
Digest named-stream names/contents/sizes on files/directories/root; unreadable,
incomplete, unsupported or missing stream evidence refuses preservation. POSIX
records explicit absence. Unverified permissions fail. Comparison proves neither
installed integrity nor adopter qualification: `evidence_scope:
PRESERVATION_COMPARISON_ONLY` replaces its fixed nested `qualification_status:
NOT_QUALIFIED`. Plans remain NOT_QUALIFIED; only installed reports can qualify.

### Derived migration policy

`prepare_upgrade` verifies target manifest/pin and authenticates baseline membership
against its manifest through known-version recognition. `compare_upgrade` records
rules/hashes/`approved_changes`, exact `changes`, baseline/manifest bindings.

- Require exact managed target hashes/sizes. Removals require authenticated old
  membership/target absence. Baseline files outside old managed membership are owner
  content: newly managed collisions refuse, even for identical target bytes or
  removal before installation. **AGENTS.md is managed**; other owner paths stay identical.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve all
  other values/types/permissions.
- Only recorded supported schema migrations change state; prederive exact bytes,
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
- New directories serve exact outputs. Preserve existing permissions/streams; new
  paths match installer modes/inheritance through disposable probes leaving baseline
  inventories identical. Managed removals cannot discard streams.

External deltas cannot bypass policy. Authentic-library regressions require clean
preservation and refusals for owner/instruction deletion/change, managed mismatch,
unexpected files/archives/objects, configuration/prior-archive/ACL/CODEOWNERS/routing/operating/receipt/provenance
changes; reproduce newly-managed owner collisions, injected settled-row deletion
and owner-stream deletion across upgrade. Synthetic faults prove no production loss.
Run focused suites on the final tree; orchestrator runs installed qualification/review.

## Recovery and final gates

Interrupt staging/writes/receipts/provenance/runtime staging/swap/cleanup; recover in
fresh invocations. Tampered/missing objects refuse diagnostically. Remove only
authenticated installer-created empty directories; refuse replacements/external
contents. Retain unproved runtimes. Windows handles authenticate identity; POSIX
mkdir/rmdir cannot: refuse cleanup, retain directories/journal, report unresolved
restoration. Never relax assertions; preserve archives without recursion.
Failed/unknown stages never establish CONFIGURED/ACTIVE.

Freeze commit/tree/manifests/version/reviews/body/archive/catalog/trust hashes/base-head-body
scan. Generate supported artifacts; rebind changes. Retain one heavy reservation/host,
exactly-once allocation, limits/timeouts/caps. Require focused/source/archive/installed
cohorts, actual Windows Python 3.12, commands/platform/interpreter/timings/counts/skips/reasons/failures/receipt hashes.

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Require exits zero/integrity true/ACCEPTED configuration/bound hashes; ambient/library
checks cannot substitute. CONFIGURED proves consistency, not provenance; status
proves no historical execution. Require current-head independent/specialist review,
hosted checks/trust/publication scan/review barrier/owner acceptance. CI grants no
publication/merge authority. Rollout requires target authorization/disposable proof.
ACTIVE additionally requires independent trust, observed receipt-changing adoption
merge/accepted raw default-branch bytes; enables no adapters. Report lanes/usable
identities/capacity/concurrency separately. Close R2 A1-A7/R3 A1-A8/R4 A1-A7 individually:
evidence or NOT MET.
