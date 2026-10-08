# Upgrade AWF 1.9.3 to 1.9.4

This release adds the AWF-16 tier policy and the AWF-29 review-loop preparation path. AWF-30 is deferred to 1.9.5. AWF-40 (#68) fixes the doc 22 automated-review-loop guidance. Existing 1.9.3 configurations remain valid; the 1.9.3 → 1.9.4 upgrade is version-only. The 1.9.4 migration step adds the optional `tier3_review` review-tier defaults; it is not added to `new_required_settings`. This AWF-16 behavior is provisional until PR #64 merges.

Run the normal locked, offline upgrade command from verified 1.9.4 source with the approved manifest digest. A verified 1.9.3 receipt is accepted directly. The upgrade changes only `template.expected_workflow_version` from 1.9.3 to 1.9.4 and refreshes the receipt/provenance; owner configuration, comments, ordering, line endings, budgets, routes and retained state are preserved.

Tier policy defaults are Tier 1: one independent PASS; Tier 2: up to three rounds, with owner escalation for an open P1 at the cap; and Tier 3: the full loop plus Jonathan's review. Highest matching tier wins. Review-loop source-repository allowlisting, strict critic schema fields, role reasoning effort and explicit Codex overrides remain opt-in and validated by the reviewed host configuration. AWF-30 remains deferred to 1.9.5.

Known issues: AWF-34 and AWF-35 (accepted for 1.9.4 installs, fix in 1.9.5).
