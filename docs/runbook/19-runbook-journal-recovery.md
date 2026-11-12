### Journal recovery

Use trusted external release/bootstrap interpreter, especially when installed files/runtime may be incomplete.

Windows:

```powershell
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --recover
```

macOS/Linux:

```sh
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --recover
```

| Observed recovery condition | Supported behavior |
|---|---|
| No pending intent | Returns `NO_PENDING_INSTALL`. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1578`. |
| Authenticated unfinished transaction | Restores provable old state only when no runtime removal is needed; returns `ROLLED_BACK`. Otherwise refuses before managed mutation, preserving the active journal, marker and runtimes. Follow the [owner recovery procedure](../../MIGRATION-v1.9.5-to-v1.9.6.md). Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `rollback`. |
| Durable commit in cleanup | Verifies payload and returns `status: COMMITTED`. Retained backups add `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories` and `installation_recovery`; this is successful recovery with unresolved diagnostics. Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `recover` and `_with_retained_inventory`. |
| Marker without journal | Refuses recovery. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1582`. |
| Journal without marker | Only a provable completed endpoint can be finalized; it cannot freely rewrite project/runtime bytes. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1584`. |
| Missing/tampered recovery objects or external edits | Refuses and retains recovery evidence. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1238`; `7fcaeece:.agentic/lib/agentic/installer.py:1244`. |
| POSIX directory removal | Identity-bound removal refuses; recovery is retained. Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `_cleanup_transaction_directories`. |

