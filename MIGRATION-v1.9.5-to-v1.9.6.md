# Upgrade from 1.9.5 to 1.9.6

Verify release/pin. Stop dispatch/settlement; obtain exclusive ownership. Rehearse disposably; retain untouched authentic baseline/external reports.

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

Compare proves preservation **since BEFORE**. Backups require exact replaced/deleted originals/metadata; arbitrary additions fail. Default conflict handling creates no backups. Missing receipt/provenance originals fail preservation.

Identity must match existing configuration; inspect the plan. Unresolved validation commands refuse. Rejected settings remain pending owner review, except incomplete enabled Jira identity may become accepted with JIRA_IDENTITY_UNBOUND. The plan reports this compatibility change; settings stay unchanged and writes refuse until explicit identity adoption. Disabled Jira stays disabled; adoption enables no adapter/controller.

Accepted YAML retains comments/format; only its version changes. Refusals name paths/remedies. Preserve unexpected import/schema files externally; correct or move them outside managed directories. Preserve linked targets externally, restore contained independent regular objects and reconcile references before replanning. Preserve history/containment; unrelated product directories are not walked.

Preserve routing/operating choices/reviewers/pins/budgets/accounting. Archive unknown state read-only; preserve prior archives nonrecursively. Conflicting destinations refuse; never reset accounting/edit receipts.

`RUNTIME` is the absolute installed `.agentic/.venv/Scripts/python.exe` on Windows or `.agentic/.venv/bin/python` elsewhere. `WORKFLOW` is the absolute installed `.agentic/scripts/workflow.py`:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE verify-installation
RUNTIME -B -I WORKFLOW --root DISPOSABLE validate-config
RUNTIME -B -I WORKFLOW --root DISPOSABLE status
```

CONFIGURED requires actual zero exits, integrity true, ACCEPTED configuration and matching digests. ACTIVE additionally needs independent release trust and observed human-reviewed adoption acceptance; it enables no adapter/controller. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md).

Retain marker/journal/recovery objects. Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8). Refusal precedes managed mutation; retries cannot clear it. Remain quiescent. Never reinstall over unknown/tampered state.

Owner recovery:

1. Obtain exclusive owner control; stop writers.
2. Preserve journal/objects. Authenticate the backup tree against the journal's `runtime.previous_sha256` using trusted tooling; preserve bytes/metadata. Refuse mismatches.
3. Move failed canonical `.agentic/.venv` to retained quarantine outside the project, leaving the canonical path absent. Authenticate/quarantine staged runtimes requiring removal too; preserve every endpoint/evidence.
4. Run trusted external bootstrap with `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --recover`. Existing rollback renames authenticated backup back and restores managed files, receipt and provenance.
5. Carefully resolve retained diagnostics: the recorded canonical path now holds the restored runtime. Never delete it as residue or just remove the marker. Reconcile every path/quarantine/recovery before retiring resolved diagnostics.
6. Verify with matching restored-version tooling through its isolated canonical interpreter: integrity/configuration/status checks. Rollback proves no CONFIGURED/ACTIVE state.

Forward recovery retaining backups returns `status: COMMITTED` with `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories` and `installation_recovery`. Nested diagnostic status remains COMMITTED_CLEANUP_PENDING. Inspect exact inventory; recovery never authorizes deletion.

Recovery recognises only exact sibling temporary names derived from the journal's
transaction identity, naming scheme and recorded targets. Unrecorded look-alikes
and unknown managed additions refuse with marker retained. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. The upgrade report/state disposition records the prior hash and EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness or adoption.

Request adoption in AWF chat. From fetched default, the skill preserves OPERATING_CONFIG.yaml comments/text and adds accounting/enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime. Include in the upgrade PR when requested. `operating set --adopt-budgeting --instruction "Adopt dormant budget controls"` prepares this change without --set choices or enabling controls. Restricted publication prepares branch/body for the owner. Owner review/merge/fetch makes it effective; unmerged edits do nothing. Non-OFF changes need a later settings PR. Git/PR history replaces confirmation transactions.

Before enabling, stop legacy writers; inventory state-store/routing/per-host databases,
checkpoint WALs and retain immutable snapshots/SHA-256 pins. Trusted hosts call
installed `agentic.upgrade.import_budget_history` with one protected canonical ledger
outside worktrees, exact project ID, complete worktree roots and source-label
`{path, sha256}` mappings. Both hosts select that ledger. Capture independent
`prepare_budget_import` before/`check_budget_import` after via `scripts/qualify_upgrade.py`.
Preserve sources/reservations/UNKNOWN usage/external results. Exact replay deduplicates;
reconcile conflicts. Enabled admission retains legacy import/charge checks; OFF
adoption needs no import; declining retains legacy behavior.

Published 1.9.5 operating records verify without confirmation. Unsupported pre-release
confirmation fields refuse plainly; preserve records and restore a supported baseline before upgrade.
See [budgeting](.agentic/docs/39-BUDGETING.md).

Rollout remains unauthorized.
