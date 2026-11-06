# Release 1.9.6 qualification and preservation

AWF-238, Tier 3, FULL; includes AWF-237/AWF-239. No release/rollout/controller/adapter
authority. Failed/missing/stale/unexecuted evidence stays outstanding.

## Baseline and scope

Retain PR #271 recognition/migration. Published
`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Git proves no hosted provenance. Published assets/tags/pins/receipts stay immutable;
fixtures use published-installer receipts.

Scope follows the current owner contract. Retain failures/dispositions; changes invalidate passes.

## Disposable preservation

Synthetic customization proves no adopter preservation. Forbidden/cancelled targets
stay excluded. Adopter copies require accepted
baselines, exclusive ownership, quiescence, approved external storage and separate
authorization. Record repository/ID/default-branch/releases/permissions,
owner files/settings/instructions/CODEOWNERS, routes/pins/opt-outs/Jira scope/budgets,
ledgers/sidecars/reservations/operating history/archives/receipts. Preserve default-OFF choices.

`scripts/qualify_upgrade.py` provides:

- `snapshot --project DISPOSABLE --output BEFORE`: hash bytes/sizes/modes,
  ownership/access control, named streams and directories/root. Exclude only root
  `.git`/`.agentic/.venv`; nested archive Git/runtime/cache names remain evidence.
  Refuse links, incomplete identity and unreadable enumeration.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH
  --output PLAN`: require identical inventories; test main/master/trunk and wrong
  repository/ID/branch refusals.
- Keep an untouched disposable baseline matching BEFORE. After bootstrap, snapshot
  again. `compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE
  --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON`
  runs real preservation checks and generates `approved_delta` from verified release
  identity; owners inspect `remaining_changes`. Optional `--allowed-delta` only
  constrains this policy. Without baseline/release inputs checks are NOT_RUN and
  preservation fails. Owner/history loss cannot be waived.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: require clean committed source and a current manifest; bind
  commit/tree/pin without regenerating candidate manifests. Bootstrap authentic baseline,
  seed instructions, populated legacy ledger and published-API accounting history;
  snapshot/derive policy/upgrade/compare after installed
  checks. Verify downloads against each lock; require network/complete wheel seeds.
  Retain external stdout/stderr/hashes. Optional `--allowed-delta` cannot widen policy.
  Never reuse approvals. Failures retain inventories/comparison
  and NOT_QUALIFIED/failed-stage; later execution remains unproved. Success requires
  QUALIFIED_DISPOSABLE_UPGRADE, two CONFIGURED stages, canonical exits zero,
  integrity true, ACCEPTED configuration, bound source/policy/receipt hashes and
  preservation. All `checks` must execute/PASS: `source_managed_membership`,
  `historical_state_continuity`, `named_streams`.
  Report exercised state migrations and continuity paths; a missing legacy-ledger
  migration/SQL continuity check refuses qualification.

Exclusively create external reports; emit permission/stream digests, never owner
contents or SIDs. Missing/unverified permission or stream evidence fails; POSIX
records explicit stream absence. Comparison has `evidence_scope:
PRESERVATION_COMPARISON_ONLY`; it proves neither installed integrity nor
qualification. Plans stay NOT_QUALIFIED; only installed reports qualify.

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
- New directories serve exact outputs. Preserve existing permissions/streams; new
  paths match installer modes/inheritance through disposable probes leaving baseline
  inventories identical. Managed removals cannot discard streams.

External deltas cannot bypass policy. Reproduce owner/managed/configuration/history/
permission/receipt/provenance loss, collisions and stream deletion. Budget disposition
preserves legacy behavior; retained settings require manual owner adoption and
AWF-242 load confirmation. Explicit `import_budget_history` merges pinned
state-store/per-host history into one shared ledger. Independently capture
`prepare_budget_import`; `check_budget_import` verifies sources, canonical
charges/UNKNOWN values and deduplication proofs. Test published API-generated
history, lost-row injection, interruption/replay and concurrency. Installed
qualification requires this import/check/replay. Follow
[owner adoption](../../MIGRATION-v1.9.5-to-v1.9.6.md), including Jira identity.

## Recovery and final gates

Interrupt staging/writes/receipts/provenance/runtime swaps/cleanup; recover in fresh
invocations. Missing/tampered objects refuse. Remove only authenticated installer
empty directories; retain replacements, external contents and unproved runtimes.
POSIX cleanup without authenticated identity retains directories/journal and reports
unresolved restoration. Preserve archives without recursion. Failed/unknown stages
prove no CONFIGURED/ACTIVE.

Freeze candidate/artifact/review/trust/publication bindings; rebind changes. Retain
one heavy reservation/host, exactly-once allocation and approved limits. Record
focused/source/archive/installed cohorts, Windows Python 3.12, commands/platform/
interpreter/timings/counts/skips/failures/receipt hashes.

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Require exits zero/integrity true/ACCEPTED configuration/bound hashes. CONFIGURED
proves consistency; status proves no historical execution. Require independent/
specialist review, hosted checks/trust/publication scan/review barrier/owner acceptance.
Rollout requires target authorization/disposable proof. ACTIVE additionally needs
independent trust and observed receipt-changing adoption merge with accepted raw
default-branch bytes; enables no adapters. Report lanes/capacity/concurrency separately.
Close each acceptance criterion: evidence or NOT MET.
