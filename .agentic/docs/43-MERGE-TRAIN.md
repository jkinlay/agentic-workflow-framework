# Merge train ADR and tests-first plan

**Status:** proposed, design only. AWF-54 is Tier 3 with `FULL` closure. No
implementation starts until the four owner questions below are decided. This
ADR grants no provider, merge, Jira, or target-push authority.

## Context and recommended design

The integration queue preserves source branches as merge candidates and uses
its integration branch only as evidence. A merge train instead creates a new
batch candidate, so it needs a separate coordinator while reusing the queue's
immutable input, prefix-build, validation, and registered-receipt primitives.
It must not turn the serial queue into batch mode or add a weaker approval
store.

Admission should reconstruct each source PR's current final gate and bind its
exact head, tree, base, critic completion, required CI, provenance outcome, and
provider-observed owner review. Source evidence authorizes admission only; it
cannot authorize the batch. The batch needs its own authenticated mark-ready
operation, frozen independent reviews, final gate, owner authorization, and
human merge.

One generation freezes the target and admitted source heads. It orders sources
dependency-first, then by approval time and PR number, and builds a two-parent
merge for every prefix. Every prefix has a bound validation result and
registered reconstruction receipt. Target, source-head, or evidence drift
abandons the generation.

Only a reproduced generated-path conflict may be repaired once by regenerating
from the final combined tree. Non-generated conflicts, unexpected generated
bytes, and the first failing local prefix eject the affected source and rebuild
from the frozen target. A final remote-only CI failure abandons the generation
for diagnosis rather than guessing a culprit. The coordinator may prepare and
publish a batch candidate, but exposes no merge verb or target push. Only the
controller may reconcile constituent Jira records after observed batch merge
and batch-aware closeout.

## Planned changed-file map

| Area | Planned paths |
| --- | --- |
| Shared construction | New `.agentic/lib/agentic/integration_build.py`; adapt `integration_queue.py` without changing its merge-source rule. |
| Train | New `.agentic/lib/agentic/merge_train.py` and `.agentic/scripts/merge_train.py`. |
| Trust boundaries | `provenance.py`, `gates.py`, `closeout.py`, and `continuous_controller.py`. |
| Contracts/config | `scripts/generate_contracts.py`; generated batch and constituent-closeout schemas; every `PROJECT_CONFIG.yaml` projection, with optional `merge_train`. |
| Guidance/tests | Docs 23, 29, 30, 34, 41, and 42; focused merge-train, provenance, gate, authorization, closeout, and controller tests. |
| Inventory | `MANIFEST.json` and `MANIFEST.md`, regenerated after all other outputs. |

## 54A/54B split

**54A** defines generated contracts, shared deterministic construction,
admission, ordering, generation invalidation, and narrowly registered batch
provenance. **54B**, based on accepted 54A, adds authenticated publication,
freeze-race handling, batch lifecycle/final-gate plumbing, constituent
closeout, and controller-only reconciliation. Both are Tier 3 and receive
independent current-head review; 54A grants no live publication path.

## Tests-first plan

Each implementation assertion is added before its code, demonstrated failing
on `2f5aabf`, then passing on its implementation head. Tests use temporary
repositories, portable POSIX-style fixture paths, and no network.

54A first tests the admission matrix (stale/missing/wrong-head approval, critic,
CI, provenance, draft/base/head drift), source/batch authorization isolation,
dependency ordering, duplicate/shared-range refusal, prefix receipts,
same-input tree equality, generation invalidation, conflict policy, and strict
batch-parent provenance. 54B first tests freeze checks before every mutation,
the push/PR-creation orphan race, draft-versus-full exact-head CI, batch
mark-ready and merge authorization, ordinary-closeout refusal for a
constituent, batch-aware closeout, controller-only Jira writes, and absence of
merge/target-push APIs. This design-only lane adds no assertions or tests; its
pre-change release-hygiene baseline remains the applicable tests-first record.

## Sequencing and conflicts

Code follows accepted heads in this order: `AWF-140 -> AWF-95 -> AWF-79 ->
AWF-98 -> AWF-96 -> AWF-97 -> AWF-117 -> AWF-54`. One writer at a time owns
`provenance.py`, `gates.py`, contract generation, or manifests. AWF-95/79/96/97
directly conflict in provenance and generated outputs; AWF-98 conflicts in
gates/contracts; AWF-117 must first bind commit-range provenance into the
existing gate. AWF-145's final draft/full-CI behavior and a live AWF-142 freeze
observation are runtime prerequisites, never inferred from unmerged source.

## Open owner questions

1. What does **owner-approved** mean? **Option A (recommended):** a fresh
   provider-observed exact-head owner review qualifies a source for admission
   only. **Option B:** treat source merge authorization as replayable for the
   batch.
2. What is the default? **Option A (recommended):**
   `merge_train.enabled: false` in every projection until explicit opt-in.
   **Option B:** retain the old adopter-on/AWF-repository-off ruling.
3. What is the delivery shape? **Option A (recommended):** sequential 54A and
   54B PRs. **Option B:** retain the old one-PR ruling.
4. Must lower-tier sources have owner approval before admission? **Option A
   (recommended):** require the same exact-head owner review for every source.
   **Option B:** require it only where the source tier's normal final gate does.
