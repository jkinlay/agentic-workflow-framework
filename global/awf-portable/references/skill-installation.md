# Install or upgrade

Use Python 3.11+ and the trusted distribution's `install_awf.py`, first with `--dry-run`. The launcher verifies the helper/manifest; obtain the outer ZIP digest independently.

Alternatively run `scripts/install_skill.py --expected-manifest-sha256 TRUSTED_DIGEST`. Use `--dest ABSOLUTE_SKILLS_DIRECTORY/awf` to select a copy. Default discovery prefers the existing user `awf`.

A fresh installation into an empty skills root needs no earlier AWF skill. When upgrading, do not uninstall first. The helper verifies exact inventory, stages replacement, and writes the complete `awf-host-skill-trust-1` receipt before atomically replacing the skill directory. The receipt binds version, skill-manifest digest, every packaged path/digest and every preserved-local path/digest outside any candidate repository. It preserves catalog/update-channel/local settings and retains the complete old folder outside scanned skills directories. Custom `--preserve-relative` selections persist in receipts. Identical reruns verify without replacement; unknown/conflicting/newer versions fail closed.

Installed project `workflow.py status --json` re-verifies that receipt, every inventory member and the bundled release. It reports the resolved Codex home and host-skill path and needs no temporary release source or wrapper. Never copy a second AWF implementation into the governed project.

Backups must share the destination filesystem. Failed replacement attempts restore the old folder; inspect the reported backup if rollback fails. Locks/stages after a crash require deliberate recovery, not blind deletion. Duplicate copies/plugins are reported, not removed. Refresh discovery on the next turn or start a new task. Projects remain unchanged.
