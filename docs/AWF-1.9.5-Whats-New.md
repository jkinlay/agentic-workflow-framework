# AWF 1.9.5 - What's New (summary)

## Overview

This summary covers the release-candidate inventory through `7fcaeece364531da65ca1b8293ebd7643bf264ed`, dated 20 October 2026. That cutoff declares `1.9.4`; publication and integrated 1.9.4-to-1.9.5 upgrade acceptance remain **UNVERIFIED**. The repository-only full text preserves historical source wording, including supplied-snapshot `main/...` observations; those are not current-base verification.

## Changes covered by the inventory

- **Install, upgrade and adoption:** interrupted-upgrade recovery, malformed-recovery handling, stale-bytecode tolerance, Windows path identity fixes, project-owned launch declarations and installed-runtime checks. Review-policy migration adds defaults while preserving configuration. These changes do not prove integrated upgrade acceptance.
- **Review, provenance and merge train:** risk-based tiers, per-ticket caps, provider-verified owner review, writer/critic provenance and workflow-content CI binding. Batch construction, publication, orphan recovery and immutable audit retain separate candidate evidence. Validation or green CI alone cannot authorize marking a PR ready; source-PR review cannot authorize a batch merge.
- **Recovery and safety:** task-bound external-resource admission, history-aware publication scans, tracked path-and-digest large-file allowances, diagnostic redaction, bounded subprocess callers and input reads, and complete-envelope limits for publication records and host run logs. Remaining bounded-I/O work is not established as complete.
- **Controller and planning:** cadence controls, readiness reporting, automation switches, backlog planning primitives, authenticated capacity observations, safe zero-capacity plans and durable planning history. Production planning triggers remain pending.
- **Tooling and routing:** configurable model routing, manifest synchronization, safer release credential routing and broader portable/Windows CI coverage. Invalid routing inputs refuse rather than silently falling back.

## Upgrade and activation

INSTALLED establishes receipt consistency; CONFIGURED adds accepted configuration and successful installed verification/validation with matching digests. ACTIVE requires independently trusted release bytes and observed acceptance of receipt-changing adoption on the current default branch.

Use absolute installed canonical runtime/script paths and an explicit root:

```text
<installed-canonical-runtime> -B -I <installed-workflow.py> --root <repo-path> status --require-active --release-source <verified-release-source> --expected-manifest-sha256 <independently-approved-pin>
```

The verified release source must be outside the project. Add `--adoption-pr <receipt-changing-pr-number>` when needed. Omitting both explicit source/pin arguments is supported only with a valid, complete, matching trusted host-skill installation receipt and verified inventory at `$CODEX_HOME/skills/awf` (default `~/.codex/skills/awf`); an arbitrary project receipt does not authenticate the publisher. Exit 4 means below ACTIVE; exit 2 means invalid input or no established state. Inspect capabilities separately: ACTIVE does not establish adapter readiness or enable live capabilities.

## Upgrade and operating cautions

Quiesce all five automation switches before installation or upgrade. Preserve project instructions, configuration, ownership, backups and recovery evidence. Review policy/routing inputs and retain actual installed verification results. Live review hosts still require qualification evidence. Stricter provenance, authenticated draft-state authority, durable dispatch bindings and serialized-record limits can reject previously accepted workflows; supplemental configuration cannot grant large-file allowance authority.

## Pending and deferred work

The historical inventory excludes pending #219 (planning), #205 (cross-owner Git), #198/#197/#191 (provenance tests) and #192 (status-report tests) from merged claims. The AWF-54 canonical-key successor, d2-006d and unwritten POSTSUITE-001 successor do not establish integrated release acceptance. AWF-50, AWF-54, AWF-55, AWF-97 and AWF-117 remain incomplete in that evidence.

Deferred 1.9.6 work includes rendered-verdict verification, controller freeze rules, legacy batch-audit migration, standalone merge-train CLI, Windows filesystem/timeout hardening, remaining bounded capture, Jira UNKNOWN recovery, changelog pagination and load-sensitive timing. AWF-76's merged stall/App/closeout work is distinct from its deferred changelog follow-up.

## Known limitations

Release publishing is not supported on macOS in 1.9.5. The release step fails closed because the host lacks atomic no-follow release materialization primitives. Publish from Windows or Linux.

Post-#249, the reference adapter supplies the cycle CLI's planning operations and passes its adapter check on fresh installs and upgrades from 1.9.4 (#249 tests: `.agentic/tests/test_reference_controller_adapter.py:1652`, `.agentic/tests/test_reference_controller_adapter_install.py:133`, `:143`; Task 55 dry run, 25 October 2026 16:33 GMT). Successful current-controller operation remains UNVERIFIED.

Historical publication blockers include the controller assertion conflicting with cadence 600, undefined `source_external_unit`/`installed_external_unit` report inputs and complete report serialization, exact AC42 migration-delta acceptance, and uncovered required admission checks. Their current status is UNVERIFIED; preserve cadence 600. Consistent release identities, predecessor registration, reproducible outputs, final manifests, independent pins, candidate-bound reviews, platform evidence and publication acceptance remain required.

## Further reading

[Install and adoption runbook](runbook/INDEX.md)

[Full What's New text (repository-only; not included in the release ZIP)](https://github.com/jkinlay/agentic-workflow-framework/blob/8ad399943c8f5e4f17db72f3e321b9365ee27782/docs/showcase/AWF-1.9.5-Whats-New.md)

The companion draft was supplied after the cutoff review, where it was absent. Its historical link remains provisional; the current operational route is the index above. Supplied documentation does not certify integrated upgrade acceptance.
