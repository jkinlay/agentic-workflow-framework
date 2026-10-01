# AWF integration design: reviewed epic integration (1.9.3 item J), with full queue mode deferred

**Date:** 25 September 2026 · **Revision 4** (reviewer's alternative and owner decisions; see "Revision notes") · **Status:** design for owner review. Scheduled as **1.9.3 item J** (1.9.3 change request Revision 3.4). The Revision 3.1 queue architecture is retained unchanged as an **optional later mode** (Appendix A), with no release scheduled.
**File name:** kept as `AWF-v1.10.0-integration-queue-design.md` so existing references stay valid.
**Prerequisites:**
- 1.9.3 items C (tested-tree equality) and E (outcome templates).
- 1.9.3 item F (the AWF GitHub App identity) turns J's integration check from advisory into an enforced, App-bound required check. J works without F in advisory mode. If F moves to 1.9.4, J's enforced mode moves with it.
- Branch protection on the target branch of each governed repository. Neither repository has it today; it is set up as part of J's rollout.

## Why Revision 4

A reviewer argued that the Revision 3.1 queue is over-engineered for our scale and proposed a shared feature branch. We accept the core of that proposal and keep AWF's review guarantees:

| Reviewer's proposal | Revision 4 |
| --- | --- |
| A feature branch per epic. Every agent merges into it, and the tests run there. | Kept, as a **disposable integration branch** that is rebuilt deterministically. Nothing ever merges from it into the target. |
| Agents branch off the feature branch to see each other's work. | Agents **read** the integration branch as untrusted context. They branch from the target, or from a declared dependency's branch (a stacked PR). |
| The owner reviews each task's own branch, not the feature branch. | Kept. **Each task PR targets the target branch** (or its dependency's branch) and contains only that task's commits. |
| Race conditions are acceptable, and whoever merged last fixes a failing test. | Replaced by **one admission at a time**, **attribution by order**, and **removing the failing change**. Nobody edits another ticket's code. |
| PRs go in series, so order matters. | Kept, as an **explicit merge order** that the owner can change. The steward rebuilds and retests after every change. |

**What Revision 4 removes from the day-to-day path:** candidate generations, drift detection by patch-id, the integration-domain map, speculative queue depth, queue backends and signed-digest batch authorisation. They remain in Appendix A.

## Model

- **Target `T`:** the protected branch that PRs merge into (normally `main`).
- **Epic:** a set of related tickets that run concurrently. Epic mode is switched on per epic by an owner instruction, recorded as a governance record. The default for every project, new or upgraded, stays `integration.mode: serial`.
- **Task branch:** one per ticket, as today.
  - An **independent** task branches from `T`.
  - A **dependent** task declares `depends_on: [<PR>]` in its worker result and branches from its dependency's head. Its PR's base is the dependency's branch (a stacked PR).
- **Task PR:** the only thing a human reviews. Its diff is its own commits against its base. Review evidence, critic, specialists and owner approval work exactly as today and are bound to `head_r`.
- **Merge order `O`:** the steward's ordered list of the epic's open PRs. Dependencies always come after what they depend on. Otherwise, a PR enters at the back when its first validated head is admitted.
- **Integration branch `awf/int/<epic>`:** `T`, then each admitted head in `O` merged in order with `--no-ff` merge commits. Only the steward writes it. It is force-updated on every rebuild and deleted when the epic closes.
- **Integration build:** one immutable record per build: build ID; `T` SHA; the ordered list of `(PR, head_r)`; the resulting commit and tree; the test command; and the result for each prefix that was run.

## How work flows

1. **Before designing a task,** the worker reads the current integration branch as untrusted context. The goal is to reuse what other tickets are building and avoid duplicating it. If the task needs code that exists only in another open PR, the worker declares `depends_on`; otherwise it branches from `T`.
2. **When a worker's head passes its own validation,** the steward admits it. The steward holds a lock, so only one admission happens at a time: it merges the head onto the current integration tip and runs the project's test command.
3. **If the tests pass,** the head stays in and becomes visible to other agents.
4. **If the tests fail, or the merge conflicts,** the steward rebuilds the integration branch **without** that head. The PR is marked `INTEGRATION_FAILED` (with the failing output) or `INTEGRATION_CONFLICT` (with the paths). It leaves `O` and goes back to its own worker through the normal amendment process. Other tickets' branches are never touched.
5. **Review runs in parallel and in any order.** The critic and specialists review each task PR against its own base.
6. **Merging to `T` happens in series, front of `O` first.** The front PR is ready to merge when all of these hold:
   - valid review evidence on `head_r`;
   - an integration build on the **current** `T` whose first entry is exactly `(PR, head_r)` and which is green. This is the same candidate the Revision 3.1 design called `C`: `head_r` merged onto `T`;
   - the path-overlap rule below is satisfied;
   - owner authorisation: the owner merges the PR, or with F, the effective owner review on the exact head.
