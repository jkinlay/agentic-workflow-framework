# 1.9.6 qualification/preservation

AWF-238: Tier 3, owner-approved FULL rebind except automatic
deletion deferred to [AWF-258](#awf-258-successor). Retain pre-F5 #1
(object-continuity deletion). Rollout/adapters unauthorized.

## 1.9.6 limitation

**1.9.6 limitation—quiescent, identity-checked namespace publication.** Name-based rename or single-file unlink is permitted only for an object created by AWF in this transaction, or an explicitly managed object whose recorded identity is checked immediately before mutation. Every affected parent must remain pinned and held by AWF; the operation must hold the applicable exclusive transaction lock, and the owner must establish quiescence across installers, agents, editors, schedulers and other writers. Owner files are never eligible. Unknown identity, replacement, links, unexpected occupancy or lost custody causes refusal and retained, reported evidence. These checks do not atomically bind a pathname mutation to its inspected leaf: a non-cooperating process with directory-write access can still win the final race. Object-bound publication/closeout is deferred to AWF-258, release 1.9.8. Recursive runtime deletion and deletion-dependent automatic rollback remain refused.

## Baseline

`v1.9.5` commit: `8ad399943c8f5e4f17db72f3e321b9365ee27782`; manifest SHA-256:
`1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7`.
Immutable assets/pins/receipts; Git proves no publication.

## Preservation

Require baselines/exclusivity/quiescence/authorization. Preserve owner/configuration/routing/Jira/budget/state/history/receipt inventories and default OFF settings.

External absolute PYTHON/RELEASE/PROJECT/WHEELS paths: see `RELEASE/MIGRATION-v1.9.5-to-v1.9.6.md`. Quiesce/exclusively own affected projects. Reports/WORK are new external paths.

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py plan --project PROJECT --source RELEASE --expected-manifest-sha256 PIN --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --output PLAN
PYTHON -B RELEASE/scripts/qualify_upgrade.py installed --source RELEASE --work-dir WORK --wheelhouse WHEELS --output REPORT
```

 snapshot before apply, retain BEFORE/authentic baseline; compare derives identity-verified `approved_delta`; inspect `remaining_changes`. `--allowed-delta` only constrains policy; owner/history loss is unwaivable.

`installed` requires clean committed source/current manifest and complete locked wheels; binds commit/tree/pin, never regenerates. Bootstraps authentic 1.9.5; checks owner/history preservation. Failures retain inventories/comparison/NOT_QUALIFIED/failed-stage. Require QUALIFIED_DISPOSABLE_UPGRADE/two CONFIGURED stages/zero exits/integrity true/ACCEPTED configuration/bound source-policy-receipt hashes/preservation and PASS `source_managed_membership`/`historical_state_continuity`/`named_streams`.

Digest-only: no contents/SIDs; unverifiable permissions/streams refuse; PRESERVATION_COMPARISON_ONLY grants no qualification.

Require verified RELEASE's `docs/runbook/19-runbook-journal-recovery.md`#migration-preservation-policy, including every managed/state/metadata membership check.

POSIX qualification requires TMPDIR outside PROJECT on its filesystem; select external same-filesystem storage. Private 0700 wrappers retain inheritance replicas; qualify JSON lists exact paths. Preserve wrappers; owner-controlled cleanup only. Windows metadata preflights dispose creating handles; dry runs skip them.

Require verified RELEASE's `docs/runbook/19-runbook-journal-recovery.md`#migration-budget-controls.

## AWF-258 successor

See limitation.

## Recovery/gates

Interrupt staging/writes/receipts/provenance/swaps/cleanup; recover freshly. Missing/tampered objects refuse; protect empty directories. No recursive deletion; retained directories consume space. Removal-dependent rollback refuses before managed mutation. Committed recovery retires coordination, returning `status: COMMITTED`, `transaction_cleanup: COMMITTED_CLEANUP_PENDING`, `transaction_cleanup_failures`, `retained_directories`, `installation_recovery`. Inspect inventory.
Retain unresolved directories in `.agentic-install/retained-journal.json`; valid retention blocks no integrity/capabilities/transactions. Adopt only reused existing managed directories after recording source evidence in `directory-adoptions.json`; delete none. Standalone failures retain stages/original-errors without cleanup retries; portable stages use sibling cleanup-pending.json. Archives stay nonrecursive.

Follow [canonical owner recovery steps 1-8](https://github.com/jkinlay/agentic-workflow-framework/blob/v1.9.6/docs/runbook/19-runbook-journal-recovery.md#owner-recovery-and-retiring-resolved-diagnostics) in the verified journal-writing RELEASE. The upstream link locates the source-only runbook; authenticate your RELEASE copy before executing it.

Require verified RELEASE's `docs/runbook/19-runbook-journal-recovery.md`#qualification-gates.
