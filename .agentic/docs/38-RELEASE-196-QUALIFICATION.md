# 1.9.6 qualification/preservation

AWF-238: Tier 3, owner-approved FULL rebind except automatic
deletion deferred to [AWF-258](#awf-258-successor). Retain pre-F5 #1
(object-continuity deletion). Rollout/adapters unauthorized.

## 1.9.6 limitation

**1.9.6 limitation—quiescent, identity-checked namespace publication.** Name-based rename or single-file unlink is permitted only for an object created by AWF in this transaction, or an explicitly managed object whose recorded identity is checked immediately before mutation. Every affected parent must remain pinned and held by AWF; the operation must hold the applicable exclusive transaction lock, and the owner must establish quiescence across installers, agents, editors, schedulers and other writers. Owner files are never eligible. Unknown identity, replacement, links, unexpected occupancy or lost custody causes refusal and retained, reported evidence. These checks do not atomically bind a pathname mutation to its inspected leaf: a non-cooperating process with directory-write access can still win the final race. Object-bound publication/closeout is deferred to AWF-258, release 1.9.8. Recursive runtime deletion and deletion-dependent automatic rollback remain refused.

## Baseline

`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Immutable assets/pins/receipts; Git proves no publication.

## Preservation

Require baselines/exclusivity/quiescence/authorization. Preserve owner/configuration/routing/Jira/budget/state/history/receipt inventories and default OFF settings.

`RELEASE/scripts/qualify_upgrade.py`; commands: `RELEASE/MIGRATION-v1.9.5-to-v1.9.6.md`. snapshot before apply, retain BEFORE/authentic baseline; compare derives identity-verified `approved_delta`; inspect `remaining_changes`. `--allowed-delta` only constrains policy; owner/history loss is unwaivable.

`installed` requires clean committed source/current manifest and complete locked wheels; binds commit/tree/pin, never regenerates. Bootstraps authentic 1.9.5; checks owner/history preservation. Failures retain inventories/comparison/NOT_QUALIFIED/failed-stage. Require QUALIFIED_DISPOSABLE_UPGRADE/two CONFIGURED stages/zero exits/integrity true/ACCEPTED configuration/bound source-policy-receipt hashes/preservation and PASS `source_managed_membership`/`historical_state_continuity`/`named_streams`.

Digest-only: no contents/SIDs; unverifiable permissions/streams refuse; PRESERVATION_COMPARISON_ONLY grants no qualification.

Require verified RELEASE's `docs/runbook/19-runbook-journal-recovery.md` ?Migration preservation policy, including every managed/state/metadata membership check.

POSIX qualification requires TMPDIR outside PROJECT on its filesystem; select external same-filesystem storage. Private 0700 wrappers retain inheritance replicas; qualify JSON lists exact paths. Preserve wrappers; owner-controlled cleanup only. Windows metadata preflights dispose creating handles; dry runs skip them.

Require verified RELEASE's `docs/runbook/19-runbook-journal-recovery.md` ?Migration budget controls.

## AWF-258 successor

See limitation.

## Recovery/gates

Interrupt staging/writes/receipts/provenance/swaps/cleanup; recover freshly. Missing/tampered objects refuse; protect empty directories. No recursive deletion; retained directories consume space. Removal-dependent rollback refuses before managed mutation. Committed recovery retires coordination, returning `status: COMMITTED`, `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories`, `installation_recovery`. Inspect inventory.
Retain unresolved directories in `.agentic-install/retained-journal.json`; valid retention blocks no integrity/capabilities/transactions. Adopt only reused existing managed directories after recording source evidence in `directory-adoptions.json`; delete none. Standalone failures retain stages/original-errors without cleanup retries; portable stages use sibling cleanup-pending.json. Archives stay nonrecursive.

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Lock presence proves nothing.
2. Preserve journal copy/objects/endpoints/lock metadata (Windows: ADS). LOCK=PROJECT/.agentic-install/lock. POSIX xattrs without getfattr: `PYTHON -c "import os,sys,json;print(json.dumps({n:os.getxattr(sys.argv[1],n).hex() for n in os.listxattr(sys.argv[1])}))" LOCK > EXTERNAL_XATTRS`. Unavailable capture: use step-3 parked path. `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`: require 0/`matches:true`; stop for 1=mismatch or 2=malformed/refused. PYTHON=external interpreter; RELEASE=verified journal-writing release; PROJECT=absolute. Authenticate backup against `runtime.previous_sha256`.
3. If backup is absent AND canonical `.agentic/.venv` matches previous hash, keep canonical; quarantine stage only, if present. Otherwise, previous hash nonnull AND backup absent requires parked path below. Authenticate backup if present; quarantine present new canonical AND stage; leave canonical absent. Authenticate each new endpoint with step-2 runtime-hash AND `--journal JOURNAL_COPY`, using `runtime.new_sha256`: require hash AND journal-recorded `runtime.stage_identity` match. AWF records POSIX device AND inode (available birth-time, never ctime), or Windows volume AND file-ID AND creation-time. External observation unnecessary. Unknown endpoint OR null identity OR null digest OR unsupported capture: preserve everything, record PARKED_UNAUTHENTICATED; restore untouched pre-upgrade snapshot into another project, run step 8 there; original stays quiescent without retries.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`: expect ROLLED_BACK; backup refusal leads to step 5.
5. `.agentic-backup/<txid>`: journal-4 with created_directories AND legacy journals. If files remain, use step-2 journal copy; authenticate every listed FILE: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST`. Require new_sha256 AND new_mode; authenticate DIGEST=new_sha256 AND returned mode=new_mode. POSIX `posix_ownership` records ORIGINALS only; copy UID=installing effective UID; GID=parent's group if setgid, otherwise installing effective GID (preserved inheritance evidence). Windows mode=journal; ACLs AND streams=accepted baseline. Unknown OR differing OR unrecorded OR unprovable metadata stops; reject extras. Quarantine exact directory; rerun step 4: ROLLED_BACK. Empty directories: retain deliberately. Repeated --recover: combined records=RETAINED_DIRECTORIES; single legacy refusal=RECOVERY_REFUSED; single empty-directory record=ROLLED_BACK. Quarantined paths remain listed. Never edit journal OR marker.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Null-identity empty managed directories cannot authenticate: retain deliberately; acceptable restored-1.9.5 residue. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` to owner-controlled same-filesystem storage outside PROJECT, preserving permissions. No retirement CLI; preserve objects/adoption audit. Confirm step-4 command returns NO_PENDING_INSTALL.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute paths: RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

Require verified RELEASE's `docs/runbook/19-runbook-journal-recovery.md` ?Qualification gates.