7. **After every merge to `T`,** the steward rebuilds the integration branch from the new `T` and retests. If the target branch's own post-merge CI goes red, the steward opens a **revert PR** for the owner. It never pushes to `T` itself.
8. **When `O` is empty,** the epic closes and the integration branch is deleted.

## Rebuilds and ordering

- **Triggers.** The steward rebuilds from `T` in order `O` whenever any of these happens: `T` moves (a merge, or an outside push); a PR's head changes; `O` is reordered; or a PR is removed. Each rebuild is a new integration build. Evidence from an older build never satisfies the gate.
- **Finding the culprit.** If a rebuild goes red, the steward re-runs prefixes of `O` to find the first failing entry. Only that entry is removed. The search is deterministic and attributes the failure by order, never by timing.
- **Reordering and skipping.** The owner can reorder `O` or skip a PR with a recorded instruction. The steward rebuilds and retests before the new front PR becomes mergeable.
- **Stalls.** A PR that has been at the front, unmerged, for longer than `integration.stall_after_hours` (default 24) moves behind the next independent PR. The owner is notified. A PR that fails review, or needs a code change, leaves `O` and re-enters at the back with its new validated head.
- **Dependencies are never reordered past what they depend on.** Keep stacks shallow. Preflight warns when a stack is deeper than `integration.max_stack_depth` (default 2).

## Stacked PRs

- A dependent PR's base is its dependency's branch, so its diff shows only its own commits.
- When the dependency merges, the steward retargets the dependent PR to `T`. The reviewed commit set is identified by its stable patch-id and range. Evidence is kept if the patch-id is unchanged. If it changed, the PR needs review on the new effective diff before it can reach the front.

## Path-overlap delta review

Tests cannot see every interaction. If PRs merged to `T` after this PR's review base touched any path in this PR's reviewed diff `R`, a **fresh critic delta review** of `R` against `R' = git diff T C` is required before the gate. It is the Revision 3.1 delta review, triggered only by path overlap.

- **A clean delta review** uses a run and no review round.
- **A finding that needs code** follows the normal amendment cycle and uses a round.
- The 1.9.3 rule that the run cap and the round cap are independent still holds.

## The integration check

- **The check.** The steward publishes an `awf-integration` status on each task PR's head. It is `success` only when that PR is at the front of `O` and every merge condition above holds for the current integration build. Otherwise it is `pending`, with the reason.
- **Before F (advisory).** The controller identity posts the status. The owner merges by hand, and the controller refuses to mark a PR ready while its status is pending.
- **With F (enforced).** The AWF App posts the status. The target's branch protection requires `awf-integration` **from the App's integration ID**, as in the Revision 3.1 rule Q14. Epic mode refuses to activate in enforced mode if that binding cannot be verified.
- **Merge method.** Merge commits only, so `head_r` is a parent of what lands. The existing post-merge parent check still applies. Squash and rebase merges are refused in epic mode.

## Integration steward

The steward is a deterministic controller component. It has no model route and uses no model tokens.

- **What it does:** maintains `O`; admits heads under the lock; builds, tests and rebuilds; finds the culprit prefix; sets `awf-integration`; retargets stacked PRs; opens revert PRs; writes every decision, with inputs and outputs, to the ledger.
- **What it may not do:** write or amend code on any task branch; issue review verdicts; merge to `T`; skip a required delta review; or change routes, budgets or the order without a recorded owner instruction (stall handling excepted).
- **Identity.** It uses the App once F exists. Until then it uses the controller identity, and it never uses the owner's personal token for integration writes.

## Configuration

```yaml
integration:
  mode: serial              # serial | epic (epic is enabled per epic by an owner instruction)
  test_command: null        # null means validation.commands
  stall_after_hours: 24
  max_stack_depth: 2
```

