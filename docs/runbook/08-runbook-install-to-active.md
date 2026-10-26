## 3. INSTALL TO ACTIVE

**All three OS sequences below are conditional on the [publication checkpoint in §2](06-runbook-publication-checkpoint.md). Their successful execution with the integrated 1.9.5 release is UNVERIFIED.**

**Reading sequence:** [Repository states and installation results](09-runbook-repository-states-and-installation-results.md); then [Windows](10-runbook-windows-powershell.md), [macOS](11-runbook-macos-posix-shell.md), [Linux](12-runbook-linux-posix-shell.md).

**Historical evidence:** Branch labels and revision citations describe their supplied snapshots. Present merge, CI and release status is **UNVERIFIED** unless independently supplied. Historical citations are not observations of current main. Unconfirmed output-capture work remains separate from #219, d2-006d, the AWF-54 successor and POSTSUITE-001; no verified output-capture revision is supplied here.

**AWF-177 correction coverage**

This is a new reconciliation derived from supplied evidence, not a recovered historical coverage artifact. Base lines below refer to `dc5b3a9de095a643bc2f7a27d3cf54b9f4366c84`; row IDs are historical identifiers, not current line numbers. All 14 corrections are already delivered at 20 locations. Outside-range rows remain preserved in their existing parts and are linked here.

| Row ID | Locations | Current destination | Base line(s) | Disposition |
|---|---:|---|---|---|
| R-038 | 1 | [Authority](04-runbook-authority-and-quiescence.md) | 3 | Preserve governance citations. |
| R-050 | 1 | [Quiescence](04-runbook-authority-and-quiescence.md) | 15 | Preserve installer/AGENTS citations. |
| R-067 | 1 | [Publication checkpoint](06-runbook-publication-checkpoint.md) | 14 | Preserve complete blockers. |
| R-139 | 3 | [Windows](10-runbook-windows-powershell.md); [macOS](11-runbook-macos-posix-shell.md); [Linux](12-runbook-linux-posix-shell.md) | 11 / 9 / 9 | Preserve discovery before installation. |
| R-202 | 3 | [Windows](10-runbook-windows-powershell.md); [macOS](11-runbook-macos-posix-shell.md); [Linux](12-runbook-linux-posix-shell.md) | 77 / 67 / 67 | Preserve operating table, PR, choices and instruction/acceptance distinction. |
| R-204 | 3 | [Windows](10-runbook-windows-powershell.md); [macOS](11-runbook-macos-posix-shell.md); [Linux](12-runbook-linux-posix-shell.md) | 79 / 69 / 69 | Preserve candidate before self-test, byte matching, reports/skips and fresh-install exception. |
| R-414 | 1 | [Installed self-test](13-runbook-verifying-with-installed-self-test.md) | 3 | Preserve sequence and fresh-install qualification. |
| R-448 | 1 | [Starting installations](15-runbook-supported-starting-installations.md) | 12 | Preserve pending byte-exact predecessor and integrated identities. |
| R-499 | 1 | [Migration](18-runbook-migration-behavior.md) | 8 | Bind archive observation to the base; retain pending runtime-derived targets. |
| R-537 | 1 | [Post-upgrade verification](20-runbook-post-upgrade-verification-and-reactivation.md) | 3 | Preserve recorded upgrade candidate before self-test. |
| R-552 | 1 | [Post-adoption checks](22-runbook-post-adoption-checks.md) | 3 | Preserve separate owner rules application. |
| R-575 | 1 | [Live prerequisites](22-runbook-post-adoption-checks.md) | 26 | Preserve both checks and qualification; add complete rules template. |
| R-617 | 1 | [Publisher handoff](29-runbook-orig-head-lock-or-sandbox-refusal.md) | 5 | Preserve corrected handoff citation. |
| R-769 | 1 | [Final acceptance](40-runbook-unverified-until-publish.md) | 3 | Preserve blocker closure and bound acceptance. |
| **Total** | **20** | **14 distinct rows** | | |

[Runbook index](INDEX.md)
