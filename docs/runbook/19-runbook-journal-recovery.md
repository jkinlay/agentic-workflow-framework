### Journal recovery

Use trusted external PYTHON and verified journal-writing release at RELEASE, especially with incomplete installed tools. Uppercase paths are absolute. Platform examples use the separate bootstrap environment and verified release cache:

Windows:

```powershell
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --recover
```

macOS/Linux:

```sh
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --recover
```

No intent returns NO_PENDING_INSTALL. Authenticated rollback returns ROLLED_BACK; deletion-dependent rollback refuses before managed mutation. Committed cleanup returns COMMITTED with COMMITTED_CLEANUP_PENDING/transaction_cleanup_failures/retained_directories/installation_recovery diagnostics. Missing/tampered objects or orphan marker refuse; journal-only recovery requires a proved endpoint. Retain all evidence; never edit journal/marker or glob-delete temporaries. Lock presence proves no exclusivity. Preserve stable lock bytes/inode; custody metadata never replaces exclusive ownership. Phases `object_prepare`, `active`, `rollback_cleanup`, `commit_cleanup`, `commit_cleanup_authenticated` are not activation.

### Owner recovery and retiring resolved diagnostics

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Keep exclusive ownership/quiescence through completion.
2. Copy journal/marker to external owner storage; leave objects/endpoints/lock in place. Preserve lock bytes/metadata using the platform commands below. If backup exists, authenticate it against `runtime.previous_sha256`: `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`. Require 0/`matches:true`; 1=mismatch or 2=malformed/refused enters PARKED below. Before quarantine, run step-4 command once with stage still present: existing pending-update normalization may succeed then refuse deletion-dependent rollback. Verify marker pending_journal_sha256 is null, refresh journal/marker copies; other refusal enters PARKED. Already ROLLED_BACK/COMMITTED: skip quarantine, inspect diagnostics at step 5.
3. If backup is absent AND canonical `.agentic/.venv` matches previous hash, keep canonical; authenticate stage before quarantine if present. Otherwise, nonnull previous hash AND absent backup enters PARKED. Authenticate present backup; authenticate/quarantine present new canonical AND stage; leave canonical absent. Each new endpoint uses step-2 runtime-hash AND `--journal JOURNAL_COPY`, DIGEST=`runtime.new_sha256`: require hash AND journal-recorded `runtime.stage_identity` match. POSIX: device/inode/birth-time (st_birthtime_ns, else integer ns from st_birthtime seconds; never ctime). Windows: volume/file-ID/creation-time. Unknown endpoint OR null identity OR null digest OR unsupported capture: PARKED. Absent custody metadata alone does not invalidate provable endpoints.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`: expect ROLLED_BACK; backup refusal leads to step 5; other refusals enter PARKED.
5. After step 4 returns ROLLED_BACK OR backup refusal, inspect remaining `.agentic-backup/<txid>`: journal-4 with created_directories AND legacy journals. If files remain, use step-2 journal copy; authenticate every listed FILE: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST`. Require new_sha256 AND new_mode: DIGEST=new_sha256, returned mode=new_mode. POSIX `posix_ownership` records ORIGINALS only; backup UID=installing effective UID; GID=parent's group if setgid, otherwise installing effective GID (preserved inheritance evidence). Windows mode=journal; ACLs/streams=accepted baseline. Unknown OR differing OR unrecorded OR unprovable metadata stops movement and enters PARKED; reject extras. Quarantine authenticated exact directory. All-empty backup tree: retain deliberately. Rerun step 4: active rollback=ROLLED_BACK; combined retained records=RETAINED_DIRECTORIES; single legacy refusal=RECOVERY_REFUSED; single empty-directory record=ROLLED_BACK. Quarantined paths remain listed.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Null-identity empty managed directories cannot authenticate: retain deliberately; acceptable restored-1.9.5 residue. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` to owner-controlled same-filesystem storage outside PROJECT, preserving permissions. No retirement CLI; preserve objects/adoption audit. Confirm step-4 command returns NO_PENDING_INSTALL. Later transactions may adopt only directories actually reused, recording source evidence in `.agentic-install/directory-adoptions.json`; absent/quarantined paths cannot be adopted.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

#### Lock metadata preservation

