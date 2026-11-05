# Release 1.9.6 qualification and preservation

AWF-238, Tier 3, FULL. AWF-237 and AWF-239 requirements are consolidated here.
This checklist grants no release, live-upgrade, controller or adapter authority.
Failed, missing, stale and unexecuted evidence remains outstanding.

## Baseline and scope

Retain PR #271's published-baseline recognition, migration and Windows Python
3.12 coverage. Authentic `v1.9.5` peels to commit
`8ad399943c8f5e4f17db72f3e321b9365ee27782`; its manifest SHA-256 is
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Local Git verification establishes byte identity, not independent hosted
provenance. Never alter published assets, pins, tags or historical receipts.
The fixture invokes the published installer to generate genuine receipts.

Qualify all seven selected tickets: AWF-236 identities/routes, AWF-238 upgrades
and release, AWF-241 reporting, AWF-242 accounting, AWF-243 Jira defaults/scope,
AWF-244 hours/batching and AWF-247 chat. Inventory retained implementation,
necessary compatibility repairs, generated artifacts and deferred work.
Preserve each historical failure, cause, disposition and affected rerun.
A previous candidate's pass cannot qualify changed bytes. Bring deferred work
into scope only for a demonstrated selected-scope failure and owner decision.

## Disposable preservation

Synthetic customization is not CMC evidence. Keep the accepted CMC installation
untouched. Cancelled personal Quant Foundry and forbidden HFT targets remain
excluded. Actual adopter copies need accepted baselines, exclusive ownership,
quiescence, approved external storage and separate authorization.

Record repository/numeric ID/default branch, releases, permissions, owner files,
CODEOWNERS, instructions, routes/pins, adapter opt-outs, Jira scope, budgets,
ledgers/sidecars, reservations, operating history, archives and receipts.
Preserve explicit owner settings and approved default-OFF accounting choices.

Use `scripts/qualify_upgrade.py` with these subcommands:

- `snapshot --project DISPOSABLE --output EXTERNAL_BEFORE`: hash bytes, modes
  and directories; exclude Git/runtime/cache trees; refuse links and incomplete
  identity. Quiescence is mandatory; this is no live database snapshot.
- `plan --source VERIFIED_RELEASE --project DISPOSABLE
  --expected-manifest-sha256 PIN --output EXTERNAL_PLAN`: require unchanged
  inventories. Exercise main/master/trunk and wrong repository/ID/branch refusals.
- After reviewing the exact migration delta and running supported bootstrap,
  snapshot again. `compare --project DISPOSABLE --before BEFORE --after AFTER
  --allowed-delta DELTA --output EXTERNAL_COMPARISON` requires exact before/after
  records, including modes and sizes. Directory paths end in `/`. No wildcard,
  unused exception or changed repository identity passes.
- `installed --source SOURCE_CHECKOUT --work-dir NEW_EXTERNAL_DIRECTORY
  --wheelhouse LOCKED_WHEELS --output EXTERNAL_REPORT` projects the current
  source into disposable storage, generates only that projection's manifests,
  bootstraps authentic published bytes and upgrades through supported bootstrap.
  Provision each release lock with hash-checked pip downloads, using the optional
  wheelhouse as a local seed. Network access or a complete seed is required.
  Retain external stage stdout/stderr and their report hashes. Success prints
  `QUALIFIED_DISPOSABLE_UPGRADE`, two CONFIGURED stages, canonical check exits
  zero, integrity true, ACCEPTED configuration and source/policy/receipt hashes.
  Failure prints NOT_QUALIFIED and the failed stage; never infer later checks ran.

Reports are exclusively created outside source/project. Comparison establishes
preservation, not installed integrity or actual adopter qualification.

Interrupt object staging, managed writes, receipt/provenance replacement,
runtime staging/swap and committed cleanup. Recover in fresh invocations and
repeat deterministically. Missing/tampered objects retain diagnostics and refuse.
Remove only authenticated installer-created empty directories; refuse replacements
or external contents. Preserve unproved partial runtimes for inspection.
Old archives remain intact without recursive archival. Failed/unknown stages
never establish CONFIGURED or ACTIVE.

## Final candidate and owner gates

After feature integration, freeze commit/tree, manifests, version, review
inventory, canonical body, archive/catalog/trust hashes and base/head/body scan.
Generate native artifacts through supported tooling. Rebind affected evidence
after changes. Keep one accountable heavy reservation per constrained host;
retain existing exactly-once allocation, limits, timeouts and review caps.

Require focused, source, closed-archive and configured-installed cohorts,
including actual Windows Python 3.12. Retain commands, interpreter/platform,
start/end/duration, counts, skips/reasons, failures and raw receipt hashes.
Canonical installed commands are:

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Require both exit zero, integrity true, ACCEPTED configuration and bound hashes.
Ambient Python and library checks cannot substitute. CONFIGURED proves receipt
consistency, not release provenance. Status recomputation proves no prior child run.

Require current-head independent review, specialists, hosted checks, release
trust, publication scan, final review barrier and explicit owner acceptance.
Green CI grants no publication/merge authority. Live rollout needs separate
target authorization and matching disposable proof. ACTIVE additionally needs
independent release trust, observed receipt-changing adoption merge and accepted
raw default-branch bytes. It enables no adapters. Report configured lanes,
usable identities, observed capacity and demonstrated concurrency separately.
Close out R2 A1-A7, R3 A1-A8 and R4 A1-A7 individually with evidence or NOT MET.
