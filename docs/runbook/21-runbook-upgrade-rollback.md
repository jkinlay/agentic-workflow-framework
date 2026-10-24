### Upgrade rollback

Before durable commit, authenticated recovery can restore the exact old managed/runtime state. After durable commit, recovery completes the committed endpoint; it is not a downgrade command. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1544`; `7fcaeece:.agentic/lib/agentic/installer.py:1494`.

For rollback after a completed upgrade or accepted merge, use an owner-reviewed restoration/revert plan based on preserved backups and accepted history. **Exact commands, complete restore procedure, and subsequent ACTIVE acceptance are UNVERIFIED.** Do not use `--recover` as an assumed undo operation.
