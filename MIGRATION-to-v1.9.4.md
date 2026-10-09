# Direct migration to AWF 1.9.4

Use the verified 1.9.4 release manifest and the standard bootstrap upgrade flow. Projects with a valid 1.9.3 receipt upgrade through the version-only step in [the detailed migration](MIGRATION-v1.9.3-to-v1.9.4.md); older supported receipts use the registered adjacent chain. Preserve owner configuration and state, and review the Tier 1–3 policy before enabling any host adapter.

The 1.9.3 → 1.9.4 fallback is version-only and adds no tier-3 review configuration. AWF-16 and AWF-30 are deferred to 1.9.5; AWF-41 remains an owner-review check. AWF-40 (#68) fixes the doc 22 automated-review-loop guidance.

Known issues: AWF-34 and AWF-35 (accepted for 1.9.4 installs, fix in 1.9.5).

- AWF-34: Publication scan can never PASS on a repo with an existing tracked file over 32 MB: add a recorded allowance or baseline.
- AWF-35: self_test fails on project-owned scripts not listed in install-pinned .agentic/launch-surfaces.json.
