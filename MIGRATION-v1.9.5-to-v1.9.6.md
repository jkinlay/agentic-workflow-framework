# Upgrade from 1.9.5 to 1.9.6

Verify release/pin; quiesce/exclusively own; rehearse disposably; retain untouched authentic baseline/external reports.

[Adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md).

Before managed commits/checkouts merge `.agentic/templates/installed.gitattributes`
into root `.gitattributes`; preserve product rules. Prevent `core.autocrlf=true` corruption; inspect nested/info overrides. Never renormalize unrelated files. Reverify fresh checkouts.

`PYTHON` is the absolute interpreter in a separate bootstrap environment (`python -m venv BOOTSTRAP`). Install verified dependencies with `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. WHEELS satisfies the lock; uppercase paths are absolute.

Snapshot **before apply**; retain untouched quiescent BASELINE:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Comparison preserves **since BEFORE**: exact replaced/deleted originals/metadata/receipt/provenance; no arbitrary additions. Default conflicts create no backups.

Inspect plan: identity matches configuration; unresolved validation commands refuse. Rejected settings await owner review; incomplete enabled Jira identity alone may become accepted with JIRA_IDENTITY_UNBOUND. Plan reports compatibility; unchanged settings refuse writes pending explicit identity adoption. Disabled Jira remains disabled; adapters/controllers stay OFF.

YAML changes only version; preserve comments/format; refusals name paths/remedies. Externally preserve unexpected import/schema files; correct/move outside managed directories. Preserve linked targets externally, restore contained independent regular objects and reconcile references before replanning. Preserve history/containment; exclude unrelated product trees.

Preserve routes/operating/reviewers/pins/budgets/accounting. Archive unknown state read-only; prior archives nonrecursively. Conflicting destinations refuse; never reset accounting/edit receipts.

`RUNTIME` is the absolute installed `.agentic/.venv/Scripts/python.exe` on Windows or `.agentic/.venv/bin/python` elsewhere. `WORKFLOW` is the absolute installed `.agentic/scripts/workflow.py`:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE verify-installation
RUNTIME -B -I WORKFLOW --root DISPOSABLE validate-config
RUNTIME -B -I WORKFLOW --root DISPOSABLE status
```

CONFIGURED requires executed zero exits/integrity true/ACCEPTED configuration/matching digests. ACTIVE additionally requires independent release trust/observed human-reviewed adoption acceptance; adapters/controllers stay OFF. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md).

Retain marker/journal/recovery objects. Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8). Refusal precedes managed mutation; retries cannot clear it. Stay quiescent; never reinstall over unknown/tampered state.

Owner recovery:

1. Obtain exclusive owner control; stop writers.
2. Preserve journal/objects/endpoints/bytes/metadata; use `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`: require exit 0/`matches:true`; mismatch/error stops. PYTHON=external bootstrap Python; RELEASE=the verified journal-writing candidate release. Backup: recorded-path/`runtime.previous_sha256`.
3. If no backup exists and canonical `.agentic/.venv` matches `runtime.previous_sha256`, quarantine only the stage outside the project; leave the canonical runtime untouched. Otherwise quarantine failed canonical/staged runtimes outside the project, leaving canonical absent. Authenticate stages with the same `runtime-hash` command and `runtime.new_sha256`; use canonical/`runtime.previous_sha256` for the no-backup check.
4. Run candidate release's external bootstrap `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`; restore provable managed files/receipt/provenance/authenticated backup.
5. For legacy `.agentic-backup/<txid>` refusal, authenticate journal-recorded files/metadata; quarantine that exact directory externally; rerun `--recover`. Never edit journal/marker.
6. Reconcile rows: record authenticated restoration/quarantine or deliberate retention. The canonical RECOVERY_REFUSED row names restored runtime; it is not automatically adopted. Resolve via step 7; never discard runtime/remove marker.
7. After resolving every row, archive index/referenced evidence/path-by-path resolutions; move only the resolved `retained-journal.json` outside the project. No owner retirement CLI exists. Preserve evidence objects/adoption audit.
8. Run `RUNTIME -B -I WORKFLOW --root PROJECT` with `verify-installation`, `validate-config`, then `status`. RUNTIME=restored `.agentic/.venv/bin/python` (`.agentic/.venv/Scripts/python.exe` on Windows); WORKFLOW=restored `.agentic/scripts/workflow.py`; use absolute paths. Restored 1.9.5 tooling prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE state.

Forward recovery with retained backups returns `status: COMMITTED`, `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories`, `installation_recovery`; nested diagnostic status remains COMMITTED_CLEANUP_PENDING. Inspect exact inventory; recovery authorizes no deletion.

Recovery recognises only exact sibling temporaries derived from journal transaction identity/naming scheme/targets. Unrecorded look-alikes/managed additions refuse, retaining marker. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. The upgrade report/state disposition records the prior hash and EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness or adoption.

Request AWF-chat adoption from fetched default: preserve OPERATING_CONFIG.yaml comments/text; add accounting/enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime in the upgrade PR. `operating set --adopt-budgeting --instruction "Adopt dormant budget controls"` prepares this without --set choices/enabling controls. Restricted publication prepares owner branch/body. Owner review/merge/fetch applies; unmerged edits do nothing. Non-OFF changes require another settings PR. Git/PR history replaces confirmation transactions.

Before enabling: stop legacy writers; inventory state-store/routing/per-host databases,
checkpoint WALs and retain immutable snapshots/SHA-256 pins. Trusted hosts call installed `agentic.upgrade.import_budget_history` with one protected canonical ledger outside worktrees/exact project ID/complete worktree roots/source-label
`{path, sha256}` mappings. Both hosts select it. Capture independent
`prepare_budget_import` before/`check_budget_import` after via `scripts/qualify_upgrade.py`.
Preserve sources/reservations/UNKNOWN usage/external results. Exact replay deduplicates;
reconcile conflicts. Enabled admission retains legacy import/charge checks; OFF
adoption needs no import; declining retains legacy behavior.

Published 1.9.5 operating records verify without confirmation; unsupported pre-release fields refuse. Preserve records; restore supported baseline before upgrade.
[Budgeting](.agentic/docs/39-BUDGETING.md).

Rollout unauthorized.
