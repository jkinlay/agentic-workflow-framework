### Journal recovery

Use the trusted external release/bootstrap interpreter, especially when installed files/runtime may be incomplete.

Windows:

```powershell
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --recover
```

macOS/Linux:

```sh
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --recover
```

CLI recovery dispatch: `7fcaeece:scripts/bootstrap_project.py:181`.

| Observed recovery condition | Supported behavior |
|---|---|
| No pending intent | Returns `NO_PENDING_INSTALL`. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1578`. |
| Authenticated unfinished transaction | Restores provable old state only when no runtime removal is needed; returns `ROLLED_BACK`. Otherwise refuses before managed mutation, preserving the active journal, marker and runtimes. Follow the [owner recovery procedure](../../MIGRATION-v1.9.5-to-v1.9.6.md). Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `rollback`. |
| Durable commit in cleanup | Verifies payload and returns `status: COMMITTED`. Retained backups add `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories` and `installation_recovery`; this is successful recovery with unresolved diagnostics. Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `recover` and `_with_retained_inventory`. |
| Marker without journal | Refuses recovery. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1582`. |
| Journal without marker | Only a provable completed endpoint can be finalized; it cannot freely rewrite project/runtime bytes. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1584`. |
| Missing/tampered recovery objects or external edits | Refuses and retains recovery evidence. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1238`; `7fcaeece:.agentic/lib/agentic/installer.py:1244`. |
| POSIX directory removal | Identity-bound removal refuses; recovery is retained. Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `_cleanup_transaction_directories`. |

Recovery phases are `object_prepare`, `active`, `rollback_cleanup`, `commit_cleanup`, and `commit_cleanup_authenticated`. They are transaction phases, not repository activation states. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1121`.

A stable installer lock may remain after successful rollback; its presence alone does not prove an active installer. Tests retain its inode. Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `install_lock`.

**Owner recovery and retiring resolved diagnostics:**

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Lock presence proves nothing.
2. Preserve journal copy/objects/endpoints/metadata. `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST` requires 0/`matches:true`; stop for 1=mismatch or 2=malformed/refused. PYTHON=external bootstrap interpreter; RELEASE=verified journal-writing release; PROJECT=absolute project. Backup=recorded-path/`runtime.previous_sha256`.
3. No backup/canonical `.agentic/.venv`=previous hash: quarantine stage only. Otherwise authenticate canonical/stage=`runtime.new_sha256`; quarantine authenticated runtimes, leaving canonical absent. Absent stage: canonical=swapped:new/unswapped:previous. Existing stage/null new_sha256: unauthenticated; retain diagnostics. Quarantine needs independent owner-verified creation evidence; otherwise stop. Meaning: contemporaneous trusted evidence binding stage creation/identity to this transaction.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover` restores provable managed files/receipt/provenance/backup.
5. Legacy `.agentic-backup/<txid>`: use step-2 journal copy; authenticate existing journal-listed FILEs: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST` (new_sha256). Require new_sha256/new_mode. POSIX `posix_ownership` records ORIGINALS only; copy UID=installing effective UID (preserved); GID=parent's group if setgid, otherwise installing effective GID (preserved parent-inheritance evidence). Windows modes/ACLs/streams=journal/accepted baseline. Unknown/differing/unrecorded/unprovable metadata stops. Reject extras; quarantine exact directory/rerun recovery. Never edit journal/marker.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted; use step 7. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` externally. No retirement CLI; preserve evidence objects/adoption audit.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute restored paths: RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

A later successful transaction can instead adopt an existing managed directory it actually reuses, without creation/deletion. It records the source evidence and "adopted by transaction <id>" in `.agentic-install/directory-adoptions.json` before resolving that row; the installer retires the index only when all rows are adopted. Missing/quarantined paths are not automatically adopted.

**Related rollback guidance:** [Upgrade rollback](21-runbook-upgrade-rollback.md); [installation/adoption rollback](23-runbook-rollback-install-adoption.md).

### Migration budget controls

External deltas never waive preservation; absent controls stay LEGACY_RETAINED.
Owner-requested OFF adoption preserves comments; owner merge/fetch applies.
Non-OFF changes need another PR. Published audits remain readable; unsupported
pre-release confirmations refuse/remain retained. `import_budget_history` merges
pinned state-store/per-host history. Independent `prepare_budget_import`/`check_budget_import` verify sources/charges/UNKNOWN/deduplication. Qualification
requires published-API history, lost-row injection, import/check/replay/interruption/
concurrency. See `MIGRATION-v1.9.5-to-v1.9.6.md`.

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
