# Upgrade 1.9.5 to 1.9.6

Verify release/pin; quiesce/exclusively own; rehearse disposably; retain untouched authentic baseline/external reports.

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

Require the [migration preservation policy](docs/runbook/19-runbook-journal-recovery.md#migration-preservation-policy).

Preserve routes/operating/reviewers/pins/budgets/accounting. Archive unknown state read-only; prior archives nonrecursively. Conflicting destinations refuse; never reset accounting/edit receipts.

`RUNTIME` =absolute installed `.agentic/.venv/Scripts/python.exe` on Windows or `.agentic/.venv/bin/python` elsewhere. `WORKFLOW` =absolute installed `.agentic/scripts/workflow.py`:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE verify-installation
RUNTIME -B -I WORKFLOW --root DISPOSABLE validate-config
RUNTIME -B -I WORKFLOW --root DISPOSABLE status
```

CONFIGURED requires executed zero exits/integrity true/ACCEPTED configuration/matching digests. ACTIVE additionally requires independent release trust/observed human-reviewed adoption acceptance; adapters/controllers stay OFF. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md).

Retain marker/journal/recovery objects/lock-metadata. Automatic rollback that needs runtime removal is unavailable in 1.9.6; it returns with AWF-258 (1.9.8). Refusal precedes managed mutation; retries cannot clear it. Stay quiescent; never reinstall over unknown/tampered state.

## 1.9.6 limitation

**1.9.6 limitation—quiescent, identity-checked namespace publication.** Name-based rename or single-file unlink is permitted only for an object created by AWF in this transaction, or an explicitly managed object whose recorded identity is checked immediately before mutation. Every affected parent must remain pinned and held by AWF; the operation must hold the applicable exclusive transaction lock, and the owner must establish quiescence across installers, agents, editors, schedulers and other writers. Owner files are never eligible. Unknown identity, replacement, links, unexpected occupancy or lost custody causes refusal and retained, reported evidence. These checks do not atomically bind a pathname mutation to its inspected leaf: a non-cooperating process with directory-write access can still win the final race. Object-bound publication/closeout is deferred to AWF-258, release 1.9.8. Recursive runtime deletion and deletion-dependent automatic rollback remain refused.

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

Budget adoption and compatibility require the [migration budget controls](docs/runbook/19-runbook-journal-recovery.md#migration-budget-controls).

[Budgeting](.agentic/docs/39-BUDGETING.md).

Rollout unauthorized.