## Acceptance criteria (1.9.3 item J)

| ID | Criterion |
| --- | --- |
| AC18 | The integration branch equals `T` plus each admitted head in `O`, merged in order with merge commits. Each build records `T`, the ordered `(PR, head_r)` list, the commit, the tree and the test results. Rebuilding from the same inputs gives the same tree. |
| AC19 | Two heads pushed at the same moment are admitted one at a time. If the tests fail after admitting X, only X is removed and marked `INTEGRATION_FAILED`. No other ticket's branch changes, and no commit by one ticket's worker appears on another ticket's branch. |
| AC20 | A head that cannot merge cleanly onto the integration tip is recorded as `INTEGRATION_CONFLICT` with its paths. It is removed and returns to its worker's amendment cycle. |
| AC21 | `awf-integration` succeeds only for the front PR of `O`, and only when its review evidence on `head_r` is valid and a green build on the current `T` starts with exactly `(PR, head_r)`. A PR that is not at the front stays pending, with its position as the reason. |
| AC22 | A move of `T`, a head change, a reorder or a removal each produce a new build. A stale build never satisfies the check. After a red rebuild, the prefix search removes exactly the first failing entry. |
| AC23 | An owner reorder or skip is recorded, rebuilt and retested before the new front becomes mergeable. A stalled front PR moves behind the next independent PR, and the owner is notified. Dependencies are never reordered past what they depend on. |
| AC24 | A declared `depends_on` puts the dependent after its dependency, and the dependent PR's base is the dependency's branch. After the dependency merges, the PR is retargeted to `T`. Evidence is kept if the stable patch-id of its own commits is unchanged, and review is required if it changed. |
| AC25 | If PRs merged since this PR's review base overlap its paths, a fresh critic delta review of `R` against `R'` is required, and it uses a run but no round. Without overlap, none is required. A code-changing finding uses a round. |
| AC26 | For an independent pair and for a stacked pair, each task PR's diff against its base contains only its own commits. |
| AC27 | The steward has no model route and uses no model tokens. Only the steward force-updates `awf/int/*`. A red post-merge CI on `T` produces a revert PR, never a push to `T`. Closing the epic deletes the integration branch. |
| AC28 | `integration.mode` defaults to `serial` for new and upgraded projects, and epic mode needs a recorded owner instruction. With F, activation refuses unless `awf-integration` is required from the App's integration ID. Without F, the check is advisory, and the controller will not mark a pending PR ready. |
| AC29 | The worker prompt tells workers to read the integration branch as untrusted context, to declare `depends_on` when they use unmerged code, and otherwise to branch from `T`. |

## Invariants to preserve during implementation

- **Nothing reaches `T` except a reviewed task PR.** The integration branch is evidence, never a source.
- **Blame follows order, never timing.** The steward removes the failing change. It never repairs code.
- **Authorisation stays bound to `head_r`.** Rebuilding the integration branch never asks the owner to re-authorise an unchanged head that has clean evidence.

## Appendix A: full queue mode (deferred; Revision 3.1 architecture retained)

This is the frozen Revision 3.1 queue architecture, kept unchanged for a later release if merge contention grows beyond what epic mode handles: more concurrent streams, organisation repositories with native merge queues, or batch authorisation by signed digest. **It is not part of 1.9.3 and has no scheduled release.** Where it names "1.10.0", read "the release that adopts queue mode". Its Q1–Q17 criteria stay as written, for that release.

### Problem

Merging is serial. Each merge puts every other open PR out of date. The owner then has to rebase and rerun CI, and review evidence tied to the old head forces a fresh review, one PR at a time. For N open PRs that is roughly N² rebases. At 10–20 tickets a day across three streams, that is the largest remaining cost. Re-review after a rebase sometimes finds real problems, but those come from how the PRs interact, not from the rebase itself.

### The invariant being changed

- **Today:** head + current base → review → authorise → merge.
- **Proposed:** reviewed head + evolving base → integration evidence (per candidate generation) → conditional delta review → authorise → interlocked queued landing.

Review stays bound to what the author wrote. Integration becomes separate evidence, bound to an exact candidate.

### Evidence objects

Recorded by SHA for each queued PR and each candidate generation:

| Object | Definition |
| --- | --- |
| `base_r` | Base at review time: the merge base of the reviewed head with the target. |
| `head_r` | The reviewed head. |
| `R` | The reviewed diff, `git diff base_r head_r`. Its patch-id is recorded. |
| `base_q` | The **predecessor candidate**: the target head for the first PR in the queue, otherwise the immediately preceding PR's candidate `C`. |
| `C` | This PR's candidate: `head_r` merged onto `base_q`. |
| `I` | The intervening integration diff, `git diff base_r base_q`, with the list of landed and queued PRs it contains. |
| `R'` | The effective contribution, `git diff base_q C`. This is fully determined by git; no "restriction" step is needed. |
| `drift` | The range-diff of `R` against `R'` (by patch-id and hunk content). |
| `impact` | The integration-impact classification (below), with the signals that fired. |
| `gen` | The candidate generation: a counter that increases whenever `base_q` changes for this PR. |

**Delta-review input** is exactly: `R`, `R'`, `drift`, `I`, and the diffs of the PRs in `I` that fired an impact signal.

### Candidate generations and freshness

- **What starts a new generation.** Any change to `base_q` for a PR starts a new generation `gen+1`. Causes include an earlier PR landing, being ejected, being reordered or being removed, and the target branch moving. It also covers the native backend recreating its merge-group branches.
- **What is recomputed.** In the new generation, `C`, `I`, `R'`, `drift`, `impact` and queue CI are all recomputed.
- **What stale evidence can't do.** Evidence bound to an earlier `(gen, base_q, C)` never satisfies the final gate for the current generation.
- **When the owner re-authorises.** Not merely because the generation changed. The owner's authorisation is bound to `head_r`, and it carries over to the new generation when all of these hold:
  - `head_r` is unchanged;
  - the new generation's evidence is clean (queue CI green; `impact = none`, or a delta review that passed with no findings);
  - the drift signal did not fire (stable patch-id unchanged).

  The machine evidence must always be regenerated.

### Integration conflict

- **State `INTEGRATION_CONFLICT`.** If `head_r` cannot be merged cleanly onto `base_q`, no `C` exists, so there is no `R'`, no queue CI and no final gate.
- **What happens.** The PR is ejected from the queue and the conflict is recorded in the ledger with the paths involved.
- **Who resolves it.** No delta reviewer resolves the conflict, and the steward never edits it. The ticket returns to the worker through the normal amendment process.
- **Re-entry.** The new head needs ordinary head-bound review evidence and owner authorisation of that head. Downstream PRs start a new generation.

### Integration-impact classifier

Deterministic signals, combined into a result of `none` or `review`:

1. **Path overlap.** `R` and `I` touch the same file.
2. **Declared integration domains.** The project configuration maps paths to domains such as API, schema, config, dependencies/lockfiles, security, runtime contract, generated artefacts and shared fixtures. The signal fires when `R` and `I` touch the same domain, even through different paths.
3. **Always-review paths.** Any change in `I` to dependency manifests, lockfiles, CI configuration, AWF managed files or project configuration fires the signal for every queued PR.
4. **Drift.** Any change in the effective patch fires the signal. The test is stable patch equivalence: `git patch-id --stable` of `R` compared with that of `R'`. 1.10.0 applies no fuzzy context-line threshold; false positives are accepted and can be tuned later from evidence.
5. **Unmapped paths.** If `I` changes a production path that is not in the integration-domain map, and the current PR's `R` touches any path in the same top-level component (or also touches unmapped production paths), the signal fires. A path missing from the map is never taken as proof of independence.

**The domain map.** It lives in project configuration. The controller may *propose* changes, but only an owner-approved governance PR changes the map. Preflight reports unmapped production paths.

**Queue CI on `C` is mandatory in every case.** It is the safety net for interactions the classifier cannot see. `impact = none` means "no delta review", never "no integration evidence".

### Queue backends

- **Detection.** Preflight reports `integration_backend = native | awf | unavailable`, plus a separate status line for native queue capability. Native capability requires a qualifying organisation-owned repository and the App's `merge_queues` permission.
- **The AWF backend is the primary backend.** It must be complete and is what 1.10.0 is verified on. Both current governed repositories (`jkinlay/agentic-workflow-framework` and `jkinlay/signal-lab`) are owned by a personal account, and GitHub's native merge queue is unavailable to them. The AWF backend:
  - builds candidates on `awf/queue/<target>/<gen>/<n>` branches;
  - runs CI on them;
  - lands them by advancing the target branch;
  - cleans up the branches.
- **AWF backend lifecycle.**
  - **Speculative depth** is configurable, default 3, in line with the existing three-stream concurrency.
  - **Branches:** superseded, ejected and landed candidate branches are deleted immediately. On startup, the steward reconciles and deletes abandoned `awf/queue/*` branches.
  - **Outside pushes:** any movement of the target branch outside the queue invalidates every downstream generation. They are rebuilt from the new target.
- **The native backend** (GitHub `merge_group`) is optional. It is verified on a separate organisation-owned integration-test repository.
- **Merge method.** Both backends use merge commits only, so `head_r` stays a parent of what lands and the existing post-merge parent check holds. Squash and rebase merges are refused in queue mode.

### Authorisation interlock (blocking requirement)

- **The required check.** Nothing can land before the owner authorises it. Every queue-mode target branch requires a status check, **`awf-final-gate`**, which only the AWF App can set.
- **While work is in progress.** The check stays `pending` during candidate CI and delta review.
- **When it succeeds.** It becomes `success` for a specific `(PR, head_r, gen, C)` only when all of these hold:
  - review evidence on `head_r` is valid;
  - queue CI on `C` is green;
  - `impact = none`, or a passing delta review is bound to this generation;
  - a qualifying owner authorisation for `head_r` exists (either mode, below).
- **When it resets.** A new generation, a change to `head_r` or a revoked authorisation resets it to `pending`.
- **Bound to the App's identity, not just the check name.** The required check is configured to accept `awf-final-gate` only from the AWF App (the branch rule or ruleset records the App's integration ID as the expected source). A same-named status or check from any other identity does not satisfy the gate. Preflight verifies three things together: the check name, the expected App ID, and that the branch rule or ruleset is actually enforced. **Queue mode refuses to activate if that binding can't be enforced**, for example where the repository's plan does not support required checks on it.
- **Native backend.** GitHub merges only when all required checks pass, so this check is what stops the native queue landing an unauthorised PR. The App needs `checks: write` (or `statuses: write`) as well as `merge_queues`.
- **AWF backend.** The same gate is evaluated before advancing the target, and the check is still published, so branch protection enforces it independently.

### Integration steward: deterministic controller, not a model

The steward is a **deterministic controller component**. It has no model route, uses no model tokens, and makes no judgement calls.

- **What it does, all as policy computation.** Orders the queue; builds candidates and tracks generations; evaluates the classifier; associates CI; evaluates the final gate and sets `awf-final-gate`; invalidates stale evidence; lands PRs; writes the ledger.
- **What it may not do.** Write or amend code; issue review verdicts; skip a delta review the classifier requires; edit the integration-domain map; change routes or budgets; land anything without a successful `awf-final-gate` for the exact `(PR, head_r, gen, C)`.
- **Model work it hands off.** Delta reviews go to a **fresh critic** context, a normal model route with run accounting. An optional **digest summary** may be written by a model. It is presentation only: its text is never read by the gate, and the digest's authoritative content is the canonical JSON below.
- **Identity.** It uses the AWF App identity, never the owner's token. Every decision goes to the ledger with its inputs and outputs.

### Owner authorisation modes

There are two modes, kept separate.

#### Signed digest authorisation (several PRs, one action)

- **Canonical form.** Schema version; UTF-8 canonical JSON (sorted keys, no insignificant whitespace, entries sorted by PR number).
- **Each entry contains:** the PR, `head_r`, the review verdicts and specialist results, `gen`, `impact` and its signals, the delta-review result, and queue CI on `C`.
- **Hash and signature.** The digest ID is the SHA-256 of the canonical bytes. The owner signs one AWF record over that ID.
- **What it covers.** It authorises exactly the listed `(PR, head_r)` pairs.
  - The authorisation is tied to `head_r`, not `C`. It carries across generations under the freshness rule above.
  - A change to a listed `head_r`, or a delta-review finding, removes only that PR.
- **Expiry.** The signed digest expires after a configurable lifetime (default 24 h). The expiry applies to digest records only.

#### GitHub authorisation (per PR)

- **The rule.** Each PR uses the effective-owner-review rule from 1.9.3 F: the owner's latest non-COMMENTED review on the exact head, by immutable ID, must be APPROVED, and not dismissed or later superseded.
- **Presentation.** A digest may *list* these PRs for convenience. The digest is then presentation only, and the approvals gain **no** new expiry. They behave exactly as in 1.9.3.

### Delta findings and the amendment cycle

- **A clean delta review** uses one run and no review round.
- **A delta-review finding blocks the gate.**
  - If the finding needs a code change, the PR **leaves the queue** and goes back through the normal amendment process: worker fix, then an ordinary head-bound review that uses the applicable review round, under the 1.9.3 round cap.
  - It may rejoin the queue only once review evidence bound to the new head exists, and it then needs owner authorisation of that new `head_r`.
- **The 1.9.3 invariant still holds.** Run cap and round cap stay independent. Integration findings never create free amendment rounds.

### Stacked PRs: deferred (future compatibility invariant, not a 1.10.0 criterion)

- **What is reviewed.** The dependent PR's commit set, identified by patch-id and range.
- **After the parent lands,** the dependent's effective diff is recomputed against its new `base_q`. An unchanged patch-id and range-diff keep the evidence. A change triggers a delta review under the rules above.

### Defaults and rollout

- **Default mode.** `integration.mode: serial` is the universal default in 1.10.0, for new and upgraded projects. Queue mode is enabled through a governance PR, using `integration.backend: awf` (or `native` where available).
- **App permissions.** 1.10.0 owns the expansion of the App's permission set (`checks`/`statuses: write`; `merge_queues` for native). Preflight reports any missing permission and never assumes that the App created for 1.9.3 already has it.
- **Changing the default.** Re-evaluate it after real use on at least two projects.

### Draft acceptance criteria

| ID | Criterion |
| --- | --- |
| Q1 | For queue order PR1, PR2: PR1's `base_q` is the target head, and PR2's `base_q` is PR1's `C`. `R'` equals `git diff base_q C` for each, and `I` equals `git diff base_r base_q`. |
| Q2 | When PR1 is ejected, PR2 gets a new generation. Its `C`, `I`, `R'`, `impact` and CI are recomputed, and evidence from the old generation fails the final gate. |
| Q3 | With `head_r` unchanged and clean new-generation evidence (no drift), the existing owner authorisation carries over. A delta-review finding removes it. |
| Q4 | `awf-final-gate` stays pending without owner authorisation. The native backend cannot merge a PR with a pending gate, and the AWF backend refuses to advance the target without it. |
| Q5 | Two approved PRs with disjoint paths and no shared domain land without rebases. Each has review evidence on `head_r` and queue CI on its `C`. |
| Q6 | Path overlap, a shared declared domain through disjoint paths, a lockfile/CI change in `I`, or drift each trigger a delta review whose recorded input is exactly the defined set. |
| Q7 | Queue CI on `C` is required even when `impact = none`. A red candidate is ejected without landing. |
| Q8 | Squash and rebase merges are refused in queue mode. The post-merge check proves the merged parent equals `head_r`. |
| Q9 | The steward has no model route and consumes no model tokens. An optional digest summary cannot change any gate outcome: mutating the summary text leaves gate results identical. |
| Q10 | The signed digest's canonical JSON and SHA-256 are reproducible from the same inputs. A digest authorises exactly its `(PR, head_r)` pairs and expires. GitHub approvals listed in a digest get no digest expiry. |
| Q11 | A delta finding that leads to a code change removes the PR from the queue, uses a review round on the new head, and needs fresh authorisation. A clean delta review uses a run and no round. |
| Q12 | Preflight reports `integration_backend` and native capability separately, and reports missing App permissions. On a personal-account repository, native is reported unavailable and `awf` is offered. |
| Q13 | `integration.mode` defaults to `serial` for new and upgraded projects; switching requires a governance record. |
| Q14 | App-bound gate: a successful `awf-final-gate` status or check from any identity other than the configured AWF App does not satisfy the gate. Queue mode refuses to activate when the App binding or branch enforcement can't be verified. |
| Q15 | Integration conflict: an unmergeable `(head_r, base_q)` produces no candidate, is ejected without landing, records `INTEGRATION_CONFLICT`, and returns to the normal amendment process. Re-entry requires new head-bound review evidence and owner authorisation. |
| Q16 | Drift uses stable patch-id equivalence. An unmapped production path in `I` near the PR's changes triggers a delta review. Only an owner-approved governance PR changes the domain map. |
| Q17 | AWF backend: speculative depth defaults to 3. Superseded, ejected and landed candidate branches are deleted. Abandoned `awf/queue/*` branches are removed on startup. An outside push to the target rebuilds every downstream generation. |

**Future compatibility invariant (stacked PRs, not a 1.10.0 release criterion).** When stacks arrive, a dependent PR's evidence must stay attributable after its parent lands, and a material change in its effective diff must trigger a delta review.

### Resolved questions (Revision 3.1)

1. **Domain map:** the controller proposes; only an owner-approved governance PR changes the map; unmapped paths are reported and treated conservatively (classifier signal 5).
2. **Drift:** stable patch-id equivalence, with no fuzzy threshold in 1.10.0 (signal 4).
3. **AWF backend lifecycle:** depth 3, immediate branch deletion, startup reconciliation, and a rebuild on any outside push to the target (Queue backends).
4. **Digest batching:** operational, not architectural. It is configurable (maximum PRs per digest, maximum wait before a digest is sent). A single ready PR is never held back past the maximum wait.

### Invariant to preserve during implementation

A change of candidate generation alone **never** invalidates the owner's authorisation. Authorisation is bound to `head_r`; machine evidence is regenerated per generation. It carries over when the head is unchanged, the new evidence is clean and there is no drift. This rule is what removes the N² re-review and re-authorisation cost. Implementation must not "simplify" it into re-authorising on every generation.

## Revision notes

**Revision 4** (25 September 2026; reviewer's alternative and owner decisions):

- A reviewer proposed a shared feature branch per epic, with agents branching from it and tests run on it. The owner added two decisions: the human reviews each task's own branch, not the feature branch; and PRs are merged in series, so order matters.
- Adopted as **reviewed epic integration**:
  - a disposable integration branch rebuilt by a deterministic steward;
  - one admission at a time, with blame by order and failing changes removed;
  - task PRs that target `T` (or a declared dependency's branch) and contain only their own commits;
  - an explicit merge order with owner reorder, skip and stall handling;
  - path-overlap delta review;
  - an `awf-integration` check (AC18–AC29).
- **Owner decision: this lands in 1.9.3 as item J,** the sixth PR after F. It is advisory without F and enforced and App-bound with F.
- The Revision 3.1 queue architecture moves unchanged to Appendix A as an optional later mode with no scheduled release.

**Revision 3.1** (fourth external review: architecture PASS, short finalisation pass):

- `awf-final-gate` is bound to the AWF App's integration ID. Preflight verifies name, App ID and enforcement, and queue mode refuses to activate without it (Q14).
- Added the `INTEGRATION_CONFLICT` state and its rules (Q15).
- Stacked PRs moved from the criteria table to a future compatibility invariant.
- Froze the domain-map governance and the conservative handling of unmapped paths, drift by stable patch-id, and the AWF backend lifecycle (depth 3, cleanup, outside pushes) (Q16, Q17). Digest batching is configurable.
- The rule that authorisation carries over across generations is recorded as an implementation invariant.

**Revision 3** (third external review, "promising, not yet implementation-ready"):

- `base_q` is now the predecessor candidate, so `R' = git diff base_q C` is exact.
- Added candidate generations and freshness invalidation, with owner authorisation carried over only under stated conditions.
- Added the `awf-final-gate` required-check interlock (blocking requirement) for both backends.
- The steward is now a deterministic controller with no model route; any model-written digest summary is presentation only.
- Signed-digest and per-PR GitHub authorisation are separated, and canonical digest hashing is defined.
- A delta finding that changes code goes back to the normal amendment cycle and uses a review round.
- The App identity is a hard capability prerequisite. The App's permission expansion belongs to 1.10.0.
- Backend detection is now `native | awf | unavailable`. The AWF backend is primary, because both governed repositories are owned by a personal account (checked 24 Sep 2026), and the native backend is verified on a separate organisation repository.
- Q1–Q14 replace the earlier Q1–Q11.

**Revision 2:** added the integration steward and digest authorisation.

**Revision 1:** created from 1.9.3 section A.