LOCK=PROJECT/.agentic-install/lock; outputs are new external owner files. Windows PowerShell captures every ADS as hex (empty array means absent):

```powershell
ConvertTo-Json -InputObject @(Get-Item -LiteralPath $LOCK -Stream * -ErrorAction Stop | Where-Object Stream -ne ':$DATA' | ForEach-Object { $bytes=[byte[]](Get-Content -LiteralPath $LOCK -Stream $_.Stream -Encoding Byte -Raw -ErrorAction Stop); if ($null -eq $bytes) { $bytes=[byte[]]@() }; @{name=$_.Stream; hex=[BitConverter]::ToString($bytes).Replace('-','')} }) -Depth 4 | Out-File -LiteralPath $EXTERNAL_ADS -Encoding utf8 -NoClobber
```

POSIX Python, including macOS without os.*xattr:

```text
PYTHON -c "import os,sys,json;available=all(hasattr(os,n) for n in ('listxattr','getxattr'));print(json.dumps({n:os.getxattr(sys.argv[1],n).hex() for n in os.listxattr(sys.argv[1])} if available else {'capture':'APIS_UNAVAILABLE'}))" LOCK > EXTERNAL_XATTRS
```

Empty metadata is absent; missing APIs mean unobserved, not unreadable recorded custody. That supported path writes no custody xattr: live install/bootstrap/upgrade work. Fresh recovery of unprovable surviving temporaries refuses: quarantine exact named temporaries under exclusive owner control, preserving evidence. Unreadable recorded metadata enters PARKED; never erase/forge custody.

#### PARKED_UNAUTHENTICATED

Owner creates `PROJECT/AWF-RECOVERY-PARKED.txt` exclusively, outside .agentic-install, recording reason/transaction/endpoints/evidence references/pre-upgrade commit. Original stays quiescent/untouched, without retries. Digest snapshots cannot restore bytes. With managed files committed, restore elsewhere:

```text
git clone --no-hardlinks OWNER_REPOSITORY FRESH
git -C FRESH checkout --detach PRE_UPGRADE_COMMIT
```

In FRESH, provision from hash-verified WHEELS:

```text
python -B -c "import sys; sys.path.insert(0, '.agentic/lib'); from agentic.adoption_config import ensure_installed_runtime; ensure_installed_runtime('.', wheelhouse='<hash-verified wheelhouse>')"
```

Run step 8 there; retain original and evidence. Without committed originals, owner must supply a full pre-upgrade backup before restoration.

**Related rollback guidance:** [Upgrade rollback](21-runbook-upgrade-rollback.md); [installation/adoption rollback](23-runbook-rollback-install-adoption.md).

### Migration preservation policy

Full [policy](../../MIGRATION-v1.9.5-to-v1.9.6.md#migration-preservation-policy).

### Migration budget controls

Full [controls](../../MIGRATION-v1.9.5-to-v1.9.6.md#migration-budget-controls).

### Budget-history preservation

Before enabling: stop legacy writers; inventory state-store/routing/per-host databases,
checkpoint WALs and retain immutable snapshots/SHA-256 pins. Trusted hosts call installed `agentic.upgrade.import_budget_history` with protected canonical ledger outside worktrees/exact project-ID/complete worktree-roots/source-label
`{path, sha256}` mappings. Both hosts select it; capture independent
`prepare_budget_import` before/`check_budget_import` after via `scripts/qualify_upgrade.py`.
Preserve sources/reservations/UNKNOWN usage/external results. Exact replay deduplicates;
reconcile conflicts. Enabled admission retains legacy import/charge checks; OFF
adoption needs no import; declining retains legacy behavior.



### Qualification gates

Bind candidate/artifact/review/trust/publication, one-reservation/host/exactly-once allocation/limits; record commands/platform/interpreter/timings/counts/skips/failures/receipt hashes.

Require zero exits/integrity true/ACCEPTED configuration/bound hashes. CONFIGURED proves consistency, not execution history. Require independent/specialist review, hosted checks/trust/publication-scan/review-barrier/owner-acceptance. Authorized rollout needs
disposable proof. ACTIVE requires independent trust/observed receipt-changing adoption
merge/accepted raw default-branch bytes; enables no adapters.

[Runbook index](INDEX.md)
