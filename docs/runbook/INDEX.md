# Runbook index

The AWF 1.9.5 install and adoption runbook is kept in the repository in parts, split byte-exact at its level-3 sub-headings with no rewording (ruling jk-direct-1657). Read the parts in this order. Joining them in this order reproduces the source byte for byte: SHA-256 24fc50e5dd4854ab63d4426f1caa936cb2bbc9e43bfc7ce678c79282d30dbb83, 71585 bytes.

1. [00-runbook-introduction.md](00-runbook-introduction.md): Title, introduction and section 1 heading
2. [01-runbook-required-inputs.md](01-runbook-required-inputs.md): Required inputs
3. [02-runbook-placeholder-convention.md](02-runbook-placeholder-convention.md): Placeholder convention
4. [03-runbook-authority-and-quiescence.md](03-runbook-authority-and-quiescence.md): Authority and quiescence (ends with the section 2 heading)
5. [04-runbook-publication-checkpoint.md](04-runbook-publication-checkpoint.md): Publication checkpoint
6. [05-runbook-distribution-and-source-preparation.md](05-runbook-distribution-and-source-preparation.md): Distribution and source preparation (ends with the section 3 heading)
7. [06-runbook-repository-states-and-installation-results.md](06-runbook-repository-states-and-installation-results.md): Repository states and installation results
8. [07-runbook-windows-powershell.md](07-runbook-windows-powershell.md): Windows, PowerShell
9. [08-runbook-macos-posix-shell.md](08-runbook-macos-posix-shell.md): macOS, POSIX shell
10. [09-runbook-linux-posix-shell.md](09-runbook-linux-posix-shell.md): Linux, POSIX shell (also holds sections 4 and the section 5 heading)
11. [10-runbook-supported-starting-installations.md](10-runbook-supported-starting-installations.md): Supported starting installations
12. [11-runbook-pre-upgrade-checks-and-backup.md](11-runbook-pre-upgrade-checks-and-backup.md): Pre-upgrade checks and backup
13. [12-runbook-upgrade-commands.md](12-runbook-upgrade-commands.md): Upgrade commands
14. [13-runbook-migration-behavior.md](13-runbook-migration-behavior.md): Migration behavior
15. [14-runbook-journal-recovery.md](14-runbook-journal-recovery.md): Journal recovery
16. [15-runbook-post-upgrade-verification-and-reactivation.md](15-runbook-post-upgrade-verification-and-reactivation.md): Post-upgrade verification and reactivation
17. [16-runbook-upgrade-rollback.md](16-runbook-upgrade-rollback.md): Upgrade rollback (also holds section 6 and the section 7 heading)
18. [17-runbook-portable-skill.md](17-runbook-portable-skill.md): Portable skill
19. [18-runbook-project-installation.md](18-runbook-project-installation.md): Project installation (ends with the section 8 heading)
20. [19-runbook-windows-short-name-aliases.md](19-runbook-windows-short-name-aliases.md): Windows temporary short-name aliases versus resolved paths
21. [20-runbook-winerror-1314-symlink-privilege.md](20-runbook-winerror-1314-symlink-privilege.md): WinError 1314, symlink privilege
22. [21-runbook-orig-head-lock-or-sandbox-refusal.md](21-runbook-orig-head-lock-or-sandbox-refusal.md): ORIG_HEAD.lock or sandbox refusal to write Git metadata
23. [22-runbook-missing-or-incompatible-wheelhouse.md](22-runbook-missing-or-incompatible-wheelhouse.md): Missing or incompatible wheelhouse
24. [23-runbook-unexpected-import-files-or-bytecode.md](23-runbook-unexpected-import-files-or-bytecode.md): Unexpected import files or bytecode
25. [24-runbook-configured-never-becomes-active.md](24-runbook-configured-never-becomes-active.md): CONFIGURED never becomes ACTIVE
26. [25-runbook-check-manifest-failure.md](25-runbook-check-manifest-failure.md): check-manifest failure
27. [26-runbook-release-hygiene-failure.md](26-runbook-release-hygiene-failure.md): Release-hygiene failure
28. [27-runbook-publication-scan-failure.md](27-runbook-publication-scan-failure.md): Publication-scan failure
29. [28-runbook-pre-push-gate-failure.md](28-runbook-pre-push-gate-failure.md): Pre-push gate failure (ends with the Appendix A heading)
30. [29-runbook-full-reads-at-7fcaeece.md](29-runbook-full-reads-at-7fcaeece.md): Full reads at 7fcaeece
31. [30-runbook-other-revisions-and-partial-coverage.md](30-runbook-other-revisions-and-partial-coverage.md): Other revisions and partial coverage (also holds the closing Unverified until publish section)

Each part is capped at 1,200 words by release hygiene. The parts may be rejoined when AWF-200 moves the cap to 1,200 words per section.
