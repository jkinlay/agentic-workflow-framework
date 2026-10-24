### Portable skill

The installer stages and verifies replacement, moves the prior complete directory to an external backup, and restores it if the replacement rename fails. Backups must share the destination filesystem. Sources: `7fcaeece:global/awf-portable/scripts/install_skill.py:340`; `7fcaeece:global/awf-portable/tests/test_install_skill.py:213`; `7fcaeece:global/awf-portable/references/skill-installation.md:11`.

Manual restoration after a completed skill upgrade is **UNVERIFIED** as a CLI procedure. The installer rejects downgrading a newer installed skill; reinstalling an older package is not a documented rollback shortcut. Source: `7fcaeece:global/awf-portable/scripts/install_skill.py:302`.
