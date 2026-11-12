# Upgrade 1.9.5 to 1.9.6

Verify release/pin; rehearse disposably.

Before managed commits/checkouts merge `.agentic/templates/installed.gitattributes` into root `.gitattributes`; preserve product rules. Inspect nested/info overrides; prevent autocrlf corruption; never renormalize unrelated files.

`PYTHON`=absolute separate-bootstrap interpreter (`python -m venv BOOTSTRAP`). Install verified dependencies: `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. Uppercase paths=absolute. POSIX TMPDIR: external same-filesystem storage; retain private 0700 inheritance replicas listed in qualify JSON. Windows preflights dispose creating handles; dry runs skip them.

Snapshot **before apply**, keeping untouched quiescent BASELINE:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Require [preservation policy](docs/runbook/19-runbook-journal-recovery.md#migration-preservation-policy): exact replaced/deleted originals/metadata/receipt/provenance, settings/routes/operating/reviewers/pins/budgets/accounting/history. Default conflicts: no backups; never reset accounting/edit receipts. Unresolved validation commands refuse. JIRA_IDENTITY_UNBOUND permits no writes before explicit identity adoption; disabled Jira stays disabled.

Installed checks: step 8. CONFIGURED requires zero exits/integrity true/ACCEPTED configuration/matching digests. ACTIVE also requires independent trust/observed adoption acceptance; adapters/controllers stay OFF. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md). Never reinstall unknown/tampered state.

## 1.9.6 limitation

**1.9.6 limitation—quiescent, identity-checked namespace publication.** Name-based rename or single-file unlink is permitted only for an object created by AWF in this transaction, or an explicitly managed object whose recorded identity is checked immediately before mutation. Every affected parent must remain pinned and held by AWF; the operation must hold the applicable exclusive transaction lock, and the owner must establish quiescence across installers, agents, editors, schedulers and other writers. Owner files are never eligible. Unknown identity, replacement, links, unexpected occupancy or lost custody causes refusal and retained, reported evidence. These checks do not atomically bind a pathname mutation to its inspected leaf: a non-cooperating process with directory-write access can still win the final race. Object-bound publication/closeout is deferred to AWF-258, release 1.9.8. Recursive runtime deletion and deletion-dependent automatic rollback remain refused.

Owner recovery:

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Lock presence proves nothing.
2. Preserve journal copy/objects/endpoints/lock metadata (Windows: ADS). LOCK=PROJECT/.agentic-install/lock. POSIX xattrs without getfattr: `PYTHON -c "import os,sys,json;print(json.dumps({n:os.getxattr(sys.argv[1],n).hex() for n in os.listxattr(sys.argv[1])}))" LOCK > EXTERNAL_XATTRS`. Unavailable capture: use step-3 parked path. `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`: require 0/`matches:true`; stop for 1=mismatch or 2=malformed/refused. PYTHON=external interpreter; RELEASE=verified journal-writing release; PROJECT=absolute. Authenticate backup against `runtime.previous_sha256`.
3. If backup is absent AND canonical `.agentic/.venv` matches previous hash, keep canonical; quarantine stage only, if present. Otherwise, previous hash nonnull AND backup absent requires parked path below. Authenticate backup if present; quarantine present new canonical AND stage; leave canonical absent. Authenticate each new endpoint with step-2 runtime-hash AND `--journal JOURNAL_COPY`, using `runtime.new_sha256`: require hash AND journal-recorded `runtime.stage_identity` match. AWF records POSIX device AND inode (available birth-time, never ctime), or Windows volume AND file-ID AND creation-time. External observation unnecessary. Unknown endpoint OR null identity OR null digest OR unsupported capture: preserve everything, record PARKED_UNAUTHENTICATED; restore untouched pre-upgrade snapshot into another project, run step 8 there; original stays quiescent without retries.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`: expect ROLLED_BACK; backup refusal leads to step 5.
5. `.agentic-backup/<txid>`: journal-4 with created_directories AND legacy journals. If files remain, use step-2 journal copy; authenticate every listed FILE: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST`. Require new_sha256 AND new_mode; authenticate DIGEST=new_sha256 AND returned mode=new_mode. POSIX `posix_ownership` records ORIGINALS only; copy UID=installing effective UID; GID=parent's group if setgid, otherwise installing effective GID (preserved inheritance evidence). Windows mode=journal; ACLs AND streams=accepted baseline. Unknown OR differing OR unrecorded OR unprovable metadata stops; reject extras. Quarantine exact directory; rerun step 4: ROLLED_BACK. Empty directories: retain deliberately. Repeated --recover: combined records=RETAINED_DIRECTORIES; single legacy refusal=RECOVERY_REFUSED; single empty-directory record=ROLLED_BACK. Quarantined paths remain listed. Never edit journal OR marker.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Null-identity empty managed directories cannot authenticate: retain deliberately; acceptable restored-1.9.5 residue. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` to owner-controlled same-filesystem storage outside PROJECT, preserving permissions. No retirement CLI; preserve objects/adoption audit. Confirm step-4 command returns NO_PENDING_INSTALL.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute paths: RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

Forward recovery: [journal recovery](docs/runbook/19-runbook-journal-recovery.md).

Recovery recognises only exact sibling temporaries derived from journal transaction identity/naming scheme/targets. Unrecorded look-alikes/managed additions refuse, retaining marker. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budget adoption and compatibility require the [migration budget controls](docs/runbook/19-runbook-journal-recovery.md#migration-budget-controls).

[Budgeting](.agentic/docs/39-BUDGETING.md).

Rollout unauthorized.
