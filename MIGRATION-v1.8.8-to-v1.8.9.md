# Upgrade AWF 1.8.8 to 1.8.9

Verify the new source/distribution pins, install the portable skill with its verified launcher, and use the existing adoption flow to prepare the project draft PR. Retain the previous skill backup, project configuration, operating pins and audit evidence. The owner merges governance changes; skill installation alone does not migrate a project.

## Operating ceilings

`operating show` and the operating state supplied to agents expose a computed `effective_ceiling` with `effective_ceiling_governance_path`; `effective_ceiling_sources` retains tied limits. Use this current result when translating a requested stream count. Limits under `execution.host_broker` bind only when `execution.host_broker.enabled` is true. A disabled broker's stored `max_workers: 3` must not cause refusal of four streams under a project ceiling of six.

Configured ceilings do not prove free host slots or authorize a launch. Preserve observed shared capacity, independent reviewers, existing reservations and legacy reviewer constraints. This patch does not raise accepted governance limits or enable the broker.

An enabled broker's lower worker limit now also bounds operating validation and native planning. Existing operating counts above that limit are rejected with the binding path, never silently clamped. Review the count and broker policy during adoption; changing protected broker limits still requires the governance PR. The planner's structural six-stream limit remains explicit when it alone binds.

## Recommendation display

Unchanged pinned routes appear in a compact footnote instead of repeated no-change table rows. Pins stay unchanged unless the user explicitly edits them. Mandatory risk and review floors still apply. Real recommendations remain visible and require acceptance; request fresh recommendations after adopting the new version.

## Evidence and separate project work

The original 1.8.8 pilot remains eight completed, substantive 7/8 FAIL and original strict 3/8 FAIL. NATIVE-40 incorrectly treated a disabled broker limit as active. This patch changes the information presented to agents and adds deterministic regression checks; it has no new model measurement and does not regrade historical responses.

Project-specific CI fixes, workflow identity pins, live canary qualification and ownership remain separate from this portable release. Existing cross-version bootstrap uses verified arguments with `--mode install --on-conflict backup`; add `--propose-operating-capacity` only when staging that explicit proposal for owner review.
