# Install or upgrade

Use Python 3.11+ and the trusted distribution's `install_awf.py`, first with `--dry-run`. The launcher verifies the helper/manifest; obtain the outer ZIP digest independently.

Alternatively run `scripts/install_skill.py --expected-manifest-sha256 TRUSTED_DIGEST`. Use `--dest ABSOLUTE_SKILLS_DIRECTORY/awf` to select a copy. Default discovery prefers the existing user `awf`.

Do not uninstall first. The helper verifies exact inventory, stages replacement, preserves catalog/update-channel/local settings and retains the complete old folder outside scanned skills directories. Custom `--preserve-relative` selections persist in receipts. Identical reruns verify without replacement; unknown/conflicting/newer versions fail closed.

Backups must share the destination filesystem. Failed replacement attempts restore the old folder; inspect the reported backup if rollback fails. Locks/stages after a crash require deliberate recovery, not blind deletion. Duplicate copies/plugins are reported, not removed. Refresh discovery on the next turn or start a new task. Projects remain unchanged.
