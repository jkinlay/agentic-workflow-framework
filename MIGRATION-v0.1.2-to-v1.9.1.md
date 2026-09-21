# Public upstream migration: 0.1.2 to 1.9.1

## Scope

This is a major migration from the documentation-first 0.1.2 scaffold to the
1.9.1 Agentic Workflow Framework runtime. Git history preserves the retired
scaffold; this release establishes the new public upstream source tree.

## What changes

- Versioned contracts, lifecycle validation, offline gates, deterministic
  digests, release tooling, host preflight, and provider adapters replace the
  earlier manual-only scaffold.
- Review tiers, finite review-cap disposition, closeout binding, lifecycle
  mirroring, local/CI parity, and named resource leases are available through
  the provider-neutral contract layer.
- The port removes product-specific references, uses neutral resource fixtures,
  and contains no machine-local paths, credentials, private issue content, or
  raw execution logs.

## Upgrade and release conditions

This source port is not a release publication or an activation of any adopter.
Regenerate `MANIFEST.json` after any local change, run the complete self-test,
and obtain a fresh independent current-head review plus archive acceptance
before publication. Projects adopt or upgrade through the normal reviewed
workflow and retain their own instructions, configuration, ownership, and
human approval requirements.
