# Change history

## 1.9.5 (unreleased)

CONDITIONAL (#245): AWF-210 version bump.

AWF-201 7/9: planning retention/recovery, 4,000 events/cycle ceiling, restricted sidecar permissions.

Ticket-level merged inventory: AWF-5,AWF-8,AWF-10,AWF-14,AWF-16,AWF-17,AWF-18,AWF-19,AWF-20,AWF-22,AWF-23,AWF-24,AWF-26,AWF-28,AWF-30,AWF-31,AWF-32,AWF-33,AWF-34,AWF-35,AWF-36,AWF-37,AWF-41,AWF-42,AWF-43,AWF-44,AWF-45,AWF-47,AWF-48,AWF-50,AWF-51,AWF-52,AWF-53,AWF-54,AWF-55,AWF-57,AWF-58,AWF-59,AWF-60,AWF-61,AWF-62,AWF-63,AWF-64,AWF-65,AWF-66,AWF-68,AWF-69,AWF-70,AWF-71,AWF-72,AWF-73,AWF-74,AWF-75,AWF-76,AWF-77,AWF-78,AWF-79,AWF-80,AWF-81,AWF-82,AWF-87,AWF-88,AWF-89,AWF-91,AWF-93,AWF-94,AWF-96,AWF-97,AWF-100,AWF-101,AWF-107,AWF-110,AWF-115,AWF-117,AWF-131,AWF-132,AWF-140,AWF-145,AWF-148,AWF-150,AWF-151,AWF-154,AWF-157,AWF-158,AWF-177,AWF-178,AWF-181,AWF-182,AWF-185,AWF-195,AWF-201,AWF-205,AWF-206,AWF-208,AWF-212,AWF-213,AWF-214,AWF-215,AWF-217. These changes cover planning and controller operation, bounded review/publication and provenance, installation and upgrade safety, Windows portability, diagnostics, manifests, and release documentation. First-parent entries without ticket IDs are the Windows pre-push gate (#230), batch merge #81, and ADR merges #134, #135, and #137; their detailed history remains in the release’s first-parent log.

Runner #262 and C2 #263 are merged into main at `604d1922` under D-1026-28. CONDITIONAL: #245, #257, #259, C1, and AWF-55 remain; #251 is outside the D-1026-25 order.

### Known issues (1.9.5)

No listed issue remains open on supplied commit evidence.

Fixed in 1.9.5: AWF-18 (#63), AWF-19 (#88), AWF-21 (#61), AWF-22 (#75), AWF-23 (#82), AWF-24 (#52), AWF-31 (#80), AWF-32 (#78/#83/#190), AWF-33 (#102), AWF-34 (#131/#218), and AWF-35 (#113).

## 1.9.4 — 8 October 2026

AWF-15 (#56) binds closeout records to the merge SHA. AWF-29 fixes protected-path refusal, critic-schema compatibility, and sandbox/effort pass-through. AWF-40 (#68) fixes doc 22 guidance. AWF-16 and AWF-30 are deferred to 1.9.5; AWF-41 remains an owner-review check. The 1.9.3 → 1.9.4 fallback is version-only and ships no tier-3 review configuration.

Known in 1.9.4: AWF-34 and AWF-35; fixed in 1.9.5 by #131/#218 and #113.

## 1.9.3 — 8 October 2026

Adds Git-aware `tested_tree`, bounded routing, byte-verified upgrades, publication scanning and strict release trust; unsafe evidence fails closed.

Upgrades tolerate a venv `lib64` → `lib` link (#54). `gh release` binds `--repo`; origin is validated before tagging (#55).

### Known issues (1.9.4)

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
