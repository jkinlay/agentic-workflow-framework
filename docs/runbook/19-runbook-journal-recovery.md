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
| Authenticated unfinished transaction | Restores old managed/runtime state when ownership and bytes can be proved; returns `ROLLED_BACK`. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1606`. |
| Durable commit in cleanup | Verifies committed payload, resumes cleanup, and returns `COMMITTED` or `COMMITTED_CLEANUP_PENDING`. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1598`. |
| Marker without journal | Refuses recovery. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1582`. |
| Journal without marker | Only a provable completed endpoint can be finalized; it cannot freely rewrite project/runtime bytes. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1584`. |
| Missing/tampered recovery objects or external edits | Refuses and retains recovery evidence. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1238`; `7fcaeece:.agentic/lib/agentic/installer.py:1244`. |
| **[d2-006d branch]** Directory before-image/cleanup anchor | Bound sidecar and anchor preserve directory recovery authority across cleanup interruptions. Damaged or conflicting bindings refuse. Sources: `9f44c2a1:.agentic/lib/agentic/installer.py:751`; `9f44c2a1:.agentic/lib/agentic/installer.py:795`. |
| **[d2-006d branch]** POSIX directory removal | Identity-bound removal is explicitly unsupported and refuses; recovery is retained. Source: `9f44c2a1:.agentic/lib/agentic/installer.py:683`. |

Recovery phases are `object_prepare`, `active`, `rollback_cleanup`, `commit_cleanup`, and `commit_cleanup_authenticated`. They are transaction phases, not repository activation states. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1121`.

A stable installer lock may remain after successful rollback; its presence alone does not prove an active installer. The tests deliberately retain its inode. Source: `9f44c2a1:.agentic/tests/test_upgrade_195_path.py:214`.

**Related rollback guidance:** [Upgrade rollback](21-runbook-upgrade-rollback.md); [installation/adoption rollback](23-runbook-rollback-install-adoption.md).

[Runbook index](INDEX.md)
