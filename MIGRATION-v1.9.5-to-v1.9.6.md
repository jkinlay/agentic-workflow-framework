# Upgrade from 1.9.5 to 1.9.6

Use the separately verified release and its manifest pin. Stop dispatch and establish exclusive ownership. First rehearse on disposable copies of an accepted baseline. Retain an untouched baseline, external preservation reports and recovery records. `--on-conflict backup` retains backups; the default does not.

Run with the prepared bootstrap interpreter (replace uppercase arguments):

```text
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
```

Identity must match existing configuration. Inspect the plan before applying. Unresolved validation commands refuse; other rejected settings remain REJECTED pending owner review. No settings are repaired. Accepted YAML retains comments/format; only its version scalar changes. Unexpected import files name their paths: preserve and move them outside `.agentic/lib`/`.agentic/scripts`, then repeat planning. Unsafe retained state (links, unsupported names or files above 64 MiB) also names its path: preserve it externally, reconcile references and repeat planning. Unrelated product directories are not traversed. Never weaken managed-path containment or discard history.

The published 1.9.5 receipt is recognized. Routing, operating choices, reviewers, pins, budgets and accounting remain. Request new stream/model defaults separately within reviewed ceilings.

Fresh projects default to twelve logical workers and gpt-6.1-sol/high; qualifying simple work uses gpt-6-luna/high. Observed host capacity limits dispatch.

Unknown state remains with a read-only archive copy. Existing archives remain without recursive archival. Conflicting archive destinations refuse before writes. Never reset accounting or edit receipts.

Verify integrity/configuration through the installed isolated interpreter. ACTIVE additionally requires human-reviewed adoption acceptance and independent release trust; it enables no adapter or controller.

Follow the [release qualification checklist](.agentic/docs/38-RELEASE-196-QUALIFICATION.md)
for external snapshots, automatically derived release deltas, real preservation
checks and installed cohorts. Synthetic evidence is not adopter evidence.

If installation is interrupted, keep the transaction marker, journal and recovery
objects intact. Invoke the verified release's `scripts/bootstrap_project.py
--dest DISPOSABLE_PROJECT --recover` in a fresh process, retain its actual result,
then repeat recovery to prove a stable endpoint. A missing/tampered recovery
object or unknown outcome requires investigation; do not remove diagnostics,
rehash corruption, edit receipts or reinstall over the installation. Re-run both
canonical installed checks after recovery. Never infer CONFIGURED/ACTIVE from a
rollback result or a successful library-only check.

Budget adoption is explicit. Missing `budgeting` settings retain the legacy
engine and reviewed limits; upgrading never silently switches budgeting OFF.
Existing explicit settings remain unchanged. The upgrade report and
`.agentic-state/upgrade/budgeting-1.9.6.json` record `LEGACY_RETAINED` or
`EXPLICIT_SETTINGS_RETAINED`, the prior `operating_sha256` and outstanding import
requirements. Neither disposition certifies history completeness or owner adoption.

Before adopting the new controls:

1. Stop legacy dispatch/settlement across every host and worktree. Inventory all
   state-store, routing and per-host budget databases. Checkpoint WALs, retain
   immutable snapshots and pin their SHA-256 digests. Never remove reservations,
   UNKNOWN usage or archives to make admission pass.
2. Before import, capture `prepare_budget_import` evidence from
   `scripts/qualify_upgrade.py`. These are Python APIs, not installed commands;
   there is currently no supported general budget-import CLI. A trusted host
   integration must explicitly call them and retain their results.
3. Select one protected canonical project ledger outside worker/reviewer
   worktrees; configure both controller and review host to use that same path.
   Have the trusted operator call installed `agentic.upgrade.import_budget_history`
   with that destination, exact project ID, complete worktree roots and a mapping
   of source labels to `{path, sha256}`. This is an explicit host API, not an
   automatically enabled adapter. Import every old per-host ledger, even if its
   rows appear duplicated. Exact identities/rows deduplicate with retained proofs;
   conflicts refuse for reconciliation. Retain original databases unchanged.
   Run `check_budget_import` from `scripts/qualify_upgrade.py` afterwards. These
   query-only checks compare source continuity, canonical charges and deduplication
   proofs. Keep private rows outside upstream. Receipts cannot excuse lost usage.
4. If interrupted, replay the identical pinned sources and destination. Import
   charges/proofs commit together; replay never refunds or double-charges.
   Changed sources, unknown usage or conflicting recovery state require
   investigation. Preserve UNKNOWN as unknown, never a retrospective zero.
5. Through the authenticated owner/host operating boundary, explicitly adopt
   accounting/enforcement settings. New dormant limits are 4,000,000 tokens per
   ticket and 1,000,000,000 per project lifetime. A limit alone enables neither
   switch; enforcement requires accounting. Verify persisted settings and totals
   before resuming. Enabled state-store admission validates every retained legacy
   row against its import proof and exact canonical charge, including project,
   ticket, usage and reservation identity. Missing/mismatched proofs or changed
   legacy rows refuse; retain sources and reconcile rather than bypassing the
   guard. Exact admission replay never double-charges. Explicit OFF needs no
   import; `LEGACY_RETAINED` continues using the original engine and limits.

Jira needs explicit controller identity adoption binding the expected project and
repository IDs. Old keys/configuration/receipts grant no writes. Disabled Jira
stays disabled; identity adoption enables no adapter/controller.

Final integrated qualification, independent review and owner release/adopter
acceptance remain outstanding until actually observed. This guide authorizes no
live upgrade or activation.
