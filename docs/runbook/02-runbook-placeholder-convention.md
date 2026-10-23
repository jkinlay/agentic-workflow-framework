### Placeholder convention

The following are operator-selected absolute locations, **not shipped directory names**:

- `<repo-path>`: the isolated adoption/upgrade checkout.
- `<install-dir>`: an external installation workspace, separate from the governed repository.
- `<python>`: the approved base Python executable.
- `<release-manifest-sha256>`: the independently approved release-manifest pin.
- `<owner>/<repo>`, `<numeric-id>`, `<default-branch>`, `<codeowner>`, and `<project-test-command>`: observed project values.

The example subdirectories under `<install-dir>` are operator choices. Their exact layout is **UNVERIFIED**; the supported separation requirements are established by `7fcaeece:.agentic/lib/agentic/installer.py:1624`, `7fcaeece:.agentic/lib/agentic/release_trust.py:24`, and `7fcaeece:global/awf-portable/scripts/prepare_release.py:183`.

