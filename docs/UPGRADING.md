# Upgrading a Project Instance

## Principle

An AWF upgrade is a reviewable project change, not an automatic overwrite.

## Upgrade procedure

1. Read the installed version from `.agentic/workflow.lock.yaml`.
2. Compare every installed artifact with the new distribution manifest.
3. Verify installed hashes before updating `managed` artifacts; stop on drift.
4. Leave `seed-once` and `local-only` artifacts unchanged.
5. For `merge-assisted` artifacts, produce a proposed diff with rationale.
6. Re-run provider, schema, repository, confidentiality, and secret checks.
7. Regenerate the lock file only after the upgrade diff is complete, including
   new source and installed hashes. A lock file is output, never a copied
   scaffold.
8. Submit the upgrade through normal project review and CI.

## Conflict policy

- Never discard a project-local change to resolve an upgrade conflict.
- Never weaken project rules to match a generic scaffold.
- A project may defer an upgrade and record the reason.
- Security-critical updates still require project-owner review; urgency does
  not authorize silent mutation.

## Rollback

Rollback uses ordinary version-control reversal of the reviewed upgrade change.
Do not implement a hidden mutable cache or out-of-band restoration mechanism.
