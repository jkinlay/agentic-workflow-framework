# Upgrade AWF 1.8.7 to 1.8.8

Verify the new source/distribution pins and use the existing adoption flow to prepare a draft PR. Skill installation and project upgrade remain separate; retain the previous skill backup, accepted project configuration and original evidence.

## Operating recommendations

Epic scopes such as `data/**` and `data/*` now have the same bounded directory meaning as `data/`. Unsupported wildcard forms are refused with the Epic ID and value. Unknown scopes still produce a conservative recommendation with the actual missing/invalid scope identified.

Adopting a recommendation no longer pins a governance-default route. Existing user pins remain visible and unchanged unless the user explicitly changes them. This preserves simple-work routing and optional escalation for unpinned defaults. Risk/review floors remain binding. Request a new recommendation after upgrading: old recommendation records are historical and should not be applied under changed recommendation behavior.

Existing default routes accidentally pinned by an earlier adoption stay unchanged during upgrade. If the user wants to restore simple-work routing, show the affected rows and use an explicit operating edit such as `--set streams.A.worker.pinned=false`, preserving the audited instruction. Do not infer that a stored pin was accidental.

## Governance proposal in the upgrade PR

Ordinary upgrades preserve `execution.max_parallel_tickets` and any legacy `execution.independent_reviewers.count`. The report offers their concrete current/proposed values. With the explicit `--propose-operating-capacity` option, bootstrap stages a ceiling of at least six and removes the fixed reviewer count, deriving one reviewer per operating stream. A stronger existing ceiling remains unchanged; actual host capacity still binds.

For cross-version migration, use the normal verified bootstrap arguments with the existing governance file and explicit conflict backup:

```text
python -B scripts/bootstrap_project.py --mode install --on-conflict backup --propose-operating-capacity ...
```

This is a local governance proposal for the owner to review and merge in the adoption PR. It does not establish accepted default-branch policy, increase a host ceiling, enable dispatch or authorize merge. Without that option, a project retaining a ceiling of three continues to refuse four operating streams with the exact PR remedy.

The proposal option requires existing governance. `--dry-run` reports PLANNED_FOR_REVIEW; an applied local installation reports STAGED_FOR_REVIEW. The existing `--mode upgrade` supports a managed same-version installation; do not use it to bypass cross-version conflict/backup checks.

## Audit files and pilot evidence

Commit `OPERATING_CONFIG.yaml` and `.agentic-state/operating/changes/` together. Bootstrap appends narrow transient-file ignore rules while preserving existing `.gitignore` content. Locks, pending journals and AWF staging files remain local; ignore rules do not remove them or repair an interrupted transaction.

The new [routine pilot protocol](.agentic/benchmarks/native/EVALUATION-v1.8.8.md) precommits separate required/prohibited/extras and original strict results. A completed response failure does not halt subsequent cases. Transport, usage and integrity failures stop collection; no automatic retries occur. The historical 1.8.7 pilot remains INCOMPLETE / strict FAIL, one invalid response and seven unrun cases. No historical response is regraded.
