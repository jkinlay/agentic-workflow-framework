# Release 1.9.6 qualification and preservation

AWF-238, Tier 3, FULL. AWF-237 and AWF-239 requirements are consolidated here.
This checklist grants no release, live-upgrade, controller or adapter authority.
Failed, missing, stale and unexecuted evidence remains outstanding.

## Baseline and scope

Retain PR #271's published-baseline recognition, migration and Windows Python
3.12 coverage. Authentic `v1.9.5` peels to commit
`8ad399943c8f5e4f17db72f3e321b9365ee27782`; its manifest SHA-256 is
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Git proves byte identity, not hosted provenance. Never alter published assets,
pins, tags or receipts. The fixture uses the published installer for receipts.

Qualify all seven selected tickets: AWF-236 identities/routes, AWF-238 upgrades
and release, AWF-241 reporting, AWF-242 accounting, AWF-243 Jira defaults/scope,
AWF-244 hours/batching and AWF-247 chat. Inventory implementation, repairs,
generated artifacts and deferrals. Retain historical failures, causes,
dispositions and affected reruns. Changed bytes invalidate prior passes.
Deferred work needs a demonstrated scope failure and owner decision.

## Disposable preservation

Synthetic customization is not CMC evidence. Keep CMC untouched; cancelled
personal Quant Foundry and forbidden HFT targets stay excluded. Adopter copies
need accepted baselines, exclusive ownership, quiescence, approved external
storage and separate authorization.

Record repository/numeric ID/default branch, releases, permissions, owner files,
CODEOWNERS, instructions, routes/pins, adapter opt-outs, Jira scope, budgets,
ledgers/sidecars, reservations, operating history, archives and receipts.
Preserve explicit owner settings and approved default-OFF accounting choices.

Use `scripts/qualify_upgrade.py` with these subcommands:

- `snapshot --project DISPOSABLE --output EXTERNAL_BEFORE`: hash bytes, modes,
  ownership/access control and directories (including the project root); exclude
  only root `.git` and `.agentic/.venv`. Nested archive `.git`, `.venv` and
  `__pycache__` names remain evidence. Refuse links and incomplete
  identity and unreadable directories. Require quiescence.
- `plan --source VERIFIED_RELEASE --project DISPOSABLE
  --expected-manifest-sha256 PIN --output EXTERNAL_PLAN`: require unchanged
  inventories. Exercise main/master/trunk and wrong repository/ID/branch refusals.
- After reviewing the exact migration delta and running supported bootstrap,
  snapshot again. `compare --project DISPOSABLE --before BEFORE --after AFTER
  --allowed-delta DELTA --output EXTERNAL_COMPARISON` requires exact before/after
  records including modes/sizes. Directory paths end in `/`. Reject wildcards,
  unused exceptions and identity changes. Hash Windows owner/group/DACL SDDL
  or POSIX owner/group/mode; never report raw SIDs/descriptors. Missing/unverified
  permissions block preservation regardless of accepted deltas.
- `installed --source SOURCE_CHECKOUT --work-dir NEW_EXTERNAL_DIRECTORY
  --wheelhouse LOCKED_WHEELS --output EXTERNAL_REPORT` projects the current
  source into disposable storage, generates only that projection's manifests,
  bootstraps authentic published bytes and upgrades through supported bootstrap.
  Hash-check pip downloads against each lock; network or complete wheel seeds
  are required. Retain external stdout/stderr and report hashes. Seed synthetic
  owner instructions, snapshot before upgrade and compare after installed checks.
  Supply reviewed exact `--allowed-delta DELTA`; default accepts no changes.
  Never auto-approve observed changes. Receipt identities/times are cohort-specific;
  prior deltas cannot approve fresh runs. Unaccepted changes retain before/after
  and comparison evidence as NOT_QUALIFIED. Success prints
  `QUALIFIED_DISPOSABLE_UPGRADE`, two CONFIGURED stages, canonical check exits
  zero, integrity true, ACCEPTED configuration, source/policy/receipt hashes and
  an accepted preservation comparison with verified permissions for every entry.
  Failure prints NOT_QUALIFIED and the failed stage; never infer later checks ran.

Reports are exclusively created outside source/project. Comparison establishes
preservation, not installed integrity or actual adopter qualification.

Interrupt object staging, managed writes, receipt/provenance replacement,
runtime staging/swap and committed cleanup. Recover in fresh invocations and
repeat deterministically. Missing/tampered objects retain diagnostics and refuse.
Remove only authenticated installer-created empty directories; refuse replacements
or external contents. Preserve unproved partial runtimes for inspection.
Windows captures identity through atomic create-only handles denying replacement;
cleanup verifies its deleting handle. POSIX mkdir/rmdir cannot bind creation or
deletion to an inode atomically: record no authority, refuse cleanup and retain
directories/journal. Exact-restoration assertions remain unresolved on POSIX;
never relax them to claim qualification.
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
