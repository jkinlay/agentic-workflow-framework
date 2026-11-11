# Upgrade from 1.9.5 to 1.9.6

Verify release/pin; quiesce/exclusively own; rehearse disposably; retain untouched authentic baseline/external reports.

[Adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md).

Before managed commits/checkouts merge `.agentic/templates/installed.gitattributes` into root `.gitattributes`; preserve product rules. Inspect nested/info overrides; prevent `core.autocrlf=true` corruption. Never renormalize unrelated files; reverify checkouts.

`PYTHON`=absolute separate-bootstrap interpreter (`python -m venv BOOTSTRAP`). Install verified dependencies: `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. WHEELS satisfies lock; uppercase paths=absolute.

POSIX qualification requires TMPDIR outside PROJECT on its filesystem; select external same-filesystem storage. Private 0700 wrappers retain inheritance replicas; qualify JSON lists exact paths. Preserve wrappers; owner-controlled cleanup only. Windows metadata preflights dispose creating handles; dry runs skip them.

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

`RUNTIME` =absolute installed `.agentic/.venv/Scripts/python.exe` on Windows or `.agentic/.venv/bin/python` elsewhere. `WORKFLOW` =absolute installed `.agentic/scripts/workflow.py`:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE verify-installation
RUNTIME -B -I WORKFLOW --root DISPOSABLE validate-config
RUNTIME -B -I WORKFLOW --root DISPOSABLE status
```

CONFIGURED requires executed zero exits/integrity true/ACCEPTED configuration/matching digests. ACTIVE additionally requires independent release trust/observed human-reviewed adoption acceptance; adapters/controllers stay OFF. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md).

Retain marker/journal/recovery objects. Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8). Refusal precedes managed mutation; retries cannot clear it. Stay quiescent; never reinstall over unknown/tampered state.

Owner recovery:

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Lock presence proves nothing.
2. Preserve journal copy/objects/endpoints/metadata. `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST` requires 0/`matches:true`; stop for 1=mismatch or 2=malformed/refused. PYTHON=external bootstrap interpreter; RELEASE=verified journal-writing release; PROJECT=absolute project. Backup=recorded-path/`runtime.previous_sha256`.
3. No backup/canonical `.agentic/.venv`=previous hash: quarantine stage only. Otherwise authenticate canonical/stage=`runtime.new_sha256`; quarantine authenticated runtimes, leaving canonical absent. Absent stage: canonical=swapped:new/unswapped:previous. Existing stage/null new_sha256: unauthenticated; retain diagnostics. Quarantine needs independent owner-verified creation evidence; otherwise stop. Meaning: contemporaneous trusted evidence binding stage creation/identity to this transaction.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover` restores provable managed files/receipt/provenance/backup.
5. Legacy `.agentic-backup/<txid>`: use step-2 journal copy; authenticate existing journal-listed FILEs: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST` (new_sha256). Require new_sha256/new_mode. POSIX `posix_ownership` records ORIGINALS only; copy UID=installing effective UID (preserved); GID=parent's group if setgid, otherwise installing effective GID (preserved parent-inheritance evidence). Windows modes/ACLs/streams=journal/accepted baseline. Unknown/differing/unrecorded/unprovable metadata stops. Reject extras; quarantine exact directory/rerun recovery. Never edit journal/marker.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted; use step 7. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` externally. No retirement CLI; preserve evidence objects/adoption audit.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute restored paths: RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

Forward recovery with retained backups returns `status: COMMITTED`, `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories`, `installation_recovery`; nested diagnostic status remains COMMITTED_CLEANUP_PENDING. Inspect exact inventory; recovery authorizes no deletion.

Recovery recognises only exact sibling temporaries derived from journal transaction identity/naming scheme/targets. Unrecorded look-alikes/managed additions refuse, retaining marker. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. Upgrade report/state records prior-hash/EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness/adoption.

Request AWF-chat adoption from fetched default: preserve OPERATING_CONFIG.yaml comments/text; add accounting/enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime in upgrade PR. `operating set --adopt-budgeting --instruction "Adopt dormant budget controls"` prepares adoption without --set choices/enabling controls. Restricted publication prepares owner branch/body. Owner review/merge/fetch applies; unmerged edits do nothing. Non-OFF changes require another settings PR. Git/PR history replaces confirmation transactions.

Before enabling, follow all [budget-history preservation prerequisites](docs/runbook/19-runbook-journal-recovery.md#budget-history-preservation).

Published 1.9.5 operating records verify without confirmation; unsupported pre-release fields refuse. Preserve records/restore supported baseline before upgrade.
[Budgeting](.agentic/docs/39-BUDGETING.md).

Rollout unauthorized.
