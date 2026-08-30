# Upgrading a Project Instance

## Principle

An AWF upgrade is a reviewable project change, not an automatic overwrite.

## Version 0.1.1 upgrade note

Version 0.1.1 corrects downstream path wording and terminal whitespace in the
Copilot distribution. It does not change review-gate execution, permissions,
schema, qualification semantics, or reviewer identity.

Projects upgrading from 0.1.0 should:

1. verify the installed 0.1.0 hashes before changing any managed target;
2. update the managed `docs/agent-runtime/review-policy.md`,
   `.github/instructions/awf-review.instructions.md`, and
   `.github/workflows/awf-review-gate.yml` targets from the 0.1.1 sources;
3. apply the terminal-blank-line removal to the merge-assisted
   `.github/copilot-instructions.md` only after confirming that any project
   content remains preserved;
4. regenerate `.agentic/workflow.lock.yaml` from the final staged tree and the
   immutable 0.1.1 source commit; and
5. run normal project CI and independent review.

The owner-authorized [issue #6](https://github.com/jkinlay/agentic-workflow-framework/issues/6)
defines four cosmetic defect fixes: the installed schema path, the gate's
source/installed policy-path comment, and one terminal blank-line removal in
each of the two Copilot instruction sources. None changes reviewer-instruction
content, so this is not a material engine/instruction change under the
measurement rule in `docs/REVIEW_POLICY.md` and existing qualification evidence
may be retained. The upgrade itself still requires a current-head review and
normal human approval. Any later reviewer-instruction content change requires
the seeded benchmark and affected qualification evidence to be rerun.

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
