# Release 1.9.6 qualification and preservation

AWF-238, Tier 3, FULL; consolidates AWF-237/AWF-239. No release, rollout,
controller or adapter authority. Failed/missing/stale/unexecuted evidence remains
outstanding.

## Baseline and scope

Retain PR #271 recognition/migration and Windows Python 3.12 coverage. Authentic
`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Git proves bytes, not hosted provenance. Published assets/tags/pins/receipts remain
immutable; fixtures use published-installer receipts.

Qualify AWF-236/238/241/242/243/244/247. Inventory implementation, repairs,
generated artifacts and deferrals. Retain failures, causes, dispositions and
affected reruns. Changed bytes invalidate passes. Deferred work requires
demonstrated scope failure and owner decision.

## Disposable preservation

Synthetic customization is not CMC evidence. Keep CMC untouched; cancelled
personal Quant Foundry/forbidden HFT targets remain excluded. Adopter copies
require accepted baselines, exclusive ownership, quiescence, approved external
storage and separate authorization. Record repository/ID/default branch,
releases, permissions, owner files/settings, instructions, CODEOWNERS, routes/pins,
opt-outs, Jira scope, budgets, ledgers/sidecars, reservations, operating history,
archives and receipts. Preserve default-OFF choices.

Use `scripts/qualify_upgrade.py`:

- `snapshot --project DISPOSABLE --output BEFORE`: hash bytes/sizes/modes,
  ownership/access control and directories including root. Exclude only root
  `.git`/`.agentic/.venv`; nested archive Git/runtime/cache names remain evidence.
  Refuse links, incomplete identity and unreadable enumeration.
- `plan --source RELEASE --project DISPOSABLE --expected-manifest-sha256 PIN
  --output PLAN`: require identical inventories; exercise main/master/trunk and
  wrong repository/ID/branch refusals.
- After supported bootstrap, snapshot again. `compare --project DISPOSABLE
  --before BEFORE --after AFTER --allowed-delta DELTA --output COMPARISON`
  requires exact externally reviewed records including modes/sizes. Directories
  end in `/`; reject wildcards, unused exceptions and identity changes.
- `installed --source CHECKOUT --work-dir NEW_DISPOSABLE --wheelhouse WHEELS
  --output REPORT`: project source, generate only disposable manifests, bootstrap
  authentic baseline, seed synthetic instructions, snapshot, derive policy,
  upgrade and compare after installed checks. Verify downloads against each lock;
  require network/complete wheel seeds. Retain external stdout/stderr/hashes.
  Optional `--allowed-delta DELTA` cannot widen policy. Cohort-specific
  receipts/times cannot reuse approvals. Failure retains inventories/comparison,
  prints NOT_QUALIFIED/failed stage; never infer later execution. Success requires
  QUALIFIED_DISPOSABLE_UPGRADE, two CONFIGURED stages, canonical exits zero,
  integrity true, ACCEPTED configuration, source/policy/receipt hashes and accepted
  preservation with verified permissions throughout.

Exclusively create reports outside source/project. Emit only permission digests
(Windows owner/group/DACL SDDL; POSIX owner/group/mode), never raw SIDs/descriptors
or owner contents. Unverified permissions fail. Comparison alone proves neither
installed integrity nor adopter qualification.

### Derived migration policy

`prepare_upgrade` verifies target manifest bytes/pin and authenticates baseline
through installer known-version recognition. `compare_upgrade` records approved
rules/hashes in `approved_changes`, exact before/after records in `changes`, and
baseline/manifest bindings.

- Managed membership derives from manifest/installer ownership exceptions;
  require exact target hashes/sizes. Removals require authenticated old membership
  and target absence. **AGENTS.md is managed**. CODEOWNERS, instructions, routing,
  operating settings and other owner paths remain identical.
- Parse PROJECT_CONFIG: only `template.expected_workflow_version` changes.
  Every other value/type and all permissions remain identical.
- Only recorded supported schema migrations change state; derive exact bytes
  beforehand, record source/target hashes/schema actions. Derive named
  `upgrade-to-<target>` copies/manifest using `state_archive_plan`; require exact
  content/source bindings/read-only mode. Prior archives stay identical. Prefixes
  authorize nothing.
- Reconstruct receipt membership/hashes, embedded manifest, retained install/project
  IDs, mutable paths and configuration hash. Provenance requires exact
  target/schema/manifest, retained ID, upgrade/manual profile and canonical cohort
  time. Extra fields fail. No recovery-object files/journal/marker/lock remain;
  only the named empty object-store directory is allowed.
- New directories serve exact outputs. Existing permissions stay identical;
  new paths match installer modes/parent inheritance, measured with disposable
  probes leaving baseline inventories identical.

Standalone `compare` exceptions cannot bypass installed policy. Authentic-library
regressions require clean preservation and disposable-copy refusals for owner
deletion/change, manifest mismatch, unexpected files, extra config changes,
prior-archive changes, Windows ACL additions, archives, objects, CODEOWNERS,
routing, operating, receipts and provenance.

## Recovery and final gates

Interrupt staging, writes, receipts/provenance, runtime staging/swap and cleanup;
recover deterministically in fresh invocations. Tampered/missing objects refuse
with diagnostics. Remove only authenticated installer-created empty directories;
refuse replacements/external contents. Retain unproved runtimes. Windows handles
authenticate creation/deletion identity. POSIX mkdir/rmdir cannot: refuse cleanup,
retain directories/journal, report unresolved exact restoration. Never relax
assertions. Preserve archives without recursion. Failed/unknown stages never
establish CONFIGURED/ACTIVE.

Freeze commit/tree, manifests/version, reviews/body, archive/catalog/trust hashes
and base/head/body scan. Generate supported artifacts; rebind changed evidence.
Retain one heavy reservation/host, exactly-once allocation, limits/timeouts/caps.
Require focused/source/archive/installed cohorts, actual Windows Python 3.12,
commands/platform/interpreter, timings/counts/skips/reasons/failures/receipt hashes.

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Both require exit zero, integrity true, ACCEPTED configuration, bound hashes.
Ambient/library checks cannot substitute. CONFIGURED proves consistency, not
provenance; status proves no historical execution. Require current-head
independent/specialist review, hosted checks, trust, publication scan, review
barrier and owner acceptance. CI grants no publication/merge authority. Rollout
requires target authorization/disposable proof. ACTIVE additionally needs
independent trust, observed receipt-changing adoption merge and accepted raw
default-branch bytes; it enables no adapters. Separately report lanes, usable
identities, capacity/concurrency. Close R2 A1-A7/R3 A1-A8/R4 A1-A7 individually
with evidence or NOT MET.
