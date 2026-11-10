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
| **[d2-006d branch]** Directory before-image/cleanup anchor | Bound sidecar and anchor preserve directory recovery authority across cleanup interruptions. Damaged or conflicting bindings refuse. Sources: `9f44c2a1:.agentic/lib/agentic/installer.py:751`; `9f44c2a1:.agentic/lib/agentic/installer.py:795`. |
| **[d2-006d branch]** POSIX directory removal | Identity-bound removal is explicitly unsupported and refuses; recovery is retained. Source: `9f44c2a1:.agentic/lib/agentic/installer.py:683`. |

Recovery phases are `object_prepare`, `active`, `rollback_cleanup`, `commit_cleanup`, and `commit_cleanup_authenticated`. They are transaction phases, not repository activation states. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1121`.

A stable installer lock may remain after successful rollback; its presence alone does not prove an active installer. The tests deliberately retain its inode. Source: `9f44c2a1:.agentic/tests/test_upgrade_195_path.py:214`.

**Owner recovery and retiring resolved diagnostics:**

1. Obtain exclusive owner control; stop writers.
2. Preserve journal/objects/endpoints/bytes/metadata; use `PYTHON -B RELEASE/scripts/qualify_upgrade.py runtime-hash --path ENDPOINT --expected-sha256 DIGEST`: require exit 0/`matches:true`; mismatch/error stops. PYTHON=external bootstrap Python; RELEASE=the verified journal-writing candidate release. Backup: recorded-path/`runtime.previous_sha256`.
3. If no backup exists and canonical `.agentic/.venv` matches `runtime.previous_sha256`, quarantine only the stage outside the project; leave the canonical runtime untouched. Otherwise quarantine failed canonical/staged runtimes outside the project, leaving canonical absent. Authenticate stages with the same `runtime-hash` command and `runtime.new_sha256`; use canonical/`runtime.previous_sha256` for the no-backup check.
4. Run candidate release's external bootstrap `PYTHON -B RELEASE/scripts/bootstrap_project.py --dest PROJECT --recover`; restore provable managed files/receipt/provenance/authenticated backup.
5. For legacy `.agentic-backup/<txid>` refusal, authenticate journal-recorded files/metadata; quarantine that exact directory externally; rerun `--recover`. Never edit journal/marker.
6. Reconcile rows: record authenticated restoration/quarantine or deliberate retention. The canonical RECOVERY_REFUSED row names restored runtime; it is not automatically adopted. Resolve via step 7; never discard runtime/remove marker.
7. After resolving every row, archive index/referenced evidence/path-by-path resolutions; move only the resolved `retained-journal.json` outside the project. No owner retirement CLI exists. Preserve evidence objects/adoption audit.
8. Run `RUNTIME -B -I WORKFLOW --root PROJECT` with `verify-installation`, `validate-config`, then `status`. RUNTIME=restored `.agentic/.venv/bin/python` (`.agentic/.venv/Scripts/python.exe` on Windows); WORKFLOW=restored `.agentic/scripts/workflow.py`; use absolute paths. Restored 1.9.5 tooling prints CONFIGURED; rollback proves no CONFIGURED/ACTIVE state.

A later successful transaction can instead adopt an existing managed directory it actually reuses, without creation/deletion. It records the source evidence and "adopted by transaction <id>" in `.agentic-install/directory-adoptions.json` before resolving that row; the installer retires the index only when all rows are adopted. Missing/quarantined paths are not automatically adopted.

**Related rollback guidance:** [Upgrade rollback](21-runbook-upgrade-rollback.md); [installation/adoption rollback](23-runbook-rollback-install-adoption.md).

[Runbook index](INDEX.md)
