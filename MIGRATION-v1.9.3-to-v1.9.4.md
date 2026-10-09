# Upgrade AWF 1.9.3 to 1.9.4

This fallback carries the AWF-29 review-loop preparation path. AWF-16 and AWF-30 are deferred to 1.9.5; AWF-41 remains an owner-review check. AWF-40 (#68) fixes the doc 22 automated-review-loop guidance. Existing 1.9.3 configurations remain valid; the 1.9.3 → 1.9.4 upgrade is version-only and adds no tier-3 review configuration.

Run the normal locked, offline upgrade command from verified 1.9.4 source with the approved manifest digest. A verified 1.9.3 receipt is accepted directly. The upgrade changes only `template.expected_workflow_version` from 1.9.3 to 1.9.4 and refreshes the receipt/provenance; owner configuration, comments, ordering, line endings, budgets, routes and retained state are preserved.

Review-loop source-repository allowlisting, strict critic schema fields, role reasoning effort and explicit Codex overrides remain opt-in and validated by the reviewed host configuration. AWF-16 and AWF-30 remain deferred to 1.9.5, pending the applicable owner review.

Known issues: AWF-34 and AWF-35 (accepted for 1.9.4 installs, fix in 1.9.5).

- AWF-34: Publication scan can never PASS on a repo with an existing tracked file over 32 MB: add a recorded allowance or baseline.
- AWF-35: self_test fails on project-owned scripts not listed in install-pinned .agentic/launch-surfaces.json.
