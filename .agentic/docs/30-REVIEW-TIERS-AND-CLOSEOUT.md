# Review tiers, cap dispositions and closeout

Version 1.9.4 runtime; AWF-16 policy revision and migration ship in 1.9.5. The code (`review_tiers.py`, `review_policy.py`, `gates.py`, `jira_lifecycle.py`, `closeout.py`, `digest.py`, `host_preflight.py`) and the [lifecycle](23-TICKET-LIFECYCLE.md) are the authority; nothing here grants execution authority.

## Risk tiers

Every contract declares `risk_tier` (1, 2 or 3) and justification; uncertain is Tier 2. Tier 1 needs eligible paths, no protected paths and no true `risk_flags`. The gate recomputes the highest match. Only an owner-signed `tier-reassignment` and re-issued contract changes it; all records rebind.

Tier 1 uses one critic; findings advise. Tier 2 has three rounds; at cap, ticket P2s and route P1s to the owner. Tier 3 covers governance, release, merge/qualification and CI gates, has an unextendable three-round cap, and requires owner review bound to the candidate. Escalation preserves history. Boundaries always block.

Each round's exact `critic_artifact_binding` contains critic run ID, retained
canonical UTF-8 `result.json` SHA-256, head and round. Its receipt retains those
bytes and frozen completion identities. The gate reparses every artifact and
derives verdict/findings; copied fields cannot substitute. Unchanged diffs keep
old-head history; changed diffs need a current round. Only Tier 2 may exceed the
cap through authenticated owner disposition.

Verdicts and posting observations repeat that binding. Posting also binds the
verdict/digest, repository/PR, immutable comment ID/URL, exact comment/body,
anchor, collector run and time. The runtime-owned production registry is empty
until an adapter is installed. `production_posting_collector_ids` only narrows
it; bundle/config/environment declarations and fixture collectors never
register. Pending AWF-60, production posting remains `NOT_READY`.

## Bases, lineage and dispositions

A BLOCKER or MAJOR finding carries `basis`: `{criterion_id}` from the contract or `{boundary_code}` from `PROTECTED_PATH`, `SCOPE_ESCAPE`, `CREDENTIAL_EXPOSURE`, `UNSAFE_PATH`, `UNREGISTERED_PRODUCER`, `CI_BINDING`, `INDEPENDENCE`; without one the record is refused and the critic re-emits. A new serious finding on a locus (same `path` and basis) with a lineage names `supersedes_finding_id`. A defect outside the closure standard is a successor-ticket proposal, MINOR, never a new round.

`finding-disposition` `ACCEPT_RISK` or `NOT_A_DEFECT` makes a non-boundary finding non-blocking for the exact `head_sha` and copies it into `residual_risks`; `REQUIRE_FIX` keeps it open; `HEAD_CHANGED` voids it.

`authorization.verify_owner_record` requires an exact `AWF1.2 DISPOSE` comment, trusted actor, one unedited comment per record, and a verifier independent of worker/critic. Cap dispositions sign `cycles/extensions:ids`; tier/closure records sign `head=-`.

## Closure standard

`closure_standard.kind` is `FULL` or `DECLARED_LIMITATIONS` (at least one attributed limitation), frozen under `contract_hash` before review; changing it is `REQUIREMENTS_CHANGED`. `worker-result` and `critic-review` each record `closure.result`; the `acceptance_criteria` gate needs `MET` from both.

## The amendment cap

`execution.max_amendment_cycles` (3) bounds cycles; evidence-only amendments consume none. At `REVIEW_CAP_REACHED`, the controller records one owner-signed `review-cap-disposition`:

| Decision | Transition | Effect |
| --- | --- | --- |
| `MERGE_WITH_NOTES` | → FINAL_REVIEW | Listed open non-boundary findings become notes in `residual_risks` and the authorization text. |
| `PARK` | → BLOCKED | `reason_code: REVIEW_CAP_PARKED`, `resume_state: CHANGES_REQUESTED`. |
| `RESCOPE` | → SUPERSEDED | Successor recorded. |
| `EXTEND_ONE_CYCLE` | → CHANGES_REQUESTED | Tier 2 only: increments `cap_extensions`; refused at `execution.max_cap_extensions` (default 2, maximum 3). |

`workflow.py cap-plan` translates a disposition into event and facts or refuses it; the host loop pauses with `REVIEW_CAP_REACHED` and resumes only with `--disposition`.

`MERGE_WITH_NOTES` authenticates the exact terminal verdict/digest and artifact.
Otherwise the terminal artifact must be `APPROVE`/`PASS`; only this cap path may
carry its `REQUEST_CHANGES`. The gate records its ordered verified bindings.

## Jira lifecycle mirroring

The controller is the sole writer. `WORKER_STARTED` → `in_progress`; `PR_READY` → `in_review`; `OWNER_CHANGES_REQUESTED` or `HEAD_CHANGED` → `in_progress`; `JIRA_RECONCILED` → `done` with a closing comment naming the PR, reviewed head and merge commit. BLOCK/PARK never write. Tickets matching `jira.owner_closure_keywords` (the gate refuses a contract hiding the match) wait in `MERGED_PENDING_OWNER_CLOSURE` for an `owner-closure` record. `jira.lifecycle_writes` turns a mapping off, never adds one. Read back after every write; a mismatch or unknown result stops that ticket's writes, keeps observed actor/time or unknown, and is never reissued. `controller.auto_transition_jira: true` skips the per-write prompt.

## Closeout and history

`workflow.py validate-closeout RECORD --repository PATH` resolves reviewed head, base, merge commit, tree and bound blobs through Git plumbing and compares digests; absent objects fail closed and the working tree is never read. `JIRA_RECONCILED` requires `closeout_valid`. `verifies_history: true` requires `checkout_depth: full` (`fetch-depth: 0`, with the long-path step).

## Digests, skips, parity, resources, preflight

`workflow.py digest` renders the fixed state, gates, findings, validation,
reviewer and authority footer; `--prose` caps free text at 80 words. Posted
digests are `digest_sha256`-bound `evidence_comment`s, never transitions.

Worker validation records discovered/executed tests, fixed-code skips and
unevaluable files; undeclared skips or unevaluable files fail acceptance.
`local_ci_parity` matches required checks to local commands unless a declared
platform distinction names a regression test.

Host resources have named slots copied contract→dispatch→lease; missing or
overlapping capacity fails dispatch/provenance. `POST_MERGE_FINDING` opens a
`corrects` successor without changing merged state.

Preflight records nonblocking PASS/WARN/SKIP/N_A for paths, lint scope,
`core.longpaths`, policy, symlinks, line endings and LFS. Warnings alone do not
block adoption or review.
