### Repository states and installation results

The accepted repository-state progression is:

```text
UNVERIFIED → INSTALLED → CONFIGURED → ACTIVE
```

UNVERIFIED is the initial status presentation when no installation state can be established. INSTALLED, CONFIGURED, and ACTIVE are independently established stages; bootstrap may complete installation and configuration in one invocation. Sources: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:450`; `7fcaeece:.agentic/lib/agentic/providers/github_status.py:467`; `7fcaeece:.agentic/lib/agentic/providers/github_status.py:490`; `7fcaeece:.agentic/lib/agentic/providers/github_status.py:674`.

| State/result | Meaning and next action |
|---|---|
| `PLAN` | Bootstrap dry-run result; `installed: false`. Review the plan; no installation has been established. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1870`. |
| `UNVERIFIED` | Installation integrity could not be established. Restore or recover the verified files before claiming installation. Source: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:450`. |
| `INSTALLED` | Managed bytes verify against the receipt. Resolve configuration and repeat verification/validation. Source: `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:50`. |
| `INSTALLED_UNCONFIGURED` | Bootstrap verified installation but did not establish configuration acceptance; exit 1. Source: `7fcaeece:scripts/bootstrap_project.py:224`. |
| `INSTALLATION_VERIFICATION_FAILED` | Bootstrap could not prove installation integrity; exit 2. Source: `7fcaeece:scripts/bootstrap_project.py:224`. |
| `CONFIGURED` | Installed checks succeeded with accepted configuration and matching source, policy, and operating digests. Proceed through adoption review, merge, and accepted-checkout observation. Source: `7fcaeece:.agentic/lib/agentic/adoption_config.py:873`. |
| `ACTIVE` | Independently trusted release, observed receipt-changing adoption merge, and accepted current default-branch checkout verify. Source: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:584`. |

The command showing current state is the installed canonical runtime invoking:

```text
workflow.py --root <repo-path> status --json
```

Use the complete OS-specific commands for [Windows](10-runbook-windows-powershell.md), [macOS](11-runbook-macos-posix-shell.md), [Linux](12-runbook-linux-posix-shell.md). CLI parsing and dispatch: `7fcaeece:.agentic/lib/agentic/cli.py:141`; `7fcaeece:.agentic/lib/agentic/cli.py:380`.

Ordinary status exit 0 can mean INSTALLED or CONFIGURED. **Only `status --require-active` exit 0 establishes ACTIVE.** Exit 4 means ACTIVE was not established; exit 2 means no valid project state/input was established. Sources: `7fcaeece:.agentic/lib/agentic/cli.py:390`; `7fcaeece:.agentic/tests/test_adoption_status.py:1163`.

[Runbook index](INDEX.md)
