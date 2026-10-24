### Project installation

For interrupted installation, use §5’s trusted external `--recover` command. Preserve journal, marker, recovery objects, runtime backups, and—when d2-006d is integrated—directory sidecar/anchor. Recovery refuses external drift instead of overwriting it. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1244`; `9f44c2a1:.agentic/lib/agentic/installer.py:795`.

For an unmerged adoption, retain the isolated change and backups while the owner decides whether to repair or abandon it. A universal uninstall/reset command is **UNVERIFIED**.

For an already merged adoption, use an owner-reviewed revert/restoration plan. Exact commands and activation consequences are **UNVERIFIED**. Installation does not authorize destructive cleanup or changes to server rules. Source: `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:56`.
