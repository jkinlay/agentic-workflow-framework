# Change history

## 1.9.3 — 9 October 2026

Adds Git-format-aware `tested_tree`; undeclared changes fail or are excluded, and the publisher's `HEAD^{tree}` must match.

Defaults: workers use `gpt-5.6-luna` / medium (simple work: low); the shared critic/adversarial handler, controller and specialist use `gpt-5.6-sol` / high, also the review/risk floor. Escalation: `gpt-5.6-terra` / high, then `gpt-5.6-sol` / high. Five slots cover three workers, critic and controller. Caps, adopted configurations and legacy models/routes, including `gpt-6-*`, remain accepted. New adoptions use a 16-run ticket ceiling; upgrades retain theirs. Release trust binds host receipts and verified archives; ACTIVE requires fresh default-branch status.

Adds outcome templates; verdicts remain separate. Adds byte-verified install/upgrade; unsafe evidence fails closed.

Scans messages, patches, files and PR text; findings block, and receipts bind base/head/body. An ignored mapping supplements built-in detectors.

Adds one-commit unpublished-branch rewrite with tree preservation, replacement scanning and ref-reachability checks; published evidence is refused.

Hardens activation with versioned merge identity, PR/base/head cross-checks, Git-object checkout comparison and strict ACTIVE exit.

Adds K8-K11 Windows diagnostics, preflight, safe encoding and rollback-safe runtimes from pinned offline wheels.

Adds environment exclusions, launch inventory, provider identity gates, Jira binding proposals, publication readiness and resumable owner-publication handoffs.

Upgrades tolerate a venv `lib64` → `lib` link (#54). `gh release` binds `--repo`; origin is validated before tagging (#55). **[PENDING: #52, AC42 evidence]**

### Known issues (1.9.4)

- AWF-18 (#22): Release tooling forces `core.autocrlf=true` on Windows, overriding repo setting.
- AWF-19 (#23): Upgrade: crash during `managed_after` journal update can't be rolled back.
- AWF-20 (#47): Source-repo detection: a downstream project with a root `MANIFEST.json` is treated as the AWF source.
- AWF-21 (#48): `doctor --handoff` can MATCH an incomplete snapshot; `host_broker` read from wrong config location.
- AWF-22 (#51): AC42 can pass without the admission check; ACTIVE check uses the source checkout, not the installed `workflow.py`.
- AWF-23: Heavy validation: support POSIX hosts and symlinked managed-venv interpreters (`.agentic/.venv/bin/python`).

## 1.9.2 — 23 September 2026

Adds token-only budgets, higher new-project caps, publisher commits for sandboxes unable to write Git metadata, lint-scope preflight and portable skills. Child processes exclude provider API keys. Release builds reject temp residue and forbid `.tmp-tests` links. Budgets reflect expected ticket volume and prior AWF workloads; existing project configuration remains preserved, and review, merge, reconciliation and Jira boundaries stay unchanged. No model pilot.

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

[Ordinary self-tests separated from release qualification, adoption warnings, rulesets, CODEOWNERS and read-only observations](MIGRATION-v1.8.4-to-v1.8.5.md). Prior release/pilot evidence, including the strict 10/12 FAIL, is preserved.

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
