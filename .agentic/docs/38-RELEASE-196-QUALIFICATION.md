# Release 1.9.6 qualification and preservation

AWF-238, Tier 3, FULL closure. This is an execution checklist, not a release
acceptance record. AWF-237 and AWF-239 supply consolidated requirements; they
are not additional release tickets. Unexecuted, failed, stale and unavailable
evidence stays outstanding. No live upgrade is authorized by this document.

## Reconcile before freezing

Retain PR #271's published-1.9.5 recognition, migration, upgrade tests and
Windows Python 3.12 CI coverage. The baseline is the authentic `v1.9.5` tag,
peeled commit `8ad399943c8f5e4f17db72f3e321b9365ee27782`, with source manifest
SHA-256 `1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Local tag/blob checks establish local byte identity, not authenticated hosted
publication or independent release provenance. Never replace published pins,
assets, tags or receipts. The baseline helper invokes the published installer
to generate receipts; it stores no additional historical blob corpus.

| Selected ticket | Evidence required on the final candidate |
| --- | --- |
| AWF-236 | A-L identity, reviewed routes/defaults, capacity versus concurrency |
| AWF-238 | Authentic preservation, recovery, complete release qualification |
| AWF-241 | Reporting, permissions and actual platform qualification |
| AWF-242 | Owner choices/history preserved; approved default-OFF accounting migration |
| AWF-243 | Jira defaults, scope and controller-only write boundary |
| AWF-244 | Operating hours, batching and owner review windows |
| AWF-247 | Actual chat ingress/response and separate platform permission |

Inventory the existing diff by required feature, necessary compatibility fix,
generated output and deferred work. For every historical failure retain its
candidate, failing criterion, cause, disposition, correction and affected rerun.
A previous broad pass or focused correction never qualifies a changed candidate.
Do not pull in the 1.9.7 backlog without a demonstrated selected-scope failure
and bounded owner scope decision.

## Disposable preservation cohort

The deterministic fixtures are **SYNTHETIC_OWNER_CUSTOMIZATION**, not CMC
evidence. The accepted CMC installation is untouched. An actual adopter copy
requires its accepted baseline, approved external storage and separate owner
authorization; that evidence is unavailable to the synthetic fixture harness.
Cancelled personal Quant Foundry and forbidden HFT targets stay excluded.

1. Establish exclusive ownership and quiesce writers. Record source and target
   release pins, repository/numeric ID/default branch, accepted configuration,
   permissions and before-state. Include owner instructions/CODEOWNERS, model
   routes/pins, explicit adapter opt-outs, Jira scope, operating history,
   ledger rows, active reservations, archives, receipts and backups. Retain
   raw evidence externally; upstream receives sanitized references only.
2. Use `scripts/qualify_upgrade.py snapshot --project DISPOSABLE_PROJECT
   --output EXTERNAL_BEFORE.json`. It hashes file bytes and permission modes,
   includes ledger sidecars, and excludes Git/runtime/cache trees. It refuses
   links and requires a complete repository identity. It does not acquire a
   live writer's lease or make a database snapshot; quiescence is mandatory.
3. Run `scripts/qualify_upgrade.py plan --source VERIFIED_RELEASE
   --project DISPOSABLE_PROJECT --expected-manifest-sha256 APPROVED_PIN
   --output EXTERNAL_PLAN.json`. Require an unchanged before/after inventory.
   Test main, master and trunk without reading a live/parent repository's
   configuration; wrong repository, numeric ID and branch overrides must refuse.
4. Review the proposed migration and its exact allowed delta before applying
   the supported bootstrap to this disposable copy. Preserve explicit owner
   settings and accounting/evidence history. Never manufacture or edit receipts.
   A managed-path or archive conflict must refuse without project mutations.
5. Take an after snapshot, then run `scripts/qualify_upgrade.py compare
   --project DISPOSABLE_PROJECT --before EXTERNAL_BEFORE.json
   --after EXTERNAL_AFTER.json --allowed-delta EXTERNAL_DELTA.json
   --output EXTERNAL_COMPARISON.json`. The delta is a mapping of relative paths
   to exact `before`/`after` records (file SHA-256, mode and size; directory
   paths end in `/` and carry a mode; null means absent). No prefixes/globs or
   unused exceptions pass. Repository identity
   changes always fail, even when file changes are otherwise allowed.
6. Retain previous archives and recovery objects. Archive copies use the target
   release identity and exclude every existing archive subtree. Exercise
   interruption at staging, managed writes, receipt/provenance replacement,
   runtime swap and committed cleanup. Recover in a fresh invocation, then
   repeat recovery. Missing/tampered objects retain diagnostics and refuse.
   Accept only an exact verified old endpoint or a proven committed new endpoint.
   Failed/unknown stages never establish CONFIGURED or ACTIVE.

The runner only plans and reports; it performs no upgrade, release, activation,
adapter action or owner authorization. Its comparison is preservation evidence,
not installation verification. Report files are created exclusively outside the
source and disposable project; existing reports are never overwritten.

## Freeze and qualify

After all six feature tickets integrate, freeze the exact commit/tree, version,
source/configuration manifests, review inventory, canonical provider-body bytes,
archive hashes, catalog/trust identity and approved qualification plan. Record
the base/head/body publication-scan tuple. Only then regenerate artifacts via
the supported release generators; never hand-edit generated manifests/examples.
Rebind affected evidence after any candidate change.

Use existing `publish_release.py`, `validate_archive.py`, installed-runtime
checks and the heavy-validation reservation mechanism. One accountable heavy
reservation per constrained host; use only already approved exactly-once shard
allocation. Do not increase timeouts, caps, limits or concurrency. Focused
development tests are not release qualification.

| Cohort | Required retained evidence |
| --- | --- |
| Focused regressions | Authentic baseline/preservation, archive collisions, recovery and identity negatives |
| Source | Supported Python/platform checks, security/privacy scans, generated-output consistency |
| Closed archive | Exact artifact hash, extraction/membership/modes, declared source-only skips |
| Configured installed | Actual canonical isolated commands, exits, raw outputs and bound hashes |
| Windows Python 3.12 | Actual 3.12 installed-runtime result; matrix declaration alone is insufficient |
| Review/publication | Independent current-head review, specialists, hosted checks, release/catalog trust, publication scan |

For each cohort retain command, interpreter/platform identity, candidate/artifact
hashes, start/end/duration, discovered/executed counts, skips with reasons,
failures and raw receipt digests. Missing data is unknown, never zero or PASS.
Existing CI already declares Python 3.12.10 and 3.13.3 installed Windows cohorts;
their actual final-candidate runs remain necessary.

Installed checks must execute the target `.agentic/.venv` canonical interpreter:

```text
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT verify-installation
ABS_CANONICAL_RUNTIME -B -I ABS_INSTALLED_SCRIPT --root ABS_PROJECT validate-config
```

Require both exit 0, `integrity_valid: true`, `status: ACCEPTED` and matching
source/policy/receipt hashes. Ambient test Python or a library-only verification
cannot substitute. INSTALLED/CONFIGURED prove receipt/configuration consistency,
not independent release provenance. Status recomputes checks, not prior child
execution.

## Owner and rollout gates

Current-head independent review, applicable specialists, the final review
barrier and explicit owner release acceptance remain mandatory. Retain Tier 3
review rounds/caps and findings. Green CI grants no merge/publication authority.
Publish only through the existing supported release process after owner approval.

Release acceptance and target upgrade authorization are separate. Before any
authorized live upgrade require the matching disposable-copy preservation and
runtime proof, target-specific owner direction and a retained recovery route.
ACTIVE additionally requires independent release trust, an observed
receipt-changing adoption merge and accepted raw AWF/configuration/receipt/
provenance bytes on the fresh default branch. It enables no adapter. Jira,
reporting, chat and controllers retain separate opt-in/qualification/permissions.
Report configured lanes, usable runtime identities, observed host capacity and
actual concurrency separately; twelve simultaneous workers are not required
just to prove twelve labels.

At closeout record each R2 A1-A7, R3 A1-A8 and R4 A1-A7 as MET with exact
evidence or NOT MET. Include limitations, unresolved stages, owner decisions,
release identity and per-target outcome. Do not infer actual rollout from this
checklist, synthetic tests, ticket edits or earlier installation claims.
