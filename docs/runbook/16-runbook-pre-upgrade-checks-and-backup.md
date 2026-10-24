### Pre-upgrade checks and backup

1. Complete the §2 publication/pin checkpoint and portable-skill update. **Do not uninstall the previous skill first.** The skill installer preserves selected local settings and backs up the previous complete skill directory. Sources: `7fcaeece:global/awf-portable/references/skill-installation.md:7`; `7fcaeece:global/awf-portable/tests/test_install_skill.py:60`.

2. Record the existing accepted version, receipt, provenance, project identity, current state, operating configuration, project instructions, CODEOWNERS policy, project-test results, and pending recovery artifacts. The adoption checklist requires observed previous version, commands, state, residue, and rollback location. Source: `7fcaeece:.agentic/templates/adoption-pr.md:7`.

3. Pause the five controller switches listed in §1 and arrange exclusive ownership. Source: `7fcaeece:.agentic/lib/agentic/installer.py:801`.

4. Preserve an independent recoverable backup, including ignored state and recovery material. **A complete OS-specific backup/restore command is UNVERIFIED.** The installer’s `--on-conflict backup` saves changed original files under a transaction-specific backup directory; it is not demonstrated as a complete project/runtime backup. Sources: `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:21`; `7fcaeece:.agentic/lib/agentic/installer.py:1889`.

5. Recover unfinished installation before planning a new upgrade. Existing intent rejects another installation. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1701`.
