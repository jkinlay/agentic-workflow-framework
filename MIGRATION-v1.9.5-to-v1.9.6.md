# Upgrade from 1.9.5 to 1.9.6

Verify release/pin; stop dispatch/settlement; obtain exclusive ownership. Rehearse disposably, preserving untouched authentic baseline/external reports.

See [adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md).

Before committing/checking out managed bytes, merge `.agentic/templates/installed.gitattributes`
into root `.gitattributes`; preserve product rules. Prevent `core.autocrlf=true` corruption; inspect nested/info overrides. Never renormalize unrelated files. Reverify fresh checkouts.

`PYTHON` is the absolute interpreter in a separate bootstrap environment (`python -m venv BOOTSTRAP`). Install verified dependencies with `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. WHEELS must satisfy the lock; uppercase arguments require absolute paths.

Snapshot **before apply**; retain untouched quiescent BASELINE:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Comparison proves preservation **since BEFORE**: exact replaced/deleted originals/metadata, no arbitrary additions, receipt/provenance originals required. Default conflict handling creates no backups.

Inspect plan: identity must match existing configuration; unresolved validation commands refuse. Rejected settings await owner review; incomplete enabled Jira identity alone may become accepted with JIRA_IDENTITY_UNBOUND. Plan reports compatibility; settings remain unchanged; writes refuse until explicit identity adoption. Disabled Jira remains disabled; adapters/controllers stay OFF.

Accepted YAML changes only version, preserving comments/format; refusals name paths/remedies. Preserve unexpected import/schema files externally; correct or move them outside managed directories. Preserve linked targets externally, restore contained independent regular objects and reconcile references before replanning. Preserve history/containment; unrelated product directories are not walked.

Preserve routing/operating choices/reviewers/pins/budgets/accounting. Archive unknown state read-only; preserve prior archives nonrecursively. Conflicting destinations refuse; never reset accounting/edit receipts.

`RUNTIME` is the absolute installed `.agentic/.venv/Scripts/python.exe` on Windows or `.agentic/.venv/bin/python` elsewhere. `WORKFLOW` is the absolute installed `.agentic/scripts/workflow.py`:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE verify-installation
RUNTIME -B -I WORKFLOW --root DISPOSABLE validate-config
RUNTIME -B -I WORKFLOW --root DISPOSABLE status
```

CONFIGURED requires executed zero exits/integrity true/ACCEPTED configuration/matching digests. ACTIVE additionally requires independent release trust/observed human-reviewed adoption acceptance; adapters/controllers stay OFF. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md).

Retain marker/journal/recovery objects. Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8). Refusal precedes managed mutation; retries cannot clear it. Remain quiescent. Never reinstall over unknown/tampered state.

Owner recovery:

1. Obtain exclusive owner control; stop writers.
2. Preserve journal/objects/endpoints/bytes/metadata. Authenticate any backup against `runtime.previous_sha256`; refuse mismatches.
3. If no backup exists and canonical `.agentic/.venv` matches `runtime.previous_sha256`, quarantine only the stage outside the project; leave the canonical runtime untouched. Otherwise quarantine failed canonical/staged runtimes outside the project, leaving canonical absent. Authenticate recorded stages against `runtime.new_sha256`.
4. Run trusted external bootstrap `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --recover`: restore provable managed files/receipt/provenance and any authenticated backup.
5. For legacy `.agentic-backup/<txid>` refusal, authenticate journal-recorded files/metadata; quarantine that exact directory externally; rerun `--recover`. Never edit journal/marker.
6. Reconcile every diagnostic path/quarantine: canonical may hold the restored runtime. Never discard it or just remove the marker.
7. After resolving every row, archive index/referenced evidence/path-by-path resolutions; move only the resolved `retained-journal.json` outside the project. No owner retirement CLI exists. Preserve evidence objects/adoption audit.
8. Verify integrity/configuration/status through matching restored-version tooling's isolated canonical runtime. Rollback proves no CONFIGURED/ACTIVE state.

Forward recovery with retained backups returns `status: COMMITTED`, `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories`, `installation_recovery`; nested diagnostic status remains COMMITTED_CLEANUP_PENDING. Inspect exact inventory; recovery authorizes no deletion.

Recovery recognises only exact sibling temporary names derived from the journal's
transaction identity, naming scheme and recorded targets. Unrecorded look-alikes
and unknown managed additions refuse with marker retained. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. The upgrade report/state disposition records the prior hash and EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness or adoption.

Request AWF-chat adoption from fetched default: preserve OPERATING_CONFIG.yaml comments/text; add accounting/enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime in the requested upgrade PR. `operating set --adopt-budgeting --instruction "Adopt dormant budget controls"` prepares this without --set choices/enabling controls. Restricted publication prepares owner branch/body. Owner review/merge/fetch applies it; unmerged edits do nothing. Non-OFF changes need a later settings PR. Git/PR history replaces confirmation transactions.

Before enabling, stop legacy writers; inventory state-store/routing/per-host databases,
checkpoint WALs and retain immutable snapshots/SHA-256 pins. Trusted hosts call
installed `agentic.upgrade.import_budget_history` with one protected canonical ledger
outside worktrees, exact project ID, complete worktree roots and source-label
`{path, sha256}` mappings. Both hosts select that ledger. Capture independent
`prepare_budget_import` before/`check_budget_import` after via `scripts/qualify_upgrade.py`.
Preserve sources/reservations/UNKNOWN usage/external results. Exact replay deduplicates;
reconcile conflicts. Enabled admission retains legacy import/charge checks; OFF
adoption needs no import; declining retains legacy behavior.

Published 1.9.5 operating records verify without confirmation; unsupported pre-release
confirmation fields refuse. Preserve records; restore a supported baseline before upgrade.
See [budgeting](.agentic/docs/39-BUDGETING.md).

Rollout remains unauthorized.
