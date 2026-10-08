# Change history

## 1.9.4 — 8 October 2026

AWF-15 (#56) binds closeout records to the merge SHA. AWF-29 fixes protected-path refusal, critic-schema compatibility, and sandbox/effort pass-through. AWF-40 (#68) fixes doc 22 guidance. AWF-16 and AWF-30 are deferred to 1.9.5; AWF-41 remains an owner-review check. The 1.9.3 → 1.9.4 fallback is version-only and ships no tier-3 review configuration.

### Known issues (1.9.5)

- AWF-24 (#52): AC42 Windows fixes moved to 1.9.5; the caveat still applies.
- AWF-31: operator qualification flags.
- AWF-32: missing controller adapter.
- AWF-33: sessions can pick up an older skill.
- AWF-18 (#22): Release tooling forces `core.autocrlf=true` on Windows, overriding repo setting.
- AWF-19 (#23): Upgrade: crash during `managed_after` journal update can't be rolled back.
- AWF-21 (#48): `doctor --handoff` can MATCH an incomplete snapshot; `host_broker` read from wrong config location.
- AWF-22 (#51): AC42 can pass without the admission check; ACTIVE check uses the source checkout, not the installed `workflow.py`.
- AWF-23: Heavy validation: support POSIX hosts and symlinked managed-venv interpreters (`.agentic/.venv/bin/python`).
- AWF-34: Publication scan can never PASS on a repo with an existing tracked file over 32 MB: add a recorded allowance or baseline.
- AWF-35: self_test fails on project-owned scripts not listed in install-pinned .agentic/launch-surfaces.json.

Known issues: AWF-34 and AWF-35 (accepted for 1.9.4 installs, fix in 1.9.5).

## 1.9.3 — 8 October 2026

Adds Git-aware `tested_tree`, bounded routing, byte-verified upgrades, publication scanning and strict release trust; unsafe evidence fails closed.

Upgrades tolerate a venv `lib64` → `lib` link (#54). `gh release` binds `--repo`; origin is validated before tagging (#55).

### Known issues (1.9.4)

- AWF-18 (#22): Release tooling forces `core.autocrlf=true` on Windows, overriding repo setting.
- AWF-19 (#23): Upgrade: crash during `managed_after` journal update can't be rolled back.
- AWF-20 (#47): Source-repo detection: a downstream project with a root `MANIFEST.json` is treated as the AWF source.
- AWF-21 (#48): `doctor --handoff` can MATCH an incomplete snapshot; `host_broker` read from wrong config location.
- AWF-22 (#51): AC42 can pass without the admission check; ACTIVE check uses the source checkout, not the installed `workflow.py`.
- AWF-23: Heavy validation: support POSIX hosts and symlinked managed-venv interpreters (`.agentic/.venv/bin/python`).

## 1.9.2 — 23 September 2026

Adds token-only budgets, publisher commits for metadata-limited sandboxes, lint-scope preflight and portable skills. Existing configuration and review, merge, reconciliation and Jira boundaries remain preserved. No model pilot.

## 1.9.1 — 21 September 2026

Corrects the adoption-status regression test for Windows host-preflight WARN rows. Runtime behavior is unchanged: those rows remain non-blocking and remain the status next action. No new model pilot.

## 1.9.0 — 21 September 2026

[Review tiers, cap dispositions, Jira lifecycle mirroring, closeout binding, digests, host preflight and named resource leases](MIGRATION-v1.8.9-to-v1.9.0.md). This is a provider-neutral framework release; no new model pilot.

The public upstream port from the earlier 0.1.x scaffold uses neutral fixtures and its own regenerated manifest; see [the port migration](MIGRATION-v0.1.2-to-v1.9.1.md).

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
