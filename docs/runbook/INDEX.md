# Runbook index

The AWF 1.9.5 install and adoption runbook is kept in 41 ordered repository parts, each retaining its initial heading. Sections 08–23 are the AWF-177 install/adoption reconciliation destination; use this index for the complete runbook. The **historical original split identity**, split byte-exact at every level-2 and level-3 heading without rewording under rulings jk-direct-1657 and jk-direct-1715, was SHA-256 956d39cab43ce8bfee823af6196aeda9fc21db2836e8d5537239acf77f81409c, 71,546 bytes. Subsequent scoped edits do not retain that original byte identity.

1. [00-runbook-introduction.md](00-runbook-introduction.md): AWF 1.9.5 Install and Adoption Runbook
2. [01-runbook-prerequisites.md](01-runbook-prerequisites.md): 1. Prerequisites
3. [02-runbook-required-inputs.md](02-runbook-required-inputs.md): Required inputs
4. [03-runbook-placeholder-convention.md](03-runbook-placeholder-convention.md): Placeholder convention
5. [04-runbook-authority-and-quiescence.md](04-runbook-authority-and-quiescence.md): Authority and quiescence
6. [05-runbook-getting-v195.md](05-runbook-getting-v195.md): 2. Getting v1.9.5
7. [06-runbook-publication-checkpoint.md](06-runbook-publication-checkpoint.md): Publication checkpoint
8. [07-runbook-distribution-and-source-preparation.md](07-runbook-distribution-and-source-preparation.md): Distribution and source preparation
9. [08-runbook-install-to-active.md](08-runbook-install-to-active.md): 3. INSTALL TO ACTIVE
10. [09-runbook-repository-states-and-installation-results.md](09-runbook-repository-states-and-installation-results.md): Repository states and installation results
11. [10-runbook-windows-powershell.md](10-runbook-windows-powershell.md): Windows — PowerShell
12. [11-runbook-macos-posix-shell.md](11-runbook-macos-posix-shell.md): macOS — POSIX shell
13. [12-runbook-linux-posix-shell.md](12-runbook-linux-posix-shell.md): Linux — POSIX shell
14. [13-runbook-verifying-with-installed-self-test.md](13-runbook-verifying-with-installed-self-test.md): 4. Verifying with the installed self-test
15. [14-runbook-upgrade.md](14-runbook-upgrade.md): 5. UPGRADE
16. [15-runbook-supported-starting-installations.md](15-runbook-supported-starting-installations.md): Supported starting installations
17. [16-runbook-pre-upgrade-checks-and-backup.md](16-runbook-pre-upgrade-checks-and-backup.md): Pre-upgrade checks and backup
18. [17-runbook-upgrade-commands.md](17-runbook-upgrade-commands.md): Upgrade commands
19. [18-runbook-migration-behavior.md](18-runbook-migration-behavior.md): Migration behavior
20. [19-runbook-journal-recovery.md](19-runbook-journal-recovery.md): Journal recovery
21. [20-runbook-post-upgrade-verification-and-reactivation.md](20-runbook-post-upgrade-verification-and-reactivation.md): Post-upgrade verification and reactivation
22. [21-runbook-upgrade-rollback.md](21-runbook-upgrade-rollback.md): Upgrade rollback
23. [22-runbook-post-adoption-checks.md](22-runbook-post-adoption-checks.md): 6. Post-adoption checks
24. [23-runbook-rollback-install-adoption.md](23-runbook-rollback-install-adoption.md): 7. Rollback — install/adoption
25. [24-runbook-portable-skill.md](24-runbook-portable-skill.md): Portable skill
26. [25-runbook-project-installation.md](25-runbook-project-installation.md): Project installation
27. [26-runbook-troubleshooting.md](26-runbook-troubleshooting.md): 8. Troubleshooting
28. [27-runbook-windows-short-name-aliases.md](27-runbook-windows-short-name-aliases.md): Windows temporary short-name aliases versus resolved paths
29. [28-runbook-winerror-1314-symlink-privilege.md](28-runbook-winerror-1314-symlink-privilege.md): WinError 1314 — symlink privilege
30. [29-runbook-orig-head-lock-or-sandbox-refusal.md](29-runbook-orig-head-lock-or-sandbox-refusal.md): `ORIG_HEAD.lock` or sandbox refusal to write Git metadata
31. [30-runbook-missing-or-incompatible-wheelhouse.md](30-runbook-missing-or-incompatible-wheelhouse.md): Missing or incompatible wheelhouse
32. [31-runbook-unexpected-import-files-or-bytecode.md](31-runbook-unexpected-import-files-or-bytecode.md): Unexpected import files or bytecode
33. [32-runbook-configured-never-becomes-active.md](32-runbook-configured-never-becomes-active.md): CONFIGURED never becomes ACTIVE
34. [33-runbook-check-manifest-failure.md](33-runbook-check-manifest-failure.md): `check-manifest` failure
35. [34-runbook-release-hygiene-failure.md](34-runbook-release-hygiene-failure.md): Release-hygiene failure
36. [35-runbook-publication-scan-failure.md](35-runbook-publication-scan-failure.md): Publication-scan failure
37. [36-runbook-pre-push-gate-failure.md](36-runbook-pre-push-gate-failure.md): Pre-push gate failure
38. [37-runbook-appendix-a-source-read-inventory.md](37-runbook-appendix-a-source-read-inventory.md): Appendix A — Source-read inventory
39. [38-runbook-full-reads-at-7fcaeece.md](38-runbook-full-reads-at-7fcaeece.md): Full reads at `7fcaeece`
40. [39-runbook-other-revisions-and-partial-coverage.md](39-runbook-other-revisions-and-partial-coverage.md): Other revisions and partial coverage
41. [40-runbook-unverified-until-publish.md](40-runbook-unverified-until-publish.md): Unverified until publish

Each part is capped at 1,200 words by release hygiene. The parts may be rejoined when AWF-200 moves the cap to 1,200 words per section.

**Current joined-content identity:** SHA-256 f2183d1b8246039a4b25eb018364b684c0a374befdef462bb733d307eb9ac8ae, 81,260 bytes. Join all 41 parts in the order above, each with exactly one terminal LF; exclude INDEX itself. This content identity does not establish release qualification.
