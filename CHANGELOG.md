# Change history

## 1.9.3 — 24 September 2026

Adds Git-format-aware `tested_tree` for publisher-routed workers. Declared changes form the candidate; undeclared changes fail or are excluded, and the publisher's `HEAD^{tree}` must match exactly.

Sets new-adoption risk and specialist routes to `gpt-5.6-sol` / high. Expiring capability observations feed warning-only route preflight; existing owner-set routes persist.

Adds settlement outcomes and `route_model.py outcome-template`; verdicts remain separate records.

Adds byte-verified installation and upgrade from 1.8.3, 1.8.9, 1.9.1 and 1.9.2; unsafe inputs make no changes.

Adds history-aware publication scanning of messages, patch lines, changed files, PR bodies and comments. Redacted findings block; receipts bind base, head and body digest. An ignored local mapping supplies aliases and deny entries alongside built-in path/network detectors and alias rendering.

Adds an atomic one-commit rewrite for unpublished branches, preserving `HEAD^{tree}`, scanning the replacement and checking old ref reachability. It refuses published evidence or unsupported counts and reports reflog retention. This covers L1, L2 and L9 scanning; producer retrofits remain P1.

## 1.9.2 — 23 September 2026

Adds token-only host budgets with larger new-project token/run defaults, an exact-tree publisher commit route for workers unable to write Git metadata, project lint-scope preflight, and the repository-owned portable skill source. Host child processes now consistently exclude Anthropic, Codex and OpenAI API keys. Release builds reject `.tmp`/`tmp` path components and `*.tmp` files before rewriting manifests or creating an archive, while retaining `.tmp-tests` as an excluded local test area whose links remain forbidden. The defaults reflect an owner workload of 10–20 tickets a day, signal-lab SL-1's 282,975 tokens in 3 runs, the 77-file AWF 1.9.2 PR's 1,402,537 tokens in 6 runs across three critic rounds, and 8–9 runs for a full ticket. Existing project configuration remains preserved on upgrade; review, merge, reconciliation and Jira boundaries are unchanged. No new model pilot.

## 1.9.1 — 21 September 2026

Corrects the adoption-status regression test for Windows host-preflight WARN rows. Runtime behavior is unchanged: those rows remain non-blocking and remain the status next action. No new model pilot.

## 1.9.0 — 21 September 2026

[Review tiers, cap dispositions, Jira lifecycle mirroring, closeout binding, digests, host preflight and named resource leases](MIGRATION-v1.8.9-to-v1.9.0.md). This is a provider-neutral framework release; no new model pilot.

The public upstream port from the earlier 0.1.x scaffold uses neutral resource
fixtures and excludes product-specific material. Its contract semantics match
the verified 1.9.1 source; the port has its own regenerated manifest and
current-head review requirements. See [the port migration](MIGRATION-v0.1.2-to-v1.9.1.md).

## 1.8.9 — 16 September 2026

[Ceilings and compact recommendations](MIGRATION-v1.8.8-to-v1.8.9.md); no new pilot.

## 1.8.8 — 15 September 2026

[Glob scopes, preserved pins, ignored transient state, optional upgrade governance proposals and routine metrics](MIGRATION-v1.8.7-to-v1.8.8.md). Historical pilot grades remain unchanged.

## 1.8.7 — 15 September 2026

[Operating settings, scoped routes and routine-flow fixes](RED-TEAM-DISPOSITION-v1.8.7.md).

## 1.8.6 — 14 September 2026

Bootstrap checks installed commands and reports configuration remedies. Empty CI and disabled Jira permit adoption without live authority. Status distinguishes INSTALLED, CONFIGURED and independently verified ACTIVE.

## 1.8.5 — 14 September 2026

Ordinary self-tests run without release-review pins and report unqualified component success; explicit release checks, archive acceptance and catalog publication retain current-review requirements. Adoption proceeds with missing/unobserved repository rules as warnings. Live enablement retains observed-rule prerequisites and existing qualification. A shipped ruleset follows the default branch, requires PRs without solo-maintainer self-approval, permits squash/rebase and starts with no required checks. CODEOWNERS seeds a configurable owner, defaults to @maintainer and preserves project-owned policy. Read-only observations distinguish APPLIED, MISSING and UNOBSERVED without granting execution authority. New regression coverage and complete portable packaging preserve prior release/pilot evidence, including the strict 10/12 FAIL result; changed prompts have no new model measurements.

## 1.8.4 — 14 September 2026

Consistent current-review evidence for release qualification; a versioned native decision contract with exclusive-decision checks; explicit historical grading of failed pilots.

## 1.8.3 — 14 September 2026

Stable retry-limit approvals with first-use expiry; per-file current-release review records; mojibake hygiene; gate wake and HTTPS endpoint hardening; new model pilot cases fixed before responses.

## 1.8.2 — 14 September 2026

External operator test command in archive acceptance; supervised model-phase deadlines and output caps; trusted workflow wake of the protected PR gate; audited retry ceilings; a bounded actual-model decision evaluator.

## 1.8.1 — 14 September 2026

External-review hardening (structured transport failures, input limits, untrusted candidate contracts, bounded timing); reconciliation cap increases through a trusted-host ledger operation; accurate description of the native smoke harness.

## 1.8.0 — 13 September 2026

Finite reconciliation retries, preserved policy-hash cohorts, substantive native prompts, the restored split external model/publisher adapter, focused runbooks with per-file word budgets and CLI failure-path tests.

## 1.7.0 — 13 September 2026

Red-team hardening, three independent reviewers (one per workstream), consolidated documentation and Apache-2.0 core distribution.

## Earlier releases

The verified 1.6 archive preserves the full historical changelog, migrations and review dispositions. Its source ZIP SHA-256 is `b6d9302f72feb0860d069fa23cf547b970c727fa84a3dc84f2f860c83e5836f2`. Historical evidence is not current operating policy.