Phases (not activation): `object_prepare`, `active`, `rollback_cleanup`, `commit_cleanup`, `commit_cleanup_authenticated`. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1121`.

The held installer lock retains temporary creating identities across crashes; missing or mismatched custody refuses cleanup. Preserve its bytes and metadata with recovery evidence. One active record uses POSIX user xattrs or Windows ADS; unsupported metadata refuses before payload publication. No lock bytes or inode are replaced.

Successful rollback may retain the stable lock inode; presence proves no active installer. Source: [.agentic/lib/agentic/installer.py](../../.agentic/lib/agentic/installer.py), `install_lock`.

**Owner recovery and retiring resolved diagnostics:**

Quarantine: owner-controlled same-filesystem rename outside PROJECT; preserve links/modes/ownership; no copy/delete fallback.

1. No exclusivity-proof CLI: owner stops/checks every host's installers/agents/editors/schedulers/processes/leases. Lock presence proves nothing.
2. Preserve journal copy/objects/endpoints/lock metadata (Windows: ADS). LOCK=PROJECT/.agentic-install/lock. POSIX xattrs without getfattr: `PYTHON -c "import os,sys,json;print(json.dumps({n:os.getxattr(sys.argv[1],n).hex() for n in os.listxattr(sys.argv[1])}))" LOCK > EXTERNAL_XATTRS`. Unavailable capture: use step-3 parked path. `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`: require 0/`matches:true`; stop for 1=mismatch or 2=malformed/refused. PYTHON=external interpreter; RELEASE=verified journal-writing release; PROJECT=absolute. Authenticate backup against `runtime.previous_sha256`.
3. If backup is absent AND canonical `.agentic/.venv` matches previous hash, keep canonical; quarantine stage only, if present. Otherwise, previous hash nonnull AND backup absent requires parked path below. Authenticate backup if present; quarantine present new canonical AND stage; leave canonical absent. Authenticate each new endpoint with step-2 runtime-hash AND `--journal JOURNAL_COPY`, using `runtime.new_sha256`: require hash AND journal-recorded `runtime.stage_identity` match. AWF records POSIX device AND inode (available birth-time, never ctime), or Windows volume AND file-ID AND creation-time. External observation unnecessary. Unknown endpoint OR null identity OR null digest OR unsupported capture: preserve everything, record PARKED_UNAUTHENTICATED; restore untouched pre-upgrade snapshot into another project, run step 8 there; original stays quiescent without retries.
4. `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`: expect ROLLED_BACK; backup refusal leads to step 5.
5. `.agentic-backup/<txid>`: journal-4 with created_directories AND legacy journals. If files remain, use step-2 journal copy; authenticate every listed FILE: `PYTHON -B RELEASE/scripts/qualify_upgrade.py file-hash --path FILE --expected-sha256 DIGEST`. Require new_sha256 AND new_mode; authenticate DIGEST=new_sha256 AND returned mode=new_mode. POSIX `posix_ownership` records ORIGINALS only; copy UID=installing effective UID; GID=parent's group if setgid, otherwise installing effective GID (preserved inheritance evidence). Windows mode=journal; ACLs AND streams=accepted baseline. Unknown OR differing OR unrecorded OR unprovable metadata stops; reject extras. Quarantine exact directory; rerun step 4: ROLLED_BACK. Empty directories: retain deliberately. Repeated --recover: combined records=RETAINED_DIRECTORIES; single legacy refusal=RECOVERY_REFUSED; single empty-directory record=ROLLED_BACK. Quarantined paths remain listed. Never edit journal OR marker.
6. Resolve rows: authenticated restoration/quarantine or deliberate retention. Null-identity empty managed directories cannot authenticate: retain deliberately; acceptable restored-1.9.5 residue. Canonical RECOVERY_REFUSED names restored runtime, never automatically adopted. Never discard runtime/remove marker.
7. Archive index/references/path-by-path resolutions; move only resolved `retained-journal.json` to owner-controlled same-filesystem storage outside PROJECT, preserving permissions. No retirement CLI; preserve objects/adoption audit. Confirm step-4 command returns NO_PENDING_INSTALL.
8. Previous installation: `RUNTIME -B -I WORKFLOW --root PROJECT`: `verify-installation`, `validate-config`, `status`. Absolute paths: RUNTIME=`.agentic/.venv/bin/python` (Windows: `.agentic/.venv/Scripts/python.exe`); WORKFLOW=`.agentic/scripts/workflow.py`. Restored 1.9.5 prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE. Fresh-install rollback: confirm runtime/workflow/receipt absent; archive evidence/report uninstalled; checks unavailable. Retry requires recovery/resolution/owner authorization.

A later successful transaction can instead adopt an existing managed directory it actually reuses, without creation/deletion. It records the source evidence and "adopted by transaction <id>" in `.agentic-install/directory-adoptions.json` before resolving that row; the installer retires the index only when all rows are adopted. Missing/quarantined paths are not automatically adopted.

**Related rollback guidance:** [Upgrade rollback](21-runbook-upgrade-rollback.md); [installation/adoption rollback](23-runbook-rollback-install-adoption.md).

### Migration preservation policy

YAML changes only version; preserve comments/format; refusals name paths/remedies. Externally preserve unexpected import/schema files; correct/move outside managed directories. Preserve linked targets externally, restore contained independent regular objects and reconcile references before replanning. Preserve history/containment; exclude unrelated product trees.

`prepare_upgrade` authenticates target manifest/pin/baseline membership; `compare_upgrade` binds rules/hashes/`approved_changes`/`changes`/baseline/manifest.

- Exact managed hashes/sizes; removals require authenticated old-membership/target-absence. Outside-membership content stays owner-owned; refuse collisions, including identical-byte/preinstallation removals. **AGENTS.md is managed**; preserve other owner paths.
- PROJECT_CONFIG changes only `template.expected_workflow_version`; preserve other values/types/permissions.
- Only recorded schema-migrations change state; prederive bytes/source-target-hashes/actions.
  `state_archive_plan` binds named `upgrade-to-<target>` read-only copies/manifest
  to exact contents/sources. Preserve prior archives; prefixes authorize nothing.
- Independently snapshot pre-migration state; query-only in-memory SQLite invokes no migration/initializer/upgrade validator. Preserve all historical tables/columns/rowids/typed values/duplicates/reservations/settlements/unknown charges/audits. **Existing-field exceptions: none.** Check exact new-column/table schema/migrations separately. Non-SQL state/reservations/audits/sidecars/unknown records remain byte-identical; missing continuity refuses.
  Migrations cannot authorize loss.
- Reconstruct receipt membership/hashes/embedded manifest/install-project IDs/mutable
  paths/configuration hash. Require exact provenance target/schema/manifest/retained
  ID/upgrade/manual profile/canonical cohort time; reject extras. No recovery-objects/journal/marker/lock; retain only empty named object-store.
- Authenticate complete journals/files/directories/metadata/fresh transaction namespaces. Backups match exact replaced/deleted originals/metadata; no prefix waiver.
- Exact output directories/permissions/streams; unsupported metadata refuses. Windows probes dispose pinned handles; POSIX replicas remain external.
  Managed removals preserve streams.

### Migration budget controls

External deltas never waive preservation; absent controls stay LEGACY_RETAINED.
Owner-requested OFF adoption preserves comments; owner merge/fetch applies.
Non-OFF changes need another PR. Published audits remain readable; unsupported
pre-release confirmations refuse/remain retained. `import_budget_history` merges
pinned state-store/per-host history. Independent `prepare_budget_import`/`check_budget_import` verify sources/charges/UNKNOWN/deduplication. Qualification
requires published-API history, lost-row injection, import/check/replay/interruption/
concurrency. See `MIGRATION-v1.9.5-to-v1.9.6.md`.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. Upgrade report/state records prior-hash/EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness/adoption.

Request AWF-chat adoption from fetched default: preserve OPERATING_CONFIG.yaml comments/text; add accounting/enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime in upgrade PR. `operating set --adopt-budgeting --instruction "Adopt dormant budget controls"` prepares adoption without --set choices/enabling controls. Restricted publication prepares owner branch/body. Owner review/merge/fetch applies; unmerged edits do nothing. Non-OFF changes require another settings PR. Git/PR history replaces confirmation transactions.

Before enabling, follow all [budget-history preservation prerequisites](docs/runbook/19-runbook-journal-recovery.md#budget-history-preservation).

Published 1.9.5 operating records verify without confirmation; unsupported pre-release fields refuse. Preserve records/restore supported baseline before upgrade.

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
