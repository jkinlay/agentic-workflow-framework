# Review tiers, cap dispositions and closeout

Version 1.9.4 runtime; AWF-16 revision ships in 1.9.5. The code and [lifecycle](23-TICKET-LIFECYCLE.md) are authoritative; this document grants no execution authority.

## Risk tiers

Every contract declares `risk_tier` (1, 2 or 3) and justification; uncertain is Tier 2. Tier 1 needs eligible paths, no protected paths and no true `risk_flags`. The gate recomputes the highest match. Only an owner-signed `tier-reassignment` and re-issued contract changes it; all records rebind.

Tier 1 uses one critic; findings advise. Tier 2 has three rounds; at cap, ticket P2s and route P1s to the owner. Tier 3 covers governance, release, merge/qualification and CI gates, defaults to a mandatory maximum of three, and requires candidate-bound owner review. Boundaries always block.

Each round's `critic_artifact_binding` contains critic run ID, retained canonical
UTF-8 `result.json` SHA-256, head and round. Its receipt retains those bytes and
frozen completion identities. The gate reparses artifacts and derives findings;
copied fields cannot substitute. Unchanged diffs keep history; changed diffs need
a current round. Tier 2 exceeds its cap only through authenticated owner
disposition. Tier 3 may exceed its cap for round N only when a PR comment from
the posting actor's trusted owner id predates that round and consists exactly of:

`AWF-OVERCAP-APPROVAL: round N of cap M, approver <owner>, ruling <time>`

Wrong-round, malformed, untrusted or late records do not qualify.

Verdicts/posting observations repeat it, binding verdict/digest,
repository/PR, immutable comment ID/URL, exact comment/body, anchor, run/time.
GitHub collector re-reads both via the review host, giving
the CLI gate a runtime registry pinned to implementation, release, repository
and per-run receipt (schema/template). Configured IDs narrow it;
bundle/config/environment/fixtures never register. Mismatches stay `NOT_READY`
with a reason.

## Bases, lineage and dispositions

BLOCKER/MAJOR findings carry contract `{criterion_id}` or `{boundary_code}` from `PROTECTED_PATH`, `SCOPE_ESCAPE`, `CREDENTIAL_EXPOSURE`, `UNSAFE_PATH`, `UNREGISTERED_PRODUCER`, `CI_BINDING`, `INDEPENDENCE`; missing bases are refused. A new serious finding at an existing path/basis names `supersedes_finding_id`. An out-of-closure defect is a MINOR successor-ticket proposal, never a new round.

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
| `EXTEND_ONE_CYCLE` | → CHANGES_REQUESTED | Tier 2: increments `cap_extensions`; refused at `execution.max_cap_extensions` (default 2, maximum 3). Tier 3 uses the canonical pre-round approval above. |

`workflow.py cap-plan` translates a disposition into event and facts or refuses it; the host loop pauses with `REVIEW_CAP_REACHED` and resumes only with `--disposition`.

`MERGE_WITH_NOTES` authenticates the exact terminal verdict/digest and artifact.
Otherwise the terminal artifact must be `APPROVE`/`PASS`; only this cap path may
carry its `REQUEST_CHANGES`. The gate records its ordered verified bindings.

## Jira lifecycle mirroring

Only the controller writes. `WORKER_STARTED` → `in_progress`; `PR_READY` → `in_review`; owner changes → `in_progress`; `JIRA_RECONCILED` → `done` with PR, reviewed head and merge commit. BLOCK/PARK never write. Tickets matching `jira.owner_closure_keywords` wait for an `owner-closure` record. `jira.lifecycle_writes` only disables mappings. Read back every write; mismatch or unknown stops that ticket's writes and is never reissued. `controller.auto_transition_jira: true` skips per-write prompts.

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
